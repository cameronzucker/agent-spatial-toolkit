# Wizard UX redesign — Flow B prescriptive coaching

**Status:** Design — pending user approval. Cross-model review applied 2026-05-05 (4 BLOCKER + 9 MAJOR + 9 MINOR + 3 NIT findings, all addressed in this revision).
**Date:** 2026-05-05
**Supersedes:** Phase-based wizard implementation in [2026-05-03-design.md](2026-05-03-design.md) §3 (Phases 2a–2f).
**Author:** session 7 (Claude Opus 4.7), brainstormed with user via `superpowers:brainstorming` after gate-test failure on PR #48.
**Tracking:** see `project_wizard_ux_gate_failure_2026_05_05.md` in auto-memory.

---

## 1. Goals & non-goals

### Goal

A wizard that walks a non-CAD user through capturing a 3D part with their phone, in enough geometric detail that an LLM downstream can use the resulting `annotations.json` to act on the part (generate CAD, plan assembly, identify components, etc.). The user **never** sees CV jargon, **never** types coordinates, and **never** has to know what "pose" or "intrinsics" mean.

### Primary user

Someone with a phone, a part, and *no* prior CAD knowledge. They have a wallet (credit card, dollar bill) and access to a flat surface. They may or may not have a printer.

### "Part" definition

Any rigid assembly of one or more physical components that can sit on a flat surface as a unit and that doesn't move during capture. The Pi-with-HATs-and-accessories case is **in scope** — the assembled stack is one "part." Out of scope: parts that require flipping or repositioning during the session (v2), assemblies with moving sub-parts (v2+).

### Non-goals (explicit, so we don't drift)

- **Not a sub-mm-precision metrology tool.** ±0.5–1 mm with wallet-item references is the v1 target. Printed markers offer ±0.1 mm as a power-user upgrade.
- **Not a research tool.** No Structure-from-Motion, marker-less photogrammetry, or neural depth estimation in v1. The wizard refuses to operate without *some* known-geometry reference object visible in each photo.
- **Not desktop-first in v1.** Responsive enough to render on desktop, but the prescriptive flow assumes phone capture (`<input type="file" accept="image/*" capture>`).
- **Not a CAD generator.** The wizard produces `annotations.json`; what consumes it is out of scope.
- **Not multi-part-per-session.** One assembly per wizard invocation.
- **Not full-sphere coverage.** v1 works in the "moderately oblique" angle range (0–70° from vertical). Beyond ~70° the flat scale-reference becomes too edge-on for `cv2.solvePnP` to resolve. Tall narrow parts where features are predominantly side-mounted are explicitly v2 territory.
- **Not a 2-point-ruler reference path.** A ruler with only 2 endpoints is geometrically degenerate for `cv2.solvePnP` (PnP requires ≥3 non-collinear points; 2 points have no rotation constraint). v1 ships with 3 reference options only: credit card, dollar bill, printed marker. Ruler support requires ≥4 marks (e.g., calibrated tick crosses) and is deferred to v2.

### Deferred to later versions

- **v2:** Phone+desktop sync via QR code (capture on phone, annotate on desktop).
- **v2:** Free-form workspace mode for power users who don't want prescriptive coaching.
- **v2:** Multi-pose support — flipping the part with rigid-transform recovery via shared features.
- **v2:** Vertical-marker / multi-marker / 3D-fiducial modes for full-sphere coverage of tall parts.
- **v2:** Ruler-as-reference (with ≥4 calibrated marks; geometrically non-degenerate).
- **v3+:** Marker-less reference recovery (SfM, common-object auto-detect from logos).

---

## 2. User flow

The flow is **linear up to the first labeled feature, loops thereafter, terminates when feature-coverage is satisfied.** Phone-native, prescriptive coaching ("take this photo now"), inline labeling per photo.

### Pre-loop (one-time, three screens)

1. **Welcome / project naming.** Single short text field: *"What are you capturing?"* → stored verbatim as `project_name` in `annotations.json`. Skippable but encouraged. Provides the downstream LLM semantic context (e.g., *"Raspberry Pi 5 with cooling HAT and PoE accessory"*).
2. **Reference picker.** Three options pre-loaded with known dimensions:
   - **Credit / debit card** (85.6 × 53.98 mm, ISO/IEC 7810 ID-1) — ±0.5 mm
   - **US dollar bill** (156.1 × 66.3 mm) — ±0.4 mm
   - **Printed marker** (ChArUco PDF served by wizard) — ±0.1 mm

   Choice is sticky for the session. (Ruler support is v2 — see §1 non-goals.)
