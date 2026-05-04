#!/usr/bin/env bash
# lock_helpers.sh — sourced library for atomic on-disk locks.
#
# Locks are JSON files written via mktemp+mv (so consumers either see the full
# lock or no lock — never a partial write). Each lock records:
#   session_id, pid, ppid, host, boot_id, started_at, heartbeat_at,
#   current_branch, current_task_id, phase
#
# Functions exported:
#   acquire_lock <path>            -> 0 acquired, 1 collision (fresh), 2 stale
#   release_lock <path>            -> 0 released, nonzero on ownership mismatch
#   is_lock_fresh <path> <max_age> -> 0 if fresh, 1 otherwise
#   update_heartbeat <path>        -> 0 ok, nonzero on ownership mismatch
#   get_boot_id                    -> prints kernel boot id
#
# Acquire/release semantics use ln(1) on a temp file, which is atomic on
# POSIX filesystems — same primitive flock(1) is built on, but works for
# files we want to keep readable across process boundaries.
#
# Caller is responsible for setting AUTO_CLAUDE_SESSION_ID before calling
# acquire_lock; we don't auto-generate so tests can reuse a known id.

if [[ -z "${AUTO_CLAUDE_REPO_ROOT:-}" ]]; then
    _lock_self="${BASH_SOURCE[0]}"
    AUTO_CLAUDE_REPO_ROOT="$(cd "$(dirname "$_lock_self")/../.." && pwd)"
    export AUTO_CLAUDE_REPO_ROOT
fi

get_boot_id() {
    if [[ -r /proc/sys/kernel/random/boot_id ]]; then
        cat /proc/sys/kernel/random/boot_id
    else
        # Best-effort fallback for non-Linux: hash uptime + hostname so we at
        # least notice a reboot. Not as strong as boot_id but better than nothing.
        printf '%s-%s' "$(uname -n)" "$(uptime 2>/dev/null | tr -d ' \n' || echo nouptime)" \
            | sha256sum | awk '{print $1}'
    fi
}

# Internal: write a fresh lock file atomically. Caller must have decided the
# path is free (or a stale lock has been removed).
_lock_write() {
    local path="$1"
    local task_id="${2:-}"
    local branch="${3:-}"
    local phase="${4:-leased}"

    local session_id="${AUTO_CLAUDE_SESSION_ID:-}"
    if [[ -z "$session_id" ]]; then
        echo "lock_helpers: AUTO_CLAUDE_SESSION_ID must be set before acquire_lock" >&2
        return 10
    fi

    local now boot_id host tmp
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    boot_id="$(get_boot_id)"
    host="$(hostname -s 2>/dev/null || echo unknown)"
    tmp="$(mktemp "${path}.tmp.XXXXXX")"

    jq -n \
        --arg session_id "$session_id" \
        --arg pid "$$" \
        --arg ppid "$PPID" \
        --arg host "$host" \
        --arg boot_id "$boot_id" \
        --arg started_at "$now" \
        --arg heartbeat_at "$now" \
        --arg branch "$branch" \
        --arg task_id "$task_id" \
        --arg phase "$phase" \
        '{
            session_id: $session_id,
            pid: ($pid|tonumber),
            ppid: ($ppid|tonumber),
            host: $host,
            boot_id: $boot_id,
            started_at: $started_at,
            heartbeat_at: $heartbeat_at,
            current_branch: $branch,
            current_task_id: $task_id,
            phase: $phase
        }' > "$tmp"

    # ln(1) gives us atomic create-only-if-not-exists semantics. If the file
    # exists, ln fails and we leave the temp behind for the caller to inspect
    # (we clean it up in acquire_lock itself).
    if ln "$tmp" "$path" 2>/dev/null; then
        rm -f "$tmp"
        return 0
    else
        rm -f "$tmp"
        return 1
    fi
}

