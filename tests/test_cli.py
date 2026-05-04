"""Tests for cli.py — argument parsing and the annotate command."""

import json
import threading
import time
import urllib.request
from pathlib import Path

import pytest


def _make_test_jpeg(path: Path) -> None:
    """Create a tiny on-disk JPEG so tests have a valid photo path."""
    from PIL import Image

    Image.new("RGB", (10, 10), color=(128, 128, 128)).save(path, "JPEG")


def _make_test_png(path: Path) -> None:
    """Create a tiny on-disk PNG."""
    from PIL import Image

    Image.new("RGB", (10, 10), color=(255, 0, 0)).save(path, "PNG")


def test_cli_missing_part_id_exits_nonzero() -> None:
    """argparse must require --part-id; missing => SystemExit non-zero."""
    from agent_spatial_toolkit.cli import main

    with pytest.raises(SystemExit) as exc:
        main(argv=["annotate", "--photos", "/tmp/nonexistent.jpg"])
    assert exc.value.code != 0


def test_cli_rejects_non_image_extension(tmp_path: Path) -> None:
    """A photo with a non-image extension must be rejected at parse time."""
    from agent_spatial_toolkit.cli import main

    bogus = tmp_path / "doc.txt"
    bogus.write_text("not an image", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(argv=["annotate", "--part-id", "test", "--photos", str(bogus)])
    assert exc.value.code != 0


def test_cli_rejects_nonexistent_photo_path(tmp_path: Path) -> None:
    """A photo path that doesn't exist must be rejected at parse time."""
    from agent_spatial_toolkit.cli import main

    missing = tmp_path / "nope.jpg"
    with pytest.raises(SystemExit) as exc:
        main(argv=["annotate", "--part-id", "test", "--photos", str(missing)])
    assert exc.value.code != 0


def test_cli_rejects_invalid_part_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Invalid part_id exits 2 with a clean stderr message; no traceback."""
    from agent_spatial_toolkit.cli import main

    photo = tmp_path / "p.jpg"
    _make_test_jpeg(photo)
    code = main(
        argv=[
            "annotate",
            "--part-id",
            "../escape",
            "--photos",
            str(photo),
            "--out",
            str(tmp_path / "sessions"),
        ]
    )
    assert code == 2
    captured = capsys.readouterr()
    assert "part_id" in captured.err


def test_cli_rejects_corrupt_jpeg(tmp_path: Path) -> None:
    """A .jpg file that isn't actually JPEG is rejected at argparse time."""
    from agent_spatial_toolkit.cli import main

    fake = tmp_path / "fake.jpg"
    fake.write_bytes(b"this is not a JPEG, just bytes")
    with pytest.raises(SystemExit) as exc:
        main(argv=["annotate", "--part-id", "test", "--photos", str(fake)])
    assert exc.value.code == 2


def test_cli_warns_on_non_loopback_host(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Spec §8 line 620: --host 0.0.0.0 (or other non-loopback) emits an explicit warning."""
    import agent_spatial_toolkit.cli as cli_module

    photo = tmp_path / "p.jpg"
    _make_test_jpeg(photo)

    # Avoid actually binding to 0.0.0.0 in CI by failing start_server. The
    # warning fires BEFORE start_server, so it is still captured.
    def _fake_start_server(*args: object, **kwargs: object) -> object:
        raise RuntimeError("test-deliberate-fail")

    orig = cli_module.start_server
    cli_module.start_server = _fake_start_server  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError, match="test-deliberate-fail"):
            cli_module.main(
                argv=[
                    "annotate",
                    "--part-id",
                    "test",
                    "--photos",
                    str(photo),
                    "--out",
                    str(tmp_path / "sessions"),
                    "--host",
                    "0.0.0.0",
                ]
            )
    finally:
        cli_module.start_server = orig  # type: ignore[assignment]

    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert "0.0.0.0" in captured.err


def test_cli_copies_photos_and_writes_manifest(tmp_path: Path) -> None:
    """The CLI's setup helpers create the photos directory + manifest with sha256/size."""
    from agent_spatial_toolkit.cli import (
        _copy_photos_and_build_manifest,
        _write_manifest,
    )

    photos_src = tmp_path / "src"
    photos_src.mkdir()
    p1 = photos_src / "top_down.jpg"
    p2 = photos_src / "side.png"
    _make_test_jpeg(p1)
    _make_test_png(p2)

    session_dir = tmp_path / "session"
    photos_dst = session_dir / "photos"

    manifest = _copy_photos_and_build_manifest([p1, p2], photos_dst)
    assert "top_down.jpg" in manifest
    assert "side.png" in manifest
    for name, meta in manifest.items():
        assert isinstance(meta["sha256"], str)
        assert len(meta["sha256"]) == 64  # hex digest length
        assert meta["path"] == f"photos/{name}"
        assert isinstance(meta["size_bytes"], int)
        assert meta["size_bytes"] > 0
        assert (photos_dst / name).is_file()

    _write_manifest(manifest, session_dir)
    written = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
    assert written == manifest


def test_cli_compute_sha256_matches_expected(tmp_path: Path) -> None:
    """sha256 helper produces the expected digest for a known input."""
    from agent_spatial_toolkit.cli import _compute_sha256

    p = tmp_path / "f.bin"
    p.write_bytes(b"hello world")
    expected = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    assert _compute_sha256(p) == expected


def test_cli_duplicate_photo_filename_raises(tmp_path: Path) -> None:
    """Two photos with the same basename should collide on copy and raise."""
    from agent_spatial_toolkit.cli import _copy_photos_and_build_manifest

    src1 = tmp_path / "a"
    src2 = tmp_path / "b"
    src1.mkdir()
    src2.mkdir()
    p1 = src1 / "same.jpg"
    p2 = src2 / "same.jpg"
    _make_test_jpeg(p1)
    _make_test_jpeg(p2)

    photos_dst = tmp_path / "dst"
    with pytest.raises(FileExistsError, match="duplicate filename"):
        _copy_photos_and_build_manifest([p1, p2], photos_dst)


def test_cli_full_annotate_starts_and_can_be_shutdown(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """End-to-end: annotate creates a session, starts the server, prints URL,
    and exits cleanly when /api/finalize triggers shutdown."""
    from agent_spatial_toolkit import cli

    photo = tmp_path / "p.jpg"
    _make_test_jpeg(photo)

    # Wrap start_server with a short idle timeout so the test fails fast if the
    # finalize-triggered shutdown never fires.
    orig_start_server = cli.start_server

    def _short_timeout_start(*args: object, **kwargs: object) -> object:
        kwargs["idle_timeout_seconds"] = 30.0
        return orig_start_server(*args, **kwargs)

    cli.start_server = _short_timeout_start  # type: ignore[assignment]
    try:
        result_holder: dict[str, int | None] = {"code": None}

        def _run() -> None:
            result_holder["code"] = cli.main(
                argv=[
                    "annotate",
                    "--part-id",
                    "testpart",
                    "--photos",
                    str(photo),
                    "--out",
                    str(tmp_path / "sessions"),
                ]
            )

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        # Poll captured stdout for the printed URL. Use capfd (file-descriptor
        # capture) so writes from the CLI thread are picked up reliably.
        url: str | None = None
        for _ in range(50):
            captured = capfd.readouterr()
            if captured.out and "http://" in captured.out:
                for word in captured.out.split():
                    if word.startswith("http://"):
                        url = word.rstrip(".,")
                        break
                if url is not None:
                    break
            time.sleep(0.1)
        assert url is not None, "CLI did not print the server URL"

        # POST /api/finalize triggers an async shutdown after a 1s delay.
        finalize_url = f"{url.rstrip('/')}/api/finalize"
        req = urllib.request.Request(
            finalize_url,
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            assert resp.status == 200

        # The CLI thread should exit once the server's serve_thread is joined.
        thread.join(timeout=10.0)
        assert not thread.is_alive(), "CLI thread did not exit after /api/finalize"
        assert result_holder["code"] == 0
    finally:
        cli.start_server = orig_start_server  # type: ignore[assignment]
