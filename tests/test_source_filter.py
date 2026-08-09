"""Source as a view filter — see docs/design_source_filter.md.

The rule under test throughout: provenance comes from `photos.source`, never
from the folder-name suffix. A folder is a location, a source is a provenance,
and `SCRAPE_FOLDER_SUFFIX=0` plus manual uploads make the two diverge.
"""

import os

import pytest
from PIL import Image

from promptstudio.config import SAVED_DIR
from promptstudio.storage.archive import ArchiveStore
from promptstudio.storage.db import ArchiveIndex


def _photo(creator: str, name: str, source: str) -> str:
    """Index one real JPEG under `creator` with an explicit source."""
    folder = os.path.join(SAVED_DIR, creator)
    os.makedirs(folder, exist_ok=True)
    full = os.path.join(folder, name)
    Image.new("RGB", (32, 32), (10, 20, 30)).save(full, "JPEG")
    rel = f"{creator}/{name}"
    ArchiveIndex.get().upsert_photo(rel, source=source, post_id=name.split(".")[0])
    return rel


@pytest.fixture
def mixed_archive():
    """Three creators: IG-only, X-only, and one merged folder holding both."""
    _photo("nina", "ig_1.jpg", "instagram")
    _photo("nina", "ig_2.jpg", "instagram")
    _photo("nina", "ig_3.jpg", "instagram")
    _photo("kaya__x", "x_1.jpg", "x")
    # The merged case: SCRAPE_FOLDER_SUFFIX=0, one bare folder, two platforms.
    _photo("mira", "ig_a.jpg", "instagram")
    _photo("mira", "x_a.jpg", "x")
    _photo("mira", "x_b.jpg", "x")
    return ArchiveIndex.get()


# ------------------------------------------------------------------ creators


def test_creators_unfiltered_counts_every_source(mixed_archive):
    by_name = {c["name"]: c for c in mixed_archive.list_creators()}
    assert by_name["nina"]["photo_count"] == 3
    assert by_name["kaya__x"]["photo_count"] == 1
    assert by_name["mira"]["photo_count"] == 3


def test_creators_scoped_to_source(mixed_archive):
    by_name = {c["name"]: c for c in mixed_archive.list_creators(source="x")}
    assert set(by_name) == {"kaya__x", "mira"}
    # The merged folder reports only its X half.
    assert by_name["mira"]["photo_count"] == 2
    assert "nina" not in by_name, "IG-only creator must drop out of the X view"


def test_merged_folder_appears_under_both_pills(mixed_archive):
    for source in ("instagram", "x"):
        names = {c["name"] for c in mixed_archive.list_creators(source=source)}
        assert "mira" in names, f"merged folder missing from {source} view"


def test_sources_map_is_not_narrowed_by_the_filter(mixed_archive):
    """The sidebar must mark a folder multi-source *while* a filter is active."""
    by_name = {c["name"]: c for c in mixed_archive.list_creators(source="x")}
    assert by_name["mira"]["sources"] == {"instagram": 1, "x": 2}
    assert by_name["kaya__x"]["sources"] == {"x": 1}


def test_unfiltered_cover_comes_from_the_largest_source(mixed_archive):
    by_name = {c["name"]: c for c in mixed_archive.list_creators()}
    # mira is 1 IG + 2 X, so the cover should be an X file.
    assert "x_a.jpg" in by_name["mira"]["cover_url"]


def test_filtered_cover_comes_from_the_filtered_source(mixed_archive):
    by_name = {c["name"]: c for c in mixed_archive.list_creators(source="instagram")}
    assert "ig_a.jpg" in by_name["mira"]["cover_url"]


def test_creators_sorted_by_count_desc(mixed_archive):
    counts = [c["photo_count"] for c in mixed_archive.list_creators()]
    assert counts == sorted(counts, reverse=True)


# -------------------------------------------------------------------- photos


def test_photos_filtered_by_source(mixed_archive):
    store = ArchiveStore()
    photos, total = store.query_photos(source="x")
    assert total == 3
    assert {p["filename"] for p in photos} == {"x_1.jpg", "x_a.jpg", "x_b.jpg"}


def test_photos_source_and_creator_are_anded(mixed_archive):
    store = ArchiveStore()
    _photos, total = store.query_photos(creator="mira", source="x")
    assert total == 2, "creator + source must intersect, not union"


def test_photos_unfiltered_returns_every_source(mixed_archive):
    store = ArchiveStore()
    _photos, total = store.query_photos()
    assert total == 7  # 3 nina + 1 kaya__x + 3 mira


# ------------------------------------------------------------------ verdicts


def test_verdict_counts_are_source_scoped(mixed_archive):
    """Otherwise the reject pill counts IG rejects while you are viewing X."""
    index = mixed_archive
    index.set_verdict("mira/ig_a.jpg", tier=0, reason="ig reject")
    index.set_verdict("mira/x_a.jpg", tier=4, reason="x keep")

    ig = index.creator_verdict_counts(source="instagram")["mira"]
    assert ig["reject_count"] == 1
    assert ig["keep_count"] == 0

    x = index.creator_verdict_counts(source="x")["mira"]
    assert x["reject_count"] == 0
    assert x["keep_count"] == 1

    both = index.creator_verdict_counts()["mira"]
    assert both["reject_count"] == 1
    assert both["keep_count"] == 1


def test_list_creators_carries_scoped_verdict_counts(mixed_archive):
    mixed_archive.set_verdict("mira/ig_a.jpg", tier=0, reason="ig reject")
    by_name = {c["name"]: c for c in mixed_archive.list_creators(source="x")}
    assert by_name["mira"]["reject_count"] == 0, (
        "X view must not inherit the Instagram reject"
    )


# ----------------------------------------------------------------- normalize


def test_source_value_is_normalized(mixed_archive):
    """Aliases and casing resolve to the canonical name at the storage layer."""
    assert len(mixed_archive.list_creators(source="X")) == 2


def test_legacy_rows_default_to_instagram(make_photo):
    """`source` is NOT NULL DEFAULT 'instagram', so legacy media needs no backfill."""
    make_photo(creator="legacy", name="old.jpg")
    by_name = {c["name"]: c for c in ArchiveIndex.get().list_creators()}
    assert by_name["legacy"]["sources"] == {"instagram": 1}
