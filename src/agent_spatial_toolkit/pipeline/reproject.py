"""Render validation overlays (spec §3 Phase 2e, §5.1 step 6)."""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult


def render_overlay(
    photo_path: Path,
    out_path: Path,
    features: list[tuple[str, np.ndarray]],  # (label, xyz_mm)
    pose: PoseResult,
    intrinsics: Intrinsics,
    marker_radius_px: int = 12,
    marker_color: tuple[int, int, int] = (255, 80, 200),  # BGR magenta
    label_color: tuple[int, int, int] = (255, 255, 255),
) -> Path:
    """Project features onto photo as colored circles + labels; save as PNG.

    Used by spec §3 Phase 2e — the user sees their photos with predicted
    feature positions and confirms or corrects.
    """
    # Finite-input contract: see spec §5.2 — never silently produce garbage.
    # Non-finite pose/intrinsics/feature xyz would project to NaN pixels that
    # silently fail the bounds check and drop features without surfacing the
    # bad input. Validate up-front, before any I/O.
    if not (np.isfinite(pose.rvec).all() and np.isfinite(pose.tvec).all()):
        raise ValueError("render_overlay: pose.rvec and pose.tvec must be finite")
    if not (
        math.isfinite(intrinsics.fx_px)
        and math.isfinite(intrinsics.fy_px)
        and math.isfinite(intrinsics.cx)
        and math.isfinite(intrinsics.cy)
        and np.isfinite(intrinsics.distortion).all()
    ):
        raise ValueError("render_overlay: intrinsics must be finite")
    if intrinsics.fx_px <= 0 or intrinsics.fy_px <= 0:
        raise ValueError(
            f"render_overlay: intrinsics fx_px and fy_px must be positive; "
            f"got fx_px={intrinsics.fx_px}, fy_px={intrinsics.fy_px}"
        )
    for label, xyz in features:
        if not np.isfinite(xyz).all():
            raise ValueError(f"render_overlay: feature '{label}' has non-finite coordinates: {xyz}")

    img = cv2.imread(str(photo_path))
    if img is None:
        raise FileNotFoundError(f"Cannot open image: {photo_path}")

    K = np.array(  # noqa: N806 — canonical CV name for camera matrix
        [
            [intrinsics.fx_px, 0, intrinsics.cx],
            [0, intrinsics.fy_px, intrinsics.cy],
            [0, 0, 1],
        ],
        dtype=np.float64,
    )
    dist = np.array(intrinsics.distortion, dtype=np.float64)

    if features:
        world_pts = np.array([xyz for _, xyz in features], dtype=np.float64).reshape(-1, 1, 3)
        projected, _ = cv2.projectPoints(world_pts, pose.rvec, pose.tvec, K, dist)
        projected = projected.reshape(-1, 2)
    else:
        projected = np.zeros((0, 2))

    for (label, _), (px, py) in zip(features, projected, strict=True):
        if not (0 <= px < img.shape[1] and 0 <= py < img.shape[0]):
            continue  # off-frame
        cv2.circle(img, (int(px), int(py)), marker_radius_px, marker_color, 2, lineType=cv2.LINE_AA)
        cv2.circle(img, (int(px), int(py)), 2, marker_color, -1)
        # Label slightly above and right of the marker
        cv2.putText(
            img,
            label,
            (int(px) + marker_radius_px + 4, int(py) - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            label_color,
            2,
            cv2.LINE_AA,
        )

    # Atomic write: same dir for atomic POSIX rename. Matches the pattern
    # PR #17 established for annotations.json (pipeline/emit.py).
    # Tmp name preserves the original suffix at the end (e.g. overlay.tmp.png)
    # because cv2.imwrite picks the encoder from the trailing extension and
    # would reject a name like overlay.png.tmp.
    tmp_path = out_path.with_suffix(".tmp" + out_path.suffix)
    ok = cv2.imwrite(str(tmp_path), img)
    if not ok:
        raise OSError(f"cv2.imwrite returned False; could not write {tmp_path}")
    tmp_path.replace(out_path)
    return out_path
