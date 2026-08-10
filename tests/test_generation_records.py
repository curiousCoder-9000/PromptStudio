"""A0 — what `_save_outputs` records, end to end through a mocked ComfyUI.

`tests/test_generations_store.py` covers the table in isolation. This file
covers the write path that fills it, which is where the truncation and the
20-per-source cap actually lived.
"""

from __future__ import annotations

from promptstudio.comfy.client import ComfyJobManager
from promptstudio.storage.db import ArchiveIndex


def _save_one(manager, source_rel, *, index, seed=4242, positive="a portrait"):
    """One output through the real save path, with a mocked download."""
    return manager._save_outputs(
        source_rel,
        [{"filename": f"out_{index}.png", "subfolder": "", "type": "output"}],
        positive,
        "blurry",
        extra={"workflow": "pro", "seed": seed, "steps": 30, "cfg": 7.0},
    )


def test_two_generations_in_the_same_second_do_not_overwrite_each_other(
    make_photo, fake_comfy
):
    """The output filename stamp was second-resolution, so a second generation
    of the same photo within one second wrote over the first — on disk, before
    any index was involved."""
    rel, _ = make_photo(creator="gentest", name="collide.jpg")
    manager = ComfyJobManager()

    first = _save_one(manager, rel, index=1)
    second = _save_one(manager, rel, index=2)

    assert first["primary_rel"] != second["primary_rel"]
    assert len(ArchiveIndex.get().list_generations_for(rel)) == 2


def test_more_than_twenty_generations_for_one_source_all_survive(
    make_photo, fake_comfy
):
    """§2.2: `items[:20]` was data loss that only becomes visible once
    something renders the history."""
    rel, _ = make_photo(creator="gentest", name="many.jpg")
    manager = ComfyJobManager()

    for i in range(22):
        _save_one(manager, rel, index=i)

    assert len(ArchiveIndex.get().list_generations_for(rel)) == 22


def test_the_recorded_prompt_is_not_truncated(make_photo, fake_comfy):
    """`positive[:500]` / `negative[:300]` — enough to display, not enough to
    reproduce."""
    rel, _ = make_photo(creator="gentest", name="long.jpg")
    long_positive = "cinematic portrait, golden hour, 85mm, " * 40
    assert len(long_positive) > 500
    manager = ComfyJobManager()

    _save_one(manager, rel, index=1, positive=long_positive)

    row = ArchiveIndex.get().list_generations_for(rel)[0]
    assert row["positive_prompt"] == long_positive


def test_checkpoint_and_mode_e_are_persisted(make_photo, fake_comfy):
    """Both existed only in the handler's `mode_meta` and were never written,
    so "is Mode E worth it" was unanswerable (§3.3)."""
    rel, _ = make_photo(creator="gentest", name="meta.jpg")
    manager = ComfyJobManager()

    manager._save_outputs(
        rel,
        [{"filename": "out.png", "subfolder": "", "type": "output"}],
        "a portrait",
        "blurry",
        extra={
            "workflow": "pro",
            "seed": 4242,
            "checkpoint": "realvis_v5.safetensors",
            "mode_e": True,
            "prompt_version": "v2-structured",
        },
    )

    row = ArchiveIndex.get().list_generations_for(rel)[0]
    assert row["checkpoint"] == "realvis_v5.safetensors"
    assert row["mode_e"] == 1
    assert row["prompt_version"] == "v2-structured"


def test_a_full_job_records_the_row_with_the_seed_it_rendered_with(
    make_photo, fake_comfy, run_comfy_job
):
    """The end-to-end gate from §4: a mocked generate, read back from the
    table, reproduces the seed that reached the graph."""
    from promptstudio.comfy.client import PRO_NODE_SAMPLER

    rel, _ = make_photo(creator="gentest", name="e2e.jpg")

    status = run_comfy_job(
        source_rel=rel,
        positive="a photo",
        negative="blurry",
        workflow="pro",
        seed=None,
    )

    assert status["error"] is None, status["error"]
    rows = ArchiveIndex.get().list_generations_for(rel)
    assert len(rows) == 1
    assert rows[0]["seed"] == fake_comfy["graph"][PRO_NODE_SAMPLER]["inputs"]["seed"]
    assert rows[0]["workflow"] == "pro"
    assert rows[0]["creator"] == "gentest"


def test_a_pro_job_records_the_checkpoint_it_rendered_with(
    make_photo, fake_comfy, run_comfy_job
):
    """`checkpoint` reached `start()` and the graph but was never put in the
    record, so "which model produced this" was unanswerable."""
    rel, _ = make_photo(creator="gentest", name="ckpt.jpg")

    run_comfy_job(
        source_rel=rel,
        positive="a photo",
        negative="blurry",
        workflow="pro",
        seed=4242,
        checkpoint="realvis_v5.safetensors",
    )

    row = ArchiveIndex.get().list_generations_for(rel)[0]
    assert row["checkpoint"] == "realvis_v5.safetensors"


def test_mode_e_and_prompt_version_reach_the_row(
    make_photo, fake_comfy, run_comfy_job
):
    """Both are known at the call site and neither was persisted, which is why
    "is Mode E worth it" cannot be answered today (§3.3)."""
    rel, _ = make_photo(creator="gentest", name="modee.jpg")

    run_comfy_job(
        source_rel=rel,
        positive="a photo",
        negative="blurry",
        workflow="pro",
        seed=4242,
        mode_e=True,
        prompt_version="v2-structured",
    )

    row = ArchiveIndex.get().list_generations_for(rel)[0]
    assert row["mode_e"] == 1
    assert row["prompt_version"] == "v2-structured"
