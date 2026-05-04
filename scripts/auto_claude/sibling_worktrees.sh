#!/usr/bin/env bash
# sibling_worktrees.sh — list sibling git worktrees and probe their .handoff/.lock.
#
# Used by the SessionStart hook (.claude/hooks/auto_claude_session_brief.sh) to
# warn when another orchestrator is actively working in a sibling worktree of
# the same repo. Also used by session_boot.sh to surface
# `alert_sibling_orchestrator_active` in the audit log when boot detects
# concurrent activity.
#
# A "sibling" is any worktree from `git worktree list --porcelain` whose path
# is NOT our own repo root. We don't try to detect siblings in unrelated
# checkouts of the same upstream — that's a different problem (different
# .git directories entirely) and would require something like a shared
# coordination directory in $XDG_RUNTIME_DIR or /var/run.
#
# Functions:
#   sibling_worktrees_list           -> emits one line per active sibling
#   sibling_worktrees_has_active     -> exit 0 if any sibling has fresh lock
#
# Output format for sibling_worktrees_list (TSV-style, one row per sibling
# with a fresh lock):
#   <path>\t<branch>\t<heartbeat_age_seconds>\t<task_id>\t<session_id>
#
# Empty output means no sibling has a fresh lock (the common case).

set -euo pipefail

if [[ -z "${AUTO_CLAUDE_REPO_ROOT:-}" ]]; then
    _sib_self="${BASH_SOURCE[0]}"
    AUTO_CLAUDE_REPO_ROOT="$(cd "$(dirname "$_sib_self")/../.." && pwd)"
    export AUTO_CLAUDE_REPO_ROOT
fi

_SIB_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lock_helpers.sh
# shellcheck disable=SC1091
source "$_SIB_SCRIPT_DIR/lock_helpers.sh"

# Internal: human-friendly age string from a number of seconds.
_sib_age_human() {
    local s="$1"
    if (( s < 60 )); then
        printf '%ds\n' "$s"
    elif (( s < 3600 )); then
        printf '%dm\n' $(( s / 60 ))
    elif (( s < 86400 )); then
        printf '%dh%dm\n' $(( s / 3600 )) $(( (s % 3600) / 60 ))
    else
        printf '%dd%dh\n' $(( s / 86400 )) $(( (s % 86400) / 3600 ))
    fi
}

# sibling_worktrees_list [max_age_seconds]
#
# Walks `git worktree list --porcelain` and emits one TSV line per sibling
# whose .handoff/.lock is fresh (heartbeat within max_age, default 600s).
#
# Resilient to:
#   - the repo not having `git worktree list` (returns empty)
#   - sibling worktrees whose .handoff dir doesn't exist (skipped)
#   - sibling locks with malformed JSON (skipped, no error)
#   - the sibling's path being absent on disk (deleted but still in
#     `git worktree list` until `git worktree prune`) — skipped
sibling_worktrees_list() {
    local max_age="${1:-${AUTO_CLAUDE_LOCK_MAX_AGE:-600}}"
    local our_root
    our_root="$(cd "$AUTO_CLAUDE_REPO_ROOT" && pwd -P)"

    # `git worktree list --porcelain` may legitimately fail (e.g. brand-new
    # repo with no commits, or running outside any worktree). Treat any
    # failure as "no siblings" — never noisy on a hook.
    local porcelain
    if ! porcelain=$(cd "$AUTO_CLAUDE_REPO_ROOT" && git worktree list --porcelain 2>/dev/null); then
        return 0
    fi

    local now_epoch
    now_epoch=$(date -u +%s)

    # Parse porcelain: blocks separated by blank lines, each block starts
    # with `worktree <path>` and may include `branch refs/heads/<name>`.
    local wt="" branch=""
    while IFS= read -r line || [[ -n "$line" ]]; do
        if [[ -z "$line" ]]; then
            _sib_emit_if_active "$wt" "$branch" "$max_age" "$now_epoch" "$our_root"
            wt=""; branch=""
            continue
        fi
        case "$line" in
            "worktree "*)  wt="${line#worktree }" ;;
            "branch "*)
                # Strip the refs/heads/ prefix if present for human display.
                branch="${line#branch }"
                branch="${branch#refs/heads/}"
                ;;
            "detached")    branch="(detached)" ;;
        esac
    done <<<"$porcelain"
    # Last block (no trailing blank line)
    _sib_emit_if_active "$wt" "$branch" "$max_age" "$now_epoch" "$our_root"
}

