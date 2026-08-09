"""Reel classifier eval harness.

The harness is what makes "did that change help" answerable, so its own
arithmetic has to be right — a wrong metric is worse than no metric, because it
gets believed. These cover sampling, the label round trip, and every number the
report prints.

No Ollama: `score_item` is the only part that touches it and is excluded here.
"""

import json

import pytest

from promptstudio.evalset import (
    TARGETS,
    EvalItem,
    EvalResult,
    choose_sample,
    compute_metrics,
    load_labels,
    meets_target,
    merge_labels,
    render_label_page,
    save_labels,
    stratify,
)


def _rows(**counts):
    """Build candidate rows with a given count per glam score."""
    out, n = [], 0
    for score, how_many in counts.items():
        for _ in range(how_many):
            out.append({"rel_path": f"c/{n}.mp4", "glam_score": int(score)})
            n += 1
    return out


# ── stratification ───────────────────────────────────────────────────


def test_buckets_by_current_score():
    buckets = stratify(_rows(**{"-1": 2, "0": 1, "1": 1, "2": 3, "3": 4}))
    assert len(buckets["unscored"]) == 2
    assert len(buckets["low"]) == 2  # 0 and 1 collapse
    assert len(buckets["mid"]) == 3
    assert len(buckets["high"]) == 4


def test_rows_without_a_path_are_dropped():
    assert stratify([{"rel_path": "", "glam_score": 3}])["high"] == []


def test_missing_score_counts_as_unscored():
    assert len(stratify([{"rel_path": "a.mp4"}])["unscored"]) == 1


# ── sampling ─────────────────────────────────────────────────────────


def test_sample_is_evenly_split_across_strata():
    buckets = stratify(_rows(**{"-1": 50, "0": 50, "2": 50, "3": 50}))
    picked = choose_sample(buckets, total=120)
    per = {}
    for _, stratum in picked:
        per[stratum] = per.get(stratum, 0) + 1
    assert len(picked) == 120
    assert set(per.values()) == {30}


def test_a_thin_stratum_gives_its_slack_back():
    """120 requested must still yield 120 when one bucket is nearly empty."""
    buckets = stratify(_rows(**{"-1": 2, "0": 100, "2": 100, "3": 100}))
    picked = choose_sample(buckets, total=120)
    assert len(picked) == 120


def test_sample_cannot_exceed_what_exists():
    buckets = stratify(_rows(**{"3": 5}))
    assert len(choose_sample(buckets, total=120)) == 5


def test_same_seed_gives_the_same_sample():
    buckets = stratify(_rows(**{"-1": 40, "3": 40}))
    assert choose_sample(buckets, 20, seed=7) == choose_sample(buckets, 20, seed=7)


def test_different_seed_gives_a_different_sample():
    buckets = stratify(_rows(**{"-1": 40, "3": 40}))
    assert choose_sample(buckets, 20, seed=7) != choose_sample(buckets, 20, seed=8)


def test_no_duplicate_paths_in_a_sample():
    buckets = stratify(_rows(**{"-1": 30, "0": 30, "2": 30, "3": 30}))
    picked = [p for p, _ in choose_sample(buckets, total=120)]
    assert len(picked) == len(set(picked))


def test_empty_archive_samples_nothing():
    assert choose_sample({}, total=120) == []


# ── labels round trip ────────────────────────────────────────────────


def test_labels_survive_a_save_load_cycle(tmp_path):
    path = str(tmp_path / "labels.jsonl")
    items = [
        EvalItem(rel_path="c/a.mp4", true_exposure=4, reveal_at_end=True, note="bikini"),
        EvalItem(rel_path="c/b.mp4"),
    ]
    save_labels(items, path)
    back = load_labels(path)
    assert [i.rel_path for i in back] == ["c/a.mp4", "c/b.mp4"]
    assert back[0].true_exposure == 4
    assert back[0].reveal_at_end is True
    assert back[1].is_labelled() is False


def test_corrupt_line_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text(
        json.dumps({"rel_path": "c/a.mp4", "true_exposure": 3}) + "\n"
        "{ truncated mid-write\n"
        + json.dumps({"rel_path": "c/b.mp4", "true_exposure": 1}) + "\n"
    )
    assert [i.rel_path for i in load_labels(str(path))] == ["c/a.mp4", "c/b.mp4"]


