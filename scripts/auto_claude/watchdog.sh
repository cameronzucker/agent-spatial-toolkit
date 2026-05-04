#!/usr/bin/env bash
# watchdog.sh — cron-driven supervisor.
#
# Runs the reconciler, decides whether to spawn a successor session, and
# emits state to .handoff/STATUS.md. Idempotent and concurrency-safe via
# flock on .handoff/.watchdog.lock.
#
# Sessions never spawn their own successors. The watchdog is the only
# scheduler.

set -euo pipefail

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

WATCHDOG_LOCK="$AUTO_CLAUDE_REPO_ROOT/.handoff/.watchdog.lock"
SESSION_LOCK="$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
BACKOFF_FILE="$AUTO_CLAUDE_REPO_ROOT/.handoff/.backoff.json"
EVENTS="$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
LOGS_DIR="$AUTO_CLAUDE_REPO_ROOT/.handoff/logs"
REFLOG_DIR="$AUTO_CLAUDE_REPO_ROOT/.handoff/reflog-snapshots"
RATE_LIMIT_PER_HOUR="${AUTO_CLAUDE_RATE_LIMIT:-6}"
LOCK_MAX_AGE="${AUTO_CLAUDE_LOCK_MAX_AGE:-600}"   # 10 min default
STALE_HARD_AGE="${AUTO_CLAUDE_STALE_HARD_AGE:-3600}"  # 60 min

