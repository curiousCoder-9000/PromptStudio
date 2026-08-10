"""Background per-creator keep/reject classification job."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from promptstudio.config import EXCLUDED_FOLDERS, MODEL_NAME
from promptstudio.jobs import OLLAMA, BackgroundJob
from promptstudio.logging_setup import get_logger
from promptstudio.scraping.media_classifier import (
    TIER_LABELS,
    active_prompt_versions,
    classify_media,
    media_kind_for,
    ollama_reachable,
    persist_verdict,
    tier_is_reject,
)
from promptstudio.storage.db import ArchiveIndex
from promptstudio.storage.journal import RunJournal

log = get_logger(__name__)

LEASE_OWNER = "classify"


class ClassifyJobManager(BackgroundJob):
    """Singleton background job: classify unclassified media for one creator."""

    resources = (OLLAMA,)
    owner = LEASE_OWNER
    busy_noun = "the vision model — wait or cancel it first"
    busy_message = "Classify job already running"

    @staticmethod
    def _empty_hist() -> Dict[str, int]:
        """Tier buckets. "-1" is the error bucket, not a tier."""
        return {"-1": 0, "0": 0, "1": 0, "2": 0, "3": 0, "4": 0}

    def _idle_status(self) -> Dict[str, Any]:
        return {
            **super()._idle_status(),
            # None = idle. "" = an archive-wide run (every creator), which is
            # a different thing from "no job", so the UI must not conflate them.
            "creator": None,
            "kept": 0,
            "rejected": 0,
            "current_creator": "",
            "include_videos": True,
            "force": False,
            "model": MODEL_NAME,
            # Distribution guard: a classifier that emits one value for most of
            # the archive carries almost no information, which is how the v2
            # prompt shipped 85% on one value unnoticed. Surfacing the histogram
            # makes that visible per run without needing a labelled eval set.
            "tier_hist": ClassifyJobManager._empty_hist(),
            "top_tier_share": 0.0,
            "error_rate": 0.0,
        }

    def get_status(self) -> Dict[str, Any]:
        status = super().get_status()
        # The base copy is shallow — copy the histogram too, or callers observe
        # it mutating under them while the job runs.
        hist = status.get("tier_hist")
        if isinstance(hist, dict):
            status["tier_hist"] = dict(hist)
        return status

    def _record_tier(self, tier: int) -> None:
        """Bucket one result and refresh the derived shares. Call under lock."""
        hist = self._status.get("tier_hist")
        if not isinstance(hist, dict):
            hist = self._empty_hist()
            self._status["tier_hist"] = hist
        key = str(int(tier)) if 0 <= int(tier) <= 4 else "-1"
        hist[key] = int(hist.get(key, 0)) + 1

        scored = [int(hist.get(str(t), 0)) for t in range(5)]
        total_scored = sum(scored)
        errors = int(hist.get("-1", 0))
        total = total_scored + errors
        self._status["top_tier_share"] = (
            round(max(scored) / total_scored, 4) if total_scored else 0.0
        )
        self._status["error_rate"] = round(errors / total, 4) if total else 0.0

    def list_pending(
        self,
        creator: str,
        *,
        force: bool = False,
        include_videos: bool = True,
        limit: Optional[int] = None,
        rescore_stale: bool = False,
    ) -> List[Dict[str, Any]]:
        """Media that still needs classifying. Empty `creator` = whole archive.

        `rescore_stale` also picks up files judged by a prompt version that is no
        longer current. Without it, improving a prompt never re-runs anything and
        the only way to adopt it is --force over the whole archive.
        """
        creator = (creator or "").strip().lstrip("@")
        return ArchiveIndex.get().list_unclassified(
            creator,
            include_videos=include_videos,
            force=force,
            stale_versions=active_prompt_versions() if rescore_stale else (),
            limit=limit,
        )

    def start(
        self,
        creator: str = "",
        *,
        force: bool = False,
        include_videos: bool = True,
        limit: Optional[int] = None,
        only_unclassified: bool = True,
        rescore_stale: bool = False,
    ) -> Dict[str, Any]:
        """Start a classify job. Empty `creator` classifies the whole archive.

        Archive-wide is the run you actually leave going overnight, and it was
        unreachable: the sidebar panel only exists once a creator is selected,
        so coverage was capped at whatever you remembered to run one folder at
        a time. Batch analyze has always been archive-wide; this matches it.

        Returns a result dict:
          status: started | nothing_to_do | busy | ollama_down | bad_creator
        """
        creator = (creator or "").strip().lstrip("@")
        if creator in EXCLUDED_FOLDERS or creator.startswith((".", "_")):
            return {"status": "bad_creator", "message": f"not a creator: {creator}"}

        if not ollama_reachable():
            return {
                "status": "ollama_down",
                "message": f"Ollama not reachable (need model {MODEL_NAME})",
            }

        # force overrides only_unclassified
        use_force = bool(force) or not only_unclassified
        pending = self.list_pending(
            creator,
            force=use_force,
            include_videos=include_videos,
            limit=limit,
            rescore_stale=bool(rescore_stale),
        )
        if not pending:
            return {"status": "nothing_to_do", "pending": 0, "creator": creator}

        def runner() -> None:
            journal = RunJournal.for_kind("classify")
            with journal.run(
                creator=creator,
                total=len(pending),
                force=bool(use_force),
                include_videos=bool(include_videos),
                model=MODEL_NAME,
            ) as run:
                for item in pending:
                    if self._cancel.is_set():
                        with self._job_lock:
                            self._status["cancelled"] = True
                            self._status["error"] = "cancelled"
                            done = self._status["completed"]
                        run.event("cancelled", completed=done)
                        break
                    rel = item["rel_path"]
                    full = item["full_path"]
                    with self._job_lock:
                        self._status["current"] = rel
                        # Archive-wide runs span creators; "412/3100" alone
                        # says nothing about where it has got to.
                        self._status["current_creator"] = item.get("creator", "")
                    if not os.path.isfile(full):
                        with self._job_lock:
                            self._status["failed"] += 1
                            self._status["completed"] += 1
                            self._record_tier(-1)
                        run.item(path=rel, ok=False, reason="missing_file")
                        continue

                    started = time.monotonic()
                    try:
                        verdict = classify_media(full, rel_path=rel)
                    except Exception as exc:
                        log.warning("classify failed for %s: %s", rel, exc)
                        with self._job_lock:
                            self._status["failed"] += 1
                            self._status["completed"] += 1
                            self._record_tier(-1)
                        run.item(path=rel, ok=False, reason=str(exc)[:200])
                        continue
                    elapsed_ms = int((time.monotonic() - started) * 1000)

                    persist_verdict(
                        rel, verdict, full_path=full, duration_ms=elapsed_ms
                    )
                    if verdict.ok:
                        tier = int(verdict.exposure_tier)
                        reject = tier_is_reject(tier)
                        with self._job_lock:
                            self._status["completed"] += 1
                            if reject:
                                self._status["rejected"] += 1
                            else:
                                self._status["kept"] += 1
                            self._record_tier(tier)
                        run.item(
                            path=rel,
                            ok=True,
                            tier=tier,
                            label=TIER_LABELS.get(tier, ""),
                            reject=reject,
                            kind=media_kind_for(full),
                            source=verdict.source,
                            prompt_version=verdict.prompt_version,
                            calls=(verdict.evidence or {}).get(
                                "frames_sent_to_vision"
                            ),
                            ms=elapsed_ms,
                        )
                    else:
                        with self._job_lock:
                            self._status["failed"] += 1
                            self._status["completed"] += 1
                            self._record_tier(-1)
                        run.item(
                            path=rel,
                            ok=False,
                            reason=verdict.error[:200],
                            ms=elapsed_ms,
                        )

                # Distribution is the signal that a prompt change collapsed
                # the output space; keep it per run so drift is visible.
                with self._job_lock:
                    run.summary(
                        tier_hist=dict(self._status.get("tier_hist") or {}),
                        top_tier_share=self._status.get("top_tier_share"),
                        error_rate=self._status.get("error_rate"),
                        kept=self._status.get("kept"),
                        rejected=self._status.get("rejected"),
                    )

        # The lease is taken here, only once there is work to do, and released
        # in BackgroundJob's finally. Atomic against a concurrent batch start.
        started = self._start(
            runner,
            creator=creator,
            total=len(pending),
            include_videos=bool(include_videos),
            force=bool(use_force),
        )
        if not started:
            return {
                "status": "busy",
                "message": self.last_refusal,
                "creator": self.get_status().get("creator"),
            }
        return {
            "status": "started",
            "pending": len(pending),
            "creator": creator,
            "force": bool(use_force),
            "include_videos": bool(include_videos),
        }
