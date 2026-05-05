# Wizard Redesign PR-2 — Server Endpoints + HEIC Decode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the server-side endpoint contracts the redesigned wizard's UI will call. Adds in-wizard photo uploads (with transparent HEIC→JPEG decode), repurposes `/api/anchors` → `/api/reference` (with reference-object dimension lookup), extends `/api/feature` to accept multi-view click lists, and lands stub implementations for the three new endpoints whose real logic arrives in PR-3 (`/api/marker_detect`, `/api/next_prompt`, `/api/reproject_all`).

**Architecture:** All changes live in `src/agent_spatial_toolkit/server/`. New file `reference_objects.py` carries the wallet-item / marker dimension lookup. Existing `app.py` gains 4 new routes plus an extension of `/api/feature`. New dependency `pillow-heif` decodes iPhone-default HEIC photos transparently — user never sees a format error. **Old endpoints (`/api/anchors`, `/api/lens_catalog`, `/api/wireframe`) remain fully functional during transition** — they're deleted in PR-4 once the new UI fully replaces the old wiring.

**Tech Stack:** Python 3.10+, Flask, OpenCV (`cv2.solvePnP`, `cv2.aruco` deferred to PR-3), Pillow + `pillow-heif`, NumPy. New dep: `pillow-heif`. No other new deps.

**Reference:** [docs/specs/2026-05-05-wizard-ux-redesign-design.md](../specs/2026-05-05-wizard-ux-redesign-design.md) §3 (Endpoint changes table, HEIC handling, photo lifecycle), §6 (PR-2 row).

