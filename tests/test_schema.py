"""Tests for schema/models.py — dataclasses mirroring spec §6 schema."""

import json

import pytest

from agent_spatial_toolkit.schema.models import (
    AnchorClick,
    Annotations,
    CameraDetected,
    Feature,
    FeatureClick,
    FeatureMeasurement,
    Part,
    Photo,
    QualitySummary,
    ReferenceFrame,
    SessionArtifacts,
)
from agent_spatial_toolkit.schema.validators import (
    QUALITY_FLAGS,
    ValidationError,
    validate_annotations,
    validate_quality_flag,
)


def test_minimal_annotations_serializes() -> None:
    """A bare-minimum Annotations object emits valid JSON matching spec §6."""
    ann = Annotations(
        schema_version=1,
        toolkit_version="0.0.1",
        generated_at="2026-05-03T22:14:00Z",
        part=Part(id="x1207", display_name=None, part_class=None, notes=None),
        reference_frame=ReferenceFrame(
            origin_description="pcb_bottom_left_corner",
            x_axis_description="along_long_edge",
            y_axis_description="along_short_edge",
            z_axis_description="up_from_pcb_bottom",
            units="mm",
        ),
        photos=[],
        features=[],
        quality_summary=QualitySummary(
            feature_count=0,
            triangulated_count=0,
            z_assumed_count=0,
            median_reprojection_rms_px=None,
            max_reprojection_rms_px=None,
            flags=[],
        ),
        session_artifacts=SessionArtifacts(
            overlay_pngs=[],
            events_jsonl="events.jsonl",
            manifest="manifest.json",
        ),
    )

    data = ann.to_dict()
    # Round-trip through json
    s = json.dumps(data)
    parsed = json.loads(s)

    assert parsed["schema_version"] == 1
    assert parsed["part"]["id"] == "x1207"
    assert parsed["reference_frame"]["units"] == "mm"
    assert parsed["features"] == []
    assert parsed["photos"] == []


def test_feature_with_planar_intersection_omits_residual() -> None:
    """planar_intersection method does not emit reprojection_residual_px in clicks."""
    feature = Feature(
        id="usb_c",
        visible_in=["top_down"],
        pcb_xyz_mm=(82.0, 5.0, 0.0),
        measurements=FeatureMeasurement(
            method="planar_intersection",
            z_assumed_mm=0.0,
            z_assumed_reason="single_photo_only_default",
            per_photo_clicks=[
                FeatureClick(photo="top_down", pixel=(1280, 1050), reprojection_residual_px=None),
            ],
        ),
    )
    d = feature.to_dict()
    # The click dict must NOT have reprojection_residual_px
    assert "reprojection_residual_px" not in d["measurements"]["per_photo_clicks"][0]


def test_feature_with_triangulation_includes_residual() -> None:
    """triangulation_K_views emits reprojection_residual_px on every click."""
    feature = Feature(
        id="rj45",
        visible_in=["top_down", "long_edge_a"],
        pcb_xyz_mm=(78.0, 47.0, 7.0),
        measurements=FeatureMeasurement(
            method="triangulation_2_views",
            triangulation_rms_px=0.7,
            max_residual_px=1.1,
            per_photo_clicks=[
                FeatureClick(photo="top_down", pixel=(2015, 1820), reprojection_residual_px=0.6),
                FeatureClick(photo="long_edge_a", pixel=(1870, 940), reprojection_residual_px=0.8),
            ],
        ),
    )
    d = feature.to_dict()
    for click_d in d["measurements"]["per_photo_clicks"]:
        assert "reprojection_residual_px" in click_d


def test_pcb_xyz_mm_uses_array_uniformly() -> None:
    """Per spec §6 contract, pcb_xyz_mm is always a [X, Y, Z] array."""
    feature = Feature(
        id="x",
        visible_in=["a"],
        pcb_xyz_mm=(1.0, 2.0, 3.0),
        measurements=FeatureMeasurement(
            method="planar_intersection",
            z_assumed_mm=3.0,
            z_assumed_reason="user_override",
            per_photo_clicks=[],
        ),
    )
    d = feature.to_dict()
    assert d["pcb_xyz_mm"] == [1.0, 2.0, 3.0]