usage() {
    cat <<'EOF'
watchdog.sh — supervisor for auto-claude. Run from cron.

Usage:
    scripts/auto_claude/watchdog.sh [--dry-run]

Behavior:
    1. Acquires .handoff/.watchdog.lock (flock).
    2. Runs reconcile.sh.
    3. Decides: continue / alert / spawn successor / quiescent.
    4. Generates STATUS.md.
    5. Releases lock.

Environment:
    AUTO_CLAUDE_DRY_RUN=1   skip the actual claude spawn
    AUTO_CLAUDE_LOCK_MAX_AGE  heartbeat freshness window (default 600s)
    AUTO_CLAUDE_STALE_HARD_AGE  stale-but-recoverable cutoff (default 3600s)
    AUTO_CLAUDE_RATE_LIMIT  max spawns per rolling hour (default 6)
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

dry_run="${AUTO_CLAUDE_DRY_RUN:-0}"
if [[ "${1:-}" == "--dry-run" ]]; then
    dry_run=1
fi

mkdir -p "$AUTO_CLAUDE_REPO_ROOT/.handoff" "$LOGS_DIR" "$REFLOG_DIR"

# Single-instance: flock -n on watchdog lock.
exec 9>"$WATCHDOG_LOCK"
if ! flock -n 9; then
    # Another watchdog already in progress. Quiet exit.
    exit 0
fi

audit_init
audit_event "watchdog_start" '{"trigger":"cron","dry_run":'"$dry_run"'}'

# Initialize state if missing
if [[ ! -f "$(state_path)" ]]; then
    state_init || {
        audit_event "watchdog_error" '{"reason":"state_init_failed"}'
        exit 1
    }
fi

# Run reconciler
recon=$("$SCRIPT_DIR/reconcile.sh" 2>>"$LOGS_DIR/watchdog.err.log") || {
    audit_event "watchdog_error" '{"reason":"reconcile_failed"}'
    "$SCRIPT_DIR/status_render.sh" || true
    exit 1
}

# Extract decision-relevant fields
lock_state=$(jq -r '.lock.state' <<<"$recon")
lock_age=$(jq -r '.lock.age_s // 0' <<<"$recon")
git_state=$(jq -r '.git.state' <<<"$recon")
state_valid=$(jq -r '.state_file.valid' <<<"$recon")
next_task_id=$(jq -r '.next_pending_task.id // empty' <<<"$recon")
next_task_branch=$(jq -r '.next_pending_task.branch // empty' <<<"$recon")

# Decision tree.
decision="quiescent"
reason=""

case "$lock_state" in
    fresh)
        decision="session_healthy"
        reason="lock fresh (age ${lock_age}s)"
        ;;
    dangling)
        decision="alert_dangling"
        reason="PID dead but heartbeat fresh — session_exit cleanup never ran"
        audit_event "alert_dangling" "$(jq -cn --argjson recon "$recon" '{recon:$recon}')"
        ;;
    stale)
        if (( lock_age > 0 && lock_age < STALE_HARD_AGE )); then
            decision="alert_stuck"
            reason="lock stale (age ${lock_age}s, between $LOCK_MAX_AGE and $STALE_HARD_AGE)"
            audit_event "alert_stuck" "$(jq -cn --argjson recon "$recon" '{recon:$recon}')"
        else
            # B6: verify-then-rm. The reconcile snapshot was taken some ms ago.
            # A new session might have just acquired the lock; if we rm it
            # blindly we wipe a live session's lock and the watchdog spawns a
            # successor that races the live session. Re-read .handoff/.lock
            # and confirm session_id + boot_id still match the snapshot before
            # deleting.
            stale_session_id=$(jq -r '.lock.session_id // empty' <<<"$recon")
            stale_pid=$(jq -r '.lock.pid // 0' <<<"$recon")
            stale_heartbeat=$(jq -r '.lock.heartbeat_at // empty' <<<"$recon")
            current_session_id=""
            current_pid="0"
            current_heartbeat=""
            if [[ -f "$SESSION_LOCK" ]]; then
                current_session_id=$(jq -r '.session_id // empty' "$SESSION_LOCK" 2>/dev/null || echo "")
                current_pid=$(jq -r '.pid // 0' "$SESSION_LOCK" 2>/dev/null || echo "0")
                current_heartbeat=$(jq -r '.heartbeat_at // empty' "$SESSION_LOCK" 2>/dev/null || echo "")
            fi
            if [[ -f "$SESSION_LOCK" \
                  && ( "$current_session_id" != "$stale_session_id" \
                       || "$current_pid" != "$stale_pid" \
                       || "$current_heartbeat" != "$stale_heartbeat" ) ]]; then
                # The lock changed between the snapshot and now. Abort cleanup.
                decision="alert_lock_changed_during_cleanup"
                reason="stale-lock cleanup aborted: lock identity changed (snapshot=$stale_session_id/$stale_pid, current=$current_session_id/$current_pid)"
                audit_event "alert_lock_changed_during_cleanup" "$(jq -cn \
                    --arg snap_session "$stale_session_id" \
                    --arg snap_pid "$stale_pid" \
                    --arg snap_hb "$stale_heartbeat" \
                    --arg cur_session "$current_session_id" \
                    --arg cur_pid "$current_pid" \
                    --arg cur_hb "$current_heartbeat" \
                    '{snapshot:{session_id:$snap_session, pid:($snap_pid|tonumber), heartbeat_at:$snap_hb}, current:{session_id:$cur_session, pid:($cur_pid|tonumber), heartbeat_at:$cur_hb}}')"
            else
                decision="consider_spawn"
                reason="lock stale beyond hard cutoff (age ${lock_age}s) — clearing"
                # Explicitly clear the dead lock so next iteration sees it as absent.
                rm -f "$SESSION_LOCK"
                audit_event "lock_cleared_stale" "$(jq -cn --argjson recon "$recon" '{recon:$recon}')"
            fi
        fi
        ;;
    absent)
        decision="consider_spawn"
        reason="no lock present"
        ;;
    *)
        decision="alert_unknown_lock_state"
        reason="lock state '$lock_state' not recognized"
        audit_event "alert_unknown_lock_state" "$(jq -cn --argjson recon "$recon" '{recon:$recon}')"
        ;;
esac

# Sub-checks for the consider_spawn branch
if [[ "$decision" == "consider_spawn" ]]; then
    # Backoff?
    if [[ -f "$BACKOFF_FILE" ]]; then
        local_backoff_until=$(jq -r '.until // empty' "$BACKOFF_FILE" 2>/dev/null || echo "")
        if [[ -n "$local_backoff_until" ]]; then
            now_epoch=$(date -u +%s)
            until_epoch=$(date -u -d "$local_backoff_until" +%s 2>/dev/null || echo 0)
            if (( now_epoch < until_epoch )); then
                decision="backoff_wait"
                reason="backoff active until $local_backoff_until"
                audit_event "backoff_wait" '{"until":"'"$local_backoff_until"'"}'
            fi
        fi
    fi
