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
