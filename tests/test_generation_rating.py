"""A3 — rating generations.

One ordinal, not a boolean plus a star flag:
`-1` discard · `0` unrated · `1` keep · `2` star (design_generation_loop.md §3.3).

The rating is the only human judgement the generation loop captures, so the
rules here are about not losing it and not faking it: an unknown id reports
failure rather than succeeding silently, and an out-of-range value is refused
rather than stored and later averaged into a metric.
"""

from __future__ import annotations

import pytest

from promptstudio.config import ARCHIVE_DB_FILE
from promptstudio.storage.db import ArchiveIndex


def _seed_generation(index, *, rel="_generations/nina/g1.png", **over):
    kwargs = dict(
        rel_path=rel,
        source_rel="nina/photo.jpg",
        creator="nina",
        workflow="pro",
        seed=4242,
        positive_prompt="a portrait",
    )
    kwargs.update(over)
    return index.record_generation(**kwargs)


def test_rating_a_generation_stores_the_value_and_when():
    index = ArchiveIndex.get()
    gen_id = _seed_generation(index)

    assert index.rate_generation(gen_id, 2) is True

    row = index.list_generations_for("nina/photo.jpg")[0]
    assert row["rating"] == 2
    assert row["rated_at"]


@pytest.mark.parametrize("rating", [-1, 0, 1, 2])
def test_every_point_on_the_scale_is_accepted(rating):
    index = ArchiveIndex.get()
    gen_id = _seed_generation(index)

    assert index.rate_generation(gen_id, rating) is True
    assert index.list_generations_for("nina/photo.jpg")[0]["rating"] == rating


@pytest.mark.parametrize("rating", [3, -2, 99, "keep", None, 1.5])
def test_a_rating_off_the_scale_is_refused(rating):
    """Stored, it would be silently averaged into keep_rate later."""
    index = ArchiveIndex.get()
    gen_id = _seed_generation(index)

    with pytest.raises(ValueError):
        index.rate_generation(gen_id, rating)

    assert index.list_generations_for("nina/photo.jpg")[0]["rating"] == 0


def test_rating_an_unknown_generation_reports_failure():
    """Silently succeeding would make the UI show a rating the store discarded."""
    assert ArchiveIndex.get().rate_generation("no-such-gen-id", 1) is False


def test_returning_to_unrated_clears_the_timestamp():
    """`rated_at` set with `rating = 0` would claim a judgement that was undone."""
    index = ArchiveIndex.get()
    gen_id = _seed_generation(index)
    index.rate_generation(gen_id, 1)

    index.rate_generation(gen_id, 0)

    row = index.list_generations_for("nina/photo.jpg")[0]
    assert row["rating"] == 0
    assert row["rated_at"] is None


def test_a_rating_survives_a_restart():
    """The §4 gate. A second ArchiveIndex on the same file is the closest thing
    to a process restart without one."""
    index = ArchiveIndex.get()
    gen_id = _seed_generation(index)
    index.rate_generation(gen_id, 2)

    reopened = ArchiveIndex(db_path=ARCHIVE_DB_FILE)

    assert reopened.list_generations_for("nina/photo.jpg")[0]["rating"] == 2


def test_re_recording_a_generation_does_not_wipe_its_rating():
    """A0 leaves `rating` out of the ON CONFLICT update on purpose — the user's
    verdict has to outlive a metadata rewrite."""
    index = ArchiveIndex.get()
    gen_id = _seed_generation(index)
    index.rate_generation(gen_id, 2)

    _seed_generation(index, positive_prompt="a rewritten prompt")

    row = index.list_generations_for("nina/photo.jpg")[0]
    assert row["positive_prompt"] == "a rewritten prompt"
    assert row["rating"] == 2
