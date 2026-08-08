"""Vision-based glam / fashion keep-filter classifier (Ollama).

JSON field names are stable for the archive DB API. Tune behaviour via env
(CLASSIFY_*, GLAM_SEXY_MIN) — do not commit personal classify dumps.

Reels are scored from a *contact sheet*: one vision call over a chronological
grid of freeze-frames spanning the whole clip, so a reveal in the final seconds
is seen. Single-frame sampling structurally could not see it.

Two scoring vocabularies coexist:

* **legacy** (``v2-skin-exposure``) — three booleans, still the default for
  photos so existing scores stay comparable.
* **ordinal** (``v4-*``) — a 0–4 ``exposure_tier`` with explicit anchors,
  mapped back onto the same 0–3 ``glam_score`` column. No migration, and the
  gallery Sexy filter (``glam_score >= GLAM_SEXY_MIN``) is untouched.

Deliberately *not* generous: the old prompt's "when unsure prefer true" pushed
85% of scored videos to glam 3, which made the filter a no-op. Generosity is a
policy choice and belongs at the threshold, not in the measurement.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from promptstudio.config import (
    CLASSIFY_KEEP_ALIVE,
    CLASSIFY_MAX_EDGE,
    CLASSIFY_NUM_CTX,
    CLASSIFY_NUM_PREDICT,
    CLASSIFY_PHOTO_ORDINAL,
    CLASSIFY_REEL_CANDIDATES,
    CLASSIFY_REEL_SHEET,
    CLASSIFY_REEL_SHEET_PANELS,
    CLASSIFY_REEL_UNCERTAIN_HI,
    CLASSIFY_REEL_UNCERTAIN_LO,
    CLASSIFY_REEL_VISION_MAX,
    CLASSIFY_RETRIES,
    CLASSIFY_SHEET_MAX_EDGE,
    CLASSIFY_STRUCTURED,
    CLASSIFY_TIMEOUT,
    IMAGE_EXTENSIONS,
    MODEL_NAME,
    OLLAMA_URL,
    SAVED_DIR,
    VIDEO_EXTENSIONS,
)
from promptstudio.scraping.video_frames import (
    compose_contact_sheet,
    extract_frame_at,
    find_video_cover_image,
    select_best_video_frames,
)

CLASSIFY_PROMPT_VERSION = "v2-skin-exposure"
CLASSIFY_REEL_PROMPT_VERSION = "v3-reel-frames"
CLASSIFY_FRAME_V4_VERSION = "v4-ordinal-frame"
CLASSIFY_SHEET_VERSION = "v4-reel-sheet"

# exposure_tier (0-4) -> glam_score (0-3). Tier 3 is the Sexy-filter boundary.
TIER_TO_GLAM = {0: 0, 1: 0, 2: 1, 3: 2, 4: 3}

# Failures that mean "no frames", as opposed to "the model call failed".
_NO_FRAMES_ERRORS = frozenset({"no_usable_reel_frames", "no frame scores"})

_TIER_ANCHORS = (
    "     0 = no woman present\n"
    "     1 = fully modest: everyday coverage, no skin beyond face and hands\n"
    "     2 = normal fashion: some skin (arms, neck, shoulders), fitted but not revealing\n"
    "     3 = revealing: midriff, cleavage, bare back or thighs visible; short dress or "
    "skirt; tight fit that emphasises the figure\n"
    "     4 = maximally revealing: bikini, swimwear, lingerie, bodysuit, sheer or mesh\n"
)

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

# Legacy per-frame reel prompt — kept for CLASSIFY_REEL_SHEET=0 and A/B runs.
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

# Single-frame ordinal prompt: cascade confirmation, and photos when opted in.
CLASSIFY_FRAME_V4_PROMPT = (
    "Rate the outfit in this image for a personal fashion KEEP filter. "
    "Return ONLY valid JSON:\n"
    '  "has_woman": boolean — a woman / female-presenting person is a main subject. '
    "false for title cards, logos, text-only frames, scenery, food, cartoons, or men only.\n"
    '  "exposure_tier": integer 0-4 for how much the outfit reveals:\n'
    + _TIER_ANCHORS
    + '  "figure_visible": boolean — the bust or body shape is discernible\n'
    '  "confidence": number 0.0-1.0\n'
    '  "brief_reason": short phrase naming the garment\n'
    "Judge only clothing and body. Ignore captions, stickers, watermarks and UI chrome. "
    "If ambiguous choose the LOWER tier and report confidence below 0.5. Do not inflate tiers."
)


def reel_sheet_prompt(n_panels: int) -> str:
    """Whole-reel prompt for a chronological contact sheet of n_panels frames."""
    return (
        f"This image is a CONTACT SHEET: {n_panels} freeze-frames sampled in chronological "
        "order from ONE Instagram Reel, arranged left-to-right, top-to-bottom. "
        "Each panel is captioned with its panel number and timestamp.\n"
        "Instagram creators routinely start a reel in modest or everyday clothing and reveal "
        "the real outfit in the FINAL panels. Judge every panel on its own, and pay particular "
        "attention to the last ones.\n"
        "Return ONLY valid JSON.\n"
        'For each panel, an entry in "panels" with:\n'
        '  "i": the panel number as captioned (1-based)\n'
        '  "has_woman": boolean — a woman / female-presenting person is a main subject in '
        "THAT panel. false for title cards, logos, text-only frames, scenery, food, cartoons, "
        "or men only.\n"
        '  "exposure_tier": integer 0-4 for how much the outfit in THAT panel reveals:\n'
        + _TIER_ANCHORS
        + "Then, for the reel as a whole:\n"
        '  "peak_panel": panel number with the highest exposure_tier\n'
        '  "reel_exposure": the highest exposure_tier among panels containing a woman\n'
        '  "outfit_changes": boolean — the outfit differs between early and late panels\n'
        '  "figure_visible": boolean — bust or body shape is discernible in the peak panel\n'
        '  "confidence": number 0.0-1.0\n'
        '  "brief_reason": short phrase naming the peak panel and its outfit\n'
        "Judge only clothing and body. Ignore captions, stickers, progress bars and watermarks. "
        "If a panel is ambiguous choose the LOWER tier and report confidence below 0.5. "
        "Do not inflate tiers."
    )


# ── JSON schemas for Ollama structured output ────────────────────────
# Constrained decoding makes malformed JSON mechanically impossible; the regex
# scrape below stays as a fallback for models/versions that reject `format`.

LEGACY_FRAME_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["has_woman", "sexy_revealing_outfit", "good_breasts", "confidence"],
    "properties": {
        "has_woman": {"type": "boolean"},
        "sexy_revealing_outfit": {"type": "boolean"},
        "good_breasts": {"type": "boolean"},
        "confidence": {"type": "number"},
        "brief_reason": {"type": "string"},
    },
}

FRAME_V4_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["has_woman", "exposure_tier", "confidence"],
    "properties": {
        "has_woman": {"type": "boolean"},
        "exposure_tier": {"type": "integer", "minimum": 0, "maximum": 4},
        "figure_visible": {"type": "boolean"},
        "confidence": {"type": "number"},
        "brief_reason": {"type": "string"},
    },
}

SHEET_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["panels", "reel_exposure", "confidence"],
    "properties": {
        "panels": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["i", "has_woman", "exposure_tier"],
                "properties": {
                    "i": {"type": "integer"},
                    "has_woman": {"type": "boolean"},
                    "exposure_tier": {"type": "integer", "minimum": 0, "maximum": 4},
                },
            },
        },
        "peak_panel": {"type": "integer"},
        "reel_exposure": {"type": "integer", "minimum": 0, "maximum": 4},
        "outfit_changes": {"type": "boolean"},
        "figure_visible": {"type": "boolean"},
        "confidence": {"type": "number"},
        "brief_reason": {"type": "string"},
    },
}


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
        log.warning("classify encode failed for %s: %s", image_path, e)
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
    exposure_tier: int = -1  # -1 when scored with the legacy boolean prompt
    source: str = "image"  # image | video | video_sheet | video_cover
    prompt_version: str = CLASSIFY_PROMPT_VERSION
    evidence: Dict[str, Any] = field(default_factory=dict)

    def matches_keep(self) -> bool:
        if self.exposure_tier >= 0:
            return self.has_woman and self.exposure_tier >= 3 and self.confidence >= 0.5
        return (
            self.has_woman
            and (self.sexy_revealing_outfit or self.good_breasts)
            and self.confidence >= 0.5
        )

    def compute_glam_score(self) -> int:
        """Map vision output → 0–3 glam score (gallery sexy filter uses >= 2)."""
        if not self.ok:
            return -1
        if self.exposure_tier >= 0:
            if not self.has_woman:
                return 0
            return TIER_TO_GLAM.get(int(self.exposure_tier), 0)
        # Legacy boolean vocabulary (photos, and scripts/backfill_glam_scores.py)
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


def _parse_json_object(raw: str) -> Optional[Dict[str, Any]]:
    text = re.sub(r"^```(?:json)?\s*", "", raw)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _ollama_vision_json(
    image_path: str | Sequence[str],
    prompt: str = CLASSIFY_PROMPT,
    *,
    schema: Optional[Dict[str, Any]] = None,
    max_edge: int = CLASSIFY_MAX_EDGE,
    num_predict: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    Run one vision request and return the parsed JSON object.

    Retries transient failures and unparseable output up to CLASSIFY_RETRIES —
    without this, a single blip left glam_score at -1 forever, which is the bulk
    of the historical unscored backlog.
    """
    paths = [image_path] if isinstance(image_path, str) else list(image_path)
    images: List[str] = []
    for p in paths:
        b64 = encode_image_for_classify(p, max_edge=max_edge)
        if not b64:
            return {"_error": f"encode failed: {os.path.basename(str(p))}"}
        images.append(b64)
    if not images:
        return {"_error": "no images to classify"}

    payload: Dict[str, Any] = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "images": images,
        "stream": False,
        "keep_alive": CLASSIFY_KEEP_ALIVE,
        "options": {
            "temperature": 0.1,
            "num_predict": int(num_predict or CLASSIFY_NUM_PREDICT),
            "top_p": 0.85,
            "num_ctx": int(CLASSIFY_NUM_CTX),
        },
    }
    if schema and CLASSIFY_STRUCTURED:
        payload["format"] = schema

    attempts = max(1, int(CLASSIFY_RETRIES) + 1)
    last_error = "unknown"
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                OLLAMA_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=CLASSIFY_TIMEOUT) as response:
                raw = (json.loads(response.read().decode("utf-8")).get("response") or "").strip()
        except Exception as e:
            last_error = str(e)
            # A rejected `format` will not start working on retry — drop the
            # constraint and let the regex fallback handle the free-text reply.
            if "format" in payload and "format" in last_error.lower():
                payload.pop("format", None)
        else:
            data = _parse_json_object(raw)
            if data is not None:
                return data
            last_error = f"no JSON in model output: {raw[:120]}"

        if attempt < attempts - 1:
            time.sleep(0.5 * (2**attempt))

    return {"_error": last_error}


