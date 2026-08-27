"""P0.1 — the gallery page must be an indexed, covering read.

`docs/review_gallery_performance.md` measured a **1,674 ms** `sort=newest` first
page against the live 61k archive, and named three causes:

  * `COUNT(*)` with two LEFT JOINs the filter never asked for (1,036 ms cold),
  * `SELECT p.*`, which drags `caption_search` / `prompt_search` / leftover
    `glam_*` along for a grid that shows none of them (268 ms vs 0.2 ms slim),
  * `ORDER BY IFNULL(p.added_at, p.mtime)`, which cannot touch
    `idx_photos_added` — 58 ms and a temp B-tree instead of 0.1 ms.

These tests pin the **plan**, never the clock: timings on a shared runner are
noise, which is why `scripts/benchmark_queries.py` is not a CI gate either
(rule 13). The statements are captured from the connection's trace callback, so
what is asserted is exactly what the gallery executed — not a reconstruction.
"""

from __future__ import annotations

import contextlib
import os

import pytest
from PIL import Image

from promptstudio.config import SAVED_DIR
from promptstudio.storage.db import ArchiveIndex


@pytest.fixture
def index():
    return ArchiveIndex.get()


@contextlib.contextmanager
def sql_trace(index):
    """Every statement SQLite actually ran, with parameters already inlined.

    Through `ArchiveIndex.set_trace_callback`, not `index._conn`: the gallery
    reads run on the P1 read-only pool now, so a tracer installed on the writer
    alone would observe nothing and every assertion below would pass vacuously.
    """
    seen: list[str] = []
    index.set_trace_callback(seen.append)
    try:
        yield seen
    finally:
        index.set_trace_callback(None)


def _photo_stmts(seen: list[str]) -> list[str]:
    return [s for s in seen if "FROM photos p" in s]


def _is_count(stmt: str) -> bool:
    """Leading `SELECT COUNT(*)`, not merely a statement containing one.

    The grouped page carries `COUNT(*) AS group_count` in its own projection,
    so "contains COUNT(*)" classifies the page as the count and then asserts
    the page's joins away — the joins it needs to draw a verdict badge.
    """
    return stmt.lstrip().upper().startswith("SELECT COUNT(*)")


def _counts(seen: list[str]) -> list[str]:
    out = [s for s in _photo_stmts(seen) if _is_count(s)]
    assert out, f"no COUNT statement ran; saw {seen}"
    return out


def _pages(seen: list[str]) -> list[str]:
    out = [s for s in _photo_stmts(seen) if not _is_count(s)]
    assert out, f"no page statement ran; saw {seen}"
    return out


def _plan(index, sql: str) -> str:
    rows = index._conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
    return " | ".join(str(r[3]) for r in rows)


# ── ORDER BY must be able to use an index ────────────────────────────

@pytest.mark.parametrize("sort", ["newest", "oldest"])
def test_ingest_sorts_ride_the_added_at_index(index, make_photo, sort):
    make_photo(name="a.jpg")
    with sql_trace(index) as seen:
        index.query_photos(sort=sort, limit=60)
    plan = _plan(index, _pages(seen)[0])
    assert "idx_photos_added" in plan, plan


@pytest.mark.parametrize("sort", ["posted", "posted_oldest"])
def test_post_time_sorts_ride_the_mtime_index(index, make_photo, sort):
    make_photo(name="a.jpg")
    with sql_trace(index) as seen:
        index.query_photos(sort=sort, limit=60)
    plan = _plan(index, _pages(seen)[0])
    assert "idx_photos_mtime" in plan, plan


def test_newest_needs_no_temp_btree_for_the_filename_tiebreak(index, make_photo):
    """A single-column index leaves `p.filename ASC` to a temp B-tree.

    Measured on the live archive: the composite is what turns 58 ms into 0.1 ms.
    """
    make_photo(name="a.jpg")
    with sql_trace(index) as seen:
        index.query_photos(sort="newest", limit=60)
    plan = _plan(index, _pages(seen)[0])
    assert "TEMP B-TREE" not in plan.upper(), plan


def test_newest_order_no_longer_wraps_the_column(index, make_photo):
    """`added_at` is written on every row (0 of 61,344 null), so the IFNULL was
    defending a case that cannot happen — at the cost of the index."""
    make_photo(name="a.jpg")
    with sql_trace(index) as seen:
        index.query_photos(sort="newest", limit=60)
    page = _pages(seen)[0]
    order = page[page.upper().rindex("ORDER BY"):]
    assert "IFNULL" not in order.upper(), order


# ── the COUNT must not join tables the filter never mentioned ────────

def test_unfiltered_count_joins_neither_verdicts_nor_labels(index, make_photo):
    make_photo(name="a.jpg")
    with sql_trace(index) as seen:
        index.query_photos(sort="newest", limit=60)
    for stmt in _counts(seen):
        assert "media_verdicts" not in stmt, stmt
        assert "labels" not in stmt, stmt


def test_count_joins_verdicts_only_when_the_verdict_filter_needs_them(index, make_photo):
    rel, _ = make_photo(name="a.jpg")
    index.set_verdict(rel, tier=4)
    with sql_trace(index) as seen:
        index.query_photos(verdict="keep", limit=60)
    joined = _counts(seen)
    assert all("media_verdicts" in s for s in joined), joined
    assert all("labels" not in s for s in joined), joined