# is_lock_fresh — heartbeat within window AND boot_id matches AND PID alive.
is_lock_fresh() {
    local path="$1"
    local max_age="${2:-600}"  # seconds; default 10 minutes

    [[ -f "$path" ]] || return 1

    local heartbeat_at boot_id pid
    heartbeat_at=$(jq -r '.heartbeat_at // empty' "$path" 2>/dev/null) || return 1
    boot_id=$(jq -r '.boot_id // empty' "$path" 2>/dev/null) || return 1
    pid=$(jq -r '.pid // empty' "$path" 2>/dev/null) || return 1

    [[ -n "$heartbeat_at" && -n "$boot_id" && -n "$pid" ]] || return 1

    # boot_id mismatch -> machine rebooted, lock is definitely stale
    local current_boot
    current_boot="$(get_boot_id)"
    [[ "$boot_id" == "$current_boot" ]] || return 1

    # PID alive?
    kill -0 "$pid" 2>/dev/null || return 1

    # Heartbeat within window?
    local hb_epoch now_epoch
    hb_epoch=$(date -u -d "$heartbeat_at" +%s 2>/dev/null) || return 1
    now_epoch=$(date -u +%s)
    local age=$(( now_epoch - hb_epoch ))
    (( age <= max_age )) || return 1

    return 0
}

# acquire_lock <path> [task_id] [branch] [phase]
# Returns:
#   0 acquired
#   1 collision with a fresh lock — back off
#   2 stale lock found (caller must explicitly clear it before retry)
acquire_lock() {
    local path="$1"
    local task_id="${2:-}"
    local branch="${3:-}"
    local phase="${4:-leased}"
    local max_age="${AUTO_CLAUDE_LOCK_MAX_AGE:-600}"

    mkdir -p "$(dirname "$path")"

    if _lock_write "$path" "$task_id" "$branch" "$phase"; then
        return 0
    fi

    # Existed. Is it fresh?
    if is_lock_fresh "$path" "$max_age"; then
        return 1
    fi
    return 2
}

# release_lock <path>
# Verifies session_id matches AUTO_CLAUDE_SESSION_ID before unlinking.
# Held under .serial flock so it doesn't race with update_heartbeat.
release_lock() {
    local path="$1"
    local serial
    serial="$(_lock_serial_path "$path")"
    mkdir -p "$(dirname "$serial")"
    (
        flock -x 9
        [[ -f "$path" ]] || exit 0

        local owner
        owner=$(jq -r '.session_id // empty' "$path" 2>/dev/null) || exit 5
        if [[ "$owner" != "${AUTO_CLAUDE_SESSION_ID:-}" ]]; then
            echo "release_lock: refusing to release lock owned by '$owner' (we are '${AUTO_CLAUDE_SESSION_ID:-}')" >&2
            exit 6
        fi
        rm -f "$path"
    ) 9>"$serial"
}

# Path of the cross-process serial lock guarding lock-file mutations
# (update_heartbeat, release_lock cleanup races, etc.). Sibling lock to the
# session lock itself; held only briefly during read-check-write.
_lock_serial_path() {
    local session_lock="$1"
    printf '%s\n' "${session_lock}.serial"
}

# update_heartbeat <path>
# Atomically refresh heartbeat_at on a lock we own.
#
# M4: read-check-write must be serialized. Without flock, the watchdog can
# `rm` the lock between our owner-check and our `mv`, and the `mv`
# resurrects a deleted lock with our session metadata — a successor that
# thought the slot was free now sees a "fresh" lock that isn't theirs.
# Serialize on a sibling .serial lock; both update_heartbeat and release_lock
# acquire it before touching the lock file.
update_heartbeat() {
    local path="$1"
    local serial
    serial="$(_lock_serial_path "$path")"
    mkdir -p "$(dirname "$serial")"
    (
        flock -x 9
        [[ -f "$path" ]] || exit 1

        local owner
        owner=$(jq -r '.session_id // empty' "$path" 2>/dev/null) || exit 5
        if [[ "$owner" != "${AUTO_CLAUDE_SESSION_ID:-}" ]]; then
            echo "update_heartbeat: lock owned by '$owner', not us ('${AUTO_CLAUDE_SESSION_ID:-}')" >&2
            exit 6
        fi

        local now tmp
        now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        tmp="$(mktemp "${path}.tmp.XXXXXX")"
        jq --arg hb "$now" '.heartbeat_at = $hb' "$path" > "$tmp"
        # If the lock file disappeared while we were composing the new content
        # (e.g. release_lock between our check and now), don't resurrect it.
        if [[ ! -f "$path" ]]; then
            rm -f "$tmp"
            exit 7
        fi
        mv "$tmp" "$path"
    ) 9>"$serial"
}
