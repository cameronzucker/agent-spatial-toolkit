#!/usr/bin/env bash
# install_systemd.sh — install the auto-claude watchdog as a user systemd
# unit + timer. Idempotent; supports --uninstall, --status, --dry-run.
#
# This is the preferred deployment path on Linux hosts where the user
# systemd instance is running. Falls back to install_cron.sh otherwise.
#
# Hard requirement: NO root. Everything writes to ~/.config/systemd/user/.

set -euo pipefail

_self="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd "$(dirname "$_self")" && pwd)"
DEFAULT_PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATE_DIR="$DEFAULT_PROJECT_ROOT/systemd"

# Allow tests to inject a no-op systemctl.
SYSTEMCTL="${SYSTEMCTL:-systemctl}"
LOGINCTL="${LOGINCTL:-loginctl}"

UNIT_NAME="claude-watchdog.service"
TIMER_NAME="claude-watchdog.timer"
USER_UNIT_DIR="${SYSTEMD_USER_UNIT_DIR:-$HOME/.config/systemd/user}"

usage() {
    cat <<'EOF'
install_systemd.sh — install the auto-claude watchdog as a user systemd unit + timer.

Usage:
    install_systemd.sh [--project-root PATH] [--dry-run]
    install_systemd.sh --uninstall [--dry-run]
    install_systemd.sh --status

Default --project-root is the directory containing this script's grandparent
(i.e. the checkout itself).

Flags:
    --project-root PATH   Substitute @PROJECT_ROOT@ with PATH (default: pwd of repo).
    --dry-run             Print what would happen; do not write files or run systemctl.
    --uninstall           Stop + disable the timer, remove the unit files, daemon-reload.
    --status              Show timer + service status and the most recent watchdog event.
    -h, --help            This message.

Environment:
    SYSTEMCTL      Override the systemctl binary (tests use `true` for no-op).
    LOGINCTL       Override loginctl (tests use `true` for no-op).
    SYSTEMD_USER_UNIT_DIR
                   Override target unit dir (default ~/.config/systemd/user).
EOF
}

# ---- arg parse ---------------------------------------------------------

mode="install"
dry_run=0
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
            echo "install_systemd.sh: unknown arg: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

# ---- helpers -----------------------------------------------------------

run_cmd() {
    # Echo + execute, unless --dry-run.
    if (( dry_run )); then
        printf '[dry-run] %s\n' "$*"
    else
        "$@"
    fi
}

write_file_atomic() {
    # write_file_atomic <dest> <content-on-stdin>
    local dest="$1"
    if (( dry_run )); then
        printf '[dry-run] write %s:\n' "$dest"
        sed 's/^/[dry-run]   /'
        return 0
    fi
    local tmp
    tmp="$(mktemp "${dest}.XXXXXX")"
    cat > "$tmp"
    chmod 0644 "$tmp"
    mv "$tmp" "$dest"
}

