"""Optional ComfyUI client: queue API workflow, poll, save outputs."""

from __future__ import annotations

import copy
import json
import mimetypes
import os
import random
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from promptstudio.comfy.params import GenerationParams
from promptstudio.comfy.runner import ComfyRunner
from promptstudio.config import (
    COMFYUI_CHECKPOINT,
    COMFYUI_DEFAULT_CFG,
    COMFYUI_DEFAULT_DENOISE,
    COMFYUI_DEFAULT_STEPS,
    COMFYUI_PRO_WORKFLOW,
    COMFYUI_URL,
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

        is_pro = workflow in ("pro", "modeltoimage_pro", "ref")
        params = GenerationParams(
            rel_path=source_rel,
            positive=positive,
            negative=negative,
            workflow="pro" if is_pro else "txt2img",
            variant=workflow,
            aspect=aspect,
            steps=(steps if steps is not None else (COMFYUI_DEFAULT_STEPS if is_pro else 30)),
            cfg=(cfg if cfg is not None else (COMFYUI_DEFAULT_CFG if is_pro else 7.0)),
            denoise=(
                (denoise if denoise is not None else COMFYUI_DEFAULT_DENOISE)
                if is_pro
                else None
            ),
            seed=resolved_seed,
            checkpoint=checkpoint if is_pro else (checkpoint or COMFYUI_CHECKPOINT),
            mode_e=bool(mode_e),
            prompt_version=prompt_version,
        )

        def runner() -> None:
            try:
                saved = self._runner().run(params, seed=resolved_seed)
                with self._job_lock:
                    self._status["result"] = saved
                    self._status["progress"] = "Complete"
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

    def _runner(self) -> "ComfyRunner":
        """A runner wired to report into this job's status dict.

        The generation itself lives in `ComfyRunner` because A2's batch does
        exactly the same work per item; this class is now only the singleton,
        the lease and the status shape the lightbox polls.
        """

        def progress(text: str) -> None:
            with self._job_lock:
                self._status["progress"] = text

        def prompt_id(pid: str) -> None:
            with self._job_lock:
                self._status["prompt_id"] = pid

        return ComfyRunner(on_progress=progress, on_prompt_id=prompt_id)

