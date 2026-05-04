#!/usr/bin/env bash
# auto_claude_session_brief.sh — Claude Code SessionStart hook.
#
# Runs whenever a Claude session starts in this project (interactive or
# headless). Anything we print to stdout is injected as additional context
# for the session; stderr is shown to the user but not added to context.
#
# We MUST never fail the session start. Any error path exits 0 with at
# most a stderr breadcrumb.
#
# Behavior:
#   1. If .handoff/state.json doesn't exist, exit 0 silently — the project
#      hasn't been bootstrapped with auto-claude, nothing to brief on.
#   2. Regenerate STATUS.md via scripts/auto_claude/status_render.sh.
#   3. Emit a banner-wrapped block containing:
#        - STATUS.md
#        - Last 5 events from events.jsonl, newest first
#        - Cross-orchestrator detection (sibling worktrees with fresh locks)
#
# Why this is a SessionStart hook and not a slash command: the central PR
# B motivation is the cross-orchestrator collision that happened today —
# two Claude sessions sharing one working tree, one ran `git checkout` and
# wiped the other's edits. The fix is to make sibling activity impossible
# to miss at session boot, BEFORE the user types anything. A slash command
# only helps if the user remembers to run it; a SessionStart hook fires
# every time, including for headless `claude -p` invocations the
# orchestrator itself spawns. (Headless sessions inherit the same hook
# config, which is the right behavior — the implementer claude session
# also benefits from knowing whether a sibling worktree is active before
# it starts editing.)

set -euo pipefail

# CLAUDE_PROJECT_DIR is set by the harness when the hook runs. Fall back
# to walking up from the script location.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" ]]; then
    REPO_ROOT="$CLAUDE_PROJECT_DIR"
else
    _hook_self="${BASH_SOURCE[0]}"
    REPO_ROOT="$(cd "$(dirname "$_hook_self")/../.." && pwd)"
fi

STATE="$REPO_ROOT/.handoff/state.json"
EVENTS="$REPO_ROOT/.handoff/events.jsonl"
STATUS_MD="$REPO_ROOT/.handoff/STATUS.md"
SCRIPT_DIR="$REPO_ROOT/scripts/auto_claude"

# Silent no-op if the project isn't auto-claude-bootstrapped. Most projects
# will fall into this branch — the hook adds zero overhead beyond a few
# stat() calls and exits.
if [[ ! -f "$STATE" ]]; then
    exit 0
fi

# Defensive: missing scripts means a partial install. Don't fail; emit a
# warning to stderr (visible to the user but not in context) and exit.
if [[ ! -x "$SCRIPT_DIR/status_render.sh" ]]; then
    echo "auto_claude_session_brief: status_render.sh not found or not executable; skipping" >&2
    exit 0
fi

# Regenerate STATUS.md. Suppress any errors — we'd rather inject a stale
# STATUS.md than block the session.
"$SCRIPT_DIR/status_render.sh" 2>/dev/null || true

# Banner. Distinctive enough that the agent recognizes it as injected
# context and doesn't confuse it with user input.
printf '=== auto-claude status brief (auto-generated at session start) ===\n'

if [[ -f "$STATUS_MD" ]]; then
    cat "$STATUS_MD"
else
    echo "(STATUS.md not generated — status_render.sh may have failed)"
fi

# Last 5 events, newest first, human-readable.
echo
echo "## Recent events (newest first)"
if [[ -f "$EVENTS" ]]; then
    # `tac` reverses; head -5 then takes the most recent five rows.
    if command -v tac >/dev/null 2>&1; then
        tac "$EVENTS" 2>/dev/null | head -5 | while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            ts=$(jq -r '.ts // "?"' <<<"$line" 2>/dev/null || echo "?")
            type=$(jq -r '.type // "?"' <<<"$line" 2>/dev/null || echo "?")
            summary=$(jq -r '
                [
                    (.task_id // empty),
                    (.decision // empty),
                    (.reason // empty),
                    (.session_id // empty),
                    (.final_status // empty)
                ] | map(select(. != "")) | join(" ")
            ' <<<"$line" 2>/dev/null || echo "")
            echo "- $ts  $type  $summary"
        done
    else
        # macOS without `tac`: use tail then awk-reverse.
        tail -5 "$EVENTS" 2>/dev/null | awk '{a[NR]=$0} END {for(i=NR;i>=1;i--) print a[i]}' | while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            ts=$(jq -r '.ts // "?"' <<<"$line" 2>/dev/null || echo "?")
            type=$(jq -r '.type // "?"' <<<"$line" 2>/dev/null || echo "?")
            echo "- $ts  $type"
        done
    fi
else
    echo "(no events.jsonl yet)"
fi

# Cross-orchestrator detection: any sibling worktree with a fresh lock?
echo
if [[ -x "$SCRIPT_DIR/sibling_worktrees.sh" ]]; then
    # sibling_worktrees_format prints nothing if no siblings are active,
    # which is the common case (one orchestrator per machine).
    sibling_out=$("$SCRIPT_DIR/sibling_worktrees.sh" format 2>/dev/null || true)
    if [[ -n "$sibling_out" ]]; then
        echo "## Cross-orchestrator alert"
        echo "$sibling_out"
    fi
fi

printf '=== end auto-claude brief ===\n'
exit 0
