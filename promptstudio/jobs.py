"""Exclusive leases on shared external resources.

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
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Dict, Iterable, Iterator, List, Optional

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