def test_unknown_fields_are_ignored(tmp_path):
    """A newer export must not crash an older harness."""
    path = tmp_path / "labels.jsonl"
    path.write_text(json.dumps({"rel_path": "c/a.mp4", "true_exposure": 2, "future": 1}) + "\n")
    assert load_labels(str(path))[0].true_exposure == 2


def test_merge_keeps_existing_labels_when_the_import_has_none():
    """Re-running `sample` must never blank a reel someone already judged."""
    existing = [EvalItem(rel_path="c/a.mp4", true_exposure=4, reveal_at_end=True)]
    incoming = [EvalItem(rel_path="c/a.mp4", sheet="c__a.mp4.sheet.jpg")]
    merged = merge_labels(existing, incoming)
    assert merged[0].true_exposure == 4
    assert merged[0].sheet == "c__a.mp4.sheet.jpg"


def test_merge_applies_a_new_label():
    existing = [EvalItem(rel_path="c/a.mp4")]
    incoming = [EvalItem(rel_path="c/a.mp4", true_exposure=1, reveal_at_end=False)]
    assert merge_labels(existing, incoming)[0].true_exposure == 1


def test_merge_adds_unseen_items():
    merged = merge_labels([EvalItem(rel_path="c/a.mp4")], [EvalItem(rel_path="c/b.mp4")])
    assert [i.rel_path for i in merged] == ["c/a.mp4", "c/b.mp4"]


def test_merge_overwrites_an_earlier_judgement():
    existing = [EvalItem(rel_path="c/a.mp4", true_exposure=1)]
    incoming = [EvalItem(rel_path="c/a.mp4", true_exposure=4)]
    assert merge_labels(existing, incoming)[0].true_exposure == 4


# ── metrics ──────────────────────────────────────────────────────────


def _pair(path, true_tier, pred_tier, *, reveal=False, ok=True, calls=1, glam=None):
    item = EvalItem(rel_path=path, true_exposure=true_tier, reveal_at_end=reveal)
    tier_to_glam = {0: 0, 1: 0, 2: 1, 3: 2, 4: 3}
    result = EvalResult(
        rel_path=path,
        ok=ok,
        predicted_tier=pred_tier,
        glam_score=tier_to_glam.get(pred_tier, -1) if glam is None else glam,
        vision_calls=calls,
        ms=100,
    )
    return item, result


def _metrics(*pairs):
    return compute_metrics([p[0] for p in pairs], [p[1] for p in pairs])


def test_perfect_prediction():
    m = _metrics(_pair("a", 4, 4), _pair("b", 1, 1))
    assert m.exact_accuracy == 1.0
    assert m.within_one == 1.0
    assert m.mean_signed_error == 0.0


def test_off_by_one_counts_for_within_one_only():
    m = _metrics(_pair("a", 3, 4), _pair("b", 1, 1))
    assert m.exact_accuracy == 0.5
    assert m.within_one == 1.0


def test_signed_error_shows_the_direction_of_the_bias():
    over = _metrics(_pair("a", 1, 4), _pair("b", 1, 3))
    under = _metrics(_pair("a", 4, 1), _pair("b", 3, 1))
    assert over.mean_signed_error > 0, "positive = over-scoring"
    assert under.mean_signed_error < 0


def test_reveal_recall_only_counts_reveal_reels():
    m = _metrics(
        _pair("a", 4, 4, reveal=True),
        _pair("b", 4, 1, reveal=True),
        _pair("c", 4, 1),  # not a reveal — must not drag recall down
    )
    assert m.reveal_n == 2
    assert m.reveal_recall == 0.5


def test_reveal_recall_accepts_over_scoring():
    """Recall asks 'did it reach the peak outfit', not 'was it exact'."""
    m = _metrics(_pair("a", 3, 4, reveal=True))
    assert m.reveal_recall == 1.0


def test_reveal_recall_is_zero_when_the_reveal_is_missed():
    m = _metrics(_pair("a", 4, 1, reveal=True), _pair("b", 4, 0, reveal=True))
    assert m.reveal_recall == 0.0


def test_no_reveal_reels_leaves_recall_at_zero_with_n_zero():
    m = _metrics(_pair("a", 2, 2))
    assert (m.reveal_n, m.reveal_recall) == (0, 0.0)


def test_top_score_share_catches_a_collapsed_distribution():
    """The v2 failure: 85% of everything scored glam 3."""
    m = _metrics(*[_pair(f"p{i}", 4, 4) for i in range(9)], _pair("x", 1, 1))
    assert m.top_score_share == 0.9
    assert meets_target("top_score_share", m.top_score_share) is False


