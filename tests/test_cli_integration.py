"""End-to-end CLI integration test (Task 1.E.3).

Drives the real CLI (`cli.main`) → `create_session` → manifest → `create_app`
→ HTTP server → `/api/anchors` → `/api/feature` → `/api/finalize` → emit
pipeline against the synthetic-card fixture, then verifies the resulting
``annotations.json`` recovers each feature's part-local position to within
1 mm of ground truth.

Where this differs from `test_smoke_synthetic.py`
-------------------------------------------------
The smoke test calls the math layer (``solve_pnp``, ``pixel_to_part_local``)
directly. It proves the math is sound but bypasses the CLI, the Flask
routes, JSON (de)serialization, the session/event log layer, and the emit
pipeline. This test exercises that whole stack — it is the lowest-overhead
way to catch regressions where the math is right but the wiring is wrong
(intrinsics dict round-trip, NaN-rejecting boundary, manifest sha256
plumbing into ``photos[*]``, status.json write, closed-enum flag emission).

Where this differs from `test_cli.py::test_cli_full_annotate_starts_and_can_be_shutdown`
----------------------------------------------------------------------------------------
That test verifies start-up + clean shutdown only (URL printed, /api/finalize
returns 200, thread joins). It does NOT POST anchors or features and does
NOT inspect the resulting annotations.json. This test does both.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from tests.lifecycle_helpers import best_effort_finalize, scrape_url_from_capfd

FIXTURES = Path(__file__).parent / "fixtures/synthetic_card"


def _http_post_json(url: str, body: dict[str, Any], timeout_s: float = 5.0) -> dict[str, Any]:
    """POST a JSON body and return the decoded JSON response."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        assert resp.status == 200, f"POST {url} returned {resp.status}"
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json(url: str, timeout_s: float = 5.0) -> dict[str, Any]:
    """GET a URL and return the decoded JSON response."""
    with urllib.request.urlopen(url, timeout=timeout_s) as resp:
        assert resp.status == 200, f"GET {url} returned {resp.status}"
        return json.loads(resp.read().decode("utf-8"))


def _retry_get_json(url: str, deadline_s: float = 5.0) -> dict[str, Any]:
    """GET-with-retry — handles the brief window where the URL is printed
    before the WSGI listener has fully bound the socket on slow CI.
    """
    deadline = time.monotonic() + deadline_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return _http_get_json(url, timeout_s=2.0)
        except (ConnectionRefusedError, urllib.error.URLError, OSError) as e:
            last_exc = e
            time.sleep(0.05)
    raise AssertionError(
        f"GET {url} never succeeded within {deadline_s}s; last error: {last_exc!r}"
    )