3. **Bootstrap instruction + first-photo prompt.** Setup diagram (phone-above-part-with-reference). "Place your part on a flat surface. Take a photo straight down." → `<input type="file" accept="image/*" capture>` launches OS camera. Phone returns the photo to the wizard.

### Per-photo loop

4. **Photo arrived → confirm scale (guided corner walkthrough).** Printed marker = auto-detect via `cv2.aruco.detectMarkers`, no user clicks needed. Wallet item = **guided one-corner-at-a-time walkthrough**, NOT a "tap 4 corners clockwise" prose instruction:
   - The wizard overlays a glowing target highlight on a single corner of the reference object as detected from a coarse rectangle estimate (or from a hand-drawn quadrant if no auto-rectangle is found). Caption: *"Tap THIS corner."*
   - User taps the highlighted corner. Counter advances (1/4). Next corner highlights with the same target marker.
   - Repeats for corners 2, 3, 4. Walking-the-rectangle order is wizard-determined and visible — the user never has to know which corner is "top-left" or what direction "clockwise" runs from. No directional prose appears.
   - Pose solves via `cv2.solvePnP` once 4 corners land. Tier feedback (§4) renders.
5. **Label features.** "Anything to label here?" Tap photo → bottom-sheet input opens with a marker pinned at the tap location on the photo. The photo auto-pans/zooms to keep the pinned marker visible above the soft keyboard (which occupies ~50% of phone viewport when active). User types label → confirm → marker becomes a colored dot. Persistent features panel below. Hierarchical labels with `/` separator group visually (e.g., `pi/usb_c` and `pi/hdmi` cluster under a *Pi* header). Each feature shows status: *needs 2nd view* → *✓ triangulated*. Each feature row supports a **long-press menu**: *Re-click in this photo* / *Mark as approximate (single-view only)* / *Delete*.
6. **Next-photo prompt.** Wizard scores candidate directions per the simplified v1 algorithm (§4). Always-visible coverage bar. **Persistent "Finish" CTA in wizard chrome** from this step onward — user can soft-finalize at any time (§5 Bucket 3 governs override behavior). "Open camera" CTA. Loop back to step 4.

### Termination — one screen

7. **Done / review / export.** Triggered when every labeled feature is triangulated, OR the user clicks *"Finish"* with un-triangulated features (soft-block — see §5). Review shows project name, photo thumbnails, hierarchical features panel with status icons. **Raw `(x, y, z) mm` coordinates are never surfaced in the UI** — the user verifies correctness via reprojection overlay:
   - Each photo thumbnail shows colored dots overlaid where the system thinks each labeled feature is.
   - **Each dot is tappable.** Tapping opens a per-feature drill-down: that feature's photos with its dot positions, a "this dot looks wrong" affordance that surfaces the re-click flow from step 5, and the option to mark single-view-only (same long-press menu).
   - Per-feature warnings link directly to the offending photo with the bad dot highlighted.
   - Coordinates ship in the downloaded JSON for the LLM. "Download annotations.json" CTA.

### Camera launch mechanism

v1 uses `<input type="file" accept="image/*" capture>` rather than WebRTC live preview. Reasons: zero-permissions, works on every phone OS today, the OS camera is already familiar to the user. v2 may add live-preview for in-page composition guidance.

### Direction-prompt fidelity (v1)

Each next-photo prompt shows: a brief plain-English instruction ("Shoot from the right side"), a **generic abstract rotation-arrow + compass widget** (NOT a part-silhouette overlay; the wizard has no side-photo geometry to render against until at least one side has been shot), and the *reason* ("we still need a 2nd view of one of your features").

Part-silhouette overlays for direction prompts are explicitly **v2 polish** (see §1 deferred items). They depend on at least one prior side-shot being available and on per-part-shape model fitting that's outside v1 scope.

---

## 3. Architecture & math primitives

### Components

| Layer | Status | Notes |
|---|---|---|
| **Math kernel** (`pipeline/normalize`, `intrinsics`, `pose`, `ray`, `emit`, `reproject`) | ✅ already done, proven by PR #28 smoke | Zero new math. We compose existing primitives. |
| **Triangulation** (`triangulate_feature`) | Per the original spec, exists in math kernel; not yet wired to server endpoint | Wire it into the per-feature flow. |
| **Server (Flask, `server/app.py`)** | Major surgery — see endpoint table below | Delete some routes, repurpose others, add ~3 new ones. |
| **HEIC decode** | New dependency | Bundle `pillow-heif` as a v1 hard requirement (see HEIC commitment below). |
| **UI (`ui/index.html`, `app.js`, `helpers.js`)** | Full rewrite | The current 117-line phase-based shell goes away. |
| **Schema (`schema/models.py`)** | Extend additively | New fields: `project_name`, hierarchical feature names with `/`, `method` enum, `noisy` boolean, expanded `quality_summary.flags`. |
| **Session persistence** (`session.py`, `events.py`, `lifecycle.py`) | Keep | Schemas evolve; persistence model unchanged. |

