"""B1 quality insights — free metrics from existing signals."""

from promptstudio.insights import compute_insights
from promptstudio.prompts.cache import PromptCache
from promptstudio.prompts.engine import ENGINE_ID


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
    assert "generations" not in data
    assert "facets" not in data


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


# ── B4 saturation, reported alongside the metric it invalidates ──────
#
# `top_tier_share` and `keep_rate` were already computed here. What was
# missing is the *verdict*: a number on a dashboard is advisory, and the
# person who needs to see a warning banner is the person who stopped opening
# the panel. Both metrics now arrive with the guard's own answer attached, so
# `/api/insights`, the pass-rate badges and the pytest gate cannot disagree.


def test_classify_insights_carry_the_saturation_verdict(store, make_photo, monkeypatch):
    from promptstudio import config
    from promptstudio.storage.db import ArchiveIndex

    monkeypatch.setattr(config, "DISTRIBUTION_MIN_CLASSIFIED", 5)
    index = ArchiveIndex.get()
    for i in range(9):
        rel, _ = make_photo(name=f"m{i}.jpg")
        index.set_verdict(rel, creator="test_creator", tier=1 if i else 0)

    guard = compute_insights()["classify"]["saturation"]
    assert guard["measured"] is True
    assert guard["saturated"] is True
    assert guard["top_bucket"] == "tier 1"
    assert "tier 1" in guard["message"]


def test_classify_saturation_is_not_judged_on_a_thin_archive(store, make_photo):
    """The default minimum is 100; three photos must not read as healthy."""
    from promptstudio.storage.db import ArchiveIndex

    index = ArchiveIndex.get()
    for i in range(3):
        rel, _ = make_photo(name=f"m{i}.jpg")
        index.set_verdict(rel, creator="test_creator", tier=1)

    guard = compute_insights()["classify"]["saturation"]
    assert guard["measured"] is False
    assert guard["saturated"] is False


def test_classify_insights_report_gold_labels_without_moving_the_histogram(
    store, make_photo, monkeypatch
):
    """A T1→T2 correction must not make the classifier look healthier."""
    from promptstudio import config
    from promptstudio.storage.db import ArchiveIndex

    monkeypatch.setattr(config, "DISTRIBUTION_MIN_CLASSIFIED", 5)
    index = ArchiveIndex.get()
    rels = []
    for i in range(8):
        rel, _ = make_photo(name=f"m{i}.jpg")
        index.set_verdict(rel, creator="test_creator", tier=1)
        rels.append(rel)
    index.set_corrected_tier(rels[0], 2)

    c = compute_insights()["classify"]
    assert c["distribution"] == {"1": 8}
    assert c["corrections"] == 1
    assert c["disagreements"] == 1
    assert c["saturation"]["top_bucket"] == "tier 1"


def test_classify_saturation_ignores_failed_vision_calls(store, make_photo, monkeypatch):
    """tier -1 is a retry, not a judgement — it must not dilute the share."""
    from promptstudio import config
    from promptstudio.storage.db import ArchiveIndex

    monkeypatch.setattr(config, "DISTRIBUTION_MIN_CLASSIFIED", 4)
    index = ArchiveIndex.get()
    for i in range(4):
        rel, _ = make_photo(name=f"ok{i}.jpg")
        index.set_verdict(rel, creator="test_creator", tier=1)
    for i in range(6):
        rel, _ = make_photo(name=f"bad{i}.jpg")
        index.set_verdict(rel, creator="test_creator", tier=-1, error="timeout")

    guard = compute_insights()["classify"]["saturation"]
    assert guard["n"] == 4
    assert guard["saturated"] is True
