#!/usr/bin/env bash
# install.sh — bootstrap auto-claude on this checkout.
#
# Validates dependencies, ensures .handoff/ structure, initializes state.json
# from template if missing. Idempotent.

set -euo pipefail

_self="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd "$(dirname "$_self")" && pwd)"
AUTO_CLAUDE_REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

usage() {
    cat <<'EOF'
install.sh — set up auto-claude

Usage:
    install.sh [--no-deps]

Validates that jq, flock, gh, claude are on PATH, then prepares .handoff/.

Pass --no-deps to skip the dependency check (useful in CI / containers
where claude isn't installed).
EOF
}

skip_deps=0
if [[ "${1:-}" == "--no-deps" ]]; then
    skip_deps=1
elif [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

# shellcheck source=state_helpers.sh
export AUTO_CLAUDE_REPO_ROOT
source "$SCRIPT_DIR/state_helpers.sh"

problems=()
check_dep() {
    local name="$1"
    if ! command -v "$name" >/dev/null 2>&1; then
        problems+=("missing dependency: $name")
    fi
}

if (( skip_deps == 0 )); then
    check_dep jq
    check_dep flock
    check_dep gh
    check_dep claude
    check_dep git
    check_dep setsid
fi

if (( ${#problems[@]} > 0 )); then
    echo "install.sh: dependency problems found:" >&2
    for p in "${problems[@]}"; do
        echo "  - $p" >&2
    done
    echo "Install missing tools (e.g. apt-get install jq util-linux gh) then re-run." >&2
    exit 1
fi

mkdir -p "$AUTO_CLAUDE_REPO_ROOT/.handoff/logs"
mkdir -p "$AUTO_CLAUDE_REPO_ROOT/.handoff/reflog-snapshots"
touch "$AUTO_CLAUDE_REPO_ROOT/.handoff/events.jsonl"

# Initialize state.json from template if missing
if state_init; then
    echo "auto-claude: state.json ready at $(state_path)"
else
    echo "auto-claude: state_init failed" >&2
    exit 1
fi

# Validate state file
if state_validate; then
    echo "auto-claude: state.json passes schema validation"
else
    echo "auto-claude: state.json failed schema validation — please fix" >&2
    exit 1
fi

cat <<EOF

auto-claude installed. Next steps:

  1. Edit $(state_path) to add tasks (see state.example.json for the shape).
  2. Try a dry run:
         scripts/auto_claude/watchdog.sh --dry-run
  3. Install the scheduler:
         systemd (preferred):  ./scripts/auto_claude/install_systemd.sh
         cron (fallback):      ./scripts/auto_claude/install_cron.sh
     See docs/auto-claude-design.md §6 for details, including how to
     monitor, pause, and uninstall. Both installers support --dry-run.

For status, look at .handoff/STATUS.md after each watchdog run, or run
  scripts/auto_claude/install_systemd.sh --status   (or install_cron.sh --status)
EOF
