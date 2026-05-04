"""Lifecycle wrapper for the wizard HTTP server (spec §4)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from wsgiref.simple_server import WSGIServer, make_server

from agent_spatial_toolkit.server.session import Session

# Default idle timeout: spec §4 calls out "auto-shutdown after 30 minutes idle".
DEFAULT_IDLE_TIMEOUT_SECONDS: float = 30 * 60

# How often the idle watcher polls the clock. 1 second is fine for a 30-minute
# real-world timeout; tests inject a mocked time_source and tolerate this poll.
_IDLE_POLL_INTERVAL_SECONDS: float = 1.0


@dataclass
class Server:
    """A running wizard HTTP server bound to a session.

    Attributes prefixed with ``_`` are mutable single-element boxes shared
    between the request thread, the idle-watcher thread, and the caller. They
    are intentionally lists (not plain attributes) so that all threads observe
    the same object without needing a lock for these scalar reads/writes —
    Python guarantees atomicity for single-element list assignment under the
    GIL, which is sufficient here.
    """

    session: Session
    httpd: WSGIServer
    serve_thread: threading.Thread
    _idle_timer: threading.Thread
    _last_activity: list[float]
    _shutdown_called: list[bool]
    idle_timeout_seconds: float

    @property
    def port(self) -> int:
        """The port the HTTP server is actually bound to."""
        return self.httpd.server_address[1]

    @property
    def url(self) -> str:
        """The base URL clients should connect to."""
        return f"http://localhost:{self.port}/"

    def mark_activity(self) -> None:
        """Reset the idle-timeout clock.

        Called by the activity-tracking middleware on every request. Exposed
        publicly so future code (e.g. background workers) can also defer the
        idle shutdown if needed.
        """
        self._last_activity[0] = time.monotonic()

    def shutdown(self) -> None:
        """Stop the server and join its threads. Idempotent."""
        if self._shutdown_called[0]:
            return
        self._shutdown_called[0] = True
        # httpd.shutdown() blocks until serve_forever() returns, so the serve
        # thread should be done by the time the join is reached. The 5 s
        # timeout is a safety net; an unbounded join would hang tests on bugs.
        self.httpd.shutdown()
        self.httpd.server_close()
        self.serve_thread.join(timeout=5.0)
        # The idle-watcher thread polls _shutdown_called[0] and exits on its
        # own; we deliberately do NOT join it here to avoid waiting up to one
        # poll-interval on every shutdown.


def start_server(
    wsgi_app: Callable,
    session: Session,
    host: str = "127.0.0.1",
    port: int = 0,
    idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
    time_source: Callable[[], float] = time.monotonic,
) -> Server:
    """Start a WSGI server in a background thread; return a Server handle.

    The given ``wsgi_app`` is wrapped with activity-tracking middleware that
    refreshes the idle clock on every request. A daemon idle-watcher thread
    polls the clock once per second and triggers ``Server.shutdown()`` if no
    request has arrived within ``idle_timeout_seconds``.

    For tests, pass a mocked ``time_source`` (a callable returning the current
    fake time) so the timer can be verified deterministically without sleeping
    for the full timeout.

    Parameters
    ----------
    wsgi_app:
        Any WSGI callable. The lifecycle wrapper is intentionally generic so
        that the Flask app introduced in a later task can plug in unchanged.
    session:
        The Session this server is serving. Stored on the Server handle so
        callers can correlate.
    host, port:
        Bind address. ``port=0`` asks the OS to pick a free port (recommended
        for tests).
    idle_timeout_seconds:
        Auto-shutdown threshold in seconds.
    time_source:
        Clock used by the activity tracker and idle watcher. Defaults to
        ``time.monotonic``; tests inject a controllable callable.
    """
    last_activity: list[float] = [time_source()]
    shutdown_called: list[bool] = [False]

    def activity_middleware(environ, start_response):
        last_activity[0] = time_source()
        return wsgi_app(environ, start_response)

    httpd = make_server(host, port, activity_middleware)

    serve_thread = threading.Thread(
        target=httpd.serve_forever,
        name="agent-spatial-toolkit-wsgi",
        daemon=True,
    )
    serve_thread.start()

    # Forward declaration: the idle watcher closes over `server`, which is
    # bound to the Server instance below before the watcher thread starts.
    # Python closures resolve names at call time, so this is safe as long as
    # the assignment happens before idle_timer.start().
    server: Server

    def _idle_watcher() -> None:
        while not shutdown_called[0]:
            elapsed = time_source() - last_activity[0]
            if elapsed > idle_timeout_seconds:
                if not shutdown_called[0]:
                    server.shutdown()
                return
            time.sleep(_IDLE_POLL_INTERVAL_SECONDS)

    idle_timer = threading.Thread(
        target=_idle_watcher,
        name="agent-spatial-toolkit-idle-watcher",
        daemon=True,
    )

    server = Server(
        session=session,
        httpd=httpd,
        serve_thread=serve_thread,
        _idle_timer=idle_timer,
        _last_activity=last_activity,
        _shutdown_called=shutdown_called,
        idle_timeout_seconds=idle_timeout_seconds,
    )
    idle_timer.start()
    return server
