"""Tests for pipeline/pose.py — PnP wrapper + anchor validation."""

import cv2
import numpy as np
import pytest

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import (
    ANCHOR_RMS_THRESHOLD_NORMALIZED_PX,
    PoseResult,
    PoseSolveError,
    solve_pnp,
)


def _make_test_intrinsics() -> Intrinsics:
    """Synthetic intrinsics for a 1000×1000 image, fx=fy=1000, no distortion."""
    return Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="test",
        fx_px=1000.0,
        fy_px=1000.0,
        cx=500.0,
        cy=500.0,
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )


def _project_synthetic(
    world_pts: np.ndarray, rvec: np.ndarray, tvec: np.ndarray, intr: Intrinsics
) -> np.ndarray:
    """Project 3D world points to 2D pixels using the given pose + intrinsics."""
    K = np.array(  # noqa: N806 — canonical CV name for camera matrix
        [[intr.fx_px, 0, intr.cx], [0, intr.fy_px, intr.cy], [0, 0, 1]], dtype=np.float64
    )
    dist = np.array(intr.distortion, dtype=np.float64)
    pts2d, _ = cv2.projectPoints(world_pts.astype(np.float64), rvec, tvec, K, dist)
    return pts2d.reshape(-1, 2)


def test_solve_pnp_recovers_known_pose_with_4_anchors() -> None:
    """Ground-truth round trip: project known points, solve PnP, recover pose to within tolerance."""
    intr = _make_test_intrinsics()
    # Place a 50×30 mm rectangle at Z=0
    world_pts = np.array(
        [
            [0.0, 0.0, 0.0],
            [50.0, 0.0, 0.0],
            [50.0, 30.0, 0.0],
            [0.0, 30.0, 0.0],
        ]
    )
    # Camera looking down at the rectangle from 200 mm above
    true_rvec = np.array([0.0, 0.0, 0.0])
    true_tvec = np.array([-25.0, -15.0, 200.0])  # offsets so rectangle is centered
    pixels = _project_synthetic(world_pts, true_rvec, true_tvec, intr)

    result = solve_pnp(
        world_points=world_pts,
        pixel_points=pixels,
        intrinsics=intr,
        image_size=(1000, 1000),
    )

    assert isinstance(result, PoseResult)
    assert result.anchor_reprojection_rms_px < 0.5  # should be ~zero, allow noise
    # Recovered tvec should be very close to true_tvec
    assert np.allclose(result.tvec, true_tvec, atol=1e-6)
    assert np.allclose(result.rvec, true_rvec, atol=1e-6)


def test_solve_pnp_with_3_collinear_points_raises() -> None:
    """3 collinear points have no PnP solution (singular configuration)."""
    intr = _make_test_intrinsics()
    world_pts = np.array(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [20.0, 0.0, 0.0],
        ]
    )
    pixels = np.array([[400.0, 500.0], [500.0, 500.0], [600.0, 500.0]])

    with pytest.raises(PoseSolveError):
        solve_pnp(
            world_points=world_pts,
            pixel_points=pixels,
            intrinsics=intr,
            image_size=(1000, 1000),
        )


def test_solve_pnp_with_only_2_anchors_raises() -> None:
    """PnP requires >= 3 anchors."""
    intr = _make_test_intrinsics()
    world_pts = np.array([[0.0, 0.0, 0.0], [50.0, 0.0, 0.0]])
    pixels = np.array([[400.0, 500.0], [600.0, 500.0]])

    with pytest.raises(PoseSolveError, match="at least 3 anchors"):
        solve_pnp(
            world_points=world_pts,
            pixel_points=pixels,
            intrinsics=intr,
            image_size=(1000, 1000),
        )


