"""P0.2 — the 60 thumbnail GETs behind one gallery page must reuse the socket.

`docs/review_gallery_performance.md` §2 found `protocol_version` unset, so
Python answered HTTP/1.0 and a first page of tiles cost 60 TCP handshakes.

The doc calls the fix a one-liner on the grounds that "`_serve_local_file` and
`_send_json` already send `Content-Length`". `_send_json` did not, and neither
did `do_OPTIONS`, the `do_HEAD` fallback or the 416 branch. Under HTTP/1.0 that
was invisible — the close *was* the end of the body. Under keep-alive an
unframed response is a client blocked on an EOF that never arrives.

So these tests are about framing and connection bookkeeping, not speed:

  * every response says how long it is,
  * a connection the server intends to close says `Connection: close`, exactly
    once,
  * and a request that carried a body never leaves its unread remainder to be
    parsed as the next request line.
"""

from __future__ import annotations

import http.client
import json
import urllib.parse

import pytest

from promptstudio.server.handler import GalleryRequestHandler


@pytest.fixture
def conn(_api_server):
    parsed = urllib.parse.urlparse(_api_server)
    client = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
    try:
        yield client
    finally:
        client.close()


def _get(conn, path, method="GET", body=None, headers=None):
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    payload = resp.read()
    return resp, payload


def test_the_server_speaks_http_1_1():
    assert GalleryRequestHandler.protocol_version == "HTTP/1.1"


def test_idle_keep_alive_connections_are_reaped():
    """Keep-alive holds a thread per connection, so idle ones need a deadline."""
    assert GalleryRequestHandler.timeout, "a keep-alive server needs a timeout"


# ── framing ──────────────────────────────────────────────────────────

def test_json_responses_declare_their_length(conn):
    resp, body = _get(conn, "/api/stats")
    assert resp.status == 200
    assert resp.getheader("Content-Length") == str(len(body))
    assert json.loads(body.decode("utf-8"))["total_photos"] >= 0


def test_options_preflight_declares_an_empty_body(conn):
    resp, body = _get(conn, "/api/photos", method="OPTIONS")
    assert resp.status == 200
    assert resp.getheader("Content-Length") == "0"
    assert body == b""
    assert not resp.will_close


def test_error_responses_declare_their_length(conn):
    resp, body = _get(conn, "/api/media/detail")  # 400, path required
    assert resp.status == 400
    assert resp.getheader("Content-Length") == str(len(body))


def test_a_thumb_response_declares_its_length(conn, make_photo):
    rel, _ = make_photo(name="framed.jpg")
    resp, body = _get(conn, f"/media/thumb/{urllib.parse.quote(rel)}")
    assert resp.status == 200
    assert resp.getheader("Content-Length") == str(len(body))


# ── connection reuse, which is the whole point ───────────────────────

def test_two_json_requests_share_one_socket(conn):
    resp, _ = _get(conn, "/api/stats")
    assert not resp.will_close
    first = conn.sock
    resp2, _ = _get(conn, "/api/stats")
    assert resp2.status == 200
    # http.client silently reconnects when the server hung up, so a *new*
    # socket object here is exactly the HTTP/1.0 behaviour this replaced.
    assert conn.sock is first


def test_a_page_worth_of_thumbs_shares_one_socket(conn, make_photo):
    rels = [make_photo(name=f"tile_{i:02d}.jpg")[0] for i in range(12)]
    _get(conn, "/api/stats")
    first = conn.sock
    for rel in rels:
        resp, body = _get(conn, f"/media/thumb/{urllib.parse.quote(rel)}")
        assert resp.status == 200, rel
        assert body[:2] == b"\xff\xd8", f"{rel} is not a JPEG"
        assert conn.sock is first, f"connection dropped at {rel}"


# ── bodies must not bleed into the next request ──────────────────────

def test_a_rejected_request_with_a_body_closes_instead_of_desyncing(conn):
    """The route 404s without reading the body. Under keep-alive those bytes
    would become the next request line."""
    junk = json.dumps({"payload": "x" * 600}).encode("utf-8")
    resp, _ = _get(
        conn,
        "/api/no-such-route",
        method="POST",
        body=junk,
        headers={"Content-Type": "application/json", "Content-Length": str(len(junk))},
    )
    assert resp.status == 404
    assert resp.getheader("Connection", "").lower() == "close"
    assert resp.will_close

    # And the next request still gets a real answer rather than a 400 from a
    # desynchronised parse.
    resp2, body2 = _get(conn, "/api/stats")
    assert resp2.status == 200
    assert json.loads(body2.decode("utf-8"))["total_photos"] >= 0


def test_connection_close_is_sent_once(conn):
    """`send_error` sends its own, so end_headers must not add a second."""
    junk = b"{}"
    resp, _ = _get(
        conn,
        "/api/no-such-route",
        method="POST",
        body=junk,
        headers={"Content-Type": "application/json", "Content-Length": str(len(junk))},
    )
    # http.client joins repeated headers with ", ".
    assert resp.getheader("Connection", "").lower() == "close"


def test_a_consumed_body_still_answers_and_closes_cleanly(conn, make_photo):
    rel, _ = make_photo(name="fav.jpg")
    payload = json.dumps({"path": rel, "favorite": True}).encode("utf-8")
    resp, body = _get(
        conn,
        "/api/favorite",
        method="PUT",
        body=payload,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        },
    )
    assert resp.status == 200, body
    assert resp.getheader("Content-Length") == str(len(body))
    resp2, _ = _get(conn, "/api/stats")
    assert resp2.status == 200
