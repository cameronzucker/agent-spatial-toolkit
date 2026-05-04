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

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from flask import Flask, jsonify, request, send_from_directory

from agent_spatial_toolkit.pipeline.emit import SessionState, emit_annotations
from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult, PoseSolveError, solve_pnp
from agent_spatial_toolkit.pipeline.ray import pixel_to_part_local
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


def _intrinsics_from_dict(d: dict[str, Any]) -> Intrinsics:
    """Reconstruct an Intrinsics dataclass from its to_dict() shape.

    The wire format groups distortion as ``{k1, k2, p1, p2, k3}`` per spec
    §6; the dataclass stores it as a flat list. This helper is the inverse
    of ``Intrinsics.to_dict()``. Kept private to app.py rather than added
    as a method on ``Intrinsics`` to avoid a cross-cutting change in PR #8.
    """
    distortion_dict = d["distortion"]
    return Intrinsics(
        profile_source=d["profile_source"],
        profile_id=d["profile_id"],
        fx_px=float(d["fx_px"]),
        fy_px=float(d["fy_px"]),
        cx=float(d["cx"]),
        cy=float(d["cy"]),
        distortion=[
            float(distortion_dict["k1"]),
            float(distortion_dict["k2"]),
            float(distortion_dict["p1"]),
            float(distortion_dict["p2"]),
            float(distortion_dict["k3"]),
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
    """Build the JSON shape returned by ``GET /api/state``."""
    photos_out: list[dict[str, Any]] = []
    for photo_id, entry in mem["photos"].items():
        pose = entry.get("pose")
        photos_out.append(
            {
                "id": photo_id,
                "intrinsics": entry.get("intrinsics"),
                "pose": pose.to_dict() if isinstance(pose, PoseResult) else None,
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

    _register_routes(app)
    return app


# ─────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────


def _register_routes(app: Flask) -> None:
    @app.get("/")
    def index() -> Any:
        """Serve ``ui/index.html`` if present, else a placeholder.

        Stream D (UI build) ships index.html. For this PR the UI does not
        exist yet — return 200 with a placeholder so the URL works and
        clients can still poll the API alongside.
        """
        ui_index = Path(app.root_path).parent.parent.parent / "ui" / "index.html"
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

    @app.post("/api/anchors")
    def post_anchors() -> Any:
        event_log: EventLog = app.config["EVENT_LOG"]
        mem: dict[str, Any] = app.config["STATE"]

        body = request.get_json(silent=True) or {}
        try:
            photo_id = body["photo_id"]
            intrinsics_dict = body["intrinsics"]
            anchors_list = body["anchors"]
            image_size_in = body["image_size"]
        except (KeyError, TypeError):
            return (
                jsonify(
                    {"error": "missing required field (photo_id, intrinsics, anchors, image_size)"}
                ),
                400,
            )

        if not isinstance(anchors_list, list) or not isinstance(image_size_in, list):
            return jsonify({"error": "anchors and image_size must be arrays"}), 400
        if len(image_size_in) != 2:
            return jsonify({"error": "image_size must be [width, height]"}), 400

        try:
            intrinsics = _intrinsics_from_dict(intrinsics_dict)
        except (KeyError, TypeError, ValueError) as e:
            return jsonify({"error": f"invalid intrinsics: {e}"}), 400

        try:
            world_points = np.array([a["pcb_xyz_mm"] for a in anchors_list], dtype=np.float64)
            pixel_points = np.array([a["pixel"] for a in anchors_list], dtype=np.float64)
        except (KeyError, TypeError, ValueError) as e:
            return jsonify({"error": f"invalid anchor entries: {e}"}), 400

        image_size = (int(image_size_in[0]), int(image_size_in[1]))

        try:
            pose = solve_pnp(world_points, pixel_points, intrinsics, image_size)
        except PoseSolveError as e:
            event_log.write(
                {
                    "type": "pose_failed",
                    "photo_id": photo_id,
                    "error": str(e),
                }
            )
            return jsonify({"error": str(e)}), 400
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

        try:
            feature_id = body["feature_id"]
            photo_id = body["photo_id"]
            pixel_in = body["pixel"]
        except (KeyError, TypeError):
            return jsonify({"error": "missing required field (feature_id, photo_id, pixel)"}), 400

        z_assumed_mm = float(body.get("z_assumed_mm", 0.0))

        photo_entry = mem["photos"].get(photo_id)
        if photo_entry is None:
            return jsonify(
                {"error": f"photo '{photo_id}' has no pose; call /api/anchors first"}
            ), 404

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

    @app.post("/api/finalize")
    def post_finalize() -> Any:
        session: Session = app.config["SESSION"]
        server: _ShutdownableServer = app.config["SERVER"]
        event_log: EventLog = app.config["EVENT_LOG"]
        mem: dict[str, Any] = app.config["STATE"]

        body = request.get_json(silent=True) or {}
        extra_flags = body.get("flags", []) if isinstance(body, dict) else []
        if not isinstance(extra_flags, list):
            return jsonify({"error": "flags must be a list"}), 400

        all_flags = list(mem.get("flags", [])) + list(extra_flags)

        # Build SessionState from the in-memory state.
        photos_out: list[Photo] = []
        for photo_id, entry in mem["photos"].items():
            pose = entry["pose"]
            anchors_clicked = [
                AnchorClick(
                    id=a["id"],
                    pcb_xyz_mm=tuple(a["pcb_xyz_mm"]),
                    pixel=tuple(a["pixel"]),
                )
                for a in entry.get("anchors", [])
            ]
            photos_out.append(
                Photo(
                    id=photo_id,
                    path=f"photos/{photo_id}",
                    sha256="",  # populated by upload pipeline in a later PR
                    camera_detected=CameraDetected(),
                    intrinsics=entry["intrinsics"],
                    pose=pose.to_dict() if isinstance(pose, PoseResult) else pose,
                    anchors_clicked=anchors_clicked,
                )
            )

        features_out: list[Feature] = []
        for feature_id, entry in mem["features"].items():
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

        event_log.write({"type": "finalized", "annotations_path": str(out_path)})

        # Schedule shutdown so the response can flush before the WSGI thread
        # is asked to stop (issue #25 option 1).
        _trigger_async_shutdown(server)

        return jsonify({"annotations_path": str(out_path), "status": "done"})

    @app.get("/static/photos/<path:requested_id>")
    def static_photo(requested_id: str) -> Any:
        session: Session = app.config["SESSION"]
        return _safe_static_send(session.session_dir / "photos", requested_id)

    @app.get("/static/overlays/<path:requested_id>")
    def static_overlay(requested_id: str) -> Any:
        session: Session = app.config["SESSION"]
        return _safe_static_send(session.session_dir / "overlays", requested_id)
