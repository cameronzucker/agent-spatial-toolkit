#!/usr/bin/env bash
# state_helpers.sh — sourced library for reading/writing .handoff/state.json.
#
# state.json is the SINGLE source of truth for task state. All writes go
# through these helpers so we maintain:
#   - atomic mv-based writes
#   - schema validation on every write
#   - lease-ownership checks (a script that doesn't hold the current lease
#     cannot mutate task state for the leased task)
#
# Functions:
#   state_path                       -> prints absolute path
#   state_init                       -> create from template if missing
#   state_validate [path]            -> exit nonzero if malformed
#   state_get_task <id>              -> jq -c on one task
#   state_get_next_pending           -> jq -c on next eligible task or null
#   state_set_task_status <id> <s>   -> mutate status, bump updated_at
#   state_set_task_phase <id> <p>    -> mutate phase, bump updated_at
#   state_set_task_pr <id> <num> <url> -> record PR
#   state_acquire_lease <id> <session_id> <branch> <head_sha>
#   state_release_lease <id> <session_id> <final_status>
#   state_get_current_lease          -> jq on current_lease (null if none)

if [[ -z "${AUTO_CLAUDE_REPO_ROOT:-}" ]]; then
    _state_self="${BASH_SOURCE[0]}"
    AUTO_CLAUDE_REPO_ROOT="$(cd "$(dirname "$_state_self")/../.." && pwd)"
    export AUTO_CLAUDE_REPO_ROOT
fi

state_path() {
    printf '%s\n' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
}

# Path of the cross-process lock that serializes state-mutating callers.
# Held with an exclusive flock for the duration of any read-check-write
# sequence (state_init / state_acquire_lease / state_release_lease /
# state_set_task_*). The session lock that gates which process is "the
# implementer" is a different lock; this one is purely for serializing
# state.json writes between any two processes that might race.
_state_lock_path() {
    printf '%s\n' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.lock"
}

# _with_state_lock <function> [args...]
# Runs the given callable while holding an exclusive flock on state.lock.
# flock(1) is bash-compatible and is released on FD close, so the wrapper
# opens-and-closes its FD.
#
# Reentrancy (NM1): a previous version honored AUTO_CLAUDE_STATE_LOCK_HELD=1
# as a marker that "we already hold the lock, skip re-acquiring." But the
# implementer Claude session inherits this env var, so any subagent could
# `export AUTO_CLAUDE_STATE_LOCK_HELD=1` and bypass every state-mutation
# lock. We now gate the reentrancy escape on a PID match: only a caller
# whose marker value equals THIS process's `$$` is treated as already
# inside the lock. Crossing a process boundary (subshell, sub-process,
# inherited env into a `claude` invocation) breaks `$$` equality and forces
# a real flock.
_with_state_lock() {
    if [[ "${AUTO_CLAUDE_STATE_LOCK_HELD:-}" == "$$" ]]; then
        # Already inside a state-lock critical section in THIS process; do
        # not re-flock or we could deadlock on a non-reentrant filesystem.
        "$@"
        return $?
    fi
    local lp
    lp="$(_state_lock_path)"
    mkdir -p "$(dirname "$lp")"
    : > /dev/null  # noop; ensure $? clean before flock
    (
        # Acquire exclusive lock. flock blocks; the watchdog runs at most
        # once per minute so contention is bounded.
        flock -x 9
        # Mark with our PID so a nested call inside the same process can
        # detect it and skip re-flocking. The subshell inherits the parent's
        # `$$`, so $$ here equals the parent's PID — exactly what we want.
        AUTO_CLAUDE_STATE_LOCK_HELD="$$"
        export AUTO_CLAUDE_STATE_LOCK_HELD
        "$@"
    ) 9>"$lp"
}

# Validate a task_id: uppercase letters, digits, `_`, `-`; starts with letter;
# 1..64 chars. Refuses leading dashes (which break git as a positional arg)
# and refuses anything containing shell or path metacharacters.
_validate_task_id() {
    local id="$1"
    if [[ ! "$id" =~ ^[A-Z][A-Z0-9_-]{0,63}$ ]]; then
        echo "validate_task_id: rejected '$id' (must match ^[A-Z][A-Z0-9_-]{0,63}\$)" >&2
        return 1
    fi
    return 0
}

# Validate a branch name. Defers to git's own check-ref-format (which knows
# about all the bizarre rules — no `..`, no trailing `/`, no `@{`, etc.) and
# additionally refuses leading dashes (which break `git checkout -b`) and
# `--` anywhere (option-eaten by various git subcommands).
_validate_branch() {
    local branch="$1"
    if [[ -z "$branch" ]]; then
        echo "validate_branch: empty branch name" >&2
        return 1
    fi
    case "$branch" in
        -*)
            echo "validate_branch: rejected '$branch' (leading dash)" >&2
            return 1 ;;
        *' '*)
            echo "validate_branch: rejected '$branch' (embedded space)" >&2
            return 1 ;;
        *'..'*)
            echo "validate_branch: rejected '$branch' (embedded dotdot)" >&2
            return 1 ;;
    esac
    # Ask git itself. Suppresses output; only the exit code matters.
    if ! git check-ref-format --branch "$branch" >/dev/null 2>&1; then
        echo "validate_branch: rejected '$branch' (git check-ref-format failed)" >&2
        return 1
    fi
    return 0
}

# Internal: write JSON atomically to state.json after schema validation.
_state_write() {
    local new_json="$1"
    local sp
    sp="$(state_path)"
    local tmp
    tmp="$(mktemp "${sp}.tmp.XXXXXX")"
    printf '%s\n' "$new_json" > "$tmp"
    if ! state_validate "$tmp"; then
        rm -f "$tmp"
        echo "_state_write: refusing to write — schema validation failed" >&2
        return 1
    fi
    mv "$tmp" "$sp"
}

_state_init_locked() {
    local sp
    sp="$(state_path)"
    if [[ -f "$sp" ]]; then
        return 0
    fi
    local example="$AUTO_CLAUDE_REPO_ROOT/.handoff/state.example.json"
    if [[ ! -f "$example" ]]; then
        echo "state_init: template missing at $example" >&2
        return 1
    fi
    # Build the canonical state JSON in memory, then write atomically.
    # Writing directly to $sp would leave a truncated file on a mid-write
    # crash — every subsequent state_validate would then fail and the
    # framework would be wedged until manual recovery.
    local new_json
    new_json=$(jq 'del(._comment) | .tasks = []' "$example") || {
        echo "state_init: failed to compose canonical state from $example" >&2
        return 1
    }
    [[ -n "$new_json" ]] || { echo "state_init: empty composition" >&2; return 1; }
    _state_write "$new_json"
}

state_init() {
    _with_state_lock _state_init_locked
}

