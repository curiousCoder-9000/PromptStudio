"""Turning a photo into a set of ComfyUI parameters.

This logic used to live inline in `/api/comfy/generate` — 90 lines of prompt
selection, Mode E assembly and default resolution reachable only over HTTP and
covered by nothing. A2 needs exactly the same decisions per batch item, and
copying them would have guaranteed the two paths drifted, so it moved into
`comfy/params.py` first (review_backend_architecture.md S7, done incrementally
inside a feature change rather than as a standalone restructure).

These tests pin what the route already did. Any difference here is a
regression, not a new opinion.
"""

import pytest

from promptstudio.comfy.params import NoPromptError, resolve_generation_params
from promptstudio.prompts.cache import PromptCache


def _cache(rel, **entry):
    PromptCache().set(rel, entry, push_history=False)


# ── workflow inference ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "variant,expected",
    [
        ("pro", "pro"),
        ("ref", "pro"),
        ("modeltoimage_pro", "pro"),
        ("txt2img", "txt2img"),
        ("sdxl", "txt2img"),
        ("flux", "txt2img"),
        ("pony", "txt2img"),
        ("something_else", "pro"),
    ],
)
def test_workflow_is_inferred_from_variant(make_photo, variant, expected):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="a woman")
    params = resolve_generation_params(rel, {"variant": variant})
    assert params.workflow == expected


def test_an_explicit_workflow_wins_over_the_variant(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="a woman")
    params = resolve_generation_params(rel, {"variant": "flux", "workflow": "pro"})
    assert params.workflow == "pro"


# ── prompt selection ─────────────────────────────────────────────────


def test_an_explicit_positive_prompt_is_used_verbatim(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="cached")
    params = resolve_generation_params(
        rel, {"workflow": "txt2img", "positive_prompt": "explicit"}
    )
    assert params.positive == "explicit"


def test_variant_picks_its_own_export(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(
        rel,
        positive_prompt="base",
        exports={"flux": "flux text", "pony": "pony text", "sdxl": "sdxl text"},
    )
    assert resolve_generation_params(rel, {"variant": "flux"}).positive == "flux text"
    assert resolve_generation_params(rel, {"variant": "pony"}).positive == "pony text"
    assert resolve_generation_params(rel, {"variant": "sdxl"}).positive == "sdxl text"


def test_export_falls_back_to_the_cached_positive(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="base", exports={})
    assert resolve_generation_params(rel, {"variant": "sdxl"}).positive == "base"


def test_the_negative_has_a_last_resort_default(make_photo):
    """An empty negative on an SDXL graph produces mush; the route always had one."""
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="base")
    params = resolve_generation_params(rel, {"variant": "sdxl"})
    assert params.negative == "deformed, bad anatomy, blurry"


def test_a_photo_with_no_prompt_is_refused(make_photo):
    """The route answered 400 "generate one first"; A2 counts it as skipped."""
    rel, _ = make_photo(creator="nina", name="a.jpg")
    with pytest.raises(NoPromptError):
        resolve_generation_params(rel, {"variant": "sdxl"})


def test_mode_e_synthesises_a_prompt_for_an_unanalyzed_photo(make_photo):
    """Not a bug on the pro path — img2img gets its subject from the reference
    image, so a generic styling prompt still produces something sensible. It
    does mean "was this photo analyzed" cannot be read back off the text."""
    rel, _ = make_photo(creator="nina", name="a.jpg")
    params = resolve_generation_params(rel, {"variant": "pro"})
    assert params.positive.strip()
    assert params.mode_meta["source"] == "fallback"


def test_an_unanalyzed_photo_says_so_even_when_mode_e_filled_the_text(make_photo):
    """The flag A2 skips on. Reading it off `positive` would have batched every
    unanalyzed photo in the archive through the same fallback string."""
    rel, _ = make_photo(creator="nina", name="a.jpg")
    assert resolve_generation_params(rel, {"variant": "pro"}).has_prompt_source is False


def test_an_analyzed_photo_has_a_prompt_source(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="a woman on a beach")
    assert resolve_generation_params(rel, {"variant": "pro"}).has_prompt_source is True


def test_a_typed_prompt_counts_as_a_prompt_source(make_photo):
    """An explicit override is the user saying what they want; refusing it
    because the cache is empty would be pedantry."""
    rel, _ = make_photo(creator="nina", name="a.jpg")
    params = resolve_generation_params(
        rel, {"variant": "pro", "positive_prompt": "typed by hand"}
    )
    assert params.has_prompt_source is True


