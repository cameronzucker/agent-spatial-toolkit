"""Closed-enum validation for quality_summary.flags (spec §6)."""

from __future__ import annotations


class ValidationError(ValueError):
    """Raised when a value does not satisfy the schema's closed contracts."""


# Closed enum of valid flag prefixes and whether they require ":<id>" suffix.
QUALITY_FLAGS: dict[str, bool] = {
    "intrinsics_suspect_high_anchor_rms": False,  # parameterless
    "intrinsics_session_recommend_chessboard": False,
    "photo_excluded_due_to_pose_failure": True,  # requires :<photo_id>
    "feature_clicked_only_once": True,  # requires :<feature_id>
    "feature_high_triangulation_rms": True,  # requires :<feature_id>
    "ultrawide_lens_rejected": True,  # requires :<photo_id>
}


def validate_quality_flag(flag: str) -> None:
    """Validate a single flag string against the closed enum."""
    if ":" in flag:
        prefix, _, suffix = flag.partition(":")
        if not suffix:
            raise ValidationError(f"Flag '{flag}' has empty :<id> suffix")
    else:
        prefix = flag

    if prefix not in QUALITY_FLAGS:
        raise ValidationError(
            f"'{prefix}' is not a recognized flag. Valid prefixes: {sorted(QUALITY_FLAGS.keys())}"
        )

    needs_id = QUALITY_FLAGS[prefix]
    has_id = ":" in flag
    if needs_id and not has_id:
        raise ValidationError(f"Flag '{prefix}' requires ':<id>' suffix")
    if not needs_id and has_id:
        raise ValidationError(f"Flag '{prefix}' does not take an :<id> suffix")


def validate_annotations(data: dict) -> None:
    """Top-level validation of an annotations.json dict.

    Currently checks: quality_summary.flags are all valid. More invariants
    can be added (e.g., feature.visible_in references actual photo IDs;
    pcb_xyz_mm[2] consistent with z_assumed_mm; etc.) as the implementation
    matures.
    """
    flags = data.get("quality_summary", {}).get("flags", [])
    for flag in flags:
        validate_quality_flag(flag)