# state_validate [path]
# Validates schema invariants. Path defaults to state.json. Returns nonzero on
# violation; emits human-readable error on stderr.
state_validate() {
    local path="${1:-$(state_path)}"
    [[ -f "$path" ]] || { echo "state_validate: $path missing" >&2; return 1; }

    # 1. parseable JSON
    if ! jq -e . "$path" >/dev/null 2>&1; then
        echo "state_validate: $path is not valid JSON" >&2
        return 2
    fi

    # 2. required top-level keys + types. Use a single jq script so we get one
    #    pass; it returns "ok" or a list of failures.
    local report
    report=$(jq -r '
        def fail(msg): "FAIL: " + msg;
        [
            (if .schema_version == 1 then empty else fail("schema_version != 1") end),
            (if (.tasks | type) == "array" then empty else fail("tasks must be array") end),
            (if (.current_lease == null or (.current_lease | type) == "object")
                then empty else fail("current_lease must be null or object") end),
            (.tasks | to_entries[] | .key as $i | .value as $t |
                (if ($t.id | type) == "string" then empty
                    else fail("tasks[\($i)].id must be string") end),
                (if ($t.status // "") | IN("pending","leased","in_progress","pr_open","blocked","done")
                    then empty else fail("tasks[\($i)].status invalid: \($t.status)") end),
                (if ($t.branch | type) == "string" then empty
                    else fail("tasks[\($i)].branch must be string") end),
                (if ($t.attempts // 0) | type == "number" then empty
                    else fail("tasks[\($i)].attempts must be number") end),
                (if ($t.depends_on // []) | type == "array" then empty
                    else fail("tasks[\($i)].depends_on must be array") end)
            )
        ] | if length == 0 then "ok" else .[] end
    ' "$path" 2>&1)

    if [[ "$report" != "ok" ]]; then
        echo "state_validate: $report" >&2
        return 3
    fi

    # 3. unique task ids
    local dupe
    dupe=$(jq -r '[.tasks[].id] | group_by(.)[] | select(length>1) | .[0]' "$path")
    if [[ -n "$dupe" ]]; then
        echo "state_validate: duplicate task id: $dupe" >&2
        return 4
    fi

    return 0
}

state_get_task() {
    local id="$1"
    jq -c --arg id "$id" '.tasks[] | select(.id == $id)' "$(state_path)"
}

# state_get_next_pending — first task that is pending and whose deps are all done.
state_get_next_pending() {
    jq -c '
        . as $root |
        [.tasks[] | select(.status == "pending")] as $pending |
        ($pending[] | . as $t |
            ($t.depends_on // []) as $deps |
            if ($deps | length) == 0 then $t
            else
                (
                    [$deps[] as $d | $root.tasks[] | select(.id == $d) | .status] as $dep_statuses |
                    if ($dep_statuses | length) == ($deps | length) and (all($dep_statuses[]; . == "done"))
                    then $t else empty end
                )
            end
        ) | . // null
    ' "$(state_path)" | head -1
}

state_get_current_lease() {
    jq -c '.current_lease // null' "$(state_path)"
}

# Mutating helpers below NEVER interpolate runtime values into jq programs.
# All values flow through `--arg` / `--argjson` so a `pr_url` containing `"`,
# or a `phase` containing jq syntax (the implementer session is untrusted
# code that calls these wrappers) cannot corrupt the filter or trigger
# arbitrary jq expression evaluation against state.json.

_state_set_task_status_locked() {
    local id="$1"
    local status="$2"
    local sp now new_json
    sp="$(state_path)"
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    new_json=$(jq \
        --arg id "$id" \
        --arg status "$status" \
        --arg now "$now" \
        '.tasks |= map(if .id == $id then .status = $status | .updated_at = $now else . end)' \
        "$sp") || return 1
    [[ -n "$new_json" ]] || { echo "state_set_task_status: empty result" >&2; return 1; }
    _state_write "$new_json"
}

state_set_task_status() {
    local id="$1"
    local status="$2"
    case "$status" in
        pending|leased|in_progress|pr_open|blocked|done) ;;
        *) echo "state_set_task_status: invalid status '$status'" >&2; return 1 ;;
    esac
    _with_state_lock _state_set_task_status_locked "$id" "$status"
}

_state_set_task_phase_locked() {
    local id="$1"
    local phase="$2"
    local sp now new_json
    sp="$(state_path)"
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    new_json=$(jq \
        --arg id "$id" \
        --arg phase "$phase" \
        --arg now "$now" \
        '.tasks |= map(if .id == $id then .phase = $phase | .updated_at = $now else . end)' \
        "$sp") || return 1
    [[ -n "$new_json" ]] || { echo "state_set_task_phase: empty result" >&2; return 1; }
    _state_write "$new_json"
}

state_set_task_phase() {
    local id="$1"
    local phase="$2"
    # phase comes from the implementer session — untrusted. Validate against
    # the known set rather than blindly writing.
    case "$phase" in
        leased|branch_created|editing|tests_running|committing|pushing|pr_opening|pr_open|done|blocked)
            ;;
        *)
            echo "state_set_task_phase: invalid phase '$phase'" >&2
            return 1
            ;;
    esac
    _with_state_lock _state_set_task_phase_locked "$id" "$phase"
}

_state_set_task_pr_locked() {
    local id="$1"
    local pr_number="$2"
    local pr_url="$3"
    local sp now new_json
    sp="$(state_path)"
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    new_json=$(jq \
        --arg id "$id" \
        --argjson pr_number "$pr_number" \
        --arg pr_url "$pr_url" \
        --arg now "$now" \
        '.tasks |= map(if .id == $id then .pr_number = $pr_number | .pr_url = $pr_url | .status = "pr_open" | .updated_at = $now else . end)' \
        "$sp") || return 1
    [[ -n "$new_json" ]] || { echo "state_set_task_pr: empty result" >&2; return 1; }
    _state_write "$new_json"
}

state_set_task_pr() {
    local id="$1"
    local pr_number="$2"
    local pr_url="$3"
    # pr_number must be a non-negative integer; reject anything else so jq's
    # --argjson doesn't blow up on non-numeric input (and so a value like
    # `null) | .secret = ...` can't sneak through).
    if ! [[ "$pr_number" =~ ^[0-9]+$ ]]; then
        echo "state_set_task_pr: pr_number must be a non-negative integer (got '$pr_number')" >&2
        return 1
    fi
    _with_state_lock _state_set_task_pr_locked "$id" "$pr_number" "$pr_url"
}

# state_acquire_lease <task_id> <session_id> <branch> <head_sha>
# Atomic read-check-write: validates that the task is currently pending and
# there's no competing lease, then writes the lease + flips status. The whole
# sequence runs under an exclusive flock on .handoff/state.lock so two
# concurrent callers cannot race past the precondition checks (B5).
_state_acquire_lease_locked() {
    local id="$1"
    local session_id="$2"
    local branch="$3"
    local head_sha="$4"
    local cwd_root="${AUTO_CLAUDE_REPO_ROOT}"
    local now
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local sp
    sp="$(state_path)"

    # Pre-flight: task is pending, no current lease, branch matches.
    local current_status current_lease
    current_status=$(jq -r --arg id "$id" '.tasks[] | select(.id == $id) | .status // "missing"' "$sp")
    if [[ -z "$current_status" || "$current_status" == "missing" ]]; then
        echo "state_acquire_lease: task $id not found" >&2
        return 1
    fi
    if [[ "$current_status" != "pending" ]]; then
        echo "state_acquire_lease: task $id is $current_status, not pending" >&2
        return 2
    fi
    current_lease=$(jq -r '.current_lease // empty' "$sp")
    if [[ -n "$current_lease" ]]; then
        echo "state_acquire_lease: another lease is active" >&2
        return 3
    fi

    local new_json
    new_json=$(jq \
        --arg id "$id" \
        --arg session_id "$session_id" \
        --arg branch "$branch" \
        --arg head_sha "$head_sha" \
        --arg cwd_root "$cwd_root" \
        --arg now "$now" \
        '
        .tasks |= map(
            if .id == $id then
                .status = "leased"
                | .head_sha_at_lease_start = $head_sha
                | .phase = "leased"
                | .updated_at = $now
                | .attempts = (.attempts // 0) + 1
            else . end
        )
        | .current_lease = {
            task_id: $id,
            session_id: $session_id,
            branch: $branch,
            head_sha_at_lease_start: $head_sha,
            cwd_root: $cwd_root,
            acquired_at: $now
        }
        | .last_session_id = $session_id
        ' "$sp")
    _state_write "$new_json"
}

state_acquire_lease() {
    local id="$1"
    local session_id="$2"
    local branch="$3"
    local head_sha="$4"

    # Reject malformed inputs before they reach git or state.json (M3).
    if ! _validate_task_id "$id"; then
        return 4
    fi
    if ! _validate_branch "$branch"; then
        return 5
    fi
    _with_state_lock _state_acquire_lease_locked "$id" "$session_id" "$branch" "$head_sha"
}

# state_release_lease <task_id> <session_id> <final_status>
# Verifies the lease belongs to this session before clearing.
_state_release_lease_locked() {
    local id="$1"
    local session_id="$2"
    local final_status="$3"

    local sp
    sp="$(state_path)"
    local owner
    owner=$(jq -r '.current_lease.session_id // empty' "$sp")
    if [[ "$owner" != "$session_id" ]]; then
        echo "state_release_lease: lease owned by '$owner', not '$session_id'" >&2
        return 2
    fi

    local now
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local new_json
    new_json=$(jq \
        --arg id "$id" \
        --arg final_status "$final_status" \
        --arg now "$now" \
        '
        .tasks |= map(
            if .id == $id then
                .status = $final_status
                | .updated_at = $now
                | .phase = (if $final_status == "done" or $final_status == "pr_open"
                            then "done"
                            else .phase end)
            else . end
        )
        | .current_lease = null
        ' "$sp")
    _state_write "$new_json"
}

state_release_lease() {
    local id="$1"
    local session_id="$2"
    local final_status="$3"
    case "$final_status" in
        pending|pr_open|blocked|done) ;;
        *) echo "state_release_lease: invalid final_status '$final_status'" >&2; return 1 ;;
    esac
    _with_state_lock _state_release_lease_locked "$id" "$session_id" "$final_status"
}

# Refuse a write if the caller's session is not the lease owner. Used by any
# helper that mutates a leased task. id is optional; if omitted, asserts on
# whatever the current lease is.
state_assert_lease_owner() {
    local session_id="$1"
    local id="${2:-}"
    local sp
    sp="$(state_path)"
    local owner_session owner_task
    owner_session=$(jq -r '.current_lease.session_id // empty' "$sp")
    owner_task=$(jq -r '.current_lease.task_id // empty' "$sp")
    if [[ -z "$owner_session" ]]; then
        echo "state_assert_lease_owner: no lease held; refusing write" >&2
        return 1
    fi
    if [[ "$owner_session" != "$session_id" ]]; then
        echo "state_assert_lease_owner: lease owned by '$owner_session', not '$session_id'" >&2
        return 2
    fi
    if [[ -n "$id" && "$owner_task" != "$id" ]]; then
        echo "state_assert_lease_owner: lease is on '$owner_task', not '$id'" >&2
        return 3
    fi
    return 0
}