fi

if [[ "$decision" == "consider_spawn" ]]; then
    # Dirty git?
    case "$git_state" in
        mid_rebase|mid_merge|mid_cherry_pick)
            decision="alert_dirty_git"
            reason="git state is $git_state — refusing to spawn"
            audit_event "alert_dirty_git" "$(jq -cn --arg s "$git_state" '{git_state:$s}')"
            ;;
        dirty_tracked)
            decision="alert_dirty_git"
            reason="git working tree is dirty — refusing to spawn"
            audit_event "alert_dirty_git" "$(jq -cn --arg s "$git_state" '{git_state:$s}')"
            ;;
    esac
fi

if [[ "$decision" == "consider_spawn" && "$state_valid" != "true" ]]; then
    decision="alert_state_invalid"
    reason="state.json failed schema validation"
    audit_event "alert_state_invalid" '{}'
fi

if [[ "$decision" == "consider_spawn" && -z "$next_task_id" ]]; then
    decision="quiescent"
    reason="no pending tasks with deps met"
fi

if [[ "$decision" == "consider_spawn" ]]; then
    # Rate limit: count spawn_result events in the last hour.
    spawn_count=0
    if [[ -f "$EVENTS" ]]; then
        # Use jq to filter by ts within the last hour
        cutoff=$(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ)
        spawn_count=$(jq -s --arg cutoff "$cutoff" \
            '[.[] | select(.type == "spawn_result" and .ts > $cutoff)] | length' \
            "$EVENTS" 2>/dev/null || echo 0)
    fi
    if (( spawn_count >= RATE_LIMIT_PER_HOUR )); then
        decision="alert_rate_limit"
        reason="rate limit hit ($spawn_count spawns in last hour, max $RATE_LIMIT_PER_HOUR)"
        audit_event "alert_rate_limit" "$(jq -cn --arg c "$spawn_count" '{count:($c|tonumber)}')"
    fi
fi

if [[ "$decision" == "consider_spawn" ]]; then
    decision="spawn"
fi

# Execute spawn if green-lit
if [[ "$decision" == "spawn" ]]; then
    # Reflog snapshot before spawn (Geographica lesson)
    snapshot_ts=$(date -u +%Y%m%dT%H%M%SZ)
    snapshot="$REFLOG_DIR/$snapshot_ts.reflog"
    (cd "$AUTO_CLAUDE_REPO_ROOT" && git reflog --all --no-abbrev --date=iso > "$snapshot" 2>/dev/null) || true
    audit_event "reflog_snapshot" "$(jq -cn --arg p "$snapshot" '{path:$p}')"

    audit_event "spawn_intent" "$(jq -cn --arg t "$next_task_id" --arg b "$next_task_branch" '{task_id:$t, branch:$b}')"

    if [[ "$dry_run" == "1" ]]; then
        audit_event "spawn_result" "$(jq -cn --arg t "$next_task_id" '{task_id:$t, dry_run:true, pid:0}')"
        reason="$reason; dry-run, no spawn"
    else
        # Detached background spawn. setsid + nohup + redirect.
        log_out="$LOGS_DIR/watchdog-spawn-$snapshot_ts.out.log"
        log_err="$LOGS_DIR/watchdog-spawn-$snapshot_ts.err.log"
        setsid nohup "$SCRIPT_DIR/session_boot.sh" "$next_task_id" \
            >"$log_out" 2>"$log_err" </dev/null &
        spawn_pid=$!
        # Give the child a moment to fork off, but don't wait for completion.
        disown "$spawn_pid" 2>/dev/null || true
        audit_event "spawn_result" "$(jq -cn --arg t "$next_task_id" --arg p "$spawn_pid" '{task_id:$t, pid:($p|tonumber)}')"
    fi
fi

audit_event "watchdog_decision" "$(jq -cn --arg d "$decision" --arg r "$reason" '{decision:$d, reason:$r}')"

# Always regenerate STATUS.md
"$SCRIPT_DIR/status_render.sh" >/dev/null 2>&1 || true

audit_event "watchdog_end" "$(jq -cn --arg d "$decision" '{decision:$d}')"
exit 0