def test_cli_synthetic_card_recovers_features_to_within_1mm(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Full CLI → API → emit pipeline on the synthetic card fixture.

    The renderer drew the card orthographically at 4 px/mm (200×150 mm →
    800×600 px) — see ``expected_geometry.json``. We feed the CLI the same
    photo, then drive the wizard via direct HTTP POSTs:

      1. ``/api/anchors`` with the four corner-pixel positions and
         near-orthographic intrinsics (fx=fy=1e6 ≈ orthographic emulation).
      2. ``/api/feature`` once per ground-truth feature, at its known
         pixel coordinate.
      3. ``/api/finalize`` to write annotations.json + status.json.

    Then we read ``annotations.json`` and assert each emitted feature's
    ``pcb_xyz_mm[:2]`` is within 1 mm of its ground-truth ``pcb_xyz_mm[:2]``.
    """
    from agent_spatial_toolkit import cli

    expected = json.loads((FIXTURES / "expected_geometry.json").read_text(encoding="utf-8"))
    px_per_mm = expected["render_resolution_px_per_mm"]
    photo_path = FIXTURES / "test1.jpg"
    photo_basename = photo_path.name  # "test1.jpg"

    # Cap idle timeout so the test fails fast (not 30 minutes) if /api/finalize
    # never reaches the lifecycle layer for any reason.
    orig_start_server = cli.start_server

    def _short_timeout_start(*args: object, **kwargs: object) -> object:
        kwargs["idle_timeout_seconds"] = 30.0
        return orig_start_server(*args, **kwargs)

    cli.start_server = _short_timeout_start  # type: ignore[assignment]

    sessions_root = tmp_path / "sessions"
    cli_result: dict[str, int | None] = {"code": None}

    def _run_cli() -> None:
        cli_result["code"] = cli.main(
            argv=[
                "annotate",
                "--part-id",
                "synthcard",
                "--photos",
                str(photo_path),
                "--out",
                str(sessions_root),
            ]
        )

    cli_thread = threading.Thread(target=_run_cli, daemon=True)
    cli_thread.start()

    # Bind these before the try-block so the finally cleanup and the
    # post-block artifact-verification are robust to mid-flow failures
    # (e.g. URL scrape raising would otherwise leave session_dir undefined
    # and mask the real failure with UnboundLocalError).
    url: str | None = None
    session_dir: Path | None = None

    try:
        url = scrape_url_from_capfd(capfd)

        # Resolve the live session_dir via /api/state. _retry_get_json
        # handles the brief window between "URL printed" and "socket
        # accept()ing" on slow CI runners.
        state = _retry_get_json(f"{url.rstrip('/')}/api/state")
        session_dir = Path(state["session_dir"])
        assert session_dir.is_dir()

        # Manifest contract (spec §6 session_artifacts.manifest): every photo
        # entry carries sha256, path, and size_bytes — assert all three so
        # a regression that drops any of them surfaces here.
        assert (session_dir / "manifest.json").is_file(), "CLI must have written manifest.json"
        manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
        assert photo_basename in manifest, "manifest should index the copied photo"
        expected_sha = manifest[photo_basename]["sha256"]
        assert len(expected_sha) == 64
        assert manifest[photo_basename]["path"] == f"photos/{photo_basename}"
        actual_size = (session_dir / "photos" / photo_basename).stat().st_size
        assert manifest[photo_basename]["size_bytes"] == actual_size

        # Build the orthographic intrinsics dict — same recipe as
        # test_smoke_synthetic.py, in the wire-format shape /api/anchors
        # expects (distortion grouped as {k1, k2, p1, p2, k3}).
        width, height = 800, 600
        intrinsics_dict = {
            "profile_source": "fov_class_fallback",
            "profile_id": "synthetic_orthographic",
            "fx_px": 1e6,
            "fy_px": 1e6,
            "cx": width / 2.0,
            "cy": height / 2.0,
            "distortion": {"k1": 0.0, "k2": 0.0, "p1": 0.0, "p2": 0.0, "k3": 0.0},
            "distortion_model": "opencv_5param",
            "profile_calibration_rms_px": None,
        }

        anchors_body = {
            "photo_id": photo_basename,
            "intrinsics": intrinsics_dict,
            "image_size": [width, height],
            "anchors": [
                {
                    "id": a["id"],
                    "pcb_xyz_mm": list(a["pcb_xyz_mm"]),
                    "pixel": [a["pcb_xyz_mm"][0] * px_per_mm, a["pcb_xyz_mm"][1] * px_per_mm],
                }
                for a in expected["anchors"]
            ],
        }
        anchors_resp = _http_post_json(f"{url.rstrip('/')}/api/anchors", anchors_body)
        assert anchors_resp["pose"]["anchor_reprojection_rms_px"] < 5.0
        assert anchors_resp["intrinsics_suspect"] is False

        # POST one feature at a time at its known pixel position.
        for feat in expected["features"]:
            true_xy = feat["pcb_xyz_mm"]
            feat_pixel = [true_xy[0] * px_per_mm, true_xy[1] * px_per_mm]
            feat_resp = _http_post_json(
                f"{url.rstrip('/')}/api/feature",
                {
                    "feature_id": feat["id"],
                    "photo_id": photo_basename,
                    "pixel": feat_pixel,
                    "z_assumed_mm": 0.0,
                },
            )
            recovered = feat_resp["pcb_xyz_mm"]
            assert abs(recovered[0] - true_xy[0]) < 1.0, f"X mismatch for {feat['id']}"
            assert abs(recovered[1] - true_xy[1]) < 1.0, f"Y mismatch for {feat['id']}"

        finalize_resp = _http_post_json(f"{url.rstrip('/')}/api/finalize", {})
        assert finalize_resp["status"] == "done"
        # Compare resolved paths so the assertion is symlink-/relative-form-tolerant.
        assert (
            Path(finalize_resp["annotations_path"]).resolve()
            == (session_dir / "annotations.json").resolve()
        )

        # The CLI thread should exit once the lifecycle's serve_thread joins
        # (triggered by the async shutdown scheduled in /api/finalize).
        cli_thread.join(timeout=10.0)
        assert not cli_thread.is_alive(), "CLI thread did not exit after /api/finalize"
        assert cli_result["code"] == 0
    finally:
        # Restore the patched module attribute first so a re-run starts clean.
        cli.start_server = orig_start_server  # type: ignore[assignment]
        # If an assertion failed mid-flow, the lifecycle server is still
        # running — trigger a best-effort shutdown so the bound port and
        # daemon thread don't leak into downstream tests in the same
        # pytest session. Errors are swallowed; the test is already failing.
        if cli_thread.is_alive() and url is not None:
            best_effort_finalize(url)
            cli_thread.join(timeout=10.0)

    assert session_dir is not None, "session_dir was never resolved"
    # ─── Verify the emitted artifacts ─────────────────────────────────────
    annotations = json.loads((session_dir / "annotations.json").read_text(encoding="utf-8"))

    # status.json is a separate, agent-pollable terminal marker (spec §3
    # line 122/129). Strict equality is correct — the spec defines exactly
    # one field with exactly one value at terminal state.
    status = json.loads((session_dir / "status.json").read_text(encoding="utf-8"))
    assert status == {"status": "done"}

    # Schema envelope (spec §6) — assert presence + correct shape of every
    # top-level key so a regression that drops any of them surfaces here
    # rather than getting smuggled past as a partial-pass.
    assert annotations["schema_version"] == 1
    assert isinstance(annotations["toolkit_version"], str) and annotations["toolkit_version"]
    assert isinstance(annotations["generated_at"], str) and annotations["generated_at"]
    assert isinstance(annotations["reference_frame"], dict)
    assert annotations["reference_frame"]["units"] == "mm"
    # session_artifacts wires the sibling-file names back into the canonical
    # artifact (spec §6 session_artifacts block) so a downstream consumer
    # knows which files form the complete bundle.
    assert annotations["session_artifacts"]["events_jsonl"] == "events.jsonl"
    assert annotations["session_artifacts"]["manifest"] == "manifest.json"

    # Photos: exactly one, with the manifest-derived sha256 plumbed through.
    assert len(annotations["photos"]) == 1
    emitted_photo = annotations["photos"][0]
    assert emitted_photo["id"] == photo_basename
    assert emitted_photo["sha256"] == expected_sha
    assert emitted_photo["path"] == f"photos/{photo_basename}"

    # Features: each ground-truth feature should appear with its part-local
    # XY recovered to within 1 mm. Z is the assumed plane (0). Method must
    # be the β-mode marker per spec §5.1 — guards against a silent regression
    # to "triangulation" or some other string.
    emitted_by_id = {f["id"]: f for f in annotations["features"]}
    assert set(emitted_by_id) == {f["id"] for f in expected["features"]}
    for feat in expected["features"]:
        true_xy = feat["pcb_xyz_mm"]
        emitted = emitted_by_id[feat["id"]]
        emitted_xy = emitted["pcb_xyz_mm"]
        assert abs(emitted_xy[0] - true_xy[0]) < 1.0, f"emit X mismatch for {feat['id']}"
        assert abs(emitted_xy[1] - true_xy[1]) < 1.0, f"emit Y mismatch for {feat['id']}"
        assert emitted_xy[2] == 0.0
        assert emitted["measurements"]["method"] == "planar_intersection"

    # Closed-enum flags: spec §6 line 458 — the server auto-emits
    # feature_clicked_only_once:<id> for every β-mode feature. Per the
    # canonical schema (Annotations.to_dict), flags are nested under
    # quality_summary, not at the top level.
    flags = annotations["quality_summary"]["flags"]
    for feat in expected["features"]:
        assert f"feature_clicked_only_once:{feat['id']}" in flags, (
            f"missing closed-enum flag for {feat['id']}"
        )
    # Negative assertions: the synthetic card pose succeeds with anchor
    # RMS << 5 px, so the suspect-intrinsics flags MUST NOT appear.
    # Spurious emission would itself be a regression.
    assert "intrinsics_suspect_high_anchor_rms" not in flags
    assert "intrinsics_session_recommend_chessboard" not in flags

    # quality_summary counts: 5 β-mode features ⇒ z_assumed_count=5,
    # triangulated_count=0 (γ-mode is deferred to v0.1.0).
    assert annotations["quality_summary"]["feature_count"] == len(expected["features"])
    assert annotations["quality_summary"]["triangulated_count"] == 0
    assert annotations["quality_summary"]["z_assumed_count"] == len(expected["features"])

    # Part identity round-tripped from the CLI flag.
    assert annotations["part"]["id"] == "synthcard"
