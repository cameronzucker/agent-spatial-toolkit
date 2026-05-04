# PR-α Implementation Plan: Lens Catalog + Phase 2c Click Flow

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Phase 2c functional anchor-click flow: server-side lens catalog + extended `/api/anchors` + client-side anchor checklist + click capture + POST + result display. PR-β follows with wireframe overlay and retry UX.

**Architecture:** Server owns lens-id → intrinsics resolution via `lens_catalog.py`; client sends `lens_id` (not full intrinsics) to `/api/anchors`. Backward-compat: existing intrinsics-dict path still works. UI: one card per photo with anchor checklist → click capture → "Solve pose" button → response display.

**Tech Stack:** Python 3.10+, Flask, OpenCV (cv2.solvePnP via existing `pose.py`), vanilla JS (no framework — matches existing wizard), Playwright for UI tests.

**Spec sources:** [docs/specs/2026-05-03-design.md §3 Phase 2c, §5.1 step 4, §5.3](../specs/2026-05-03-design.md), [docs/plans/2026-05-04-1d5-phase2c-design.md](2026-05-04-1d5-phase2c-design.md)

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/agent_spatial_toolkit/server/lens_catalog.py` | Create | Canonical lens table + `resolve(lens_id, image_size, exif)` → `Intrinsics \| None` |
| `tests/test_lens_catalog.py` | Create | Unit tests for catalog + resolution |
| `src/agent_spatial_toolkit/server/app.py:319-411` | Modify | Add `GET /api/lens_catalog`; extend `POST /api/anchors` to accept `lens_id` |
| `tests/test_app.py:143-211` | Modify | New tests: lens catalog endpoint; lens_id resolution path; lens_id-without-intrinsics rejection |
| `src/agent_spatial_toolkit/ui/index.html:71-74` | Modify | Replace placeholder in `<section id="phase-2c">` with container scaffold |
| `src/agent_spatial_toolkit/ui/app.js` | Modify | Replace static `LENS_OPTIONS` with fetched catalog; new `wirePhase2c()` |
| `tests/test_ui_phase_2c.py` | Create | Playwright happy-path: load → click 4 anchors → "Solve pose" → see result |

**Design intent reminders:**
- The lens catalog stores `fallback_focal_35mm_equiv_mm` per lens (NOT pre-resolved intrinsics) because intrinsics depend on image size. Server resolves per-request via existing `resolve_fallback_intrinsics(focal, image_size)`.
- Sentinel lens IDs `exif:detected` and `other` carry `fallback_focal_35mm_equiv_mm = None` and trigger different code paths in `resolve()`.
- Ultrawide cameras (Pi Camera Module 3 wide, iPhone 13mm) get `fallback_focal_35mm_equiv_mm` populated but `resolve_fallback_intrinsics` returns `None` for ultrawide focal lengths — surfaces as "no intrinsics; please use chessboard or manual" in PR-β UX. PR-α just returns the 400 from the existing endpoint.

---

## Task 1: lens_catalog module

**Files:**
- Create: `src/agent_spatial_toolkit/server/lens_catalog.py`
- Test: `tests/test_lens_catalog.py`

- [ ] **Step 1.1: Write the failing tests**

```python
# tests/test_lens_catalog.py
"""Tests for server/lens_catalog.py — lens-id → Intrinsics resolution."""
from __future__ import annotations

import pytest

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.server.lens_catalog import (
    LENS_CATALOG,
    LensCatalogEntry,
    list_lens_entries,
    resolve,
)


def test_catalog_has_expected_lens_ids() -> None:
    """The v0 catalog includes the lens IDs the wizard's Phase 2a dropdown expects."""
    expected = {
        "pi_camera_module_3_wide",
        "pi_camera_module_3_standard",
        "iphone_15_pro_24mm",
        "iphone_15_pro_13mm",
        "iphone_15_pro_77mm",
        "exif:detected",
        "other",
    }
    assert set(LENS_CATALOG.keys()) == expected


def test_list_lens_entries_returns_id_label_resolvable_tuples() -> None:
    """list_lens_entries returns the catalog as a list of public-shape dicts."""
    entries = list_lens_entries()
    assert isinstance(entries, list)
    by_id = {e["id"]: e for e in entries}
    assert "pi_camera_module_3_standard" in by_id
    assert by_id["pi_camera_module_3_standard"]["label"] == "Pi Camera Module 3 (standard)"
    # The "resolvable" flag tells the client whether resolve() can produce
    # intrinsics without additional input (EXIF dict / manual intrinsics).
    assert by_id["pi_camera_module_3_standard"]["resolvable"] is True
    assert by_id["exif:detected"]["resolvable"] is False  # needs exif arg
    assert by_id["other"]["resolvable"] is False  # needs intrinsics arg
    # Ultrawide cameras have a fallback focal but resolve_fallback_intrinsics
    # rejects ultrawide; we surface this as resolvable=False.
    assert by_id["pi_camera_module_3_wide"]["resolvable"] is False


def test_resolve_pi_camera_standard_returns_intrinsics() -> None:
    """resolve() returns an Intrinsics for a known wide-class lens."""
    intr = resolve("pi_camera_module_3_standard", image_size=(4608, 2592))
    assert isinstance(intr, Intrinsics)
    assert intr.profile_source == "fov_class_fallback"
    # Pi Camera Module 3 standard is 25mm-35mm-equiv → wide class → fx_px scales
    # with long_edge / 36.0 * 25.0
    assert intr.fx_px == pytest.approx(4608 * 25.0 / 36.0, rel=1e-6)
    assert intr.cx == pytest.approx(4608 / 2.0)
    assert intr.cy == pytest.approx(2592 / 2.0)


def test_resolve_ultrawide_returns_none() -> None:
    """resolve() returns None for ultrawide cameras (FOV-class fallback rejects)."""
    assert resolve("pi_camera_module_3_wide", image_size=(2304, 1296)) is None
    assert resolve("iphone_15_pro_13mm", image_size=(4032, 3024)) is None


def test_resolve_unknown_lens_id_returns_none() -> None:
    """resolve() returns None for an unrecognized lens_id."""
    assert resolve("does_not_exist", image_size=(1000, 1000)) is None


def test_resolve_exif_detected_uses_exif_focal_length() -> None:
    """resolve('exif:detected', exif={...}) reads focal from the EXIF dict."""
    exif = {"focalLength35mm": 50.0}
    intr = resolve("exif:detected", image_size=(4032, 3024), exif=exif)
    assert isinstance(intr, Intrinsics)
    assert intr.fx_px == pytest.approx(4032 * 50.0 / 36.0)


def test_resolve_exif_detected_without_exif_returns_none() -> None:
    """resolve('exif:detected') with no exif arg cannot resolve."""
    assert resolve("exif:detected", image_size=(1000, 1000)) is None


def test_resolve_exif_detected_with_missing_focal_returns_none() -> None:
    """resolve('exif:detected') with EXIF that lacks focalLength35mm returns None."""
    assert resolve("exif:detected", image_size=(1000, 1000), exif={"make": "X"}) is None


def test_resolve_other_returns_none() -> None:
    """resolve('other') always returns None — caller must provide intrinsics directly."""
    assert resolve("other", image_size=(1000, 1000)) is None


def test_lens_catalog_entry_dataclass_shape() -> None:
    """LensCatalogEntry exposes the documented fields."""
    entry = LENS_CATALOG["pi_camera_module_3_standard"]
    assert isinstance(entry, LensCatalogEntry)
    assert entry.label == "Pi Camera Module 3 (standard)"
    assert entry.fallback_focal_35mm_equiv_mm == 25.0
    # `notes` is informational; can be empty for sane defaults.
    assert isinstance(entry.notes, str)
