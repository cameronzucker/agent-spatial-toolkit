"""Tests for server/app.py — Flask routes for the wizard API (spec §4)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np
import pytest

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.server.app import create_app
from agent_spatial_toolkit.server.session import create_session


class _StubServer:
    """Minimal Server stand-in for /api/finalize shutdown.

    The lifecycle.Server class wraps a real WSGI socket; tests use Flask's
    test client, so a stub that just records shutdown() is sufficient.
    """

    def __init__(self) -> None:
        self.shutdown_called = False

    def shutdown(self) -> None:
        self.shutdown_called = True


@pytest.fixture
def app_factory(tmp_path: Path) -> Callable:
    """Return a factory producing (app, session, server) tuples per test."""

    def _make():
        session = create_session(part_id="testpart", base_dir=tmp_path / "sessions")
        server = _StubServer()
        app = create_app(server=server, session=session)
        app.config["TESTING"] = True
        return app, session, server

    return _make


def _make_test_intrinsics_dict(width: int = 1000, height: int = 1000) -> dict:
    """Build a JSON-serializable Intrinsics dict matching Intrinsics.to_dict() shape."""
    intr = Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="test",
        fx_px=1000.0,
        fy_px=1000.0,
        cx=width / 2.0,
        cy=height / 2.0,
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )
    return intr.to_dict()


def _project_anchors(world_pts: np.ndarray, image_size: tuple[int, int]) -> list[list[float]]:
    """Project synthetic anchors with a known pose so tests can assert recovery."""
    intr_dict = _make_test_intrinsics_dict(*image_size)
    K = np.array(  # noqa: N806
        [
            [intr_dict["fx_px"], 0, intr_dict["cx"]],
            [0, intr_dict["fy_px"], intr_dict["cy"]],
            [0, 0, 1],
        ],
        dtype=np.float64,
    )
    dist = np.array([0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float64)
    rvec = np.array([0.0, 0.0, 0.0])
    tvec = np.array([-25.0, -15.0, 200.0])
    pts2d, _ = cv2.projectPoints(world_pts.astype(np.float64), rvec, tvec, K, dist)
    return pts2d.reshape(-1, 2).tolist()


def _valid_anchors_payload(image_size: tuple[int, int] = (1000, 1000)) -> dict:
    """Build a /api/anchors payload with 4 non-collinear, non-degenerate anchors."""
    world_pts = np.array(
        [
            [0.0, 0.0, 0.0],
            [50.0, 0.0, 0.0],
            [50.0, 30.0, 0.0],
            [0.0, 30.0, 0.0],
        ]
    )
    pixels = _project_anchors(world_pts, image_size)
    return {
        "photo_id": "top_down",
        "intrinsics": _make_test_intrinsics_dict(*image_size),
        "image_size": list(image_size),
        "anchors": [
            {"id": f"a{i}", "pcb_xyz_mm": world_pts[i].tolist(), "pixel": pixels[i]}
            for i in range(4)
        ],
    }


# ─────────────────────────────────────────────────────────────────────────
# GET /
# ─────────────────────────────────────────────────────────────────────────


def test_index_route_exists(app_factory) -> None:
    """GET / returns a response (placeholder is fine while UI is unbuilt)."""
    app, _, _ = app_factory()
    client = app.test_client()
    resp = client.get("/")
    # We accept either 200 (placeholder) or 503 (UI not yet built);
    # implementation chose 200 with a placeholder string.
    assert resp.status_code == 200
    assert b"Wizard UI" in resp.data or b"wizard" in resp.data.lower()


# ─────────────────────────────────────────────────────────────────────────
# GET /api/state
# ─────────────────────────────────────────────────────────────────────────


def test_state_route_returns_fresh_session(app_factory) -> None:
    """GET /api/state on a fresh session returns the in-memory state shape."""
    app, session, _ = app_factory()
    client = app.test_client()

    resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["part_id"] == "testpart"
    assert data["session_dir"] == str(session.session_dir)
    assert data["status"] == "in_progress"
    assert data["photos"] == []
    assert data["features"] == []
    assert data["flags"] == []


# ─────────────────────────────────────────────────────────────────────────
# POST /api/anchors
# ─────────────────────────────────────────────────────────────────────────


def test_anchors_route_recovers_pose_with_4_anchors(app_factory) -> None:
    """POST /api/anchors with 4 valid anchors returns a usable pose."""
    app, _, _ = app_factory()
    client = app.test_client()

    payload = _valid_anchors_payload()
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()

    assert "pose" in body
    assert "rvec" in body["pose"]
    assert "tvec" in body["pose"]
    assert body["pose"]["pose_solver"].startswith("cv2.solvePnP")
    assert body["intrinsics_suspect"] is False
    # State should now have the photo registered
    state = client.get("/api/state").get_json()
    photo_ids = [p["id"] for p in state["photos"]]
    assert "top_down" in photo_ids


def test_anchors_route_rejects_2_anchors(app_factory) -> None:
    """POST /api/anchors with only 2 anchors returns 400."""
    app, _, _ = app_factory()
    client = app.test_client()

    payload = _valid_anchors_payload()
    payload["anchors"] = payload["anchors"][:2]  # truncate to 2 anchors
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 400
    body = resp.get_json()
    assert "error" in body


def test_anchors_route_rejects_missing_field(app_factory) -> None:
    """POST /api/anchors with no body / missing fields returns 400."""
    app, _, _ = app_factory()
    client = app.test_client()

    resp = client.post("/api/anchors", json={})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


# ─────────────────────────────────────────────────────────────────────────
# POST /api/feature
# ─────────────────────────────────────────────────────────────────────────


def test_feature_route_returns_pcb_xyz_after_pose(app_factory) -> None:
    """POST /api/feature after a successful pose returns pcb_xyz_mm."""
    app, _, _ = app_factory()
    client = app.test_client()

    # First establish a pose for the photo
    anchors_resp = client.post("/api/anchors", json=_valid_anchors_payload())
    assert anchors_resp.status_code == 200, anchors_resp.get_json()

    # Click a feature at the image center — should map close to the rectangle's
    # center (X=0, Y=0 in part-local frame, since tvec offset centers it).
    resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f1",
            "photo_id": "top_down",
            "pixel": [500.0, 500.0],
            "z_assumed_mm": 0.0,
        },
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert "pcb_xyz_mm" in body
    assert len(body["pcb_xyz_mm"]) == 3
    assert body["pcb_xyz_mm"][2] == pytest.approx(0.0)


def test_feature_route_unknown_photo_returns_404(app_factory) -> None:
    """POST /api/feature for an unknown photo_id returns 404."""
    app, _, _ = app_factory()
    client = app.test_client()

    resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f1",
            "photo_id": "nonexistent",
            "pixel": [500.0, 500.0],
        },
    )
    assert resp.status_code == 404
    assert "error" in resp.get_json()


# ─────────────────────────────────────────────────────────────────────────
# POST /api/finalize
# ─────────────────────────────────────────────────────────────────────────


def test_finalize_writes_annotations_status_and_schedules_shutdown(app_factory) -> None:
    """POST /api/finalize produces annotations.json + status.json and schedules shutdown."""
    app, session, server = app_factory()
    client = app.test_client()

    resp = client.post("/api/finalize", json={})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["status"] == "done"
    assert body["annotations_path"].endswith("annotations.json")

    # annotations.json was written
    ann_path = session.session_dir / "annotations.json"
    assert ann_path.exists()
    ann = json.loads(ann_path.read_text(encoding="utf-8"))
    assert ann["schema_version"] == 1
    assert ann["part"]["id"] == "testpart"

    # status.json was written (separate from state.json — spec §3 lines 122/129)
    status_path = session.session_dir / "status.json"
    assert status_path.exists()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "done"

    # Shutdown was scheduled — wait for the daemon thread to fire.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not server.shutdown_called:
        time.sleep(0.1)
    assert server.shutdown_called is True


def test_finalize_rejects_invalid_flag(app_factory) -> None:
    """POST /api/finalize with an invalid flag returns 400 with no annotations.json."""
    app, session, _ = app_factory()
    client = app.test_client()

    resp = client.post("/api/finalize", json={"flags": ["totally_invalid_flag"]})
    assert resp.status_code == 400
    assert "error" in resp.get_json()
    # annotations.json must NOT have been written when validation rejects flags
    assert not (session.session_dir / "annotations.json").exists()


# ─────────────────────────────────────────────────────────────────────────
# GET /static/photos/<id>
# ─────────────────────────────────────────────────────────────────────────


def test_static_photo_serves_real_file(app_factory) -> None:
    """GET /static/photos/<id> serves a file that exists in <session>/photos/."""
    app, session, _ = app_factory()
    client = app.test_client()

    photo_path = session.session_dir / "photos" / "top.jpg"
    photo_path.write_bytes(b"\xff\xd8\xff\xe0FAKE_JPEG_BYTES")

    resp = client.get("/static/photos/top.jpg")
    assert resp.status_code == 200
    assert resp.data == b"\xff\xd8\xff\xe0FAKE_JPEG_BYTES"


def test_static_photo_path_traversal_rejected(app_factory) -> None:
    """GET /static/photos/<id> rejects paths that escape the photos directory."""
    app, _, _ = app_factory()
    client = app.test_client()

    # Try a path traversal attempt — must NOT serve /etc/passwd
    resp = client.get("/static/photos/..%2F..%2F..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in (400, 404)
    # Must not leak passwd contents
    assert b"root:" not in resp.data


def test_static_photo_missing_file_returns_404(app_factory) -> None:
    """GET /static/photos/<id> for a file that doesn't exist returns 404."""
    app, _, _ = app_factory()
    client = app.test_client()

    resp = client.get("/static/photos/does_not_exist.jpg")
    assert resp.status_code == 404


def test_static_overlay_serves_real_file(app_factory) -> None:
    """GET /static/overlays/<id> serves a file that exists in <session>/overlays/."""
    app, session, _ = app_factory()
    client = app.test_client()

    overlay_path = session.session_dir / "overlays" / "top_overlay.png"
    overlay_path.write_bytes(b"\x89PNGFAKE")

    resp = client.get("/static/overlays/top_overlay.png")
    assert resp.status_code == 200
    assert resp.data == b"\x89PNGFAKE"
