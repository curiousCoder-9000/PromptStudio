"""Mode E / Comfy-ref prompt helpers for modelToimage_pro.

Reference image owns face + body. Text should drive outfit, scene, and lighting.
See ComfyUI-master/doc/workflow-tuning.md (Mode E).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set

# Clauses matching these are dropped from freeform positives for Mode E.
_IDENTITY_CLAUSE_RE = re.compile(
    r"(?i)\b("
    r"faces?|eyes?|lips?|noses?|cheeks?|jaws?|smiles?|gazes?|expressions?|"
    r"hairs?|blonde|brunette|redhead|ponytail|bangs|braids?|wigs?|"
    r"ethnicity|asian|caucasian|latina|ebony|pale skin|dark skin|skin tone|"
    r"breasts?|boobs?|cleavage|nipples?|bust|chest|"
    r"waists?|hips?|thighs?|ass|butts?|figures?|hourglass|curvy|slim|petite|voluptuous|"
    r"body shape|body type|anatomy|skin pores|skin texture"
    r")\b"
)

_QUALITY_KEEP_RE = re.compile(
    r"(?i)\b("
    r"raw photo|photorealistic|masterpiece|best quality|8k|4k|"
    r"cinematic lighting|soft natural lighting|shallow depth of field|"
    r"ultra detailed|sharp focus|35mm|f/?1\.?8"
    r")\b"
)

BASE_MODE_E_NEGATIVE = (
    "boxy body, flat chest, rectangular torso, wide waist, bulky uniform, "
    "bad proportions, deformed body, 3d render, cgi, plastic skin, airbrushed, "
    "bad anatomy, bad hands, extra fingers, cartoon, anime, low quality, "
    "watermark, text, logo"
)

# When source clothing matches a key, append these anti terms so outfit can change.
_ANTI_CLOTHING: Dict[str, List[str]] = {
    "bikini": [
        "bikini",
        "red bikini",
        "bikini top",
        "bikini bottom",
        "thong",
        "swimwear",
        "swimsuit",
        "two-piece bikini",
        "micro bikini",
    ],
    "swimsuit": ["swimsuit", "swimwear", "one-piece swimsuit", "bathing suit"],
    "lingerie": [
        "lingerie",
        "underwear",
        "bra",
        "panties",
        "thong",
        "lace lingerie",
        "teddy",
    ],
    "nude": ["nude", "naked", "topless", "bottomless", "fully nude"],
    "dress": ["same dress", "exact same dress", "identical dress"],
    "skirt": ["same skirt", "exact same skirt"],
    "jeans": ["same jeans", "denim shorts from reference"],
}


def _split_clauses(text: str) -> List[str]:
    if not text:
        return []
    parts = re.split(r"[,;\n]+", text)
    return [p.strip(" .") for p in parts if p.strip(" .")]


def strip_identity_clauses(text: str) -> str:
    """Drop comma-clauses that describe face/hair/body; keep outfit/scene/quality."""
    kept: List[str] = []
    for clause in _split_clauses(text):
        if _IDENTITY_CLAUSE_RE.search(clause) and not _QUALITY_KEEP_RE.search(clause):
            # Keep if it also strongly looks like clothing/scene
            if not re.search(
                r"(?i)\b(outfit|dress|skirt|shirt|blouse|jacket|uniform|lingerie|"
                r"bikini|lighting|background|room|outdoor|indoor|pose wearing)\b",
                clause,
            ):
                continue
        kept.append(clause)
    return ", ".join(kept)


def mode_e_positive_from_structured(structured: Dict[str, Any]) -> str:
    """Build outfit/scene-only positive from vision JSON fields."""
    parts: List[str] = []
    for key in ("clothing", "lighting", "background"):
        val = (structured.get(key) or "").strip()
        if val:
            parts.append(val)
    # Pose is skeleton-locked by OpenPose — optional short cue, no body metrics
    pose = (structured.get("pose") or "").strip()
    if pose and not _IDENTITY_CLAUSE_RE.search(pose):
        parts.append(pose)
    core = ", ".join(parts).strip(" ,")
    if not core:
        return ""
    return (
        f"{core}, soft natural lighting, shallow depth of field, "
        f"RAW photo, photorealistic, 8k"
    )


def detect_source_clothing_keys(text: str) -> List[str]:
    lower = (text or "").lower()
    hits: List[str] = []
    for key in _ANTI_CLOTHING:
        if key in lower:
            hits.append(key)
    return hits


def anti_clothing_terms(*texts: str) -> List[str]:
    blob = " ".join(t for t in texts if t)
    keys = detect_source_clothing_keys(blob)
    terms: List[str] = []
    seen: Set[str] = set()
    for key in keys:
        for term in _ANTI_CLOTHING[key]:
            tl = term.lower()
            if tl not in seen:
                seen.add(tl)
                terms.append(term)
    return terms


def merge_negative_prompts(*parts: str, extra_terms: Optional[Sequence[str]] = None) -> str:
    seen: Set[str] = set()
    out: List[str] = []
    for part in parts:
        for clause in _split_clauses(part or ""):
            key = clause.lower()
            if key and key not in seen:
                seen.add(key)
                out.append(clause)
    for term in extra_terms or ():
        key = term.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(term.strip())
    return ", ".join(out)


def build_mode_e_bundle(
    *,
    positive: str = "",
    negative: str = "",
    structured: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return Mode E positive/negative plus metadata for Comfy Pro."""
    structured = structured if isinstance(structured, dict) else None
    clothing = ""
    if structured:
        clothing = str(structured.get("clothing") or "")
        from_struct = mode_e_positive_from_structured(structured)
    else:
        from_struct = ""

    if from_struct:
        comfy_positive = from_struct
        source = "structured"
    else:
        comfy_positive = strip_identity_clauses(positive) or strip_identity_clauses(
            positive.replace("masterpiece,", "").replace("best quality,", "")
        )
        source = "stripped"
        if not comfy_positive.strip():
            comfy_positive = (
                "stylish outfit, soft natural lighting, RAW photo, photorealistic, 8k"
            )
            source = "fallback"

    antis = anti_clothing_terms(clothing, positive)
    comfy_negative = merge_negative_prompts(
        negative,
        BASE_MODE_E_NEGATIVE,
        extra_terms=antis,
    )
    return {
        "positive": comfy_positive,
        "negative": comfy_negative,
        "anti_terms": antis,
        "source": source,
        "clothing_keys": detect_source_clothing_keys(f"{clothing} {positive}"),
    }


def enrich_exports(
    exports: Optional[Dict[str, str]],
    *,
    positive: str,
    negative: str,
    structured: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Ensure exports include comfy_ref / comfy_negative."""
    out = dict(exports or {})
    bundle = build_mode_e_bundle(
        positive=positive, negative=negative, structured=structured
    )
    out["comfy_ref"] = bundle["positive"]
    out["comfy_negative"] = bundle["negative"]
    if "negative" not in out:
        out["negative"] = negative
    return out
