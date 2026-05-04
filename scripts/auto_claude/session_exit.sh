#!/usr/bin/env bash
# session_exit.sh — finalize a session: classify result, release lease, snapshot.
#
# Args: task_id session_id claude_exit_code

set -euo pipefail

usage() {
    cat <<'EOF'
session_exit.sh — finalize an auto-claude session

Usage:
    session_exit.sh <task_id> <session_id> <claude_exit_code>
EOF
}

if [[ $# -lt 3 || "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 1
fi

task_id="$1"
session_id="$2"
claude_exit="$3"

_self="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd "$(dirname "$_self")" && pwd)"
AUTO_CLAUDE_REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export AUTO_CLAUDE_REPO_ROOT
export AUTO_CLAUDE_SESSION_ID="$session_id"

# shellcheck source=audit.sh
source "$SCRIPT_DIR/audit.sh"
# shellcheck source=lock_helpers.sh
source "$SCRIPT_DIR/lock_helpers.sh"
# shellcheck source=state_helpers.sh
source "$SCRIPT_DIR/state_helpers.sh"

cd "$AUTO_CLAUDE_REPO_ROOT"

SESSION_LOCK="$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
RECOVERY_DIR="$AUTO_CLAUDE_REPO_ROOT/.handoff/recovery"
REFLOG_DIR="$AUTO_CLAUDE_REPO_ROOT/.handoff/reflog-snapshots"

start_epoch=$(date -u +%s)

# Verify lease ownership before mutating
if ! state_assert_lease_owner "$session_id" "$task_id" 2>/dev/null; then
    audit_event "session_exit_warning" '{"reason":"lease_not_owned"}'
    # Release lock if we own it
    release_lock "$SESSION_LOCK" 2>/dev/null || true
    exit 1
fi

task_json=$(state_get_task "$task_id" || echo "")
task_branch=$(jq -r '.branch // empty' <<<"$task_json")
attempts=$(jq -r '.attempts // 0' <<<"$task_json")
max_attempts=$(jq -r '.max_attempts // 3' <<<"$task_json")

# Detect mid-rebase / mid-merge / mid-cherry-pick — these indicate a session
# that left the repo in a recovery state.
in_recovery=0
recovery_reason=""
if [[ -d "$AUTO_CLAUDE_REPO_ROOT/.git/rebase-merge" || -d "$AUTO_CLAUDE_REPO_ROOT/.git/rebase-apply" ]]; then
    in_recovery=1
    recovery_reason="mid_rebase"
elif [[ -f "$AUTO_CLAUDE_REPO_ROOT/.git/MERGE_HEAD" ]]; then
    in_recovery=1
    recovery_reason="mid_merge"
elif [[ -f "$AUTO_CLAUDE_REPO_ROOT/.git/CHERRY_PICK_HEAD" ]]; then
    in_recovery=1
    recovery_reason="mid_cherry_pick"
fi

# Quality gates for "the session shipped a PR":
# - claude exited 0
# - branch pushed (remote tracking ref exists for the branch)
# - no uncommitted changes
# - PR exists for the branch via gh
gate_ok=1
gate_failures=()

if [[ "$claude_exit" != "0" ]]; then
    gate_ok=0
    gate_failures+=("claude_exit=$claude_exit")
fi

if [[ -n "$(git status --porcelain=v2 2>/dev/null | grep -E '^[12u]' || true)" ]]; then
    gate_ok=0
    gate_failures+=("uncommitted_changes")
fi

# Branch pushed?
remote_ref="refs/remotes/origin/$task_branch"
if ! git rev-parse --verify "$remote_ref" >/dev/null 2>&1; then
    gate_ok=0
    gate_failures+=("branch_not_pushed")
fi

# PR exists for the branch?
pr_number=""
pr_url=""
if command -v gh >/dev/null 2>&1; then
    pr_json=$(gh pr list --head "$task_branch" --state open \
        --json number,url --limit 1 2>/dev/null || echo "[]")
    pr_number=$(jq -r '.[0].number // empty' <<<"$pr_json")
    pr_url=$(jq -r '.[0].url // empty' <<<"$pr_json")
    if [[ -z "$pr_number" ]]; then
        gate_ok=0
        gate_failures+=("no_open_pr")
    fi
fi

# Tests-passed marker: last commit message mentions "test" or .handoff/tests-passed exists
tests_evidence=0
last_msg=$(git log -1 --pretty=%B 2>/dev/null || echo "")
if [[ -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/tests-passed" ]]; then
    tests_evidence=1
elif grep -qiE 'test' <<<"$last_msg"; then
    tests_evidence=1
fi
if (( tests_evidence == 0 )); then
    gate_failures+=("no_tests_evidence")
    # Tests evidence is a soft gate for v1 — log it but don't block PR_OPEN
    # if all hard gates pass. Comment this in/out per policy.
    # gate_ok=0
fi

# Determine final_status
final_status="pending"
if (( in_recovery )); then
    final_status="blocked"
    snap_dir="$RECOVERY_DIR/$(date -u +%Y%m%dT%H%M%SZ)-$task_id"
    mkdir -p "$snap_dir"
    git status --porcelain=v2 > "$snap_dir/status.txt" 2>&1 || true
    git log -20 --oneline > "$snap_dir/log.txt" 2>&1 || true
    git reflog -50 > "$snap_dir/reflog.txt" 2>&1 || true
    cp -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" "$snap_dir/state.json" 2>/dev/null || true
    audit_event "recovery_snapshot" "$(jq -cn --arg p "$snap_dir" --arg r "$recovery_reason" '{path:$p, reason:$r}')"
elif (( gate_ok == 1 )); then
    final_status="pr_open"
    if [[ -n "$pr_number" && -n "$pr_url" ]]; then
        # Update task with PR info; status_release_lease will set status.
        state_set_task_pr "$task_id" "$pr_number" "$pr_url" || true
    fi
elif (( attempts >= max_attempts )); then
    final_status="blocked"
else
    final_status="pending"
fi

audit_event "quality_gates" "$(jq -cn \
    --arg final "$final_status" \
    --arg ok "$gate_ok" \
    --argjson failures "$(printf '%s\n' "${gate_failures[@]:-}" | jq -R . | jq -s .)" \
    '{final_status:$final, gate_ok:($ok|tonumber), failures:$failures}')"

# Release lease
state_release_lease "$task_id" "$session_id" "$final_status" || {
    audit_event "session_exit_warning" '{"reason":"state_release_lease_failed"}'
}

# Reflog snapshot at exit
exit_snap="$REFLOG_DIR/exit-$(date -u +%Y%m%dT%H%M%SZ)-$session_id.reflog"
git reflog --all --no-abbrev --date=iso > "$exit_snap" 2>/dev/null || true

# Final lock release
release_lock "$SESSION_LOCK" 2>/dev/null || true

end_epoch=$(date -u +%s)
duration=$(( end_epoch - start_epoch ))

audit_event "session_exit" "$(jq -cn \
    --arg s "$session_id" \
    --arg t "$task_id" \
    --arg fs "$final_status" \
    --arg ce "$claude_exit" \
    --arg dur "$duration" \
    '{session_id:$s, task_id:$t, final_status:$fs, claude_exit_code:($ce|tonumber), duration_seconds:($dur|tonumber)}')"

exit 0
