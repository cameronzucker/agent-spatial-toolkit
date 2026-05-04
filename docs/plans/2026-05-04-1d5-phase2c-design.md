# Task 1.D.5 — Phase 2c implementation design

**Parent plan:** [2026-05-03-v1-implementation.md](2026-05-03-v1-implementation.md) §Task 1.D.1–1.D.9
**Spec section:** [2026-05-03-design.md §3 Phase 2c](../specs/2026-05-03-design.md), §5.1 step 4 (PnP + RMS gate), §5.3 (anchor minimum)
**Status:** approved 2026-05-04 — proceeding to writing-plans skill

This sub-design records the architectural choices made for Task 1.D.5 (Phase 2c — per-photo anchor calibration), since the master plan delegates "the dispatching subagent receives this plan and the spec §3 Phase 2a–2f text. They have authority to flesh out the test/code per the same red-green-commit pattern."

## Scope

Phase 2c per spec §3:

> For each photo, the wizard prompts the user to click each declared anchor's pixel location:
> - The list from Phase 2b shows up as a checklist beside the photo
> - Minimum 3 non-collinear anchors per photo for `cv2.solvePnP` to converge
> - After clicks, the server runs `cv2.solvePnP` to compute camera pose
> - Wireframe overlay drawn onto the photo using the computed pose lets the user verify orientation
> - If anchor reprojection RMS exceeds threshold on the first attempt, photo is flagged `intrinsics_suspect`. The user gets one retry. If RMS is still high after the retry, the photo is marked `uncalibrated` and the user is prompted to either re-take the photo, switch to chessboard calibration, or proceed without that photo.

## Three architectural decisions

### Decision 1: Lens catalog lives server-side

A new `GET /api/lens_catalog` endpoint returns `{lens_id: {label, intrinsics_dict | null}}`. The client populates its lens dropdown from this endpoint and sends `lens_id` (not the full intrinsics dict) to `/api/anchors`. The server resolves `lens_id` → intrinsics via the catalog before solving PnP.

**Why server-side:** the science layer (`pipeline/intrinsics.py`) already owns intrinsics math; one source of truth. The UI's existing `LENS_OPTIONS` constant becomes display-only labels populated from the server.

**Backward compatibility:** `POST /api/anchors` still accepts `{intrinsics: <dict>}` directly when no `lens_id` is provided, so existing tests in `tests/test_app.py` keep passing without modification.

**Catalog v0 contents:**
- `pi_camera_module_3_wide` — Pi Camera Module 3 wide-angle, intrinsics from `intrinsics.from_fov_class("pi_camera_module_3_wide")` (existing FOV-class fallback)
- `pi_camera_module_3_standard` — same source
- `iphone_15_pro_24mm` / `_13mm` / `_77mm` — entries present with `intrinsics: null` (forces user into the EXIF-detected path until chessboard calibration ships in Phase 2 Stream G)
- `exif:detected` — sentinel; client sends EXIF dict alongside, server runs `intrinsics.from_exif()`
- `other` — sentinel; client must send full `intrinsics` dict (manual mode)

### Decision 2: Wireframe overlay rendered server-side

A new `render_wireframe(pose, intrinsics, image_size, frame_anchors) -> Path` function in `pipeline/reproject.py` projects the part-frame axes (X red, Y green, Z blue) and the anchors-as-wireframe-edges onto the photo and saves a PNG via the existing atomic-write pattern from `render_overlay`.

A new `GET /api/wireframe/<photo_id>` endpoint serves the generated PNG (with the same `secure_filename` path-traversal guard the existing `/static/overlays/<id>` endpoint uses).

**Why server-side:** matches the Phase 2e pattern (`render_overlay` → `/static/overlays/`); reuses tested `cv2.projectPoints`; avoids duplicating projection math in JS.

**Wireframe shape:** axes at the origin (X, Y, Z arrows of length = max(longMm, shortMm) × 0.3) plus lines connecting consecutive anchors in `frame_anchors` order (forms the bounding rectangle for the standard PCB preset; arbitrary polyline for custom mode).

### Decision 3: Two PRs, not three or one

**PR-α** (this branch, `feat/ui-phase-2c-anchor-clicking`): lens catalog endpoint + Phase 2c functional click flow. Leaves the user able to complete Phase 2c sequence (clicks → POST → see pose summary + intrinsics_suspect flag), without visual confirmation.

**PR-β** (follow-on branch, e.g. `feat/ui-phase-2c-wireframe-retry`): wireframe rendering + retry-on-RMS UX layer.

**Why two:** PR-α is a coherent functional milestone; PR-β is the polish layer. Three PRs would over-fragment (lens catalog alone is ~30 LOC). One PR risks compaction mid-flight given session length.

## Per-PR contracts

### PR-α: lens catalog + functional anchor flow

