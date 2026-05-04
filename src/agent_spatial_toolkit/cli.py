"""CLI entry point for the wizard server (spec §3 step 1).

Invocation:

    agent-spatial-toolkit annotate \\
        --part-id <id> --photos <path> [<path> ...] \\
        [--out DIR] [--host HOST] [--port PORT]

Sequence:

1. Validate ``--part-id`` against the session module's allow-list regex (no
   path-traversal, no separators).
2. Validate each ``--photos`` path: must exist, must be a JPEG/PNG (spec §8).
3. Create a session directory via ``server.session.create_session``.
4. Copy each photo into ``<session_dir>/photos/`` (filenames preserved).
5. Compute SHA-256 of each copied photo and emit ``manifest.json`` (spec §6's
   ``session_artifacts.manifest`` — ``/api/state`` and ``/api/finalize`` consume
   it as the source of truth for ``photos[*].sha256``).
6. Build the Flask app and start the lifecycle-managed HTTP server.
7. Print ``Open <url>`` to stdout (so an orchestrating agent can scrape the URL).
8. Block until shutdown — the server stops on ``/api/finalize`` or an idle
   timeout (default 30 minutes per spec §4). Exits 0 on clean shutdown.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from agent_spatial_toolkit.server.app import create_app
from agent_spatial_toolkit.server.lifecycle import start_server

# ``_validate_part_id`` is single-underscore-prefixed in session.py; importing
# it here is acceptable for first-party code and keeps the regex as a single
# source of truth shared by both ``create_session`` and the CLI.
from agent_spatial_toolkit.server.session import (
    _validate_part_id,
    create_session,
    default_base_dir,
)

ALLOWED_PHOTO_EXTS = frozenset({".jpg", ".jpeg", ".png"})


def _validate_photo_path(path_str: str) -> Path:
    """argparse type-converter: path must exist + be a decodable JPEG/PNG (spec §8 line 626)."""
    p = Path(path_str).expanduser().resolve()
    if not p.is_file():
        raise argparse.ArgumentTypeError(f"photo not found: {path_str}")
    if p.suffix.lower() not in ALLOWED_PHOTO_EXTS:
        raise argparse.ArgumentTypeError(f"photo must be JPEG or PNG; got {p.suffix!r}: {path_str}")
    # Content validation: open + verify(). Pillow's verify() does a fast
    # structural check without decoding pixel data. A .jpg-named text file
    # would pass the suffix check above and later break the wizard; this
    # rejects spoofed/corrupt files at argparse time.
    try:
        from PIL import Image

        with Image.open(p) as img:
            img.verify()
    except Exception as e:
        raise argparse.ArgumentTypeError(
            f"photo is not a valid JPEG/PNG (decode failed): {path_str}: {e}"
        ) from e
    return p


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser with the ``annotate`` subcommand."""
    parser = argparse.ArgumentParser(
        prog="agent-spatial-toolkit",
        description="Wizard for capturing spatial annotations on PCB photos.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    annotate = sub.add_parser("annotate", help="Start the wizard for a part.")
    annotate.add_argument(
        "--part-id",
        required=True,
        help="Part identifier (e.g. 'x1207').",
    )
    annotate.add_argument(
        "--photos",
        required=True,
        nargs="+",
        type=_validate_photo_path,
        help="Paths to one or more photos (JPEG or PNG).",
    )
    annotate.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Override the default session base directory (~/.spatial-annotations/).",
    )
    annotate.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind. Default 127.0.0.1; use 0.0.0.0 for LAN access.",
    )
    annotate.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port to bind. Default 0 (OS-assigned).",
    )
    return parser


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 of a file. Streams in 1 MB chunks so large photos don't OOM."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_photos_and_build_manifest(
    photos: list[Path],
    session_photos_dir: Path,
) -> dict[str, dict[str, object]]:
    """Copy each photo to ``session_photos_dir``, returning a manifest dict.

    Manifest shape (per spec §6 ``session_artifacts.manifest``)::

        {
            "<filename>": {
                "sha256": "<hex digest>",
                "path":   "photos/<filename>",
                "size_bytes": <int>,
            },
            ...
        }

    Raises ``FileExistsError`` if two source photos share a basename — duplicate
    filenames would silently clobber otherwise.
    """
    session_photos_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, object]] = {}
    for src in photos:
        dst = session_photos_dir / src.name
        if dst.exists():
            raise FileExistsError(f"photo destination already exists: {dst} (duplicate filename?)")
        shutil.copy2(src, dst)
        manifest[src.name] = {
            "sha256": _compute_sha256(dst),
            "path": f"photos/{src.name}",
            "size_bytes": dst.stat().st_size,
        }
    return manifest


