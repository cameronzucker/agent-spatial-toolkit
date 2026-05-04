#!/usr/bin/env bash
# audit.sh — sourced library for emitting audit events to .handoff/events.jsonl
#
# Usage:
#   source scripts/auto_claude/audit.sh
#   audit_event "spawn_intent" '{"task_id":"TASK-001"}'
#
# Every line in events.jsonl is a self-contained JSON object with at least:
#   ts (ISO 8601 UTC), type, pid, host
# plus whatever fields the caller adds.

# Resolve repo root from this script's location so the library works regardless
# of caller's cwd. AUTO_CLAUDE_REPO_ROOT can be overridden for tests.
if [[ -z "${AUTO_CLAUDE_REPO_ROOT:-}" ]]; then
    _audit_self="${BASH_SOURCE[0]}"
    AUTO_CLAUDE_REPO_ROOT="$(cd "$(dirname "$_audit_self")/../.." && pwd)"
    export AUTO_CLAUDE_REPO_ROOT
fi

audit_event() {
    local type="$1"
    local extra_json="${2:-{\}}"
    local events_file="$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    local ts
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local host
    host="$(hostname -s 2>/dev/null || echo unknown)"

    mkdir -p "$AUTO_CLAUDE_REPO_ROOT/.handoff"

    # Build the line via jq so we get correct escaping. The base object is
    # constructed from CLI args; the extra_json is merged in (caller-provided
    # fields override base fields, which is the documented contract).
    local line
    if ! line=$(jq -cn \
            --arg ts "$ts" \
            --arg type "$type" \
            --arg pid "$$" \
            --arg host "$host" \
            --argjson extra "$extra_json" \
            '{ts:$ts, type:$type, pid:($pid|tonumber), host:$host} + $extra' \
            2>/dev/null); then
        # jq failed: fall back to a minimal hand-built line so we never lose
        # an event, but flag it so log inspectors notice.
        line=$(printf '{"ts":"%s","type":"%s","pid":%s,"host":"%s","_audit_jq_failed":true}' \
            "$ts" "$type" "$$" "$host")
    fi
    printf '%s\n' "$line" >> "$events_file"
}

audit_init() {
    # Ensure events file exists; cron-launched runs may need to create it.
    mkdir -p "$AUTO_CLAUDE_REPO_ROOT/.handoff"
    : >> "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
}
