"""Tests for pipeline/intrinsics.py — EXIF parsing and FOV-class fallback."""

from pathlib import Path

import pytest
from PIL import Image

from agent_spatial_toolkit.pipeline.intrinsics import (
    FOV_CLASS_NORMAL,
    FOV_CLASS_TELEPHOTO,
    FOV_CLASS_ULTRAWIDE,
    FOV_CLASS_WIDE,
    Intrinsics,
    extract_exif_camera_info,
    resolve_fallback_intrinsics,
    resolve_fov_class,
)


def _make_jpeg_with_exif(tmp_path: Path, exif_dict: dict) -> Path:
    """Helper: create a minimal JPEG with the given EXIF tags."""
    img = Image.new("RGB", (4032, 3024), color=(128, 128, 128))
    exif = img.getexif()
    for tag_id, value in exif_dict.items():
        exif[tag_id] = value
    out = tmp_path / "test.jpg"
    img.save(out, "JPEG", exif=exif)
    return out


def test_extract_exif_camera_info_with_full_metadata(tmp_path: Path) -> None:
    """A JPEG with Make, Model, FocalLengthIn35mmFilm yields CameraDetected with all fields."""
    # EXIF tag IDs: Make=271, Model=272, FocalLengthIn35mmFilm=41989, LensModel=42036
    img_path = _make_jpeg_with_exif(
        tmp_path,
        {
            271: "Samsung",
            272: "SM-S908U",
            41989: 70,
            42036: "S22 Ultra Telephoto",
        },
    )

    info = extract_exif_camera_info(img_path)

    assert info is not None
    assert info.make == "Samsung"
    assert info.model == "SM-S908U"
    assert info.focal_length_35mm_equiv == 70
    assert info.lens_label == "S22 Ultra Telephoto"
    assert info.detection_source == "exif"


def test_extract_exif_returns_none_when_no_exif(tmp_path: Path) -> None:
    """A bare image with no EXIF returns None."""
    img = Image.new("RGB", (1024, 768), color=(0, 0, 0))
    out = tmp_path / "bare.jpg"
    img.save(out, "JPEG")

    info = extract_exif_camera_info(out)

    assert info is None


def test_extract_exif_handles_partial_metadata(tmp_path: Path) -> None:
    """If Make is present but Model is missing, return what we have."""
    img_path = _make_jpeg_with_exif(
        tmp_path,
        {
            271: "Apple",
            # No model
        },
    )
    info = extract_exif_camera_info(img_path)
    assert info is not None
    assert info.make == "Apple"
    assert info.model is None


def test_extract_exif_treats_whitespace_only_strings_as_missing(tmp_path: Path) -> None:
    """Whitespace-only EXIF string tags are equivalent to missing tags."""
    img_path = _make_jpeg_with_exif(
        tmp_path,
        {
            271: "   ",  # Make: whitespace only
            272: "\t\n",  # Model: whitespace only
        },
    )
    info = extract_exif_camera_info(img_path)
    assert info is None


def test_extract_exif_returns_none_when_only_non_camera_tags(tmp_path: Path) -> None:
    """EXIF with only non-camera tags (e.g., DateTime) yields None."""
    # 306 is DateTime — present but does not identify the camera
    img_path = _make_jpeg_with_exif(tmp_path, {306: "2026:05:03 22:30:00"})
    info = extract_exif_camera_info(img_path)
    assert info is None


def test_extract_exif_drops_malformed_focal_length(tmp_path: Path) -> None:
    """A non-numeric FocalLengthIn35mmFilm yields None for the field, not an exception."""
    img_path = _make_jpeg_with_exif(
        tmp_path,
        {
            271: "Apple",
            41989: "not a number",  # malformed focal length
        },
    )
    info = extract_exif_camera_info(img_path)
    assert info is not None
    assert info.make == "Apple"
    assert info.focal_length_35mm_equiv is None


def test_extract_exif_unknown_format_returns_none(tmp_path: Path) -> None:
    """Calling on a non-image file returns None gracefully."""
    bad = tmp_path / "garbage.jpg"
    bad.write_bytes(b"this is not an image")
    info = extract_exif_camera_info(bad)
    assert info is None


@pytest.mark.parametrize(
    "focal_35mm,expected_class",
    [
        (10, FOV_CLASS_ULTRAWIDE),
        (21, FOV_CLASS_ULTRAWIDE),
        (22, FOV_CLASS_WIDE),
        (28, FOV_CLASS_WIDE),
        (34, FOV_CLASS_WIDE),
        (35, FOV_CLASS_NORMAL),
        (50, FOV_CLASS_NORMAL),
        (69, FOV_CLASS_NORMAL),
        (70, FOV_CLASS_NORMAL),
        (70.001, FOV_CLASS_TELEPHOTO),
        (135, FOV_CLASS_TELEPHOTO),
        (300, FOV_CLASS_TELEPHOTO),
    ],
)
def test_resolve_fov_class(focal_35mm: float, expected_class: str) -> None:
    """FOV-class boundaries match spec §5.2."""
    assert resolve_fov_class(focal_35mm) == expected_class


