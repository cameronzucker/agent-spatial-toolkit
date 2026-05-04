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
    marker_radius_px: int | None = None,
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

    # Scale marker geometry with image size so overlays stay visible on
    # high-resolution photos (issue #19).  min(w,h) avoids oversized markers
    # on ultra-wide images; the floor of 6 keeps markers visible on thumbnails.
    h_img, w_img = img.shape[:2]
    if marker_radius_px is None:
        marker_radius_px = max(6, int(min(w_img, h_img) * 0.005))
    marker_thickness = max(1, marker_radius_px // 4)
    font_scale = max(0.4, marker_radius_px / 20.0)
    font_thickness = max(1, marker_radius_px // 6)

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
        cv2.circle(
            img,
            (int(px), int(py)),
            marker_radius_px,
            marker_color,
            marker_thickness,
            lineType=cv2.LINE_AA,
        )
        cv2.circle(img, (int(px), int(py)), max(1, marker_thickness), marker_color, -1)
        # Label slightly above and right of the marker
        cv2.putText(
            img,
            label,
            (int(px) + marker_radius_px + 4, int(py) - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            label_color,
            font_thickness,
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


def render_wireframe(
    photo_path: Path,
    out_path: Path,
    frame_anchors: list[dict],
    pose: PoseResult,
    intrinsics: Intrinsics,
    axis_length_mm: float | None = None,
    line_thickness_px: int = 2,
) -> Path:
    """Project frame anchors as a closed polyline + draw RGB axes from origin.

    Used by Phase 2c (Task 1.D.5 PR-β) so the user can visually confirm
    the camera pose computed from anchor clicks orients the part correctly.

    ``frame_anchors``: list of ``{"id": str, "xyz": [x, y, z]}`` — drawn as
    a closed polyline in array order. The last anchor connects back to the
    first so the user always sees a closed shape (degenerate when collinear).

    ``axis_length_mm``: arrow length for the X/Y/Z axes drawn from origin.
    Defaults to ``max(abs(coord) for anchor in anchors for coord in anchor['xyz']) * 0.5``,
    or 30.0 mm if all anchors collapse to the origin.

    Same atomic-write + finite-input contract as ``render_overlay``.
    """
    # Finite-input contract (matches render_overlay)
    if not (np.isfinite(pose.rvec).all() and np.isfinite(pose.tvec).all()):
        raise ValueError("render_wireframe: pose.rvec and pose.tvec must be finite")
    if not (
        math.isfinite(intrinsics.fx_px)
        and math.isfinite(intrinsics.fy_px)
        and math.isfinite(intrinsics.cx)
        and math.isfinite(intrinsics.cy)
        and np.isfinite(intrinsics.distortion).all()
    ):
        raise ValueError("render_wireframe: intrinsics must be finite")
    if intrinsics.fx_px <= 0 or intrinsics.fy_px <= 0:
        raise ValueError(
            f"render_wireframe: intrinsics fx_px and fy_px must be positive; "
            f"got fx_px={intrinsics.fx_px}, fy_px={intrinsics.fy_px}"
        )
    if not frame_anchors:
        raise ValueError("render_wireframe: frame_anchors must contain at least one anchor")
    for anchor in frame_anchors:
        xyz = np.array(anchor["xyz"], dtype=np.float64)
        if not np.isfinite(xyz).all():
            raise ValueError(
                f"render_wireframe: anchor '{anchor.get('id')}' has non-finite coordinates: {xyz}"
            )

    img = cv2.imread(str(photo_path))
    if img is None:
        raise FileNotFoundError(f"Cannot open image: {photo_path}")

    K = np.array(  # noqa: N806 — canonical CV name
        [
            [intrinsics.fx_px, 0, intrinsics.cx],
            [0, intrinsics.fy_px, intrinsics.cy],
            [0, 0, 1],
        ],
        dtype=np.float64,
    )
    dist = np.array(intrinsics.distortion, dtype=np.float64)

    # Determine axis length: half the largest coord magnitude across all
    # anchors. Caller can override.
    if axis_length_mm is None:
        max_coord = max(
            (abs(c) for anchor in frame_anchors for c in anchor["xyz"]),
            default=0.0,
        )
        axis_length_mm = max(max_coord * 0.5, 30.0) if max_coord > 0 else 30.0

    # Project the frame anchors
    anchor_pts = np.array([anchor["xyz"] for anchor in frame_anchors], dtype=np.float64).reshape(
        -1, 1, 3
    )
    anchor_proj, _ = cv2.projectPoints(anchor_pts, pose.rvec, pose.tvec, K, dist)
    anchor_proj = anchor_proj.reshape(-1, 2)

    # Project axis endpoints (origin + 3 axis tips)
    axis_world_pts = np.array(
        [
            [0.0, 0.0, 0.0],  # origin
            [axis_length_mm, 0.0, 0.0],  # +X
            [0.0, axis_length_mm, 0.0],  # +Y
            [0.0, 0.0, axis_length_mm],  # +Z
        ],
        dtype=np.float64,
    ).reshape(-1, 1, 3)
    axis_proj, _ = cv2.projectPoints(axis_world_pts, pose.rvec, pose.tvec, K, dist)
    axis_proj = axis_proj.reshape(-1, 2).astype(np.int32)

    # Draw the closed wireframe polyline (cyan, BGR (255, 255, 0)).
    # cv2.polylines wants an int32 array shaped (1, N, 2) for a single closed loop.
    if len(anchor_proj) >= 2:
        anchor_int = anchor_proj.astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(
            img,
            [anchor_int],
            isClosed=True,
            color=(255, 255, 0),  # BGR cyan
            thickness=line_thickness_px,
            lineType=cv2.LINE_AA,
        )

    # Draw axes — BGR colors: X=red(0,0,255), Y=green(0,255,0), Z=blue(255,0,0).
    origin = tuple(axis_proj[0])
    for i, color in enumerate([(0, 0, 255), (0, 255, 0), (255, 0, 0)], start=1):
        cv2.arrowedLine(
            img,
            origin,
            tuple(axis_proj[i]),
            color,
            thickness=line_thickness_px,
            line_type=cv2.LINE_AA,
            tipLength=0.15,
        )

    # Atomic write — same pattern as render_overlay
    tmp_path = out_path.with_suffix(".tmp" + out_path.suffix)
    ok = cv2.imwrite(str(tmp_path), img)
    if not ok:
        raise OSError(f"cv2.imwrite returned False; could not write {tmp_path}")
    tmp_path.replace(out_path)
    return out_path
