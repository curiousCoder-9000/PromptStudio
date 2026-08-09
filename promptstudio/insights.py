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
    from promptstudio.comfy.client import GenerationsIndex

    data = GenerationsIndex().load()
    sources = 0
    total_outputs = 0
    multi = 0
    for _key, items in (data or {}).items():
        if not isinstance(items, list) or not items:
            continue
        sources += 1
        n = len(items)
        total_outputs += n
        if n > 1:
            multi += 1
    return {
        "sources_with_gens": sources,
        "total_outputs": total_outputs,
        "avg_per_source": round(total_outputs / sources, 3) if sources else 0.0,
        "sources_with_multiple": multi,
        # Rating is Phase 13 A3 — field will fill in once that lands.
        "rated": 0,
        "keep_rate": None,
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

    return {
        "prompts": prompts,
        "generations": generations,
    }
