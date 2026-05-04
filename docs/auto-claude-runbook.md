# auto-claude — operating runbook

Quick reference for running, monitoring, and pausing the orchestration framework. The full design is in [auto-claude-design.md](auto-claude-design.md); this is the short-form ops cheatsheet.

## First-time setup (per repository)

```bash
# 1. Bootstrap the .handoff/ directory and check for required tools.
./scripts/auto_claude/install.sh

# 2. Seed state.json with the tasks you want autonomously shipped.
#    See .handoff/state.example.json for the schema.
cp .handoff/state.example.json .handoff/state.json
$EDITOR .handoff/state.json    # add real tasks; remove the example

# 3. Dry-run the watchdog once to verify everything works.
./scripts/auto_claude/watchdog.sh --dry-run

# 4. Pick a deployment path and install the scheduler.
./scripts/auto_claude/install_systemd.sh    # preferred (user systemd)
# OR
./scripts/auto_claude/install_cron.sh       # fallback (user crontab)
```

After install, the watchdog fires every 20 minutes and ships PRs as fast as task quality gates allow.

## Daily monitoring

```bash
# Quick status — current lease, last events, open PRs, why nothing is running.
./scripts/auto_claude/status_render.sh && cat .handoff/STATUS.md

# Tail the systemd journal (preferred deployment).
journalctl --user -u claude-watchdog -f

# OR tail the cron log (fallback deployment).
tail -f .handoff/logs/cron.log

# Inspect every event the framework has emitted (newest last).
tail -f .handoff/events.jsonl | jq .
```

## Pausing without uninstalling

To stop new sessions from spawning while leaving the framework installed:

```bash
# systemd: stop the timer (the .service stays installed; nothing fires).
systemctl --user stop claude-watchdog.timer

# cron: comment out the watchdog line.
crontab -l | sed 's|^\(.*scripts/auto_claude/watchdog.sh.*\)$|# \1|' | crontab -
```

To resume:

```bash
systemctl --user start claude-watchdog.timer
# OR uncomment the cron line via $EDITOR
```

## Pausing without stopping the timer (set a backoff)

If the timer should keep firing (so you can see the audit log activity) but no spawns should happen for a while, set a future backoff:

```bash
# Pause spawns until 2026-05-05T08:00:00Z.
echo '{"backoff_until": "2026-05-05T08:00:00Z", "reason": "manual pause"}' \
  > .handoff/.backoff.json
```

The watchdog reads this each tick and refuses to spawn while the timestamp is in the future. Delete the file to resume.

## Stopping a stuck session

If a session's lock is fresh (heartbeat <10 min) but the work has clearly stalled (no progress in events.jsonl for an hour), kill it manually:

```bash
# Identify the session pid from the lock.
jq . .handoff/.lock

# Kill the session and its children. The heartbeat thread will stop;
# the next watchdog run will see a stale lock and clean up.
kill -TERM "<pid>"

# After ~5 minutes the watchdog will reconcile and free the lease.
# To accelerate, manually remove the lock (only when you're certain
# the process is dead — don't race a live session):
rm .handoff/.lock

# Re-run the watchdog to immediately reclaim and (optionally) re-spawn.
./scripts/auto_claude/watchdog.sh
```

## Inspecting why a task is `blocked`

```bash
# Find blocked tasks.
jq '.tasks[] | select(.status == "blocked") | {id, title, attempts, notes}' .handoff/state.json

# Look at the recovery snapshots if the task hit a mid-rebase/merge crash.
ls .handoff/recovery/
```

## Adjusting cost limits

Edit `scripts/auto_claude/watchdog.sh` to change:

- `RATE_LIMIT_PER_HOUR` (default 6) — max sessions spawned per hour
- The `OnUnitActiveSec=` in `systemd/claude-watchdog.timer.template` — tick frequency (re-run `install_systemd.sh` after editing)

For a more conservative night-time run: `RATE_LIMIT_PER_HOUR=3`, `OnUnitActiveSec=30min`.

## Safe overnight checklist

Before leaving the framework unattended overnight:

- [ ] `gh auth status` — GitHub auth is valid and won't expire mid-night
- [ ] `claude --version` — `claude` is on PATH for the user systemd will run as
- [ ] `loginctl show-user "$USER" --property=Linger` returns `Linger=yes` (so user systemd survives logout)
- [ ] `.handoff/state.json` has `max_attempts` set conservatively (≤2) on every task
- [ ] No PR is currently in flight that you need to review before more work lands
- [ ] Backup: `git fetch --all` so the local reflog is current
- [ ] Spot-check: `./scripts/auto_claude/watchdog.sh --dry-run` shows the expected next task

## Cross-orchestrator co-existence

If you want to be interactively coding via VS Code while the framework runs in the background, **work in a separate worktree**:

```bash
git worktree add ../$(basename $PWD)-interactive main
cd ../$(basename $PWD)-interactive
# Open VS Code here. The framework continues in the original checkout.
```

Each worktree has its own HEAD, so `git checkout` in one will never affect the other. The SessionStart hook (`.claude/hooks/auto_claude_session_brief.sh`) detects sibling worktrees with active orchestrator activity and warns you at session start.

See [auto-claude-design.md §5](auto-claude-design.md#5-worktree-discipline) for the worktree-discipline rationale.

## Uninstall

```bash
./scripts/auto_claude/install_systemd.sh --uninstall
# OR
./scripts/auto_claude/install_cron.sh --uninstall

# Optionally clean up the .handoff/ directory (loses audit history).
# rm -rf .handoff/
```

The framework leaves no global system state — everything is user-level systemd or user crontab plus files in the project directory.
