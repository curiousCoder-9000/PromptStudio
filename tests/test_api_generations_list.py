"""A1 — `GET /api/generations/list` and `DELETE /api/generation`.

The list mirrors `/api/photos`: same `offset` / `limit` / `has_more` / `total`
shape, so the paging the gallery already implements works unchanged.

Delete is the interesting one. Archive media is unrecoverable, so
`DELETE /api/photo` soft-deletes into `_trash/`. A generation with a recorded
seed, prompt and checkpoint is reproducible by construction, so it is deleted
outright — and that asymmetry is asserted here rather than left to be
rediscovered.
"""

from __future__ import annotations

import os

from promptstudio.config import GENERATIONS_DIR, SAVED_DIR
from promptstudio.storage.db import ArchiveIndex

LIST = "/api/generations/list"


def _make_output(index, name="a.png", *, creator="nina", **over):
    """A real PNG under _generations/ plus its row, the way a job leaves it."""
    from PIL import Image

    folder = os.path.join(GENERATIONS_DIR, creator)
    os.makedirs(folder, exist_ok=True)
    full = os.path.join(folder, name)
    Image.new("RGB", (16, 16), (80, 20, 120)).save(full, "PNG")
    rel = f"_generations/{creator}/{name}"
    kwargs = dict(
        rel_path=rel,
        source_rel=f"{creator}/photo.jpg",
        creator=creator,
        workflow="pro",
        seed=4242,
        positive_prompt="a portrait",
    )
    kwargs.update(over)
    return index.record_generation(**kwargs), rel, full


def test_list_returns_the_photos_paging_shape(api):
    index = ArchiveIndex.get()
    for i in range(5):
        _make_output(index, f"g{i}.png")

    status, payload = api("GET", f"{LIST}?limit=2&offset=0")

    assert status == 200
    assert len(payload["generations"]) == 2
    assert payload["total"] == 5
    assert payload["offset"] == 0
    assert payload["limit"] == 2
    assert payload["has_more"] is True


def test_the_last_page_reports_no_more(api):
    index = ArchiveIndex.get()
    for i in range(3):
        _make_output(index, f"g{i}.png")

    _status, payload = api("GET", f"{LIST}?limit=2&offset=2")

    assert payload["has_more"] is False


def test_filters_reach_the_query(api):
    index = ArchiveIndex.get()
    _make_output(index, "pro.png", workflow="pro")
    _make_output(index, "txt.png", workflow="txt2img")

    _status, payload = api("GET", f"{LIST}?workflow=txt2img")

    assert payload["total"] == 1
    assert payload["generations"][0]["workflow"] == "txt2img"


def test_has_source_and_until_reach_the_query(api):
    index = ArchiveIndex.get()
    _make_output(
        index, "ref.png", creator="nina",
        created_at="2026-08-10T12:00:00+00:00",
    )
    index.record_generation(
        rel_path="_generations/nina/orphan.png",
        source_rel="",
        creator="nina",
        workflow="txt2img",
        seed=1,
        positive_prompt="no ref",
        created_at="2026-08-20T12:00:00+00:00",
    )

    _s, no_src = api("GET", f"{LIST}?has_source=0")
    _s2, with_src = api("GET", f"{LIST}?has_source=1")
    _s3, until = api("GET", f"{LIST}?until=2026-08-15")

    assert no_src["total"] == 1
    assert no_src["generations"][0]["has_source"] is False
    assert with_src["total"] == 1
    assert with_src["generations"][0]["has_source"] is True
    assert with_src["generations"][0]["source_thumb_url"]
    assert until["total"] == 1
    assert until["generations"][0]["has_source"] is True


def test_photos_path_returns_exactly_that_row(api, make_photo):
    rel, _full = make_photo(creator="nina", name="keep.jpg")
    make_photo(creator="nina", name="other.jpg")

    status, payload = api("GET", f"/api/photos?path={rel}")

    assert status == 200
    assert payload["total"] == 1
    assert payload["photos"][0]["rel_path"] == rel.replace("\\", "/")