def _coerce_confidence(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _verdict_from_vision_data(
    image_path: str,
    data: Optional[Dict[str, Any]],
    *,
    source: str,
    prompt_version: str,
) -> PostVerdict:
    """Build a verdict from legacy three-boolean output."""
    verdict = PostVerdict(path=image_path, source=source, prompt_version=prompt_version)
    if not data:
        verdict.error = "empty vision response"
        return verdict
    if data.get("_error"):
        verdict.error = str(data["_error"])
        return verdict
    verdict.has_woman = bool(data.get("has_woman"))
    verdict.sexy_revealing_outfit = bool(data.get("sexy_revealing_outfit"))
    verdict.good_breasts = bool(data.get("good_breasts"))
    verdict.confidence = _coerce_confidence(data.get("confidence"))
    verdict.brief_reason = str(data.get("brief_reason") or "")[:160]
    verdict.ok = True
    verdict.glam_score = verdict.compute_glam_score()
    return verdict


def _verdict_from_tier_data(
    path: str,
    data: Optional[Dict[str, Any]],
    *,
    source: str,
    prompt_version: str,
) -> PostVerdict:
    """Build a verdict from ordinal (exposure_tier) output."""
    verdict = PostVerdict(path=path, source=source, prompt_version=prompt_version)
    if not data:
        verdict.error = "empty vision response"
        return verdict
    if data.get("_error"):
        verdict.error = str(data["_error"])
        return verdict

    verdict.has_woman = bool(data.get("has_woman"))
    try:
        tier = int(data.get("exposure_tier", 0))
    except (TypeError, ValueError):
        tier = 0
    verdict.exposure_tier = max(0, min(4, tier))
    if not verdict.has_woman:
        verdict.exposure_tier = 0
    verdict.confidence = _coerce_confidence(data.get("confidence"))
    verdict.brief_reason = str(data.get("brief_reason") or "")[:160]
    # Keep the legacy booleans populated — the gallery UI and CLI scripts read them.
    verdict.sexy_revealing_outfit = verdict.exposure_tier >= 3
    verdict.good_breasts = bool(data.get("figure_visible"))
    verdict.ok = True
    verdict.glam_score = verdict.compute_glam_score()
    return verdict


def classify_image(
    image_path: str,
    *,
    prompt: Optional[str] = None,
    source: str = "image",
    prompt_version: Optional[str] = None,
    ordinal: Optional[bool] = None,
    max_edge: int = CLASSIFY_MAX_EDGE,
) -> PostVerdict:
    """
    Classify a still image.

    Uses the legacy boolean prompt by default so existing photo scores stay
    comparable; set CLASSIFY_PHOTO_ORDINAL=1 (or pass ordinal=True) for the v4
    tier vocabulary.
    """
    use_ordinal = CLASSIFY_PHOTO_ORDINAL if ordinal is None else bool(ordinal)
    if use_ordinal and prompt is None:
        data = _ollama_vision_json(
            image_path,
            prompt=CLASSIFY_FRAME_V4_PROMPT,
            schema=FRAME_V4_SCHEMA,
            max_edge=max_edge,
        )
        return _verdict_from_tier_data(
            image_path,
            data,
            source=source,
            prompt_version=prompt_version or CLASSIFY_FRAME_V4_VERSION,
        )

    use_prompt = prompt if prompt is not None else CLASSIFY_PROMPT
    ver = prompt_version or (
        CLASSIFY_REEL_PROMPT_VERSION
        if use_prompt == CLASSIFY_REEL_PROMPT
        else CLASSIFY_PROMPT_VERSION
    )
    data = _ollama_vision_json(
        image_path,
        prompt=use_prompt,
        schema=LEGACY_FRAME_SCHEMA,
        max_edge=max_edge,
    )
    return _verdict_from_vision_data(image_path, data, source=source, prompt_version=ver)


def _classify_frame_ordinal(
    frame_path: str,
    *,
    source: str,
    max_edge: int = CLASSIFY_MAX_EDGE,
) -> PostVerdict:
    data = _ollama_vision_json(
        frame_path,
        prompt=CLASSIFY_FRAME_V4_PROMPT,
        schema=FRAME_V4_SCHEMA,
        max_edge=max_edge,
    )
    return _verdict_from_tier_data(
        frame_path, data, source=source, prompt_version=CLASSIFY_FRAME_V4_VERSION
    )


# ── Contact-sheet reel scoring ───────────────────────────────────────


def _aggregate_sheet_panels(
    data: Dict[str, Any],
    n_panels: int,
) -> tuple[int, int, List[Dict[str, Any]]]:
    """
    Reduce per-panel readings to (reel_tier, peak_panel_1based, clean_panels).

    Max-over-panels is computed here rather than trusting the model's own
    ``reel_exposure`` rollup: the panel array is the auditable part, and max is
    exactly the "revealing at any point counts" semantic the filter wants.
    Tiers on panels the model marked as having no woman are ignored.
    """
    panels: List[Dict[str, Any]] = []
    raw_panels = data.get("panels")
    if isinstance(raw_panels, list):
        for entry in raw_panels:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("i", 0))
                tier = int(entry.get("exposure_tier", 0))
            except (TypeError, ValueError):
                continue
            if not 1 <= idx <= n_panels:
                continue
            has_woman = bool(entry.get("has_woman"))
            panels.append(
                {
                    "i": idx,
                    "has_woman": has_woman,
                    "exposure_tier": max(0, min(4, tier)) if has_woman else 0,
                }
            )

    scored = [p for p in panels if p["has_woman"]]
    if not scored:
        return 0, 0, panels

    # Ties break to the later panel: on a reveal reel that is the held payoff
    # pose, which is also the better thumbnail and the better confirm frame.
    peak = max(scored, key=lambda p: (p["exposure_tier"], p["i"]))
    return int(peak["exposure_tier"]), int(peak["i"]), panels


