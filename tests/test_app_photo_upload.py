"""Tests for POST /api/photo: in-wizard photo upload with HEIC→JPEG decode.

Covers the wizard's photo-capture step (design §2 step 4 / §3 HEIC handling).
The endpoint accepts JPEG/PNG/HEIC/HEIF, transparently decodes HEIC via
pillow-heif, hashes the file, and persists JPEG to <session>/photos/<id>.jpg.
"""

from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from agent_spatial_toolkit.server.app import create_app
from agent_spatial_toolkit.server.session import create_session


class _StubServer:
    """Minimal Server stand-in (mirrors tests/test_app.py).

    Duplicated rather than imported across test modules to avoid coupling
    test files; refactoring the fixture into ``tests/conftest.py`` would
    broaden sharing but is out of scope for this task.
    """

    def __init__(self) -> None:
        self.shutdown_called = False

    def shutdown(self) -> None:
        self.shutdown_called = True


@pytest.fixture
def app_factory(tmp_path: Path) -> Callable:
    """Return a factory producing (app, session, server) tuples per test.

    Mirrors the fixture in tests/test_app.py — see module docstring above
    for why this is intentionally duplicated rather than shared.
    """

    def _make():
        session = create_session(part_id="testpart", base_dir=tmp_path / "sessions")
        server = _StubServer()
        app = create_app(server=server, session=session)
        app.config["TESTING"] = True
        return app, session, server

    return _make


def _make_jpeg_bytes(width: int = 200, height: int = 150) -> bytes:
    """Return a small valid JPEG payload for upload tests."""
    img = Image.new("RGB", (width, height), color=(128, 64, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _make_png_bytes(width: int = 200, height: int = 150) -> bytes:
    """Return a small valid PNG payload."""
    img = Image.new("RGB", (width, height), color=(64, 200, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_heic_bytes(width: int = 200, height: int = 150) -> bytes:
    """Generate HEIC/HEIF bytes via pillow-heif.

    Uses ``pytest.importorskip`` defensively — pillow-heif is a hard dep
    (Task 1) so this should never skip in CI, but the guard keeps the
    test from masking unrelated import failures.
    """
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    img = Image.new("RGB", (width, height), color=(200, 128, 64))
    buf = io.BytesIO()
    img.save(buf, format="HEIF", quality=85)
    return buf.getvalue()


def test_jpeg_upload_round_trips_and_persists(app_factory: Callable) -> None:
    """JPEG upload returns photo_id derived from sha256 prefix; file lands on disk."""
    app, session, _server = app_factory()
    client = app.test_client()
    body = _make_jpeg_bytes()

    response = client.post("/api/photo", data=body, content_type="image/jpeg")
    assert response.status_code == 200
    data = response.get_json()
    expected_sha = hashlib.sha256(body).hexdigest()
    assert data["photo_id"] == f"photo_{expected_sha[:12]}"
    assert data["sha256"] == expected_sha
    assert data["stored_format"] == "jpeg"

    photo_path = session.session_dir / "photos" / f"{data['photo_id']}.jpg"
    assert photo_path.exists(), "photo must persist to <session>/photos/<id>.jpg"


def test_png_upload_persists_as_jpeg(app_factory: Callable) -> None:
    """PNG uploads are accepted; saved as JPEG for consistency."""
    app, session, _server = app_factory()
    client = app.test_client()
    body = _make_png_bytes()

    response = client.post("/api/photo", data=body, content_type="image/png")
    assert response.status_code == 200
    data = response.get_json()
    assert data["stored_format"] == "jpeg"
    photo_path = session.session_dir / "photos" / f"{data['photo_id']}.jpg"
    assert photo_path.exists()
    # Confirm it's actually a JPEG, not the original PNG bytes.
    with photo_path.open("rb") as f:
        header = f.read(3)
    assert header == b"\xff\xd8\xff", "saved file must be JPEG (SOI marker)"


def test_heic_upload_decodes_to_jpeg(app_factory: Callable) -> None:
    """The headline HEIC path: iPhone photo arrives, server decodes to JPEG."""
    app, session, _server = app_factory()
    client = app.test_client()
    body = _make_heic_bytes()

    response = client.post("/api/photo", data=body, content_type="image/heic")
    assert response.status_code == 200
    data = response.get_json()
    assert data["stored_format"] == "jpeg"
    photo_path = session.session_dir / "photos" / f"{data['photo_id']}.jpg"
    assert photo_path.exists()
    with photo_path.open("rb") as f:
        header = f.read(3)
    assert header == b"\xff\xd8\xff", "saved file must be JPEG (SOI marker)"


def test_unsupported_format_returns_415(app_factory: Callable) -> None:
    """Non-image content types are rejected with 415."""
    app, _session, _server = app_factory()
    client = app.test_client()
    response = client.post("/api/photo", data=b"some text", content_type="text/plain")
    assert response.status_code == 415
    err = response.get_json()["error"]
    assert "JPEG" in err or "PNG" in err or "HEIC" in err


def test_payload_too_large_returns_413(app_factory: Callable) -> None:
    """Payloads >50 MB are rejected to prevent abuse / fat-finger uploads."""
    app, _session, _server = app_factory()
    client = app.test_client()
    body = b"\x00" * (51 * 1024 * 1024)  # 51 MB
    response = client.post("/api/photo", data=body, content_type="image/jpeg")
    assert response.status_code == 413


def test_re_upload_same_photo_is_idempotent(app_factory: Callable) -> None:
    """Same content → same photo_id; registry has one entry."""
    app, _session, _server = app_factory()
    client = app.test_client()
    body = _make_jpeg_bytes()

    r1 = client.post("/api/photo", data=body, content_type="image/jpeg")
    r2 = client.post("/api/photo", data=body, content_type="image/jpeg")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.get_json()["photo_id"] == r2.get_json()["photo_id"]
    # Registry should hold exactly one entry for this content-derived id.
    mem = app.config["STATE"]
    assert len(mem["photos"]) == 1
