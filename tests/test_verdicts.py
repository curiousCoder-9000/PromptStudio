"""Keep/reject verdict storage.

The load-bearing property is that **only the tier is stored**. Keep vs reject is
derived at query time against `CLASSIFY_REJECT_MAX_TIER`, so moving the
threshold re-thresholds the archive with no re-classify. The previous
subsystem stored the collapsed answer instead, and every change of mind about
where the line sat cost a full-archive rescore.

Second property: a manual override outranks the model and survives everything —
a re-classify, and a soft delete + Undo.
"""

import os

import pytest

from promptstudio.storage.archive import ArchiveStore
from promptstudio.storage.db import ArchiveIndex


@pytest.fixture
def index():
    return ArchiveIndex.get()


def _seed(index, make_photo, rows):
    """rows: [(name, tier)] -> {name: rel_path}"""
    out = {}
    for name, tier in rows:
        rel, _full = make_photo(name=name)
        index.set_verdict(
            rel,
            creator="test_creator",
            tier=tier,
            reason=f"tier {tier}",
            media_kind="photo",
            verdict_source="image",
            confidence=0.8,
            prompt_version="v4-ordinal-frame-v7a",
            error=None if tier >= 0 else "vision timeout",
        )
        out[name] = rel
    return out


# ── round trip ───────────────────────────────────────────────────────

def test_set_and_get_round_trips_every_field(index, make_photo):
    rel, _full = make_photo(name="a.jpg")
    index.set_verdict(
        rel,
        creator="test_creator",
        tier=3,
        reason="crop top + jeans",
        media_kind="photo",
        verdict_source="image",
        confidence=0.77,
        prompt_version="v4-ordinal-frame-v7a",
        sheet_path=None,
        duration_ms=1234,
    )
    v = index.get_verdict(rel)
    assert v["tier"] == 3
    assert v["verdict"] == "keep"
    assert v["reason"] == "crop top + jeans"
    assert v["media_kind"] == "photo"
    assert v["confidence"] == pytest.approx(0.77)
    assert v["prompt_version"] == "v4-ordinal-frame-v7a"
    assert v["duration_ms"] == 1234
    assert v["manual"] is None


def test_get_verdict_of_unclassified_is_empty(index, make_photo):
    rel, _full = make_photo(name="a.jpg")
    assert index.get_verdict(rel) == {}


def test_reclassify_overwrites_the_tier(index, make_photo):
    rel, _full = make_photo(name="a.jpg")
    index.set_verdict(rel, creator="test_creator", tier=1)
    index.set_verdict(rel, creator="test_creator", tier=4)
    assert index.get_verdict(rel)["tier"] == 4


def test_a_failed_attempt_is_its_own_state(index, make_photo):
    """tier -1 + error is distinguishable from "never attempted"."""
    rel, _full = make_photo(name="a.jpg")
    index.set_verdict(rel, creator="test_creator", tier=-1, error="vision timeout")
    v = index.get_verdict(rel)
    assert v["verdict"] == "error"
    assert v["error"] == "vision timeout"
    # And it is retryable — an errored row comes back in the pending list.
    pending = index.list_unclassified("test_creator")
    assert [p["rel_path"] for p in pending] == [rel]


# ── the threshold is a query-time knob ───────────────────────────────

@pytest.mark.parametrize(
    "cut,expected_rejects",
    [
        (0, {"t0.jpg"}),
        (1, {"t0.jpg", "t1.jpg"}),
        (2, {"t0.jpg", "t1.jpg", "t2.jpg"}),
    ],
)
def test_moving_the_cut_rethresholds_without_reclassifying(
    index, make_photo, cut, expected_rejects
):
    rows = [(f"t{t}.jpg", t) for t in range(5)]
    _seed(index, make_photo, rows)
    photos, _total = index.query_photos(verdict="reject", reject_cut=cut)
    assert {os.path.basename(p["rel_path"]) for p in photos} == expected_rejects