def _needs_confirm(tier: int, confidence: float) -> bool:
    """
    Escalate to a full-resolution look at the peak frame.

    Contact-sheet panels are ~256px wide, which is enough for "how much skin"
    but not for sheer-vs-opaque. Confirm exactly where it matters: on the
    tier 2/3 boundary that decides the Sexy filter, or when the model is unsure.
    """
    return tier in (2, 3) or confidence < 0.5


def _classify_reel_sheet(video_path: str, vision_budget: int) -> PostVerdict:
    n_panels = max(1, int(CLASSIFY_REEL_SHEET_PANELS))
    evidence: Dict[str, Any] = {
        "prompt_version": CLASSIFY_SHEET_VERSION,
        "candidates": int(CLASSIFY_REEL_CANDIDATES),
        "vision_budget": vision_budget,
        "frames_considered": 0,
        "frames_sent_to_vision": 0,
        "frame_times_sec": [],
        "used_cover": False,
        "mode": "sheet",
    }

    sheet = compose_contact_sheet(video_path, panels=n_panels)
    if sheet is None:
        return PostVerdict(
            path=video_path,
            source="video",
            error="no_usable_reel_frames",
            prompt_version=CLASSIFY_SHEET_VERSION,
            evidence=evidence,
        )

    evidence["frames_considered"] = len(sheet.picks)
    evidence["frame_times_sec"] = [round(p.t_sec, 2) for p in sheet.picks]
    evidence["frame_metrics"] = [p.to_dict() for p in sheet.picks]
    evidence["sheet"] = sheet.to_dict()

    confirm_path = ""
    try:
        data = _ollama_vision_json(
            sheet.path,
            prompt=reel_sheet_prompt(len(sheet.picks)),
            schema=SHEET_SCHEMA,
            max_edge=CLASSIFY_SHEET_MAX_EDGE,
        )
        evidence["frames_sent_to_vision"] = 1

        if not data or data.get("_error"):
            return PostVerdict(
                path=video_path,
                source="video_sheet",
                error=str((data or {}).get("_error") or "empty vision response"),
                prompt_version=CLASSIFY_SHEET_VERSION,
                evidence=evidence,
            )

        tier, peak_panel, panels = _aggregate_sheet_panels(data, len(sheet.picks))
        confidence = _coerce_confidence(data.get("confidence"))

        evidence["panels"] = panels
        evidence["peak_panel"] = peak_panel
        evidence["outfit_changes"] = bool(data.get("outfit_changes"))
        model_rollup = data.get("reel_exposure")
        if model_rollup is not None:
            evidence["model_reel_exposure"] = model_rollup
            try:
                if int(model_rollup) != tier:
                    # Not an error — logged so prompt drift is visible in the sidecar.
                    evidence["rollup_disagreement"] = True
            except (TypeError, ValueError):
                pass

        if not panels:
            # Schema satisfied but no usable panel rows — fall back to the
            # model's own rollup rather than silently scoring 0.
            try:
                tier = max(0, min(4, int(model_rollup)))
            except (TypeError, ValueError):
                tier = 0
            evidence["panels_missing"] = True

        peak_t = 0.0
        if 1 <= peak_panel <= len(sheet.picks):
            peak_t = float(sheet.picks[peak_panel - 1].t_sec)
            evidence["peak_time_sec"] = round(peak_t, 2)
            evidence["peak_in_last_shot"] = (
                sheet.picks[peak_panel - 1].shot == sheet.picks[-1].shot
            )

        verdict = PostVerdict(
            path=video_path,
            has_woman=any(p["has_woman"] for p in panels) if panels else tier > 0,
            exposure_tier=tier,
            confidence=confidence,
            brief_reason=str(data.get("brief_reason") or "")[:160],
            ok=True,
            source="video_sheet",
            prompt_version=CLASSIFY_SHEET_VERSION,
        )
        verdict.sexy_revealing_outfit = tier >= 3
        verdict.good_breasts = bool(data.get("figure_visible"))
        verdict.glam_score = verdict.compute_glam_score()

        # Cascade: re-read the peak frame at full resolution when it matters.
        if vision_budget >= 2 and peak_t > 0 and _needs_confirm(tier, confidence):
            confirm_path = extract_frame_at(video_path, peak_t)
            if confirm_path:
                confirm = _classify_frame_ordinal(confirm_path, source="video")
                evidence["frames_sent_to_vision"] = 2
                evidence["confirm"] = {
                    "t": round(peak_t, 2),
                    "ok": confirm.ok,
                    "tier": confirm.exposure_tier,
                    "has_woman": confirm.has_woman,
                    "confidence": round(confirm.confidence, 3),
                }
                # The confirm sees the same moment at full resolution, so it
                # supersedes the sheet — except when it loses the subject the
                # sheet clearly saw, where the sheet reading is kept.
                if confirm.ok and confirm.has_woman:
                    verdict.exposure_tier = confirm.exposure_tier
                    verdict.confidence = confirm.confidence
                    verdict.good_breasts = confirm.good_breasts
                    verdict.brief_reason = confirm.brief_reason or verdict.brief_reason
                    verdict.sexy_revealing_outfit = confirm.exposure_tier >= 3
                    verdict.glam_score = verdict.compute_glam_score()
                elif confirm.ok and not confirm.has_woman and verdict.has_woman:
                    evidence["confirm_lost_subject"] = True

        verdict.evidence = evidence
        return verdict
    finally:
        for temp in (sheet.path, confirm_path):
            if temp and os.path.isfile(temp):
                try:
                    os.remove(temp)
                except OSError:
                    pass


