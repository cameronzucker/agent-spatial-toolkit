"""Flask routes for the wizard API (spec §4).

This is the integration crucible — Flask routes that exercise all prior
modules (intrinsics, pose, ray, emit, validators, session, events, lifecycle).

Route summary
-------------
- ``GET  /``                       — wizard UI shell (placeholder until Stream D)
- ``GET  /api/state``              — current in-memory session state
- ``POST /api/anchors``            — solve PnP from clicked anchors, store pose
- ``POST /api/feature``            — pixel → part-local via planar ray-cast (β-mode)
- ``POST /api/finalize``           — emit annotations.json + status.json + shutdown
- ``GET  /static/photos/<id>``     — serve photos with path-traversal protection
- ``GET  /static/overlays/<id>``   — serve overlays with path-traversal protection

The Flask ``app`` is built by :func:`create_app`, an explicit factory that
takes a ``Server`` (for shutdown) and a ``Session`` (for session_dir, photos,
event log). No globals — each test creates a fresh app/session/server triple.

Design notes
------------
- Triangulation (γ-mode) is not implemented in v0.1.0-alpha; only β-mode
  (single-photo planar ray-cast) is wired up here.
- Path security on /static/* uses ``Path.resolve()`` + ``is_relative_to()``
  to reject any traversal attempt that escapes the photos/overlays subdir.
- ``/api/finalize`` schedules its own shutdown asynchronously via a daemon
  thread (per issue #25): a synchronous shutdown from inside the request
  handler would deadlock against the WSGI thread serving the request.
- All error responses use the shape ``{"error": str}`` with no stack traces.
"""

# PR-4-HANDOFF: user-facing-translation-required ----------------------------
# Per design §9 (forbidden vocabulary) the strings listed below are NOT
# safe to display verbatim in the wizard UI — they currently use CV jargon
# that non-CAD users won't understand. PR-4's UI MUST translate each to
# plain English before showing to the user. This list is exhaustive as of
# PR-3 merge; if PR-4 adds new error strings, this list MUST be updated.
#
# Routes & strings:
#   /api/anchors:
#     - "PnP failed: anchors are degenerate or insufficient"
#     - "must provide either intrinsics or lens_id"
#     - "lens_id '<id>' could not resolve to intrinsics; provide an explicit
#        intrinsics dict"
#     - "invalid intrinsics: <e>"
#     - "invalid anchor entries: <e>"
#     - "internal error during pose solve"
#   /api/reference:
#     - "pose solve failed: corners may be too oblique or mis-clicked"
#     - "could not derive camera intrinsics; provide an explicit intrinsics dict"
#     - "invalid intrinsics: <e>"
#     - "internal error during pose solve"
#   /api/feature:
#     - "no pose for photo <id>"   (multiple call sites: legacy + triangulation)
#     - "photo '<id>' has no pose; call /api/anchors first"  (legacy fallback path)
#     - "unknown photo_id '<id>' in clicks[<i>]"  (triangulation path)
#     - "stored intrinsics are malformed: <e>"
#     - "v1 triangulates from up to 6 views; received <N> — please reduce to
#        your 6 best views"
#     - "internal error during ray-cast"
#     - "method '<m>' not implemented in v0.1.0-alpha (β-mode only)"
#   /api/wireframe:
#     - "no pose for photo <id>"
#     - "stored intrinsics invalid: <e>"
#     - "wireframe render failed: <e>"
#   /api/marker_detect:
#     - "photo file for <id> not found on disk"
#   (Routes not listed here ship only plain-English errors safe to forward.)
#
# /api/next_prompt response: the `direction` field is an internal axis label
# (e.g., '+long', '-short') — UI MUST render it as a silhouette icon, never
# as prose. The `reason` field is a fallback English string that interpolates
# the axis label literally; UI MUST render the localized prose from
# `reason_code` (an enum) instead. The fallback `reason` is for debugger
# inspection only.
# ---------------------------------------------------------------------------

from __future__ import annotations

import hashlib
import io
import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from flask import Flask, jsonify, request, send_file, send_from_directory

from agent_spatial_toolkit.pipeline.emit import SessionState, emit_annotations
from agent_spatial_toolkit.pipeline.error_mm import pose_rms_mm
from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult, PoseSolveError, solve_pnp
from agent_spatial_toolkit.pipeline.ray import pixel_to_part_local
from agent_spatial_toolkit.pipeline.triangulate import (
    MAX_VIEWS,
    TriangulationError,
    triangulate_feature,
)
from agent_spatial_toolkit.schema.models import (
    AnchorClick,
    CameraDetected,
    Feature,
    FeatureClick,
    FeatureMeasurement,
    Photo,
)
from agent_spatial_toolkit.schema.validators import ValidationError
from agent_spatial_toolkit.server.events import EventLog
from agent_spatial_toolkit.server.lens_catalog import list_lens_entries
from agent_spatial_toolkit.server.session import Session


class _ShutdownableServer(Protocol):
    """Protocol for a server with a shutdown() method.

    Both ``lifecycle.Server`` (production) and the test stub satisfy this.
    Defined as a Protocol so create_app doesn't import the heavy lifecycle
    module just for a type hint.
    """

    def shutdown(self) -> None: ...


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _coerce_finite_int(value: Any, field: str) -> int:
    """Coerce ``value`` to ``int``, raising ``ValueError`` with field context.

    Rejects NaN, ±Inf, and anything that doesn't cleanly cast. The HTTP
    routes catch ``ValueError`` and surface 400, so this keeps non-finite
    inputs from leaking into pose.py / ray.py and surfacing as 500.
    """
    try:
        result = int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field} must be an integer; got {value!r}") from e
    # ``int`` itself is always finite, but guard against bool/float-derived
    # values that round-tripped via numpy etc.
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite; got {value!r}")
    return result


def _coerce_finite_float(value: Any, field: str) -> float:
    """Coerce ``value`` to ``float``, raising ``ValueError`` on non-finite.

    Mirror of :func:`_coerce_finite_int` for floating-point fields. NaN/Inf
    are rejected explicitly so the API boundary returns 400, not 500.
    """
    try:
        result = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field} must be a number; got {value!r}") from e
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite; got {value!r}")
    return result


def _intrinsics_from_dict(d: dict[str, Any]) -> Intrinsics:
    """Reconstruct an Intrinsics dataclass from its to_dict() shape.

    The wire format groups distortion as ``{k1, k2, p1, p2, k3}`` per spec
    §6; the dataclass stores it as a flat list. This helper is the inverse
    of ``Intrinsics.to_dict()``. Kept private to app.py rather than added
    as a method on ``Intrinsics`` to avoid a cross-cutting change in PR #8.
    """
    distortion_dict = d["distortion"]
    # Reject NaN/Inf at the boundary — without this the failure surfaces
    # deep in pose.py / ray.py as a generic 500. Each numeric field carries
    # its own context label so the 400 message is actionable.
    return Intrinsics(
        profile_source=d["profile_source"],
        profile_id=d["profile_id"],
        fx_px=_coerce_finite_float(d["fx_px"], "intrinsics.fx_px"),
        fy_px=_coerce_finite_float(d["fy_px"], "intrinsics.fy_px"),
        cx=_coerce_finite_float(d["cx"], "intrinsics.cx"),
        cy=_coerce_finite_float(d["cy"], "intrinsics.cy"),
        distortion=[
            _coerce_finite_float(distortion_dict["k1"], "intrinsics.distortion.k1"),
            _coerce_finite_float(distortion_dict["k2"], "intrinsics.distortion.k2"),
            _coerce_finite_float(distortion_dict["p1"], "intrinsics.distortion.p1"),
            _coerce_finite_float(distortion_dict["p2"], "intrinsics.distortion.p2"),
            _coerce_finite_float(distortion_dict["k3"], "intrinsics.distortion.k3"),
        ],
        distortion_model=d.get("distortion_model", "opencv_5param"),
        profile_calibration_rms_px=d.get("profile_calibration_rms_px"),
    )


