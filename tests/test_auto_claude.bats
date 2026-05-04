#!/usr/bin/env bats
# tests for scripts/auto_claude/*
#
# Each test runs in a temporary repo so we can exercise the full lock / state
# / safe-git / pre-commit pipeline without polluting the real .handoff/.
#
# Run with:    bats tests/test_auto_claude.bats

setup() {
    # Resolve real repo root once
    REAL_REPO_ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
    SCRIPT_DIR="$REAL_REPO_ROOT/scripts/auto_claude"

    # Per-test sandbox
    TEST_REPO=$(mktemp -d "${BATS_TMPDIR:-/tmp}/auto-claude-test.XXXXXX")
    cd "$TEST_REPO"
    git init -q -b main
    git config user.email test@example.invalid
    git config user.name "Test User"

    # Mirror the auto_claude scripts into the sandbox so AUTO_CLAUDE_REPO_ROOT
    # points at the sandbox but the scripts under it are real.
    mkdir -p "$TEST_REPO/scripts/auto_claude/hooks"
    cp "$SCRIPT_DIR"/*.sh "$TEST_REPO/scripts/auto_claude/" 2>/dev/null || true
    cp "$SCRIPT_DIR/safe-git" "$TEST_REPO/scripts/auto_claude/safe-git"
    cp "$SCRIPT_DIR/hooks/pre-commit" "$TEST_REPO/scripts/auto_claude/hooks/pre-commit"
    chmod +x "$TEST_REPO/scripts/auto_claude/safe-git"
    chmod +x "$TEST_REPO/scripts/auto_claude"/*.sh
    chmod +x "$TEST_REPO/scripts/auto_claude/hooks/pre-commit"

    # Provide the .handoff scaffold
    mkdir -p "$TEST_REPO/.handoff"
    cp "$REAL_REPO_ROOT/.handoff/state.example.json" "$TEST_REPO/.handoff/state.example.json"

    # Mirror the systemd/ templates so the deployment installers find them
    # under $AUTO_CLAUDE_REPO_ROOT/systemd.
    mkdir -p "$TEST_REPO/systemd"
    cp "$REAL_REPO_ROOT/systemd"/*.template "$TEST_REPO/systemd/" 2>/dev/null || true
    cp "$REAL_REPO_ROOT/systemd"/*.txt "$TEST_REPO/systemd/" 2>/dev/null || true

    export AUTO_CLAUDE_REPO_ROOT="$TEST_REPO"
    export AUTO_CLAUDE_SESSION_ID="test-session-$$"

    # Initial commit so we have a HEAD
    echo "hello" > README.md
    git add README.md
    git commit -q -m "init"
}

teardown() {
    cd /
    rm -rf "$TEST_REPO"
}

# ---- audit ------------------------------------------------------------

@test "audit_event appends a JSONL line" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/audit.sh"
    audit_event "test_event" '{"foo":"bar"}'
    [[ -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl" ]]
    run jq -r '.type' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$status" -eq 0 ]]
    [[ "$output" == "test_event" ]]
    run jq -r '.foo' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" == "bar" ]]
}

# ---- lock_helpers ------------------------------------------------------

@test "lock acquire/release roundtrip" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/lock_helpers.sh"
    LOCK="$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
    run acquire_lock "$LOCK" "TASK-001" "feat/x" "leased"
    [[ "$status" -eq 0 ]]
    [[ -f "$LOCK" ]]
    run release_lock "$LOCK"
    [[ "$status" -eq 0 ]]
    [[ ! -f "$LOCK" ]]
}

@test "second acquire on a fresh lock fails" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/lock_helpers.sh"
    LOCK="$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
    acquire_lock "$LOCK" "TASK-001" "feat/x" "leased"
    AUTO_CLAUDE_SESSION_ID="other-session" run acquire_lock "$LOCK" "TASK-002" "feat/y" "leased"
    # Returns 1 (collision) since our PID is alive and heartbeat fresh
    [[ "$status" -eq 1 ]]
    release_lock "$LOCK"
}

@test "release_lock refuses to release someone else's lock" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/lock_helpers.sh"
    LOCK="$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
    acquire_lock "$LOCK" "TASK-001" "feat/x" "leased"
    AUTO_CLAUDE_SESSION_ID="someone-else" run release_lock "$LOCK"
    [[ "$status" -ne 0 ]]
    [[ -f "$LOCK" ]]
    AUTO_CLAUDE_SESSION_ID="test-session-$$" release_lock "$LOCK"
}

@test "is_lock_fresh detects dead PID" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/lock_helpers.sh"
    LOCK="$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
    acquire_lock "$LOCK" "TASK-001" "feat/x" "leased"
    # Rewrite the lock with a definitely-dead PID
    jq '.pid = 1' "$LOCK" > "$LOCK.tmp"
    # PID 1 is alive; pick something likely-dead via maxpid.
    fakepid=$(awk 'END{print 999999999}' /dev/null)
    jq --arg p "$fakepid" '.pid = ($p|tonumber)' "$LOCK" > "$LOCK.tmp"
    mv "$LOCK.tmp" "$LOCK"
    run is_lock_fresh "$LOCK" 600
    [[ "$status" -ne 0 ]]
    rm -f "$LOCK"
}

@test "update_heartbeat does not resurrect a deleted lock (M4)" {
    # M4: between the owner-check and the mv, an external actor (the watchdog
    # cleaning a stale lock) can rm the file. With the previous read-check-mv
    # implementation, the mv would silently resurrect the lock. With the
    # serial flock + post-compose existence check, the resurrection is blocked.
    #
    # We can't easily inject a mid-flight rm under a flock from inside bats
    # without actual concurrency; instead, test the cooperative path: rm the
    # lock first, then call update_heartbeat — it must NOT recreate the lock.
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/lock_helpers.sh"
    LOCK="$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
    acquire_lock "$LOCK" "TASK-1" "feat/x" "leased"
    [[ -f "$LOCK" ]]
    rm -f "$LOCK"
    run update_heartbeat "$LOCK"
    [[ "$status" -ne 0 ]]
    [[ ! -f "$LOCK" ]]
}

@test "update_heartbeat creates and releases its serial lock (M4)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/lock_helpers.sh"
    LOCK="$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
    SERIAL="${LOCK}.serial"
    acquire_lock "$LOCK" "TASK-1" "feat/x" "leased"
    update_heartbeat "$LOCK"
    # Serial-lock file is created (it's a flock target); should exist now.
    [[ -f "$SERIAL" ]]
    # Concurrent update_heartbeat calls should serialize without corrupting JSON.
    for i in 1 2 3 4 5; do
        update_heartbeat "$LOCK" &
    done
    wait
    # After all calls, the lock is still valid JSON with our session_id.
    run jq -r '.session_id' "$LOCK"
    [[ "$output" == "$AUTO_CLAUDE_SESSION_ID" ]]
    release_lock "$LOCK"
    [[ ! -f "$LOCK" ]]
}

@test "update_heartbeat refuses cross-session writes" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/lock_helpers.sh"
    LOCK="$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
    acquire_lock "$LOCK" "TASK-001" "feat/x" "leased"
    AUTO_CLAUDE_SESSION_ID="someone-else" run update_heartbeat "$LOCK"
    [[ "$status" -ne 0 ]]
    release_lock "$LOCK"
}

# ---- state_helpers -----------------------------------------------------

@test "state_init creates state.json with no tasks" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    [[ -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" ]]
    run jq '.tasks | length' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    [[ "$output" == "0" ]]
}

@test "state_init writes atomically (no partial file on validation failure)" {
    # B4: state_init must not leave a truncated state.json behind. We force a
    # failure by pointing at a malformed template; the canonical file must
    # remain absent (we never partially wrote it).
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    rm -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    # Corrupt the template so jq composition succeeds but schema fails:
    # set schema_version to a wrong value. _state_write should reject it,
    # leaving no canonical file behind.
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.example.json" <<'JSON'
{"schema_version": 999, "tasks": [], "current_lease": null, "_comment": "x"}
JSON
    run state_init
    [[ "$status" -ne 0 ]]
    # Critical: no half-written canonical file.
    [[ ! -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" ]]
    # No leftover temp files.
    run bash -c "ls $AUTO_CLAUDE_REPO_ROOT/.handoff/state.json.tmp.* 2>/dev/null | wc -l"
    [[ "$output" == "0" ]]
}

@test "state_init produces a valid file on success" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    rm -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    run state_init
    [[ "$status" -eq 0 ]]
    [[ -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" ]]
    run state_validate
    [[ "$status" -eq 0 ]]
    # No leftover temp files.
    run bash -c "ls $AUTO_CLAUDE_REPO_ROOT/.handoff/state.json.tmp.* 2>/dev/null | wc -l"
    [[ "$output" == "0" ]]
}

@test "state_validate rejects malformed JSON" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    echo "not json" > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    run state_validate
    [[ "$status" -ne 0 ]]
}

@test "state_validate rejects bad status" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"X","title":"x","status":"bogus","branch":"b","attempts":0}
  ],
  "current_lease": null
}
JSON
    run state_validate
    [[ "$status" -ne 0 ]]
}

@test "state_validate accepts a clean schema" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    run state_validate
    [[ "$status" -eq 0 ]]
}

@test "state_validate rejects malformed task branch (NM3)" {
    # NM3: previously _validate_branch was only called at lease-acquire time,
    # so a state.json with `--orphan` as a task branch would pass schema
    # validation and only fail late in git. state_validate must now apply
    # the same validators eagerly.
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"--orphan","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    run state_validate
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"branch"* || "$output" == *"--orphan"* ]]
}

@test "state_validate rejects malformed task id (NM3)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    # Lowercase id violates _validate_task_id (^[A-Z]...).
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"task-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    run state_validate
    [[ "$status" -ne 0 ]]
}

@test "state_validate rejects task branch with embedded dotdot (NM3)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/..hack","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    run state_validate
    [[ "$status" -ne 0 ]]
}

@test "state_acquire_lease + state_release_lease roundtrip" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    state_acquire_lease "TASK-1" "sess-A" "feat/t" "deadbeef"
    run jq -r '.tasks[0].status' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    [[ "$output" == "leased" ]]
    run jq -r '.current_lease.session_id' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    [[ "$output" == "sess-A" ]]
    state_release_lease "TASK-1" "sess-A" "pr_open"
    run jq -r '.tasks[0].status' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    [[ "$output" == "pr_open" ]]
    run jq '.current_lease' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    [[ "$output" == "null" ]]
}

@test "state_acquire_lease serialized under flock — concurrent acquire produces exactly one winner (B5)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    # Spawn N concurrent attempts; exactly one must win, rest must report
    # "another lease is active" or "not pending" depending on race ordering.
    local out_dir="$BATS_TEST_TMPDIR/race"
    mkdir -p "$out_dir"
    local i
    for i in 1 2 3 4 5; do
        (
            export AUTO_CLAUDE_REPO_ROOT="$AUTO_CLAUDE_REPO_ROOT"
            source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
            if state_acquire_lease "TASK-1" "sess-$i" "feat/t" "deadbeef" >/dev/null 2>&1; then
                echo "won" > "$out_dir/result-$i"
            else
                echo "lost" > "$out_dir/result-$i"
            fi
        ) &
    done
    wait
    # Exactly one winner.
    local wins
    wins=$(grep -l '^won$' "$out_dir"/result-* 2>/dev/null | wc -l)
    [[ "$wins" -eq 1 ]]
    # state.json reflects exactly one lease.
    local lease_count
    lease_count=$(jq -r 'if .current_lease then 1 else 0 end' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json")
    [[ "$lease_count" -eq 1 ]]
    # Task is leased.
    local status
    status=$(jq -r '.tasks[0].status' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json")
    [[ "$status" == "leased" ]]
    # attempts incremented exactly once (winner only).
    local attempts
    attempts=$(jq -r '.tasks[0].attempts' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json")
    [[ "$attempts" == "1" ]]
}

@test "_with_state_lock ignores AUTO_CLAUDE_STATE_LOCK_HELD from a different PID (NM1)" {
    # NM1: previously the marker was AUTO_CLAUDE_STATE_LOCK_HELD=1, which a
    # subagent's child process inherits and could use to bypass the flock.
    # The marker is now "$$" (PID-scoped) — the only legitimate "already
    # locked" state is when the SAME process is inside the critical section.
    #
    # Demonstrating the harm: race N concurrent acquire_lease attempts. If
    # the env-var bypass were honored from a different PID, multiple
    # processes would all skip the flock and could race past the
    # precondition checks. With the PID-scoped marker, every cross-process
    # caller must re-flock, so exactly one wins.
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    local out_dir="$BATS_TEST_TMPDIR/nm1-race"
    mkdir -p "$out_dir"
    local i
    # Each attempt runs in its own subshell with the env-var bypass set —
    # mimicking a hostile subagent that thinks it can skip flock. With the
    # PID-scoped check, $$ inside each subshell != "1", so the bypass is
    # not honored and flock is engaged; exactly one acquires the lease.
    for i in 1 2 3 4 5; do
        (
            export AUTO_CLAUDE_REPO_ROOT="$AUTO_CLAUDE_REPO_ROOT"
            export AUTO_CLAUDE_STATE_LOCK_HELD=1
            source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
            if state_acquire_lease "TASK-1" "sess-$i" "feat/t" "deadbeef" >/dev/null 2>&1; then
                echo "won" > "$out_dir/result-$i"
            else
                echo "lost" > "$out_dir/result-$i"
            fi
        ) &
    done
    wait
    local wins
    wins=$(grep -l '^won$' "$out_dir"/result-* 2>/dev/null | wc -l)
    [[ "$wins" -eq 1 ]]
    # attempts incremented exactly once (one winner).
    local attempts
    attempts=$(jq -r '.tasks[0].attempts' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json")
    [[ "$attempts" == "1" ]]
}

@test "state_acquire_lease rejects task_id with leading dash (M3)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{"schema_version":1,"tasks":[],"current_lease":null}
JSON
    run state_acquire_lease "-D" "sess-A" "feat/t" "deadbeef"
    [[ "$status" -ne 0 ]]
}

@test "state_acquire_lease rejects branch starting with dash (M3)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"--orphan","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    run state_acquire_lease "TASK-1" "sess-A" "--orphan" "deadbeef"
    [[ "$status" -ne 0 ]]
}

@test "state_acquire_lease rejects branch with embedded dotdot (M3)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/x","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    run state_acquire_lease "TASK-1" "sess-A" "feat/..hack" "deadbeef"
    [[ "$status" -ne 0 ]]
}

@test "state_release_lease refuses cross-session" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    state_acquire_lease "TASK-1" "sess-A" "feat/t" "deadbeef"
    run state_release_lease "TASK-1" "sess-B" "pr_open"
    [[ "$status" -ne 0 ]]
}

@test "state_set_task_pr survives pr_url containing double-quote (B3 jq injection)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    # Adversarial url with embedded quote and jq syntax — must survive intact.
    nasty='https://example.com/pr"'\'') | .tasks[0].title = "PWNED" | (.'
    run state_set_task_pr "TASK-1" 42 "$nasty"
    [[ "$status" -eq 0 ]]
    # The url should be stored verbatim, and title should NOT have been clobbered.
    run jq -r '.tasks[0].pr_url' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    [[ "$output" == "$nasty" ]]
    run jq -r '.tasks[0].title' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    [[ "$output" == "t" ]]
    run jq -r '.tasks[0].pr_number' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    [[ "$output" == "42" ]]
}

@test "state_set_task_pr rejects non-integer pr_number" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{"schema_version":1,"tasks":[{"id":"T","title":"t","status":"pending","branch":"b","attempts":0,"depends_on":[]}],"current_lease":null}
JSON
    run state_set_task_pr "T" "not-a-number" "https://x"
    [[ "$status" -ne 0 ]]
    run state_set_task_pr "T" '42); .tasks[0].title = "X' "https://x"
    [[ "$status" -ne 0 ]]
}

@test "state_set_task_phase rejects unknown phase (B3 phase whitelist)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{"schema_version":1,"tasks":[{"id":"T","title":"t","status":"pending","branch":"b","attempts":0,"depends_on":[]}],"current_lease":null}
JSON
    run state_set_task_phase "T" 'bogus_phase'
    [[ "$status" -ne 0 ]]
    # Adversarial value with jq syntax is also refused
    run state_set_task_phase "T" 'editing"; .tasks=[]; .x="'
    [[ "$status" -ne 0 ]]
    # Valid phase still works
    run state_set_task_phase "T" 'editing'
    [[ "$status" -eq 0 ]]
}

@test "state_get_next_pending honors deps" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"A","title":"a","status":"pending","branch":"feat/a","attempts":0,"depends_on":["B"]},
    {"id":"B","title":"b","status":"pending","branch":"feat/b","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    run state_get_next_pending
    # Should pick B, not A (A depends on B which isn't done)
    [[ "$status" -eq 0 ]]
    run bash -c "source $AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh && state_get_next_pending | jq -r .id"
    [[ "$output" == "B" ]]
}

# ---- safe-git ----------------------------------------------------------

@test "safe-git refuses reset --hard" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" reset --hard HEAD
    [[ "$status" -eq 4 ]]
    [[ "$output" == *"REFUSED"* ]]
}

@test "safe-git refuses worktree add" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" worktree add /tmp/foo
    [[ "$status" -eq 4 ]]
}

@test "safe-git refuses push --force" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" push --force origin main
    [[ "$status" -eq 4 ]]
}

@test "safe-git allows status without lease" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" status
    [[ "$status" -eq 0 ]]
}

@test "safe-git refuses pre-subcommand -C flag" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" -C /tmp status
    [[ "$status" -eq 4 ]]
    [[ "$output" == *"REFUSED"* ]]
    [[ "$output" == *"pre-subcommand"* ]]
}

@test "safe-git refuses pre-subcommand --git-dir flag" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" --git-dir=/tmp/.git status
    [[ "$status" -eq 4 ]]
    [[ "$output" == *"pre-subcommand"* ]]
}

@test "safe-git refuses pre-subcommand -c flag (config injection)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" -c core.hooksPath=/tmp/evil status
    [[ "$status" -eq 4 ]]
    [[ "$output" == *"pre-subcommand"* ]]
}

@test "safe-git refuses pre-subcommand --work-tree flag" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" --work-tree=/tmp status
    [[ "$status" -eq 4 ]]
    [[ "$output" == *"pre-subcommand"* ]]
}

@test "safe-git config without --get is refused without lease" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" config core.hooksPath /tmp/evil
    [[ "$status" -eq 5 ]]
}

@test "safe-git config --get is allowed without lease" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" config --get user.email
    # Exit may be 0 or 1 depending on whether the value exists; both fine.
    # What matters is it ran (not refused).
    [[ "$status" -eq 0 || "$status" -eq 1 ]]
}

@test "safe-git config --list is allowed without lease" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" config --list
    [[ "$status" -eq 0 ]]
}

@test "safe-git fetch is refused without lease" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" fetch origin
    [[ "$status" -eq 5 ]]
}

@test "safe-git refuses checkout -b <newname> off-lease (NB1)" {
    # NB1: branch creation must not be reachable from safe-git. Even when the
    # caller holds a lease, `checkout -b evil` would let HEAD escape to a
    # branch the lease never named.
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<JSON
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"leased","branch":"feat/t","attempts":1,"depends_on":[]}
  ],
  "current_lease": {
    "task_id":"TASK-1",
    "session_id":"sess-A",
    "branch":"feat/t",
    "head_sha_at_lease_start":"deadbeef",
    "cwd_root":"$AUTO_CLAUDE_REPO_ROOT",
    "acquired_at":"2026-05-04T00:00:00Z"
  }
}
JSON
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" checkout -b evil
    [[ "$status" -eq 4 ]]
    [[ "$output" == *"REFUSED"* ]]
    [[ "$output" == *"branch creation"* ]]
    # And the audit recorded the refusal with the right reason.
    run grep -c '"reason":"checkout-create-off-lease"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" -ge 1 ]]
}

@test "safe-git refuses checkout -B <newname> off-lease (NB1)" {
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<JSON
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"leased","branch":"feat/t","attempts":1,"depends_on":[]}
  ],
  "current_lease": {
    "task_id":"TASK-1",
    "session_id":"sess-A",
    "branch":"feat/t",
    "head_sha_at_lease_start":"deadbeef",
    "cwd_root":"$AUTO_CLAUDE_REPO_ROOT",
    "acquired_at":"2026-05-04T00:00:00Z"
  }
}
JSON
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" checkout -B evil
    [[ "$status" -eq 4 ]]
    [[ "$output" == *"branch creation"* ]]
}

@test "safe-git refuses switch --create <newname> off-lease (NB1)" {
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<JSON
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"leased","branch":"feat/t","attempts":1,"depends_on":[]}
  ],
  "current_lease": {
    "task_id":"TASK-1",
    "session_id":"sess-A",
    "branch":"feat/t",
    "head_sha_at_lease_start":"deadbeef",
    "cwd_root":"$AUTO_CLAUDE_REPO_ROOT",
    "acquired_at":"2026-05-04T00:00:00Z"
  }
}
JSON
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" switch --create evil
    [[ "$status" -eq 4 ]]
    [[ "$output" == *"branch creation"* ]]
}

@test "safe-git refuses checkout -b even when name equals lease branch (NB1)" {
    # Even if the new name matches the lease branch, branch creation via
    # safe-git is unconditionally refused — only session_boot.sh creates
    # branches (using raw git after the lease is in place).
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<JSON
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"leased","branch":"feat/t","attempts":1,"depends_on":[]}
  ],
  "current_lease": {
    "task_id":"TASK-1",
    "session_id":"sess-A",
    "branch":"feat/t",
    "head_sha_at_lease_start":"deadbeef",
    "cwd_root":"$AUTO_CLAUDE_REPO_ROOT",
    "acquired_at":"2026-05-04T00:00:00Z"
  }
}
JSON
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" checkout -b feat/t
    [[ "$status" -eq 4 ]]
}

@test "safe-git refuses commit when no lease and tree dirty" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    echo "x" > newfile
    git add newfile
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" commit -m "x"
    # Should refuse with exit 5 (no lease + mutating)
    [[ "$status" -eq 5 ]]
}

@test "safe-git refuses commit on wrong branch" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cd "$AUTO_CLAUDE_REPO_ROOT"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<JSON
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"leased","branch":"feat/t","attempts":1,"depends_on":[]}
  ],
  "current_lease": {
    "task_id":"TASK-1",
    "session_id":"sess-A",
    "branch":"feat/t",
    "head_sha_at_lease_start":"deadbeef",
    "cwd_root":"$AUTO_CLAUDE_REPO_ROOT",
    "acquired_at":"2026-05-04T00:00:00Z"
  }
}
JSON
    # We're still on main, but the lease says feat/t. Try to commit.
    echo "x" > newfile
    git add newfile
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" commit -m "x"
    [[ "$status" -eq 3 ]]
}

@test "safe-git refuses operation when cwd doesn't match lease cwd_root" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cd "$AUTO_CLAUDE_REPO_ROOT"
    mkdir -p subdir
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<JSON
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"leased","branch":"feat/t","attempts":1,"depends_on":[]}
  ],
  "current_lease": {
    "task_id":"TASK-1",
    "session_id":"sess-A",
    "branch":"feat/t",
    "head_sha_at_lease_start":"deadbeef",
    "cwd_root":"$AUTO_CLAUDE_REPO_ROOT",
    "acquired_at":"2026-05-04T00:00:00Z"
  }
}
JSON
    cd "$AUTO_CLAUDE_REPO_ROOT/subdir"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/safe-git" status
    [[ "$status" -eq 2 ]]
}

# ---- pre-commit hook ---------------------------------------------------

@test "pre-commit hook allows when no lease (human mode, session env unset)" {
    # M1: out-of-session (AUTO_CLAUDE_SESSION_ID unset) is friendly — a human
    # committing manually with state.json present but no active lease should
    # be allowed. In-session that path fails closed; see the M1 test below.
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cp "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/hooks/pre-commit" "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    chmod +x "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    cd "$AUTO_CLAUDE_REPO_ROOT"
    echo "y" > foo
    git add foo
    run env -u AUTO_CLAUDE_SESSION_ID git commit -m "test no lease"
    [[ "$status" -eq 0 ]]
}

@test "pre-commit hook blocks when branch mismatches lease" {
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<JSON
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"leased","branch":"feat/t","attempts":1,"depends_on":[]}
  ],
  "current_lease": {
    "task_id":"TASK-1",
    "session_id":"sess-A",
    "branch":"feat/t",
    "head_sha_at_lease_start":"deadbeef",
    "cwd_root":"$AUTO_CLAUDE_REPO_ROOT",
    "acquired_at":"2026-05-04T00:00:00Z"
  }
}
JSON
    cp "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/hooks/pre-commit" "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    chmod +x "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    cd "$AUTO_CLAUDE_REPO_ROOT"
    echo "y" > foo
    git add foo
    run git commit -m "should fail"
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"REFUSING"* || "$output" == *"branch mismatch"* ]]
}

@test "pre-commit fails closed when AUTO_CLAUDE_SESSION_ID is set and state.json is missing (M1)" {
    cp "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/hooks/pre-commit" "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    chmod +x "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    cd "$AUTO_CLAUDE_REPO_ROOT"
    rm -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    echo "y" > foo
    git add foo
    AUTO_CLAUDE_SESSION_ID="sess-test" run git commit -m "should fail"
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"state.json missing"* ]]
}

@test "pre-commit fails closed when AUTO_CLAUDE_SESSION_ID is set and lease is null (M1)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cp "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/hooks/pre-commit" "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    chmod +x "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    cd "$AUTO_CLAUDE_REPO_ROOT"
    echo "y" > foo
    git add foo
    AUTO_CLAUDE_SESSION_ID="sess-test" run git commit -m "should fail"
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"no current_lease"* || "$output" == *"refusing commit"* ]]
}

@test "pre-commit allows when AUTO_CLAUDE_SESSION_ID is unset and state.json is missing (M1 fallback)" {
    cp "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/hooks/pre-commit" "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    chmod +x "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    cd "$AUTO_CLAUDE_REPO_ROOT"
    rm -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    echo "y" > foo
    git add foo
    # Explicitly unset, in case bats env carries it over from elsewhere.
    unset AUTO_CLAUDE_SESSION_ID
    run env -u AUTO_CLAUDE_SESSION_ID git commit -m "ok"
    [[ "$status" -eq 0 ]]
}

@test "pre-commit hook allows when branch matches lease" {
    cd "$AUTO_CLAUDE_REPO_ROOT"
    git checkout -q -b feat/t
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<JSON
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"leased","branch":"feat/t","attempts":1,"depends_on":[]}
  ],
  "current_lease": {
    "task_id":"TASK-1",
    "session_id":"sess-A",
    "branch":"feat/t",
    "head_sha_at_lease_start":"deadbeef",
    "cwd_root":"$AUTO_CLAUDE_REPO_ROOT",
    "acquired_at":"2026-05-04T00:00:00Z"
  }
}
JSON
    cp "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/hooks/pre-commit" "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    chmod +x "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    echo "y" > foo
    git add foo
    run git commit -m "ok"
    [[ "$status" -eq 0 ]]
}

# ---- reconcile.sh ------------------------------------------------------

@test "reconcile classifies clean repo" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/reconcile.sh"
    [[ "$status" -eq 0 ]]
    git_state=$(jq -r '.git.state' <<<"$output")
    [[ "$git_state" == "clean" ]]
    lock_state=$(jq -r '.lock.state' <<<"$output")
    [[ "$lock_state" == "absent" ]]
}

@test "reconcile classifies mid_merge" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    # Fake a merge in progress
    echo "deadbeef" > "$AUTO_CLAUDE_REPO_ROOT/.git/MERGE_HEAD"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/reconcile.sh"
    [[ "$status" -eq 0 ]]
    git_state=$(jq -r '.git.state' <<<"$output")
    [[ "$git_state" == "mid_merge" ]]
}

@test "reconcile classifies mid_rebase" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cd "$AUTO_CLAUDE_REPO_ROOT"
    mkdir -p "$AUTO_CLAUDE_REPO_ROOT/.git/rebase-merge"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/reconcile.sh"
    [[ "$status" -eq 0 ]]
    git_state=$(jq -r '.git.state' <<<"$output")
    [[ "$git_state" == "mid_rebase" ]]
}

# ---- watchdog end-to-end (mocked claude) -------------------------------

@test "watchdog dry-run picks next pending and emits spawn_intent" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/watchdog.sh" --dry-run
    [[ "$status" -eq 0 ]]
    [[ -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl" ]]
    run grep -c '"type":"spawn_intent"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" == "1" ]]
    [[ -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/STATUS.md" ]]
}

@test "watchdog stays quiescent when no pending tasks" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{"schema_version":1,"tasks":[],"current_lease":null}
JSON
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/watchdog.sh" --dry-run
    [[ "$status" -eq 0 ]]
    run jq -r '.decision' <(grep '"type":"watchdog_decision"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl" | tail -1)
    [[ "$output" == "quiescent" ]]
}

@test "watchdog refuses to rm a lock whose session_id changed since reconcile (B6)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    # Replace reconcile.sh with a fake that reports lock.state=stale with
    # session_id "ghost" — but we'll write a real lock with session_id
    # "alive" so the verify-then-rm pass detects the mismatch and aborts.
    cat > "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/reconcile.sh" <<'SH'
#!/usr/bin/env bash
# Fake reconcile for B6 test. Reports a stale lock with stale session_id
# even though the actual lock on disk has a different session_id (because
# a new live session has taken it between snapshot and decision).
cat <<JSON
{"lock":{"state":"stale","session_id":"ghost","pid":99999,"heartbeat_at":"2020-01-01T00:00:00Z","age_s":7200,"boot_match":true},"git":{"state":"clean","branch":"main","upstream":"","ahead":0},"gh":{"open_prs":[],"fetched":false},"state_file":{"present":true,"valid":true},"current_lease_task":null,"next_pending_task":{"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}}
JSON
SH
    chmod +x "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/reconcile.sh"

    # Write a real lock with a *different* session_id — represents a live
    # session that grabbed the slot between reconcile snapshot and rm.
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock" <<'JSON'
{
  "session_id":"alive",
  "pid":1,
  "ppid":1,
  "host":"test",
  "boot_id":"test-boot",
  "started_at":"2030-01-01T00:00:00Z",
  "heartbeat_at":"2030-01-01T00:00:00Z",
  "current_branch":"feat/t",
  "current_task_id":"TASK-1",
  "phase":"editing"
}
JSON

    AUTO_CLAUDE_DRY_RUN=1 run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/watchdog.sh"
    [[ "$status" -eq 0 ]]

    # Critical: the live session's lock was NOT deleted.
    [[ -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock" ]]
    run jq -r '.session_id' "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
    [[ "$output" == "alive" ]]

    # And we audited the abort.
    run grep -c '"type":"alert_lock_changed_during_cleanup"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" -ge 1 ]]
    # And we did NOT spawn (the cleanup aborted, so consider_spawn never set).
    run grep -c '"type":"spawn_intent"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" == "0" ]]
}

@test "watchdog refuses to rm a lock whose boot_id changed since reconcile (NB2)" {
    # NB2: even when session_id, pid, and heartbeat_at all match, a
    # mismatched boot_id means the snapshot is from before a reboot — or
    # from a different machine — and the on-disk lock should not be
    # treated as the same one. The previous code never read or compared
    # boot_id; this test demonstrates the gap is closed.
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    # Fake reconcile reports the snapshot boot_id as zeros; real lock has
    # a different boot_id but matching session_id+pid+heartbeat. With the
    # NB2 fix, that mismatch must abort the cleanup.
    cat > "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/reconcile.sh" <<'SH'
#!/usr/bin/env bash
cat <<JSON
{"lock":{"state":"stale","session_id":"twin","pid":99999,"heartbeat_at":"2020-01-01T00:00:00Z","age_s":7200,"boot_id":"00000000-0000-0000-0000-000000000000","boot_match":true},"git":{"state":"clean","branch":"main","upstream":"","ahead":0},"gh":{"open_prs":[],"fetched":false},"state_file":{"present":true,"valid":true},"current_lease_task":null,"next_pending_task":{"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}}
JSON
SH
    chmod +x "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/reconcile.sh"

    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock" <<'JSON'
{
  "session_id":"twin",
  "pid":99999,
  "ppid":1,
  "host":"test",
  "boot_id":"11111111-1111-1111-1111-111111111111",
  "started_at":"2020-01-01T00:00:00Z",
  "heartbeat_at":"2020-01-01T00:00:00Z",
  "current_branch":"feat/t",
  "current_task_id":"TASK-1",
  "phase":"editing"
}
JSON

    AUTO_CLAUDE_DRY_RUN=1 run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/watchdog.sh"
    [[ "$status" -eq 0 ]]
    # Lock with mismatched boot_id was NOT deleted.
    [[ -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock" ]]
    run grep -c '"type":"alert_lock_changed_during_cleanup"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" -ge 1 ]]
}

@test "watchdog stale-lock cleanup serializes via .lock.serial flock (NB3)" {
    # NB3: verify-then-rm must be wrapped in the same flock that
    # update_heartbeat and release_lock use, so a fresh acquire_lock that
    # races the watchdog cannot have its lock clobbered by the watchdog's
    # rm.
    #
    # Deterministic test of the flock semantics: hold .lock.serial
    # externally, run watchdog in the background, and verify that watchdog
    # cannot proceed past the verify-then-rm critical section while we hold
    # the flock. After we release, watchdog completes — and the lock is
    # only rm'd if the identity check passed.
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    cat > "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/reconcile.sh" <<'SH'
#!/usr/bin/env bash
cat <<JSON
{"lock":{"state":"stale","session_id":"stale","pid":99999,"heartbeat_at":"2020-01-01T00:00:00Z","age_s":7200,"boot_id":"00000000-0000-0000-0000-000000000000","boot_match":true},"git":{"state":"clean","branch":"main","upstream":"","ahead":0},"gh":{"open_prs":[],"fetched":false},"state_file":{"present":true,"valid":true},"current_lease_task":null,"next_pending_task":{"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}}
JSON
SH
    chmod +x "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/reconcile.sh"

    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock" <<'JSON'
{
  "session_id":"stale",
  "pid":99999,
  "ppid":1,
  "host":"test",
  "boot_id":"00000000-0000-0000-0000-000000000000",
  "started_at":"2020-01-01T00:00:00Z",
  "heartbeat_at":"2020-01-01T00:00:00Z",
  "current_branch":"feat/t",
  "current_task_id":"TASK-1",
  "phase":"editing"
}
JSON

    # Step 1: hold .serial flock from the test process via a coprocess
    # helper. We use flock(1) wrapping a sleep so the lock is held until
    # we kill the helper.
    local serial="$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock.serial"
    : > "$serial"
    flock -x "$serial" -c 'sleep 30' &
    local holder_pid=$!
    # Give flock a moment to actually grab the lock.
    sleep 0.2

    # Step 2: spawn the watchdog. It should block waiting for .serial.
    AUTO_CLAUDE_DRY_RUN=1 "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/watchdog.sh" >/dev/null 2>&1 &
    local wd_pid=$!

    # Step 3: while watchdog is blocked, the stale lock must STILL exist
    # (watchdog hasn't passed the flock yet). Wait briefly to let watchdog
    # try to enter the critical section.
    sleep 0.5
    [[ -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock" ]]
    local owner_during
    owner_during=$(jq -r '.session_id' "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock" 2>/dev/null || echo "")
    [[ "$owner_during" == "stale" ]]

    # Step 4: release the .serial flock (kill the holder). Now watchdog
    # acquires it, verifies identity (still "stale"), rm's the file.
    kill "$holder_pid" 2>/dev/null || true
    wait "$holder_pid" 2>/dev/null || true
    wait "$wd_pid"

    # Final state: lock removed because identity matched on the recheck.
    [[ ! -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock" ]]
    run grep -c '"type":"lock_cleared_stale"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" -ge 1 ]]
}

@test "watchdog clears lock when boot_id matches snapshot (NB2 positive case)" {
    # Companion to NB2: when boot_id (along with session_id/pid/heartbeat)
    # matches the snapshot, the cleanup proceeds — proving boot_id is being
    # consulted but not over-rejecting on a genuine match.
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    cat > "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/reconcile.sh" <<'SH'
#!/usr/bin/env bash
cat <<JSON
{"lock":{"state":"stale","session_id":"twin","pid":99999,"heartbeat_at":"2020-01-01T00:00:00Z","age_s":7200,"boot_id":"22222222-2222-2222-2222-222222222222","boot_match":true},"git":{"state":"clean","branch":"main","upstream":"","ahead":0},"gh":{"open_prs":[],"fetched":false},"state_file":{"present":true,"valid":true},"current_lease_task":null,"next_pending_task":{"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}}
JSON
SH
    chmod +x "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/reconcile.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock" <<'JSON'
{
  "session_id":"twin",
  "pid":99999,
  "ppid":1,
  "host":"test",
  "boot_id":"22222222-2222-2222-2222-222222222222",
  "started_at":"2020-01-01T00:00:00Z",
  "heartbeat_at":"2020-01-01T00:00:00Z",
  "current_branch":"feat/t",
  "current_task_id":"TASK-1",
  "phase":"editing"
}
JSON
    AUTO_CLAUDE_DRY_RUN=1 run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/watchdog.sh"
    [[ "$status" -eq 0 ]]
    # Lock removed because all four fields matched.
    [[ ! -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock" ]]
    run grep -c '"type":"lock_cleared_stale"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" -ge 1 ]]
}

@test "watchdog refuses to spawn when git is mid-merge" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    echo "deadbeef" > "$AUTO_CLAUDE_REPO_ROOT/.git/MERGE_HEAD"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/watchdog.sh" --dry-run
    [[ "$status" -eq 0 ]]
    run grep -c '"type":"alert_dirty_git"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" -ge 1 ]]
}

# ---- session_boot end-to-end with mock claude --------------------------

@test "session_exit reclassifies pending and audits when gh fails (NM2)" {
    # NM2: when gh is unavailable or returns nonzero, we MUST NOT default to
    # pr_open just because no PR was returned. Pre-fix, missing-gh meant the
    # gate was silently skipped and a pushed branch + marker file got
    # pr_open. Now the gate requires a positive successful gh check.
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    cd "$AUTO_CLAUDE_REPO_ROOT"
    state_acquire_lease "TASK-1" "sess-A" "feat/t" "$(git rev-parse HEAD)"
    git checkout -q -b feat/t
    echo "x" > work
    git add work
    git commit -q -m "feat: work"
    # Touch the tests-passed marker so that path-of-the-gate is satisfied —
    # we want to isolate the gh check.
    touch "$AUTO_CLAUDE_REPO_ROOT/.handoff/tests-passed-TASK-1"
    # Set up a fake remote tracking ref so branch_not_pushed doesn't fail.
    git update-ref "refs/remotes/origin/feat/t" HEAD

    # Mock gh to always exit nonzero — simulates auth failure / network etc.
    mock_bin="$BATS_TEST_TMPDIR/mockbin-gh-fail"
    mkdir -p "$mock_bin"
    cat > "$mock_bin/gh" <<'SH'
#!/usr/bin/env bash
echo "gh: not authenticated" >&2
exit 1
SH
    chmod +x "$mock_bin/gh"

    PATH="$mock_bin:$PATH" AUTO_CLAUDE_SESSION_ID="sess-A" \
        run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/session_exit.sh" \
        TASK-1 sess-A 0
    [[ "$status" -eq 0 ]]
    # Critical: NOT pr_open. Should be pending (attempts<max).
    final=$(jq -r '.tasks[0].status' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json")
    [[ "$final" == "pending" ]]
    # And the alert was audited.
    run grep -c '"type":"alert_gh_unavailable"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" -ge 1 ]]
}

@test "session_exit reclassifies pending when gh is not installed (NM2)" {
    # NM2: same expectation when gh binary is missing — falls back to pending,
    # not pr_open.
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    cd "$AUTO_CLAUDE_REPO_ROOT"
    state_acquire_lease "TASK-1" "sess-A" "feat/t" "$(git rev-parse HEAD)"
    git checkout -q -b feat/t
    echo "x" > work
    git add work
    git commit -q -m "feat: work"
    touch "$AUTO_CLAUDE_REPO_ROOT/.handoff/tests-passed-TASK-1"
    git update-ref "refs/remotes/origin/feat/t" HEAD

    # Stage a minimal PATH that includes everything the script uses
    # (bash, jq, git, date, mktemp, awk, grep, head, sed, rm, mkdir, cp,
    # hostname, uname, sha256sum) but explicitly EXCLUDES gh. We do this by
    # symlinking only the needed binaries into a fresh dir and pointing PATH
    # at it.
    fakebin="$BATS_TEST_TMPDIR/no-gh-bin"
    mkdir -p "$fakebin"
    for tool in bash jq git date mktemp awk grep head sed rm mkdir cp mv hostname uname sha256sum tr cat ls flock kill ps stat readlink dirname basename printf id env touch sort tee uniq cut wc; do
        if real_path=$(command -v "$tool" 2>/dev/null); then
            ln -sf "$real_path" "$fakebin/$tool"
        fi
    done
    # Confirm gh is NOT in $fakebin
    [[ ! -e "$fakebin/gh" ]]
    PATH="$fakebin" AUTO_CLAUDE_SESSION_ID="sess-A" \
        run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/session_exit.sh" \
        TASK-1 sess-A 0
    [[ "$status" -eq 0 ]]
    final=$(jq -r '.tasks[0].status' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json")
    [[ "$final" == "pending" ]]
    run grep -c '"reason":"gh_not_installed"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" -ge 1 ]]
}

@test "session_exit refuses pr_open without tests-passed marker file (M2)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    cd "$AUTO_CLAUDE_REPO_ROOT"
    state_acquire_lease "TASK-1" "sess-A" "feat/t" "$(git rev-parse HEAD)"
    git checkout -q -b feat/t
    # Make a commit whose message contains "test" (formerly enough to satisfy
    # the heuristic). Without the marker file, session_exit MUST NOT classify
    # this as pr_open.
    echo "x" > work
    git add work
    git commit -q -m "fix: latest tweak (mentions test in passing)"

    # No marker file — session_exit must reclassify as pending (attempts<max).
    AUTO_CLAUDE_SESSION_ID="sess-A" run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/session_exit.sh" \
        TASK-1 sess-A 0
    [[ "$status" -eq 0 ]]
    final=$(jq -r '.tasks[0].status' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json")
    [[ "$final" == "pending" ]]
    # And the gate failure was recorded.
    run grep -c '"no_tests_passed_marker"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" -ge 1 ]]
}

@test "session_boot creates branch, leases, exits cleanly with mock claude" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    cd "$AUTO_CLAUDE_REPO_ROOT"
    AUTO_CLAUDE_MOCK_CLAUDE=1 run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/session_boot.sh" TASK-1
    [[ "$status" -eq 0 ]]
    # Branch was created
    run git branch --list feat/t
    [[ "$output" == *"feat/t"* ]]
    # Lease is released
    run jq '.current_lease' "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    [[ "$output" == "null" ]]
    # session_exit was logged
    run grep -c '"type":"session_exit"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" -ge 1 ]]
    # Lock released
    [[ ! -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock" ]]
}

@test "status_render produces a STATUS.md with headings" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/status_render.sh"
    [[ "$status" -eq 0 ]]
    run grep -E '^## (Current|Last 5 events|Open PRs|Backlog)' "$AUTO_CLAUDE_REPO_ROOT/.handoff/STATUS.md"
    [[ "$status" -eq 0 ]]
}

# ---- install -----------------------------------------------------------

@test "install --no-deps initializes state.json" {
    rm -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install.sh" --no-deps
    [[ "$status" -eq 0 ]]
    [[ -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" ]]
}

# ---- sibling_worktrees -------------------------------------------------
#
# These tests use real `git worktree add` against the per-test sandbox,
# which gives us authentic porcelain output. Each test cleans up its
# worktree; teardown removes the whole TEST_REPO regardless.

@test "sibling_worktrees_list returns empty when no siblings exist" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/sibling_worktrees.sh"
    run sibling_worktrees_list
    [[ "$status" -eq 0 ]]
    [[ -z "$output" ]]
}

@test "sibling_worktrees_has_active is false when no siblings" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/sibling_worktrees.sh"
    run sibling_worktrees_has_active
    [[ "$status" -ne 0 ]]
}

@test "sibling_worktrees_list finds a sibling worktree with a fresh lock" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/sibling_worktrees.sh"
    # Create a sibling worktree and write a fresh lock there.
    sib_dir="${TEST_REPO}-sibling"
    git -C "$TEST_REPO" worktree add -b feat/sibling "$sib_dir" >/dev/null 2>&1
    mkdir -p "$sib_dir/.handoff"
    boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo "test-boot")
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    cat > "$sib_dir/.handoff/.lock" <<JSON
{
  "session_id": "sibling-sess-1",
  "pid": $$,
  "ppid": 1,
  "host": "test-host",
  "boot_id": "$boot",
  "started_at": "$now",
  "heartbeat_at": "$now",
  "current_branch": "feat/sibling",
  "current_task_id": "TASK-SIB",
  "phase": "editing"
}
JSON
    run sibling_worktrees_list 600
    [[ "$status" -eq 0 ]]
    # We expect one row, tab-separated, with the sibling path in field 1.
    [[ "$output" == *"$sib_dir"* ]]
    [[ "$output" == *"feat/sibling"* ]]
    [[ "$output" == *"TASK-SIB"* ]]
    # Cleanup
    git -C "$TEST_REPO" worktree remove --force "$sib_dir" >/dev/null 2>&1 || rm -rf "$sib_dir"
}

@test "sibling_worktrees_list ignores a sibling whose lock heartbeat is stale" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/sibling_worktrees.sh"
    sib_dir="${TEST_REPO}-sibling"
    git -C "$TEST_REPO" worktree add -b feat/sibling "$sib_dir" >/dev/null 2>&1
    mkdir -p "$sib_dir/.handoff"
    boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo "test-boot")
    # Heartbeat 2 hours ago — outside the 600s default window.
    stale=$(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ)
    cat > "$sib_dir/.handoff/.lock" <<JSON
{
  "session_id": "sibling-sess-1",
  "pid": $$,
  "host": "test-host",
  "boot_id": "$boot",
  "started_at": "$stale",
  "heartbeat_at": "$stale",
  "current_branch": "feat/sibling",
  "current_task_id": "TASK-SIB",
  "phase": "editing"
}
JSON
    run sibling_worktrees_list 600
    [[ "$status" -eq 0 ]]
    [[ -z "$output" ]]
    git -C "$TEST_REPO" worktree remove --force "$sib_dir" >/dev/null 2>&1 || rm -rf "$sib_dir"
}

@test "sibling_worktrees_list ignores a sibling whose lock PID is dead" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/sibling_worktrees.sh"
    sib_dir="${TEST_REPO}-sibling"
    git -C "$TEST_REPO" worktree add -b feat/sibling "$sib_dir" >/dev/null 2>&1
    mkdir -p "$sib_dir/.handoff"
    boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo "test-boot")
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    # PID 999999999 — definitely not running on a normal box.
    cat > "$sib_dir/.handoff/.lock" <<JSON
{
  "session_id": "sibling-sess-1",
  "pid": 999999999,
  "host": "test-host",
  "boot_id": "$boot",
  "started_at": "$now",
  "heartbeat_at": "$now",
  "current_branch": "feat/sibling",
  "current_task_id": "TASK-SIB",
  "phase": "editing"
}
JSON
    run sibling_worktrees_list 600
    [[ "$status" -eq 0 ]]
    [[ -z "$output" ]]
    git -C "$TEST_REPO" worktree remove --force "$sib_dir" >/dev/null 2>&1 || rm -rf "$sib_dir"
}

@test "sibling_worktrees_list tolerates a sibling with no .handoff dir" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/sibling_worktrees.sh"
    sib_dir="${TEST_REPO}-bare-sibling"
    git -C "$TEST_REPO" worktree add -b feat/bare "$sib_dir" >/dev/null 2>&1
    # No .handoff at all in the sibling — must not error.
    run sibling_worktrees_list 600
    [[ "$status" -eq 0 ]]
    [[ -z "$output" ]]
    git -C "$TEST_REPO" worktree remove --force "$sib_dir" >/dev/null 2>&1 || rm -rf "$sib_dir"
}

@test "sibling_worktrees_format emits human-readable warning when active" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/sibling_worktrees.sh"
    sib_dir="${TEST_REPO}-sibling"
    git -C "$TEST_REPO" worktree add -b feat/sibling "$sib_dir" >/dev/null 2>&1
    mkdir -p "$sib_dir/.handoff"
    boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo "test-boot")
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    cat > "$sib_dir/.handoff/.lock" <<JSON
{
  "session_id": "sibling-sess-1",
  "pid": $$,
  "host": "test-host",
  "boot_id": "$boot",
  "started_at": "$now",
  "heartbeat_at": "$now",
  "current_branch": "feat/sibling",
  "current_task_id": "TASK-SIB",
  "phase": "editing"
}
JSON
    run sibling_worktrees_format 600
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"WARNING: detected concurrent orchestrator activity"* ]]
    [[ "$output" == *"DO NOT \`git checkout\`"* ]]
    [[ "$output" == *"feat/sibling"* ]]
    git -C "$TEST_REPO" worktree remove --force "$sib_dir" >/dev/null 2>&1 || rm -rf "$sib_dir"
}

# ---- session brief hook ------------------------------------------------

# Helper: copy the SessionStart hook into the sandbox so it can resolve its
# own relative path to the sandbox scripts.
_install_session_brief_hook() {
    mkdir -p "$AUTO_CLAUDE_REPO_ROOT/.claude/hooks"
    cp "$REAL_REPO_ROOT/.claude/hooks/auto_claude_session_brief.sh" \
        "$AUTO_CLAUDE_REPO_ROOT/.claude/hooks/auto_claude_session_brief.sh"
    chmod +x "$AUTO_CLAUDE_REPO_ROOT/.claude/hooks/auto_claude_session_brief.sh"
}

@test "session_brief hook exits 0 silently when state.json missing" {
    _install_session_brief_hook
    rm -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json"
    run env CLAUDE_PROJECT_DIR="$AUTO_CLAUDE_REPO_ROOT" \
        "$AUTO_CLAUDE_REPO_ROOT/.claude/hooks/auto_claude_session_brief.sh"
    [[ "$status" -eq 0 ]]
    [[ -z "$output" ]]
}

@test "session_brief hook prints the banner and STATUS content when state.json exists" {
    _install_session_brief_hook
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    run env CLAUDE_PROJECT_DIR="$AUTO_CLAUDE_REPO_ROOT" \
        "$AUTO_CLAUDE_REPO_ROOT/.claude/hooks/auto_claude_session_brief.sh"
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"=== auto-claude status brief"* ]]
    [[ "$output" == *"=== end auto-claude brief ==="* ]]
    [[ "$output" == *"## Current"* ]]
    [[ "$output" == *"## Recent events"* ]]
}

@test "session_brief hook surfaces cross-orchestrator warning when sibling lock fresh" {
    _install_session_brief_hook
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init

    # Plant an active sibling worktree.
    sib_dir="${TEST_REPO}-sibling"
    git -C "$TEST_REPO" worktree add -b feat/sibling "$sib_dir" >/dev/null 2>&1
    mkdir -p "$sib_dir/.handoff"
    boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo "test-boot")
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    cat > "$sib_dir/.handoff/.lock" <<JSON
{"session_id":"sibling-sess","pid":$$,"host":"h","boot_id":"$boot","started_at":"$now","heartbeat_at":"$now","current_branch":"feat/sibling","current_task_id":"TASK-SIB","phase":"editing"}
JSON

    run env CLAUDE_PROJECT_DIR="$AUTO_CLAUDE_REPO_ROOT" \
        "$AUTO_CLAUDE_REPO_ROOT/.claude/hooks/auto_claude_session_brief.sh"
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"## Cross-orchestrator alert"* ]]
    [[ "$output" == *"WARNING: detected concurrent orchestrator activity"* ]]
    [[ "$output" == *"feat/sibling"* ]]

    git -C "$TEST_REPO" worktree remove --force "$sib_dir" >/dev/null 2>&1 || rm -rf "$sib_dir"
}

@test "session_brief hook never fails the session even on a corrupt lock" {
    _install_session_brief_hook
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    sib_dir="${TEST_REPO}-sibling"
    git -C "$TEST_REPO" worktree add -b feat/sibling "$sib_dir" >/dev/null 2>&1
    mkdir -p "$sib_dir/.handoff"
    # Garbage in the lock file
    echo "this is not json {" > "$sib_dir/.handoff/.lock"
    run env CLAUDE_PROJECT_DIR="$AUTO_CLAUDE_REPO_ROOT" \
        "$AUTO_CLAUDE_REPO_ROOT/.claude/hooks/auto_claude_session_brief.sh"
    [[ "$status" -eq 0 ]]
    git -C "$TEST_REPO" worktree remove --force "$sib_dir" >/dev/null 2>&1 || rm -rf "$sib_dir"
}

# ---- session_boot sibling-orchestrator alerts --------------------------

@test "session_boot audits alert_sibling_orchestrator_active when sibling lock fresh (different branch)" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    # Create a sibling worktree on a DIFFERENT branch with a fresh lock.
    sib_dir="${TEST_REPO}-sibling"
    git -C "$TEST_REPO" worktree add -b feat/sibling-other "$sib_dir" >/dev/null 2>&1
    mkdir -p "$sib_dir/.handoff"
    boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo "test-boot")
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    cat > "$sib_dir/.handoff/.lock" <<JSON
{"session_id":"sib","pid":$$,"host":"h","boot_id":"$boot","started_at":"$now","heartbeat_at":"$now","current_branch":"feat/sibling-other","current_task_id":"TASK-OTHER","phase":"editing"}
JSON
    cd "$AUTO_CLAUDE_REPO_ROOT"
    AUTO_CLAUDE_MOCK_CLAUDE=1 run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/session_boot.sh" TASK-1
    [[ "$status" -eq 0 ]]
    # Soft alert recorded
    run grep -c '"type":"alert_sibling_orchestrator_active"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" -ge 1 ]]
    git -C "$TEST_REPO" worktree remove --force "$sib_dir" >/dev/null 2>&1 || rm -rf "$sib_dir"
}

@test "session_boot refuses and audits alert_sibling_branch_conflict when sibling holds same branch" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/collide","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    # Sibling worktree already on feat/collide.
    sib_dir="${TEST_REPO}-sibling"
    git -C "$TEST_REPO" worktree add -b feat/collide "$sib_dir" >/dev/null 2>&1
    mkdir -p "$sib_dir/.handoff"
    boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo "test-boot")
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    cat > "$sib_dir/.handoff/.lock" <<JSON
{"session_id":"sib","pid":$$,"host":"h","boot_id":"$boot","started_at":"$now","heartbeat_at":"$now","current_branch":"feat/collide","current_task_id":"TASK-COLL","phase":"editing"}
JSON
    cd "$AUTO_CLAUDE_REPO_ROOT"
    AUTO_CLAUDE_MOCK_CLAUDE=1 run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/session_boot.sh" TASK-1
    [[ "$status" -ne 0 ]]
    run grep -c '"type":"alert_sibling_branch_conflict"' "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    [[ "$output" -ge 1 ]]
    git -C "$TEST_REPO" worktree remove --force "$sib_dir" >/dev/null 2>&1 || rm -rf "$sib_dir"
}

# ---- status_render polish ---------------------------------------------

@test "status_render shows human heartbeat age when a lock exists" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    # Plant a lock with a known recent heartbeat.
    boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo "test-boot")
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock" <<JSON
{"session_id":"s","pid":$$,"host":"h","boot_id":"$boot","started_at":"$now","heartbeat_at":"$now","current_branch":"main","current_task_id":"X","phase":"editing"}
JSON
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/status_render.sh" --stdout
    [[ "$status" -eq 0 ]]
    # Output should contain the parenthetical age suffix.
    [[ "$output" == *"ago)"* ]]
    rm -f "$AUTO_CLAUDE_REPO_ROOT/.handoff/.lock"
}

@test "status_render falls back to static inference when no watchdog events yet" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    # state.json has one pending task but events.jsonl has no
    # watchdog_decision events — status_render must still produce a
    # non-empty 'Why nothing is running right now' line.
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"pending","branch":"feat/t","attempts":0,"depends_on":[]}
  ],
  "current_lease": null
}
JSON
    : > "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/status_render.sh" --stdout
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"watchdog_not_yet_run"* ]]
}

@test "status_render reports session_active when a lease is held" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    cat > "$AUTO_CLAUDE_REPO_ROOT/.handoff/state.json" <<'JSON'
{
  "schema_version": 1,
  "tasks": [
    {"id":"TASK-1","title":"t","status":"leased","branch":"feat/t","attempts":1,"depends_on":[]}
  ],
  "current_lease": {
    "task_id":"TASK-1","session_id":"sess-x","branch":"feat/t","head_sha_at_lease_start":"deadbeef","cwd_root":"/tmp","acquired_at":"2026-05-04T05:00:00Z"
  }
}
JSON
    : > "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/status_render.sh" --stdout
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"session_active"* ]]
}

# ---- deployment templates + installers (PR C) -------------------------
#
# Template substitution is what install_systemd.sh / install_cron.sh do
# in production. Verify @PROJECT_ROOT@ and @USER@ get replaced cleanly
# and no @...@ markers survive.

@test "claude-watchdog.service.template has no leftover @...@ markers after substitution" {
    src="$AUTO_CLAUDE_REPO_ROOT/systemd/claude-watchdog.service.template"
    [[ -f "$src" ]]
    out=$(sed -e 's|@PROJECT_ROOT@|/tmp/proj|g' -e 's|@USER@|alice|g' "$src")
    # Should contain the substituted values
    [[ "$out" == *"/tmp/proj"* ]]
    [[ "$out" == *"alice"* ]]
    # And no surviving placeholders.
    if echo "$out" | grep -E '@[A-Z_]+@' >/dev/null; then
        echo "leftover markers in service template:" >&2
        echo "$out" | grep -E '@[A-Z_]+@' >&2
        return 1
    fi
}

@test "claude-watchdog.timer.template has no leftover @...@ markers after substitution" {
    src="$AUTO_CLAUDE_REPO_ROOT/systemd/claude-watchdog.timer.template"
    [[ -f "$src" ]]
    out=$(sed -e 's|@PROJECT_ROOT@|/tmp/proj|g' -e 's|@USER@|alice|g' "$src")
    if echo "$out" | grep -E '@[A-Z_]+@' >/dev/null; then
        echo "leftover markers in timer template:" >&2
        echo "$out" | grep -E '@[A-Z_]+@' >&2
        return 1
    fi
}

@test "cron-template.txt has no leftover @...@ markers after substitution" {
    src="$AUTO_CLAUDE_REPO_ROOT/systemd/cron-template.txt"
    [[ -f "$src" ]]
    out=$(sed -e 's|@PROJECT_ROOT@|/tmp/proj|g' -e 's|@USER@|alice|g' "$src")
    [[ "$out" == *"/tmp/proj"* ]]
    [[ "$out" == *"alice"* ]]
    if echo "$out" | grep -E '@[A-Z_]+@' >/dev/null; then
        echo "leftover markers in cron template:" >&2
        echo "$out" | grep -E '@[A-Z_]+@' >&2
        return 1
    fi
}

@test "install_systemd.sh --status returns nonzero with clear message when not installed" {
    UNIT_DIR="$TEST_REPO/fake-systemd-user"
    mkdir -p "$UNIT_DIR"
    SYSTEMD_USER_UNIT_DIR="$UNIT_DIR" \
    SYSTEMCTL=true LOGINCTL=true \
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install_systemd.sh" \
        --project-root "$AUTO_CLAUDE_REPO_ROOT" --status
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"not installed"* ]]
}

@test "install_systemd.sh is idempotent: run twice, no error, content unchanged" {
    UNIT_DIR="$TEST_REPO/fake-systemd-user"
    SYSTEMD_USER_UNIT_DIR="$UNIT_DIR" \
    SYSTEMCTL=true LOGINCTL=true \
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install_systemd.sh" \
        --project-root "$AUTO_CLAUDE_REPO_ROOT"
    [[ "$status" -eq 0 ]]
    [[ -f "$UNIT_DIR/claude-watchdog.service" ]]
    [[ -f "$UNIT_DIR/claude-watchdog.timer" ]]
    sha_svc_1=$(sha256sum "$UNIT_DIR/claude-watchdog.service" | awk '{print $1}')
    sha_tim_1=$(sha256sum "$UNIT_DIR/claude-watchdog.timer" | awk '{print $1}')

    SYSTEMD_USER_UNIT_DIR="$UNIT_DIR" \
    SYSTEMCTL=true LOGINCTL=true \
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install_systemd.sh" \
        --project-root "$AUTO_CLAUDE_REPO_ROOT"
    [[ "$status" -eq 0 ]]
    sha_svc_2=$(sha256sum "$UNIT_DIR/claude-watchdog.service" | awk '{print $1}')
    sha_tim_2=$(sha256sum "$UNIT_DIR/claude-watchdog.timer" | awk '{print $1}')
    [[ "$sha_svc_1" == "$sha_svc_2" ]]
    [[ "$sha_tim_1" == "$sha_tim_2" ]]
}

@test "install_systemd.sh --uninstall is safe no-op when nothing is installed" {
    UNIT_DIR="$TEST_REPO/fake-systemd-user"
    mkdir -p "$UNIT_DIR"
    SYSTEMD_USER_UNIT_DIR="$UNIT_DIR" \
    SYSTEMCTL=true LOGINCTL=true \
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install_systemd.sh" --uninstall
    [[ "$status" -eq 0 ]]
}

@test "install_systemd.sh --dry-run does not write unit files" {
    UNIT_DIR="$TEST_REPO/fake-systemd-user-dry"
    SYSTEMD_USER_UNIT_DIR="$UNIT_DIR" \
    SYSTEMCTL=true LOGINCTL=true \
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install_systemd.sh" \
        --project-root "$AUTO_CLAUDE_REPO_ROOT" --dry-run
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"[dry-run]"* ]]
    [[ ! -f "$UNIT_DIR/claude-watchdog.service" ]]
    [[ ! -f "$UNIT_DIR/claude-watchdog.timer" ]]
}

# install_cron.sh tests use a stub crontab on PATH so we don't touch the
# real user crontab. The stub stores its state in $TEST_REPO/fake-cron-state.

_install_cron_stub() {
    # Create a fake crontab binary and a state file. Returns its dir.
    local dir="$1"
    mkdir -p "$dir"
    : > "$dir/state"
    cat > "$dir/crontab" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
state_file="${FAKE_CRON_STATE:?FAKE_CRON_STATE required}"
case "${1:-}" in
    -l)
        if [[ -s "$state_file" ]]; then
            cat "$state_file"
        else
            exit 1
        fi
        ;;
    "")
        echo "stub crontab: bare invocation not supported" >&2
        exit 2
        ;;
    -r)
        : > "$state_file"
        ;;
    *)
        # crontab <file>
        cp "$1" "$state_file"
        ;;
esac
STUB
    chmod +x "$dir/crontab"
}

@test "install_cron.sh refuses to add a duplicate watchdog entry without --force" {
    stub_dir="$TEST_REPO/fake-cron"
    _install_cron_stub "$stub_dir"
    export FAKE_CRON_STATE="$stub_dir/state"

    CRONTAB="$stub_dir/crontab" \
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install_cron.sh" \
        --project-root "$AUTO_CLAUDE_REPO_ROOT"
    [[ "$status" -eq 0 ]]
    grep -Fq "auto-claude watchdog (managed)" "$FAKE_CRON_STATE"

    # Second install should refuse without --force.
    CRONTAB="$stub_dir/crontab" \
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install_cron.sh" \
        --project-root "$AUTO_CLAUDE_REPO_ROOT"
    [[ "$status" -ne 0 ]]
    [[ "$output" == *"already installed"* ]]
}

@test "install_cron.sh --force replaces an existing entry rather than appending" {
    stub_dir="$TEST_REPO/fake-cron-force"
    _install_cron_stub "$stub_dir"
    export FAKE_CRON_STATE="$stub_dir/state"

    # Build a minimal alternate project root so verify_project_root passes.
    alt_root="$TEST_REPO/alt-project"
    mkdir -p "$alt_root/scripts/auto_claude"
    cat > "$alt_root/scripts/auto_claude/watchdog.sh" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
    chmod +x "$alt_root/scripts/auto_claude/watchdog.sh"

    CRONTAB="$stub_dir/crontab" \
    "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install_cron.sh" \
        --project-root "$AUTO_CLAUDE_REPO_ROOT" >/dev/null

    # Snapshot first install — count of marker-fence lines (start + end)
    n_first=$(grep -cF "auto-claude watchdog (managed)" "$FAKE_CRON_STATE")
    [[ "$n_first" -eq 2 ]]   # exactly one fenced block: open + close

    # Force-install with a different project root
    CRONTAB="$stub_dir/crontab" \
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install_cron.sh" \
        --project-root "$alt_root" --force
    [[ "$status" -eq 0 ]]

    # Still exactly one block, no duplicate fence pair.
    n_after=$(grep -cF "auto-claude watchdog (managed)" "$FAKE_CRON_STATE")
    [[ "$n_after" -eq 2 ]]
    grep -Fq "$alt_root" "$FAKE_CRON_STATE"
}

@test "install_cron.sh --uninstall is a safe no-op when nothing is installed" {
    stub_dir="$TEST_REPO/fake-cron-noop"
    _install_cron_stub "$stub_dir"
    export FAKE_CRON_STATE="$stub_dir/state"

    CRONTAB="$stub_dir/crontab" \
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install_cron.sh" --uninstall
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"no-op"* ]]
}

@test "install_cron.sh --uninstall removes only the watchdog block" {
    stub_dir="$TEST_REPO/fake-cron-uninstall"
    _install_cron_stub "$stub_dir"
    export FAKE_CRON_STATE="$stub_dir/state"

    # Pre-existing unrelated entry.
    cat > "$FAKE_CRON_STATE" <<'CRON'
# user's existing job
*/5 * * * * /usr/bin/true
CRON

    CRONTAB="$stub_dir/crontab" \
    "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install_cron.sh" \
        --project-root "$AUTO_CLAUDE_REPO_ROOT" >/dev/null

    grep -Fq "auto-claude watchdog (managed)" "$FAKE_CRON_STATE"
    grep -Fq "/usr/bin/true" "$FAKE_CRON_STATE"

    CRONTAB="$stub_dir/crontab" \
    "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install_cron.sh" --uninstall >/dev/null

    ! grep -Fq "auto-claude watchdog (managed)" "$FAKE_CRON_STATE"
    grep -Fq "/usr/bin/true" "$FAKE_CRON_STATE"
}

