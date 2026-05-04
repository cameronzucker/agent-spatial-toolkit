#!/usr/bin/env bash
# install_cron.sh — install the auto-claude watchdog as a user crontab entry.
# Idempotent; supports --uninstall, --status, --dry-run, --force.
#
# This is the fallback for hosts without user systemd. Prefer
# install_systemd.sh on Linux with user systemd available.

set -euo pipefail

_self="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd "$(dirname "$_self")" && pwd)"
DEFAULT_PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATE_DIR="$DEFAULT_PROJECT_ROOT/systemd"

CRONTAB="${CRONTAB:-crontab}"

# Marker comment is the contract — we grep for it on uninstall + duplicate check.
# Keep these distinct from any text that may appear in the embedded template
# header, so block detection is unambiguous.
MARKER="# >>> auto-claude watchdog (managed) >>>"
MARKER_END="# <<< auto-claude watchdog (managed) <<<"

usage() {
    cat <<'EOF'
install_cron.sh — install the auto-claude watchdog as a user crontab entry.

Usage:
    install_cron.sh [--project-root PATH] [--dry-run] [--force]
    install_cron.sh --uninstall [--dry-run]
    install_cron.sh --status

Flags:
    --project-root PATH   Substitute @PROJECT_ROOT@ with PATH (default: pwd of repo).
    --dry-run             Print what would happen; do not modify the crontab.
    --force               Replace an existing watchdog entry (otherwise refuse on duplicate).
    --uninstall           Remove the watchdog entry from the crontab.
    --status              Show whether the entry is installed and the next firing time.
    -h, --help            This message.

Environment:
    CRONTAB        Override the crontab binary (tests use a stub).
EOF
}

# ---- arg parse ---------------------------------------------------------

mode="install"
dry_run=0
force=0
project_root="$DEFAULT_PROJECT_ROOT"

while (( $# > 0 )); do
    case "$1" in
        --project-root)
            shift
            project_root="${1:?--project-root requires a value}"
            ;;
        --project-root=*)
            project_root="${1#--project-root=}"
            ;;
        --dry-run)
            dry_run=1
            ;;
        --force)
            force=1
            ;;
        --uninstall)
            mode="uninstall"
            ;;
        --status)
            mode="status"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "install_cron.sh: unknown arg: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

# ---- helpers -----------------------------------------------------------

require_crontab() {
    if ! command -v "$CRONTAB" >/dev/null 2>&1; then
        echo "install_cron.sh: \`$CRONTAB\` is not on PATH. Install cron / vixie-cron / cronie." >&2
        return 1
    fi
}

read_crontab() {
    # Print current crontab to stdout. Empty if none installed.
    "$CRONTAB" -l 2>/dev/null || true
}

write_crontab() {
    # Read new crontab from stdin. Atomic via mktemp + crontab <file>.
    local tmp
    tmp="$(mktemp "${TMPDIR:-/tmp}/auto-claude-crontab.XXXXXX")"
    cat > "$tmp"
    if (( dry_run )); then
        echo "[dry-run] would install crontab:"
        sed 's/^/[dry-run]   /' "$tmp"
        rm -f "$tmp"
        return 0
    fi
    "$CRONTAB" "$tmp"
    rm -f "$tmp"
}

has_marker() {
    # 0 if marker present in crontab, 1 otherwise.
    read_crontab | grep -Fq "$MARKER"
}

build_block() {
    # Stdout: the substituted crontab block, marker comments included.
    local src="$TEMPLATE_DIR/cron-template.txt"
    if [[ ! -f "$src" ]]; then
        echo "install_cron.sh: missing template: $src" >&2
        return 1
    fi
    local body
    body="$(sed -e "s|@PROJECT_ROOT@|${project_root}|g" \
                -e "s|@USER@|${USER}|g" \
                "$src")"
    # Wrap in marker fences so uninstall is a clean delete-between-fences.
    printf '%s\n%s\n%s\n' "$MARKER" "$body" "$MARKER_END"
}

strip_block() {
    # Stdin: existing crontab. Stdout: same crontab with the auto-claude
    # block (between MARKER and MARKER_END inclusive) removed.
    awk -v start="$MARKER" -v end="$MARKER_END" '
        BEGIN { skip = 0 }
        index($0, start) == 1 { skip = 1; next }
        skip && index($0, end) == 1 { skip = 0; next }
        !skip { print }
    '
}

