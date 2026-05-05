# Wizard UX redesign — Flow B prescriptive coaching

**Status:** Design — pending user approval, then cross-model review.
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

### Deferred to later versions

- **v2:** Phone+desktop sync via QR code (capture on phone, annotate on desktop).
- **v2:** Free-form workspace mode for power users who don't want prescriptive coaching.
- **v2:** Multi-pose support — flipping the part with rigid-transform recovery via shared features.
- **v2:** Vertical-marker / multi-marker / 3D-fiducial modes for full-sphere coverage of tall parts.
- **v3+:** Marker-less reference recovery (SfM, common-object auto-detect from logos).

---

## 2. User flow

The flow is **linear up to the first labeled feature, loops thereafter, terminates when feature-coverage is satisfied.** Phone-native, prescriptive coaching ("take this photo now"), inline labeling per photo.

### Pre-loop (one-time, three screens)

1. **Welcome / project naming.** Single short text field: *"What are you capturing?"* → stored verbatim as `project_name` in `annotations.json`. Skippable but encouraged. Provides the downstream LLM semantic context (e.g., *"Raspberry Pi 5 with cooling HAT and PoE accessory"*).
2. **Reference picker.** Four options pre-loaded with known dimensions:
   - **Credit / debit card** (85.6 × 53.98 mm, ISO/IEC 7810 ID-1) — ±0.5 mm
   - **US dollar bill** (156.1 × 66.3 mm) — ±0.4 mm
   - **Ruler** (user-declared length, click 2 endpoints) — ±0.3 mm
   - **Printed marker** (ChArUco PDF served by wizard) — ±0.1 mm
   Choice is sticky for the session.
3. **Bootstrap instruction + first-photo prompt.** Setup diagram (phone-above-part-with-reference). "Place your part on a flat surface. Take a photo straight down." → `<input type="file" accept="image/*" capture>` launches OS camera. Phone returns the photo to the wizard.

### Per-photo loop

4. **Photo arrived → confirm scale.** Printed marker = auto-detect via `cv2.aruco.detectMarkers`. Wallet item = "Tap 4 corners, top-left first, clockwise" with 1/4 → 4/4 counter. Pose solves via `cv2.solvePnP` once 4 corners land. Bad pose triggers tiered feedback (§4 below).
5. **Label features.** "Anything to label here?" Tap photo → input box appears at tap point → type label → confirm. Persistent features panel below. Hierarchical labels with `/` separator group visually (e.g., `pi/usb_c` and `pi/hdmi` cluster under a *Pi* header). Each feature shows status: *needs 2nd view* → *✓ triangulated*.
6. **Next-photo prompt.** Wizard scores candidate directions by combining feature-driven and coverage-driven signals (§4 algorithm). Always-visible coverage bar (top + 4 sides; bottom revealed only if used). "Open camera" CTA. Loop back to step 4.

### Termination — one screen