```

- [ ] **Step 1.2: Run test to verify failure**

Run: `cd /home/administrator/Code/agent-spatial-toolkit-ui && uv run pytest tests/test_lens_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_spatial_toolkit.server.lens_catalog'`

- [ ] **Step 1.3: Create the module**

```python
# src/agent_spatial_toolkit/server/lens_catalog.py
"""Server-side lens catalog (Task 1.D.5 PR-α).

Maps human-friendly lens IDs (chosen in the wizard's Phase 2a dropdown) to
the data needed to resolve camera intrinsics for that lens at request time.
``resolve(lens_id, image_size, exif=None)`` returns an ``Intrinsics`` if the
catalog can compute it, or ``None`` if the caller must provide intrinsics
explicitly (manual mode, ultrawide-pending-chessboard, EXIF-without-focal).

The catalog stores ``fallback_focal_35mm_equiv_mm`` rather than pre-resolved
intrinsics because intrinsics depend on image size (cx, cy, fx all scale
with the photo's pixel dimensions). Per-request resolution avoids hardcoding
intrinsics for every possible image resolution.

Why server-side rather than client-side:
- The science layer (``pipeline/intrinsics.py``) already owns intrinsics
  math; one source of truth.
- Client only needs labels for the dropdown — no need to ship camera data
  through the JS bundle.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_spatial_toolkit.pipeline.intrinsics import (
    Intrinsics,
    resolve_fallback_intrinsics,
    resolve_fov_class,
    FOV_CLASS_ULTRAWIDE,
)


@dataclass(frozen=True)
class LensCatalogEntry:
    """One entry in the lens catalog.

    ``fallback_focal_35mm_equiv_mm`` is the 35mm-equivalent focal length used
    to compute intrinsics via ``resolve_fallback_intrinsics``. ``None`` for
    sentinel entries (``exif:detected``, ``other``) where focal comes from
    a different source at request time.
    """

    label: str
    fallback_focal_35mm_equiv_mm: float | None
    notes: str = ""


# Sentinel lens IDs handled specially in resolve():
LENS_ID_EXIF_DETECTED = "exif:detected"
LENS_ID_OTHER = "other"


# v0 lens catalog. Pi Camera Module 3 wide and iPhone 15 Pro ultrawide are
# included for completeness but resolve to None — FOV-class fallback rejects
# ultrawide (spec §5.2). Surface as resolvable=False to the client; PR-β's
# UX presents "switch to chessboard or manual entry" affordances.
LENS_CATALOG: dict[str, LensCatalogEntry] = {
    "pi_camera_module_3_wide": LensCatalogEntry(
        label="Pi Camera Module 3 (wide)",
        fallback_focal_35mm_equiv_mm=20.0,
        notes="Ultrawide; FOV-class fallback rejects. Use chessboard calibration (Phase 2 Stream G) or manual intrinsics.",
    ),
    "pi_camera_module_3_standard": LensCatalogEntry(
        label="Pi Camera Module 3 (standard)",
        fallback_focal_35mm_equiv_mm=25.0,
    ),
    "iphone_15_pro_24mm": LensCatalogEntry(
        label="iPhone 15 Pro — 24mm equiv (main)",
        fallback_focal_35mm_equiv_mm=24.0,
    ),
    "iphone_15_pro_13mm": LensCatalogEntry(
        label="iPhone 15 Pro — 13mm equiv (ultrawide)",
        fallback_focal_35mm_equiv_mm=13.0,
        notes="Ultrawide; FOV-class fallback rejects. Use chessboard or manual.",
    ),
    "iphone_15_pro_77mm": LensCatalogEntry(
        label="iPhone 15 Pro — 77mm equiv (telephoto)",
        fallback_focal_35mm_equiv_mm=77.0,
    ),
    LENS_ID_EXIF_DETECTED: LensCatalogEntry(
        label="(detected from EXIF)",
        fallback_focal_35mm_equiv_mm=None,
        notes="Server reads focalLength35mm from the photo's EXIF at request time.",
    ),
    LENS_ID_OTHER: LensCatalogEntry(
        label="Other (manual entry)",
        fallback_focal_35mm_equiv_mm=None,
        notes="Caller must provide a full intrinsics dict in the /api/anchors body.",
    ),
}


def _is_resolvable(lens_id: str, entry: LensCatalogEntry) -> bool:
    """Whether resolve() can produce intrinsics for this lens without additional input.

    Returns False for the two sentinels (need exif/intrinsics from caller) and
    for ultrawide cameras (resolve_fallback_intrinsics rejects them).
    """
    if lens_id in (LENS_ID_EXIF_DETECTED, LENS_ID_OTHER):
        return False
    if entry.fallback_focal_35mm_equiv_mm is None:
        return False
    if resolve_fov_class(entry.fallback_focal_35mm_equiv_mm) == FOV_CLASS_ULTRAWIDE:
        return False
    return True


def list_lens_entries() -> list[dict[str, Any]]:
    """Return the catalog as a list of public-shape dicts for GET /api/lens_catalog.

    Intrinsics data is NOT exposed — clients only need id + label + resolvability.
    """
    return [
        {
            "id": lens_id,
            "label": entry.label,
            "resolvable": _is_resolvable(lens_id, entry),
            "notes": entry.notes,
        }
        for lens_id, entry in LENS_CATALOG.items()
    ]


def resolve(
    lens_id: str,
    image_size: tuple[int, int],
    exif: dict[str, Any] | None = None,
) -> Intrinsics | None:
    """Resolve a lens_id to Intrinsics for a photo of the given image_size.

    Returns None if:
    - lens_id is unknown
    - lens_id is "other" (caller must supply intrinsics)
    - lens_id is "exif:detected" but ``exif`` is missing or lacks focalLength35mm
    - the resolved focal length is ultrawide (FOV-class fallback rejects it)
    """
    entry = LENS_CATALOG.get(lens_id)
    if entry is None:
        return None
    if lens_id == LENS_ID_OTHER:
        return None
    if lens_id == LENS_ID_EXIF_DETECTED:
        if not exif:
            return None
        focal = exif.get("focalLength35mm")
        if focal is None:
            return None
        try:
            focal_f = float(focal)
        except (TypeError, ValueError):
            return None
        return resolve_fallback_intrinsics(focal_f, image_size)
    focal = entry.fallback_focal_35mm_equiv_mm
    if focal is None:
        return None
    return resolve_fallback_intrinsics(focal, image_size)
```

Note: this imports `resolve_fov_class` and `FOV_CLASS_ULTRAWIDE` from `pipeline/intrinsics.py`. Verify those names match by running `grep -n "FOV_CLASS_ULTRAWIDE\|def resolve_fov_class" src/agent_spatial_toolkit/pipeline/intrinsics.py` before running the tests; if the symbol names differ, adjust the import accordingly.

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `cd /home/administrator/Code/agent-spatial-toolkit-ui && uv run pytest tests/test_lens_catalog.py -v`
Expected: 9 PASS, 0 FAIL.

- [ ] **Step 1.5: Commit**

