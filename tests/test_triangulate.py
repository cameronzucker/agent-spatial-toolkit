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
    """If we perturb one pixel by 5 px, the residual on that view should reflect that.

    Note: with only 2 views the DLT is exactly determined and absorbs the
    perturbation by shifting the 3D point — residuals stay near zero. Use
    3 views (1 perturbed, 2 clean) so the over-constrained system forces
    the residual to grow on the perturbed view.
    """
    intr = _make_intrinsics()
    world_xyz = np.array([50.0, 30.0, 10.0])
    rvec1 = np.array([0.0, 0.0, 0.0])
    tvec1 = np.array([-25.0, -15.0, 200.0])
    rvec2 = np.array([0.0, 0.3, 0.0])
    tvec2 = np.array([-60.0, -15.0, 200.0])
    rvec3 = np.array([0.0, -0.3, 0.0])
    tvec3 = np.array([10.0, -15.0, 200.0])

    pose1 = PoseResult(rvec1, tvec1, 0.5, False, "cv2.solvePnP_ITERATIVE")
    pose2 = PoseResult(rvec2, tvec2, 0.5, False, "cv2.solvePnP_ITERATIVE")
    pose3 = PoseResult(rvec3, tvec3, 0.5, False, "cv2.solvePnP_ITERATIVE")
    p1 = _project_to_view(world_xyz, rvec1, tvec1, intr)
    p2 = _project_to_view(world_xyz, rvec2, tvec2, intr)
    p3 = _project_to_view(world_xyz, rvec3, tvec3, intr)
    # Perturb view 2's pixel by (+5, 0).
    perturbed_p2 = (p2[0] + 5.0, p2[1])

    views = [(pose1, intr, p1), (pose2, intr, perturbed_p2), (pose3, intr, p3)]
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
