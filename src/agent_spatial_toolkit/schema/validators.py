"""Closed-enum validation for quality_summary.flags (spec §6)."""

from __future__ import annotations

from typing import Any


class ValidationError(ValueError):
    """Raised when a value does not satisfy the schema's closed contracts."""


# Closed enum of valid flag prefixes and whether they require ":<id>" suffix.
QUALITY_FLAGS: dict[str, bool] = {
    "intrinsics_suspect_high_anchor_rms": False,  # parameterless
    "intrinsics_session_recommend_chessboard": False,
    "intrinsics_estimated": False,  # EXIF missing, fell back to FOV-class (design §3)
    "photo_excluded_due_to_pose_failure": True,  # requires :<photo_id>
    "feature_clicked_only_once": True,  # requires :<feature_id>
    "feature_high_triangulation_rms": True,  # requires :<feature_id>
    "ultrawide_lens_rejected": True,  # requires :<photo_id>
}


def validate_quality_flag(flag: str) -> None:
    """Validate a single flag string against the closed enum."""
    has_id = ":" in flag
    if has_id:
        prefix, suffix = flag.split(":", maxsplit=1)
        if not suffix:
            raise ValidationError(f"Flag '{flag}' has empty :<id> suffix")
        if ":" in suffix:
            raise ValidationError(
                f"Flag '{flag}' has multi-colon suffix '{suffix}'; "
                "spec §6 grammar is 'prefix:<id>' with a single id token"
            )
    else:
        prefix = flag

    if prefix not in QUALITY_FLAGS:
        raise ValidationError(
            f"'{prefix}' is not a recognized flag. Valid prefixes: {sorted(QUALITY_FLAGS.keys())}"
        )

    needs_id = QUALITY_FLAGS[prefix]
    if needs_id and not has_id:
        raise ValidationError(f"Flag '{prefix}' requires ':<id>' suffix")
    if not needs_id and has_id:
        raise ValidationError(f"Flag '{prefix}' does not take an :<id> suffix")


def validate_annotations(data: dict[str, Any]) -> None:
    """Top-level validation of an annotations.json dict.

    v1 scope: validates quality_summary.flags against the closed enum and
    requires quality_summary to be present (spec §6 makes it a required
    top-level key). More invariants (visible_in references, pcb_xyz_mm[2]
    consistency, etc.) can be added as the implementation matures.

    Raises ValidationError if quality_summary is missing or any flag is
    invalid. Returns None on success.
    """
    if "quality_summary" not in data:
        raise ValidationError("annotations document is missing required 'quality_summary' key")
    flags = data["quality_summary"].get("flags", [])
    for flag in flags:
        validate_quality_flag(flag)
