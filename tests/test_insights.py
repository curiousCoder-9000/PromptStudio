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
    assert data["generations"]["total_outputs"] == 0
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



def _gen(index, rel, out, **over):
    """Seed one generation row. A3 moved insights off the JSON index onto the
    `generations` table, so this seeds the table — the previous version of this
    helper wrote `generations_index.json`."""
    kwargs = dict(
        rel_path=f"_generations/gen/{out}",
        source_rel=rel,
        creator="gen",
        workflow="pro",
        seed=1,
        positive_prompt="a portrait",
    )
    kwargs.update(over)
    return index.record_generation(**kwargs)


def test_generation_counts(store, make_photo):
    from promptstudio.storage.db import ArchiveIndex

    rel_a, _ = make_photo(name="src_a.jpg")
    rel_b, _ = make_photo(name="src_b.jpg")
    index = ArchiveIndex.get()
    _gen(index, rel_a, "out1.png")
    _gen(index, rel_a, "out2.png")
    _gen(index, rel_b, "out3.png")

    g = compute_insights()["generations"]
    assert g["sources_with_gens"] == 2
    assert g["total_outputs"] == 3
    assert g["sources_with_multiple"] == 1
    assert g["avg_per_source"] == 1.5


def test_keep_rate_counts_only_what_was_actually_rated(store, make_photo):
    """`keep_rate = kept / rated`. Unrated outputs are not evidence either way,
    so dividing by the total would drift toward 0 as the archive grows."""
    from promptstudio.storage.db import ArchiveIndex

    rel, _ = make_photo(name="src.jpg")
    index = ArchiveIndex.get()
    keep = _gen(index, rel, "keep.png")
    star = _gen(index, rel, "star.png")
    junk = _gen(index, rel, "junk.png")
    _gen(index, rel, "unrated.png")
    index.rate_generation(keep, 1)
    index.rate_generation(star, 2)
    index.rate_generation(junk, -1)

    g = compute_insights()["generations"]
    assert g["total_outputs"] == 4
    assert g["rated"] == 3
    assert g["kept"] == 2
    assert g["discarded"] == 1
    assert g["starred"] == 1
    assert g["keep_rate"] == round(2 / 3, 4)


def test_keep_rate_is_none_before_anything_is_rated(store, make_photo):
    """Not 0.0 — "nothing rated" and "everything rejected" are different, and
    0.0 would render as a damning score for an untouched archive."""
    from promptstudio.storage.db import ArchiveIndex

    rel, _ = make_photo(name="src.jpg")
    _gen(ArchiveIndex.get(), rel, "out.png")

    assert compute_insights()["generations"]["keep_rate"] is None


def test_keep_rate_is_sliced_by_the_three_cuts_that_answer_something(
    store, make_photo
):
    """§3.3: per prompt_version, per workflow, per checkpoint — "did the
    v2-structured pipeline help" and "is this checkpoint worth keeping"."""
    from promptstudio.storage.db import ArchiveIndex

    rel, _ = make_photo(name="src.jpg")
    index = ArchiveIndex.get()
    good = _gen(index, rel, "v2.png", prompt_version="v2", workflow="pro",
                checkpoint="ckpt_a")
    bad = _gen(index, rel, "v1.png", prompt_version="v1", workflow="txt2img",
               checkpoint="ckpt_b")
    index.rate_generation(good, 1)
    index.rate_generation(bad, -1)

    g = compute_insights()["generations"]
    assert g["by_prompt_version"]["v2"]["keep_rate"] == 1.0
    assert g["by_prompt_version"]["v1"]["keep_rate"] == 0.0
    assert g["by_workflow"]["pro"]["keep_rate"] == 1.0
    assert g["by_checkpoint"]["ckpt_b"]["keep_rate"] == 0.0


def test_mode_e_is_its_own_cut(store, make_photo):
    """§3.3 names "is Mode E worth it" as a question the cuts answer, but none
    of the three named cuts splits on it — `mode_e` is its own column."""
    from promptstudio.storage.db import ArchiveIndex

    rel, _ = make_photo(name="src.jpg")
    index = ArchiveIndex.get()
    with_e = _gen(index, rel, "e.png", mode_e=True)
    without = _gen(index, rel, "plain.png", mode_e=False)
    index.rate_generation(with_e, 2)
    index.rate_generation(without, -1)

    g = compute_insights()["generations"]
    assert g["by_mode_e"]["on"]["keep_rate"] == 1.0
    assert g["by_mode_e"]["off"]["keep_rate"] == 0.0