def test_anchor_click_serializes() -> None:
    a = AnchorClick(id="pcb_corner_origin", pcb_xyz_mm=(0.0, 0.0, 0.0), pixel=(342, 218))
    d = a.to_dict()
    assert d["pcb_xyz_mm"] == [0.0, 0.0, 0.0]
    assert d["pixel"] == [342, 218]
    assert d["id"] == "pcb_corner_origin"


def test_planar_with_stray_residual_raises() -> None:
    """A planar feature with a residual on a click must raise (spec §6 lines 435-436)."""
    with pytest.raises(ValueError, match="must NOT have"):
        FeatureMeasurement(
            method="planar_intersection",
            z_assumed_mm=0.0,
            z_assumed_reason="single_photo_only_default",
            per_photo_clicks=[
                FeatureClick(
                    photo="top_down",
                    pixel=(1280, 1050),
                    reprojection_residual_px=0.6,  # stray — invalid for planar
                ),
            ],
        )


def test_triangulation_with_missing_residual_raises() -> None:
    """A triangulation feature missing a residual on any click must raise (spec §6 line 423)."""
    with pytest.raises(ValueError, match="requires reprojection_residual_px"):
        FeatureMeasurement(
            method="triangulation_2_views",
            triangulation_rms_px=0.7,
            max_residual_px=1.1,
            per_photo_clicks=[
                FeatureClick(
                    photo="top_down",
                    pixel=(2015, 1820),
                    reprojection_residual_px=0.6,
                ),
                FeatureClick(
                    photo="long_edge_a",
                    pixel=(1870, 940),
                    reprojection_residual_px=None,  # missing — invalid for triangulation
                ),
            ],
        )


def test_minimal_part_omits_none_fields() -> None:
    """Part with all-None optionals emits only 'id'."""
    p = Part(id="x1207", display_name=None, part_class=None, notes=None)
    d = p.to_dict()
    assert d == {"id": "x1207"}
    assert "display_name" not in d
    assert "part_class" not in d
    assert "notes" not in d


def test_populated_part_emits_all_fields() -> None:
    """Part with all fields populated emits all four."""
    p = Part(
        id="x1207",
        display_name="Sample PCB",
        part_class="rev_A",
        notes="prototype",
    )
    d = p.to_dict()
    assert d == {
        "id": "x1207",
        "display_name": "Sample PCB",
        "part_class": "rev_A",
        "notes": "prototype",
    }


def test_annotations_with_photos_and_features_round_trips() -> None:
    """Non-empty photos and features round-trip through JSON; field ordering in
    FeatureMeasurement matches spec §6 (summary metadata before per_photo_clicks)."""
    photo = Photo(
        id="top_down",
        path="photos/top_down.jpg",
        sha256="a" * 64,
        camera_detected=CameraDetected(
            make="Apple",
            model="iPhone 13",
            lens_label=None,
            detection_source="exif",
        ),
        intrinsics={
            "profile_source": "fov_class_fallback",
            "profile_id": "fov_normal_v1",
            "fx_px": 5600.0,
            "fy_px": 5600.0,
            "cx": 2016.0,
            "cy": 1512.0,
            "distortion_model": "opencv_5param",
            "distortion": {"k1": 0.01, "k2": 0.005, "p1": 0.0, "p2": 0.0, "k3": 0.0},
        },
        pose={
            "rvec": [0.024, -1.567, 0.011],
            "tvec": [12.4, -8.2, 287.3],
            "anchor_reprojection_rms_px": 0.8,
            "pose_solver": "cv2.solvePnP_ITERATIVE",
        },
        anchors_clicked=[
            AnchorClick(
                id="pcb_corner_origin",
                pcb_xyz_mm=(0.0, 0.0, 0.0),
                pixel=(342, 218),
            ),
        ],
    )
    feature = Feature(
        id="usb_c",
        visible_in=["top_down"],
        pcb_xyz_mm=(82.0, 5.0, 0.0),
        measurements=FeatureMeasurement(
            method="planar_intersection",
            z_assumed_mm=0.0,
            z_assumed_reason="single_photo_only_default",
            per_photo_clicks=[
                FeatureClick(
                    photo="top_down",
                    pixel=(1280, 1050),
                    reprojection_residual_px=None,
                ),
            ],
        ),
    )
    ann = Annotations(
        schema_version=1,
        toolkit_version="0.0.1",
        generated_at="2026-05-04T00:00:00Z",
        part=Part(id="x1207"),
        reference_frame=ReferenceFrame(
            origin_description="pcb_bottom_left_corner",
            x_axis_description="along_long_edge",
            y_axis_description="along_short_edge",
            z_axis_description="up_from_pcb_bottom",
            units="mm",
        ),
        photos=[photo],
        features=[feature],
        quality_summary=QualitySummary(
            feature_count=1,
            triangulated_count=0,
            z_assumed_count=1,
            median_reprojection_rms_px=None,
            max_reprojection_rms_px=None,
            flags=[],
        ),
        session_artifacts=SessionArtifacts(
            overlay_pngs=[],
            events_jsonl="events.jsonl",
            manifest="manifest.json",
        ),
    )

    data = ann.to_dict()
    parsed = json.loads(json.dumps(data))

    assert parsed["photos"][0]["id"] == "top_down"
    assert parsed["features"][0]["id"] == "usb_c"
    assert parsed["features"][0]["measurements"]["z_assumed_mm"] == 0.0
    # Spec §6 ordering: summary metadata before per_photo_clicks.
    measurement_keys = list(parsed["features"][0]["measurements"].keys())
    assert measurement_keys.index("z_assumed_mm") < measurement_keys.index("per_photo_clicks")
    assert measurement_keys.index("z_assumed_reason") < measurement_keys.index("per_photo_clicks")


