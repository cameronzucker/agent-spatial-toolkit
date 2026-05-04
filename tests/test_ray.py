"""Tests for pipeline/ray.py — single-photo planar ray-cast (β-mode)."""

import cv2
import numpy as np
import pytest

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult
from agent_spatial_toolkit.pipeline.ray import (
    intersect_ray_with_plane,
    pixel_to_part_local,
)


def _test_pose() -> PoseResult:
    """Camera 200mm above origin looking down (180° rotation about X axis).

    cv2 convention: rvec=[π, 0, 0], tvec=[0, 0, 200] places the camera at
    world (0, 0, 200) with optical axis pointing in -Z.
    """
    return PoseResult(
        rvec=np.array([np.pi, 0.0, 0.0]),
        tvec=np.array([0.0, 0.0, 200.0]),
        anchor_reprojection_rms_px=0.0,
        intrinsics_suspect=False,
        pose_solver="test",
    )


def _test_intrinsics() -> Intrinsics:
    return Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="test",
        fx_px=1000.0,
        fy_px=1000.0,
        cx=500.0,
        cy=500.0,
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )


def test_pixel_at_image_center_maps_to_origin() -> None:
    """Camera at (0,0,200) looking down; click at image center hits Z=0 plane at (0,0,0)."""
    pose = _test_pose()
    intr = _test_intrinsics()
    xy = pixel_to_part_local(
        pixel=np.array([500.0, 500.0]),
        pose=pose,
        intrinsics=intr,
        z_assumed_mm=0.0,
    )
    assert xy[0] == pytest.approx(0.0, abs=0.001)
    assert xy[1] == pytest.approx(0.0, abs=0.001)


def test_pixel_offset_maps_proportionally() -> None:
    """Camera at (0,0,200), focal=1000: 100px offset = 100 * 200/1000 = 20mm offset."""
    pose = _test_pose()
    intr = _test_intrinsics()
    xy = pixel_to_part_local(
        pixel=np.array([600.0, 500.0]),
        pose=pose,
        intrinsics=intr,
        z_assumed_mm=0.0,
    )
    assert xy[0] == pytest.approx(20.0, abs=0.01)
    assert xy[1] == pytest.approx(0.0, abs=0.01)


def test_z_assumed_nonzero() -> None:
    """If user specifies the feature is at Z=10mm above PCB, ray-cast intersects Z=10 plane."""
    pose = _test_pose()
    intr = _test_intrinsics()
    xy = pixel_to_part_local(
        pixel=np.array([600.0, 500.0]),
        pose=pose,
        intrinsics=intr,
        z_assumed_mm=10.0,
    )
    # At Z=10, the ray has traveled (200-10)/200 = 0.95 of the way down,
    # so the offset is 0.95 * 20mm = 19mm
    assert xy[0] == pytest.approx(19.0, abs=0.01)


def test_intersect_ray_with_plane_simple() -> None:
    """Ray from (0,0,200) along (0,0,-1) intersects Z=0 at (0,0,0)."""
    origin = np.array([0.0, 0.0, 200.0])
    direction = np.array([0.0, 0.0, -1.0])
    pt = intersect_ray_with_plane(origin, direction, plane_z=0.0)
    assert np.allclose(pt, [0.0, 0.0, 0.0])


def test_intersect_ray_parallel_to_plane_raises() -> None:
    """A ray parallel to the Z=0 plane never intersects it."""
    origin = np.array([0.0, 0.0, 200.0])
    direction = np.array([1.0, 0.0, 0.0])  # parallel to Z=0
    with pytest.raises(ValueError, match="parallel"):
        intersect_ray_with_plane(origin, direction, plane_z=0.0)


def test_intersect_ray_with_plane_behind_camera_raises() -> None:
    """A ray pointing away from the plane raises with a 'behind' message."""
    origin = np.array([0.0, 0.0, 200.0])
    direction = np.array([0.0, 0.0, 1.0])  # ray goes UP, away from Z=0
    with pytest.raises(ValueError, match="behind"):
        intersect_ray_with_plane(origin, direction, plane_z=0.0)


def test_pixel_to_part_local_round_trip_oblique() -> None:
    """A non-symmetric pose: project a known world point through cv2.projectPoints,
    then ray-cast back, verify recovery within tolerance."""
    intr = _test_intrinsics()
    # Non-symmetric rotation (tests R^T vs R; rvec=[π,0,0] is its own transpose)
    rvec = np.array([0.3, 0.1, 0.2])
    tvec = np.array([10.0, -5.0, 200.0])
    pose = PoseResult(
        rvec=rvec,
        tvec=tvec,
        anchor_reprojection_rms_px=0.0,
        intrinsics_suspect=False,
        pose_solver="test",
    )
    # Pick a known world point at Z=0
    world_pt = np.array([[15.0, 7.5, 0.0]])
    K = np.array(  # noqa: N806 — canonical CV name for camera matrix
        [
            [intr.fx_px, 0.0, intr.cx],
            [0.0, intr.fy_px, intr.cy],
            [0.0, 0.0, 1.0],
        ]
    )
    dist = np.array(intr.distortion)
    projected, _ = cv2.projectPoints(
        world_pt.reshape(-1, 1, 3).astype(np.float64),
        rvec,
        tvec,
        K,
        dist,
    )
    pixel = projected.reshape(2)

    xy = pixel_to_part_local(pixel=pixel, pose=pose, intrinsics=intr, z_assumed_mm=0.0)
    assert xy[0] == pytest.approx(15.0, abs=0.001)
    assert xy[1] == pytest.approx(7.5, abs=0.001)


def test_pixel_to_part_local_with_distortion() -> None:
    """Non-zero distortion: with k1=0.05 (mild barrel), an off-center pixel
    must produce a different XY than the same pixel with zero distortion.
    Verifies the cv2.undistortPoints path actually runs."""
    intr_no_dist = _test_intrinsics()
    intr_with_dist = Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="test_with_dist",
        fx_px=1000.0,
        fy_px=1000.0,
        cx=500.0,
        cy=500.0,
        distortion=[0.05, 0.0, 0.0, 0.0, 0.0],
    )
    pose = _test_pose()
    pixel = np.array([700.0, 500.0])  # off-center — distortion has effect

    xy_no_dist = pixel_to_part_local(
        pixel=pixel, pose=pose, intrinsics=intr_no_dist, z_assumed_mm=0.0
    )
    xy_with_dist = pixel_to_part_local(
        pixel=pixel, pose=pose, intrinsics=intr_with_dist, z_assumed_mm=0.0
    )

    assert xy_no_dist[0] != pytest.approx(xy_with_dist[0])


def test_pixel_to_part_local_rejects_nan_pixel() -> None:
    """A NaN in the pixel raises ValueError with a 'finite' message."""
    pose = _test_pose()
    intr = _test_intrinsics()
    with pytest.raises(ValueError, match="finite"):
        pixel_to_part_local(
            pixel=np.array([float("nan"), 500.0]),
            pose=pose,
            intrinsics=intr,
            z_assumed_mm=0.0,
        )


def test_pixel_to_part_local_rejects_wrong_pixel_shape() -> None:
    """A wrong-shape pixel raises ValueError with a 'shape' message."""
    pose = _test_pose()
    intr = _test_intrinsics()
    with pytest.raises(ValueError, match="shape"):
        pixel_to_part_local(
            pixel=np.array([500.0, 500.0, 0.0]),  # (3,) instead of (2,)
            pose=pose,
            intrinsics=intr,
            z_assumed_mm=0.0,
        )
