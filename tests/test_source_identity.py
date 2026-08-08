"""Platform-scoped identity in the archive index.

Post ids are only unique *within* a platform. Before `source`/`platform` existed,
a Reddit submission id or X tweet id could collide with an Instagram
mediaid/shortcode, and the failure was silent in both directions: a fresh post
skipped as "already deleted", or a deliberately deleted post coming back.
"""

import os
import sqlite3

import pytest

from promptstudio.storage.db import DEFAULT_SOURCE, ArchiveIndex


@pytest.fixture
def index():
    return ArchiveIndex.get()


# ── tombstones ──────────────────────────────────────────────────────────

def test_legacy_calls_default_to_instagram(index):
    """Every pre-existing caller omits `platform` and must keep working."""
    index.record_deleted_post("nina", shortcode="ABC123")
    assert index.is_deleted_post("nina", shortcode="ABC123") is True
    assert index.is_deleted_post("nina", shortcode="ABC123", platform="instagram") is True


def test_tombstone_does_not_leak_across_platforms(index):
    """The actual bug: an IG tombstone must not hide an X post with the same id."""
    index.record_deleted_post("nina", post_id="1772623353")
    assert index.is_deleted_post("nina", post_id="1772623353", platform="x") is False
    assert index.is_deleted_post("nina", post_id="1772623353", platform="reddit") is False
    assert index.is_deleted_post("nina", post_id="1772623353") is True


def test_same_id_can_be_tombstoned_per_platform(index):
    for platform in ("instagram", "x", "reddit"):
        assert index.record_deleted_post("nina", post_id="555", platform=platform)
    for platform in ("instagram", "x", "reddit"):
        assert index.is_deleted_post("nina", post_id="555", platform=platform) is True


def test_clearing_one_platform_leaves_the_others(index):
    index.record_deleted_post("nina", shortcode="DUP", platform="instagram")
    index.record_deleted_post("nina", shortcode="DUP", platform="x")

    assert index.clear_deleted_post("nina", shortcode="DUP", platform="x") == 1
    assert index.is_deleted_post("nina", shortcode="DUP", platform="x") is False
    assert index.is_deleted_post("nina", shortcode="DUP", platform="instagram") is True


def test_platform_is_normalized(index):
    index.record_deleted_post("nina", post_id="9", platform="X")
    assert index.is_deleted_post("nina", post_id="9", platform="x") is True
    assert index.is_deleted_post("nina", post_id="9", platform="  x  ") is True


def test_blank_platform_falls_back_to_instagram(index):
    index.record_deleted_post("nina", post_id="77", platform="")
    assert index.is_deleted_post("nina", post_id="77") is True


def test_creator_scoping_still_applies(index):
    index.record_deleted_post("nina", post_id="1", platform="x")
    assert index.is_deleted_post("other", post_id="1", platform="x") is False


# ── carousel_paths ──────────────────────────────────────────────────────

def test_carousel_paths_is_scoped_by_source(index, make_photo):
    """An X tweet id must not match an IG row and look like a complete carousel."""
    make_photo(
        creator="nina",
        name="ig.jpg",
        meta={"post_id": "collide", "shortcode": "sc1", "source": "instagram"},
    )
    make_photo(
        creator="nina__x",
        name="x.jpg",
        meta={"post_id": "collide", "shortcode": "sc1", "source": "x"},
    )

    ig = index.carousel_paths(post_id="collide", source="instagram")
    xs = index.carousel_paths(post_id="collide", source="x")

    assert ig == ["nina/ig.jpg"]
    assert xs == ["nina__x/x.jpg"]


def test_carousel_paths_defaults_to_instagram(index, make_photo):
    make_photo(
        creator="nina__x",
        name="x.jpg",
        meta={"post_id": "xonly", "source": "x"},
    )
    # The Instagram downloader calls this without a source argument.
    assert index.carousel_paths(post_id="xonly") == []


def test_carousel_paths_can_search_every_source(index, make_photo):
    make_photo(creator="nina", name="ig.jpg", meta={"post_id": "c", "source": "instagram"})
    make_photo(creator="nina__x", name="x.jpg", meta={"post_id": "c", "source": "x"})
    assert len(index.carousel_paths(post_id="c", source=None)) == 2


# ── source column ───────────────────────────────────────────────────────

def test_source_read_from_sidecar_on_index(index, make_photo):
    rel, _ = make_photo(creator="r_fashion__reddit", name="a.jpg", meta={"source": "reddit"})
    assert index.get_photo_source(rel) == "reddit"


def test_photos_without_sidecar_default_to_instagram(index, make_photo):
    rel, _ = make_photo(creator="legacy", name="a.jpg")
    assert index.get_photo_source(rel) == DEFAULT_SOURCE


def test_explicit_source_wins_over_sidecar(index, make_photo):
    rel, _ = make_photo(creator="nina__x", name="a.jpg", meta={"source": "instagram"})
    index.upsert_photo(rel, source="x")
    assert index.get_photo_source(rel) == "x"


def test_later_upserts_preserve_source(index, make_photo):
    """Favorite/prompt writes call upsert_photo with no source — must not reset it."""
    rel, _ = make_photo(creator="nina__x", name="a.jpg", meta={"source": "x"})
    index.upsert_photo(rel, favorite=1)
    index.upsert_photo(rel, has_prompt=1)
    assert index.get_photo_source(rel) == "x"


