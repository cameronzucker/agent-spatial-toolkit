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
