"""Tests for server.next_prompt — design §4 scoring algorithm."""

from __future__ import annotations

import numpy as np

from agent_spatial_toolkit.pipeline.pose import PoseResult
from agent_spatial_toolkit.server.next_prompt import (
    GREEN_TIER_MM_THRESHOLD,
    score_next_prompt,
)


def _make_pose(rvec: list[float], tvec: list[float]) -> PoseResult:
    return PoseResult(
        rvec=np.array(rvec),
        tvec=np.array(tvec),
        anchor_reprojection_rms_px=0.5,
        intrinsics_suspect=False,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )


def test_no_photos_no_features_starts_with_top() -> None:
    """Empty session: prompt for the top-down photo first."""
    result = score_next_prompt(photos=[], features=[])
    assert result["direction"] == "top"
    assert result["reason_code"] == "top_first_photo"


def test_features_pending_and_empty_cell_picks_empty_side() -> None:
    """When some feature is single-view-only AND a side cell is empty, the
    algorithm picks an empty-cell side."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    photos = [{"id": "top", "pose": top_pose}]
    features = [{"id": "f1", "method": "planar_intersection"}]
    result = score_next_prompt(photos=photos, features=features)
    assert result["direction"] != "top"
    assert result["reason_code"] == "second_view_needed_empty_cell"


def test_features_pending_no_empty_cells_says_different_angle() -> None:
    """When all side cells are filled and a feature is still pending, the
    reason_code adapts to second_view_needed_filled_cell."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    plus_long = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    minus_long = _make_pose([0.0, np.deg2rad(-80.0), 0.0], [30.0, 0.0, 100.0])
    minus_short = _make_pose([np.deg2rad(80.0), 0.0, 0.0], [0.0, 30.0, 100.0])
    plus_short = _make_pose([np.deg2rad(-80.0), 0.0, 0.0], [0.0, -30.0, 100.0])
    photos = [
        {"id": str(i), "pose": p}
        for i, p in enumerate([top_pose, plus_long, minus_long, minus_short, plus_short])
    ]
    features = [{"id": "f1", "method": "planar_intersection"}]
    result = score_next_prompt(photos=photos, features=features)
    assert result["reason_code"] == "second_view_needed_filled_cell"


def test_no_pending_features_empty_cells_says_lower_priority() -> None:
    """All features triangulated, but cells remain empty: prompt is lower-priority."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    photos = [{"id": "top", "pose": top_pose}]
    features = [{"id": "f1", "method": "triangulation_2_views"}]
    result = score_next_prompt(photos=photos, features=features)
    assert result["reason_code"] == "unseen_side_low_priority"


def test_call_it_done_when_3_cells_filled_and_no_pending() -> None:
    """No pending features AND ≥3 cells filled: tone shifts to 'call it done'."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    plus_long = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    minus_short = _make_pose([np.deg2rad(80.0), 0.0, 0.0], [0.0, 30.0, 100.0])
    photos = [
        {"id": "top", "pose": top_pose},
        {"id": "p1", "pose": plus_long},
        {"id": "p2", "pose": minus_short},
    ]
    features = [
        {"id": "f1", "method": "triangulation_2_views"},
        {"id": "f2", "method": "triangulation_3_views"},
    ]
    result = score_next_prompt(photos=photos, features=features)
    assert result["reason_code"] == "call_it_done"


def test_suppression_rule_call_it_done_with_only_2_cells_when_6_uniformly_green() -> None:
    """Design §4 completion criterion #2 suppression: when feature_count ≥ 6
    AND all features are green-tier triangulated, the ≥3-cells soft nudge
    is suppressed and the wizard says 'call it done' even with 2 cells filled."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    plus_long = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    photos = [
        {"id": "top", "pose": top_pose},
        {"id": "p1", "pose": plus_long},
    ]
    features = [
        {
            "id": f"f{i}",
            "method": "triangulation_2_views",
            "max_error_mm": GREEN_TIER_MM_THRESHOLD - 0.1,  # green tier
        }
        for i in range(6)
    ]
    result = score_next_prompt(photos=photos, features=features)
    assert result["reason_code"] == "call_it_done"


def test_suppression_rule_does_not_apply_when_one_feature_is_yellow() -> None:
    """If even one of 6 features is above green threshold, suppression
    doesn't apply and we fall back to the empty-cells branch."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    plus_long = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    photos = [
        {"id": "top", "pose": top_pose},
        {"id": "p1", "pose": plus_long},
    ]
    features = [
        {
            "id": f"f{i}",
            "method": "triangulation_2_views",
            "max_error_mm": GREEN_TIER_MM_THRESHOLD - 0.1,
        }
        for i in range(5)
    ] + [
        {
            "id": "f5_yellow",
            "method": "triangulation_2_views",
            "max_error_mm": GREEN_TIER_MM_THRESHOLD + 0.1,  # above green
        }
    ]
    result = score_next_prompt(photos=photos, features=features)
    # 2 cells filled (top + +long), 3 still empty; not "call_it_done".
    assert result["reason_code"] in {
        "unseen_side_low_priority",
        "another_angle_low_priority",
    }


def test_rotation_tiebreak_picks_neighbor_over_opposite() -> None:
    """When all cells are tied on score, tie-break picks the side closest
    in rotation to the most-recent photo."""
    top_pose = _make_pose([0.0, 0.0, 0.0], [0.0, 0.0, 200.0])
    # Most recent photo: at +long (camera optical axis pointing toward -X).
    plus_long_recent = _make_pose([0.0, np.deg2rad(80.0), 0.0], [-30.0, 0.0, 100.0])
    photos = [
        {"id": "top", "pose": top_pose},
        {"id": "recent_plus_long", "pose": plus_long_recent},
    ]
    # No features → features_pending=0; only +short / -short / -long have
    # empty_cell_score=1. Rotation distances from +long-recent:
    #   to +long: ~0   (already filled, score=10*0+0=0, lower base)
    #   to -long: 180° (empty, score=1)
    #   to +short: 90° (empty, score=1)
    #   to -short: 90° (empty, score=1)
    # The two short sides tie at distance 90°. Tie-break picks first iter
    # (deterministic given dict order in CELL_LABELS); but BOTH are 90°
    # neighbors. Anything BUT -long is acceptable.
    features: list = []
    result = score_next_prompt(photos=photos, features=features)
    assert result["direction"] != "-long"  # must NOT pick the 180° opposite


def test_response_shape_includes_reason_code() -> None:
    """The full response shape includes reason_code, reason, direction,
    coverage_cells, features."""
    result = score_next_prompt(photos=[], features=[])
    assert set(result.keys()) >= {
        "direction",
        "reason_code",
        "reason",
        "coverage_cells",
        "features",
    }
    assert isinstance(result["coverage_cells"], dict)
    assert isinstance(result["features"], list)
