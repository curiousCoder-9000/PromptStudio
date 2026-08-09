"""Vision keep/reject classifier for archive photos and reels (Ollama).

One vocabulary, one number: every file gets a 0–4 ``exposure_tier`` with
explicit garment-class anchors, and keep/reject is *derived* from it against
``CLASSIFY_REJECT_MAX_TIER``. Nothing stores the word "reject", so moving the
threshold re-thresholds the archive without a single new vision call.

That is the lesson from the subsystem this replaces. The old ``glam_score`` was
a 0–3 scalar produced directly by the prompt, so every change of mind about
where the line sat cost a full-archive rescore — and because the taste lived in
prose, six prompt revisions produced six incomparable archives. Measurement and
policy are separated here: the model reports a tier, the config decides what a
tier means.

Reels are scored from a **contact sheet** — one vision call over a chronological
grid of freeze-frames spanning the whole clip, so a reveal in the final seconds
is seen. Single-frame sampling structurally could not see it. The sheet is kept
on disk under ``_classify/`` so the review UI can show what the model actually
looked at; a verdict you cannot audit is a verdict you cannot trust.

Tune via ``CLASSIFY_*`` env (see ``.env.example``). Never commit classify dumps.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from promptstudio.config import (
    CLASSIFY_KEEP_ALIVE,
    CLASSIFY_MAX_EDGE,
    CLASSIFY_NUM_CTX,
    CLASSIFY_NUM_PREDICT,
    CLASSIFY_REEL_CANDIDATES,
    CLASSIFY_REEL_SHEET,
    CLASSIFY_REEL_SHEET_PANELS,
    CLASSIFY_REEL_UNCERTAIN_HI,
    CLASSIFY_REEL_UNCERTAIN_LO,
    CLASSIFY_REEL_VISION_MAX,
    CLASSIFY_REJECT_MAX_TIER,
    CLASSIFY_RETRIES,
    CLASSIFY_SHEET_DIR,
    CLASSIFY_SHEET_MAX_EDGE,
    CLASSIFY_STRUCTURED,
    CLASSIFY_TIMEOUT,
    MODEL_NAME,
    OLLAMA_URL,
    VIDEO_EXTENSIONS,
)
from promptstudio.logging_setup import get_logger
from promptstudio.scraping.video_frames import (
    compose_contact_sheet,
    extract_frame_at,
    find_video_cover_image,
    select_best_video_frames,
)
from promptstudio.storage.paths import safe_join

log = get_logger(__name__)

# v2: restore tier 4 (bikini/lingerie). v3: stop dumping normal fashion into 3
# (photo eval: v2 still sent 29/36 true-tier-2 -> 3).
# v4: any man present -> tier 0 (discard couples/groups with men, not only men-only).
# v5: unusable quality (blur / heavy distortion / pixelation) -> tier 0.
# v6: poster-like / flyer / graphic promo layouts -> tier 0.
# v7a: undo v3's downward tiebreak at the 2/3 boundary, and nothing else — a
#   clean ablation, so whatever moves is attributable to the flip. v3's "if
#   unsure between 2 and 3, choose 2" cost 27 of 43 true tier-3s on the round-2
#   holdout. The error budget was entirely recall (precision 1.000, recall
#   0.576), so the tiebreak now points up.
CLASSIFY_FRAME_VERSION = "v4-ordinal-frame-v7a"
CLASSIFY_SHEET_VERSION = "v4-reel-sheet"

# Human labels for the tiers. The API sends these so the review UI never has to
# keep a second copy of the ontology in JavaScript and let it drift.
TIER_LABELS: Dict[int, str] = {
    -1: "Not classified",
    0: "Unusable",
    1: "Fully modest",
    2: "Normal fashion",
    3: "Revealing daywear",
    4: "Swim / lingerie",
}

# Failures that mean "no frames", as opposed to "the model call failed".
_NO_FRAMES_ERRORS = frozenset({"no_usable_reel_frames", "no frame scores"})

# The sheet reply parsed but yielded no usable panel row — the model did not
# read the captions it was shown. Deliberately *not* in _NO_FRAMES_ERRORS: the
# video decoded fine, so falling back to the cover would score the "before"
# outfit, the exact failure the contact sheet exists to avoid. Falls back to a
# ranked frame scored with the same ordinal vocabulary instead.
#
# Only the empty case trips this. Under-reporting does not: a model that omits
# the panels it judged empty is behaving sensibly, and max-over-panels treats an
# absent panel exactly like tier 0, so the reading stays correct.
_SHEET_UNREADABLE = "sheet_panels_unreadable"

# Evidence keys worth keeping in the sidecar. The full evidence dict carries
# per-frame metrics that would bloat every .meta.json for no reader.
_EVIDENCE_KEYS = (
    "frames_considered",
    "frames_sent_to_vision",
    "frame_times_sec",
    "used_cover",
    "candidates",
    "vision_budget",
    "mode",
    "peak_panel",
    "peak_time_sec",
    "peak_in_last_shot",
    "outfit_changes",
    "confirm",
    "sheet",
    "rollup_disagreement",
)

# Anchors shared by the single-frame and contact-sheet prompts. Written as hard
# decision rules, not soft vibes — VLMs collapse adjacent tiers unless the 3<->4
# and 2<->3 cuts are named as garment classes.
_TIER_ANCHORS = (
    "     0 = DISCARD: no woman as main subject, OR any adult man / male person is "
    "visible in the frame (couples, male friend in shot, group with men, men only), "
    "OR title card / logo / scenery / food / meme / cartoon with no woman alone, "
    "OR unusable image quality — heavy blur, motion blur, extreme soft-focus, "
    "heavy pixelation/compression, warped or extreme lens distortion, heavy "
    "glitch/artifact, out-of-focus subject, or face/outfit cannot be judged clearly, "
    "OR poster-like / graphic layout — event flyer, promo poster, concert/tour poster, "
    "magazine-cover graphic, collage with heavy typography, ad layout, or designed "
    "graphic where text/layout dominates over a natural photo of a person\n"
    "     1 = fully modest: opaque everyday clothes; skin only face/hands/maybe wrists; "
    "no cleavage, no bare midriff, no short hem\n"
    "     2 = normal fashion: street/casual/office wear with SOME skin (bare arms, "
    "shoulders, collarbone, modest neckline) OR fitted clothing that does NOT show "
    "midriff, deep cleavage, or upper thigh. Sundress with normal length, jeans+top, "
    "blouse, sweater, jacket over a top — stay at 2 even if stylish or glamorous\n"
    "     3 = revealing daywear (NOT swim/lingerie): crop top with bare midriff, "
    "deep cleavage or plunging neckline, bare back, mini skirt/dress with upper thigh, "
    "tight bodycon that clearly emphasises bust/hips, sideboob through clothes, "
    "cut-outs on the torso. Still street/party clothes, not beachwear or underwear\n"
    "     4 = maximally revealing / undress-class garments — USE 4 when ANY of these "
    "apply: bikini, microbikini, monokini, swimsuit, swimwear, lingerie, bra+panties, "
    "bodysuit worn as underwear, sheer or mesh over bare skin, see-through fabric, "
    "pasties, underboob as the look, towel/robe open on bare body, nude or near-nude. "
    "If it would be worn at a beach, pool, or as underwear → 4, not 3\n"
)

CLASSIFY_FRAME_PROMPT = (
    "Rate the outfit in this still image for a personal fashion KEEP filter. "
    "Return ONLY valid JSON.\n"
    '  "has_woman": boolean — a woman / female-presenting person is a main subject. '
    "false for title cards, logos, text-only frames, scenery, food, cartoons, or when "
    "only men are present. true if a woman is a main subject even when a man also appears "
    "(still set exposure_tier to 0 in that case).\n"
    '  "exposure_tier": integer 0-4. Decide in this order (stop at first match):\n'
    "    (1) If no woman as main subject → 0. "
    "Also → 0 if ANY adult man / male-presenting person is visible in the frame "
    "(boyfriend/couple shots, group photos with men, male friend in background). "
    "Also → 0 if image quality is unusable: heavy blur, motion blur, extreme soft-focus, "
    "heavy pixelation/compression mush, severe warping/distortion, heavy glitch, or the "
    "face/outfit cannot be judged clearly. Mild Instagram filters or slight softness are OK. "
    "Also → 0 if the image looks like a poster or graphic design: event/promo flyer, "
    "concert poster, magazine-cover graphic, heavy text layout, collage-ad, or designed "
    "promo art (not a natural portrait/fashion photo). "
    "Only sharp enough, women-only, natural photos continue past this step.\n"
    "    (2) If garment is swimwear, bikini, lingerie, sheer/mesh over bare skin, or "
    "near-nude → 4.\n"
    "    (3) Else if AT LEAST ONE clear reveal is visible — bare midriff (stomach skin "
    "between top and bottoms), deep cleavage (not just a modest V-neck), fully bare back, "
    "mini hem at upper thigh, large torso cut-outs, short shorts or hot pants at upper "
    "thigh, tight ass-hugging shorts/shortalls — → 3.\n"
    "    (4) Else if she is clothed in normal or stylish fashion (dress, jeans, top, "
    "blouse, coat, jumpsuit, etc.) → 2.\n"
    "    (5) Else fully covered modest everyday clothes with almost no skin → 1.\n"
    "Tier definitions:\n"
    + _TIER_ANCHORS
    + "Hard rules (override vibes):\n"
    "  - ANY man visible in the frame = ALWAYS tier 0. Couples and mixed groups are discard, "
    "even if the woman's outfit would otherwise be 2–4.\n"
    "  - Unusable quality (heavy blur / distortion / pixelation / unreadable subject) = "
    "ALWAYS tier 0. Do not guess the outfit on a mushy or warped frame.\n"
    "  - Poster-like / flyer / heavy graphic promo layout = ALWAYS tier 0, even if a "
    "woman appears on the poster. Natural photos with a small watermark/sticker are OK.\n"
    "  - Bikini / swimsuit / lingerie / sheer lingerie-look = ALWAYS 4. Never call these 3.\n"
    "  - Most dresses, jumpsuits, jeans+top, blouses, sweaters = 2 even if glamorous, "
    "tight, or low-shoulder. Glamour alone is not tier 3.\n"
    "  - Bare arms, bare shoulders, collarbone, sleeveless, off-shoulder with covered "
    "midriff and normal neckline = 2, not 3.\n"
    "  - Escalate 2→3 whenever a listed reveal in step (3) is visible. "
    "If unsure between 2 and 3, choose 3.\n"
    "  - Crop top that clearly shows the stomach = 3. A short top that still covers "
    "the waistband = 2.\n"
    "  - Short shorts / hot pants / tight shortalls showing upper thigh = 3, even with "
    "an ordinary top.\n"
    "  - Do NOT skip tier 4. The scale has five steps; using only 0–3 is wrong.\n"
    '  "figure_visible": boolean — bust or body shape is clearly discernible\n'
    '  "confidence": number 0.0-1.0 — lower when cropped, dark, or garment class is unclear\n'
    '  "brief_reason": short phrase naming the garment class (e.g. "bikini set", '
    '"crop top + jeans", "crewneck sweater")\n'
    "Judge only clothing and body. Ignore captions, stickers, watermarks and UI chrome. "
    "Between 3 and 4, require an undress-class garment for 4."
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
        "or when only men are present.\n"
        '  "exposure_tier": integer 0-4 for how much the outfit in THAT panel reveals. '
        "If ANY adult man is visible in THAT panel → 0 (couples/mixed groups discard). "
        "If THAT panel is heavily blurred, distorted, pixelated, or the subject is unreadable → 0. "
        "If THAT panel looks like a poster/flyer/graphic promo → 0:\n"
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

FRAME_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["has_woman", "exposure_tier", "confidence"],
    "properties": {
        "has_woman": {"type": "boolean"},
        "exposure_tier": {"type": "integer", "minimum": 0, "maximum": 4},
        "figure_visible": {"type": "boolean"},
        "confidence": {"type": "number"},
        # Capped: an unbounded reason can run past num_predict and truncate the
        # JSON mid-object, which costs a retry and can leave the item unscored.
        "brief_reason": {"type": "string", "maxLength": 120},
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
        "brief_reason": {"type": "string", "maxLength": 120},
    },
}


def encode_image_for_classify(
    image_path: str, max_edge: int = CLASSIFY_MAX_EDGE
) -> Optional[str]:
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


def tier_is_reject(tier: int, cut: Optional[int] = None) -> bool:
    """Policy, in one place. Tiers 0..cut are rejects; -1 is neither."""
    cut_v = int(CLASSIFY_REJECT_MAX_TIER if cut is None else cut)
    return 0 <= int(tier) <= cut_v


@dataclass
class MediaVerdict:
    """One classify attempt on one file."""

    path: str
    has_woman: bool = False
    figure_visible: bool = False
    exposure_tier: int = -1  # 0-4; -1 means the attempt failed
    confidence: float = 0.0
    brief_reason: str = ""
    ok: bool = False
    error: str = ""
    source: str = "image"  # image | video_sheet | video_cover | video
    prompt_version: str = CLASSIFY_FRAME_VERSION
    sheet_path: str = ""  # archive-relative, under _classify/; reels only
    evidence: Dict[str, Any] = field(default_factory=dict)

    def is_reject(self, cut: Optional[int] = None) -> bool:
        return bool(self.ok) and tier_is_reject(self.exposure_tier, cut)

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(int(self.exposure_tier), "Unknown")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tier_label"] = self.tier_label
        d["reject"] = self.is_reject()
        return d


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


def _is_bad_request(exc: Exception) -> bool:
    """True for HTTP 400 — how Ollama rejects an unsupported request field."""
    return isinstance(exc, urllib.error.HTTPError) and exc.code == 400


def _describe_request_error(exc: Exception) -> str:
    """str(HTTPError) omits the body, which is where Ollama says what broke.

    The body is a stream and can only be read once. Cache it on the exception:
    without that, a retry against the same error object re-reads an exhausted
    stream and the diagnostic degrades to a bare "HTTP Error 400: Bad Request"
    by the time it reaches the caller.
    """
    text = str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        body = getattr(exc, "_ps_body", None)
        if body is None:
            try:
                body = exc.read().decode("utf-8", "replace").strip()
            except Exception:
                body = ""
            try:
                exc._ps_body = body
            except AttributeError:
                pass
        if body:
            text = f"{text}: {body[:200]}"
    return text


def _ollama_vision_json(
    image_path: str | Sequence[str],
    prompt: str = CLASSIFY_FRAME_PROMPT,
    *,
    schema: Optional[Dict[str, Any]] = None,
    max_edge: int = CLASSIFY_MAX_EDGE,
    num_predict: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Run one vision request and return the parsed JSON object.

    Retries transient failures and unparseable output up to CLASSIFY_RETRIES —
    without this, a single blip left a file unclassified forever, which was the
    bulk of the historical unscored backlog.
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
                raw = (
                    json.loads(response.read().decode("utf-8")).get("response") or ""
                ).strip()
        except Exception as e:
            last_error = _describe_request_error(e)
            # A rejected `format` will not start working on retry — drop the
            # constraint and let the regex fallback handle the free-text reply.
            # Match on the HTTP status, not the message: urllib renders an
            # HTTPError as just "HTTP Error 400: Bad Request", so a substring
            # test for "format" never fired and the fallback was unreachable.
            if "format" in payload and _is_bad_request(e):
                payload.pop("format", None)
                last_error += " (retrying without structured output)"
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


def _verdict_from_tier_data(
    path: str,
    data: Optional[Dict[str, Any]],
    *,
    source: str,
    prompt_version: str,
) -> MediaVerdict:
    """Build a verdict from ordinal (exposure_tier) model output."""
    verdict = MediaVerdict(path=path, source=source, prompt_version=prompt_version)
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
    verdict.figure_visible = bool(data.get("figure_visible"))
    verdict.ok = True
    return verdict


def classify_image(
    image_path: str,
    *,
    source: str = "image",
    prompt_version: Optional[str] = None,
    max_edge: int = CLASSIFY_MAX_EDGE,
) -> MediaVerdict:
    """Classify a still image with the ordinal tier vocabulary."""
    data = _ollama_vision_json(
        image_path,
        prompt=CLASSIFY_FRAME_PROMPT,
        schema=FRAME_SCHEMA,
        max_edge=max_edge,
    )
    return _verdict_from_tier_data(
        image_path,
        data,
        source=source,
        prompt_version=prompt_version or CLASSIFY_FRAME_VERSION,
    )


# ── Contact-sheet reel scoring ───────────────────────────────────────


def sheet_rel_path(rel_path: str) -> str:
    """Archive-relative path of the saved contact sheet for a reel.

    Mirrors the media layout under `_classify/`, e.g.
    ``nina/reel_123.mp4`` -> ``nina/reel_123.sheet.jpg``.

    Deliberately does *not* strip a leading slash. Doing so turned an absolute
    input into a relative one, which quietly defeated `safe_join`'s
    absolute-path rejection — the result stayed inside the sheet dir, but by
    accident rather than by the check that exists to guarantee it.
    """
    rel = (rel_path or "").replace("\\", "/")
    if not rel:
        return ""
    base, _ = os.path.splitext(rel)
    return f"{base}.sheet.jpg"


def sheet_full_path(rel_path: str) -> str:
    """Filesystem path for `sheet_rel_path`, or "" if it escapes the sheet dir."""
    rel = sheet_rel_path(rel_path)
    if not rel:
        return ""
    return safe_join(os.path.expanduser(CLASSIFY_SHEET_DIR), rel) or ""


def _aggregate_sheet_panels(
    data: Dict[str, Any],
    n_panels: int,
) -> tuple[int, int, List[Dict[str, Any]]]:
    """Reduce per-panel readings to (reel_tier, peak_panel_1based, clean_panels).

    Max-over-panels is computed here rather than trusting the model's own
    ``reel_exposure`` rollup: the panel array is the auditable part, and max is
    exactly the "revealing at any point counts" semantic the filter wants.
    Tiers on panels the model marked as having no woman are ignored.
    """
    panels: List[Dict[str, Any]] = []
    seen: set[int] = set()
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
            # First reading per index wins. A model that repeats `i` has stopped
            # tracking the captions, and taking the max would let one panel it
            # re-read three times outvote the eight it never looked at.
            if idx in seen:
                continue
            seen.add(idx)
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


def _needs_confirm(
    tier: int,
    confidence: float,
    peak_in_last_shot: bool = False,
) -> bool:
    """Escalate to a full-resolution look at the peak frame.

    Contact-sheet panels are ~256px wide, which is enough for "how much skin"
    but not for sheer-vs-opaque. Confirm exactly where it matters: on the
    boundaries that decide keep/reject, when the model is unsure, or when a high
    tier came from the final shot — the reveal case the whole pipeline exists
    for, and the one most worth being right about.
    """
    return tier in (1, 2, 3) or confidence < 0.5 or (peak_in_last_shot and tier >= 3)


def _classify_reel_sheet(
    video_path: str, vision_budget: int, *, sheet_out: str = ""
) -> MediaVerdict:
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

    if sheet_out:
        try:
            os.makedirs(os.path.dirname(sheet_out), exist_ok=True)
        except OSError as exc:
            log.warning("sheet dir create failed for %s: %s", sheet_out, exc)
            sheet_out = ""

    sheet = compose_contact_sheet(video_path, panels=n_panels, out_path=sheet_out)
    if sheet is None:
        return MediaVerdict(
            path=video_path,
            source="video",
            error="no_usable_reel_frames",
            prompt_version=CLASSIFY_SHEET_VERSION,
            evidence=evidence,
        )
    # Kept only when it lands in the sheet dir; a temp sheet is still cleaned up.
    keep_sheet = bool(sheet_out) and sheet.path == sheet_out

    # Frames decoded to choose the panels — not the panel count. Recording
    # picks here made the sheet path look like it had considered 9 frames when
    # it had ranked 16.
    evidence["frames_considered"] = int(sheet.considered) or len(sheet.picks)
    evidence["frame_times_sec"] = [round(p.t_sec, 2) for p in sheet.picks]
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
            return MediaVerdict(
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
        evidence["panels_read"] = len(panels)
        if len(panels) < len(sheet.picks):
            # Visible in the sidecar even when we still trust the reading: a
            # reel scored from 4 of 9 panels should not look like one scored
            # from all 9.
            evidence["panels_incomplete"] = True

        # No usable row at all means the captions were not read. Scoring from
        # the model's own `reel_exposure` here would trust the one number the
        # design deliberately treats as unauditable.
        if not panels:
            return MediaVerdict(
                path=video_path,
                source="video_sheet",
                error=_SHEET_UNREADABLE,
                prompt_version=CLASSIFY_SHEET_VERSION,
                evidence=evidence,
            )

        model_rollup = data.get("reel_exposure")
        if model_rollup is not None:
            evidence["model_reel_exposure"] = model_rollup
            try:
                if int(model_rollup) != tier:
                    # Not an error — logged so prompt drift is visible.
                    evidence["rollup_disagreement"] = True
            except (TypeError, ValueError):
                pass

        peak_t = 0.0
        if 1 <= peak_panel <= len(sheet.picks):
            peak_t = float(sheet.picks[peak_panel - 1].t_sec)
            evidence["peak_time_sec"] = round(peak_t, 2)
            evidence["peak_in_last_shot"] = (
                sheet.picks[peak_panel - 1].shot == sheet.picks[-1].shot
            )

        verdict = MediaVerdict(
            path=video_path,
            has_woman=any(p["has_woman"] for p in panels) if panels else tier > 0,
            exposure_tier=tier,
            confidence=confidence,
            brief_reason=str(data.get("brief_reason") or "")[:160],
            figure_visible=bool(data.get("figure_visible")),
            ok=True,
            source="video_sheet",
            prompt_version=CLASSIFY_SHEET_VERSION,
        )

        # Cascade: re-read the peak frame at full resolution when it matters.
        if (
            vision_budget >= 2
            and peak_t > 0
            and _needs_confirm(
                tier, confidence, bool(evidence.get("peak_in_last_shot"))
            )
        ):
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
                    verdict.figure_visible = confirm.figure_visible
                    verdict.brief_reason = confirm.brief_reason or verdict.brief_reason
                elif confirm.ok and not confirm.has_woman and verdict.has_woman:
                    evidence["confirm_lost_subject"] = True

        verdict.evidence = evidence
        return verdict
    finally:
        temps = [confirm_path]
        if not keep_sheet:
            temps.append(sheet.path)
        for temp in temps:
            if temp and os.path.isfile(temp):
                try:
                    os.remove(temp)
                except OSError:
                    pass


def _classify_frame_ordinal(
    frame_path: str,
    *,
    source: str,
    max_edge: int = CLASSIFY_MAX_EDGE,
) -> MediaVerdict:
    return classify_image(frame_path, source=source, max_edge=max_edge)


# ── Ranked-frame reel scoring (CLASSIFY_REEL_SHEET=0) ────────────────


def _prefer_verdict(a: MediaVerdict, b: MediaVerdict) -> MediaVerdict:
    """Pick the better of two frame verdicts (highest tier, then confidence)."""
    if not a.ok:
        return b
    if not b.ok:
        return a
    if b.exposure_tier > a.exposure_tier:
        return b
    if b.exposure_tier == a.exposure_tier and b.confidence > a.confidence:
        return b
    return a


def _needs_second_reel_frame(v: MediaVerdict) -> bool:
    """True when the first pass is weak enough to be worth a second look."""
    if not v.ok:
        return True
    lo = float(CLASSIFY_REEL_UNCERTAIN_LO)
    hi = float(CLASSIFY_REEL_UNCERTAIN_HI)
    if lo <= v.confidence <= hi:
        return True
    if not v.has_woman and v.confidence < 0.75:
        return True
    # A confident *low* tier is the reveal case: the model is sure about the
    # "before" outfit. Keep looking rather than stopping early.
    if v.has_woman and tier_is_reject(v.exposure_tier):
        return True
    return False


def _classify_reel_frames(video_path: str, vision_budget: int) -> MediaVerdict:
    evidence: Dict[str, Any] = {
        "prompt_version": CLASSIFY_FRAME_VERSION,
        "candidates": int(CLASSIFY_REEL_CANDIDATES),
        "vision_budget": vision_budget,
        "frames_considered": 0,
        "frames_sent_to_vision": 0,
        "frame_times_sec": [],
        "used_cover": False,
        "mode": "frames",
    }
    best: Optional[MediaVerdict] = None
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

        if not picks:
            return MediaVerdict(
                path=video_path,
                source="video",
                error="no_usable_reel_frames",
                prompt_version=CLASSIFY_FRAME_VERSION,
                evidence=evidence,
            )

        for pick in picks:
            if not pick.path:
                continue
            if int(evidence["frames_sent_to_vision"]) >= vision_budget:
                break
            if best is not None and not _needs_second_reel_frame(best):
                break

            v = _classify_frame_ordinal(pick.path, source="video")
            v.path = video_path
            evidence["frames_sent_to_vision"] = (
                int(evidence["frames_sent_to_vision"]) + 1
            )
            evidence["frame_times_sec"].append(round(float(pick.t_sec), 3))
            best = v if best is None else _prefer_verdict(best, v)

        if best is None:
            return MediaVerdict(
                path=video_path,
                source="video",
                error="no frame scores",
                prompt_version=CLASSIFY_FRAME_VERSION,
                evidence=evidence,
            )
        best.path = video_path
        best.evidence = evidence
        return best
    finally:
        for fp in pick_paths:
            try:
                if fp and os.path.isfile(fp):
                    os.remove(fp)
            except OSError:
                pass


def _classify_sheet_fallback_frame(
    video_path: str,
    sheet_evidence: Dict[str, Any],
) -> MediaVerdict:
    """Score one ranked frame when the sheet reply was unusable."""
    evidence = dict(sheet_evidence)
    evidence["mode"] = "sheet_fallback_frame"

    picks = select_best_video_frames(video_path, top_n=1, write_jpeg=True)
    if not picks or not picks[0].path:
        return MediaVerdict(
            path=video_path,
            source="video",
            error="no_usable_reel_frames",
            prompt_version=CLASSIFY_SHEET_VERSION,
            evidence=evidence,
        )

    pick = picks[0]
    try:
        verdict = _classify_frame_ordinal(pick.path, source="video")
    finally:
        if os.path.isfile(pick.path):
            try:
                os.remove(pick.path)
            except OSError:
                pass

    verdict.path = video_path
    evidence["frames_sent_to_vision"] = int(evidence.get("frames_sent_to_vision", 0)) + 1
    evidence["frame_times_sec"] = [round(float(pick.t_sec), 2)]
    verdict.evidence = evidence
    return verdict


def classify_video(
    video_path: str,
    max_frames: Optional[int] = None,
    *,
    rel_path: str = "",
) -> MediaVerdict:
    """Classify a reel/video.

    Default path renders a chronological contact sheet of the whole clip and
    scores it in one vision call, optionally confirming the peak frame at full
    resolution. Set CLASSIFY_REEL_SHEET=0 for the ranked-frame path.

    `rel_path` is where the sheet gets filed under `_classify/` so the review UI
    can show it. Without it the sheet is a temp file, as before.
    """
    vision_budget = max(
        1, int(max_frames if max_frames is not None else CLASSIFY_REEL_VISION_MAX)
    )

    if CLASSIFY_REEL_SHEET:
        out = sheet_full_path(rel_path) if rel_path else ""
        verdict = _classify_reel_sheet(video_path, vision_budget, sheet_out=out)
        if out and verdict.source == "video_sheet" and os.path.isfile(out):
            verdict.sheet_path = sheet_rel_path(rel_path)
        if not verdict.ok and verdict.error == _SHEET_UNREADABLE:
            saved_sheet = verdict.sheet_path
            verdict = _classify_sheet_fallback_frame(video_path, verdict.evidence)
            # The sheet is still the evidence for what the model was shown, even
            # though the reading came from a single frame.
            verdict.sheet_path = saved_sheet
    else:
        verdict = _classify_reel_frames(video_path, vision_budget)

    if verdict.ok:
        return verdict

    # Undecodable video — a companion cover still is better than nothing.
    # Never a short-circuit: covers show the "before" outfit. Only frame
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
            evidence["frames_sent_to_vision"] = (
                int(evidence.get("frames_sent_to_vision", 0)) + 1
            )
            cv.evidence = evidence
            return cv
    return verdict


def classify_media(path: str, *, rel_path: str = "") -> MediaVerdict:
    """Classify one archive file — photo or reel."""
    if path.lower().endswith(VIDEO_EXTENSIONS):
        return classify_video(path, rel_path=rel_path)
    return classify_image(path)


def media_kind_for(path: str) -> str:
    return "reel" if str(path).lower().endswith(VIDEO_EXTENSIONS) else "photo"


def current_prompt_version(path: str) -> str:
    """The prompt version a fresh classify of `path` would record.

    Config decides this — CLASSIFY_REEL_SHEET changes the reel vocabulary — so
    callers must not hardcode it.
    """
    if str(path).lower().endswith(VIDEO_EXTENSIONS):
        return CLASSIFY_SHEET_VERSION if CLASSIFY_REEL_SHEET else CLASSIFY_FRAME_VERSION
    return CLASSIFY_FRAME_VERSION


def active_prompt_versions() -> List[str]:
    """Every version a fresh classify could produce, for staleness queries.

    Includes the sheet's single-frame fallback and confirm version, which a reel
    legitimately ends up tagged with — treating those as stale would re-run them
    forever.
    """
    return sorted({CLASSIFY_FRAME_VERSION, current_prompt_version("x.mp4")})


def delete_sheet(rel_path: str) -> None:
    """Remove a saved contact sheet. Best effort — it is derived data."""
    full = sheet_full_path(rel_path)
    if full and os.path.isfile(full):
        try:
            os.remove(full)
        except OSError as exc:
            log.debug("sheet delete failed for %s: %s", full, exc)


def persist_verdict(
    rel_path: str,
    verdict: MediaVerdict,
    full_path: str = "",
    *,
    duration_ms: Optional[int] = None,
) -> None:
    """Write one verdict to the SQLite index and the file's sidecar."""
    try:
        from promptstudio.storage.db import ArchiveIndex

        ArchiveIndex.get().set_verdict(
            rel_path,
            creator=(rel_path or "").split("/", 1)[0],
            tier=verdict.exposure_tier if verdict.ok else -1,
            reason=verdict.brief_reason if verdict.ok else "",
            media_kind=media_kind_for(full_path or rel_path),
            verdict_source=verdict.source,
            confidence=verdict.confidence if verdict.ok else None,
            prompt_version=verdict.prompt_version if verdict.ok else None,
            sheet_path=verdict.sheet_path or None,
            # Recorded only on failure, so a row with `error` set is a retry
            # candidate rather than something that was never attempted.
            error=None if verdict.ok else (verdict.error or "unknown"),
            duration_ms=duration_ms,
        )
    except Exception as e:
        log.warning("verdict index write failed for %s: %s", rel_path, e)

    # Sidecar only on success. A failed retry must not overwrite a good verdict
    # — the failure is recorded in the DB's `error` column instead.
    if verdict.ok and full_path and os.path.isfile(full_path):
        try:
            from promptstudio.storage.metadata import (
                load_post_metadata,
                save_post_metadata,
            )

            meta = load_post_metadata(full_path) or {}
            block: Dict[str, Any] = {
                "exposure_tier": int(verdict.exposure_tier),
                "tier_label": verdict.tier_label,
                "reject": verdict.is_reject(),
                "has_woman": bool(verdict.has_woman),
                "figure_visible": bool(verdict.figure_visible),
                "confidence": verdict.confidence,
                "brief_reason": verdict.brief_reason,
                "source": verdict.source,
                "prompt_version": verdict.prompt_version,
            }
            if verdict.sheet_path:
                block["sheet_path"] = verdict.sheet_path
            if verdict.evidence:
                block.update(
                    {k: v for k, v in verdict.evidence.items() if k in _EVIDENCE_KEYS}
                )
            meta["classify"] = block
            save_post_metadata(full_path, meta)
        except Exception as e:
            log.warning("verdict sidecar write failed for %s: %s", rel_path, e)
