# Task 1.D.6 — Phase 2d implementation design

**Parent plan:** [2026-05-03-v1-implementation.md](2026-05-03-v1-implementation.md) §Task 1.D.6
**Spec section:** [2026-05-03-design.md §3 Phase 2d](../specs/2026-05-03-design.md), §5.1 step 5 (ray-cast / triangulate)
**Status:** approved 2026-05-04 — proceeding to writing-plans skill

## Three-PR breakdown (user approved 2026-05-04)

| PR | Scope | Status |
|---|---|---|
| **PR-α** | Single-view click + label + POST `/api/feature` + per-photo features panel. No multi-view. Free-text label entry with session-local autocomplete. | this branch |
| **PR-β** | "Visible in" checkbox row per feature; D1 side-by-side view when adding the 2nd click; server triangulates eagerly when ≥2 clicks land. Layout: horizontal 50/50. | next |
| **PR-γ** | D3 dropdown — clicking in any photo offers "link to existing feature: <name>" alongside "create new". Filters by which features declared "visible in" current photo. "Done with feature labeling" advances to 2e. | last |

## Defaults user signed off

1. **Label entry**: free-text input, autocomplete pool = labels already used in this session. No fixed taxonomy. Caller (downstream agent) decides its own taxonomy from the schema.
2. **Re-trigger triangulation**: eager — every new click for an existing feature re-triangulates. Matches spec §3 "re-triangulates with the larger set".
3. **D3 dropdown timing**: only appears in PR-γ. PR-α has no dropdown (only one click per feature anyway); PR-β adds via the "visible in" checkbox row.

## What's already shipped that constrains PR-α

- `POST /api/feature` server endpoint (server/app.py:447) — accepts `{feature_id, photo_id, pixel, z_assumed_mm?}`. Returns ray-cast xyz_mm + method on success. 404 if no pose for the photo. v0.1.0-alpha is single-view only (β-mode: planar ray-cast); triangulation deferred to Phase 2 Stream F.
- `mem["features"]` server-side store — keyed by `feature_id`.
- Phase 2c (1.D.5) shipped: solved poses + wireframe overlay + retry UX. The "Next: feature labeling →" button at the end of Phase 2c is already wired to advance to Phase 2d (currently a placeholder).
- `helpers.js` exports `captureClick(canvas, callback)` and `loadImageToCanvas(url, canvas)` — same primitives Phase 2c uses.

## PR-α architecture

**Server changes:** none. The existing `POST /api/feature` is sufficient for PR-α.

**Client changes:**

`src/agent_spatial_toolkit/ui/index.html` — replace the Phase 2d placeholder with a static container scaffold:
- Description paragraph
- `<div id="phase-2d-photos" class="phase-2d-photo-grid">` — populated at runtime by `wirePhase2d`, one card per photo (only photos with solved poses)
- `<button id="phase-2d-next">` — disabled until at least one feature has been added
- `<p id="phase-2d-error">`

`src/agent_spatial_toolkit/ui/app.js` — add `wirePhase2d` + helpers:
- MutationObserver pattern (one-shot) on `#phase-2d.hidden` mirrors Phase 2c
- `renderPhase2dPhotos()` — for each photo where `spatialState.photos[id].pose` exists, append a card
- Each card carries: photo canvas (loadImageToCanvas) + features panel (`<ul>`) + "Click on the photo to add a feature" instruction
- captureClick(canvas, ...) → on click, prompt for label (HTML `<input>` modal-ish — see "Label input" below) → POST → append feature to panel
- Feature panel row: label + xyz_mm + "remove" button (DELETE not in this PR; just removes from local state)

**Label input UX**: a small inline form below the canvas: text input + "Add" button. As the user types, an autocomplete `<datalist>` element offers session-local labels (those already used). On submit, the click is sent with `feature_id = normalize(label)` where normalize = lowercase + replace non-alphanumeric with underscore.

**Local state additions on `window.spatialState`:**
- `spatialState.features: { [feature_id]: { label, photoId, pixel, xyz_mm } }` — populated as the user clicks-and-labels.

## Tests

- `tests/test_app.py` — existing `/api/feature` tests should be unchanged.
- `tests/test_ui_phase_2d.py` (new) — Playwright happy path: open wizard → seed Phase 2c-completed state via `page.evaluate` → reveal Phase 2d → click on canvas → enter label → see feature appear in panel → POST hits server.

## Risks / open issues for PR-β/γ

- **Feature ID stability across multi-view**: PR-α uses `feature_id = normalize(label)`. If the user labels two different features with the same string (e.g. "screw"), they collide. PR-α: accept the collision (server overwrites). PR-β: introduce explicit "create new" vs "link to existing" gate before sending POST.
- **Z-plane assumption**: ray-cast uses `z_assumed_mm = 0` by default. For multi-component PCBs (e.g. clicking the top of a header), the actual feature is at z > 0. PR-α exposes a "z (mm above PCB) override" input on the per-feature row. PR-β's triangulation ignores `z_assumed_mm` (real Z comes from triangulation).
- **MutationObserver back-to-2c re-render**: same caveat as Phase 2c. Documented; polish if it bites.

## Out of scope explicitly

- Server triangulation endpoint changes (Phase 2 Stream F)
- Feature deletion (DELETE /api/feature) — defer to PR-β if needed
- Annotation export / finalize (Phase 2f, Task 1.D.8)
- Visual polish (Task 1.D.9)