def test_resolve_fov_class_none_returns_none() -> None:
    """No focal length → no class."""
    assert resolve_fov_class(None) is None


def test_resolve_fov_class_rejects_nan() -> None:
    """NaN focal length is treated as missing, not classified."""
    assert resolve_fov_class(float("nan")) is None


def test_resolve_fov_class_rejects_inf() -> None:
    """Infinite focal length is treated as missing, not classified."""
    assert resolve_fov_class(float("inf")) is None


def test_resolve_fallback_intrinsics_rejects_nan() -> None:
    """NaN focal length yields None, not NaN-laced Intrinsics."""
    assert resolve_fallback_intrinsics(float("nan"), (4032, 3024)) is None


def test_resolve_fallback_intrinsics_rejects_zero_dimensions() -> None:
    """Zero or negative image dimensions yield None."""
    assert resolve_fallback_intrinsics(50.0, (0, 3024)) is None
    assert resolve_fallback_intrinsics(50.0, (4032, 0)) is None
    assert resolve_fallback_intrinsics(50.0, (-1, 3024)) is None


def test_resolve_fallback_intrinsics_orientation_independent() -> None:
    """Same focal+pixel-count produces same fx in either orientation."""
    landscape = resolve_fallback_intrinsics(50.0, (4032, 3024))
    portrait = resolve_fallback_intrinsics(50.0, (3024, 4032))
    assert landscape is not None and portrait is not None
    assert landscape.fx_px == pytest.approx(portrait.fx_px)
    assert landscape.fy_px == pytest.approx(portrait.fy_px)
    # Principal point still tracks the supplied dimensions
    assert landscape.cx == pytest.approx(2016.0)
    assert portrait.cx == pytest.approx(1512.0)


def test_resolve_fallback_intrinsics_telephoto() -> None:
    """A telephoto lens gets near-zero distortion."""
    intrinsics = resolve_fallback_intrinsics(
        focal_length_35mm_equiv=85.0,
        image_size=(4032, 3024),
    )
    assert intrinsics is not None
    assert intrinsics.profile_source == "fov_class_fallback"
    # Telephoto profile: zero distortion
    assert intrinsics.distortion == [0.0, 0.0, 0.0, 0.0, 0.0]
    # Principal point at image center
    assert intrinsics.cx == pytest.approx(2016.0)
    assert intrinsics.cy == pytest.approx(1512.0)


def test_resolve_fallback_intrinsics_ultrawide_rejects() -> None:
    """An ultrawide lens triggers a rejection (returns None with rejection flag)."""
    intrinsics = resolve_fallback_intrinsics(
        focal_length_35mm_equiv=14.0,
        image_size=(4032, 3024),
    )
    assert intrinsics is None  # rejected — caller must check focal length first


def test_resolve_fallback_intrinsics_normal_focal() -> None:
    """A normal-focal photo gets mild distortion + correct fx/fy from focal length."""
    intrinsics = resolve_fallback_intrinsics(
        focal_length_35mm_equiv=50.0,
        image_size=(4032, 3024),
    )
    assert intrinsics is not None
    # 50mm equiv on 4032px wide sensor: fx = 50/36 * 4032 = 5600 px
    # (using 36mm reference width for 35mm-equivalent)
    assert intrinsics.fx_px == pytest.approx(5600.0, rel=0.01)
    # Pinned to the plan's literal value. The sign convention (positive k1 →
    # pincushion in OpenCV's model) is flagged for validation against
    # chessboard ground truth in Tier A — see issue tracker.
    assert intrinsics.distortion == [0.010, 0.005, 0.0, 0.0, 0.0]


def test_intrinsics_dataclass_serializes_to_dict() -> None:
    """Intrinsics has a to_dict() method matching the spec §6 schema shape."""
    intrinsics = Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="fov_normal_v1",
        fx_px=5600.0,
        fy_px=5600.0,
        cx=2016.0,
        cy=1512.0,
        distortion=[0.01, 0.005, 0.0, 0.0, 0.0],
        distortion_model="opencv_5param",
    )
    d = intrinsics.to_dict()
    assert d["profile_source"] == "fov_class_fallback"
    assert d["distortion"]["k1"] == 0.01
    assert d["distortion"]["k2"] == 0.005
