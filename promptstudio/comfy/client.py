"""Optional ComfyUI client: queue API workflow, poll, save outputs."""

from __future__ import annotations

import copy
import json
import mimetypes
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from promptstudio.config import (
    COMFYUI_CHECKPOINT,
    COMFYUI_DEFAULT_CFG,
    COMFYUI_DEFAULT_DENOISE,
    COMFYUI_DEFAULT_STEPS,
    COMFYUI_PRO_WORKFLOW,
    COMFYUI_URL,
    GENERATIONS_DIR,
    GENERATIONS_INDEX_FILE,
    GENERATIONS_KEEP_PER_SOURCE,
)
from promptstudio.jobs import COMFY, LEASES
from promptstudio.logging_setup import get_logger
from promptstudio.storage.atomic import atomic_write_json

log = get_logger(__name__)

LEASE_OWNER = "comfy"

# Node ids in modelToimage_pro.api.json (Export API)
PRO_NODE_LOAD_IMAGE = "4"
PRO_NODE_POSITIVE = "6"
PRO_NODE_NEGATIVE = "7"
PRO_NODE_SAMPLER = "9"
PRO_NODE_SAVE = "11"
PRO_NODE_FACE_DETAILER = "22"
PRO_NODE_CHECKPOINT = "1"


def check_comfy_health(timeout: float = 0.4) -> Dict[str, Any]:
    """Probe ComfyUI. Default timeout is short: Comfy is usually off, and a
    stacked 1.5s+1.5s miss made every /api/health (and the UI boot) feel hung.
    """
    try:
        req = urllib.request.Request(f"{COMFYUI_URL}/system_stats", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return {"comfy": True, "url": COMFYUI_URL, "detail": payload.get("system")}
    except Exception:
        try:
            req = urllib.request.Request(f"{COMFYUI_URL}/", method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read(64)
            return {"comfy": True, "url": COMFYUI_URL, "detail": None}
        except Exception:
            return {"comfy": False, "url": COMFYUI_URL, "detail": None}


def resolve_seed(seed: Optional[int]) -> int:
    """Materialise a concrete seed before it is used anywhere.

    The workflow builders used to roll the random seed themselves when handed
    ``None``. The value then existed only as a local: the graph got a real
    number, the generation record got ``None``. Since the UI leaves the seed
    lock off by default, that meant **every** generation made the normal way was
    unreproducible — no regenerate, no A/B on one parameter, no seed comparison.

    Resolve once, up front, and hand the same int to the graph, the job status,
    and the saved record. Builders now require an int so this cannot regress.
    """
    if seed is None:
        return random.randint(0, 2**32 - 1)
    return int(seed)


def aspect_to_size(aspect: str, base: int = 1024) -> Tuple[int, int]:
    aspect = (aspect or "4:5").strip()
    try:
        a, b = aspect.split(":")
        aw, ah = float(a), float(b)
        if aw <= 0 or ah <= 0:
            raise ValueError
        if aw >= ah:
            w = base
            h = int(round(base * ah / aw))
        else:
            h = base
            w = int(round(base * aw / ah))
        w = max(512, (w // 8) * 8)
        h = max(512, (h // 8) * 8)
        return w, h
    except Exception:
        return 896, 1152


def build_txt2img_workflow(
    positive: str,
    negative: str,
    *,
    seed: int,
    width: int = 896,
    height: int = 1152,
    steps: int = 30,
    cfg: float = 7.0,
    checkpoint: str = COMFYUI_CHECKPOINT,
) -> Dict[str, Any]:
    """Minimal CheckpointLoader → KSampler API graph (legacy / txt2img fallback).

    ``seed`` is required: see `resolve_seed`.
    """
    seed = int(seed)
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(seed),
                "steps": int(steps),
                "cfg": float(cfg),
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": int(width), "height": int(height), "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": positive, "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["4", 1]},
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": "promptstudio", "images": ["8", 0]},
        },
    }


def load_pro_workflow_template(path: str = COMFYUI_PRO_WORKFLOW) -> Dict[str, Any]:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"ComfyUI Pro workflow not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not data:
        raise ValueError(f"Invalid ComfyUI Pro workflow: {path}")
    return data


def upload_image_to_comfy(
    local_path: str,
    *,
    filename: Optional[str] = None,
    overwrite: bool = True,
) -> str:
    """POST multipart to ComfyUI /upload/image; return stored filename for LoadImage."""
    if not os.path.isfile(local_path):
        raise FileNotFoundError(local_path)
    name = filename or os.path.basename(local_path)
    name = name.replace("\\", "/").split("/")[-1]
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with open(local_path, "rb") as f:
        raw = f.read()

    boundary = f"----PromptStudio{uuid.uuid4().hex}"
    parts: List[bytes] = []

    def add_field(field: str, value: str) -> None:
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )

    add_field("type", "input")
    add_field("overwrite", "true" if overwrite else "false")
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(raw)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    req = urllib.request.Request(
        f"{COMFYUI_URL}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    stored = payload.get("name")
    if not stored:
        raise RuntimeError(f"ComfyUI upload failed: {payload}")
    sub = (payload.get("subfolder") or "").strip()
    return f"{sub}/{stored}" if sub else stored


def build_pro_workflow(
    *,
    image_name: str,
    positive: str,
    negative: str,
    seed: int,
    steps: int = COMFYUI_DEFAULT_STEPS,
    cfg: float = COMFYUI_DEFAULT_CFG,
    denoise: float = COMFYUI_DEFAULT_DENOISE,
    checkpoint: Optional[str] = None,
    filename_prefix: str = "promptstudio_pro",
) -> Dict[str, Any]:
    """Clone modelToimage_pro API graph and inject runtime inputs.

    ``seed`` is required: see `resolve_seed`.
    """
    workflow = copy.deepcopy(load_pro_workflow_template())
    seed = int(seed)

    def node(nid: str) -> Dict[str, Any]:
        n = workflow.get(nid)
        if not isinstance(n, dict) or "inputs" not in n:
            raise KeyError(f"Pro workflow missing node {nid}")
        return n

    node(PRO_NODE_LOAD_IMAGE)["inputs"]["image"] = image_name
    node(PRO_NODE_POSITIVE)["inputs"]["text"] = positive
    node(PRO_NODE_NEGATIVE)["inputs"]["text"] = negative

    sampler_in = node(PRO_NODE_SAMPLER)["inputs"]
    sampler_in["seed"] = seed
    sampler_in["steps"] = int(steps)
    sampler_in["cfg"] = float(cfg)
    sampler_in["denoise"] = float(denoise)

    node(PRO_NODE_SAVE)["inputs"]["filename_prefix"] = filename_prefix

    if PRO_NODE_FACE_DETAILER in workflow:
        fd = node(PRO_NODE_FACE_DETAILER)["inputs"]
        if "seed" in fd:
            fd["seed"] = seed

    if checkpoint and PRO_NODE_CHECKPOINT in workflow:
        node(PRO_NODE_CHECKPOINT)["inputs"]["ckpt_name"] = checkpoint

    return workflow


class GenerationsIndex:
    def __init__(self, path: str = GENERATIONS_INDEX_FILE) -> None:
        self.path = path

    def load(self) -> Dict[str, Any]:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save(self, data: Dict[str, Any]) -> None:
        try:
            atomic_write_json(self.path, data)
        except OSError as e:
            log.error("saving generations index %s: %s", self.path, e)

    def list_for(self, rel_path: str) -> List[Dict[str, Any]]:
        key = rel_path.replace("\\", "/")
        entry = self.load().get(key) or []
        return list(entry) if isinstance(entry, list) else []

    def add(self, rel_path: str, record: Dict[str, Any]) -> None:
        """Append to the legacy JSON index.

        Kept alongside the `generations` table for one release so a rollback is
        possible (design_generation_loop.md §3.1). The table is the source of
        truth; this file is the parachute.
        """
        key = rel_path.replace("\\", "/")
        data = self.load()
        items = list(data.get(key) or [])
        items.insert(0, record)
        # 0 = unbounded. The old hardcoded 20 was silent data loss.
        if GENERATIONS_KEEP_PER_SOURCE > 0:
            items = items[:GENERATIONS_KEEP_PER_SOURCE]
        data[key] = items
        self.save(data)


class ComfyJobManager:
    _instance: Optional["ComfyJobManager"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._job_lock = threading.Lock()
        # Why the last start() returned False, for the API's 409 message.
        self.last_refusal = ""
        self._status: Dict[str, Any] = {
            "running": False,
            "prompt_id": None,
            "source_path": None,
            "progress": "Idle",
            "error": None,
            "result": None,
            "started_at": None,
            "finished_at": None,
            "workflow": None,
            "seed": None,
        }
        self.index = GenerationsIndex()

    @classmethod
    def get(cls) -> "ComfyJobManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = ComfyJobManager()
        return cls._instance

    def get_status(self) -> Dict[str, Any]:
        with self._job_lock:
            return dict(self._status)

    def is_running(self) -> bool:
        return bool(self.get_status().get("running"))

    def start(
        self,
        *,
        source_rel: str,
        positive: str,
        negative: str,
        workflow: str = "pro",
        aspect: str = "4:5",
        steps: Optional[int] = None,
        cfg: Optional[float] = None,
        denoise: Optional[float] = None,
        seed: Optional[int] = None,
        checkpoint: Optional[str] = None,
        mode_e: bool = False,
        prompt_version: Optional[str] = None,
    ) -> bool:
        workflow = (workflow or "pro").lower()
        # Resolve before the thread starts so the caller can report the seed in
        # the HTTP response — the runner is async and would be too late.
        resolved_seed = resolve_seed(seed)
        # One ComfyUI, one job. Taken before the status flip so two requests
        # arriving together cannot both observe running=False and proceed.
        blocker = LEASES.acquire([COMFY], LEASE_OWNER)
        if blocker:
            self.last_refusal = (
                f"{LEASES.holder(blocker) or 'another job'} is using ComfyUI"
            )
            return False
        with self._job_lock:
            if self._status.get("running"):
                LEASES.release(LEASE_OWNER)
                self.last_refusal = "A generation is already running"
                return False
            self.last_refusal = ""
            self._status = {
                "running": True,
                "prompt_id": None,
                "source_path": source_rel,
                "progress": "Queueing…",
                "error": None,
                "result": None,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "workflow": workflow,
                "seed": resolved_seed,
            }

        def runner() -> None:
            try:
                if workflow in ("pro", "modeltoimage_pro", "ref"):
                    self._run_pro(
                        source_rel=source_rel,
                        positive=positive,
                        negative=negative,
                        steps=steps if steps is not None else COMFYUI_DEFAULT_STEPS,
                        cfg=cfg if cfg is not None else COMFYUI_DEFAULT_CFG,
                        denoise=denoise if denoise is not None else COMFYUI_DEFAULT_DENOISE,
                        seed=resolved_seed,
                        checkpoint=checkpoint,
                        mode_e=mode_e,
                        prompt_version=prompt_version,
                    )
                else:
                    self._run_txt2img(
                        source_rel=source_rel,
                        positive=positive,
                        negative=negative,
                        aspect=aspect,
                        steps=steps if steps is not None else 30,
                        cfg=cfg if cfg is not None else 7.0,
                        seed=resolved_seed,
                        checkpoint=checkpoint or COMFYUI_CHECKPOINT,
                        mode_e=mode_e,
                        prompt_version=prompt_version,
                    )
            except Exception as exc:
                log.exception("ComfyUI job failed for %s", source_rel)
                with self._job_lock:
                    self._status["error"] = str(exc)
                    self._status["progress"] = "Failed"
            finally:
                # Release before the status flip so a client that sees
                # running=False can immediately start the next generation.
                LEASES.release(LEASE_OWNER)
                with self._job_lock:
                    self._status["running"] = False
                    self._status["finished_at"] = datetime.now(timezone.utc).isoformat()

        threading.Thread(target=runner, daemon=True).start()
        return True

    def _run_pro(
        self,
        *,
        source_rel: str,
        positive: str,
        negative: str,
        steps: int,
        cfg: float,
        denoise: float,
        seed: int,
        checkpoint: Optional[str],
        mode_e: bool = False,
        prompt_version: Optional[str] = None,
    ) -> None:
        with self._job_lock:
            self._status["progress"] = "Uploading reference…"
        # One containment check in the codebase, not two. Escaped and missing
        # collapse to the same refusal on purpose: the caller cannot act on the
        # difference, and the old FileNotFoundError(full) put an absolute
        # filesystem path into a user-visible job error.
        from promptstudio.storage.archive import ArchiveStore

        full = ArchiveStore().resolve_path(source_rel)
        if not full:
            raise FileNotFoundError(
                f"Reference image not found in archive: {source_rel}"
            )
        creator = source_rel.replace("\\", "/").split("/", 1)[0]
        base = os.path.splitext(os.path.basename(source_rel))[0]
        upload_name = f"ps_{creator}_{base}{os.path.splitext(full)[1] or '.jpg'}"
        # Keep upload filename filesystem-safe
        upload_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in upload_name)
        image_name = upload_image_to_comfy(full, filename=upload_name, overwrite=True)

        prefix = f"promptstudio/{creator}/{base}"
        graph = build_pro_workflow(
            image_name=image_name,
            positive=positive,
            negative=negative,
            seed=seed,
            steps=steps,
            cfg=cfg,
            denoise=denoise,
            checkpoint=checkpoint,
            filename_prefix=prefix,
        )
        with self._job_lock:
            self._status["progress"] = "Queueing Pro workflow…"
        client_id = str(uuid.uuid4())
        prompt_id = self._queue_prompt(graph, client_id)
        with self._job_lock:
            self._status["prompt_id"] = prompt_id
            self._status["progress"] = "Generating (Pro)…"
        outputs = self._wait_for_images(prompt_id, timeout_sec=900)
        if not outputs:
            raise RuntimeError("ComfyUI finished with no image outputs")
        saved = self._save_outputs(
            source_rel,
            outputs,
            positive,
            negative,
            extra={
                "workflow": "pro",
                "denoise": denoise,
                "steps": steps,
                "cfg": cfg,
                "seed": seed,
                "reference": image_name,
                "checkpoint": checkpoint,
                "mode_e": mode_e,
                "prompt_version": prompt_version,
            },
        )
        with self._job_lock:
            self._status["result"] = saved
            self._status["progress"] = "Complete"

    def _run_txt2img(
        self,
        *,
        source_rel: str,
        positive: str,
        negative: str,
        aspect: str,
        steps: int,
        cfg: float,
        seed: int,
        checkpoint: str,
        mode_e: bool = False,
        prompt_version: Optional[str] = None,
    ) -> None:
        w, h = aspect_to_size(aspect)
        graph = build_txt2img_workflow(
            positive,
            negative,
            seed=seed,
            width=w,
            height=h,
            steps=steps,
            cfg=cfg,
            checkpoint=checkpoint,
        )
        client_id = str(uuid.uuid4())
        prompt_id = self._queue_prompt(graph, client_id)
        with self._job_lock:
            self._status["prompt_id"] = prompt_id
            self._status["progress"] = "Generating…"
        outputs = self._wait_for_images(prompt_id, timeout_sec=600)
        if not outputs:
            raise RuntimeError("ComfyUI finished with no image outputs")
        saved = self._save_outputs(
            source_rel,
            outputs,
            positive,
            negative,
            extra={
                "workflow": "txt2img",
                "steps": steps,
                "cfg": cfg,
                "seed": seed,
                "checkpoint": checkpoint,
                "mode_e": mode_e,
                "prompt_version": prompt_version,
            },
        )
        with self._job_lock:
            self._status["result"] = saved
            self._status["progress"] = "Complete"

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

    def _wait_for_images(self, prompt_id: str, timeout_sec: int = 600) -> List[Dict[str, str]]:
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

    def _save_outputs(
        self,
        source_rel: str,
        outputs: List[Dict[str, str]],
        positive: str,
        negative: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
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
                    "url": "/media/" + "/".join(
                        urllib.parse.quote(part) for part in rel.split("/")
                    ),
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
        self.index.add(source_rel, record)
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
                index.record_generation(
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
