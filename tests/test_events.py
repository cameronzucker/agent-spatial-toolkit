"""Tests for server/events.py — append-only JSONL event stream."""

import json
from pathlib import Path

from agent_spatial_toolkit.server.events import EventLog


def test_event_log_appends_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    log.write({"type": "anchor_clicked", "anchor_id": "o", "pixel": [100, 200]})
    log.write({"type": "feature_added", "feature_id": "x"})

    lines = log_path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "anchor_clicked"
    assert json.loads(lines[1])["feature_id"] == "x"


def test_event_log_includes_timestamp(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    log.write({"type": "test"})
    line = (tmp_path / "events.jsonl").read_text().splitlines()[0]
    rec = json.loads(line)
    assert "ts" in rec
    # ISO-8601 UTC: '...T...Z'
    assert "T" in rec["ts"] and rec["ts"].endswith("Z")


def test_event_log_replay(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    log.write({"type": "a", "n": 1})
    log.write({"type": "b", "n": 2})
    events = list(log.replay())
    assert len(events) == 2
    assert events[0]["n"] == 1
    assert events[1]["n"] == 2


def test_event_log_replay_on_missing_file_yields_nothing(tmp_path: Path) -> None:
    """A replay() against a never-written path yields zero events, not an error."""
    log = EventLog(tmp_path / "events.jsonl")
    events = list(log.replay())
    assert events == []


def test_event_log_replay_skips_blank_lines(tmp_path: Path) -> None:
    """Blank lines in the file are silently skipped during replay."""
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    log.write({"type": "first"})
    log_path.write_text(log_path.read_text() + "\n\n   \n", encoding="utf-8")
    log.write({"type": "second"})
    events = list(log.replay())
    assert [e["type"] for e in events] == ["first", "second"]


def test_event_log_write_does_not_mutate_caller_dict(tmp_path: Path) -> None:
    """write() injects ts on a copy; the caller's dict is unchanged."""
    log = EventLog(tmp_path / "events.jsonl")
    event = {"type": "anchor_clicked", "anchor_id": "o"}
    log.write(event)
    assert "ts" not in event
    assert event == {"type": "anchor_clicked", "anchor_id": "o"}


def test_event_log_multi_instance_appends(tmp_path: Path) -> None:
    """A second EventLog at the same path appends; doesn't truncate."""
    log_path = tmp_path / "events.jsonl"
    log_a = EventLog(log_path)
    log_a.write({"type": "first"})
    log_b = EventLog(log_path)
    log_b.write({"type": "second"})
    events = list(log_b.replay())
    assert [e["type"] for e in events] == ["first", "second"]
