"""E5a — the distribution guard, as a failing check rather than a banner.

The previous classifier shipped with 85% of the archive on one tier and the
Sexy filter admitting ~92%, and three prompt versions went out before anyone
noticed. A filter that selects almost everything carries no information.

Since Stage 1 the insights panel renders a warning above 0.6, but it is
advisory — and the person who needs to see a warning banner is exactly the
person who stopped opening that panel. This module makes it fail.

It is a **platform** rule, not a classifier one-off (product_review.md B4), so
the same check runs over taste labels and P(keep) scores.

Three layers, because a guard that can only pass is not a guard:

1. `saturation_report` — the rule itself. Pure, fast, always runs.
2. The proof: a saturated fixture, asserted to be *caught* — both as a plain
   distribution and end-to-end through `tier_histogram()`, the aggregates the
   app actually serves.
3. The gate over the real archive, skipped below a minimum N so it is inert in
   CI (which has no archive) and on a fresh checkout.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Dict

import pytest

from promptstudio.config import (
    ARCHIVE_DB_FILE,
    DISTRIBUTION_MAX_SHARE,
    DISTRIBUTION_MIN_CLASSIFIED,
    DISTRIBUTION_MIN_RATED,
    archive_db_file,
    resolve_archive_dir,
)
from promptstudio.insights import saturation_report
from promptstudio.storage.db import ArchiveIndex

# ── reading the archive the developer actually has ───────────────────
#
# `conftest` points every other test at a temp archive; saturation is a
# property of real data, so the gate needs the real one. conftest stashes the
# pre-override path here before it clobbers PROMPTSTUDIO_ARCHIVE. Set it
# yourself to aim the gate somewhere else.
GUARD_ARCHIVE = resolve_archive_dir(os.environ.get("PROMPTSTUDIO_GUARD_ARCHIVE"))
GUARD_DB = archive_db_file(GUARD_ARCHIVE)

# Classified means scored: tier -1 is a failed vision call, which `error_rate`
# reports separately and which must not dilute the distribution.
TIER_SQL = "SELECT tier, COUNT(*) FROM media_verdicts WHERE tier >= 0 GROUP BY tier"
LABEL_SQL = "SELECT label, COUNT(*) FROM labels GROUP BY label"
PKEEP_SQL = (
    "SELECT CASE "
    "WHEN p_keep >= 0.75 THEN 'high' "
    "WHEN p_keep >= 0.5 THEN 'mid' "
    "WHEN p_keep >= 0.25 THEN 'low' "
    "ELSE 'drop' END, COUNT(*) FROM photos WHERE p_keep IS NOT NULL GROUP BY 1"
)

LABEL_NAMES = {"-1": "discard", "1": "keep"}


def _read_only_counts(db_path: str, sql: str) -> Dict[str, int]:
    """`{bucket: count}` from a database this process must not touch.

    `ArchiveIndex` is the obvious reader and the wrong one: its constructor
    creates tables and runs migrations. A test that reaches for the
    developer's real archive must not be able to write to it, so this opens
    `mode=ro` and nothing else.

    A missing file or a pre-classifier schema is "nothing measured", not a
    failure — the caller turns that into a skip.
    """
    if not os.path.isfile(db_path):
        return {}
    try:
        conn = sqlite3.connect(f"{Path(db_path).as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        return {str(bucket): int(n) for bucket, n in conn.execute(sql)}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def assert_not_saturated(counts: Dict[str, int], *, what: str, min_n: int) -> None:
    """The gate body. Skips below `min_n`, fails above the share limit.

    Shared by the real-archive gates and by the tests that prove they bite,
    so what is proven is the code that runs.
    """
    report = saturation_report(counts, what=what, min_n=min_n)
    if not report["measured"]:
        pytest.skip(report["message"])
    assert not report["saturated"], report["message"]


def tier_buckets(db_path: str) -> Dict[str, int]:
    return {f"tier {t}": n for t, n in _read_only_counts(db_path, TIER_SQL).items()}


def label_buckets(db_path: str) -> Dict[str, int]:
    return {
        LABEL_NAMES.get(r, f"label {r}"): n
        for r, n in _read_only_counts(db_path, LABEL_SQL).items()
    }


def p_keep_buckets(db_path: str) -> Dict[str, int]:
    return _read_only_counts(db_path, PKEEP_SQL)


# ── 1. the rule ──────────────────────────────────────────────────────


def test_a_spread_distribution_is_not_saturated():
    report = saturation_report(
        {"tier 0": 20, "tier 1": 25, "tier 2": 30, "tier 3": 25}, what="tier", min_n=10
    )
    assert report["measured"] is True
    assert report["saturated"] is False
    assert report["n"] == 100
    assert report["top_bucket"] == "tier 2"
    assert report["top_share"] == 0.3


def test_one_bucket_over_the_limit_is_saturated():
    report = saturation_report(
        {"tier 3": 85, "tier 0": 5, "tier 1": 5, "tier 2": 5}, what="tier", min_n=10
    )
    assert report["saturated"] is True
    assert report["top_bucket"] == "tier 3"
    assert report["top_share"] == 0.85


def test_the_failure_message_names_the_bucket_and_the_share():
    """CI output has to say what to look at, not just that something is wrong."""
    report = saturation_report({"tier 3": 85, "tier 0": 15}, what="tier", min_n=10)
    assert "tier 3" in report["message"]
    assert "85" in report["message"]
    assert "60" in report["message"]
    assert "100" in report["message"]  # the denominator it was measured over


def test_exactly_at_the_limit_is_not_saturated():
    """The rule is "exceeds 60%", so 60.0% passes. A boundary that is a coin
    flip makes the gate flap between runs as one item lands either side."""
    report = saturation_report({"a": 60, "b": 40}, what="tier", min_n=10)
    assert report["top_share"] == 0.6
    assert report["saturated"] is False


def test_below_the_minimum_nothing_is_judged():
    report = saturation_report({"tier 3": 9}, what="tier", min_n=10)
    assert report["measured"] is False
    assert report["saturated"] is False
    assert "9" in report["message"] and "10" in report["message"]


def test_an_empty_distribution_is_not_judged():
    report = saturation_report({}, what="tier", min_n=1)
    assert report["measured"] is False
    assert report["top_bucket"] is None
    assert report["top_share"] is None


def test_a_single_bucket_is_saturated_by_definition():
    """One value for everything is the exact failure this exists to catch."""
    report = saturation_report({"tier 3": 40}, what="tier", min_n=10)
    assert report["saturated"] is True
    assert report["top_share"] == 1.0


def test_the_threshold_comes_from_config():
    assert saturation_report({"a": 1}, what="tier", min_n=1)["threshold"] == (
        DISTRIBUTION_MAX_SHARE
    )


# ── 2. the gate bites ────────────────────────────────────────────────


def test_the_gate_fails_on_a_saturated_distribution():
    """A guard that can only pass is not a guard. Run the real gate body over
    a saturated fixture and require it to raise, naming the bucket."""
    with pytest.raises(AssertionError) as caught:
        assert_not_saturated({"tier 3": 85, "tier 1": 15}, what="tier", min_n=10)
    assert "tier 3" in str(caught.value)
    assert "85" in str(caught.value)


def test_the_gate_skips_rather_than_passing_when_there_is_too_little_data():
    """Silently passing on 3 items would read as "the archive is healthy"."""
    with pytest.raises(pytest.skip.Exception):
        assert_not_saturated({"tier 3": 3}, what="tier", min_n=10)


def test_the_gate_passes_a_healthy_distribution():
    assert_not_saturated({"a": 30, "b": 35, "c": 35}, what="tier", min_n=10)


# ── 3. plumbed through the aggregates the app really serves ──────────


def _seed_tiers(make_photo, tiers, creator="tester"):
    index = ArchiveIndex.get()
    for i, tier in enumerate(tiers):
        rel, _ = make_photo(creator=creator, name=f"g_{i:03d}.jpg")
        index.set_verdict(rel, creator=creator, tier=tier)
    return index


def test_a_saturated_archive_is_caught_through_the_tier_histogram(make_photo):
    """Not the pure function this time — the real query path.

    85% on one tier is the number the previous classifier actually shipped.
    """
    index = _seed_tiers(make_photo, [3] * 17 + [0, 1, 4])
    counts = {
        f"tier {t}": n for t, n in index.tier_histogram().items() if int(t) >= 0
    }

    with pytest.raises(AssertionError) as caught:
        assert_not_saturated(counts, what="tier", min_n=10)
    assert "tier 3" in str(caught.value)


def test_a_spread_archive_passes_through_the_tier_histogram(make_photo):
    index = _seed_tiers(make_photo, [0, 1, 2, 3, 4] * 4)
    counts = {
        f"tier {t}": n for t, n in index.tier_histogram().items() if int(t) >= 0
    }
    assert_not_saturated(counts, what="tier", min_n=10)


def test_failed_vision_calls_do_not_dilute_the_distribution(make_photo):
    """tier -1 is a retryable error, not a judgement. Counting it as a bucket
    would let a broken Ollama hide a saturated classifier."""
    index = _seed_tiers(make_photo, [3] * 12 + [-1] * 8)
    counts = {
        f"tier {t}": n for t, n in index.tier_histogram().items() if int(t) >= 0
    }

    assert counts == {"tier 3": 12}
    report = saturation_report(counts, what="tier", min_n=10)
    assert report["n"] == 12
    assert report["saturated"] is True


def test_the_guards_own_sql_agrees_with_the_index_aggregates(make_photo):
    """The gate reads the DB read-only rather than through `ArchiveIndex`
    (whose constructor writes). Pin the two definitions together so the guard
    cannot quietly start measuring something else."""
    index = _seed_tiers(make_photo, [0, 0, 1, 2, 3, 3, 4, -1])

    tiers = tier_buckets(ARCHIVE_DB_FILE)
    assert tiers, "read-only reader found nothing — check the WAL/ro open"
    assert tiers == {
        f"tier {t}": n for t, n in index.tier_histogram().items() if int(t) >= 0
    }


# ── 4. the gate, over the real archive ───────────────────────────────
#
# These are the two that fail a local run when the distribution saturates.
# They skip in CI and on a fresh checkout, where GUARD_DB does not exist.


def test_archive_tier_distribution_is_not_saturated():
    assert_not_saturated(
        tier_buckets(GUARD_DB),
        what=f"classified tier ({GUARD_DB})",
        min_n=DISTRIBUTION_MIN_CLASSIFIED,
    )


def test_taste_labels_are_judged_over_the_labelled_denominator(make_photo):
    """Unlabeled photos are not a bucket. Counting them would fire on every
    fresh archive, which is how a guard gets switched off."""
    index = ArchiveIndex.get()
    for i in range(12):
        rel, _ = make_photo(name=f"want_{i}.jpg")
        index.set_label(rel, 1)
    for i in range(8):
        make_photo(name=f"unseen_{i}.jpg")

    counts = label_buckets(ARCHIVE_DB_FILE)
    assert counts == {"keep": 12}, counts
    with pytest.raises(AssertionError) as caught:
        assert_not_saturated(counts, what="taste label", min_n=10)
    assert "keep" in str(caught.value)


def test_archive_taste_label_distribution_is_not_saturated():
    assert_not_saturated(
        label_buckets(GUARD_DB),
        what=f"taste label ({GUARD_DB})",
        min_n=DISTRIBUTION_MIN_RATED,
    )


def test_p_keep_guard_ignores_unscored_rows(make_photo):
    index = ArchiveIndex.get()
    scored = []
    for i in range(12):
        rel, _ = make_photo(name=f"pk_{i}.jpg")
        scored.append(rel)
    make_photo(name="unscored.jpg")
    index.set_p_keeps([(rel, 0.9) for rel in scored])

    counts = p_keep_buckets(ARCHIVE_DB_FILE)
    assert counts == {"high": 12}, counts
    with pytest.raises(AssertionError):
        assert_not_saturated(counts, what="p_keep", min_n=10)


def test_archive_p_keep_distribution_is_not_saturated():
    assert_not_saturated(
        p_keep_buckets(GUARD_DB),
        what=f"p_keep ({GUARD_DB})",
        min_n=DISTRIBUTION_MIN_RATED,
    )


def test_keep_tier_filters_are_judged_over_classified_keep_tiers(make_photo):
    """t1/t2 split the keep pile. Rejects and unclassified are not in their
    denominator — counting either would fire on a mostly-T0 archive, or on one
    that has not been classified yet, which is how a guard gets switched off.
    """
    index = ArchiveIndex.get()
    for i, tier in enumerate([1] * 12 + [0] * 8):
        rel, _ = make_photo(name=f"kt_{i:03d}.jpg")
        index.set_verdict(rel, creator="tester", tier=tier)
    for i in range(5):
        make_photo(name=f"unseen_{i}.jpg")

    counts = {
        name: index.query_photos(verdict=name)[1] for name in ("t1", "t2")
    }
    assert counts == {"t1": 12, "t2": 0}, counts
    with pytest.raises(AssertionError) as caught:
        assert_not_saturated(counts, what="keep-tier filter", min_n=10)
    assert "t1" in str(caught.value)


def test_disagreement_filter_is_judged_over_corrections(make_photo):
    """Gold-vs-model. Uncorrected rows are not in the denominator — counting
    them as 'agreement' would fire on every archive nobody has labelled yet.
    """
    index = ArchiveIndex.get()
    rels = []
    for i in range(20):
        rel, _ = make_photo(name=f"dg_{i:03d}.jpg")
        index.set_verdict(rel, creator="tester", tier=1)
        rels.append(rel)
    for i in range(5):
        make_photo(name=f"unseen_dg_{i}.jpg")
    # 12 corrections, all mismatches. 8 classified but unlabelled.
    for rel in rels[:12]:
        index.set_corrected_tier(rel, 2)

    gold = index.correction_counts()
    disagree = index.query_photos(verdict="disagreement")[1]
    assert gold["corrections"] == 12
    assert gold["disagreements"] == 12
    assert disagree == 12
    counts = {
        "disagreement": disagree,
        "agreement": gold["corrections"] - gold["disagreements"],
    }
    assert counts == {"disagreement": 12, "agreement": 0}
    with pytest.raises(AssertionError) as caught:
        assert_not_saturated(counts, what="tier correction", min_n=10)
    assert "disagreement" in str(caught.value)

    # Histogram is still the model's distribution — gold must not paper over it.
    hist = {
        f"tier {t}": n for t, n in index.tier_histogram().items() if int(t) >= 0
    }
    assert hist == {"tier 1": 20}
