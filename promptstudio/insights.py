"""Quality insights from signals already on disk (roadmap B1).

No new instrumentation. Reads:

- prompt bundles (`manual_edit`, `history`, pipeline version) — edit/regenerate rates
- `generations_index.json` — generations-per-source (until A0 moves this into SQLite)

The media score distribution used to be reported here too; it went out with
the classifier.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from promptstudio.logging_setup import get_logger
from promptstudio.prompts.cache import PromptCache

log = get_logger(__name__)


def _rate(num: int, den: int) -> Optional[float]:
    if den <= 0:
        return None
    return round(num / den, 4)


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
    """
    from promptstudio.storage.db import ArchiveIndex

    index = ArchiveIndex.get()
    return index.generation_rating_summary()


def _classify_insights() -> Dict[str, Any]:
    """Tier distribution and reject rate over everything classified so far.

    This is the metric that would have caught the previous classifier on day
    one: it shipped with 85% of the archive on a single value, which makes a
    filter a no-op, and nothing was reading the distribution. `top_tier_share`
    is the number to watch — above ~0.6 the classifier is barely discriminating.
    """
    from promptstudio.config import CLASSIFY_REJECT_MAX_TIER
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
