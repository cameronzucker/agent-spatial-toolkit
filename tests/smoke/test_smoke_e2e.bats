#!/usr/bin/env bats
# tests/smoke/test_smoke_e2e.bats
#
# End-to-end smoke validation of the auto-claude framework. Each test
# stands up an isolated test repo (working tree + bare-repo upstream),
# bootstraps the framework into it, seeds a task into state.json, and
# drives the watchdog → session_boot → mock-claude → session_exit
# pipeline through to completion.
#
# This is the "proves the framework is real" suite, complementary to the
# unit-level BATS tests in tests/test_auto_claude.bats. We intentionally
# do not stub internal functions — every layer (lock, state, safe-git,
# pre-commit, audit) executes for real.
#
# The mock_claude.sh stub stands in for the implementer Claude session.
# No real Anthropic API calls are made; cost = $0.
#
# Run with:    bats tests/smoke/test_smoke_e2e.bats

setup() {
    REAL_REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
    SCRIPT_SRC="$REAL_REPO_ROOT/scripts/auto_claude"
    SMOKE_DIR="$BATS_TEST_DIRNAME"

    # Per-test sandbox (bare repo + working repo)
    SMOKE_ROOT=$(mktemp -d "${BATS_TMPDIR:-/tmp}/auto-claude-smoke.XXXXXX")
    SMOKE_UPSTREAM="$SMOKE_ROOT/upstream.git"
    SMOKE_REPO="$SMOKE_ROOT/repo"
    SMOKE_PATH_DIR="$SMOKE_ROOT/path-shadow"

    # 1. Bare upstream
    git init -q --bare -b main "$SMOKE_UPSTREAM"

    # 2. Working repo
    git init -q -b main "$SMOKE_REPO"
    cd "$SMOKE_REPO"
    git config user.email smoke@example.invalid
    git config user.name "Smoke Test"
    git config commit.gpgsign false
    git remote add origin "$SMOKE_UPSTREAM"

    # 3. Mirror auto_claude scripts into the test repo
    mkdir -p "$SMOKE_REPO/scripts/auto_claude/hooks"
    cp "$SCRIPT_SRC"/*.sh "$SMOKE_REPO/scripts/auto_claude/" 2>/dev/null || true
    cp "$SCRIPT_SRC/safe-git" "$SMOKE_REPO/scripts/auto_claude/safe-git"
    cp "$SCRIPT_SRC/hooks/pre-commit" "$SMOKE_REPO/scripts/auto_claude/hooks/pre-commit"
    chmod +x "$SMOKE_REPO/scripts/auto_claude/safe-git"
    chmod +x "$SMOKE_REPO/scripts/auto_claude"/*.sh
    chmod +x "$SMOKE_REPO/scripts/auto_claude/hooks/pre-commit"

    # 4. .handoff scaffold + state template
    mkdir -p "$SMOKE_REPO/.handoff"
    cp "$REAL_REPO_ROOT/.handoff/state.example.json" "$SMOKE_REPO/.handoff/state.example.json"

    # 5. Initial commit on main and push to upstream
    echo "smoke test repo" > README.md
    git add README.md
    git commit -q -m "initial"
    git push -q origin main

    # 6. PATH shadow: claude -> mock_claude.sh, gh -> mock_gh.sh
    mkdir -p "$SMOKE_PATH_DIR"
    ln -s "$SMOKE_DIR/mock_claude.sh" "$SMOKE_PATH_DIR/claude"
    ln -s "$SMOKE_DIR/mock_gh.sh" "$SMOKE_PATH_DIR/gh"

    # 7. Bootstrap the framework. --no-deps keeps install.sh from
    #    requiring a real `claude` on PATH (we hand it one via
    #    AUTO_CLAUDE_CLAUDE_BIN at session boot).
    "$SMOKE_REPO/scripts/auto_claude/install.sh" --no-deps >/dev/null

    # 8. Export environment shared by all tests.
    export AUTO_CLAUDE_REPO_ROOT="$SMOKE_REPO"
    export AUTO_CLAUDE_CLAUDE_BIN="$SMOKE_DIR/mock_claude.sh"
    export PATH="$SMOKE_PATH_DIR:$PATH"
    # Tighten lock-age windows so any stuck-session test doesn't take
    # 10 minutes to detect a stale lock.
    export AUTO_CLAUDE_LOCK_MAX_AGE=2
    export AUTO_CLAUDE_STALE_HARD_AGE=5
}

teardown() {
    cd /
    rm -rf "$SMOKE_ROOT"
}

# ---- helpers ------------------------------------------------------------

# Seed a single pending task into state.json. State.json is gitignored, so
# we write directly to it (no need to commit). state_validate runs
# automatically on the next watchdog tick.
seed_task() {
    local id="$1"
    local branch="$2"
    local title="${3:-smoke task}"
    local now
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    jq --arg id "$id" \
       --arg branch "$branch" \
       --arg title "$title" \
       --arg now "$now" \
       '.tasks = [{
            id: $id,
            title: $title,
            status: "pending",
            branch: $branch,
            head_sha_at_lease_start: null,
            pr_number: null,
            pr_url: null,
            phase: null,
            attempts: 0,
            max_attempts: 3,
            depends_on: [],
            created_at: $now,
            updated_at: $now,
            notes: "smoke test seed"
        }]' "$SMOKE_REPO/.handoff/state.json" > "$SMOKE_REPO/.handoff/state.json.new"
    mv "$SMOKE_REPO/.handoff/state.json.new" "$SMOKE_REPO/.handoff/state.json"
}

# Wait for the spawned session to finish. session_boot runs detached via
# `setsid nohup ... &`, so we must poll until the on-disk side-effects
# settle. We watch for either:
#   (a) state.json shows the task in a terminal status (pr_open/blocked/done),
#   (b) the lock has gone away AND there's a session_exit event,
# or time out. A 30s ceiling gives the mock plenty of headroom; the actual
# work is microseconds.
wait_for_session_complete() {
    local task_id="$1"
    local timeout_s="${2:-30}"
    local deadline=$(( $(date +%s) + timeout_s ))
    while (( $(date +%s) < deadline )); do
        local status
        status=$(jq -r --arg id "$task_id" \
            '.tasks[] | select(.id == $id) | .status' \
            "$SMOKE_REPO/.handoff/state.json" 2>/dev/null || echo "")
        case "$status" in
            pr_open|blocked|done) return 0 ;;
        esac
        # also accept "lock gone + session_exit event for this task"
        if [[ ! -f "$SMOKE_REPO/.handoff/.lock" ]]; then
            local n_exit
            n_exit=$(grep -c '"type":"session_exit"' "$SMOKE_REPO/.handoff/events.jsonl" 2>/dev/null || echo 0)
            n_exit=${n_exit//[^0-9]/}  # strip stray characters from grep -c
            if (( ${n_exit:-0} > 0 )); then
                return 0
            fi
        fi
        sleep 0.2
    done
    echo "wait_for_session_complete: timed out after ${timeout_s}s" >&2
    echo "--- state.json ---" >&2
    cat "$SMOKE_REPO/.handoff/state.json" >&2 2>/dev/null || true
    echo "--- events.jsonl tail ---" >&2
    tail -20 "$SMOKE_REPO/.handoff/events.jsonl" >&2 2>/dev/null || true
    return 1
}

# Count events of a given type.
count_events() {
    local type="$1"
    local n
    n=$(grep -c "\"type\":\"$type\"" "$SMOKE_REPO/.handoff/events.jsonl" 2>/dev/null || echo 0)
    n=${n//[^0-9]/}
    printf '%s' "${n:-0}"
}

# ---- 1. Full happy-path watchdog cycle ---------------------------------

@test "smoke: watchdog full cycle — pending → leased → pr_open" {
    seed_task "TASK-SMOKE1" "feat/smoke-1" "smoke happy path"

    "$SMOKE_REPO/scripts/auto_claude/watchdog.sh"

    wait_for_session_complete "TASK-SMOKE1" 30

    # task transitioned to pr_open
    run jq -r '.tasks[0].status' "$SMOKE_REPO/.handoff/state.json"
    [[ "$status" -eq 0 ]]
    [[ "$output" == "pr_open" ]]

    # PR fields populated from mock_gh
    run jq -r '.tasks[0].pr_number' "$SMOKE_REPO/.handoff/state.json"
    [[ "$output" == "4242" ]]

    # current_lease cleared
    run jq -r '.current_lease' "$SMOKE_REPO/.handoff/state.json"
    [[ "$output" == "null" ]]

    # marker file from mock_claude exists in the working tree
    [[ -f "$SMOKE_REPO/tests/smoke/marker-TASK-SMOKE1" ]]

    # branch was pushed to bare upstream
    run git --git-dir="$SMOKE_UPSTREAM" rev-parse --verify "refs/heads/feat/smoke-1"
    [[ "$status" -eq 0 ]]

    # tests-passed marker was consumed by session_exit (proof the gate fired)
    [[ ! -f "$SMOKE_REPO/.handoff/tests-passed-TASK-SMOKE1" ]]

    # lock is gone
    [[ ! -f "$SMOKE_REPO/.handoff/.lock" ]]

    # event sequence: spawn_intent → spawn_result → session_boot_start →
    # claude_invoke → claude_returned → quality_gates → session_exit
    [[ "$(count_events spawn_intent)" -ge 1 ]]
    [[ "$(count_events spawn_result)" -ge 1 ]]
    [[ "$(count_events session_boot_start)" -ge 1 ]]
    [[ "$(count_events claude_invoke)" -ge 1 ]]
    [[ "$(count_events claude_returned)" -ge 1 ]]
    [[ "$(count_events quality_gates)" -ge 1 ]]
    [[ "$(count_events session_exit)" -ge 1 ]]

    # final_status in quality_gates was pr_open
    last_gates=$(grep '"type":"quality_gates"' "$SMOKE_REPO/.handoff/events.jsonl" | tail -1)
    run jq -r '.final_status' <<<"$last_gates"
    [[ "$output" == "pr_open" ]]

    # reflog snapshot was taken before the spawn
    n_snaps=$(find "$SMOKE_REPO/.handoff/reflog-snapshots" -name '*.reflog' 2>/dev/null | wc -l)
    [[ "$n_snaps" -ge 1 ]]
}

# ---- 2. No tasks → quiescent --------------------------------------------

@test "smoke: empty backlog yields quiescent decision, no spawn" {
    # state.json is fresh out of install.sh — tasks=[]
    "$SMOKE_REPO/scripts/auto_claude/watchdog.sh"

    # Latest watchdog_decision should be quiescent
    last_decision=$(grep '"type":"watchdog_decision"' "$SMOKE_REPO/.handoff/events.jsonl" | tail -1)
    run jq -r '.decision' <<<"$last_decision"
    [[ "$output" == "quiescent" ]]

    # No spawn_intent emitted
    [[ "$(count_events spawn_intent)" -eq 0 ]]

    # No lock left
    [[ ! -f "$SMOKE_REPO/.handoff/.lock" ]]
}

# ---- 3. Stale lock + a pending task: cleanup then spawn -----------------

@test "smoke: watchdog clears a stale lock and proceeds to spawn" {
    seed_task "TASK-STALE" "feat/stale" "stale-lock recovery"

    # Hand-craft a lock that's already aged out beyond STALE_HARD_AGE.
    # boot_id matches current host (so it's not auto-classified as reboot).
    local boot_id
    if [[ -r /proc/sys/kernel/random/boot_id ]]; then
        boot_id="$(cat /proc/sys/kernel/random/boot_id)"
    else
        boot_id="fallback-$(uname -n)"
    fi

    # heartbeat 60s ago — well past STALE_HARD_AGE=5
    local hb
    hb=$(date -u -d '60 seconds ago' +%Y-%m-%dT%H:%M:%SZ)
    cat > "$SMOKE_REPO/.handoff/.lock" <<EOF
{
    "session_id": "stale-from-test",
    "pid": 1,
    "ppid": 0,
    "host": "$(hostname -s 2>/dev/null || echo unknown)",
    "boot_id": "$boot_id",
    "started_at": "$hb",
    "heartbeat_at": "$hb",
    "current_branch": "feat/stale-orphan",
    "current_task_id": "TASK-DEAD",
    "phase": "editing"
}
EOF

    "$SMOKE_REPO/scripts/auto_claude/watchdog.sh"

    # The watchdog should have fired lock_cleared_stale and then proceeded
    # to spawn (or at least gone past consider_spawn).
    [[ "$(count_events lock_cleared_stale)" -ge 1 ]]

    wait_for_session_complete "TASK-STALE" 30

    run jq -r '.tasks[0].status' "$SMOKE_REPO/.handoff/state.json"
    [[ "$output" == "pr_open" ]]

    [[ ! -f "$SMOKE_REPO/.handoff/.lock" ]]
}

# ---- 4. Concurrent watchdogs: only one runs ----------------------------

@test "smoke: two simultaneous watchdog invocations serialize via flock" {
    seed_task "TASK-CONC" "feat/conc" "concurrency check"

    # Start two watchdogs in parallel. flock -n on .watchdog.lock means
    # the second should exit silently with status 0 without doing work.
    "$SMOKE_REPO/scripts/auto_claude/watchdog.sh" &
    pid1=$!
    "$SMOKE_REPO/scripts/auto_claude/watchdog.sh" &
    pid2=$!
    wait "$pid1" "$pid2" 2>/dev/null || true

    wait_for_session_complete "TASK-CONC" 30

    # Exactly one spawn event should have been emitted by the surviving
    # watchdog (the other returned immediately without progressing past
    # the flock).
    [[ "$(count_events spawn_intent)" -eq 1 ]]
    [[ "$(count_events spawn_result)" -eq 1 ]]

    run jq -r '.tasks[0].status' "$SMOKE_REPO/.handoff/state.json"
    [[ "$output" == "pr_open" ]]
}

# ---- 5. Rate limit ------------------------------------------------------

@test "smoke: rate limit refuses spawn when 7 spawn_results in last hour" {
    seed_task "TASK-RATE" "feat/rate" "rate-limit check"

    # Fabricate 7 spawn_result events with timestamps inside the rolling
    # 1-hour window. The watchdog uses `date -u -d '1 hour ago'` as cutoff
    # and counts events with ts > cutoff.
    local now
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    for i in 1 2 3 4 5 6 7; do
        printf '{"ts":"%s","type":"spawn_result","pid":1,"host":"smoke","task_id":"TASK-FAKE-%d"}\n' \
            "$now" "$i" >> "$SMOKE_REPO/.handoff/events.jsonl"
    done

    # Default RATE_LIMIT_PER_HOUR=6, so 7 fabricated + would-be-1-more > 6.
    "$SMOKE_REPO/scripts/auto_claude/watchdog.sh"

    # Expect a rate_limit alert and no new spawn_intent / no lock.
    [[ "$(count_events alert_rate_limit)" -ge 1 ]]
    [[ "$(count_events spawn_intent)" -eq 0 ]]
    [[ ! -f "$SMOKE_REPO/.handoff/.lock" ]]

    # Task still pending (untouched).
    run jq -r '.tasks[0].status' "$SMOKE_REPO/.handoff/state.json"
    [[ "$output" == "pending" ]]
}
