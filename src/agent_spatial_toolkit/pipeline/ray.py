"""Single-photo planar ray-cast for β-mode (spec §5.1 step 5).

Given a pixel click in a photo with known camera pose, project the click
into the part-local frame by ray-casting through the camera's optical center
and intersecting with the assumed-Z plane.
"""

from __future__ import annotations

import cv2
import numpy as np

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult


def intersect_ray_with_plane(
    origin: np.ndarray,  # (3,) ray origin in part-local mm
    direction: np.ndarray,  # (3,) ray direction (need not be unit-length)
    plane_z: float,  # Z value of the horizontal plane to intersect
) -> np.ndarray:
    """Intersect a ray with a horizontal plane at Z=plane_z.

    Raises ValueError if the ray is parallel to the plane.
    """
    if abs(direction[2]) < 1e-9:
        raise ValueError("Ray is parallel to the Z=plane_z plane; no intersection")
    t = (plane_z - origin[2]) / direction[2]
    return origin + t * direction


def pixel_to_part_local(
    pixel: np.ndarray,  # (2,) absolute px
    pose: PoseResult,
    intrinsics: Intrinsics,
    z_assumed_mm: float = 0.0,
) -> np.ndarray:
    """Convert a single pixel click to part-local (X, Y, Z=z_assumed_mm).

    Returns a (2,) array of [X, Y] in mm. Z is the supplied z_assumed_mm.
    """
    # Step 1: undistort the pixel
    K = np.array(  # noqa: N806 — canonical CV name for camera matrix
        [
            [intrinsics.fx_px, 0, intrinsics.cx],
            [0, intrinsics.fy_px, intrinsics.cy],
            [0, 0, 1],
        ],
        dtype=np.float64,
    )
    dist = np.array(intrinsics.distortion, dtype=np.float64)
    pix_in = pixel.astype(np.float64).reshape(-1, 1, 2)
    pix_undist = cv2.undistortPoints(pix_in, K, dist, P=K).reshape(2)

    # Step 2: build a ray in camera frame.
    # Camera coordinates of the click: ((u-cx)/fx, (v-cy)/fy, 1)
    cam_dir = np.array(
        [
            (pix_undist[0] - intrinsics.cx) / intrinsics.fx_px,
            (pix_undist[1] - intrinsics.cy) / intrinsics.fy_px,
            1.0,
        ]
    )

    # Step 3: transform ray to world (part-local) frame.
    # Camera pose: world point P_w = R * P_c + t
    # Therefore camera origin in world = -R^T * t (wait — it's actually computed below)
    # cv2 convention: rvec/tvec take WORLD points to CAMERA frame.
    # So to invert: R_inv * (P_c - t) = P_w
    R, _ = cv2.Rodrigues(pose.rvec)  # noqa: N806 — canonical CV name for rotation matrix
    R_inv = R.T  # noqa: N806 — canonical CV name for inverse rotation matrix
    cam_origin_world = -R_inv @ pose.tvec
    cam_dir_world = R_inv @ cam_dir

    # Step 4: intersect with Z=z_assumed_mm plane
    intersection = intersect_ray_with_plane(
        origin=cam_origin_world,
        direction=cam_dir_world,
        plane_z=z_assumed_mm,
    )
    return intersection[:2]  # (X, Y) only — Z is the assumed value
