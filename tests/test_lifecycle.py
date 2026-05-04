"""Tests for server/lifecycle.py — server lifecycle + auto-shutdown."""

import time
import urllib.request
from pathlib import Path

from agent_spatial_toolkit.server.lifecycle import Server, start_server
from agent_spatial_toolkit.server.session import create_session


def _hello_app(environ, start_response):
    """Trivial WSGI app for testing."""
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"hello"]


def test_start_server_returns_running_server(tmp_path: Path) -> None:
    """start_server returns a Server with a bound port and URL; the server responds."""
    session = create_session(part_id="testpart", base_dir=tmp_path / "sessions")
    server = start_server(wsgi_app=_hello_app, session=session, port=0)
    try:
        assert isinstance(server, Server)
        assert server.port > 0
        assert server.url == f"http://localhost:{server.port}/"
        # Real HTTP request to verify the server is actually serving.
        with urllib.request.urlopen(server.url, timeout=2.0) as r:
            assert r.read() == b"hello"
    finally:
        server.shutdown()


def test_server_shutdown_is_idempotent(tmp_path: Path) -> None:
    """Calling shutdown() twice doesn't error."""
    session = create_session(part_id="testpart", base_dir=tmp_path / "sessions")
    server = start_server(wsgi_app=_hello_app, session=session, port=0)
    server.shutdown()
    server.shutdown()  # second call must be a no-op, not an exception


def test_idle_timer_shuts_down_after_timeout(tmp_path: Path) -> None:
    """With a mocked time_source, the idle watcher fires shutdown when threshold exceeded."""
    session = create_session(part_id="testpart", base_dir=tmp_path / "sessions")

    # Mocked clock: callers control the value. Start at 0.
    fake_now = [0.0]
    server = start_server(
        wsgi_app=_hello_app,
        session=session,
        port=0,
        idle_timeout_seconds=10.0,  # short timeout for the test
        time_source=lambda: fake_now[0],
    )
    try:
        assert not server._shutdown_called[0]
        # Advance fake time past the idle timeout.
        fake_now[0] = 11.0
        # Wait for the idle_watcher's poll loop (1s cadence) to notice.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if server._shutdown_called[0]:
                break
            time.sleep(0.1)
        assert server._shutdown_called[0]
    finally:
        server.shutdown()


def test_request_resets_idle_clock(tmp_path: Path) -> None:
    """A new HTTP request via the WSGI middleware resets last_activity."""
    session = create_session(part_id="testpart", base_dir=tmp_path / "sessions")

    fake_now = [0.0]
    server = start_server(
        wsgi_app=_hello_app,
        session=session,
        port=0,
        idle_timeout_seconds=100.0,  # long enough that the test won't trip it
        time_source=lambda: fake_now[0],
    )
    try:
        initial = server._last_activity[0]
        # Advance fake time to 50 (still well within the 100s timeout).
        fake_now[0] = 50.0
        # Make a request — middleware should call time_source() and update last_activity.
        with urllib.request.urlopen(server.url, timeout=2.0) as r:
            r.read()
        # last_activity should now reflect the post-request fake time.
        assert server._last_activity[0] >= 50.0
        assert server._last_activity[0] > initial
    finally:
        server.shutdown()
