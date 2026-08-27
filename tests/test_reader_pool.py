"""P1 — gallery reads must not queue behind a write.

`docs/review_gallery_performance.md` §6: the whole index shared one SQLite
connection behind a process-wide `RLock`, so WAL bought nothing. WAL only lets a
reader run during a write when the reader is a *different* connection. The
symptoms were in the same probe that produced the query numbers —
`list_creators` at a 122 ms median against a 1,601 ms max, an offset page
ranging 390–1,931 ms — contention, not SQL.

What is pinned here: reads go to read-only handles, those handles cannot write,
a read completes while a write transaction is open, nesting does not deadlock,
and turning the pool off still works.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from promptstudio.storage.db import ArchiveIndex


@pytest.fixture
def index():
    return ArchiveIndex.get()


# ── the reads actually moved off the writer ──────────────────────────

def test_a_gallery_page_runs_on_a_reader(index, make_photo):
    make_photo(name="a.jpg")
    seen: list[str] = []
    index._conn.set_trace_callback(seen.append)
    try:
        index.query_photos(sort="newest", limit=60)
    finally:
        index._conn.set_trace_callback(None)
    pages = [s for s in seen if "FROM photos p" in s]
    assert not pages, f"gallery SQL still ran on the writer connection: {pages}"


def test_the_index_level_tracer_still_sees_it(index, make_photo):
    """Which is why `set_trace_callback` exists — see the test above."""
    make_photo(name="a.jpg")
    seen: list[str] = []
    index.set_trace_callback(seen.append)
    try:
        index.query_photos(sort="newest", limit=60)
    finally:
        index.set_trace_callback(None)
    assert [s for s in seen if "FROM photos p" in s]


@pytest.mark.parametrize(
    "call",
    [
        lambda i: i.query_photos(sort="newest", limit=60),
        lambda i: i.query_photos(sort="newest", limit=60, group_posts=True),
        lambda i: i.query_photos(sort="newest", paths_only=True),
        lambda i: i.list_creators(),
        lambda i: i.stats(),
        lambda i: i.tier_histogram(),
        lambda i: i.label_counts(),
        lambda i: i.unclassified_total(),
        lambda i: i.verdict_facet_counts(),
    ],
    ids=[
        "page",
        "grouped page",
        "paths_only",
        "list_creators",
        "stats",
        "tier_histogram",
        "label_counts",
        "unclassified_total",
        "verdict_facets",
    ],
)
def test_read_paths_do_not_touch_the_writer(index, make_photo, call):
    rel, _ = make_photo(name="a.jpg")
    index.set_verdict(rel, tier=3)
    seen: list[str] = []
    index._conn.set_trace_callback(seen.append)
    try:
        call(index)
    finally:
        index._conn.set_trace_callback(None)
    selects = [s for s in seen if s.lstrip().upper().startswith("SELECT")]
    assert not selects, selects


def test_photos_for_rel_paths_runs_on_a_reader(index, make_photo):
    rel, _ = make_photo(name="a.jpg")
    seen: list[str] = []
    index._conn.set_trace_callback(seen.append)
    try:
        got = index.photos_for_rel_paths([rel])
    finally:
        index._conn.set_trace_callback(None)
    assert rel in got
    assert not [s for s in seen if "FROM photos p" in s], seen


# ── a reader is a reader ─────────────────────────────────────────────

def test_a_reader_refuses_to_write(index, make_photo):
    make_photo(name="a.jpg")
    with index._read() as conn:
        if conn is index._conn:
            pytest.skip("pool unavailable on this platform; writer fallback in use")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM photos")


def test_a_refused_write_does_not_freeze_the_handle(index, make_photo):
    """The nastiest failure this pool can have, and it is silent.

    Python's sqlite3 puts an implicit `BEGIN` in front of a DML statement. On a
    read-only handle the DML is then refused — but the transaction stays open
    (`in_transaction` is True afterwards). The next SELECT pins a WAL snapshot
    for the life of that transaction, the handle goes back into the pool, and
    every later read that draws it answers from a frozen archive. Four readers
    means about a quarter of gallery requests, with nothing logged.
    """
    rel, _ = make_photo(name="poison.jpg")
    with index._read() as conn:
        if conn is index._conn:
            pytest.skip("pool unavailable on this platform; writer fallback in use")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM photos")
        # Pin a snapshot inside the transaction the refusal left open.
        conn.execute("SELECT COUNT(*) FROM photos").fetchall()

    # The handle is back in the pool. A row added now has to be visible through
    # it, which means whatever was open got closed on the way back.
    make_photo(name="after_poison.jpg")
    for _ in range(6):  # enough draws to hit the recycled handle
        _photos, total = index.query_photos(sort="newest", limit=60)
        assert total == 2, "a reader returned a frozen snapshot"


def test_a_returned_reader_is_never_left_mid_transaction(index, make_photo):
    make_photo(name="a.jpg")
    with index._read() as conn:
        if conn is index._conn:
            pytest.skip("pool unavailable on this platform; writer fallback in use")
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE photos SET favorite = 1")
        assert conn.in_transaction, "premise changed: the refusal left no transaction"
    assert not conn.in_transaction, "handle went back to the pool mid-transaction"


def test_reads_do_not_wait_on_an_open_write_transaction(index, make_photo):
    """The behaviour the whole pool exists for: an overnight classify holds the
    writer, and the gallery still paints."""
    for n in range(5):
        make_photo(name=f"held_{n}.jpg")
    with index._read() as conn:
        if conn is index._conn:
            pytest.skip("pool unavailable on this platform; writer fallback in use")

    # Hold the writer open, from another thread, exactly as a job batch does.
    holding = threading.Event()
    release = threading.Event()

    def writer():
        with index._lock:
            index._conn.execute("BEGIN IMMEDIATE")
            index._conn.execute(
                "UPDATE photos SET favorite = 1 WHERE rel_path LIKE 'held%'"
            )
            holding.set()
            release.wait(10)
            index._conn.rollback()

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    assert holding.wait(10), "writer never opened its transaction"
    try:
        start = time.perf_counter()
        photos, total = index.query_photos(sort="newest", limit=60)
        elapsed = time.perf_counter() - start
        assert total >= 5
        # busy_timeout is 5 s; the old shared-connection path would have queued
        # on the RLock for the whole life of the transaction.
        assert elapsed < 2.0, f"read waited {elapsed:.2f}s on an open write"
    finally:
        release.set()
        t.join(timeout=10)


def test_uncommitted_writes_are_not_visible_to_readers(index, make_photo):
    rel, _ = make_photo(name="iso.jpg")
    with index._read() as conn:
        if conn is index._conn:
            pytest.skip("pool unavailable on this platform; writer fallback in use")

    holding = threading.Event()
    release = threading.Event()

    def writer():
        with index._lock:
            index._conn.execute("BEGIN IMMEDIATE")
            index._conn.execute(
                "UPDATE photos SET favorite = 1 WHERE rel_path = ?", (rel,)
            )
            holding.set()
            release.wait(10)
            index._conn.rollback()

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    assert holding.wait(10)
    try:
        photos, _ = index.query_photos(path=rel)
        assert photos[0]["favorite"] is False, "reader saw an uncommitted write"
    finally:
        release.set()
        t.join(timeout=10)


def test_a_committed_write_is_visible_immediately(index, make_photo):
    rel, _ = make_photo(name="fresh.jpg")
    before, _ = index.query_photos(path=rel)
    assert before[0]["favorite"] is False
    index.upsert_photo(rel, favorite=1)
    after, _ = index.query_photos(path=rel)
    assert after[0]["favorite"] is True, "reader kept a stale snapshot"


# ── nesting and capacity ─────────────────────────────────────────────

def test_a_read_nested_in_a_read_reuses_the_same_handle(index, make_photo):
    """`query_photos`'s semantic branch calls `all_embeddings` and
    `photos_for_rel_paths`; `list_creators` calls two more. Without reentrancy a
    pool of N deadlocks at N nested reads."""
    make_photo(name="a.jpg")
    with index._read() as outer:
        with index._read() as inner:
            assert inner is outer


def test_more_concurrent_readers_than_the_pool_still_all_finish(index, make_photo):
    for n in range(4):
        make_photo(name=f"c_{n}.jpg")
    errors: list[BaseException] = []
    totals: list[int] = []
    lock = threading.Lock()

    def read():
        try:
            for _ in range(6):
                _photos, total = index.query_photos(sort="newest", limit=60)
                with lock:
                    totals.append(total)
        except Exception as exc:  # recorded, then asserted on in the parent
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=read, daemon=True) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "a reader thread never finished (pool exhausted?)"
    assert not errors, errors
    assert len(totals) == 72
    assert set(totals) == {4}


def test_pool_is_returned_not_leaked(index, make_photo):
    make_photo(name="a.jpg")
    for _ in range(30):
        index.query_photos(sort="newest", limit=60)
    # Never more handles than the configured cap, however many queries ran.
    from promptstudio.config import DB_READERS

    assert index._readers_made <= max(DB_READERS, 0)


# ── the escape hatch ─────────────────────────────────────────────────

def test_disabling_the_pool_falls_back_to_the_writer(tmp_path, monkeypatch):
    """`PROMPTSTUDIO_DB_READERS=0` is the pre-P1 behaviour, and it must still
    produce the same answers rather than an error."""
    import promptstudio.storage.db as db_mod

    monkeypatch.setattr(db_mod, "DB_READERS", 0)
    idx = db_mod.ArchiveIndex(
        db_path=str(tmp_path / "a.db"), base_dir=str(tmp_path / "m")
    )
    try:
        with idx._read() as conn:
            assert conn is idx._conn
        photos, total = idx.query_photos(sort="newest", limit=60)
        assert (photos, total) == ([], 0)
        assert idx.stats()["total_photos"] == 0
    finally:
        idx.close()


def test_close_releases_the_readers(tmp_path):
    import promptstudio.storage.db as db_mod

    idx = db_mod.ArchiveIndex(
        db_path=str(tmp_path / "b.db"), base_dir=str(tmp_path / "m")
    )
    idx.query_photos(sort="newest", limit=60)
    parked = idx._readers.qsize()
    idx.close()
    assert idx._readers.qsize() == 0, f"{parked} readers left open"