def _trigger_async_shutdown(
    server: _ShutdownableServer, delay_seconds: float = 1.0
) -> threading.Thread:
    """Schedule ``server.shutdown()`` after a short delay (issue #25 option 1).

    A synchronous call to ``server.shutdown()`` from inside a Flask request
    handler would deadlock: ``WSGIServer.shutdown()`` blocks until the
    serving thread exits, but the serving thread is currently executing
    *this* handler. The daemon thread defers the shutdown until after the
    response has flushed.
    """

    def _delayed_shutdown() -> None:
        time.sleep(delay_seconds)
        server.shutdown()

    t = threading.Thread(target=_delayed_shutdown, daemon=True)
    t.start()
    return t


def _safe_static_send(base_dir: Path, requested_id: str) -> Any:
    """Send a file from ``base_dir`` after verifying the path stays inside it.

    Defends against ``../`` traversal: resolve both the base and the target,
    then verify the target is still relative to base. Returns a Flask
    response (404 JSON if missing, file content if present).
    """
    base_resolved = base_dir.resolve()
    candidate = (base_dir / requested_id).resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError:
        # Path traversal — return 404 to avoid leaking the existence/shape
        # of paths outside the allowed directory.
        return jsonify({"error": "not found"}), 404
    if not candidate.is_file():
        return jsonify({"error": "not found"}), 404
    # send_from_directory re-applies its own safe_join, but we've already
    # validated so the resolved candidate.name within base_resolved is safe.
    return send_from_directory(base_resolved, candidate.relative_to(base_resolved).as_posix())


def _state_snapshot(session: Session, mem: dict[str, Any]) -> dict[str, Any]:
    """Build the JSON shape returned by ``GET /api/state``.

    Includes uploaded-but-not-yet-anchored photos by scanning
    ``session_dir/photos/`` so the wizard's Phase 2c can render them for
    anchor-clicking. ``pose=null`` differentiates "uploaded only" from
    "anchored" (pose populated).
    """
    photos_dir = session.session_dir / "photos"
    uploaded: list[str] = []
    if photos_dir.is_dir():
        for entry in sorted(photos_dir.iterdir()):
            if entry.is_file():
                uploaded.append(entry.name)

    photos_out: list[dict[str, Any]] = []
    seen: set[str] = set()
    # First, surface all photos on disk (with or without pose).
    for filename in uploaded:
        seen.add(filename)
        photo_entry = mem["photos"].get(filename, {})
        pose = photo_entry.get("pose")
        photos_out.append(
            {
                "id": filename,
                "url": f"/static/photos/{filename}",
                "intrinsics": photo_entry.get("intrinsics"),
                "pose": pose.to_dict() if isinstance(pose, PoseResult) else None,
                "intrinsics_suspect": (
                    pose.intrinsics_suspect if isinstance(pose, PoseResult) else None
                ),
                "image_size": (
                    list(photo_entry["image_size"]) if "image_size" in photo_entry else None
                ),
            }
        )
    # Then any in-memory photos that don't correspond to a file on disk yet
    # (e.g. tests that POST /api/anchors without uploading a photo file).
    for photo_id, entry in mem["photos"].items():
        if photo_id in seen:
            continue
        pose = entry.get("pose")
        photos_out.append(
            {
                "id": photo_id,
                "url": f"/static/photos/{photo_id}",
                "intrinsics": entry.get("intrinsics"),
                "pose": pose.to_dict() if isinstance(pose, PoseResult) else None,
                "intrinsics_suspect": (
                    pose.intrinsics_suspect if isinstance(pose, PoseResult) else None
                ),
                "image_size": list(entry["image_size"]) if "image_size" in entry else None,
            }
        )

    features_out: list[dict[str, Any]] = []
    for feature_id, entry in mem["features"].items():
        features_out.append(
            {
                "id": feature_id,
                "photo_id": entry["photo_id"],
                "pixel": list(entry["pixel"]),
                "pcb_xyz_mm": list(entry["pcb_xyz_mm"]),
                "method": entry["method"],
            }
        )

    return {
        "part_id": session.part_id,
        "session_dir": str(session.session_dir),
        "status": session.status,
        "anchors": mem.get("anchors", {}),
        "photos": photos_out,
        "features": features_out,
        "flags": list(mem.get("flags", [])),
    }


# ─────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────


def create_app(server: _ShutdownableServer, session: Session) -> Flask:
    """Create a Flask app bound to a running server + session.

    The server reference is stored on app.config so /api/finalize can
    schedule async shutdown without circular imports. The session reference
    drives session_dir, photo storage, and event log paths.
    """
    app = Flask(__name__)
    app.config["SERVER"] = server
    app.config["SESSION"] = session
    app.config["EVENT_LOG"] = EventLog(session.session_dir / "events.jsonl")
    # In-memory state — per-session, not per-request. Routes mutate this dict
    # under the GIL; Flask's dev server is single-threaded by default and the
    # alpha wizard runs locally one user at a time, so no extra locking is
    # required for v0.1.0.
    app.config["STATE"] = {
        "photos": {},  # photo_id -> {"intrinsics": dict, "pose": PoseResult, "image_size": (w, h), "anchors": [...]}
        "features": {},  # feature_id -> {"photo_id", "pixel", "pcb_xyz_mm", "method"}
        "anchors": {},  # photo_id -> raw anchors list (for replay/debugging)
        "flags": [],
    }

    # Short-circuit oversize bodies at the WSGI layer rather than reading the
    # full payload into memory before checking. The /api/photo route also
    # has its own 413 check as defense-in-depth (some clients use chunked
    # transfer-encoding without Content-Length, which bypasses this gate).
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

    @app.errorhandler(413)
    def _too_large(_e: Any) -> Any:
        """Friendly 413 message; matches the in-route check's wording."""
        return (
            jsonify(
                {"error": "This photo is unusually large (>50 MB) — reshoot at lower resolution."}
            ),
            413,
        )

    _register_routes(app)
    return app


# ─────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────