_sib_emit_if_active() {
    local wt="$1" branch="$2" max_age="$3" now_epoch="$4" our_root="$5"
    [[ -z "$wt" ]] && return 0
    [[ -d "$wt" ]] || return 0

    # Skip ourselves. Compare resolved paths so symlinks / trailing slashes
    # don't fool the equality check.
    local wt_real
    wt_real="$(cd "$wt" 2>/dev/null && pwd -P)" || return 0
    [[ "$wt_real" == "$our_root" ]] && return 0

    local lock="$wt/.handoff/.lock"
    [[ -f "$lock" ]] || return 0

    # Read lock fields. Bail (silently) on parse failure — we don't want a
    # corrupt sibling lock to break our hook.
    local hb pid task_id session_id boot_id
    hb=$(jq -r '.heartbeat_at // empty' "$lock" 2>/dev/null) || return 0
    pid=$(jq -r '.pid // empty' "$lock" 2>/dev/null) || return 0
    task_id=$(jq -r '.current_task_id // empty' "$lock" 2>/dev/null) || true
    session_id=$(jq -r '.session_id // empty' "$lock" 2>/dev/null) || true
    boot_id=$(jq -r '.boot_id // empty' "$lock" 2>/dev/null) || true
    [[ -n "$hb" ]] || return 0

    local hb_epoch age
    hb_epoch=$(date -u -d "$hb" +%s 2>/dev/null) || return 0
    age=$(( now_epoch - hb_epoch ))
    (( age >= 0 )) || age=0
    (( age <= max_age )) || return 0

    # boot_id check: if we're on the same host (sibling worktree of the same
    # repo always is) AND the boot_id differs from our own, the sibling's
    # process is from a previous boot and definitely dead.
    if [[ -n "$boot_id" ]]; then
        local our_boot
        our_boot="$(get_boot_id 2>/dev/null || echo "")"
        if [[ -n "$our_boot" && "$our_boot" != "$boot_id" ]]; then
            return 0
        fi
    fi

    # PID alive? If the lock claims a PID that doesn't exist on this host
    # (sibling worktree on the same machine — same PID namespace), the
    # session is dead even if the heartbeat looks recent.
    if [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]]; then
        if ! kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi

    # Emit one TSV row. Use a literal tab so consumers can splitline cheaply.
    local sep
    sep=$'\t'
    printf '%s%s%s%s%s%s%s%s%s\n' \
        "$wt_real" "$sep" \
        "${branch:-?}" "$sep" \
        "$age" "$sep" \
        "${task_id:-?}" "$sep" \
        "${session_id:-?}"
}

# sibling_worktrees_has_active [max_age_seconds]
# Returns 0 if any sibling has a fresh lock, 1 otherwise.
sibling_worktrees_has_active() {
    local out
    out="$(sibling_worktrees_list "${1:-}")"
    [[ -n "$out" ]]
}

# sibling_worktrees_format <max_age_seconds>
# Renders sibling list as a human-readable block. Empty output if none.
sibling_worktrees_format() {
    local max_age="${1:-${AUTO_CLAUDE_LOCK_MAX_AGE:-600}}"
    local rows
    rows="$(sibling_worktrees_list "$max_age")"
    [[ -z "$rows" ]] && return 0

    echo "WARNING: detected concurrent orchestrator activity:"
    while IFS=$'\t' read -r path branch age task _session; do
        [[ -z "$path" ]] && continue
        local age_h
        age_h="$(_sib_age_human "$age")"
        echo "  $path  (branch $branch, last heartbeat ${age_h} ago, task ${task})"
    done <<<"$rows"
    cat <<'EOF'

This means another Claude session is actively working in a sibling worktree.
DO NOT `git checkout` from this tree — that's a global operation (one .git
directory backs all worktrees) and will swap branches under the sibling.
If you need to do parallel work, create your own worktree:
  git worktree add ../this-repo-N main
EOF
}

# CLI entry point: when invoked directly, run sibling_worktrees_format.
# When sourced, just register the functions.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-format}" in
        list)   sibling_worktrees_list "${2:-}" ;;
        active) sibling_worktrees_has_active "${2:-}" ;;
        format) sibling_worktrees_format "${2:-}" ;;
        --help|-h)
            cat <<'EOF'
sibling_worktrees.sh — probe sibling git worktrees for active orchestrators.

Usage:
    sibling_worktrees.sh list   [max_age]    # TSV: path \t branch \t age_s \t task_id \t session_id
    sibling_worktrees.sh active [max_age]    # exit 0 if any sibling fresh
    sibling_worktrees.sh format [max_age]    # human-readable warning (default)

max_age defaults to AUTO_CLAUDE_LOCK_MAX_AGE or 600 seconds.
EOF
            ;;
        *) echo "sibling_worktrees.sh: unknown subcommand '$1'" >&2; exit 1 ;;
    esac
fi