**Server changes:**
- New module `src/agent_spatial_toolkit/server/lens_catalog.py` — returns the canonical `LENS_CATALOG: dict[str, LensEntry]` constant (resolves intrinsics lazily via `intrinsics.from_fov_class` so the import doesn't fail when called outside a configured environment).
- `app.py`: new `GET /api/lens_catalog` endpoint returning `{lenses: [{id, label, has_intrinsics: bool}]}` (intrinsics not exposed to client — client only sees the ID and whether the catalog can resolve it).
- `app.py`: extend `POST /api/anchors` body schema to accept `lens_id` (string) as an alternative to `intrinsics` (dict). If both provided, `intrinsics` wins (explicit override). If `lens_id="exif:detected"`, body must also include `exif: <dict>`; server runs `from_exif`. If `lens_id="other"`, body must include full `intrinsics` (so it falls through to existing path).

**Client changes (`src/agent_spatial_toolkit/ui/app.js`):**
- Replace static `LENS_OPTIONS` with a fetch from `/api/lens_catalog` at init (cache the response on `window.spatialState.lensCatalog`).
- New Phase 2c `wirePhase2c()` function:
  - Render one card per photo in `<section id="phase-2c">`: photo name + canvas (via `loadImageToCanvas`) + anchor checklist (left side) + per-photo "Solve pose" button + result area
  - Each anchor in the checklist is a clickable `<li>`; clicking it sets `currentAnchorId` for that photo and arms `captureClick(canvas, ...)` to record the next click as that anchor's pixel
  - When ≥ 3 anchors clicked, "Solve pose" enables; click POSTs `{photo_id, lens_id, anchors, image_size}` to `/api/anchors`
  - Display response (compact: `pose computed (RMS = X.X normalized px) — intrinsics_suspect: true/false`) in result area
  - "Next: feature labeling →" advances to Phase 2d once at least one photo has a pose

**`index.html`:** replace placeholder `<p>` in `<section id="phase-2c">` with the static container scaffold (`<div id="phase-2c-photos"></div>` + `<button id="phase-2c-next">`).

**Tests:**
- `tests/test_app.py`: new test class `TestLensCatalog` — `GET /api/lens_catalog` returns expected lens IDs + labels
- `tests/test_app.py`: extend `TestAnchors` — `POST /api/anchors` with `lens_id="pi_camera_module_3_wide"` resolves intrinsics and solves PnP; `lens_id="other"` without intrinsics returns 400
- `tests/test_lens_catalog.py` (new) — unit tests for the catalog module's `resolve(lens_id, exif=None) -> Intrinsics | None`
- Playwright: `tests/ui/test_phase_2c.py` — happy-path click flow on the synthetic-card fixture (load → click 4 anchors → POST → see "pose computed" in result area)

**Out of scope for PR-α** (deferred to PR-β):
- Wireframe overlay
- Retry UX
- "uncalibrated" handling
- Skip-photo / continue-anyway buttons
- Switch-to-chessboard option (Phase 2 Stream G work)

### PR-β: wireframe overlay + retry-on-RMS UX

**Server changes:**
- `pipeline/reproject.py`: new `render_wireframe(...) -> Path`
- `app.py`: new `GET /api/wireframe/<photo_id>` endpoint (lazy: renders on first request, caches in session dir)

**Client changes (`app.js`):**
- After successful POST in Phase 2c, fetch the wireframe PNG and overlay it on the photo card (CSS absolute-positioned `<img>` with reduced opacity)
- If response `intrinsics_suspect=true` and attempts=1: show "Re-click anchors" button that clears the photo's pose and re-arms capture
- If response `intrinsics_suspect=true` and attempts=2: mark photo "uncalibrated" in spatialState; show "Skip this photo" + "Continue anyway" buttons; "Switch to chessboard" placeholder button (disabled, tooltip "available in v0.1.0")

**Tests:**
- `tests/test_reproject.py`: extend with `TestRenderWireframe` — render on a canonical Pose+Intrinsics, assert axis endpoints land at expected pixels (within 1 px tolerance)
- `tests/test_app.py`: `GET /api/wireframe/<id>` returns PNG content-type after a pose is solved; returns 404 when no pose yet; returns 404 with path-traversal attempts
- Playwright: retry-flow test (force a high-RMS solve, click "Re-click", verify state transition)

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| `from_fov_class` doesn't yet return Pi Camera Module 3 wide intrinsics | Verify in PR-α step 1; add the entries to FOV table if missing |
| Existing `tests/test_app.py` tests use `intrinsics` field directly — could collide with new `lens_id` path | Backward-compat (intrinsics still accepted) is explicit; tests should pass unchanged |
| Anchor checklist ordering vs Phase 2b's anchor list | Use `spatialState.frame.anchors` order verbatim (preserves user's mental model from 2b) |
| `cv2.solvePnP` divergence on coplanar anchors (4 PCB corners are coplanar) | Existing `pose.py` already handles this — see issue #12 (open, design decision pending). For v0.1.0-alpha PR-α just surfaces the failure via the existing 400 response |
| Mid-PR rebase needed if PR #39's rebase pattern repeats | Use the env-var rebase pattern from PARALLEL-NOTES.md §3 |

## Out-of-scope explicitly

- Real intrinsics for iPhone lenses (chessboard work, Phase 2 Stream G)
- Switch-to-chessboard mid-wizard button (Phase 2 work)
- Mobile-touch-friendly click handling beyond what `captureClick` already does (Phase 1.D.9 polish task)
- Multi-photo cycling animation / advanced UX (1.D.9 polish)
