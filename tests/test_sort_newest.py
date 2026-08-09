"""Newest sort must prefer archive-ingest time over Instagram post mtime."""

from __future__ import annotations

import os
import time

from PIL import Image

from promptstudio.config import SAVED_DIR
from promptstudio.storage.db import ArchiveIndex, file_added_at


def _make_photo(creator: str, name: str, *, mtime: float) -> str:
    folder = os.path.join(SAVED_DIR, creator)
    os.makedirs(folder, exist_ok=True)
    full = os.path.join(folder, name)
    Image.new("RGB", (8, 8), (10, 20, 30)).save(full, "JPEG")
    os.utime(full, (mtime, mtime))
    return full


def test_file_added_at_prefers_birth_or_ctime_over_mtime():
    """Downloaders stamp mtime to the post date; creation time is later."""
    post_time = time.time() - 86400 * 30
    full = _make_photo("sort_a", "old_post.jpg", mtime=post_time)
    # Force mtime old while creation/access stay recent where the OS allows.
    os.utime(full, (post_time, post_time))
    added = file_added_at(full)
    assert added > post_time + 60, (added, post_time)


def test_newest_sort_orders_by_added_at_not_mtime():
    index = ArchiveIndex.get()
    old_post = time.time() - 86400 * 10
    older_post = time.time() - 86400 * 20

    # File A: newer Instagram date (mtime), but indexed first → older added_at
    full_a = _make_photo("sort_b", "a_newer_post.jpg", mtime=old_post)
    index.upsert_photo("sort_b/a_newer_post.jpg")
    time.sleep(0.05)
    # File B: older Instagram date, indexed second → newer added_at
    full_b = _make_photo("sort_b", "b_older_post.jpg", mtime=older_post)
    index.upsert_photo("sort_b/b_older_post.jpg")

    # Simulate downloader stamping remote post times onto mtime.
    os.utime(full_a, (old_post, old_post))
    os.utime(full_b, (older_post, older_post))
    # Refresh mtime columns without changing added_at.
    index.upsert_photo("sort_b/a_newer_post.jpg")
    index.upsert_photo("sort_b/b_older_post.jpg")

    rows, _ = index.query_photos(creator="sort_b", sort="newest")
    assert [r["filename"] for r in rows] == [
        "b_older_post.jpg",
        "a_newer_post.jpg",
    ]

    rows_old, _ = index.query_photos(creator="sort_b", sort="oldest")
    assert [r["filename"] for r in rows_old] == [
        "a_newer_post.jpg",
        "b_older_post.jpg",
    ]


def test_upsert_keeps_added_at_stable():
    index = ArchiveIndex.get()
    _make_photo("sort_c", "stable.jpg", mtime=time.time() - 1000)
    index.upsert_photo("sort_c/stable.jpg")
    with index._lock:
        first = index._conn.execute(
            "SELECT added_at FROM photos WHERE rel_path = ?",
            ("sort_c/stable.jpg",),
        ).fetchone()["added_at"]
    time.sleep(0.05)
    index.upsert_photo("sort_c/stable.jpg", favorite=1)
    with index._lock:
        second = index._conn.execute(
            "SELECT added_at FROM photos WHERE rel_path = ?",
            ("sort_c/stable.jpg",),
        ).fetchone()["added_at"]
    assert first == second


def test_posted_sort_orders_by_mtime_post_time():
    """posted = Instagram chronology (mtime), independent of download order."""
    index = ArchiveIndex.get()
    newer_post = time.time() - 100
    older_post = time.time() - 10_000

    full_old = _make_photo("sort_d", "downloaded_second_older_post.jpg", mtime=older_post)
    index.upsert_photo("sort_d/downloaded_second_older_post.jpg")
    time.sleep(0.05)
    full_new = _make_photo("sort_d", "downloaded_first_newer_post.jpg", mtime=newer_post)
    index.upsert_photo("sort_d/downloaded_first_newer_post.jpg")

    # Re-stamp mtimes after index (simulates downloader post-date stamps).
    os.utime(full_old, (older_post, older_post))
    os.utime(full_new, (newer_post, newer_post))
    index.upsert_photo("sort_d/downloaded_second_older_post.jpg")
    index.upsert_photo("sort_d/downloaded_first_newer_post.jpg")

    by_post, _ = index.query_photos(creator="sort_d", sort="posted")
    assert [r["filename"] for r in by_post] == [
        "downloaded_first_newer_post.jpg",
        "downloaded_second_older_post.jpg",
    ]

    by_post_old, _ = index.query_photos(creator="sort_d", sort="posted_oldest")
    assert [r["filename"] for r in by_post_old] == [
        "downloaded_second_older_post.jpg",
        "downloaded_first_newer_post.jpg",
    ]


def test_posted_sort_falls_back_when_mtime_missing():
    index = ArchiveIndex.get()
    _make_photo("sort_e", "no_mtime.jpg", mtime=time.time() - 50)
    index.upsert_photo("sort_e/no_mtime.jpg")
    with index._lock:
        index._conn.execute(
            "UPDATE photos SET mtime = 0 WHERE rel_path = ?",
            ("sort_e/no_mtime.jpg",),
        )
        index._conn.commit()
    rows, _ = index.query_photos(creator="sort_e", sort="posted")
    assert len(rows) == 1
    assert rows[0]["filename"] == "no_mtime.jpg"
