"""Session directory management (spec §4 architecture)."""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Session:
    """An in-flight annotation session bound to a directory."""

    part_id: str
    session_dir: Path
    base_dir: Path
    timestamp: str
    suffix: str
    status: str = "in_progress"

    def state_path(self) -> Path:
        return self.session_dir / "state.json"

    def to_state_dict(self) -> dict:
        return {
            "part_id": self.part_id,
            "session_dir": str(self.session_dir),
            "timestamp": self.timestamp,
            "suffix": self.suffix,
            "status": self.status,
        }


def default_base_dir() -> Path:
    """Default session base directory: ~/.spatial-annotations/."""
    return Path(os.environ.get("HOME", "~")).expanduser() / ".spatial-annotations"


def session_dir_name(part_id: str, timestamp: str, suffix: str) -> str:
    """Compute the session directory name."""
    return f"{part_id}-{timestamp}-{suffix}"


def create_session(part_id: str, base_dir: Path | None = None) -> Session:
    """Create a new session directory and return a Session object."""
    if base_dir is None:
        base_dir = default_base_dir()
    base_dir.mkdir(parents=True, exist_ok=True)

    now = dt.datetime.now(dt.timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%S")
    suffix = secrets.token_hex(4)  # 8 hex chars
    name = session_dir_name(part_id, timestamp, suffix)

    session_dir = base_dir / name
    session_dir.mkdir(exist_ok=False)
    (session_dir / "photos").mkdir()
    (session_dir / "overlays").mkdir()

    session = Session(
        part_id=part_id,
        session_dir=session_dir,
        base_dir=base_dir,
        timestamp=timestamp,
        suffix=suffix,
    )
    state_path = session.state_path()
    tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(session.to_state_dict(), indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(state_path)
    return session


def load_session(session_dir: Path) -> Session:
    """Load an existing session from its directory."""
    state = json.loads((session_dir / "state.json").read_text())
    return Session(
        part_id=state["part_id"],
        session_dir=session_dir,
        base_dir=session_dir.parent,
        timestamp=state["timestamp"],
        suffix=state["suffix"],
        status=state.get("status", "in_progress"),
    )