verify_project_root() {
    local watchdog="$project_root/scripts/auto_claude/watchdog.sh"
    if [[ ! -x "$watchdog" ]]; then
        echo "install_cron.sh: $watchdog not found or not executable" >&2
        echo "  pass --project-root /absolute/path/to/checkout if running from another dir" >&2
        return 1
    fi
}

# ---- modes -------------------------------------------------------------

do_install() {
    verify_project_root || exit 1
    require_crontab || exit 1

    local existing
    existing="$(read_crontab)"

    if printf '%s\n' "$existing" | grep -Fq "$MARKER"; then
        if (( force == 0 )); then
            cat >&2 <<EOF
install_cron.sh: an auto-claude watchdog entry is already installed.

Re-run with --force to replace it, or run --uninstall first.
EOF
            return 1
        fi
        # Force path: strip the old block, then append the new.
        existing="$(printf '%s\n' "$existing" | strip_block)"
    fi

    {
        # Preserve existing crontab content (without trailing blank chaos).
        if [[ -n "$existing" ]]; then
            printf '%s\n' "$existing" | sed '/^$/N;/^\n$/D'
        fi
        build_block
    } | write_crontab

    if (( dry_run )); then
        return 0
    fi

    cat <<EOF

auto-claude watchdog cron entry installed.

Cadence: every 20 min (next firing aligned to */20 minute marks).
Logs:    $project_root/.handoff/logs/cron.log

Inspect:
  crontab -l | sed -n '/$MARKER/,/$MARKER_END/p'

Tail logs:
  tail -f $project_root/.handoff/logs/cron.log

Pause:
  $0 --uninstall   (or comment-out the line in \`crontab -e\`)
EOF
}

do_uninstall() {
    require_crontab || exit 1
    local existing
    existing="$(read_crontab)"

    if ! printf '%s\n' "$existing" | grep -Fq "$MARKER"; then
        echo "install_cron.sh: no auto-claude watchdog entry found (no-op)."
        return 0
    fi

    printf '%s\n' "$existing" | strip_block | write_crontab

    if (( dry_run )); then
        return 0
    fi
    echo "auto-claude watchdog cron entry removed."
}

do_status() {
    if ! command -v "$CRONTAB" >/dev/null 2>&1; then
        echo "install_cron.sh --status: crontab not on PATH" >&2
        return 1
    fi
    local existing
    existing="$(read_crontab)"
    if ! printf '%s\n' "$existing" | grep -Fq "$MARKER"; then
        echo "install_cron.sh --status: no auto-claude watchdog cron entry installed."
        return 1
    fi
    echo "==== installed cron entry ===="
    printf '%s\n' "$existing" \
        | awk -v s="$MARKER" -v e="$MARKER_END" '
            index($0,s)==1 {p=1}
            p {print}
            index($0,e)==1 {p=0}
        '
    echo

    # Approximate next-run for */20 * * * * — next minute that is a multiple of 20.
    local now_min next_min wait_min
    now_min=$(date +%-M)
    next_min=$(( ((now_min / 20) + 1) * 20 ))
    if (( next_min >= 60 )); then next_min=0; fi
    wait_min=$(( next_min - now_min ))
    if (( wait_min <= 0 )); then wait_min=$(( wait_min + 60 )); fi
    echo "Approx. next firing: in ${wait_min} min (cadence */20 * * * *)"

    local events="$project_root/.handoff/events.jsonl"
    if [[ -f "$events" ]]; then
        echo
        echo "==== last watchdog event ===="
        if command -v jq >/dev/null 2>&1; then
            tail -n 50 "$events" \
                | jq -c 'select((.type // "") | test("watchdog|spawn|alert"))' 2>/dev/null \
                | tail -n 1 \
                || tail -n 1 "$events"
        else
            tail -n 1 "$events"
        fi
    fi
}

case "$mode" in
    install)   do_install ;;
    uninstall) do_uninstall ;;
    status)    do_status ;;
    *) echo "install_cron.sh: unknown mode: $mode" >&2; exit 2 ;;
esac
