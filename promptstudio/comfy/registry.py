"""A4 — the workflow registry. Graphs are data; nothing here is graph-specific.

Before this, the Pro graph was addressed through seven module-level node-id
constants and a hand-written injector, and the FaceDetailer's second seed input
was a literal `if` in that injector. A second graph meant a second injector,
which is why "one workflow, hardcoded" was the product's ceiling
(`product_review.md` §3).

A workflow is now a directory:

    <root>/<name>/graph.json    # ComfyUI "Export (API)" output, untouched
    <root>/<name>/slots.json    # where this app's runtime values go

`slots.json` names the workflow, labels it for the picker, declares its `kind`,
and maps each slot to `{"node": "6", "field": "text"}` — **or to a list of
those**. The list form is what deletes the FaceDetailer special case: `pro`
declares `seed` at nodes 9 and 22 and the injector never learns why.

Two roots, searched in order:

* the package's own `workflows/` — `pro` and `txt2img` ship there, so a fresh
  checkout with an empty archive can still generate;
* `COMFY_WORKFLOWS_DIR` (`<archive>/_workflows`) — the user's own imports, which
  **shadow** a built-in of the same name. Overriding `pro` with your own export
  should not require editing the package.

Not cached. A graph is ~30 nodes of JSON read once per generation, against a run
that takes tens of seconds on a GPU; caching it would trade a measurable nothing
for a stale-file bug (hard rule 13 — measure before optimising).
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from promptstudio.config import (
    COMFY_BUILTIN_WORKFLOWS_DIR,
    COMFY_WORKFLOWS_DIR,
    COMFYUI_DEFAULT_DENOISE,
)
from promptstudio.logging_setup import get_logger

log = get_logger(__name__)

GRAPH_FILE = "graph.json"
SLOTS_FILE = "slots.json"

KINDS = ("img2img", "txt2img")

# What a request that names no workflow gets, and what the picker preselects.
# `pro` is a built-in, so this only fails to resolve if the user has deleted it
# from the package — `default_workflow()` falls back to whatever is left.
DEFAULT_WORKFLOW = "pro"

# Every slot this app knows how to fill, and the type it is coerced to before
# injection. The coercion is not cosmetic: ComfyUI validates input types, and
# the legacy builders cast at every injection site (`int(steps)`, `float(cfg)`).
# Keeping the table here means a new workflow gets the same casts for free.
SLOT_TYPES: Dict[str, type] = {
    "positive": str,
    "negative": str,
    "image": str,
    "checkpoint": str,
    "filename_prefix": str,
    "seed": int,
    "steps": int,
    "width": int,
    "height": int,
    "cfg": float,
    "denoise": float,
}
SLOT_NAMES = tuple(SLOT_TYPES)

# Without these a run cannot be assembled at all, so their absence is a load
# error rather than a surprise at queue time.
REQUIRED_SLOTS: Dict[str, Tuple[str, ...]] = {
    "img2img": ("positive", "image", "seed"),
    "txt2img": ("positive", "seed"),
}


class WorkflowError(ValueError):
    """A workflow directory is unusable. The message always names the slot,
    node or field at fault — design §3.6: "never a generic failure"."""


class UnknownWorkflowError(WorkflowError):
    def __init__(self, name: str, known: Sequence[str]) -> None:
        self.name = name
        listed = ", ".join(known) if known else "none"
        super().__init__(f"unknown workflow {name!r}. Available: {listed}")


@dataclass(frozen=True)
class SlotRef:
    node: str
    field: str


@dataclass(frozen=True)
class WorkflowSpec:
    name: str
    label: str
    kind: str
    slots: Dict[str, Tuple[SlotRef, ...]]
    directory: str
    builtin: bool

    def has(self, slot: str) -> bool:
        return slot in self.slots

    @property
    def needs_image(self) -> bool:
        """Does a run of this workflow have to upload a reference photo?

        Derived from the slot map, not from `kind`, because that is the fact the
        runner actually acts on.
        """
        return self.has("image")

    def summary(self) -> Dict[str, str]:
        """What the picker needs. Deliberately not the slot map: the UI has no
        use for node ids and exposing them invites a client-side injector."""
        return {"name": self.name, "label": self.label, "kind": self.kind}

    def load_graph(self) -> Dict[str, Any]:
        path = os.path.join(self.directory, GRAPH_FILE)
        return _read_graph(path, self.name)


# ── loading ──────────────────────────────────────────────────────────


def workflow_roots() -> List[Tuple[str, bool]]:
    """(directory, is_builtin), lowest precedence first."""
    return [(COMFY_BUILTIN_WORKFLOWS_DIR, True), (COMFY_WORKFLOWS_DIR, False)]


def is_valid_name(name: str) -> bool:
    """A workflow name is a directory name, never a path.

    It arrives straight off an HTTP body and is joined onto the registry root,
    so `..` would read the parent and an absolute path would make `os.path.join`
    discard the root entirely — `get_workflow("/etc")` really did try to open
    `/etc/slots.json` before this check existed. Same containment rule as
    `storage/paths.py`, reached by refusing separators outright rather than
    normalising after the fact.
    """
    wanted = (name or "").strip()
    if not wanted or wanted in (".", ".."):
        return False
    if wanted != os.path.basename(wanted) or "/" in wanted or "\\" in wanted:
        return False
    return not os.path.isabs(wanted)


def _read_json(path: str, name: str, what: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise WorkflowError(f"workflow {name!r}: {what} is missing ({path})") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"workflow {name!r}: {what} is not readable JSON — {exc}") from None


def _read_graph(path: str, name: str) -> Dict[str, Any]:
    graph = _read_json(path, name, GRAPH_FILE)
    if not isinstance(graph, dict) or not graph:
        raise WorkflowError(
            f"workflow {name!r}: {GRAPH_FILE} must be a non-empty ComfyUI API export"
        )
    return graph


def _parse_slot(name: str, slot: str, raw: Any) -> Tuple[SlotRef, ...]:
    entries = raw if isinstance(raw, list) else [raw]
    if not entries:
        raise WorkflowError(f"workflow {name!r}: slot {slot!r} maps to nothing")
    refs: List[SlotRef] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise WorkflowError(
                f"workflow {name!r}: slot {slot!r} must be "
                '{"node": "<id>", "field": "<input>"} or a list of those'
            )
        node = str(entry.get("node") or "").strip()
        field = str(entry.get("field") or "").strip()
        if not node:
            raise WorkflowError(f"workflow {name!r}: slot {slot!r} is missing 'node'")
        if not field:
            raise WorkflowError(f"workflow {name!r}: slot {slot!r} is missing 'field'")
        refs.append(SlotRef(node=node, field=field))
    return tuple(refs)


def _validate_against_graph(spec: WorkflowSpec, graph: Dict[str, Any]) -> None:
    """Every referenced node id and input must exist in graph.json.

    Checked at load, not at inject: a typo'd node id would otherwise surface as
    a generation that ran happily with the user's prompt silently dropped.
    """
    for slot, refs in spec.slots.items():
        for ref in refs:
            node = graph.get(ref.node)
            if not isinstance(node, dict):
                raise WorkflowError(
                    f"workflow {spec.name!r}: slot {slot!r} references node {ref.node!r}, "
                    f"which is not in {GRAPH_FILE}"
                )
            inputs = node.get("inputs")
            if not isinstance(inputs, dict) or ref.field not in inputs:
                raise WorkflowError(
                    f"workflow {spec.name!r}: slot {slot!r} references input {ref.field!r} "
                    f"on node {ref.node!r} ({node.get('class_type') or 'unknown class'}), "
                    f"which that node does not have"
                )


def load_workflow(directory: str, *, builtin: bool = False) -> WorkflowSpec:
    """Read and fully validate one workflow directory."""
    name_hint = os.path.basename(os.path.normpath(directory))
    raw = _read_json(os.path.join(directory, SLOTS_FILE), name_hint, SLOTS_FILE)
    if not isinstance(raw, dict):
        raise WorkflowError(f"workflow {name_hint!r}: {SLOTS_FILE} must be a JSON object")

    name = str(raw.get("name") or name_hint).strip()
    if name != name_hint:
        raise WorkflowError(
            f"workflow {name_hint!r}: {SLOTS_FILE} declares name {name!r}; "
            "the directory name is the id and the two must agree"
        )
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise WorkflowError(
            f"workflow {name!r}: kind {kind or '(missing)'!r} is not one of {', '.join(KINDS)}"
        )

    raw_slots = raw.get("slots")
    if not isinstance(raw_slots, dict) or not raw_slots:
        raise WorkflowError(f"workflow {name!r}: {SLOTS_FILE} has no 'slots' object")
    unknown = [s for s in raw_slots if s not in SLOT_TYPES]
    if unknown:
        raise WorkflowError(
            f"workflow {name!r}: unknown slot(s) {', '.join(sorted(unknown))}. "
            f"Known: {', '.join(SLOT_NAMES)}"
        )
    slots = {slot: _parse_slot(name, slot, raw_slots[slot]) for slot in raw_slots}

    missing = [s for s in REQUIRED_SLOTS[kind] if s not in slots]
    if missing:
        raise WorkflowError(
            f"workflow {name!r}: kind {kind!r} requires slot(s) "
            f"{', '.join(missing)} — none declared in {SLOTS_FILE}"
        )
    if kind == "txt2img" and "image" in slots:
        raise WorkflowError(
            f"workflow {name!r}: kind 'txt2img' declares an 'image' slot; "
            "a workflow that takes a reference photo is kind 'img2img'"
        )

    spec = WorkflowSpec(
        name=name,
        label=str(raw.get("label") or name).strip() or name,
        kind=kind,
        slots=slots,
        directory=directory,
        builtin=builtin,
    )
    _validate_against_graph(spec, spec.load_graph())
    return spec


def list_workflows() -> List[WorkflowSpec]:
    """Every usable workflow, user entries shadowing built-ins of the same name.

    A broken directory is logged with the specific reason and left out rather
    than taking the picker down with it — one bad import should not cost the
    user the workflows that do work.
    """
    found: Dict[str, WorkflowSpec] = {}
    for root, builtin in workflow_roots():
        if not os.path.isdir(root):
            continue
        for entry in sorted(os.listdir(root)):
            directory = os.path.join(root, entry)
            if entry.startswith((".", "_")) or not os.path.isdir(directory):
                continue
            if not is_valid_name(entry):  # pragma: no cover - listdir cannot produce one
                continue
            try:
                found[entry] = load_workflow(directory, builtin=builtin)
            except WorkflowError as exc:
                log.warning("skipping workflow in %s: %s", directory, exc)
    return [found[name] for name in sorted(found)]


def get_workflow(name: str) -> WorkflowSpec:
    """One workflow by name. Raises `UnknownWorkflowError` if there is no such
    entry, and `WorkflowError` naming the fault if there is one but it is broken.
    """
    wanted = (name or "").strip()
    if not is_valid_name(wanted):
        raise UnknownWorkflowError(name, [s.name for s in list_workflows()])
    for root, builtin in reversed(workflow_roots()):
        directory = os.path.join(root, wanted)
        if os.path.isdir(directory):
            return load_workflow(directory, builtin=builtin)
    raise UnknownWorkflowError(wanted, [s.name for s in list_workflows()])


def workflow_names() -> List[str]:
    return [spec.name for spec in list_workflows()]


def default_workflow(available: Optional[Sequence[str]] = None) -> str:
    """The name the picker preselects. Never a name that is not in the list —
    a picker whose default is missing renders blank and generates nothing."""
    names = list(available) if available is not None else workflow_names()
    if DEFAULT_WORKFLOW in names:
        return DEFAULT_WORKFLOW
    return names[0] if names else DEFAULT_WORKFLOW


# ── injection ────────────────────────────────────────────────────────


def _slot_values(
    spec: WorkflowSpec,
    params: Any,
    *,
    seed: int,
    image_name: Optional[str],
    filename_prefix: Optional[str],
    checkpoint: Optional[str],
) -> Dict[str, Any]:
    """Resolve every declared slot to a value, or to None for "leave the graph
    alone". Only slots the workflow actually declares are looked at, so adding a
    field to `GenerationParams` never touches a graph that does not want it."""
    values: Dict[str, Any] = {
        "positive": getattr(params, "positive", None),
        "negative": getattr(params, "negative", None),
        "seed": seed,
        "steps": getattr(params, "steps", None),
        "cfg": getattr(params, "cfg", None),
        "image": image_name,
        "filename_prefix": filename_prefix,
        "checkpoint": checkpoint if checkpoint is not None else getattr(params, "checkpoint", None),
    }
    if spec.has("denoise"):
        denoise = getattr(params, "denoise", None)
        # An img2img graph without a denoise is not a run, it is a full
        # re-render of the reference. The configured default stands in.
        values["denoise"] = COMFYUI_DEFAULT_DENOISE if denoise is None else denoise
    if spec.has("width") or spec.has("height"):
        from promptstudio.comfy.client import aspect_to_size

        width, height = aspect_to_size(getattr(params, "aspect", "") or "4:5")
        values["width"] = width
        values["height"] = height
    return values


def build_graph(
    workflow: Any,
    params: Any,
    *,
    seed: int,
    image_name: Optional[str] = None,
    filename_prefix: Optional[str] = None,
    checkpoint: Optional[str] = None,
) -> Dict[str, Any]:
    """A ready-to-queue ComfyUI API graph.

    `workflow` is a registry name or a `WorkflowSpec`; `params` is a
    `GenerationParams` (or anything with the same attribute names).

    `seed` is required and already resolved — see `client.resolve_seed` for why
    materialising it exactly once, before anything uses it, is load-bearing.

    A slot whose value is `None` is left at whatever the graph shipped with. That
    is what makes "no checkpoint override" mean "use the one in the export"
    rather than "blank the field".
    """
    spec = workflow if isinstance(workflow, WorkflowSpec) else get_workflow(workflow)
    if spec.needs_image and not image_name:
        raise WorkflowError(
            f"workflow {spec.name!r} declares an 'image' slot, so it needs an uploaded "
            "reference; none was given"
        )

    graph = copy.deepcopy(spec.load_graph())
    values = _slot_values(
        spec,
        params,
        seed=seed,
        image_name=image_name,
        filename_prefix=filename_prefix,
        checkpoint=checkpoint,
    )
    for slot, refs in spec.slots.items():
        value = values.get(slot)
        if value is None:
            continue
        cast = SLOT_TYPES[slot]
        for ref in refs:
            # Validated at load: the node and its input both exist.
            graph[ref.node]["inputs"][ref.field] = cast(value)
    return graph