```bash
cd /home/administrator/Code/agent-spatial-toolkit-ui
git add src/agent_spatial_toolkit/server/lens_catalog.py tests/test_lens_catalog.py
GIT_AUTHOR_NAME=cameronzucker GIT_AUTHOR_EMAIL=cameronzucker@gmail.com \
GIT_COMMITTER_NAME=cameronzucker GIT_COMMITTER_EMAIL=cameronzucker@gmail.com \
git commit -m "feat(server): lens catalog module (Task 1.D.5 PR-α step 1)

Maps lens IDs to 35mm-equivalent focal lengths; resolve() produces
Intrinsics by feeding focal + image_size through the existing
resolve_fallback_intrinsics. Sentinel entries 'exif:detected' and
'other' carry None focals and trigger different code paths.

Tests: 9 cases covering resolve happy paths, ultrawide rejection,
sentinel handling, EXIF-with/without focal, unknown lens IDs.

Part of Task 1.D.5 PR-α — see docs/plans/2026-05-04-1d5-phase2c-design.md.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: GET /api/lens_catalog endpoint

**Files:**
- Modify: `src/agent_spatial_toolkit/server/app.py` (add new route after line 323)
- Modify: `tests/test_app.py` (add new test class)

- [ ] **Step 2.1: Write the failing tests**

Append to `tests/test_app.py` (use the existing `app_factory` fixture):

```python
# Append to tests/test_app.py


def test_lens_catalog_route_returns_expected_lens_ids(app_factory) -> None:
    """GET /api/lens_catalog returns the v0 catalog entries."""
    app, _, _ = app_factory()
    client = app.test_client()

    resp = client.get("/api/lens_catalog")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "lenses" in body
    ids = {entry["id"] for entry in body["lenses"]}
    assert "pi_camera_module_3_standard" in ids
    assert "exif:detected" in ids
    assert "other" in ids


def test_lens_catalog_entries_carry_label_and_resolvable_flag(app_factory) -> None:
    """Each entry has id, label, resolvable, notes (no intrinsics leakage)."""
    app, _, _ = app_factory()
    client = app.test_client()

    body = client.get("/api/lens_catalog").get_json()
    by_id = {e["id"]: e for e in body["lenses"]}
    standard = by_id["pi_camera_module_3_standard"]
    assert standard["label"] == "Pi Camera Module 3 (standard)"
    assert standard["resolvable"] is True
    assert "intrinsics" not in standard  # do NOT leak intrinsics into client payload
    # Ultrawide → resolvable=False
    assert by_id["pi_camera_module_3_wide"]["resolvable"] is False
    assert by_id["exif:detected"]["resolvable"] is False
```

- [ ] **Step 2.2: Run the new tests; expect failure**

Run: `cd /home/administrator/Code/agent-spatial-toolkit-ui && uv run pytest tests/test_app.py::test_lens_catalog_route_returns_expected_lens_ids tests/test_app.py::test_lens_catalog_entries_carry_label_and_resolvable_flag -v`
Expected: FAIL with HTTP 404 (no route registered).

- [ ] **Step 2.3: Add the route**

In `src/agent_spatial_toolkit/server/app.py`, add the import near the other server-module imports (around line where EventLog is imported):

```python
from agent_spatial_toolkit.server.lens_catalog import list_lens_entries
```

Then immediately after the `get_state` route definition (around line 323), add:

```python
    @app.get("/api/lens_catalog")
    def get_lens_catalog() -> Any:
        """Return the canonical lens catalog (id + label + resolvability).

        Intrinsics data is NOT exposed — clients only need to populate the
        Phase 2a dropdown and decide whether resolution requires extra input
        (EXIF dict, full intrinsics dict).
        """
        return jsonify({"lenses": list_lens_entries()})
```

- [ ] **Step 2.4: Run tests; expect pass**

Run: `cd /home/administrator/Code/agent-spatial-toolkit-ui && uv run pytest tests/test_app.py -k lens_catalog -v`
Expected: 2 PASS.

- [ ] **Step 2.5: Run full test_app.py to verify no regressions**

Run: `cd /home/administrator/Code/agent-spatial-toolkit-ui && uv run pytest tests/test_app.py -v`
Expected: All previously-passing tests still pass; 2 new pass; 0 fail.

- [ ] **Step 2.6: Commit**

```bash
cd /home/administrator/Code/agent-spatial-toolkit-ui
git add src/agent_spatial_toolkit/server/app.py tests/test_app.py
GIT_AUTHOR_NAME=cameronzucker GIT_AUTHOR_EMAIL=cameronzucker@gmail.com \
GIT_COMMITTER_NAME=cameronzucker GIT_COMMITTER_EMAIL=cameronzucker@gmail.com \
git commit -m "feat(server): GET /api/lens_catalog (Task 1.D.5 PR-α step 2)

Exposes lens id + label + resolvable flag + notes to the client. Intrinsics
data is intentionally NOT exposed; clients only need to populate the Phase 2a
dropdown and decide whether resolution requires additional input.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Extend POST /api/anchors to accept lens_id

**Files:**
- Modify: `src/agent_spatial_toolkit/server/app.py` lines 325-411 (the `post_anchors` route)
- Modify: `tests/test_app.py` (add new tests after the existing anchor-route tests)

- [ ] **Step 3.1: Write the failing tests**

Append to `tests/test_app.py`:

```python
# Append to tests/test_app.py


def _valid_anchors_payload_with_lens_id(image_size: tuple[int, int] = (4608, 2592)) -> dict:
    """Same shape as _valid_anchors_payload but uses lens_id instead of intrinsics."""
    base = _valid_anchors_payload(image_size=image_size)
    base.pop("intrinsics", None)
    base["lens_id"] = "pi_camera_module_3_standard"
    base["image_size"] = list(image_size)
    return base


def test_anchors_route_accepts_lens_id_and_resolves_intrinsics(app_factory) -> None:
    """POST /api/anchors with lens_id resolves intrinsics server-side and solves PnP."""
    app, _, _ = app_factory()
    client = app.test_client()

    payload = _valid_anchors_payload_with_lens_id()
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert "pose" in body
    assert body["pose"]["pose_solver"].startswith("cv2.solvePnP")


def test_anchors_route_lens_id_unresolvable_returns_400(app_factory) -> None:
    """POST /api/anchors with an ultrawide lens_id (unresolvable) returns 400."""
    app, _, _ = app_factory()
    client = app.test_client()

    payload = _valid_anchors_payload_with_lens_id()
    payload["lens_id"] = "pi_camera_module_3_wide"  # ultrawide → resolve() returns None
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 400
    body = resp.get_json()
    assert "error" in body
    assert "lens" in body["error"].lower()


def test_anchors_route_lens_id_other_without_intrinsics_returns_400(app_factory) -> None:
    """POST /api/anchors with lens_id='other' and no intrinsics dict returns 400."""
    app, _, _ = app_factory()
    client = app.test_client()

    payload = _valid_anchors_payload_with_lens_id()
    payload["lens_id"] = "other"
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 400


def test_anchors_route_lens_id_exif_detected_uses_exif(app_factory) -> None:
    """POST /api/anchors with lens_id='exif:detected' + exif dict resolves intrinsics."""
    app, _, _ = app_factory()
    client = app.test_client()

    payload = _valid_anchors_payload_with_lens_id()
    payload["lens_id"] = "exif:detected"
    payload["exif"] = {"focalLength35mm": 50.0}
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 200, resp.get_json()


def test_anchors_route_intrinsics_dict_still_works_backward_compat(app_factory) -> None:
    """Existing intrinsics-dict path still works (no breaking change)."""
    app, _, _ = app_factory()
    client = app.test_client()

    payload = _valid_anchors_payload()  # original fixture, with intrinsics dict
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 200, resp.get_json()
```

- [ ] **Step 3.2: Run new tests; expect failure**

Run: `cd /home/administrator/Code/agent-spatial-toolkit-ui && uv run pytest tests/test_app.py -k "lens_id or backward_compat" -v`
Expected: 4 of the 5 FAIL; the backward-compat one PASSES (the existing path is unchanged).

The 4 failing should fail with messages indicating the new fields aren't recognized — likely 400 errors complaining about `missing required field (photo_id, intrinsics, anchors, image_size)` because `intrinsics` is required in the current code.

- [ ] **Step 3.3: Modify post_anchors to accept lens_id**

