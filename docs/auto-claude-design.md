# auto-claude — design notes

> Long-running, unattended Claude Code orchestration for a single dev box.
> A trusted-mode framework: not a sandbox, not multi-tenant. v1 ships
> defense-in-depth for the failure modes a busy human owner will hit;
> sandboxing is v2.

This document is the canonical reference for how `scripts/auto_claude/`
fits together. Read this before changing any script in that directory.

## 1. Motivation

The original use case: ship a backlog of well-scoped PRs while sleeping,
travelling, or otherwise away from the keyboard. The owner reviews and
merges PRs in the morning; overnight, a watchdog process spawns Claude
Code sessions one at a time, each with a single task to land as a PR.

Three properties drive everything else:

1. **Unattended**. We must not require a human to dismiss prompts, watch
   for runaway behavior, or babysit. The framework decides when to start
   a session, when to back off, when to alert.
2. **Production-quality outputs**. PRs must follow the existing repo's
   conventions (semver, conventional commits, tests pass before push).
   Failures should be loud and recoverable, not silent.
3. **Low blast radius on bugs**. A misfire — a session that loops, a
   stuck lock, a corrupted state file — should be self-healing or at
   worst quiescent within a bounded window. Never destructive.

The framework is one piece of a four-PR rollout:

| PR  | Surface | Purpose |
| --- | ------- | ------- |
| A   | core    | safe-git, locks, state, watchdog, reconcile, BATS suite |
| B   | interactive surface (this PR) | session brief hook, sibling-worktree detection, design doc |
| C   | scheduling | cron / systemd unit, log rotation |
| D   | observability | per-task health dashboard, alert dispatch |

## 2. Architecture

Three components, all on disk:

```
                   ┌──────────────────────┐
                   │ .handoff/state.json  │  single source of truth
                   │ .handoff/.lock       │  current session marker
                   │ .handoff/events.jsonl│  append-only audit
                   └──────────┬───────────┘
                              │
        ┌──────────cron───────┴────────hook──────────────┐
        │                                                │
        ▼                                                ▼
  ┌──────────┐  spawn_intent →  ┌──────────────┐   ┌──────────────┐
  │ watchdog │ ───────────────► │ session_boot │   │  Claude Code │
  │  (1/min) │   spawn_result   │  (lease,     │   │  session     │
  │          │ ◄─────────────── │   branch)    │   │  (-p)        │
  └──────────┘    session_exit  └──────┬───────┘   └──────┬───────┘
                                       │                  │
                                       └────session_exit──┘
                                              │
                                              ▼
                                   open PR via `gh pr create`
```

`watchdog.sh` is the only scheduler — sessions never spawn their own
successors, and the watchdog never runs while another watchdog is in
flight (single-instance flock). Sessions are short-lived; the watchdog
is the long-lived authority.

## 3. State schema

`state.json` (gitignored; `state.example.json` is the template) is read
and written under an exclusive flock on `state.lock`. Schema (current
`schema_version: 1`):

```json
{
  "schema_version": 1,
  "tasks": [
    {
      "id": "TASK-001",                        // ^[A-Z][A-Z0-9_-]{0,63}$
      "title": "Human-readable title",
      "status": "pending|leased|in_progress|pr_open|blocked|done",
      "branch": "feat/example",                // git check-ref-format
      "head_sha_at_lease_start": null,
      "pr_number": null,
      "pr_url": null,
      "phase": null,                            // closed enum (see below)
      "attempts": 0,
      "max_attempts": 3,
      "depends_on": [],                         // array of task ids
      "created_at": "...",
      "updated_at": "...",
      "notes": "Free-form for the implementer"
    }
  ],
  "current_lease": null,
  "backoff_until": null,
  "last_session_id": null
}
```

Closed enums (changes here are breaking — see §4 below):

- `status`: `pending → leased → in_progress → pr_open → done` (or
  `blocked` from any state)
- `phase`: `leased → branch_created → editing → tests_running →
  committing → pushing → pr_opening → pr_open → done` (also `blocked`)

The lock file `.handoff/.lock` (also gitignored) records the active
session's process metadata so a watchdog crash + reboot doesn't strand
us:

