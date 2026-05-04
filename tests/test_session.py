"""Tests for server/session.py — session directory creation and management."""

import json
from pathlib import Path

from agent_spatial_toolkit.server.session import (
    create_session,
    load_session,
    session_dir_name,
)


def test_session_dir_name_format() -> None:
    """Session directory name is <part_id>-YYYYMMDDTHHMMSS-<8char>."""
    name = session_dir_name(part_id="x1207", timestamp="20260503T221400", suffix="a1b2c3d4")
    assert name == "x1207-20260503T221400-a1b2c3d4"


def test_create_session_makes_dir_with_state(tmp_path: Path) -> None:
    base = tmp_path / "sessions"
    session = create_session(part_id="testpart", base_dir=base)

    assert session.session_dir.exists()
    assert session.session_dir.parent == base
    assert (session.session_dir / "photos").is_dir()
    assert (session.session_dir / "overlays").is_dir()

    state_file = session.session_dir / "state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert state["part_id"] == "testpart"
    assert state["status"] == "in_progress"


def test_load_session_round_trips(tmp_path: Path) -> None:
    """A created session can be loaded back from its directory."""
    base = tmp_path / "sessions"
    s1 = create_session(part_id="p", base_dir=base)
    s2 = load_session(s1.session_dir)
    assert s2.part_id == "p"
    assert s2.session_dir == s1.session_dir


def test_session_default_base_dir_is_user_home(monkeypatch, tmp_path: Path) -> None:
    """When no --out is passed, session_dir defaults to ~/.spatial-annotations/."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from agent_spatial_toolkit.server.session import default_base_dir

    assert default_base_dir() == tmp_path / ".spatial-annotations"
