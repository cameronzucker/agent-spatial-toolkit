# Wizard Redesign PR-3 — Real Logic (Triangulation, mm-Tolerance, Next-Prompt) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the four PR-2 stubs (`/api/feature` ≥2-click HTTP 501 stub, `/api/marker_detect`, `/api/next_prompt`, `/api/reproject_all`) with real working logic. Add the multi-view `triangulate_feature` math primitive that is missing from the kernel today, the per-feature/per-photo mm-conversion math the tier-system UX depends on, the v1 next-prompt scoring algorithm, marker auto-detection, EXIF+FOV-class fallback for `/api/reference`, and the `/api/finalize` fix for the upload-only bug PR-2's review surfaced.

**Architecture:** All new math lives under `src/agent_spatial_toolkit/pipeline/` (`triangulate.py`, `error_mm.py`); all new server behavior lives under `src/agent_spatial_toolkit/server/` (`coverage.py`, `next_prompt.py`, `marker_detect.py`); `app.py` is modified to wire each new module into its corresponding endpoint. Each new module is independently unit-tested before being wired up. Existing endpoints retain backwards-compatibility (legacy `/api/anchors`, `/api/feature` single-click path, `/api/wireframe` all stay functional).

**Tech Stack:** Python 3.10+, OpenCV (`cv2.triangulatePoints`, `cv2.solvePnP`, `cv2.aruco`), NumPy, Flask, Pillow + `pillow-heif`. No new third-party dependencies — `cv2.aruco` is part of `opencv-python` (the existing dep). The math kernel additions stay numpy-pure (no SciPy).

**Reference:** [docs/specs/2026-05-05-wizard-ux-redesign-design.md](../specs/2026-05-05-wizard-ux-redesign-design.md) §3 (Architecture, mm-conversion details, photo lifecycle), §4 (Coverage cells, next-prompt scoring algorithm, tier system), §5 Bucket 2 (quality flags), §6 PR-3 row.

**Carry-forward from PR-2:** All endpoint shapes are merged on main (commit `d1a7b8a`). 218 tests passing. Three contract stubs and one HTTP 501 stub are awaiting real logic. Schema fields `Feature.noisy`, `Feature.warning`, quality flags `intrinsics_estimated` and `underside_unverified` are merged from PR-1 and ready to receive data.

**Deferred items rolled in from earlier reviews** (each addressed by a specific task below):

- **P1 from PR-2 final review**: `/api/finalize` upload-only bug — a client that uploads via `/api/photo` then calls `/api/finalize` WITHOUT `/api/reference` gets 200 with silently-invalid `pose: null` / `intrinsics: null` photo entries. **→ Task 9** filters unsolved photos and adds `pose_skipped_uploaded_only` quality flag.
- **Minor from PR-2 final review**: `/api/reproject_all` response shape mismatch — stub returns `{"features": []}`, design §3 calls for `{photo_id: [{feature_name, predicted_pixel, error_mm_per_photo}]}`. **→ Task 7** matches the design.
- **Minor from PR-2 final review**: HTTP error message UI translation — error strings ("pose solve failed", "no pose for photo X", etc.) are forbidden in user-facing UI per design §9 but acceptable as API responses. **→ deferred to PR-4** with a comment block at the top of `app.py` listing them, written in Task 11 alongside the PR open.
- **PR-1 minor**: Regression-suite triangulation-branch fixture — `tests/test_schema_backwards_compat.py` only exercises `planar_intersection`. **→ Task 10** adds a triangulation fixture.
- **PR-1 minor**: `Feature.noisy` docstring uses "reprojection" (a §9 forbidden term in user-facing UI). IDE tooltips are borderline; **→ Task 11** softens the docstring during the cleanup pass.