def _register_routes(app: Flask) -> None:
    @app.get("/")
    def index() -> Any:
        """Serve ``ui/index.html`` if present, else a placeholder.

        ``app.root_path`` resolves to the directory of the module that
        constructed the Flask instance — here ``…/agent_spatial_toolkit/server/``
        — so the UI lives in the sibling ``ui/`` directory inside the package
        (``…/agent_spatial_toolkit/ui/index.html``). Keeping UI inside the
        installable package is the only layout that survives a wheel build.
        """
        ui_index = Path(app.root_path).parent / "ui" / "index.html"
        if ui_index.is_file():
            return ui_index.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html"}
        return (
            "Wizard UI not yet built — API is available at /api/state, /api/anchors, "
            "/api/feature, /api/finalize.",
            200,
            {"Content-Type": "text/plain"},
        )

    @app.get("/api/state")
    def get_state() -> Any:
        session: Session = app.config["SESSION"]
        mem: dict[str, Any] = app.config["STATE"]
        return jsonify(_state_snapshot(session, mem))

    @app.post("/api/photo")
    def post_photo() -> Any:
        """Upload a photo to the session (design §3 photo lifecycle).

        Accepts JPEG, PNG, HEIC, HEIF. HEIC/HEIF is transparently decoded
        to JPEG via pillow-heif so iPhone users never see a format error.
        Photo ID is content-derived (``photo_<sha256[:12]>``) so re-upload
        of the same content is idempotent. Persists JPEG to
        ``<session>/photos/<photo_id>.jpg`` via the atomic tmp+rename
        pattern.
        """
        # Lazy import — keeps create_app() startup fast and avoids
        # paying the pillow-heif import cost on servers that never see
        # an HEIC upload.
        import pillow_heif
        from PIL import Image

        # 50 MB cap; phone shots rarely exceed 30 MB even at max-res HEIF.
        max_bytes = 50 * 1024 * 1024

        content_type = (request.content_type or "").lower().split(";")[0].strip()
        if content_type not in {"image/jpeg", "image/png", "image/heic", "image/heif"}:
            return (
                jsonify(
                    {"error": ("Please use JPEG, PNG, or HEIC. Most phones export one of these.")}
                ),
                415,
            )

        body = request.get_data(cache=False)
        if len(body) > max_bytes:
            return (
                jsonify(
                    {
                        "error": (
                            "This photo is unusually large (>50 MB) — reshoot at lower resolution."
                        )
                    }
                ),
                413,
            )

        sha256 = hashlib.sha256(body).hexdigest()
        photo_id = f"photo_{sha256[:12]}"

        # Decode then re-encode as JPEG for consistent on-disk format.
        # register_heif_opener() is idempotent so calling it on every
        # HEIC/HEIF request is safe.
        if content_type in {"image/heic", "image/heif"}:
            pillow_heif.register_heif_opener()
        try:
            img = Image.open(io.BytesIO(body))
            img.load()  # force decode now so failures surface here, not later
        except Exception as e:
            return jsonify({"error": f"could not decode image: {e}"}), 400

        # JPEG can't encode RGBA / palette modes — convert. HEIC and PNG
        # frequently arrive with alpha or non-RGB modes.
        if img.mode != "RGB":
            img = img.convert("RGB")

        session: Session = app.config["SESSION"]
        photos_dir = session.session_dir / "photos"
        photo_path = photos_dir / f"{photo_id}.jpg"
        # Atomic write: write to .jpg.tmp then rename. Mirrors the precedent
        # set in session.py / emit.py / app.py finalize.
        tmp_path = photo_path.with_suffix(".jpg.tmp")
        img.save(tmp_path, format="JPEG", quality=95)
        tmp_path.replace(photo_path)

        # Update in-memory state. Idempotent: re-upload of the same content
        # produces the same photo_id, so we keep the existing record.
        mem: dict[str, Any] = app.config["STATE"]
        if photo_id not in mem["photos"]:
            mem["photos"][photo_id] = {
                "id": photo_id,
                "path": str(photo_path),
                "sha256": sha256,
                "intrinsics": None,  # populated when /api/reference (or /api/anchors) runs
                "pose": None,
                "anchors": [],
            }

        event_log: EventLog = app.config["EVENT_LOG"]
        event_log.write(
            {
                "type": "photo_uploaded",
                "photo_id": photo_id,
                "sha256": sha256,
                "source_format": content_type,
            }
        )

        return jsonify(
            {
                "photo_id": photo_id,
                "sha256": sha256,
                "stored_format": "jpeg",
            }
        )

    @app.get("/api/lens_catalog")
    def get_lens_catalog() -> Any:
        """Return the canonical lens catalog (id + label + resolvability).

        Intrinsics data is NOT exposed — clients only need to populate the
        Phase 2a dropdown and decide whether resolution requires extra input
        (EXIF dict, full intrinsics dict).
        """
        return jsonify({"lenses": list_lens_entries()})

    @app.post("/api/anchors")
    def post_anchors() -> Any:
        event_log: EventLog = app.config["EVENT_LOG"]
        mem: dict[str, Any] = app.config["STATE"]

        body = request.get_json(silent=True) or {}
        try:
            photo_id = body["photo_id"]
            anchors_list = body["anchors"]
            image_size_in = body["image_size"]
        except (KeyError, TypeError):
            return (
                jsonify({"error": "missing required field (photo_id, anchors, image_size)"}),
                400,
            )

        if not isinstance(anchors_list, list) or not isinstance(image_size_in, list):
            return jsonify({"error": "anchors and image_size must be arrays"}), 400
        if len(image_size_in) != 2:
            return jsonify({"error": "image_size must be [width, height]"}), 400

        try:
            image_size = (
                _coerce_finite_int(image_size_in[0], "image_size[0]"),
                _coerce_finite_int(image_size_in[1], "image_size[1]"),
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        # Resolve intrinsics: explicit dict wins; otherwise resolve via lens_id.
        # The lens_id branch is the new wizard path (Task 1.D.5); the dict path
        # preserves backward compatibility with existing tests + scripts.
        intrinsics_dict = body.get("intrinsics")
        if intrinsics_dict is not None:
            try:
                intrinsics = _intrinsics_from_dict(intrinsics_dict)
            except (KeyError, TypeError, ValueError) as e:
                return jsonify({"error": f"invalid intrinsics: {e}"}), 400
        else:
            lens_id = body.get("lens_id")
            if not lens_id:
                return (
                    jsonify({"error": "must provide either intrinsics or lens_id"}),
                    400,
                )
            from agent_spatial_toolkit.server.lens_catalog import resolve as _resolve_lens

            intr_obj = _resolve_lens(lens_id, image_size, exif=body.get("exif"))
            if intr_obj is None:
                return (
                    jsonify(
                        {
                            "error": f"lens_id '{lens_id}' could not resolve to intrinsics; provide an explicit intrinsics dict"
                        }
                    ),
                    400,
                )
            intrinsics = intr_obj
            intrinsics_dict = intr_obj.to_dict()  # for the mem["photos"] record below

        try:
            world_points = np.array([a["pcb_xyz_mm"] for a in anchors_list], dtype=np.float64)
            pixel_points = np.array([a["pixel"] for a in anchors_list], dtype=np.float64)
        except (KeyError, TypeError, ValueError) as e:
            return jsonify({"error": f"invalid anchor entries: {e}"}), 400

        try:
            pose = solve_pnp(world_points, pixel_points, intrinsics, image_size)
        except PoseSolveError as e:
            # Verbose detail goes to events.jsonl for debugging; the client
            # gets a stable, message that doesn't leak cv2.error text or
            # source-file paths (per app.py module docstring).
            event_log.write(
                {
                    "type": "pose_failed",
                    "photo_id": photo_id,
                    "error_detail": str(e),
                }
            )
            return (
                jsonify({"error": "PnP failed: anchors are degenerate or insufficient"}),
                400,
            )
        except Exception:
            return jsonify({"error": "internal error during pose solve"}), 500

        # Store in in-memory state
        mem["photos"][photo_id] = {
            "intrinsics": intrinsics_dict,
            "pose": pose,
            "image_size": image_size,
            "anchors": anchors_list,
        }
        mem["anchors"][photo_id] = anchors_list

        event_log.write(
            {
                "type": "pose_solved",
                "photo_id": photo_id,
                "anchor_reprojection_rms_px": pose.anchor_reprojection_rms_px,
                "intrinsics_suspect": pose.intrinsics_suspect,
            }
        )

        return jsonify(
            {
                "pose": pose.to_dict(),
                "intrinsics_suspect": pose.intrinsics_suspect,
            }
        )

    @app.post("/api/reference")
    def post_reference() -> Any:
        """Confirm scale via the wizard's 4-corner reference-object flow.

        Design §2 step 4. Replaces the legacy ``/api/anchors`` path for the
        redesigned wizard. Same ``cv2.solvePnP`` machinery, but the 4 world
        points are derived from a known-dimensions reference type (credit
        card, dollar bill, marker) rather than typed by the user.

        The client POSTs ``{photo_id, reference_type, pixel_corners,
        image_size}`` plus either an explicit ``intrinsics`` dict or a
        ``lens_id`` (transitional; PR-3 adds full EXIF-auto + FOV-class
        fallback). ``pixel_corners`` MUST be a list of exactly 4 ``[x, y]``
        entries in clockwise-from-top-left order, index-aligned with the
        world corners returned by ``get_corner_positions_mm``.
        """
        from agent_spatial_toolkit.server.reference_objects import (
            ReferenceObjectError,
            get_corner_positions_mm,
        )

        event_log: EventLog = app.config["EVENT_LOG"]
        mem: dict[str, Any] = app.config["STATE"]
        session: Session = app.config["SESSION"]

        body = request.get_json(silent=True) or {}
        try:
            photo_id = body["photo_id"]
            reference_type = body["reference_type"]
            pixel_corners_in = body["pixel_corners"]
            image_size_in = body["image_size"]
        except (KeyError, TypeError):
            return (
                jsonify(
                    {
                        "error": (
                            "missing required field "
                            "(photo_id, reference_type, pixel_corners, image_size)"
                        )
                    }
                ),
                400,
            )

        if not isinstance(pixel_corners_in, list) or len(pixel_corners_in) != 4:
            return (
                jsonify({"error": "pixel_corners must be an array of exactly 4 [x, y] entries"}),
                400,
            )

        if not isinstance(image_size_in, list) or len(image_size_in) != 2:
            return jsonify({"error": "image_size must be [width, height]"}), 400

        try:
            world_corners = get_corner_positions_mm(reference_type)
        except ReferenceObjectError as e:
            return jsonify({"error": str(e)}), 400

        try:
            image_size = (
                _coerce_finite_int(image_size_in[0], "image_size[0]"),
                _coerce_finite_int(image_size_in[1], "image_size[1]"),
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        # Reuse intrinsics-resolution from /api/anchors (transitional;
        # PR-3 adds full EXIF-auto + FOV-class fallback).
        intrinsics_dict = body.get("intrinsics")
        if intrinsics_dict is not None:
            try:
                intrinsics = _intrinsics_from_dict(intrinsics_dict)
            except (KeyError, TypeError, ValueError) as e:
                return jsonify({"error": f"invalid intrinsics: {e}"}), 400
        else:
            # Fallback chain: lens_id (if provided) → EXIF (if photo on disk
            # carries focal_35) → wide-class default. Each fallback raises
            # the intrinsics_estimated flag for the LLM downstream.
            lens_id = body.get("lens_id")
            intr_obj: Intrinsics | None = None

            if lens_id:
                from agent_spatial_toolkit.server.lens_catalog import resolve as _resolve_lens

                intr_obj = _resolve_lens(lens_id, image_size, exif=body.get("exif"))

            # EXIF in request body (UI extracted client-side).
            if intr_obj is None:
                exif = body.get("exif") or {}
                focal_35 = exif.get("focal_length_35mm_equiv") if isinstance(exif, dict) else None
                if focal_35 is not None:
                    from agent_spatial_toolkit.pipeline.intrinsics import (
                        resolve_fallback_intrinsics,
                    )

                    intr_obj = resolve_fallback_intrinsics(float(focal_35), image_size)

            # EXIF read directly from the photo file on disk.
            if intr_obj is None:
                photos_dir = session.session_dir / "photos"
                candidates = list(photos_dir.glob(f"{photo_id}.*"))
                if candidates:
                    from agent_spatial_toolkit.pipeline.intrinsics import (
                        extract_exif_camera_info,
                        resolve_fallback_intrinsics,
                    )

                    cam_info = extract_exif_camera_info(candidates[0])
                    if cam_info is not None and cam_info.focal_length_35mm_equiv is not None:
                        intr_obj = resolve_fallback_intrinsics(
                            cam_info.focal_length_35mm_equiv, image_size
                        )

            # Final fallback: wide-class (24 mm 35mm-equiv) default.
            if intr_obj is None:
                from agent_spatial_toolkit.pipeline.intrinsics import (
                    resolve_fallback_intrinsics,
                )

                intr_obj = resolve_fallback_intrinsics(24.0, image_size)
                if intr_obj is None:
                    return (
                        jsonify(
                            {
                                "error": (
                                    "could not derive camera intrinsics; "
                                    "provide an explicit intrinsics dict"
                                )
                            }
                        ),
                        400,
                    )

            # Fallback paths all raise the estimation flag (the schema's
            # intrinsics_estimated, schema-merged in PR-1).
            existing = list(mem.get("flags", []))
            if "intrinsics_estimated" not in existing:
                existing.append("intrinsics_estimated")
                mem["flags"] = existing

            intrinsics = intr_obj
            intrinsics_dict = intr_obj.to_dict()

        try:
            world_points = np.array(world_corners, dtype=np.float64)
            pixel_points = np.array(pixel_corners_in, dtype=np.float64)
            if pixel_points.shape != (4, 2):
                raise ValueError("each pixel_corner must be [x, y]")
        except (ValueError, TypeError) as e:
            return jsonify({"error": f"invalid pixel_corners: {e}"}), 400

        try:
            pose = solve_pnp(world_points, pixel_points, intrinsics, image_size)
        except PoseSolveError as e:
            event_log.write(
                {
                    "type": "pose_failed",
                    "photo_id": photo_id,
                    "reference_type": reference_type,
                    "error_detail": str(e),
                }
            )
            return (
                jsonify(
                    {"error": ("pose solve failed: corners may be too oblique or mis-clicked")}
                ),
                400,
            )
        except Exception:
            return jsonify({"error": "internal error during pose solve"}), 500

        # Update in-memory state. Matches the photo-record shape established
        # by /api/photo (Task 3): photo entry exists from upload, this route
        # populates intrinsics + pose + reference fields. setdefault keeps
        # the route safe even if the client somehow skipped /api/photo.
        mem.setdefault("photos", {})
        photo_record = mem["photos"].setdefault(
            photo_id,
            {
                "id": photo_id,
                "intrinsics": None,
                "pose": None,
                "anchors": [],
            },
        )
        photo_record["intrinsics"] = intrinsics_dict
        photo_record["pose"] = pose
        photo_record["image_size"] = image_size
        photo_record["reference_type"] = reference_type
        photo_record["pixel_corners"] = pixel_corners_in

        event_log.write(
            {
                "type": "reference_solved",
                "photo_id": photo_id,
                "reference_type": reference_type,
                "anchor_reprojection_rms_px": pose.anchor_reprojection_rms_px,
                "intrinsics_suspect": pose.intrinsics_suspect,
            }
        )

        rms_mm_value = pose_rms_mm(
            pose=pose,
            intrinsics=intrinsics,
            anchor_rms_px=pose.anchor_reprojection_rms_px,
        )
        return jsonify(
            {
                "pose": pose.to_dict(),
                "intrinsics_suspect": pose.intrinsics_suspect,
                "pose_rms_mm": rms_mm_value,
            }
        )

    @app.post("/api/feature")
    def post_feature() -> Any:
        event_log: EventLog = app.config["EVENT_LOG"]
        mem: dict[str, Any] = app.config["STATE"]

        body = request.get_json(silent=True) or {}

        # Triangulation (γ-mode) is deferred to v0.1.0 — return 501 if the
        # caller explicitly requests it.
        method = body.get("method")
        if method and method != "planar_intersection":
            return (
                jsonify(
                    {"error": f"method '{method}' not implemented in v0.1.0-alpha (β-mode only)"}
                ),
                501,
            )

        # Wizard-redesign (PR-2 Task 5): the new request shape carries a
        # ``clicks`` list of ``{photo_id, pixel}`` entries.
        #   - len == 1  → unwrap into the existing single-view ray-cast path.
        #   - len >= 2  → triangulation, deferred to PR-3 (HTTP 501 stub).
        #   - len == 0  → 400.
        # The legacy shape (top-level photo_id + pixel, no ``clicks`` key)
        # is preserved during the transition until PR-4 migrates UI callers.
        if "clicks" in body:
            clicks = body["clicks"]
            if not isinstance(clicks, list):
                return jsonify({"error": "clicks must be an array"}), 400
            if len(clicks) == 0:
                return jsonify({"error": "clicks must contain at least one entry"}), 400
            if len(clicks) > MAX_VIEWS:
                return (
                    jsonify(
                        {
                            "error": (
                                f"v1 triangulates from up to {MAX_VIEWS} views; "
                                f"received {len(clicks)} — please reduce to your "
                                f"{MAX_VIEWS} best views"
                            )
                        }
                    ),
                    400,
                )
            if len(clicks) >= 2:
                # Multi-view triangulation path. Build (pose, intrinsics, pixel)
                # triples for each click; any missing photo / pose / intrinsics
                # is a 404 with the offending photo_id called out.
                try:
                    feature_id = body["feature_id"]
                except (KeyError, TypeError):
                    return (
                        jsonify({"error": "missing required field: feature_id"}),
                        400,
                    )
                view_triples: list[Any] = []
                for i, click in enumerate(clicks):
                    try:
                        c_photo_id = click["photo_id"]
                        c_pixel = click["pixel"]
                    except (KeyError, TypeError):
                        return (
                            jsonify(
                                {"error": (f"clicks[{i}] missing required field (photo_id, pixel)")}
                            ),
                            400,
                        )
                    photo_entry = mem["photos"].get(c_photo_id)
                    if photo_entry is None:
                        return (
                            jsonify({"error": f"unknown photo_id '{c_photo_id}' in clicks[{i}]"}),
                            404,
                        )
                    if photo_entry.get("pose") is None or photo_entry.get("intrinsics") is None:
                        return (
                            jsonify({"error": f"no pose for photo {c_photo_id}"}),
                            404,
                        )
                    try:
                        intrinsics_obj = _intrinsics_from_dict(photo_entry["intrinsics"])
                    except (KeyError, TypeError, ValueError) as e:
                        return (
                            jsonify({"error": f"stored intrinsics are malformed: {e}"}),
                            500,
                        )
                    pose_obj: PoseResult = photo_entry["pose"]
                    try:
                        pixel_pair = (
                            _coerce_finite_float(c_pixel[0], f"clicks[{i}].pixel[0]"),
                            _coerce_finite_float(c_pixel[1], f"clicks[{i}].pixel[1]"),
                        )
                    except (TypeError, ValueError, IndexError) as e:
                        return jsonify({"error": str(e)}), 400
                    view_triples.append((pose_obj, intrinsics_obj, pixel_pair))

                try:
                    tri = triangulate_feature(view_triples)
                except TriangulationError as e:
                    return jsonify({"error": str(e)}), 400

                method = f"triangulation_{tri.n_views}_views"
                pcb_xyz = [float(tri.xyz_mm[0]), float(tri.xyz_mm[1]), float(tri.xyz_mm[2])]
                # Persist with the multi-view shape: clicks list (not single
                # pixel), method enum reflecting view count, and the residual
                # metadata for emit.py to surface in quality_summary.
                mem["features"][feature_id] = {
                    "photo_id": clicks[0]["photo_id"],  # primary photo for legacy state-shape
                    "pixel": [float(clicks[0]["pixel"][0]), float(clicks[0]["pixel"][1])],
                    "pcb_xyz_mm": pcb_xyz,
                    "method": method,
                    "clicks": [
                        {
                            "photo_id": c["photo_id"],
                            "pixel": [float(c["pixel"][0]), float(c["pixel"][1])],
                            "reprojection_residual_px": tri.per_click_residuals_px[i],
                        }
                        for i, c in enumerate(clicks)
                    ],
                    "triangulation_rms_px": tri.triangulation_rms_px,
                    "max_residual_px": tri.max_residual_px,
                }
                event_log.write(
                    {
                        "type": "feature_triangulated",
                        "feature_id": feature_id,
                        "n_views": tri.n_views,
                        "triangulation_rms_px": tri.triangulation_rms_px,
                        "max_residual_px": tri.max_residual_px,
                    }
                )
                return jsonify(
                    {
                        "pcb_xyz_mm": pcb_xyz,
                        "method": method,
                        "n_views": tri.n_views,
                        "triangulation_rms_px": tri.triangulation_rms_px,
                        "max_residual_px": tri.max_residual_px,
                        "per_click_residuals_px": tri.per_click_residuals_px,
                    }
                )
            # Single-click case: fall through to the existing ray-cast logic
            # (unwrap clicks[0] into the legacy fields).
            try:
                feature_id = body["feature_id"]
                first_click = clicks[0]
                photo_id = first_click["photo_id"]
                pixel_in = first_click["pixel"]
            except (KeyError, TypeError):
                return (
                    jsonify(
                        {
                            "error": (
                                "missing required field "
                                "(feature_id, clicks[0].photo_id, clicks[0].pixel)"
                            )
                        }
                    ),
                    400,
                )
        else:
            try:
                feature_id = body["feature_id"]
                photo_id = body["photo_id"]
                pixel_in = body["pixel"]
            except (KeyError, TypeError):
                return jsonify(
                    {"error": "missing required field (feature_id, photo_id, pixel)"}
                ), 400

        try:
            z_assumed_mm = _coerce_finite_float(body.get("z_assumed_mm", 0.0), "z_assumed_mm")
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        photo_entry = mem["photos"].get(photo_id)
        if photo_entry is None:
            return jsonify(
                {"error": f"photo '{photo_id}' has no pose; call /api/anchors first"}
            ), 404
        # Match the wireframe handler's guard (app.py ~1076): an upload-only
        # photo (created via /api/photo without a subsequent /api/reference
        # or /api/anchors call) has a photo entry but no pose/intrinsics yet.
        # Without this check, _intrinsics_from_dict(None) raises TypeError
        # which the surrounding except returns as 500 "stored intrinsics are
        # malformed" — wrong status, wrong message. Return 404 like the
        # legacy contract for "no pose for this photo yet".
        if photo_entry.get("pose") is None or photo_entry.get("intrinsics") is None:
            return jsonify({"error": f"no pose for photo {photo_id}"}), 404

        try:
            intrinsics = _intrinsics_from_dict(photo_entry["intrinsics"])
        except (KeyError, TypeError, ValueError) as e:
            return jsonify({"error": f"stored intrinsics are malformed: {e}"}), 500

        pose: PoseResult = photo_entry["pose"]
        try:
            pixel = np.array(pixel_in, dtype=np.float64)
        except (TypeError, ValueError) as e:
            return jsonify({"error": f"invalid pixel: {e}"}), 400

        try:
            xy = pixel_to_part_local(pixel, pose, intrinsics, z_assumed_mm)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception:
            return jsonify({"error": "internal error during ray-cast"}), 500

        pcb_xyz = [float(xy[0]), float(xy[1]), z_assumed_mm]
        mem["features"][feature_id] = {
            "photo_id": photo_id,
            "pixel": [float(pixel_in[0]), float(pixel_in[1])],
            "pcb_xyz_mm": pcb_xyz,
            "method": "planar_intersection",
            "z_assumed_mm": z_assumed_mm,
        }

        event_log.write(
            {
                "type": "feature_clicked",
                "feature_id": feature_id,
                "photo_id": photo_id,
                "pixel": [float(pixel_in[0]), float(pixel_in[1])],
                "pcb_xyz_mm": pcb_xyz,
                "method": "planar_intersection",
            }
        )

        return jsonify({"pcb_xyz_mm": pcb_xyz})

    # ─────────────────────────────────────────────────────────────────
    # Stub endpoints: contract surface for the redesigned wizard. Real
    # logic for these arrives in PR-3; PR-2 ships the shapes only so
    # the UI (PR-4) and PR-3's wiring can land independently.
    # ─────────────────────────────────────────────────────────────────

    @app.get("/api/marker_detect/<path:photo_id>")
    def get_marker_detect(photo_id: str) -> Any:
        """Run cv2.aruco.detectMarkers against the photo on disk.

        Returns ``{"corners": [[x, y], [x, y], [x, y], [x, y]]}`` clockwise
        from top-left when the wizard's MARKER_ID is found; ``{"corners":
        null}`` when no marker is detected (the UI falls through to the
        manual corner walkthrough). 404 if the photo file is missing on
        disk.
        """
        from werkzeug.utils import secure_filename

        from agent_spatial_toolkit.server.marker_detect import detect_marker_corners

        safe_id = secure_filename(photo_id)
        if not safe_id or safe_id != photo_id:
            return jsonify({"error": "invalid photo_id"}), 404

        session: Session = app.config["SESSION"]
        photos_dir = session.session_dir / "photos"
        candidates = list(photos_dir.glob(f"{safe_id}.*"))
        # Filter to safe candidates only (no traversal escape).
        photos_dir_resolved = photos_dir.resolve()
        safe_candidates = [c for c in candidates if c.resolve().is_relative_to(photos_dir_resolved)]
        if not safe_candidates:
            return jsonify({"error": f"photo file for {safe_id} not found on disk"}), 404

        try:
            corners = detect_marker_corners(safe_candidates[0])
        except FileNotFoundError:
            return jsonify({"error": f"photo file for {safe_id} not found on disk"}), 404

        if corners is None:
            return jsonify({"corners": None})
        return jsonify({"corners": [[x, y] for x, y in corners]})

    @app.get("/api/next_prompt")
    def get_next_prompt() -> Any:
        """Return the next-photo prompt per design §4 scoring algorithm.

        Computes per-feature `max_error_mm` from the stored triangulation
        residuals so the suppression rule (≥6 features uniformly green-tier)
        can fire even with fewer than 3 cells filled.
        """
        from agent_spatial_toolkit.pipeline.error_mm import (
            feature_error_mm_triangulated,
        )
        from agent_spatial_toolkit.server.next_prompt import score_next_prompt

        mem: dict[str, Any] = app.config["STATE"]

        photos_in: list[dict[str, Any]] = []
        for photo_id, entry in mem["photos"].items():
            pose = entry.get("pose")
            if isinstance(pose, PoseResult):
                photos_in.append({"id": photo_id, "pose": pose})

        features_in: list[dict[str, Any]] = []
        for feature_id, feat in mem["features"].items():
            method = feat.get("method", "")
            entry = {"id": feature_id, "method": method}
            # For triangulated features, compute the worst per-photo mm error
            # so score_next_prompt can apply the suppression rule.
            if method.startswith("triangulation_"):
                xyz = np.array(feat.get("pcb_xyz_mm", [0.0, 0.0, 0.0]), dtype=np.float64)
                clicks = feat.get("clicks", [])
                worst_mm = 0.0
                for click in clicks:
                    photo_entry = mem["photos"].get(click.get("photo_id"))
                    if photo_entry is None or not isinstance(photo_entry.get("pose"), PoseResult):
                        continue
                    intrinsics = _intrinsics_from_dict(photo_entry["intrinsics"])
                    err_mm = feature_error_mm_triangulated(
                        feature_xyz_mm=xyz,
                        pose=photo_entry["pose"],
                        intrinsics=intrinsics,
                        residual_px=float(click.get("reprojection_residual_px", 0.0)),
                    )
                    if err_mm > worst_mm:
                        worst_mm = err_mm
                entry["max_error_mm"] = worst_mm
            features_in.append(entry)

        return jsonify(score_next_prompt(photos=photos_in, features=features_in))

    @app.get("/api/reproject_all")
    def get_reproject_all() -> Any:
        """For every (photo, feature) where the feature is triangulated,
        project its 3D position into the photo and report the predicted
        pixel + error_mm. Single-view-planar features are reported on
        their owning photo only.

        Response: {"by_photo": {photo_id: [{feature_id, predicted_pixel,
        error_mm}, ...], ...}}.
        """
        from agent_spatial_toolkit.pipeline.error_mm import (
            feature_error_mm_planar,
            feature_error_mm_triangulated,
        )

        mem: dict[str, Any] = app.config["STATE"]
        by_photo: dict[str, list[dict[str, Any]]] = {}

        # Initialize a per-photo list for every photo with a pose.
        for photo_id, entry in mem["photos"].items():
            if isinstance(entry.get("pose"), PoseResult):
                by_photo[photo_id] = []

        for feature_id, feat in mem["features"].items():
            method: str = feat.get("method", "")
            xyz = np.array(feat.get("pcb_xyz_mm", [0.0, 0.0, 0.0]), dtype=np.float64)

            if method.startswith("triangulation_"):
                # Project into every photo the feature was clicked in.
                clicks = feat.get("clicks", [])
                for click in clicks:
                    photo_id = click["photo_id"]
                    photo_entry = mem["photos"].get(photo_id)
                    if photo_entry is None or not isinstance(photo_entry.get("pose"), PoseResult):
                        continue
                    intrinsics = _intrinsics_from_dict(photo_entry["intrinsics"])
                    pose: PoseResult = photo_entry["pose"]
                    K = intrinsics.to_camera_matrix()  # noqa: N806
                    dist = np.array(intrinsics.distortion, dtype=np.float64)
                    projected, _ = cv2.projectPoints(
                        xyz.reshape(1, 1, 3),
                        pose.rvec.astype(np.float64),
                        pose.tvec.astype(np.float64),
                        K,
                        dist,
                    )
                    u, v = projected.reshape(2)
                    residual = float(click.get("reprojection_residual_px", 0.0))
                    err_mm = feature_error_mm_triangulated(
                        feature_xyz_mm=xyz,
                        pose=pose,
                        intrinsics=intrinsics,
                        residual_px=residual,
                    )
                    by_photo.setdefault(photo_id, []).append(
                        {
                            "feature_id": feature_id,
                            "predicted_pixel": [float(u), float(v)],
                            "error_mm": err_mm,
                        }
                    )
            elif method == "planar_intersection":
                # Single-view: report on the owning photo only.
                photo_id = feat.get("photo_id")
                if photo_id is None:
                    continue
                photo_entry = mem["photos"].get(photo_id)
                if photo_entry is None or not isinstance(photo_entry.get("pose"), PoseResult):
                    continue
                intrinsics = _intrinsics_from_dict(photo_entry["intrinsics"])
                pose = photo_entry["pose"]
                K = intrinsics.to_camera_matrix()  # noqa: N806
                dist = np.array(intrinsics.distortion, dtype=np.float64)
                projected, _ = cv2.projectPoints(
                    xyz.reshape(1, 1, 3),
                    pose.rvec.astype(np.float64),
                    pose.tvec.astype(np.float64),
                    K,
                    dist,
                )
                u, v = projected.reshape(2)
                # Planar features have no per-photo residual stored; report 0.
                err_mm = feature_error_mm_planar(
                    pose=pose,
                    intrinsics=intrinsics,
                    residual_px=0.0,
                )
                by_photo.setdefault(photo_id, []).append(
                    {
                        "feature_id": feature_id,
                        "predicted_pixel": [float(u), float(v)],
                        "error_mm": err_mm,
                    }
                )

        return jsonify({"by_photo": by_photo})

    @app.post("/api/finalize")
    def post_finalize() -> Any:
        session: Session = app.config["SESSION"]
        server: _ShutdownableServer = app.config["SERVER"]
        event_log: EventLog = app.config["EVENT_LOG"]
        mem: dict[str, Any] = app.config["STATE"]

        body = request.get_json(silent=True) or {}
        caller_flags = body.get("flags", []) if isinstance(body, dict) else []
        if not isinstance(caller_flags, list):
            return jsonify({"error": "flags must be a list"}), 400

        # Auto-derive spec-mandated closed-enum flags (spec §6 line 458 —
        # "Implementations MUST emit only these strings"; the server, not
        # the client, is responsible for emitting them).
        auto_flags: list[str] = []

        # Every β-mode feature ⇒ feature_clicked_only_once:<feature_id>.
        # In v0.1.0-alpha all features are β-mode (single-photo) by design.
        for feature_id, _entry in mem["features"].items():
            auto_flags.append(f"feature_clicked_only_once:{feature_id}")

        # Any photo with intrinsics_suspect=True ⇒ session-wide
        # intrinsics_suspect_high_anchor_rms (spec §6 line 462).
        suspect_photos = [
            pid
            for pid, p in mem["photos"].items()
            if isinstance(p.get("pose"), PoseResult) and p["pose"].intrinsics_suspect
        ]
        if suspect_photos:
            auto_flags.append("intrinsics_suspect_high_anchor_rms")

        # ≥2 suspect photos ⇒ intrinsics_session_recommend_chessboard
        # (spec §6 line 464 / §5.2 "session-wide auto-promotion recommendation").
        if len(suspect_photos) >= 2:
            auto_flags.append("intrinsics_session_recommend_chessboard")

        # Read manifest.json (written by the CLI in PR #27) for sha256 + path.
        # Manifest missing or corrupt is non-fatal: the server tolerates a
        # no-CLI test/dev path by falling back to empty sha256 + a default
        # path string.
        manifest_path = session.session_dir / "manifest.json"
        manifest_data: dict[str, dict[str, Any]] = {}
        if manifest_path.is_file():
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest_data = {}

        # Build SessionState from the in-memory state.
        photos_out: list[Photo] = []
        skipped_unsolved_flags: list[str] = []
        for photo_id, entry in mem["photos"].items():
            pose = entry.get("pose")
            intr = entry.get("intrinsics")
            if pose is None or intr is None:
                # Photo was uploaded via /api/photo but never had pose solved
                # via /api/reference (or /api/anchors). Skip it from the
                # final annotations and surface a per-photo flag so the LLM
                # downstream sees what happened.
                skipped_unsolved_flags.append(f"pose_skipped_uploaded_only:{photo_id}")
                continue
            anchors_clicked = [
                AnchorClick(
                    id=a["id"],
                    pcb_xyz_mm=tuple(a["pcb_xyz_mm"]),
                    pixel=tuple(a["pixel"]),
                )
                for a in entry.get("anchors", [])
            ]
            photo_meta = manifest_data.get(photo_id, {})
            photos_out.append(
                Photo(
                    id=photo_id,
                    path=photo_meta.get("path", f"photos/{photo_id}"),
                    sha256=photo_meta.get("sha256", ""),
                    camera_detected=CameraDetected(),
                    intrinsics=entry["intrinsics"],
                    pose=pose.to_dict() if isinstance(pose, PoseResult) else pose,
                    anchors_clicked=anchors_clicked,
                )
            )

        # Merge: server-derived auto-flags + per-photo skipped flags +
        # in-memory accumulated flags + caller-supplied. Dedupe via
        # dict-of-keys preserving order.
        all_flags = list(
            dict.fromkeys(
                auto_flags
                + skipped_unsolved_flags
                + list(mem.get("flags", []))
                + list(caller_flags)
            )
        )

        features_out: list[Feature] = []
        for feature_id, entry in mem["features"].items():
            entry_method = entry.get("method", "")
            if entry_method.startswith("triangulation_"):
                # Multi-view triangulated feature (Task 2 / PR-3 marquee path).
                # The feature was stored with a clicks list — each click
                # carrying its own reprojection_residual_px — plus session-
                # level triangulation_rms_px / max_residual_px. Emit the
                # full multi-view shape; visible_in lists every click's
                # photo_id, NOT just the primary.
                clicks_entries = entry.get("clicks", [])
                features_out.append(
                    Feature(
                        id=feature_id,
                        visible_in=[c["photo_id"] for c in clicks_entries],
                        pcb_xyz_mm=tuple(entry["pcb_xyz_mm"]),
                        measurements=FeatureMeasurement(
                            method=entry_method,  # type: ignore[arg-type]
                            triangulation_rms_px=entry["triangulation_rms_px"],
                            max_residual_px=entry["max_residual_px"],
                            per_photo_clicks=[
                                FeatureClick(
                                    photo=c["photo_id"],
                                    pixel=tuple(c["pixel"]),
                                    reprojection_residual_px=c["reprojection_residual_px"],
                                )
                                for c in clicks_entries
                            ],
                        ),
                    )
                )
            else:
                # Single-view planar_intersection (legacy ray-cast path).
                features_out.append(
                    Feature(
                        id=feature_id,
                        visible_in=[entry["photo_id"]],
                        pcb_xyz_mm=tuple(entry["pcb_xyz_mm"]),
                        measurements=FeatureMeasurement(
                            method="planar_intersection",
                            z_assumed_mm=entry.get("z_assumed_mm", 0.0),
                            z_assumed_reason="single_photo_only_default",
                            per_photo_clicks=[
                                FeatureClick(
                                    photo=entry["photo_id"],
                                    pixel=tuple(entry["pixel"]),
                                )
                            ],
                        ),
                    )
                )

        state = SessionState(
            part_id=session.part_id,
            part_display_name=None,
            part_class=None,
            notes=None,
            reference_frame={
                "origin_description": "(populated in later UI PR)",
                "x_axis_description": "(populated in later UI PR)",
                "y_axis_description": "(populated in later UI PR)",
                "z_axis_description": "(populated in later UI PR)",
                "units": "mm",
            },
            photos=photos_out,
            features=features_out,
            flags=all_flags,
            events_jsonl_filename="events.jsonl",
            manifest_filename="manifest.json",
            overlay_pngs=[],
            chessboard_calibration_image=None,
        )

        try:
            out_path = emit_annotations(state, session_dir=session.session_dir)
        except ValidationError as e:
            return jsonify({"error": str(e)}), 400
        except Exception:
            return jsonify({"error": "internal error during annotations emit"}), 500

        # Spec §3 lines 122/129: status.json is a SEPARATE file from
        # state.json, used by agent pollers to detect "done". Atomic write
        # via tmp + replace mirrors the precedent in session.py / emit.py.
        status_path = session.session_dir / "status.json"
        tmp_status = status_path.with_suffix(".json.tmp")
        tmp_status.write_text('{"status": "done"}\n', encoding="utf-8")
        tmp_status.replace(status_path)
        session.status = "done"

        # Update state.json to reflect the terminal status (spec line 239 —
        # "live session, replaced on each event"; finalize is the most
        # consequential event). Without this, a wizard interruption + reload
        # after finalize would still see status="in_progress".
        state_path = session.state_path()
        tmp_state = state_path.with_suffix(state_path.suffix + ".tmp")
        tmp_state.write_text(
            json.dumps(session.to_state_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        tmp_state.replace(state_path)

        event_log.write({"type": "finalized", "annotations_path": str(out_path)})

        # Schedule shutdown so the response can flush before the WSGI thread
        # is asked to stop (issue #25 option 1).
        _trigger_async_shutdown(server)

        return jsonify({"annotations_path": str(out_path), "status": "done"})

    @app.get("/api/wireframe/<path:photo_id>")
    def get_wireframe(photo_id: str) -> Any:
        """Render + serve a wireframe overlay PNG for the given photo (PR-β).

        Lazily renders on each request using ``render_wireframe`` from
        ``pipeline.reproject``. The photo must have a solved pose in ``mem``
        (i.e. ``/api/anchors`` has been POSTed for it). Path traversal is
        blocked via ``secure_filename`` + parent-resolution check.

        Returns 404 when the source photo file is missing on disk — the
        wireframe's purpose is visual confirmation against the actual photo,
        so synthesizing a blank fallback would silently undermine that. The
        wireframe test in tests/test_app.py seeds a real photo file in the
        session before POSTing anchors.
        """
        from werkzeug.utils import secure_filename

        from agent_spatial_toolkit.pipeline.reproject import render_wireframe

        # Path-traversal protection: secure_filename collapses traversal
        # segments so the result must equal the original id.
        safe_id = secure_filename(photo_id)
        if not safe_id or safe_id != photo_id:
            return jsonify({"error": "invalid photo_id"}), 404

        session: Session = app.config["SESSION"]
        mem: dict[str, Any] = app.config["STATE"]
        photo_state = mem["photos"].get(safe_id)
        if photo_state is None:
            return jsonify({"error": f"no pose for photo {safe_id}"}), 404
        # /api/photo (PR-2 Task 3) creates a photo entry before any pose is
        # solved (intrinsics=None, pose=None). The wireframe endpoint
        # pre-dates that flow and historically assumed photo_state was only
        # populated post-anchors. Without this check, GET /api/wireframe
        # for an upload-only photo returns 500 (NoneType subscript). 404 is
        # the correct legacy contract: "no pose for this photo yet".
        if photo_state.get("pose") is None or photo_state.get("intrinsics") is None:
            return jsonify({"error": f"no pose for photo {safe_id}"}), 404

        # Locate the source photo file (id may be the basename without extension
        # or with — handle both by globbing).
        photos_dir = session.session_dir / "photos"
        candidates = list(photos_dir.glob(f"{safe_id}.*")) + (
            [photos_dir / safe_id] if (photos_dir / safe_id).exists() else []
        )

        # Defensive: ensure resolved path is under photos_dir (no traversal escape).
        safe_candidates = []
        for c in candidates:
            try:
                c.resolve().relative_to(photos_dir.resolve())
                safe_candidates.append(c)
            except ValueError:
                pass

        # Build the wireframe output dir.
        wireframes_dir = session.session_dir / "wireframes"
        wireframes_dir.mkdir(parents=True, exist_ok=True)
        out_path = wireframes_dir / f"{safe_id}_wireframe.png"

        # Reconstruct Intrinsics + PoseResult + frame anchors from stored state.
        try:
            intrinsics = _intrinsics_from_dict(photo_state["intrinsics"])
        except (KeyError, TypeError, ValueError) as e:
            return jsonify({"error": f"stored intrinsics invalid: {e}"}), 500

        pose_val = photo_state["pose"]
        if isinstance(pose_val, PoseResult):
            pose = pose_val
        else:
            pose = PoseResult(
                rvec=np.array(pose_val["rvec"], dtype=np.float64),
                tvec=np.array(pose_val["tvec"], dtype=np.float64),
                anchor_reprojection_rms_px=pose_val.get("anchor_reprojection_rms_px", 0.0),
                intrinsics_suspect=pose_val.get("intrinsics_suspect", False),
                pose_solver=pose_val.get("pose_solver", "unknown"),
            )

        anchors_list = mem["anchors"].get(safe_id, [])
        frame_anchors = [
            {"id": f"a{i}", "xyz": a["pcb_xyz_mm"]} for i, a in enumerate(anchors_list)
        ]

        if not safe_candidates:
            return jsonify({"error": f"photo file for {safe_id} not found on disk"}), 404
        photo_path = safe_candidates[0]

        try:
            render_wireframe(
                photo_path=photo_path,
                out_path=out_path,
                frame_anchors=frame_anchors,
                pose=pose,
                intrinsics=intrinsics,
            )
        except (ValueError, FileNotFoundError, OSError) as e:
            return jsonify({"error": f"wireframe render failed: {e}"}), 500

        return send_file(out_path, mimetype="image/png")

    @app.get("/static/photos/<path:requested_id>")
    def static_photo(requested_id: str) -> Any:
        session: Session = app.config["SESSION"]
        return _safe_static_send(session.session_dir / "photos", requested_id)

    @app.get("/static/overlays/<path:requested_id>")
    def static_overlay(requested_id: str) -> Any:
        session: Session = app.config["SESSION"]
        return _safe_static_send(session.session_dir / "overlays", requested_id)

    @app.get("/ui/<path:filename>")
    def ui_asset(filename: str) -> Any:
        # Serve helpers.js / app.js / style.css from the package's ui/
        # directory. ``app.root_path`` resolves to the server/ subpackage,
        # so .parent / "ui" is the sibling UI directory shipped in the
        # wheel. _safe_static_send guards against ``../`` traversal.
        ui_dir = Path(app.root_path).parent / "ui"
        return _safe_static_send(ui_dir, filename)