```json
{
  "session_id": "sess-...",  "pid": 12345,    "ppid": 1,
  "host": "pandora",         "boot_id": "<kernel boot uuid>",
  "started_at": "...",       "heartbeat_at": "...",
  "current_branch": "feat/x", "current_task_id": "TASK-001",
  "phase": "editing"
}
```

`boot_id` matters: a stale lock from before the last reboot is *known*
dead, even if the on-disk timestamps look recent. (Linux only — falls
back to `uname-n + uptime` hash on macOS/BSD.)

## 4. Safety model

The framework is *not* a sandbox. The implementer Claude session has
write access to anything under the repo. Defenses are layered:

1. **`safe-git` wrapper**. Subagents and session scripts call
   `scripts/auto_claude/safe-git` instead of `git`. It refuses
   pre-subcommand flags (`-C`, `-c`, `--git-dir`, `--work-tree`, …),
   `reset --hard`, `worktree add`, `push --force`, branch creation
   off-lease, and any mutating op when cwd or current branch doesn't
   match the recorded lease.
2. **Pre-commit hook**. Installed into `.git/hooks/pre-commit` at
   session boot. Blocks commits when the session env is set but state
   is missing, when the branch doesn't match the lease, etc. Fails
   *closed* — a missing state file is treated as "refuse," never
   "allow."
3. **`flock` serialization**. Every read-check-write on `state.json`
   takes an exclusive flock on `.handoff/state.lock`. Lock-file mutation
   takes a sibling `.lock.serial` flock — without that, the watchdog's
   `rm` can race `update_heartbeat`'s `mv` and resurrect a deleted lock.
4. **Two-phase audit**. `events.jsonl` records `spawn_intent` *before*
   the spawn, `spawn_result` after. `session_boot_start` /
   `claude_invoke` / `claude_returned` / `session_exit`. A reader can
   reconstruct what happened even if the next session crashes mid-write.
5. **Atomic writes**. Every state mutation goes through `mktemp` + `mv`
   so a crash mid-write leaves the previous valid file in place.
6. **Validators on input**. `_validate_task_id` and `_validate_branch`
   run *before* values reach git or jq filters — leading dashes,
   embedded `..`, jq-syntax injection in `pr_url`, all rejected at
   schema validate.
7. **Identity-checked stale-lock cleanup**. The watchdog snapshots the
   lock's `(session_id, pid, heartbeat_at, boot_id)` tuple in
   `reconcile.sh`, then re-verifies it under a flock before `rm`. If a
   new session acquired the lock in the meantime, the snapshot tuple
   won't match and we abort cleanup with `alert_lock_changed_during_cleanup`.

## 5. Worktree discipline

This is the section that motivated PR B. **Two different rules for two
different layers, and confusing them caused a real collision.**

### 5.1 Within-orchestrator: worktrees BANNED

A single auto-claude orchestrator MUST NOT use `git worktree add`
internally. Geographica pitfall #14 (workspace correctness) is the
canonical write-up; the short version:

- `git worktree add` creates a sibling checkout with a separate
  index/HEAD but **shared `.git/` data**. Subsequent ops (fetch, gc,
  prune, hook installs) on one checkout affect the other.
- The watchdog assumes one canonical `.handoff/` per orchestrator. Two
  worktrees with two `.handoff/` dirs and two state files diverge
  immediately. The lock files no longer talk to each other.
- `safe-git` enforces this — `safe-git worktree add` is in the
  blacklist (exit 4).

If you find yourself wanting two simultaneous sessions inside one
orchestrator, the answer is "queue the second task." The watchdog
already serializes tasks one-at-a-time on purpose. Don't fight it.

### 5.2 Cross-orchestrator: separate worktrees MANDATORY

If a *human* wants to run two parallel orchestrations on the same repo
(say, "ship UI work" and "ship backend work" overnight, in two terminal
windows, each with its own Claude Code session and its own backlog),
they MUST use separate `git worktree`s — one per orchestrator.

This is the situation that bit us today. Two Claude sessions started in
the *same* working tree:

- Session A was on `feat/ui-wizard-shell`, mid-edit, with uncommitted
  changes in `pyproject.toml` and `src/...`.
