"""P0.1's migration, against a DB with the schema the live archive actually has.

The whole indexed-ORDER-BY change rests on a claim about stored data: that
`added_at` and `mtime` are always populated, so the query can name them
directly instead of wrapping them in `IFNULL`/`CASE` and losing the index. On
the live archive that was measured true (0 of 61,344 null). It is *made* true
here, for every DB that predates the change.

The shape below is not invented — it is the column list of a real pre-existing
`archive.db` on a developer machine: no `added_at`, no `caption_search`, no
`p_keep`, and the orphaned `glam_score` that 1cc0f44 left behind. If the
migration is wrong, the symptom on that archive is not an error, it is a
"newest" page that silently orders 61k rows by a column full of zeroes.
"""

from __future__ import annotations

import os
import sqlite3
import time

import pytest

from promptstudio.storage.db import ArchiveIndex

# Exactly what a pre-added_at archive.db carries.
_OLD_SCHEMA = """
CREATE TABLE photos (
  rel_path TEXT PRIMARY KEY,
  creator TEXT NOT NULL,
  filename TEXT NOT NULL,
  taken_at TEXT,
  mtime REAL,
  favorite INTEGER NOT NULL DEFAULT 0,
  has_prompt INTEGER NOT NULL DEFAULT 0,
  prompt_stale INTEGER NOT NULL DEFAULT 0,
  prompt_search TEXT,
  post_id TEXT,
  shortcode TEXT,
  glam_score REAL,
  source TEXT NOT NULL DEFAULT 'instagram'
);
CREATE INDEX idx_photos_creator ON photos(creator);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

POSTED = time.time() - 86400 * 30


@pytest.fixture
def upgraded(tmp_path):
    """An old-schema DB, opened once by the current code."""
    db = tmp_path / "archive.db"
    media = tmp_path / "media"
    media.mkdir()
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_SCHEMA)
    conn.executemany(
        "INSERT INTO photos(rel_path, creator, filename, mtime) VALUES (?,?,?,?)",
        [
            # A normal row: the downloader stamped mtime to the post date.
            ("someone/a_post.jpg", "someone", "a_post.jpg", POSTED),
            # A reel, so media_kind has to come out 'video'.
            ("someone/a_reel.mp4", "someone", "a_reel.mp4", POSTED - 100),
            # mtime 0 — the case the old `CASE WHEN mtime > 0` defended.
            ("someone/no_mtime.jpg", "someone", "no_mtime.jpg", 0),
            # mtime NULL, which is not the same as 0 and broke COALESCE.
            ("someone/null_mtime.webm", "someone", "null_mtime.webm", None),
        ],
    )
    conn.commit()
    conn.close()

    index = ArchiveIndex(db_path=str(db), base_dir=str(media))
    try:
        yield index
    finally:
        index.close()


def _rows(index):
    with index._lock:
        return {
            r["rel_path"]: r
            for r in index._conn.execute(
                "SELECT rel_path, added_at, mtime, media_kind FROM photos"
            ).fetchall()
        }


def _indexes(index):
    with index._lock:
        return {
            r[0]
            for r in index._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='photos'"
            ).fetchall()
        }


# ── the columns the new ORDER BY reads ───────────────────────────────

def test_added_at_is_never_null_after_the_upgrade(upgraded):
    for rel, row in _rows(upgraded).items():
        assert row["added_at"] is not None, rel


def test_mtime_is_never_null_after_the_upgrade(upgraded):
    for rel, row in _rows(upgraded).items():
        assert row["mtime"] is not None, rel


def test_a_row_with_only_a_post_time_borrows_it_for_added_at(upgraded):
    """The old `IFNULL(added_at, mtime)` is now a stored value."""
    row = _rows(upgraded)["someone/a_post.jpg"]
    assert row["added_at"] == pytest.approx(POSTED)


def test_a_row_with_no_time_at_all_keeps_zero_rather_than_now(upgraded):
    """Substituting now() would shove unknown rows to the top of the one view
    whose entire purpose is showing what just arrived."""
    rows = _rows(upgraded)
    assert rows["someone/no_mtime.jpg"]["added_at"] == 0
    assert rows["someone/null_mtime.webm"]["added_at"] == 0
    assert rows["someone/no_mtime.jpg"]["mtime"] == 0


def test_unknown_ingest_times_sort_last_under_newest(upgraded):
    photos, _total = upgraded.query_photos(sort="newest")
    order = [p["rel_path"] for p in photos]
    assert order[:2] == ["someone/a_post.jpg", "someone/a_reel.mp4"], order
    assert set(order[2:]) == {"someone/no_mtime.jpg", "someone/null_mtime.webm"}


# ── media_kind ───────────────────────────────────────────────────────

def test_media_kind_is_backfilled_from_the_extension(upgraded):
    rows = _rows(upgraded)
    assert rows["someone/a_post.jpg"]["media_kind"] == "photo"
    assert rows["someone/no_mtime.jpg"]["media_kind"] == "photo"
    assert rows["someone/a_reel.mp4"]["media_kind"] == "video"
    assert rows["someone/null_mtime.webm"]["media_kind"] == "video"


def test_the_media_type_filter_works_on_a_migrated_db(upgraded):
    videos, total = upgraded.query_photos(media_type="video", sort="newest")
    assert total == 2
    assert {p["rel_path"] for p in videos} == {
        "someone/a_reel.mp4",
        "someone/null_mtime.webm",
    }


def test_stats_counts_videos_on_a_migrated_db(upgraded):
    stats = upgraded.stats()
    assert stats["total_videos"] == 2
    assert stats["total_photos"] == 2


# ── the indexes the plan depends on ──────────────────────────────────

def test_the_composite_sort_indexes_are_created(upgraded):
    names = _indexes(upgraded)
    assert "idx_photos_added_name" in names
    assert "idx_photos_mtime" in names
    # Kept, not replaced: the planner picks the single-column ASC index for
    # `oldest`, which the DESC composite cannot serve.
    assert "idx_photos_added" in names


@pytest.mark.parametrize(
    ("sort", "wanted"),
    [("newest", "idx_photos_added"), ("posted", "idx_photos_mtime")],
)
def test_the_migrated_db_can_actually_use_them(upgraded, sort, wanted):
    with upgraded._lock:
        plan = " | ".join(
            str(r[3])
            for r in upgraded._conn.execute(
                "EXPLAIN QUERY PLAN SELECT p.rel_path FROM photos p "
                f"ORDER BY p.{'added_at' if sort == 'newest' else 'mtime'} DESC, "
                "p.filename ASC LIMIT 60"
            ).fetchall()
        )
    assert wanted in plan, plan


# ── it is a one-shot, not a per-startup scan ─────────────────────────

def test_reopening_does_not_redo_the_backfill(upgraded, tmp_path):
    """`WHERE media_kind IS NULL` over 61k rows on every boot is the thing the
    meta flag exists to avoid."""
    with upgraded._lock:
        assert upgraded._meta_get("sort_columns_coalesced") == "1"
        # A row that arrives with the flag already set is left alone, which is
        # what proves the pass does not run again.
        upgraded._conn.execute(
            "INSERT INTO photos(rel_path, creator, filename, mtime, added_at) "
            "VALUES ('someone/later.jpg', 'someone', 'later.jpg', 0, 0)"
        )
        upgraded._conn.commit()

    reopened = ArchiveIndex(
        db_path=upgraded.db_path, base_dir=upgraded.base_dir
    )
    try:
        with reopened._lock:
            row = reopened._conn.execute(
                "SELECT media_kind FROM photos WHERE rel_path = 'someone/later.jpg'"
            ).fetchone()
        assert row["media_kind"] is None
    finally:
        reopened.close()


def test_the_orphaned_glam_column_is_left_alone_but_unread(upgraded):
    """Rewriting `photos` to drop it is not worth it; reading it is not either."""
    with upgraded._lock:
        cols = {
            r[1] for r in upgraded._conn.execute("PRAGMA table_info(photos)").fetchall()
        }
        assert "glam_score" in cols
        seen: list[str] = []
        upgraded.set_trace_callback(seen.append)
        try:
            upgraded.query_photos(sort="newest", limit=60)
        finally:
            upgraded.set_trace_callback(None)
    assert seen
    for stmt in seen:
        assert "glam_score" not in stmt, stmt
        assert "p.*" not in stmt, stmt


def test_a_photo_upserted_after_the_upgrade_is_sortable(upgraded):
    """The migration fixes history; `upsert_photo` has to keep it true."""
    from PIL import Image

    folder = os.path.join(upgraded.base_dir, "someone")
    os.makedirs(folder, exist_ok=True)
    full = os.path.join(folder, "fresh.jpg")
    Image.new("RGB", (8, 8), (7, 7, 7)).save(full, "JPEG")
    upgraded.upsert_photo("someone/fresh.jpg")

    row = _rows(upgraded)["someone/fresh.jpg"]
    assert row["added_at"] > 0
    assert row["mtime"] > 0
    assert row["media_kind"] == "photo"