def _write_manifest(manifest: dict[str, dict[str, object]], session_dir: Path) -> None:
    """Atomic write of ``manifest.json`` (matches PR #17/#20 tmp+replace precedent)."""
    out_path = session_dir / "manifest.json"
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)


class _ProxyServer:
    """Forwards ``shutdown()`` to the real Server once ``bind()`` is called.

    The Flask app needs a server reference (for ``/api/finalize`` shutdown), but
    ``start_server`` needs the Flask app first — a chicken-and-egg dependency.
    The proxy is created up front, the app is built around it, the real server
    starts, and we then point the proxy at the real server.

    If ``shutdown()`` is called BEFORE ``bind()`` — the race window between
    ``start_server`` returning and ``proxy.bind(server)`` running, during which
    a fast loopback ``POST /api/finalize`` could fire ``_trigger_async_shutdown``
    — the call is recorded as pending and flushed when ``bind()`` runs. The
    lock-protected check-and-set ensures that if ``bind()`` and ``shutdown()``
    race, exactly one of two things happens: either ``bind()`` sees
    ``_pending_shutdown=True`` and triggers the shutdown itself, OR
    ``shutdown()`` sees ``_real != None`` and calls through directly.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._real: object | None = None
        self._pending_shutdown = False

    def bind(self, real: object) -> None:
        with self._lock:
            self._real = real
            should_shutdown = self._pending_shutdown
        if should_shutdown:
            real.shutdown()  # type: ignore[attr-defined]

    def shutdown(self) -> None:
        with self._lock:
            real = self._real
            if real is None:
                self._pending_shutdown = True
                return
        real.shutdown()  # type: ignore[attr-defined]


def _annotate(args: argparse.Namespace) -> int:
    """Implement the ``annotate`` subcommand. Returns the exit code."""
    # Surface part_id validation errors as a clean exit-2 message rather than
    # a Python traceback (matches argparse's convention for usage errors).
    try:
        _validate_part_id(args.part_id)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Spec §8 line 620: explicit warning when binding to a non-loopback host.
    if args.host not in ("127.0.0.1", "localhost"):
        print(
            f"WARNING: binding to {args.host} exposes the wizard server to other "
            "machines on this network. Ensure the network is trusted; the wizard "
            "has no authentication.",
            file=sys.stderr,
            flush=True,
        )

    base_dir = args.out.expanduser().resolve() if args.out else default_base_dir()
    session = create_session(part_id=args.part_id, base_dir=base_dir)
    try:
        manifest = _copy_photos_and_build_manifest(args.photos, session.session_dir / "photos")
        _write_manifest(manifest, session.session_dir)

        proxy = _ProxyServer()
        app = create_app(server=proxy, session=session)
        server = start_server(wsgi_app=app, session=session, host=args.host, port=args.port)
        proxy.bind(server)
    except BaseException:
        # Setup failed after the session dir was created; clean up the
        # half-built directory so a re-run starts fresh. BaseException
        # (vs Exception) ensures cleanup runs on KeyboardInterrupt /
        # SystemExit too. Re-raise so the operator sees the underlying error.
        shutil.rmtree(session.session_dir, ignore_errors=True)
        raise

    print(f"Open {server.url} to annotate part {args.part_id}.", flush=True)

    # Block until the WSGI thread exits — driven by /api/finalize or idle timeout.
    server.serve_thread.join()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "annotate":
        return _annotate(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
