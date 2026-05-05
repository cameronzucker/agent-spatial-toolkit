"""Tests for server.coverage — pose-to-cell binning.

Pose-fixture sign convention (see coverage.py module docstring): with
OpenCV PnP and the part at world z=0, a synthetic `rvec=0, tvec=[*,*,+z]`
pose has the camera at world z<0 looking up along +Z toward the part.
The "top" axis (camera optical axis when filling the top cell) is +Z.
"""

from __future__ import annotations

import numpy as np

from agent_spatial_toolkit.pipeline.pose import PoseResult
from agent_spatial_toolkit.server.coverage import (
    CELL_CANONICAL_AXES,
    CELL_LABELS,
    angular_distance_to_cell,
    bin_pose_to_cell,
    compute_coverage_cells,
)


def _make_pose(rvec: list[float], tvec: list[float]) -> PoseResult:
    return PoseResult(
        rvec=np.array(rvec),
        tvec=np.array(tvec),
        anchor_reprojection_rms_px=0.5,
        intrinsics_suspect=False,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )


def test_top_down_pose_bins_to_top() -> None:
    """rvec=0 → R=I → R[2,:]=[0,0,+1] → matches 'top' canonical axis."""
    pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    assert bin_pose_to_cell(pose) == "top"


def test_y_rotation_bins_to_long_side() -> None:
    """Rotation about Y axis → optical axis lies in XZ plane → +long or -long."""
    side = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    cell = bin_pose_to_cell(side)
    # Ry(80°) gives R[2,:]=[-sin80°, 0, cos80°] ≈ [-0.985, 0, 0.174].
    # axis[0] < 0 → '+long' per the binning logic.
    assert cell == "+long"


def test_x_rotation_bins_to_short_side() -> None:
    """Rotation about X axis → optical axis lies in YZ plane → +short or -short."""
    side = _make_pose([np.deg2rad(80.0), 0.0, 0.0], [0.0, 30.0, 100.0])
    cell = bin_pose_to_cell(side)
    # Rx(80°) gives R[2,:]=[0, sin80°, cos80°] ≈ [0, 0.985, 0.174].
    # axis[1] > 0 → '-short' per the binning logic.
    assert cell == "-short"


def test_compute_coverage_cells_marks_filled() -> None:
    """A top-down + a Y-rotated side photo fills 'top' AND one long-side cell."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    side_pose = _make_pose([0.0, np.deg2rad(60.0), 0.0], [-30.0, 0.0, 150.0])
    cells = compute_coverage_cells([top_pose, side_pose])
    assert cells["top"] is True
    assert cells["+long"] is True
    assert cells["-long"] is False
    assert cells["+short"] is False
    assert cells["-short"] is False


def test_compute_coverage_cells_with_4_side_poses_fills_all_4_sides() -> None:
    """4 different rotation directions fill all 4 side cells."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    plus_long = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    minus_long = _make_pose([0.0, np.deg2rad(-80.0), 0.0], [30.0, 0.0, 100.0])
    # +Rx maps to '-short', -Rx maps to '+short' per the sign convention; the
    # fixture's rvec sign chooses which cell fills, not which way the user
    # rotated their phone.
    minus_short_via_pos_rx = _make_pose([np.deg2rad(80.0), 0.0, 0.0], [0.0, 30.0, 100.0])
    plus_short_via_neg_rx = _make_pose([np.deg2rad(-80.0), 0.0, 0.0], [0.0, -30.0, 100.0])
    cells = compute_coverage_cells(
        [top_pose, plus_long, minus_long, minus_short_via_pos_rx, plus_short_via_neg_rx]
    )
    assert cells["top"] is True
    assert cells["+long"] is True
    assert cells["-long"] is True
    assert cells["+short"] is True
    assert cells["-short"] is True


def test_empty_photo_list_returns_all_false() -> None:
    cells = compute_coverage_cells([])
    for label in CELL_LABELS:
        assert cells[label] is False


def test_only_visible_cells_are_in_default_response() -> None:
    """Bottom is hidden by default — design §4."""
    cells = compute_coverage_cells([])
    assert set(cells.keys()) == {"top", "+long", "-long", "+short", "-short"}


def test_canonical_axes_round_trip_through_binning() -> None:
    """Each cell's canonical axis, when used as a synthetic pose's R[2,:],
    bins back to that cell. This locks the canonical-axis lookup to the
    binning logic."""
    for cell, axis in CELL_CANONICAL_AXES.items():
        # Build a pose whose R[2,:] equals the canonical axis. The optical
        # axis in world is R^T @ [0,0,1] = R[2,:] (OpenCV convention), so
        # we need R such that R^T @ z_hat = target, i.e., R sends target
        # back to z_hat. Cross product order is target × z_hat (NOT
        # z_hat × target — that gives R such that R @ z_hat = target,
        # which is R[:,2] = target, not R[2,:] = target).
        target = axis / float(np.linalg.norm(axis))
        z_hat = np.array([0.0, 0.0, 1.0])
        rotation_axis = np.cross(target, z_hat)
        sin_theta = float(np.linalg.norm(rotation_axis))
        cos_theta = float(np.dot(target, z_hat))
        if sin_theta < 1e-9:
            # axis is parallel to z_hat (i.e., 'top') — no rotation needed.
            rvec = np.array([0.0, 0.0, 0.0])
        else:
            rvec = rotation_axis / sin_theta * float(np.arctan2(sin_theta, cos_theta))
        pose = _make_pose(rvec.tolist(), [0.0, 0.0, 100.0])
        assert bin_pose_to_cell(pose) == cell, f"canonical axis for {cell} bins to wrong cell"


def test_angular_distance_neighbors_vs_opposites() -> None:
    """Neighboring side cells are 90° apart; opposites are 180°."""
    # Build a pose that exactly fills '+long' (canonical axis [-1, 0, 0]).
    # See canonical-axis-round-trip test for the cross-product order rationale.
    z_hat = np.array([0.0, 0.0, 1.0])
    target = CELL_CANONICAL_AXES["+long"]
    rotation_axis = np.cross(target, z_hat)
    sin_theta = float(np.linalg.norm(rotation_axis))
    cos_theta = float(np.dot(target, z_hat))
    rvec = rotation_axis / sin_theta * float(np.arctan2(sin_theta, cos_theta))
    pose_at_plus_long = _make_pose(rvec.tolist(), [0.0, 0.0, 100.0])

    d_self = angular_distance_to_cell(pose_at_plus_long, "+long")
    d_neighbor = angular_distance_to_cell(pose_at_plus_long, "+short")
    d_opposite = angular_distance_to_cell(pose_at_plus_long, "-long")
    d_top = angular_distance_to_cell(pose_at_plus_long, "top")

    assert d_self < 1e-3  # ~0
    assert abs(d_neighbor - np.pi / 2) < 1e-3  # 90°
    assert abs(d_opposite - np.pi) < 1e-3  # 180°
    assert abs(d_top - np.pi / 2) < 1e-3  # 90°