7. **Done / review / export.** Triggered when every labeled feature is triangulated, OR the user clicks *"I'm done"* (soft-block — see §5). Review shows project name, photo thumbnails, hierarchical features panel with status icons. **No raw `(x, y, z) mm` coordinates surfaced by default** — the user verifies correctness via reprojection overlay (each feature's 3D position projected back onto each photo as a colored dot; user sees if dots land on the right spot). Coordinates ship in the downloaded JSON for the LLM. "Download annotations.json" CTA.

### Camera launch mechanism

v1 uses `<input type="file" accept="image/*" capture>` rather than WebRTC live preview. Reasons: zero-permissions, works on every phone OS today, the OS camera is already familiar to the user. v2 may add live-preview for in-page composition guidance.

### Direction-prompt fidelity

Each next-photo prompt shows: a brief plain-English instruction ("Shoot from the right side"), a directional icon (rotation arrow with the part as silhouette), and the *reason* ("we'll catch USB-C from there"). v1 doesn't render a 3D arrow / pose sketch; that's polish for later.

---

## 3. Architecture & math primitives

### Components

| Layer | Status | Notes |
|---|---|---|
| **Math kernel** (`pipeline/normalize`, `intrinsics`, `pose`, `ray`, `emit`, `reproject`) | ✅ already done, proven by PR #28 smoke | Zero new math. We compose existing primitives. |
| **Triangulation** (`triangulate_feature`) | Per the original spec, exists in math kernel; not yet wired to server endpoint | Wire it into the per-feature flow. |
| **Server (Flask, `server/app.py`)** | Major surgery — see endpoint table below | Delete some routes, repurpose others, add ~3 new ones. |
| **UI (`ui/index.html`, `app.js`, `helpers.js`)** | Full rewrite | The current 117-line phase-based shell goes away. |
| **Schema (`schema/models.py`)** | Extend additively | New fields: `project_name`, hierarchical feature names with `/`, `method` enum, `noisy` boolean, expanded `quality_summary.flags`. |
| **Session persistence** (`session.py`, `events.py`, `lifecycle.py`) | Keep | Schemas evolve; persistence model unchanged. |

### Endpoint changes

| Endpoint | Disposition | Notes |
|---|---|---|
| `GET /` | Keep | Serves the new UI. |
| `GET /api/state` | Keep | Same role: rehydrate the wizard if user reloads. |
| `GET /api/lens_catalog` | **Delete** | No more user-facing lens picker. EXIF-auto + FOV-class fallback (existing `pipeline/intrinsics.py`) replaces it. |
| `POST /api/anchors` | **Repurpose → `POST /api/reference`** | Same `cv2.solvePnP` machinery. Inputs: `photo_id`, 4 pixel coords, reference type (`credit_card` / `dollar_bill` / `ruler` / `marker`). For ruler: 2 endpoints + declared length. For marker: auto-detected (no user clicks). |
| `POST /api/feature` | **Extend** | Now accepts list of `(pixel, photo_id)` pairs. ≥2 entries → `triangulate_feature`. 1 entry → single-view ray-cast (planar fallback). |
| `POST /api/finalize` | Keep | Emits `annotations.json` per extended schema. |
| `GET /api/wireframe/<photo_id>` | **Delete** | Replaced by per-feature reprojection overlay (review screen). |
| `GET /api/marker_detect/<photo_id>` | **New** | Calls `cv2.aruco.detectMarkers`. Returns 4 corners if found, else null → user falls back to manual click. Only relevant when reference type = `marker`. |
| `GET /api/next_prompt` | **New** | Server-side scoring algorithm (§4). Returns `{direction, reason, coverage_cells, features}`. UI renders this. |
| `GET /api/reproject_all` | **New** | For review screen. Returns `{photo_id: [{feature_name, predicted_pixel, error_mm}]}` for every triangulated feature in every photo. Drives dot overlays on review thumbnails. |

### Camera intrinsics handling — the silent simplification

The biggest UX win in this redesign comes from *removing* the camera/lens picker entirely.

- **EXIF carries focal length** on most modern phone shots → `pipeline/intrinsics.py` extracts it.
- **EXIF missing** → FOV-class fallback (PR #8) supplies a sensible default (~70° FOV typical phone wide).
- **Scale is not recovered from intrinsics** anyway — it's locked by the wallet item's known dimensions. Inaccurate intrinsics hurt pose accuracy by a small amount, not scale fundamentally.
- **EXIF-missing flag** → `quality_summary.flags: ["intrinsics_estimated"]` warns the LLM downstream that coords may be ±2 mm rather than ±0.5 mm.

### One photo's lifecycle (top to bottom)

1. User taps "Open camera." OS camera launches via `<input type="file" accept="image/*" capture>`.
2. Photo lands in browser. Client POSTs to `/api/photo`, server hashes + persists to `<session>/photos/<id>.jpg`.
3. Client calls `GET /api/marker_detect/<id>` (auto-path, marker mode only) OR shows the corner-click UI (manual path, wallet-item mode). Once 4 corners → `POST /api/reference` solves pose.
4. Client shows the photo with reference outline highlighted, prompts for feature labels. Each label tap → `POST /api/feature`. Server triangulates if ≥2 views exist; otherwise stores as single-view ray-cast.
5. Client requests `GET /api/next_prompt`. Server returns next direction + reason. UI renders.

---

## 4. Coverage & completion criterion

### Coverage cells

Each photo's pose (recovered from `cv2.solvePnP`) yields the camera's optical-axis vector. Bin that vector into the closest of 6 world-axis directions (`±X`, `±Y`, `±Z`) — that's the photo's "cell." The visible coverage bar shows 5 cells: top + 4 sides. Bottom is hidden by default (revealed if a photo's pose is genuinely downward-pointing OR if a labeled feature requires it).

### Next-prompt scoring algorithm

For each candidate direction the wizard could prompt:

```
feature_score    = (# un-triangulated features expected to be visible from this angle)
coverage_score   = (1 if this fills an empty cell, else 0)
total_score      = feature_score × 10 + coverage_score
```

Pick the direction with highest `total_score`. Features outweigh coverage 10×, so feature-driven prompts always win when they exist. Coverage acts as tiebreaker and as fallback once features are exhausted. The prompt's user-visible *reason* is generated from the dominant signal:

- `feature_score > 0`, `coverage_score > 0` → "Shoot the right side — we'll catch USB-C and fill in a side you haven't seen."
- `feature_score > 0`, `coverage_score = 0` → "Shoot the right side — we need a 2nd view of USB-C from there."
- `feature_score = 0`, `coverage_score > 0` → "Shoot the left side — you haven't seen it yet. Anything to label there?" *(Lower priority; defers to user.)*

Once `feature_score = 0` for all candidates AND ≥3 cells are filled, the wizard transitions tone: "All your labeled features are captured. Want to shoot the remaining sides, or call it done?"

### Tiered tolerance UX (mm-based, not pixel-based)

Reprojection error in pixels misleads because it depends on shot distance (pixel pitch in object space scales linearly with distance from camera to part). The wizard converts per-feature reprojection errors to **millimeters** at solve time using EXIF focal length + `solvePnP`-derived distance + image resolution. This applies uniformly to pose RMS (during scale confirmation) and per-feature triangulation error.

| Error (mm) | Badge | UX |
|---|---|---|
| < 0.3 mm | **✓ Excellent** (green, animation) | "Locked — your alignment is sub-third-of-a-millimeter precise." Brief celebration; advance. |
| 0.3 – 0.5 mm | **✓ Good** (green, plain) | Accepted silently; advance. |
| 0.5 – 1.0 mm | **⚠ Approximate** (yellow) | "Accepted, but you can do better — re-click for tighter precision?" Optional retry; user can ignore. |
| > 1.0 mm | **✗ Try again** (red) | Blocks advance. "Those clicks look off. Re-click, or retake the photo." |

The 0.3 mm tier is the visible aspiration — users get a positive target without feeling like they're failing if they hit "Good" instead of "Excellent." The yellow band is where the "you can do better" coaching happens — visible but not blocking.

### Completion criterion (final form)

The wizard transitions to "you can finalize now" when:

1. **(Required)** Every labeled feature is either triangulated within tolerance (≤ 0.5 mm reprojection error) OR explicitly user-skipped to single-view.
2. **(Soft nudge)** At least 3 of the 5 default coverage cells are filled. Below this, the wizard nudges *"you've only seen the part from 2 angles — anything else worth labeling?"* but doesn't block finalization.

The user can finalize whenever (1) is satisfied; (2) is just a nudge. **No hard cap on photos** — users who want sub-mm precision can keep adding views.

### Skip-this-feature support

Some features genuinely can't be triangulated (e.g., underside features when a flat marker breaks down at near-horizontal angles). The user can mark a feature as "single-view, accept" — the wizard ray-casts to the marker plane and flags it in the export:

```json
"features": [
  {"name": "fan_connector_hat", "xyz_mm": [12.4, 28.3, 5.1], "method": "triangulated"},
  {"name": "screw_5_underside", "xyz_mm": [-7.2, 11.8, 0.0], "method": "single_view_planar",
   "warning": "Z is approximate; only 1 view available"}
]
```

Downstream LLM reads the `method` field and knows when a coordinate is rough.

---

## 5. Error handling & retakes

The wizard slots every failure into one of three buckets, each with a consistent UX pattern.

### Bucket 1 — Recoverable (user fixes inline, stays in flow)

| Failure | UX |
|---|---|
| Marker auto-detect failed | "Couldn't auto-detect — please tap the 4 corners manually." Falls through to manual-click flow. |
| Pose RMS in red tier (>1 mm) after manual click | "Those corners look slightly off. Try again or retake the photo." After 3 failed retries: hint that the angle may be too oblique. |
| Photo too oblique (>70° from vertical) | Wizard refuses pose solve: "This shot is too low-angle for your scale reference. Try a more vertical angle, or skip this shot." |
| Per-feature reprojection in yellow band (0.5–1 mm) | Inline warning with link to offending photo. Re-click or accept. |
| User wants to retake a photo | Edit affordance per photo. Retaking invalidates derived data; wizard warns before discarding. |
| User wants to delete or re-click a feature | Edit affordance per feature. Triangulation re-runs after re-click. |

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
| Unsupported file format | "Please re-export as JPEG or PNG." (Implementation note: server-side HEIC decode via `pillow-heif` is the friction-free path — iOS has defaulted to HEIC since iOS 11; relying on the user to switch to JPEG mode is a usability footgun. Decide during PR-2 whether to bundle `pillow-heif` or fall back to user-side conversion.) |
| File too large (>50 MB) | "This photo is unusually large — reshoot at lower resolution." |
| Finalize with un-triangulated features | **Soft block**: "2 features have no triangulation — skip and finalize, or take more photos?" User can override; flagged in export. |
| Session expired (existing `lifecycle.py` auto-shutdown) | "Session expired — restart wizard or restore from saved state." |

### Resume and abort

The existing `events.jsonl` + `state.json` already persist the session per `server/session.py`. New behavior: if the user reopens the wizard URL with an active session directory, they see a "resume previous session?" landing screen. v1 doesn't add explicit multi-session management or session listing; that's v2 polish.

---

## 6. Migration plan & PR sequencing

### Disposition summary

- **Delete:** `ui/index.html` + `app.js` + `helpers.js` (full rewrite); `/api/lens_catalog` + lens catalog data; `/api/wireframe/<id>` + associated tests.
- **Repurpose:** `/api/anchors` → `/api/reference`; `/api/feature` extended for multi-view.
- **Add:** `/api/marker_detect`, `/api/next_prompt`, `/api/reproject_all`.
- **Keep + extend:** `schema/models.py` (additive only), math kernel (no changes), session lifecycle, CLI entry point, PR #28 synthetic-card smoke (minor adaptation if API rename impacts HTTP calls).
- **Don't do:** revert PR #48 (commits stay in main; new UI supersedes forward); break `annotations.json` schema (additions only); change global git config (per-command env vars only).

### PR sequence

| # | Title | Scope | Cross-model review focus |
|---|---|---|---|
| 1 | `feat(schema): extend annotations.json schema for redesigned wizard` | `project_name`, hierarchical feature names, `method` enum, `noisy` boolean, expanded `quality_summary.flags`. Tests. Backwards-compatible. | Spec-compliance (schema additions are additive, old files still parse). |
| 2 | `feat(server): new endpoint contracts for prescriptive flow` | New stubs + extended `/api/feature`. Repurposed `/api/anchors` → `/api/reference`. Old endpoints remain functional during transition. Tests for new contracts. | UX of error responses; spec-compliance of endpoint surface. |
| 3 | `feat(server): real logic — triangulation, mm-tolerance, next-prompt scoring` | Wire `triangulate_feature`. Implement mm-conversion. Implement next-prompt scoring algorithm. Marker auto-detect (`cv2.aruco`). Tests on synthetic scenarios. | Math correctness; algorithm clarity; **UX of error messages**. |
| 4 | `feat(ui): full rewrite — Flow B prescriptive coaching` | New `index.html` + `app.js` + `helpers.js`. Reference picker, project naming, photo-capture loop, hierarchical features panel, coverage bar. Mobile-first responsive. | **UX review primary** (does this actually work for non-CAD users?); spec-compliance secondary. |
| 5 | `feat(ui): tiered tolerance UX + soft-block finalize` | The Excellent/Good/Approximate/Try-again badge system across pose RMS and feature triangulation. Soft-block finalize with un-triangulated features. | **UX review primary** (do the badges read clearly? does the override flow feel right?). |
| 6 | `feat(ui): review screen + reprojection overlay + e2e tests` | Reprojection overlay using `pipeline/reproject.py`. Per-feature drill-down with re-click hooks. Playwright e2e against synthetic-card fixture. Mobile viewport tests. | **UX review primary** (does the visual confirmation actually build trust?); spec-compliance on `annotations.json` final shape. |

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

Each PR cross-review must explicitly include a **UX-and-functionality reviewer**, not only spec-compliance. The first wizard's reviewers caught math/correctness bugs but never asked "would a real human know what this label means?" — that gap is what this redesign exists to fix. Reviewer briefs MUST instruct the reviewer to assess: (a) does each user-facing label/message read clearly to a non-CAD user, (b) does the screen flow feel coherent end-to-end, (c) does error messaging give the user actionable next steps without jargon. Codex cross-perspective remains mandatory.

### Tightly scoped subagent direction

Each implementer subagent receives:
1. The slice of this design doc relevant to their PR (not the whole doc).
2. Explicit pre-corrections for known systematic defects (PEP 257, `dt.timezone.utc`, `from collections.abc`, `encoding="utf-8"`, atomic-write pattern, `# noqa: N806` on canonical CV vars).
3. The current contract surface they must preserve.
4. Test-first requirement: red test before implementation.

### Deliberately-not-decided (open questions)

These were considered and intentionally deferred to implementation discretion or v2:

- **Direction-prompt fidelity beyond text + simple icon.** v1 ships text + 2D rotation arrow; 3D pose sketch is v2 polish.
- **Live preview camera composition.** v1 uses OS camera via `<input capture>`; WebRTC live-preview is v2.
- **Multi-session management.** v1 supports single-session resume only; multi-session listing is v2.
- **Bottom-cell coverage in coverage bar.** Hidden by default; revealed dynamically only if used. Showing all 6 cells always (with grey-disabled bottom) was considered and rejected as visual noise.
- **Strict vs graceful-degrade for >70° oblique photos.** Strict refusal chosen to avoid silent precision loss.
- **Pose-RMS retry budget.** 3 manual-click retries before suggesting "the angle may be too oblique." Configurable; 3 is the starting point.

---

## 8. Success criteria

This design ships successfully when:

1. A non-CAD user (proxy: someone unfamiliar with this project) can complete the wizard end-to-end on a phone with no instructions beyond what the wizard itself displays, and produce an `annotations.json` whose features are triangulated within ±0.5 mm of ground truth on the synthetic-card fixture.
2. The Pi-with-HATs use case (the user's canonical scenario) successfully captures ≥10 features across the assembled stack with hierarchical names grouping correctly, all triangulated within tolerance.
3. The user-visible vocabulary contains zero of the following terms: `phase`, `reprojection`, `RMS`, `intrinsics`, `pose`, `solvePnP`, `anchor`, `frame declaration`, `pcb_corner_origin`, `x_max`, `y_max`. (Plain-English equivalents only.)
4. PR cross-reviews catch and fix at least one UX problem per PR before merge.
5. PR #28 synthetic-card smoke continues to pass; all 139+ existing tests stay green; `annotations.json` from old wizard versions remains parseable by new schema.

---

## 9. Appendix: deleted vocabulary

For clarity, here is the explicit list of internal/CV terms that **must not appear in user-facing UI strings** under any circumstance:

| Forbidden in UI | Use instead |
|---|---|
| Phase 2a / 2b / 2c / 2d / 2e / 2f | "Setup" / "Confirm scale" / "Label" / "Take next photo" / "Review" / (no equivalent — finalize is implicit) |
| Reprojection RMS | "alignment precision" or "how off your clicks were, in mm" |
| Intrinsics | "camera info" (and only if surfaced at all — usually hidden) |
| Pose / solve pose | (silent — happens automatically when 4 corners click) |
| Anchor | "reference object corner" or "card corner" |
| Frame declaration / reference frame | (deleted — frame is implicit, derived from reference object) |
| `pcb_corner_origin`, `x_max`, `y_max` | (deleted — user never types coordinates) |
| Triangulation | "we got it from 2 angles" |
| EXIF | "camera info" (only if relevant; usually silent) |
| FOV-class | (silent — fallback happens automatically) |

This list is **non-exhaustive**; the principle is: if a term comes from a CV textbook, it doesn't appear in the user-facing UI. The implementing subagent is responsible for finding plain-English equivalents wherever a CV term would otherwise leak through.
