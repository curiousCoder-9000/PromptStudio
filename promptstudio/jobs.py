"""Background jobs and the exclusive leases they contend for.

Two things live here, and they belong together: the resource a job needs and
the scaffolding for running it are the same subject, and `BackgroundJob` is
useless without `LEASES`.

## Leases

Background jobs contend for three things that cannot be shared: the Ollama
vision model, the Instagram session, and ComfyUI. That contention used to be
encoded as pairwise `is_running()` checks scattered across modules —
`BatchPromptManager` and `handler` re-checking each other at
eight call sites. Two problems with that:

* **It is O(n) per new job type.** Every new manager means editing every other
  manager's guard.
* **It races.** `is_running()` is polled, the job is started, and nothing holds
  a lock across both steps — so two requests arriving together can each see
  "free" and both start.

A lease closes both. Acquisition of *all* a job's resources happens under one
lock, so it is atomic: either the job holds everything it needs or it holds
nothing and is told which resource blocked it.

    holder = LEASES.acquire([OLLAMA], "classify")
    if holder:
        return {"status": "busy", "message": f"{holder} in use"}
    try:
        ...
    finally:
        LEASES.release("classify")

This models the contention that already exists. ComfyUI is declared but
deliberately not made exclusive with Ollama: they do share a GPU, but today they
can run together, and changing that is a product decision rather than a
refactor.

## BackgroundJob

`review_backend_architecture.md` S6 counted five managers independently
reimplementing the same singleton / `_job_lock` / `_cancel` / `get_status` /
`is_running` / `cancel` / thread-spawn scaffolding. `BackgroundJob` is that
scaffolding, written once.

Only three managers use it: `BatchPromptManager`, `ClassifyJobManager` and
`ComfyBatchManager`. **`SyncManager` and `CreatorScrapeQueue` are deliberately
left alone** — they carry pause/resume, multi-day pacing and per-lane queue
state, which is a different shape, and bending them to fit would be shaping the
abstraction to the review doc rather than to the code.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence

from promptstudio.logging_setup import get_logger

log = get_logger(__name__)

# Resource names. Values are user-facing — they appear in "busy" messages.
OLLAMA = "ollama"
COMFY = "comfy"

# Scrape capacity is per platform, not global. Instagram's session is the thing
# that cannot be shared; Reddit has nothing in common with it, so serialising
# the two only ever cost throughput. One lease name per source turns the single
# global scrape slot into one slot per lane — see docs/design_scrape_lanes.md.
SCRAPE_PREFIX = "scrape:"


def scrape_resource(source: str) -> str:
    """Lease name for one source's scrape lane."""
    name = (source or "").strip().lower()
    if not name:
        raise ValueError("source required for a scrape lease")
    return f"{SCRAPE_PREFIX}{name}"


# Back-compat alias. Every caller that meant "the Instagram session" still gets
# exactly that; it is now simply one lane among several.
INSTAGRAM = scrape_resource("instagram")

_STATIC_RESOURCES = (OLLAMA, COMFY)


def all_resources() -> tuple:
    """Every lease name, for /api/health and debugging.

    Scrape lanes are derived from the source registry rather than hardcoded, so
    registering a source creates its lane with no edit here. The import is lazy
    and the registry itself is lazy, so this does not drag in instaloader or
    probe for the gallery-dl binary.
    """
    try:
        from promptstudio.scraping.sources import known_sources

        lanes = tuple(scrape_resource(name) for name in known_sources())
    except Exception:  # pragma: no cover - registry import should not fail
        lanes = (INSTAGRAM,)
    return _STATIC_RESOURCES + lanes


