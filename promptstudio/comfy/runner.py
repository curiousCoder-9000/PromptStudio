"""Execute one generation against ComfyUI.

No lease, no thread, no singleton, no status dict — those belong to the job
that owns the runner. What is here is the part that is identical whether the
image was asked for from the lightbox or is item 37 of a batch: upload the
reference, inject the graph, queue it, poll, download, save, index.

It is a class rather than a function so a job can hand it progress callbacks
and so the ComfyUI I/O is one seam to fake in tests.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from promptstudio.comfy.params import GenerationParams
from promptstudio.comfy.registry import build_graph, get_workflow
from promptstudio.config import (
    COMFY_BATCH_ITEM_TIMEOUT,
    COMFYUI_CHECKPOINT,
    COMFYUI_URL,
    GENERATIONS_DIR,
)
from promptstudio.logging_setup import get_logger

log = get_logger(__name__)

ProgressFn = Optional[Callable[[str], None]]
PromptIdFn = Optional[Callable[[str], None]]


def _creator_and_base(rel_path: str) -> tuple:
    """('creator', 'photo_1') from 'creator/photo_1.jpg'."""
    creator = rel_path.replace("\\", "/").split("/", 1)[0]
    return creator, os.path.splitext(os.path.basename(rel_path))[0]


class ComfyRunner:
    """One generation, start to indexed row."""

    def __init__(
        self,
        *,
        on_progress: ProgressFn = None,
        on_prompt_id: PromptIdFn = None,
        item_timeout: Optional[int] = None,
    ) -> None:
        self._on_progress = on_progress
        self._on_prompt_id = on_prompt_id
        self.item_timeout = int(item_timeout or COMFY_BATCH_ITEM_TIMEOUT)

    # ── progress plumbing ────────────────────────────────────────────

    def _progress(self, text: str) -> None:
        if self._on_progress:
            self._on_progress(text)

    def _announce_prompt_id(self, prompt_id: str) -> None:
        if self._on_prompt_id:
            self._on_prompt_id(prompt_id)

    # ── the run ──────────────────────────────────────────────────────

    def run(
        self,
        params: GenerationParams,
        *,
        seed: int,
        batch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate one image. Returns the saved record.

        `seed` is required and already resolved — see `client.resolve_seed` for
        why materialising it exactly once, before anything uses it, is the whole
        point.

        There is one path, not one per workflow. Everything that used to differ
        between `_run_pro` and `_run_txt2img` — inject here, inject there,
        upload or not — is now read off the slot map (A4): a workflow needs a
        reference upload exactly when it declares an `image` slot.
        """
        spec = get_workflow(params.workflow)

        image_name: Optional[str] = None
        filename_prefix: Optional[str] = None
        if spec.needs_image:
            image_name = self._upload_reference(params.rel_path)
        if spec.has("filename_prefix"):
            creator, base = _creator_and_base(params.rel_path)
            filename_prefix = f"promptstudio/{creator}/{base}"

        checkpoint = params.checkpoint
        if checkpoint is None and spec.kind == "txt2img":
            # A txt2img graph is a skeleton with no checkpoint worth keeping, so
            # the configured default fills it. An img2img graph is the user's own
            # ComfyUI export — "no override" there means "use the one you
            # exported", not "substitute mine".
            checkpoint = COMFYUI_CHECKPOINT

        graph = build_graph(
            spec,
            params,
            seed=seed,
            image_name=image_name,
            filename_prefix=filename_prefix,
            checkpoint=checkpoint,
        )
        self._progress(f"Queueing {spec.label}…")
        outputs = self._execute(graph, f"Generating ({spec.label})…")

        extra: Dict[str, Any] = {
            "workflow": spec.name,
            "steps": params.steps,
            "cfg": params.cfg,
            "seed": seed,
            "checkpoint": checkpoint,
            "mode_e": params.mode_e,
            "prompt_version": params.prompt_version,
            "batch_id": batch_id,
        }
        if params.denoise is not None:
            extra["denoise"] = params.denoise
        if image_name:
            extra["reference"] = image_name
        return self._save_outputs(
            params.rel_path, outputs, params.positive, params.negative, extra=extra
        )

    def _upload_reference(self, rel_path: str) -> str:
        from promptstudio.comfy.client import upload_image_to_comfy
        from promptstudio.storage.archive import ArchiveStore

        self._progress("Uploading reference…")
        # One containment check in the codebase, not two. Escaped and missing
        # collapse to the same refusal on purpose: the caller cannot act on the
        # difference, and the old FileNotFoundError(full) put an absolute
        # filesystem path into a user-visible job error.
        full = ArchiveStore().resolve_path(rel_path)
        if not full:
            raise FileNotFoundError(f"Reference image not found in archive: {rel_path}")
        creator, base = _creator_and_base(rel_path)
        upload_name = f"ps_{creator}_{base}{os.path.splitext(full)[1] or '.jpg'}"
        # Keep upload filename filesystem-safe
        upload_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in upload_name)
        return upload_image_to_comfy(full, filename=upload_name, overwrite=True)

    def _execute(self, graph: Dict[str, Any], progress: str) -> List[Dict[str, str]]:
        prompt_id = self._queue_prompt(graph, str(uuid.uuid4()))
        self._announce_prompt_id(prompt_id)
        self._progress(progress)
        outputs = self._wait_for_images(prompt_id, timeout_sec=self.item_timeout)
        if not outputs:
            raise RuntimeError("ComfyUI finished with no image outputs")
        return outputs

    # ── ComfyUI I/O ──────────────────────────────────────────────────

    def _queue_prompt(self, workflow: Dict[str, Any], client_id: str) -> str:
        body = json.dumps({"prompt": workflow, "client_id": client_id}).encode("utf-8")
        req = urllib.request.Request(
            f"{COMFYUI_URL}/prompt",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI queue failed: {data}")
        node_errors = data.get("node_errors") or {}
        if node_errors:
            raise RuntimeError(f"ComfyUI node errors: {node_errors}")
        return prompt_id

    def _wait_for_images(
        self, prompt_id: str, timeout_sec: int = 600
    ) -> List[Dict[str, str]]:
        import time

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            req = urllib.request.Request(
                f"{COMFYUI_URL}/history/{urllib.parse.quote(prompt_id)}",
                method="GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    hist = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    time.sleep(1.5)
                    continue
                raise
            entry = hist.get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error" or status.get("completed") is False:
                    messages = status.get("messages") or []
                    raise RuntimeError(f"ComfyUI execution error: {messages}")
                outputs = entry.get("outputs") or {}
                images: List[Dict[str, str]] = []
                for node_out in outputs.values():
                    for img in node_out.get("images") or []:
                        images.append(
                            {
                                "filename": img.get("filename", ""),
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output"),
                            }
                        )
                if images:
                    return images
            time.sleep(1.5)
        raise TimeoutError("Timed out waiting for ComfyUI result")

    def _download_image(self, meta: Dict[str, str]) -> bytes:
        qs = urllib.parse.urlencode(
            {
                "filename": meta["filename"],
                "subfolder": meta.get("subfolder") or "",
                "type": meta.get("type") or "output",
            }
        )
        req = urllib.request.Request(f"{COMFYUI_URL}/view?{qs}", method="GET")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    def interrupt(self, prompt_id: Optional[str]) -> bool:
        """Stop the run in flight. True if ComfyUI was actually told to stop.

        `/interrupt` has no argument — it kills whatever is executing. So it is
        only sent when the head of the running queue is *our* prompt; otherwise
        cancelling a PromptStudio batch would kill an unrelated job the user
        started in the ComfyUI tab (design §8). The pending copy is dropped by
        id either way, which is unambiguous.
        """
        if not prompt_id:
            return False
        stopped = False
        try:
            if self._is_running_prompt(prompt_id):
                req = urllib.request.Request(
                    f"{COMFYUI_URL}/interrupt", data=b"", method="POST"
                )
                with urllib.request.urlopen(req, timeout=10):
                    pass
                stopped = True
        except Exception as exc:
            log.warning("could not interrupt ComfyUI prompt %s: %s", prompt_id, exc)
        try:
            body = json.dumps({"delete": [prompt_id]}).encode("utf-8")
            req = urllib.request.Request(
                f"{COMFYUI_URL}/queue",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10):
                pass
        except Exception as exc:
            log.warning("could not drop ComfyUI prompt %s: %s", prompt_id, exc)
        return stopped

    def _is_running_prompt(self, prompt_id: str) -> bool:
        req = urllib.request.Request(f"{COMFYUI_URL}/queue", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            queue = json.loads(resp.read().decode("utf-8"))
        for entry in queue.get("queue_running") or []:
            # [number, prompt_id, prompt, extra_data, outputs_to_execute]
            if isinstance(entry, (list, tuple)) and len(entry) > 1:
                if entry[1] == prompt_id:
                    return True
        return False

    # ── persistence ──────────────────────────────────────────────────

    def _save_outputs(
        self,
        source_rel: str,
        outputs: List[Dict[str, str]],
        positive: str,
        negative: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from promptstudio.comfy.client import GenerationsIndex

        creator = source_rel.replace("\\", "/").split("/", 1)[0]
        base = os.path.splitext(os.path.basename(source_rel))[0]
        out_dir = os.path.join(GENERATIONS_DIR, creator)
        os.makedirs(out_dir, exist_ok=True)
        # Microseconds, not seconds. Two generations of the same photo inside
        # one second produced byte-identical filenames, so the second silently
        # overwrote the first on disk — and with `rel_path` UNIQUE it would now
        # also collapse two rows into one.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        saved_files = []
        for i, meta in enumerate(outputs):
            raw = self._download_image(meta)
            ext = os.path.splitext(meta.get("filename") or "")[1] or ".png"
            name = f"{base}_gen_{stamp}_{i + 1}{ext}"
            full = os.path.join(out_dir, name)
            with open(full, "wb") as f:
                f.write(raw)
            rel = f"_generations/{creator}/{name}".replace("\\", "/")
            saved_files.append(
                {
                    "filename": name,
                    "rel_path": rel,
                    "url": "/media/"
                    + "/".join(urllib.parse.quote(part) for part in rel.split("/")),
                }
            )
        record: Dict[str, Any] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": saved_files,
            # Full text. Truncating to 500/300 kept enough to display and not
            # enough to reproduce, which is the wrong half to keep.
            "positive_prompt": positive,
            "negative_prompt": negative,
            "primary_url": saved_files[0]["url"] if saved_files else None,
            "primary_rel": saved_files[0]["rel_path"] if saved_files else None,
        }
        if extra:
            record.update(extra)
        self._record_in_db(source_rel, creator, record, saved_files)
        GenerationsIndex().add(source_rel, record)
        return record

    def _record_in_db(
        self,
        source_rel: str,
        creator: str,
        record: Dict[str, Any],
        saved_files: List[Dict[str, str]],
    ) -> None:
        """Write one `generations` row per output file.

        Best-effort: the images are already on disk and returned to the caller,
        so an index failure must not turn a successful generation into an error.
        It is logged rather than swallowed — a silently unindexed generation is
        exactly the class of loss A0 exists to stop.
        """
        from promptstudio.storage.db import ArchiveIndex

        try:
            index = ArchiveIndex.get()
            for item in saved_files:
                # Stamped back onto the file entry so the job status — which is
                # all the lightbox has after a generate — can rate the output
                # without a second round trip to look the id up.
                item["gen_id"] = index.record_generation(
                    rel_path=item["rel_path"],
                    source_rel=source_rel,
                    creator=creator,
                    workflow=str(record.get("workflow") or "pro"),
                    seed=record.get("seed"),
                    positive_prompt=record.get("positive_prompt") or "",
                    negative_prompt=record.get("negative_prompt") or "",
                    created_at=record.get("created_at"),
                    batch_id=record.get("batch_id"),
                    checkpoint=record.get("checkpoint"),
                    steps=record.get("steps"),
                    cfg=record.get("cfg"),
                    denoise=record.get("denoise"),
                    mode_e=bool(record.get("mode_e")),
                    prompt_version=record.get("prompt_version"),
                )
        except Exception:
            log.exception("failed to index generation for %s", source_rel)
