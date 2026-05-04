"""End-to-end test for Phase 2b — frame declaration (Task 1.D.4).

Phase 2b is purely declarative (spec §3 line 161): no photo interaction.
The user picks a frame preset, enters known dimensions, and advances.
The form's output is a list of named anchor positions stored on
``window.spatialState.frame.anchors`` for Phase 2c (1.D.5) to consume.

Test coverage
-------------
1. **Form shape**: phase-2b contains a preset ``<select>``, long-edge
   and short-edge dimension inputs, and a Next button.
2. **Preset semantics**: selecting the "Standard PCB" preset and entering
   86.5 mm × 84.5 mm yields the four corner anchors at canonical
   positions (per spec line 164's verbatim example).
3. **Phase advance**: Next click hides phase-2b, reveals phase-2c, and
   commits the computed anchors to ``window.spatialState.frame``.

Tests Phase 2b in isolation by programmatically revealing phase-2b
(via page.evaluate) instead of clicking through Phase 2a's Next. The
2a → 2b transition is already covered by tests/test_ui_phase_2a.py.
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from tests.lifecycle_helpers import best_effort_finalize, scrape_url_from_capfd

FIXTURES = Path(__file__).parent / "fixtures/synthetic_card"


def _navigate_with_retry(page: Page, url: str, deadline_s: float = 5.0) -> None:
    """Retry page.goto across the brief socket-bind race on slow CI runners."""
    import time

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


def test_phase_2b_form_computes_anchors_and_advances(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], page: Page
) -> None:
    """Phase 2b's preset + dimensions form computes 4 corner anchors on Next.

    Verifies the canonical PCB case from spec §3 line 164:
    "PCB long edge = 86.5 mm, short edge = 84.5 mm" → anchors at
    pcb_corner_origin (0,0,0), pcb_corner_x_max (86.5,0,0), pcb_corner_y_max
    (0,84.5,0), pcb_corner_xy_max (86.5,84.5,0).
    """
    from agent_spatial_toolkit import cli

    photo_path = FIXTURES / "test1.jpg"
    orig_start_server = cli.start_server
    server_ref: dict[str, object] = {"server": None}

    def _short_timeout_start(*args: object, **kwargs: object) -> object:
        kwargs["idle_timeout_seconds"] = 30.0
        server = orig_start_server(*args, **kwargs)
        server_ref["server"] = server
        return server

    cli.start_server = _short_timeout_start  # type: ignore[assignment]

    sessions_root = tmp_path / "sessions"
    cli_result: dict[str, int | None] = {"code": None}

    def _run_cli() -> None:
        cli_result["code"] = cli.main(
            argv=[
                "annotate",
                "--part-id",
                "uiphase2b",
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
        url = scrape_url_from_capfd(capfd)
        _navigate_with_retry(page, url)

        # Wait for app.js init (signalled by the thumbnail appearing).
        page.wait_for_selector(
            "#phase-2a-thumbnails .thumbnail",
            state="attached",
            timeout=5_000,
        )

        # Reveal phase-2b directly (skip 2a's Next click — that path is
        # covered by tests/test_ui_phase_2a.py).
        page.evaluate("""() => {
            document.getElementById('phase-2a').hidden = true;
            document.getElementById('phase-2b').hidden = false;
        }""")

        # ─── Form shape ─────────────────────────────────────────────────
        preset_select = page.locator("#phase-2b select.frame-preset")
        assert preset_select.count() == 1, "phase-2b must include a frame-preset select"
        preset_options = preset_select.locator("option").all_text_contents()
        assert len(preset_options) >= 2, (
            f"preset dropdown should have at least 2 options (placeholder + 1 real), "
            f"got {len(preset_options)}: {preset_options!r}"
        )

        long_input = page.locator("#phase-2b input.long-edge-mm")
        short_input = page.locator("#phase-2b input.short-edge-mm")
        next_btn = page.locator("#phase-2b-next")
        assert long_input.count() == 1, "phase-2b must include a long-edge-mm input"
        assert short_input.count() == 1, "phase-2b must include a short-edge-mm input"
        assert next_btn.count() == 1, "phase-2b must include a Next button"

        # ─── Fill form with the canonical PCB case ──────────────────────
        # Standard PCB preset, 86.5 × 84.5 mm — the verbatim example from
        # spec §3 line 164.
        preset_select.select_option(value="pcb_standard")
        long_input.fill("86.5")
        short_input.fill("84.5")

        # ─── Next click → anchors computed + phase advance ──────────────
        next_btn.click()

        page.wait_for_selector("#phase-2c:not([hidden])", timeout=2_000)
        assert not page.locator("#phase-2b").is_visible(), "phase-2b must hide after Next"
        assert page.locator("#phase-2c").is_visible(), "phase-2c must reveal after Next"

        # ─── Anchors stored on spatialState.frame ───────────────────────
        frame_state = page.evaluate("() => window.spatialState.frame")
        assert isinstance(frame_state, dict), (
            f"spatialState.frame must be an object, got {frame_state!r}"
        )
        assert frame_state.get("preset") == "pcb_standard"
        assert frame_state.get("longMm") == 86.5
        assert frame_state.get("shortMm") == 84.5

        anchors = frame_state.get("anchors")
        assert isinstance(anchors, list) and len(anchors) == 4, (
            f"Standard PCB preset should produce 4 corner anchors; got {anchors!r}"
        )
        # Anchor IDs and positions must match spec §3 line 164's example
        # (origin at bottom-left, +X long edge, +Y short edge).
        anchors_by_id = {a["id"]: a["xyz"] for a in anchors}
        assert anchors_by_id == {
            "pcb_corner_origin": [0, 0, 0],
            "pcb_corner_x_max": [86.5, 0, 0],
            "pcb_corner_y_max": [0, 84.5, 0],
            "pcb_corner_xy_max": [86.5, 84.5, 0],
        }, f"anchor positions don't match spec example: {anchors_by_id!r}"
    finally:
        cli.start_server = orig_start_server  # type: ignore[assignment]
        if cli_thread.is_alive():
            if url is not None:
                best_effort_finalize(url)
            elif server_ref["server"] is not None:
                with contextlib.suppress(Exception):
                    server_ref["server"].shutdown()  # type: ignore[attr-defined]
            cli_thread.join(timeout=10.0)
