"""Tests for pipeline/reproject.py — validation overlay PNG generation."""

from pathlib import Path

import numpy as np
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
