"""ChArUco marker auto-detection for the wizard's marker reference path.

When the user picks "Printed marker" as their scale reference, the wizard
serves a known PDF carrying an ArUco marker (DICT_4X4_50, ID 0, 100 mm
square). On photo upload the wizard calls /api/marker_detect/<photo_id>;
this module is what that endpoint calls.

Returns the 4 corner pixel coordinates clockwise from top-left, matching
the order /api/reference's pixel_corners argument expects (so the UI can
forward the result directly without reordering).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

# v1 marker dictionary + ID. The wizard's served PDF must match these.
MARKER_DICT = cv2.aruco.DICT_4X4_50
MARKER_ID = 0


def detect_marker_corners(
    photo_path: Path | str,
) -> list[tuple[float, float]] | None:
    """Detect the wizard's ArUco marker in `photo_path`.

    Returns the 4 corner coords clockwise from top-left, or None if the
    marker is not found. Raises FileNotFoundError if the path doesn't
    exist.
    """
    path = Path(photo_path)
    if not path.is_file():
        raise FileNotFoundError(f"photo not found: {path}")

    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"could not read image: {path}")

    aruco_dict = cv2.aruco.getPredefinedDictionary(MARKER_DICT)
    parameters = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(aruco_dict, parameters)
    corners, ids, _ = detector.detectMarkers(img)

    if ids is None or len(ids) == 0:
        return None

    # Find our specific MARKER_ID; if absent, return None.
    ids_flat = np.asarray(ids).flatten()
    matches = np.where(ids_flat == MARKER_ID)[0]
    if len(matches) == 0:
        return None

    # detectMarkers returns corners as a list of (1, 4, 2) arrays. cv2.aruco
    # documents the corner order as TL, TR, BR, BL — clockwise from top-left,
    # which is exactly what /api/reference expects.
    chosen = corners[matches[0]].reshape(4, 2)
    return [(float(x), float(y)) for x, y in chosen]
