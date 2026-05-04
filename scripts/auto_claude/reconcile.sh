#!/usr/bin/env bash
# reconcile.sh — read-only inspection of lock + git + GitHub state.
#
# Watchdog runs this before any spawn decision. Output is a single JSON
# object on stdout. Exit code is 0 unless something is genuinely broken.
#
# Output schema:
#   {
#     "lock": {"state":"fresh|stale|dangling|absent",
#              "session_id":..., "pid":..., "heartbeat_at":..., "age_s":...},
#     "git": {"state":"clean|dirty_tracked|mid_rebase|mid_merge|mid_cherry_pick",
#             "branch":..., "ahead":..., "upstream":...},
#     "gh":  {"open_prs":[{"number","title","head","url"}], "fetched":true|false},
#     "state_file": {"present":true|false, "valid":true|false},
#     "current_lease_task": "TASK-001"|null,
#     "next_pending_task": {...}|null
#   }

set -euo pipefail

_self="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd "$(dirname "$_self")" && pwd)"
AUTO_CLAUDE_REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export AUTO_CLAUDE_REPO_ROOT

# shellcheck source=lock_helpers.sh
source "$SCRIPT_DIR/lock_helpers.sh"
# shellcheck source=state_helpers.sh
source "$SCRIPT_DIR/state_helpers.sh"

# Source state_helpers for state_set_task_status used in inspect_gh's
# pr_open → done auto-update.
if ! declare -f state_set_task_status >/dev/null 2>&1; then
    source "$(dirname "$0")/state_helpers.sh"
fi

LOCK_PATH="$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
MAX_LOCK_AGE="${AUTO_CLAUDE_LOCK_MAX_AGE:-600}"

inspect_lock() {
    if [[ ! -f "$LOCK_PATH" ]]; then
        echo '{"state":"absent"}'
        return
    fi
    local heartbeat_at boot_id pid session_id
    heartbeat_at=$(jq -r '.heartbeat_at // empty' "$LOCK_PATH" 2>/dev/null) || heartbeat_at=""
    boot_id=$(jq -r '.boot_id // empty' "$LOCK_PATH" 2>/dev/null) || boot_id=""
    pid=$(jq -r '.pid // empty' "$LOCK_PATH" 2>/dev/null) || pid=""
    session_id=$(jq -r '.session_id // empty' "$LOCK_PATH" 2>/dev/null) || session_id=""

    local age_s=-1
    if [[ -n "$heartbeat_at" ]]; then
        local hb_epoch now_epoch
        hb_epoch=$(date -u -d "$heartbeat_at" +%s 2>/dev/null || echo 0)
        now_epoch=$(date -u +%s)
        age_s=$(( now_epoch - hb_epoch ))
    fi

    local current_boot
    current_boot="$(get_boot_id)"

    local pid_alive=false
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        pid_alive=true
    fi

    local state="stale"
    if [[ "$boot_id" != "$current_boot" ]]; then
        state="stale"  # reboot wiped the process; lock is dead
    elif [[ "$pid_alive" == "false" ]]; then
        # PID dead. Heartbeat fresh? -> dangling (supervisor crashed).
        if (( age_s >= 0 && age_s <= MAX_LOCK_AGE )); then
            state="dangling"
        else
            state="stale"
        fi
    elif (( age_s >= 0 && age_s <= MAX_LOCK_AGE )); then
        state="fresh"
    else
        state="stale"
    fi

    jq -cn \
        --arg state "$state" \
        --arg session_id "$session_id" \
        --arg pid "${pid:-0}" \
        --arg heartbeat_at "$heartbeat_at" \
        --arg age_s "$age_s" \
        --arg boot_id "$boot_id" \
        --arg current_boot "$current_boot" \
        '{
            state:$state,
            session_id:$session_id,
            pid:($pid|tonumber),
            heartbeat_at:$heartbeat_at,
            age_s:($age_s|tonumber),
            boot_id:$boot_id,
            boot_match: ($boot_id == $current_boot)
        }'
}

inspect_git() {
    local state="clean"
    if [[ -d "$AUTO_CLAUDE_REPO_ROOT/.git/rebase-merge" || -d "$AUTO_CLAUDE_REPO_ROOT/.git/rebase-apply" ]]; then
        state="mid_rebase"
    elif [[ -f "$AUTO_CLAUDE_REPO_ROOT/.git/MERGE_HEAD" ]]; then
        state="mid_merge"
    elif [[ -f "$AUTO_CLAUDE_REPO_ROOT/.git/CHERRY_PICK_HEAD" ]]; then
        state="mid_cherry_pick"
    else
        # Any tracked changes? --porcelain=v2 yields lines starting with '1' or '2'
        # for tracked entries. Untracked-only ('? ') is OK for our purposes here.
        local porcelain
        porcelain=$(cd "$AUTO_CLAUDE_REPO_ROOT" && git status --porcelain=v2 2>/dev/null || echo "")
        if grep -qE '^[12u]' <<<"$porcelain"; then
            state="dirty_tracked"
        fi
    fi

    local branch
    branch=$(cd "$AUTO_CLAUDE_REPO_ROOT" && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "DETACHED")

    local upstream ahead
    upstream=$(cd "$AUTO_CLAUDE_REPO_ROOT" && git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo "")
    if [[ -n "$upstream" ]]; then
        ahead=$(cd "$AUTO_CLAUDE_REPO_ROOT" && git rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)
    else
        ahead=0
    fi

    jq -cn \
        --arg state "$state" \
        --arg branch "$branch" \
        --arg upstream "$upstream" \
        --arg ahead "$ahead" \
        '{state:$state, branch:$branch, upstream:$upstream, ahead:($ahead|tonumber)}'
}

