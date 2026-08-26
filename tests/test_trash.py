"""Soft delete and restore.

The reject-review flow can select and delete hundreds of vision-classified
photos at once, so "delete" must be reversible and a restore must bring back
the *whole* record — file, sidecar, prompt bundle, favorite flag, index row —
and un-tombstone the post so a future sync isn't blocked.
"""

import json
import os

import pytest
from PIL import Image

from promptstudio.config import METADATA_SUFFIX, SAVED_DIR, TRASH_DIR
from promptstudio.prompts.cache import PromptCache
from promptstudio.storage.db import ArchiveIndex
from promptstudio.storage.favorites import FavoritesStore
from promptstudio.storage.trash import TrashStore

META = {
    "post_id": "999",
    "shortcode": "ABC123",
    "caption": "hi",
    "taken_at": "2026-01-02T03:04:05+00:00",
}
BUNDLE = {
    "positive_prompt": "a purple test frame",
    "negative_prompt": "blurry",
    "visual_tags": ["test", "purple"],
    "parameters": {"vision_engine": "smoke", "pipeline_version": "v2-structured"},
}


@pytest.fixture
def trash():
    return TrashStore()


@pytest.fixture
def photo(make_photo):
    """An indexed photo with sidecar metadata, a prompt bundle, and a favorite."""
    rel, full = make_photo(meta=META)
    PromptCache().set(rel, dict(BUNDLE), push_history=False)
    FavoritesStore().set_favorite(rel, True)
    return rel, full


# ── soft delete ──────────────────────────────────────────────────────

def test_soft_delete_removes_from_archive_and_index(store, photo):
    rel, full = photo
    result = store.delete_photo(rel)

    assert result["permanent"] is False
    assert result["trash_id"]
    assert not os.path.exists(full), "media should be gone from the archive"
    assert not os.path.exists(full + METADATA_SUFFIX), "sidecar should move too"
    assert ArchiveIndex.get().get_photo_identity(rel)[0] is None
    assert PromptCache().get(rel, os.path.basename(rel)) is None
    assert FavoritesStore().is_favorite(rel) is False


def test_soft_delete_writes_a_tombstone(store, photo):
    rel, _ = photo
    store.delete_photo(rel)
    assert ArchiveIndex.get().is_deleted_post("test_creator", shortcode="ABC123") is True


def test_manifest_captures_restorable_state(store, photo):
    rel, _ = photo
    tid = store.delete_photo(rel)["trash_id"]

    entry_dir = os.path.join(TRASH_DIR, tid)
    with open(os.path.join(entry_dir, "entry.json"), encoding="utf-8") as f:
        manifest = json.load(f)

    assert manifest["rel_path"] == rel
    assert manifest["favorite"] is True
    assert manifest["prompt_bundle"]["positive_prompt"] == BUNDLE["positive_prompt"]
    assert manifest["tombstoned"] is True
    assert manifest["taken_at"] == META["taken_at"]
    assert manifest["file_size"] > 0
    assert os.path.isfile(os.path.join(entry_dir, "photo_1.jpg"))
    assert os.path.isfile(os.path.join(entry_dir, "photo_1.jpg" + METADATA_SUFFIX))


def test_delete_missing_path_returns_none(store):
    assert store.delete_photo("test_creator/absent.jpg") is None


def test_delete_ghost_index_row_drops_the_catalog_entry(store, make_photo):
    """File already gone (folder wiped / deleted outside the app) used to 404."""
    rel, full = make_photo(
        creator="voidclub.bkk",
        name="voidclub.bkk_2024-12-12_08-00-00_UTC.jpg",
        meta=META,
    )
    os.remove(full)
    sidecar = full + METADATA_SUFFIX
    if os.path.isfile(sidecar):
        os.remove(sidecar)

    result = store.delete_photo(rel)

    assert result is not None
    assert result["permanent"] is True
    assert result["trash_id"] is None
    assert result["rel_path"] == rel
    assert ArchiveIndex.get().get_photo_identity(rel)[0] is None
    # Delete still tombstones, so a later scrape does not bring the ghost back.
    assert ArchiveIndex.get().is_deleted_post(
        "voidclub.bkk", shortcode="ABC123"
    ) is True


def test_delete_ghost_via_api(api, make_photo):
    rel, full = make_photo(
        creator="voidclub.bkk",
        name="voidclub.bkk_2024-12-12_08-00-00_UTC.jpg",
    )
    os.remove(full)
    from urllib.parse import quote

    status, payload = api("DELETE", f"/api/photo?path={quote(rel, safe='')}")
    assert status == 200
    assert payload["status"] == "deleted"
    assert payload["trash_id"] is None
    assert ArchiveIndex.get().get_photo_identity(rel)[0] is None


def test_photo_without_sidecar_or_prompt_still_trashes(store, make_photo):
    rel, full = make_photo(name="bare.jpg")
    result = store.delete_photo(rel)
    assert result["trash_id"]
    assert not os.path.exists(full)


# ── listing ──────────────────────────────────────────────────────────

def test_list_and_stats(store, trash, photo):
    rel, _ = photo
    store.delete_photo(rel)

    entries, total = trash.list_entries()
    assert total == 1
    assert entries[0]["media_present"] is True

    stats = trash.stats()
    assert stats["count"] == 1
    assert stats["bytes"] > 0
    assert stats["retention_days"] == 30


def test_count_entries_matches_list(store, trash, make_photo):
    for n in range(3):
        rel, _ = make_photo(name=f"p{n}.jpg")
        store.delete_photo(rel)
    assert trash.count_entries() == 3
    assert trash.list_entries()[1] == 3


