"""PnP wrapper + anchor validation (spec §5.1, §5.3)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from agent_spatial_toolkit.normalize import to_normalized_px
from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics

# Threshold from spec §5.1 — anchor RMS above this normalized-px value flags
# the photo as intrinsics_suspect.
ANCHOR_RMS_THRESHOLD_NORMALIZED_PX: float = 5.0


class PoseSolveError(RuntimeError):
    """Raised when solvePnP cannot converge to a usable pose."""


@dataclass
class PoseResult:
    """Result of a PnP solve for one photo."""

    rvec: np.ndarray  # 3-vector, axis-angle rotation
    tvec: np.ndarray  # 3-vector, translation in part-local mm
    anchor_reprojection_rms_px: float  # normalized px
    intrinsics_suspect: bool  # True if RMS > ANCHOR_RMS_THRESHOLD_NORMALIZED_PX
    pose_solver: str  # which cv2.solvePnP method was used

    def to_dict(self) -> dict[str, Any]:
        """Serialize to spec §6 photos[].pose schema."""
        return {
            "rvec": self.rvec.tolist(),
            "tvec": self.tvec.tolist(),
            "anchor_reprojection_rms_px": float(self.anchor_reprojection_rms_px),
            "pose_solver": self.pose_solver,
        }


def solve_pnp(
    world_points: np.ndarray,  # (N, 3) part-local mm
    pixel_points: np.ndarray,  # (N, 2) absolute px
    intrinsics: Intrinsics,
    image_size: tuple[int, int],
) -> PoseResult:
    """Solve PnP with the given anchors.

    Raises PoseSolveError if anchors are insufficient (< 3) or pose cannot
    be recovered (e.g., collinear). For high-RMS solves, returns the
    PoseResult with intrinsics_suspect=True rather than raising — the caller
    decides whether to retry, fall back to chessboard calibration, or
    proceed with the noisy estimate.
    """
    # KNOWN LIMITATIONS (tracked):
    #   - SOLVEPNP_ITERATIVE rejects N=3 at the C++ layer; spec §5.3
    #     promises N=3 non-collinear works. See #11.
    #   - Coplanar anchor sets have a 2-solution ambiguity that this
    #     wrapper does not surface. Spec §5.3 line 664 defers the
    #     resolution to UI. See #12.
    if world_points.ndim != 2 or world_points.shape[1] != 3:
        raise PoseSolveError(f"world_points must have shape (N, 3); got {world_points.shape}")
    if pixel_points.ndim != 2 or pixel_points.shape[1] != 2:
        raise PoseSolveError(f"pixel_points must have shape (N, 2); got {pixel_points.shape}")
    if world_points.shape[0] < 3:
        raise PoseSolveError(f"PnP requires at least 3 anchors, got {world_points.shape[0]}")
    if world_points.shape[0] != pixel_points.shape[0]:
        raise PoseSolveError(
            f"world_points and pixel_points length mismatch: "
            f"{world_points.shape[0]} vs {pixel_points.shape[0]}"
        )
    if not (np.isfinite(world_points).all() and np.isfinite(pixel_points).all()):
        raise PoseSolveError("world_points and pixel_points must be finite (no NaN/Inf)")

    K = intrinsics.to_camera_matrix()  # noqa: N806 — canonical CV name for camera matrix
    dist = np.array(intrinsics.distortion, dtype=np.float64)
    obj_pts = world_points.astype(np.float64).reshape(-1, 1, 3)
    img_pts = pixel_points.astype(np.float64).reshape(-1, 1, 2)

    try:
        success, rvec, tvec = cv2.solvePnP(obj_pts, img_pts, K, dist, flags=cv2.SOLVEPNP_ITERATIVE)
    except cv2.error as e:
        # SOLVEPNP_ITERATIVE asserts npoints >= 4 at the C++ layer (or
        # npoints == 3 with useExtrinsicGuess). N=3 inputs always raise
        # cv2.error here even when non-collinear — see #11 for the gap
        # vs spec §5.3's "min 3 non-collinear" promise.
        raise PoseSolveError(
            f"cv2.solvePnP rejected input (likely collinear or degenerate): {e}"
        ) from e
    if not success:
        raise PoseSolveError("cv2.solvePnP returned failure (likely collinear or degenerate input)")

    # Compute reprojection error in absolute then normalized px
    try:
        projected, _ = cv2.projectPoints(obj_pts, rvec, tvec, K, dist)
    except cv2.error as e:
        raise PoseSolveError(f"cv2.projectPoints failed: {e}") from e
    diffs = projected.reshape(-1, 2) - pixel_points
    abs_rms = float(np.sqrt(np.mean(np.sum(diffs**2, axis=1))))
    norm_rms = to_normalized_px(abs_rms, image_size)

    if not (np.isfinite(rvec).all() and np.isfinite(tvec).all() and math.isfinite(norm_rms)):
        raise PoseSolveError("solvePnP returned non-finite pose (input was likely degenerate)")

    return PoseResult(
        rvec=rvec.flatten(),
        tvec=tvec.flatten(),
        anchor_reprojection_rms_px=norm_rms,
        intrinsics_suspect=norm_rms > ANCHOR_RMS_THRESHOLD_NORMALIZED_PX,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )
