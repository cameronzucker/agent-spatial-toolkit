"""EXIF parsing and FOV-class intrinsics fallback (spec §5.2).

Tier B (FOV-class fallback) is implemented here in v0.1.0-alpha.
Tier A (chessboard calibration) is added in v0.1.0 — see chessboard.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import ExifTags, Image

# EXIF tag name → tag ID lookup (built once from PIL)
_TAG_NAME_TO_ID = {name: tag_id for tag_id, name in ExifTags.TAGS.items()}


@dataclass
class CameraDetected:
    """Camera + lens identification extracted from photo metadata."""

    make: str | None = None
    model: str | None = None
    focal_length_35mm_equiv: float | None = None
    lens_label: str | None = None
    detection_source: Literal["exif", "user_specified", "fallback_generic"] = "exif"


def _clean_str(value: object) -> str | None:
    """Coerce an EXIF string value to a stripped non-empty str, or None."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _coerce_float(value: object) -> float | None:
    """Coerce an EXIF numeric value to float, or None on malformed input."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_exif_camera_info(image_path: Path | str) -> CameraDetected | None:
    """Read EXIF camera identification from an image file.

    Returns None if the file cannot be opened as an image. Returns
    CameraDetected with whatever fields were present (others as None) if
    the image has at least one camera-identifying tag.
    """
    image_path = Path(image_path)
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
    except (OSError, ValueError, Image.DecompressionBombError, Image.UnidentifiedImageError):
        return None

    if not exif:
        return None

    make = _clean_str(exif.get(_TAG_NAME_TO_ID.get("Make")))
    model = _clean_str(exif.get(_TAG_NAME_TO_ID.get("Model")))
    focal_35 = _coerce_float(exif.get(_TAG_NAME_TO_ID.get("FocalLengthIn35mmFilm")))
    lens = _clean_str(exif.get(_TAG_NAME_TO_ID.get("LensModel")))

    # If nothing identifying is present, return None
    if make is None and model is None and focal_35 is None and lens is None:
        return None

    return CameraDetected(
        make=make,
        model=model,
        focal_length_35mm_equiv=focal_35,
        lens_label=lens,
        detection_source="exif",
    )
