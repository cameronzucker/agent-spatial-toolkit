"""Dataclasses mirroring the spec §6 annotations.json schema.

Each class has a to_dict() that emits the canonical JSON shape. Inverse
parsing (from_dict) is intentionally not provided in v1; agents and
the wizard server consume annotations.json by reading JSON directly per
spec §6.485-491.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ─────────────────────────────────────────────────────────────────────────
# Anchors and feature clicks
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class AnchorClick:
    """A reference-frame anchor: known 3D position, clicked pixel."""

    id: str
    pcb_xyz_mm: tuple[float, float, float]
    pixel: tuple[int, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pcb_xyz_mm": list(self.pcb_xyz_mm),
            "pixel": list(self.pixel),
        }


@dataclass
class FeatureClick:
    """One click of a feature in one photo."""

    photo: str
    pixel: tuple[int, int]
    reprojection_residual_px: float | None = None
    """None means this click came from a planar_intersection (no residual to report)."""

    def to_dict(self, include_residual: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {"photo": self.photo, "pixel": list(self.pixel)}
        if include_residual and self.reprojection_residual_px is not None:
            d["reprojection_residual_px"] = float(self.reprojection_residual_px)
        return d


# ─────────────────────────────────────────────────────────────────────────
# Photos
# ─────────────────────────────────────────────────────────────────────────


# Schema-layer twin of pipeline.intrinsics.CameraDetected; this version
# intentionally omits focal_length_35mm_equiv per spec §6 lines 389-393.
@dataclass
class CameraDetected:
    make: str | None = None
    model: str | None = None
    lens_label: str | None = None
    detection_source: Literal["exif", "user_specified", "fallback_generic"] = "exif"

    def to_dict(self) -> dict[str, Any]:
        return {
            "make": self.make,
            "model": self.model,
            "lens_label": self.lens_label,
            "detection_source": self.detection_source,
        }


@dataclass
class Photo:
    """One photo in a session — its provenance, intrinsics, pose, and anchors."""

    id: str
    path: str
    sha256: str
    camera_detected: CameraDetected
    intrinsics: dict[str, Any]  # produced by Intrinsics.to_dict()
    pose: dict[str, Any]  # produced by PoseResult.to_dict()
    anchors_clicked: list[AnchorClick] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "sha256": self.sha256,
            "camera_detected": self.camera_detected.to_dict(),
            "intrinsics": self.intrinsics,
            "pose": self.pose,
            "anchors_clicked": [a.to_dict() for a in self.anchors_clicked],
        }


# ─────────────────────────────────────────────────────────────────────────
# Features
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class FeatureMeasurement:
    """Provenance for a feature's pcb_xyz_mm value."""

    method: Literal[
        "planar_intersection",
        "triangulation_2_views",
        "triangulation_3_views",
        "triangulation_4_views",
        "triangulation_5_views",
        "triangulation_6_views",
    ]
    per_photo_clicks: list[FeatureClick]
    # Triangulation-only:
    triangulation_rms_px: float | None = None
    max_residual_px: float | None = None
    # Planar-only:
    z_assumed_mm: float | None = None
    z_assumed_reason: str | None = None

    def __post_init__(self) -> None:
        is_triangulation = self.method.startswith("triangulation")
        for i, click in enumerate(self.per_photo_clicks):
            if is_triangulation and click.reprojection_residual_px is None:
                raise ValueError(
                    f"FeatureMeasurement(method={self.method!r}) requires "
                    f"reprojection_residual_px on every click; "
                    f"per_photo_clicks[{i}] is None (spec §6 line 423)"
                )
            if not is_triangulation and click.reprojection_residual_px is not None:
                raise ValueError(
                    f"FeatureMeasurement(method={self.method!r}) must NOT have "
                    f"reprojection_residual_px on clicks; "
                    f"per_photo_clicks[{i}].reprojection_residual_px="
                    f"{click.reprojection_residual_px} (spec §6 line 435-436)"
                )

    def to_dict(self) -> dict[str, Any]:
        is_triangulation = self.method.startswith("triangulation")
        d: dict[str, Any] = {"method": self.method}
        # Spec §6 §419-425 / §432-438: summary metadata BEFORE per_photo_clicks.
        if is_triangulation:
            d["triangulation_rms_px"] = self.triangulation_rms_px
            d["max_residual_px"] = self.max_residual_px
        else:  # planar_intersection
            d["z_assumed_mm"] = self.z_assumed_mm
            d["z_assumed_reason"] = self.z_assumed_reason
        d["per_photo_clicks"] = [
            c.to_dict(include_residual=is_triangulation) for c in self.per_photo_clicks
        ]
        return d


@dataclass
class Feature:
    """A named feature with its part-local 3D position."""

    id: str
    visible_in: list[str]
    pcb_xyz_mm: tuple[float, float, float]
    measurements: FeatureMeasurement
    user_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "visible_in": list(self.visible_in),
            "pcb_xyz_mm": list(self.pcb_xyz_mm),
            "measurements": self.measurements.to_dict(),
        }
        if self.user_tags:
            d["user_tags"] = list(self.user_tags)
        return d


# ─────────────────────────────────────────────────────────────────────────
# Top-level
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class Part:
    id: str
    display_name: str | None = None
    part_class: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            k: v
            for k, v in {
                "id": self.id,
                "display_name": self.display_name,
                "part_class": self.part_class,
                "notes": self.notes,
            }.items()
            if v is not None or k == "id"
        }


@dataclass
class ReferenceFrame:
    origin_description: str
    x_axis_description: str
    y_axis_description: str
    z_axis_description: str
    units: Literal["mm"] = "mm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin_description": self.origin_description,
            "x_axis_description": self.x_axis_description,
            "y_axis_description": self.y_axis_description,
            "z_axis_description": self.z_axis_description,
            "units": self.units,
        }


@dataclass
class QualitySummary:
    feature_count: int
    triangulated_count: int
    z_assumed_count: int
    median_reprojection_rms_px: float | None
    max_reprojection_rms_px: float | None
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_count": self.feature_count,
            "triangulated_count": self.triangulated_count,
            "z_assumed_count": self.z_assumed_count,
            "median_reprojection_rms_px": self.median_reprojection_rms_px,
            "max_reprojection_rms_px": self.max_reprojection_rms_px,
            "flags": list(self.flags),
        }


@dataclass
class SessionArtifacts:
    overlay_pngs: list[str]
    events_jsonl: str
    manifest: str
    chessboard_calibration_image: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "overlay_pngs": list(self.overlay_pngs),
            "events_jsonl": self.events_jsonl,
            "manifest": self.manifest,
        }
        if self.chessboard_calibration_image is not None:
            d["chessboard_calibration_image"] = self.chessboard_calibration_image
        return d


@dataclass
class Annotations:
    """Top-level annotations.json contents."""

    schema_version: int
    toolkit_version: str
    generated_at: str
    part: Part
    reference_frame: ReferenceFrame
    photos: list[Photo]
    features: list[Feature]
    quality_summary: QualitySummary
    session_artifacts: SessionArtifacts

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "toolkit_version": self.toolkit_version,
            "generated_at": self.generated_at,
            "part": self.part.to_dict(),
            "reference_frame": self.reference_frame.to_dict(),
            "photos": [p.to_dict() for p in self.photos],
            "features": [f.to_dict() for f in self.features],
            "quality_summary": self.quality_summary.to_dict(),
            "session_artifacts": self.session_artifacts.to_dict(),
        }