# ── Legacy per-frame reel scoring (CLASSIFY_REEL_SHEET=0) ────────────


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
    """True when the first vision pass is weak enough to be worth a second look."""
    if not v.ok:
        return True
    lo = float(CLASSIFY_REEL_UNCERTAIN_LO)
    hi = float(CLASSIFY_REEL_UNCERTAIN_HI)
    if lo <= v.confidence <= hi:
        return True
    if not v.has_woman and v.confidence < 0.75:
        return True
    # A confident *low* score is the reveal case: the model is sure about the
    # "before" outfit. Keep looking rather than stopping early.
    if v.has_woman and v.glam_score < 2:
        return True
    return False


def _classify_reel_frames(video_path: str, vision_budget: int) -> PostVerdict:
    evidence: Dict[str, Any] = {
        "prompt_version": CLASSIFY_REEL_PROMPT_VERSION,
        "candidates": int(CLASSIFY_REEL_CANDIDATES),
        "vision_budget": vision_budget,
        "frames_considered": 0,
        "frames_sent_to_vision": 0,
        "frame_times_sec": [],
        "frame_metrics": [],
        "used_cover": False,
        "mode": "frames",
    }
    best: Optional[PostVerdict] = None
    pick_paths: List[str] = []

    try:
        picks = select_best_video_frames(
            video_path,
            top_n=max(2, vision_budget),
            candidates=CLASSIFY_REEL_CANDIDATES,
            write_jpeg=True,
        )
        pick_paths = [p.path for p in picks if p.path]
        evidence["frames_considered"] = int(CLASSIFY_REEL_CANDIDATES)
        evidence["frame_metrics"] = [p.to_dict() for p in picks]

        if not picks:
            return PostVerdict(
                path=video_path,
                source="video",
                error="no_usable_reel_frames",
                prompt_version=CLASSIFY_REEL_PROMPT_VERSION,
                evidence=evidence,
            )

        for pick in picks:
            if not pick.path:
                continue
            if int(evidence["frames_sent_to_vision"]) >= vision_budget:
                break
            if best is not None and not _needs_second_reel_frame(best):
                break

            v = classify_image(
                pick.path,
                prompt=CLASSIFY_REEL_PROMPT,
                source="video",
                prompt_version=CLASSIFY_REEL_PROMPT_VERSION,
            )
            v.path = video_path
            v.source = "video"
            evidence["frames_sent_to_vision"] = int(evidence["frames_sent_to_vision"]) + 1
            evidence["frame_times_sec"].append(round(float(pick.t_sec), 3))
            best = v if best is None else _prefer_verdict(best, v)

        if best is None:
            return PostVerdict(
                path=video_path,
                source="video",
                error="no frame scores",
                prompt_version=CLASSIFY_REEL_PROMPT_VERSION,
                evidence=evidence,
            )
        best.path = video_path
        best.prompt_version = CLASSIFY_REEL_PROMPT_VERSION
        best.evidence = evidence
        return best
    finally:
        for fp in pick_paths:
            try:
                if fp and os.path.isfile(fp):
                    os.remove(fp)
            except OSError:
                pass


