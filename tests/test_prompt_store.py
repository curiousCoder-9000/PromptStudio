"""Prompt storage moved from prompts_cache.json into archive.db.

Three things had to survive the move: the public PromptCache API, the existing
JSON file's contents, and the has_prompt/prompt_stale flags on `photos`. One
thing had to change — the filename fallback that let two creators with the same
filename read each other's prompt.
"""

import json

import pytest

from promptstudio.prompts.cache import PromptCache
from promptstudio.storage.db import ArchiveIndex, _fts_query

ENTRY = {
    "positive_prompt": "woman in a red bikini on a beach",
    "negative_prompt": "blurry",
    "visual_tags": ["bikini", "beach"],
    "parameters": {"vision_engine": "E1", "pipeline_version": "v2-structured"},
}


@pytest.fixture
def cache(tmp_path):
    """Cache pointed at a JSON path that does not exist — DB-backed only."""
    c = PromptCache(cache_file=str(tmp_path / "prompts_cache.json"))
    c.invalidate_memory()
    return c


# ── round trip ───────────────────────────────────────────────────────


def test_set_then_get(cache):
    cache.set("nina/a.jpg", dict(ENTRY))
    got = cache.get("nina/a.jpg", "a.jpg")
    assert got["positive_prompt"] == ENTRY["positive_prompt"]
    assert got["visual_tags"] == ["bikini", "beach"]


def test_get_missing_returns_none(cache):
    assert cache.get("nina/nope.jpg", "nope.jpg") is None


def test_load_materialises_every_entry(cache):
    cache.set("nina/a.jpg", dict(ENTRY))
    cache.set("mia/b.jpg", dict(ENTRY))
    assert set(cache.load()) == {"nina/a.jpg", "mia/b.jpg"}


def test_count_ready_tracks_rows(cache):
    assert cache.count_ready() == 0
    cache.set("nina/a.jpg", dict(ENTRY))
    assert cache.count_ready() == 1


def test_delete_removes_the_entry(cache):
    cache.set("nina/a.jpg", dict(ENTRY))
    cache.delete("nina/a.jpg", "a.jpg")
    assert cache.get("nina/a.jpg", "a.jpg") is None
    assert cache.count_ready() == 0


def test_backslash_paths_normalise(cache):
    cache.set("nina\\a.jpg", dict(ENTRY))
    assert cache.get("nina/a.jpg", "a.jpg") is not None


# ── the bug the move fixes ───────────────────────────────────────────


def test_same_filename_under_two_creators_stays_separate(cache):
    """The JSON lookup fell back to bare filename, so these collided."""
    cache.set("nina/photo_1.jpg", {**ENTRY, "positive_prompt": "nina's prompt"})
    cache.set("mia/photo_1.jpg", {**ENTRY, "positive_prompt": "mia's prompt"})

    assert cache.get("nina/photo_1.jpg", "photo_1.jpg")["positive_prompt"] == "nina's prompt"
    assert cache.get("mia/photo_1.jpg", "photo_1.jpg")["positive_prompt"] == "mia's prompt"


def test_ambiguous_filename_lookup_refuses_to_guess(cache):
    """With two owners of a filename, a filename-only lookup returns nothing
    rather than one creator's prompt at random."""
    cache.set("nina/photo_1.jpg", dict(ENTRY))
    cache.set("mia/photo_1.jpg", dict(ENTRY))
    cache.invalidate_memory()
    assert ArchiveIndex.get().prompt_get("unknown/photo_1.jpg", "photo_1.jpg") is None


def test_unambiguous_filename_lookup_still_resolves(cache):
    """Legacy JSON keyed some entries by bare filename; one owner is safe."""
    cache.set("nina/only.jpg", dict(ENTRY))
    cache.invalidate_memory()
    assert ArchiveIndex.get().prompt_get("moved/only.jpg", "only.jpg") is not None


# ── history ──────────────────────────────────────────────────────────


def test_editing_a_prompt_pushes_history(cache):
    cache.set("nina/a.jpg", dict(ENTRY))
    cache.set("nina/a.jpg", {**ENTRY, "positive_prompt": "second version"})
    entry = cache.get("nina/a.jpg", "a.jpg")
    assert entry["positive_prompt"] == "second version"
    assert entry["history"][0]["positive_prompt"] == ENTRY["positive_prompt"]


def test_restore_history_swaps_back(cache):
    cache.set("nina/a.jpg", dict(ENTRY))
    cache.set("nina/a.jpg", {**ENTRY, "positive_prompt": "second version"})
    restored = cache.restore_history("nina/a.jpg", 0)
    assert restored["positive_prompt"] == ENTRY["positive_prompt"]
    assert cache.get("nina/a.jpg", "a.jpg")["positive_prompt"] == ENTRY["positive_prompt"]


def test_history_is_capped(cache):
    from promptstudio.config import PROMPT_HISTORY_MAX

    for i in range(PROMPT_HISTORY_MAX + 4):
        cache.set("nina/a.jpg", {**ENTRY, "positive_prompt": f"v{i}"})
    assert len(cache.get("nina/a.jpg", "a.jpg")["history"]) <= PROMPT_HISTORY_MAX


# ── JSON import ──────────────────────────────────────────────────────