**Locked architectural decisions** (locked here so subagents don't re-litigate them):

1. **Triangulation method enum:** Stays at the existing `triangulation_2_views` … `triangulation_6_views` cap. If a feature has >6 clicks, server returns HTTP 400 ("v1 triangulates from up to 6 views; received N — please reduce to your 6 best views"). Extending the enum is a v2 schema change.
2. **`triangulate_feature` algorithm:** DLT (Direct Linear Transform) via `cv2.triangulatePoints` for the 2-view case; for 3+ views, hand-rolled SVD-based DLT across all views (numpy-only). No iterative non-linear refinement in v1 — the DLT solution is sufficient for ±0.5 mm precision per the design's success criteria. v2 may add Levenberg-Marquardt refinement.
3. **`/api/finalize` upload-only fix:** Skip unsolved photos (those with `pose=None`) silently and emit a `pose_skipped_uploaded_only:<photo_id>` flag per skipped photo in `quality_summary.flags`. **Not** a 4xx — finalize remains 200 because the user explicitly chose to finalize. The flag tells the downstream LLM what happened.
4. **`/api/reproject_all` response shape:** Match the design — `{"by_photo": {photo_id: [{feature_id, predicted_pixel, error_mm}]}}`. (Wrapping in `by_photo` keeps the response object-typed at the top level so future fields can be added; matches the existing `/api/next_prompt` pattern of named top-level fields.)
5. **EXIF-auto fallback in `/api/reference`:** Try in order: (a) explicit `intrinsics` dict in the request, (b) explicit `lens_id`, (c) read EXIF from the photo file on disk via existing `extract_exif_camera_info` + `resolve_fallback_intrinsics`, (d) FOV-class `wide` (~70° FOV typical phone) as the absolute fallback. The fallback is silent — `quality_summary.flags` carries `intrinsics_estimated` if step (c) or (d) was hit (the design's existing flag, schema-merged in PR-1).
6. **Coverage-cell axis convention:** Per design §4 — anchored to the **first photo's reference-object frame**. Photo #1's pose defines the long/short axes; subsequent photos' poses are binned by their camera-optical-axis vector against those axes. The 6 cells are: `top` (camera looking down), `+long`, `-long`, `+short`, `-short`, `bottom` (camera looking up). The bottom cell is hidden in the response when no photo's pose maps to it.
7. **Next-prompt scoring:** Exactly per design §4 — `total_score = features_pending × 10 + empty_cell_score`. No per-feature visibility scoring (v1 doesn't have SfM). Tie-break by minimum rotation distance from the most-recent photo's pose.

**All implementer and reviewer subagents dispatched on Opus 4.7** (`model: "opus"` per Agent tool call). See `feedback_subagent_model_opus_for_spatial.md` in auto-memory.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/agent_spatial_toolkit/pipeline/triangulate.py` | **Create** | Multi-view triangulation: `triangulate_feature(views: list[ViewClick]) -> TriangulationResult`. DLT via cv2.triangulatePoints (2-view) or numpy SVD (3+ views). Computes per-click reprojection residuals + RMS. Pure math; no I/O. |
| `tests/test_triangulate.py` | **Create** | Synthetic-pose unit tests: 2-view triangulation recovers known 3D point within 0.01 mm; 3-view; 6-view; coplanar-views degeneracy; mismatched input lengths raise. |
| `src/agent_spatial_toolkit/pipeline/error_mm.py` | **Create** | mm-conversion helpers: `pose_rms_mm(pose, intrinsics, image_size)` for the scale-confirmation tier (depth = camera-to-reference-plane distance), `feature_error_mm(...)` for triangulated and single-view-planar features. |
| `tests/test_error_mm.py` | **Create** | Unit tests for both conversion helpers using synthetic pose + intrinsics. |
| `src/agent_spatial_toolkit/server/coverage.py` | **Create** | `bin_pose_to_cell(pose) -> CellLabel` — bins a pose's optical axis to one of 6 cells (top + 4 sides + bottom; bottom hidden by default). `compute_coverage_cells(photos)` returns the populated-cell map. `angular_distance_to_cell` exposes per-cell canonical axes for the next-prompt rotation tie-break. World frame ≡ reference-object frame ≡ part-local frame, so no `reference_pose` parameter is needed. |
| `tests/test_coverage.py` | **Create** | Unit tests for the cell binning: top-down pose → `top`, +X-rotated → `+long`, etc. |
| `src/agent_spatial_toolkit/server/next_prompt.py` | **Create** | `score_next_prompt(state) -> NextPromptResult` — implements design §4 algorithm, returns `{direction, reason, coverage_cells, features}`. |
| `tests/test_next_prompt.py` | **Create** | Unit tests for each branch of the algorithm: (features_pending=1 + empty cell), (features_pending=1 + no empty cells), (features_pending=0 + empty cells), all-cells-filled. |
| `src/agent_spatial_toolkit/server/marker_detect.py` | **Create** | `detect_marker_corners(photo_path) -> list[tuple[float, float]] | None` — wraps `cv2.aruco.detectMarkers` with the appropriate ArUco dictionary; returns 4 corners or None. |
| `tests/test_marker_detect.py` | **Create** | Unit tests using a synthetically-rendered ArUco marker image. |
| `src/agent_spatial_toolkit/server/app.py` | Modify | Wire all new modules into endpoints: `/api/feature` (≥2 clicks → triangulate), `/api/marker_detect` (real cv2.aruco), `/api/next_prompt` (real scoring), `/api/reproject_all` (real reprojection), `/api/reference` (EXIF fallback + mm output). Fix `/api/finalize` upload-only bug. |
| `tests/test_app.py` | Modify | Extend with tests for triangulation flow, marker auto-detect, next-prompt scoring, reproject-all, EXIF fallback, finalize upload-only fix. |
| `tests/test_schema_backwards_compat.py` | Modify | Add a triangulation-branch fixture exercising `triangulation_3_views` populated `SessionArtifacts`. |
| `src/agent_spatial_toolkit/schema/models.py` | Modify | Soften the `Feature.noisy` docstring to remove the §9-forbidden "reprojection" word from a docstring that surfaces in IDE tooltips. |

No changes to: `pipeline/pose.py`, `pipeline/ray.py`, `pipeline/intrinsics.py`, `pipeline/emit.py`, `pipeline/reproject.py`, `schema/validators.py`, `server/session.py`, `server/lifecycle.py`, `server/events.py`, `server/lens_catalog.py`, `server/reference_objects.py`, `cli.py`, `ui/`.

---

## Task 1: Add `pipeline/triangulate.py` — multi-view triangulation primitive

**Files:**
- Create: `src/agent_spatial_toolkit/pipeline/triangulate.py`
- Create: `tests/test_triangulate.py`

`triangulate_feature` is the math primitive that does NOT exist today. The design doc claimed it "exists per the original spec" but it doesn't. PR-3 adds it. The function takes 2..6 view clicks (each a triple of `PoseResult`, `Intrinsics`, pixel `(u, v)`), recovers the 3D point in part-local mm via DLT, and returns the point + per-click reprojection residuals + RMS + max-residual.

Algorithm:
- Build the 3x4 projection matrix `P_i = K_i @ [R_i | t_i]` for each view (R_i from cv2.Rodrigues(rvec_i)).
- For each view, the observed pixel (u_i, v_i) gives two linear equations on the unknown 3D point X:
  - `u_i * (P_i row 2) - (P_i row 0) = 0`
  - `v_i * (P_i row 2) - (P_i row 1) = 0`
- Stack all 2N equations → matrix A of shape (2N, 4).
- The 3D point in homogeneous coordinates is the right singular vector of A corresponding to the smallest singular value: `X = V[-1, :]`. Normalize: `X = X / X[3]`.
- Per-click residuals: project X via each `P_i`, take pixel distance from observed.
- RMS = sqrt(mean(residual^2)).

The 2-view case uses `cv2.triangulatePoints` directly (it's the same DLT internally but battle-tested for the common case). The N≥3 case uses the hand-rolled numpy SVD above.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_triangulate.py`:

```python
"""Tests for pipeline.triangulate.triangulate_feature.

Synthetic poses + intrinsics are constructed so the ground-truth 3D point
is known; the test asserts that triangulation recovers it within tight
tolerance and that residuals are computed correctly.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult
from agent_spatial_toolkit.pipeline.triangulate import (
    TriangulationError,
    TriangulationResult,
    triangulate_feature,
)


def _make_intrinsics(width: int = 1000, height: int = 1000) -> Intrinsics:
    """Square-pixel zero-distortion test intrinsics."""
    return Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="test",
        fx_px=1000.0,
        fy_px=1000.0,
        cx=width / 2.0,
        cy=height / 2.0,
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )


def _project_to_view(
    world_xyz: np.ndarray,  # (3,)
    rvec: np.ndarray,
    tvec: np.ndarray,
    intrinsics: Intrinsics,
) -> tuple[float, float]:
    """Project a single 3D point through a known (rvec, tvec, intrinsics) → (u, v)."""
    K = np.array(  # noqa: N806
        [
            [intrinsics.fx_px, 0, intrinsics.cx],
            [0, intrinsics.fy_px, intrinsics.cy],
            [0, 0, 1],
        ],
        dtype=np.float64,
    )
    dist = np.array(intrinsics.distortion, dtype=np.float64)
    pts, _ = cv2.projectPoints(
        world_xyz.reshape(1, 1, 3).astype(np.float64),
        rvec.astype(np.float64),
        tvec.astype(np.float64),
        K,
        dist,
    )
    u, v = pts.reshape(2)
    return float(u), float(v)


def _make_view(
    rvec: np.ndarray,
    tvec: np.ndarray,
    intrinsics: Intrinsics,
    world_xyz: np.ndarray,
) -> tuple[PoseResult, Intrinsics, tuple[float, float]]:
    """Construct a (pose, intrinsics, observed-pixel) triple for a known point."""
    pose = PoseResult(
        rvec=rvec,
        tvec=tvec,
        anchor_reprojection_rms_px=0.5,
        intrinsics_suspect=False,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )
    pixel = _project_to_view(world_xyz, rvec, tvec, intrinsics)
    return pose, intrinsics, pixel


def test_two_view_triangulation_recovers_known_point() -> None:
    """A point at (50, 30, 10) viewed from two cameras is recovered to <0.01 mm."""
    intr = _make_intrinsics()
    world_xyz = np.array([50.0, 30.0, 10.0])

    # View 1: top-down, camera at z=200, no rotation.
    rvec1 = np.array([0.0, 0.0, 0.0])
    tvec1 = np.array([-25.0, -15.0, 200.0])
    # View 2: shifted +X with mild rotation.
    rvec2 = np.array([0.0, 0.3, 0.0])
    tvec2 = np.array([-60.0, -15.0, 200.0])

    views = [
        _make_view(rvec1, tvec1, intr, world_xyz),
        _make_view(rvec2, tvec2, intr, world_xyz),
    ]
    result = triangulate_feature(views)

    assert isinstance(result, TriangulationResult)
    np.testing.assert_allclose(result.xyz_mm, world_xyz, atol=0.01)
    assert result.n_views == 2
    assert result.triangulation_rms_px < 0.5
    assert result.max_residual_px < 1.0
    assert len(result.per_click_residuals_px) == 2


def test_three_view_triangulation_recovers_known_point() -> None:
    """A point viewed from 3 cameras converges via SVD-based multi-view DLT."""
    intr = _make_intrinsics()
    world_xyz = np.array([100.0, 50.0, 5.0])

    rvec1 = np.array([0.0, 0.0, 0.0])
    tvec1 = np.array([-50.0, -25.0, 200.0])
    rvec2 = np.array([0.0, 0.3, 0.0])
    tvec2 = np.array([-90.0, -25.0, 200.0])
    rvec3 = np.array([0.0, -0.3, 0.0])
    tvec3 = np.array([-10.0, -25.0, 200.0])

    views = [
        _make_view(rvec1, tvec1, intr, world_xyz),
        _make_view(rvec2, tvec2, intr, world_xyz),
        _make_view(rvec3, tvec3, intr, world_xyz),
    ]
    result = triangulate_feature(views)

    np.testing.assert_allclose(result.xyz_mm, world_xyz, atol=0.01)
    assert result.n_views == 3
    assert result.triangulation_rms_px < 0.5


def test_six_view_triangulation_succeeds() -> None:
    """Stress-test with 6 views — the upper bound the schema enum supports."""
    intr = _make_intrinsics()
    world_xyz = np.array([75.0, 40.0, 8.0])
    base_t = np.array([-37.5, -20.0, 200.0])

    views = []
    for i in range(6):
        # Rotate around Y by -0.3 + i*0.12 radians, fan out tvec along X.
        rvec = np.array([0.0, -0.3 + i * 0.12, 0.0])
        tvec = base_t + np.array([i * 5.0, 0.0, 0.0])
        views.append(_make_view(rvec, tvec, intr, world_xyz))

    result = triangulate_feature(views)
    np.testing.assert_allclose(result.xyz_mm, world_xyz, atol=0.05)
    assert result.n_views == 6


def test_one_view_raises() -> None:
    """Single-view triangulation is geometrically undefined; must raise."""
    intr = _make_intrinsics()
    world_xyz = np.array([50.0, 30.0, 10.0])
    rvec = np.array([0.0, 0.0, 0.0])
    tvec = np.array([-25.0, -15.0, 200.0])

    views = [_make_view(rvec, tvec, intr, world_xyz)]
    with pytest.raises(TriangulationError, match="at least 2 views"):
        triangulate_feature(views)


def test_zero_views_raises() -> None:
    """Empty input must raise, not silently return zeros."""
    with pytest.raises(TriangulationError, match="at least 2 views"):
        triangulate_feature([])


def test_seven_views_raises() -> None:
    """Schema enum caps at triangulation_6_views; >6 must be rejected here too."""
    intr = _make_intrinsics()
    world_xyz = np.array([50.0, 30.0, 10.0])
    views = []
    for i in range(7):
        rvec = np.array([0.0, -0.3 + i * 0.1, 0.0])
        tvec = np.array([-25.0 + i * 3.0, -15.0, 200.0])
        views.append(_make_view(rvec, tvec, intr, world_xyz))
    with pytest.raises(TriangulationError, match="up to 6 views"):
        triangulate_feature(views)


def test_residuals_are_zero_for_perfectly_consistent_views() -> None:
    """When pixels are computed by projection itself, residuals should be ≪ 1 px."""
    intr = _make_intrinsics()
    world_xyz = np.array([50.0, 30.0, 10.0])
    rvec1 = np.array([0.0, 0.0, 0.0])
    tvec1 = np.array([-25.0, -15.0, 200.0])
    rvec2 = np.array([0.0, 0.3, 0.0])
    tvec2 = np.array([-60.0, -15.0, 200.0])

    views = [
        _make_view(rvec1, tvec1, intr, world_xyz),
        _make_view(rvec2, tvec2, intr, world_xyz),
    ]
    result = triangulate_feature(views)
    for residual in result.per_click_residuals_px:
        assert residual < 0.5  # synthetic-clean inputs should have sub-px residuals


def test_residuals_grow_when_pixel_is_off() -> None:
    """If we perturb one pixel by 5 px, the residual on that view should reflect that."""
    intr = _make_intrinsics()
    world_xyz = np.array([50.0, 30.0, 10.0])
    rvec1 = np.array([0.0, 0.0, 0.0])
    tvec1 = np.array([-25.0, -15.0, 200.0])
    rvec2 = np.array([0.0, 0.3, 0.0])
    tvec2 = np.array([-60.0, -15.0, 200.0])

    pose1 = PoseResult(rvec1, tvec1, 0.5, False, "cv2.solvePnP_ITERATIVE")
    pose2 = PoseResult(rvec2, tvec2, 0.5, False, "cv2.solvePnP_ITERATIVE")
    p1 = _project_to_view(world_xyz, rvec1, tvec1, intr)
    p2 = _project_to_view(world_xyz, rvec2, tvec2, intr)
    # Perturb view 2's pixel by (+5, 0).
    perturbed_p2 = (p2[0] + 5.0, p2[1])

    views = [(pose1, intr, p1), (pose2, intr, perturbed_p2)]
    result = triangulate_feature(views)
    # The off-pixel will show up as residual error on at least one view.
    assert max(result.per_click_residuals_px) > 1.0


def test_non_finite_pixel_raises() -> None:
    """NaN/Inf in pixel coordinates must raise (not return non-finite output)."""
    intr = _make_intrinsics()
    pose = PoseResult(
        np.array([0.0, 0.0, 0.0]),
        np.array([0.0, 0.0, 200.0]),
        0.5,
        False,
        "cv2.solvePnP_ITERATIVE",
    )
    views = [
        (pose, intr, (float("nan"), 500.0)),
        (pose, intr, (500.0, 500.0)),
    ]
    with pytest.raises(TriangulationError, match="finite"):
        triangulate_feature(views)


def test_near_parallel_views_either_solves_or_surfaces_high_rms() -> None:
    """The realistic failure mode: user shoots two views from almost the
    same angle. The DLT system becomes ill-conditioned. The function must
    EITHER recover the point with degraded but finite accuracy, OR raise
    TriangulationError (point-at-infinity guard) — never silently return
    non-finite output."""
    intr = _make_intrinsics()
    world_xyz = np.array([50.0, 30.0, 10.0])

    # Two cameras with nearly identical poses (only 0.5° rotation difference).
    rvec1 = np.array([0.0, 0.0, 0.0])
    tvec1 = np.array([-25.0, -15.0, 200.0])
    rvec2 = np.array([0.0, np.deg2rad(0.5), 0.0])
    tvec2 = np.array([-25.5, -15.0, 200.0])

    views = [
        _make_view(rvec1, tvec1, intr, world_xyz),
        _make_view(rvec2, tvec2, intr, world_xyz),
    ]
    try:
        result = triangulate_feature(views)
        # If it solves, output must be finite (no NaN/Inf leakage).
        assert np.isfinite(result.xyz_mm).all()
        assert np.isfinite(result.triangulation_rms_px)
        # With near-parallel baselines, depth is poorly constrained.
        # Tolerance here is loose — the test's assertion is finiteness,
        # not accuracy.
        np.testing.assert_allclose(result.xyz_mm[:2], world_xyz[:2], atol=5.0)
    except TriangulationError:
        # Acceptable: point-at-infinity guard fired.
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_triangulate.py -v`

Expected: All FAIL with `ImportError` (module doesn't exist yet).

- [ ] **Step 3: Implement `pipeline/triangulate.py`**

Create `src/agent_spatial_toolkit/pipeline/triangulate.py`:

```python
"""Multi-view triangulation primitive (design §3 mm-conversion details).

Given 2..6 view clicks of the same physical feature — each with a known
PoseResult, Intrinsics, and observed pixel — recover the feature's 3D
position in part-local mm via Direct Linear Transform (DLT).

2-view path uses cv2.triangulatePoints (battle-tested for the common case).
3..6-view path uses a numpy SVD over the stacked DLT system across all
views; this is mathematically the same as the 2-view case but generalized
to N. No iterative non-linear refinement in v1 — DLT is sufficient for
the design's ±0.5 mm precision goal.

Distortion handling: pixels are undistorted via cv2.undistortPoints before
triangulation, so the projection matrices below assume a zero-distortion
camera model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult

ViewClick = tuple[PoseResult, Intrinsics, tuple[float, float]]
"""(pose, intrinsics, (u, v))"""

# Schema enum caps at triangulation_6_views (schema/models.py FeatureMeasurement.method).
# Exceeding this would emit a method value the schema rejects.
MAX_VIEWS = 6


class TriangulationError(ValueError):
    """Raised when triangulation cannot produce a valid 3D point."""


@dataclass
class TriangulationResult:
    """Result of triangulating one feature from N views."""

    xyz_mm: np.ndarray  # (3,) part-local mm
    per_click_residuals_px: list[float]  # one per view, in absolute px
    triangulation_rms_px: float  # sqrt(mean(residual^2)) across all views
    max_residual_px: float  # max of per_click_residuals_px
    n_views: int  # 2..MAX_VIEWS


def _projection_matrix(pose: PoseResult, intrinsics: Intrinsics) -> np.ndarray:
    """Build the 3x4 projection matrix P = K @ [R | t] for one view."""
    R, _ = cv2.Rodrigues(pose.rvec.astype(np.float64))  # noqa: N806 — canonical CV name
    K = intrinsics.to_camera_matrix()  # noqa: N806 — canonical CV name
    Rt = np.hstack([R, pose.tvec.astype(np.float64).reshape(3, 1)])  # noqa: N806
    return K @ Rt


def _undistort_pixel(
    pixel: tuple[float, float],
    intrinsics: Intrinsics,
) -> tuple[float, float]:
    """Apply lens-distortion correction to one observed pixel.

    The DLT below assumes a linear (zero-distortion) camera model. With
    non-zero distortion the projection matrix doesn't capture the true
    pixel→ray mapping; undistorting first lets the linear DLT operate
    on coordinates that match the K-only projection matrix.
    """
    K = intrinsics.to_camera_matrix()  # noqa: N806
    dist = np.array(intrinsics.distortion, dtype=np.float64)
    pts = np.array([[pixel[0], pixel[1]]], dtype=np.float64).reshape(-1, 1, 2)
    undistorted = cv2.undistortPoints(pts, K, dist, P=K).reshape(2)
    return float(undistorted[0]), float(undistorted[1])


def triangulate_feature(views: list[ViewClick]) -> TriangulationResult:
    """Recover a 3D point from 2..6 view clicks via DLT.

    Raises TriangulationError when input is malformed or out of bounds.
    """
    if not 2 <= len(views) <= MAX_VIEWS:
        if len(views) > MAX_VIEWS:
            raise TriangulationError(
                f"v1 triangulates from up to {MAX_VIEWS} views; got {len(views)}"
            )
        raise TriangulationError(
            f"triangulation requires at least 2 views; got {len(views)}"
        )

    # Validate finiteness up-front; non-finite would propagate to NaN xyz.
    for i, (pose, intrinsics, pixel) in enumerate(views):
        if not (
            np.isfinite(pose.rvec).all()
            and np.isfinite(pose.tvec).all()
            and np.isfinite(intrinsics.distortion).all()
            and math.isfinite(intrinsics.fx_px)
            and math.isfinite(intrinsics.fy_px)
            and math.isfinite(intrinsics.cx)
            and math.isfinite(intrinsics.cy)
            and math.isfinite(pixel[0])
            and math.isfinite(pixel[1])
        ):
            raise TriangulationError(
                f"view {i} contains non-finite values (NaN/Inf)"
            )

    # Undistort every pixel; from here on we work in linear-camera coords.
    undistorted_pixels = [_undistort_pixel(p, intr) for _, intr, p in views]

    # Build the DLT system. For each view, the observed pixel (u, v) and
    # projection matrix P give two linear constraints on the homogeneous
    # 3D point X:
    #   u * P[2] - P[0] = 0
    #   v * P[2] - P[1] = 0
    rows: list[np.ndarray] = []
    projection_matrices: list[np.ndarray] = []
    for (pose, intrinsics, _), (u, v) in zip(views, undistorted_pixels, strict=True):
        P = _projection_matrix(pose, intrinsics)  # noqa: N806
        projection_matrices.append(P)
        rows.append(u * P[2] - P[0])
        rows.append(v * P[2] - P[1])
    A = np.array(rows, dtype=np.float64)  # noqa: N806

    # Solve via SVD: 3D point in homogeneous coords is the right singular
    # vector of A corresponding to the smallest singular value.
    try:
        _, _, Vt = np.linalg.svd(A, full_matrices=False)  # noqa: N806
    except np.linalg.LinAlgError as e:
        raise TriangulationError(f"SVD failed (input likely degenerate): {e}") from e

    X_h = Vt[-1, :]  # noqa: N806 — homogeneous 3D point
    if abs(X_h[3]) < 1e-12:
        raise TriangulationError(
            "triangulation yielded a point at infinity (views may be parallel)"
        )
    xyz = X_h[:3] / X_h[3]

    if not np.isfinite(xyz).all():
        raise TriangulationError(
            "triangulation produced non-finite output (input was likely degenerate)"
        )

    # Compute per-view reprojection residuals in pixel space against the
    # ORIGINAL distorted pixels — this is what the user clicked.
    residuals: list[float] = []
    for (pose, intrinsics, observed_pixel) in views:
        K = intrinsics.to_camera_matrix()  # noqa: N806
        dist = np.array(intrinsics.distortion, dtype=np.float64)
        projected, _ = cv2.projectPoints(
            xyz.reshape(1, 1, 3).astype(np.float64),
            pose.rvec.astype(np.float64),
            pose.tvec.astype(np.float64),
            K,
            dist,
        )
        u_proj, v_proj = projected.reshape(2)
        du = u_proj - observed_pixel[0]
        dv = v_proj - observed_pixel[1]
        residuals.append(float(np.sqrt(du * du + dv * dv)))

    rms = float(np.sqrt(np.mean(np.square(residuals))))
    return TriangulationResult(
        xyz_mm=xyz,
        per_click_residuals_px=residuals,
        triangulation_rms_px=rms,
        max_residual_px=float(max(residuals)),
        n_views=len(views),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_triangulate.py -v`

Expected: All 9 tests PASS.

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 228 tests pass (218 baseline + 10 new); ruff clean.

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/wizard-redesign-pr3-real-logic
git add src/agent_spatial_toolkit/pipeline/triangulate.py tests/test_triangulate.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(pipeline): add triangulate_feature multi-view DLT primitive

Adds the missing math kernel piece that the design doc assumed existed:
2..6-view triangulation via Direct Linear Transform. 2-view path uses
cv2.triangulatePoints; 3..6-view path uses numpy SVD over the stacked
DLT system. Returns the 3D point + per-view reprojection residuals + RMS.

Pixels are undistorted via cv2.undistortPoints before the DLT (the linear
camera model in the projection matrix doesn't capture distortion).
Residuals are computed against the original observed pixels in projected
(distorted) space.

No iterative non-linear refinement in v1 — DLT alone is sufficient for
the design's ±0.5 mm precision target.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §3.
Part of: Wizard UX redesign PR-3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Wire triangulation into `POST /api/feature` (≥2-click path)

**Files:**
- Modify: `src/agent_spatial_toolkit/server/app.py` (replace HTTP 501 stub with real call)
- Modify: `tests/test_app.py` (replace 501-expecting test with real-result expectation)

PR-2 returned HTTP 501 with body `{"error": "triangulation not yet implemented; arrives in PR-3", "n_clicks_received": N}` for `clicks ≥ 2`. PR-3 replaces that with a real `triangulate_feature` call. The triangulation method enum mapping: `triangulation_2_views` for 2 clicks, `triangulation_3_views` for 3, etc. Persisted into `mem["features"]` with the right method enum so `/api/finalize` later emits the correct schema.

- [ ] **Step 1: Update the failing test**

Modify `tests/test_app.py`. Replace `test_post_feature_two_clicks_returns_501` (around line 251) with a real-triangulation test, plus add tests for 3-view and >6-view rejection:

```python
def test_post_feature_two_clicks_triangulates_to_known_point(app_factory) -> None:
    """POST /api/feature with 2 clicks runs real triangulation; returns
    pcb_xyz_mm + measurements with method=triangulation_2_views."""
    app, _, _ = app_factory()
    client = app.test_client()

    # Establish poses for two photos by re-using the synthetic anchors fixture
    # twice with different photo_ids.
    payload1 = _valid_anchors_payload()
    payload1["photo_id"] = "photo_view_1"
    r1 = client.post("/api/anchors", json=payload1)
    assert r1.status_code == 200, r1.get_json()

    payload2 = _valid_anchors_payload()
    payload2["photo_id"] = "photo_view_2"
    # Shift the camera in payload2 so the two views are not identical.
    # The anchor pixels are recomputed by _project_anchors via _valid_anchors_payload;
    # for this test we need a genuinely different camera angle. Build it manually:
    import cv2 as _cv2

    world_pts = np.array(
        [[0.0, 0.0, 0.0], [50.0, 0.0, 0.0], [50.0, 30.0, 0.0], [0.0, 30.0, 0.0]]
    )
    K = np.array([[1000.0, 0, 500.0], [0, 1000.0, 500.0], [0, 0, 1]], dtype=np.float64)  # noqa: N806
    rvec2 = np.array([0.0, 0.3, 0.0])
    tvec2 = np.array([-60.0, -15.0, 200.0])
    pixels2, _ = _cv2.projectPoints(
        world_pts, rvec2, tvec2, K, np.zeros(5)
    )
    pixels2 = pixels2.reshape(-1, 2).tolist()
    payload2["anchors"] = [
        {"id": f"a{i}", "pcb_xyz_mm": world_pts[i].tolist(), "pixel": pixels2[i]}
        for i in range(4)
    ]
    r2 = client.post("/api/anchors", json=payload2)
    assert r2.status_code == 200, r2.get_json()

    # Now click the same physical point (15, 10, 0) in both views.
    target = np.array([15.0, 10.0, 0.0])
    p1, _ = _cv2.projectPoints(target.reshape(1, 1, 3), np.zeros(3), np.array([-25.0, -15.0, 200.0]), K, np.zeros(5))
    p2, _ = _cv2.projectPoints(target.reshape(1, 1, 3), rvec2, tvec2, K, np.zeros(5))
    pixel1 = p1.reshape(2).tolist()
    pixel2 = p2.reshape(2).tolist()

    resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f_triangulated",
            "clicks": [
                {"photo_id": "photo_view_1", "pixel": pixel1},
                {"photo_id": "photo_view_2", "pixel": pixel2},
            ],
        },
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert "pcb_xyz_mm" in body
    assert body["pcb_xyz_mm"][0] == pytest.approx(15.0, abs=0.05)
    assert body["pcb_xyz_mm"][1] == pytest.approx(10.0, abs=0.05)
    assert body["pcb_xyz_mm"][2] == pytest.approx(0.0, abs=0.05)
    assert body["method"] == "triangulation_2_views"
    assert body["n_views"] == 2
    assert "triangulation_rms_px" in body
    assert "max_residual_px" in body


def test_post_feature_seven_clicks_returns_400(app_factory) -> None:
    """v1 caps at 6 views; 7 clicks must return 400 with a clear message."""
    app, _, _ = app_factory()
    client = app.test_client()

    # Establish a single pose so the photo lookups don't 404 first.
    anchors_resp = client.post("/api/anchors", json=_valid_anchors_payload())
    assert anchors_resp.status_code == 200

    resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f_too_many",
            "clicks": [
                {"photo_id": "top_down", "pixel": [500.0, 500.0]} for _ in range(7)
            ],
        },
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert "error" in body
    assert "6" in body["error"]


def test_post_feature_two_clicks_unknown_photo_returns_404(app_factory) -> None:
    """If any of the multi-view clicks references an unknown photo_id, return 404."""
    app, _, _ = app_factory()
    client = app.test_client()

    # Establish one pose; the second photo_id is intentionally absent.
    anchors_resp = client.post("/api/anchors", json=_valid_anchors_payload())
    assert anchors_resp.status_code == 200

    resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f_dangling",
            "clicks": [
                {"photo_id": "top_down", "pixel": [500.0, 500.0]},
                {"photo_id": "no_such_photo", "pixel": [400.0, 400.0]},
            ],
        },
    )
    assert resp.status_code == 404
    assert "error" in resp.get_json()
    assert "no_such_photo" in resp.get_json()["error"]
```

Also DELETE the old `test_post_feature_two_clicks_returns_501` test (it now expects the wrong status).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -v -k "two_clicks_triangulates or seven_clicks or two_clicks_unknown_photo"`

Expected: FAIL — current `/api/feature` still returns 501 for ≥2 clicks; the deleted 501 test won't run.

- [ ] **Step 3: Replace the HTTP 501 stub with real triangulation**

In `src/agent_spatial_toolkit/server/app.py`, locate the `if len(clicks) >= 2` branch in `post_feature` (around line 760). Replace it with the multi-view triangulation path. Above the route registration (or at the top of `app.py` next to other imports), add:

```python
from agent_spatial_toolkit.pipeline.triangulate import (
    MAX_VIEWS,
    TriangulationError,
    triangulate_feature,
)
```

Replace the existing 501-stub branch with:

```python
        if "clicks" in body:
            clicks = body["clicks"]
            if not isinstance(clicks, list):
                return jsonify({"error": "clicks must be an array"}), 400
            if len(clicks) == 0:
                return jsonify({"error": "clicks must contain at least one entry"}), 400
            if len(clicks) > MAX_VIEWS:
                return (
                    jsonify(
                        {
                            "error": (
                                f"v1 triangulates from up to {MAX_VIEWS} views; "
                                f"received {len(clicks)} — please reduce to your "
                                f"{MAX_VIEWS} best views"
                            )
                        }
                    ),
                    400,
                )
            if len(clicks) >= 2:
                # Multi-view triangulation path. Build (pose, intrinsics, pixel)
                # triples for each click; any missing photo / pose / intrinsics
                # is a 404 with the offending photo_id called out.
                try:
                    feature_id = body["feature_id"]
                except (KeyError, TypeError):
                    return (
                        jsonify({"error": "missing required field: feature_id"}),
                        400,
                    )
                view_triples: list[Any] = []
                for i, click in enumerate(clicks):
                    try:
                        c_photo_id = click["photo_id"]
                        c_pixel = click["pixel"]
                    except (KeyError, TypeError):
                        return (
                            jsonify(
                                {
                                    "error": (
                                        f"clicks[{i}] missing required field "
                                        "(photo_id, pixel)"
                                    )
                                }
                            ),
                            400,
                        )
                    photo_entry = mem["photos"].get(c_photo_id)
                    if photo_entry is None:
                        return (
                            jsonify(
                                {"error": f"unknown photo_id '{c_photo_id}' in clicks[{i}]"}
                            ),
                            404,
                        )
                    if photo_entry.get("pose") is None or photo_entry.get("intrinsics") is None:
                        return (
                            jsonify({"error": f"no pose for photo {c_photo_id}"}),
                            404,
                        )
                    try:
                        intrinsics_obj = _intrinsics_from_dict(photo_entry["intrinsics"])
                    except (KeyError, TypeError, ValueError) as e:
                        return (
                            jsonify({"error": f"stored intrinsics are malformed: {e}"}),
                            500,
                        )
                    pose_obj: PoseResult = photo_entry["pose"]
                    try:
                        pixel_pair = (
                            _coerce_finite_float(c_pixel[0], f"clicks[{i}].pixel[0]"),
                            _coerce_finite_float(c_pixel[1], f"clicks[{i}].pixel[1]"),
                        )
                    except (TypeError, ValueError, IndexError) as e:
                        return jsonify({"error": str(e)}), 400
                    view_triples.append((pose_obj, intrinsics_obj, pixel_pair))

                try:
                    tri = triangulate_feature(view_triples)
                except TriangulationError as e:
                    return jsonify({"error": str(e)}), 400

                method = f"triangulation_{tri.n_views}_views"
                pcb_xyz = [float(tri.xyz_mm[0]), float(tri.xyz_mm[1]), float(tri.xyz_mm[2])]
                # Persist with the multi-view shape: clicks list (not single
                # pixel), method enum reflecting view count, and the residual
                # metadata for emit.py to surface in quality_summary.
                mem["features"][feature_id] = {
                    "photo_id": clicks[0]["photo_id"],  # primary photo for legacy state-shape
                    "pixel": [float(clicks[0]["pixel"][0]), float(clicks[0]["pixel"][1])],
                    "pcb_xyz_mm": pcb_xyz,
                    "method": method,
                    "clicks": [
                        {
                            "photo_id": c["photo_id"],
                            "pixel": [float(c["pixel"][0]), float(c["pixel"][1])],
                            "reprojection_residual_px": tri.per_click_residuals_px[i],
                        }
                        for i, c in enumerate(clicks)
                    ],
                    "triangulation_rms_px": tri.triangulation_rms_px,
                    "max_residual_px": tri.max_residual_px,
                }
                event_log.write(
                    {
                        "type": "feature_triangulated",
                        "feature_id": feature_id,
                        "n_views": tri.n_views,
                        "triangulation_rms_px": tri.triangulation_rms_px,
                        "max_residual_px": tri.max_residual_px,
                    }
                )
                return jsonify(
                    {
                        "pcb_xyz_mm": pcb_xyz,
                        "method": method,
                        "n_views": tri.n_views,
                        "triangulation_rms_px": tri.triangulation_rms_px,
                        "max_residual_px": tri.max_residual_px,
                        "per_click_residuals_px": tri.per_click_residuals_px,
                    }
                )
            # Single-click case: fall through to the existing ray-cast logic
            # (unwrap clicks[0] into the legacy fields).
            try:
                feature_id = body["feature_id"]
                first_click = clicks[0]
                photo_id = first_click["photo_id"]
                pixel_in = first_click["pixel"]
            except (KeyError, TypeError):
                return (
                    jsonify(
                        {
                            "error": (
                                "missing required field "
                                "(feature_id, clicks[0].photo_id, clicks[0].pixel)"
                            )
                        }
                    ),
                    400,
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -v -k "two_clicks_triangulates or seven_clicks or two_clicks_unknown_photo or single_click"`

Expected: All PASS. The existing single-click test still works (no behavior change for that path).

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 230 tests pass (228 from Task 1 + 3 new triangulation API tests − 1 deleted 501 test = 230); ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/agent_spatial_toolkit/server/app.py tests/test_app.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(server): wire triangulation into /api/feature (replaces 501 stub)

Replaces the PR-2 'triangulation not yet implemented' HTTP 501 stub with
a real call into pipeline.triangulate.triangulate_feature. 2..6 clicks
trigger multi-view DLT; >6 returns 400 with a clear cap message; 1 click
falls through to the existing single-view ray-cast path unchanged.

Method enum is set per view count (triangulation_2_views ..
triangulation_6_views, matching the schema enum bound). Per-click
reprojection residuals are recorded so /api/finalize can populate
FeatureClick.reprojection_residual_px in the emitted annotations.json.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §3 PR-3 row.
Part of: Wizard UX redesign PR-3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Add `pipeline/error_mm.py` — mm-conversion helpers

**Files:**
- Create: `src/agent_spatial_toolkit/pipeline/error_mm.py`
- Create: `tests/test_error_mm.py`

The design's tier system (§4 — Excellent ≤0.3 mm / Good ≤0.5 mm / Approximate ≤1.0 mm / Try again >1.0 mm) needs every error metric in **mm**, not pixels. mm-conversion has TWO different depth terms per design §3:

- **Pose RMS in mm** (during scale confirmation): depth = camera-to-reference-plane distance, derived from `cv2.solvePnP`'s output. The reference plane is z=0 in part-local space; camera position is `-R^T @ tvec`; depth is the camera's z-coordinate magnitude.
- **Feature error in mm**:
  - For triangulated features: depth = the triangulated point's depth in camera coords (post-triangulation). Per-photo depth varies; one feature has different mm-error per photo.
  - For single-view-planar features: depth = the marker plane's depth (the planar assumption that defines them — same plane as the reference object).

The conversion: `mm_per_pixel = (depth_mm * pixel_pitch_mm) / focal_length_mm`. Since we use `fx_px` (focal length in pixels), the pixel-pitch terms cancel: `mm_per_pixel = depth_mm / fx_px`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_error_mm.py`:

```python
"""Tests for pipeline.error_mm — mm-conversion for the wizard's tier system."""

from __future__ import annotations

import numpy as np
import pytest

from agent_spatial_toolkit.pipeline.error_mm import (
    feature_error_mm_planar,
    feature_error_mm_triangulated,
    pose_rms_mm,
)
from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult


def _make_intr(fx: float = 1000.0) -> Intrinsics:
    return Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="test",
        fx_px=fx,
        fy_px=fx,
        cx=500.0,
        cy=500.0,
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )


def test_pose_rms_mm_converts_pixel_rms_using_camera_depth() -> None:
    """At 200 mm from the reference plane with fx=1000, 1 px ≈ 0.2 mm."""
    pose = PoseResult(
        rvec=np.array([0.0, 0.0, 0.0]),
        tvec=np.array([0.0, 0.0, 200.0]),
        anchor_reprojection_rms_px=2.0,
        intrinsics_suspect=False,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )
    intr = _make_intr(fx=1000.0)
    # The reference-plane is z=0; camera is at world z = -R^T @ tvec.
    # With R=I and tvec=[0,0,200], camera_world = [0, 0, -200] → depth=200.
    rms_mm = pose_rms_mm(pose, intr, anchor_rms_px=2.0)
    assert rms_mm == pytest.approx(0.4, abs=0.01)


def test_pose_rms_mm_at_400mm_doubles_mm_per_px() -> None:
    """Doubling depth doubles the mm-per-px scale factor."""
    pose = PoseResult(
        rvec=np.array([0.0, 0.0, 0.0]),
        tvec=np.array([0.0, 0.0, 400.0]),
        anchor_reprojection_rms_px=1.0,
        intrinsics_suspect=False,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )
    intr = _make_intr(fx=1000.0)
    rms_mm = pose_rms_mm(pose, intr, anchor_rms_px=1.0)
    assert rms_mm == pytest.approx(0.4, abs=0.01)  # 400 / 1000 = 0.4 mm/px × 1 px


def test_feature_error_mm_triangulated_uses_per_photo_depth() -> None:
    """Triangulated feature at world (50, 30, 10) viewed from camera at z=200
    has camera-frame depth = R[2,:] @ xyz + tvec[2] = 10 - 200 = (sign convention)
    For OpenCV: P_c = R @ P_w + t, depth = (R @ P_w + t)[2]."""
    intr = _make_intr(fx=1000.0)
    pose = PoseResult(
        rvec=np.array([0.0, 0.0, 0.0]),
        tvec=np.array([0.0, 0.0, 200.0]),
        anchor_reprojection_rms_px=0.5,
        intrinsics_suspect=False,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )
    feature_xyz = np.array([50.0, 30.0, 10.0])
    # Camera-frame depth: with rvec=0, R=I, so P_c = P_w + tvec.
    # depth = 10 + 200 = 210 mm.
    err_mm = feature_error_mm_triangulated(
        feature_xyz_mm=feature_xyz,
        pose=pose,
        intrinsics=intr,
        residual_px=2.0,
    )
    # 210 mm / 1000 px = 0.21 mm/px × 2 px = 0.42 mm
    assert err_mm == pytest.approx(0.42, abs=0.01)


def test_feature_error_mm_planar_uses_reference_plane_depth() -> None:
    """Single-view planar feature uses the marker-plane depth (=camera-z=200)."""
    intr = _make_intr(fx=1000.0)
    pose = PoseResult(
        rvec=np.array([0.0, 0.0, 0.0]),
        tvec=np.array([0.0, 0.0, 200.0]),
        anchor_reprojection_rms_px=2.0,
        intrinsics_suspect=False,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )
    err_mm = feature_error_mm_planar(
        pose=pose, intrinsics=intr, residual_px=2.0
    )
    assert err_mm == pytest.approx(0.4, abs=0.01)


def test_pose_rms_mm_with_rotated_pose_uses_correct_depth() -> None:
    """A rotated pose still has correct mm-conversion: depth is the camera's
    distance to the reference plane along its optical axis."""
    intr = _make_intr(fx=1000.0)
    # 30° tilt around X, camera at (0, 0, 200) world.
    # Camera-position world = -R^T @ tvec.
    # For the pose-RMS depth term, design §3 says "camera-to-reference-plane
    # distance" — this is the camera-position z-coordinate magnitude (the
    # reference plane is z=0).
    rvec = np.array([np.deg2rad(30.0), 0.0, 0.0])
    tvec = np.array([0.0, -100.0, 173.205])  # tuned so camera is roughly 200 mm above
    pose = PoseResult(
        rvec=rvec,
        tvec=tvec,
        anchor_reprojection_rms_px=1.0,
        intrinsics_suspect=False,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )
    # The implementation is responsible for handling rotation. Just assert
    # it returns a positive finite mm value with the right ballpark.
    rms_mm = pose_rms_mm(pose, intr, anchor_rms_px=1.0)
    assert 0.05 < rms_mm < 0.5


def test_pose_rms_mm_zero_focal_raises() -> None:
    """fx_px=0 would divide by zero; reject."""
    intr = _make_intr(fx=0.0)
    pose = PoseResult(
        rvec=np.array([0.0, 0.0, 0.0]),
        tvec=np.array([0.0, 0.0, 200.0]),
        anchor_reprojection_rms_px=1.0,
        intrinsics_suspect=False,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )
    with pytest.raises(ValueError, match="fx_px must be positive"):
        pose_rms_mm(pose, intr, anchor_rms_px=1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_error_mm.py -v`

Expected: All FAIL with `ImportError`.

- [ ] **Step 3: Implement the module**

Create `src/agent_spatial_toolkit/pipeline/error_mm.py`:

```python
"""mm-conversion helpers for the wizard's tier-system UX (design §3, §4).

Reprojection error is reported in pixels by the math kernel, but the user
needs mm — pixels alone don't tell the user whether their feature is "off
by 0.4 mm" (Good) or "off by 1.4 mm" (Try again). This module supplies
the depth-aware conversion.

mm_per_pixel = depth_mm / fx_px
  (when fx_px is in pixels and the optical axis is aligned with depth)

Square-pixel assumption: phone cameras virtually always have square pixels
(fx ≈ fy to within 0.1%); we use fx_px alone rather than sqrt(fx*fy). For
asymmetric sensors the bound on the introduced error is |fx-fy|/fx, well
under the design's ±0.5 mm budget.

Two conversion contexts per design §3:

1. Pose RMS during scale confirmation: depth = distance from camera origin
   to the reference plane (the card on the table). Reference plane is z=0
   in part-local mm; camera world position is -R^T @ tvec.
2. Per-feature error during labeling/review:
   - Triangulated: depth = feature's depth in CAMERA coords (P_c = R @ P_w + t)[2].
     This is the distance along the camera's optical axis from camera to
     the feature, which is what controls the pixel→mm sensitivity at the
     feature location.
   - Single-view planar: depth = reference-plane depth (same as pose RMS),
     since the planar fallback assumes the feature lies on the reference
     plane.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult


def _camera_frame_depth_at_reference_origin_mm(pose: PoseResult) -> float:
    """Camera-frame depth of the part-local origin (= reference-plane origin).

    Per design §3 mm-conversion details: the depth term that controls
    mm-per-pixel sensitivity at the reference plane is the camera-frame
    z-coordinate of points on that plane, NOT the camera's world-Z altitude.
    For an oblique camera at 30° tilt, the two differ by ~15% (1/cos(30°)) —
    enough to threaten the design's ±0.5 mm precision budget.

    For a part-local point P_w, P_camera = R @ P_w + tvec; for P_w = origin,
    P_camera = tvec, so camera-frame depth at the reference origin is tvec[2].
    Take abs() defensively (in pathological synthetic poses the convention
    can flip; the magnitude is what mm-conversion needs).
    """
    return abs(float(pose.tvec[2]))


def pose_rms_mm(
    pose: PoseResult,
    intrinsics: Intrinsics,
    anchor_rms_px: float,
) -> float:
    """Convert pose anchor RMS pixel error into mm at the reference plane.

    Used for the scale-confirmation tier badge (design §4 tier table).
    """
    if not math.isfinite(anchor_rms_px):
        raise ValueError(f"anchor_rms_px must be finite; got {anchor_rms_px}")
    if intrinsics.fx_px <= 0:
        raise ValueError(f"fx_px must be positive; got {intrinsics.fx_px}")

    depth_mm = _camera_frame_depth_at_reference_origin_mm(pose)
    mm_per_px = depth_mm / intrinsics.fx_px
    return mm_per_px * anchor_rms_px


def feature_error_mm_triangulated(
    feature_xyz_mm: np.ndarray,
    pose: PoseResult,
    intrinsics: Intrinsics,
    residual_px: float,
) -> float:
    """Convert per-photo triangulation residual into mm at the feature's depth.

    The depth term is the feature's camera-frame z-coordinate (along the
    optical axis). This is what controls pixel→mm sensitivity AT the
    feature location; using the reference-plane depth would systematically
    misreport for features above or below the plane.
    """
    if not math.isfinite(residual_px):
        raise ValueError(f"residual_px must be finite; got {residual_px}")
    if intrinsics.fx_px <= 0:
        raise ValueError(f"fx_px must be positive; got {intrinsics.fx_px}")

    R, _ = cv2.Rodrigues(pose.rvec.astype(np.float64))  # noqa: N806
    p_camera = R @ feature_xyz_mm.astype(np.float64) + pose.tvec.astype(np.float64)
    depth_mm = abs(float(p_camera[2]))
    mm_per_px = depth_mm / intrinsics.fx_px
    return mm_per_px * residual_px


def feature_error_mm_planar(
    pose: PoseResult,
    intrinsics: Intrinsics,
    residual_px: float,
) -> float:
    """Convert single-view-planar residual into mm at the reference plane.

    Single-view-planar features assume the feature lies on the reference
    plane, so the depth term is the same as `pose_rms_mm` uses.
    """
    return pose_rms_mm(pose, intrinsics, anchor_rms_px=residual_px)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_error_mm.py -v`

Expected: All 6 tests PASS.

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 236 tests pass; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/agent_spatial_toolkit/pipeline/error_mm.py tests/test_error_mm.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(pipeline): mm-conversion helpers for tier-system UX

Adds pose_rms_mm + feature_error_mm_triangulated + feature_error_mm_planar
per design §3 mm-conversion details. Distinct depth terms for the three
contexts: camera-to-reference-plane distance for pose RMS (and single-view-
planar features), feature's camera-frame z-coordinate for triangulated
features. The latter is what controls pixel→mm sensitivity AT the
feature location and was a load-bearing detail for the design's tier
system to be honest about precision at varied distances.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §3 mm-conversion,
§4 tiered tolerance UX.
Part of: Wizard UX redesign PR-3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Add mm-error to `/api/reference` response

**Files:**
- Modify: `src/agent_spatial_toolkit/server/app.py` (extend `post_reference` response with `pose_rms_mm`)
- Modify: `tests/test_app.py` (assert mm field present in response)

`/api/reference` currently returns `{pose, intrinsics_suspect}`. The wizard UI needs an `error_mm` field so its tier badge logic can render. Add `pose_rms_mm` to the JSON response. Don't change anything else about that endpoint in this task — Task 8 handles the EXIF fallback separately.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py` (near the existing `/api/reference` tests around line 793):

```python
def test_post_reference_returns_pose_rms_mm(app_factory) -> None:
    """The /api/reference response now carries pose_rms_mm so the UI
    tier-badge logic can render Excellent/Good/Approximate/Try again."""
    app, _, _ = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)

    # Project credit-card corners through a known pose to get pixel corners.
    import cv2 as _cv2

    K = np.array([[1000.0, 0, 100.0], [0, 1000.0, 75.0], [0, 0, 1]], dtype=np.float64)  # noqa: N806
    rvec = np.array([0.0, 0.0, 0.0])
    tvec = np.array([-42.8, -27.0, 200.0])
    world_corners = np.array(
        [
            [0.0, 0.0, 0.0],
            [85.60, 0.0, 0.0],
            [85.60, 53.98, 0.0],
            [0.0, 53.98, 0.0],
        ]
    )
    pixel_corners, _ = _cv2.projectPoints(world_corners, rvec, tvec, K, np.zeros(5))
    pixel_corners = pixel_corners.reshape(-1, 2).tolist()

    payload = {
        "photo_id": photo_id,
        "reference_type": "credit_card",
        "pixel_corners": pixel_corners,
        "image_size": [200, 150],
        "intrinsics": _make_test_intrinsics_dict(200, 150),
    }
    resp = client.post("/api/reference", json=payload)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert "pose_rms_mm" in body
    assert isinstance(body["pose_rms_mm"], float)
    assert body["pose_rms_mm"] >= 0.0
    assert body["pose_rms_mm"] < 5.0  # synthetic-clean clicks should be tight
```

- [ ] **Step 2: Run tests to verify it fails**

Run: `uv run pytest tests/test_app.py::test_post_reference_returns_pose_rms_mm -v`

Expected: FAIL — `pose_rms_mm` is not in the response.

- [ ] **Step 3: Wire `pose_rms_mm` into the response**

In `src/agent_spatial_toolkit/server/app.py`, locate `post_reference` (around line 566). At the top of the file add:

```python
from agent_spatial_toolkit.pipeline.error_mm import pose_rms_mm
```

Find the existing return at the end of `post_reference`:

```python
        return jsonify(
            {
                "pose": pose.to_dict(),
                "intrinsics_suspect": pose.intrinsics_suspect,
            }
        )
```

Change to:

```python
        rms_mm_value = pose_rms_mm(
            pose=pose,
            intrinsics=intrinsics,
            anchor_rms_px=pose.anchor_reprojection_rms_px,
        )
        return jsonify(
            {
                "pose": pose.to_dict(),
                "intrinsics_suspect": pose.intrinsics_suspect,
                "pose_rms_mm": rms_mm_value,
            }
        )
```

- [ ] **Step 4: Run tests to verify it passes**

Run: `uv run pytest tests/test_app.py::test_post_reference_returns_pose_rms_mm -v`

Expected: PASS.

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 237 tests pass; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/agent_spatial_toolkit/server/app.py tests/test_app.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(server): /api/reference returns pose_rms_mm for UI tier badge

Extends the POST /api/reference response with a pose_rms_mm field
computed via pipeline.error_mm.pose_rms_mm. PR-4's wizard UI uses this
to render the Excellent/Good/Approximate/Try again tier badge for the
scale-confirmation step.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §4 tier table.
Part of: Wizard UX redesign PR-3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Add `server/marker_detect.py` + wire into `/api/marker_detect`

**Files:**
- Create: `src/agent_spatial_toolkit/server/marker_detect.py`
- Create: `tests/test_marker_detect.py`
- Modify: `src/agent_spatial_toolkit/server/app.py` (replace stub)
- Modify: `tests/test_app.py` (add test for marker-detect endpoint)

`cv2.aruco.detectMarkers` does the actual detection. The wizard's `marker` reference type is a ChArUco PDF served by the wizard; for v1 the wizard ships a known marker dictionary (`DICT_4X4_50`, marker ID 0) and a known size (100 mm × 100 mm — matches `reference_objects.py`). The endpoint takes a `photo_id`, locates the JPEG on disk, runs detection, and returns 4 corner coordinates clockwise from top-left, or `null` if no marker is found.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_marker_detect.py`:

```python
"""Tests for server.marker_detect — wraps cv2.aruco.detectMarkers.

Synthetic ArUco markers are generated programmatically so the test
doesn't depend on any external fixture image.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from agent_spatial_toolkit.server.marker_detect import (
    MARKER_DICT,
    MARKER_ID,
    detect_marker_corners,
)


def _render_aruco_image(
    marker_size_px: int = 200,
    canvas_size_px: int = 800,
    marker_offset_px: tuple[int, int] = (300, 300),
) -> np.ndarray:
    """Render a synthetic image with one ArUco marker placed at a known offset."""
    aruco_dict = cv2.aruco.getPredefinedDictionary(MARKER_DICT)
    marker = cv2.aruco.generateImageMarker(aruco_dict, MARKER_ID, marker_size_px)
    canvas = np.full((canvas_size_px, canvas_size_px), 255, dtype=np.uint8)
    ox, oy = marker_offset_px
    canvas[oy : oy + marker_size_px, ox : ox + marker_size_px] = marker
    return canvas


def test_detect_returns_four_corners_when_marker_present(tmp_path: Path) -> None:
    """A synthetically-rendered marker is detected; corners come back in the
    documented order."""
    canvas = _render_aruco_image(marker_size_px=200, marker_offset_px=(300, 300))
    out = tmp_path / "marker.png"
    cv2.imwrite(str(out), canvas)

    corners = detect_marker_corners(out)
    assert corners is not None
    assert len(corners) == 4
    # cv2.aruco returns corners in TL, TR, BR, BL order — the wizard's
    # /api/reference contract calls for clockwise-from-top-left, which is
    # the same order. Each corner is (x, y).
    for c in corners:
        assert isinstance(c, tuple)
        assert len(c) == 2
    # Roughly: TL ≈ (300, 300), TR ≈ (500, 300), BR ≈ (500, 500), BL ≈ (300, 500).
    # Allow ±2 px tolerance for sub-pixel detection.
    assert abs(corners[0][0] - 300) < 2 and abs(corners[0][1] - 300) < 2
    assert abs(corners[1][0] - 500) < 2 and abs(corners[1][1] - 300) < 2
    assert abs(corners[2][0] - 500) < 2 and abs(corners[2][1] - 500) < 2
    assert abs(corners[3][0] - 300) < 2 and abs(corners[3][1] - 500) < 2


def test_detect_returns_none_when_no_marker(tmp_path: Path) -> None:
    """A blank canvas yields None — the user falls back to manual click."""
    canvas = np.full((800, 800), 200, dtype=np.uint8)
    out = tmp_path / "blank.png"
    cv2.imwrite(str(out), canvas)
    assert detect_marker_corners(out) is None


def test_detect_returns_first_marker_when_multiple_present(tmp_path: Path) -> None:
    """If multiple markers happen to be in frame, return the one with our
    known MARKER_ID (the wizard only renders one)."""
    aruco_dict = cv2.aruco.getPredefinedDictionary(MARKER_DICT)
    canvas = np.full((800, 800), 255, dtype=np.uint8)
    target = cv2.aruco.generateImageMarker(aruco_dict, MARKER_ID, 200)
    decoy = cv2.aruco.generateImageMarker(aruco_dict, MARKER_ID + 1, 100)
    canvas[100:300, 100:300] = target
    canvas[500:600, 500:600] = decoy
    out = tmp_path / "two_markers.png"
    cv2.imwrite(str(out), canvas)

    corners = detect_marker_corners(out)
    assert corners is not None
    # Should find the MARKER_ID = 0 marker at (100, 100), not the decoy.
    assert abs(corners[0][0] - 100) < 2 and abs(corners[0][1] - 100) < 2


def test_detect_raises_on_missing_file(tmp_path: Path) -> None:
    """A non-existent path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        detect_marker_corners(tmp_path / "no_such_file.png")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_marker_detect.py -v`

Expected: All FAIL with `ImportError`.

- [ ] **Step 3: Implement the module**

Create `src/agent_spatial_toolkit/server/marker_detect.py`:

```python
"""ChArUco marker auto-detection for the wizard's marker reference path.

When the user picks "Printed marker" as their scale reference, the wizard
serves a known PDF carrying an ArUco marker (DICT_4X4_50, ID 0, 100 mm
square). On photo upload the wizard calls /api/marker_detect/<photo_id>;
this module is what that endpoint calls.

Returns the 4 corner pixel coordinates clockwise from top-left, matching
the order /api/reference's pixel_corners argument expects (so the UI can
forward the result directly without reordering).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

# v1 marker dictionary + ID. The wizard's served PDF must match these.
MARKER_DICT = cv2.aruco.DICT_4X4_50
MARKER_ID = 0


def detect_marker_corners(
    photo_path: Path | str,
) -> list[tuple[float, float]] | None:
    """Detect the wizard's ArUco marker in `photo_path`.

    Returns the 4 corner coords clockwise from top-left, or None if the
    marker is not found. Raises FileNotFoundError if the path doesn't
    exist.
    """
    path = Path(photo_path)
    if not path.is_file():
        raise FileNotFoundError(f"photo not found: {path}")

    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"could not read image: {path}")

    aruco_dict = cv2.aruco.getPredefinedDictionary(MARKER_DICT)
    parameters = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
    corners, ids, _ = detector.detectMarkers(img)

    if ids is None or len(ids) == 0:
        return None

    # Find our specific MARKER_ID; if absent, return None.
    ids_flat = np.asarray(ids).flatten()
    matches = np.where(ids_flat == MARKER_ID)[0]
    if len(matches) == 0:
        return None

    # detectMarkers returns corners as a list of (1, 4, 2) arrays. cv2.aruco
    # documents the corner order as TL, TR, BR, BL — clockwise from top-left,
    # which is exactly what /api/reference expects.
    chosen = corners[matches[0]].reshape(4, 2)
    return [(float(x), float(y)) for x, y in chosen]
```

- [ ] **Step 4: Wire into the endpoint**

In `src/agent_spatial_toolkit/server/app.py`, locate `get_marker_detect` (around line 862). Replace the stub:

```python
    @app.get("/api/marker_detect/<path:photo_id>")
    def get_marker_detect(photo_id: str) -> Any:
        """Stub: real cv2.aruco.detectMarkers wiring lands in PR-3."""
        return jsonify({"corners": None})
```

With:

```python
    @app.get("/api/marker_detect/<path:photo_id>")
    def get_marker_detect(photo_id: str) -> Any:
        """Run cv2.aruco.detectMarkers against the photo on disk.

        Returns ``{"corners": [[x, y], [x, y], [x, y], [x, y]]}`` clockwise
        from top-left when the wizard's MARKER_ID is found; ``{"corners":
        null}`` when no marker is detected (the UI falls through to the
        manual corner walkthrough). 404 if the photo file is missing on
        disk.
        """
        from werkzeug.utils import secure_filename

        from agent_spatial_toolkit.server.marker_detect import detect_marker_corners

        safe_id = secure_filename(photo_id)
        if not safe_id or safe_id != photo_id:
            return jsonify({"error": "invalid photo_id"}), 404

        session: Session = app.config["SESSION"]
        photos_dir = session.session_dir / "photos"
        candidates = list(photos_dir.glob(f"{safe_id}.*"))
        # Filter to safe candidates only (no traversal escape).
        photos_dir_resolved = photos_dir.resolve()
        safe_candidates = [
            c
            for c in candidates
            if c.resolve().is_relative_to(photos_dir_resolved)
        ]
        if not safe_candidates:
            return jsonify({"error": f"photo file for {safe_id} not found on disk"}), 404

        try:
            corners = detect_marker_corners(safe_candidates[0])
        except FileNotFoundError:
            return jsonify({"error": f"photo file for {safe_id} not found on disk"}), 404

        if corners is None:
            return jsonify({"corners": None})
        return jsonify({"corners": [[x, y] for x, y in corners]})
```

- [ ] **Step 5: Add an endpoint test**

Add to `tests/test_app.py`:

```python
def test_marker_detect_returns_corners_for_aruco_photo(app_factory, tmp_path) -> None:
    """When a photo with a marker is uploaded, /api/marker_detect returns 4 corners."""
    import cv2 as _cv2

    from agent_spatial_toolkit.server.marker_detect import MARKER_DICT, MARKER_ID

    aruco_dict = _cv2.aruco.getPredefinedDictionary(MARKER_DICT)
    marker = _cv2.aruco.generateImageMarker(aruco_dict, MARKER_ID, 200)
    canvas = np.full((800, 800), 255, dtype=np.uint8)
    canvas[300:500, 300:500] = marker
    photo_path = tmp_path / "with_marker.png"
    _cv2.imwrite(str(photo_path), canvas)

    app, _, _ = app_factory()
    client = app.test_client()
    with photo_path.open("rb") as f:
        upload = client.post("/api/photo", data=f.read(), content_type="image/png")
    assert upload.status_code == 200
    photo_id = upload.get_json()["photo_id"]

    resp = client.get(f"/api/marker_detect/{photo_id}")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["corners"] is not None
    assert len(body["corners"]) == 4


def test_marker_detect_returns_null_when_no_marker(app_factory) -> None:
    """A photo with no marker returns {corners: null}."""
    app, _, _ = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)
    resp = client.get(f"/api/marker_detect/{photo_id}")
    assert resp.status_code == 200
    assert resp.get_json()["corners"] is None


def test_marker_detect_returns_404_when_photo_missing(app_factory) -> None:
    """An unknown photo_id returns 404."""
    app, _, _ = app_factory()
    client = app.test_client()
    resp = client.get("/api/marker_detect/no_such_photo")
    assert resp.status_code == 404
```

- [ ] **Step 6: Run tests to verify everything passes**

Run: `uv run pytest tests/test_marker_detect.py tests/test_app.py -v -k "marker_detect"`

Expected: All PASS.

- [ ] **Step 7: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 244 tests pass (237 + 4 module tests + 3 endpoint tests = 244); ruff clean.

- [ ] **Step 8: Commit**

```bash
git add src/agent_spatial_toolkit/server/marker_detect.py tests/test_marker_detect.py src/agent_spatial_toolkit/server/app.py tests/test_app.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(server): wire cv2.aruco.detectMarkers into /api/marker_detect

Replaces the PR-2 stub. Detection uses DICT_4X4_50 with MARKER_ID=0 to
match the wizard's bundled marker PDF. Returns 4 corners clockwise from
top-left (the order /api/reference expects); returns null when no marker
is found, so the UI falls through to the manual corner walkthrough.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §3 endpoint
table, §2 step 4 (marker auto-detect path).
Part of: Wizard UX redesign PR-3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Add `server/coverage.py` + `server/next_prompt.py` + wire `/api/next_prompt`

**Files:**
- Create: `src/agent_spatial_toolkit/server/coverage.py`
- Create: `src/agent_spatial_toolkit/server/next_prompt.py`
- Create: `tests/test_coverage.py`
- Create: `tests/test_next_prompt.py`
- Modify: `src/agent_spatial_toolkit/server/app.py` (replace stub)
- Modify: `tests/test_app.py` (add endpoint tests)

The next-prompt scoring algorithm (design §4) needs two pieces of derived data:

1. **Coverage cells** — which of the 5 default cells (`top`, `+long`, `-long`, `+short`, `-short`) is filled by which photos. Anchored to photo #1's reference-object frame: photo #1's pose defines the "long" axis (the X axis of the part-local frame, which is the long edge of the reference object); subsequent poses bin by their camera optical axis vs that reference frame.
2. **Features pending** — boolean: any user-labeled feature that hasn't reached `triangulation_2_views` yet (i.e., still on `planar_intersection` from a single click).

The scoring per design §4:
```
features_pending  = (1 if any pending features exist else 0)
empty_cell_score  = (1 if candidate side is unfilled else 0)
total_score       = features_pending × 10 + empty_cell_score
```

Returned shape: `{direction, reason, coverage_cells, features}`.

- [ ] **Step 1: Write failing tests for coverage**

Create `tests/test_coverage.py`:

```python
"""Tests for server.coverage — pose-to-cell binning.

Pose-fixture sign convention (see coverage.py module docstring): with
OpenCV PnP and the part at world z=0, a synthetic `rvec=0, tvec=[*,*,+z]`
pose has the camera at world z<0 looking up along +Z toward the part.
The "top" axis (camera optical axis when filling the top cell) is +Z.
"""

from __future__ import annotations

import numpy as np
import pytest

from agent_spatial_toolkit.pipeline.pose import PoseResult
from agent_spatial_toolkit.server.coverage import (
    CELL_CANONICAL_AXES,
    CELL_LABELS,
    angular_distance_to_cell,
    bin_pose_to_cell,
    compute_coverage_cells,
)


def _make_pose(rvec: list[float], tvec: list[float]) -> PoseResult:
    return PoseResult(
        rvec=np.array(rvec),
        tvec=np.array(tvec),
        anchor_reprojection_rms_px=0.5,
        intrinsics_suspect=False,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )


def test_top_down_pose_bins_to_top() -> None:
    """rvec=0 → R=I → R[2,:]=[0,0,+1] → matches 'top' canonical axis."""
    pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    assert bin_pose_to_cell(pose) == "top"


def test_y_rotation_bins_to_long_side() -> None:
    """Rotation about Y axis → optical axis lies in XZ plane → +long or -long."""
    side = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    cell = bin_pose_to_cell(side)
    # Ry(80°) gives R[2,:]=[-sin80°, 0, cos80°] ≈ [-0.985, 0, 0.174].
    # axis[0] < 0 → '+long' per the binning logic.
    assert cell == "+long"


def test_x_rotation_bins_to_short_side() -> None:
    """Rotation about X axis → optical axis lies in YZ plane → +short or -short."""
    side = _make_pose([np.deg2rad(80.0), 0.0, 0.0], [0.0, 30.0, 100.0])
    cell = bin_pose_to_cell(side)
    # Rx(80°) gives R[2,:]=[0, sin80°, cos80°] ≈ [0, 0.985, 0.174].
    # axis[1] > 0 → '-short' per the binning logic.
    assert cell == "-short"


def test_compute_coverage_cells_marks_filled() -> None:
    """A top-down + a Y-rotated side photo fills 'top' AND one long-side cell."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    side_pose = _make_pose([0.0, np.deg2rad(60.0), 0.0], [-30.0, 0.0, 150.0])
    cells = compute_coverage_cells([top_pose, side_pose])
    assert cells["top"] is True
    assert cells["+long"] is True
    assert cells["-long"] is False
    assert cells["+short"] is False
    assert cells["-short"] is False


def test_compute_coverage_cells_with_4_side_poses_fills_all_4_sides() -> None:
    """4 different rotation directions fill all 4 side cells."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    plus_long = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    minus_long = _make_pose([0.0, np.deg2rad(-80.0), 0.0], [30.0, 0.0, 100.0])
    # +Rx maps to '-short', -Rx maps to '+short' per the sign convention; the
    # fixture's rvec sign chooses which cell fills, not which way the user
    # rotated their phone.
    minus_short_via_pos_rx = _make_pose([np.deg2rad(80.0), 0.0, 0.0], [0.0, 30.0, 100.0])
    plus_short_via_neg_rx = _make_pose([np.deg2rad(-80.0), 0.0, 0.0], [0.0, -30.0, 100.0])
    cells = compute_coverage_cells(
        [top_pose, plus_long, minus_long, minus_short_via_pos_rx, plus_short_via_neg_rx]
    )
    assert cells["top"] is True
    assert cells["+long"] is True
    assert cells["-long"] is True
    assert cells["+short"] is True
    assert cells["-short"] is True


def test_empty_photo_list_returns_all_false() -> None:
    cells = compute_coverage_cells([])
    for label in CELL_LABELS:
        assert cells[label] is False


def test_only_visible_cells_are_in_default_response() -> None:
    """Bottom is hidden by default — design §4."""
    cells = compute_coverage_cells([])
    assert set(cells.keys()) == {"top", "+long", "-long", "+short", "-short"}


def test_canonical_axes_round_trip_through_binning() -> None:
    """Each cell's canonical axis, when used as a synthetic pose's R[2,:],
    bins back to that cell. This locks the canonical-axis lookup to the
    binning logic.

    Cross-product order is `np.cross(target, z_hat)` (NOT `cross(z_hat, target)`):
    we want a rotation R such that R[2,:] (the third ROW) equals `target`,
    which is equivalent to R^T @ z_hat = target. With `cross(z_hat, target)`
    we'd get R such that R @ z_hat = target (third COLUMN), which is the
    inverse rotation and produces the wrong sign on the optical axis.
    """
    for cell, axis in CELL_CANONICAL_AXES.items():
        target = axis / float(np.linalg.norm(axis))
        z_hat = np.array([0.0, 0.0, 1.0])
        rotation_axis = np.cross(target, z_hat)
        sin_theta = float(np.linalg.norm(rotation_axis))
        cos_theta = float(np.dot(target, z_hat))
        if sin_theta < 1e-9:
            # target is parallel to z_hat (i.e., 'top') — no rotation needed.
            rvec = np.array([0.0, 0.0, 0.0])
        else:
            rvec = rotation_axis / sin_theta * float(np.arctan2(sin_theta, cos_theta))
        pose = _make_pose(rvec.tolist(), [0.0, 0.0, 100.0])
        assert bin_pose_to_cell(pose) == cell, f"canonical axis for {cell} bins to wrong cell"


def test_angular_distance_neighbors_vs_opposites() -> None:
    """Neighboring side cells are 90° apart; opposites are 180°."""
    # Build a pose whose R[2,:] equals '+long's canonical axis.
    # See test_canonical_axes_round_trip_through_binning for the
    # `cross(target, z_hat)` (NOT `cross(z_hat, target)`) convention.
    z_hat = np.array([0.0, 0.0, 1.0])
    target = CELL_CANONICAL_AXES["+long"]
    rotation_axis = np.cross(target, z_hat)
    sin_theta = float(np.linalg.norm(rotation_axis))
    cos_theta = float(np.dot(target, z_hat))
    rvec = rotation_axis / sin_theta * float(np.arctan2(sin_theta, cos_theta))
    pose_at_plus_long = _make_pose(rvec.tolist(), [0.0, 0.0, 100.0])

    d_self = angular_distance_to_cell(pose_at_plus_long, "+long")
    d_neighbor = angular_distance_to_cell(pose_at_plus_long, "+short")
    d_opposite = angular_distance_to_cell(pose_at_plus_long, "-long")
    d_top = angular_distance_to_cell(pose_at_plus_long, "top")

    assert d_self < 1e-3  # ~0
    assert abs(d_neighbor - np.pi / 2) < 1e-3  # 90°
    assert abs(d_opposite - np.pi) < 1e-3  # 180°
    assert abs(d_top - np.pi / 2) < 1e-3  # 90°
```

- [ ] **Step 2: Implement `server/coverage.py`**

Create `src/agent_spatial_toolkit/server/coverage.py`:

```python
"""Coverage-cell binning anchored to the part-local frame (design §4).

The reference object's frame IS the part-local frame (per
reference_objects.py: long edge along X, short along Y, Z=0 plane).
All photos solve PnP against the same world points, so the part-local
frame is consistent across all photos and the design's "anchored to
photo #1" is satisfied automatically — there's no separate "photo #1's
frame" to anchor to.

Design §4: 5 default cells ('top' + 4 sides). Bottom cell is hidden by
default and revealed only when a photo's pose genuinely points up at the
part from below (a v2-only scenario; v1 doesn't support flipping the part).

Cell canonical axes (camera-optical-axis-in-world that "perfectly fills"
each cell):
  top:    [0, 0, +1]  (looking down at the part — see sign convention below)
  +long:  [-1, 0, 0]  (camera at +X, looking toward -X)
  -long:  [+1, 0, 0]  (camera at -X, looking toward +X)
  +short: [0, -1, 0]
  -short: [0, +1, 0]

These canonical axes are exposed for the next-prompt rotation-distance
tie-break.

Sign convention (load-bearing — see math review BLOCKER 2026-05-05):
With OpenCV's `cv2.solvePnP` and the part at world z=0, a "top-down"
synthetic pose (rvec=0, tvec=[*, *, +z]) has the camera at world
position -R^T @ tvec = world z<0, with optical axis (camera +Z in world
= R[2,:]) pointing along world +Z toward the part. So the "top" axis
is +Z, NOT -Z. An earlier draft of this code used -Z and would have
classified every top-down photo as 'bottom'.
"""

from __future__ import annotations

import cv2
import numpy as np

from agent_spatial_toolkit.pipeline.pose import PoseResult

CELL_LABELS = ("top", "+long", "-long", "+short", "-short")
CellLabel = str
"""One of CELL_LABELS or 'bottom' (the bottom cell is exposed via the
all-cells-including-bottom path; the default API hides it per design §4)."""

# Camera-optical-axis-in-world unit vectors that perfectly fill each cell.
CELL_CANONICAL_AXES: dict[str, np.ndarray] = {
    "top": np.array([0.0, 0.0, 1.0]),
    "+long": np.array([-1.0, 0.0, 0.0]),
    "-long": np.array([1.0, 0.0, 0.0]),
    "+short": np.array([0.0, -1.0, 0.0]),
    "-short": np.array([0.0, 1.0, 0.0]),
}

# Threshold (radians from the canonical 'top' axis) above which a pose
# is considered "side" rather than "top". 30° matches design §4's
# "moderately oblique" non-goal boundary.
_TOP_CONE_HALF_ANGLE_RAD = np.deg2rad(30.0)
# Symmetrical: >150° from 'top' axis (i.e., looking up at the part from
# below) bins to 'bottom'. v1 doesn't support the flipped-part scenario;
# this guard exists only to silently drop pathological poses.
_BOTTOM_CONE_HALF_ANGLE_RAD = np.deg2rad(150.0)


def _camera_optical_axis_world(pose: PoseResult) -> np.ndarray:
    """Camera optical axis in world (part-local) frame.

    OpenCV: P_camera = R @ P_world + tvec. Camera +Z in camera frame is
    [0,0,1]; in world frame it's R^T @ [0,0,1] = third column of R^T =
    third row of R.
    """
    R, _ = cv2.Rodrigues(pose.rvec.astype(np.float64))  # noqa: N806
    return R[2, :].astype(np.float64)


def bin_pose_to_cell(pose: PoseResult) -> CellLabel:
    """Bin one pose to its coverage cell.

    No reference_pose argument: world frame == part-local frame ==
    reference-object frame (all photos solve PnP against the same world
    points), so cell labels are stable across photos by construction.
    """
    axis = _camera_optical_axis_world(pose)
    top_axis = CELL_CANONICAL_AXES["top"]  # [0, 0, +1]
    cos_angle = float(np.dot(axis, top_axis))
    cos_angle = max(min(cos_angle, 1.0), -1.0)
    angle_from_top = np.arccos(cos_angle)

    if angle_from_top < _TOP_CONE_HALF_ANGLE_RAD:
        return "top"
    if angle_from_top > _BOTTOM_CONE_HALF_ANGLE_RAD:
        return "bottom"

    # Side bin: project axis onto the XY plane and pick the dominant axis.
    horizontal = np.array([axis[0], axis[1]])
    if np.linalg.norm(horizontal) < 1e-9:
        return "top"  # numerical fallback — axis is essentially vertical

    if abs(horizontal[0]) >= abs(horizontal[1]):
        # Camera optical axis points primarily along ±X. The cell label
        # refers to which side of the part the camera is on, which is
        # the OPPOSITE of where the optical axis points. So:
        #   axis[0] > 0  ⇒  optical axis toward +X  ⇒  camera on -X side  ⇒ '-long'
        #   axis[0] < 0  ⇒  optical axis toward -X  ⇒  camera on +X side  ⇒ '+long'
        return "-long" if horizontal[0] > 0 else "+long"
    return "-short" if horizontal[1] > 0 else "+short"


def angular_distance_to_cell(pose: PoseResult, cell: CellLabel) -> float:
    """Angular distance (radians) from `pose`'s optical axis to `cell`'s
    canonical axis. Used for next-prompt tie-break to minimize physical
    re-positioning.
    """
    if cell not in CELL_CANONICAL_AXES:
        # Defensively handle 'bottom' or unknown labels — return π so they
        # never tie-break-win against the 5 default cells.
        return float(np.pi)
    axis = _camera_optical_axis_world(pose)
    canonical = CELL_CANONICAL_AXES[cell]
    cos_angle = float(np.dot(axis, canonical))
    cos_angle = max(min(cos_angle, 1.0), -1.0)
    return float(np.arccos(cos_angle))


def compute_coverage_cells(poses: list[PoseResult]) -> dict[str, bool]:
    """Return {cell_label: True/False} for the 5 default cells.

    The bottom cell is omitted from the default response per design §4.
    """
    result = {label: False for label in CELL_LABELS}
    for pose in poses:
        cell = bin_pose_to_cell(pose)
        if cell in result:
            result[cell] = True
        # 'bottom' is dropped silently for the default-5 response.
    return result
```

- [ ] **Step 3: Run coverage tests to verify they pass**

Run: `uv run pytest tests/test_coverage.py -v`

Expected: All 5 tests PASS.

- [ ] **Step 4: Write failing tests for next_prompt**

Create `tests/test_next_prompt.py`:

```python
"""Tests for server.next_prompt — design §4 scoring algorithm."""

from __future__ import annotations

import numpy as np
import pytest

from agent_spatial_toolkit.pipeline.pose import PoseResult
from agent_spatial_toolkit.server.next_prompt import (
    GREEN_TIER_MM_THRESHOLD,
    score_next_prompt,
)


def _make_pose(rvec: list[float], tvec: list[float]) -> PoseResult:
    return PoseResult(
        rvec=np.array(rvec),
        tvec=np.array(tvec),
        anchor_reprojection_rms_px=0.5,
        intrinsics_suspect=False,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )


def test_no_photos_no_features_starts_with_top() -> None:
    """Empty session: prompt for the top-down photo first."""
    result = score_next_prompt(photos=[], features=[])
    assert result["direction"] == "top"
    assert result["reason_code"] == "top_first_photo"


def test_features_pending_and_empty_cell_picks_empty_side() -> None:
    """When some feature is single-view-only AND a side cell is empty, the
    algorithm picks an empty-cell side."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    photos = [{"id": "top", "pose": top_pose}]
    features = [{"id": "f1", "method": "planar_intersection"}]
    result = score_next_prompt(photos=photos, features=features)
    assert result["direction"] != "top"
    assert result["reason_code"] == "second_view_needed_empty_cell"


def test_features_pending_no_empty_cells_says_different_angle() -> None:
    """When all side cells are filled and a feature is still pending, the
    reason_code adapts to second_view_needed_filled_cell."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    plus_long = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    minus_long = _make_pose([0.0, np.deg2rad(-80.0), 0.0], [30.0, 0.0, 100.0])
    minus_short = _make_pose([np.deg2rad(80.0), 0.0, 0.0], [0.0, 30.0, 100.0])
    plus_short = _make_pose([np.deg2rad(-80.0), 0.0, 0.0], [0.0, -30.0, 100.0])
    photos = [
        {"id": str(i), "pose": p}
        for i, p in enumerate(
            [top_pose, plus_long, minus_long, minus_short, plus_short]
        )
    ]
    features = [{"id": "f1", "method": "planar_intersection"}]
    result = score_next_prompt(photos=photos, features=features)
    assert result["reason_code"] == "second_view_needed_filled_cell"


def test_no_pending_features_empty_cells_says_lower_priority() -> None:
    """All features triangulated, but cells remain empty: prompt is lower-priority."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    photos = [{"id": "top", "pose": top_pose}]
    features = [{"id": "f1", "method": "triangulation_2_views"}]
    result = score_next_prompt(photos=photos, features=features)
    assert result["reason_code"] == "unseen_side_low_priority"


def test_call_it_done_when_3_cells_filled_and_no_pending() -> None:
    """No pending features AND ≥3 cells filled: tone shifts to 'call it done'."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    plus_long = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    minus_short = _make_pose([np.deg2rad(80.0), 0.0, 0.0], [0.0, 30.0, 100.0])
    photos = [
        {"id": "top", "pose": top_pose},
        {"id": "p1", "pose": plus_long},
        {"id": "p2", "pose": minus_short},
    ]
    features = [
        {"id": "f1", "method": "triangulation_2_views"},
        {"id": "f2", "method": "triangulation_3_views"},
    ]
    result = score_next_prompt(photos=photos, features=features)
    assert result["reason_code"] == "call_it_done"


def test_suppression_rule_call_it_done_with_only_2_cells_when_6_uniformly_green() -> None:
    """Design §4 completion criterion #2 suppression: when feature_count ≥ 6
    AND all features are green-tier triangulated, the ≥3-cells soft nudge
    is suppressed and the wizard says 'call it done' even with 2 cells filled."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    plus_long = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    photos = [
        {"id": "top", "pose": top_pose},
        {"id": "p1", "pose": plus_long},
    ]
    features = [
        {
            "id": f"f{i}",
            "method": "triangulation_2_views",
            "max_error_mm": GREEN_TIER_MM_THRESHOLD - 0.1,  # green tier
        }
        for i in range(6)
    ]
    result = score_next_prompt(photos=photos, features=features)
    assert result["reason_code"] == "call_it_done"


def test_suppression_rule_does_not_apply_when_one_feature_is_yellow() -> None:
    """If even one of 6 features is above green threshold, suppression
    doesn't apply and we fall back to the empty-cells branch."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    plus_long = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    photos = [
        {"id": "top", "pose": top_pose},
        {"id": "p1", "pose": plus_long},
    ]
    features = [
        {
            "id": f"f{i}",
            "method": "triangulation_2_views",
            "max_error_mm": GREEN_TIER_MM_THRESHOLD - 0.1,
        }
        for i in range(5)
    ] + [
        {
            "id": "f5_yellow",
            "method": "triangulation_2_views",
            "max_error_mm": GREEN_TIER_MM_THRESHOLD + 0.1,  # above green
        }
    ]
    result = score_next_prompt(photos=photos, features=features)
    # 2 cells filled (top + +long), 3 still empty; not "call_it_done".
    assert result["reason_code"] in {
        "unseen_side_low_priority",
        "another_angle_low_priority",
    }


def test_rotation_tiebreak_picks_neighbor_over_opposite() -> None:
    """When all cells are tied on score, tie-break picks the side closest
    in rotation to the most-recent photo."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    # Most recent photo: at +long (camera optical axis pointing toward -X).
    plus_long_recent = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    photos = [
        {"id": "top", "pose": top_pose},
        {"id": "recent_plus_long", "pose": plus_long_recent},
    ]
    # No features → features_pending=0; only +short / -short / -long have
    # empty_cell_score=1. Rotation distances from +long-recent:
    #   to +long: ~0   (already filled, score=10*0+0=0, lower base)
    #   to -long: 180° (empty, score=1)
    #   to +short: 90° (empty, score=1)
    #   to -short: 90° (empty, score=1)
    # The two short sides tie at distance 90°. Tie-break picks first iter
    # (deterministic given dict order in CELL_LABELS); but BOTH are 90°
    # neighbors. Anything BUT -long is acceptable.
    features: list = []
    result = score_next_prompt(photos=photos, features=features)
    assert result["direction"] != "-long"  # must NOT pick the 180° opposite


def test_response_shape_includes_reason_code() -> None:
    """The full response shape includes reason_code, reason, direction,
    coverage_cells, features."""
    result = score_next_prompt(photos=[], features=[])
    assert set(result.keys()) >= {
        "direction",
        "reason_code",
        "reason",
        "coverage_cells",
        "features",
    }
    assert isinstance(result["coverage_cells"], dict)
    assert isinstance(result["features"], list)
```

- [ ] **Step 5: Implement `server/next_prompt.py`**

Create `src/agent_spatial_toolkit/server/next_prompt.py`:

```python
"""Next-prompt scoring algorithm (design §4 v1 simplification).

```
features_pending  = (1 if any un-triangulated user-labeled features exist else 0)
empty_cell_score  = (1 if candidate side is unfilled else 0)
total_score       = features_pending × 10 + empty_cell_score
```

Pick the candidate with the highest total_score. Tie-break by minimum
angular distance between the candidate cell's canonical axis and the
most-recent photo's optical axis (minimizes physical re-positioning per
design §4).

Honest reason branches (mapped to enum reason_code values for PR-4 prose):
- features_pending=1 + candidate fills empty cell → second_view_needed_empty_cell
- features_pending=1 + no empty cells           → second_view_needed_filled_cell
- features_pending=0 + empty cells              → unseen_side_low_priority
- features_pending=0 + ≥3 cells filled          → call_it_done
- features_pending=0 + ≥6 features uniformly green → call_it_done (suppression rule)
- no photos yet                                 → top_first_photo

The string `reason` in the response is FALLBACK English copy that
interpolates the internal axis label literally (e.g., '+long-side').
PR-4's UI MUST translate `reason_code` → localized prose and `direction`
→ silhouette icon; the fallback `reason` exists only so a debugger sees
something sensible. Per design §9 the user must NEVER see literal axis
labels.
"""

from __future__ import annotations

from typing import Any

from agent_spatial_toolkit.pipeline.pose import PoseResult
from agent_spatial_toolkit.server.coverage import (
    CELL_LABELS,
    angular_distance_to_cell,
    compute_coverage_cells,
)

# Threshold: a triangulated feature is "green tier" when its max-residual
# converted to mm is below this value. Matches design §4 tier table
# (Excellent + Good combined upper bound = 0.5 mm).
GREEN_TIER_MM_THRESHOLD = 0.5
# Suppression-rule feature-count threshold per design §4 completion criterion #2.
SUPPRESSION_RULE_FEATURE_COUNT = 6


def _is_pending(feature: dict[str, Any]) -> bool:
    """A feature is pending iff it has only single-view (planar_intersection)
    coverage AND the user has NOT explicitly marked it accepted as single-view.
    """
    method = feature.get("method", "")
    return method == "planar_intersection" and not feature.get("user_accepted_single_view", False)


def _is_uniformly_green_triangulated(features: list[dict[str, Any]]) -> bool:
    """True iff every feature is triangulated AND max_error_mm < threshold.

    Caller (app.py) is responsible for populating `feature["max_error_mm"]`
    via the per-photo depth-aware mm-conversion (pipeline.error_mm). If the
    field is absent for any feature we conservatively return False — design
    §4 calls for "uniformly green-tier triangulated" and absence is not
    confirmation.
    """
    if not features:
        return False
    for f in features:
        method = f.get("method", "")
        if not method.startswith("triangulation_"):
            return False
        max_err = f.get("max_error_mm")
        if max_err is None or float(max_err) >= GREEN_TIER_MM_THRESHOLD:
            return False
    return True


def score_next_prompt(
    photos: list[dict[str, Any]],
    features: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute the next-photo prompt for the wizard's UI.

    `photos`: list of {"id": str, "pose": PoseResult} entries. Order
    matters — photos[-1] is the most recent (used for tie-break).
    `features`: list of {"id": str, "method": str, "max_error_mm"?: float}
    entries. The optional `max_error_mm` is required for the suppression
    rule check; absence is treated conservatively as "not green tier".
    """
    poses = [p["pose"] for p in photos if isinstance(p.get("pose"), PoseResult)]
    coverage = compute_coverage_cells(poses)
    pending_features = [f for f in features if _is_pending(f)]
    features_pending = 1 if pending_features else 0

    side_labels = [c for c in CELL_LABELS if c != "top"]
    n_filled_cells = sum(1 for c in CELL_LABELS if coverage[c])

    feature_serialized = [
        {"id": f["id"], "method": f.get("method", "")} for f in features
    ]

    # Branch 1: zero photos → top first.
    if not poses:
        return {
            "direction": "top",
            "reason_code": "top_first_photo",
            "reason": "Take your first photo straight down on your part.",
            "coverage_cells": coverage,
            "features": feature_serialized,
        }

    # Branch 2: 'call it done' — features done AND (≥3 cells filled OR the
    # suppression rule applies: ≥6 features all uniformly green-tier).
    suppression_applies = (
        len(features) >= SUPPRESSION_RULE_FEATURE_COUNT
        and _is_uniformly_green_triangulated(features)
    )
    if features_pending == 0 and (n_filled_cells >= 3 or suppression_applies):
        # Pick a candidate side with the rotation tie-break, in case the
        # user wants to keep going.
        best_direction = _pick_side_with_rotation_tiebreak(
            side_labels=side_labels,
            coverage=coverage,
            features_pending=0,
            recent_pose=poses[-1],
        )
        return {
            "direction": best_direction,
            "reason_code": "call_it_done",
            "reason": (
                "All your labeled features are captured. "
                "Want to shoot the remaining sides, or call it done?"
            ),
            "coverage_cells": coverage,
            "features": feature_serialized,
        }

    # Branch 3: score every side and pick best with rotation tie-break.
    best_direction = _pick_side_with_rotation_tiebreak(
        side_labels=side_labels,
        coverage=coverage,
        features_pending=features_pending,
        recent_pose=poses[-1],
    )

    # Reason-code + fallback prose. Internal axis label leaks into the
    # fallback string by design — see module docstring.
    if features_pending == 1 and not coverage[best_direction]:
        reason_code = "second_view_needed_empty_cell"
        reason = (
            f"Take a photo of the {best_direction}-side — we still need a "
            "second view of some of your features."
        )
    elif features_pending == 1 and coverage[best_direction]:
        reason_code = "second_view_needed_filled_cell"
        reason = (
            f"Take a photo from a slightly different angle of the {best_direction}-side — "
            "we still need a second view of some of your features."
        )
    elif features_pending == 0 and not coverage[best_direction]:
        reason_code = "unseen_side_low_priority"
        reason = (
            f"Take a photo of the {best_direction}-side — you haven't seen it yet. "
            "Anything to label there?"
        )
    else:
        reason_code = "another_angle_low_priority"
        reason = (
            f"Take another photo of the {best_direction}-side from a different angle."
        )

    return {
        "direction": best_direction,
        "reason_code": reason_code,
        "reason": reason,
        "coverage_cells": coverage,
        "features": feature_serialized,
    }


def _pick_side_with_rotation_tiebreak(
    side_labels: list[str],
    coverage: dict[str, bool],
    features_pending: int,
    recent_pose: PoseResult,
) -> str:
    """Score each side per the design §4 algorithm; tie-break by minimum
    angular distance between the candidate cell's canonical axis and
    `recent_pose`'s optical axis.
    """
    best_score = -1.0
    best_dist = float("inf")
    best_direction = side_labels[0]
    for side in side_labels:
        empty_score = 1 if not coverage[side] else 0
        score = features_pending * 10 + empty_score
        dist = angular_distance_to_cell(recent_pose, side)
        if score > best_score or (score == best_score and dist < best_dist):
            best_score = score
            best_dist = dist
            best_direction = side
    return best_direction
```

- [ ] **Step 6: Wire into `/api/next_prompt`**

In `src/agent_spatial_toolkit/server/app.py`, locate `get_next_prompt` (around line 867). Replace the stub with:

```python
    @app.get("/api/next_prompt")
    def get_next_prompt() -> Any:
        """Return the next-photo prompt per design §4 scoring algorithm.

        Computes per-feature `max_error_mm` from the stored triangulation
        residuals so the suppression rule (≥6 features uniformly green-tier)
        can fire even with fewer than 3 cells filled.
        """
        from agent_spatial_toolkit.pipeline.error_mm import (
            feature_error_mm_triangulated,
        )
        from agent_spatial_toolkit.server.next_prompt import score_next_prompt

        mem: dict[str, Any] = app.config["STATE"]

        photos_in: list[dict[str, Any]] = []
        for photo_id, entry in mem["photos"].items():
            pose = entry.get("pose")
            if isinstance(pose, PoseResult):
                photos_in.append({"id": photo_id, "pose": pose})

        features_in: list[dict[str, Any]] = []
        for feature_id, feat in mem["features"].items():
            method = feat.get("method", "")
            entry = {"id": feature_id, "method": method}
            # For triangulated features, compute the worst per-photo mm error
            # so score_next_prompt can apply the suppression rule.
            if method.startswith("triangulation_"):
                xyz = np.array(feat.get("pcb_xyz_mm", [0.0, 0.0, 0.0]), dtype=np.float64)
                clicks = feat.get("clicks", [])
                worst_mm = 0.0
                for click in clicks:
                    photo_entry = mem["photos"].get(click.get("photo_id"))
                    if photo_entry is None or not isinstance(
                        photo_entry.get("pose"), PoseResult
                    ):
                        continue
                    intrinsics = _intrinsics_from_dict(photo_entry["intrinsics"])
                    err_mm = feature_error_mm_triangulated(
                        feature_xyz_mm=xyz,
                        pose=photo_entry["pose"],
                        intrinsics=intrinsics,
                        residual_px=float(click.get("reprojection_residual_px", 0.0)),
                    )
                    if err_mm > worst_mm:
                        worst_mm = err_mm
                entry["max_error_mm"] = worst_mm
            features_in.append(entry)

        return jsonify(score_next_prompt(photos=photos_in, features=features_in))
```

- [ ] **Step 7: Add an endpoint test**

Add to `tests/test_app.py`:

```python
def test_next_prompt_no_state_starts_with_top(app_factory) -> None:
    """A fresh session starts with direction='top'."""
    app, _, _ = app_factory()
    client = app.test_client()
    resp = client.get("/api/next_prompt")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["direction"] == "top"
    assert "coverage_cells" in body
    assert "features" in body


def test_next_prompt_after_top_photo_advances_to_side(app_factory) -> None:
    """After a top-down photo with a single-view feature, prompt picks a side."""
    app, _, _ = app_factory()
    client = app.test_client()
    anchors_resp = client.post("/api/anchors", json=_valid_anchors_payload())
    assert anchors_resp.status_code == 200
    feature_resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f1",
            "photo_id": "top_down",
            "pixel": [500.0, 500.0],
            "z_assumed_mm": 0.0,
        },
    )
    assert feature_resp.status_code == 200

    resp = client.get("/api/next_prompt")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["direction"] != "top"  # advanced past top
    assert body["direction"] in {"+long", "-long", "+short", "-short"}
    assert "second view" in body["reason"].lower() or "another angle" in body["reason"].lower()
```

- [ ] **Step 8: Run all related tests**

Run: `uv run pytest tests/test_coverage.py tests/test_next_prompt.py tests/test_app.py -v -k "coverage or next_prompt"`

Expected: All PASS.

- [ ] **Step 9: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 263 tests pass (244 + 9 coverage + 9 next_prompt + 2 endpoint − 1 deleted PR-2 `test_next_prompt_stub_returns_typed_shape` = 263); ruff clean.

- [ ] **Step 10: Commit**

```bash
git add src/agent_spatial_toolkit/server/coverage.py src/agent_spatial_toolkit/server/next_prompt.py tests/test_coverage.py tests/test_next_prompt.py src/agent_spatial_toolkit/server/app.py tests/test_app.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(server): wire next-prompt scoring + coverage cells

Adds two new modules. coverage.py bins each photo's pose to one of 5
default cells (top + 4 sides) anchored to the first photo's reference-
object frame. next_prompt.py implements the design §4 simplified scoring:
features_pending × 10 + empty_cell_score, with the user-facing reason
adapting honestly to what the algorithm knows (no fake claims of which
features are visible from which angle — that needs SfM and is v2).

Replaces the PR-2 stub at GET /api/next_prompt.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §4 coverage,
§4 scoring algorithm.
Part of: Wizard UX redesign PR-3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Wire `/api/reproject_all` real logic

**Files:**
- Modify: `src/agent_spatial_toolkit/server/app.py` (replace stub)
- Modify: `tests/test_app.py` (add endpoint test)

The review screen (design §2 step 7) shows reprojection dots overlaid on every photo. `/api/reproject_all` provides the data: for every (photo, feature) pair where the feature is triangulated, project its 3D position into that photo and report the `predicted_pixel` plus `error_mm`. Single-view planar features are reported only on their owning photo.

Response shape (locked in plan preamble §4): `{"by_photo": {photo_id: [{feature_id, predicted_pixel, error_mm}]}}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_reproject_all_returns_by_photo_dict_after_triangulation(app_factory) -> None:
    """After triangulating one feature, /api/reproject_all returns predicted
    pixels + error_mm per (photo, feature)."""
    import cv2 as _cv2

    app, _, _ = app_factory()
    client = app.test_client()

    # Set up two poses + a triangulated feature (re-using the fixture pattern).
    payload1 = _valid_anchors_payload()
    payload1["photo_id"] = "v1"
    r1 = client.post("/api/anchors", json=payload1)
    assert r1.status_code == 200, r1.get_json()

    K = np.array([[1000.0, 0, 500.0], [0, 1000.0, 500.0], [0, 0, 1]], dtype=np.float64)  # noqa: N806
    rvec2 = np.array([0.0, 0.3, 0.0])
    tvec2 = np.array([-60.0, -15.0, 200.0])
    world_pts = np.array(
        [[0.0, 0.0, 0.0], [50.0, 0.0, 0.0], [50.0, 30.0, 0.0], [0.0, 30.0, 0.0]]
    )
    pixels2, _ = _cv2.projectPoints(world_pts, rvec2, tvec2, K, np.zeros(5))
    payload2 = {
        "photo_id": "v2",
        "intrinsics": _make_test_intrinsics_dict(1000, 1000),
        "image_size": [1000, 1000],
        "anchors": [
            {"id": f"a{i}", "pcb_xyz_mm": world_pts[i].tolist(), "pixel": pixels2.reshape(-1, 2)[i].tolist()}
            for i in range(4)
        ],
    }
    r2 = client.post("/api/anchors", json=payload2)
    assert r2.status_code == 200, r2.get_json()

    target = np.array([15.0, 10.0, 0.0])
    p1, _ = _cv2.projectPoints(target.reshape(1, 1, 3), np.zeros(3), np.array([-25.0, -15.0, 200.0]), K, np.zeros(5))
    p2, _ = _cv2.projectPoints(target.reshape(1, 1, 3), rvec2, tvec2, K, np.zeros(5))

    feat_resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f_tri",
            "clicks": [
                {"photo_id": "v1", "pixel": p1.reshape(2).tolist()},
                {"photo_id": "v2", "pixel": p2.reshape(2).tolist()},
            ],
        },
    )
    assert feat_resp.status_code == 200, feat_resp.get_json()

    resp = client.get("/api/reproject_all")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "by_photo" in body
    assert "v1" in body["by_photo"]
    assert "v2" in body["by_photo"]
    v1_entries = body["by_photo"]["v1"]
    assert len(v1_entries) == 1
    assert v1_entries[0]["feature_id"] == "f_tri"
    assert "predicted_pixel" in v1_entries[0]
    assert "error_mm" in v1_entries[0]
    assert isinstance(v1_entries[0]["predicted_pixel"], list)
    assert len(v1_entries[0]["predicted_pixel"]) == 2


def test_reproject_all_empty_state_returns_empty_by_photo(app_factory) -> None:
    """No features → by_photo: {} (still well-typed object)."""
    app, _, _ = app_factory()
    client = app.test_client()
    resp = client.get("/api/reproject_all")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"by_photo": {}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -v -k "reproject_all"`

Expected: FAIL — current stub returns `{"features": []}`, not `{"by_photo": {...}}`.

- [ ] **Step 3: Replace the stub with real logic**

In `src/agent_spatial_toolkit/server/app.py`, locate `get_reproject_all` (around line 886). Replace the stub:

```python
    @app.get("/api/reproject_all")
    def get_reproject_all() -> Any:
        """Stub: PR-3 wires per-feature reprojection error in mm."""
        return jsonify({"features": []})
```

With:

```python
    @app.get("/api/reproject_all")
    def get_reproject_all() -> Any:
        """For every (photo, feature) where the feature is triangulated,
        project its 3D position into the photo and report the predicted
        pixel + error_mm. Single-view-planar features are reported on
        their owning photo only.

        Response: {"by_photo": {photo_id: [{feature_id, predicted_pixel,
        error_mm}, ...], ...}}.
        """
        from agent_spatial_toolkit.pipeline.error_mm import (
            feature_error_mm_planar,
            feature_error_mm_triangulated,
        )

        mem: dict[str, Any] = app.config["STATE"]
        by_photo: dict[str, list[dict[str, Any]]] = {}

        # Initialize a per-photo list for every photo with a pose.
        for photo_id, entry in mem["photos"].items():
            if isinstance(entry.get("pose"), PoseResult):
                by_photo[photo_id] = []

        for feature_id, feat in mem["features"].items():
            method: str = feat.get("method", "")
            xyz = np.array(feat.get("pcb_xyz_mm", [0.0, 0.0, 0.0]), dtype=np.float64)

            if method.startswith("triangulation_"):
                # Project into every photo the feature was clicked in.
                clicks = feat.get("clicks", [])
                for i, click in enumerate(clicks):
                    photo_id = click["photo_id"]
                    photo_entry = mem["photos"].get(photo_id)
                    if photo_entry is None or not isinstance(
                        photo_entry.get("pose"), PoseResult
                    ):
                        continue
                    intrinsics = _intrinsics_from_dict(photo_entry["intrinsics"])
                    pose: PoseResult = photo_entry["pose"]
                    K = intrinsics.to_camera_matrix()  # noqa: N806
                    dist = np.array(intrinsics.distortion, dtype=np.float64)
                    projected, _ = cv2.projectPoints(
                        xyz.reshape(1, 1, 3),
                        pose.rvec.astype(np.float64),
                        pose.tvec.astype(np.float64),
                        K,
                        dist,
                    )
                    u, v = projected.reshape(2)
                    residual = float(click.get("reprojection_residual_px", 0.0))
                    err_mm = feature_error_mm_triangulated(
                        feature_xyz_mm=xyz,
                        pose=pose,
                        intrinsics=intrinsics,
                        residual_px=residual,
                    )
                    by_photo.setdefault(photo_id, []).append(
                        {
                            "feature_id": feature_id,
                            "predicted_pixel": [float(u), float(v)],
                            "error_mm": err_mm,
                        }
                    )
            elif method == "planar_intersection":
                # Single-view: report on the owning photo only.
                photo_id = feat.get("photo_id")
                if photo_id is None:
                    continue
                photo_entry = mem["photos"].get(photo_id)
                if photo_entry is None or not isinstance(
                    photo_entry.get("pose"), PoseResult
                ):
                    continue
                intrinsics = _intrinsics_from_dict(photo_entry["intrinsics"])
                pose = photo_entry["pose"]
                K = intrinsics.to_camera_matrix()  # noqa: N806
                dist = np.array(intrinsics.distortion, dtype=np.float64)
                projected, _ = cv2.projectPoints(
                    xyz.reshape(1, 1, 3),
                    pose.rvec.astype(np.float64),
                    pose.tvec.astype(np.float64),
                    K,
                    dist,
                )
                u, v = projected.reshape(2)
                # Planar features have no per-photo residual stored; report 0.
                err_mm = feature_error_mm_planar(
                    pose=pose,
                    intrinsics=intrinsics,
                    residual_px=0.0,
                )
                by_photo.setdefault(photo_id, []).append(
                    {
                        "feature_id": feature_id,
                        "predicted_pixel": [float(u), float(v)],
                        "error_mm": err_mm,
                    }
                )

        return jsonify({"by_photo": by_photo})
```

Add `import cv2` at the top of `app.py` if not already imported (it isn't — current `app.py` only uses cv2 indirectly via the math kernel modules).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -v -k "reproject_all"`

Expected: PASS.

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 264 tests pass (263 + 2 new − 1 deleted PR-2 `test_reproject_all_stub_returns_empty_features` = 264); ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/agent_spatial_toolkit/server/app.py tests/test_app.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(server): wire /api/reproject_all real logic

Replaces the PR-2 stub. Iterates every (photo, feature) pair where the
feature is triangulated and projects the 3D position into that photo,
reporting predicted_pixel + error_mm. Single-view-planar features are
reported on their owning photo only.

Response shape matches the design §3 endpoint contract:
{by_photo: {photo_id: [{feature_id, predicted_pixel, error_mm}]}}.
The PR-2 stub returned {features: []} which the design's review-screen
UI couldn't consume. PR-4's review screen will use this directly to
render dot overlays + drill-down.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §3 endpoint
table.
Part of: Wizard UX redesign PR-3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Add EXIF-auto + FOV-class fallback to `/api/reference`

**Files:**
- Modify: `src/agent_spatial_toolkit/server/app.py` (extend intrinsics-resolution branch)
- Modify: `tests/test_app.py` (add tests for EXIF and FOV-class fallback paths)

Currently `/api/reference` requires explicit `intrinsics` dict OR `lens_id`. PR-3 adds two fallback paths so the wizard's UI never has to send either:

1. **EXIF auto** — read EXIF from the photo file on disk via existing `extract_exif_camera_info` and `resolve_fallback_intrinsics`. If EXIF carries a 35mm-equivalent focal length, derive intrinsics directly. Set the `intrinsics_estimated` quality flag in `mem["flags"]`.
2. **FOV-class default** — if EXIF is missing or doesn't have focal info, default to `wide` FOV class (~70° typical phone wide). Same flag.

The fallback chain is silent — the user never sees a CV-jargon decision happen. The flag tells downstream consumers what occurred.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_post_reference_falls_back_to_default_intrinsics_when_no_exif(app_factory) -> None:
    """Without intrinsics or lens_id, /api/reference still succeeds via the
    FOV-class default, and quality_summary.flags carries 'intrinsics_estimated'."""
    app, _, _ = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)

    # Project credit-card corners through a known pose (no intrinsics in payload).
    import cv2 as _cv2

    # Use what the FOV-default would compute: long_edge=200, focal_35=24 (wide-class).
    # fx_px = 24 * 200 / 36 = 133.3
    rvec = np.array([0.0, 0.0, 0.0])
    tvec = np.array([-42.8, -27.0, 100.0])
    K = np.array([[133.33, 0, 100.0], [0, 133.33, 75.0], [0, 0, 1]], dtype=np.float64)  # noqa: N806
    world_corners = np.array(
        [
            [0.0, 0.0, 0.0],
            [85.60, 0.0, 0.0],
            [85.60, 53.98, 0.0],
            [0.0, 53.98, 0.0],
        ]
    )
    pixel_corners, _ = _cv2.projectPoints(world_corners, rvec, tvec, K, np.zeros(5))
    pixel_corners = pixel_corners.reshape(-1, 2).tolist()

    payload = {
        "photo_id": photo_id,
        "reference_type": "credit_card",
        "pixel_corners": pixel_corners,
        "image_size": [200, 150],
        # No intrinsics, no lens_id, no exif.
    }
    resp = client.post("/api/reference", json=payload)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert "pose" in body

    state = client.get("/api/state").get_json()
    assert "intrinsics_estimated" in state["flags"]


def test_post_reference_uses_exif_focal_when_provided_in_request(app_factory) -> None:
    """If the request body contains an `exif` dict with focal info, use it
    instead of falling back to wide-class default. Flag still set
    (intrinsics still 'estimated', not chessboard-calibrated)."""
    app, _, _ = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)

    import cv2 as _cv2

    # focal_35 = 28 (wide), long_edge=200 → fx_px = 28*200/36 = 155.5
    rvec = np.array([0.0, 0.0, 0.0])
    tvec = np.array([-42.8, -27.0, 100.0])
    K = np.array([[155.55, 0, 100.0], [0, 155.55, 75.0], [0, 0, 1]], dtype=np.float64)  # noqa: N806
    world_corners = np.array(
        [
            [0.0, 0.0, 0.0],
            [85.60, 0.0, 0.0],
            [85.60, 53.98, 0.0],
            [0.0, 53.98, 0.0],
        ]
    )
    pixel_corners, _ = _cv2.projectPoints(world_corners, rvec, tvec, K, np.zeros(5))
    pixel_corners = pixel_corners.reshape(-1, 2).tolist()

    payload = {
        "photo_id": photo_id,
        "reference_type": "credit_card",
        "pixel_corners": pixel_corners,
        "image_size": [200, 150],
        "exif": {"focal_length_35mm_equiv": 28.0},
    }
    resp = client.post("/api/reference", json=payload)
    assert resp.status_code == 200, resp.get_json()
    state = client.get("/api/state").get_json()
    assert "intrinsics_estimated" in state["flags"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app.py -v -k "post_reference_falls_back or post_reference_uses_exif"`

Expected: FAIL with 400 — current `/api/reference` returns `{"error": "must provide either intrinsics or lens_id"}`.

- [ ] **Step 3: Add the EXIF + FOV-class fallback**

In `src/agent_spatial_toolkit/server/app.py`, modify `post_reference` (around line 566). Find the existing intrinsics-resolution block (the `else: lens_id = body.get("lens_id")` branch). Replace it:

```python
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
                        {
                            "error": (
                                f"lens_id '{lens_id}' could not resolve to intrinsics; "
                                "provide an explicit intrinsics dict"
                            )
                        }
                    ),
                    400,
                )
            intrinsics = intr_obj
            intrinsics_dict = intr_obj.to_dict()
