"""End-to-end test for Phase 2a wiring (Task 1.D.3).

Phase 2a is the wizard's first interactive phase: surface the photos
already uploaded via the CLI, let the user confirm the lens used,
label each photo's view, optionally drag-drop additional photos, and
advance to Phase 2b (frame declaration).

Test coverage
-------------
1. **Init**: ``app.js`` loads, fetches ``/api/state``, and renders one
   ``.thumbnail`` per photo in ``#phase-2a-thumbnails``.
2. **Thumbnail content**: each thumbnail has an ``<img>``, a lens
   ``<select>`` with at least one real option, a view-label ``<select>``
   exposing the four labels spec §3 Phase 2a calls out
   ("top-down", "long-edge-A", "side-iso", "other"), and an EXIF
   info area (showing a placeholder for the synthetic-card fixture
   which carries no EXIF).
3. **Phase advance**: the "Next" button hides ``#phase-2a`` and reveals
   ``#phase-2b``, demonstrating the phase-machine transition that
   1.D.4–1.D.8 will extend.

What this test does NOT cover (deferred)
----------------------------------------
- Drag-drop functional path (Playwright synthetic drag events are
  awkward; helpers.attachDragDrop is unit-tested by 1.D.2's smoke check).
- Lens/view-label state persistence to backend (no backend route until
  1.D.5 — values land in client-side ``window.spatialState`` for now).
- Validation rules (e.g., must-pick-lens-before-Next) — spec doesn't
  require them at Phase 2a end; 1.D.5 enforces at /api/anchors POST time.
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

EXPECTED_VIEW_LABELS = {"top-down", "long-edge-A", "side-iso", "other"}


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


def test_phase_2a_renders_thumbnails_dropdowns_and_advances(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], page: Page
) -> None:
    """Phase 2a renders one thumbnail per uploaded photo and advances on Next.

    The CLI is launched with --photos tests/fixtures/synthetic_card/test1.jpg,
    so /api/state surfaces one photo and the wizard renders one thumbnail
    card. The card carries lens + view-label dropdowns (per spec §3
    Phase 2a) and an EXIF info area. Clicking Next must advance the
    phase machine to 2b.
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
                "uiphase2a",
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

        # ─── Wait for app.js init to populate thumbnails ────────────────
        # Init is async (fetch /api/state then DOM mutation). Wait for the
        # first .thumbnail element to appear before asserting on shape.
        page.wait_for_selector(
            "#phase-2a-thumbnails .thumbnail",
            state="attached",
            timeout=5_000,
        )

        # ─── Exactly one thumbnail (CLI was launched with one photo) ────
        thumbnails = page.locator("#phase-2a-thumbnails .thumbnail")
        assert thumbnails.count() == 1, (
            f"expected 1 thumbnail for the synthetic-card photo, got {thumbnails.count()}"
        )

        # ─── Thumbnail renders the photo image ──────────────────────────
        img = page.locator("#phase-2a-thumbnails .thumbnail img").first
        assert img.count() == 1, "thumbnail must render an <img> for the photo"
        # The img URL should resolve via the existing /static/photos/<id> route.
        assert img.get_attribute("src", timeout=2_000) is not None

        # ─── Lens dropdown: present with at least 2 options ─────────────
        lens_select = page.locator("#phase-2a-thumbnails .thumbnail select.lens-select").first
        assert lens_select.count() == 1, "thumbnail must include a lens-select dropdown"
        lens_option_count = lens_select.locator("option").count()
        assert lens_option_count >= 2, (
            f"lens dropdown should have at least 2 options (placeholder + lens), got {lens_option_count}"
        )

        # ─── View-label dropdown: includes all 4 spec-required labels ───
        view_label_select = page.locator(
            "#phase-2a-thumbnails .thumbnail select.view-label-select"
        ).first
        assert view_label_select.count() == 1, "thumbnail must include a view-label-select dropdown"
        view_label_options = view_label_select.locator("option").all_text_contents()
        view_label_set = {opt.strip() for opt in view_label_options}
        assert EXPECTED_VIEW_LABELS.issubset(view_label_set), (
            f"view-label dropdown missing required labels; got {view_label_set!r}, "
            f"expected superset of {EXPECTED_VIEW_LABELS!r}"
        )

        # ─── EXIF info area: shows something (placeholder OK) ───────────
        # The synthetic card is a generated PIL image with no EXIF, so the
        # parser returns null and the wizard should display a "no EXIF"
        # placeholder rather than an empty area.
        exif_area = page.locator("#phase-2a-thumbnails .thumbnail .exif-info").first
        assert exif_area.count() == 1, "thumbnail must include an .exif-info element"
        exif_text = exif_area.text_content() or ""
        assert exif_text.strip(), "EXIF area must show some text (placeholder for missing EXIF)"

        # ─── Phase advance: Next button hides 2a, reveals 2b ────────────
        assert page.locator("#phase-2a").is_visible(), "precondition: phase-2a visible"
        assert not page.locator("#phase-2b").is_visible(), "precondition: phase-2b hidden"

        next_btn = page.locator("#phase-2a-next")
        assert next_btn.count() == 1, "phase-2a must include a Next button"
        next_btn.click()

        # Visibility flips after click. Use a brief wait to allow the
        # synchronous DOM mutation to propagate (Playwright reads the
        # post-mutation tree).
        page.wait_for_selector("#phase-2b:not([hidden])", timeout=2_000)
        assert not page.locator("#phase-2a").is_visible(), (
            "phase-2a must be hidden after Next click"
        )
        assert page.locator("#phase-2b").is_visible(), "phase-2b must be visible after Next click"
    finally:
        cli.start_server = orig_start_server  # type: ignore[assignment]
        if cli_thread.is_alive():
            if url is not None:
                best_effort_finalize(url)
            elif server_ref["server"] is not None:
                with contextlib.suppress(Exception):
                    server_ref["server"].shutdown()  # type: ignore[attr-defined]
            cli_thread.join(timeout=10.0)
