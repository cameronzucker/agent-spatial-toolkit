"""Lock in backwards-compat: v0.0.1-shaped annotations.json must still
round-trip cleanly after PR-1's additive schema changes.

If this test ever fails, the wizard-redesign PR series violated its
'additive only' commitment per docs/specs/2026-05-05-wizard-ux-redesign-design.md §6.
"""

from __future__ import annotations

import json

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
from agent_spatial_toolkit.schema.validators import validate_annotations


def _v0_0_1_fixture() -> Annotations:
    """A representative annotations.json shape from current main (pre-redesign)."""
    return Annotations(
        schema_version=1,
        toolkit_version="0.0.1",
        generated_at="2026-05-04T12:00:00Z",
        part=Part(id="testpart_001", display_name="Test PCB", part_class="pcb"),
        reference_frame=ReferenceFrame(
            origin_description="upper-left corner of PCB top face",
            x_axis_description="along the long edge",
            y_axis_description="along the short edge",
            z_axis_description="up out of PCB top face",
        ),
        photos=[
            Photo(
                id="photo_001",
                path="photos/photo_001.jpg",
                sha256="a" * 64,
                camera_detected=CameraDetected(
                    make="Apple", model="iPhone 14", lens_label="main", detection_source="exif"
                ),
                intrinsics={"fx_px": 3000.0, "fy_px": 3000.0, "cx_px": 2016.0, "cy_px": 1512.0},
                pose={"rvec": [0.0, 0.0, 0.0], "tvec": [0.0, 0.0, 300.0]},
                anchors_clicked=[
                    AnchorClick(id="corner_a", pcb_xyz_mm=(0.0, 0.0, 0.0), pixel=(100, 100)),
                ],
            ),
        ],
        features=[
            Feature(
                id="usb_c",
                visible_in=["photo_001"],
                pcb_xyz_mm=(12.4, 28.3, 0.0),
                measurements=FeatureMeasurement(
                    method="planar_intersection",
                    per_photo_clicks=[
                        FeatureClick(photo="photo_001", pixel=(150, 200)),
                    ],
                    z_assumed_mm=0.0,
                    z_assumed_reason="PCB top, z=0 assumed",
                ),
            ),
        ],
        quality_summary=QualitySummary(
            feature_count=1,
            triangulated_count=0,
            z_assumed_count=1,
            median_reprojection_rms_px=None,
            max_reprojection_rms_px=None,
            flags=["feature_clicked_only_once:usb_c"],
        ),
        session_artifacts=SessionArtifacts(
            overlay_pngs=["overlays/photo_001.png"],
            events_jsonl="events.jsonl",
            manifest="manifest.json",
        ),
    )


def test_v0_0_1_fixture_emits_and_validates() -> None:
    """An old-shape annotations.json must still emit and validate after PR-1."""
    fixture = _v0_0_1_fixture()
    d = fixture.to_dict()
    # Validate against the (extended) closed-flag enum.
    validate_annotations(d)


def test_v0_0_1_fixture_omits_new_optional_fields() -> None:
    """PR-1's additive fields (noisy, warning) must not appear in
    output when not set — guarantees byte-stable output for old-style data."""
    fixture = _v0_0_1_fixture()
    d = fixture.to_dict()
    feature_dict = d["features"][0]
    assert "noisy" not in feature_dict, (
        "default-False noisy must be omitted; old-shape annotations.json "
        "must remain byte-identical for features that don't use the new field"
    )
    assert "warning" not in feature_dict, (
        "default-None warning must be omitted; old-shape annotations.json "
        "must remain byte-identical for features that don't use the new field"
    )


def test_v0_0_1_fixture_round_trips_through_json() -> None:
    """JSON-serialize and parse the fixture; required keys all survive."""
    fixture = _v0_0_1_fixture()
    text = json.dumps(fixture.to_dict(), ensure_ascii=False)
    parsed = json.loads(text)
    assert parsed["schema_version"] == 1
    assert parsed["part"]["id"] == "testpart_001"
    assert parsed["part"]["display_name"] == "Test PCB"
    assert parsed["features"][0]["id"] == "usb_c"
    assert parsed["features"][0]["measurements"]["method"] == "planar_intersection"
    assert parsed["quality_summary"]["flags"] == ["feature_clicked_only_once:usb_c"]