def test_a_spread_distribution_passes():
    m = _metrics(_pair("a", 0, 0), _pair("b", 2, 2), _pair("c", 3, 3), _pair("d", 4, 4))
    assert m.top_score_share <= 0.6


def test_unscored_rate_counts_failures():
    m = _metrics(_pair("a", 4, 4), _pair("b", 4, -1, ok=False))
    assert m.unscored_rate == 0.5


def test_failures_are_excluded_from_accuracy():
    """A failed score is not a wrong answer; it is an absent one."""
    m = _metrics(_pair("a", 4, 4), _pair("b", 1, -1, ok=False))
    assert m.exact_accuracy == 1.0
    assert m.scored == 2


def test_median_vision_calls_includes_failures():
    m = _metrics(_pair("a", 4, 4, calls=1), _pair("b", 4, 4, calls=3))
    assert m.median_vision_calls == 2.0


def test_unlabelled_items_are_ignored():
    labels = [EvalItem(rel_path="a", true_exposure=4), EvalItem(rel_path="b")]
    results = [EvalResult(rel_path="a", ok=True, predicted_tier=4, glam_score=3)]
    m = compute_metrics(labels, results)
    assert (m.labelled, m.scored) == (1, 1)


def test_a_label_with_no_result_is_not_scored():
    labels = [EvalItem(rel_path="a", true_exposure=4), EvalItem(rel_path="b", true_exposure=1)]
    results = [EvalResult(rel_path="a", ok=True, predicted_tier=4, glam_score=3)]
    m = compute_metrics(labels, results)
    assert (m.labelled, m.scored) == (2, 1)


def test_empty_inputs_do_not_divide_by_zero():
    m = compute_metrics([], [])
    assert (m.scored, m.exact_accuracy, m.unscored_rate) == (0, 0.0, 0.0)


def test_confusion_matrix_shows_where_it_goes_wrong():
    m = _metrics(_pair("a", 4, 1), _pair("b", 4, 1), _pair("c", 1, 1))
    assert m.confusion["4->1"] == 2
    assert m.confusion["1->1"] == 1


# ── targets ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,value,expected",
    [
        ("reveal_recall", 0.9, True),
        ("reveal_recall", 0.8, False),
        ("exact_accuracy", 0.75, True),
        ("top_score_share", 0.6, True),
        ("top_score_share", 0.61, False),
        ("unscored_rate", 0.0, True),
        ("median_vision_calls", 3.0, False),
    ],
)
def test_targets_match_the_design_doc(name, value, expected):
    assert meets_target(name, value) is expected


def test_unknown_metric_has_no_target():
    assert meets_target("median_ms", 1.0) is None


def test_every_target_is_reported():
    """A target nothing prints is a target nobody checks."""
    reported = {
        "reveal_recall", "exact_accuracy", "within_one",
        "top_score_share", "unscored_rate", "median_vision_calls",
    }
    assert set(TARGETS) == reported


# ── labelling page ───────────────────────────────────────────────────


def test_label_page_embeds_the_items(tmp_path):
    out = str(tmp_path / "label.html")
    render_label_page(
        [EvalItem(rel_path="c/a.mp4", sheet="c__a.mp4.sheet.jpg", stratum="high", prior_glam=3)],
        out,
    )
    html = open(out, encoding="utf-8").read()
    assert "c/a.mp4" in html
    assert "c__a.mp4.sheet.jpg" in html
    assert "__ITEMS__" not in html, "template placeholder left unfilled"
    assert "__ANCHORS__" not in html
    assert "__LABELS__" not in html


def test_label_page_is_self_contained(tmp_path):
    """Opened over file:// on whatever machine holds the archive."""
    out = str(tmp_path / "label.html")
    render_label_page([EvalItem(rel_path="c/a.mp4", sheet="s.jpg")], out)
    html = open(out, encoding="utf-8").read()
    assert "<script" in html and "src=\"http" not in html


def test_label_page_survives_a_quote_in_a_path(tmp_path):
    out = str(tmp_path / "label.html")
    render_label_page([EvalItem(rel_path='c/we"ird.mp4', sheet="s.jpg")], out)
    html = open(out, encoding="utf-8").read()
    assert '\\"' in html or "we\\u0022ird" in html
