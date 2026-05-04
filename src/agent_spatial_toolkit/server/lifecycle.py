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

    Threading model:
    - serve_thread runs the WSGIServer in a daemon thread.
    - _idle_timer (daemon) polls last_activity vs the idle timeout.
    - _last_activity is a single-element list-box updated atomically by the
      activity middleware; reads under the GIL are torn-free for scalar
      writes/reads.
    - shutdown() is concurrency-safe via _shutdown_lock + _shutdown_done event.
      Concurrent callers all wait until shutdown completes.
    """

    session: Session
    httpd: WSGIServer
    serve_thread: threading.Thread
    _idle_timer: threading.Thread
    _last_activity: list[float]
    _shutdown_started: list[bool]
    _shutdown_lock: threading.Lock
    _shutdown_done: threading.Event
    idle_timeout_seconds: float
    time_source: Callable[[], float]

    @property
    def port(self) -> int:
        """The port the HTTP server is actually bound to."""
        return self.httpd.server_address[1]

    @property
    def url(self) -> str:
        """The base URL clients should connect to."""
        return f"http://localhost:{self.port}"

    @property
    def is_shutdown(self) -> bool:
        """True after shutdown() has fully completed."""
        return self._shutdown_done.is_set()

    @property
    def last_activity(self) -> float:
        """Timestamp (per time_source) of most recent activity."""
        return self._last_activity[0]

    def mark_activity(self) -> None:
        """Reset the idle-timeout clock.

        Called publicly so future code (e.g. background workers) can defer the
        idle shutdown if needed. Routes through ``time_source`` for test
        reproducibility — the activity middleware updates the box directly via
        the captured ``time_source`` instead, because ``server`` is not yet
        bound when the closure is created.
        """
        self._last_activity[0] = self.time_source()

    def shutdown(self) -> None:
        """Stop the server. Idempotent: concurrent callers all wait for completion."""
        do_shutdown = False
        with self._shutdown_lock:
            if self._shutdown_done.is_set():
                return  # already done
            if not self._shutdown_started[0]:
                self._shutdown_started[0] = True
                do_shutdown = True
        if do_shutdown:
            try:
                # httpd.shutdown() blocks until serve_forever() returns, so the
                # serve thread should be done by the time the join is reached.
                # The 5 s timeout is a safety net; an unbounded join would hang
                # tests on bugs.
                self.httpd.shutdown()
                self.httpd.server_close()
                self.serve_thread.join(timeout=5.0)
            finally:
                self._shutdown_done.set()
            # The idle-watcher thread polls _shutdown_started[0] and exits on
            # its own; we deliberately do NOT join it here to avoid waiting up
            # to one poll-interval on every shutdown.
        else:
            # Another thread is performing shutdown; wait for it to complete so
            # every caller observes a fully-stopped server before returning.
            self._shutdown_done.wait(timeout=10.0)


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
    shutdown_started: list[bool] = [False]
    shutdown_lock = threading.Lock()
    shutdown_done = threading.Event()

    def activity_middleware(environ, start_response):
        # Update directly via the captured time_source (server isn't bound yet
        # when this closure is created). External callers should prefer
        # server.mark_activity() which routes through Server.time_source.
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
        while not shutdown_started[0]:
            elapsed = time_source() - last_activity[0]
            # Recheck under-the-wire: a request may have arrived between the
            # first read above and now. The `and` short-circuits, re-reading
            # time_source() / last_activity[0] only when the cheap initial
            # check trips. Not perfectly atomic — the activity update could
            # STILL land between the recheck and the shutdown call — but
            # reduces the race window from ~1s to microseconds.
            if (
                elapsed > idle_timeout_seconds
                and time_source() - last_activity[0] > idle_timeout_seconds
            ):
                if not shutdown_started[0]:
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
        _shutdown_started=shutdown_started,
        _shutdown_lock=shutdown_lock,
        _shutdown_done=shutdown_done,
        idle_timeout_seconds=idle_timeout_seconds,
        time_source=time_source,
    )
    idle_timer.start()
    return server
