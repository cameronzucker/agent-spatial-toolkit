"""Tests for server/session.py — session directory creation and management."""

import json
from pathlib import Path

import pytest

from agent_spatial_toolkit.server.session import (
    SessionLoadError,
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


def test_create_session_rejects_path_traversal(tmp_path: Path) -> None:
    """A part_id like '../escape' is rejected (path traversal vulnerability)."""
    base = tmp_path / "sessions"
    with pytest.raises(ValueError, match="part_id"):
        create_session(part_id="../escape", base_dir=base)


def test_create_session_rejects_slashes(tmp_path: Path) -> None:
    """A part_id containing '/' is rejected (would create nested dirs)."""
    base = tmp_path / "sessions"
    with pytest.raises(ValueError, match="part_id"):
        create_session(part_id="a/b", base_dir=base)


def test_create_session_rejects_dot_aliases(tmp_path: Path) -> None:
    """part_id of '.' or '..' is rejected even though regex would otherwise allow them."""
    base = tmp_path / "sessions"
    with pytest.raises(ValueError, match="part_id"):
        create_session(part_id=".", base_dir=base)
    with pytest.raises(ValueError, match="part_id"):
        create_session(part_id="..", base_dir=base)


def test_load_session_raises_on_missing_state(tmp_path: Path) -> None:
    """A directory without state.json yields SessionLoadError with path context."""
    bogus_dir = tmp_path / "no_state_here"
    bogus_dir.mkdir()
    with pytest.raises(SessionLoadError, match="state.json not found"):
        load_session(bogus_dir)


def test_load_session_raises_on_malformed_state(tmp_path: Path) -> None:
    """A corrupt state.json yields SessionLoadError mentioning malformed."""
    bogus_dir = tmp_path / "corrupt"
    bogus_dir.mkdir()
    (bogus_dir / "state.json").write_text("{ not valid json")
    with pytest.raises(SessionLoadError, match="malformed"):
        load_session(bogus_dir)
