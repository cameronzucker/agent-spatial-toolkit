"""Playwright happy-path test for Phase 2c (Task 1.D.5 PR-α).

Mirrors tests/test_ui_phase_2b.py's structure: spin up the wizard server
on the synthetic-card fixture, programmatically reveal Phase 2c after
seeding spatialState.frame.anchors via page.evaluate, click anchor
buttons + canvas to record pixels, click Solve, verify the result text
appears and the Next button enables.

Two scenarios:
1. Happy path with the standard PCB preset's 4 anchors and a resolvable
   lens (pi_camera_module_3_standard) — verifies POST /api/anchors
   round-trip and result-text rendering.
2. No-lens-picked path — verifies the UI surfaces the server's 400
   error rather than silently failing.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from tests.lifecycle_helpers import best_effort_finalize, scrape_url_from_capfd

FIXTURES = Path(__file__).parent / "fixtures/synthetic_card"


def _navigate_with_retry(page: Page, url: str, deadline_s: float = 5.0) -> None:
    """Retry page.goto across the brief socket-bind race on slow CI runners.

    Duplicated from tests/test_ui_phase_2b.py — a future cleanup task
    could move this into tests/lifecycle_helpers.py.
    """
    deadline = time.monotonic() + deadline_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=2_000)
            return
        except PlaywrightError as e:
            last_exc = e
            time.sleep(0.05)
    raise AssertionError(
        f"page.goto({url}) never succeeded within {deadline_s}s; last={last_exc!r}"
    )


def _start_cli_and_get_url(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    photo_path: Path,
    part_id: str,
) -> tuple[str, threading.Thread]:
    """Spin up the wizard CLI on a daemon thread and return its served URL.

    Mirrors the pattern in tests/test_ui_phase_2b.py — monkey-patches
    cli.start_server to use a short idle timeout so leaked daemons don't
    keep the test session alive.
    """
    from agent_spatial_toolkit import cli

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
                part_id,
                "--photos",
                str(photo_path),
                "--out",
                str(sessions_root),
            ]
        )

    cli_thread = threading.Thread(target=_run_cli, daemon=True)
    cli_thread.start()

    url = scrape_url_from_capfd(capfd)
    return url, cli_thread


def _reveal_phase_2c_with_seeded_state(
    page: Page, lens_id: str | None = "pi_camera_module_3_standard"
) -> None:
    """Skip Phase 2a/2b interaction; seed spatialState directly and reveal Phase 2c.

    Sets photo.lens (so /api/anchors gets a lens_id) and frame.anchors (the
    standard PCB preset's 4 corners at 86.5 mm × 84.5 mm). Then sets the
    `hidden` attribute on #phase-2c to false, which fires the wirePhase2c
    MutationObserver and triggers card render.
    """
    page.evaluate(
        """(lensId) => {
            // Seed state for the first photo
            const photoIds = Object.keys(window.spatialState.photos || {});
            if (photoIds.length > 0 && lensId) {
                window.spatialState.photos[photoIds[0]].lens = lensId;
            }
            // Seed Phase 2b's frame anchors
            window.spatialState.frame = {
                preset: 'pcb_standard',
                longMm: 86.5,
                shortMm: 84.5,
                notes: '',
                anchors: [
                    { id: 'pcb_corner_origin',  xyz: [0, 0, 0] },
                    { id: 'pcb_corner_x_max',   xyz: [86.5, 0, 0] },
                    { id: 'pcb_corner_y_max',   xyz: [0, 84.5, 0] },
                    { id: 'pcb_corner_xy_max',  xyz: [86.5, 84.5, 0] },
                ],
            };
            // Hide 2a/2b, reveal 2c (fires the MutationObserver)
            document.getElementById('phase-2a').hidden = true;
            document.getElementById('phase-2b').hidden = true;
            document.getElementById('phase-2c').hidden = false;
        }""",
        lens_id,
    )


def _click_4_anchors_at_canvas_corners(page: Page, card: object) -> None:
    """For the given Phase 2c card, click each of the 4 anchor buttons in turn,
    then synthesise a click event directly on the canvas. Pixel positions don't
    need to be physically meaningful — solvePnP may return high RMS but the
    endpoint still returns 200; the test asserts presence of result text, not
    solver accuracy.

    We dispatch a synthetic MouseEvent via dispatchEvent rather than relying on
    page.mouse.click so that the click lands on the canvas element regardless
    of any CSS transform or overflow: hidden clipping that might intercept a
    real pointer event on a headless runner.
    """
    from playwright.sync_api import Locator

    assert isinstance(card, Locator)
    canvas = card.locator("canvas.phase-2c-canvas")
    # Wait for loadImageToCanvas to set the canvas dimensions (captureClick is
    # registered in the same .then() callback, so once width > 0 the click
    # handler is already wired).
    page.wait_for_function(
        "el => el && el.width > 0 && el.height > 0",
        arg=canvas.element_handle(),
        timeout=10_000,
    )

    anchor_buttons = card.locator(".phase-2c-anchor-btn")
    assert anchor_buttons.count() == 4, "PCB preset should produce exactly 4 anchors"

    # Four distinct pixel offsets within the canvas (corners).
    # clientX/Y are irrelevant for the captureClick handler — it reads
    # e.clientX minus getBoundingClientRect().left, so we compute those
    # from the actual bounding rect and pass matching coords.
    canvas_handle = canvas.element_handle()
    assert canvas_handle is not None

    for i in range(4):
        # 1. Arm the anchor (set state.currentAnchorId via the button click).
        anchor_buttons.nth(i).click()
        # 2. Dispatch a synthetic click to the canvas element. Use a small
        #    offset from a corner so the coords land within the image area.
        page.evaluate(
            """([canvasEl, idx]) => {
                var rect = canvasEl.getBoundingClientRect();
                var offsets = [
                    [100, 100],
                    [rect.width - 100, 100],
                    [100, rect.height - 100],
                    [rect.width - 100, rect.height - 100],
                ];
                var off = offsets[idx] || [100, 100];
                var cx = rect.left + off[0];
                var cy = rect.top  + off[1];
                canvasEl.dispatchEvent(new MouseEvent('click', {
                    bubbles: true, cancelable: true,
                    clientX: cx, clientY: cy,
                }));
            }""",
            [canvas_handle, i],
        )


def test_phase_2c_anchor_click_flow_happy_path(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], page: Page
) -> None:
    """End-to-end: load wizard → seed state → click 4 anchors → Solve → success."""
    photo_path = FIXTURES / "test1.jpg"
    url: str | None = None
    try:
        url, _ = _start_cli_and_get_url(tmp_path, capfd, photo_path, "uiphase2c_happy")
        _navigate_with_retry(page, url)
        page.wait_for_selector(
            "#phase-2a-thumbnails .thumbnail",
            state="attached",
            timeout=5_000,
        )

        _reveal_phase_2c_with_seeded_state(page, lens_id="pi_camera_module_3_standard")

        first_card = page.locator(".phase-2c-card").first
        _click_4_anchors_at_canvas_corners(page, first_card)

        solve_btn = first_card.locator(".phase-2c-solve-btn")
        page.wait_for_function(
            "el => el && !el.disabled",
            arg=solve_btn.element_handle(),
            timeout=5_000,
        )
        solve_btn.click()

        # Wait for the result area to leave the in-flight "Solving pose…" state.
        result_locator = first_card.locator(".phase-2c-result")
        page.wait_for_function(
            "el => el && el.textContent && el.textContent.length > 0"
            " && !el.textContent.includes('Solving pose')",
            arg=result_locator.element_handle(),
            timeout=15_000,
        )
        result_text = result_locator.text_content() or ""
        # Require the success path: 4 anchors at known PCB corners + a
        # resolvable lens (pi_camera_module_3_standard) MUST produce a pose.
        # If the result is "Solve failed", something regressed in the
        # POST /api/anchors round-trip — fail the test. (Earlier draft of
        # this test accepted either outcome; tightened per cross-model
        # review feedback.)
        assert "Pose solved" in result_text, f"Expected 'Pose solved'; got: {result_text!r}"

        # Next button must enable after a successful solve.
        next_btn = page.locator("#phase-2c-next")
        page.wait_for_function(
            "el => el && !el.disabled",
            arg=next_btn.element_handle(),
            timeout=5_000,
        )
    finally:
        if url is not None:
            best_effort_finalize(url)


def test_phase_2c_no_lens_picked_surfaces_error(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], page: Page
) -> None:
    """If no lens is picked, Solve POSTs and the server's 400 surfaces visibly.

    The UI must surface the server's error rather than silently failing.
    """
    photo_path = FIXTURES / "test1.jpg"
    url: str | None = None
    try:
        url, _ = _start_cli_and_get_url(tmp_path, capfd, photo_path, "uiphase2c_nolens")
        _navigate_with_retry(page, url)
        page.wait_for_selector(
            "#phase-2a-thumbnails .thumbnail",
            state="attached",
            timeout=5_000,
        )

        # Reveal Phase 2c without picking a lens (lens_id=None)
        _reveal_phase_2c_with_seeded_state(page, lens_id=None)

        first_card = page.locator(".phase-2c-card").first
        _click_4_anchors_at_canvas_corners(page, first_card)

        solve_btn = first_card.locator(".phase-2c-solve-btn")
        page.wait_for_function(
            "el => el && !el.disabled",
            arg=solve_btn.element_handle(),
            timeout=5_000,
        )
        solve_btn.click()

        # Server returns 400 ("must provide either intrinsics or lens_id");
        # UI sets .phase-2c-result to error class with "Solve failed:" prefix.
        page.wait_for_selector(".phase-2c-result.error", state="visible", timeout=10_000)
        err_text = first_card.locator(".phase-2c-result.error").text_content() or ""
        assert "Solve failed" in err_text, f"Expected 'Solve failed:' prefix, got: {err_text!r}"
    finally:
        if url is not None:
            best_effort_finalize(url)
