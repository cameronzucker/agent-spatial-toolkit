"""Single-photo planar ray-cast for β-mode (spec §5.1 step 5).

Given a pixel click in a photo with known camera pose, project the click
into the part-local frame by ray-casting through the camera's optical center
and intersecting with the assumed-Z plane.
"""

from __future__ import annotations

import math

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

    Raises ValueError if the ray direction is zero, parallel to the plane
    (within ~89.99994° of horizontal), or points away from the plane (t<0).
    """
    norm = float(np.linalg.norm(direction))
    if norm == 0.0:
        raise ValueError("Ray direction must be non-zero")
    if abs(direction[2]) / norm < 1e-6:
        raise ValueError(
            f"Ray is parallel to Z={plane_z} plane (within ~89.99994°); no usable intersection"
        )
    t = (plane_z - origin[2]) / direction[2]
    if t < 0.0:
        raise ValueError(
            f"Ray points away from Z={plane_z} plane "
            f"(intersection at t={t:.3f} is behind ray origin)"
        )
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
    # Shape validation
    if pixel.shape != (2,):
        raise ValueError(f"pixel must have shape (2,); got {pixel.shape}")

    # Finite-input validation
    if not (
        np.isfinite(pixel).all()
        and np.isfinite(pose.rvec).all()
        and np.isfinite(pose.tvec).all()
        and np.isfinite(intrinsics.distortion).all()
        and math.isfinite(intrinsics.fx_px)
        and math.isfinite(intrinsics.fy_px)
        and math.isfinite(intrinsics.cx)
        and math.isfinite(intrinsics.cy)
        and math.isfinite(z_assumed_mm)
    ):
        raise ValueError("pixel_to_part_local: all numeric inputs must be finite (no NaN/Inf)")

    # Step 1: undistort the pixel
    K = intrinsics.to_camera_matrix()  # noqa: N806 — canonical CV name for camera matrix
    dist = np.array(intrinsics.distortion, dtype=np.float64)
    pix_in = pixel.astype(np.float64).reshape(-1, 1, 2)
    try:
        pix_undist = cv2.undistortPoints(pix_in, K, dist, P=K).reshape(2)
    except cv2.error as e:
        raise ValueError(f"cv2.undistortPoints failed: {e}") from e

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
    # OpenCV convention: P_c = R @ P_w + t. Inverting:
    #   P_w = R^T @ (P_c - t)
    # Camera origin (P_c=0) → world position: -R^T @ t
    # Direction vector (no translation) → world: R^T @ d
    try:
        R, _ = cv2.Rodrigues(pose.rvec)  # noqa: N806 — canonical CV name for rotation matrix
    except cv2.error as e:
        raise ValueError(f"cv2.Rodrigues failed: {e}") from e
    R_inv = R.T  # noqa: N806 — canonical CV name for inverse rotation matrix
    cam_origin_world = -R_inv @ pose.tvec
    cam_dir_world = R_inv @ cam_dir

    # Step 4: intersect with Z=z_assumed_mm plane
    intersection = intersect_ray_with_plane(
        origin=cam_origin_world,
        direction=cam_dir_world,
        plane_z=z_assumed_mm,
    )

    # Finite-output check (defensive)
    if not np.isfinite(intersection).all():
        raise ValueError(
            "pixel_to_part_local: produced non-finite output (input was likely degenerate)"
        )

    return intersection[:2]  # (X, Y) only — Z is the assumed value
