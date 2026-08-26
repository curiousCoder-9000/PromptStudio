"""B3 — taste labels for the preference model.

One ordinal, stored only when judged: 1 keep, -1 discard. Returning to 0
deletes the row so "not labelled yet" is the absence of a row, not a third
value that would leak into keep_rate.
"""

from __future__ import annotations

import pytest

from promptstudio.storage.db import ArchiveIndex


def test_labelling_a_photo_stores_the_value_and_when(make_photo):
    index = ArchiveIndex.get()
    rel, _ = make_photo()

    assert index.set_label(rel, 1) is True

    row = index.get_label(rel)
    assert row["label"] == 1
    assert row["labelled_at"]
    assert row["source"] == "manual"


@pytest.mark.parametrize("label", [-1, 1])
def test_both_points_on_the_scale_are_accepted(make_photo, label):
    index = ArchiveIndex.get()
    rel, _ = make_photo()

    assert index.set_label(rel, label) is True
    assert index.get_label(rel)["label"] == label


@pytest.mark.parametrize("label", [2, -2, 99, "keep", None, True, 1.0])
def test_a_label_off_the_scale_is_refused(make_photo, label):
    index = ArchiveIndex.get()
    rel, _ = make_photo()

    with pytest.raises(ValueError):
        index.set_label(rel, label)

    assert index.get_label(rel) is None


def test_clearing_a_label_deletes_the_row(make_photo):
    index = ArchiveIndex.get()
    rel, _ = make_photo()
    index.set_label(rel, 1)

    assert index.set_label(rel, 0) is True
    assert index.get_label(rel) is None


def test_labelling_an_unknown_photo_reports_failure():
    assert ArchiveIndex.get().set_label("no/such.jpg", 1) is False


def test_seed_copies_favorites_and_trash_without_overwriting(make_photo):
    index = ArchiveIndex.get()
    keep, _ = make_photo(name="fav.jpg")
    discard, _ = make_photo(name="gone.jpg")
    already, _ = make_photo(name="mine.jpg")
    index.set_label(already, -1)

    result = index.seed_labels(keep_paths=[keep, already], discard_paths=[discard])

    assert result["inserted_keep"] == 1
    assert result["inserted_discard"] == 1
    assert result["skipped"] == 1
    assert index.get_label(keep)["source"] == "favorite"
    assert index.get_label(discard)["source"] == "trash"
    assert index.get_label(already)["label"] == -1, "seed overwrote a real judgement"


def test_unlabeled_filter_excludes_labelled_rows(make_photo):
    index = ArchiveIndex.get()
    a, _ = make_photo(name="a.jpg")
    b, _ = make_photo(name="b.jpg")
    index.set_label(a, 1)

    rows, total = index.query_photos(label="unlabeled")
    assert total == 1
    assert rows[0]["rel_path"] == b
    assert "taste_label" not in rows[0]


def test_keep_filter_finds_only_keeps(make_photo):
    index = ArchiveIndex.get()
    a, _ = make_photo(name="a.jpg")
    make_photo(name="b.jpg")
    index.set_label(a, 1)

    rows, total = index.query_photos(label="keep")
    assert total == 1
    assert rows[0]["taste_label"] == 1


def test_label_counts_split_unlabeled_from_labelled(make_photo):
    index = ArchiveIndex.get()
    a, _ = make_photo(name="a.jpg")
    make_photo(name="b.jpg")
    make_photo(name="c.jpg")
    index.set_label(a, 1)

    counts = index.label_counts()
    assert counts["keep"] == 1
    assert counts["discard"] == 0
    assert counts["labelled"] == 1
    assert counts["unlabeled"] == 2