def test_keep_is_the_complement_of_reject(index, make_photo):
    _seed(index, make_photo, [(f"t{t}.jpg", t) for t in range(5)])
    rejects, n_rejects = index.query_photos(verdict="reject", reject_cut=1)
    keeps, n_keeps = index.query_photos(verdict="keep", reject_cut=1)
    assert n_rejects == 2 and n_keeps == 3
    assert not {p["rel_path"] for p in rejects} & {p["rel_path"] for p in keeps}


def test_unusable_and_modest_split_the_reject_pile(index, make_photo):
    _seed(index, make_photo, [(f"t{t}.jpg", t) for t in range(5)])
    unusable, n_unusable = index.query_photos(verdict="unusable")
    modest, n_modest = index.query_photos(verdict="modest")
    assert n_unusable == 1 and n_modest == 1
    assert index.query_photos(verdict="reject")[1] == n_unusable + n_modest
    assert all(p["verdict"]["tier"] == 0 for p in unusable)
    assert all(p["verdict"]["tier"] == 1 for p in modest)


def test_t2_t3_t4_split_the_keep_pile(index, make_photo):
    """Reject already is T0+T1. Keep without these is T2+T3+T4 in one bucket."""
    _seed(index, make_photo, [(f"t{t}.jpg", t) for t in range(5)])
    by_tier = {}
    for name in ("t2", "t3", "t4"):
        photos, total = index.query_photos(verdict=name)
        by_tier[name] = total
        assert total == 1
        assert photos[0]["verdict"]["tier"] == int(name[1])
    assert index.query_photos(verdict="keep")[1] == sum(by_tier.values())
    # A hand-kept T0 is a keep, but not evidence about the T2 pile.
    rels = _seed(index, make_photo, [("hand.jpg", 0)])
    index.set_manual_verdict(rels["hand.jpg"], "keep")
    assert index.query_photos(verdict="keep")[1] == sum(by_tier.values()) + 1
    assert index.query_photos(verdict="t2")[1] == 1


def test_unclassified_excludes_anything_with_a_row(index, make_photo):
    _seed(index, make_photo, [("scored.jpg", 2), ("failed.jpg", -1)])
    make_photo(name="never.jpg")
    photos, total = index.query_photos(verdict="unclassified")
    assert total == 1
    assert photos[0]["rel_path"].endswith("never.jpg")
    assert "verdict" not in photos[0]


def test_error_filter_finds_only_failed_attempts(index, make_photo):
    _seed(index, make_photo, [("ok.jpg", 2), ("bad.jpg", -1)])
    photos, total = index.query_photos(verdict="error")
    assert total == 1
    assert photos[0]["rel_path"].endswith("bad.jpg")


# ── manual override ──────────────────────────────────────────────────

def test_manual_override_outranks_the_tier(index, make_photo):
    rels = _seed(index, make_photo, [("t0.jpg", 0)])
    assert index.get_verdict(rels["t0.jpg"])["verdict"] == "reject"
    assert index.set_manual_verdict(rels["t0.jpg"], "keep") is True
    v = index.get_verdict(rels["t0.jpg"])
    assert v["verdict"] == "keep"
    # The measurement is untouched — only the policy for this one file changed.
    assert v["tier"] == 0
    assert index.query_photos(verdict="reject")[1] == 0
    assert index.query_photos(verdict="keep")[1] == 1


def test_manual_override_survives_a_reclassify(index, make_photo):
    rels = _seed(index, make_photo, [("a.jpg", 0)])
    index.set_manual_verdict(rels["a.jpg"], "keep")
    index.set_verdict(rels["a.jpg"], creator="test_creator", tier=0, reason="again")
    v = index.get_verdict(rels["a.jpg"])
    assert v["manual"] == "keep"
    assert v["verdict"] == "keep"


def test_clearing_the_override_returns_to_the_model(index, make_photo):
    rels = _seed(index, make_photo, [("a.jpg", 0)])
    index.set_manual_verdict(rels["a.jpg"], "keep")
    index.set_manual_verdict(rels["a.jpg"], None)
    assert index.get_verdict(rels["a.jpg"])["verdict"] == "reject"


def test_override_on_an_unclassified_file_is_refused(index, make_photo):
    rel, _full = make_photo(name="a.jpg")
    assert index.set_manual_verdict(rel, "keep") is False


