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
        echo "- Last heartbeat: $hb"
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
    # Best-effort: use the most recent watchdog_decision event
    if [[ -f "$EVENTS" ]]; then
        local last_decision
        last_decision=$(grep '"type":"watchdog_decision"' "$EVENTS" | tail -1 || echo "")
        if [[ -n "$last_decision" ]]; then
            local d r
            d=$(jq -r '.decision // "?"' <<<"$last_decision")
            r=$(jq -r '.reason // ""' <<<"$last_decision")
            echo "$d: $r"
        else
            echo "(no watchdog_decision events yet — has the watchdog run?)"
        fi
    else
        echo "(no events recorded)"
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