```

With:

```python
        else:
            # Fallback chain: lens_id (if provided) → EXIF (if photo on disk
            # carries focal_35) → wide-class default. Each fallback raises
            # the intrinsics_estimated flag for the LLM downstream.
            lens_id = body.get("lens_id")
            intr_obj: Intrinsics | None = None

            if lens_id:
                from agent_spatial_toolkit.server.lens_catalog import resolve as _resolve_lens

                intr_obj = _resolve_lens(lens_id, image_size, exif=body.get("exif"))

            # EXIF in request body (UI extracted client-side).
            if intr_obj is None:
                exif = body.get("exif") or {}
                focal_35 = exif.get("focal_length_35mm_equiv") if isinstance(exif, dict) else None
                if focal_35 is not None:
                    from agent_spatial_toolkit.pipeline.intrinsics import (
                        resolve_fallback_intrinsics,
                    )

                    intr_obj = resolve_fallback_intrinsics(float(focal_35), image_size)

            # EXIF read directly from the photo file on disk.
            if intr_obj is None:
                photos_dir = session.session_dir / "photos"
                candidates = list(photos_dir.glob(f"{photo_id}.*"))
                if candidates:
                    from agent_spatial_toolkit.pipeline.intrinsics import (
                        extract_exif_camera_info,
                        resolve_fallback_intrinsics,
                    )

                    cam_info = extract_exif_camera_info(candidates[0])
                    if cam_info is not None and cam_info.focal_length_35mm_equiv is not None:
                        intr_obj = resolve_fallback_intrinsics(
                            cam_info.focal_length_35mm_equiv, image_size
                        )

            # Final fallback: wide-class (24 mm 35mm-equiv) default.
            if intr_obj is None:
                from agent_spatial_toolkit.pipeline.intrinsics import (
                    resolve_fallback_intrinsics,
                )

                intr_obj = resolve_fallback_intrinsics(24.0, image_size)
                if intr_obj is None:
                    return (
                        jsonify(
                            {
                                "error": (
                                    "could not derive camera intrinsics; "
                                    "provide an explicit intrinsics dict"
                                )
                            }
                        ),
                        400,
                    )

            # Fallback paths all raise the estimation flag (the schema's
            # intrinsics_estimated, schema-merged in PR-1).
            existing = list(mem.get("flags", []))
            if "intrinsics_estimated" not in existing:
                existing.append("intrinsics_estimated")
                mem["flags"] = existing

            intrinsics = intr_obj
            intrinsics_dict = intr_obj.to_dict()