def _triangulation_branch_fixture() -> Annotations:
    """A representative annotations.json shape exercising the triangulation
    branch (method='triangulation_3_views') so backwards-compat coverage
    isn't planar-only. Three photos, three clicks, residuals on every click."""
    photos = [
        Photo(
            id=f"photo_00{i}",
            path=f"photos/photo_00{i}.jpg",
            sha256=str(i) * 64,
            camera_detected=CameraDetected(
                make="Apple", model="iPhone 14", lens_label="main", detection_source="exif"
            ),
            intrinsics={"fx_px": 3000.0, "fy_px": 3000.0, "cx_px": 2016.0, "cy_px": 1512.0},
            pose={"rvec": [0.0, 0.0, 0.0], "tvec": [0.0, 0.0, 300.0 + i]},
            anchors_clicked=[
                AnchorClick(id="corner_a", pcb_xyz_mm=(0.0, 0.0, 0.0), pixel=(100, 100)),
            ],
        )
        for i in (1, 2, 3)
    ]
    return Annotations(
        schema_version=1,
        toolkit_version="0.0.1",
        generated_at="2026-05-04T12:00:00Z",
        part=Part(id="testpart_002", display_name="Test PCB triangulated", part_class="pcb"),
        reference_frame=ReferenceFrame(
            origin_description="upper-left corner of PCB top face",
            x_axis_description="along the long edge",
            y_axis_description="along the short edge",
            z_axis_description="up out of PCB top face",
        ),
        photos=photos,
        features=[
            Feature(
                id="led_d1",
                visible_in=["photo_001", "photo_002", "photo_003"],
                pcb_xyz_mm=(20.0, 15.0, 1.2),
                measurements=FeatureMeasurement(
                    method="triangulation_3_views",
                    triangulation_rms_px=0.42,
                    max_residual_px=0.61,
                    per_photo_clicks=[
                        FeatureClick(
                            photo="photo_001", pixel=(150, 200), reprojection_residual_px=0.35
                        ),
                        FeatureClick(
                            photo="photo_002", pixel=(160, 210), reprojection_residual_px=0.61
                        ),
                        FeatureClick(
                            photo="photo_003", pixel=(170, 220), reprojection_residual_px=0.30
                        ),
                    ],
                ),
            ),
        ],
        quality_summary=QualitySummary(
            feature_count=1,
            triangulated_count=1,
            z_assumed_count=0,
            median_reprojection_rms_px=0.42,
            max_reprojection_rms_px=0.42,
            flags=[],
        ),
        session_artifacts=SessionArtifacts(
            overlay_pngs=["overlays/photo_001.png"],
            events_jsonl="events.jsonl",
            manifest="manifest.json",
        ),
    )


def test_triangulation_branch_fixture_emits_and_validates() -> None:
    """Triangulation-branch fixture must round-trip through to_dict() and
    validate against the (extended) closed-flag enum. Locks in that PR-1's
    additive changes did not regress the triangulation emit path — every
    per-photo click keeps its reprojection_residual_px, and summary fields
    triangulation_rms_px / max_residual_px appear in canonical order."""
    fixture = _triangulation_branch_fixture()
    d = fixture.to_dict()
    validate_annotations(d)
    measurements = d["features"][0]["measurements"]
    assert measurements["method"] == "triangulation_3_views"
    assert measurements["triangulation_rms_px"] == 0.42
    assert measurements["max_residual_px"] == 0.61
    assert len(measurements["per_photo_clicks"]) == 3
    for click in measurements["per_photo_clicks"]:
        assert "reprojection_residual_px" in click
    # Spec §6 §419-425: summary metadata must precede per_photo_clicks.
    keys = list(measurements.keys())
    assert keys.index("triangulation_rms_px") < keys.index("per_photo_clicks")
    assert keys.index("max_residual_px") < keys.index("per_photo_clicks")
    # PR-1 additive fields stay omitted when default.
    assert "noisy" not in d["features"][0]
    assert "warning" not in d["features"][0]