def test_bad_override_value_raises(index, make_photo):
    rels = _seed(index, make_photo, [("a.jpg", 0)])
    with pytest.raises(ValueError):
        index.set_manual_verdict(rels["a.jpg"], "maybe")


def test_overridden_rows_leave_the_unusable_bucket(index, make_photo):
    """`unusable`/`modest` are raw-tier views, so a hand-kept file must drop out."""
    rels = _seed(index, make_photo, [("t0.jpg", 0)])
    assert index.query_photos(verdict="unusable")[1] == 1
    index.set_manual_verdict(rels["t0.jpg"], "keep")
    assert index.query_photos(verdict="unusable")[1] == 0


def test_bulk_override_is_one_transaction(index, make_photo):
    rels = _seed(index, make_photo, [("a.jpg", 0), ("b.jpg", 1), ("c.jpg", 4)])
    unclassified, _ = make_photo(name="never.jpg")
    result = index.set_manual_verdicts(
        [rels["a.jpg"], rels["b.jpg"], unclassified, rels["a.jpg"]],
        "keep",
    )
    assert set(result["updated"]) == {rels["a.jpg"], rels["b.jpg"]}
    assert result["missing"] == [unclassified]
    assert index.get_verdict(rels["a.jpg"])["verdict"] == "keep"
    assert index.get_verdict(rels["b.jpg"])["verdict"] == "keep"
    assert index.get_verdict(rels["c.jpg"])["manual"] is None
    assert index.query_photos(verdict="keep")[1] == 3  # c was already keep


def test_bulk_override_empty_input(index):
    assert index.set_manual_verdicts([], "keep") == {"updated": [], "missing": []}


def test_paths_only_returns_favorite_flags(index, make_photo):
    rels = _seed(index, make_photo, [("a.jpg", 0), ("b.jpg", 0), ("c.jpg", 4)])
    index.set_favorite(rels["b.jpg"], True)
    rows, total = index.query_photos(verdict="reject", paths_only=True)
    assert total == 2
    by_path = {r["rel_path"]: r["favorite"] for r in rows}
    assert by_path[rels["a.jpg"]] is False
    assert by_path[rels["b.jpg"]] is True
    assert rels["c.jpg"] not in by_path
    # Full photo payloads must not leak into a path-list response.
    assert set(rows[0]) == {"rel_path", "favorite"}


def test_paths_only_honours_limit_without_lying_about_total(index, make_photo):
    _seed(index, make_photo, [(f"r{i}.jpg", 0) for i in range(8)])
    rows, total = index.query_photos(verdict="reject", paths_only=True, limit=3)
    assert total == 8
    assert len(rows) == 3


# ── counters ─────────────────────────────────────────────────────────

def test_creator_counts_add_up_to_photo_count(index, make_photo):
    _seed(index, make_photo, [("t0.jpg", 0), ("t1.jpg", 1), ("t4.jpg", 4), ("err.jpg", -1)])
    make_photo(name="never.jpg")
    counts = index.creator_verdict_counts()["test_creator"]
    assert counts["keep_count"] == 1
    assert counts["reject_count"] == 2
    assert counts["error_count"] == 1
    assert counts["unclassified_count"] == 1
    assert counts["unusable_count"] == 1
    assert counts["modest_count"] == 1
    assert counts["t2_count"] == 0
    assert counts["t3_count"] == 0
    assert counts["t4_count"] == 1
    total = (
        counts["keep_count"]
        + counts["reject_count"]
        + counts["error_count"]
        + counts["unclassified_count"]
    )
    assert total == 5


def test_stale_count_tracks_superseded_prompt_versions(index, make_photo):
    rel, _full = make_photo(name="old.jpg")
    index.set_verdict(rel, creator="test_creator", tier=2, prompt_version="v1-ancient")
    fresh, _full2 = make_photo(name="new.jpg")
    index.set_verdict(fresh, creator="test_creator", tier=2, prompt_version="v-current")

    counts = index.creator_verdict_counts(stale_versions=["v-current"])["test_creator"]
    assert counts["stale_count"] == 1
    # No stale_versions given means staleness is unknown, not "everything".
    assert index.creator_verdict_counts()["test_creator"]["stale_count"] == 0


