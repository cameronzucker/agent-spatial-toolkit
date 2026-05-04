#!/usr/bin/env bash
# status_render.sh — generate .handoff/STATUS.md from state.json + events.jsonl.
#
# Read-only with respect to state. Idempotent.

set -euo pipefail

_self="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd "$(dirname "$_self")" && pwd)"
AUTO_CLAUDE_REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
export AUTO_CLAUDE_REPO_ROOT

# shellcheck source=state_helpers.sh
source "$SCRIPT_DIR/state_helpers.sh"

# Format a number of seconds as a coarse human age. Matches the format
# produced by sibling_worktrees.sh so the two surfaces feel consistent.
_status_age_human() {
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

usage() {
    cat <<'EOF'
status_render.sh — regenerate .handoff/STATUS.md

Usage:
    status_render.sh [--stdout]

With --stdout, prints to stdout instead of writing the file.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

stdout_mode=0
if [[ "${1:-}" == "--stdout" ]]; then
    stdout_mode=1
fi

EVENTS="$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
LOCK="$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
STATUS="$AUTO_CLAUDE_REPO_ROOT/.handoff/STATUS.md"
sp="$(state_path)"

now=$(date -u +%Y-%m-%dT%H:%M:%SZ)

render() {
    echo "# auto-claude status — generated $now"
    echo ""

    # Current
    echo "## Current"
    if [[ -f "$sp" ]]; then
        local lease
        lease=$(jq -c '.current_lease // null' "$sp")
        if [[ "$lease" == "null" ]]; then
            echo "- Lease: none"
            echo "- Branch: main"
            echo "- Phase: idle"
        else
            local ltask lbr lphase lacq
            ltask=$(jq -r '.current_lease.task_id // "?"' "$sp")
            lbr=$(jq -r '.current_lease.branch // "?"' "$sp")
            lacq=$(jq -r '.current_lease.acquired_at // "?"' "$sp")
            lphase=$(jq -r '.tasks[] | select(.id == "'"$ltask"'") | .phase // "?"' "$sp")
            echo "- Lease: $ltask"
            echo "- Branch: $lbr"
            echo "- Phase: $lphase"
            echo "- Started: $lacq"
        fi
    else
        echo "- (no state.json present)"
    fi

    if [[ -f "$LOCK" ]]; then
        local hb
        hb=$(jq -r '.heartbeat_at // "?"' "$LOCK" 2>/dev/null || echo "?")
        # Compute heartbeat age in human terms — easier to triage at a
        # glance than a raw ISO timestamp ("2 minutes ago" beats parsing
        # 2026-05-04T05:13:42Z by eye).
        local hb_epoch now_epoch age age_h
        if hb_epoch=$(date -u -d "$hb" +%s 2>/dev/null); then
            now_epoch=$(date -u +%s)
            age=$(( now_epoch - hb_epoch ))
            (( age < 0 )) && age=0
            age_h="$(_status_age_human "$age")"
            echo "- Last heartbeat: $hb (${age_h} ago)"
        else
            echo "- Last heartbeat: $hb"
        fi
    else
        echo "- Last heartbeat: (no lock)"
    fi

    echo ""
    echo "## Last 5 events"
    if [[ -f "$EVENTS" ]]; then
        tail -5 "$EVENTS" | while IFS= read -r line; do
            local ts type
            ts=$(jq -r '.ts // "?"' <<<"$line" 2>/dev/null || echo "?")
            type=$(jq -r '.type // "?"' <<<"$line" 2>/dev/null || echo "?")
            # Make a one-line summary: pick a few likely-interesting fields if present
            local summary
            summary=$(jq -r '
                . as $e |
                [
                    (.task_id // empty),
                    (.decision // empty),
                    (.reason // empty),
                    (.session_id // empty),
                    (.final_status // empty)
                ] | map(select(. != "")) | join(" ")
            ' <<<"$line" 2>/dev/null || echo "")
            echo "- $ts $type $summary"
        done
    else
        echo "- (no events.jsonl yet)"
    fi

    echo ""
    echo "## Open PRs"
    if command -v gh >/dev/null 2>&1; then
        local prs
        prs=$(cd "$AUTO_CLAUDE_REPO_ROOT" && gh pr list --state open \
            --json number,title,headRefName 2>/dev/null || echo "[]")
        local count
        count=$(jq 'length' <<<"$prs")
        if (( count == 0 )); then
            echo "- (none)"
        else
            jq -r '.[] | "- #\(.number) \(.title) (\(.headRefName))"' <<<"$prs"
        fi
    else
        echo "- (gh not installed)"
    fi

    echo ""
    echo "## Backlog"
    if [[ -f "$sp" ]]; then
        local nrows
        nrows=$(jq '.tasks | length' "$sp")
        if (( nrows == 0 )); then
            echo "- (no tasks)"
        else
            jq -r '.tasks[] | "- \(.id) [\(.status)] \(.title)" + (if .notes != "" and .notes != null then " (note: \(.notes | tostring | .[0:80]))" else "" end)' "$sp"
        fi
    else
        echo "- (no state.json)"
    fi

    echo ""
    echo "## Why nothing is running right now"
    # Prefer the most recent watchdog_decision event — that's the framework's
    # own self-diagnosis. Fall back to inferring from state.json if the
    # watchdog hasn't run yet, so a brand-new install still shows something
    # useful instead of "(has the watchdog run?)".
    local last_decision=""
    if [[ -f "$EVENTS" ]]; then
        last_decision=$(grep '"type":"watchdog_decision"' "$EVENTS" | tail -1 || echo "")
    fi
    if [[ -n "$last_decision" ]]; then
        local d r
        d=$(jq -r '.decision // "?"' <<<"$last_decision")
        r=$(jq -r '.reason // ""' <<<"$last_decision")
        echo "- $d: $r"
    elif [[ -f "$sp" ]]; then
        # Static inference from state. Order matters: check most-specific first.
        local has_lease pending_count blocked_count
        has_lease=$(jq -r '.current_lease // "null"' "$sp")
        pending_count=$(jq '[.tasks[] | select(.status == "pending")] | length' "$sp")
        blocked_count=$(jq '[.tasks[] | select(.status == "blocked")] | length' "$sp")
        if [[ "$has_lease" != "null" ]]; then
            echo "- session_active: a lease is held; the watchdog will not spawn a successor while a session is in flight"
        elif (( pending_count == 0 && blocked_count > 0 )); then
            echo "- quiescent: no pending tasks ($blocked_count blocked); add a task or unblock an existing one"
        elif (( pending_count == 0 )); then
            echo "- quiescent: no pending tasks (and no blocked tasks); backlog is empty"
        else
            echo "- watchdog_not_yet_run: $pending_count pending task(s) but no watchdog_decision events on record yet — run scripts/auto_claude/watchdog.sh (or wait for cron)"
        fi
    else
        echo "- (no state.json present — run scripts/auto_claude/install.sh)"
    fi
}

if (( stdout_mode == 1 )); then
    render
else
    mkdir -p "$AUTO_CLAUDE_REPO_ROOT/.handoff"
    tmp=$(mktemp "${STATUS}.tmp.XXXXXX")
    render > "$tmp"
    mv "$tmp" "$STATUS"
fi
