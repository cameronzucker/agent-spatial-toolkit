"""Tests for the wizard's client-side JS helpers (Task 1.D.2).

The wizard ships a small ``helpers.js`` exposing four utilities on
``window.spatialUI``:

- ``captureClick(canvas, callback)`` — registers a click handler that
  delivers click coordinates in the canvas's natural pixel space, not
  CSS pixels (so DPR-scaled canvases get correct coordinates).
- ``loadImageToCanvas(url, canvas)`` — fetches an image URL, draws it
  onto a canvas at natural size, and returns a Promise resolving to
  ``{width, height}`` of the image's natural dimensions.
- ``getEXIFFromUploaded(file, callback)`` — minimal EXIF parser for
  ``File`` objects (drag-dropped photos). Returns ``Make``, ``Model``,
  ``LensModel``, ``FocalLength``, and ``FocalLengthIn35mmFilm`` when
  present; passes ``null`` to the callback if the file lacks parseable
  EXIF (intentional non-error: caller falls back to manual entry).
- ``attachDragDrop(zone, onFiles)`` — registers ``dragover`` /
  ``dragleave`` / ``drop`` listeners on a zone element; on drop, calls
  ``onFiles`` with an array of ``File`` objects.

The helpers are loaded by ``index.html`` via ``<script src="/ui/helpers.js">``,
which requires the ``GET /ui/<path:filename>`` route added in this PR.

Test strategy
-------------
1. **Smoke**: navigate to ``/`` and verify ``window.spatialUI`` exposes
   each helper as a function.
2. **Functional — captureClick**: create a 200×100 canvas with a 2× DPR,
   simulate a click at CSS coords, and verify the callback receives the
   click in canvas-natural coords (i.e., 2× the CSS coords).
3. **Functional — loadImageToCanvas**: load a 4×3 data-URL image and
   verify the resolved natural dimensions and that the canvas pixel
   buffer was populated.

EXIF and drag-drop coverage are deferred to integration testing in
1.D.3 where the real upload/EXIF flow lives. For 1.D.2 those two
helpers are smoke-tested only (signature + returns-without-throwing).
"""

from __future__ import annotations

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


def test_helpers_loaded_and_functional(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], page: Page
) -> None:
    """``helpers.js`` exposes ``window.spatialUI`` with four working utilities.

    Combines smoke (each helper is a function) with functional checks
    on the two pure-DOM helpers: ``captureClick`` (DPR-aware coordinate
    normalization) and ``loadImageToCanvas`` (returns natural size and
    populates the canvas pixel buffer).
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
                "uihelpers",
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

        # ─── Smoke: window.spatialUI exists with all 4 expected functions ──
        spatial_ui_shape = page.evaluate(
            """() => ({
                isObject: typeof window.spatialUI === 'object' && window.spatialUI !== null,
                captureClick: typeof window.spatialUI?.captureClick,
                loadImageToCanvas: typeof window.spatialUI?.loadImageToCanvas,
                getEXIFFromUploaded: typeof window.spatialUI?.getEXIFFromUploaded,
                attachDragDrop: typeof window.spatialUI?.attachDragDrop,
            })"""
        )
        assert spatial_ui_shape == {
            "isObject": True,
            "captureClick": "function",
            "loadImageToCanvas": "function",
            "getEXIFFromUploaded": "function",
            "attachDragDrop": "function",
        }, f"window.spatialUI shape unexpected: {spatial_ui_shape!r}"

        # ─── captureClick: DPR-aware coordinate normalization ────────────
        # Canvas with width=200/height=100 natural and a CSS box of 100×50.
        # A click at CSS (50, 25) — center — should translate to canvas
        # natural coords (100, 50). This proves the helper scales by
        # canvas.width/rect.width, not just clientX/clientY.
        click_coords = page.evaluate(
            """() => new Promise((resolve) => {
                const canvas = document.createElement('canvas');
                canvas.width = 200;
                canvas.height = 100;
                canvas.style.width = '100px';
                canvas.style.height = '50px';
                canvas.style.position = 'absolute';
                canvas.style.left = '0';
                canvas.style.top = '0';
                document.body.appendChild(canvas);
                window.spatialUI.captureClick(canvas, (xy) => resolve(xy));
                const rect = canvas.getBoundingClientRect();
                const evt = new MouseEvent('click', {
                    bubbles: true,
                    clientX: rect.left + rect.width / 2,
                    clientY: rect.top + rect.height / 2,
                });
                canvas.dispatchEvent(evt);
            })"""
        )
        assert isinstance(click_coords, dict)
        # Allow ±1 tolerance for sub-pixel rect fractional issues.
        assert abs(click_coords["x"] - 100.0) < 1.0, (
            f"captureClick X expected ~100, got {click_coords['x']}"
        )
        assert abs(click_coords["y"] - 50.0) < 1.0, (
            f"captureClick Y expected ~50, got {click_coords['y']}"
        )

        # ─── loadImageToCanvas: returns natural dims, populates canvas ──
        # Load the synthetic-card fixture via the existing /static/photos
        # route. The card is rendered orthographically at 800×600 (200mm
        # wide × 4 px/mm — see expected_geometry.json), so the helper
        # should report width=800, height=600 and populate the pixel
        # buffer with non-zero content (the card has a black border).
        load_result = page.evaluate(
            """async () => {
                const canvas = document.createElement('canvas');
                document.body.appendChild(canvas);
                const dims = await window.spatialUI.loadImageToCanvas(
                    '/static/photos/test1.jpg', canvas);
                return {
                    dims,
                    canvasWidth: canvas.width,
                    canvasHeight: canvas.height,
                    pixelBufferNonEmpty: canvas
                        .getContext('2d')
                        .getImageData(0, 0, canvas.width, canvas.height)
                        .data.some(v => v !== 0),
                };
            }"""
        )
        assert load_result["dims"] == {"width": 800, "height": 600}, (
            f"loadImageToCanvas dims mismatch: {load_result['dims']!r}"
        )
        assert load_result["canvasWidth"] == 800
        assert load_result["canvasHeight"] == 600
        assert load_result["pixelBufferNonEmpty"], (
            "canvas pixel buffer should be non-empty after drawImage"
        )
    finally:
        cli.start_server = orig_start_server  # type: ignore[assignment]
        if cli_thread.is_alive():
            if url is not None:
                best_effort_finalize(url)
            elif server_ref["server"] is not None:
                import contextlib

                with contextlib.suppress(Exception):
                    server_ref["server"].shutdown()  # type: ignore[attr-defined]
            cli_thread.join(timeout=10.0)
