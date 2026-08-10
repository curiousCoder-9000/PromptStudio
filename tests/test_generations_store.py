"""A0 storage — the `generations` table.

Covers the three defects design_generation_loop.md §2.2 records in the JSON
index: prompts truncated to 500/300 chars, a silent 20-per-source cap, and no
column for a rating. Each test names the behaviour, not the schema.
"""

from __future__ import annotations

import os

import pytest

from promptstudio.config import GENERATIONS_INDEX_FILE
from promptstudio.storage.atomic import atomic_write_json
from promptstudio.storage.db import ArchiveIndex


def _write_legacy_index(data: dict) -> None:
    atomic_write_json(GENERATIONS_INDEX_FILE, data)


def _legacy_record(name: str, **over) -> dict:
    """A record in the pre-A0 JSON shape, truncated prompts and all."""
    rec = {
        "created_at": "2026-08-01T10:00:00+00:00",
        "files": [{"filename": name, "rel_path": f"_generations/nina/{name}"}],
        "positive_prompt": "a portrait",
        "negative_prompt": "blurry",
        "primary_rel": f"_generations/nina/{name}",
        "workflow": "pro",
        "steps": 30,
        "cfg": 7.0,
        "denoise": 0.6,
        "seed": None,
    }
    rec.update(over)
    return rec


def test_full_prompt_survives_the_round_trip():
    """§2.2: `positive[:500]` meant the recorded prompt could not reproduce the
    image it claimed to describe."""
    index = ArchiveIndex.get()
    long_positive = "cinematic portrait, golden hour, 85mm, " * 40  # ~1,560 chars
    long_negative = "blurry, low quality, watermark, " * 20  # ~640 chars
    assert len(long_positive) > 500 and len(long_negative) > 300

    index.record_generation(
        rel_path="_generations/nina/photo_gen_1.png",
        source_rel="nina/photo.jpg",
        creator="nina",
        workflow="pro",
        seed=987654321,
        positive_prompt=long_positive,
        negative_prompt=long_negative,
    )

    rows = index.list_generations_for("nina/photo.jpg")
    assert len(rows) == 1
    assert rows[0]["positive_prompt"] == long_positive
    assert rows[0]["negative_prompt"] == long_negative
    assert rows[0]["seed"] == 987654321


def test_a_generation_without_a_resolved_seed_is_refused():
    """§2.1 must be impossible to reintroduce, not merely discouraged.

    The writer rejects it before SQLite does, so the caller gets "requires a
    resolved seed" rather than a NOT NULL constraint error naming a column.
    """
    index = ArchiveIndex.get()
    with pytest.raises(ValueError, match="resolved seed"):
        index.record_generation(
            rel_path="_generations/nina/no_seed.png",
            source_rel="nina/photo.jpg",
            creator="nina",
            workflow="pro",
            seed=None,
            positive_prompt="a portrait",
        )
    assert index.list_generations_for("nina/photo.jpg") == []


def test_legacy_json_import_marks_the_unrecorded_seed_as_unknown():
    """§8 risk row: pre-A0 records have `seed: null` and the value is gone for
    good. Import writes -1 — distinguishable from a real seed — rather than
    inventing one that would make regenerate silently wrong."""
    _write_legacy_index({"nina/photo.jpg": [_legacy_record("a_gen_1.png")]})
    index = ArchiveIndex.get()

    imported = index.import_generations_from_json()

    assert imported == 1
    rows = index.list_generations_for("nina/photo.jpg")
    assert len(rows) == 1
    assert rows[0]["seed"] == -1
    assert rows[0]["rating"] == 0
    assert rows[0]["positive_prompt"] == "a portrait"


def test_legacy_import_keeps_a_real_seed_that_was_recorded():
    """Seed-locked generations did record a seed; -1 is only for the null ones."""
    _write_legacy_index({"nina/photo.jpg": [_legacy_record("a_gen_1.png", seed=4242)]})

    ArchiveIndex.get().import_generations_from_json()

    rows = ArchiveIndex.get().list_generations_for("nina/photo.jpg")
    assert rows[0]["seed"] == 4242


def test_one_row_per_output_file_not_per_job():
    """A workflow with a batch node emits several images from one record, and
    each is independently rateable (§3.1)."""
    rec = _legacy_record("a_gen_1.png")
    rec["files"] = [
        {"filename": "a_gen_1.png", "rel_path": "_generations/nina/a_gen_1.png"},
        {"filename": "a_gen_2.png", "rel_path": "_generations/nina/a_gen_2.png"},
    ]
    _write_legacy_index({"nina/photo.jpg": [rec]})

    assert ArchiveIndex.get().import_generations_from_json() == 2
    assert len(ArchiveIndex.get().list_generations_for("nina/photo.jpg")) == 2


def test_import_runs_once_and_does_not_duplicate_on_restart():
    """Guarded by a meta key like the prompts import — every ArchiveIndex
    construction would otherwise re-import the whole file."""
    _write_legacy_index({"nina/photo.jpg": [_legacy_record("a_gen_1.png")]})
    index = ArchiveIndex.get()

    assert index.import_generations_from_json() == 1
    assert index.import_generations_from_json() == 0
    assert len(index.list_generations_for("nina/photo.jpg")) == 1


def test_import_is_a_no_op_when_there_is_no_legacy_file():
    assert not os.path.exists(GENERATIONS_INDEX_FILE)
    assert ArchiveIndex.get().import_generations_from_json() == 0


def test_a_legacy_record_missing_its_files_list_is_skipped_not_fatal():
    """The JSON is hand-editable and was written by a path that could crash
    mid-run; one malformed record must not abandon the rest of the import."""
    _write_legacy_index(
        {
            "nina/photo.jpg": [
                {"created_at": "2026-08-01T10:00:00+00:00", "positive_prompt": "x"},
                _legacy_record("a_gen_1.png"),
            ]
        }
    )

    assert ArchiveIndex.get().import_generations_from_json() == 1


def test_a_fresh_index_imports_the_legacy_file_without_being_asked(tmp_path):
    """"On first run" means construction, not a CLI step nobody will run."""
    _write_legacy_index({"nina/photo.jpg": [_legacy_record("a_gen_1.png")]})

    fresh = ArchiveIndex(db_path=str(tmp_path / "fresh.db"))

    assert len(fresh.list_generations_for("nina/photo.jpg")) == 1
