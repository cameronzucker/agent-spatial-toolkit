#!/usr/bin/env bash
# mock_claude.sh — pretend to be `claude -p` for the smoke test.
#
# session_boot.sh invokes `claude -p "<prompt>" --allowedTools ... --permission-mode acceptEdits --output-format json`.
# This mock emulates a well-behaved implementer session by:
#
#   1. Parsing the prompt out of -p and extracting the task_id from it
#      (the prompt contains a line "Task: TASK-XXX — title").
#   2. Performing the trivial work: touch a marker file in tests/smoke/.
#   3. Adding + committing the marker via the safe-git wrapper (so the
#      pre-commit hook + safe-git lease guard are exercised).
#   4. Writing the tests-passed gate marker (.handoff/tests-passed-<task_id>).
#   5. Pushing the branch via safe-git to the bare-repo "remote" set up by
#      the BATS smoke fixture.
#   6. Writing .handoff/mock-pr-<task_id>.txt to record that "a PR would be
#      created here." Real PR creation runs against GitHub via gh; the
#      smoke fixture shadows gh with mock_gh.sh which reads this marker
#      and returns a fake PR number/url so session_exit.sh's gh check
#      passes.
#   7. Emitting --output-format json to stdout so session_boot's caller
#      doesn't see garbage on the captured output stream.
#
# If the prompt does NOT contain a recognizable Task: line, the mock exits
# 1 — we never want a malformed prompt to silently false-pass the smoke
# test.

set -euo pipefail

# --- argv parsing --------------------------------------------------------
prompt=""
while (( $# > 0 )); do
    case "$1" in
        -p)
            shift
            prompt="${1:-}"
            shift || true
            ;;
        --allowedTools|--permission-mode|--output-format|--model)
            # Consume value-bearing flags as no-ops (we don't honor them).
            shift
            shift || true
            ;;
        -p=*|--prompt=*)
            prompt="${1#*=}"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

if [[ -z "$prompt" ]]; then
    echo "mock_claude: no -p prompt argument" >&2
    exit 1
fi

# --- task_id extraction --------------------------------------------------
# session_boot writes a line "Task: $task_id — $task_title" into the prompt.
# Extract the first whitespace token after "Task: ".
task_id=$(printf '%s' "$prompt" | grep -m1 '^Task: ' | sed -E 's/^Task: ([A-Z][A-Z0-9_-]+).*/\1/' | head -n1)

if [[ -z "$task_id" ]]; then
    echo "mock_claude: could not parse task_id from prompt; refusing to fake-pass" >&2
    exit 1
fi

# --- locate repo + safe-git ---------------------------------------------
# session_boot exports AUTO_CLAUDE_REPO_ROOT before invoking us; honor it.
repo_root="${AUTO_CLAUDE_REPO_ROOT:-$(pwd)}"
cd "$repo_root"

safe_git="$repo_root/scripts/auto_claude/safe-git"
if [[ ! -x "$safe_git" ]]; then
    echo "mock_claude: safe-git wrapper missing at $safe_git" >&2
    exit 1
fi

# --- detect current branch (set by session_boot before calling us) -------
current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "DETACHED")
if [[ "$current_branch" == "DETACHED" || "$current_branch" == "main" ]]; then
    echo "mock_claude: expected to be on a feature branch, got '$current_branch'" >&2
    exit 1
fi

# --- the trivial work ----------------------------------------------------
mkdir -p tests/smoke
marker_rel="tests/smoke/marker-$task_id"
echo "smoke marker for $task_id" > "$marker_rel"

# Stage + commit via safe-git so the pre-commit hook fires and the lease
# guard is exercised. safe-git refuses commits when the branch doesn't
# match the lease, so a buggy prior step would fail here loudly.
"$safe_git" add "$marker_rel"
"$safe_git" commit -m "test(smoke): mock impl for $task_id"

# --- tests-passed quality gate ------------------------------------------
mkdir -p .handoff
touch ".handoff/tests-passed-$task_id"

# --- push to the bare-repo "remote" -------------------------------------
"$safe_git" push -u origin "$current_branch"

# --- record a "PR would exist here" marker ------------------------------
# session_exit.sh queries `gh pr list --head <branch>`. The smoke fixture
# shadows `gh` with mock_gh.sh, which reads this marker file to produce a
# fake but valid pr_list response. If we don't write it, mock_gh returns
# an empty list and session_exit correctly classifies the run as
# "no_open_pr" (which is its own valid test case).
cat > ".handoff/mock-pr-$task_id.txt" <<EOF
{
    "task_id": "$task_id",
    "branch": "$current_branch",
    "pr_number": 4242,
    "pr_url": "https://example.invalid/mock/pr/4242"
}
EOF

# --- emit JSON to stdout (matches --output-format json) -----------------
cat <<EOF
{"type":"mock_result","task_id":"$task_id","branch":"$current_branch","exit":0}
EOF

exit 0