def test_listing_is_newest_first_and_paginates(store, trash, make_photo):
    for n in range(5):
        rel, _ = make_photo(name=f"p{n}.jpg")
        store.delete_photo(rel)

    page, total = trash.list_entries(limit=2, offset=0)
    assert total == 5
    assert len(page) == 2
    stamps = [e["deleted_at"] for e in trash.list_entries()[0]]
    assert stamps == sorted(stamps, reverse=True)


def test_empty_trash_lists_nothing(trash):
    assert trash.list_entries() == ([], 0)
    assert trash.count_entries() == 0


# ── restore ──────────────────────────────────────────────────────────

def test_restore_brings_back_everything(store, trash, photo):
    rel, full = photo
    tid = store.delete_photo(rel)["trash_id"]

    result = trash.restore(tid)

    assert result["status"] == "restored"
    assert os.path.isfile(full)
    assert os.path.isfile(full + METADATA_SUFFIX)
    assert ArchiveIndex.get().get_photo_identity(rel)[0] is not None
    assert FavoritesStore().is_favorite(rel) is True
    assert PromptCache().get(rel, "photo_1.jpg")["positive_prompt"] == BUNDLE["positive_prompt"]
    assert ArchiveIndex.get().is_deleted_post("test_creator", shortcode="ABC123") is False
    assert not os.path.isdir(os.path.join(TRASH_DIR, tid))


def test_restore_recreates_a_deleted_creator_folder(store, trash, photo):
    rel, full = photo
    tid = store.delete_photo(rel)["trash_id"]
    os.rmdir(os.path.join(SAVED_DIR, "test_creator"))

    assert trash.restore(tid)["status"] == "restored"
    assert os.path.isfile(full)


def test_restore_refuses_to_overwrite_an_occupied_path(store, trash, photo):
    rel, full = photo
    tid = store.delete_photo(rel)["trash_id"]
    Image.new("RGB", (10, 10), (0, 255, 0)).save(full, "JPEG")  # something else took the slot

    result = trash.restore(tid)

    assert result["status"] == "conflict"
    assert Image.open(full).size == (10, 10), "existing file must be untouched"
    assert os.path.isdir(os.path.join(TRASH_DIR, tid)), "entry kept so no data is lost"


@pytest.mark.parametrize("bad_id", ["", "nope", "../../etc", "..", "./x", "a/b"])
def test_restore_rejects_unknown_and_traversing_ids(trash, bad_id):
    assert trash.restore(bad_id)["status"] == "not_found"


def test_restore_reports_error_when_media_vanished(store, trash, photo):
    rel, _ = photo
    tid = store.delete_photo(rel)["trash_id"]
    os.remove(os.path.join(TRASH_DIR, tid, "photo_1.jpg"))

    result = trash.restore(tid)
    assert result["status"] == "error"
    assert "missing" in result["message"]


def test_restored_photo_can_be_deleted_again(store, trash, photo):
    rel, _ = photo
    tid = store.delete_photo(rel)["trash_id"]
    trash.restore(tid)
    again = store.delete_photo(rel)
    assert again["trash_id"] and again["trash_id"] != tid


# ── permanent delete ─────────────────────────────────────────────────

def test_permanent_delete_skips_the_trash(store, trash, photo):
    rel, full = photo
    result = store.delete_photo(rel, permanent=True)

    assert result["permanent"] is True
    assert result["trash_id"] is None
    assert not os.path.exists(full)
    assert not os.path.exists(full + METADATA_SUFFIX)
    assert trash.count_entries() == 0


# ── purge ────────────────────────────────────────────────────────────

def test_purge_by_id(store, trash, photo):
    rel, _ = photo
    tid = store.delete_photo(rel)["trash_id"]
    assert trash.purge(tid) is True
    assert trash.count_entries() == 0


def test_purge_unknown_id_is_false(trash):
    assert trash.purge("does-not-exist") is False


def test_empty_removes_all(store, trash, make_photo):
    for n in range(3):
        rel, _ = make_photo(name=f"p{n}.jpg")
        store.delete_photo(rel)
    assert trash.empty() == 3
    assert trash.count_entries() == 0


def test_purge_expired_keeps_fresh_entries(store, trash, photo):
    rel, _ = photo
    store.delete_photo(rel)
    assert trash.purge_expired(30) == 0
    assert trash.count_entries() == 1


def test_purge_expired_with_zero_days_is_a_noop(store, trash, photo):
    """days<=0 disables purging rather than deleting everything."""
    rel, _ = photo
    store.delete_photo(rel)
    assert trash.purge_expired(0) == 0
    assert trash.count_entries() == 1


def test_purge_expired_removes_old_entries(store, trash, photo):
    rel, _ = photo
    tid = store.delete_photo(rel)["trash_id"]

    # Backdate the manifest instead of waiting 31 days
    manifest_path = os.path.join(TRASH_DIR, tid, "entry.json")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["deleted_at"] = "2020-01-01T00:00:00+00:00"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f)

    assert trash.purge_expired(30) == 1
    assert trash.count_entries() == 0


def test_purge_expired_tolerates_a_corrupt_timestamp(store, trash, photo):
    rel, _ = photo
    tid = store.delete_photo(rel)["trash_id"]
    manifest_path = os.path.join(TRASH_DIR, tid, "entry.json")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["deleted_at"] = "not a date"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f)

    assert trash.purge_expired(30) == 0  # skipped, not crashed
    assert trash.count_entries() == 1


# ── gallery isolation ────────────────────────────────────────────────

def test_trashed_media_never_appears_in_the_gallery(store, photo):
    rel, _ = photo
    store.delete_photo(rel)
    store.ensure_ready(force=True)  # full reindex — _trash must stay excluded

    rel_paths = [p["rel_path"] for p in store.iter_photos()]
    assert rel_paths == []
    assert "_trash" not in [c["name"] for c in store.list_creators()]
