"""Glam verdicts in queryable columns, not just a collapsed int.

`set_glam_score` used to accept has_woman/sexy and silently drop them: only the
0-3 score reached SQLite, and everything else lived in per-file sidecars. That
made three routine operations require walking the archive and parsing thousands
of JSON files:

* "which files did the *old* prompt judge?" — nothing recorded the version, so
  adopting a better prompt meant --force over everything;
* "which ones failed transiently?" — an error was indistinguishable from a file
  never attempted, both being glam_score = -1;
* "sort by confidence" — impossible.

These cover the columns, the write path, and the two queries they exist for.
"""

import pytest

from promptstudio.scraping import outfit_classifier as oc
from promptstudio.storage.db import ArchiveIndex


@pytest.fixture
def index():
    return ArchiveIndex.get()


def _verdict(path, **kw):
    v = oc.PostVerdict(path=path, ok=True, **kw)
    v.glam_score = v.compute_glam_score()
    return v


# ── the flags actually land ──────────────────────────────────────────


def test_set_glam_score_persists_the_whole_verdict(index, make_photo):
    rel, _full = make_photo("alice", "a.jpg")

    index.set_glam_score(
        rel, 3, has_woman=1, sexy=1, confidence=0.82, tier=4,
        prompt_version="v4-reel-sheet",
    )

    got = index.get_glam_verdict(rel)
    assert got["glam_score"] == 3
    assert got["has_woman"] is True
    assert got["sexy"] is True
    assert got["confidence"] == pytest.approx(0.82)
    assert got["tier"] == 4
    assert got["prompt_version"] == "v4-reel-sheet"
    assert got["error"] is None
    assert got["scored_at"]


def test_flags_survive_a_rescore(index, make_photo):
    rel, _full = make_photo("alice", "a.jpg")
    index.set_glam_score(rel, 3, has_woman=1, sexy=1, tier=4, prompt_version="old")

    index.set_glam_score(rel, 1, has_woman=1, sexy=0, tier=2, prompt_version="new")

    got = index.get_glam_verdict(rel)
    assert (got["glam_score"], got["tier"], got["sexy"]) == (1, 2, False)
    assert got["prompt_version"] == "new"


def test_verdict_of_an_unknown_file_is_empty(index):
    assert index.get_glam_verdict("nobody/none.jpg") == {}


def test_persist_glam_score_writes_the_columns(index, make_photo):
    rel, full = make_photo("alice", "a.jpg")
    verdict = _verdict(full, has_woman=True, exposure_tier=4, confidence=0.9)
    verdict.prompt_version = oc.CLASSIFY_SHEET_VERSION

    oc.persist_glam_score(rel, verdict, full_path=full)

    got = index.get_glam_verdict(rel)
    assert got["glam_score"] == 3
    assert got["tier"] == 4
    assert got["prompt_version"] == oc.CLASSIFY_SHEET_VERSION
    assert got["error"] is None


# ── error taxonomy ───────────────────────────────────────────────────


def test_failed_classify_records_why(index, make_photo):
    rel, full = make_photo("alice", "a.jpg")
    failed = oc.PostVerdict(path=full, error="HTTP Error 500: timeout")

    oc.persist_glam_score(rel, failed, full_path=full)

    got = index.get_glam_verdict(rel)
    assert got["glam_score"] == -1, "a failure must not invent a score"
    assert got["error"] == "HTTP Error 500: timeout"
    assert index.list_glam_errors() == [
        {"rel_path": rel, "error": "HTTP Error 500: timeout",
         "scored_at": got["scored_at"]}
    ]


def test_never_attempted_is_not_an_error(index, make_photo):
    rel, _full = make_photo("alice", "a.jpg")
    assert index.get_glam_score(rel) == -1
    assert index.list_glam_errors() == [], "unscored != failed"


def test_a_later_success_clears_the_error(index, make_photo):
    rel, full = make_photo("alice", "a.jpg")
    oc.persist_glam_score(rel, oc.PostVerdict(path=full, error="boom"), full_path=full)
    assert index.list_glam_errors()

    oc.persist_glam_score(
        rel, _verdict(full, has_woman=True, exposure_tier=3, confidence=0.7),
        full_path=full,
    )

    assert index.list_glam_errors() == []
    assert index.get_glam_verdict(rel)["glam_score"] == 2


def test_failed_retry_does_not_erase_a_good_sidecar_score(make_photo):
    from promptstudio.storage.metadata import load_post_metadata

    rel, full = make_photo("alice", "a.jpg")
    oc.persist_glam_score(
        rel, _verdict(full, has_woman=True, exposure_tier=4, confidence=0.9),
        full_path=full,
    )

    oc.persist_glam_score(rel, oc.PostVerdict(path=full, error="boom"), full_path=full)

    assert (load_post_metadata(full) or {}).get("glam_score") == 3