In `src/agent_spatial_toolkit/server/app.py`, find the `post_anchors` route (currently lines 325-411). Modify the body-parsing section (lines 330-352) so it accepts `lens_id` as an alternative to `intrinsics`. Replace the existing block:

```python
        body = request.get_json(silent=True) or {}
        try:
            photo_id = body["photo_id"]
            intrinsics_dict = body["intrinsics"]
            anchors_list = body["anchors"]
            image_size_in = body["image_size"]
        except (KeyError, TypeError):
            return (
                jsonify(
                    {"error": "missing required field (photo_id, intrinsics, anchors, image_size)"}
                ),
                400,
            )

        if not isinstance(anchors_list, list) or not isinstance(image_size_in, list):
            return jsonify({"error": "anchors and image_size must be arrays"}), 400
        if len(image_size_in) != 2:
            return jsonify({"error": "image_size must be [width, height]"}), 400

        try:
            intrinsics = _intrinsics_from_dict(intrinsics_dict)
        except (KeyError, TypeError, ValueError) as e:
            return jsonify({"error": f"invalid intrinsics: {e}"}), 400
```

with:

```python
        body = request.get_json(silent=True) or {}
        try:
            photo_id = body["photo_id"]
            anchors_list = body["anchors"]
            image_size_in = body["image_size"]
        except (KeyError, TypeError):
            return (
                jsonify(
                    {"error": "missing required field (photo_id, anchors, image_size)"}
                ),
                400,
            )

        if not isinstance(anchors_list, list) or not isinstance(image_size_in, list):
            return jsonify({"error": "anchors and image_size must be arrays"}), 400
        if len(image_size_in) != 2:
            return jsonify({"error": "image_size must be [width, height]"}), 400

        try:
            image_size = (
                _coerce_finite_int(image_size_in[0], "image_size[0]"),
                _coerce_finite_int(image_size_in[1], "image_size[1]"),
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        # Resolve intrinsics: explicit dict wins; otherwise resolve via lens_id.
        # The lens_id branch is the new wizard path (Task 1.D.5); the dict path
        # preserves backward compatibility with existing tests + scripts.
        intrinsics_dict = body.get("intrinsics")
        if intrinsics_dict is not None:
            try:
                intrinsics = _intrinsics_from_dict(intrinsics_dict)
            except (KeyError, TypeError, ValueError) as e:
                return jsonify({"error": f"invalid intrinsics: {e}"}), 400
        else:
            lens_id = body.get("lens_id")
            if not lens_id:
                return (
                    jsonify({"error": "must provide either intrinsics or lens_id"}),
                    400,
                )
            from agent_spatial_toolkit.server.lens_catalog import resolve as _resolve_lens
            intr_obj = _resolve_lens(lens_id, image_size, exif=body.get("exif"))
            if intr_obj is None:
                return (
                    jsonify(
                        {"error": f"lens_id '{lens_id}' could not resolve to intrinsics; provide an explicit intrinsics dict"}
                    ),
                    400,
                )
            intrinsics = intr_obj
            intrinsics_dict = intr_obj.to_dict()  # for the mem["photos"] record below
```

Then **delete** the now-redundant `image_size` coercion block that appears later (lines 360-366 in the original — the block starting `try: image_size = (` and ending after the ValueError handler), since we moved it above. Verify the rest of the route uses the `image_size` and `intrinsics` variables already in scope.

(Note: importing `resolve` inside the function avoids a top-of-file circular-import risk if anyone ever imports `app.py` from `lens_catalog.py`. Keep it as a function-local import.)

- [ ] **Step 3.4: Run new tests; expect pass**

Run: `cd /home/administrator/Code/agent-spatial-toolkit-ui && uv run pytest tests/test_app.py -k "lens_id or backward_compat or anchors" -v`
Expected: 5 new PASS + all original anchor tests still PASS (≥ 7 total in this filter).

- [ ] **Step 3.5: Run full test_app.py + test_lens_catalog.py to confirm no regressions**

Run: `cd /home/administrator/Code/agent-spatial-toolkit-ui && uv run pytest tests/test_app.py tests/test_lens_catalog.py -v`
Expected: All PASS. If any previously-passing test now fails, the modification dropped a code path — re-read the diff before continuing.

- [ ] **Step 3.6: Commit**

```bash
cd /home/administrator/Code/agent-spatial-toolkit-ui
git add src/agent_spatial_toolkit/server/app.py tests/test_app.py
GIT_AUTHOR_NAME=cameronzucker GIT_AUTHOR_EMAIL=cameronzucker@gmail.com \
GIT_COMMITTER_NAME=cameronzucker GIT_COMMITTER_EMAIL=cameronzucker@gmail.com \
git commit -m "feat(server): /api/anchors accepts lens_id (Task 1.D.5 PR-α step 3)

The existing intrinsics-dict path is preserved (backward compat); when
intrinsics is absent, server resolves via lens_catalog.resolve(lens_id,
image_size, exif). Returns 400 with a descriptive message when the lens_id
can't resolve (ultrawide, sentinels needing extra input, unknown id).

Tests: 5 new (lens_id happy path, ultrawide rejection, 'other' rejection,
'exif:detected' happy path, backward-compat).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: index.html Phase 2c container scaffold

**Files:**
- Modify: `src/agent_spatial_toolkit/ui/index.html` lines 71-74

- [ ] **Step 4.1: Replace the placeholder section**

In `src/agent_spatial_toolkit/ui/index.html`, replace lines 71-74 (the entire `<section id="phase-2c">` block):

```html
        <!-- Spec §3 Phase 2c — per-photo anchors (wired in 1.D.5). -->
        <section id="phase-2c" class="phase" hidden>
            <h2>Phase 2c — Per-photo anchors</h2>
            <p class="placeholder">Anchor click capture + /api/anchors POST wires up in Task 1.D.5.</p>
        </section>
```

with:

```html
        <!-- Spec §3 Phase 2c — per-photo anchors. Wired by app.js wirePhase2c
             (Task 1.D.5 PR-α). One #phase-2c-photo card per photo is appended
             at runtime; each carries a canvas + anchor checklist + result area.
             PR-β layers in a wireframe overlay <img> + retry buttons. -->
        <section id="phase-2c" class="phase" hidden>
            <h2>Phase 2c — Per-photo anchors</h2>
            <p>Click each declared anchor's pixel position in each photo. Minimum 3 anchors per photo to solve pose; the system computes the camera position and reports reprojection RMS so you can verify accuracy.</p>
            <div id="phase-2c-photos" class="phase-2c-photo-grid"></div>
            <button id="phase-2c-next" type="button" disabled>Next: feature labeling →</button>
            <p id="phase-2c-error" class="error" hidden></p>
        </section>
```

- [ ] **Step 4.2: Verify the page still loads via the existing UI smoke test**

Run: `cd /home/administrator/Code/agent-spatial-toolkit-ui && uv run pytest tests/test_ui_smoke.py -v`
Expected: All previously-passing tests still pass (smoke just checks the page renders + initial state).

- [ ] **Step 4.3: Commit**

```bash
cd /home/administrator/Code/agent-spatial-toolkit-ui
git add src/agent_spatial_toolkit/ui/index.html
GIT_AUTHOR_NAME=cameronzucker GIT_AUTHOR_EMAIL=cameronzucker@gmail.com \
GIT_COMMITTER_NAME=cameronzucker GIT_COMMITTER_EMAIL=cameronzucker@gmail.com \
git commit -m "feat(ui): Phase 2c container scaffold (Task 1.D.5 PR-α step 4)

Replaces placeholder paragraph with the static container app.js fills at
runtime: instructions, #phase-2c-photos grid (one card per photo appended
by wirePhase2c), Next button (disabled until at least one photo has a
solved pose), error display.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: app.js — fetch lens catalog at init, replace static LENS_OPTIONS