def test_unreproducible_legacy_rows_are_counted(store, make_photo):
    """Success criterion #1 is "100% of new rows have a real seed" and nothing
    measured it. Legacy imports carry seed = -1."""
    from promptstudio.storage.db import ArchiveIndex

    rel, _ = make_photo(name="src.jpg")
    index = ArchiveIndex.get()
    _gen(index, rel, "legacy.png", seed=-1)
    _gen(index, rel, "modern.png", seed=12345)

    g = compute_insights()["generations"]
    assert g["unreproducible"] == 1


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
        index.set_verdict(rel, creator="test_creator", tier=3 if i else 0)

    guard = compute_insights()["classify"]["saturation"]
    assert guard["measured"] is True
    assert guard["saturated"] is True
    assert guard["top_bucket"] == "tier 3"
    assert "tier 3" in guard["message"]


def test_classify_saturation_is_not_judged_on_a_thin_archive(store, make_photo):
    """The default minimum is 100; three photos must not read as healthy."""
    from promptstudio.storage.db import ArchiveIndex

    index = ArchiveIndex.get()
    for i in range(3):
        rel, _ = make_photo(name=f"m{i}.jpg")
        index.set_verdict(rel, creator="test_creator", tier=3)

    guard = compute_insights()["classify"]["saturation"]
    assert guard["measured"] is False
    assert guard["saturated"] is False


def test_classify_saturation_ignores_failed_vision_calls(store, make_photo, monkeypatch):
    """tier -1 is a retry, not a judgement — it must not dilute the share."""
    from promptstudio import config
    from promptstudio.storage.db import ArchiveIndex

    monkeypatch.setattr(config, "DISTRIBUTION_MIN_CLASSIFIED", 4)
    index = ArchiveIndex.get()
    for i in range(4):
        rel, _ = make_photo(name=f"ok{i}.jpg")
        index.set_verdict(rel, creator="test_creator", tier=3)
    for i in range(6):
        rel, _ = make_photo(name=f"bad{i}.jpg")
        index.set_verdict(rel, creator="test_creator", tier=-1, error="timeout")

    guard = compute_insights()["classify"]["saturation"]
    assert guard["n"] == 4
    assert guard["saturated"] is True


def test_generation_saturation_is_measured_over_rated_outputs_only(
    store, make_photo, monkeypatch
):
    """Same denominator as `keep_rate`. Counting unrated outputs as a bucket
    would fire on every archive nobody has judged yet."""
    from promptstudio import config
    from promptstudio.storage.db import ArchiveIndex

    monkeypatch.setattr(config, "DISTRIBUTION_MIN_RATED", 4)
    rel, _ = make_photo(name="src.jpg")
    index = ArchiveIndex.get()
    for i in range(20):
        _gen(index, rel, f"unrated{i}.png")
    for i in range(5):
        index.rate_generation(_gen(index, rel, f"keep{i}.png"), 1)

    g = compute_insights()["generations"]
    assert g["rated"] == 5
    assert g["saturation"]["n"] == 5
    assert g["saturation"]["saturated"] is True
    assert g["saturation"]["top_bucket"] == "keep"


def test_generation_saturation_is_silent_while_nothing_is_rated(store, make_photo):
    from promptstudio.storage.db import ArchiveIndex

    rel, _ = make_photo(name="src.jpg")
    index = ArchiveIndex.get()
    for i in range(50):
        _gen(index, rel, f"out{i}.png")

    guard = compute_insights()["generations"]["saturation"]
    assert guard["measured"] is False
    assert guard["n"] == 0


def test_a_spread_of_ratings_is_not_saturated(store, make_photo, monkeypatch):
    from promptstudio import config
    from promptstudio.storage.db import ArchiveIndex

    monkeypatch.setattr(config, "DISTRIBUTION_MIN_RATED", 5)
    rel, _ = make_photo(name="src.jpg")
    index = ArchiveIndex.get()
    for i, rating in enumerate([-1, -1, 1, 1, 2, 2]):
        index.rate_generation(_gen(index, rel, f"out{i}.png"), rating)

    guard = compute_insights()["generations"]["saturation"]
    assert guard["measured"] is True
    assert guard["saturated"] is False
