"""Glam score distribution tracking on the classify job.

The v2 prompt shipped emitting glam=3 for 85% of scored videos and nobody
noticed, because the job only ever reported kept/rejected counts — and with the
generous prompt almost everything was "kept". These tests cover the histogram
that makes a collapsed distribution visible per run.

No Ollama here: _record_score is pure bookkeeping.
"""

from promptstudio.scraping.classify_job import ClassifyJobManager


def make_manager(**status):
    """A fresh manager (bypassing the singleton) with a running-job status."""
    mgr = ClassifyJobManager()
    mgr._status = mgr._idle_status()
    mgr._status.update(status)
    return mgr


def record_all(mgr, scores):
    for score in scores:
        mgr._record_score(score)
    return mgr.get_status()


def test_starts_empty():
    status = ClassifyJobManager()._idle_status()
    assert status["score_hist"] == {"-1": 0, "0": 0, "1": 0, "2": 0, "3": 0}
    assert status["top_score_share"] == 0.0
    assert status["unscored_rate"] == 0.0


def test_buckets_each_score():
    status = record_all(make_manager(), [0, 1, 2, 3, 3])
    assert status["score_hist"] == {"-1": 0, "0": 1, "1": 1, "2": 1, "3": 2}


def test_top_score_share_flags_a_collapsed_distribution():
    # The real v2 archive shape: 284/21/24/4 across g3/g2/g1/g0.
    scores = [3] * 284 + [2] * 21 + [1] * 24 + [0] * 4
    status = record_all(make_manager(), scores)
    assert status["top_score_share"] == 0.8529
    # P4's distribution guard fires above 0.60 — this run would trip it.
    assert status["top_score_share"] > 0.60


def test_top_score_share_ignores_the_error_bucket():
    # Errors are a reliability signal, not a score: 2 of 4 *scored* items are 3.
    status = record_all(make_manager(), [3, 3, 2, 1, -1, -1, -1])
    assert status["score_hist"]["-1"] == 3
    assert status["top_score_share"] == 0.5


def test_unscored_rate_counts_errors_against_everything_attempted():
    status = record_all(make_manager(), [3, 2, -1, -1])
    assert status["unscored_rate"] == 0.5


def test_unknown_scores_land_in_the_error_bucket():
    status = record_all(make_manager(), [7, -5, 3])
    assert status["score_hist"]["-1"] == 2
    assert status["score_hist"]["3"] == 1


def test_get_status_deep_copies_the_histogram():
    mgr = make_manager()
    snapshot = mgr.get_status()
    mgr._record_score(3)
    assert snapshot["score_hist"]["3"] == 0, "caller saw the live dict mutate"
    assert mgr.get_status()["score_hist"]["3"] == 1


def test_start_resets_the_histogram_between_runs():
    mgr = make_manager()
    record_all(mgr, [3, 3, 3])
    mgr._status = mgr._idle_status()
    assert mgr.get_status()["score_hist"]["3"] == 0