def test_a_whitespace_only_prompt_counts_as_no_prompt(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="   ")
    with pytest.raises(NoPromptError):
        resolve_generation_params(rel, {"variant": "sdxl"})


# ── Mode E ───────────────────────────────────────────────────────────


def test_mode_e_is_on_by_default_for_pro_and_off_for_txt2img(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="base")
    assert resolve_generation_params(rel, {"variant": "pro"}).mode_e is True
    assert resolve_generation_params(rel, {"variant": "txt2img"}).mode_e is False


def test_mode_e_can_be_turned_off_explicitly(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="base")
    params = resolve_generation_params(rel, {"variant": "pro", "use_mode_e": False})
    assert params.mode_e is False


def test_mode_e_prefers_the_cached_comfy_ref_export(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(
        rel,
        positive_prompt="base",
        negative_prompt="base neg",
        exports={"comfy_ref": "ref text", "comfy_negative": "ref neg"},
    )
    params = resolve_generation_params(rel, {"variant": "pro"})
    assert params.positive == "ref text"
    assert params.negative == "ref neg"
    assert params.mode_meta == {"source": "exports", "anti_terms": []}


def test_an_explicit_positive_bypasses_the_cached_export(make_photo):
    """Overriding the text in the lightbox must not be silently ignored."""
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="base", exports={"comfy_ref": "ref text"})
    params = resolve_generation_params(
        rel, {"variant": "pro", "positive_prompt": "typed by hand"}
    )
    assert "typed by hand" in params.positive
    assert params.mode_meta["source"] != "exports"


def test_mode_e_builds_a_bundle_when_there_is_no_export(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="a woman on a beach", negative_prompt="blurry")
    params = resolve_generation_params(rel, {"variant": "pro"})
    assert params.positive
    assert params.mode_meta and "source" in params.mode_meta


def test_mode_e_is_never_claimed_for_a_txt2img_run(make_photo):
    """`mode_e` lands in the generations row and slices keep_rate — a wrong
    value there silently poisons the only metric that answers "did it help"."""
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="base")
    params = resolve_generation_params(
        rel, {"variant": "txt2img", "use_mode_e": True}
    )
    assert params.mode_e is False


# ── numeric defaults ─────────────────────────────────────────────────


def test_pro_uses_the_configured_defaults(make_photo):
    from promptstudio.config import (
        COMFYUI_DEFAULT_CFG,
        COMFYUI_DEFAULT_DENOISE,
        COMFYUI_DEFAULT_STEPS,
    )

    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="base")
    params = resolve_generation_params(rel, {"variant": "pro"})
    assert params.steps == COMFYUI_DEFAULT_STEPS
    assert params.cfg == COMFYUI_DEFAULT_CFG
    assert params.denoise == COMFYUI_DEFAULT_DENOISE


def test_txt2img_has_no_denoise(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="base")
    params = resolve_generation_params(rel, {"variant": "txt2img"})
    assert params.denoise is None
    assert params.steps == 30
    assert params.cfg == 7.0


def test_overrides_beat_defaults(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="base")
    params = resolve_generation_params(
        rel, {"variant": "pro", "steps": 12, "cfg_scale": 3.5, "denoise": 0.25}
    )
    assert (params.steps, params.cfg, params.denoise) == (12, 3.5, 0.25)


def test_aspect_comes_from_the_cached_parameters_when_not_given(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="base", parameters={"aspect_ratio": "16:9"})
    assert resolve_generation_params(rel, {"variant": "txt2img"}).aspect == "16:9"


def test_aspect_defaults_to_four_by_five(make_photo):
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="base")
    assert resolve_generation_params(rel, {"variant": "txt2img"}).aspect == "4:5"


def test_prompt_version_is_carried_from_the_cache(make_photo):
    """It slices keep_rate by prompt engine; losing it loses the comparison."""
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="base", parameters={"vision_engine": "v2-structured"})
    params = resolve_generation_params(rel, {"variant": "pro"})
    assert params.prompt_version == "v2-structured"


def test_seed_is_left_unresolved_here(make_photo):
    """`resolve_seed` owns that, once, at job start — not two places."""
    rel, _ = make_photo(creator="nina", name="a.jpg")
    _cache(rel, positive_prompt="base")
    assert resolve_generation_params(rel, {"variant": "pro"}).seed is None
    assert resolve_generation_params(rel, {"variant": "pro", "seed": 9}).seed == 9
