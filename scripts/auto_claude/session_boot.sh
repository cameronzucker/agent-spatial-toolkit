#!/usr/bin/env bash
# session_boot.sh — start a new claude session for a task.
#
# Called by watchdog with one arg: task_id. Acquires the session lock,
# leases the task, creates the branch, installs the pre-commit hook,
# starts a heartbeat, and execs `claude -p`. On return, calls session_exit.

set -euo pipefail

usage() {
    cat <<'EOF'
session_boot.sh — boot a claude session for a task

Usage:
    session_boot.sh <task_id>

Detached background entry point used by watchdog.sh. Not normally invoked
directly except in tests with AUTO_CLAUDE_MOCK_CLAUDE=1.
EOF
}

if [[ $# -lt 1 || "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 1
fi

task_id="$1"

_self="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd "$(dirname "$_self")" && pwd)"
AUTO_CLAUDE_REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export AUTO_CLAUDE_REPO_ROOT

# shellcheck source=audit.sh
source "$SCRIPT_DIR/audit.sh"
# shellcheck source=lock_helpers.sh
source "$SCRIPT_DIR/lock_helpers.sh"
# shellcheck source=state_helpers.sh
source "$SCRIPT_DIR/state_helpers.sh"

# Always operate from repo root so all relative resolution is consistent.
cd "$AUTO_CLAUDE_REPO_ROOT"

SESSION_LOCK="$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
LOGS_DIR="$AUTO_CLAUDE_REPO_ROOT/.handoff/logs"
mkdir -p "$LOGS_DIR"

# Generate session id
session_id="${AUTO_CLAUDE_SESSION_ID:-}"
if [[ -z "$session_id" ]]; then
    session_id="sess-$(date -u +%Y%m%dT%H%M%S)-$$-$RANDOM"
fi
export AUTO_CLAUDE_SESSION_ID="$session_id"

audit_init
audit_event "session_boot_start" "$(jq -cn --arg t "$task_id" --arg s "$session_id" '{task_id:$t, session_id:$s}')"

# Sanity check: cwd is repo root.
if [[ "$(pwd -P)" != "$(cd "$AUTO_CLAUDE_REPO_ROOT" && pwd -P)" ]]; then
    audit_event "session_boot_error" "$(jq -cn --arg pwd "$(pwd -P)" --arg expected "$AUTO_CLAUDE_REPO_ROOT" '{reason:"cwd_mismatch", pwd:$pwd, expected:$expected}')"
    exit 1
fi

# Read task; verify pending.
task_json=$(state_get_task "$task_id" || echo "")
if [[ -z "$task_json" || "$task_json" == "null" ]]; then
    audit_event "session_boot_error" "$(jq -cn --arg t "$task_id" '{reason:"task_not_found", task_id:$t}')"
    exit 1
fi
task_status=$(jq -r '.status' <<<"$task_json")
task_branch=$(jq -r '.branch' <<<"$task_json")

if [[ "$task_status" != "pending" ]]; then
    audit_event "session_boot_error" "$(jq -cn --arg t "$task_id" --arg s "$task_status" '{reason:"task_not_pending", task_id:$t, status:$s}')"
    exit 1
fi

# Acquire lock first (we use the not-yet-leased state as our marker).
if ! acquire_lock "$SESSION_LOCK" "$task_id" "$task_branch" "leased"; then
    rc=$?
    audit_event "session_boot_error" "$(jq -cn --arg rc "$rc" '{reason:"lock_collision", rc:($rc|tonumber)}')"
    exit 1
fi
trap '_session_cleanup' EXIT

_heartbeat_pid=""
# shellcheck disable=SC2317  # invoked via `trap`
_session_cleanup() {
    if [[ -n "${_heartbeat_pid:-}" ]] && kill -0 "$_heartbeat_pid" 2>/dev/null; then
        kill "$_heartbeat_pid" 2>/dev/null || true
        wait "$_heartbeat_pid" 2>/dev/null || true
    fi
    release_lock "$SESSION_LOCK" 2>/dev/null || true
}

# Fetch origin (read-only).
git fetch origin 2>>"$LOGS_DIR/session-$session_id.err.log" || {
    audit_event "session_boot_warning" '{"reason":"git_fetch_failed_continuing"}'
}

# Verify on main, clean. (We allow being on the target branch already if it
# exists locally — but for v1 we require main + clean.)
current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "DETACHED")
if [[ "$current_branch" != "main" ]]; then
    audit_event "alert_dirty_at_boot" "$(jq -cn --arg b "$current_branch" '{reason:"not_on_main", branch:$b}')"
    exit 1
fi
if [[ -n "$(git status --porcelain=v2 2>/dev/null | grep -E '^[12u]' || true)" ]]; then
    audit_event "alert_dirty_at_boot" '{"reason":"working_tree_dirty"}'
    exit 1
fi

head_sha=$(git rev-parse HEAD)

# Acquire lease in state.json
if ! state_acquire_lease "$task_id" "$session_id" "$task_branch" "$head_sha"; then
    audit_event "session_boot_error" '{"reason":"state_acquire_lease_failed"}'
    exit 1
fi
state_set_task_phase "$task_id" "branch_created" || true

# Create the branch
if git rev-parse --verify "$task_branch" >/dev/null 2>&1; then
    audit_event "session_boot_warning" "$(jq -cn --arg b "$task_branch" '{reason:"branch_existed_checked_out", branch:$b}')"
    "$SCRIPT_DIR/safe-git" checkout "$task_branch"
else
    "$SCRIPT_DIR/safe-git" checkout -b "$task_branch"
fi

# Install pre-commit hook
hook_dest="$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
cp -f "$SCRIPT_DIR/hooks/pre-commit" "$hook_dest"
chmod +x "$hook_dest"
audit_event "hook_installed" "$(jq -cn --arg p "$hook_dest" '{path:$p}')"

# Heartbeat in background
(
    while sleep 300; do
        update_heartbeat "$SESSION_LOCK" 2>/dev/null || exit 0
    done
) &
_heartbeat_pid=$!

# Build the implementer prompt
task_title=$(jq -r '.title' <<<"$task_json")
task_notes=$(jq -r '.notes // ""' <<<"$task_json")

prompt=$(cat <<EOF
You are an implementer claude session running under the auto-claude orchestration framework.

Task: $task_id — $task_title
Branch: $task_branch (already checked out)
Notes from the orchestrator:
$task_notes

BINDING RULES (violations will be rejected by the safe-git wrapper and the pre-commit hook):
1. Use scripts/auto_claude/safe-git instead of git for ALL git operations.
2. You MUST stay on branch '$task_branch'. Do not checkout, switch, or reset to any other branch.
3. You MUST NOT cd outside the repo root '$AUTO_CLAUDE_REPO_ROOT'.
4. Update task phase as you progress: scripts/auto_claude/state_helpers.sh provides state_set_task_phase.
   Phases: editing -> tests_running -> committing -> pushing -> pr_opening -> pr_open
5. Before exit: ensure tests pass, commit, push, open a PR via 'gh pr create'.
6. If something goes wrong you cannot fix, leave a clear note in .handoff/ and exit nonzero.

Success criteria for this task:
- Tests pass.
- Branch is pushed to origin.
- A PR is open against main.
- The session terminates cleanly (exit 0) after PR creation.
EOF
)

# Exec claude (or a mock for tests)
out_log="$LOGS_DIR/session-$session_id.out.log"
err_log="$LOGS_DIR/session-$session_id.err.log"

claude_bin="${AUTO_CLAUDE_CLAUDE_BIN:-claude}"
claude_exit=0
if [[ "${AUTO_CLAUDE_MOCK_CLAUDE:-0}" == "1" ]]; then
    audit_event "claude_invoke_mock" "$(jq -cn --arg s "$session_id" '{session_id:$s}')"
    # Mock: just record we ran and exit 0. Used in end-to-end tests.
    : > "$out_log"
    echo "mock claude run for session $session_id" > "$err_log"
    claude_exit=0
else
    audit_event "claude_invoke" "$(jq -cn --arg s "$session_id" '{session_id:$s}')"
    set +e
    "$claude_bin" -p "$prompt" \
        --allowedTools "Read,Write,Edit,Bash,Grep,Glob" \
        --permission-mode acceptEdits \
        --output-format json \
        > "$out_log" 2> "$err_log"
    claude_exit=$?
    set -e
fi

audit_event "claude_returned" "$(jq -cn --arg s "$session_id" --arg rc "$claude_exit" '{session_id:$s, exit_code:($rc|tonumber)}')"

# Hand off to session_exit for finalization. session_exit takes care of
# heartbeat kill + lock release on its own; clear our trap so cleanup
# doesn't double-fire.
trap - EXIT
if [[ -n "${_heartbeat_pid:-}" ]] && kill -0 "$_heartbeat_pid" 2>/dev/null; then
    kill "$_heartbeat_pid" 2>/dev/null || true
    wait "$_heartbeat_pid" 2>/dev/null || true
fi

"$SCRIPT_DIR/session_exit.sh" "$task_id" "$session_id" "$claude_exit"
exit 0
