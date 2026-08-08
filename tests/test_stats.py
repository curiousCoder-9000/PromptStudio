"""Gallery stats counters.

`/api/stats` runs on every app init. `prompts_ready` used to walk every photo in
the archive and load the entire prompt cache; it now reads the indexed
`has_prompt` column. These tests pin the *values* so the cheap path can't drift
from the exact one.
"""

from promptstudio.prompts.batch import count_prompts_ready
from promptstudio.prompts.cache import PromptCache
from promptstudio.prompts.engine import ENGINE_ID
from promptstudio.storage.db import ArchiveIndex

FRESH = {
    "positive_prompt": "a photo",
    "negative_prompt": "blurry",
    "visual_tags": ["a"],
    "parameters": {"vision_engine": ENGINE_ID, "pipeline_version": "v2-structured"},
}
STALE_ENGINE = {
    "positive_prompt": "old",
    "negative_prompt": "blurry",
    "visual_tags": [],
    "parameters": {"vision_engine": "moondream:old", "pipeline_version": "v1"},
}


def test_empty_archive_reports_zeros(store):
    stats = store.stats()
    assert stats["total_photos"] == 0
    assert stats["total_creators"] == 0
    assert stats["prompts_ready"] == 0


def test_counts_photos_and_creators(store, make_photo):
    make_photo(creator="alice", name="a1.jpg")
    make_photo(creator="alice", name="a2.jpg")
    make_photo(creator="bob", name="b1.jpg")

    stats = store.stats()
    assert stats["total_photos"] == 3
    assert stats["total_creators"] == 2


def test_prompts_ready_counts_only_current_engine(store, make_photo):
    rel_fresh, _ = make_photo(name="fresh.jpg")
    rel_stale, _ = make_photo(name="stale.jpg")
    make_photo(name="none.jpg")  # no prompt at all

    cache = PromptCache()
    cache.set(rel_fresh, dict(FRESH), push_history=False)
    cache.set(rel_stale, dict(STALE_ENGINE), push_history=False)

    assert store.stats()["prompts_ready"] == 1


def test_indexed_count_matches_the_exact_cache_walk(store, make_photo):
    """The cheap indexed count must agree with count_prompts_ready()."""
    cache = PromptCache()
    for n in range(5):
        rel, _ = make_photo(name=f"p{n}.jpg")
        if n % 2 == 0:
            cache.set(rel, dict(FRESH), push_history=False)

    assert store.stats()["prompts_ready"] == 3
    assert count_prompts_ready() == 3


def test_prompts_ready_drops_when_a_photo_is_deleted(store, make_photo):
    rel, _ = make_photo(name="doomed.jpg")
    PromptCache().set(rel, dict(FRESH), push_history=False)
    assert store.stats()["prompts_ready"] == 1

    store.delete_photo(rel)
    assert store.stats()["prompts_ready"] == 0
    assert store.stats()["total_photos"] == 0


def test_prompts_ready_returns_after_a_restore(store, make_photo):
    from promptstudio.storage.trash import TrashStore

    rel, _ = make_photo(name="restore_me.jpg")
    PromptCache().set(rel, dict(FRESH), push_history=False)

    trash_id = store.delete_photo(rel)["trash_id"]
    assert store.stats()["prompts_ready"] == 0

    assert TrashStore().restore(trash_id)["status"] == "restored"
    assert store.stats()["prompts_ready"] == 1, "restore must re-flag has_prompt"


def test_survives_a_full_reindex(store, make_photo):
    rel, _ = make_photo(name="p.jpg")
    PromptCache().set(rel, dict(FRESH), push_history=False)
    assert store.stats()["prompts_ready"] == 1

    ArchiveIndex.get().rebuild()
    assert store.stats()["prompts_ready"] == 1, "rebuild must re-derive has_prompt"