def test_a_hostile_sort_does_not_error_or_execute(api):
    index = ArchiveIndex.get()
    _make_output(index, "a.png")

    status, payload = api("GET", f"{LIST}?sort=%27%3B+DROP+TABLE+generations%3B+--")

    assert status == 200
    assert payload["total"] == 1
    assert ArchiveIndex.get().list_generations()[1] == 1


def test_each_row_carries_a_thumb_url_the_grid_can_render(api):
    index = ArchiveIndex.get()
    _make_output(index, "a.png")

    _status, payload = api("GET", LIST)

    row = payload["generations"][0]
    assert row["url"].startswith("/media/_generations/")
    assert row["thumb_url"]
    assert row["gen_id"]


def test_facets_list_what_is_actually_present(api):
    index = ArchiveIndex.get()
    _make_output(index, "a.png", workflow="pro", checkpoint="ckpt_a")
    _make_output(index, "b.png", workflow="txt2img", checkpoint="ckpt_b")

    _status, payload = api("GET", LIST)

    assert payload["facets"]["workflows"] == ["pro", "txt2img"]
    assert payload["facets"]["checkpoints"] == ["ckpt_a", "ckpt_b"]


# ── delete ───────────────────────────────────────────────────────────


def test_delete_removes_the_row_and_the_file(api):
    index = ArchiveIndex.get()
    gen_id, _rel, full = _make_output(index, "doomed.png")
    assert os.path.isfile(full)

    status, _payload = api("DELETE", f"/api/generation?gen_id={gen_id}")

    assert status == 200
    assert not os.path.isfile(full)
    assert index.list_generations()[1] == 0


def test_delete_is_permanent_and_does_not_use_the_trash(api):
    """The documented asymmetry with DELETE /api/photo. A generation is
    reproducible from its own row, so a restore path would be dead weight."""
    from promptstudio.storage.trash import TrashStore

    index = ArchiveIndex.get()
    gen_id, _rel, _full = _make_output(index, "doomed.png")

    api("DELETE", f"/api/generation?gen_id={gen_id}")

    entries, count = TrashStore().list_entries()
    assert entries == []
    assert count == 0


def test_deleting_a_generation_leaves_the_source_photo_alone(api, make_photo):
    index = ArchiveIndex.get()
    src_rel, src_full = make_photo(creator="nina", name="photo.jpg")
    gen_id, _rel, _full = _make_output(index, "doomed.png")

    api("DELETE", f"/api/generation?gen_id={gen_id}")

    assert os.path.isfile(src_full), "source photo was deleted with its output"


def test_deleting_an_unknown_generation_is_a_404(api):
    status, _ = api("DELETE", "/api/generation?gen_id=no-such-id")

    assert status == 404


def test_delete_without_a_gen_id_is_a_400(api):
    status, _ = api("DELETE", "/api/generation")

    assert status == 400


def test_a_row_pointing_outside_the_archive_does_not_unlink_anything(api):
    """The row is ours, but it is data — a hand-edited or migrated one must not
    become an arbitrary file delete."""
    index = ArchiveIndex.get()
    outside = SAVED_DIR + "_backup"
    os.makedirs(outside, exist_ok=True)
    victim = os.path.join(outside, "precious.png")
    with open(victim, "w", encoding="utf-8") as f:
        f.write("PRECIOUS")
    gen_id = index.record_generation(
        rel_path="../" + os.path.basename(outside) + "/precious.png",
        source_rel="nina/photo.jpg",
        creator="nina",
        workflow="pro",
        seed=1,
        positive_prompt="p",
    )
    try:
        api("DELETE", f"/api/generation?gen_id={gen_id}")
        assert os.path.isfile(victim), "escaped the archive and deleted a file"
    finally:
        os.remove(victim)
        os.rmdir(outside)