### Endpoint changes

| Endpoint | Disposition | Notes |
|---|---|---|
| `GET /` | Keep | Serves the new UI. |
| `GET /api/state` | Keep | Same role: rehydrate the wizard if user reloads — drives the resume-landing screen (see §5). |
| `GET /api/lens_catalog` | **Delete** | No more user-facing lens picker. EXIF-auto + FOV-class fallback (existing `pipeline/intrinsics.py`) replaces it. |
| `POST /api/anchors` | **Repurpose → `POST /api/reference`** | Same `cv2.solvePnP` machinery. Inputs: `photo_id`, 4 pixel coords, reference type enum: `credit_card` / `dollar_bill` / `marker`. For marker: auto-detected (no user clicks). |
| `POST /api/feature` | **Extend** | Now accepts list of `(pixel, photo_id)` pairs. ≥2 entries → `triangulate_feature`. 1 entry → single-view ray-cast (planar fallback, marked `method: "single_view_planar"`). |
| `POST /api/finalize` | Keep | Emits `annotations.json` per extended schema. |
| `GET /api/wireframe/<photo_id>` | **Delete** | Replaced by per-feature reprojection overlay (review screen). |
| `GET /api/marker_detect/<photo_id>` | **New** | Calls `cv2.aruco.detectMarkers`. Returns 4 corners if found, else null → user falls back to manual click. Only relevant when reference type = `marker`. |
| `GET /api/next_prompt` | **New** | Server-side scoring algorithm (§4). Returns `{direction, reason, coverage_cells, features}`. UI renders this. |
| `GET /api/reproject_all` | **New** | For review screen. Returns `{photo_id: [{feature_name, predicted_pixel, error_mm_per_photo}]}` for every triangulated feature in every photo. The review UI surfaces the **worst** per-photo `error_mm` per feature (so the dot-overlay drill-down naturally highlights the worst offender first). Drives dot overlays on review thumbnails. |

Old endpoints (`/api/anchors`, `/api/wireframe`, `/api/lens_catalog`) **return HTTP 410 Gone** after PR-4 merges. PR-4's test suite includes regression tests confirming this — prevents the new UI from silently retaining a code path to the old contract surface.

### HEIC handling — committed

iPhone exports HEIC by default since iOS 11 (2017). Relying on the user to manually switch their phone to "Most Compatible" JPEG mode is a usability footgun; doing so would violate Success Criterion #1 ("non-CAD user can complete the wizard with no instructions beyond what the wizard displays") on every iPhone in the world.

**v1 commitment:** bundle `pillow-heif` as a hard dependency. Server-side decode of HEIC → in-memory JPEG happens transparently on photo upload. The user never sees a format-conversion step. Added in PR-2 alongside the new endpoint contracts.

### Camera intrinsics handling — the silent simplification

The biggest UX win in this redesign comes from *removing* the camera/lens picker entirely.