- Session B started in the same directory, ran `git checkout main`
  intending to start a fresh task, and silently wiped Session A's
  uncommitted edits. Recovery was a `git reflog` archaeology dig and a
  `git worktree add` to give Session B its own tree retroactively.

**`git checkout` is a global operation against the index and worktree
state. It does not respect "the other Claude session is using this
file."** The only thing that prevents collision is each session having
its own physical worktree.

The framework now surfaces this at every session boot:

- `.claude/hooks/auto_claude_session_brief.sh` walks
  `git worktree list --porcelain`, parses each sibling's
  `.handoff/.lock`, and prints a banner-wrapped warning if any sibling
  has a fresh heartbeat. Output goes to stdout, which Claude Code's
  hooks docs describe as "added as context for Claude" — so the new
  session reads about its sibling before it reads its first user
  prompt.
- `session_boot.sh` (the orchestrator's own spawn path) does the same
  check and emits `alert_sibling_orchestrator_active` to the audit
  log. Same-branch collisions are a hard stop
  (`alert_sibling_branch_conflict`); different-branch is a warning.

**Why warn instead of refuse?** The sibling might be doing legitimate,
unrelated work. Refusing would mean one orchestrator could DoS another
by holding any lock. The audit trail + the hook brief give the human
all the information needed to decide.

## 6. Operating model

### 6.1 First-time install (per repo / per worktree)

```bash
scripts/auto_claude/install.sh           # validates jq/flock/gh/claude
                                          # and initializes .handoff/state.json
$EDITOR .handoff/state.json              # add tasks
scripts/auto_claude/watchdog.sh --dry-run  # confirm it picks up the next task
```

Then install the scheduler. Two paths, pick one:

### 6.2 Scheduling — systemd (preferred)

Use this on Linux hosts where the user systemd instance is running
(every modern desktop, the Pi 5 with bookworm, etc.). It integrates with
journald, survives reboots, and gives clean `--status` / `--uninstall`
verbs. **No root required** — everything writes under
`~/.config/systemd/user/`.

One-time prerequisite if you want the timer to keep running across
logouts (recommended for an unattended Pi):

```bash
sudo loginctl enable-linger "$USER"
```

`install_systemd.sh` will warn if linger is not enabled but won't fail.

Install:

```bash
./scripts/auto_claude/install_systemd.sh           # idempotent
./scripts/auto_claude/install_systemd.sh --dry-run # inspect first
```

What the installer does:

1. Verifies `systemctl --user` is available; bails to the cron path otherwise.
2. Substitutes `@PROJECT_ROOT@` and `@USER@` in the templates under
   `systemd/` and writes the unit + timer to
   `~/.config/systemd/user/claude-watchdog.{service,timer}`.
3. Runs `systemctl --user daemon-reload`.
4. Enables + starts the timer.
5. Prints the next firing time and the tail command.

Cadence is `OnUnitActiveSec=20min`, with `Persistent=true` so missed
runs after a reboot or sleep fire a catch-up tick.

Monitor:

```bash
journalctl --user -u claude-watchdog -f      # tail live
journalctl --user -u claude-watchdog --since='1 hour ago'
./scripts/auto_claude/install_systemd.sh --status
```

Pause / resume:

```bash
systemctl --user stop  claude-watchdog.timer    # pause (timer stays installed)
systemctl --user start claude-watchdog.timer    # resume
```

If the watchdog is in `backoff_until`, clear it manually by editing
`.handoff/state.json` (or `.handoff/.backoff.json`) under a quiet moment.

Uninstall:

```bash
./scripts/auto_claude/install_systemd.sh --uninstall
```

This stops + disables the timer, removes the two unit files, and
daemon-reloads. Safe to re-run.

### 6.3 Scheduling — cron (fallback)

Use this if user systemd isn't available, or you just prefer cron:

```bash
./scripts/auto_claude/install_cron.sh              # idempotent
./scripts/auto_claude/install_cron.sh --dry-run    # inspect first
./scripts/auto_claude/install_cron.sh --force      # replace existing entry
./scripts/auto_claude/install_cron.sh --uninstall
./scripts/auto_claude/install_cron.sh --status
```

The installer reads your current crontab, refuses to add a duplicate
(without `--force`), and surrounds its block with
`# >>> auto-claude watchdog (managed) >>>` /
`# <<< auto-claude watchdog (managed) <<<` marker comments so uninstall
is a clean delete-between-fences. The substituted block is in
`systemd/cron-template.txt`.

Default cadence: `*/20 * * * *`. Output goes to
`.handoff/logs/cron.log`, not mail.

Monitor:

```bash
tail -f .handoff/logs/cron.log
./scripts/auto_claude/install_cron.sh --status
```

### 6.4 Status check

`STATUS.md` is regenerated every watchdog tick:

```bash
cat .handoff/STATUS.md
```

Or stream the last few events:

```bash
tail -20 .handoff/events.jsonl | jq -c '{ts,type,task_id,decision,reason}'
```

The SessionStart hook also injects a status brief into every Claude
session that opens in this repo, so just running `claude` will show you
where things are.

### 6.5 Worked example (this Pi)

Bring it up on `agent-spatial-toolkit`, on a Raspberry Pi 5 running
bookworm with user `administrator`:

```bash
cd /home/administrator/Code/agent-spatial-toolkit
sudo loginctl enable-linger administrator             # one-time, optional but recommended
./scripts/auto_claude/install.sh                      # bootstrap .handoff/
$EDITOR .handoff/state.json                           # add at least one task
./scripts/auto_claude/watchdog.sh --dry-run           # sanity-check decision
./scripts/auto_claude/install_systemd.sh --dry-run    # see what we'd write
./scripts/auto_claude/install_systemd.sh              # actually install
systemctl --user start claude-watchdog.service        # force one tick now
journalctl --user -u claude-watchdog --since='2 min ago'
./scripts/auto_claude/install_systemd.sh --status     # combined health check
./scripts/auto_claude/install_systemd.sh --uninstall  # remove when done
```

## 7. Failure modes and recovery

| Mode | Detection | Response |
|------|-----------|----------|
| Session PID dies but lock file remains "fresh" | reconcile sees PID gone but heartbeat recent | `alert_dangling`; cleanup deferred until heartbeat ages out |
| Session lock heartbeat goes stale (>10 min) | reconcile flags `lock_state=stale` | `alert_stuck` between 10–60 min; `consider_spawn` after 60 min with verify-then-rm under serial flock |
| `state.json` schema corruption | `state_validate` failure on every read | watchdog emits `alert_state_invalid` and refuses to spawn until human fixes |
| Mid-merge / mid-rebase / mid-cherry-pick | reconcile inspects `.git/MERGE_HEAD` etc. | `alert_dirty_git`, refuse spawn |
| Reboot mid-session | `boot_id` mismatch on next watchdog tick | lock immediately classified stale; cleared at next stale cutoff |
| Sibling worktree active | `sibling_worktrees.sh` finds fresh lock in sibling | session brief warning; `alert_sibling_orchestrator_active` in audit; same-branch is a hard stop |
| Rate-limit blowout | watchdog counts `spawn_result` events in last 1h | `alert_rate_limit` at 6 spawns/h (configurable) |
| `gh pr create` fails | session_exit catches non-zero from `gh` | task reclassified back to `pending`; `alert_pr_create_failed` |
| Lost work via reflog | every watchdog tick snapshots `git reflog --all` to `.handoff/reflog-snapshots/` | `git reflog` + the snapshot directory are recovery primitives |

## 8. Cost model

Pessimistic upper bound on unattended overnight cost (assumes nothing
intervenes):

- Watchdog rate limit: `AUTO_CLAUDE_RATE_LIMIT=6` spawns/hour by default.
- 8 hours overnight × 6 spawns = 48 sessions max.
- Each session is a single Claude Code `-p` invocation against one
  task. With prompt caching the average session in a small repo on
  Sonnet-class models is roughly $0.50–$2.00.
- Worst case ≈ $100/night. In practice the dependency graph and the
  task backlog gate this far below the rate limit.

`max_attempts` (default 3 per task) prevents the same failing task from
burning through the whole rate limit. After three attempts the task
flips to `blocked` and the watchdog skips it.

## 9. What this is NOT

- **Not a sandbox.** The implementer session can write any file in the
  repo. `safe-git` and the pre-commit hook are tripwires, not walls.
- **Not multi-tenant.** One owner, one machine, one trusted set of
  credentials.
- **Not Pi-specific.** Tested on Raspberry Pi, but everything is
  portable bash + jq + flock + git + gh.
- **Not a replacement for code review.** Every PR the orchestrator
  opens still goes through human review before merge.

v1 is **trusted-mode for solo dev box use**. Multi-machine deployment
needs the v2 hardening below.

## 10. Future hardening

- **Bubblewrap / systemd sandbox (v2).** Run each implementer session
  under a unit with `ProtectHome=`, `ReadOnlyPaths=`, `NoNewPrivileges=`,
  and a tmpfs-bound `/tmp`. The repo would be the only writable path.
- **Anthropic Routines as alternative to local watchdog (v2).** If the
  Routines product graduates, swap `watchdog.sh` for a remote scheduler
  and keep the lock/state layer here for local arbitration. The audit
  log + safe-git stay regardless.
- **Per-task worktree (v3).** Today the framework runs one session at
  a time on purpose — multiple parallel sessions in one tree is the
  Geographica pitfall. With the cross-orchestrator detection from PR B
  in place, the bones for "spawn N sessions, each in its own worktree"
  exist. Adding it requires per-worktree `.handoff/` dirs, a
  worktree-aware watchdog, and probably a `tmux`-style session naming
  scheme. Not v1.
- **Per-task budget caps (v2).** Pass `--max-tokens` or a wall-clock
  cap into the headless `claude -p` invocation; refuse spawn if a
  recent budget event flips a kill switch.

## 11. Lessons learned (worked example)

**Today's cross-orchestrator collision:**

1. 22:30. User opens a Claude Code session in `agent-spatial-toolkit/`
   to work on the wizard shell PR. Session A starts editing
   `pyproject.toml`, `src/...`.
2. 23:15. User opens a *second* terminal in the same directory, starts
   another Claude Code session, and asks it to start an unrelated task.
   Session B's first action is `git checkout main`. This is a global
   filesystem operation: index entries are rewritten, untracked files
   that don't conflict are kept, but Session A's *modified-but-uncommitted*
   files are silently overwritten with the index's version of `main`.
3. Session A's next save attempt sees stale-on-disk content. User
   notices the lost edits. Recovery: `git reflog`, hand-pick the
   pre-collision SHA, and `git worktree add ../foo-2 main` to give
   Session B its own tree from then on.

**Why the framework let this happen:** the framework didn't, exactly —
it wasn't installed yet. But the *pattern* (two sessions, one tree) is
exactly what PR B's hook is designed to detect. If
`auto_claude_session_brief.sh` had been live when Session B opened, it
would have read Session A's `.handoff/.lock`, noticed a fresh heartbeat,
and printed the WARNING block before Session B accepted its first user
prompt. Session B would have started by saying "I see Session A is
working in this tree; should I create a worktree?" instead of running
`git checkout`.

**Generalization for other projects adopting this pattern:** the
collision class is "two cooperating-but-uncoordinated agents share a
piece of mutable global state (here, the working tree)." The defense is
a SessionStart-equivalent hook that probes the global state and warns
the agent before it touches it. Don't rely on humans to remember.

## 12. Glossary

- **Watchdog**. The cron-driven supervisor. Runs `reconcile.sh`,
  decides spawn vs. quiescent, executes `session_boot.sh` in the
  background.
- **Session**. One invocation of `session_boot.sh` → headless
  `claude -p` → `session_exit.sh`. Owns the lock for its duration.
- **Lease**. The `current_lease` field in state.json. Records which
  task / session / branch is in flight. Cleared on session_exit.
- **Reconcile**. `reconcile.sh` reads state, lock, git status; emits a
  JSON snapshot the watchdog uses for its decision. Read-only.
- **Implementer Claude session**. The headless `claude -p` invocation
  with the task prompt; the thing actually editing code. Distinct from
  the orchestrator (the watchdog) and the operator (the human).
- **Sibling worktree**. Another `git worktree add`-created checkout of
  this repo. Detected via `git worktree list --porcelain`.
