"""Tests for schema/models.py — dataclasses mirroring spec §6 schema."""

import json

from agent_spatial_toolkit.schema.models import (
    AnchorClick,
    Annotations,
    Feature,
    FeatureClick,
    FeatureMeasurement,
    Part,
    QualitySummary,
    ReferenceFrame,
    SessionArtifacts,
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