def test_count_joins_labels_only_when_the_label_filter_is_set(index, make_photo):
    make_photo(name="a.jpg")
    with sql_trace(index) as seen:
        index.query_photos(label="unlabeled", limit=60)
    joined = _counts(seen)
    assert all("labels" in s for s in joined), joined
    assert all("media_verdicts" not in s for s in joined), joined


def test_grouped_count_also_drops_the_unused_joins(index, make_photo):
    make_photo(name="a.jpg")
    with sql_trace(index) as seen:
        index.query_photos(sort="newest", limit=60, group_posts=True)
    for stmt in _counts(seen):
        assert "media_verdicts" not in stmt, stmt
        assert "labels" not in stmt, stmt


def test_paths_only_count_drops_the_unused_joins(index, make_photo):
    make_photo(name="a.jpg")
    with sql_trace(index) as seen:
        index.query_photos(sort="newest", paths_only=True)
    for stmt in _counts(seen):
        assert "media_verdicts" not in stmt, stmt


# ── the page must project what the grid draws, not the whole row ─────

def test_page_select_does_not_read_the_search_blobs(index, make_photo):
    """`caption_search` alone is 4.7 MB across the live archive, and no tile
    shows a character of it."""
    make_photo(name="a.jpg")
    with sql_trace(index) as seen:
        index.query_photos(sort="newest", limit=60)
    page = _pages(seen)[0]
    projection = page[: page.upper().index(" FROM ")]
    assert "p.*" not in projection, projection
    assert "caption_search" not in projection, projection
    assert "prompt_search" not in projection, projection


def test_grid_verdict_carries_exactly_the_fields_a_card_or_triage_panel_reads(
    index, make_photo
):
    """`renderTriageBlock` reads the *grid* row, not `/api/media/detail`, so
    confidence / prompt_version / sheet_path have to survive the slimming.
    `media_kind`, `verdict_source` and `classified_at` are read by nothing."""
    rel, _ = make_photo(name="a.jpg")
    index.set_verdict(
        rel,
        tier=4,
        reason="strong",
        confidence=0.9,
        prompt_version="v2-structured",
        sheet_path="_sheets/a.jpg",
        media_kind="photo",
        verdict_source="vision",
    )
    photo = index.query_photos(path=rel)[0][0]
    assert set(photo["verdict"]) == {
        "verdict",
        "tier",
        "manual",
        "reason",
        "confidence",
        "prompt_version",
        "sheet_path",
        "error",
    }


def test_slimming_keeps_every_column_row_to_photo_publishes(index, make_photo):
    rel, _ = make_photo(name="a.jpg", meta={"post_id": "123", "shortcode": "AbC"})
    photo = index.query_photos(path=rel)[0][0]
    for key in (
        "rel_path",
        "creator",
        "filename",
        "url",
        "thumb_url",
        "full_path",
        "taken_at",
        "favorite",
        "has_prompt",
        "prompt_stale",
        "post_id",
        "shortcode",
        "added_at",
    ):
        assert key in photo, key


# ── the fallbacks move to write time, where they are paid once ───────

def test_upsert_writes_a_sortable_mtime_when_the_filesystem_has_none(
    index, monkeypatch
):
    """`ORDER BY p.mtime` replaces a CASE that fell back to added_at. The
    fallback still exists — it just happens once, at write time."""
    folder = os.path.join(SAVED_DIR, "mt_a")
    os.makedirs(folder, exist_ok=True)
    full = os.path.join(folder, "no_mtime.jpg")
    Image.new("RGB", (8, 8), (1, 2, 3)).save(full, "JPEG")

    real_getmtime = os.path.getmtime

    def boom(p):
        if os.path.abspath(p) == os.path.abspath(full):
            raise OSError("no mtime here")
        return real_getmtime(p)

    monkeypatch.setattr(os.path, "getmtime", boom)
    index.upsert_photo("mt_a/no_mtime.jpg")

    with index._lock:
        row = index._conn.execute(
            "SELECT mtime, added_at FROM photos WHERE rel_path = ?",
            ("mt_a/no_mtime.jpg",),
        ).fetchone()
    assert row["mtime"], "mtime must be sortable, not 0"
    assert row["mtime"] == row["added_at"]


# ── media_kind: a stored fact, so the filter stops scanning filenames ─

def test_media_kind_is_written_at_upsert(index, make_photo):
    still, _ = make_photo(name="a.jpg")
    with index._lock:
        kinds = dict(
            index._conn.execute("SELECT rel_path, media_kind FROM photos").fetchall()
        )
    assert kinds[still] == "photo"


def test_media_kind_survives_a_rebuild(index, make_photo):
    still, _ = make_photo(name="a.jpg")
    index.rebuild()
    with index._lock:
        kind = index._conn.execute(
            "SELECT media_kind FROM photos WHERE rel_path = ?", (still,)
        ).fetchone()["media_kind"]
    assert kind == "photo"


def test_media_type_filter_stops_scanning_filenames(index, make_photo):
    make_photo(name="a.jpg")
    with sql_trace(index) as seen:
        index.query_photos(media_type="photo", limit=60)
    for stmt in _photo_stmts(seen):
        assert "LIKE" not in stmt.upper(), stmt


def test_stats_video_count_stops_scanning_filenames(index, make_photo):
    make_photo(name="a.jpg")
    with sql_trace(index) as seen:
        index.stats()
    scans = [s for s in seen if "FROM photos" in s and "LIKE" in s.upper()]
    assert not scans, scans
