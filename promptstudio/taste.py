"""Taste embeddings, P(keep), semantic search, and embedding near-dups.

Phase 15 B2/C1/C3. One embedding per photo, mined three ways:

* B2 — logistic head on B3 labels → calibrated ``p_keep``, ``sort=foryou``
* C1 — cosine of a query embedding against the same vectors
* C3 — kNN near-dups on cosine, after pHash has had its pass

No new dependencies. sqlite-vec and SigLIP-2 were the sketch; at archive
sizes we actually have (a few thousand rows) brute-force cosine over a
float32 blob is the thing that has been measured to be cheap, and hashed
n-grams over the vision JSON + prompt (already on disk) need no extra
model. Set ``OLLAMA_EMBED_MODEL`` to switch the vectors to Ollama
``/api/embed`` without changing the rest of the pipeline.

The VLM stays for the jobs it is good at: the human-readable brief, and
the structured fields C5 facets already read.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from promptstudio.config import OLLAMA_EMBED_MODEL, OLLAMA_EMBED_URL, TASTE_EMBED_DIM
from promptstudio.jobs import OLLAMA, BackgroundJob
from promptstudio.logging_setup import get_logger
from promptstudio.storage.journal import RunJournal

log = get_logger(__name__)

HASHED_MODEL = "hashed-ngrams"
# C3: two embeddings this close are the same picture under a different crop
# or a cross-creator re-encode that pHash missed. Hashed n-grams of identical
# vision JSON land at 1.0; unrelated photos sit well below 0.4 in tests.
EMBED_DUP_MIN_COSINE = 0.92
_TOKEN = re.compile(r"[a-z0-9]+")


def embed_model_name() -> str:
    return (OLLAMA_EMBED_MODEL or "").strip() or HASHED_MODEL


def text_blob_for_photo(prompt: Optional[Dict[str, Any]], *, fallback: str = "") -> str:
    """The string we embed: vision JSON + prompt, not pixels.

    Pixel embeddings would need SigLIP. The structured vision object and the
    rewritten prompt are the description the archive already paid for, and
    they are what search and taste actually use.
    """
    parts: List[str] = []
    if isinstance(prompt, dict):
        sv = prompt.get("structured_vision")
        if isinstance(sv, dict):
            parts.extend(str(v) for v in sv.values() if v)
        pos = prompt.get("positive_prompt") or ""
        if pos:
            parts.append(str(pos))
        tags = prompt.get("visual_tags") or []
        if isinstance(tags, list):
            parts.extend(str(t) for t in tags if t)
        raw = prompt.get("raw_vision_description") or ""
        if raw:
            parts.append(str(raw))
    blob = " ".join(parts).strip()
    return blob or (fallback or "")


def hashed_embed(text: str, dim: int = TASTE_EMBED_DIM) -> np.ndarray:
    """Signed hashed n-grams, L2-normalised. Deterministic, no model."""
    dim = max(8, int(dim))
    vec = np.zeros(dim, dtype=np.float32)
    tokens = _TOKEN.findall((text or "").lower())
    if not tokens:
        return vec
    ngrams = list(tokens)
    ngrams.extend(f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False))
    for ng in ngrams:
        digest = hashlib.blake2b(ng.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[idx] += sign
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec


def ollama_embed(text: str) -> Optional[np.ndarray]:
    """One vector from Ollama ``/api/embed``. None if the daemon is down."""
    model = (OLLAMA_EMBED_MODEL or "").strip()
    if not model:
        return None
    payload = json.dumps({"model": model, "input": text or " "}).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_EMBED_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        log.warning("ollama embed failed: %s", exc)
        return None
    raw = None
    if isinstance(body, dict):
        if isinstance(body.get("embeddings"), list) and body["embeddings"]:
            raw = body["embeddings"][0]
        elif isinstance(body.get("embedding"), list):
            raw = body["embedding"]
    if not isinstance(raw, list) or not raw:
        return None
    vec = np.asarray(raw, dtype=np.float32)
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec /= norm
    return vec


def embed_text(text: str) -> Tuple[np.ndarray, str]:
    """``(vector, model_name)``. Falls back to hashed n-grams if Ollama fails."""
    if (OLLAMA_EMBED_MODEL or "").strip():
        vec = ollama_embed(text)
        if vec is not None:
            return vec, (OLLAMA_EMBED_MODEL or "").strip()
        log.info("ollama embed unavailable; using hashed n-grams")
    return hashed_embed(text), HASHED_MODEL


def pack_vec(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def unpack_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0 or b.size == 0 or a.size != b.size:
        return 0.0
    return float(np.dot(a, b))


def fit_logistic(
    X: np.ndarray,
    y: np.ndarray,
    *,
    l2: float = 1.0,
    steps: int = 250,
    lr: float = 0.4,
) -> Tuple[np.ndarray, float]:
    """Ridge logistic. Small-n, d=256 — gradient descent is enough."""
    n, d = X.shape
    w = np.zeros(d, dtype=np.float32)
    b = 0.0
    y = y.astype(np.float32)
    for _ in range(int(steps)):
        z = np.clip(X @ w + b, -20.0, 20.0)
        p = 1.0 / (1.0 + np.exp(-z))
        err = p - y
        w -= np.float32(lr) * (X.T @ err / n + l2 * w / max(n, 1))
        b -= float(lr) * float(err.mean())
    return w, float(b)


def predict_proba(X: np.ndarray, w: np.ndarray, b: float) -> np.ndarray:
    z = np.clip(X @ w + b, -20.0, 20.0)
    return 1.0 / (1.0 + np.exp(-z))


def rank_by_query(
    query: str,
    embeddings: Dict[str, np.ndarray],
    *,
    candidates: Optional[Sequence[str]] = None,
) -> List[Tuple[str, float]]:
    """``(rel_path, cosine)`` highest first. Paths with no vector are omitted."""
    q, _model = embed_text(query)
    paths = list(candidates) if candidates is not None else list(embeddings)
    scored: List[Tuple[str, float]] = []
    for rel in paths:
        vec = embeddings.get(rel)
        if vec is None or vec.size != q.size:
            continue
        scored.append((rel, cosine(q, vec)))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored


def embedding_near_dup_groups(
    embeddings: Dict[str, np.ndarray],
    *,
    min_cosine: float = EMBED_DUP_MIN_COSINE,
    exclude: Optional[Iterable[str]] = None,
) -> List[List[str]]:
    """Union-find over pairs whose cosine is at least ``min_cosine``."""
    skip = {str(p) for p in (exclude or ())}
    paths = [p for p in embeddings if p not in skip]
    if len(paths) < 2:
        return []
    mats: List[np.ndarray] = []
    usable: List[str] = []
    dim = None
    for p in paths:
        vec = embeddings[p]
        if dim is None:
            dim = int(vec.size)
        if int(vec.size) != dim:
            continue
        mats.append(vec)
        usable.append(p)
    if len(usable) < 2:
        return []
    matrix = np.stack(mats)
    sim = matrix @ matrix.T
    parent = list(range(len(usable)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    n = len(usable)
    for i in range(n - 1):
        row = sim[i, i + 1 :]
        for offset in np.nonzero(row >= min_cosine)[0]:
            union(i, i + 1 + int(offset))

    clusters: Dict[int, List[str]] = {}
    for i, path in enumerate(usable):
        clusters.setdefault(find(i), []).append(path)
    groups = [sorted(members) for members in clusters.values() if len(members) > 1]
    groups.sort(key=lambda g: (-len(g), g[0]))
    return groups


def train(index: Any, *, force: bool = False, on_progress: Optional[Any] = None) -> Dict[str, Any]:
    """Embed missing (or all) photos, fit the logistic, write ``p_keep``.

    ``index`` is an ``ArchiveIndex``. Returns a summary for the journal / API.
    """
    from promptstudio.prompts.cache import PromptCache

    cache = PromptCache()
    model = embed_model_name()
    faceted = index.backfill_facets()
    missing = (
        index.paths_missing_embedding(model=model)
        if not force
        else index.all_photo_paths()
    )
    embedded = 0
    failed = 0
    total = len(missing)
    for i, rel in enumerate(missing):
        if on_progress is not None:
            on_progress(i, total, rel)
        entry = cache.get(rel, rel.rsplit("/", 1)[-1])
        blob = text_blob_for_photo(entry, fallback=rel.replace("/", " ").replace("_", " "))
        try:
            vec, used_model = embed_text(blob)
            index.set_embedding(rel, pack_vec(vec), dim=int(vec.size), model=used_model)
            embedded += 1
        except Exception:
            log.exception("embed failed for %s", rel)
            failed += 1

    labelled = index.labelled_embedding_matrix(model=embed_model_name())
    scored = 0
    keep_rate = None
    weights_ok = False
    if labelled is not None:
        X, y, _paths = labelled
        n_keep = int((y == 1).sum())
        n_disc = int((y == 0).sum())
        if n_keep >= 2 and n_disc >= 2:
            w, b = fit_logistic(X, y)
            index.set_taste_weights(w, b, model=embed_model_name(), labelled=int(y.size))
            weights_ok = True
            all_emb = index.all_embeddings(model=embed_model_name())
            if all_emb:
                paths = list(all_emb)
                matrix = np.stack([all_emb[p] for p in paths])
                if matrix.shape[1] == w.size:
                    probs = predict_proba(matrix, w, b)
                    index.set_p_keeps(list(zip(paths, (float(p) for p in probs), strict=True)))
                    scored = len(paths)
                    keep_rate = round(float(n_keep / y.size), 4)
        else:
            log.info(
                "taste train: need ≥2 keep and ≥2 discard labels with embeddings "
                "(have keep=%s discard=%s)",
                n_keep,
                n_disc,
            )
    return {
        "faceted": faceted,
        "embedded": embedded,
        "embed_failed": failed,
        "scored": scored,
        "labelled": 0 if labelled is None else int(labelled[1].size),
        "weights": weights_ok,
        "model": embed_model_name(),
        "keep_rate": keep_rate,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }


class TasteJob(BackgroundJob):
    """Embed + fit in the background. Hashed n-grams take no lease."""

    owner = "taste"
    busy_noun = "the embedding model"
    busy_message = "Taste training already running"

    @property
    def resources(self) -> tuple:
        return (OLLAMA,) if (OLLAMA_EMBED_MODEL or "").strip() else ()

    def _idle_status(self) -> Dict[str, Any]:
        return {
            **super()._idle_status(),
            "embedded": 0,
            "scored": 0,
            "model": embed_model_name(),
        }

    def start(self, *, force: bool = False) -> bool:
        return self._start(lambda: self._run(force=force), force=bool(force))

    def _run(self, *, force: bool) -> None:
        from promptstudio.storage.db import ArchiveIndex

        index = ArchiveIndex.get()
        journal = RunJournal.for_kind("taste")

        def on_progress(i: int, total: int, rel: str) -> None:
            if self.cancel_requested():
                raise KeyboardInterrupt("cancelled")
            with self._job_lock:
                self._status["completed"] = i
                self._status["total"] = total
                self._status["current"] = rel

        with journal.run(force=force, model=embed_model_name()) as run:
            summary = train(index, force=force, on_progress=on_progress)
            run.summary(**summary)
            with self._job_lock:
                self._status.update(
                    {
                        "embedded": summary["embedded"],
                        "scored": summary["scored"],
                        "model": summary["model"],
                        "completed": summary["embedded"],
                        "total": summary["embedded"] + summary["embed_failed"],
                        "failed": summary["embed_failed"],
                    }
                )