def test_list_creators_carries_the_counters(index, make_photo):
    _seed(index, make_photo, [("t0.jpg", 0), ("t3.jpg", 3)])
    row = next(c for c in index.list_creators() if c["name"] == "test_creator")
    assert row["reject_count"] == 1
    assert row["keep_count"] == 1
    assert row["unclassified_count"] == 0


def test_creators_with_no_verdicts_still_have_every_key(index, make_photo):
    make_photo(name="a.jpg")
    row = next(c for c in index.list_creators() if c["name"] == "test_creator")
    for key in (
        "keep_count",
        "reject_count",
        "unclassified_count",
        "stale_count",
        "t2_count",
        "t3_count",
        "t4_count",
    ):
        assert key in row


# ── pending list ─────────────────────────────────────────────────────

def test_pending_skips_what_is_already_classified(index, make_photo):
    _seed(index, make_photo, [("done.jpg", 2)])
    make_photo(name="todo.jpg")
    pending = index.list_unclassified("test_creator")
    assert [os.path.basename(p["rel_path"]) for p in pending] == ["todo.jpg"]


def test_force_takes_everything(index, make_photo):
    _seed(index, make_photo, [("a.jpg", 2), ("b.jpg", 3)])
    assert len(index.list_unclassified("test_creator", force=True)) == 2


def test_stale_versions_pull_superseded_rows_back_in(index, make_photo):
    rel, _full = make_photo(name="old.jpg")
    index.set_verdict(rel, creator="test_creator", tier=2, prompt_version="v1-ancient")
    assert index.list_unclassified("test_creator") == []
    pending = index.list_unclassified("test_creator", stale_versions=["v-current"])
    assert [p["rel_path"] for p in pending] == [rel]


def test_pending_can_exclude_videos(index, make_photo):
    make_photo(name="a.jpg")
    # A .mp4 need not decode — list_unclassified only stats the file.
    rel, _full = make_photo(name="clip.jpg")
    video = os.path.join(os.path.dirname(_full), "clip.mp4")
    os.rename(_full, video)
    index.upsert_photo("test_creator/clip.mp4")
    index.delete_photo(rel)

    with_videos = index.list_unclassified("test_creator", include_videos=True)
    without = index.list_unclassified("test_creator", include_videos=False)
    assert {os.path.basename(p["rel_path"]) for p in with_videos} == {"a.jpg", "clip.mp4"}
    assert {os.path.basename(p["rel_path"]) for p in without} == {"a.jpg"}
    assert next(p for p in with_videos if p["is_video"])["rel_path"].endswith(".mp4")


def test_pending_skips_index_rows_whose_file_is_gone(index, make_photo):
    _rel, full = make_photo(name="ghost.jpg")
    os.remove(full)
    assert index.list_unclassified("test_creator") == []


def test_pending_refuses_excluded_folders(index):
    assert index.list_unclassified("_trash") == []
    assert index.list_unclassified("_classify") == []
    assert index.list_unclassified("") == []


def test_pending_respects_limit(index, make_photo):
    for i in range(5):
        make_photo(name=f"p{i}.jpg")
    assert len(index.list_unclassified("test_creator", limit=2)) == 2


# ── lifecycle with delete ────────────────────────────────────────────

def test_soft_delete_keeps_the_verdict_so_undo_restores_the_pile(index, make_photo):
    """Trashing 40 rejects and hitting Undo must not require re-running the model."""
    rels = _seed(index, make_photo, [("a.jpg", 0)])
    rel = rels["a.jpg"]
    store = ArchiveStore()
    result = store.delete_photo(rel)
    assert result["trash_id"]
    # Invisible while the photo row is gone: every verdict query joins out from
    # photos, so the orphan cannot leak into a filter or a counter.
    assert index.query_photos(verdict="reject")[1] == 0
    assert index.creator_verdict_counts().get("test_creator", {}).get("reject_count", 0) == 0
    # But still there, so a restore brings the verdict back with the file.
    assert index.get_verdict(rel)["tier"] == 0


