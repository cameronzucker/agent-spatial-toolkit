"""Shared pytest fixtures for agent-spatial-toolkit.

Provides:
- ``fixtures_dir`` / ``synthetic_card_dir``: filesystem fixture paths.
- ``_StubServer``: minimal Server stand-in for /api/finalize shutdown.
- ``app_factory``: pytest fixture returning a callable that builds
  fresh ``(app, session, server)`` tuples per test.

Imported automatically by pytest from sibling test files (no explicit
import needed in test modules).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from agent_spatial_toolkit.server.app import create_app
from agent_spatial_toolkit.server.session import create_session

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Return the absolute path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def synthetic_card_dir(fixtures_dir: Path) -> Path:
    """Path to synthetic-card smoke fixture (created in Task 1.E.1)."""
    p = fixtures_dir / "synthetic_card"
    if not p.exists():
        pytest.skip("synthetic_card fixture not yet generated")
    return p


class _StubServer:
    """Minimal Server stand-in for /api/finalize shutdown.

    The lifecycle.Server class wraps a real WSGI socket; tests use Flask's
    test client, so a stub that just records shutdown() is sufficient.
    """

    def __init__(self) -> None:
        self.shutdown_called = False

    def shutdown(self) -> None:
        self.shutdown_called = True


@pytest.fixture
def app_factory(tmp_path: Path) -> Callable:
    """Return a factory producing (app, session, server) tuples per test."""

    def _make() -> tuple:
        session = create_session(part_id="testpart", base_dir=tmp_path / "sessions")
        server = _StubServer()
        app = create_app(server=server, session=session)
        app.config["TESTING"] = True
        return app, session, server

    return _make
