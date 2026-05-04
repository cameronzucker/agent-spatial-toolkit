"""Playwright happy-path test for Phase 2d (Task 1.D.6 PR-α).

Walks through Phases 2a → 2b → 2c (real Solve POST so server has a pose)
then exercises Phase 2d: click on canvas → enter label → click Add → verify
feature row appears + Next button enables.

Mirrors the structure of tests/test_ui_phase_2c.py.
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

    Duplicated from tests/test_ui_phase_2c.py — a future cleanup task could
    DRY this into tests/lifecycle_helpers.py.
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
    """Spin up the wizard CLI on a daemon thread; return its served URL."""
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


def _walk_phase_2a_to_2c_solve(page: Page) -> None:
    """Walk through Phase 2a/2b/2c on the synthetic card fixture so the server
    has a real pose in mem["photos"][photo_id] before Phase 2d's click + POST.
    """
    page.wait_for_selector("#phase-2a-thumbnails .thumbnail", state="attached", timeout=5_000)

    # Phase 2a: pick standard Pi camera lens
    first_lens_select = page.locator(".thumbnail .lens-select").first
    first_lens_select.select_option(value="pi_camera_module_3_standard")
    page.locator("#phase-2a-next").click()
    page.wait_for_selector("#phase-2b", state="visible")

    # Phase 2b: PCB preset + dimensions
    page.locator("#phase-2b-preset").select_option(value="pcb_standard")
    page.locator("#phase-2b-long-edge").fill("86.5")
    page.locator("#phase-2b-short-edge").fill("84.5")
    page.locator("#phase-2b-next").click()
    page.wait_for_selector("#phase-2c", state="visible")

    # Phase 2c: click 4 anchors + Solve
    first_card = page.locator(".phase-2c-card").first
    canvas = first_card.locator("canvas.phase-2c-canvas")
    page.wait_for_function(
        "el => el && el.width > 0 && el.height > 0",
        arg=canvas.element_handle(),
        timeout=5_000,
    )
    canvas_handle = canvas.element_handle()
    assert canvas_handle is not None

    anchor_buttons = first_card.locator(".phase-2c-anchor-btn")
    assert anchor_buttons.count() == 4

    for i in range(4):
        anchor_buttons.nth(i).click()
        # Use dispatchEvent — page.mouse.click() can't reliably reach the
        # canvas in headless Chromium when the section just became visible.
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

    solve_btn = first_card.locator(".phase-2c-solve-btn")
    page.wait_for_function(
        "el => el && !el.disabled",
        arg=solve_btn.element_handle(),
        timeout=5_000,
    )
    solve_btn.click()

    # Wait for "Pose solved" — server must have a real pose for /api/feature to work
    result_locator = first_card.locator(".phase-2c-result")
    page.wait_for_function(
        "el => el && el.textContent && el.textContent.includes('Pose solved')",
        arg=result_locator.element_handle(),
        timeout=15_000,
    )

    # Advance to Phase 2d
    next_btn_2c = page.locator("#phase-2c-next")
    page.wait_for_function(
        "el => el && !el.disabled",
        arg=next_btn_2c.element_handle(),
        timeout=5_000,
    )
    next_btn_2c.click()
    page.wait_for_selector("#phase-2d", state="visible")


def test_phase_2d_feature_click_and_label_happy_path(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], page: Page
) -> None:
    """End-to-end: walk 2a→2c → reveal 2d → click on canvas → enter label →
    Add → verify feature appears in panel and Next enables."""
    photo_path = FIXTURES / "test1.jpg"
    url: str | None = None
    try:
        url, _ = _start_cli_and_get_url(tmp_path, capfd, photo_path, "uiphase2d_happy")
        _navigate_with_retry(page, url)
        _walk_phase_2a_to_2c_solve(page)

        # Phase 2d: card should appear for the photo
        page.wait_for_selector(".phase-2d-card", state="visible", timeout=5_000)
        card = page.locator(".phase-2d-card").first

        canvas = card.locator("canvas.phase-2d-canvas")
        page.wait_for_function(
            "el => el && el.width > 0 && el.height > 0",
            arg=canvas.element_handle(),
            timeout=5_000,
        )
        canvas_handle = canvas.element_handle()
        assert canvas_handle is not None

        # Click on the canvas at the centre using dispatchEvent — same workaround
        # as Phase 2c for just-revealed canvas sections in headless Chromium.
        page.evaluate(
            """(canvasEl) => {
                var rect = canvasEl.getBoundingClientRect();
                var cx = rect.left + rect.width / 2;
                var cy = rect.top  + rect.height / 2;
                canvasEl.dispatchEvent(new MouseEvent('click', {
                    bubbles: true, cancelable: true,
                    clientX: cx, clientY: cy,
                }));
            }""",
            canvas_handle,
        )

        # Status text should include "Pixel captured"
        status = card.locator(".phase-2d-status")
        page.wait_for_function(
            "el => el && el.textContent && el.textContent.includes('Pixel captured')",
            arg=status.element_handle(),
            timeout=3_000,
        )

        # Enter a label and click Add
        label_input = card.locator(".phase-2d-label-input")
        label_input.fill("usb_c")
        add_btn = card.locator(".phase-2d-add-btn")
        page.wait_for_function(
            "el => el && !el.disabled",
            arg=add_btn.element_handle(),
            timeout=2_000,
        )
        add_btn.click()

        # Verify a feature row appears with the label + xyz_mm coordinates
        page.wait_for_selector(".phase-2d-feature-row", state="visible", timeout=10_000)
        feature_row = card.locator(".phase-2d-feature-row").first
        row_text = feature_row.text_content() or ""
        assert "usb_c" in row_text, f"Expected 'usb_c' in feature row: {row_text!r}"
        assert "mm" in row_text, f"Expected xyz_mm formatted text: {row_text!r}"
        # Regression guard: cross-model review of PR #48 caught that the
        # client read resp.data.xyz_mm but the server returns pcb_xyz_mm.
        # The bug surfaced as every row showing (0.0, 0.0, 0.0) mm with no
        # error. Assert the displayed xyz is not all-zero — a click at canvas
        # center on the synthetic card with a real pose must ray-cast to a
        # non-zero point in the part frame.
        assert "(0.0, 0.0, 0.0)" not in row_text, (
            f"Feature row shows all-zero xyz; client may be reading wrong response key: {row_text!r}"
        )

        # Next button must enable after at least one feature is recorded
        next_btn = page.locator("#phase-2d-next")
        page.wait_for_function(
            "el => el && !el.disabled",
            arg=next_btn.element_handle(),
            timeout=3_000,
        )
    finally:
        if url is not None:
            best_effort_finalize(url)