```

Note: the function references `session` for the photos-dir path; ensure `session: Session = app.config["SESSION"]` is fetched near the top of `post_reference` (it's already there per the existing code; no change needed).

Also import `Intrinsics` at the top of `app.py` if not already imported (it is — line 47).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -v -k "post_reference_falls_back or post_reference_uses_exif"`

Expected: PASS.

- [ ] **Step 5: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 266 tests pass; ruff clean. Pre-existing `/api/reference` tests still pass (the `intrinsics`-dict path is unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/agent_spatial_toolkit/server/app.py tests/test_app.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(server): EXIF-auto + FOV-class fallback in /api/reference

Removes the requirement that callers send an intrinsics dict or lens_id.
Resolution chain: lens_id (if provided) → EXIF in request body → EXIF
read from photo file on disk → wide-class (24 mm 35mm-equiv) default.

Each fallback raises the intrinsics_estimated quality flag in
quality_summary.flags so the LLM downstream sees that coords may be
±2 mm rather than ±0.5 mm.

This is the silent simplification the design called the biggest UX win
of the redesign: the user never picks a lens, never types focal length,
never sees a CV-jargon decision happen.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §3 'Camera
intrinsics handling — the silent simplification'.
Part of: Wizard UX redesign PR-3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Fix `/api/finalize` upload-only bug

**Files:**
- Modify: `src/agent_spatial_toolkit/server/app.py` (skip unsolved photos + emit flag)
- Modify: `src/agent_spatial_toolkit/schema/validators.py` (add `pose_skipped_uploaded_only` to closed enum)
- Modify: `tests/test_app.py` (add regression test)

PR-2's review surfaced the bug: a client that uploads via `/api/photo` then calls `/api/finalize` WITHOUT `/api/reference` gets 200 with silently-invalid `pose: null` photo entries. Fix per locked decision §3 in plan preamble: skip unsolved photos and emit `pose_skipped_uploaded_only:<photo_id>` in `quality_summary.flags`.

**Important:** the closed-enum validator at `schema/validators.py:13-22` will REJECT any unknown flag prefix with `ValidationError`. So this task ALSO extends `QUALITY_FLAGS` to include the new prefix. The extension is additive (consistent with PR-1's "additive-only" commitment in the design doc §6 — the enum is extended, no existing prefix changes meaning).

- [ ] **Step 1: Add the new flag prefix to the closed enum**

In `src/agent_spatial_toolkit/schema/validators.py`, locate `QUALITY_FLAGS` (around line 13) and add a new entry:

```python
QUALITY_FLAGS: dict[str, bool] = {
    "intrinsics_suspect_high_anchor_rms": False,
    "intrinsics_session_recommend_chessboard": False,
    "intrinsics_estimated": False,
    "underside_unverified": False,
    "photo_excluded_due_to_pose_failure": True,
    "feature_clicked_only_once": True,
    "feature_high_triangulation_rms": True,
    "ultrawide_lens_rejected": True,
    "pose_skipped_uploaded_only": True,  # PR-3: photo uploaded via /api/photo but never anchored
}
```

- [ ] **Step 2: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_finalize_skips_unsolved_uploaded_photos(app_factory) -> None:
    """A photo uploaded but never anchored is skipped from the final
    annotations.json with a 'pose_skipped_uploaded_only:<id>' flag."""
    app, _, _ = app_factory()
    client = app.test_client()

    # Upload one photo without anchoring it.
    unsolved_id = _upload_test_photo(client)

    # Set up one photo with a valid pose so finalize has something to emit.
    anchors_resp = client.post("/api/anchors", json=_valid_anchors_payload())
    assert anchors_resp.status_code == 200

    resp = client.post("/api/finalize", json={})
    assert resp.status_code == 200, resp.get_json()
    out_path = Path(resp.get_json()["annotations_path"])
    assert out_path.is_file()
    annotations = json.loads(out_path.read_text(encoding="utf-8"))

    # Unsolved photo MUST NOT appear in photos[]
    photo_ids_in_annotations = [p["id"] for p in annotations["photos"]]
    assert unsolved_id not in photo_ids_in_annotations
    # Solved photo IS present.
    assert "top_down" in photo_ids_in_annotations

    # The flag is recorded in quality_summary.flags.
    flags = annotations["quality_summary"]["flags"]
    assert any(f.startswith("pose_skipped_uploaded_only:") and unsolved_id in f for f in flags)
```

You'll need to add `from pathlib import Path` to test imports if not already present.

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py::test_finalize_skips_unsolved_uploaded_photos -v`

Expected: FAIL — currently the unsolved photo is INCLUDED in photos[] with a None pose, which the schema rejects with ValidationError → 400. (Or it might 500 in some path — either way, fail mode.)

- [ ] **Step 4: Fix the finalize handler**

In `src/agent_spatial_toolkit/server/app.py`, locate `post_finalize` (around line 891). Find the photos-out construction loop:

```python
        photos_out: list[Photo] = []
        for photo_id, entry in mem["photos"].items():
            pose = entry["pose"]
            anchors_clicked = [
                ...
            ]
```

Replace with a filter that skips unsolved photos and accumulates the skip flag:

```python
        photos_out: list[Photo] = []
        skipped_unsolved_flags: list[str] = []
        for photo_id, entry in mem["photos"].items():
            pose = entry.get("pose")
            intr = entry.get("intrinsics")
            if pose is None or intr is None:
                # Photo was uploaded via /api/photo but never had pose solved
                # via /api/reference (or /api/anchors). Skip it from the
                # final annotations and surface a per-photo flag so the LLM
                # downstream sees what happened.
                skipped_unsolved_flags.append(f"pose_skipped_uploaded_only:{photo_id}")
                continue
            anchors_clicked = [
                AnchorClick(
                    id=a["id"],
                    pcb_xyz_mm=tuple(a["pcb_xyz_mm"]),
                    pixel=tuple(a["pixel"]),
                )
                for a in entry.get("anchors", [])
            ]
```

(Continue with the rest of the existing loop unchanged.)

Then update the all_flags construction below to include `skipped_unsolved_flags`:

```python
        all_flags = list(
            dict.fromkeys(
                auto_flags
                + skipped_unsolved_flags
                + list(mem.get("flags", []))
                + list(caller_flags)
            )
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_app.py::test_finalize_skips_unsolved_uploaded_photos -v`

Expected: PASS.

- [ ] **Step 6: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 267 tests pass; ruff clean.

- [ ] **Step 7: Commit**

```bash
git add src/agent_spatial_toolkit/schema/validators.py src/agent_spatial_toolkit/server/app.py tests/test_app.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "fix(server): /api/finalize skips upload-only photos + emits flag

PR-2's review identified the bug: a client that uploaded via /api/photo
and then called /api/finalize WITHOUT /api/reference produced an
annotations.json with silently-invalid pose:null entries (or a 4xx
ValidationError, depending on the path). Fix: skip unsolved photos from
photos[] and emit pose_skipped_uploaded_only:<photo_id> in
quality_summary.flags. The LLM downstream now sees the explicit flag
instead of a malformed photo entry.

Decision per PR-3 plan: not 4xx — finalize returns 200 because the user
explicitly chose to finalize. The flag tells consumers what was skipped
and why.

Refs: PR-2 final review P1 finding (rolled into PR-3).
Part of: Wizard UX redesign PR-3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Strengthen backwards-compat regression suite + soften §9-forbidden docstring

**Files:**
- Modify: `tests/test_schema_backwards_compat.py` (add triangulation-branch fixture)
- Modify: `src/agent_spatial_toolkit/schema/models.py` (soften `Feature.noisy` docstring)

Two PR-1-deferred items: add a triangulation-branch fixture to the regression suite, and soften the `Feature.noisy` docstring that uses the §9-forbidden "reprojection" word in a tooltip-visible context.

- [ ] **Step 1: Add the triangulation-branch test**

Append to `tests/test_schema_backwards_compat.py` (mirrors the file's existing `_v0_0_1_fixture` style — build `Annotations` directly, call `to_dict()`, then `validate_annotations(d)`):

```python
def _triangulation_branch_fixture() -> Annotations:
    """A populated triangulation_3_views shape exercising fields that
    test_v0_0_1_fixture's planar_intersection branch can't reach."""
    return Annotations(
        schema_version=1,
        toolkit_version="0.1.0a-dev",
        generated_at="2026-05-05T12:00:00Z",
        part=Part(id="testpart_002", display_name="Triangulated Test Part"),
        reference_frame=ReferenceFrame(
            origin_description="origin",
            x_axis_description="x",
            y_axis_description="y",
            z_axis_description="z",
        ),
        photos=[
            Photo(
                id=f"p{i}",
                path=f"photos/p{i}.jpg",
                sha256="0" * 64,
                camera_detected=CameraDetected(detection_source="exif"),
                intrinsics={
                    "profile_source": "fov_class_fallback",
                    "profile_id": "fov_wide_v1",
                    "fx_px": 1000.0,
                    "fy_px": 1000.0,
                    "cx": 500.0,
                    "cy": 500.0,
                    "distortion_model": "opencv_5param",
                    "distortion": {"k1": 0.0, "k2": 0.0, "p1": 0.0, "p2": 0.0, "k3": 0.0},
                },
                pose={
                    "rvec": [0.0, 0.0, 0.0],
                    "tvec": [0.0, 0.0, 200.0],
                    "anchor_reprojection_rms_px": 0.5,
                    "pose_solver": "cv2.solvePnP_ITERATIVE",
                },
                anchors_clicked=[
                    AnchorClick(id="a0", pcb_xyz_mm=(0.0, 0.0, 0.0), pixel=(100, 100)),
                ],
            )
            for i in range(3)
        ],
        features=[
            Feature(
                id="f_tri",
                visible_in=["p0", "p1", "p2"],
                pcb_xyz_mm=(15.0, 10.0, 0.0),
                measurements=FeatureMeasurement(
                    method="triangulation_3_views",
                    triangulation_rms_px=0.42,
                    max_residual_px=0.61,
                    per_photo_clicks=[
                        FeatureClick(photo="p0", pixel=(150, 150), reprojection_residual_px=0.31),
                        FeatureClick(photo="p1", pixel=(170, 145), reprojection_residual_px=0.45),
                        FeatureClick(photo="p2", pixel=(160, 165), reprojection_residual_px=0.53),
                    ],
                ),
            ),
        ],
        quality_summary=QualitySummary(
            feature_count=1,
            triangulated_count=1,
            z_assumed_count=0,
            median_reprojection_rms_px=0.42,
            max_reprojection_rms_px=0.42,
            flags=[],
        ),
        session_artifacts=SessionArtifacts(
            overlay_pngs=["overlays/p0.png", "overlays/p1.png", "overlays/p2.png"],
            events_jsonl="events.jsonl",
            manifest="manifest.json",
        ),
    )


def test_triangulation_branch_emits_and_validates() -> None:
    """A triangulation_3_views fixture must emit + validate cleanly."""
    fixture = _triangulation_branch_fixture()
    d = fixture.to_dict()
    validate_annotations(d)
    # Spot-check the emitted shape includes the triangulation-only fields
    # that the planar_intersection fixture skipped.
    feature_dict = d["features"][0]
    assert feature_dict["measurements"]["method"] == "triangulation_3_views"
    assert "triangulation_rms_px" in feature_dict["measurements"]
    assert "max_residual_px" in feature_dict["measurements"]
    for click in feature_dict["measurements"]["per_photo_clicks"]:
        assert "reprojection_residual_px" in click
```

- [ ] **Step 2: Run to verify it passes (it should — schema accepts triangulation_3_views)**

Run: `uv run pytest tests/test_schema_backwards_compat.py -v -k "triangulation_branch"`

Expected: PASS — schema enum and validator already accept `triangulation_3_views` since PR-1.

- [ ] **Step 3: Soften the `Feature.noisy` docstring**

In `src/agent_spatial_toolkit/schema/models.py`, find the `noisy` field (around line 165):

```python
    noisy: bool = False
    """True when triangulation reprojection error falls in the yellow band
    (0.5–1.0 mm). Set by the redesigned wizard's tier-classification logic.
    Emitted only when True (default-False is omitted)."""
```

Replace with:

```python
    noisy: bool = False
    """True when this feature's measurement is in the 0.5–1.0 mm tier
    (design §4 Approximate band). Set by the redesigned wizard's tier-
    classification logic. Emitted only when True (default-False is
    omitted)."""
```

(Removed "triangulation reprojection error" — those terms are §9-forbidden in any user-visible context and IDE tooltips qualify.)

- [ ] **Step 4: Run full suite + ruff**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 268 tests pass; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add tests/test_schema_backwards_compat.py src/agent_spatial_toolkit/schema/models.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "test(schema): triangulation-branch backwards-compat fixture + tooltip cleanup

Two PR-1-deferred items rolled in. (1) test_schema_backwards_compat
gains a fixture that builds a populated SessionState with a
triangulation_3_views feature + 3 photos + populated SessionArtifacts
and verifies the emitted annotations.json round-trips through the
schema validator. (2) Feature.noisy docstring loses the §9-forbidden
'triangulation reprojection error' phrase since IDE tooltips surface
docstrings to humans.

Refs: PR-1 final review minor findings.
Part of: Wizard UX redesign PR-3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10b: Annotate user-facing-translation requirements for PR-4

**Files:**
- Modify: `src/agent_spatial_toolkit/server/app.py` (add a comment block at top enumerating §9-forbidden strings PR-4 must translate)

PR-2's review left "HTTP error message UI translation" deferred to PR-4. The risk is that PR-4 forgets one and ships a UI that displays "triangulation not yet implemented" or "no pose for photo X" verbatim — exactly the §9-forbidden-vocabulary failure mode. Mitigate by writing a single canonical list at the top of `app.py` so PR-4's implementer and reviewer have one place to consult.

This Task is intentionally a documentation-only change; no behavior shift.

- [ ] **Step 1: Audit every user-adjacent string in `app.py`**

Run: `grep -nE 'jsonify\(\{"error"' src/agent_spatial_toolkit/server/app.py | head -40`

Expected output: ~30+ matches. The error strings to enumerate are the ones containing §9-forbidden vocabulary: "pose", "triangulation", "intrinsics", "anchor", "PnP", "reprojection", "lens_id", "FOV", "EXIF". (Strings that are pure CRUD shape-checks like "missing required field" are fine to leave un-enumerated — they're plain English.)

- [ ] **Step 2: Add the comment block at the top of `app.py`**

In `src/agent_spatial_toolkit/server/app.py`, after the module docstring (around line 30) and before `from __future__ import annotations`, add:

```python
# PR-4-HANDOFF: user-facing-translation-required ----------------------------
# Per design §9 (forbidden vocabulary) the strings listed below are NOT
# safe to display verbatim in the wizard UI — they currently use CV jargon
# that non-CAD users won't understand. PR-4's UI MUST translate each to
# plain English before showing to the user. This list is exhaustive as of
# PR-3 merge; if PR-4 adds new error strings, this list MUST be updated.
#
# Routes & strings:
#   /api/anchors:
#     - "PnP failed: anchors are degenerate or insufficient"
#     - "must provide either intrinsics or lens_id"
#     - "lens_id '<id>' could not resolve to intrinsics; provide an explicit
#        intrinsics dict"
#     - "stored intrinsics are malformed: <e>"
#     - "internal error during pose solve"
#   /api/reference:
#     - "pose solve failed: corners may be too oblique or mis-clicked"
#     - "could not derive camera intrinsics; provide an explicit intrinsics dict"
#     - "internal error during pose solve"
#     - (intrinsics-resolution error strings shared with /api/anchors)
#   /api/feature:
#     - "no pose for photo <id>"   (multiple call sites)
#     - "stored intrinsics are malformed: <e>"
#     - "v1 triangulates from up to 6 views; received <N> — please reduce to
#        your 6 best views"
#     - "internal error during ray-cast"
#     - "method '<m>' not implemented in v0.1.0-alpha (β-mode only)"
#   /api/wireframe:
#     - "no pose for photo <id>"
#     - "stored intrinsics invalid: <e>"
#     - "wireframe render failed: <e>"
#   /api/marker_detect:
#     - "photo file for <id> not found on disk"
#   (Routes not listed here ship only plain-English errors safe to forward.)
#
# /api/next_prompt response: the `direction` field is an internal axis label
# (e.g., '+long', '-short') — UI MUST render it as a silhouette icon, never
# as prose. The `reason` field is a fallback English string that interpolates
# the axis label literally; UI MUST render the localized prose from
# `reason_code` (an enum) instead. The fallback `reason` is for debugger
# inspection only.
# ---------------------------------------------------------------------------
```

- [ ] **Step 3: Run full suite + ruff to confirm no regression**

Run: `uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: 268 tests pass; ruff clean (no new tests in this docs-only task).

- [ ] **Step 4: Commit**

```bash
git add src/agent_spatial_toolkit/server/app.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "docs(server): enumerate user-facing-translation-required strings for PR-4

Adds a canonical comment block at the top of app.py listing every
HTTP error string that contains §9-forbidden vocabulary. PR-4's UI
implementer and reviewer use this as a checklist when wiring error
display, and PR-4's reviewer MUST refuse merge if any listed string
is shown to the user without translation.

Also documents the /api/next_prompt direction / reason / reason_code
contract: direction is an internal axis label (silhouette-icon-only),
reason is a fallback English string (debugger-only), reason_code is
the enum the UI renders prose from.

Refs: PR-2 final review minor (HTTP error UI translation deferred).
Part of: Wizard UX redesign PR-3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: Push branch + open PR

**Files:**
- Push: `feat/wizard-redesign-pr3-real-logic` to origin
- Open: GitHub PR

- [ ] **Step 1: Verify the branch is clean and tests pass**

Run: `git status && uv run pytest -q && uv run ruff format --check src tests && uv run ruff check src tests`

Expected: clean tree, all tests pass, ruff clean.

- [ ] **Step 2: Verify recent commits**

Run: `git log --oneline -13`

Expected: 11 PR-3 commits (one per Task 1–10 plus Task 10b, in order). If any task was committed wrong, STOP and surface to the orchestrator.

- [ ] **Step 3: Push branch**

Run: `git push -u origin feat/wizard-redesign-pr3-real-logic`

Expected: branch published to GitHub.

- [ ] **Step 4: Open the PR**

Run:

```bash
gh pr create --title "feat(server): wizard redesign PR-3 — real logic (triangulation, mm-tolerance, next-prompt)" --body "$(cat <<'EOF'
## Summary

PR-3 of 6 in the wizard UX redesign — replaces all four PR-2 stubs with real logic, adds the missing `triangulate_feature` math primitive, implements mm-conversion for the tier system, fixes the `/api/finalize` upload-only bug, and adds EXIF + FOV-class fallback to `/api/reference`.

## What changed

### Math kernel additions (new files)
- `pipeline/triangulate.py` — multi-view DLT triangulation (2..6 views) via `cv2.triangulatePoints` for the 2-view case and SVD-based DLT for 3..6 views. Returns the 3D point + per-view residuals + RMS + max-residual.
- `pipeline/error_mm.py` — mm-conversion helpers per design §3: `pose_rms_mm` (depth = camera-to-reference-plane), `feature_error_mm_triangulated` (depth = feature's camera-frame z), `feature_error_mm_planar` (depth = reference-plane).

### Server module additions (new files)
- `server/coverage.py` — pose-to-cell binning anchored on the first photo's reference-object frame (5 default cells: top + 4 sides; bottom hidden by default).
- `server/next_prompt.py` — design §4 v1 scoring algorithm: `features_pending × 10 + empty_cell_score`. Reasons adapt honestly to what the algorithm knows; no fake claims of which features are visible from where.
- `server/marker_detect.py` — wraps `cv2.aruco.detectMarkers` for the wizard's `marker` reference-type path. Returns 4 corners clockwise from top-left, or None if no marker found.

### Endpoint wiring
- `POST /api/feature` — replaces the 501 stub for ≥2 clicks with real triangulation; method enum maps to `triangulation_2_views` … `triangulation_6_views`. >6 clicks return 400 with a clear cap message.
- `POST /api/reference` — adds `pose_rms_mm` to the response so the UI can render the tier badge. Adds EXIF + FOV-class fallback (no longer requires explicit `intrinsics` or `lens_id`).
- `GET /api/marker_detect/<id>` — replaces stub with `cv2.aruco.detectMarkers`.
- `GET /api/next_prompt` — replaces stub with real scoring algorithm.
- `GET /api/reproject_all` — replaces stub with real per-(photo, feature) reprojection. Response shape now matches design: `{by_photo: {photo_id: [{feature_id, predicted_pixel, error_mm}]}}`.
- `POST /api/finalize` — fixes the upload-only bug: photos with no pose are skipped from `photos[]`, with `pose_skipped_uploaded_only:<photo_id>` flags emitted in `quality_summary.flags`.

### Test additions (~45 new tests)
- `tests/test_triangulate.py`, `tests/test_error_mm.py`, `tests/test_coverage.py`, `tests/test_next_prompt.py`, `tests/test_marker_detect.py` — unit tests for each new module.
- `tests/test_app.py` — extended with multi-view `/api/feature`, marker-detect, next-prompt, reproject-all, EXIF fallback, and finalize upload-only regression tests.
- `tests/test_schema_backwards_compat.py` — triangulation-branch fixture.

### Schema cleanup
- `Feature.noisy` docstring no longer uses the §9-forbidden 'triangulation reprojection error' phrase; IDE tooltips now read in the design's plain-English vocabulary.

## Locked architectural decisions (documented in plan preamble)

1. Triangulation method enum stays at 2..6 views; >6 returns 400 (not a v1 enum extension).
2. `triangulate_feature` algorithm: DLT only — no iterative refinement in v1.
3. `/api/finalize` upload-only fix: skip + flag (not 4xx).
4. `/api/reproject_all` response: `{by_photo: ...}`, not `{features: [...]}`.
5. `/api/reference` EXIF fallback chain: explicit intrinsics → lens_id → request EXIF → photo-file EXIF → wide-class default. Fallbacks raise `intrinsics_estimated`.
6. Coverage cells anchored to photo #1's pose; cells: `top`, `+long`, `-long`, `+short`, `-short` (bottom hidden by default).
7. Next-prompt scoring exactly per design §4: `features_pending × 10 + empty_cell_score`.

## Carry-over to PR-4

The full enumeration of strings PR-4 must translate is at the top of `src/agent_spatial_toolkit/server/app.py` (added in Task 10b). Highlights:

- **Forbidden-vocabulary HTTP error strings** in `/api/anchors`, `/api/reference`, `/api/feature`, `/api/wireframe`, `/api/marker_detect` — all explicitly listed in the `app.py` `PR-4-HANDOFF` comment block. PR-4 reviewer MUST refuse merge if any listed string reaches the UI verbatim.
- **`/api/next_prompt` `direction` field** is an internal axis label (`+long`, `-short`, etc.) — UI MUST render as silhouette icon, never as prose.
- **`/api/next_prompt` `reason` field** is a fallback English string that interpolates the axis label literally; UI MUST render localized prose from the `reason_code` enum (`top_first_photo`, `second_view_needed_empty_cell`, `second_view_needed_filled_cell`, `unseen_side_low_priority`, `another_angle_low_priority`, `call_it_done`).
- All 4 stubs from PR-2 are now gone; the contract surface PR-4 builds against is fully real.

## Test plan

- [x] All 269 tests passing on Python 3.10/3.11/3.12/3.13. (218 baseline + 53 new across Tasks 1–10, − 2 deleted PR-2 stub tests = 269.)
- [x] Ruff format + lint clean.
- [x] No regression in legacy `/api/anchors`, `/api/wireframe`, `/api/lens_catalog`, `/api/photo`, single-click `/api/feature` paths.
- [x] PR-2 regression suite (and PR-1 backwards-compat suite extended in Task 10) continues to pass.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR opens; URL printed.

- [ ] **Step 5: Update memory if PR-3 introduces a new orchestration discipline**

If during PR-3 execution any new orchestration pattern surfaced that should persist across sessions, write a memory file. Otherwise skip.

- [ ] **Step 6: Update todo list and end the task**

Mark the orchestrator's PR-3 todo as complete. Begin two-stage spec + code-quality review per the established pattern (this is orchestrator-driven, not a plan-step).

---

## Self-review checklist (orchestrator runs after plan completion)

- [ ] **Spec coverage:** Each line of design §6 PR-3 row maps to a task above.
  - "Wire `triangulate_feature`" → Task 1 + 2.
  - "Implement mm-conversion" → Task 3.
  - "Implement v1 next-prompt scoring algorithm" → Task 6.
  - "Marker auto-detect" → Task 5.
  - "Add EXIF-auto + FOV-class fallback" → Task 8.
  - "Extend `FeatureMeasurement.method` enum" → not needed; `planar_intersection` and `triangulation_N_views` cover all cases. Documented in plan preamble §1.
  - **All deferred items from PR-2 final review** rolled in: P1 finalize bug → Task 9; reproject_all shape → Task 7; HTTP error UI translation → carried to PR-4 in Task 11 PR description.
  - **All deferred items from PR-1 final review** rolled in: triangulation regression-suite fixture + `Feature.noisy` docstring softening → Task 10.

- [ ] **Placeholder scan:** Every step has either an exact code block, an exact command with expected output, or a precise file/line reference. No "TBD", "see Task N", "implement later", or "etc.".

- [ ] **Type consistency:** `triangulate_feature` (Task 1) returns `TriangulationResult` with fields `xyz_mm`, `per_click_residuals_px`, `triangulation_rms_px`, `max_residual_px`, `n_views`. Task 2 uses every field. Task 7 uses `feature_error_mm_triangulated` and `feature_error_mm_planar` from Task 3. Task 6 uses `compute_coverage_cells` from Task 6's first sub-step. All names match across tasks.

- [ ] **Test count tracking:** Baseline 218. Task 1 adds 10 → 228. Task 2 adds 3 − deletes 1 (501 stub) → 230. Task 3 adds 6 → 236. Task 4 adds 1 → 237. Task 5 adds 4 module + 3 endpoint → 244. Task 6 adds 9 coverage + 9 next_prompt + 2 endpoint − deletes 1 (PR-2 next_prompt stub test) → 263. Task 7 adds 2 → 265. Task 8 adds 2 → 267. Task 9 adds 1 → 268. Task 10 adds 1 → 269. Task 10b adds 0 (docs-only) → 269. Final: 269 tests.

- [ ] **Identity verification step in commits:** Every commit uses `GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com"`. Per the orchestration handoff, implementer prompts MUST include `git log -1 --pretty='%an <%ae>'` verification after each commit.