def test_validate_quality_flag_accepts_known() -> None:
    """Known flags pass."""
    validate_quality_flag("intrinsics_suspect_high_anchor_rms")
    validate_quality_flag("feature_clicked_only_once:gpio_socket_center")
    validate_quality_flag("photo_excluded_due_to_pose_failure:long_edge_a")


def test_validate_quality_flag_rejects_unknown() -> None:
    with pytest.raises(ValidationError, match="not a recognized flag"):
        validate_quality_flag("totally_made_up_flag")


def test_validate_quality_flag_parameterized_must_have_id() -> None:
    """Flags marked parameterized require ':<id>' suffix."""
    with pytest.raises(ValidationError, match="requires ':<id>'"):
        validate_quality_flag("feature_clicked_only_once")  # missing :<id>


def test_quality_flags_constant_is_complete() -> None:
    """The closed enum contains all six flags from spec §6."""
    expected = {
        "intrinsics_suspect_high_anchor_rms",
        "intrinsics_session_recommend_chessboard",
        "photo_excluded_due_to_pose_failure",
        "feature_clicked_only_once",
        "feature_high_triangulation_rms",
        "ultrawide_lens_rejected",
    }
    assert set(QUALITY_FLAGS.keys()) == expected


def test_validate_quality_flag_rejects_parameterless_with_id() -> None:
    """A parameterless flag with a stray :<id> must raise."""
    with pytest.raises(ValidationError, match="does not take an :<id>"):
        validate_quality_flag("intrinsics_suspect_high_anchor_rms:something")


def test_validate_quality_flag_rejects_empty_suffix() -> None:
    """A parameterized flag with empty :<id> must raise."""
    with pytest.raises(ValidationError, match="empty :<id>"):
        validate_quality_flag("feature_clicked_only_once:")


def test_validate_quality_flag_rejects_multi_colon_suffix() -> None:
    """A parameterized flag with multiple colons must raise (spec §6 single-token id)."""
    with pytest.raises(ValidationError, match="multi-colon"):
        validate_quality_flag("feature_clicked_only_once:foo:bar")


def test_validate_annotations_happy_path() -> None:
    """A document with valid flags passes validation."""
    data = {
        "quality_summary": {
            "flags": [
                "intrinsics_suspect_high_anchor_rms",
                "feature_clicked_only_once:gpio_socket_center",
            ],
        },
    }
    validate_annotations(data)  # no exception


def test_validate_annotations_missing_quality_summary_raises() -> None:
    """A document missing quality_summary raises."""
    with pytest.raises(ValidationError, match="missing required 'quality_summary'"):
        validate_annotations({})


def test_validate_annotations_invalid_flag_raises() -> None:
    """A document with an invalid flag raises (delegates to validate_quality_flag)."""
    data = {"quality_summary": {"flags": ["totally_made_up_flag"]}}
    with pytest.raises(ValidationError, match="not a recognized flag"):
        validate_annotations(data)