- **EXIF carries focal length** on most modern phone shots → `pipeline/intrinsics.py` extracts it.
- **EXIF missing** → FOV-class fallback (PR #8) supplies a sensible default (~70° FOV typical phone wide).
- **Scale is not recovered from intrinsics** anyway — it's locked by the wallet item's known dimensions. Inaccurate intrinsics hurt pose accuracy by a small amount, not scale fundamentally.
- **EXIF-missing flag** → `quality_summary.flags: ["intrinsics_estimated"]` warns the LLM downstream that coords may be ±2 mm rather than ±0.5 mm.

### mm-conversion details (load-bearing for §4 tier system)

Reprojection error in pixels misleads because pixel pitch in object space depends on shot distance. The wizard converts errors to mm via:

`mm_per_pixel = (depth_mm × pixel_size_on_sensor_mm) / focal_length_mm`

— with the depth term varying by what's being measured:

- **Pose-RMS during scale confirmation (manual corner clicks):** depth term = `cv2.solvePnP`-derived **distance from camera to the reference plane** (the card on the table). This is what's being measured at this step.
- **Per-feature triangulation error during labeling/review:** depth term = the **feature's triangulated depth** (post-triangulation Z relative to camera). For features with only 1 view (single-view-planar fallback), depth term = the marker plane's depth — which is the planar assumption that defines them.

The math is per-photo and per-feature; `/api/reproject_all` returns every (photo, feature) error as both pixels and mm so the review UI can surface whichever it needs.

### One photo's lifecycle (top to bottom)

1. User taps "Open camera." OS camera launches via `<input type="file" accept="image/*" capture>`.
2. Photo lands in browser. Client POSTs to `/api/photo`, server transparently HEIC-decodes if needed, hashes + persists JPEG to `<session>/photos/<id>.jpg`.
3. Client calls `GET /api/marker_detect/<id>` (auto-path, marker mode only) OR shows the guided corner-walkthrough (manual path, wallet-item mode — §2 step 4). Once 4 corners → `POST /api/reference` solves pose.
4. Client shows the photo with reference outline highlighted, prompts for feature labels via the bottom-sheet input (§2 step 5). Each label tap → `POST /api/feature`. Server triangulates if ≥2 views exist; otherwise stores as single-view ray-cast.
5. Client requests `GET /api/next_prompt`. Server returns next direction + reason. UI renders.

---

## 4. Coverage & completion criterion

### Coverage cells — anchored to the reference object's frame

Each photo's pose (recovered from `cv2.solvePnP`) yields the camera's optical-axis vector. Bin that vector into the closest of 6 axes — but **the axes are anchored to the reference object's long/short edges as captured in photo #1**, NOT to the user's notion of "front" or "back" of the part. This guarantees that:

- "The side with USB-C" stays geometrically stable regardless of how the user rotates the part on the table.
- The coverage bar reads as silhouettes (top, +long, -long, +short, -short) plus optional per-cell hint annotations once features are named (e.g., once `pi/usb_c` is labeled, the cell where its first photo's pose places it gets the hint *"the side with USB-C"*).

The visible coverage bar shows 5 cells: **top + 4 sides**. The bottom cell is hidden by default; revealed dynamically only if a photo's pose is genuinely downward-pointing OR a labeled feature requires a bottom view.

Internally each cell is an axis label (e.g., `+long`, `-short`); user-facing strings render as silhouette icons + optional feature-hint chips. The implementer chooses icon assets in PR-4; goal is "user immediately understands which side of their part is meant" without prose direction words.

### Next-prompt scoring algorithm (v1, simplified)

Pre-review draft used a `feature_score = (# un-triangulated features visible from this angle)` formulation — but the wizard has no way to know *which side* a 1-view-only feature would be visible from before that 2nd view exists. Implementing it required a hemisphere assumption around the existing ray, which is a fragile guess.

**v1 simplification (load-bearing for PR-3):**

```
features_pending  = (1 if any un-triangulated user-labeled features exist, else 0)
empty_cell_score  = (1 if candidate side is unfilled, else 0)
total_score       = features_pending × 10 + empty_cell_score
```

Pick the candidate with the highest `total_score`. Tie-break: prefer the side closest in rotation to the most recent shot (minimizes physical re-positioning).

The user-facing reason adapts honestly to what the algorithm actually knows:

- `features_pending = 1` and the candidate fills an empty cell → *"Take a photo of the [silhouette]-side — we still need a second view of some of your features."*
- `features_pending = 1` and no empty cells remain → *"Take a photo from a slightly different angle of the [silhouette]-side — we still need a second view of some of your features."*
- `features_pending = 0`, empty cells remain → *"Take a photo of the [silhouette]-side — you haven't seen it yet. Anything to label there?"* *(Lower priority; defers to user.)*

The wizard does NOT claim "we'll catch USB-C from there" since v1 cannot prove that. Honest under-promising > confident-but-wrong promising. v2 may add per-feature visibility scoring with a structure-from-motion approach.

Once `features_pending = 0` for all candidates AND ≥3 cells are filled, the wizard transitions tone: *"All your labeled features are captured. Want to shoot the remaining sides, or call it done?"*

### Tiered tolerance UX (mm-based, plain-English copy)

The wizard converts every error metric to mm at solve time. **Badges always pair color with a glyph and a word** — color is never load-bearing alone (≈8% of male users can't reliably distinguish green/yellow). User-facing copy is plain English; numeric mm only appears in coaching tiers where the number is *actionable*.

| Error (mm) | Badge | UX copy |
|---|---|---|
| < 0.3 mm | **✓ Excellent** (green) | "Locked in — looks great." Brief celebration; advance. |
| 0.3 – 0.5 mm | **✓ Good** (green) | Accepted silently; advance. |
| 0.5 – 1.0 mm | **⚠ Approximate** (yellow) | "Your taps were ~0.7 mm off — try once more for tighter precision?" Optional retry; user can accept and continue. |
| > 1.0 mm | **✗ Try again** (red) | Blocks advance. "Your taps look off (~1.4 mm). Re-click, or retake the photo." |

The "0.3 mm aspiration" survives in code but does NOT brag about itself in user copy. *"Locked in — looks great"* is sufficient positive feedback for the green tier; bragging about "sub-third-of-a-millimeter precise" would presume the user has any intuition for sub-mm values, which non-CAD users don't.

This same tier system applies uniformly to:
- Pose RMS during scale confirmation (depth term = card distance — see §3 mm-conversion).
- Per-feature triangulation error during labeling and review (depth term = feature's triangulated depth — see §3 mm-conversion).

### Skip-this-feature affordance — explicit surface

Some features genuinely can't be triangulated (e.g., underside features when the flat reference becomes too edge-on at near-horizontal angles per §1 non-goals). The user can mark a feature as "single-view, accept" via **two parallel surfaces**:

1. **Long-press menu on a feature row** in the persistent features panel → *"Mark as approximate (single-view only)"* option (alongside *Re-click in this photo* and *Delete*).
2. **In the soft-block finalize dialog** (§5 Bucket 3) → per-pending-feature button: *"Mark this as approximate and finalize"*.

Both transitions update the feature to `method: "single_view_planar"` with a `warning` field, and the wizard's `features_pending` counter stops including it (so next-prompt logic moves on).

```json
"features": [
  {"name": "fan_connector_hat", "xyz_mm": [12.4, 28.3, 5.1], "method": "triangulated"},
  {"name": "screw_5_underside", "xyz_mm": [-7.2, 11.8, 0.0], "method": "single_view_planar",
   "warning": "Z is approximate; only 1 view available"}
]
```

Downstream LLM reads the `method` field and knows when a coordinate is rough.

### Completion criterion (final form)

The wizard transitions to "you can finalize now" when:

1. **(Required)** Every labeled feature is either triangulated within tolerance (≤ 0.5 mm) OR explicitly user-skipped to single-view via either of the two surfaces above.
2. **(Soft nudge)** At least 3 of the 5 default coverage cells are filled. **Suppressed** when `feature_count ≥ 6 AND all features are uniformly green-tier triangulated` — many features all in good shape is sufficient evidence the part is well-captured even if the user only used 2 angles.

The user can finalize whenever (1) is satisfied; (2) is just a nudge. **No hard cap on photos** — users who want sub-mm precision can keep adding views.

---

## 5. Error handling & retakes

The wizard slots every failure into one of three buckets, each with a consistent UX pattern.

### Bucket 1 — Recoverable (user fixes inline, stays in flow)

| Failure | UX |
|---|---|
| Marker auto-detect failed | "Couldn't auto-detect — please tap the 4 corners, one at a time." Falls through to guided corner walkthrough (§2 step 4). |
| Pose RMS in red tier (>1 mm) after manual click | "Your taps look off. Try again, or retake the photo." After 3 failed retries: "Your card might be too edge-on, the lighting might be uneven, or there might be glare on your card. Try repositioning, then retake." (Three actionable causes — user self-diagnoses.) |
| Photo too oblique (>70° from vertical) | Wizard refuses pose solve: *"Your card is too edge-on in this shot — try holding the phone more above the part."* (Avoids "low-angle" and "scale reference" jargon.) |
| Per-feature reprojection in yellow band (0.5–1 mm) | Inline warning with link to the offending photo. Re-click via long-press menu, or accept. |
| User wants to retake a photo | Edit affordance per photo. **Retake cascade (specified):** new corner clicks recompute the pose for that photo; feature labels' pixel coordinates *on the retaken photo only* are cleared (the user must re-tap them on the new photo); features still triangulated have their multi-view set updated and re-triangulated. Features that lose their last view are downgraded to `single_view_planar` if any other view remains, else flagged as un-triangulated and re-prompted. Wizard shows a "this will affect: [N features]" warning before discarding. |
| User wants to delete or re-click a feature | Long-press menu on feature row (§2 step 5). Triangulation re-runs after re-click. |

### Bucket 2 — Soft warnings (don't block finalization; surfaced in `annotations.json`)

| Condition | Flag in `quality_summary.flags` | LLM downstream sees |
|---|---|---|
| EXIF missing on any photo | `"intrinsics_estimated"` | Coords may be ±2 mm instead of ±0.5 mm |
| Single-view feature (user opted in or no 2nd angle possible) | Per-feature `method: "single_view_planar"` | Z is approximate |
| Reprojection 0.5–1 mm | Per-feature `noisy: true` | Noisy but usable |
| Underside not photographed but features there | `"underside_unverified"` | Bottom features may be approximate |

### Bucket 3 — Hard refusals (wizard won't proceed)

| Condition | UX |
|---|---|
| Photo upload network failure | Inline retry + "session saved; you can resume." |
| Unsupported file format (HEIC explicitly NOT included — server-side decoded; see §3) | "Please re-export as JPEG or PNG." Reserved for genuinely-unsupported formats (TIFF variants, raw, etc.). |
| File too large (>50 MB) | "This photo is unusually large — reshoot at lower resolution." |
| Finalize with un-triangulated features | **Soft block**: dialog lists each un-triangulated feature with two per-feature buttons: *"Mark as approximate and finalize"* (single-view fallback) or *"Take more photos"*. User can override for some, finalize, and the export carries the appropriate `method` flags. |
| Session expired (existing `lifecycle.py` auto-shutdown — default 30 min idle) | "Session expired — restart wizard or restore from saved state." Resume path (below) governs the restore flow. |

### Resume and abort — explicit deliverable

The existing `events.jsonl` + `state.json` already persist the session per `server/session.py`. **Phone users will hit this routinely** (battery, page reload, swipe-away-app, OS camera taking focus). v1 must handle it cleanly or 10 minutes of capture work evaporates.

**Surface (owned by PR-4):** if the user opens the wizard URL and an active session exists in the session directory, the **first** screen is a resume-landing card:

> *"You have a session in progress: '[project_name]', [N] photos, [M] features. Continue, or start fresh?"*

— with two CTAs. Continue → rehydrate via `GET /api/state` and drop user at the most recent loop step. Start fresh → archive the old session (don't delete; keeps recovery option) and run the welcome flow.

PR-4 includes a regression test: simulate full session capture, kill browser, reopen URL, confirm resume-landing renders with the right counts and Continue actually rehydrates.

v1 doesn't add explicit multi-session listing or session management UI; that's v2 polish.

---

## 6. Migration plan & PR sequencing

### Disposition summary

- **Delete:** `ui/index.html` + `app.js` + `helpers.js` (full rewrite); `/api/lens_catalog` + lens catalog data; `/api/wireframe/<id>` + associated tests.
- **Repurpose:** `/api/anchors` → `/api/reference`; `/api/feature` extended for multi-view.
- **Add:** `/api/marker_detect`, `/api/next_prompt`, `/api/reproject_all`; `pillow-heif` dependency; resume-landing screen.
- **Keep + extend:** `schema/models.py` (additive only), math kernel (no changes), session lifecycle, CLI entry point, PR #28 synthetic-card smoke (minor adaptation if API rename impacts HTTP calls).
- **Don't do:** revert PR #48 (commits stay in main; new UI supersedes forward); break `annotations.json` schema (additions only); change global git config (per-command env vars only).

### PR sequence

| # | Title | Scope | Cross-model review focus |
|---|---|---|---|
| 1 | `feat(schema): extend annotations.json schema for redesigned wizard` | `project_name`, hierarchical feature names with `/`, `method` enum, `noisy` boolean, expanded `quality_summary.flags`. **Tests:** parser-acceptance only (validate that new schema parses; no behavioral tests yet — those land in PR-3 onwards). Backwards-compatible. | Spec-compliance (schema additions are additive, old files still parse). |
| 2 | `feat(server): new endpoint contracts + HEIC decode for prescriptive flow` | New stubs + extended `/api/feature`. Repurposed `/api/anchors` → `/api/reference`. **Add `pillow-heif` dependency + transparent HEIC→JPEG decode in `/api/photo`.** Old endpoints remain functional during transition. Tests for new contracts. | UX of error responses; spec-compliance of endpoint surface; HEIC round-trip test. |
| 3 | `feat(server): real logic — triangulation, mm-tolerance, next-prompt scoring` | Wire `triangulate_feature`. Implement mm-conversion (per §3 mm-conversion details — distinct depth terms for pose-RMS vs per-feature). Implement v1 next-prompt scoring algorithm (per §4 — `features_pending` boolean, NOT per-feature visibility). Marker auto-detect (`cv2.aruco`). Tests on synthetic scenarios including the soft-block-when-features-are-uniform-green coverage suppression rule. | Math correctness; algorithm clarity; **UX of error messages**. |
| 4 | `feat(ui): full rewrite — Flow B prescriptive coaching + resume screen + 410 cleanup` | New `index.html` + `app.js` + `helpers.js`. Reference picker (3 options), project naming, photo-capture loop with **guided corner walkthrough** (§2 step 4), bottom-sheet feature labeling (§2 step 5), persistent Finish CTA, hierarchical features panel, coverage bar (anchored to reference-object frame; silhouette icons; feature-hint chips). **Resume-landing screen** with rehydrate test (per §5). **410 Gone** regression test confirming old endpoints (`/api/anchors`, `/api/wireframe`, `/api/lens_catalog`) are unreachable. Mobile-first responsive. | **UX review primary** (does this actually work for non-CAD users?); spec-compliance secondary. |
| 5 | `feat(ui): tiered tolerance UX + soft-block finalize + skip-feature surfaces` | The Excellent/Good/Approximate/Try again badge system across pose RMS and feature triangulation, with badges always pairing color + glyph + word. Long-press menu on feature rows (Re-click / Mark approximate / Delete). Soft-block finalize dialog with per-feature override buttons. | **UX review primary** (do the badges read clearly? does the override flow feel right? color-blind mode tested?). |
| 6 | `feat(ui): review screen + reprojection drill-down + e2e tests` | Reprojection overlay using `pipeline/reproject.py`. **Tappable dots** on review thumbnails opening per-feature drill-down with re-click affordance. Playwright e2e against synthetic-card fixture. Mobile viewport tests. | **UX review primary** (does the visual confirmation actually build trust? does the drill-down feel natural?); spec-compliance on `annotations.json` final shape. |

Each PR goes through cross-model review (spec-compliance + code-quality + Codex). **All implementer and reviewer subagents dispatched on Opus 4.7** (`model: "opus"` per Agent tool call) per `feedback_subagent_model_opus_for_spatial.md` in auto-memory. PRs land sequentially because each builds on the prior contract.

### Tracking

- **Single GitHub issue** ("Wizard UX redesign — Flow B implementation") that links to this design doc and lists the 6 PRs as a checklist. Closes when PR-6 merges.
- **Existing Stream D tasks** (1.D.7 Phase 2e, 1.D.8 Phase 2f, 1.D.9 polish) get deprecated in favor of the new sequence; reference the new tracking issue from those tasks/issues.
- **Memory updates** post-PR-6: update `project_wizard_ux_gate_failure_2026_05_05.md` to RESOLVED and link the implementation issue.
- **CHANGELOG**: each PR adds a line; final v0.1.0-alpha tag bundles "Wizard UX redesign — replaces phase-based wizard with prescriptive coaching flow."

### Versioning

The redesign **does not break** any external interface (CLI invocation unchanged, `annotations.json` schema additive). v0.1.0-alpha tag remains the target after PR-6 merges. No intermediate v0.0.x tags needed.

---

## 7. Implementation constraints (load-bearing — do not skip)

### Subagent model selection

**Every Agent tool dispatch in this work — implementer subagents, reviewers, exploration subagents — passes `model: "opus"`** to invoke full-fat Opus 4.7 (1M context). Reasoning: this project's content (UX synthesis, spatial reasoning, CV pipeline correctness) sits exactly where current models struggle most. The first wizard implementation passed spec compliance but produced unusable UX precisely because the implementing subagents weren't tuned for UX synthesis. Opus 4.7 is the highest-capability tier available and the user has explicitly asked for it. See `feedback_subagent_model_opus_for_spatial.md`.

### Reviewer composition

Each PR cross-review must explicitly include a **UX-and-functionality reviewer**, not only spec-compliance. The first wizard's reviewers caught math/correctness bugs but never asked "would a real human know what this label means?" — that gap is what this redesign exists to fix. Reviewer briefs MUST instruct the reviewer to assess: (a) does each user-facing label/message read clearly to a non-CAD user, (b) does the screen flow feel coherent end-to-end, (c) does error messaging give the user actionable next steps without jargon, (d) **specifically: are any prescribed user-facing strings new flavors of the original "pcb_corner_origin" failure mode** — i.e., internally-consistent but assuming context the user doesn't have. Codex cross-perspective remains mandatory.

### Tightly scoped subagent direction

Each implementer subagent receives:
1. The slice of this design doc relevant to their PR (not the whole doc).
2. Explicit pre-corrections for known systematic defects (PEP 257, `dt.timezone.utc`, `from collections.abc`, `encoding="utf-8"`, atomic-write pattern, `# noqa: N806` on canonical CV vars).
3. The current contract surface they must preserve.
4. Test-first requirement: red test before implementation.

### Deliberately-not-decided (open questions)

These were considered and intentionally deferred to implementation discretion or v2:

- **Direction-prompt fidelity beyond text + abstract rotation arrow.** v1 ships a generic compass + rotation widget (NOT a part-silhouette overlay). Part-silhouette icons are v2 polish — they require side-photo geometry the wizard doesn't have until at least one side is shot.
- **Live preview camera composition.** v1 uses OS camera via `<input capture>`; WebRTC live-preview is v2.
- **Multi-session management.** v1 supports single-session resume only (the resume-landing screen offers Continue / Start fresh — no listing of older archived sessions). Multi-session listing is v2.
- **Bottom-cell coverage in coverage bar.** Hidden by default; revealed dynamically only if used. Showing all 6 cells always (with grey-disabled bottom) was considered and rejected as visual noise.
- **Strict vs graceful-degrade for >70° oblique photos.** Strict refusal chosen to avoid silent precision loss.
- **Pose-RMS retry budget.** 3 manual-click retries before suggesting the three-cause hint. Configurable; 3 is the starting point.
- **Coverage-cell silhouette icon style.** PR-4 implementer picks the icon assets; goal is "user immediately understands which side of their part is meant" without prose direction words.
- **Session timeout duration.** Existing `lifecycle.py` default (30 min idle); could be extended to 60+ min for v1 if testing shows phone-background-app cases trip the timeout. Adjustable in PR-2.

---

## 8. Success criteria

This design ships successfully when:

1. A non-CAD user (proxy: someone unfamiliar with this project) can complete the wizard end-to-end on a phone with no instructions beyond what the wizard itself displays, and produce an `annotations.json` whose features are triangulated within ±0.5 mm of ground truth on the synthetic-card fixture.
2. The Pi-with-HATs use case (the user's canonical scenario) successfully captures ≥10 features across the assembled stack with hierarchical names grouping correctly, all triangulated within tolerance.
3. The user-visible vocabulary contains **no terms from §9** below. (Plain-English equivalents only.)
4. PR cross-reviews catch and fix at least one UX problem per PR before merge.
5. PR #28 synthetic-card smoke continues to pass; all 139+ existing tests stay green; `annotations.json` from old wizard versions remains parseable by new schema.
6. Resume-landing screen successfully rehydrates a 5-photo session after a simulated browser-kill (PR-4 regression test).
7. HEIC photos from an iPhone reference image successfully decode and proceed through the full flow without user-visible format errors (PR-2 round-trip test).

---

## 9. Appendix: deleted vocabulary (forbidden in user-facing UI)

The following internal/CV terms **must not appear in user-facing UI strings** under any circumstance:

| Forbidden in UI | Use instead (task-grounded, never internally-jargon-y) |
|---|---|
| Phase 2a / 2b / 2c / 2d / 2e / 2f | "Setup" / "Confirm scale" / "Label" / "Take next photo" / "Review" / (no equivalent — finalize is implicit) |
| Reprojection / Reprojection RMS | "how close your taps were to the corners" (manual click), "how well the photos line up" (triangulation review) |
| RMS | (silent — render only the mm tier badge + plain-English explanation) |
| Intrinsics | "camera info" — surfaced only for the EXIF-missing flag warning; usually silent |
| Pose / solve pose | (silent — happens automatically when 4 corners click) |
| `solvePnP`, `cv2.solvePnP` | (silent — internal only) |
| Anchor | (deleted entirely; the new vocabulary is "reference object corner" if any prose refers to it, but the guided walkthrough means prose isn't needed) |
| Frame declaration / reference frame | (deleted — frame is implicit, derived from reference object) |
| `pcb_corner_origin`, `x_max`, `y_max` | (deleted — user never types coordinates) |
| Triangulation / Triangulate | "we got it from 2 angles" |
| EXIF | "camera info" (only if relevant; usually silent) |
| FOV-class / Field of view | (silent — fallback happens automatically) |
| HEIC / Format conversion | (silent — server-side decode is transparent) |
| "Top-left, clockwise" or any directional prose for corner clicks | (deleted — guided corner walkthrough highlights the next corner directly on the photo) |
| "Sub-third-of-a-millimeter precise" or any sub-mm-bragging copy | (deleted — non-CAD users have no intuition for sub-mm distances; "Locked in — looks great" is the green-tier copy) |
| "Low-angle for your scale reference" | "Your card is too edge-on in this shot — try holding the phone more above the part." |

This list is **non-exhaustive**; the principle is: **if a term comes from a CV textbook, or a piece of prose presumes axis/orientation context the user never established, it doesn't appear in the user-facing UI.** The implementing subagent and the UX-and-functionality reviewer share responsibility for catching jargon leaks.

**Replacement-design heuristic for the implementing subagent:** for any new user-facing string you're tempted to write, ask: "if I showed this to someone who has never read this codebase or any CV textbook, would they (a) understand what's being asked of them, and (b) know what to do next?" If either is no, rewrite. When in doubt, **show, don't tell** — a glowing target on a photo corner outperforms any prose about which corner is "first."
