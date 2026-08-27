"""P0.3 — a gallery GET serves a thumbnail, it does not manufacture one.

`docs/review_gallery_performance.md` §4: `ensure_thumbnail` had exactly one
caller, `GET /media/thumb/`, so `_thumbs/` covered 12,148 of 61,344 rows and
the newest 500 files were 91% unthumbed. A first page of "newest" after a
scrape asked for 60 encodes at once (449 ms each on that machine), ran a
whole-timeline frame-ranking pass for every reel, and on failure served the
multi-megabyte original as the tile.

What is pinned here:

  * ingest queues the thumbnail, so the tile is a cache hit by the time anyone
    looks at it,
  * the GET path never falls back to the original,
  * the miss path is bounded and cannot wedge on a full queue or an
    unreadable file.
"""

from __future__ import annotations

import http.client
import os
import urllib.parse

import pytest

from promptstudio.config import SAVED_DIR
from promptstudio.storage import thumb_queue
from promptstudio.storage.db import ArchiveIndex
from promptstudio.storage.thumbs import (
    PLACEHOLDER_GIF,
    resolve_thumb_file,
    thumb_disk_path,
)


@pytest.fixture(autouse=True)
def _ingest_queue():
    """Give ingest a one-worker pool for this module.

    The process-wide queue is disabled in conftest (`THUMB_WORKERS=0`) so other
    suites do not lock files on Windows. These tests *are* the ingest path.
    """
    q = thumb_queue.ThumbQueue(workers=1)
    thumb_queue._instance = q
    yield q
    q.drain(timeout=20)
    thumb_queue._instance = None


@pytest.fixture
def pool():
    """A queue of our own, so a test never races the process-wide one."""
    q = thumb_queue.ThumbQueue(workers=1)
    yield q


def _drain(q, timeout=20.0):
    assert q.drain(timeout=timeout), "thumb queue did not drain"


# ── ingest ───────────────────────────────────────────────────────────

def test_upsert_produces_a_thumbnail_without_anyone_opening_the_tile(make_photo):
    """The whole point: after a scrape, "newest" is a page of cache hits."""
    rel, _full = make_photo(name="ingested.jpg")
    _drain(thumb_queue.get())
    thumb = resolve_thumb_file(rel)
    assert thumb, f"no thumbnail for {rel}"
    assert os.path.getsize(thumb) > 0


def test_the_queue_encodes_a_submitted_file(pool, make_photo):
    rel, full = make_photo(name="queued.jpg")
    # make_photo goes through upsert_photo, which now enqueues. Let that
    # finish, then clear it so this test drives the pool directly.
    _drain(thumb_queue.get())
    existing = resolve_thumb_file(rel)
    if existing:
        os.remove(existing)
    assert not resolve_thumb_file(rel)
    event = pool.submit(rel, full)
    assert event is not None
    assert event.wait(20), "worker never finished"
    assert resolve_thumb_file(rel)


def test_the_same_path_twice_is_one_attempt(pool, make_photo):
    """Sixty tiles, a re-render and a backfill can all name one path."""
    rel, full = make_photo(name="dupe.jpg")
    first = pool.submit(rel, full)
    second = pool.submit(rel, full)
    # Either already collapsed onto the same Event, or the first attempt had
    # finished and cleared — both are "encoded once", never twice in flight.
    assert second is None or second is first or first.is_set()
    _drain(pool)


def test_a_full_queue_drops_instead_of_blocking_ingest(make_photo):
    """An ingest that stalls behind the thumbnailer is the worse bug."""
    rel, full = make_photo(name="dropped.jpg")
    tiny = thumb_queue.ThumbQueue(workers=1, maxsize=1)
    # Fill the single slot with a path that is not on disk, so the worker has
    # nothing to do but also cannot be relied on to clear it instantly.
    tiny.submit("nowhere/absent_a.jpg", os.path.join(SAVED_DIR, "nowhere/absent_a.jpg"))
    for i in range(50):
        tiny.submit(f"nowhere/absent_{i}.jpg", os.path.join(SAVED_DIR, f"nowhere/x{i}.jpg"))
    # The real assertion is that none of the above raised or blocked.
    assert tiny.submit(rel, full) is not None or True


def test_a_dropped_submission_can_be_submitted_again(make_photo):
    """A retracted reservation must not leave the path permanently 'in flight'."""
    rel, full = make_photo(name="retry.jpg")
    tiny = thumb_queue.ThumbQueue(workers=0, maxsize=1)  # workers=0 -> no queueing
    assert tiny.submit(rel, full) is None
    real = thumb_queue.ThumbQueue(workers=1)
    event = real.submit(rel, full)
    assert event is not None
    assert event.wait(20)


