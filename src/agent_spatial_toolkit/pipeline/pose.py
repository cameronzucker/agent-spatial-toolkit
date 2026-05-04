"""PnP wrapper + anchor validation (spec §5.1, §5.3)."""

from __future__ import annotations

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


def _camera_matrix(intr: Intrinsics) -> np.ndarray:
    return np.array(
        [
            [intr.fx_px, 0, intr.cx],
            [0, intr.fy_px, intr.cy],
            [0, 0, 1],
        ],
        dtype=np.float64,
    )


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
    if world_points.shape[0] < 3:
        raise PoseSolveError(f"PnP requires at least 3 anchors, got {world_points.shape[0]}")
    if world_points.shape[0] != pixel_points.shape[0]:
        raise PoseSolveError(
            f"world_points and pixel_points length mismatch: "
            f"{world_points.shape[0]} vs {pixel_points.shape[0]}"
        )

    K = _camera_matrix(intrinsics)  # noqa: N806 — canonical CV name for camera matrix
    dist = np.array(intrinsics.distortion, dtype=np.float64)
    obj_pts = world_points.astype(np.float64).reshape(-1, 1, 3)
    img_pts = pixel_points.astype(np.float64).reshape(-1, 1, 2)

    success, rvec, tvec = cv2.solvePnP(obj_pts, img_pts, K, dist, flags=cv2.SOLVEPNP_ITERATIVE)
    if not success:
        raise PoseSolveError("cv2.solvePnP returned failure (likely collinear or degenerate input)")

    # Compute reprojection error in absolute then normalized px
    projected, _ = cv2.projectPoints(obj_pts, rvec, tvec, K, dist)
    diffs = projected.reshape(-1, 2) - pixel_points
    abs_rms = float(np.sqrt(np.mean(np.sum(diffs**2, axis=1))))
    norm_rms = to_normalized_px(abs_rms, image_size)

    return PoseResult(
        rvec=rvec.flatten(),
        tvec=tvec.flatten(),
        anchor_reprojection_rms_px=norm_rms,
        intrinsics_suspect=norm_rms > ANCHOR_RMS_THRESHOLD_NORMALIZED_PX,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )
