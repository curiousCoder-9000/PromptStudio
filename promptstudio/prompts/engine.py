"""Ollama vision → structured extract → photorealistic SD/Flux prompt bundle.

Default tone is public-safe (balanced fashion / portrait). Set
PROMPT_INTENSITY=high in `.env` for more sensual rewrites (private use).
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.request
from typing import Any, Dict, Optional

import cv2

from promptstudio.config import (
    MODEL_NAME,
    OLLAMA_TEXT_URL,
    OLLAMA_URL,
    PROMPT_INTENSITY,
    PROMPT_PIPELINE_VERSION,
    REALISM_BIAS,
    REWRITE_MODEL_NAME,
)
from promptstudio.prompts.cache import PromptCache
from promptstudio.prompts.comfy_mode import enrich_exports
from promptstudio.prompts.styles import CreatorStyleStore

# Back-compat for imports that still use EROTIC_INTENSITY
EROTIC_INTENSITY = PROMPT_INTENSITY

_cache = PromptCache()
_styles = CreatorStyleStore()

ENGINE_ID = f"Ollama ({MODEL_NAME}) {PROMPT_PIPELINE_VERSION}"

STRUCTURED_FIELDS = (
    "face",
    "hair",
    "body",
    "clothing",
    "pose",
    "expression",
    "lighting",
    "background",
)

# Map legacy cache keys → current fields
_LEGACY_FIELD_MAP = {
    "breasts": "body",
    "waist_hips": "body",
}


def _intensity_rank() -> str:
    """Normalize intensity to low | balanced | high."""
    v = (PROMPT_INTENSITY or "balanced").strip().lower()
    if v in ("low", "mild", "conservative", "safe"):
        return "low"
    if v in ("high", "explicit", "sensual", "erotic"):
        return "high"
    return "balanced"


def encode_image_to_base64(image_path: str) -> Optional[str]:
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode("utf-8")
    except OSError as e:
        print(f"Base64 encoding error for {image_path}: {e}")
        return None


def clean_vision_output(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"^\s*\d+[\.\)]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[#*_`]+", "", text)
    text = re.sub(
        r"(?i)^(here is|this is|the image shows|description:|caption:).*?[:\n]",
        "",
        text,
    ).strip()
    text = re.sub(r"\s+", " ", text).strip()
    if text and text[-1] not in ".!?":
        text = text.rstrip(",; ") + "."
    return text


def build_vision_prompt() -> str:
    """Legacy single-paragraph prompt (fallback / debugging)."""
    rank = _intensity_rank()
    if rank == "high":
        base = (
            "You are an expert photorealistic image captioner for Stable Diffusion and Flux. "
            "Describe the photo in rich visual detail as one continuous dense paragraph. "
            "Focus on the subject’s face, hair, body shape, clothing, pose, and lighting. "
            "Be direct and highly descriptive when body features are clearly visible."
        )
    elif rank == "low":
        base = (
            "You are an expert photorealistic fashion and portrait captioner for Stable Diffusion. "
            "Describe the photo as one continuous paragraph focusing on clothing, pose, "
            "expression, lighting, and background. Avoid graphic body detail."
        )
    else:
        base = (
            "You are an expert photorealistic portrait and fashion captioner for Stable Diffusion and Flux. "
            "Describe the photo in detailed visual language as one continuous dense paragraph. "
            "Cover face, hair, outfit, pose, expression, lighting, and setting. "
            "Describe body proportions only when clearly visible and relevant to the outfit."
        )
    realism = (
        " CRITICAL REALISM RULES: "
        "Keep realistic body proportions. Never use extreme words like massive, enormous, "
        "gigantic, balloon, cartoonish or inhuman. "
        "Do not invent details that are not clearly visible in the photo."
    )
    focus = " Write only the descriptive paragraph — no lists, no numbering, no introduction."
    if REALISM_BIAS == "strong":
        return base + realism + focus
    return base + focus


def build_structured_vision_prompt() -> str:
    rank = _intensity_rank()
    body_hint = (
        "For body describe silhouette, proportions, and clothing interaction only if visible."
        if rank != "high"
        else "For body describe silhouette, proportions, figure, and clothing interaction when visible; stay anatomically realistic."
    )
    return (
        "Analyze this photo. Return ONLY valid JSON with these keys "
        "(use empty string if not clearly visible — never invent): "
        "face, hair, body, clothing, pose, expression, lighting, background. "
        f"{body_hint} "
        "Keep realistic proportions. No markdown, no code fences, JSON object only."
    )


def build_rewrite_prompt(structured: Dict[str, Any], style_prefix: str = "") -> str:
    rank = _intensity_rank()
    style_line = (
        f" Creator style hints (optional, only if consistent): {style_prefix}."
        if style_prefix
        else ""
    )
    if rank == "high":
        tone = (
            "You convert factual visual JSON into one continuous dense, sensual photorealistic "
            "image-generation prompt for Stable Diffusion / Flux. "
            "Use ONLY facts present in the JSON. Do not invent body parts, clothing, or setting. "
            f"Intensity: high. Be descriptive about figure and outfit when supported by the JSON; stay anatomically realistic."
        )
    elif rank == "low":
        tone = (
            "You convert factual visual JSON into one continuous photorealistic fashion/portrait "
            "image-generation prompt for Stable Diffusion / Flux. "
            "Use ONLY facts present in the JSON. Emphasize clothing, pose, lighting, and scene. "
            "Avoid graphic or sensual body language."
        )
    else:
        tone = (
            "You convert factual visual JSON into one continuous dense photorealistic "
            "image-generation prompt for Stable Diffusion / Flux. "
            "Use ONLY facts present in the JSON. Do not invent body parts, clothing, or setting. "
            "Tone: balanced editorial / fashion photography. Be vivid but professional."
        )
    return (
        f"{tone}{style_line} "
        "Output ONLY the prompt paragraph — no intro, no JSON, no labels.\n\n"
        f"JSON:\n{json.dumps(structured, ensure_ascii=False)}"
    )


def _ollama_generate(
    prompt: str,
    *,
    images: Optional[list] = None,
    model: str = MODEL_NAME,
    url: str = OLLAMA_URL,
    temperature: float = 0.25,
    num_predict: int = 450,
) -> Optional[str]:
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            "top_p": 0.88,
            "repeat_penalty": 1.12,
        },
    }
    if images:
        payload["images"] = images
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return (res_data.get("response") or "").strip()
    except Exception as e:
        print(f"Ollama generate error ({model}): {e}")
        return None


def _parse_structured_json(raw: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    # Merge legacy keys into body
    if not data.get("body"):
        parts = []
        for legacy in ("breasts", "waist_hips", "body"):
            val = data.get(legacy)
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
        if parts:
            data["body"] = "; ".join(parts)
    out = {}
    for key in STRUCTURED_FIELDS:
        val = data.get(key, "")
        out[key] = val.strip() if isinstance(val, str) else str(val or "")
    out["visible_only"] = True
    return out


def extract_structured_vision(image_path: str) -> Optional[Dict[str, Any]]:
    b64 = encode_image_to_base64(image_path)
    if not b64:
        return None
    raw = _ollama_generate(
        build_structured_vision_prompt(),
        images=[b64],
        model=MODEL_NAME,
        url=OLLAMA_URL,
        temperature=0.15,
        num_predict=500,
    )
    parsed = _parse_structured_json(raw or "")
    if parsed:
        return parsed
    legacy = _ollama_generate(
        build_vision_prompt(),
        images=[b64],
        model=MODEL_NAME,
        url=OLLAMA_URL,
    )
    if legacy:
        cleaned = clean_vision_output(legacy)
        return {
            "face": "",
            "hair": "",
            "body": "",
            "clothing": "",
            "pose": cleaned,
            "expression": "",
            "lighting": "",
            "background": "",
            "visible_only": True,
            "_fallback_paragraph": cleaned,
        }
    return None


def rewrite_prompt(structured: Dict[str, Any], style_prefix: str = "") -> Optional[str]:
    if structured.get("_fallback_paragraph") and not any(
        structured.get(k) for k in STRUCTURED_FIELDS if k != "pose"
    ):
        return clean_vision_output(structured["_fallback_paragraph"])

    raw = _ollama_generate(
        build_rewrite_prompt(structured, style_prefix=style_prefix),
        images=None,
        model=REWRITE_MODEL_NAME,
        url=OLLAMA_TEXT_URL,
        temperature=0.35,
        num_predict=400,
    )
    if raw:
        return clean_vision_output(raw)

    parts = [structured.get(k, "") for k in STRUCTURED_FIELDS if structured.get(k)]
    if style_prefix:
        parts.insert(0, style_prefix)
    return clean_vision_output(", ".join(parts)) if parts else None


# Back-compat alias
rewrite_erotic_prompt = rewrite_prompt


def analyze_with_ollama_vision(image_path: str) -> Optional[str]:
    """Return a photorealistic prompt paragraph via two-stage pipeline."""
    structured = extract_structured_vision(image_path)
    if not structured:
        return None
    return rewrite_prompt(structured)


def get_image_aspect_ratio(image_path: str) -> str:
    try:
        img = cv2.imread(image_path)
        if img is not None:
            h, w = img.shape[:2]
            ratio = w / h
            if 0.95 < ratio < 1.05:
                return "1:1"
            if 0.75 < ratio < 0.85:
                return "4:5"
            if 0.55 < ratio < 0.65:
                return "9:16"
            if 1.4 < ratio < 1.6:
                return "3:2"
            return f"{w}:{h}"
    except Exception:
        pass
    return "4:5"


def build_export_variants(
    positive: str,
    negative: str,
    structured: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Target-specific prompt strings for Flux / SDXL / Pony / Comfy Mode E."""
    flux = positive
    sdxl = f"{positive}, <lora:none>"
    pony_rating = "rating_safe" if _intensity_rank() != "high" else "rating_explicit"
    pony = f"score_9, score_8_up, score_7_up, source_real, {pony_rating}, {positive}"
    base = {
        "flux": flux,
        "sdxl": sdxl.replace(", <lora:none>", ""),
        "pony": pony,
        "negative": negative,
    }
    return enrich_exports(
        base, positive=positive, negative=negative, structured=structured
    )


def generate_prompt_for_image(image_path: str, creator_name: str = "") -> Dict[str, Any]:
    style_prefix = _styles.get_style_prefix(creator_name) if creator_name else ""
    structured = extract_structured_vision(image_path)
    vision_description = (
        rewrite_prompt(structured, style_prefix=style_prefix) if structured else None
    )
    aspect_ratio = get_image_aspect_ratio(image_path)
    clean_creator = creator_name.replace("_", " ").replace(".", " ").strip()
    quality_prefix = (
        "masterpiece, best quality, photorealistic, 8k, raw photo, "
        "highly detailed skin texture, natural body proportions, cinematic lighting, 35mm, f/1.8, "
    )
    if style_prefix:
        quality_prefix = f"{quality_prefix}{style_prefix}, "

    rank = _intensity_rank()
    if vision_description:
        anatomy_tail = (
            "ultra detailed, sharp focus, natural skin pores, realistic anatomy"
            if rank != "high"
            else "ultra detailed, sharp focus, natural skin pores, realistic anatomy, flattering figure"
        )
        positive_prompt = f"{quality_prefix}{vision_description}, {anatomy_tail}"
        words = [w.strip(".,") for w in vision_description.lower().split() if len(w) > 3]
        visual_tags = list(dict.fromkeys(words[:12]))
    else:
        positive_prompt = (
            f"{quality_prefix}portrait of a person, natural expression, stylish outfit, "
            f"confident pose, {clean_creator} style, "
            f"ultra detailed, sharp focus, realistic anatomy"
        )
        visual_tags = [
            "portrait",
            "fashion",
            "photorealistic",
            "cinematic lighting",
            "8k",
        ]

    negative_prompt = (
        "deformed, bad anatomy, disfigured, poorly drawn face, mutated extra limbs, "
        "extra fingers, fused fingers, blurry, low quality, oversaturated, "
        "painting, cartoon, 3d render, illustration, anime, watermark, text, "
        "logo, username, plastic skin, doll-like, "
        "inhuman proportions, exaggerated beyond realism, cartoonish body"
    )

    structured_out = None
    if structured:
        structured_out = {k: structured.get(k, "") for k in STRUCTURED_FIELDS}

    exports = build_export_variants(
        positive_prompt,
        negative_prompt,
        structured=structured_out,
    )

    parameters = {
        "sampler": "DPM++ 2M Karras",
        "steps": 30,
        "cfg_scale": 7.0,
        "aspect_ratio": aspect_ratio,
        "suggested_model": "SDXL / Flux.1 Dev / Realistic Vision / Pony Diffusion",
        "vision_engine": ENGINE_ID,
        "pipeline_version": PROMPT_PIPELINE_VERSION,
        "prompt_intensity": PROMPT_INTENSITY,
        "erotic_intensity": PROMPT_INTENSITY,  # legacy key for UI/cache
        "realism_bias": REALISM_BIAS,
    }

    result = {
        "positive_prompt": positive_prompt,
        "negative_prompt": negative_prompt,
        "parameters": parameters,
        "visual_tags": visual_tags,
        "raw_vision_description": vision_description,
        "structured_vision": structured_out,
        "exports": exports,
    }

    if creator_name:
        _styles.maybe_update(creator_name)

    return result


def get_prompt_for_image(
    image_path: str,
    creator_name: str = "",
    force_refresh: bool = False,
    rel_path: str = "",
) -> Dict[str, Any]:
    if not rel_path:
        rel_path = os.path.basename(image_path)
    filename = os.path.basename(image_path)
    cached = _cache.get(rel_path, filename)
    if (
        not force_refresh
        and cached
        and cached.get("parameters", {}).get("vision_engine") == ENGINE_ID
        and cached.get("parameters", {}).get("pipeline_version") == PROMPT_PIPELINE_VERSION
    ):
        structured = cached.get("structured_vision")
        exports = cached.get("exports") or {}
        if "exports" not in cached or not exports.get("comfy_ref"):
            cached["exports"] = build_export_variants(
                cached.get("positive_prompt", ""),
                cached.get("negative_prompt", ""),
                structured=structured if isinstance(structured, dict) else None,
            )
            try:
                _cache.set(rel_path, cached, push_history=False)
            except Exception:
                pass
        return cached
    prompt_data = generate_prompt_for_image(image_path, creator_name)
    _cache.set(rel_path, prompt_data)
    return prompt_data
