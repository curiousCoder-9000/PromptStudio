"""Turn a photo plus a request into the parameters one ComfyUI run needs.

Extracted verbatim from `/api/comfy/generate`, where it was ninety inline lines
of prompt selection and default resolution — reachable only over HTTP and
covered by no test. A2 makes the same decisions once per batch item, and a
second copy would have drifted from the first within a release. So both paths
call this, and the assembly finally has tests of its own.

Nothing here talks to ComfyUI or rolls a seed: `resolve_seed` owns that, once,
at job start (see `client.resolve_seed` for why "once" is load-bearing).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from promptstudio.config import (
    COMFYUI_DEFAULT_CFG,
    COMFYUI_DEFAULT_DENOISE,
    COMFYUI_DEFAULT_STEPS,
)
from promptstudio.logging_setup import get_logger

log = get_logger(__name__)

# Variants that mean "use the img2img reference graph".
_PRO_VARIANTS = ("pro", "ref", "modeltoimage_pro")
# Variants that mean "no reference image".
_TXT2IMG_VARIANTS = ("txt2img", "sdxl", "flux", "pony")

DEFAULT_NEGATIVE = "deformed, bad anatomy, blurry"


class NoPromptError(ValueError):
    """This photo has not been analyzed, so there is nothing to generate from.

    The one-shot route turns this into a 400; A2 counts it as
    `skipped_no_prompt` and moves on. Chaining batch-analyze into batch-generate
    is deliberately out of scope (design_generation_loop.md §9).
    """

    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        super().__init__("No prompt available — generate one first")


@dataclass
class GenerationParams:
    """Everything `ComfyJobManager.start()` needs, resolved and validated."""

    rel_path: str
    positive: str
    negative: str
    workflow: str
    variant: str
    aspect: str
    steps: int
    cfg: float
    denoise: Optional[float]
    seed: Optional[int]
    checkpoint: Optional[str]
    mode_e: bool
    prompt_version: Optional[str]
    # Was there a real analyzed prompt (or a typed override) behind `positive`?
    #
    # Not derivable from `positive`: Mode E's fallback fills an empty prompt
    # with a generic styling string, which is right for img2img — the reference
    # image carries the subject — but means an unanalyzed photo comes back
    # looking analyzed. A2 skips on this flag; reading the text instead would
    # have run every unanalyzed photo in the archive through one string.
    has_prompt_source: bool = False
    # Provenance of the Mode E text, for the API response. None when Mode E
    # did not run.
    mode_meta: Optional[Dict[str, Any]] = None


def _infer_workflow(variant: str, explicit: str) -> str:
    if explicit:
        return explicit
    if variant in _TXT2IMG_VARIANTS:
        return "txt2img"
    # Unknown variants get the reference graph, which is what the product is
    # for; a typo should not silently drop the reference image.
    return "pro"


def resolve_generation_params(
    rel_path: str, overrides: Optional[Dict[str, Any]] = None
) -> GenerationParams:
    """Assemble one run's parameters. Raises `NoPromptError` if there is no text.

    `overrides` is the request body: any key it sets wins over the cache and
    over the configured defaults.
    """
    import os

    from promptstudio.prompts.cache import PromptCache

    data = overrides or {}
    filename = os.path.basename(rel_path)
    cached = PromptCache().get(rel_path, filename) or {}
    exports = cached.get("exports") or {}
    params = cached.get("parameters") or {}

    variant = str(data.get("variant") or "pro").lower()
    workflow = _infer_workflow(variant, str(data.get("workflow") or "").lower())

    positive = data.get("positive_prompt")
    negative = data.get("negative_prompt")

    has_prompt_source = bool(
        str(positive or "").strip()
        or str(cached.get("positive_prompt") or "").strip()
        or any(str(v or "").strip() for v in exports.values())
    )

    use_mode_e = data.get("use_mode_e")
    if use_mode_e is None:
        use_mode_e = workflow == "pro"
    # Mode E is a property of the reference graph. Claiming it on a txt2img run
    # would write a wrong `mode_e` into the generations row, and that column is
    # one of the cuts keep_rate is sliced by.
    use_mode_e = bool(use_mode_e) and workflow == "pro"

    mode_meta: Optional[Dict[str, Any]] = None
    if use_mode_e:
        from promptstudio.prompts.comfy_mode import build_mode_e_bundle

        structured = cached.get("structured_vision")
        if not isinstance(structured, dict):
            structured = None
        base_pos = positive or cached.get("positive_prompt") or ""
        base_neg = negative or cached.get("negative_prompt") or ""
        if positive is None and exports.get("comfy_ref"):
            # A previously exported Mode E prompt is reused as-is — but only
            # when the caller did not type their own, or the override would be
            # silently discarded.
            positive = exports["comfy_ref"]
            negative = (
                exports.get("comfy_negative") or exports.get("negative") or base_neg
            )
            mode_meta = {"source": "exports", "anti_terms": []}
        else:
            bundle = build_mode_e_bundle(
                positive=str(base_pos),
                negative=str(base_neg),
                structured=structured,
            )
            positive = bundle["positive"]
            negative = bundle["negative"]
            mode_meta = {
                "source": bundle["source"],
                "anti_terms": bundle["anti_terms"],
            }
    else:
        if not positive:
            if variant == "flux":
                positive = exports.get("flux") or cached.get("positive_prompt", "")
            elif variant == "pony":
                positive = exports.get("pony") or cached.get("positive_prompt", "")
            else:
                positive = exports.get("sdxl") or cached.get("positive_prompt", "")
        if not negative:
            negative = (
                exports.get("negative")
                or cached.get("negative_prompt")
                or DEFAULT_NEGATIVE
            )

    if not str(positive).strip():
        raise NoPromptError(rel_path)

    if workflow == "pro":
        steps = int(
            data.get("steps") if data.get("steps") is not None else COMFYUI_DEFAULT_STEPS
        )
        cfg = float(
            data.get("cfg_scale")
            if data.get("cfg_scale") is not None
            else COMFYUI_DEFAULT_CFG
        )
        denoise: Optional[float] = float(
            data.get("denoise")
            if data.get("denoise") is not None
            else COMFYUI_DEFAULT_DENOISE
        )
    else:
        steps = int(data.get("steps") or params.get("steps") or 30)
        cfg = float(data.get("cfg_scale") or params.get("cfg_scale") or 7.0)
        denoise = None

    seed = data.get("seed")

    return GenerationParams(
        rel_path=rel_path,
        positive=str(positive),
        negative=str(negative),
        workflow=workflow,
        variant=variant,
        aspect=str(data.get("aspect_ratio") or params.get("aspect_ratio") or "4:5"),
        steps=steps,
        cfg=cfg,
        denoise=denoise,
        seed=int(seed) if seed is not None else None,
        checkpoint=data.get("checkpoint") or None,
        mode_e=use_mode_e,
        prompt_version=params.get("vision_engine"),
        has_prompt_source=has_prompt_source,
        mode_meta=mode_meta,
    )
