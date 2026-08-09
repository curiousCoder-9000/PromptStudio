"""Archive-wide classify (F2).

Classify was per-creator only: `start()` rejected an empty creator and the
panel only existed once a creator was selected, so coverage was capped at
whatever the user remembered to run one folder at a time. Batch analyze has
always been archive-wide; this is the matching scope for the classifier.

The distinction that matters throughout: `creator=""` means *every creator*,
`creator=None` in a status payload means *no job*. Conflating them is how the
UI ends up showing "Classifying @" or a chip for a job that finished.
"""

import time
from unittest.mock import patch

import pytest

from promptstudio.jobs import LEASES
from promptstudio.scraping import media_classifier as mc
from promptstudio.scraping.classify_job import ClassifyJobManager
from promptstudio.storage.db import ArchiveIndex


@pytest.fixture
def manager():
    LEASES.reset()
    yield ClassifyJobManager()
    LEASES.reset()


def wait_until(predicate, timeout=10.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _verdict(tier):
    return mc.MediaVerdict(
        path="x",
        ok=True,
        has_woman=tier > 0,
        exposure_tier=tier,
        confidence=0.8,
        brief_reason=f"tier {tier}",
        prompt_version=mc.CLASSIFY_FRAME_VERSION,
    )


def _seed_two_creators(make_photo):
    make_photo("alpha", "a1.jpg")
    make_photo("alpha", "a2.jpg")
    make_photo("beta", "b1.jpg")
    return 3


def _run(manager, creator="", **kwargs):
    seen = []

    def fake_classify(path, *, rel_path=""):
        seen.append(rel_path)
        return _verdict(len(seen) % 5)

    with patch("promptstudio.scraping.classify_job.ollama_reachable", return_value=True), \
         patch("promptstudio.scraping.classify_job.classify_media", side_effect=fake_classify):
        result = manager.start(creator, **kwargs)
        if result.get("status") == "started":
            assert wait_until(lambda: not manager.is_running()), "job never finished"
    return result, seen


# ── pending selection ────────────────────────────────────────────────


def test_list_pending_with_no_creator_spans_the_archive(manager, make_photo):
    total = _seed_two_creators(make_photo)
    pending = manager.list_pending("")
    assert len(pending) == total
    assert {p["creator"] for p in pending} == {"alpha", "beta"}


def test_list_pending_still_scopes_to_one_creator(manager, make_photo):
    _seed_two_creators(make_photo)
    pending = manager.list_pending("alpha")
    assert {p["creator"] for p in pending} == {"alpha"}


def test_archive_wide_pending_is_grouped_by_creator(manager, make_photo):
    """Ordering matters for the chip's "now on @x" label and for resumability."""
    _seed_two_creators(make_photo)
    creators = [p["creator"] for p in manager.list_pending("")]
    assert creators == sorted(creators)


def test_excluded_folders_never_enter_the_archive_wide_sweep(manager, make_photo):
    """_thumbs / _trash / _classify are not creators, and never get classified."""
    _seed_two_creators(make_photo)
    pending = manager.list_pending("")
    assert all(not p["creator"].startswith("_") for p in pending)


# ── start() ──────────────────────────────────────────────────────────


def test_start_with_empty_creator_classifies_everything(manager, make_photo):
    total = _seed_two_creators(make_photo)
    result, seen = _run(manager, "")
    assert result["status"] == "started"
    assert result["pending"] == total
    assert len(seen) == total


def test_verdicts_are_written_for_every_creator(manager, make_photo):
    _seed_two_creators(make_photo)
    _run(manager, "")
    counts = ArchiveIndex.get().creator_verdict_counts()
    assert counts["alpha"]["unclassified_count"] == 0
    assert counts["beta"]["unclassified_count"] == 0


def test_status_creator_is_empty_string_not_none_for_archive_runs(manager, make_photo):
    """'' is a scope; None is "no job". The chip renders them differently."""
    _seed_two_creators(make_photo)
    _run(manager, "")
    assert manager.get_status()["creator"] == ""


def test_start_rejects_an_excluded_folder_as_a_creator(manager, make_photo):
    _seed_two_creators(make_photo)
    result = manager.start("_trash")
    assert result["status"] == "bad_creator"


def test_nothing_to_do_when_the_whole_archive_is_classified(manager, make_photo):
    _seed_two_creators(make_photo)
    _run(manager, "")
    result, _seen = _run(manager, "")
    assert result["status"] == "nothing_to_do"


def test_limit_still_applies_archive_wide(manager, make_photo):
    _seed_two_creators(make_photo)
    result, seen = _run(manager, "", limit=2)
    assert result["pending"] == 2
    assert len(seen) == 2


def test_current_creator_is_reported_during_the_run(manager, make_photo):
    """Archive-wide progress needs to say where it is, not just N of M."""
    _seed_two_creators(make_photo)
    _run(manager, "")
    # After the run the last item's creator is still the last one visited.
    assert manager.get_status()["current_creator"] in {"alpha", "beta"}


def test_archive_run_counts_add_up(manager, make_photo):
    total = _seed_two_creators(make_photo)
    _run(manager, "")
    status = manager.get_status()
    assert status["completed"] == total
    assert status["kept"] + status["rejected"] + status["failed"] == total