**Carry-forward from PR-1:** Schema fields `Feature.noisy`, `Feature.warning`, quality flags `intrinsics_estimated` and `underside_unverified` are merged on main (PR #50, commit `e192494`). The schema is ready to receive the data PR-2's endpoints will produce; PR-2 itself doesn't write `noisy`/`warning` (that's PR-3 logic), it just lays the contract for PR-3 to populate.

**Deferred items from PR-1's final review** (relevant to PR-2 scope):

- `method` enum extension (`single_view_planar` value) — design §3 calls for it, deferred from PR-1; PR-3 adds it when triangulation wiring lands. **PR-2 does not need it** because PR-2's `/api/feature` extension keeps single-view ray-cast on the existing `planar_intersection` enum value.
- Regression-suite gaps (triangulation branch, populated `SessionArtifacts`) — strengthen in PR-3.

**All implementer and reviewer subagents dispatched on Opus 4.7** (`model: "opus"` per Agent tool call). See `feedback_subagent_model_opus_for_spatial.md` in auto-memory.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Add `pillow-heif >= 0.16, < 1.0` to project dependencies (sibling of `pillow`). Re-resolve via `uv lock`. |
| `src/agent_spatial_toolkit/server/reference_objects.py` | **Create** | Static lookup of known reference-object dimensions: credit card (ISO/IEC 7810 ID-1), US dollar bill, ChArUco-marker default. Returns the 4 corner positions in 3D (in the reference's own frame, with origin at the visual top-left corner, X along the long edge, Y along the short edge, Z=0 since the reference is flat) given a `reference_type` enum value. |
| `src/agent_spatial_toolkit/server/app.py` | Modify | Add 5 new routes (`POST /api/photo`, `POST /api/reference`, `GET /api/marker_detect/<photo_id>`, `GET /api/next_prompt`, `GET /api/reproject_all`). Extend `POST /api/feature` to accept multi-view click lists (1 click → existing ray-cast; ≥2 → return HTTP 501 with descriptive body until PR-3 wires triangulation). Old endpoints unchanged. |
| `tests/test_reference_objects.py` | **Create** | Tests for the dimension lookup: each reference type returns the right 3D corner positions; invalid type raises `ValidationError`. |
| `tests/test_app_photo_upload.py` | **Create** | Tests for `POST /api/photo`: JPEG round-trip, PNG round-trip, HEIC→JPEG decode round-trip, sha256 stability, manifest updates, payload-too-large rejection (>50 MB). |
| `tests/test_app.py` | Modify | Add tests for `/api/reference` (each reference type, missing-required-field rejections, intrinsics resolution paths), `/api/feature` multi-view contract (1 click works, 2 clicks returns 501), and the three stub endpoints (`/api/marker_detect` returns null, `/api/next_prompt` returns deterministic shape, `/api/reproject_all` returns empty dict). Add regression tests confirming `/api/anchors`, `/api/lens_catalog`, `/api/wireframe` still functional during transition. |
| `tests/fixtures/heic/sample.heic` | **Create** | A small sample HEIC file (≤200 KB) used by the photo-upload HEIC test. Generated from `tests/fixtures/synthetic_card/test1.jpg` via Pillow + `pillow-heif`. Generation script lives in the test itself (auto-generates on first run if missing) so we don't bloat the repo with a pre-built binary if it can be derived. |

No changes to `pipeline/`, `schema/`, `cli.py`, or `ui/` in this PR.

---

## Task 1: Add `pillow-heif` dependency

**Files:**
- Modify: `pyproject.toml` (add to `dependencies` array)
- Modify: `uv.lock` (regenerated, gitignored — no manual edit)

`pillow-heif` is the official Pillow plugin for HEIC/HEIF decoding. iPhone defaults to HEIC since iOS 11, so without this dep the wizard rejects every iPhone photo straight to Bucket-3 hard-refusal — a Success-Criterion-#1 violation per design §8.

- [ ] **Step 1: Inspect the current dependency list**

Run: `grep -A 30 'dependencies = \[' /home/administrator/Code/agent-spatial-toolkit/pyproject.toml`

Expected: an array of dependency specifiers including `pillow`, `numpy`, `flask`, etc. Note the format and version-pinning style.

- [ ] **Step 2: Add `pillow-heif`**

Modify `pyproject.toml` to add `"pillow-heif >= 0.16, < 1.0"` to the `dependencies` array (alphabetical sort if the file is alphabetized; otherwise group it next to `pillow`). The upper cap mirrors the project's existing dep-cap convention (issue #4 hygiene work).

- [ ] **Step 3: Resolve dependencies**

Run: `uv lock`

Expected: `uv.lock` is updated (gitignored, no commit). No errors. If `pillow-heif` has unsupported wheels for any of Python 3.10/3.11/3.12/3.13, STOP and surface the failure — we may need to relax the version cap or add an environment marker.

- [ ] **Step 4: Verify the import works**

Run: `uv run python -c "import pillow_heif; pillow_heif.register_heif_opener(); from PIL import Image; print('pillow-heif import OK')"`

Expected: `pillow-heif import OK`. Any failure indicates an environment issue.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(deps): add pillow-heif for transparent HEIC decode

iPhone has defaulted to HEIC since iOS 11 (2017). Without server-side
decode, the wizard would reject every iPhone photo on upload — a
Success-Criterion-#1 violation (non-CAD user must be able to complete
the flow without doing anything outside the wizard's instructions).

Pinned ' >= 0.16, < 1.0' per the project's dep-cap convention.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §3 HEIC
handling, §6 PR-2 row.
Part of: Wizard UX redesign PR-2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Add `reference_objects` lookup module

**Files:**
- Create: `src/agent_spatial_toolkit/server/reference_objects.py`
- Create: `tests/test_reference_objects.py`

This module is the new "lens catalog" replacement for the wizard's user-facing reference-picker (design §2 step 2). Three reference types in v1: `credit_card` (ISO/IEC 7810 ID-1), `dollar_bill` (US one-dollar note), and `marker` (printed ChArUco PDF — dimensions are wizard-provided so they're known). Each returns its 4 corner positions in 3D, in its own frame: origin at top-left corner (visually upper-left when reference is laid flat with long edge horizontal), X along long edge (positive direction), Y along short edge, Z=0 (reference is flat).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reference_objects.py`:

```python
"""Tests for the wallet-item / marker reference-object dimension lookup
that replaces the legacy lens_catalog for the redesigned wizard."""

from __future__ import annotations

import pytest

from agent_spatial_toolkit.server.reference_objects import (
    ReferenceObjectError,
    get_corner_positions_mm,
)


def test_credit_card_corners_match_iso_iec_7810_id_1() -> None:
    """Credit card is 85.60 × 53.98 mm per ISO/IEC 7810 ID-1."""
    corners = get_corner_positions_mm("credit_card")
    # 4 corners, clockwise from top-left in the reference's own frame.
    assert corners == [
        (0.0, 0.0, 0.0),
        (85.60, 0.0, 0.0),
        (85.60, 53.98, 0.0),
        (0.0, 53.98, 0.0),
    ]


def test_dollar_bill_corners_match_us_treasury() -> None:
    """US dollar bill is 156.1 × 66.3 mm."""
    corners = get_corner_positions_mm("dollar_bill")
    assert corners == [
        (0.0, 0.0, 0.0),
        (156.1, 0.0, 0.0),
        (156.1, 66.3, 0.0),
        (0.0, 66.3, 0.0),
    ]


def test_marker_corners_use_default_charuco_dimensions() -> None:
    """Default ChArUco marker bundled with the wizard is 100 × 100 mm.
    (The actual PDF served by the wizard will encode this size; the
    lookup mirrors the printed dimensions.)"""
    corners = get_corner_positions_mm("marker")
    assert corners == [
        (0.0, 0.0, 0.0),
        (100.0, 0.0, 0.0),
        (100.0, 100.0, 0.0),
        (0.0, 100.0, 0.0),
    ]


def test_unknown_reference_type_raises() -> None:
    with pytest.raises(ReferenceObjectError, match="unknown reference type"):
        get_corner_positions_mm("not_a_real_type")


def test_invalid_argument_type_raises() -> None:
    with pytest.raises(ReferenceObjectError, match="must be a string"):
        get_corner_positions_mm(123)  # type: ignore[arg-type]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_reference_objects.py -v`

Expected: All five tests FAIL with `ImportError` or `ModuleNotFoundError` (the file doesn't exist yet).

- [ ] **Step 3: Implement the module**

Create `src/agent_spatial_toolkit/server/reference_objects.py`:

```python
"""Reference-object dimensions for the redesigned wizard's scale references.

The wizard's first-screen picker (design §2 step 2) offers three options:
credit card, US dollar bill, or printed ChArUco marker. Each has known
physical dimensions; this module returns the 4 corner positions in 3D
(in the reference's own frame) for any chosen type.

Frame convention: origin at the reference's visual top-left corner, X
along the long edge (positive), Y along the short edge (positive), Z=0
(reference is flat on the surface).
"""

from __future__ import annotations

# Reference type → (long_edge_mm, short_edge_mm)
# Sources:
#   credit_card: ISO/IEC 7810 ID-1
#   dollar_bill: US Treasury (https://www.bep.gov)
#   marker: bundled ChArUco PDF (wizard-controlled; matches PDF size)
_DIMENSIONS_MM: dict[str, tuple[float, float]] = {
    "credit_card": (85.60, 53.98),
    "dollar_bill": (156.1, 66.3),
    "marker": (100.0, 100.0),
}


class ReferenceObjectError(ValueError):
    """Raised when the requested reference type is unknown or argument is invalid."""


def get_corner_positions_mm(reference_type: str) -> list[tuple[float, float, float]]:
    """Return the 4 corner positions in 3D for the given reference type.

    Corners are clockwise from top-left in the reference's own frame.
    Origin = top-left corner; X = along long edge; Y = along short edge;
    Z = 0.
    """
    if not isinstance(reference_type, str):
        raise ReferenceObjectError(
            f"reference_type must be a string, got {type(reference_type).__name__}"
        )
    if reference_type not in _DIMENSIONS_MM:
        raise ReferenceObjectError(
            f"unknown reference type {reference_type!r}; "
            f"valid: {sorted(_DIMENSIONS_MM.keys())}"
        )
    long_mm, short_mm = _DIMENSIONS_MM[reference_type]
    return [
        (0.0, 0.0, 0.0),
        (long_mm, 0.0, 0.0),
        (long_mm, short_mm, 0.0),
        (0.0, short_mm, 0.0),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_reference_objects.py -v`

Expected: All 5 tests PASS.

- [ ] **Step 5: Run full suite + ruff to confirm no regression**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 196 tests pass (191 baseline from PR-1 + 5 new); ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/agent_spatial_toolkit/server/reference_objects.py tests/test_reference_objects.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(server): reference-object dimension lookup (replaces lens_catalog)

Static lookup mapping reference type (credit_card, dollar_bill, marker)
to the 4 corner positions in 3D in the reference's own frame. Replaces
the user-facing lens-catalog picker for the redesigned wizard's
scale-reference step (design §2 step 2). Frame convention: origin at
top-left corner, X along long edge, Y along short edge, Z=0 (flat
reference).

Used by the upcoming /api/reference endpoint (next task) to build
world-point arrays for cv2.solvePnP from the reference-type enum the
wizard sends.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §2, §3.
Part of: Wizard UX redesign PR-2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Add `POST /api/photo` upload endpoint with HEIC decode

**Files:**
- Modify: `src/agent_spatial_toolkit/server/app.py` (add new route + helper)
- Create: `tests/test_app_photo_upload.py`

The wizard's photo-capture step (design §2 step 4) sends each new photo to `POST /api/photo`. Server transparently HEIC-decodes if needed (Task 1's `pillow-heif`), hashes the file (sha256), persists JPEG to `<session>/photos/<photo_id>.jpg`, and updates the in-memory photo registry. The pre-CLI manifest path stays usable (existing `cli.py annotate --photos *.jpg` flow unchanged); this new endpoint is the in-wizard analogue.

**Important constraints:**

- Accept `image/jpeg`, `image/png`, `image/heic`, `image/heif` content types.
- Reject other types with 415 Unsupported Media Type + plain message.
- Reject payloads >50 MB with 413 Payload Too Large.
- HEIC payloads decode via `pillow-heif`; saved as JPEG (quality=95).
- Photo ID is content-derived: `photo_<sha256[:12]>` (deterministic — same photo uploaded twice gets the same ID, idempotent).
- Manifest update: append the new photo entry if its ID isn't already present (idempotent re-upload).
- Returns `{"photo_id": "...", "sha256": "...", "stored_format": "jpeg"}` on success.

- [ ] **Step 1: Write failing tests**

Create `tests/test_app_photo_upload.py`:

```python
"""Tests for POST /api/photo: the in-wizard photo upload path with
transparent HEIC→JPEG decode (design §3 HEIC handling)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from tests.helpers import make_test_app  # follows existing tests/test_app.py convention


def _make_jpeg_bytes(width: int = 200, height: int = 150) -> bytes:
    """A small valid JPEG for upload tests."""
    img = Image.new("RGB", (width, height), color=(128, 64, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _make_png_bytes(width: int = 200, height: int = 150) -> bytes:
    img = Image.new("RGB", (width, height), color=(64, 200, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_heic_bytes(width: int = 200, height: int = 150) -> bytes:
    """Generate a HEIC payload via pillow-heif. Skips the test if the
    plugin isn't loadable in this environment (won't happen in CI)."""
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    img = Image.new("RGB", (width, height), color=(200, 128, 64))
    buf = io.BytesIO()
    img.save(buf, format="HEIF", quality=85)
    return buf.getvalue()


def test_jpeg_upload_round_trips_and_persists(tmp_path: Path) -> None:
    """JPEG upload returns photo_id derived from sha256 prefix; file lands on disk."""
    app, session = make_test_app(tmp_path)
    client = app.test_client()
    body = _make_jpeg_bytes()

    response = client.post("/api/photo", data=body, content_type="image/jpeg")
    assert response.status_code == 200
    data = response.get_json()
    assert data["photo_id"].startswith("photo_")
    assert data["sha256"] == hashlib.sha256(body).hexdigest()
    assert data["stored_format"] == "jpeg"

    photo_path = session.photos_dir / f"{data['photo_id']}.jpg"
    assert photo_path.exists(), "photo must persist to <session>/photos/<id>.jpg"


def test_png_upload_persists_as_jpeg(tmp_path: Path) -> None:
    """PNG uploads are accepted; saved as JPEG for consistency."""
    app, session = make_test_app(tmp_path)
    client = app.test_client()
    body = _make_png_bytes()

    response = client.post("/api/photo", data=body, content_type="image/png")
    assert response.status_code == 200
    data = response.get_json()
    assert data["stored_format"] == "jpeg"
    assert (session.photos_dir / f"{data['photo_id']}.jpg").exists()


def test_heic_upload_decodes_to_jpeg(tmp_path: Path) -> None:
    """The headline HEIC path: iPhone photo arrives, server decodes
    transparently, file saved as JPEG. User sees no error."""
    app, session = make_test_app(tmp_path)
    client = app.test_client()
    body = _make_heic_bytes()

    response = client.post("/api/photo", data=body, content_type="image/heic")
    assert response.status_code == 200
    data = response.get_json()
    assert data["stored_format"] == "jpeg"
    photo_path = session.photos_dir / f"{data['photo_id']}.jpg"
    assert photo_path.exists()
    # Verify the saved file is actually a JPEG, not the HEIC bytes.
    with photo_path.open("rb") as f:
        header = f.read(3)
    assert header[:3] == b"\xff\xd8\xff", "saved file must be JPEG (SOI marker)"


def test_unsupported_format_returns_415(tmp_path: Path) -> None:
    app, _ = make_test_app(tmp_path)
    client = app.test_client()
    response = client.post("/api/photo", data=b"some text", content_type="text/plain")
    assert response.status_code == 415
    assert "JPEG" in response.get_json()["error"] or "PNG" in response.get_json()["error"]


def test_payload_too_large_returns_413(tmp_path: Path) -> None:
    """Payloads >50 MB rejected to prevent abuse / phone fat-finger-uploads."""
    app, _ = make_test_app(tmp_path)
    client = app.test_client()
    # 51 MB of zero bytes.
    body = b"\x00" * (51 * 1024 * 1024)
    response = client.post("/api/photo", data=body, content_type="image/jpeg")
    assert response.status_code == 413


def test_re_upload_same_photo_is_idempotent(tmp_path: Path) -> None:
    """Same content → same photo_id → manifest only has one entry for it."""
    app, session = make_test_app(tmp_path)
    client = app.test_client()
    body = _make_jpeg_bytes()

    r1 = client.post("/api/photo", data=body, content_type="image/jpeg")
    r2 = client.post("/api/photo", data=body, content_type="image/jpeg")
    assert r1.status_code == r2.status_code == 200
    assert r1.get_json()["photo_id"] == r2.get_json()["photo_id"]
```

Note: this test file imports `tests.helpers.make_test_app`. If that helper doesn't exist yet, **STOP** and ask the orchestrator how to construct a test Flask app per the existing `tests/test_app.py` conventions — don't invent a new helper. Most likely `tests/test_app.py` already has a fixture or helper for this; mirror it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app_photo_upload.py -v`

Expected: All 6 tests FAIL — `/api/photo` doesn't exist yet, will return 404.

- [ ] **Step 3: Implement the route**

In `src/agent_spatial_toolkit/server/app.py`, add the new route alongside existing routes. Place it after `_register_routes(app: Flask)`'s `get_state` route and before `get_lens_catalog` (preserves chronological-by-flow ordering).

```python
    @app.post("/api/photo")
    def post_photo() -> Any:
        """Upload a photo to the session (design §3 photo lifecycle).

        Accepts JPEG, PNG, HEIC, HEIF. HEIC is transparently decoded to JPEG
        via pillow-heif so iPhone users never see a format error.
        Returns the content-derived photo_id (sha256 prefix) so re-uploads
        of the same content are idempotent.
        """
        import pillow_heif
        from PIL import Image

        # 50 MB cap; phone shots rarely exceed 30 MB even at max-res HEIF.
        MAX_BYTES = 50 * 1024 * 1024  # noqa: N806

        content_type = (request.content_type or "").lower().split(";")[0].strip()
        if content_type not in {"image/jpeg", "image/png", "image/heic", "image/heif"}:
            return (
                jsonify({"error": "Please use JPEG, PNG, or HEIC. Most phones export one of these."}),
                415,
            )

        body = request.get_data(cache=False)
        if len(body) > MAX_BYTES:
            return (
                jsonify({"error": "This photo is unusually large (>50 MB) — reshoot at lower resolution."}),
                413,
            )

        sha256 = hashlib.sha256(body).hexdigest()
        photo_id = f"photo_{sha256[:12]}"

        # Decode + re-encode as JPEG for consistent on-disk format.
        if content_type in {"image/heic", "image/heif"}:
            pillow_heif.register_heif_opener()
        try:
            img = Image.open(io.BytesIO(body))
            img.load()  # force decode
        except Exception as e:
            return (
                jsonify({"error": f"could not decode image: {e}"}),
                400,
            )

        # Convert to RGB (HEIC/PNG-RGBA decoded variants → JPEG-compatible).
        if img.mode != "RGB":
            img = img.convert("RGB")

        session: Session = app.config["SESSION"]
        photo_path = session.photos_dir / f"{photo_id}.jpg"
        # Atomic write per project convention: write to .tmp.jpg first, then rename.
        tmp_path = photo_path.with_suffix(".jpg.tmp")
        img.save(tmp_path, format="JPEG", quality=95)
        tmp_path.replace(photo_path)

        # Update in-memory state (idempotent: re-upload of same content is a no-op
        # for the registry beyond confirming the file exists).
        mem: dict[str, Any] = app.config["STATE"]
        mem.setdefault("photos", {})
        if photo_id not in mem["photos"]:
            mem["photos"][photo_id] = {
                "id": photo_id,
                "path": str(photo_path),
                "sha256": sha256,
                "intrinsics": None,  # populated when /api/reference is called
                "pose": None,
                "anchors": [],
            }

        event_log: EventLog = app.config["EVENT_LOG"]
        event_log.write({
            "type": "photo_uploaded",
            "photo_id": photo_id,
            "sha256": sha256,
            "source_format": content_type,
        })

        return jsonify({
            "photo_id": photo_id,
            "sha256": sha256,
            "stored_format": "jpeg",
        })
```

Add the imports at the top of `app.py` if not already present:

```python
import hashlib
import io
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app_photo_upload.py -v`

Expected: All 6 tests PASS. If `make_test_app` couldn't be located in Step 1, this is when it'll bite — adapt to the actual existing test fixture pattern.

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 202 tests pass (196 prior + 6 new); ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/agent_spatial_toolkit/server/app.py tests/test_app_photo_upload.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(server): POST /api/photo with transparent HEIC decode

In-wizard photo upload path for the redesigned wizard's photo-capture
step (design §2 step 4). Accepts JPEG, PNG, HEIC, HEIF; transparently
decodes HEIC via pillow-heif (Task 1 dep) so iPhone users never see a
format error. Writes JPEG to disk via the atomic tmp+rename pattern
established by PR #18; photo_id is content-derived (sha256 prefix) so
re-upload of same content is idempotent.

Old CLI photo flow (cli.py annotate --photos *.jpg) is unchanged and
remains fully functional.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §2 step 4,
§3 (one photo's lifecycle), §3 (HEIC handling).
Part of: Wizard UX redesign PR-2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Add `POST /api/reference` endpoint

**Files:**
- Modify: `src/agent_spatial_toolkit/server/app.py` (add new route)
- Modify: `tests/test_app.py` (add tests)

This is the wizard's "confirm scale" endpoint (design §2 step 4). User taps the 4 corners of their reference object on the photo; client POSTs `{photo_id, reference_type, pixel_corners, image_size}` along with the same `intrinsics` resolution paths that `/api/anchors` accepts. Server uses the new `reference_objects.get_corner_positions_mm` lookup (Task 2) to build the world-points array, runs `cv2.solvePnP` (existing `pose.solve_pnp`), and stores the pose. Returns `{pose, intrinsics_suspect, alignment_error_mm}` (the alignment error is in mm per design §4 — though full mm-conversion lands in PR-3; PR-2 returns the existing pixel RMS for now and PR-3 converts).

**Note on intrinsics resolution:** PR-2 keeps the same `intrinsics` dict OR `lens_id` resolution path that `/api/anchors` uses. The full EXIF-auto + FOV-class fallback lands in PR-3 alongside `pipeline/intrinsics.py` wiring. **Don't drop `lens_id` here — it's needed for tests and for the transition period until PR-4 deletes `/api/lens_catalog`.**

- [ ] **Step 1: Write failing tests**

Append to `tests/test_app.py` (use existing test conventions for app fixtures, request POSTs, etc. — mirror `test_post_anchors_*` patterns):

```python
def test_post_reference_credit_card_solves_pose(client, session) -> None:
    """A simulated 4-corner credit-card click produces a pose."""
    photo_id = _upload_test_photo(client)
    response = client.post("/api/reference", json={
        "photo_id": photo_id,
        "reference_type": "credit_card",
        "pixel_corners": [[100, 100], [400, 100], [400, 300], [100, 300]],
        "image_size": [800, 600],
        "intrinsics": {"fx_px": 800.0, "fy_px": 800.0, "cx_px": 400.0, "cy_px": 300.0},
    })
    assert response.status_code == 200
    body = response.get_json()
    assert "pose" in body
    assert "intrinsics_suspect" in body
    assert body["pose"]["rvec"] is not None
    assert body["pose"]["tvec"] is not None


def test_post_reference_dollar_bill_uses_correct_dimensions(client, session) -> None:
    """Dollar-bill 4-corner clicks produce a pose with translation derived
    from the larger reference dimensions (156.1 × 66.3 mm vs 85.6 × 53.98)."""
    photo_id = _upload_test_photo(client)
    response = client.post("/api/reference", json={
        "photo_id": photo_id,
        "reference_type": "dollar_bill",
        "pixel_corners": [[100, 100], [400, 100], [400, 300], [100, 300]],
        "image_size": [800, 600],
        "intrinsics": {"fx_px": 800.0, "fy_px": 800.0, "cx_px": 400.0, "cy_px": 300.0},
    })
    assert response.status_code == 200


def test_post_reference_unknown_type_returns_400(client, session) -> None:
    photo_id = _upload_test_photo(client)
    response = client.post("/api/reference", json={
        "photo_id": photo_id,
        "reference_type": "not_a_real_type",
        "pixel_corners": [[100, 100], [400, 100], [400, 300], [100, 300]],
        "image_size": [800, 600],
        "intrinsics": {"fx_px": 800.0, "fy_px": 800.0, "cx_px": 400.0, "cy_px": 300.0},
    })
    assert response.status_code == 400
    assert "unknown reference type" in response.get_json()["error"].lower()


def test_post_reference_missing_pixel_corners_returns_400(client, session) -> None:
    photo_id = _upload_test_photo(client)
    response = client.post("/api/reference", json={
        "photo_id": photo_id,
        "reference_type": "credit_card",
        "image_size": [800, 600],
        "intrinsics": {"fx_px": 800.0, "fy_px": 800.0, "cx_px": 400.0, "cy_px": 300.0},
        # pixel_corners omitted
    })
    assert response.status_code == 400


def test_post_reference_wrong_corner_count_returns_400(client, session) -> None:
    """Need exactly 4 corners; 3 or 5 must be rejected."""
    photo_id = _upload_test_photo(client)
    for n_corners in [3, 5]:
        corners = [[100 + i * 10, 100] for i in range(n_corners)]
        response = client.post("/api/reference", json={
            "photo_id": photo_id,
            "reference_type": "credit_card",
            "pixel_corners": corners,
            "image_size": [800, 600],
            "intrinsics": {"fx_px": 800.0, "fy_px": 800.0, "cx_px": 400.0, "cy_px": 300.0},
        })
        assert response.status_code == 400, f"expected 400 for n_corners={n_corners}"
```

(`_upload_test_photo` is a helper that uploads a JPEG via `/api/photo` and returns the photo_id. Add it to the test file or use the existing test-app helper if one exists.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -k "post_reference" -v`

Expected: All 5 tests FAIL — `/api/reference` doesn't exist yet, will 404.

- [ ] **Step 3: Implement the route**

In `src/agent_spatial_toolkit/server/app.py`, add the new route AFTER `post_anchors` (keeps the related routes adjacent during transition):

```python
    @app.post("/api/reference")
    def post_reference() -> Any:
        """Confirm scale via the wizard's 4-corner reference-object flow
        (design §2 step 4). Replaces the legacy /api/anchors path for the
        redesigned wizard. Same cv2.solvePnP machinery, but the 4 world
        points are derived from a known-dimensions reference type
        (credit card, dollar bill, marker) rather than typed by the user.
        """
        from agent_spatial_toolkit.server.reference_objects import (
            ReferenceObjectError,
            get_corner_positions_mm,
        )

        event_log: EventLog = app.config["EVENT_LOG"]
        mem: dict[str, Any] = app.config["STATE"]

        body = request.get_json(silent=True) or {}
        try:
            photo_id = body["photo_id"]
            reference_type = body["reference_type"]
            pixel_corners_in = body["pixel_corners"]
            image_size_in = body["image_size"]
        except (KeyError, TypeError):
            return (
                jsonify({
                    "error": "missing required field "
                    "(photo_id, reference_type, pixel_corners, image_size)"
                }),
                400,
            )

        if not isinstance(pixel_corners_in, list) or len(pixel_corners_in) != 4:
            return (
                jsonify({"error": "pixel_corners must be an array of exactly 4 [x, y] entries"}),
                400,
            )

        if not isinstance(image_size_in, list) or len(image_size_in) != 2:
            return jsonify({"error": "image_size must be [width, height]"}), 400

        try:
            world_corners = get_corner_positions_mm(reference_type)
        except ReferenceObjectError as e:
            return jsonify({"error": str(e)}), 400

        # Reuse intrinsics-resolution from /api/anchors (transitional;
        # PR-3 adds full EXIF-auto + FOV-class fallback).
        intrinsics_dict = body.get("intrinsics")
        if intrinsics_dict is None:
            lens_id = body.get("lens_id")
            if not lens_id:
                return (
                    jsonify({"error": "must provide either intrinsics or lens_id"}),
                    400,
                )
            from agent_spatial_toolkit.server.lens_catalog import resolve as _resolve_lens

            intr_obj = _resolve_lens(
                lens_id,
                (
                    _coerce_finite_int(image_size_in[0], "image_size[0]"),
                    _coerce_finite_int(image_size_in[1], "image_size[1]"),
                ),
                exif=body.get("exif"),
            )
            if intr_obj is None:
                return (
                    jsonify({
                        "error": f"lens_id '{lens_id}' could not resolve to intrinsics; "
                        "provide an explicit intrinsics dict"
                    }),
                    400,
                )
            intrinsics = intr_obj
            intrinsics_dict = intr_obj.to_dict()
        else:
            try:
                intrinsics = _intrinsics_from_dict(intrinsics_dict)
            except (KeyError, TypeError, ValueError) as e:
                return jsonify({"error": f"invalid intrinsics: {e}"}), 400

        try:
            image_size = (
                _coerce_finite_int(image_size_in[0], "image_size[0]"),
                _coerce_finite_int(image_size_in[1], "image_size[1]"),
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        try:
            world_points = np.array(world_corners, dtype=np.float64)
            pixel_points = np.array(pixel_corners_in, dtype=np.float64)
            if pixel_points.shape != (4, 2):
                raise ValueError("each pixel_corner must be [x, y]")
        except (ValueError, TypeError) as e:
            return jsonify({"error": f"invalid pixel_corners: {e}"}), 400

        try:
            pose = solve_pnp(world_points, pixel_points, intrinsics, image_size)
        except PoseSolveError as e:
            event_log.write({
                "type": "pose_failed",
                "photo_id": photo_id,
                "reference_type": reference_type,
                "error_detail": str(e),
            })
            return (
                jsonify({"error": "pose solve failed: corners may be too oblique or mis-clicked"}),
                400,
            )
        except Exception:
            return jsonify({"error": "internal error during pose solve"}), 500

        # Update in-memory state.
        mem.setdefault("photos", {})
        photo_record = mem["photos"].setdefault(photo_id, {
            "id": photo_id,
            "intrinsics": None,
            "pose": None,
            "anchors": [],
        })
        photo_record["intrinsics"] = intrinsics_dict
        photo_record["pose"] = pose
        photo_record["image_size"] = image_size
        photo_record["reference_type"] = reference_type
        photo_record["pixel_corners"] = pixel_corners_in

        event_log.write({
            "type": "reference_solved",
            "photo_id": photo_id,
            "reference_type": reference_type,
            "anchor_reprojection_rms_px": pose.anchor_reprojection_rms_px,
            "intrinsics_suspect": pose.intrinsics_suspect,
        })

        return jsonify({
            "pose": pose.to_dict(),
            "intrinsics_suspect": pose.intrinsics_suspect,
        })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -k "post_reference" -v`

Expected: All 5 tests PASS.

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 207 tests pass (202 + 5 new); ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/agent_spatial_toolkit/server/app.py tests/test_app.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(server): POST /api/reference (replaces /api/anchors for redesigned wizard)

The wizard's 'confirm scale' endpoint (design §2 step 4): user taps
4 corners of their reference object (credit card / dollar bill /
marker), client POSTs photo_id + reference_type + pixel_corners +
image_size + intrinsics, server derives world points from
reference_objects.get_corner_positions_mm and runs cv2.solvePnP.

Same machinery as /api/anchors, but the 4 world points come from
the reference-type lookup (Task 2) instead of being typed by the
user. Old /api/anchors remains fully functional during the
transition; PR-4 deletes it.

Intrinsics resolution preserves the legacy intrinsics-dict-OR-lens_id
paths during transition. PR-3 adds the full EXIF-auto + FOV-class
fallback once pipeline/intrinsics.py is wired in.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §2 step 4,
§3 endpoint changes table.
Part of: Wizard UX redesign PR-2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Extend `POST /api/feature` for multi-view contract

**Files:**
- Modify: `src/agent_spatial_toolkit/server/app.py` (extend existing route)
- Modify: `tests/test_app.py` (add tests)

The current `/api/feature` accepts a single `(pixel, photo_id)` and runs single-view ray-cast. The redesigned wizard sends a *list* of `(pixel, photo_id)` pairs (design §3 endpoint table). For PR-2, we extend the input shape to accept the list:

- **1 entry** → existing single-view ray-cast behavior unchanged.
- **≥2 entries** → return HTTP 501 with body `{"error": "triangulation not yet implemented; arrives in PR-3", "n_clicks_received": <N>}`.

This locks the contract surface so PR-3 can replace the 501 stub with `triangulate_feature` wiring without touching the request schema.

- [ ] **Step 1: Read existing `/api/feature` to understand current behavior**

Read `src/agent_spatial_toolkit/server/app.py:447-522` (the `post_feature` route) to understand:

- What input shape does it currently expect? (Likely a single `pixel` + `photo_id` pair plus other context.)
- What helpers does it call? (Likely `pipeline/ray.py`'s ray-cast.)
- What does it return on success?
- What error shapes does it return on failure?

Don't modify yet — read for context.

- [ ] **Step 2: Write failing tests**

Append to `tests/test_app.py`:

```python
def test_post_feature_single_click_uses_existing_ray_cast(client, session) -> None:
    """Single-click input preserves the existing single-view ray-cast path."""
    # ... fixture setup: photo uploaded, /api/reference solved pose, then call /api/feature
    response = client.post("/api/feature", json={
        "feature_id": "usb_c",
        "clicks": [{"photo_id": "<id>", "pixel": [200, 250]}],
    })
    assert response.status_code == 200
    body = response.get_json()
    # Assertions on the single-view planar response shape; mirror existing
    # /api/feature tests for the exact field expectations.


def test_post_feature_two_clicks_returns_501(client, session) -> None:
    """Multi-view triangulation contract is accepted but not yet implemented;
    server replies 501 with a clear forward-pointer to PR-3."""
    response = client.post("/api/feature", json={
        "feature_id": "usb_c",
        "clicks": [
            {"photo_id": "<id1>", "pixel": [200, 250]},
            {"photo_id": "<id2>", "pixel": [220, 240]},
        ],
    })
    assert response.status_code == 501
    body = response.get_json()
    assert "triangulation" in body["error"].lower()
    assert "PR-3" in body["error"]
    assert body["n_clicks_received"] == 2


def test_post_feature_zero_clicks_returns_400(client, session) -> None:
    response = client.post("/api/feature", json={
        "feature_id": "usb_c",
        "clicks": [],
    })
    assert response.status_code == 400


def test_post_feature_legacy_single_pixel_shape_still_works(client, session) -> None:
    """Backwards-compat: the OLD /api/feature shape (single pixel + photo_id at
    top level, no clicks list) must still work during the transition until
    PR-4's UI rewrite replaces all callers."""
    response = client.post("/api/feature", json={
        "feature_id": "usb_c",
        "photo_id": "<id>",
        "pixel": [200, 250],
    })
    assert response.status_code == 200
```

(Replace `<id>` and `<id1>`/`<id2>` with photo_ids returned from the test-fixture upload step. Mirror the existing `test_post_feature_*` setup pattern.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -k "post_feature and (single_click or two_clicks or zero_clicks or legacy)" -v`

Expected: New tests FAIL with various errors (the `clicks` field isn't accepted; the 501 isn't returned).

- [ ] **Step 4: Extend the route**

Modify `post_feature` in `app.py` to accept the new `clicks` list shape while preserving the legacy single-pixel shape. Sketch:

```python
    @app.post("/api/feature")
    def post_feature() -> Any:
        # ... existing setup (event_log, mem, body parse) ...

        body = request.get_json(silent=True) or {}

        # Accept either the new `clicks` list shape or the legacy
        # `photo_id` + `pixel` top-level shape during transition.
        if "clicks" in body:
            clicks = body["clicks"]
            if not isinstance(clicks, list) or len(clicks) == 0:
                return jsonify({"error": "clicks must be a non-empty array"}), 400
            if len(clicks) >= 2:
                return (
                    jsonify({
                        "error": "triangulation not yet implemented; arrives in PR-3",
                        "n_clicks_received": len(clicks),
                    }),
                    501,
                )
            # Single click — unwrap and fall through to existing single-view path.
            click = clicks[0]
            try:
                photo_id = click["photo_id"]
                pixel = click["pixel"]
            except (KeyError, TypeError):
                return jsonify({"error": "click must have photo_id and pixel"}), 400
        else:
            # Legacy shape — kept fully functional for PR-3 transition.
            try:
                photo_id = body["photo_id"]
                pixel = body["pixel"]
            except (KeyError, TypeError):
                return jsonify({"error": "missing required field (clicks, or legacy photo_id+pixel)"}), 400

        # ... existing single-view ray-cast logic, using photo_id + pixel ...
```

The "...existing single-view ray-cast logic..." should be preserved unchanged — your job is only to wrap the input parsing.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -k "post_feature" -v`

Expected: ALL `/api/feature` tests pass — both the 4 new ones AND every existing one (the legacy path is preserved).

- [ ] **Step 6: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 211 tests pass (207 + 4 new); ruff clean.

- [ ] **Step 7: Commit**

```bash
git add src/agent_spatial_toolkit/server/app.py tests/test_app.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(server): extend /api/feature for multi-view contract (PR-3 wires logic)

Accepts the new \`clicks\` list shape from the redesigned wizard:
- 1 click: existing single-view ray-cast (unchanged behavior)
- >=2 clicks: HTTP 501 with forward-pointer to PR-3

Legacy single-pixel + photo_id top-level shape preserved for the
transition until PR-4 rewrites the UI to use the new shape exclusively.

PR-3 will replace the 501 stub with cv2.triangulatePoints wiring via
pipeline.triangulate.triangulate_feature; the request schema is locked
in by this commit so no further API churn.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §3 endpoint
changes (POST /api/feature row).
Part of: Wizard UX redesign PR-2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Add three new endpoint stubs (`/api/marker_detect`, `/api/next_prompt`, `/api/reproject_all`)

**Files:**
- Modify: `src/agent_spatial_toolkit/server/app.py` (add 3 new routes)
- Modify: `tests/test_app.py` (add 3 stub-shape tests)

Three new endpoints whose contract surface lands in PR-2 and whose real logic arrives in PR-3:

| Endpoint | PR-2 stub returns | PR-3 fills in |
|---|---|---|
| `GET /api/marker_detect/<photo_id>` | `{"corners": null}` (no auto-detect yet) | `cv2.aruco.detectMarkers` actual call |
| `GET /api/next_prompt` | `{"direction": "+long", "reason": "Server scoring not yet implemented (PR-3)", "coverage_cells": {"top": false, "+long": false, "-long": false, "+short": false, "-short": false}, "features": []}` | Real scoring algorithm per design §4 |
| `GET /api/reproject_all` | `{"features": []}` | Per-feature reprojection error data driving review-screen overlays |

The stub responses are **typed but pessimistic**: they return the shape the UI expects but with nullish/empty values. This keeps PR-3's later implementation a pure substitution (no API change).

- [ ] **Step 1: Write failing tests**

Append to `tests/test_app.py`:

```python
def test_marker_detect_stub_returns_null_corners(client, session) -> None:
    """PR-2 stub: no auto-detect yet; PR-3 wires cv2.aruco."""
    photo_id = _upload_test_photo(client)
    response = client.get(f"/api/marker_detect/{photo_id}")
    assert response.status_code == 200
    assert response.get_json() == {"corners": None}


def test_next_prompt_stub_returns_typed_shape(client, session) -> None:
    """PR-2 stub returns the shape the UI expects; PR-3 implements scoring."""
    response = client.get("/api/next_prompt")
    assert response.status_code == 200
    body = response.get_json()
    assert body["direction"] in {"top", "+long", "-long", "+short", "-short"}
    assert "reason" in body
    assert isinstance(body["coverage_cells"], dict)
    assert "PR-3" in body["reason"], "stub reason must self-document as not-yet-implemented"


def test_reproject_all_stub_returns_empty_features(client, session) -> None:
    response = client.get("/api/reproject_all")
    assert response.status_code == 200
    assert response.get_json() == {"features": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -k "marker_detect or next_prompt or reproject_all" -v`

Expected: All 3 tests FAIL (404 — endpoints don't exist).

- [ ] **Step 3: Implement the stubs**

Add to `app.py` after the `post_feature` route, grouped with a comment header:

```python
    # ─────────────────────────────────────────────────────────────────
    # Stub endpoints: contract surface for the redesigned wizard. Real
    # logic for these arrives in PR-3; PR-2 ships the shapes only so
    # the UI (PR-4) and PR-3's wiring can land independently.
    # ─────────────────────────────────────────────────────────────────

    @app.get("/api/marker_detect/<path:photo_id>")
    def get_marker_detect(photo_id: str) -> Any:
        """Stub: real cv2.aruco.detectMarkers wiring lands in PR-3."""
        return jsonify({"corners": None})

    @app.get("/api/next_prompt")
    def get_next_prompt() -> Any:
        """Stub: returns typed shape with placeholder values; real
        scoring algorithm (design §4) lands in PR-3."""
        return jsonify({
            "direction": "+long",
            "reason": "Server scoring not yet implemented (PR-3)",
            "coverage_cells": {
                "top": False,
                "+long": False,
                "-long": False,
                "+short": False,
                "-short": False,
            },
            "features": [],
        })

    @app.get("/api/reproject_all")
    def get_reproject_all() -> Any:
        """Stub: PR-3 wires per-feature reprojection error in mm."""
        return jsonify({"features": []})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -k "marker_detect or next_prompt or reproject_all" -v`

Expected: All 3 tests PASS.

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 214 tests pass (211 + 3 new); ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/agent_spatial_toolkit/server/app.py tests/test_app.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(server): contract stubs for marker_detect, next_prompt, reproject_all

Three new endpoints whose contract surface lands in PR-2 and whose
real logic arrives in PR-3:

- GET /api/marker_detect/<photo_id>: returns {corners: null}; PR-3
  wires cv2.aruco.detectMarkers
- GET /api/next_prompt: returns the typed shape (direction, reason,
  coverage_cells, features) with placeholder values; PR-3 wires the
  feature-driven scoring algorithm from design §4
- GET /api/reproject_all: returns {features: []}; PR-3 wires
  per-feature reprojection error in mm for the review screen

Stubs are typed but pessimistic — they return the shape the UI
expects with nullish/empty values, so PR-3's later implementation
is a pure substitution with no API change.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §3 endpoint
changes (last three rows of the table).
Part of: Wizard UX redesign PR-2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Regression test — old endpoints still functional

**Files:**
- Modify: `tests/test_app.py`

The design doc's §6 disposition table says `/api/anchors`, `/api/lens_catalog`, `/api/wireframe` are **deleted in PR-4**. They must remain fully functional through PR-2 and PR-3 so the legacy UI keeps working during the transition. This task adds explicit regression tests that lock that in — if a future PR accidentally removes one before PR-4, this suite catches it.

- [ ] **Step 1: Write the regression tests**

Append to `tests/test_app.py`:

```python
def test_legacy_anchors_endpoint_still_functional(client, session) -> None:
    """Until PR-4 deletes it, /api/anchors must keep working for the
    legacy UI (and any external callers that haven't migrated to
    /api/reference yet)."""
    photo_id = _upload_test_photo(client)
    response = client.post("/api/anchors", json={
        "photo_id": photo_id,
        "anchors": [
            {"id": "a", "pcb_xyz_mm": [0, 0, 0], "pixel": [100, 100]},
            {"id": "b", "pcb_xyz_mm": [86.5, 0, 0], "pixel": [400, 100]},
            {"id": "c", "pcb_xyz_mm": [86.5, 53.98, 0], "pixel": [400, 300]},
            {"id": "d", "pcb_xyz_mm": [0, 53.98, 0], "pixel": [100, 300]},
        ],
        "image_size": [800, 600],
        "intrinsics": {"fx_px": 800.0, "fy_px": 800.0, "cx_px": 400.0, "cy_px": 300.0},
    })
    assert response.status_code == 200
    assert "pose" in response.get_json()


def test_legacy_lens_catalog_endpoint_still_functional(client, session) -> None:
    """Until PR-4 deletes it, /api/lens_catalog must keep returning
    the lens-catalog payload for any legacy UI callers."""
    response = client.get("/api/lens_catalog")
    assert response.status_code == 200
    body = response.get_json()
    assert isinstance(body, (dict, list)), "lens_catalog returns an enumerable"


def test_legacy_wireframe_endpoint_still_functional(client, session) -> None:
    """Until PR-4 deletes it, /api/wireframe/<photo_id> must keep
    returning a wireframe PNG for solved photos."""
    # Set up a photo with a solved pose (via /api/reference or /api/anchors)
    # then GET the wireframe.
    # ... test fixture setup ...
    response = client.get(f"/api/wireframe/{photo_id}")
    assert response.status_code in {200, 404}, (
        "wireframe must be functional (200) or report no-pose-solved (404), "
        "not 500 or 410"
    )
```

- [ ] **Step 2: Run tests to verify they pass on first run**

Run: `uv run pytest tests/test_app.py -k "legacy" -v`

Expected: All 3 tests PASS on first run (no implementation needed; the endpoints already exist on main).

If any fail: STOP — something in PR-2 broke a legacy endpoint inadvertently. Investigate before continuing.

- [ ] **Step 3: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 217 tests pass (214 + 3 new); ruff clean.

- [ ] **Step 4: Commit**

```bash
git add tests/test_app.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "test(server): regression tests for legacy endpoints during PR-2/PR-3 transition

/api/anchors, /api/lens_catalog, and /api/wireframe are deleted in PR-4
(per design doc §6 disposition table). Until then, they must remain
fully functional so the legacy UI keeps working. These regression tests
lock that in — if a future PR accidentally breaks a legacy endpoint
before PR-4 is ready to delete it, the suite catches it.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §6 disposition.
Part of: Wizard UX redesign PR-2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Open the PR

**Files:** none (git/gh operations only).

- [ ] **Step 1: Verify the full test suite is green**

Run: `uv run pytest -q`

Expected: `217 passed` (191 baseline from PR-1 + 26 new from PR-2: 5 reference_objects, 6 photo upload, 5 reference, 4 feature multi-view, 3 stub endpoints, 3 legacy regression).

- [ ] **Step 2: Verify ruff passes**

Run: `uv run ruff format --check src tests && uv run ruff check src tests`

Expected: both exit 0 with no output.

- [ ] **Step 3: Push the branch**

```bash
git push -u origin feat/wizard-redesign-pr2-server-endpoints
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat(server): wizard redesign PR-2 — endpoint contracts + HEIC decode" --body "$(cat <<'EOF'
## Summary
- Adds \`pillow-heif\` dependency for transparent HEIC→JPEG decode.
- Adds in-wizard photo upload at \`POST /api/photo\` (sha256-derived photo_id, idempotent).
- Adds \`server/reference_objects.py\` lookup (credit card / dollar bill / marker dimensions).
- Adds \`POST /api/reference\` (replaces \`/api/anchors\` for the redesigned wizard's confirm-scale step).
- Extends \`POST /api/feature\` to accept multi-view click lists (1 click = existing ray-cast; ≥2 = HTTP 501 with PR-3 forward-pointer).
- Adds three new endpoint contract stubs: \`/api/marker_detect\`, \`/api/next_prompt\`, \`/api/reproject_all\` (real logic in PR-3).

All legacy endpoints (\`/api/anchors\`, \`/api/lens_catalog\`, \`/api/wireframe\`) remain fully functional during the transition; PR-4 deletes them.

## Test plan
- [x] 217 tests pass (191 baseline + 26 new from this PR)
- [x] \`ruff format --check\` and \`ruff check\` clean
- [x] HEIC round-trip test passes (decode → JPEG persistence)
- [ ] CI green on Python 3.10/3.11/3.12/3.13
- [ ] Cross-model review per design doc §7 (UX-and-functionality + Codex)

## Refs
- Design: docs/specs/2026-05-05-wizard-ux-redesign-design.md (PR-2 row in §6, endpoint table in §3, HEIC handling in §3)
- Plan: docs/plans/2026-05-05-wizard-redesign-pr2-server-endpoints.md
- Builds on: PR #50 (PR-1 schema extensions, merged at e192494)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Verify CI starts**

Run: `gh pr checks` and confirm the test matrix is queued.

The PR is now ready for cross-model review (spec-compliance + UX-and-functionality + Codex). Orchestrator dispatches reviewers; this plan does not specify them.

---

## Self-review checklist (orchestrator runs this before claiming the plan complete)

**Spec coverage** (against design doc §3 endpoint changes table + §6 PR-2 row):

- ✅ `pillow-heif` dependency — Task 1
- ✅ `POST /api/photo` upload + HEIC decode — Task 3
- ✅ `reference_objects` lookup module — Task 2
- ✅ `POST /api/reference` (replaces `/api/anchors`) — Task 4
- ✅ `POST /api/feature` extended for multi-view — Task 5
- ✅ `GET /api/marker_detect/<photo_id>` stub — Task 6
- ✅ `GET /api/next_prompt` stub — Task 6
- ✅ `GET /api/reproject_all` stub — Task 6
- ✅ Old endpoints remain functional regression — Task 7
- ✅ PR opened — Task 8

**Placeholder scan:** No "TBD", "TODO", "implement later", "fill in details", "similar to Task N" patterns in the task definitions. Every step has either complete code or a specific command + expected output.

**Type consistency:** `reference_type` enum strings (`credit_card`, `dollar_bill`, `marker`) match between `reference_objects.py` (Task 2), `/api/reference` route (Task 4), and the tests (Tasks 2 + 4). Coverage cell strings (`top`, `+long`, `-long`, `+short`, `-short`) match between `/api/next_prompt` stub (Task 6) and the design doc §4. `clicks` list shape (Task 5) and `triangulate_feature` will use the same `(pixel, photo_id)` pair format in PR-3.

**Scope check:** Single coherent PR. Eight tasks, each independently committable, ~15-45 minutes per task. Total estimate: 3-5 hours of implementer + reviewer cycles.

**Cross-PR sanity:** PR-2's new shapes don't conflict with PR-1's schema (verified — `Feature.noisy` and `Feature.warning` aren't touched here, so PR-1's regression suite continues to apply). PR-3 will substitute the stubs in Tasks 5/6 with real logic without changing the request/response schemas. PR-4 will delete the legacy endpoints; Task 7's regression tests need to be removed (or repurposed as 410-Gone tests) at that point.

---

## Notes for the implementing subagent

- **Branch convention:** create a fresh branch off `main` named `feat/wizard-redesign-pr2-server-endpoints` before starting Task 1.
- **Git identity:** per-command env vars only.
- **Test fixtures:** Tasks 3, 4, 5, 6, 7 all need a way to construct a Flask test client with a valid `Session`. Look at the existing `tests/test_app.py` for the pattern (likely a `client` and `session` pytest fixture, or a `make_test_app` helper). Mirror it; do not invent a new fixture pattern. **If you can't find the existing fixture pattern, STOP and ask the orchestrator.**
- **`_upload_test_photo` helper:** referenced from Tasks 4, 5, 6, 7. Add it to `tests/test_app.py` near the existing helpers (or wherever the test conventions place such helpers). Implementation: POST a `_make_jpeg_bytes()` payload to `/api/photo` and return `response.get_json()["photo_id"]`. Define it once and reuse.
- **PEP 257 / `from __future__` / `encoding="utf-8"`** standards apply to any new files.
- **Atomic-write pattern** for the JPEG persistence in Task 3 (write `<id>.jpg.tmp`, then `replace`).
- **Task 5 implementation note:** preserve the existing `/api/feature` single-view ray-cast logic byte-for-byte. Your job is to wrap input-parsing only. Do NOT refactor or "improve" the existing handler beyond the input-shape extension.
- **Task 6 stub note:** the stubs return placeholder values. Don't try to be clever — the goal is contract-not-logic. PR-3 fills them in.
- **If a task's description references existing-codebase machinery you can't locate** (e.g., a helper named `_intrinsics_from_dict` that's used in `/api/anchors` and is referenced in Task 4), it exists. Read the existing route to find it. Don't redefine.
- **Test count math:** 191 (PR-1 final) → 217 (this PR's end). If your numbers diverge, recount before pushing.
