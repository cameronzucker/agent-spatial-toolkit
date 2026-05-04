"""Shared test-side helpers for tests that drive the CLI + wizard server.

The CLI launches a Flask server on an ephemeral port, prints the URL to
stdout, and exits when the lifecycle layer's idle timer or
``/api/finalize`` triggers shutdown. Tests that exercise this flow share
two pieces of logic:

- ``scrape_url_from_capfd`` reads the ``Open <url> ...`` line printed
  from the CLI's background thread.
- ``best_effort_finalize`` performs a non-fatal cleanup POST to
  ``/api/finalize`` so a mid-test failure doesn't leak the daemon
  thread + bound port into downstream tests in the same pytest session.

Lives in a sibling module rather than ``conftest.py`` because these are
plain imperative helpers (not pytest fixtures) and conftest.py is
conventionally reserved for fixtures + collection hooks.

The pyproject.toml pytest config matches ``test_*.py`` only, so this
module is *not* collected as a test file even though it lives in
``tests/``.
"""

from __future__ import annotations

import contextlib
import time
import urllib.error
import urllib.request

import pytest


def scrape_url_from_capfd(capfd: pytest.CaptureFixture[str], timeout_s: float = 5.0) -> str:
    """Poll captured stdout for the ``Open <url> ...`` line printed by cli._annotate.

    capfd (file-descriptor capture) is required: the CLI runs in a
    background thread and writes via flush=True; pytest's default ``capsys``
    misses cross-thread writes.
    """
    deadline = time.monotonic() + timeout_s
    accumulated = ""
    while time.monotonic() < deadline:
        captured = capfd.readouterr()
        accumulated += captured.out
        if "http://" in accumulated:
            for word in accumulated.split():
                if word.startswith("http://"):
                    return word.rstrip(".,")
        time.sleep(0.05)
    raise AssertionError(f"CLI did not print server URL within {timeout_s}s; got: {accumulated!r}")


def best_effort_finalize(url: str, timeout_s: float = 2.0) -> None:
    """Trigger /api/finalize, suppressing all errors.

    Used in test cleanup paths to ensure a mid-test failure doesn't leak
    the lifecycle server's bound port + daemon thread into downstream
    tests in the same pytest session.
    """
    with contextlib.suppress(Exception):
        req = urllib.request.Request(
            f"{url.rstrip('/')}/api/finalize",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            resp.read()
