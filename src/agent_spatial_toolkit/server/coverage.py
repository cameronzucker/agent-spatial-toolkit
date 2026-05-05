"""Coverage-cell binning anchored to the part-local frame (design §4).

The reference object's frame IS the part-local frame (per
reference_objects.py: long edge along X, short along Y, Z=0 plane).
All photos solve PnP against the same world points, so the part-local
frame is consistent across all photos and the design's "anchored to
photo #1" is satisfied automatically — there's no separate "photo #1's
frame" to anchor to.

Design §4: 5 default cells ('top' + 4 sides). Bottom cell is hidden by
default and revealed only when a photo's pose genuinely points up at the
part from below (a v2-only scenario; v1 doesn't support flipping the part).

Cell canonical axes (camera-optical-axis-in-world that "perfectly fills"
each cell):
  top:    [0, 0, +1]  (looking down at the part — see sign convention below)
  +long:  [-1, 0, 0]  (camera at +X, looking toward -X)
  -long:  [+1, 0, 0]  (camera at -X, looking toward +X)
  +short: [0, -1, 0]
  -short: [0, +1, 0]

These canonical axes are exposed for the next-prompt rotation-distance
tie-break.

Sign convention (load-bearing — see math review BLOCKER 2026-05-05):
With OpenCV's `cv2.solvePnP` and the part at world z=0, a "top-down"
synthetic pose (rvec=0, tvec=[*, *, +z]) has the camera at world
position -R^T @ tvec = world z<0, with optical axis (camera +Z in world
= R[2,:]) pointing along world +Z toward the part. So the "top" axis
is +Z, NOT -Z. An earlier draft of this code used -Z and would have
classified every top-down photo as 'bottom'.
"""

from __future__ import annotations

import cv2
import numpy as np

from agent_spatial_toolkit.pipeline.pose import PoseResult

CELL_LABELS = ("top", "+long", "-long", "+short", "-short")
CellLabel = str
"""One of CELL_LABELS or 'bottom' (the bottom cell is exposed via the
all-cells-including-bottom path; the default API hides it per design §4)."""

# Camera-optical-axis-in-world unit vectors that perfectly fill each cell.
CELL_CANONICAL_AXES: dict[str, np.ndarray] = {
    "top": np.array([0.0, 0.0, 1.0]),
    "+long": np.array([-1.0, 0.0, 0.0]),
    "-long": np.array([1.0, 0.0, 0.0]),
    "+short": np.array([0.0, -1.0, 0.0]),
    "-short": np.array([0.0, 1.0, 0.0]),
}

# Threshold (radians from the canonical 'top' axis) above which a pose
# is considered "side" rather than "top". 30° matches design §4's
# "moderately oblique" non-goal boundary.
_TOP_CONE_HALF_ANGLE_RAD = np.deg2rad(30.0)
# Symmetrical: >150° from 'top' axis (i.e., looking up at the part from
# below) bins to 'bottom'. v1 doesn't support the flipped-part scenario;
# this guard exists only to silently drop pathological poses.
_BOTTOM_CONE_HALF_ANGLE_RAD = np.deg2rad(150.0)


def _camera_optical_axis_world(pose: PoseResult) -> np.ndarray:
    """Camera optical axis in world (part-local) frame.

    OpenCV: P_camera = R @ P_world + tvec. Camera +Z in camera frame is
    [0,0,1]; in world frame it's R^T @ [0,0,1] = third column of R^T =
    third row of R.
    """
    R, _ = cv2.Rodrigues(pose.rvec.astype(np.float64))  # noqa: N806
    return R[2, :].astype(np.float64)


def bin_pose_to_cell(pose: PoseResult) -> CellLabel:
    """Bin one pose to its coverage cell.

    No reference_pose argument: world frame == part-local frame ==
    reference-object frame (all photos solve PnP against the same world
    points), so cell labels are stable across photos by construction.
    """
    axis = _camera_optical_axis_world(pose)
    top_axis = CELL_CANONICAL_AXES["top"]  # [0, 0, +1]
    cos_angle = float(np.dot(axis, top_axis))
    cos_angle = max(min(cos_angle, 1.0), -1.0)
    angle_from_top = np.arccos(cos_angle)

    if angle_from_top < _TOP_CONE_HALF_ANGLE_RAD:
        return "top"
    if angle_from_top > _BOTTOM_CONE_HALF_ANGLE_RAD:
        return "bottom"

    # Side bin: project axis onto the XY plane and pick the dominant axis.
    horizontal = np.array([axis[0], axis[1]])
    if np.linalg.norm(horizontal) < 1e-9:
        return "top"  # numerical fallback — axis is essentially vertical

    if abs(horizontal[0]) >= abs(horizontal[1]):
        # Camera optical axis points primarily along ±X. The cell label
        # refers to which side of the part the camera is on, which is
        # the OPPOSITE of where the optical axis points. So:
        #   axis[0] > 0  ⇒  optical axis toward +X  ⇒  camera on -X side  ⇒ '-long'
        #   axis[0] < 0  ⇒  optical axis toward -X  ⇒  camera on +X side  ⇒ '+long'
        return "-long" if horizontal[0] > 0 else "+long"
    return "-short" if horizontal[1] > 0 else "+short"


def angular_distance_to_cell(pose: PoseResult, cell: CellLabel) -> float:
    """Angular distance (radians) from `pose`'s optical axis to `cell`'s
    canonical axis. Used for next-prompt tie-break to minimize physical
    re-positioning.
    """
    if cell not in CELL_CANONICAL_AXES:
        # Defensively handle 'bottom' or unknown labels — return π so they
        # never tie-break-win against the 5 default cells.
        return float(np.pi)
    axis = _camera_optical_axis_world(pose)
    canonical = CELL_CANONICAL_AXES[cell]
    cos_angle = float(np.dot(axis, canonical))
    cos_angle = max(min(cos_angle, 1.0), -1.0)
    return float(np.arccos(cos_angle))


def compute_coverage_cells(poses: list[PoseResult]) -> dict[str, bool]:
    """Return {cell_label: True/False} for the 5 default cells.

    The bottom cell is omitted from the default response per design §4.
    """
    result = dict.fromkeys(CELL_LABELS, False)
    for pose in poses:
        cell = bin_pose_to_cell(pose)
        if cell in result:
            result[cell] = True
        # 'bottom' is dropped silently for the default-5 response.
    return result
