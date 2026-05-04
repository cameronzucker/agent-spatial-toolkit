"""Session directory management (spec §4 architecture)."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

_PART_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_part_id(part_id: str) -> None:
    """Reject part_ids that would produce invalid or escape-prone session paths.

    Allowed: ASCII alphanumerics, dots, underscores, hyphens. Forbidden: empty
    strings, '.' / '..' (filesystem aliases), path separators, anything that
    could escape the session base directory.
    """
    if not _PART_ID_PATTERN.fullmatch(part_id):
        raise ValueError(f"part_id must match {_PART_ID_PATTERN.pattern!r}; got {part_id!r}")
    if part_id in (".", ".."):
        raise ValueError(f"part_id must not be '.' or '..'; got {part_id!r}")


class SessionLoadError(ValueError):
    """Raised when load_session cannot reconstruct a Session from its directory."""


@dataclass
class Session:
    """An in-flight annotation session bound to a directory."""

    part_id: str
    session_dir: Path
    base_dir: Path
    timestamp: str
    suffix: str
    # NOTE: spec defines TWO files — state.json (this dataclass's full state)
    # and status.json (a separate small file with just {"status": "done"|...})
    # for agent polling per spec §3 lines 122, 129. The lifecycle/finalize task
    # is responsible for emitting status.json on the wizard's "finish" button.
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
    _validate_part_id(part_id)
    return f"{part_id}-{timestamp}-{suffix}"


def create_session(part_id: str, base_dir: Path | None = None) -> Session:
    """Create a new session directory and return a Session object."""
    _validate_part_id(part_id)
    if base_dir is None:
        base_dir = default_base_dir()
    base_dir.mkdir(parents=True, exist_ok=True)

    now = dt.datetime.now(dt.timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%S")
    suffix = secrets.token_hex(4)  # 8 hex chars
    name = session_dir_name(part_id, timestamp, suffix)

    session_dir = base_dir / name
    session_dir.mkdir(exist_ok=False)
    try:
        (session_dir / "photos").mkdir()
        (session_dir / "overlays").mkdir()

        session = Session(
            part_id=part_id,
            session_dir=session_dir,
            base_dir=base_dir,
            timestamp=timestamp,
            suffix=suffix,
        )
        # Atomic write: matches PR #17/#18 precedent.
        state_path = session.state_path()
        tmp_path = state_path.with_suffix(state_path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(session.to_state_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(state_path)
        return session
    except BaseException:
        # If anything inside the session_dir setup fails, remove the partial
        # directory so we don't leave half-built sessions for load_session.
        shutil.rmtree(session_dir, ignore_errors=True)
        raise


def load_session(session_dir: Path) -> Session:
    """Load an existing session from its directory.

    Raises SessionLoadError if state.json is missing, malformed, or missing
    required fields. The error message includes the session_dir for context.
    """
    state_path = session_dir / "state.json"
    try:
        state_text = state_path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise SessionLoadError(f"state.json not found in {session_dir}") from e
    except OSError as e:
        raise SessionLoadError(f"could not read {state_path}: {e}") from e

    try:
        state = json.loads(state_text)
    except json.JSONDecodeError as e:
        raise SessionLoadError(f"state.json in {session_dir} is malformed: {e}") from e

    try:
        return Session(
            part_id=state["part_id"],
            session_dir=session_dir,
            base_dir=session_dir.parent,
            timestamp=state["timestamp"],
            suffix=state["suffix"],
            status=state.get("status", "in_progress"),
        )
    except KeyError as e:
        raise SessionLoadError(f"state.json in {session_dir} missing required field: {e}") from e
