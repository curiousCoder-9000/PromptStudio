"""B1 quality insights — free metrics from existing signals."""

from promptstudio.comfy.client import GenerationsIndex
from promptstudio.config import GENERATIONS_INDEX_FILE
from promptstudio.insights import compute_insights
from promptstudio.prompts.cache import PromptCache
from promptstudio.prompts.engine import ENGINE_ID
from promptstudio.storage.db import ArchiveIndex


def _prompt(manual=False, history=None, pipeline="v2-structured"):
    return {
        "positive_prompt": "a photo of a woman",
        "negative_prompt": "blurry",
        "visual_tags": ["a"],
        "parameters": {
            "vision_engine": ENGINE_ID,
            "pipeline_version": pipeline,
            "manual_edit": manual,
        },
        "history": history or [],
    }


def test_empty_archive_insights_are_zero(store):
    data = compute_insights()
    assert data["prompts"]["total"] == 0
    assert data["prompts"]["edit_rate"] is None
    assert data["generations"]["total_outputs"] == 0


def test_prompt_edit_and_regenerate_rates(store, make_photo):
    cache = PromptCache()
    rel_a, _ = make_photo(name="a.jpg")
    rel_b, _ = make_photo(name="b.jpg")
    rel_c, _ = make_photo(name="c.jpg")
    # a: accepted as-is
    cache.set(rel_a, _prompt(manual=False), push_history=False)
    # b: user edited
    cache.set(rel_b, _prompt(manual=True), push_history=False)
    # c: regenerated (history snapshot exists)
    cache.set(
        rel_c,
        _prompt(manual=False, history=[{"positive_prompt": "old"}]),
        push_history=False,
    )

    p = compute_insights()["prompts"]
    assert p["total"] == 3
    assert p["manual_edits"] == 1
    assert p["edit_rate"] == round(1 / 3, 4)
    assert p["with_history"] == 1
    assert p["regenerate_rate"] == round(1 / 3, 4)
    assert p["by_pipeline_version"]["v2-structured"] == 3



def test_generation_counts(store, make_photo):
    rel_a, _ = make_photo(name="src_a.jpg")
    rel_b, _ = make_photo(name="src_b.jpg")
    idx = GenerationsIndex(path=GENERATIONS_INDEX_FILE)
    idx.add(rel_a, {"file": "out1.png", "seed": 1})
    idx.add(rel_a, {"file": "out2.png", "seed": 2})
    idx.add(rel_b, {"file": "out3.png", "seed": 3})

    g = compute_insights()["generations"]
    assert g["sources_with_gens"] == 2
    assert g["total_outputs"] == 3
    assert g["sources_with_multiple"] == 1
    assert g["avg_per_source"] == 1.5
