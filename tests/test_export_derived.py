"""E1 — export and re-import derived state.

Derived state is everything the archive cannot re-download: prompts and
verdicts that cost GPU hours, favourites and ratings that are the user's own
judgement, styles, and the generation index. Media is deliberately **not** in
the bundle — it is the one thing that can be fetched again, and including it
would turn a portable file into a copy of the archive.

The test that matters is the round trip: export, wipe, import, and every store
answers the way it did before.
"""

from __future__ import annotations

import json
import os

import pytest

from promptstudio.prompts.cache import PromptCache
from promptstudio.prompts.styles import CreatorStyleStore
from promptstudio.storage.db import ArchiveIndex
from promptstudio.storage.export_bundle import (
    BUNDLE_VERSION,
    DERIVED_KINDS,
    export_derived,
    import_derived,
)
from promptstudio.storage.favorites import FavoritesStore

PROMPT = {
    "positive_prompt": "a portrait, golden hour",
    "negative_prompt": "blurry",
    "visual_tags": ["portrait"],
    "parameters": {"vision_engine": "test", "pipeline_version": "v2-structured"},
}


@pytest.fixture
def populated(make_photo):
    """One photo carrying every kind of derived state."""
    rel, _full = make_photo(creator="nina", name="a.jpg")
    index = ArchiveIndex.get()
    PromptCache().set(rel, dict(PROMPT), push_history=False)
    FavoritesStore().set_favorite(rel, True)
    CreatorStyleStore().save({"nina": {"prefix": "shot on film", "n": 5}})
    index.set_verdict(rel, creator="nina", tier=3, reason="looks good")
    index.set_phash(rel, 1234567890)
    gen_id = index.record_generation(
        rel_path="_generations/nina/a_gen_1.png",
        source_rel=rel,
        creator="nina",
        workflow="pro",
        seed=4242,
        positive_prompt="a portrait, golden hour",
    )
    index.rate_generation(gen_id, 2)
    return rel, gen_id


def _wipe_derived():
    """Clear every derived store, leaving the media alone."""
    index = ArchiveIndex.get()
    with index._lock:
        for table in ("prompts", "media_verdicts", "phashes", "generations"):
            index._conn.execute(f"DELETE FROM {table}")
        index._conn.commit()
    PromptCache().invalidate_memory()
    FavoritesStore().save(set())
    FavoritesStore().invalidate_memory()
    CreatorStyleStore().save({})


def test_export_writes_a_versioned_bundle(populated, tmp_path):
    out = tmp_path / "derived.json"

    summary = export_derived(str(out))

    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["version"] == BUNDLE_VERSION
    assert set(data["kinds"]) == set(DERIVED_KINDS)
    assert summary["prompts"] == 1
    assert summary["generations"] == 1


def test_the_bundle_carries_no_media_and_no_absolute_paths(populated, tmp_path):
    """A portable file, not a copy of the archive — and one that does not leak
    the exporting machine's directory layout."""
    from promptstudio.config import SAVED_DIR

    out = tmp_path / "derived.json"
    export_derived(str(out))

    raw = out.read_text(encoding="utf-8")
    assert SAVED_DIR not in raw
    assert "full_path" not in raw


def test_round_trip_restores_every_kind(populated, tmp_path):
    rel, gen_id = populated
    out = tmp_path / "derived.json"
    export_derived(str(out))
    _wipe_derived()
    index = ArchiveIndex.get()
    assert index.prompt_count() == 0

    import_derived(str(out))

    assert PromptCache().get(rel, "a.jpg")["positive_prompt"] == PROMPT["positive_prompt"]
    assert FavoritesStore().is_favorite(rel) is True
    assert CreatorStyleStore().load()["nina"]["prefix"] == "shot on film"
    verdict = index.get_verdict(rel)
    assert verdict["tier"] == 3
    gens = index.list_generations_for(rel)
    assert len(gens) == 1
    assert gens[0]["rating"] == 2, "the user's judgement did not survive the trip"
    assert gens[0]["seed"] == 4242


def test_importing_twice_changes_nothing(populated, tmp_path):
    """Restore has to be safe to re-run — a half-finished import gets retried."""
    out = tmp_path / "derived.json"
    export_derived(str(out))
    _wipe_derived()

    first = import_derived(str(out))
    second = import_derived(str(out))

    assert first == second
    index = ArchiveIndex.get()
    assert index.prompt_count() == 1
    assert len(index.list_generations_for("nina/a.jpg")) == 1


def test_a_dry_run_reports_without_writing(populated, tmp_path):
    out = tmp_path / "derived.json"
    export_derived(str(out))
    _wipe_derived()

    summary = import_derived(str(out), dry_run=True)

    assert summary["prompts"] == 1
    assert ArchiveIndex.get().prompt_count() == 0, "dry run wrote to the store"


def test_a_single_kind_can_be_restored_alone(populated, tmp_path):
    """The rescore case from the spec: a prompt version was invalidated and only
    the prompts are wanted back."""
    out = tmp_path / "derived.json"
    export_derived(str(out))
    _wipe_derived()

    import_derived(str(out), kinds=["prompts"])

    assert ArchiveIndex.get().prompt_count() == 1
    assert FavoritesStore().is_favorite("nina/a.jpg") is False
    assert ArchiveIndex.get().list_generations_for("nina/a.jpg") == []


def test_exporting_a_single_kind_omits_the_others(populated, tmp_path):
    out = tmp_path / "derived.json"

    export_derived(str(out), kinds=["favorites"])

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["kinds"] == ["favorites"]
    assert "prompts" not in data["payload"]


def test_an_unknown_kind_is_rejected_rather_than_silently_skipped(tmp_path):
    with pytest.raises(ValueError, match="unknown"):
        export_derived(str(tmp_path / "x.json"), kinds=["not_a_kind"])


def test_a_bundle_from_a_future_version_is_refused(populated, tmp_path):
    """Importing a newer bundle with older code would half-apply it, and the
    half that lands is the half you cannot see."""
    out = tmp_path / "derived.json"
    export_derived(str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    data["version"] = BUNDLE_VERSION + 1
    out.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="version"):
        import_derived(str(out))


def test_a_gzipped_bundle_round_trips(populated, tmp_path):
    """Prompts dominate the size and compress well; .gz is chosen by extension
    so neither side needs a flag."""
    out = tmp_path / "derived.json.gz"
    export_derived(str(out))
    _wipe_derived()

    import_derived(str(out))

    assert ArchiveIndex.get().prompt_count() == 1


def test_importing_does_not_resurrect_state_for_missing_media(populated, tmp_path):
    """The bundle is keyed by rel_path. Restoring onto an archive that no longer
    has the photo must not invent an index row for a file that is not there."""
    rel, _gen_id = populated
    out = tmp_path / "derived.json"
    export_derived(str(out))
    _wipe_derived()
    index = ArchiveIndex.get()
    os.remove(os.path.join(index.base_dir, rel))
    # rebuild() is what drops index rows for media that is gone — without it
    # the assertion would pass on a stale row rather than on the import.
    index.rebuild()
    assert index.query_photos(creator="nina")[0] == []

    import_derived(str(out))

    photos, _total = index.query_photos(creator="nina")
    assert photos == [], "import wrote to `photos`, which rebuild() owns"