def test_rebuild_preserves_source_from_sidecars(index, make_photo):
    rel_x, _ = make_photo(creator="nina__x", name="a.jpg", meta={"source": "x"})
    rel_ig, _ = make_photo(creator="nina", name="b.jpg", meta={"source": "instagram"})
    index.rebuild()
    assert index.get_photo_source(rel_x) == "x"
    assert index.get_photo_source(rel_ig) == "instagram"


# ── trash round trip ────────────────────────────────────────────────────

def test_deleting_non_ig_media_tombstones_under_its_own_platform(store, make_photo):
    rel, _ = make_photo(
        creator="nina__x",
        name="a.jpg",
        meta={"post_id": "tw1", "source": "x"},
    )
    store.delete_photo(rel)

    index = ArchiveIndex.get()
    assert index.is_deleted_post("nina__x", post_id="tw1", platform="x") is True
    # ...and did NOT poison the Instagram namespace
    assert index.is_deleted_post("nina__x", post_id="tw1", platform="instagram") is False


def test_restoring_non_ig_media_clears_its_tombstone(store, make_photo):
    """Restore must clear the tombstone it actually wrote, not an IG-scoped one."""
    from promptstudio.storage.trash import TrashStore

    rel, _ = make_photo(
        creator="nina__x",
        name="a.jpg",
        meta={"post_id": "tw2", "source": "x"},
    )
    result = store.delete_photo(rel)
    entry_id = result.get("trash_id") if isinstance(result, dict) else None
    assert entry_id, f"expected a trash id, got {result!r}"

    index = ArchiveIndex.get()
    assert index.is_deleted_post("nina__x", post_id="tw2", platform="x") is True

    TrashStore().restore(entry_id)
    assert index.is_deleted_post("nina__x", post_id="tw2", platform="x") is False


def test_trash_manifest_records_platform(store, make_photo):
    import json

    from promptstudio.config import TRASH_DIR

    rel, _ = make_photo(
        creator="nina__x", name="a.jpg", meta={"post_id": "tw3", "source": "x"}
    )
    store.delete_photo(rel)
    entries = [d for d in os.listdir(TRASH_DIR) if os.path.isdir(os.path.join(TRASH_DIR, d))]
    assert entries
    with open(os.path.join(TRASH_DIR, entries[0], "entry.json"), encoding="utf-8") as fh:
        assert json.load(fh)["platform"] == "x"


# ── migration ───────────────────────────────────────────────────────────

def test_pre_multisource_db_migrates(tmp_path):
    """Opening an archive.db written before this feature must just work."""
    db = str(tmp_path / "archive.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE photos (rel_path TEXT PRIMARY KEY, creator TEXT NOT NULL,
          filename TEXT NOT NULL, taken_at TEXT, mtime REAL,
          favorite INTEGER NOT NULL DEFAULT 0, has_prompt INTEGER NOT NULL DEFAULT 0,
          prompt_stale INTEGER NOT NULL DEFAULT 0, prompt_search TEXT,
          post_id TEXT, shortcode TEXT, glam_score INTEGER NOT NULL DEFAULT -1);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE deleted_posts (id INTEGER PRIMARY KEY AUTOINCREMENT,
          creator TEXT NOT NULL, shortcode TEXT, post_id TEXT, rel_path TEXT,
          deleted_at TEXT NOT NULL, source TEXT DEFAULT 'ui');
        CREATE UNIQUE INDEX idx_deleted_shortcode ON deleted_posts(creator, shortcode)
          WHERE shortcode IS NOT NULL AND shortcode != '';
        CREATE UNIQUE INDEX idx_deleted_post_id ON deleted_posts(creator, post_id)
          WHERE post_id IS NOT NULL AND post_id != '';
        """
    )
    conn.execute(
        "INSERT INTO photos(rel_path, creator, filename, post_id, glam_score) "
        "VALUES('nina/a.jpg', 'nina', 'a.jpg', '111', 2)"
    )
    conn.execute(
        "INSERT INTO deleted_posts(creator, shortcode, deleted_at, source) "
        "VALUES('nina', 'GONE', '2026-01-01', 'ui')"
    )
    conn.commit()
    conn.close()

    migrated = ArchiveIndex(db_path=db, base_dir=str(tmp_path))
    try:
        # Legacy rows are Instagram, and back-filling must not lose data.
        row = migrated._conn.execute(
            "SELECT source, glam_score FROM photos WHERE rel_path='nina/a.jpg'"
        ).fetchone()
        assert (row["source"], row["glam_score"]) == (DEFAULT_SOURCE, 2)
        assert migrated.is_deleted_post("nina", shortcode="GONE") is True
        assert migrated.is_deleted_post("nina", shortcode="GONE", platform="x") is False

        # Unique indexes now include platform, so per-platform rows coexist.
        migrated.record_deleted_post("nina", shortcode="GONE", platform="x")
        count = migrated._conn.execute(
            "SELECT COUNT(*) c FROM deleted_posts WHERE shortcode='GONE'"
        ).fetchone()["c"]
        assert count == 2
    finally:
        migrated.close()


def test_migration_is_idempotent(tmp_path):
    db = str(tmp_path / "archive.db")
    first = ArchiveIndex(db_path=db, base_dir=str(tmp_path))
    first.record_deleted_post("nina", post_id="1", platform="x")
    first.close()

    second = ArchiveIndex(db_path=db, base_dir=str(tmp_path))
    try:
        assert second.is_deleted_post("nina", post_id="1", platform="x") is True
    finally:
        second.close()
