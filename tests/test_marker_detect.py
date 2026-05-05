"""Tests for server.marker_detect — wraps cv2.aruco.detectMarkers.

Synthetic ArUco markers are generated programmatically so the test
doesn't depend on any external fixture image.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from agent_spatial_toolkit.server.marker_detect import (
    MARKER_DICT,
    MARKER_ID,
    detect_marker_corners,
)


def _render_aruco_image(
    marker_size_px: int = 200,
    canvas_size_px: int = 800,
    marker_offset_px: tuple[int, int] = (300, 300),
) -> np.ndarray:
    """Render a synthetic image with one ArUco marker placed at a known offset."""
    aruco_dict = cv2.aruco.getPredefinedDictionary(MARKER_DICT)
    marker = cv2.aruco.generateImageMarker(aruco_dict, MARKER_ID, marker_size_px)
    canvas = np.full((canvas_size_px, canvas_size_px), 255, dtype=np.uint8)
    ox, oy = marker_offset_px
    canvas[oy : oy + marker_size_px, ox : ox + marker_size_px] = marker
    return canvas


def test_detect_returns_four_corners_when_marker_present(tmp_path: Path) -> None:
    """A synthetically-rendered marker is detected; corners come back in the
    documented order."""
    canvas = _render_aruco_image(marker_size_px=200, marker_offset_px=(300, 300))
    out = tmp_path / "marker.png"
    cv2.imwrite(str(out), canvas)

    corners = detect_marker_corners(out)
    assert corners is not None
    assert len(corners) == 4
    # cv2.aruco returns corners in TL, TR, BR, BL order — the wizard's
    # /api/reference contract calls for clockwise-from-top-left, which is
    # the same order. Each corner is (x, y).
    for c in corners:
        assert isinstance(c, tuple)
        assert len(c) == 2
    # Roughly: TL ≈ (300, 300), TR ≈ (500, 300), BR ≈ (500, 500), BL ≈ (300, 500).
    # Allow ±2 px tolerance for sub-pixel detection.
    assert abs(corners[0][0] - 300) < 2 and abs(corners[0][1] - 300) < 2
    assert abs(corners[1][0] - 500) < 2 and abs(corners[1][1] - 300) < 2
    assert abs(corners[2][0] - 500) < 2 and abs(corners[2][1] - 500) < 2
    assert abs(corners[3][0] - 300) < 2 and abs(corners[3][1] - 500) < 2


def test_detect_returns_none_when_no_marker(tmp_path: Path) -> None:
    """A blank canvas yields None — the user falls back to manual click."""
    canvas = np.full((800, 800), 200, dtype=np.uint8)
    out = tmp_path / "blank.png"
    cv2.imwrite(str(out), canvas)
    assert detect_marker_corners(out) is None


def test_detect_returns_first_marker_when_multiple_present(tmp_path: Path) -> None:
    """If multiple markers happen to be in frame, return the one with our
    known MARKER_ID (the wizard only renders one)."""
    aruco_dict = cv2.aruco.getPredefinedDictionary(MARKER_DICT)
    canvas = np.full((800, 800), 255, dtype=np.uint8)
    target = cv2.aruco.generateImageMarker(aruco_dict, MARKER_ID, 200)
    decoy = cv2.aruco.generateImageMarker(aruco_dict, MARKER_ID + 1, 100)
    canvas[100:300, 100:300] = target
    canvas[500:600, 500:600] = decoy
    out = tmp_path / "two_markers.png"
    cv2.imwrite(str(out), canvas)

    corners = detect_marker_corners(out)
    assert corners is not None
    # Should find the MARKER_ID = 0 marker at (100, 100), not the decoy.
    assert abs(corners[0][0] - 100) < 2 and abs(corners[0][1] - 100) < 2


def test_detect_raises_on_missing_file(tmp_path: Path) -> None:
    """A non-existent path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        detect_marker_corners(tmp_path / "no_such_file.png")
