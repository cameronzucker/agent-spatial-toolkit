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
    err_mm = feature_error_mm_planar(pose=pose, intrinsics=intr, residual_px=2.0)
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