def test_errors_can_be_scoped_to_one_creator(index, make_photo):
    a_rel, a_full = make_photo("alice", "a.jpg")
    b_rel, b_full = make_photo("bob", "b.jpg")
    oc.persist_glam_score(a_rel, oc.PostVerdict(path=a_full, error="x"), full_path=a_full)
    oc.persist_glam_score(b_rel, oc.PostVerdict(path=b_full, error="y"), full_path=b_full)

    assert [r["rel_path"] for r in index.list_glam_errors(creator="alice")] == [a_rel]


# ── staleness by prompt version ──────────────────────────────────────


def test_stale_lists_only_outdated_versions(index, make_photo):
    old, _ = make_photo("alice", "old.jpg")
    new, _ = make_photo("alice", "new.jpg")
    index.set_glam_score(old, 2, prompt_version="v2-skin-exposure")
    index.set_glam_score(new, 2, prompt_version="v4-reel-sheet")

    stale = index.list_stale_glam(["v4-reel-sheet"])

    assert stale == [old]


def test_rows_scored_before_versioning_count_as_stale(index, make_photo):
    rel, _full = make_photo("alice", "a.jpg")
    index.set_glam_score(rel, 2)  # no prompt_version — pre-migration row

    assert index.list_stale_glam(["v4-reel-sheet"]) == [rel]


def test_unscored_files_are_not_stale(index, make_photo):
    rel, _full = make_photo("alice", "a.jpg")

    assert index.list_stale_glam(["v4-reel-sheet"]) == []
    assert index.get_glam_score(rel) == -1


def test_no_current_versions_means_nothing_is_stale(index, make_photo):
    rel, _full = make_photo("alice", "a.jpg")
    index.set_glam_score(rel, 2, prompt_version="whatever")

    assert index.list_stale_glam([]) == []


def test_active_versions_cover_what_a_fresh_classify_writes():
    active = oc.active_prompt_versions()

    assert oc.current_prompt_version("x.jpg") in active
    assert oc.current_prompt_version("x.mp4") in active
    if oc.CLASSIFY_REEL_SHEET:
        # A reel can legitimately end up tagged with the confirm/fallback
        # version; treating that as stale would re-run it forever.
        assert oc.CLASSIFY_FRAME_V4_VERSION in active


def test_stale_rows_are_picked_up_for_rescore(index, make_photo):
    from promptstudio.scraping.classify_job import ClassifyJobManager

    rel, _full = make_photo("alice", "a.jpg")
    index.set_glam_score(rel, 2, prompt_version="v1-ancient")
    mgr = ClassifyJobManager()

    assert mgr.list_pending("alice") == [], "already scored — nothing to do"
    stale = mgr.list_pending("alice", rescore_stale=True)
    assert [p["rel_path"] for p in stale] == [rel]


def test_creator_list_reports_stale_count(index, make_photo):
    """The "Re-score outdated (N)" button reads this; /api/classify/status's
    `stale` is scoped to the running job's creator and cannot drive it."""
    old, _ = make_photo("alice", "old.jpg")
    cur, _ = make_photo("alice", "cur.jpg")
    make_photo("alice", "never.jpg")  # unscored — not stale
    make_photo("bob", "b.jpg")
    index.set_glam_score(old, 2, prompt_version="v1-ancient")
    index.set_glam_score(cur, 2, prompt_version=oc.current_prompt_version("cur.jpg"))

    by_name = {c["name"]: c for c in index.list_creators()}

    assert by_name["alice"]["stale_count"] == 1
    assert by_name["bob"]["stale_count"] == 0


def test_start_forwards_rescore_stale(monkeypatch, index, make_photo):
    """The API passes this straight through; start() must not drop it."""
    from promptstudio.scraping import classify_job as cj

    rel, _full = make_photo("alice", "a.jpg")
    index.set_glam_score(rel, 2, prompt_version="v1-ancient")
    monkeypatch.setattr(cj, "ollama_reachable", lambda: True)

    seen = {}
    mgr = cj.ClassifyJobManager()
    # Return nothing so start() short-circuits before spawning the worker.
    mgr.list_pending = lambda creator, **kw: seen.update(kw) or []

    result = mgr.start("alice", rescore_stale=True)

    assert seen["rescore_stale"] is True
    assert result["status"] == "nothing_to_do"


def test_start_does_not_rescore_by_default(monkeypatch, make_photo):
    from promptstudio.scraping import classify_job as cj

    monkeypatch.setattr(cj, "ollama_reachable", lambda: True)

    seen = {}
    mgr = cj.ClassifyJobManager()
    mgr.list_pending = lambda creator, **kw: seen.update(kw) or []
    mgr.start("alice")

    assert seen["rescore_stale"] is False


def test_current_version_rows_are_left_alone(index, make_photo):
    from promptstudio.scraping.classify_job import ClassifyJobManager

    rel, _full = make_photo("alice", "a.jpg")
    index.set_glam_score(rel, 2, prompt_version=oc.current_prompt_version("a.jpg"))

    assert ClassifyJobManager().list_pending("alice", rescore_stale=True) == []