**Files:**
- Modify: `src/agent_spatial_toolkit/ui/app.js` (the LENS_OPTIONS constant + init flow)

- [ ] **Step 5.1: Replace static LENS_OPTIONS with fetched catalog**

In `src/agent_spatial_toolkit/ui/app.js`, find the `LENS_OPTIONS` array at lines 35-43 and **delete** it (the new catalog-fetched data takes its place).

Then modify the `init()` function (lines 92-115) so it fetches the catalog before rendering thumbnails. Replace the existing `init()` body with:

```javascript
    function init() {
        // Wire phase controls FIRST, independent of /api/state. Without this,
        // a failed state fetch would brick the Next button (no listener
        // attached) and trap the user in Phase 2a forever.
        wirePhaseControls();
        // Fetch lens catalog + state in parallel. Catalog drives the lens
        // dropdown options in Phase 2a thumbnails (replaces the static
        // LENS_OPTIONS that lived here in 1.D.3); state drives initial
        // thumbnail render. Both must be available before renderThumbnails
        // because the lens select is built from the catalog.
        Promise.all([
            fetch('/api/lens_catalog').then(function (r) {
                if (!r.ok) throw new Error('GET /api/lens_catalog returned ' + r.status);
                return r.json();
            }),
            fetch('/api/state').then(function (r) {
                if (!r.ok) throw new Error('GET /api/state returned ' + r.status);
                return r.json();
            }),
        ])
            .then(function (results) {
                var catalogResp = results[0];
                var state = results[1];
                window.spatialState.lensCatalog = catalogResp.lenses || [];
                renderThumbnails(state.photos || []);
            })
            .catch(function (err) {
                // Surface init failure visibly so a user (or test) sees a
                // clear signal rather than a silently-empty Phase 2a.
                var container = document.getElementById('phase-2a-thumbnails');
                if (container) {
                    container.textContent = 'Failed to load wizard state: ' + err.message;
                }
            });
    }
```

Then replace the `buildLabeledSelect(...)` call for the lens dropdown in `buildThumbnailCard` (around line 169) so it uses the fetched catalog. Find:

```javascript
        meta.appendChild(buildLabeledSelect('Lens', 'lens-select', LENS_OPTIONS, function (val) {
            window.spatialState.photos[photo.id].lens = val;
        }));
```

Replace with:

```javascript
        var lensOptions = lensCatalogToOptions(window.spatialState.lensCatalog);
        meta.appendChild(buildLabeledSelect('Lens', 'lens-select', lensOptions, function (val) {
            window.spatialState.photos[photo.id].lens = val;
        }));
```

Then add the `lensCatalogToOptions` helper function (place it near the other helpers, e.g., between `buildLabeledSelect` and `populateExif`):

```javascript
    // Convert the /api/lens_catalog response into the {value, label} option
    // shape buildLabeledSelect expects. Always prepends a placeholder option
    // so a user-unselected dropdown has a stable default.
    function lensCatalogToOptions(catalog) {
        var opts = [{ value: '', label: '— Select lens —' }];
        (catalog || []).forEach(function (entry) {
            // Suffix non-resolvable entries so users see why a lens choice
            // might fail at /api/anchors time. Catalog server already includes
            // a `notes` field; we just hint here in the label.
            var label = entry.label;
            if (!entry.resolvable && entry.id !== 'exif:detected' && entry.id !== 'other') {
                label += ' (calibration required)';
            }
            opts.push({ value: entry.id, label: label });
        });
        return opts;
    }
```

- [ ] **Step 5.2: Verify Phase 2a + 2b smoke tests still pass**