inspect_gh() {
    if ! command -v gh >/dev/null 2>&1; then
        echo '{"open_prs":[],"fetched":false,"reason":"gh_not_installed"}'
        return
    fi
    local prs
    if prs=$(cd "$AUTO_CLAUDE_REPO_ROOT" && gh pr list --state open \
                --json number,title,headRefName,url 2>/dev/null); then
        # Auto-reconcile state.json: any pr_open task whose PR number is no
        # longer in the open list AND whose PR is MERGED gets bumped to done.
        # Without this, dependent tasks (depends_on: [TASK-X]) never become
        # eligible because the dependency check requires status == "done".
        # Caught in 2026-05-04 deploy: TASK-MARKER-SCALE waited indefinitely
        # for TASK-DRY-CAMERA's status to flip even after PR #41 merged.
        _reconcile_merged_prs "$prs"
        jq -cn --argjson prs "$prs" '{open_prs:$prs, fetched:true}'
    else
        echo '{"open_prs":[],"fetched":false,"reason":"gh_call_failed"}'
    fi
}

_reconcile_merged_prs() {
    local open_prs="$1"
    local sp
    sp="$(state_path)"
    [[ -f "$sp" ]] || return 0
    local pr_open_tasks
    pr_open_tasks=$(jq -c '.tasks[]? | select(.status == "pr_open" and .pr_number != null) | {id, pr_number}' "$sp" 2>/dev/null)
    [[ -z "$pr_open_tasks" ]] && return 0
    while IFS= read -r task; do
        [[ -z "$task" ]] && continue
        local task_id pr_number still_open pr_state
        task_id=$(jq -r '.id' <<<"$task")
        pr_number=$(jq -r '.pr_number' <<<"$task")
        still_open=$(jq --argjson n "$pr_number" 'any(.[]; .number == $n)' <<<"$open_prs")
        if [[ "$still_open" == "false" ]]; then
            pr_state=$(cd "$AUTO_CLAUDE_REPO_ROOT" && gh pr view "$pr_number" --json state --jq '.state' 2>/dev/null) || continue
            if [[ "$pr_state" == "MERGED" ]]; then
                if state_set_task_status "$task_id" "done" 2>/dev/null; then
                    audit_event "task_status_auto_done" "$(jq -cn --arg id "$task_id" --arg pr "$pr_number" '{task_id:$id, pr_number:($pr|tonumber)}')"
                fi
            fi
        fi
    done <<<"$pr_open_tasks"
}

inspect_state_file() {
    local sp
    sp="$(state_path)"
    if [[ ! -f "$sp" ]]; then
        echo '{"present":false,"valid":false}'
        return
    fi
    if state_validate "$sp" 2>/dev/null; then
        echo '{"present":true,"valid":true}'
    else
        echo '{"present":true,"valid":false}'
    fi
}

current_lease_task() {
    local sp
    sp="$(state_path)"
    [[ -f "$sp" ]] || { echo "null"; return; }
    jq '.current_lease.task_id // null' "$sp"
}

next_pending() {
    local sp
    sp="$(state_path)"
    if [[ ! -f "$sp" ]]; then
        echo "null"
        return
    fi
    local result
    result=$(state_get_next_pending 2>/dev/null || echo "null")
    [[ -n "$result" ]] || result="null"
    echo "$result"
}

# Compose
lock_json=$(inspect_lock)
git_json=$(inspect_git)
gh_json=$(inspect_gh)
state_json=$(inspect_state_file)
lease_task=$(current_lease_task)
next_task=$(next_pending)

jq -cn \
    --argjson lock "$lock_json" \
    --argjson git "$git_json" \
    --argjson gh "$gh_json" \
    --argjson state_file "$state_json" \
    --argjson current_lease_task "$lease_task" \
    --argjson next_pending_task "$next_task" \
    '{
        lock:$lock,
        git:$git,
        gh:$gh,
        state_file:$state_file,
        current_lease_task:$current_lease_task,
        next_pending_task:$next_pending_task
    }'