verify_user_systemd() {
    if [[ "$SYSTEMCTL" == "true" ]]; then
        # test stub
        return 0
    fi
    if ! command -v "$SYSTEMCTL" >/dev/null 2>&1; then
        cat >&2 <<EOF
install_systemd.sh: \`$SYSTEMCTL\` is not on PATH.

This host doesn't appear to have systemd. Use the cron fallback instead:
    ${SCRIPT_DIR}/install_cron.sh
EOF
        return 1
    fi
    if ! "$SYSTEMCTL" --user is-system-running --quiet >/dev/null 2>&1 \
        && ! "$SYSTEMCTL" --user show-environment >/dev/null 2>&1; then
        cat >&2 <<EOF
install_systemd.sh: user systemd instance does not appear to be running.

The user systemd instance is required for --user units. Either:
  - log in via a session that starts user systemd (most desktops do this), or
  - run \`loginctl enable-linger \$USER\` so user systemd survives logouts, or
  - use the cron fallback: ${SCRIPT_DIR}/install_cron.sh
EOF
        return 1
    fi
    return 0
}

check_linger() {
    # Warn (don't fail) if linger is off. Without linger the timer dies
    # when the user logs out. Tests stub LOGINCTL=true to suppress.
    if [[ "$LOGINCTL" == "true" ]]; then
        return 0
    fi
    if ! command -v "$LOGINCTL" >/dev/null 2>&1; then
        return 0
    fi
    local linger
    linger="$("$LOGINCTL" show-user "$USER" --property=Linger --value 2>/dev/null || echo "")"
    if [[ "$linger" != "yes" ]]; then
        cat >&2 <<EOF
install_systemd.sh: warning — user lingering is not enabled.

Without lingering, your user systemd instance (and therefore this timer)
stops when you log out. To keep the watchdog running across logouts:

    sudo loginctl enable-linger $USER

(One-time. Survives reboots.)

Continuing with installation; you can enable linger any time.
EOF
    fi
}

substitute_template() {
    # substitute_template <src> <dest>
    local src="$1" dest="$2"
    if [[ ! -f "$src" ]]; then
        echo "install_systemd.sh: missing template: $src" >&2
        return 1
    fi
    sed -e "s|@PROJECT_ROOT@|${project_root}|g" \
        -e "s|@USER@|${USER}|g" \
        "$src" \
        | write_file_atomic "$dest"
}

verify_project_root() {
    local watchdog="$project_root/scripts/auto_claude/watchdog.sh"
    if [[ ! -x "$watchdog" ]]; then
        echo "install_systemd.sh: $watchdog not found or not executable" >&2
        echo "  pass --project-root /absolute/path/to/checkout if running from another dir" >&2
        return 1
    fi
    return 0
}

# ---- modes -------------------------------------------------------------

do_install() {
    verify_project_root || exit 1
    verify_user_systemd || exit 1
    check_linger

    local svc_src="$TEMPLATE_DIR/claude-watchdog.service.template"
    local timer_src="$TEMPLATE_DIR/claude-watchdog.timer.template"
    local svc_dst="$USER_UNIT_DIR/$UNIT_NAME"
    local timer_dst="$USER_UNIT_DIR/$TIMER_NAME"

    if (( dry_run )); then
        printf '[dry-run] mkdir -p %s\n' "$USER_UNIT_DIR"
    else
        mkdir -p "$USER_UNIT_DIR"
    fi

    substitute_template "$svc_src" "$svc_dst"
    substitute_template "$timer_src" "$timer_dst"

    run_cmd "$SYSTEMCTL" --user daemon-reload
    run_cmd "$SYSTEMCTL" --user enable --now "$TIMER_NAME"

    if (( dry_run )); then
        echo "[dry-run] would print next-run time and tail command"
        return 0
    fi

    cat <<EOF

auto-claude watchdog timer installed.

Unit files:
  $svc_dst
  $timer_dst

Next run:
EOF
    "$SYSTEMCTL" --user list-timers "$TIMER_NAME" --no-pager 2>/dev/null \
        | sed -n '1,3p' || true

    cat <<EOF

Tail logs:
  journalctl --user -u claude-watchdog -f

Pause / resume:
  systemctl --user stop $TIMER_NAME      # pause
  systemctl --user start $TIMER_NAME     # resume

Uninstall:
  $0 --uninstall
EOF
}

do_uninstall() {
    local svc_dst="$USER_UNIT_DIR/$UNIT_NAME"
    local timer_dst="$USER_UNIT_DIR/$TIMER_NAME"

    # If systemd isn't running we can still clean up the files.
    if command -v "$SYSTEMCTL" >/dev/null 2>&1 && [[ "$SYSTEMCTL" != "true" ]]; then
        # Tolerate "not loaded" / "not enabled" — uninstall must be safe to re-run.
        run_cmd "$SYSTEMCTL" --user disable --now "$TIMER_NAME" 2>/dev/null || true
    fi

    for f in "$timer_dst" "$svc_dst"; do
        if [[ -f "$f" ]]; then
            run_cmd rm -f "$f"
        else
            echo "install_systemd.sh: $f not present (no-op)"
        fi
    done

    if command -v "$SYSTEMCTL" >/dev/null 2>&1 && [[ "$SYSTEMCTL" != "true" ]]; then
        run_cmd "$SYSTEMCTL" --user daemon-reload || true
    fi

    echo "auto-claude watchdog timer uninstalled."
}

do_status() {
    local svc_dst="$USER_UNIT_DIR/$UNIT_NAME"
    local timer_dst="$USER_UNIT_DIR/$TIMER_NAME"

    if [[ ! -f "$timer_dst" || ! -f "$svc_dst" ]]; then
        cat >&2 <<EOF
install_systemd.sh --status: not installed.

Expected:
  $svc_dst
  $timer_dst

Run \`$0\` to install.
EOF
        return 1
    fi

    if command -v "$SYSTEMCTL" >/dev/null 2>&1 && [[ "$SYSTEMCTL" != "true" ]]; then
        echo "==== timer ===="
        "$SYSTEMCTL" --user status "$TIMER_NAME" --no-pager 2>&1 || true
        echo
        echo "==== service ===="
        "$SYSTEMCTL" --user status "$UNIT_NAME" --no-pager 2>&1 || true
        echo
    fi

    local events="$project_root/.handoff/events.jsonl"
    if [[ -f "$events" ]]; then
        echo "==== last watchdog event ===="
        # Last line that mentions watchdog or spawn — fall back to last line.
        if command -v jq >/dev/null 2>&1; then
            tail -n 50 "$events" \
                | jq -c 'select((.type // "") | test("watchdog|spawn|alert"))' 2>/dev/null \
                | tail -n 1 \
                || tail -n 1 "$events"
        else
            tail -n 1 "$events"
        fi
    else
        echo "==== no events.jsonl yet at $events ===="
    fi
}

case "$mode" in
    install)   do_install ;;
    uninstall) do_uninstall ;;
    status)    do_status ;;
    *) echo "install_systemd.sh: unknown mode: $mode" >&2; exit 2 ;;
esac