def _seed_legacy_json(raw):
    """Write the legacy cache at the real path and re-arm the one-time import.

    Uses PROMPT_CACHE_FILE rather than a tmp path on purpose: the "already
    imported" flag is global (there is one prompt cache in production), so a
    custom path would be marked done by whichever instance ran first.
    """
    from promptstudio.config import PROMPT_CACHE_FILE

    with open(PROMPT_CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(raw if isinstance(raw, str) else json.dumps(raw))

    index = ArchiveIndex.get()
    with index._lock:
        index._conn.execute("DELETE FROM meta WHERE key = 'prompts_imported_from_json'")
        index._conn.execute("DELETE FROM prompts")
        if index.fts_enabled:
            index._conn.execute("DELETE FROM prompts_fts")
        index._conn.commit()

    fresh = PromptCache()
    fresh.invalidate_memory()
    return fresh, PROMPT_CACHE_FILE


def test_legacy_json_is_imported_once():
    cache, _ = _seed_legacy_json({"nina/a.jpg": ENTRY, "mia/b.jpg": ENTRY})
    assert cache.count_ready() == 2
    assert cache.get("nina/a.jpg", "a.jpg")["positive_prompt"] == ENTRY["positive_prompt"]


def test_import_does_not_resurrect_after_a_deliberate_clear():
    cache, _ = _seed_legacy_json({"nina/a.jpg": ENTRY})
    assert cache.count_ready() == 1

    cache.delete("nina/a.jpg", "a.jpg")
    cache.invalidate_memory()
    assert cache.count_ready() == 0, "the JSON must not be re-imported"


def test_legacy_json_is_left_on_disk_as_a_snapshot():
    cache, path = _seed_legacy_json({"nina/a.jpg": ENTRY})
    original = open(path, encoding="utf-8").read()

    cache.set("nina/a.jpg", {**ENTRY, "positive_prompt": "changed"})

    assert open(path, encoding="utf-8").read() == original, (
        "the JSON is a rollback copy, not a mirror"
    )
    assert cache.get("nina/a.jpg", "a.jpg")["positive_prompt"] == "changed"


def test_corrupt_legacy_json_does_not_break_startup():
    cache, _ = _seed_legacy_json("{ this is not json")
    assert cache.count_ready() == 0
    cache.set("nina/a.jpg", dict(ENTRY))
    assert cache.count_ready() == 1


# ── photos-table flags stay in sync ──────────────────────────────────


def test_setting_a_prompt_updates_the_photo_flags(cache, make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    cache.set(rel, dict(ENTRY))

    from promptstudio.prompts.engine import ENGINE_ID

    rows, _ = ArchiveIndex.get().query_photos(creator="nina")
    row = next(r for r in rows if r["rel_path"] == rel)
    expected = ENTRY["parameters"]["vision_engine"] == ENGINE_ID
    assert bool(row["has_prompt"]) is expected


def test_deleting_a_prompt_clears_the_photo_flags(cache, make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    cache.set(rel, dict(ENTRY))
    cache.delete(rel, "a.jpg")

    rows, _ = ArchiveIndex.get().query_photos(creator="nina")
    row = next(r for r in rows if r["rel_path"] == rel)
    assert not row["has_prompt"]


# ── search ───────────────────────────────────────────────────────────


def test_search_still_finds_prompt_text(cache, make_photo):
    rel_a, _ = make_photo(creator="nina", name="a.jpg")
    make_photo(creator="mia", name="b.jpg")
    cache.set(rel_a, dict(ENTRY))

    rows, _ = ArchiveIndex.get().query_photos(search="bikini")
    assert [r["rel_path"] for r in rows] == [rel_a]


def test_search_matches_creator_and_filename(cache, make_photo):
    rel, _ = make_photo(creator="nina", name="sunset.jpg")
    rows, _ = ArchiveIndex.get().query_photos(search="nina")
    assert rel in [r["rel_path"] for r in rows]
    rows, _ = ArchiveIndex.get().query_photos(search="sunse")
    assert rel in [r["rel_path"] for r in rows]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("red bikini", '"red"* AND "bikini"*'),
        ("BIKINI", '"bikini"*'),
        ("", ""),
        ("   ", ""),
        ("!!!", ""),
    ],
)
def test_fts_query_builder(raw, expected):
    assert _fts_query(raw) == expected


def test_fts_query_neutralises_operators():
    """FTS5 syntax inside a query must be literal, not executable."""
    q = _fts_query('foo OR bar NEAR" -baz')
    assert q == '"foo"* AND "or"* AND "bar"* AND "near"* AND "baz"*'


def test_fts_index_is_maintained_even_though_search_defaults_to_like(cache):
    index = ArchiveIndex.get()
    if not index.fts_enabled:
        pytest.skip("SQLite build without FTS5")
    cache.set("nina/a.jpg", dict(ENTRY))
    with index._lock:
        rows = index._conn.execute(
            "SELECT rel_path FROM prompts_fts WHERE prompts_fts MATCH ?", ('"bikini"*',)
        ).fetchall()
    assert [r[0] for r in rows] == ["nina/a.jpg"]


def test_fts_index_drops_deleted_prompts(cache):
    index = ArchiveIndex.get()
    if not index.fts_enabled:
        pytest.skip("SQLite build without FTS5")
    cache.set("nina/a.jpg", dict(ENTRY))
    cache.delete("nina/a.jpg", "a.jpg")
    with index._lock:
        rows = index._conn.execute(
            "SELECT rel_path FROM prompts_fts WHERE prompts_fts MATCH ?", ('"bikini"*',)
        ).fetchall()
    assert rows == []
