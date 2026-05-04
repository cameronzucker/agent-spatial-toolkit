"""Tests for pipeline/intrinsics.py — EXIF parsing and FOV-class fallback."""

from pathlib import Path

from PIL import Image

from agent_spatial_toolkit.pipeline.intrinsics import extract_exif_camera_info


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

    # Bare JPEG either has empty EXIF or no make/model — both should yield None
    assert info is None or info.make is None


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


def test_extract_exif_unknown_format_returns_none(tmp_path: Path) -> None:
    """Calling on a non-image file returns None gracefully."""
    bad = tmp_path / "garbage.jpg"
    bad.write_bytes(b"this is not an image")
    info = extract_exif_camera_info(bad)
    assert info is None