def classify_video(
    video_path: str,
    max_frames: Optional[int] = None,
) -> PostVerdict:
    """
    Classify a reel/video.

    Default path renders a chronological contact sheet of the whole clip and
    scores it in one vision call, optionally confirming the peak frame at full
    resolution. Set CLASSIFY_REEL_SHEET=0 for the legacy per-frame path.

    max_frames caps vision calls (default CLASSIFY_REEL_VISION_MAX).
    """
    vision_budget = max(
        1, int(max_frames if max_frames is not None else CLASSIFY_REEL_VISION_MAX)
    )

    if CLASSIFY_REEL_SHEET:
        verdict = _classify_reel_sheet(video_path, vision_budget)
    else:
        verdict = _classify_reel_frames(video_path, vision_budget)

    if verdict.ok:
        return verdict

    # Undecodable video — a companion cover still is better than nothing.
    # It is never a short-circuit: covers show the "before" outfit. Only frame
    # extraction failures qualify; if the vision call itself failed, a second
    # one will fail too, and across a batch that doubles the cost of an outage.
    if verdict.error not in _NO_FRAMES_ERRORS:
        return verdict

    cover = find_video_cover_image(video_path)
    if cover:
        cv = _classify_frame_ordinal(cover, source="video_cover")
        if cv.ok:
            cv.path = video_path
            evidence = dict(verdict.evidence)
            evidence["used_cover"] = True
            evidence["cover_path"] = os.path.basename(cover)
            evidence["frames_sent_to_vision"] = int(
                evidence.get("frames_sent_to_vision", 0)
            ) + 1
            cv.evidence = evidence
            return cv
    return verdict


