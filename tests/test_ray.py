"""Tests for pipeline/ray.py — single-photo planar ray-cast (β-mode)."""

import numpy as np
import pytest

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult
from agent_spatial_toolkit.pipeline.ray import (
    intersect_ray_with_plane,
    pixel_to_part_local,
)


def _test_pose() -> PoseResult:
    """Identity rotation, camera 200mm above origin looking down."""
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