def test_solve_pnp_high_rms_flags_intrinsics_suspect() -> None:
    """When clicked anchors are noisy, RMS exceeds threshold and intrinsics_suspect is True."""
    intr = _make_test_intrinsics()
    world_pts = np.array(
        [
            [0.0, 0.0, 0.0],
            [50.0, 0.0, 0.0],
            [50.0, 30.0, 0.0],
            [0.0, 30.0, 0.0],
        ]
    )
    true_rvec = np.array([0.0, 0.0, 0.0])
    true_tvec = np.array([-25.0, -15.0, 200.0])
    pixels = _project_synthetic(world_pts, true_rvec, true_tvec, intr)
    # Add 30 px of noise to each click — well above the 5-normalized-px threshold
    rng = np.random.default_rng(seed=42)
    pixels_noisy = pixels + rng.normal(0, 30, size=pixels.shape)

    result = solve_pnp(
        world_points=world_pts,
        pixel_points=pixels_noisy,
        intrinsics=intr,
        image_size=(1000, 1000),
    )

    assert result.anchor_reprojection_rms_px > ANCHOR_RMS_THRESHOLD_NORMALIZED_PX
    assert result.intrinsics_suspect is True


def test_solve_pnp_rejects_nan_pixel() -> None:
    """A NaN in pixel_points raises PoseSolveError, not silent garbage pose."""
    intr = _make_test_intrinsics()
    world_pts = np.array(
        [
            [0.0, 0.0, 0.0],
            [50.0, 0.0, 0.0],
            [50.0, 30.0, 0.0],
            [0.0, 30.0, 0.0],
        ]
    )
    pixels = np.array(
        [
            [400.0, 400.0],
            [600.0, 400.0],
            [600.0, 600.0],
            [float("nan"), 600.0],
        ]
    )
    with pytest.raises(PoseSolveError, match="finite"):
        solve_pnp(
            world_points=world_pts,
            pixel_points=pixels,
            intrinsics=intr,
            image_size=(1000, 1000),
        )


def test_solve_pnp_rejects_inf_world_point() -> None:
    """An Inf in world_points raises PoseSolveError."""
    intr = _make_test_intrinsics()
    world_pts = np.array(
        [
            [0.0, 0.0, 0.0],
            [50.0, 0.0, 0.0],
            [50.0, 30.0, 0.0],
            [float("inf"), 30.0, 0.0],
        ]
    )
    pixels = np.array(
        [
            [400.0, 400.0],
            [600.0, 400.0],
            [600.0, 600.0],
            [400.0, 600.0],
        ]
    )
    with pytest.raises(PoseSolveError, match="finite"):
        solve_pnp(
            world_points=world_pts,
            pixel_points=pixels,
            intrinsics=intr,
            image_size=(1000, 1000),
        )


def test_solve_pnp_rejects_wrong_pixel_shape() -> None:
    """A 1-D pixel_points array raises PoseSolveError with a shape message."""
    intr = _make_test_intrinsics()
    world_pts = np.array(
        [
            [0.0, 0.0, 0.0],
            [50.0, 0.0, 0.0],
            [50.0, 30.0, 0.0],
            [0.0, 30.0, 0.0],
        ]
    )
    pixels_flat = np.array([400.0, 400.0, 600.0, 400.0, 600.0, 600.0, 400.0, 600.0])
    with pytest.raises(PoseSolveError, match="shape"):
        solve_pnp(
            world_points=world_pts,
            pixel_points=pixels_flat,
            intrinsics=intr,
            image_size=(1000, 1000),
        )


def test_pose_result_serializes_to_dict() -> None:
    """PoseResult.to_dict matches the spec §6 photos[].pose schema shape."""
    pr = PoseResult(
        rvec=np.array([0.024, -1.567, 0.011]),
        tvec=np.array([12.4, -8.2, 287.3]),
        anchor_reprojection_rms_px=0.8,
        intrinsics_suspect=False,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )
    d = pr.to_dict()
    assert d["rvec"] == [0.024, -1.567, 0.011]
    assert d["tvec"] == [12.4, -8.2, 287.3]
    assert d["anchor_reprojection_rms_px"] == 0.8
    assert d["pose_solver"] == "cv2.solvePnP_ITERATIVE"