class LeaseRegistry:
    """Who currently holds each exclusive resource. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: Dict[str, str] = {}

    def acquire(self, resources: Iterable[str], owner: str) -> Optional[str]:
        """
        Take every resource in `resources` atomically.

        Returns None on success, or the name of the first resource already held
        by someone else. All-or-nothing: a partial acquisition is never left
        behind for the caller to clean up.

        Re-acquiring a resource you already hold succeeds — a job that restarts
        its own inner loop should not deadlock against itself.
        """
        wanted = list(resources)
        with self._lock:
            for name in wanted:
                current = self._held.get(name)
                if current is not None and current != owner:
                    return name
            for name in wanted:
                self._held[name] = owner
        log.debug("lease acquired: %s by %s", wanted, owner)
        return None

    def release(self, owner: str) -> List[str]:
        """Drop everything `owner` holds. Returns the released names."""
        with self._lock:
            released = [name for name, held in self._held.items() if held == owner]
            for name in released:
                del self._held[name]
        if released:
            log.debug("lease released: %s by %s", released, owner)
        return released

    def holder(self, resource: str) -> Optional[str]:
        with self._lock:
            return self._held.get(resource)

    def snapshot(self) -> Dict[str, Optional[str]]:
        """Current holders, for /api/health and debugging.

        Includes any held resource that is not a known lane, so a lease taken
        under an unexpected name shows up rather than vanishing from the health
        endpoint that exists to find exactly that.
        """
        with self._lock:
            held = dict(self._held)
        names = list(all_resources())
        names.extend(n for n in held if n not in names)
        return {name: held.get(name) for name in names}

    @contextmanager
    def hold(self, resources: Iterable[str], owner: str) -> Iterator[None]:
        """Acquire for the duration of a block. Raises ResourceBusy if taken."""
        blocker = self.acquire(resources, owner)
        if blocker:
            raise ResourceBusy(blocker, self.holder(blocker) or "")
        try:
            yield
        finally:
            self.release(owner)

    def reset(self) -> None:
        """Drop every lease. Tests only — a live job would keep running."""
        with self._lock:
            self._held.clear()


class ResourceBusy(RuntimeError):
    def __init__(self, resource: str, holder: str) -> None:
        self.resource = resource
        self.holder = holder
        super().__init__(f"{resource} is held by {holder or 'another job'}")


# Process-wide. The resources are process-wide too.
LEASES = LeaseRegistry()


class BackgroundJob:
    """One long-running job: singleton, status snapshot, cancel, leases.

    A subclass declares what it needs and what it does::

        class WidgetJob(BackgroundJob):
            resources = (OLLAMA,)
            owner = "widget"
            busy_noun = "the vision model"

            def start(self, items):
                return self._start(lambda: self._run(items), total=len(items))

    Everything below the subclass's own status keys and loop body is handled
    here: acquiring the leases atomically, refusing with an attributable
    message, spawning the thread, and — the part that was a real bug in
    `BatchPromptManager` — releasing the lease and clearing `running` in a
    `finally`, so an exception escaping the loop cannot leave the job wedged.
    """

    # What the job needs exclusive access to, and the name it holds it under.
    resources: Sequence[str] = ()
    owner: str = ""
    # Filled into "<holder> is using <busy_noun>" when a resource is taken.
    busy_noun: str = "a shared resource"
    # Refusal when *this* job is the one already running.
    busy_message: str = "A job is already running"

    # One lock guards every subclass's lazy `get()`; contention is nil.
    _singleton_lock = threading.Lock()

    def __init__(self) -> None:
        self._job_lock = threading.Lock()
        self._cancel = threading.Event()
        # Why the last start returned False, for the API's 409 message.
        self.last_refusal = ""
        self._status: Dict[str, Any] = self._idle_status()

    @classmethod
    def get(cls) -> "BackgroundJob":
        """The process-wide instance for this subclass.

        `cls.__dict__`, not `cls._instance`: a plain attribute lookup walks the
        MRO, so the first subclass to instantiate would populate the base and
        every later subclass would be handed *its* instance. Nothing about that
        fails loudly, which is why it is worth the two extra characters.
        """
        instance = cls.__dict__.get("_instance")
        if instance is None:
            with BackgroundJob._singleton_lock:
                instance = cls.__dict__.get("_instance")
                if instance is None:
                    instance = cls()
                    cls._instance = instance
        return instance

    def _idle_status(self) -> Dict[str, Any]:
        """The keys every job has. Subclasses extend via `super()`."""
        return {
            "running": False,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "current": "",
            "started_at": None,
            "finished_at": None,
            "error": None,
            "cancelled": False,
            "cancel_requested": False,
        }

    def get_status(self) -> Dict[str, Any]:
        with self._job_lock:
            status = dict(self._status)
        # Derived from the event rather than read from the dict: the event is
        # what the runner actually polls, so this cannot drift from it.
        status["cancel_requested"] = self._cancel.is_set()
        return status

    def is_running(self) -> bool:
        return bool(self.get_status().get("running"))

    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> bool:
        """Request a cooperative cancel; True if a job was running."""
        with self._job_lock:
            if not self._status.get("running"):
                return False
            self._status["cancel_requested"] = True
        self._cancel.set()
        return True

    def _refuse(self, message: str) -> bool:
        self.last_refusal = message
        return False

    def _start(self, run: Callable[[], None], **initial_status: Any) -> bool:
        """Acquire, flip to running, spawn. False (with `last_refusal`) if busy.

        `initial_status` is merged over the idle status — `total`, `creator`,
        whatever the subclass wants visible from the first poll.
        """
        # Both checks under `_job_lock`, and *this* order, which is the one the
        # hand-rolled managers got wrong. They acquired first and checked
        # `running` second — but re-acquiring your own lease legally succeeds,
        # so a duplicate start slipped through to the `release()` in the
        # already-running branch and dropped the lease out from under the job
        # still using it. The next contender then sailed through: two jobs, one
        # Ollama, from a double-clicked button.
        #
        # Lock order is always `_job_lock` → registry lock (LEASES never calls
        # back into a job), so holding both here cannot invert.
        with self._job_lock:
            if self._status.get("running"):
                return self._refuse(self.busy_message)
            blocker = LEASES.acquire(self.resources, self.owner)
            if blocker:
                holder = LEASES.holder(blocker) or "another job"
                return self._refuse(f"{holder} is using {self.busy_noun}")
            self.last_refusal = ""
            self._cancel.clear()
            self._status = {
                **self._idle_status(),
                "running": True,
                "started_at": datetime.now(timezone.utc).isoformat(),
                **initial_status,
            }

        threading.Thread(target=self._guarded(run), daemon=True).start()
        return True

    def _guarded(self, run: Callable[[], None]) -> Callable[[], None]:
        def guarded() -> None:
            try:
                run()
            except Exception as exc:
                log.exception("%s job crashed", self.owner or type(self).__name__)
                with self._job_lock:
                    self._status["error"] = str(exc)
            finally:
                # Released before the status flip so a client that sees
                # running=False can immediately start the next job.
                LEASES.release(self.owner)
                self._cancel.clear()
                with self._job_lock:
                    self._finalise()

        return guarded

    def _finalise(self) -> None:
        """Last write to the status dict. Called under `_job_lock`.

        Subclasses that need a closing computation override this and call
        `super()._finalise()`.
        """
        self._status["running"] = False
        self._status["cancel_requested"] = False
        self._status["finished_at"] = datetime.now(timezone.utc).isoformat()
        self._status["current"] = ""
