"""Append-only JSONL event log for session replay (spec §4)."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class EventLog:
    """Append-only JSONL writer + reader. Each line is one event.

    Used as the wizard server's audit/replay trail per spec §3 Phase 2f.
    Per spec §9, session RESUME goes through ``state.json``, not the events
    log — so this class treats events as debug/replay tier, not as a
    durable source of truth.

    Contract:

    - ``__init__`` creates the parent directory if absent (so writers don't
      need to pre-create the session subdir).
    - ``write()`` injects a UTC ISO-8601 ``ts`` field with microsecond
      precision; if the caller's event already has a ``ts`` key, this
      class **overwrites** it (the writer owns timestamps).
    - Each ``write()`` opens-appends-closes the file. Per-line writes are
      flushed to the OS but not fsync'd; events written shortly before a
      hard crash may be lost. The spec routes resume through
      ``state.json``, so this best-effort durability is by design.
    - ``replay()`` raises ``json.JSONDecodeError`` on a malformed line —
      the generator terminates at that point. A torn final line from a
      crashed writer would lose subsequent events. Acceptable today
      because no production code path consumes ``replay()``; revisit this
      policy if it gets wired into a critical reader.
    - Validation of event shape is the caller's responsibility; this class
      accepts any ``dict[str, Any]``.
    """

    def __init__(self, path: Path) -> None:
        """Create or open an event log at ``path``.

        Creates the parent directory if absent — supports both writer
        and reader use, where the parent dir may or may not exist yet.
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: dict[str, Any]) -> None:
        """Append one event as a JSON line, injecting ``ts``.

        ``ts`` is set to ``datetime.now(timezone.utc)`` formatted as
        ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` (microsecond precision, ``Z``
        suffix). If ``event`` already contains a ``ts`` key, it is
        overwritten — the writer owns timestamps.
        """
        record = {**event, "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def replay(self) -> Iterable[dict[str, Any]]:
        """Yield each recorded event in insertion order.

        Returns silently if the file does not exist (replay of an empty
        or freshly-created session). Skips blank lines. Raises
        ``json.JSONDecodeError`` on the first malformed line and
        terminates — see class docstring for the rationale.
        """
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
