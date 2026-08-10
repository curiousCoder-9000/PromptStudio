"""Archive path containment — one implementation, used by every caller.

A plain ``full.startswith(base)`` is **not** containment. With base
``~/Pictures/InstagramSaved``, the path ``../InstagramSaved_backup/x.jpg``
normalizes to a sibling directory that shares the prefix, so the naive check
passes and the path resolves outside the archive. A ``_backup`` / ``_old`` / ``2``
sibling next to a media archive is an ordinary setup, so this is reachable in
practice rather than theoretical.

The fix is to compare on a path *boundary*: a candidate is contained if it is
the base itself or starts with ``base + os.sep``.

This lived correctly in ``ArchiveStore.resolve_path`` and incorrectly in two
other copies (``comfy.client.resolve_archive_file``, ``TrashStore.restore``),
which is what this module exists to prevent. Anything that turns
archive-relative input into a filesystem path goes through ``safe_join``.

``comfy.client.resolve_archive_file`` no longer exists — A0 deleted it and the
Comfy runner now calls ``ArchiveStore.resolve_path`` directly, so there is one
resolver rather than two agreeing by convention.
"""

from __future__ import annotations

import os
from typing import Optional

__all__ = ["contains", "safe_join"]


def contains(base: str, candidate: str) -> bool:
    """True if `candidate` is `base` itself or lives underneath it.

    Both sides are normalized first, so ``a/b/../c`` is compared as ``a/c``.
    """
    base_n = os.path.normpath(base)
    cand_n = os.path.normpath(candidate)
    return cand_n == base_n or cand_n.startswith(base_n + os.sep)


def safe_join(base: str, rel_path: str) -> Optional[str]:
    """Join `rel_path` onto `base`, or return None if it escapes.

    Returns the normalized absolute-ish path on success. An absolute or
    parent-traversing `rel_path` yields None rather than a path outside the
    archive — ``os.path.join(base, "/etc/passwd")`` discards `base` entirely,
    and the containment check is what catches it.

    Existence is *not* checked; callers decide whether the target must already
    exist (reads) or must not (restore into the archive).
    """
    base_n = os.path.normpath(base)
    full = os.path.normpath(os.path.join(base_n, rel_path))
    return full if contains(base_n, full) else None