Run: `cd /home/administrator/Code/agent-spatial-toolkit-ui && uv run pytest tests/test_ui_smoke.py tests/test_ui_phase_2a.py tests/test_ui_phase_2b.py -v`
Expected: All PASS. (The lens dropdown's content changed but the test fixtures should accommodate any non-empty option list.)

If `test_ui_phase_2a.py` asserts a specific lens label that no longer exists post-refactor (likely — the 1.D.3 PR seeded the dropdown), patch the assertion to use the new resolvable Pi camera label `Pi Camera Module 3 (standard)` (which the catalog still exposes).

- [ ] **Step 5.3: Commit**

```bash
cd /home/administrator/Code/agent-spatial-toolkit-ui
git add src/agent_spatial_toolkit/ui/app.js
# also add tests/test_ui_phase_2a.py only if step 5.2 required a label-fixup
GIT_AUTHOR_NAME=cameronzucker GIT_AUTHOR_EMAIL=cameronzucker@gmail.com \
GIT_COMMITTER_NAME=cameronzucker GIT_COMMITTER_EMAIL=cameronzucker@gmail.com \
git commit -m "feat(ui): fetch lens catalog at init (Task 1.D.5 PR-α step 5)

Replaces the static LENS_OPTIONS constant in app.js with a fetch of
GET /api/lens_catalog at wizard init. Lens dropdown is built from the
fetched catalog; non-resolvable entries get a (calibration required)
suffix to set user expectation before they hit a 400 from /api/anchors.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: app.js wirePhase2c — anchor checklist + click capture + POST + result display

**Files:**
- Modify: `src/agent_spatial_toolkit/ui/app.js` (add wirePhase2c + helpers)

- [ ] **Step 6.1: Add wirePhase2c() and helpers**

In `src/agent_spatial_toolkit/ui/app.js`, modify `wirePhaseControls()` (currently at lines 271-274) to also call `wirePhase2c`:

```javascript
    function wirePhaseControls() {
        wirePhase2a();
        wirePhase2b();
        wirePhase2c();
    }
```

Then add the new function near the other phase wirings (e.g., after `wirePhase2b` and its helpers, before `init`):

```javascript
    // Phase 2c — per-photo anchor click capture + POST /api/anchors.
    // Renders one card per photo when Phase 2b's "Next" advances the wizard.
    // The card carries: canvas (photo loaded via spatialUI.loadImageToCanvas),
    // anchor checklist (one row per anchor from spatialState.frame.anchors),
    // "Solve pose" button (enabled when ≥3 anchors have pixel coords), and
    // a result area for the response.
    //
    // Per spec §5.3, minimum 3 non-collinear anchors per photo for solvePnP
    // to converge. We don't enforce non-collinearity client-side; the server
    // returns a 400 from PoseSolveError if the anchors are degenerate.
    function wirePhase2c() {
        // Hook into Phase 2b's "Next" — when the user advances from 2b, we
        // need to populate the photo cards (the frame anchors are now known).
        // We piggyback on the existing advancePhase by wrapping its call site:
        // Phase 2b's wirePhase2b already calls advancePhase('2b', '2c') after
        // commitFrameAndAdvance returns null. We don't have a hook there, so
        // use a MutationObserver on the phase-2c section's `hidden` attribute
        // to detect the transition. This keeps wirePhase2b unchanged.
        var phase2c = document.getElementById('phase-2c');
        if (!phase2c) return;
        var observer = new MutationObserver(function () {
            if (!phase2c.hidden) {
                renderPhase2cPhotos();
                observer.disconnect();  // one-shot: only render on first reveal
            }
        });
        observer.observe(phase2c, { attributes: true, attributeFilter: ['hidden'] });

        var nextBtn = document.getElementById('phase-2c-next');
        if (nextBtn) {
            nextBtn.addEventListener('click', function () { advancePhase('2c', '2d'); });
        }
    }

    function renderPhase2cPhotos() {
        var container = document.getElementById('phase-2c-photos');
        if (!container) return;
        clearChildren(container);
        var photoIds = Object.keys(window.spatialState.photos || {});
        if (photoIds.length === 0) {
            var empty = document.createElement('p');
            empty.className = 'placeholder';
            empty.textContent = 'No photos to annotate. Restart the CLI with --photos.';
            container.appendChild(empty);
            return;
        }
        var anchors = (window.spatialState.frame || {}).anchors || [];
        if (anchors.length === 0) {
            var noAnchors = document.createElement('p');
            noAnchors.className = 'error';
            noAnchors.textContent = 'No anchors declared in Phase 2b. Go back and pick a frame preset.';
            container.appendChild(noAnchors);
            return;
        }
        photoIds.forEach(function (photoId) {
            container.appendChild(buildPhase2cCard(photoId, anchors));
        });
    }

    function buildPhase2cCard(photoId, anchors) {
        var photo = window.spatialState.photos[photoId];
        var card = document.createElement('div');
        card.className = 'phase-2c-card';
        card.dataset.photoId = photoId;

        var name = document.createElement('div');
        name.className = 'phase-2c-photo-name';
        name.textContent = photoId;
        card.appendChild(name);

        var canvas = document.createElement('canvas');
        canvas.className = 'phase-2c-canvas';
        card.appendChild(canvas);

        var checklist = document.createElement('ul');
        checklist.className = 'phase-2c-checklist';
        card.appendChild(checklist);

        // Per-photo state held on the card's dataset / a closure
        var state = {
            currentAnchorId: null,
            clicks: {},  // anchor_id -> {x, y}
        };
        anchors.forEach(function (anchor) {
            checklist.appendChild(buildAnchorChecklistRow(anchor, card, state, photo));
        });

        var solveBtn = document.createElement('button');
        solveBtn.type = 'button';
        solveBtn.textContent = 'Solve pose';
        solveBtn.disabled = true;
        solveBtn.className = 'phase-2c-solve-btn';
        card.appendChild(solveBtn);

        var result = document.createElement('div');
        result.className = 'phase-2c-result';
        card.appendChild(result);

        // Load the photo into the canvas, then arm captureClick.
        if (window.spatialUI && photo && photo.url) {
            window.spatialUI.loadImageToCanvas(photo.url, canvas)
                .then(function (size) {
                    state.imageSize = [size.width, size.height];
                    window.spatialUI.captureClick(canvas, function (pt) {
                        if (!state.currentAnchorId) return;  // no anchor armed
                        state.clicks[state.currentAnchorId] = pt;
                        markChecklistRowComplete(checklist, state.currentAnchorId, pt);
                        state.currentAnchorId = null;
                        // Re-evaluate Solve button + Next button enablement
                        solveBtn.disabled = Object.keys(state.clicks).length < 3;
                    });
                })
                .catch(function (err) {
                    var status = document.createElement('p');
                    status.className = 'error';
                    status.textContent = 'Failed to load photo: ' + err.message;
                    card.appendChild(status);
                });
        }

        solveBtn.addEventListener('click', function () {
            postAnchors(photoId, photo, anchors, state, result, card);
        });

        return card;
    }

    function buildAnchorChecklistRow(anchor, card, state, _photo) {
        var li = document.createElement('li');
        li.className = 'phase-2c-checklist-row';
        li.dataset.anchorId = anchor.id;

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = 'Click anchor: ' + anchor.id + ' (' + anchor.xyz.join(', ') + ' mm)';
        btn.className = 'phase-2c-anchor-btn';
        btn.addEventListener('click', function () {
            // Clear armed-class on all rows in this checklist; arm this one.
            var rows = card.querySelectorAll('.phase-2c-checklist-row');
            rows.forEach(function (r) { r.classList.remove('armed'); });
            li.classList.add('armed');
            state.currentAnchorId = anchor.id;
        });
        li.appendChild(btn);

        var status = document.createElement('span');
        status.className = 'phase-2c-anchor-status';
        status.textContent = '';
        li.appendChild(status);

        return li;
    }

    function markChecklistRowComplete(checklist, anchorId, pt) {
        var row = checklist.querySelector('[data-anchor-id="' + cssEscape(anchorId) + '"]');
        if (!row) return;
        row.classList.remove('armed');
        row.classList.add('complete');
        var status = row.querySelector('.phase-2c-anchor-status');
        if (status) status.textContent = ' → (' + Math.round(pt.x) + ', ' + Math.round(pt.y) + ')';
    }

    // Minimal CSS.escape polyfill for environments where window.CSS is absent.
    // Anchor IDs are constrained to ^[A-Za-z_][A-Za-z0-9_]*$ by Phase 2b's
    // parseCustomAnchors, so this is just defensive — the regex output is
    // always selector-safe — but it future-proofs against id schemes that
    // include a colon or hyphen.
    function cssEscape(value) {
        if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
        return String(value).replace(/[^A-Za-z0-9_-]/g, function (ch) {
            return '\\' + ch.charCodeAt(0).toString(16) + ' ';
        });
    }

    function postAnchors(photoId, photo, anchors, state, resultEl, card) {
        var clickedAnchors = anchors
            .filter(function (a) { return state.clicks[a.id]; })
            .map(function (a) {
                var pt = state.clicks[a.id];
                return {
                    pcb_xyz_mm: a.xyz,
                    pixel: [pt.x, pt.y],
                };
            });

        var body = {
            photo_id: photoId,
            anchors: clickedAnchors,
            image_size: state.imageSize,
        };
        // Pick lens path: explicit lens_id from Phase 2a, with EXIF dict
        // attached when the user picked the "exif:detected" sentinel.
        var lensId = (photo && photo.lens) || '';
        if (lensId) {
            body.lens_id = lensId;
            if (lensId === 'exif:detected' && photo.exif) {
                body.exif = {
                    focalLength35mm: photo.exif.focalLength35mm,
                    focalLength: photo.exif.focalLength,
                };
            }
        }
        // No lens_id picked → server returns 400; surface as a clear error.

        var solveBtn = card.querySelector('.phase-2c-solve-btn');
        if (solveBtn) solveBtn.disabled = true;
        clearChildren(resultEl);
        resultEl.textContent = 'Solving pose…';

        fetch('/api/anchors', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
            .then(function (r) {
                return r.json().then(function (data) { return { ok: r.ok, data: data }; });
            })
            .then(function (resp) {
                clearChildren(resultEl);
                if (!resp.ok) {
                    resultEl.className = 'phase-2c-result error';
                    resultEl.textContent = 'Solve failed: ' + (resp.data.error || 'unknown error');
                    if (solveBtn) solveBtn.disabled = false;
                    return;
                }
                resultEl.className = 'phase-2c-result success';
                var rms = (resp.data.pose || {}).anchor_reprojection_rms_px;
                var suspect = resp.data.intrinsics_suspect;
                resultEl.textContent =
                    'Pose solved. RMS = ' + (rms != null ? rms.toFixed(2) : '?') +
                    ' normalized px. intrinsics_suspect: ' + (suspect ? 'yes' : 'no');
                // Record on spatialState so subsequent phases can read it.
                window.spatialState.photos[photoId].pose = resp.data.pose;
                window.spatialState.photos[photoId].intrinsicsSuspect = !!suspect;
                // Enable Next once at least one photo has a solved pose.
                var nextBtn = document.getElementById('phase-2c-next');
                if (nextBtn) nextBtn.disabled = false;
            })
            .catch(function (err) {
                clearChildren(resultEl);
                resultEl.className = 'phase-2c-result error';
                resultEl.textContent = 'Network error: ' + err.message;
                if (solveBtn) solveBtn.disabled = false;
            });
    }
```

- [ ] **Step 6.2: Verify the file is syntactically valid by running existing tests**

Run: `cd /home/administrator/Code/agent-spatial-toolkit-ui && uv run pytest tests/test_ui_smoke.py -v`
Expected: PASS. If this fails with a JS syntax error reported by Playwright's console capture, fix the syntax before continuing.

- [ ] **Step 6.3: Commit**

```bash
cd /home/administrator/Code/agent-spatial-toolkit-ui
git add src/agent_spatial_toolkit/ui/app.js
GIT_AUTHOR_NAME=cameronzucker GIT_AUTHOR_EMAIL=cameronzucker@gmail.com \
GIT_COMMITTER_NAME=cameronzucker GIT_COMMITTER_EMAIL=cameronzucker@gmail.com \
git commit -m "feat(ui): wirePhase2c — anchor checklist + click capture + POST (Task 1.D.5 PR-α step 6)

For each photo: render canvas (loadImageToCanvas), anchor checklist
(one row per Phase 2b anchor), per-photo Solve button (enabled at ≥3
clicks), result display. Click flow: arm anchor row → click on canvas →
captureClick records pixel into per-photo state.clicks → repeat → Solve
POSTs to /api/anchors with lens_id (and EXIF if exif:detected was picked).

MutationObserver on #phase-2c.hidden triggers card render on first reveal,
keeping wirePhase2b unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Playwright happy-path test

**Files:**
- Create: `tests/test_ui_phase_2c.py`

- [ ] **Step 7.1: Read existing Playwright fixture pattern**

Inspect `tests/test_ui_phase_2b.py` and `tests/conftest.py` for the existing fixture pattern (server startup, page load, etc.). Match the same idioms — same imports, same fixture names, same wait helpers. Do NOT introduce new test infrastructure.

- [ ] **Step 7.2: Write the failing test**

Create `tests/test_ui_phase_2c.py`:

```python
"""Playwright happy-path test for Phase 2c (Task 1.D.5 PR-α).

Mirrors tests/test_ui_phase_2b.py's structure: spin up the wizard server
on the synthetic-card fixture, walk through Phase 2a → 2b → 2c, click
4 anchors on the first photo, click Solve, verify the result text appears
and the Next button enables.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

# Use the same wizard-server fixture the other UI tests use; assume it lives
# in conftest.py as `wizard_server` (returns a base URL). If the actual
# fixture name differs, adjust here — match what test_ui_phase_2b.py uses.
pytestmark = pytest.mark.usefixtures("wizard_server")


def test_phase_2c_anchor_click_flow_happy_path(page: Page, wizard_server: str) -> None:
    """End-to-end: open wizard → finish 2a + 2b → click 4 anchors → Solve → success."""
    page.goto(wizard_server)

    # === Phase 2a: pick a lens for the first photo ===
    # Wait for at least one thumbnail to render (drives off /api/state).
    page.wait_for_selector(".thumbnail", state="visible")
    # Pick the standard Pi camera (resolvable in the lens catalog).
    first_lens_select = page.locator(".thumbnail .lens-select").first
    first_lens_select.select_option(value="pi_camera_module_3_standard")

    page.locator("#phase-2a-next").click()
    page.wait_for_selector("#phase-2b", state="visible")

    # === Phase 2b: pick the standard PCB preset + dimensions ===
    page.locator("#phase-2b-preset").select_option(value="pcb_standard")
    page.locator("#phase-2b-long-edge").fill("86.5")
    page.locator("#phase-2b-short-edge").fill("84.5")
    page.locator("#phase-2b-next").click()
    page.wait_for_selector("#phase-2c", state="visible")

    # === Phase 2c: click 4 anchors on the first photo, then Solve ===
    first_card = page.locator(".phase-2c-card").first
    # Wait for the canvas to be sized (loadImageToCanvas resolved).
    canvas = first_card.locator("canvas.phase-2c-canvas")
    page.wait_for_function(
        "el => el && el.width > 0 && el.height > 0",
        arg=canvas.element_handle(),
    )

    anchor_buttons = first_card.locator(".phase-2c-anchor-btn")
    assert anchor_buttons.count() == 4, "PCB preset should produce exactly 4 anchors"

    # Click each anchor button → click on the canvas at distinct pixel positions.
    # The exact pixels don't need to be physically meaningful for the API to
    # respond — solvePnP may return high RMS (intrinsics_suspect=true) but the
    # endpoint still returns 200. We assert on response presence + Next enable.
    canvas_box = canvas.bounding_box()
    assert canvas_box is not None
    click_targets = [
        (canvas_box["x"] + 100, canvas_box["y"] + 100),
        (canvas_box["x"] + canvas_box["width"] - 100, canvas_box["y"] + 100),
        (canvas_box["x"] + 100, canvas_box["y"] + canvas_box["height"] - 100),
        (canvas_box["x"] + canvas_box["width"] - 100, canvas_box["y"] + canvas_box["height"] - 100),
    ]
    for i, (cx, cy) in enumerate(click_targets):
        anchor_buttons.nth(i).click()
        # captureClick listens on the canvas; click on the canvas at the target.
        page.mouse.click(cx, cy)

    solve_btn = first_card.locator(".phase-2c-solve-btn")
    page.wait_for_function(
        "el => el && !el.disabled",
        arg=solve_btn.element_handle(),
    )
    solve_btn.click()

    # Result text appears; success path includes "Pose solved" or "intrinsics_suspect"
    # (either is OK for the smoke test — failed solve would show "Solve failed:").
    result_locator = first_card.locator(".phase-2c-result")
    page.wait_for_function(
        "el => el && el.textContent && el.textContent.length > 0 && !el.textContent.includes('Solving pose…')",
        arg=result_locator.element_handle(),
    )
    result_text = result_locator.text_content() or ""
    # Either solved cleanly, or the API returned a clear failure that the UI surfaced
    # — both prove the round-trip works. We require the success branch here so the
    # test catches a regression in the Solve button's POST path.
    assert "Pose solved" in result_text or "intrinsics_suspect" in result_text, result_text

    # Next button enables once at least one photo has a pose
    next_btn = page.locator("#phase-2c-next")
    page.wait_for_function("el => el && !el.disabled", arg=next_btn.element_handle())


def test_phase_2c_no_lens_picked_surfaces_error(page: Page, wizard_server: str) -> None:
    """If the user reaches Phase 2c without picking a lens, Solve POSTs and gets a 400.

    The UI must surface the server's error rather than silently failing.
    """
    page.goto(wizard_server)
    page.wait_for_selector(".thumbnail", state="visible")
    # Skip lens selection — go straight to Next.
    page.locator("#phase-2a-next").click()
    page.wait_for_selector("#phase-2b", state="visible")

    page.locator("#phase-2b-preset").select_option(value="pcb_standard")
    page.locator("#phase-2b-long-edge").fill("86.5")
    page.locator("#phase-2b-short-edge").fill("84.5")
    page.locator("#phase-2b-next").click()
    page.wait_for_selector("#phase-2c", state="visible")

    first_card = page.locator(".phase-2c-card").first
    canvas = first_card.locator("canvas.phase-2c-canvas")
    page.wait_for_function(
        "el => el && el.width > 0 && el.height > 0",
        arg=canvas.element_handle(),
    )
    canvas_box = canvas.bounding_box()
    anchor_buttons = first_card.locator(".phase-2c-anchor-btn")
    for i in range(min(3, anchor_buttons.count())):
        anchor_buttons.nth(i).click()
        page.mouse.click(canvas_box["x"] + 100 + i * 50, canvas_box["y"] + 100 + i * 50)

    first_card.locator(".phase-2c-solve-btn").click()
    result_locator = first_card.locator(".phase-2c-result.error")
    page.wait_for_selector(".phase-2c-result.error", state="visible")
    err_text = result_locator.text_content() or ""
    assert "Solve failed" in err_text
```

- [ ] **Step 7.3: Run the test; expect pass**

Run: `cd /home/administrator/Code/agent-spatial-toolkit-ui && uv run pytest tests/test_ui_phase_2c.py -v`
Expected: 2 PASS.

If the `wizard_server` fixture has a different name in this codebase, the test will fail at collection — adjust the fixture import / name to match what `test_ui_phase_2b.py` uses.

If the test fails because canvas clicks don't register: check that `captureClick` in `helpers.js` uses `addEventListener('click', ...)`; Playwright's `page.mouse.click(x, y)` should fire a real click. Add a `page.wait_for_timeout(50)` between arming an anchor and clicking the canvas if there's a race.

- [ ] **Step 7.4: Commit**

```bash
cd /home/administrator/Code/agent-spatial-toolkit-ui
git add tests/test_ui_phase_2c.py
GIT_AUTHOR_NAME=cameronzucker GIT_AUTHOR_EMAIL=cameronzucker@gmail.com \
GIT_COMMITTER_NAME=cameronzucker GIT_COMMITTER_EMAIL=cameronzucker@gmail.com \
git commit -m "test(ui): Phase 2c happy + error path Playwright tests (Task 1.D.5 PR-α step 7)

End-to-end: open wizard → finish 2a + 2b → click 4 anchors → Solve →
verify result text appears + Next enables. Second test exercises the
no-lens-picked path: server returns 400, UI surfaces the error visibly
(no silent failure).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Final verification + open PR

- [ ] **Step 8.1: Run the full test suite to confirm no regressions**

Run: `cd /home/administrator/Code/agent-spatial-toolkit-ui && uv run pytest -v`
Expected: All PASS. If anything fails, fix before opening the PR.

- [ ] **Step 8.2: Push the branch and open the PR**

```bash
cd /home/administrator/Code/agent-spatial-toolkit-ui
git push -u origin feat/ui-phase-2c-anchor-clicking 2>&1 | tail -5
gh pr create --title "feat(ui): Phase 2c click flow + lens catalog (Task 1.D.5 PR-α)" --body "$(cat <<'EOF'
## Summary
PR-α of two for Task 1.D.5 (Phase 2c). Lens catalog moves to the server; client sends \`lens_id\` to \`/api/anchors\`; the existing intrinsics-dict path is preserved for backward compatibility. Phase 2c UI: one card per photo with anchor checklist, click-to-record-pixel, Solve button, result display.

## What's in this PR
- \`server/lens_catalog.py\` — canonical lens table + \`resolve(lens_id, image_size, exif) -> Intrinsics | None\`. 9 unit tests.
- \`GET /api/lens_catalog\` — returns id + label + resolvable flag (no intrinsics leakage). 2 tests.
- \`POST /api/anchors\` — extended to accept \`lens_id\`; existing \`intrinsics\` dict path preserved. 5 tests.
- \`index.html\` Phase 2c container scaffold.
- \`app.js\` — fetch catalog at init; replace static \`LENS_OPTIONS\`; new \`wirePhase2c()\` with anchor checklist + captureClick + POST + result display.
- 2 Playwright tests: happy path + no-lens-picked error path.

## Out of scope (PR-β)
- Wireframe overlay rendering (server-side \`render_wireframe\` + \`/api/wireframe/<id>\`)
- Retry-on-RMS UX
- "Skip this photo" / "Continue anyway" affordances

## Design doc
See \`docs/plans/2026-05-04-1d5-phase2c-design.md\` (committed as the first commit of this branch) for the three architectural decisions and rationale.

## Test plan
- [ ] CI: 4-version pytest matrix passes
- [ ] Local Playwright tests pass (\`uv run pytest tests/test_ui_phase_2c.py -v\`)
- [ ] Backward compat: existing \`tests/test_app.py\` anchor tests pass unmodified

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8.3: Cross-model review (parallel dispatch)**

Per [feedback memory: Invoke Codex liberally for cross-perspective] and the session-5 framework discipline, dispatch two reviewers in parallel after the PR opens:

```
Agent(subagent_type="superpowers:code-reviewer", description="PR-α implementation review",
      prompt="Review PR <N> against docs/plans/2026-05-04-1d5-pra-implementation.md and
              docs/plans/2026-05-04-1d5-phase2c-design.md. Flag deviations, missing tests,
              security/XSS issues in the JS, and any backward-compat regressions on the
              /api/anchors endpoint. Report under 400 words.")

Bash(command="codex exec --skip-git-repo-check --output-last-message /tmp/codex-review.txt < /tmp/codex-prompt.txt",
     description="Codex cross-model review")
```

Where the codex prompt focuses on:
- Whether the lens-catalog design holds for the eventual chessboard-calibration extension (Phase 2 Stream G)
- Whether the JS XSS posture matches the rest of the wizard (no innerHTML, all DOM API)
- Whether the wirePhase2c MutationObserver pattern is robust to repeat 2b → 2c → back-to-2b cycles (it's not — known follow-up if anyone hits it; document in PR thread)

- [ ] **Step 8.4: Address any review findings, then merge**

If Codex / code-reviewer flag issues, address them in a fixup commit (or amend if the change is trivial). When CI is green and reviews are addressed:

```bash
gh pr merge <N> --squash --delete-branch
```

- [ ] **Step 8.5: Mark task complete in todos and proceed to PR-β planning**

After merge, pull latest main into the worktree and start the next planning cycle for PR-β (wireframe overlay + retry UX).

---

## Self-Review

**Spec coverage:**
- Spec §3 Phase 2c "anchor checklist beside the photo" → Task 6 (`buildAnchorChecklistRow`)
- Spec §3 Phase 2c "minimum 3 non-collinear anchors" → enforced by Solve button enable rule (Task 6); server returns 400 from `PoseSolveError` for collinear/insufficient
- Spec §3 Phase 2c "server runs cv2.solvePnP" → existing `pose.solve_pnp` (no change needed)
- Spec §3 Phase 2c "wireframe overlay drawn onto the photo" → **deferred to PR-β** (out-of-scope per design doc)
- Spec §3 Phase 2c "intrinsics_suspect on first attempt" → server already returns this; client displays in Task 6 (`postAnchors` result text)
- Spec §3 Phase 2c "user gets one retry" → **deferred to PR-β**
- Spec §5.1 step 4 "RMS > 5 normalized px" → existing `solve_pnp` enforces; client displays the value
- Spec §5.3 "minimum 3 non-collinear anchors per photo" → ✅
- Spec §6 Intrinsics schema → ✅ unchanged

**Placeholder scan:** All steps include actual code. No "TBD"/"TODO"/"implement later" patterns.

**Type consistency:**
- `LensCatalogEntry` defined Task 1, used Task 1 only.
- `LENS_CATALOG: dict[str, LensCatalogEntry]` — same type referenced consistently.
- `resolve(lens_id, image_size, exif)` signature stable Task 1 → Task 3.
- Client `window.spatialState.lensCatalog` introduced Task 5, consumed Task 5 (`lensCatalogToOptions`) and not re-typed.
- `state` object in `buildPhase2cCard` (Task 6) carries `currentAnchorId`, `clicks`, `imageSize` — all used consistently within the closure.

**Scope check:** Single-PR scope; clean handoff to PR-β. No cross-stream dependencies introduced.

**Ambiguity check:**
- Task 5.2 notes the possibility that `tests/test_ui_phase_2a.py` asserts on a specific lens label that changed; explicit fix-up instruction included.
- Task 7.3 notes Playwright fixture name might vary; explicit fix-up instruction included.
- Task 6's MutationObserver one-shot pattern is documented in code comments + Task 8.3 review prompt flags the "back-to-2b" edge case.
