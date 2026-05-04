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

@test "pre-commit hook allows when no lease" {
    source "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/state_helpers.sh"
    state_init
    cp "$AUTO_CLAUDE_REPO_ROOT/scripts/auto_claude/hooks/pre-commit" "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    chmod +x "$AUTO_CLAUDE_REPO_ROOT/.git/hooks/pre-commit"
    cd "$AUTO_CLAUDE_REPO_ROOT"
    echo "y" > foo
    git add foo
    run git commit -m "test no lease"
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
