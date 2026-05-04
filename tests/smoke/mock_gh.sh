#!/usr/bin/env bash
# mock_gh.sh — pretend to be `gh` for the smoke test.
#
# session_exit.sh calls `gh pr list --head <branch> --state open --json number,url --limit 1`.
# A real `gh` would talk to GitHub. In the smoke fixture we have only a
# local bare repo, so we substitute this stub on PATH ahead of the real gh.
#
# We support exactly the surfaces the framework exercises:
#
#   gh pr list --head <branch> --state open --json ... --limit ...
#       -> reads .handoff/mock-pr-<task_id>.txt files in the repo. If
#          a marker exists for the requested branch, emit a [{...}]
#          JSON array; otherwise emit []. Exit 0 either way (we are
#          "authenticated" — the question is whether a PR exists).
#
#   gh --version / gh auth status -> exit 0
#
# Anything else we haven't taught it -> exit 1, so a misconfigured test
# fails loudly rather than silently passing.

set -euo pipefail

if [[ "${1:-}" == "--version" ]]; then
    echo "gh version mock 0.0.0"
    exit 0
fi

if [[ "${1:-}" == "auth" && "${2:-}" == "status" ]]; then
    echo "mock gh: logged in"
    exit 0
fi

if [[ "${1:-}" != "pr" || "${2:-}" != "list" ]]; then
    echo "mock_gh: unsupported invocation: $*" >&2
    exit 1
fi

shift 2

# Parse the relevant flags.
head=""
while (( $# > 0 )); do
    case "$1" in
        --head)
            shift
            head="${1:-}"
            shift || true
            ;;
        --state|--json|--limit)
            shift
            shift || true
            ;;
        *)
            shift
            ;;
    esac
done

if [[ -z "$head" ]]; then
    # No --head filter; emit empty list. (Smoke test always passes --head.)
    echo "[]"
    exit 0
fi

repo_root="${AUTO_CLAUDE_REPO_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

# Find any marker file whose JSON content names this branch.
matched=""
shopt -s nullglob
for f in "$repo_root"/.handoff/mock-pr-*.txt; do
    branch_in_file=$(jq -r '.branch // empty' "$f" 2>/dev/null || echo "")
    if [[ "$branch_in_file" == "$head" ]]; then
        matched="$f"
        break
    fi
done
shopt -u nullglob

if [[ -z "$matched" ]]; then
    echo "[]"
    exit 0
fi

# Emit a one-element array shaped to match `gh pr list --json number,url`.
jq -c '[{number: .pr_number, url: .pr_url}]' "$matched"
exit 0
