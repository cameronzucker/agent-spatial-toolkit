"""Tests for pipeline/emit.py — assembling annotations.json from session state."""

import json
from pathlib import Path

import pytest

from agent_spatial_toolkit.pipeline.emit import (
    SessionState,
    emit_annotations,
)
from agent_spatial_toolkit.schema.models import (
    AnchorClick,
    CameraDetected,
    Feature,
    FeatureClick,
    FeatureMeasurement,
    Photo,
)
from agent_spatial_toolkit.schema.validators import ValidationError


def test_emit_minimal_session(tmp_path: Path) -> None:
    """An empty session produces a valid annotations.json with no features."""
    state = SessionState(
        part_id="testpart",
        part_display_name=None,
        part_class=None,
        notes=None,
        reference_frame={
            "origin_description": "origin",
            "x_axis_description": "+x",
            "y_axis_description": "+y",
            "z_axis_description": "+z",
            "units": "mm",
        },
        photos=[],
        features=[],
        flags=[],
        events_jsonl_filename="events.jsonl",
        manifest_filename="manifest.json",
        overlay_pngs=[],
        chessboard_calibration_image=None,
    )

    out_path = emit_annotations(state, session_dir=tmp_path)
    assert out_path == tmp_path / "annotations.json"
    assert out_path.exists()

    data = json.loads(out_path.read_text())
    assert data["schema_version"] == 1
    assert data["part"]["id"] == "testpart"
    assert data["features"] == []
    assert data["quality_summary"]["feature_count"] == 0


def test_emit_session_with_one_planar_feature(tmp_path: Path) -> None:
    photo = Photo(
        id="top_down",
        path="photos/top_down.jpg",
        sha256="deadbeef",
        camera_detected=CameraDetected(make="Test", model="Camera"),
        intrinsics={"profile_source": "fov_class_fallback"},
        pose={
            "rvec": [0, 0, 0],
            "tvec": [0, 0, 200],
            "anchor_reprojection_rms_px": 0.5,
            "pose_solver": "test",
        },
        anchors_clicked=[AnchorClick(id="o", pcb_xyz_mm=(0, 0, 0), pixel=(0, 0))],
    )
    feature = Feature(
        id="x",
        visible_in=["top_down"],
        pcb_xyz_mm=(10.0, 20.0, 0.0),
        measurements=FeatureMeasurement(
            method="planar_intersection",
            z_assumed_mm=0.0,
            z_assumed_reason="single_photo_only_default",
            per_photo_clicks=[FeatureClick(photo="top_down", pixel=(100, 200))],
        ),
    )

    state = SessionState(
        part_id="x",
        part_display_name=None,
        part_class=None,
        notes=None,
        reference_frame={
            "origin_description": "o",
            "x_axis_description": "+x",
            "y_axis_description": "+y",
            "z_axis_description": "+z",
            "units": "mm",
        },
        photos=[photo],
        features=[feature],
        flags=["feature_clicked_only_once:x"],
        events_jsonl_filename="events.jsonl",
        manifest_filename="manifest.json",
        overlay_pngs=["photos/top_down_overlay.png"],
        chessboard_calibration_image=None,
    )
    out_path = emit_annotations(state, session_dir=tmp_path)
    data = json.loads(out_path.read_text())

    assert data["features"][0]["id"] == "x"
    assert data["quality_summary"]["feature_count"] == 1
    assert data["quality_summary"]["z_assumed_count"] == 1
    assert data["quality_summary"]["triangulated_count"] == 0
    assert "feature_clicked_only_once:x" in data["quality_summary"]["flags"]


def test_emit_validates_flags(tmp_path: Path) -> None:
    """Invalid flags cause emit to raise ValidationError."""
    state = SessionState(
        part_id="x",
        part_display_name=None,
        part_class=None,
        notes=None,
        reference_frame={
            "origin_description": "o",
            "x_axis_description": "+x",
            "y_axis_description": "+y",
            "z_axis_description": "+z",
            "units": "mm",
        },
        photos=[],
        features=[],
        flags=["totally_invalid_flag"],
        events_jsonl_filename="e.jsonl",
        manifest_filename="m.json",
        overlay_pngs=[],
        chessboard_calibration_image=None,
    )
    with pytest.raises(ValidationError):
        emit_annotations(state, session_dir=tmp_path)
