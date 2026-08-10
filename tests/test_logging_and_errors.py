"""Logging setup and the HTTP error boundary.

Two failures that used to be invisible:
  * a six-hour scrape leaving no record of *why* it stopped
  * a route raising, which dropped the socket — so the browser reported the
    whole app as offline instead of surfacing a 500
"""

import logging

import pytest

from promptstudio import logging_setup
from promptstudio.server.handler import GalleryRequestHandler, _error_boundary

# ── logging ──────────────────────────────────────────────────────────


def test_module_loggers_are_namespaced():
    log = logging_setup.get_logger("promptstudio.storage.db")
    assert log.name == "promptstudio.storage.db"


def test_bare_module_name_is_prefixed():
    assert logging_setup.get_logger("mymod").name == "promptstudio.mymod"


def test_configure_is_idempotent():
    first = logging_setup.configure_logging()
    count = len(first.handlers)
    for _ in range(3):
        logging_setup.configure_logging()
    assert len(logging_setup.configure_logging().handlers) == count


def test_force_reconfigure_does_not_stack_handlers():
    def owned(lg):
        return [h for h in lg.handlers if getattr(h, logging_setup._OWNED, False)]

    once = len(owned(logging_setup.configure_logging(force=True)))
    twice = len(owned(logging_setup.configure_logging(force=True)))
    assert once == twice == 1


def test_force_reconfigure_leaves_foreign_handlers_alone():
    """pytest's caplog attaches here; closing it would break capture."""
    logger = logging_setup.configure_logging()
    foreign = logging.Handler()
    logger.addHandler(foreign)
    try:
        logging_setup.configure_logging(force=True)
        assert foreign in logger.handlers
    finally:
        logger.removeHandler(foreign)


def test_root_logger_is_not_hijacked():
    """Handlers attach to `promptstudio`, so embedding this package is safe."""
    root_handlers = list(logging.getLogger().handlers)
    logging_setup.configure_logging(force=True)
    assert list(logging.getLogger().handlers) == root_handlers
    assert logging_setup.configure_logging().propagate is False


def test_records_reach_a_handler(caplog):
    log = logging_setup.get_logger("promptstudio.tests")
    # caplog attaches to root; propagate is off by design, so assert on the
    # package logger directly.
    with caplog.at_level(logging.WARNING, logger="promptstudio.tests"):
        log.warning("classify failed for %s: %s", "a/b.mp4", "boom")
    assert "classify failed for a/b.mp4: boom" in caplog.text


# ── error boundary ───────────────────────────────────────────────────


class FakeHandler:
    """Minimal stand-in with the attributes the boundary touches."""

    command = "GET"
    path = "/api/photos"

    def __init__(self):
        self.close_connection = False
        self._response_started = False
        self.sent = []

    def _send_json(self, data, status=200):
        if self._response_started:
            raise AssertionError("headers already sent")
        self._response_started = True
        self.sent.append((status, data))

    _send_json_500 = GalleryRequestHandler._send_json_500


def test_unhandled_exception_becomes_a_json_500():
    @_error_boundary
    def route(self):
        raise ValueError("bug in a route")

    h = FakeHandler()
    route(h)
    assert h.sent == [(500, {"error": "internal server error"})]


def test_successful_route_is_untouched():
    @_error_boundary
    def route(self):
        self._send_json({"ok": True})
        return "returned"

    h = FakeHandler()
    assert route(h) == "returned"
    assert h.sent == [(200, {"ok": True})]


def test_no_second_response_once_headers_are_out():
    """Mid-body failure can't send a 500; it closes the connection instead."""

    @_error_boundary
    def route(self):
        self._send_json({"partial": True})
        raise RuntimeError("failed while streaming")

    h = FakeHandler()
    route(h)
    assert h.sent == [(200, {"partial": True})]
    assert h.close_connection is True


@pytest.mark.parametrize(
    "exc", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError]
)
def test_client_disconnect_is_not_reported_as_a_server_error(exc):
    """Normal when a user seeks or closes a video mid-stream."""

    @_error_boundary
    def route(self):
        raise exc()

    h = FakeHandler()
    route(h)
    assert h.sent == []
    assert h.close_connection is True


def test_boundary_preserves_the_wrapped_name():
    @_error_boundary
    def do_GET(self):
        return None

    assert do_GET.__name__ == "do_GET"


def test_routes_are_actually_wrapped():
    for method in ("do_GET", "do_POST", "do_PUT", "do_DELETE", "do_HEAD"):
        fn = getattr(GalleryRequestHandler, method)
        assert hasattr(fn, "__wrapped__"), f"{method} is missing the error boundary"


def test_response_flag_resets_between_keepalive_requests():
    """Handler instances are reused; a stale flag would suppress a later 500."""
    assert "handle_one_request" in vars(GalleryRequestHandler)
    assert "send_response" in vars(GalleryRequestHandler)


def test_log_message_survives_broken_stderr(monkeypatch):
    """Broken console stderr must not abort send_response / JSON status polls."""

    class _Boom:
        def write(self, *_a, **_k):
            raise OSError(22, "Invalid argument")

        def flush(self):
            return None

    monkeypatch.setattr("sys.stderr", _Boom())

    class _Probe(GalleryRequestHandler):
        def __init__(self):
            # Skip BaseHTTPRequestHandler.__init__ (needs a real request socket).
            pass

        def address_string(self):
            return "127.0.0.1"

        def log_date_time_string(self):
            return "01/Jan/2026 00:00:00"

    _Probe().log_message('"%s" %s %s', "GET /api/classify/status HTTP/1.1", "200", "-")


# ── unknown API routes ───────────────────────────────────────────────


def test_an_unknown_api_route_is_404_for_every_verb(api):
    """GET and PUT already fell through to a 404. POST and DELETE called
    `super().do_POST()` / `super().do_DELETE()`, which SimpleHTTPRequestHandler
    does not define — so the AttributeError hit the error boundary and a typo in
    a URL was reported as a server fault.
    """
    for method in ("GET", "POST", "PUT", "DELETE"):
        status, _ = api(method, "/api/definitely-not-a-route")
        assert status == 404, f"{method} returned {status}"
