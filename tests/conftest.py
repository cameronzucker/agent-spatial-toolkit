"""Shared pytest fixtures for agent-spatial-toolkit."""

from pathlib import Path

import pytest

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