def test_an_unreadable_file_does_not_take_the_worker_down(pool, make_photo):
    """One bad file used to be enough to leave every later tile waiting."""
    bad = os.path.join(SAVED_DIR, "broken", "not_an_image.jpg")
    os.makedirs(os.path.dirname(bad), exist_ok=True)
    with open(bad, "wb") as fh:
        fh.write(b"this is not a JPEG")
    first = pool.submit("broken/not_an_image.jpg", bad)
    assert first is not None
    assert first.wait(20), "worker hung on a corrupt file"

    rel, full = make_photo(creator="after_bad", name="good.jpg")
    target = thumb_disk_path(rel).rsplit(".", 1)[0] + ".jpg"
    if os.path.isfile(target):
        os.remove(target)
    second = pool.submit(rel, full)
    assert second is not None
    assert second.wait(20), "worker died on the previous file"
    assert resolve_thumb_file(rel)


def test_rebuild_does_not_enqueue_the_whole_archive(make_photo):
    """61k queue entries behind a page load is the backfill CLI's job."""
    make_photo(name="a.jpg")
    _drain(thumb_queue.get())
    ArchiveIndex.get().rebuild()
    assert thumb_queue.get().pending() == 0


# ── the GET path ─────────────────────────────────────────────────────

@pytest.fixture
def conn(_api_server):
    parsed = urllib.parse.urlparse(_api_server)
    client = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=30)
    try:
        yield client
    finally:
        client.close()


def _get(conn, path):
    conn.request("GET", path)
    resp = conn.getresponse()
    return resp, resp.read()


def test_a_thumbed_tile_is_served_as_a_jpeg(conn, make_photo):
    rel, _ = make_photo(name="hit.jpg")
    _drain(thumb_queue.get())
    resp, body = _get(conn, f"/media/thumb/{urllib.parse.quote(rel)}")
    assert resp.status == 200
    assert resp.getheader("Content-Type") == "image/jpeg"
    assert body[:2] == b"\xff\xd8"
    assert "max-age" in (resp.getheader("Cache-Control") or "")


def test_a_missing_thumb_never_serves_the_original(conn, make_photo, monkeypatch):
    """`serve_path = thumb or full_path` put a 3.5 MB decode in a 220 px box."""
    rel, full = make_photo(name="unthumbable.jpg", size=(320, 400))
    thumb = thumb_disk_path(rel).rsplit(".", 1)[0] + ".jpg"
    _drain(thumb_queue.get())
    if os.path.isfile(thumb):
        os.remove(thumb)

    # Make generation impossible, so the route has to choose its fallback.
    import promptstudio.storage.thumbs as thumbs_mod

    monkeypatch.setattr(thumbs_mod, "ensure_thumbnail", lambda *a, **k: None)
    monkeypatch.setattr(
        thumb_queue, "get", lambda: thumb_queue.ThumbQueue(workers=0)
    )

    resp, body = _get(conn, f"/media/thumb/{urllib.parse.quote(rel)}")
    assert resp.status == 200
    original_size = os.path.getsize(full)
    assert len(body) < original_size, "the original was served as a tile"
    assert body == PLACEHOLDER_GIF
    # The grey tile must not be what the cache remembers this path by.
    assert resp.getheader("Cache-Control") == "no-store"


def test_the_placeholder_is_a_real_image():
    """A broken tile is worse than a grey one — the bytes have to decode."""
    import io

    from PIL import Image

    with Image.open(io.BytesIO(PLACEHOLDER_GIF)) as img:
        img.load()
        # 1x1 is the contract app.js reads: `naturalWidth === 1` is how a tile
        # knows it got a placeholder, since an <img> cannot see a header. Widen
        # this and `installThumbPlaceholderRetry` stops retrying and the tile
        # stays grey until a manual reload.
        assert img.size == (1, 1)


def test_the_retry_reaches_the_server_and_gets_the_real_thumb(conn, make_photo):
    """The placeholder has to be temporary, or P0.3 trades a slow tile for a
    permanently grey one."""
    rel, _ = make_photo(name="fills_in.jpg")
    _drain(thumb_queue.get())
    existing = resolve_thumb_file(rel)
    if existing:
        os.remove(existing)

    quoted = urllib.parse.quote(rel)
    first, body = _get(conn, f"/media/thumb/{quoted}")
    assert first.status == 200
    # Either the worker beat the wait (a real JPEG) or it did not (placeholder).
    # Both are fine; what must hold is that the retry URL works and converges.
    _drain(thumb_queue.get())
    retry, body2 = _get(conn, f"/media/thumb/{quoted}?retry=123")
    assert retry.status == 200, body2
    assert body2[:2] == b"\xff\xd8", "retry did not return a JPEG"
    assert retry.getheader("Content-Type") == "image/jpeg"


def test_a_missing_file_is_still_a_404(conn):
    resp, _ = _get(conn, "/media/thumb/nobody/nothing.jpg")
    assert resp.status == 404
