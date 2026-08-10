"""A2 — generate for many photos in one unattended run.

The archive holds thousands of photos and the generator produced one image per
lightbox visit, so the two never met. This closes that: pick a filter or a
multi-selection, walk away, come back to a contact sheet.

Two decisions are worth stating, because both invert something the sibling
batch job does.

**Skips are counted, never fixed.** A photo with no prompt is reported as
`skipped_no_prompt` and left alone. Auto-analyzing it would chain two jobs that
contend for different resources, with their own cancel semantics and failure
modes — a job-composition design, not a feature flag (design §9).

**Cancel reaches into the running item.** `BatchPromptManager` deliberately
finishes the in-flight photo because a half-written prompt poisons the cache.
Here nothing is persisted until `_download_image` returns, so an interrupted
item costs exactly the GPU seconds already spent, and waiting out a 40-second
render to honour a cancel is the worse trade.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from promptstudio.comfy.params import (
    GenerationParams,
    NoPromptError,
    resolve_generation_params,
)
from promptstudio.comfy.runner import ComfyRunner
from promptstudio.config import COMFY_BATCH_MAX, IMAGE_EXTENSIONS
from promptstudio.jobs import COMFY, BackgroundJob
from promptstudio.logging_setup import get_logger
from promptstudio.storage.journal import RunJournal

log = get_logger(__name__)

LEASE_OWNER = "comfy_batch"


@dataclass
class BatchPlan:
    """What a run would do, resolved before a single GPU second is spent.

    Prompts are looked up here rather than per item in the loop so the API can
    answer "3 of your 50 have never been analyzed" immediately, instead of the
    user discovering it from a completion toast an hour later.
    """

    items: List[GenerationParams] = field(default_factory=list)
    skipped_no_prompt: int = 0
    skipped_video: int = 0
    # True when COMFY_BATCH_MAX trimmed the selection. Surfaced rather than
    # silently applied: a cap that nobody is told about reads as a bug.
    capped: bool = False


class ComfyBatchManager(BackgroundJob):
    resources = (COMFY,)
    owner = LEASE_OWNER
    busy_noun = "ComfyUI"
    busy_message = "A batch generation is already running"

    def __init__(self) -> None:
        super().__init__()
        self._current_runner: Optional[ComfyRunner] = None

    def _idle_status(self) -> Dict[str, Any]:
        return {
            **super()._idle_status(),
            "batch_id": None,
            "prompt_id": None,
            "pending": 0,
            "skipped_no_prompt": 0,
            "skipped_video": 0,
            "workflow": None,
        }

    # ── planning ─────────────────────────────────────────────────────

    def plan(
        self,
        *,
        paths: Optional[List[str]] = None,
        creator: Optional[str] = None,
        favorite: bool = False,
        media_type: Optional[str] = None,
        verdict: Optional[str] = None,
        source: Optional[str] = None,
        limit: Optional[int] = None,
        **overrides: Any,
    ) -> BatchPlan:
        """Resolve a selection into runnable items.

        Selection is either an explicit `paths` list (the gallery's multi-select)
        or the same filter vocabulary `/api/photos` already accepts. Reusing that
        query rather than inventing a second filter language is the point: what
        you see in the gallery is what the batch runs on.
        """
        candidates = (
            self._paths_to_rel(paths)
            if paths
            else self._query_to_rel(
                creator=creator,
                favorite=favorite,
                media_type=media_type,
                verdict=verdict,
                source=source,
            )
        )

        plan = BatchPlan()
        for rel in candidates:
            if not rel.lower().endswith(IMAGE_EXTENSIONS):
                # Videos have no meaningful img2img reference frame (design §3.5).
                plan.skipped_video += 1
                continue
            try:
                params = resolve_generation_params(rel, overrides)
            except NoPromptError:
                plan.skipped_no_prompt += 1
                continue
            if not params.has_prompt_source:
                # Mode E would happily render this from its fallback string,
                # which is why the flag exists rather than a text check.
                plan.skipped_no_prompt += 1
                continue
            plan.items.append(params)

        ceiling = COMFY_BATCH_MAX
        if limit:
            ceiling = min(ceiling, int(limit))
            if len(plan.items) > int(limit):
                plan.items = plan.items[: int(limit)]
        if len(plan.items) > ceiling:
            plan.items = plan.items[:ceiling]
        plan.capped = len(plan.items) >= COMFY_BATCH_MAX
        return plan

    @staticmethod
    def _paths_to_rel(paths: List[str]) -> List[str]:
        from promptstudio.storage.archive import ArchiveStore

        store = ArchiveStore()
        out: List[str] = []
        for raw in paths:
            rel = (raw or "").replace("\\", "/").strip().lstrip("/")
            # resolve_path is the one containment check; an unresolvable path is
            # dropped rather than reported, exactly as batch analyze does.
            if rel and store.resolve_path(rel):
                out.append(rel)
        return out

    @staticmethod
    def _query_to_rel(
        *,
        creator: Optional[str],
        favorite: bool,
        media_type: Optional[str],
        verdict: Optional[str],
        source: Optional[str],
    ) -> List[str]:
        from promptstudio.storage.db import ArchiveIndex

        rows, _total = ArchiveIndex.get().query_photos(
            creator=creator,
            favorite_only=bool(favorite),
            media_type=media_type,
            verdict=verdict,
            source=source,
        )
        return [row["rel_path"] for row in rows]

    # ── running ──────────────────────────────────────────────────────

    def start(self, **kwargs: Any) -> Dict[str, Any]:
        """Plan and launch. Never raises; the status is in the return value."""
        plan = self.plan(**kwargs)
        skips = {
            "skipped_no_prompt": plan.skipped_no_prompt,
            "skipped_video": plan.skipped_video,
        }
        if not plan.items:
            return {"status": "nothing_to_do", "pending": 0, **skips}

        batch_id = uuid.uuid4().hex[:12]
        pinned_seed = kwargs.get("seed")
        items = plan.items

        def runner() -> None:
            journal = RunJournal.for_kind("comfy_batch")
            with journal.run(batch_id=batch_id, total=len(items)) as run:
                for params in items:
                    if self._cancel.is_set():
                        with self._job_lock:
                            self._status["cancelled"] = True
                            done = self._status["completed"]
                        run.event("cancelled", completed=done)
                        break
                    self._run_one(params, batch_id, pinned_seed, run)
                    with self._job_lock:
                        self._status["pending"] = self._remaining()
                with self._job_lock:
                    run.summary(
                        completed=self._status["completed"],
                        failed=self._status["failed"],
                        **skips,
                    )

        ok = self._start(
            runner,
            batch_id=batch_id,
            total=len(items),
            pending=len(items),
            workflow=items[0].workflow,
            **skips,
        )
        if not ok:
            return {"status": "busy", "message": self.last_refusal, **skips}
        return {
            "status": "started",
            "batch_id": batch_id,
            "pending": len(items),
            "capped": plan.capped,
            **skips,
        }

    def _run_one(
        self,
        params: GenerationParams,
        batch_id: str,
        pinned_seed: Optional[int],
        run: Any,
    ) -> None:
        from promptstudio.comfy.client import resolve_seed

        # Rolled per item unless pinned: a batch that reuses one seed is one
        # image rendered N times.
        seed = resolve_seed(pinned_seed if pinned_seed is not None else params.seed)
        with self._job_lock:
            self._status["current"] = params.rel_path
            self._status["prompt_id"] = None
        item_runner = self._item_runner()
        self._current_runner = item_runner
        started = time.monotonic()
        try:
            item_runner.run(params, seed=seed, batch_id=batch_id)
            with self._job_lock:
                self._status["completed"] += 1
            run.item(
                path=params.rel_path,
                ok=True,
                seed=seed,
                ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:
            # One item, not the run. A ComfyUI restart mid-batch should cost the
            # image it was rendering and nothing else.
            log.warning("batch generate failed for %s: %s", params.rel_path, exc)
            with self._job_lock:
                self._status["failed"] += 1
            run.item(
                path=params.rel_path,
                ok=False,
                reason=str(exc)[:200],
                ms=int((time.monotonic() - started) * 1000),
            )
        finally:
            self._current_runner = None

    def _item_runner(self) -> ComfyRunner:
        def prompt_id(pid: str) -> None:
            with self._job_lock:
                self._status["prompt_id"] = pid

        return ComfyRunner(on_prompt_id=prompt_id)

    # ── cancel ───────────────────────────────────────────────────────

    def cancel(self) -> bool:
        """Two-level cancel (design §2.3).

        `super()` sets the flag the loop checks between items, which drains the
        remaining queue. Then the item already on the GPU is interrupted, which
        is safe here and would not be in batch analyze — see the module
        docstring.
        """
        if not super().cancel():
            return False
        runner = self._current_runner
        with self._job_lock:
            prompt_id = self._status.get("prompt_id")
        if runner and prompt_id:
            try:
                runner.interrupt(prompt_id)
            except Exception:
                # Best effort. The cooperative half already guarantees the run
                # stops; this only decides whether it stops now or in 40s.
                log.exception("interrupting the in-flight generation failed")
        return True

    # ── status ───────────────────────────────────────────────────────

    def _remaining(self) -> int:
        return max(
            0,
            self._status["total"]
            - self._status["completed"]
            - self._status["failed"],
        )

    def _finalise(self) -> None:
        self._status["pending"] = (
            self._remaining() if self._status.get("cancelled") else 0
        )
        self._status["prompt_id"] = None
        super()._finalise()
