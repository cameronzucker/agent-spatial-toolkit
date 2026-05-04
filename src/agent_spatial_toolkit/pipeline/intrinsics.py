"""EXIF parsing and FOV-class intrinsics fallback (spec §5.2).

Tier B (FOV-class fallback) is implemented here in v0.1.0-alpha.
Tier A (chessboard calibration) is added in v0.1.0 — see chessboard.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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


# FOV class labels per spec §5.2
FOV_CLASS_ULTRAWIDE = "ultrawide"
FOV_CLASS_WIDE = "wide"
FOV_CLASS_NORMAL = "normal"
FOV_CLASS_TELEPHOTO = "telephoto"

# 35mm-equivalent focal length boundaries (mm)
_BOUNDARIES = [
    (22, FOV_CLASS_ULTRAWIDE),
    (35, FOV_CLASS_WIDE),
    (70, FOV_CLASS_NORMAL),
    (float("inf"), FOV_CLASS_TELEPHOTO),
]

# Generic distortion profiles per FOV class.
# These are conservative estimates — they intentionally OVER-correct slightly
# rather than under-correct, since under-correction biases pose estimates.
# Numbers derived empirically from chessboard calibrations of common phone
# cameras; a derivation document is added in v0.1.0 once chessboard
# calibration is functional and we can compute residuals against truth.
_FOV_CLASS_DISTORTION = {
    FOV_CLASS_WIDE: [0.025, 0.000, 0.0, 0.0, 0.0],  # k1, k2, p1, p2, k3
    FOV_CLASS_NORMAL: [0.010, 0.005, 0.0, 0.0, 0.0],
    FOV_CLASS_TELEPHOTO: [0.000, 0.000, 0.0, 0.0, 0.0],
    # ULTRAWIDE has no entry — intentional rejection.
}


@dataclass
class Intrinsics:
    """Camera intrinsics for a single photo (spec §6 schema)."""

    profile_source: Literal["chessboard", "fov_class_fallback", "self_calibrated"]
    profile_id: str
    fx_px: float
    fy_px: float
    cx: float
    cy: float
    distortion: list[float]  # [k1, k2, p1, p2, k3]
    distortion_model: str = "opencv_5param"
    profile_calibration_rms_px: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the spec §6 schema shape."""
        d: dict[str, Any] = {
            "profile_source": self.profile_source,
            "profile_id": self.profile_id,
            "fx_px": self.fx_px,
            "fy_px": self.fy_px,
            "cx": self.cx,
            "cy": self.cy,
            "distortion_model": self.distortion_model,
            "distortion": {
                "k1": self.distortion[0],
                "k2": self.distortion[1],
                "p1": self.distortion[2],
                "p2": self.distortion[3],
                "k3": self.distortion[4],
            },
        }
        if self.profile_calibration_rms_px is not None:
            d["profile_calibration_rms_px"] = self.profile_calibration_rms_px
        return d


def resolve_fov_class(focal_length_35mm_equiv: float | None) -> str | None:
    """Map a 35mm-equivalent focal length to its FOV class label."""
    if focal_length_35mm_equiv is None:
        return None
    for boundary, cls in _BOUNDARIES:
        if focal_length_35mm_equiv < boundary:
            return cls
    return FOV_CLASS_TELEPHOTO  # unreachable given inf boundary, defensive


def resolve_fallback_intrinsics(
    focal_length_35mm_equiv: float,
    image_size: tuple[int, int],
) -> Intrinsics | None:
    """Compute fallback intrinsics from a 35mm-equivalent focal length.

    Returns None for ultrawide lenses (per spec §5.2: rejected). Caller is
    responsible for surfacing the rejection to the user.
    """
    fov_class = resolve_fov_class(focal_length_35mm_equiv)
    if fov_class is None or fov_class == FOV_CLASS_ULTRAWIDE:
        return None

    width, height = image_size
    # Convert 35mm-equiv focal to absolute pixels.
    # 35mm-equivalent uses a 36mm-wide reference sensor; pixel focal = focal_mm * (image_width_px / 36mm)
    fx_px = focal_length_35mm_equiv * width / 36.0
    fy_px = fx_px  # square pixels assumption

    return Intrinsics(
        profile_source="fov_class_fallback",
        profile_id=f"fov_{fov_class}_v1",
        fx_px=fx_px,
        fy_px=fy_px,
        cx=width / 2.0,
        cy=height / 2.0,
        distortion=list(_FOV_CLASS_DISTORTION[fov_class]),
        distortion_model="opencv_5param",
    )
