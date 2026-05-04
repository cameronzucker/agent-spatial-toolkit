"""End-to-end UI smoke test for the wizard shell (Task 1.D.1).

Drives the real CLI (``cli.main``) → Flask ``GET /`` → Playwright DOM
assertions to verify the wizard shell renders correctly on first paint:

- All six phase containers (``#phase-2a`` … ``#phase-2f``) exist in the DOM
- Only ``#phase-2a`` is visible (the active phase on first load)
- ``#phase-2b`` … ``#phase-2f`` are present but hidden

The test then captures a baseline screenshot for visual-regression follow-up
(1.D.9 styling pass) and triggers ``/api/finalize`` to release the daemon
thread + bound port before the next test in the session.

Where this differs from ``test_cli_integration.py``
---------------------------------------------------
That test exercises the math + emit pipeline by direct ``/api/anchors``,
``/api/feature``, and ``/api/finalize`` POSTs. This test exercises the
*UI surface* served at ``GET /`` via a real headless browser. The CLI
launch + URL-scrape + finalize-on-cleanup machinery is the same; only
the assertions differ.

Helper duplication
------------------
``_scrape_url_from_capfd`` and ``_best_effort_finalize`` are duplicated
from ``test_cli_integration.py`` to keep this PR's scope narrow. A
``conftest.py`` extraction is a clean follow-up once a third caller
appears (likely Task 1.D.5 — first phase that POSTs to a real backend).
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page

FIXTURES = Path(__file__).parent / "fixtures/synthetic_card"

PHASES = ("2a", "2b", "2c", "2d", "2e", "2f")


def _scrape_url_from_capfd(capfd: pytest.CaptureFixture[str], timeout_s: float = 5.0) -> str:
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


def _best_effort_finalize(url: str, timeout_s: float = 2.0) -> None:
    """Trigger /api/finalize, suppressing all errors. Used in test cleanup
    paths to ensure a mid-test failure doesn't leak the lifecycle server's
    bound port + daemon thread into downstream tests in the same session.
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


def _http_post_json(url: str, body: dict[str, Any], timeout_s: float = 5.0) -> dict[str, Any]:
    """POST a JSON body and return the decoded JSON response."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        assert resp.status == 200, f"POST {url} returned {resp.status}"
        return json.loads(resp.read().decode("utf-8"))


def _navigate_with_retry(page: Page, url: str, deadline_s: float = 5.0) -> None:
    """Retry page.goto across the brief socket-bind race on slow CI runners.

    Mirrors ``_retry_get_json`` in test_cli_integration.py: the CLI prints
    the URL just *before* the WSGI listener finishes its accept() bind, so
    a same-thread navigation can race the socket open.
    """
    deadline = time.monotonic() + deadline_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=2_000)
            return
        except Exception as e:  # playwright wraps net/timeout errors
            last_exc = e
            time.sleep(0.05)
    raise AssertionError(
        f"page.goto({url}) never succeeded within {deadline_s}s; last={last_exc!r}"
    )


def test_ui_wizard_shell_renders_all_six_phase_containers(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], page: Page
) -> None:
    """The wizard shell at ``GET /`` exposes six phase containers.

    Spec §3 Phase 2a–2f defines the wizard's six-phase state machine:
    upload+validate → frame declaration → per-photo anchors → feature
    labeling → validation overlay → emit. The shell must reserve a DOM
    container for each phase, with only the active phase visible.
    """
    from agent_spatial_toolkit import cli

    photo_path = FIXTURES / "test1.jpg"

    # Cap idle timeout so the test fails fast (not 30 min) if /api/finalize
    # never reaches the lifecycle layer for any reason. Same trick used in
    # test_cli_integration.py.
    orig_start_server = cli.start_server

    def _short_timeout_start(*args: object, **kwargs: object) -> object:
        kwargs["idle_timeout_seconds"] = 30.0
        return orig_start_server(*args, **kwargs)

    cli.start_server = _short_timeout_start  # type: ignore[assignment]

    sessions_root = tmp_path / "sessions"
    cli_result: dict[str, int | None] = {"code": None}

    def _run_cli() -> None:
        cli_result["code"] = cli.main(
            argv=[
                "annotate",
                "--part-id",
                "uismoke",
                "--photos",
                str(photo_path),
                "--out",
                str(sessions_root),
            ]
        )

    cli_thread = threading.Thread(target=_run_cli, daemon=True)
    cli_thread.start()

    url: str | None = None
    try:
        url = _scrape_url_from_capfd(capfd)

        _navigate_with_retry(page, url)

        # All six phase containers must exist in the DOM (spec §3 Phase 2a–2f).
        for phase in PHASES:
            locator = page.locator(f"#phase-{phase}")
            assert locator.count() == 1, (
                f"#phase-{phase} not found in DOM; wizard shell must reserve a "
                f"container for every phase 2a–2f"
            )

        # Phase 2a is the active phase on first paint (spec §3 — wizard begins
        # at upload+validate).
        assert page.locator("#phase-2a").is_visible(), (
            "#phase-2a must be visible on first paint (active phase per spec §3)"
        )

        # Phases 2b–2f are present-but-hidden until the user advances. Plan
        # 1.D.1 calls this out explicitly: "all hidden except the active phase".
        for phase in PHASES[1:]:
            assert not page.locator(f"#phase-{phase}").is_visible(), (
                f"#phase-{phase} must be hidden on first paint"
            )

        # Capture a baseline screenshot. Written under tmp_path (not committed)
        # — visual-regression baselines belong with 1.D.9's styling pass.
        screenshot_path = tmp_path / "phase-2a-empty.png"
        page.screenshot(path=str(screenshot_path))
        assert screenshot_path.is_file()
        assert screenshot_path.stat().st_size > 0, "screenshot must be non-empty"

        # Clean shutdown — /api/finalize releases the daemon thread + bound port.
        finalize_resp = _http_post_json(f"{url.rstrip('/')}/api/finalize", {})
        assert finalize_resp["status"] == "done"

        cli_thread.join(timeout=10.0)
        assert not cli_thread.is_alive(), "CLI thread did not exit after /api/finalize"
        assert cli_result["code"] == 0
    finally:
        cli.start_server = orig_start_server  # type: ignore[assignment]
        if cli_thread.is_alive() and url is not None:
            _best_effort_finalize(url)
            cli_thread.join(timeout=10.0)