def test_permanent_delete_drops_the_verdict(index, make_photo):
    rels = _seed(index, make_photo, [("a.jpg", 0)])
    ArchiveStore().delete_photo(rels["a.jpg"], permanent=True)
    assert index.get_verdict(rels["a.jpg"]) == {}


def test_purging_from_trash_drops_the_verdict(index, make_photo):
    from promptstudio.storage.trash import TrashStore

    rels = _seed(index, make_photo, [("a.jpg", 0)])
    entry = ArchiveStore().delete_photo(rels["a.jpg"])
    assert index.get_verdict(rels["a.jpg"])["tier"] == 0
    assert TrashStore().purge(entry["trash_id"]) is True
    assert index.get_verdict(rels["a.jpg"]) == {}


def test_restore_brings_the_verdict_back(index, make_photo):
    from promptstudio.storage.trash import TrashStore

    rels = _seed(index, make_photo, [("a.jpg", 0)])
    entry = ArchiveStore().delete_photo(rels["a.jpg"])
    TrashStore().restore(entry["trash_id"])
    photos, total = index.query_photos(verdict="reject")
    assert total == 1
    assert photos[0]["verdict"]["tier"] == 0


# ── query composition ────────────────────────────────────────────────

def test_verdict_filter_composes_with_creator_and_media_type(index, make_photo):
    _seed(index, make_photo, [("a.jpg", 0)])
    make_photo(creator="other", name="b.jpg")
    index.set_verdict("other/b.jpg", creator="other", tier=0)

    assert index.query_photos(verdict="reject")[1] == 2
    assert index.query_photos(verdict="reject", creator="test_creator")[1] == 1
    assert index.query_photos(verdict="reject", media_type="video")[1] == 0
    assert index.query_photos(verdict="reject", media_type="photo")[1] == 2


def test_verdict_filter_composes_with_search(index, make_photo):
    """The join makes `creator` ambiguous; search must still resolve to photos."""
    _seed(index, make_photo, [("a.jpg", 0), ("b.jpg", 0)])
    assert index.query_photos(verdict="reject", search="a.jpg")[1] == 1
    assert index.query_photos(verdict="reject", search="test_creator")[1] == 2


def test_verdict_filter_composes_with_favorites(index, make_photo):
    rels = _seed(index, make_photo, [("a.jpg", 0), ("b.jpg", 0)])
    index.set_favorite(rels["a.jpg"], True)
    assert index.query_photos(verdict="reject", favorite_only=True)[1] == 1


def test_tier_sort_puts_the_harshest_first(index, make_photo):
    _seed(index, make_photo, [("t3.jpg", 3), ("t0.jpg", 0), ("err.jpg", -1), ("t1.jpg", 1)])
    make_photo(name="none.jpg")
    photos, _total = index.query_photos(sort="tier")
    order = [os.path.basename(p["rel_path"]) for p in photos]
    # 0, 1, 3, then the error, then never-classified last.
    assert order == ["t0.jpg", "t1.jpg", "t3.jpg", "err.jpg", "none.jpg"]


def test_photo_rows_omit_the_verdict_block_when_unclassified(index, make_photo):
    make_photo(name="a.jpg")
    photos, _total = index.query_photos()
    assert "verdict" not in photos[0]


def test_tier_histogram_counts_every_bucket(index, make_photo):
    _seed(index, make_photo, [("a.jpg", 0), ("b.jpg", 0), ("c.jpg", 4), ("d.jpg", -1)])
    assert index.tier_histogram() == {"-1": 1, "0": 2, "4": 1}


def test_verdicts_for_bulk_fetch_matches_single_reads(index, make_photo):
    rels = _seed(index, make_photo, [("a.jpg", 0), ("b.jpg", 3)])
    bulk = index.verdicts_for(list(rels.values()))
    assert set(bulk) == set(rels.values())
    for rel in rels.values():
        assert bulk[rel]["verdict"] == index.get_verdict(rel)["verdict"]
    assert index.verdicts_for([]) == {}
