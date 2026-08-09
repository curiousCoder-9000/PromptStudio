"""Indexing reads each sidecar once.

Four different fields of `*.meta.json` feed one photos row — taken_at, identity,
glam, source — and each used to load and parse the file independently. That was
4 opens per photo: 18 000 across a 4500-file archive, on every index build and
again on every per-photo upsert.

These are cost regression guards. They assert the *number of reads*, because the
behaviour they protect is invisible in the output.
"""

import json
import os

import pytest

from promptstudio.storage import db as db_mod
from promptstudio.storage import metadata as md
from promptstudio.storage.db import ArchiveIndex, read_sidecar


@pytest.fixture
def count_reads(monkeypatch):
    """Count load_post_metadata calls, wherever they are reached from."""
    calls = {"n": 0}
    real = md.load_post_metadata

    def counting(path):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(md, "load_post_metadata", counting)
    return calls


SIDECAR = {
    "post_id": "p1",
    "shortcode": "s1",
    "glam_score": 3,
    "source": "instagram",
    "taken_at": "2026-08-05T09:12:00",
}


def _photo_with_sidecar(make_photo, creator="nina", name="a.jpg", meta=None):
    rel, full = make_photo(creator=creator, name=name)
    with open(full + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta or SIDECAR, f)
    return rel, full


# ── read_sidecar ─────────────────────────────────────────────────────


def test_read_sidecar_returns_the_metadata(make_photo):
    _, full = _photo_with_sidecar(make_photo)
    assert read_sidecar(full)["post_id"] == "p1"


def test_read_sidecar_reuses_a_supplied_dict(make_photo, count_reads):
    _, full = _photo_with_sidecar(make_photo)
    supplied = {"post_id": "override"}

    count_reads["n"] = 0  # creating the photo indexes it, which reads once
    assert read_sidecar(full, supplied) is supplied
    assert count_reads["n"] == 0, "a supplied sidecar must not hit the disk"


def test_read_sidecar_on_missing_file_is_empty(tmp_path):
    assert read_sidecar(str(tmp_path / "nope.jpg")) == {}


def test_read_sidecar_tolerates_an_empty_path():
    assert read_sidecar("") == {}


def test_empty_sidecar_is_distinguishable_from_absent(make_photo, count_reads):
    """`{}` means "already loaded, nothing there" and must not trigger a read."""
    _, full = _photo_with_sidecar(make_photo)

    count_reads["n"] = 0
    assert read_sidecar(full, {}) == {}
    assert count_reads["n"] == 0


# ── the cost guards ──────────────────────────────────────────────────


def test_rebuild_reads_each_sidecar_once(make_photo, count_reads):
    for i in range(6):
        _photo_with_sidecar(make_photo, name=f"p{i}.jpg")

    count_reads["n"] = 0
    indexed = ArchiveIndex.get().rebuild()

    assert indexed == 6
    assert count_reads["n"] == 6, (
        f"expected one sidecar read per file, got {count_reads['n']} for 6 files"
    )


def test_upsert_reads_the_sidecar_once(make_photo, count_reads):
    rel, _ = _photo_with_sidecar(make_photo)

    count_reads["n"] = 0
    ArchiveIndex.get().update_prompt_flags(rel, None, "E")

    assert count_reads["n"] == 1


def test_upsert_skips_the_sidecar_when_every_field_is_supplied(make_photo, count_reads):
    rel, _ = _photo_with_sidecar(make_photo)

    count_reads["n"] = 0
    ArchiveIndex.get().upsert_photo(
        rel,
        taken_at="2026-01-01T00:00:00",
        post_id="x",
        shortcode="y",
        glam_score=2,
        source="instagram",
    )
    assert count_reads["n"] == 0


# ── values still land correctly ──────────────────────────────────────


def test_rebuild_still_reads_every_field_from_the_sidecar(make_photo):
    rel, _ = _photo_with_sidecar(make_photo, creator="nina", name="a.jpg")
    index = ArchiveIndex.get()
    index.rebuild()

    creator, post_id, shortcode = index.get_photo_identity(rel)
    assert (creator, post_id, shortcode) == ("nina", "p1", "s1")
    assert index.get_photo_source(rel) == "instagram"
    assert index.get_glam_score(rel) == 3

    rows, _ = index.query_photos(creator="nina")
    assert next(r for r in rows if r["rel_path"] == rel)["taken_at"] == SIDECAR["taken_at"]


def test_nested_glam_block_is_still_understood(make_photo):
    rel, _ = _photo_with_sidecar(
        make_photo, name="b.jpg", meta={"glam": {"score": 2}, "source": "x"}
    )
    index = ArchiveIndex.get()
    index.rebuild()
    assert index.get_glam_score(rel) == 2


def test_photo_without_a_sidecar_still_indexes(make_photo):
    rel, _ = make_photo(creator="nina", name="bare.jpg")
    index = ArchiveIndex.get()
    index.rebuild()
    rows, _ = index.query_photos(creator="nina")
    assert rel in [r["rel_path"] for r in rows]
    assert index.get_glam_score(rel) == -1


def test_corrupt_sidecar_does_not_break_indexing(make_photo):
    rel, full = make_photo(creator="nina", name="bad.jpg")
    with open(full + ".meta.json", "w", encoding="utf-8") as f:
        f.write("{ not json")

    index = ArchiveIndex.get()
    index.rebuild()
    rows, _ = index.query_photos(creator="nina")
    assert rel in [r["rel_path"] for r in rows]


def test_taken_at_falls_back_to_the_filename_when_the_sidecar_is_silent(make_photo):
    rel, full = make_photo(creator="nina", name="nina_2026-08-05_09-12-43_UTC.jpg")
    assert db_mod.taken_at_for_image(full, os.path.basename(rel), {}) == (
        "2026-08-05T09:12:43"
    )