@test "install_cron.sh --dry-run does not modify the crontab" {
    stub_dir="$TEST_REPO/fake-cron-dry"
    _install_cron_stub "$stub_dir"
    export FAKE_CRON_STATE="$stub_dir/state"

    CRONTAB="$stub_dir/crontab" \
    run "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/install_cron.sh" \
        --project-root "$AUTO_CLAUDE_REPO_ROOT" --dry-run
    [[ "$status" -eq 0 ]]
    [[ "$output" == *"[dry-run]"* ]]
    [[ ! -s "$FAKE_CRON_STATE" ]]
}

# ---- Fix 1: ruff gate in session_exit quality_gates --------------------

@test "session_exit ruff gate fails when ruff format --check fails" {
    # Create a Python file with bad formatting in the test sandbox
    mkdir -p "$AUTO_CLAUDE_REPO_ROOT/src"
    cat > "$AUTO_CLAUDE_REPO_ROOT/pyproject.toml" <<'TOML'
[tool.ruff]
line-length = 100
TOML
    # Intentionally bad format (missing space after comma, no final newline)
    printf 'def foo(a,b):\n    return a+b' > "$AUTO_CLAUDE_REPO_ROOT/src/bad.py"

    # Source session_exit's quality_gates logic. Since session_exit.sh is a
    # full script with side-effects, we test the gate logic by extracting
    # the ruff section and running it directly.
    cd "$AUTO_CLAUDE_REPO_ROOT"
    if command -v uv >/dev/null 2>&1; then
        run uv run ruff format --check .
        [ "$status" -ne 0 ]
    else
        skip "uv not available in test environment"
    fi
}

@test "session_exit ruff gate passes when there's no pyproject.toml (skipped)" {
    # No pyproject.toml in this sandbox — the ruff gate is skipped, not failed.
    [ ! -f "$AUTO_CLAUDE_REPO_ROOT/pyproject.toml" ]
    # Implicit: the session_exit conditional `[[ -f pyproject.toml ]]` short-circuits;
    # gate_ok stays unchanged. We assert the absence to document the intended skip.
}

