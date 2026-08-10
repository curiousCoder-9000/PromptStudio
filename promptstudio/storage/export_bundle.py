"""E1 — export and re-import derived state as one portable file.

Derived state is everything the archive cannot re-download: prompts and
keep/reject verdicts that cost GPU hours, favourites and generation ratings
that are the user's own judgement, creator styles, perceptual hashes, the
generation index with its seeds, and the A4 workflows the user imported and
slotted by hand.

**Media is not in the bundle.** It is the one thing that can be fetched again,
and including it would turn a portable file into a second copy of the archive.
The bundle is keyed by archive-relative path, so it restores onto any machine
whose archive has the same layout.

`photos` is not exportable either — it is an index *of* the media on disk and
`rebuild()` re-derives it. Restoring it would resurrect rows for files that are
not there.

Why this exists (product_review.md E1): hundreds of GPU-hours on one disk. It
also covers the smaller case — a prompt re-run invalidated a version and the
old one is wanted back, which is a single-kind import.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from promptstudio.logging_setup import get_logger
from promptstudio.storage.atomic import atomic_write_json

log = get_logger(__name__)

# Bumped when the payload shape changes incompatibly. Import refuses anything
# newer: half-applying a bundle it does not understand would leave the archive
# in a state nobody can see or undo.
BUNDLE_VERSION = 1

# Order matters on import — prompts before generations so a restored archive is
# coherent at every intermediate step if the run is interrupted.
DERIVED_KINDS = (
    "prompts",
    "favorites",
    "styles",
    "verdicts",
    "phashes",
    "generations",
    "workflows",
)

# The kinds backed directly by a table. `favorites`, `styles` and `workflows`
# are file-backed and each has a branch in `_collect` / `_apply` instead.
_TABLE_FOR_KIND = {
    "prompts": "prompts",
    "verdicts": "media_verdicts",
    "phashes": "phashes",
    "generations": "generations",
}


def _resolve_kinds(kinds: Optional[Iterable[str]]) -> List[str]:
    if kinds is None:
        return list(DERIVED_KINDS)
    wanted = [str(k).strip() for k in kinds if str(k).strip()]
    unknown = [k for k in wanted if k not in DERIVED_KINDS]
    if unknown:
        raise ValueError(
            f"unknown kind(s): {', '.join(sorted(unknown))}. "
            f"Known: {', '.join(DERIVED_KINDS)}"
        )
    # Keep canonical order regardless of how the caller listed them.
    return [k for k in DERIVED_KINDS if k in wanted]


def _collect_workflows() -> Dict[str, Dict[str, Any]]:
    """A4 registry entries the *user* owns, as `{name: {slots, graph}}`.

    Built-ins are skipped: `pro` and `txt2img` ship with the package, so a copy
    in the bundle would only ever restore a stale version of a file the checkout
    already has — and, because a user entry shadows a built-in, that stale copy
    would win. What is worth carrying is the graph the user exported out of
    ComfyUI and slotted by hand, which no reinstall brings back.
    """
    from promptstudio.comfy import registry

    out: Dict[str, Dict[str, Any]] = {}
    for spec in registry.list_workflows():
        if spec.builtin:
            continue
        try:
            with open(
                os.path.join(spec.directory, registry.SLOTS_FILE), "r", encoding="utf-8"
            ) as f:
                slots = json.load(f)
            out[spec.name] = {"slots": slots, "graph": spec.load_graph()}
        except (OSError, ValueError) as exc:
            log.warning("skipping workflow %s in export: %s", spec.name, exc)
    return out


def _apply_workflows(payload: Any) -> int:
    from promptstudio.comfy import registry
    from promptstudio.config import COMFY_WORKFLOWS_DIR

    applied = 0
    for name, entry in (payload or {}).items():
        # The bundle names directories, and a bundle is a file from elsewhere.
        # Anything with a separator in it would *write* outside the registry
        # root, so it is refused rather than sanitised — one containment rule,
        # the registry's.
        safe = str(name).strip()
        if not registry.is_valid_name(safe):
            log.warning("refusing workflow name %r from bundle", name)
            continue
        if not isinstance(entry, dict) or "slots" not in entry or "graph" not in entry:
            log.warning("workflow %r in bundle has no slots/graph pair", safe)
            continue
        directory = os.path.join(COMFY_WORKFLOWS_DIR, safe)
        os.makedirs(directory, exist_ok=True)
        # atomic_write_json, not open(..., "w"): a truncated slots.json reads as
        # a workflow that has no slots (hard rule 9).
        atomic_write_json(os.path.join(directory, registry.SLOTS_FILE), entry["slots"])
        atomic_write_json(os.path.join(directory, registry.GRAPH_FILE), entry["graph"])
        applied += 1
    return applied


def _collect(kind: str) -> Any:
    from promptstudio.prompts.styles import CreatorStyleStore
    from promptstudio.storage.db import ArchiveIndex
    from promptstudio.storage.favorites import FavoritesStore

    if kind == "favorites":
        return sorted(FavoritesStore().load())
    if kind == "styles":
        return CreatorStyleStore().load()
    if kind == "workflows":
        return _collect_workflows()
    return ArchiveIndex.get().dump_table(_TABLE_FOR_KIND[kind])


def _count(payload: Any) -> int:
    if isinstance(payload, dict):
        return len(payload)
    if isinstance(payload, list):
        return len(payload)
    return 0


def export_derived(
    path: str, kinds: Optional[Iterable[str]] = None
) -> Dict[str, int]:
    """Write a bundle to `path`. Returns per-kind counts.

    A `.gz` extension gzips it — prompts dominate the size and compress well,
    and choosing by extension means neither side needs a flag.
    """
    selected = _resolve_kinds(kinds)
    payload = {kind: _collect(kind) for kind in selected}
    bundle = {
        "version": BUNDLE_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "kinds": selected,
        "payload": payload,
    }
    if path.endswith(".gz"):
        # Written whole then moved, for the same reason atomic_write_json
        # exists: a truncated bundle reads as an empty one.
        tmp = f"{path}.tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(bundle, f)
        os.replace(tmp, path)
    else:
        atomic_write_json(path, bundle)
    summary = {kind: _count(payload[kind]) for kind in selected}
    log.info("exported derived state to %s: %s", path, summary)
    return summary


def _read_bundle(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "payload" not in data:
        raise ValueError(f"{path} is not a PromptStudio derived-state bundle")
    version = int(data.get("version") or 0)
    if version > BUNDLE_VERSION:
        raise ValueError(
            f"bundle version {version} is newer than this build understands "
            f"({BUNDLE_VERSION}) — upgrade before importing"
        )
    return data


def _apply(kind: str, payload: Any) -> int:
    from promptstudio.prompts.cache import PromptCache
    from promptstudio.prompts.styles import CreatorStyleStore
    from promptstudio.storage.db import ArchiveIndex
    from promptstudio.storage.favorites import FavoritesStore

    if kind == "favorites":
        store = FavoritesStore()
        # Union, not replace: importing a backup onto a live archive should not
        # silently un-favourite everything starred since the export.
        merged = store.load() | {str(p) for p in (payload or [])}
        store.save(merged)
        store.invalidate_memory()
        return len(payload or [])
    if kind == "styles":
        store = CreatorStyleStore()
        merged = dict(store.load())
        merged.update(payload or {})
        store.save(merged)
        return len(payload or {})
    if kind == "workflows":
        return _apply_workflows(payload)

    applied = ArchiveIndex.get().load_table(_TABLE_FOR_KIND[kind], payload or [])
    if kind == "prompts":
        # The in-memory prompt cache is write-through and now stale.
        PromptCache().invalidate_memory()
    return applied


def import_derived(
    path: str,
    kinds: Optional[Iterable[str]] = None,
    *,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Restore a bundle. Returns per-kind counts of what was (or would be) applied.

    Idempotent: every kind has a natural key, so re-running a half-finished
    restore overwrites rather than duplicating. That matters because the failure
    this exists for is a machine that died mid-something.
    """
    data = _read_bundle(path)
    payload = data.get("payload") or {}
    available = [k for k in DERIVED_KINDS if k in payload]
    selected = _resolve_kinds(kinds)
    selected = [k for k in selected if k in available]

    summary: Dict[str, int] = {}
    for kind in selected:
        if dry_run:
            summary[kind] = _count(payload[kind])
            continue
        summary[kind] = _apply(kind, payload[kind])
    log.info(
        "%s derived state from %s: %s",
        "would import" if dry_run else "imported",
        path,
        summary,
    )
    return summary
