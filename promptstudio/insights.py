"""Quality insights from signals already on disk (roadmap B1).

No new instrumentation. Reads:

- prompt bundles (`manual_edit`, `history`, pipeline version) — edit/regenerate rates
- the `generations` table — output volume and `keep_rate` (A0/A3)
- `media_verdicts` — the classifier's tier distribution

`saturation_report` is the B4 platform rule and the only part of this module
that is not a dashboard: it is the shared answer to "has one bucket eaten this
distribution", used by `/api/insights`, by the pass-rate badges via
`DISTRIBUTION_MAX_SHARE`, and by the gate in `tests/test_distribution_guard.py`
that fails a local run when the archive is over the line.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Mapping, Optional

from promptstudio.config import DISTRIBUTION_MAX_SHARE
from promptstudio.logging_setup import get_logger
from promptstudio.prompts.cache import PromptCache

log = get_logger(__name__)


def _rate(num: int, den: int) -> Optional[float]:
    if den <= 0:
        return None
    return round(num / den, 4)


def saturation_report(
    counts: Mapping[str, int],
    *,
    what: str,
    min_n: int,
    threshold: Optional[float] = None,
) -> Dict[str, Any]:
    """Is one bucket eating the distribution? — the B4 platform rule.

    Deliberately generic. Feed it any bucket→count mapping and it answers the
    one question that matters about a score or a filter: does a single value
    hold more than `threshold` of the population. The classifier's tiers were
    only the first thing to fail it — 85% on one tier, three prompt versions
    in a row — and the same collapse in generation ratings would make
    `keep_rate` just as meaningless.

    **The caller owns the denominator.** Pass only the population the metric
    is defined over: scored tiers (never the -1 errors), rated generations
    (never the 0s). A bucket for "not judged yet" would fire on every fresh
    archive, and a guard with a standing false alarm gets switched off.

    Below `min_n` it reports `measured: False` rather than a verdict. On a
    handful of items the top share swings by tens of points per row, and
    "nothing measured yet" is a different answer from "measured and fine".

    The `message` is the whole point of the return value: it names the bucket,
    its share and the denominator, so a failing check tells you where to look.
    """
    limit = DISTRIBUTION_MAX_SHARE if threshold is None else float(threshold)
    buckets = {str(k): int(v) for k, v in (counts or {}).items() if int(v) > 0}
    n = sum(buckets.values())
    pct = f"{limit * 100:g}%"

    if n < max(1, int(min_n)):
        return {
            "what": what,
            "n": n,
            "min_n": int(min_n),
            "threshold": limit,
            "measured": False,
            "saturated": False,
            "top_bucket": None,
            "top_count": 0,
            "top_share": None,
            "message": (
                f"{what}: {n} of the {int(min_n)} needed to judge a distribution "
                "— not measured"
            ),
        }

    top_bucket, top_count = max(buckets.items(), key=lambda kv: (kv[1], kv[0]))
    share = top_count / n
    # Strictly greater: the rule reads "exceeds 60%", and a gate that trips at
    # exactly the line flaps as one item lands either side of it.
    saturated = share > limit
    head = f"{what}: {top_bucket} holds {share * 100:.1f}% of {n}"
    return {
        "what": what,
        "n": n,
        "min_n": int(min_n),
        "threshold": limit,
        "measured": True,
        "saturated": saturated,
        "top_bucket": top_bucket,
        "top_count": top_count,
        "top_share": round(share, 4),
        "message": (
            f"{head} — over the {pct} limit. One value this dominant leaves "
            "every filter built on it close to a no-op; re-check the prompt "
            "or the thresholds before trusting anything downstream of it."
            if saturated
            else f"{head} (limit {pct})"
        ),
    }


def _prompt_insights(cache: Optional[dict] = None) -> Dict[str, Any]:
    data = cache if cache is not None else PromptCache().load()
    total = 0
    manual = 0
    with_history = 0
    history_depths: List[int] = []
    by_pipeline: Counter = Counter()

    for entry in (data or {}).values():
        if not isinstance(entry, dict):
            continue
        # Count only real prompt bundles, not empty shells.
        if not (entry.get("positive_prompt") or entry.get("raw_vision_description")):
            continue
        total += 1
        params = entry.get("parameters") or {}
        if not isinstance(params, dict):
            params = {}
        if params.get("manual_edit"):
            manual += 1
        hist = entry.get("history") or []
        depth = len(hist) if isinstance(hist, list) else 0
        history_depths.append(depth)
        if depth > 0:
            with_history += 1
        ver = params.get("pipeline_version") or "(unknown)"
        by_pipeline[str(ver)] += 1

    avg_depth = (
        round(sum(history_depths) / len(history_depths), 3) if history_depths else 0.0
    )
    return {
        "total": total,
        "manual_edits": manual,
        "edit_rate": _rate(manual, total),
        "with_history": with_history,
        "regenerate_rate": _rate(with_history, total),
        "avg_history_depth": avg_depth,
        "by_pipeline_version": dict(sorted(by_pipeline.items())),
    }


def _generation_insights() -> Dict[str, Any]:
    """Output volume and the keep rate, from the `generations` table.

    Reads the table, not `generations_index.json` — the JSON is a rollback
    parachute that A0 caps nowhere and A3 never writes ratings to.

    `keep_rate = kept / rated`, deliberately **not** `kept / total`: an unrated
    output is not evidence either way, and dividing by the total would make the
    metric drift toward zero as the archive grows rather than measuring
    anything. It is `None` until something is rated, because 0.0 would render
    as a damning score for an archive nobody has judged yet.

    B4 rides along on the same denominator. If one rating value holds most of
    what has been judged, `keep_rate` has stopped discriminating for exactly
    the reason a saturated tier makes the reject filter a no-op — so the guard
    is reported here too rather than only over the classifier.
    """
    from promptstudio.config import DISTRIBUTION_MIN_RATED
    from promptstudio.storage.db import ArchiveIndex

    index = ArchiveIndex.get()
    summary = index.generation_rating_summary()

    # Reconstructed from the aggregates already fetched, not a second query:
    # `kept` is rating >= 1, so keep-only is kept minus starred, and the three
    # buckets sum to `rated` by construction.
    rated_buckets = {
        "discard": int(summary.get("discarded") or 0),
        "keep": int(summary.get("kept") or 0) - int(summary.get("starred") or 0),
        "star": int(summary.get("starred") or 0),
    }
    summary["saturation"] = saturation_report(
        rated_buckets, what="generation rating", min_n=DISTRIBUTION_MIN_RATED
    )
    return summary


def _classify_insights() -> Dict[str, Any]:
    """Tier distribution and reject rate over everything classified so far.

    This is the metric that would have caught the previous classifier on day
    one: it shipped with 85% of the archive on a single value, which makes a
    filter a no-op, and nothing was reading the distribution. `top_tier_share`
    is the number to watch — above ~0.6 the classifier is barely discriminating.

    `saturation` is that same number with the B4 verdict attached, so the
    panel, the pass-rate badges and `tests/test_distribution_guard.py` all
    read one rule instead of three copies of 0.6.
    """
    from promptstudio.config import CLASSIFY_REJECT_MAX_TIER, DISTRIBUTION_MIN_CLASSIFIED
    from promptstudio.scraping.media_classifier import TIER_LABELS
    from promptstudio.storage.db import ArchiveIndex

    index = ArchiveIndex.get()
    index.ensure_ready()
    hist = index.tier_histogram()

    scored = sum(c for tier, c in hist.items() if int(tier) >= 0)
    errors = int(hist.get("-1", 0))
    cut = int(CLASSIFY_REJECT_MAX_TIER)
    rejects = sum(c for tier, c in hist.items() if 0 <= int(tier) <= cut)
    top = max((c for tier, c in hist.items() if int(tier) >= 0), default=0)
    # Scored tiers only: a failed vision call (-1) is a retry, and letting a
    # pile of them dilute the shares would hide a saturated classifier behind
    # a broken Ollama.
    scored_buckets = {
        f"tier {tier}": c for tier, c in hist.items() if int(tier) >= 0
    }

    return {
        "classified": scored,
        "errors": errors,
        "reject_max_tier": cut,
        "distribution": dict(sorted(hist.items(), key=lambda kv: int(kv[0]))),
        "labels": {str(k): v for k, v in TIER_LABELS.items()},
        "reject_rate": _rate(rejects, scored),
        # Fraction of classified media sitting on the single most common tier.
        "top_tier_share": _rate(top, scored),
        "error_rate": _rate(errors, scored + errors),
        "saturation": saturation_report(
            scored_buckets, what="classified tier", min_n=DISTRIBUTION_MIN_CLASSIFIED
        ),
    }


def compute_insights() -> Dict[str, Any]:
    """Aggregate B1 quality signals for GET /api/insights."""
    try:
        prompts = _prompt_insights()
    except Exception as e:
        log.exception("prompt insights failed: %s", e)
        prompts = {"total": 0, "error": str(e)}
    try:
        generations = _generation_insights()
    except Exception as e:
        log.exception("generation insights failed: %s", e)
        generations = {"total_outputs": 0, "error": str(e)}
    try:
        classify = _classify_insights()
    except Exception as e:
        log.exception("classify insights failed: %s", e)
        classify = {"classified": 0, "error": str(e)}

    return {
        "prompts": prompts,
        "generations": generations,
        "classify": classify,
    }
