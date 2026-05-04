"""Tests for pipeline/reproject.py — validation overlay PNG generation."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult
from agent_spatial_toolkit.pipeline.reproject import render_overlay


def _make_test_photo(tmp_path: Path) -> Path:
    img = Image.new("RGB", (1000, 1000), color=(100, 100, 100))
    p = tmp_path / "test_photo.jpg"
    img.save(p)
    return p


def _test_pose() -> PoseResult:
    return PoseResult(
        rvec=np.array([0.0, 0.0, 0.0]),
        tvec=np.array([0.0, 0.0, 200.0]),
        anchor_reprojection_rms_px=0.0,
        intrinsics_suspect=False,
        pose_solver="test",
    )


def _test_intrinsics() -> Intrinsics:
    return Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="test",
        fx_px=1000.0,
        fy_px=1000.0,
        cx=500.0,
        cy=500.0,
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )


def test_render_overlay_writes_png(tmp_path: Path) -> None:
    """Calling render_overlay produces a PNG at the requested path."""
    photo_path = _make_test_photo(tmp_path)
    out_path = tmp_path / "overlay.png"

    render_overlay(
        photo_path=photo_path,
        out_path=out_path,
        features=[
            ("origin", np.array([0.0, 0.0, 0.0])),
            ("x10y10", np.array([10.0, 10.0, 0.0])),
        ],
        pose=_test_pose(),
        intrinsics=_test_intrinsics(),
    )

    assert out_path.exists()
    overlay = Image.open(out_path)
    assert overlay.size == (1000, 1000)


def test_render_overlay_marks_origin_at_image_center(tmp_path: Path) -> None:
    """For our standard test pose (camera at (0,0,200) looking down with focal=1000),
    the world origin (0,0,0) projects to the image center (500, 500).
    The overlay should have a non-background color at that pixel."""
    photo_path = _make_test_photo(tmp_path)
    out_path = tmp_path / "overlay.png"

    render_overlay(
        photo_path=photo_path,
        out_path=out_path,
        features=[("origin", np.array([0.0, 0.0, 0.0]))],
        pose=_test_pose(),
        intrinsics=_test_intrinsics(),
    )

    overlay = np.array(Image.open(out_path))
    # The marker should be a circle centered at (500, 500) with non-gray color
    center_pixel = overlay[500, 500]
    assert not np.array_equal(center_pixel[:3], [100, 100, 100])  # not the background gray


def test_render_overlay_returns_out_path(tmp_path: Path) -> None:
    """render_overlay returns the out_path for caller convenience (matches emit_annotations)."""
    photo_path = _make_test_photo(tmp_path)
    out_path = tmp_path / "overlay.png"
    result = render_overlay(
        photo_path=photo_path,
        out_path=out_path,
        features=[],
        pose=_test_pose(),
        intrinsics=_test_intrinsics(),
    )
    assert result == out_path


def test_render_overlay_with_empty_features_writes_unchanged_photo(tmp_path: Path) -> None:
    """Empty features list still produces a valid PNG (no markers drawn)."""
    photo_path = _make_test_photo(tmp_path)
    out_path = tmp_path / "overlay.png"
    render_overlay(
        photo_path=photo_path,
        out_path=out_path,
        features=[],
        pose=_test_pose(),
        intrinsics=_test_intrinsics(),
    )
    assert out_path.exists()
    overlay = np.array(Image.open(out_path))
    # No markers drawn; image should be near-uniform background gray
    assert overlay.shape == (1000, 1000, 3)
    # Center pixel should still be background (no marker drawn)
    assert np.array_equal(overlay[500, 500, :3], [100, 100, 100])


def test_render_overlay_skips_off_frame_features(tmp_path: Path) -> None:
    """A feature far enough from origin to project off-frame is silently skipped."""
    photo_path = _make_test_photo(tmp_path)
    out_path = tmp_path / "overlay.png"
    # On a 1000x1000 image with focal=1000 and camera at (0,0,200),
    # a world point at X=10000 projects to pixel x ~= 50000 — well off-frame.
    render_overlay(
        photo_path=photo_path,
        out_path=out_path,
        features=[("off_frame", np.array([10000.0, 0.0, 0.0]))],
        pose=_test_pose(),
        intrinsics=_test_intrinsics(),
    )
    assert out_path.exists()
    overlay = np.array(Image.open(out_path))
    # No markers drawn on the visible frame
    assert np.array_equal(overlay[500, 500, :3], [100, 100, 100])


def test_render_overlay_raises_on_missing_photo(tmp_path: Path) -> None:
    """Missing photo file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="Cannot open"):
        render_overlay(
            photo_path=tmp_path / "does_not_exist.jpg",
            out_path=tmp_path / "overlay.png",
            features=[],
            pose=_test_pose(),
            intrinsics=_test_intrinsics(),
        )


def test_render_overlay_rejects_nan_pose() -> None:
    """A pose with NaN rvec must raise; do not silently skip the feature."""
    pose_nan = PoseResult(
        rvec=np.array([float("nan"), 0.0, 0.0]),
        tvec=np.array([0.0, 0.0, 200.0]),
        anchor_reprojection_rms_px=0.0,
        intrinsics_suspect=False,
        pose_solver="test",
    )
    # We never reach the file write, but provide valid paths anyway.
    with pytest.raises(ValueError, match="finite"):
        render_overlay(
            photo_path=Path("/tmp/__unused__"),
            out_path=Path("/tmp/__unused__.png"),
            features=[("origin", np.array([0.0, 0.0, 0.0]))],
            pose=pose_nan,
            intrinsics=_test_intrinsics(),
        )


def test_render_overlay_rejects_zero_focal() -> None:
    """Degenerate intrinsics (fx=0) must raise rather than draw at principal point."""
    intr_bad = Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="bad",
        fx_px=0.0,
        fy_px=1000.0,
        cx=500.0,
        cy=500.0,
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )
    with pytest.raises(ValueError, match="positive"):
        render_overlay(
            photo_path=Path("/tmp/__unused__"),
            out_path=Path("/tmp/__unused__.png"),
            features=[],
            pose=_test_pose(),
            intrinsics=intr_bad,
        )
