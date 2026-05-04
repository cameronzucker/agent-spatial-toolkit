"""Assemble annotations.json from session state (spec §6)."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from agent_spatial_toolkit import __version__
from agent_spatial_toolkit.schema.models import (
    Annotations,
    Feature,
    Part,
    Photo,
    QualitySummary,
    ReferenceFrame,
    SessionArtifacts,
)
from agent_spatial_toolkit.schema.validators import validate_annotations


@dataclass
class SessionState:
    """In-memory session state assembled by the server during a session."""

    part_id: str
    part_display_name: str | None
    part_class: str | None
    notes: str | None
    reference_frame: dict
    photos: list[Photo]
    features: list[Feature]
    flags: list[str]
    events_jsonl_filename: str
    manifest_filename: str
    overlay_pngs: list[str]
    chessboard_calibration_image: str | None = None


def _now_iso_utc() -> str:
    """ISO-8601 UTC with Z suffix (spec §6 example uses this)."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compute_quality_summary(features: list[Feature], flags: list[str]) -> QualitySummary:
    """Derive quality_summary fields from the feature list."""
    triangulated_rmses: list[float] = []
    triangulated_count = 0
    z_assumed_count = 0
    for f in features:
        if f.measurements.method.startswith("triangulation"):
            triangulated_count += 1
            if f.measurements.triangulation_rms_px is not None:
                triangulated_rmses.append(f.measurements.triangulation_rms_px)
        else:
            z_assumed_count += 1

    return QualitySummary(
        feature_count=len(features),
        triangulated_count=triangulated_count,
        z_assumed_count=z_assumed_count,
        median_reprojection_rms_px=median(triangulated_rmses) if triangulated_rmses else None,
        max_reprojection_rms_px=max(triangulated_rmses) if triangulated_rmses else None,
        flags=list(flags),
    )


def emit_annotations(state: SessionState, session_dir: Path) -> Path:
    """Write annotations.json to session_dir and return its path.

    Validates flags against the closed enum before writing. Raises
    ValidationError on invalid flags.
    """
    quality = _compute_quality_summary(state.features, state.flags)

    ann = Annotations(
        schema_version=1,
        toolkit_version=__version__,
        generated_at=_now_iso_utc(),
        part=Part(
            id=state.part_id,
            display_name=state.part_display_name,
            part_class=state.part_class,
            notes=state.notes,
        ),
        reference_frame=ReferenceFrame(**state.reference_frame),
        photos=state.photos,
        features=state.features,
        quality_summary=quality,
        session_artifacts=SessionArtifacts(
            overlay_pngs=state.overlay_pngs,
            events_jsonl=state.events_jsonl_filename,
            manifest=state.manifest_filename,
            chessboard_calibration_image=state.chessboard_calibration_image,
        ),
    )

    data = ann.to_dict()
    validate_annotations(data)  # raises ValidationError if flags are invalid

    # Atomic write: emit to tmp, fsync(?), then atomic rename. This makes
    # annotations.json either complete or absent — never truncated.
    out_path = session_dir / "annotations.json"
    tmp_path = out_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(out_path)
    return out_path
