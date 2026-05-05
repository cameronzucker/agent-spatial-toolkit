"""Tests for server/app.py — Flask routes for the wizard API (spec §4).

The ``app_factory`` fixture and ``_StubServer`` helper used by these
tests are defined in :mod:`tests.conftest` and resolved automatically
by pytest.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics


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


def _upload_test_photo(client) -> str:
    """Upload a small JPEG via /api/photo and return its photo_id.

    Used by tests for routes that depend on a photo being in the session.
    """
    import io

    from PIL import Image

    img = Image.new("RGB", (200, 150), color=(128, 64, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    response = client.post("/api/photo", data=buf.getvalue(), content_type="image/jpeg")
    assert response.status_code == 200, f"photo upload failed: {response.get_json()}"
    return response.get_json()["photo_id"]


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


def test_post_feature_single_click_uses_existing_ray_cast(app_factory) -> None:
    """POST /api/feature with clicks=[{photo_id, pixel}] (one entry) returns
    pcb_xyz_mm via the existing single-view ray-cast path."""
    app, _, _ = app_factory()
    client = app.test_client()

    # Establish a pose for the photo (legacy /api/anchors path is fine for setup).
    anchors_resp = client.post("/api/anchors", json=_valid_anchors_payload())
    assert anchors_resp.status_code == 200, anchors_resp.get_json()

    resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f1",
            "clicks": [{"photo_id": "top_down", "pixel": [500.0, 500.0]}],
            "z_assumed_mm": 0.0,
        },
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert "pcb_xyz_mm" in body
    assert len(body["pcb_xyz_mm"]) == 3
    assert body["pcb_xyz_mm"][2] == pytest.approx(0.0)


def test_post_feature_two_clicks_triangulates_to_known_point(app_factory) -> None:
    """POST /api/feature with 2 clicks runs real triangulation; returns
    pcb_xyz_mm + measurements with method=triangulation_2_views."""
    app, _, _ = app_factory()
    client = app.test_client()

    # Establish poses for two photos by re-using the synthetic anchors fixture
    # twice with different photo_ids.
    payload1 = _valid_anchors_payload()
    payload1["photo_id"] = "photo_view_1"
    r1 = client.post("/api/anchors", json=payload1)
    assert r1.status_code == 200, r1.get_json()

    payload2 = _valid_anchors_payload()
    payload2["photo_id"] = "photo_view_2"
    # Shift the camera in payload2 so the two views are not identical.
    # The anchor pixels are recomputed by _project_anchors via _valid_anchors_payload;
    # for this test we need a genuinely different camera angle. Build it manually:
    import cv2 as _cv2

    world_pts = np.array([[0.0, 0.0, 0.0], [50.0, 0.0, 0.0], [50.0, 30.0, 0.0], [0.0, 30.0, 0.0]])
    K = np.array([[1000.0, 0, 500.0], [0, 1000.0, 500.0], [0, 0, 1]], dtype=np.float64)  # noqa: N806
    rvec2 = np.array([0.0, 0.3, 0.0])
    tvec2 = np.array([-60.0, -15.0, 200.0])
    pixels2, _ = _cv2.projectPoints(world_pts, rvec2, tvec2, K, np.zeros(5))
    pixels2 = pixels2.reshape(-1, 2).tolist()
    payload2["anchors"] = [
        {"id": f"a{i}", "pcb_xyz_mm": world_pts[i].tolist(), "pixel": pixels2[i]} for i in range(4)
    ]
    r2 = client.post("/api/anchors", json=payload2)
    assert r2.status_code == 200, r2.get_json()

    # Now click the same physical point (15, 10, 0) in both views.
    target = np.array([15.0, 10.0, 0.0])
    p1, _ = _cv2.projectPoints(
        target.reshape(1, 1, 3), np.zeros(3), np.array([-25.0, -15.0, 200.0]), K, np.zeros(5)
    )
    p2, _ = _cv2.projectPoints(target.reshape(1, 1, 3), rvec2, tvec2, K, np.zeros(5))
    pixel1 = p1.reshape(2).tolist()
    pixel2 = p2.reshape(2).tolist()

    resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f_triangulated",
            "clicks": [
                {"photo_id": "photo_view_1", "pixel": pixel1},
                {"photo_id": "photo_view_2", "pixel": pixel2},
            ],
        },
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert "pcb_xyz_mm" in body
    assert body["pcb_xyz_mm"][0] == pytest.approx(15.0, abs=0.05)
    assert body["pcb_xyz_mm"][1] == pytest.approx(10.0, abs=0.05)
    assert body["pcb_xyz_mm"][2] == pytest.approx(0.0, abs=0.05)
    assert body["method"] == "triangulation_2_views"
    assert body["n_views"] == 2
    assert "triangulation_rms_px" in body
    assert "max_residual_px" in body


def test_post_feature_seven_clicks_returns_400(app_factory) -> None:
    """v1 caps at 6 views; 7 clicks must return 400 with a clear message."""
    app, _, _ = app_factory()
    client = app.test_client()

    # Establish a single pose so the photo lookups don't 404 first.
    anchors_resp = client.post("/api/anchors", json=_valid_anchors_payload())
    assert anchors_resp.status_code == 200

    resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f_too_many",
            "clicks": [{"photo_id": "top_down", "pixel": [500.0, 500.0]} for _ in range(7)],
        },
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert "error" in body
    assert "6" in body["error"]


def test_post_feature_two_clicks_unknown_photo_returns_404(app_factory) -> None:
    """If any of the multi-view clicks references an unknown photo_id, return 404."""
    app, _, _ = app_factory()
    client = app.test_client()

    # Establish one pose; the second photo_id is intentionally absent.
    anchors_resp = client.post("/api/anchors", json=_valid_anchors_payload())
    assert anchors_resp.status_code == 200

    resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f_dangling",
            "clicks": [
                {"photo_id": "top_down", "pixel": [500.0, 500.0]},
                {"photo_id": "no_such_photo", "pixel": [400.0, 400.0]},
            ],
        },
    )
    assert resp.status_code == 404
    assert "error" in resp.get_json()
    assert "no_such_photo" in resp.get_json()["error"]


def test_post_feature_zero_clicks_returns_400(app_factory) -> None:
    """POST /api/feature with clicks=[] returns 400."""
    app, _, _ = app_factory()
    client = app.test_client()

    resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f1",
            "clicks": [],
        },
    )
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_post_feature_legacy_single_pixel_shape_still_works(app_factory) -> None:
    """Legacy shape (top-level photo_id + pixel, no clicks) still returns 200.

    Backwards-compat regression: PR-4 will migrate UI callers; until then this
    path must remain operational.
    """
    app, _, _ = app_factory()
    client = app.test_client()

    anchors_resp = client.post("/api/anchors", json=_valid_anchors_payload())
    assert anchors_resp.status_code == 200, anchors_resp.get_json()

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


def test_post_feature_upload_only_photo_returns_404(app_factory) -> None:
    """Regression: /api/photo creates photo entries with intrinsics=None;
    /api/feature must 404 (matching the wireframe handler's contract)
    rather than 500 with a misleading 'stored intrinsics are malformed'
    message. Discovered by the legacy regression suite in Task 7."""
    app, session, server = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)
    # Note: NO /api/reference call — photo is upload-only
    response = client.post(
        "/api/feature",
        json={
            "feature_id": "usb_c",
            "photo_id": photo_id,
            "pixel": [200, 250],
        },
    )
    assert response.status_code == 404, (
        f"upload-only photo should return 404, not {response.status_code}: "
        f"{response.get_data(as_text=True)}"
    )
    assert "no pose" in response.get_json()["error"].lower()


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


def test_finalize_skips_unsolved_uploaded_photos(app_factory) -> None:
    """A photo uploaded but never anchored is skipped from the final
    annotations.json with a 'pose_skipped_uploaded_only:<id>' flag."""
    app, _, _ = app_factory()
    client = app.test_client()

    # Upload one photo without anchoring it.
    unsolved_id = _upload_test_photo(client)

    # Set up one photo with a valid pose so finalize has something to emit.
    anchors_resp = client.post("/api/anchors", json=_valid_anchors_payload())
    assert anchors_resp.status_code == 200

    resp = client.post("/api/finalize", json={})
    assert resp.status_code == 200, resp.get_json()
    out_path = Path(resp.get_json()["annotations_path"])
    assert out_path.is_file()
    annotations = json.loads(out_path.read_text(encoding="utf-8"))

    # Unsolved photo MUST NOT appear in photos[]
    photo_ids_in_annotations = [p["id"] for p in annotations["photos"]]
    assert unsolved_id not in photo_ids_in_annotations
    # Solved photo IS present.
    assert "top_down" in photo_ids_in_annotations

    # The flag is recorded in quality_summary.flags.
    flags = annotations["quality_summary"]["flags"]
    assert any(f.startswith("pose_skipped_uploaded_only:") and unsolved_id in f for f in flags)


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


# ─────────────────────────────────────────────────────────────────────────
# Spec §6 closed-enum auto-emit + state.json + boundary validation
# ─────────────────────────────────────────────────────────────────────────


def test_finalize_auto_emits_feature_clicked_only_once(app_factory) -> None:
    """Per spec §6, every β-mode feature gets feature_clicked_only_once:<id>
    auto-emitted by the server. The route must not require the client to send it."""
    app, session, _ = app_factory()
    client = app.test_client()

    # Solve PnP to register photo + pose
    r = client.post("/api/anchors", json=_valid_anchors_payload())
    assert r.status_code == 200, r.get_json()

    # Add a feature
    r = client.post(
        "/api/feature",
        json={"feature_id": "f1", "photo_id": "top_down", "pixel": [500, 500]},
    )
    assert r.status_code == 200, r.get_json()

    # Finalize — no flags in body; server must auto-emit feature_clicked_only_once:f1
    r = client.post("/api/finalize", json={})
    assert r.status_code == 200, r.get_json()

    annotations = json.loads((session.session_dir / "annotations.json").read_text())
    flags = annotations["quality_summary"]["flags"]
    assert "feature_clicked_only_once:f1" in flags


def test_finalize_updates_state_json_status_done(app_factory) -> None:
    """Per spec line 239, state.json is replaced on each event; finalize updates it."""
    app, session, _ = app_factory()
    client = app.test_client()
    r = client.post("/api/finalize", json={})
    assert r.status_code == 200, r.get_json()
    state = json.loads(session.state_path().read_text())
    assert state["status"] == "done"


def test_anchors_route_rejects_nan_image_size(app_factory) -> None:
    """A non-finite image_size component returns 400."""
    app, _, _ = app_factory()
    intr = {
        "profile_source": "fov_class_fallback",
        "profile_id": "t",
        "fx_px": 1000.0,
        "fy_px": 1000.0,
        "cx": 500.0,
        "cy": 500.0,
        "distortion_model": "opencv_5param",
        "distortion": {"k1": 0, "k2": 0, "p1": 0, "p2": 0, "k3": 0},
    }
    r = app.test_client().post(
        "/api/anchors",
        json={
            "photo_id": "x",
            "intrinsics": intr,
            "image_size": ["not-a-number", 1000],
            "anchors": [],
        },
    )
    assert r.status_code == 400


def test_feature_route_rejects_nan_z_assumed(app_factory) -> None:
    """A NaN z_assumed_mm returns 400, not 500."""
    app, _, _ = app_factory()
    r = app.test_client().post(
        "/api/feature",
        json={
            "feature_id": "f",
            "photo_id": "p",
            "pixel": [100, 100],
            "z_assumed_mm": float("nan"),
        },
    )
    # Either 400 (validation) or 404 (unknown photo) is acceptable; the
    # important thing is NOT 500.
    assert r.status_code != 500


def test_state_includes_uploaded_photos(app_factory) -> None:
    """A photo file in session_dir/photos/ shows up in /api/state with pose=null."""
    app, session, _ = app_factory()
    photos_dir = session.session_dir / "photos"
    photos_dir.mkdir(exist_ok=True)
    (photos_dir / "test.jpg").write_bytes(b"fake jpeg bytes")
    r = app.test_client().get("/api/state")
    assert r.status_code == 200
    data = r.get_json()
    photo_ids = [p["id"] for p in data["photos"]]
    assert "test.jpg" in photo_ids
    test_photo = next(p for p in data["photos"] if p["id"] == "test.jpg")
    assert test_photo["pose"] is None


def test_finalize_reads_sha256_from_manifest(app_factory) -> None:
    """When manifest.json is present, /api/finalize populates Photo.sha256 from it."""
    app, session, _server = app_factory()
    client = app.test_client()

    # Write a fake manifest matching what the CLI would produce.
    manifest = {
        "top_down": {
            "sha256": "deadbeef" * 8,  # 64 hex chars
            "path": "photos/top_down",
            "size_bytes": 12345,
        },
    }
    (session.session_dir / "manifest.json").write_text(json.dumps(manifest))

    # Solve PnP to register the photo in mem.
    r = client.post("/api/anchors", json=_valid_anchors_payload())
    assert r.status_code == 200, r.get_json()

    r = client.post("/api/finalize", json={})
    assert r.status_code == 200, r.get_json()

    annotations = json.loads((session.session_dir / "annotations.json").read_text())
    assert annotations["photos"][0]["sha256"] == "deadbeef" * 8
    assert annotations["photos"][0]["path"] == "photos/top_down"


def test_anchors_pose_failure_logs_to_events_with_safe_message(app_factory) -> None:
    """A PnP failure returns a stable client-safe message; full detail goes to events.jsonl."""
    app, session, _ = app_factory()
    intr = {
        "profile_source": "fov_class_fallback",
        "profile_id": "t",
        "fx_px": 1000.0,
        "fy_px": 1000.0,
        "cx": 500.0,
        "cy": 500.0,
        "distortion_model": "opencv_5param",
        "distortion": {"k1": 0, "k2": 0, "p1": 0, "p2": 0, "k3": 0},
    }
    # Only 3 collinear anchors → cv2 ITERATIVE rejects (PoseSolveError per PR #10)
    r = app.test_client().post(
        "/api/anchors",
        json={
            "photo_id": "x",
            "intrinsics": intr,
            "image_size": [1000, 1000],
            "anchors": [
                {"id": "a", "pcb_xyz_mm": [0, 0, 0], "pixel": [100, 100]},
                {"id": "b", "pcb_xyz_mm": [10, 0, 0], "pixel": [200, 100]},
                {"id": "c", "pcb_xyz_mm": [20, 0, 0], "pixel": [300, 100]},
            ],
        },
    )
    assert r.status_code == 400
    error_msg = r.get_json()["error"]
    # Stable client-safe message — no "cv2.error", no file paths.
    assert "cv2" not in error_msg.lower()
    assert "/" not in error_msg  # no path leakage
    # Full detail in events.jsonl
    events_text = (session.session_dir / "events.jsonl").read_text()
    assert "pose_failed" in events_text


def test_lens_catalog_route_returns_expected_lens_ids(app_factory) -> None:
    """GET /api/lens_catalog returns the v0 catalog entries."""
    app, _, _ = app_factory()
    client = app.test_client()

    resp = client.get("/api/lens_catalog")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "lenses" in body
    ids = {entry["id"] for entry in body["lenses"]}
    assert "pi_camera_module_3_standard" in ids
    assert "exif:detected" in ids
    assert "other" in ids


def test_lens_catalog_entries_carry_label_and_resolvable_flag(app_factory) -> None:
    """Each entry has id, label, resolvable, notes (no intrinsics leakage)."""
    app, _, _ = app_factory()
    client = app.test_client()

    body = client.get("/api/lens_catalog").get_json()
    by_id = {e["id"]: e for e in body["lenses"]}
    standard = by_id["pi_camera_module_3_standard"]
    assert standard["label"] == "Pi Camera Module 3 (standard)"
    assert standard["resolvable"] is True
    assert "intrinsics" not in standard  # do NOT leak intrinsics into client payload
    # Ultrawide → resolvable=False
    assert by_id["pi_camera_module_3_wide"]["resolvable"] is False
    assert by_id["exif:detected"]["resolvable"] is False


def _valid_anchors_payload_with_lens_id(image_size: tuple[int, int] = (4608, 2592)) -> dict:
    """Same shape as _valid_anchors_payload but uses lens_id instead of intrinsics."""
    base = _valid_anchors_payload(image_size=image_size)
    base.pop("intrinsics", None)
    base["lens_id"] = "pi_camera_module_3_standard"
    base["image_size"] = list(image_size)
    return base


def test_anchors_route_accepts_lens_id_and_resolves_intrinsics(app_factory) -> None:
    """POST /api/anchors with lens_id resolves intrinsics server-side and solves PnP."""
    app, _, _ = app_factory()
    client = app.test_client()

    payload = _valid_anchors_payload_with_lens_id()
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert "pose" in body
    assert body["pose"]["pose_solver"].startswith("cv2.solvePnP")


def test_anchors_route_lens_id_unresolvable_returns_400(app_factory) -> None:
    """POST /api/anchors with an ultrawide lens_id (unresolvable) returns 400."""
    app, _, _ = app_factory()
    client = app.test_client()

    payload = _valid_anchors_payload_with_lens_id()
    payload["lens_id"] = "pi_camera_module_3_wide"  # ultrawide → resolve() returns None
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 400
    body = resp.get_json()
    assert "error" in body
    assert "lens" in body["error"].lower()


def test_anchors_route_lens_id_other_without_intrinsics_returns_400(app_factory) -> None:
    """POST /api/anchors with lens_id='other' and no intrinsics dict returns 400."""
    app, _, _ = app_factory()
    client = app.test_client()

    payload = _valid_anchors_payload_with_lens_id()
    payload["lens_id"] = "other"
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 400


def test_anchors_route_lens_id_exif_detected_uses_exif(app_factory) -> None:
    """POST /api/anchors with lens_id='exif:detected' + exif dict resolves intrinsics."""
    app, _, _ = app_factory()
    client = app.test_client()

    payload = _valid_anchors_payload_with_lens_id()
    payload["lens_id"] = "exif:detected"
    payload["exif"] = {"focalLength35mm": 50.0}
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 200, resp.get_json()


def test_anchors_route_intrinsics_dict_still_works_backward_compat(app_factory) -> None:
    """Existing intrinsics-dict path still works (no breaking change)."""
    app, _, _ = app_factory()
    client = app.test_client()

    payload = _valid_anchors_payload()
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 200, resp.get_json()


# Append to tests/test_app.py


def test_wireframe_route_returns_png_after_pose_solved(app_factory) -> None:
    """GET /api/wireframe/<photo_id> renders + serves a PNG once pose is solved.

    Seeds a real photo file in the session's photos/ directory because the
    wireframe endpoint reads the source photo from disk (no synth fallback).
    """
    app, session, _ = app_factory()
    client = app.test_client()

    # Seed a real photo file matching the payload's photo_id.
    payload = _valid_anchors_payload()
    photos_dir = session.session_dir / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    photo_id = payload["photo_id"]
    Image.new("RGB", tuple(payload["image_size"]), color=(80, 80, 80)).save(
        photos_dir / f"{photo_id}.jpg"
    )

    # POST anchors so a pose exists
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 200, resp.get_json()

    resp = client.get(f"/api/wireframe/{photo_id}")
    assert resp.status_code == 200, resp.data[:200]
    assert resp.mimetype == "image/png"
    # PNG signature first 8 bytes
    assert resp.data.startswith(b"\x89PNG\r\n\x1a\n")


def test_wireframe_route_returns_404_when_photo_file_missing(app_factory) -> None:
    """GET /api/wireframe/<photo_id> returns 404 when pose exists but photo file is gone.

    Production-only path: a pose was solved (mem has the photo entry) but the
    source photo file is missing on disk. Endpoint must NOT synthesize a blank
    fallback (would silently undermine the wireframe's visual-confirmation
    purpose); must return 404 with a clear error.
    """
    app, _, _ = app_factory()
    client = app.test_client()

    # POST anchors WITHOUT seeding a photo file — pose exists in mem but the
    # source photo isn't on disk.
    payload = _valid_anchors_payload()
    resp = client.post("/api/anchors", json=payload)
    assert resp.status_code == 200, resp.get_json()

    resp = client.get(f"/api/wireframe/{payload['photo_id']}")
    assert resp.status_code == 404
    body = resp.get_json()
    assert "not found on disk" in body["error"].lower()


def test_wireframe_route_returns_404_when_no_pose(app_factory) -> None:
    """GET /api/wireframe/<photo_id> returns 404 if no pose has been solved."""
    app, _, _ = app_factory()
    client = app.test_client()
    resp = client.get("/api/wireframe/never_seen")
    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_wireframe_route_rejects_path_traversal(app_factory) -> None:
    """GET /api/wireframe with traversal segments returns 404 (or 400), never reads outside session dir."""
    app, _, _ = app_factory()
    client = app.test_client()
    # Flask's <path:...> converter accepts slashes; the route handler must
    # call secure_filename and reject anything that resolves outside.
    for evil in ["..%2Fetc%2Fpasswd", "../../etc/passwd", "subdir/../escape"]:
        resp = client.get(f"/api/wireframe/{evil}")
        assert resp.status_code in (400, 404), (
            f"Expected 400/404 for {evil!r}; got {resp.status_code}"
        )


# ─────────────────────────────────────────────────────────────────────────
# POST /api/reference (wizard "confirm scale" — replaces /api/anchors)
# ─────────────────────────────────────────────────────────────────────────


def test_post_reference_credit_card_solves_pose(app_factory) -> None:
    """A simulated 4-corner credit-card click produces a pose."""
    app, _, _ = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)
    response = client.post(
        "/api/reference",
        json={
            "photo_id": photo_id,
            "reference_type": "credit_card",
            "pixel_corners": [[100, 100], [400, 100], [400, 300], [100, 300]],
            "image_size": [800, 600],
            "intrinsics": _make_test_intrinsics_dict(800, 600),
        },
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert "pose" in body
    assert "intrinsics_suspect" in body
    assert body["pose"]["rvec"] is not None
    assert body["pose"]["tvec"] is not None


def test_post_reference_dollar_bill_uses_correct_dimensions(app_factory) -> None:
    """Dollar-bill 4-corner clicks produce a pose with translation derived
    from the larger reference dimensions (156.1 x 66.3 mm vs 85.6 x 53.98)."""
    app, _, _ = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)
    response = client.post(
        "/api/reference",
        json={
            "photo_id": photo_id,
            "reference_type": "dollar_bill",
            "pixel_corners": [[100, 100], [400, 100], [400, 300], [100, 300]],
            "image_size": [800, 600],
            "intrinsics": _make_test_intrinsics_dict(800, 600),
        },
    )
    assert response.status_code == 200, response.get_json()


def test_post_reference_unknown_type_returns_400(app_factory) -> None:
    """Unknown reference_type returns 400 with 'unknown reference type' in error."""
    app, _, _ = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)
    response = client.post(
        "/api/reference",
        json={
            "photo_id": photo_id,
            "reference_type": "not_a_real_type",
            "pixel_corners": [[100, 100], [400, 100], [400, 300], [100, 300]],
            "image_size": [800, 600],
            "intrinsics": _make_test_intrinsics_dict(800, 600),
        },
    )
    assert response.status_code == 400
    assert "unknown reference type" in response.get_json()["error"].lower()


def test_post_reference_missing_pixel_corners_returns_400(app_factory) -> None:
    """Missing pixel_corners field returns 400."""
    app, _, _ = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)
    response = client.post(
        "/api/reference",
        json={
            "photo_id": photo_id,
            "reference_type": "credit_card",
            "image_size": [800, 600],
            "intrinsics": _make_test_intrinsics_dict(800, 600),
            # pixel_corners omitted
        },
    )
    assert response.status_code == 400


def test_post_reference_wrong_corner_count_returns_400(app_factory) -> None:
    """Need exactly 4 corners; 3 or 5 must be rejected."""
    app, _, _ = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)
    for n_corners in [3, 5]:
        corners = [[100 + i * 10, 100] for i in range(n_corners)]
        response = client.post(
            "/api/reference",
            json={
                "photo_id": photo_id,
                "reference_type": "credit_card",
                "pixel_corners": corners,
                "image_size": [800, 600],
                "intrinsics": _make_test_intrinsics_dict(800, 600),
            },
        )
        assert response.status_code == 400, f"expected 400 for n_corners={n_corners}"


def test_post_reference_returns_pose_rms_mm(app_factory) -> None:
    """The /api/reference response now carries pose_rms_mm so the UI
    tier-badge logic can render Excellent/Good/Approximate/Try again."""
    app, _, _ = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)

    # Project credit-card corners through a known pose to get pixel corners.
    import cv2 as _cv2

    K = np.array([[1000.0, 0, 100.0], [0, 1000.0, 75.0], [0, 0, 1]], dtype=np.float64)  # noqa: N806
    rvec = np.array([0.0, 0.0, 0.0])
    tvec = np.array([-42.8, -27.0, 200.0])
    world_corners = np.array(
        [
            [0.0, 0.0, 0.0],
            [85.60, 0.0, 0.0],
            [85.60, 53.98, 0.0],
            [0.0, 53.98, 0.0],
        ]
    )
    pixel_corners, _ = _cv2.projectPoints(world_corners, rvec, tvec, K, np.zeros(5))
    pixel_corners = pixel_corners.reshape(-1, 2).tolist()

    payload = {
        "photo_id": photo_id,
        "reference_type": "credit_card",
        "pixel_corners": pixel_corners,
        "image_size": [200, 150],
        "intrinsics": _make_test_intrinsics_dict(200, 150),
    }
    resp = client.post("/api/reference", json=payload)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert "pose_rms_mm" in body
    assert isinstance(body["pose_rms_mm"], float)
    assert body["pose_rms_mm"] >= 0.0
    assert body["pose_rms_mm"] < 5.0  # synthetic-clean clicks should be tight


def test_post_reference_falls_back_to_default_intrinsics_when_no_exif(app_factory) -> None:
    """Without intrinsics or lens_id, /api/reference still succeeds via the
    FOV-class default, and quality_summary.flags carries 'intrinsics_estimated'."""
    app, _, _ = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)

    # Project credit-card corners through a known pose (no intrinsics in payload).
    import cv2 as _cv2

    # Use what the FOV-default would compute: long_edge=200, focal_35=24 (wide-class).
    # fx_px = 24 * 200 / 36 = 133.3
    rvec = np.array([0.0, 0.0, 0.0])
    tvec = np.array([-42.8, -27.0, 100.0])
    K = np.array([[133.33, 0, 100.0], [0, 133.33, 75.0], [0, 0, 1]], dtype=np.float64)  # noqa: N806
    world_corners = np.array(
        [
            [0.0, 0.0, 0.0],
            [85.60, 0.0, 0.0],
            [85.60, 53.98, 0.0],
            [0.0, 53.98, 0.0],
        ]
    )
    pixel_corners, _ = _cv2.projectPoints(world_corners, rvec, tvec, K, np.zeros(5))
    pixel_corners = pixel_corners.reshape(-1, 2).tolist()

    payload = {
        "photo_id": photo_id,
        "reference_type": "credit_card",
        "pixel_corners": pixel_corners,
        "image_size": [200, 150],
        # No intrinsics, no lens_id, no exif.
    }
    resp = client.post("/api/reference", json=payload)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert "pose" in body

    state = client.get("/api/state").get_json()
    assert "intrinsics_estimated" in state["flags"]


def test_post_reference_uses_exif_focal_when_provided_in_request(app_factory) -> None:
    """If the request body contains an `exif` dict with focal info, use it
    instead of falling back to wide-class default. Flag still set
    (intrinsics still 'estimated', not chessboard-calibrated)."""
    app, _, _ = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)

    import cv2 as _cv2

    # focal_35 = 28 (wide), long_edge=200 → fx_px = 28*200/36 = 155.5
    rvec = np.array([0.0, 0.0, 0.0])
    tvec = np.array([-42.8, -27.0, 100.0])
    K = np.array([[155.55, 0, 100.0], [0, 155.55, 75.0], [0, 0, 1]], dtype=np.float64)  # noqa: N806
    world_corners = np.array(
        [
            [0.0, 0.0, 0.0],
            [85.60, 0.0, 0.0],
            [85.60, 53.98, 0.0],
            [0.0, 53.98, 0.0],
        ]
    )
    pixel_corners, _ = _cv2.projectPoints(world_corners, rvec, tvec, K, np.zeros(5))
    pixel_corners = pixel_corners.reshape(-1, 2).tolist()

    payload = {
        "photo_id": photo_id,
        "reference_type": "credit_card",
        "pixel_corners": pixel_corners,
        "image_size": [200, 150],
        "exif": {"focal_length_35mm_equiv": 28.0},
    }
    resp = client.post("/api/reference", json=payload)
    assert resp.status_code == 200, resp.get_json()
    state = client.get("/api/state").get_json()
    assert "intrinsics_estimated" in state["flags"]


def test_marker_detect_stub_returns_null_corners(app_factory) -> None:
    """PR-2 stub: no auto-detect yet; PR-3 wires cv2.aruco."""
    app, session, server = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)
    response = client.get(f"/api/marker_detect/{photo_id}")
    assert response.status_code == 200
    assert response.get_json() == {"corners": None}


def test_reproject_all_returns_by_photo_dict_after_triangulation(app_factory) -> None:
    """After triangulating one feature, /api/reproject_all returns predicted
    pixels + error_mm per (photo, feature)."""
    import cv2 as _cv2

    app, _, _ = app_factory()
    client = app.test_client()

    # Set up two poses + a triangulated feature (re-using the fixture pattern).
    payload1 = _valid_anchors_payload()
    payload1["photo_id"] = "v1"
    r1 = client.post("/api/anchors", json=payload1)
    assert r1.status_code == 200, r1.get_json()

    K = np.array([[1000.0, 0, 500.0], [0, 1000.0, 500.0], [0, 0, 1]], dtype=np.float64)  # noqa: N806
    rvec2 = np.array([0.0, 0.3, 0.0])
    tvec2 = np.array([-60.0, -15.0, 200.0])
    world_pts = np.array([[0.0, 0.0, 0.0], [50.0, 0.0, 0.0], [50.0, 30.0, 0.0], [0.0, 30.0, 0.0]])
    pixels2, _ = _cv2.projectPoints(world_pts, rvec2, tvec2, K, np.zeros(5))
    payload2 = {
        "photo_id": "v2",
        "intrinsics": _make_test_intrinsics_dict(1000, 1000),
        "image_size": [1000, 1000],
        "anchors": [
            {
                "id": f"a{i}",
                "pcb_xyz_mm": world_pts[i].tolist(),
                "pixel": pixels2.reshape(-1, 2)[i].tolist(),
            }
            for i in range(4)
        ],
    }
    r2 = client.post("/api/anchors", json=payload2)
    assert r2.status_code == 200, r2.get_json()

    target = np.array([15.0, 10.0, 0.0])
    p1, _ = _cv2.projectPoints(
        target.reshape(1, 1, 3), np.zeros(3), np.array([-25.0, -15.0, 200.0]), K, np.zeros(5)
    )
    p2, _ = _cv2.projectPoints(target.reshape(1, 1, 3), rvec2, tvec2, K, np.zeros(5))

    feat_resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f_tri",
            "clicks": [
                {"photo_id": "v1", "pixel": p1.reshape(2).tolist()},
                {"photo_id": "v2", "pixel": p2.reshape(2).tolist()},
            ],
        },
    )
    assert feat_resp.status_code == 200, feat_resp.get_json()

    resp = client.get("/api/reproject_all")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "by_photo" in body
    assert "v1" in body["by_photo"]
    assert "v2" in body["by_photo"]
    v1_entries = body["by_photo"]["v1"]
    assert len(v1_entries) == 1
    assert v1_entries[0]["feature_id"] == "f_tri"
    assert "predicted_pixel" in v1_entries[0]
    assert "error_mm" in v1_entries[0]
    assert isinstance(v1_entries[0]["predicted_pixel"], list)
    assert len(v1_entries[0]["predicted_pixel"]) == 2


def test_reproject_all_empty_state_returns_empty_by_photo(app_factory) -> None:
    """No features → by_photo: {} (still well-typed object)."""
    app, _, _ = app_factory()
    client = app.test_client()
    resp = client.get("/api/reproject_all")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"by_photo": {}}


# ─────────────────────────────────────────────────────────────────────────
# Legacy endpoint regression tests (deleted in PR-4; functional until then)
# ─────────────────────────────────────────────────────────────────────────


def test_legacy_anchors_endpoint_still_functional(app_factory) -> None:
    """Until PR-4 deletes it, /api/anchors must keep working for the
    legacy UI (and any external callers that haven't migrated to
    /api/reference yet)."""
    app, session, server = app_factory()
    client = app.test_client()
    _upload_test_photo(client)
    payload = _valid_anchors_payload(image_size=(1000, 1000))
    response = client.post("/api/anchors", json=payload)
    assert response.status_code == 200
    assert "pose" in response.get_json()


def test_legacy_lens_catalog_endpoint_still_functional(app_factory) -> None:
    """Until PR-4 deletes it, /api/lens_catalog must keep returning
    the lens-catalog payload for any legacy UI callers."""
    app, session, server = app_factory()
    client = app.test_client()
    response = client.get("/api/lens_catalog")
    assert response.status_code == 200
    body = response.get_json()
    assert isinstance(body, (dict, list)), "lens_catalog returns an enumerable"


def test_legacy_wireframe_endpoint_still_functional(app_factory) -> None:
    """Until PR-4 deletes it, /api/wireframe/<photo_id> must keep
    returning a wireframe PNG for solved photos (or a sensible 4xx
    if the photo has no solved pose)."""
    app, session, server = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)
    response = client.get(f"/api/wireframe/{photo_id}")
    # The endpoint may return 200 (PNG) for a solved photo OR 404 for
    # a photo with no pose. Anything else (500, 410) means the endpoint
    # is broken or has been silently disabled.
    assert response.status_code in {200, 404}, (
        f"wireframe must be functional (200) or report no-pose-solved (404), "
        f"not {response.status_code}"
    )


# ─────────────────────────────────────────────────────────────────────────
# GET /api/marker_detect/<photo_id>
# ─────────────────────────────────────────────────────────────────────────


def test_marker_detect_returns_corners_for_aruco_photo(app_factory, tmp_path) -> None:
    """When a photo with a marker is uploaded, /api/marker_detect returns 4 corners."""
    import cv2 as _cv2

    from agent_spatial_toolkit.server.marker_detect import MARKER_DICT, MARKER_ID

    aruco_dict = _cv2.aruco.getPredefinedDictionary(MARKER_DICT)
    marker = _cv2.aruco.generateImageMarker(aruco_dict, MARKER_ID, 200)
    canvas = np.full((800, 800), 255, dtype=np.uint8)
    canvas[300:500, 300:500] = marker
    photo_path = tmp_path / "with_marker.png"
    _cv2.imwrite(str(photo_path), canvas)

    app, _, _ = app_factory()
    client = app.test_client()
    with photo_path.open("rb") as f:
        upload = client.post("/api/photo", data=f.read(), content_type="image/png")
    assert upload.status_code == 200
    photo_id = upload.get_json()["photo_id"]

    resp = client.get(f"/api/marker_detect/{photo_id}")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["corners"] is not None
    assert len(body["corners"]) == 4


def test_marker_detect_returns_null_when_no_marker(app_factory) -> None:
    """A photo with no marker returns {corners: null}."""
    app, _, _ = app_factory()
    client = app.test_client()
    photo_id = _upload_test_photo(client)
    resp = client.get(f"/api/marker_detect/{photo_id}")
    assert resp.status_code == 200
    assert resp.get_json()["corners"] is None


def test_marker_detect_returns_404_when_photo_missing(app_factory) -> None:
    """An unknown photo_id returns 404."""
    app, _, _ = app_factory()
    client = app.test_client()
    resp = client.get("/api/marker_detect/no_such_photo")
    assert resp.status_code == 404


def test_next_prompt_no_state_starts_with_top(app_factory) -> None:
    """A fresh session starts with direction='top'."""
    app, _, _ = app_factory()
    client = app.test_client()
    resp = client.get("/api/next_prompt")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["direction"] == "top"
    assert "coverage_cells" in body
    assert "features" in body


def test_next_prompt_after_top_photo_advances_to_side(app_factory) -> None:
    """After a top-down photo with a single-view feature, prompt picks a side."""
    app, _, _ = app_factory()
    client = app.test_client()
    anchors_resp = client.post("/api/anchors", json=_valid_anchors_payload())
    assert anchors_resp.status_code == 200
    feature_resp = client.post(
        "/api/feature",
        json={
            "feature_id": "f1",
            "photo_id": "top_down",
            "pixel": [500.0, 500.0],
            "z_assumed_mm": 0.0,
        },
    )
    assert feature_resp.status_code == 200

    resp = client.get("/api/next_prompt")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["direction"] != "top"  # advanced past top
    assert body["direction"] in {"+long", "-long", "+short", "-short"}
    assert "second view" in body["reason"].lower() or "another angle" in body["reason"].lower()