def classify_media(path: str) -> PostVerdict:
    """Classify image or video path for glam scoring."""
    lower = path.lower()
    if lower.endswith(VIDEO_EXTENSIONS):
        return classify_video(path)
    return classify_image(path)


_GLAM_EVIDENCE_KEYS = (
    "frames_considered",
    "frames_sent_to_vision",
    "frame_times_sec",
    "frame_metrics",
    "used_cover",
    "candidates",
    "vision_budget",
    "mode",
    "panels",
    "peak_panel",
    "peak_time_sec",
    "peak_in_last_shot",
    "outfit_changes",
    "confirm",
    "sheet",
    "rollup_disagreement",
)
from promptstudio.logging_setup import get_logger

log = get_logger(__name__)


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
        log.warning("glam index write failed for %s: %s", rel_path, e)
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
            if verdict.exposure_tier >= 0:
                glam_block["exposure_tier"] = int(verdict.exposure_tier)
            if verdict.evidence:
                glam_block.update(
                    {
                        k: v
                        for k, v in verdict.evidence.items()
                        if k in _GLAM_EVIDENCE_KEYS
                    }
                )
            meta["glam"] = glam_block
            save_post_metadata(full_path, meta)
        except Exception as e:
            log.warning("glam sidecar write failed for %s: %s", rel_path, e)


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
