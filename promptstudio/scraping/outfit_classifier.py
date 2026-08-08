"""Vision-based glam / fashion keep-filter classifier (Ollama).

JSON field names are stable for the archive DB API. Tune behaviour via env
(CLASSIFY_*, GLAM_SEXY_MIN) — do not commit personal classify dumps.
"""

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
    CLASSIFY_MAX_EDGE,
    CLASSIFY_REEL_CANDIDATES,
    CLASSIFY_REEL_UNCERTAIN_HI,
    CLASSIFY_REEL_UNCERTAIN_LO,
    CLASSIFY_REEL_VISION_MAX,
    IMAGE_EXTENSIONS,
    MODEL_NAME,
    OLLAMA_URL,
    SAVED_DIR,
    VIDEO_EXTENSIONS,
)
from promptstudio.scraping.video_frames import (
    find_video_cover_image,
    select_best_video_frames,
)

CLASSIFY_PROMPT_VERSION = "v2-skin-exposure"
CLASSIFY_REEL_PROMPT_VERSION = "v3-reel-frames"

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

CLASSIFY_REEL_PROMPT = (
    "You classify a FREEZE-FRAME from an Instagram Reel / short video for a personal KEEP filter. "
    "This is NOT a studio photo — expect motion blur, captions, stickers, or UI chrome. "
    "Be GENEROUS toward glamorous feminine frames — when unsure, prefer true. "
    "Return ONLY valid JSON:\n"
    '  "has_woman": boolean — a woman / female-presenting person is a main subject in this frame. '
    "FALSE for pure title cards, logo-only, text-only, memes with no person, or empty scenery.\n"
    '  "sexy_revealing_outfit": boolean — TRUE if clothing shows a good amount of skin '
    "or is glamorous/sexy: bikini, lingerie, bodysuit, crop top, low-cut top, "
    "cleavage, short dress/skirt, tight fit that highlights the body, swimwear, "
    "backless/revealing back, visible ass/butt/thighs/legs, sheer/mesh, lingerie-adjacent fashion, "
    "or stylish revealing outfits. "
    "TRUE even for cute/nice clothes if skin (shoulders, back, waist, midriff, thighs, buttocks, cleavage) shows. "
    "FALSE only for fully modest everyday coverage, baggy opaque streetwear with no skin, "
    "men-only, cartoons, food, memes, or no person. "
    "Ignore on-screen text stickers when judging clothing.\n"
    '  "good_breasts": boolean — TRUE when feminine bust/chest OR attractive body shape/figure '
    "(cleavage, shaped by top/bikini, soft curves, side/back view of figure) is visible. "
    "Do NOT require huge/massive/heavy breasts. Average-to-full, flattering, or attractive body outline is enough. "
    "Side/3-quarter views, back views highlighting figure, lingerie, and bikini tops count as TRUE. "
    "FALSE only if completely non-sexy/modest, flat with no figure shape, body fully hidden with no outline, "
    "male chest, or no woman. If heavy motion blur makes the body unrecognizable, lower confidence.\n"
    '  "confidence": number 0.0-1.0 — lower when blurry, partial crop, or text-dominated\n'
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
    glam_score: int = -1  # -1 unscored/error, 0–3 glam scale
    source: str = "image"  # image | video | video_cover
    prompt_version: str = CLASSIFY_PROMPT_VERSION
    evidence: Dict[str, Any] = field(default_factory=dict)

    def matches_keep(self) -> bool:
        return (
            self.has_woman
            and (self.sexy_revealing_outfit or self.good_breasts)
            and self.confidence >= 0.5
        )

    def compute_glam_score(self) -> int:
        """Map vision flags → 0–3 glam score (gallery sexy filter uses >= 2)."""
        if not self.ok:
            return -1
        if not self.has_woman:
            return 0
        if self.sexy_revealing_outfit and self.good_breasts:
            return 3
        if self.sexy_revealing_outfit or self.good_breasts:
            return 2
        return 1

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.ok and self.glam_score < 0:
            d["glam_score"] = self.compute_glam_score()
        d["matches_keep"] = bool(self.ok and self.matches_keep())
        return d


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


def _ollama_vision_json(
    image_path: str,
    prompt: str = CLASSIFY_PROMPT,
) -> Optional[Dict[str, Any]]:
    b64 = encode_image_for_classify(image_path)
    if not b64:
        return None
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
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


def _verdict_from_vision_data(
    image_path: str,
    data: Optional[Dict[str, Any]],
    *,
    source: str,
    prompt_version: str,
) -> PostVerdict:
    verdict = PostVerdict(
        path=image_path, source=source, prompt_version=prompt_version
    )
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
    verdict.glam_score = verdict.compute_glam_score()
    return verdict


def classify_image(
    image_path: str,
    *,
    prompt: Optional[str] = None,
    source: str = "image",
    prompt_version: Optional[str] = None,
) -> PostVerdict:
    use_prompt = prompt if prompt is not None else CLASSIFY_PROMPT
    ver = prompt_version or (
        CLASSIFY_REEL_PROMPT_VERSION
        if use_prompt == CLASSIFY_REEL_PROMPT
        else CLASSIFY_PROMPT_VERSION
    )
    data = _ollama_vision_json(image_path, prompt=use_prompt)
    return _verdict_from_vision_data(
        image_path, data, source=source, prompt_version=ver
    )


def _prefer_verdict(a: PostVerdict, b: PostVerdict) -> PostVerdict:
    """Pick better of two video frame verdicts (max glam, then confidence)."""
    if not a.ok:
        return b
    if not b.ok:
        return a
    if b.glam_score > a.glam_score:
        return b
    if b.glam_score == a.glam_score and b.confidence > a.confidence:
        return b
    return a


def _needs_second_reel_frame(v: PostVerdict) -> bool:
    """True when first vision pass is mid-confidence / weak woman signal."""
    if not v.ok:
        return True
    lo = float(CLASSIFY_REEL_UNCERTAIN_LO)
    hi = float(CLASSIFY_REEL_UNCERTAIN_HI)
    if lo <= v.confidence <= hi:
        return True
    if not v.has_woman and v.confidence < 0.75:
        return True
    return False


def _is_strong_reel_verdict(v: PostVerdict) -> bool:
    return bool(
        v.ok
        and v.has_woman
        and v.confidence >= float(CLASSIFY_REEL_UNCERTAIN_HI)
        and v.glam_score >= 2
    )


def classify_video(
    video_path: str,
    max_frames: Optional[int] = None,
) -> PostVerdict:
    """
    Classify a reel/video via quality-ranked freeze-frames + reel prompt.

    max_frames: max *decoded-frame* vision calls (default CLASSIFY_REEL_VISION_MAX).
    A strong companion cover can short-circuit without decoding.
    """
    vision_budget = max(
        1, int(max_frames if max_frames is not None else CLASSIFY_REEL_VISION_MAX)
    )

    evidence: Dict[str, Any] = {
        "prompt_version": CLASSIFY_REEL_PROMPT_VERSION,
        "candidates": int(CLASSIFY_REEL_CANDIDATES),
        "vision_budget": vision_budget,
        "frames_considered": 0,
        "frames_sent_to_vision": 0,
        "frame_times_sec": [],
        "frame_metrics": [],
        "used_cover": False,
    }
    best: Optional[PostVerdict] = None
    pick_paths: List[str] = []

    def _vision(
        image_path: str,
        *,
        source: str,
        t_sec: Optional[float] = None,
        metrics: Optional[dict] = None,
    ) -> PostVerdict:
        nonlocal best
        v = classify_image(
            image_path,
            prompt=CLASSIFY_REEL_PROMPT,
            source=source,
            prompt_version=CLASSIFY_REEL_PROMPT_VERSION,
        )
        v.path = video_path
        v.source = source
        evidence["frames_sent_to_vision"] = int(evidence["frames_sent_to_vision"]) + 1
        if t_sec is not None:
            evidence.setdefault("frame_times_sec", []).append(round(float(t_sec), 3))
        if metrics:
            evidence.setdefault("vision_frame_metrics", []).append(metrics)
        best = v if best is None else _prefer_verdict(best, v)
        return v

    def _finalize(v: PostVerdict) -> PostVerdict:
        v.path = video_path
        v.prompt_version = CLASSIFY_REEL_PROMPT_VERSION
        v.evidence = dict(evidence)
        return v

    try:
        # 1) Companion cover (optional short-circuit)
        cover = find_video_cover_image(video_path)
        if cover:
            evidence["used_cover"] = True
            evidence["cover_path"] = os.path.basename(cover)
            cv = _vision(cover, source="video_cover")
            if _is_strong_reel_verdict(cv):
                return _finalize(cv)

        # 2) Ranked freeze-frames (time-based sample + quality gate)
        picks = select_best_video_frames(
            video_path,
            top_n=min(3, max(2, vision_budget + 1)),
            candidates=CLASSIFY_REEL_CANDIDATES,
            write_jpeg=True,
        )
        pick_paths = [p.path for p in picks if p.path]
        evidence["frames_considered"] = len(picks)
        evidence["frame_metrics"] = [p.to_dict() for p in picks]

        if not picks and best is None:
            return PostVerdict(
                path=video_path,
                source="video",
                error="no_usable_reel_frames",
                prompt_version=CLASSIFY_REEL_PROMPT_VERSION,
                evidence=evidence,
            )

        # Decoded-frame vision budget (independent of a weak cover call)
        decoded_calls = 0
        for pick in picks:
            if not pick.path:
                continue
            if decoded_calls >= vision_budget:
                break
            # After a solid decoded (or cover+decoded) result, stop early
            if (
                decoded_calls >= 1
                and best is not None
                and best.ok
                and not _needs_second_reel_frame(best)
            ):
                break
            # Second decoded frame only when uncertain and budget allows
            if decoded_calls >= 1:
                if vision_budget < 2:
                    break
                if best is not None and not _needs_second_reel_frame(best):
                    break

            _vision(
                pick.path,
                source="video",
                t_sec=pick.t_sec,
                metrics=pick.to_dict(),
            )
            decoded_calls += 1

            if best is not None and best.ok and not _needs_second_reel_frame(best):
                break

        if best is None:
            return PostVerdict(
                path=video_path,
                source="video",
                error="no frame scores",
                prompt_version=CLASSIFY_REEL_PROMPT_VERSION,
                evidence=evidence,
            )
        return _finalize(best)
    finally:
        for fp in pick_paths:
            try:
                if fp and os.path.isfile(fp):
                    os.remove(fp)
            except OSError:
                pass


def classify_media(path: str) -> PostVerdict:
    """Classify image or video path for glam scoring."""
    lower = path.lower()
    if lower.endswith(VIDEO_EXTENSIONS):
        return classify_video(path)
    return classify_image(path)


def persist_glam_score(rel_path: str, verdict: PostVerdict, full_path: str = "") -> None:
    """Write glam_score to SQLite index + sidecar metadata."""
    score = verdict.glam_score if verdict.ok else -1
    if verdict.ok and score < 0:
        score = verdict.compute_glam_score()
    try:
        from promptstudio.storage.db import ArchiveIndex

        ArchiveIndex.get().set_glam_score(
            rel_path,
            score,
            has_woman=1 if verdict.has_woman else 0,
            sexy=1 if verdict.sexy_revealing_outfit else 0,
        )
    except Exception as e:
        print(f"glam index write warning for {rel_path}: {e}")
    if full_path and os.path.isfile(full_path):
        try:
            from promptstudio.storage.metadata import load_post_metadata, save_post_metadata

            meta = load_post_metadata(full_path) or {}
            meta["glam_score"] = score
            glam_block: Dict[str, Any] = {
                "score": score,
                "has_woman": bool(verdict.has_woman),
                "sexy_revealing_outfit": bool(verdict.sexy_revealing_outfit),
                "good_breasts": bool(verdict.good_breasts),
                "confidence": verdict.confidence,
                "brief_reason": verdict.brief_reason,
                "matches_keep": bool(verdict.ok and verdict.matches_keep()),
                "source": verdict.source,
                "prompt_version": verdict.prompt_version,
            }
            if verdict.evidence:
                glam_block.update(
                    {
                        k: v
                        for k, v in verdict.evidence.items()
                        if k
                        in (
                            "frames_considered",
                            "frames_sent_to_vision",
                            "frame_times_sec",
                            "frame_metrics",
                            "used_cover",
                            "candidates",
                            "vision_budget",
                        )
                    }
                )
            meta["glam"] = glam_block
            save_post_metadata(full_path, meta)
        except Exception as e:
            print(f"glam sidecar write warning for {rel_path}: {e}")


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
