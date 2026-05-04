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
    except (OSError, ValueError, Image.UnidentifiedImageError):
        return None

    if not exif:
        return None

    make = exif.get(_TAG_NAME_TO_ID.get("Make"))
    model = exif.get(_TAG_NAME_TO_ID.get("Model"))
    focal_35 = exif.get(_TAG_NAME_TO_ID.get("FocalLengthIn35mmFilm"))
    lens = exif.get(_TAG_NAME_TO_ID.get("LensModel"))

    # If nothing identifying is present, return None
    if all(v is None for v in (make, model, focal_35, lens)):
        return None

    return CameraDetected(
        make=str(make).strip() if make else None,
        model=str(model).strip() if model else None,
        focal_length_35mm_equiv=float(focal_35) if focal_35 is not None else None,
        lens_label=str(lens).strip() if lens else None,
        detection_source="exif",
    )
