# auto-claude end-to-end smoke validation

This directory contains the smoke test that proves the auto-claude
orchestration framework works end-to-end. It is the "is the framework
real?" suite — complementary to, but distinct from, the unit-level
tests in [`../test_auto_claude.bats`](../test_auto_claude.bats).

## What it proves

The unit suite covers individual functions (lock acquisition, state
mutation, safe-git refusals, etc.) with fast, focused tests. The smoke
suite covers a single thing the unit suite cannot: **the whole pipeline
fits together**.

For each scenario, the smoke fixture:

1. Builds an isolated test repo (working tree + a local bare-repo
   "remote") under `mktemp -d`.
2. Mirrors the real `scripts/auto_claude/` into it.
3. Bootstraps the framework via `install.sh --no-deps`.
4. Seeds a task into `state.json`.
5. Invokes the real `watchdog.sh`, which spawns the real `session_boot.sh`,
   which exec's a stub `claude` (see below), which then runs the real
   `safe-git`, hits the real pre-commit hook, and exits through the real
   `session_exit.sh`.
6. Asserts on the on-disk side-effects: state transitions, event log
   entries, lock cleanup, branch push, reflog snapshot, marker files.

The five scenarios cover:

| Test | What it validates |
| ---- | ----------------- |
| 1. full cycle | happy-path `pending → leased → pr_open` end-to-end |
| 2. empty backlog | watchdog correctly classifies as `quiescent` and emits no spawn |
| 3. stale lock | watchdog cleans up an aged-out lock and proceeds to spawn |
| 4. concurrent watchdogs | only one watchdog acquires `.watchdog.lock`; the other quits cleanly |
| 5. rate limit | 7 fabricated `spawn_result` events trip the rolling-hour limiter |

## How to run

```bash
bats tests/smoke/test_smoke_e2e.bats
```

The full suite typically completes in a few seconds. Each test owns its
own `mktemp -d` sandbox and tears it down on teardown, so you can run
the smoke tests in any order against any working tree without
side-effects on the real repo.

## What this does NOT do

- **It does not invoke real `claude`.** The fixture shadows `claude` on
  `PATH` with [`mock_claude.sh`](./mock_claude.sh), and explicitly sets
  `AUTO_CLAUDE_CLAUDE_BIN` to point at it (see the env-var documented in
  `scripts/auto_claude/session_boot.sh`). Cost = $0; no Anthropic API
  calls are made.

- **It does not invoke real `gh`.** Same trick:
  [`mock_gh.sh`](./mock_gh.sh) is shadowed onto `PATH`. It implements
  the minimum surface session_exit.sh needs (`gh pr list --head <branch>
  --state open --json number,url`) by reading a marker file the mock
  claude writes. No GitHub network calls.

- **It does not validate the real Claude binary's behavior.** That is a
  separate concern — see the "real-claude validation" plan TODO below.

## Real-claude validation (TODO)

The smoke test confirms the framework is well-formed. It does *not*
confirm that a real `claude -p` session, given the implementer prompt
session_boot constructs, does the right thing. That requires:

1. A real Anthropic API key,
2. A real `gh` auth token + a real (throwaway) GitHub repo,
3. A trivial-but-real task whose success criterion is unambiguous.

That validation is the operator's smoke test before letting the
framework run unattended overnight against their backlog. It is not run
in CI (cost; auth requirements). A separate `tests/smoke/REAL_CLAUDE.md`
is the right home for the runbook when we write it. This is tracked as
PR-D follow-up.

## Mock contract

[`mock_claude.sh`](./mock_claude.sh) is the contract between the smoke
test and `session_boot.sh`. It accepts `-p <prompt>`, parses the
`Task: <task_id> — <title>` line out of the prompt, performs:

1. `touch tests/smoke/marker-<task_id>` (the trivial work)
2. `safe-git add tests/smoke/marker-<task_id>`
3. `safe-git commit -m "test(smoke): mock impl for <task_id>"`
4. `touch .handoff/tests-passed-<task_id>` (the quality-gate marker)
5. `safe-git push -u origin <branch>` (to the bare-repo upstream)
6. Writes `.handoff/mock-pr-<task_id>.txt` for `mock_gh.sh` to
   transmute into a PR-list response.
7. Emits a JSON line on stdout (matches `--output-format json`).

If the prompt does not contain a recognizable `Task:` line, the mock
exits 1 — that way a malformed prompt cannot quietly false-pass the
smoke test.

[`mock_gh.sh`](./mock_gh.sh) supports `gh --version`, `gh auth status`,
and `gh pr list --head <branch> --state open --json number,url`. Any
other invocation exits 1 so tests fail loudly on contract drift.

## CI

The smoke suite is intended to run on every PR alongside the unit
suite. It does not require systemd, cron, real claude, or real gh —
only `bash`, `bats`, `git`, `jq`, `flock`, and `shellcheck` (the latter
only if you `shellcheck` the mocks). All standard on a default Ubuntu
or Debian runner.
