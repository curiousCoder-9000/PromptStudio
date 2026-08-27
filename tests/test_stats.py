"""Gallery stats counters.

`/api/stats` runs on every app init. `prompts_ready` used to walk every photo in
the archive and load the entire prompt cache; it now reads the indexed
`has_prompt` column. These tests pin the *values* so the cheap path can't drift
from the exact one.
"""

from promptstudio.config import DISTRIBUTION_MAX_SHARE
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


# ── archive-wide unclassified count ──────────────────────────────────
#
# The navbar "Classify All" button reads this. It used to sum the sidebar's
# per-creator counters instead, which /api/creators narrows to the active
# source filter — so with a source picked the button reported that platform's
# backlog as the archive's and disabled itself saying everything was already
# classified. This number must never be scoped to anything.


def test_unclassified_total_counts_media_with_no_verdict(store, make_photo):
    index = ArchiveIndex.get()
    rel_a, _ = make_photo(creator="alice", name="a1.jpg")
    make_photo(creator="alice", name="a2.jpg")
    make_photo(creator="bob", name="b1.jpg")

    assert store.stats()["unclassified_total"] == 3
    index.set_verdict(rel_a, creator="alice", tier=3)
    assert store.stats()["unclassified_total"] == 2


def test_unclassified_total_ignores_the_source_filter(store, make_photo):
    """The one property the sidebar counters cannot provide."""
    index = ArchiveIndex.get()
    rel_ig, _ = make_photo(creator="mixed", name="ig.jpg")
    rel_x, _ = make_photo(creator="mixed", name="x.jpg")
    index.upsert_photo(rel_ig, source="instagram")
    index.upsert_photo(rel_x, source="x")
    index.set_verdict(rel_x, creator="mixed", tier=3)

    # Filtered to X the sidebar sees nothing left to do; the archive disagrees.
    scoped = index.creator_verdict_counts(source="x")["mixed"]
    assert scoped["unclassified_count"] == 0
    assert store.stats()["unclassified_total"] == 1


def test_unclassified_total_is_zero_on_an_empty_archive(store):
    assert store.stats()["unclassified_total"] == 0


def test_an_errored_verdict_still_counts_as_classified(store, make_photo):
    """`unclassified` means "no row", matching the sidebar's own definition.

    A failed attempt leaves tier -1, which the review UI surfaces separately as
    an error. Counting it here would make the button offer work that the job's
    default (retry_errors) already picks up under a different name.
    """
    rel, _ = make_photo(name="boom.jpg")
    ArchiveIndex.get().set_verdict(rel, creator="test_creator", tier=-1, error="timeout")
    assert store.stats()["unclassified_total"] == 0


# ── B4 pass-rate facets ──────────────────────────────────────────────
#
# Every verdict filter advertises the share of the archive it selects, so a
# filter that has quietly become a no-op says so where the user is standing
# rather than in a panel they stopped opening. One grouped pass serves all of
# them: five chips in the review strip plus ten options in the browse
# dropdown is fifteen round trips otherwise, on a route that runs at app init.


def _seed_tiers(make_photo, tiers, creator="tester"):
    """One photo per entry; None means "never classified"."""
    index = ArchiveIndex.get()
    for i, tier in enumerate(tiers):
        rel, _ = make_photo(creator=creator, name=f"m_{i:03d}.jpg")
        if tier is not None:
            index.set_verdict(rel, creator=creator, tier=tier)
    return index


def test_verdict_facet_counts_match_the_filter_each_one_labels(make_photo):
    """The badge and the page it describes must come from the same predicate.

    A facet computed its own way is worse than no facet: it would report a
    pass rate for a filter nobody is running.
    """
    index = _seed_tiers(make_photo, [0, 0, 1, 2, 3, 4, -1, None, None])
    facets = index.verdict_facet_counts()

    assert facets["total"] == 9
    for name, count in facets["counts"].items():
        _rows, total = index.query_photos(verdict=name)
        assert total == count, f"{name}: facet {count} vs filter {total}"


def test_verdict_facet_shares_are_that_count_over_the_whole_archive(make_photo):
    index = _seed_tiers(make_photo, [0, 0, 1, 2])
    facets = index.verdict_facet_counts()

    assert facets["counts"]["reject"] == 3  # tiers 0, 0, 1 against cut=1
    assert facets["shares"]["reject"] == 0.75
    assert facets["shares"]["keep"] == 0.25
    assert facets["counts"]["t2"] == 1
    assert facets["counts"]["t3"] == 0
    assert facets["counts"]["t4"] == 0
    assert facets["shares"]["unclassified"] == 0.0
    assert facets["warn_above"] == DISTRIBUTION_MAX_SHARE


def test_verdict_facet_shares_are_none_on_an_empty_archive():
    """Nothing measured yet is not the same answer as measured at zero."""
    facets = ArchiveIndex.get().verdict_facet_counts()
    assert facets["total"] == 0
    assert all(share is None for share in facets["shares"].values())


def test_verdict_facets_are_one_query_not_one_per_chip(make_photo):
    index = _seed_tiers(make_photo, [0, 1, 2, 3])
    statements = []
    index._conn.set_trace_callback(statements.append)
    try:
        index.verdict_facet_counts()
    finally:
        index._conn.set_trace_callback(None)

    selects = [s for s in statements if s.strip().upper().startswith("SELECT")]
    assert len(selects) == 1, statements


def test_verdict_facets_ignore_the_source_filter(make_photo):
    """Archive-wide on purpose, like `unclassified_total` above it.

    Saturation is a property of the classifier over everything it has judged.
    A share that moved as the user clicked between platforms could not be
    compared against the 60% rule at all.
    """
    index = ArchiveIndex.get()
    rel_ig, _ = make_photo(creator="mixed", name="ig.jpg")
    rel_x, _ = make_photo(creator="mixed", name="x.jpg")
    index.upsert_photo(rel_ig, source="instagram")
    index.upsert_photo(rel_x, source="x")
    index.set_verdict(rel_ig, creator="mixed", tier=4)
    index.set_verdict(rel_x, creator="mixed", tier=0)

    facets = index.verdict_facet_counts()
    assert facets["total"] == 2
    assert facets["counts"]["keep"] == 1
    assert facets["counts"]["reject"] == 1


def test_stats_route_carries_the_verdict_facets(api, make_photo):
    _seed_tiers(make_photo, [0, 0, 0, 1])
    status, payload = api("GET", "/api/stats")

    assert status == 200
    facets = payload["verdict_facets"]
    assert facets["total"] == 4
    assert facets["counts"]["reject"] == 4
    assert facets["shares"]["reject"] == 1.0
    assert facets["shares"]["unusable"] == 0.75
    assert facets["warn_above"] == DISTRIBUTION_MAX_SHARE
