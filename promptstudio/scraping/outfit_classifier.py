"""Vision-based following classifier: woman, sexy outfit, good breasts (dry-run)."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from promptstudio.config import (
    IMAGE_EXTENSIONS,
    MODEL_NAME,
    OLLAMA_URL,
    SAVED_DIR,
)

# Vision classification does not need full-res Instagram JPGs; downscaling
# cuts prompt tokens and speeds GPU inference on 8GB cards.
CLASSIFY_MAX_EDGE = int(os.environ.get("CLASSIFY_MAX_EDGE", "768"))

CLASSIFY_PROMPT_VERSION = "v2-skin-exposure"

CLASSIFY_PROMPT = (
    "You classify Instagram photos for a personal KEEP filter. "
    "Be GENEROUS toward glamorous feminine photos — when unsure, prefer true. "
    "Return ONLY valid JSON:\n"
    '  "has_woman": boolean — a woman / female-presenting person is a main subject\n'
    '  "sexy_revealing_outfit": boolean — TRUE if clothing shows a good amount of skin '
    "or is glamorous/sexy: bikini, lingerie, bodysuit, crop top, low-cut top, "
    "cleavage, short dress/skirt, tight fit that highlights the body, swimwear, "
    "backless/revealing back, visible ass/butt/thighs/legs, sheer/mesh, lingerie-adjacent fashion, "
    "or stylish revealing outfits. "
    "TRUE even for cute/nice clothes if skin (shoulders, back, waist, midriff, thighs, buttocks, cleavage) shows. "
    "FALSE only for fully modest everyday coverage, baggy opaque streetwear with no skin, "
    "men-only, cartoons, food, memes, or no person.\n"
    '  "good_breasts": boolean — TRUE when feminine bust/chest OR attractive body shape/figure '
    "(cleavage, shaped by top/bikini, soft curves, side/back view of figure) is visible. "
    "Do NOT require huge/massive/heavy breasts. Average-to-full, flattering, or attractive body outline is enough. "
    "Side/3-quarter views, back views highlighting figure, lingerie, and bikini tops count as TRUE. "
    "FALSE only if completely non-sexy/modest, flat with no figure shape, body fully hidden with no outline, "
    "male chest, or no woman.\n"
    '  "confidence": number 0.0-1.0\n'
    '  "brief_reason": short phrase\n'
    "No markdown, no code fences, JSON object only."
)


def encode_image_for_classify(image_path: str, max_edge: int = CLASSIFY_MAX_EDGE) -> Optional[str]:
    """JPEG-encode a downscaled RGB copy of the image for Ollama vision."""
    try:
        from PIL import Image

        with Image.open(image_path) as im:
            im = im.convert("RGB")
            w, h = im.size
            scale = min(1.0, float(max_edge) / float(max(w, h)))
            if scale < 1.0:
                im = im.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.Resampling.LANCZOS,
                )
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=85, optimize=True)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"classify encode error for {image_path}: {e}")
        return None


@dataclass
class PostVerdict:
    path: str
    has_woman: bool = False
    sexy_revealing_outfit: bool = False
    good_breasts: bool = False
    confidence: float = 0.0
    brief_reason: str = ""
    ok: bool = False
    error: str = ""

    def matches_keep(self) -> bool:
        return (
            self.has_woman
            and (self.sexy_revealing_outfit or self.good_breasts)
            and self.confidence >= 0.5
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AccountVerdict:
    username: str
    index: int
    decision: str  # keep | unfollow | unsure | skipped
    reason: str = ""
    posts: List[PostVerdict] = field(default_factory=list)
    source: str = ""  # local | downloaded | none

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "index": self.index,
            "decision": self.decision,
            "reason": self.reason,
            "source": self.source,
            "posts": [p.to_dict() for p in self.posts],
        }


def ollama_reachable(timeout: float = 3.0) -> bool:
    try:
        tags = OLLAMA_URL.replace("/api/generate", "/api/tags")
        with urllib.request.urlopen(tags, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _ollama_vision_json(image_path: str) -> Optional[Dict[str, Any]]:
    b64 = encode_image_for_classify(image_path)
    if not b64:
        return None
    payload = {
        "model": MODEL_NAME,
        "prompt": CLASSIFY_PROMPT,
        "images": [b64],
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 180,
            "top_p": 0.85,
            # Vision prompts need headroom; 4096 fits downscaled classify images.
            "num_ctx": 4096,
        },
    }
    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as response:
            raw = (json.loads(response.read().decode("utf-8")).get("response") or "").strip()
    except Exception as e:
        return {"_error": str(e)}

    text = re.sub(r"^```(?:json)?\s*", "", raw)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {"_error": f"no JSON in model output: {raw[:120]}"}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        return {"_error": f"JSON parse: {e}"}
    return data if isinstance(data, dict) else {"_error": "not an object"}


def classify_image(image_path: str) -> PostVerdict:
    verdict = PostVerdict(path=image_path)
    data = _ollama_vision_json(image_path)
    if not data:
        verdict.error = "empty vision response"
        return verdict
    if data.get("_error"):
        verdict.error = str(data["_error"])
        return verdict
    verdict.has_woman = bool(data.get("has_woman"))
    verdict.sexy_revealing_outfit = bool(data.get("sexy_revealing_outfit"))
    verdict.good_breasts = bool(data.get("good_breasts"))
    try:
        verdict.confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        verdict.confidence = 0.0
    verdict.brief_reason = str(data.get("brief_reason") or "")[:160]
    verdict.ok = True
    return verdict


def list_local_images(username: str, limit: int = 3) -> List[str]:
    folder = os.path.join(SAVED_DIR, username)
    if not os.path.isdir(folder):
        return []
    files: List[str] = []
    for name in os.listdir(folder):
        lower = name.lower()
        if lower.endswith(IMAGE_EXTENSIONS):
            files.append(os.path.join(folder, name))
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files[:limit]


def list_staging_images(staging_root: str, username: str, limit: int = 3) -> List[str]:
    folder = os.path.join(staging_root, username)
    if not os.path.isdir(folder):
        return []
    files: List[str] = []
    for root, _dirs, names in os.walk(folder):
        for name in names:
            if name.lower().endswith(IMAGE_EXTENSIONS):
                files.append(os.path.join(root, name))
    # Prefer unique posts: first slide of each UTC group, then fill
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    picked: List[str] = []
    seen_posts: set[str] = set()
    for path in files:
        base = os.path.basename(path)
        # e.g. 2026-08-05_09-12-43_UTC_1.jpg -> post key without slide suffix
        post_key = base.rsplit("_UTC", 1)[0] if "_UTC" in base else base
        if post_key in seen_posts:
            continue
        seen_posts.add(post_key)
        picked.append(path)
        if len(picked) >= limit:
            return picked
    # fill remaining slots with other slides if needed
    for path in files:
        if path in picked:
            continue
        picked.append(path)
        if len(picked) >= limit:
            break
    return picked


def decide_account(posts: Sequence[PostVerdict]) -> tuple[str, str]:
    """Aggregate post verdicts -> keep | unfollow | unsure."""
    usable = [p for p in posts if p.ok]
    if not usable:
        return "unsure", "no usable vision results"

    matches = [p for p in usable if p.matches_keep()]
    clear_rejects = [
        p for p in usable if p.confidence >= 0.55 and not p.matches_keep()
    ]

    need = max(1, (len(usable) + 1) // 2)
    if len(matches) >= need:
        return (
            "keep",
            f"{len(matches)}/{len(usable)} posts match woman+sexy outfit+good breasts",
        )
    if len(matches) == 0 and len(clear_rejects) >= need:
        reasons = "; ".join(p.brief_reason or "no match" for p in clear_rejects[:3])
        return "unfollow", f"{len(clear_rejects)}/{len(usable)} clear non-matches ({reasons})"
    return (
        "unsure",
        f"mixed signals: {len(matches)} match, {len(clear_rejects)} reject of {len(usable)}",
    )


def classify_paths(paths: Sequence[str]) -> List[PostVerdict]:
    return [classify_image(p) for p in paths]
