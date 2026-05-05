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
        raise TriangulationError(f"triangulation requires at least 2 views; got {len(views)}")

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
            raise TriangulationError(f"view {i} contains non-finite values (NaN/Inf)")

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
    for pose, intrinsics, observed_pixel in views:
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
