"""Captions are searchable (F1).

Everything in `prompt_search` is model-generated: positive prompt, negative
prompt, raw vision description, visual tags. The caption is downloaded on every
sync and written to every sidecar — and was indexed nowhere, so the only
human-written text in the archive could not be found.

`caption_search` is a separate column rather than more text in `prompt_search`:
the caption is fixed for the life of the file while the prompt blob is rewritten
on every regenerate, and merging them would mean re-deriving the caption on
every prompt save for no gain.
"""

import pytest

from promptstudio.storage.db import ArchiveIndex, caption_search_blob


@pytest.fixture
def index():
    idx = ArchiveIndex.get()
    idx.ensure_ready()
    return idx


# ── the blob helper ──────────────────────────────────────────────────


def test_blob_is_lowercased_and_keeps_hashes():
    blob = caption_search_blob({"caption": "Golden hour in Lisbon #OOTD #Sunset"})
    assert blob == "golden hour in lisbon #ootd #sunset"


def test_blob_includes_author_for_reposts():
    """On Reddit/X the real author is not the folder name, so it is otherwise unfindable."""
    blob = caption_search_blob({"caption": "repost", "author": "RealPhotographer"})
    assert "realphotographer" in blob


@pytest.mark.parametrize("meta", [None, {}, {"caption": ""}, {"caption": None}])
def test_blob_is_empty_when_there_is_no_caption(meta):
    assert caption_search_blob(meta) == ""


# ── indexing ─────────────────────────────────────────────────────────


def test_caption_indexed_on_upsert(index, make_photo):
    rel, _full = make_photo(
        "cap_creator", "a.jpg", meta={"caption": "Beach day in a red bikini"}
    )
    row = index._conn.execute(
        "SELECT caption_search FROM photos WHERE rel_path = ?", (rel,)
    ).fetchone()
    assert "red bikini" in row["caption_search"]


def test_caption_survives_a_favorite_toggle(index, make_photo):
    """A favorite toggle passes no sidecar fields — it must not blank the index."""
    rel, _ = make_photo("cap_creator", "b.jpg", meta={"caption": "sunset rooftop"})
    index.upsert_photo(rel, favorite=1)
    row = index._conn.execute(
        "SELECT caption_search, favorite FROM photos WHERE rel_path = ?", (rel,)
    ).fetchone()
    assert row["favorite"] == 1
    assert "sunset rooftop" in row["caption_search"]


def test_caption_survives_a_prompt_write(index, make_photo):
    rel, _ = make_photo("cap_creator", "c.jpg", meta={"caption": "studio portrait"})
    index.upsert_photo(rel, has_prompt=1, prompt_search="a woman standing")
    row = index._conn.execute(
        "SELECT caption_search, prompt_search FROM photos WHERE rel_path = ?", (rel,)
    ).fetchone()
    assert "studio portrait" in row["caption_search"]
    assert "a woman standing" in row["prompt_search"]


def test_explicit_caption_argument_wins(index, make_photo):
    rel, _ = make_photo("cap_creator", "d.jpg", meta={"caption": "from sidecar"})
    index.upsert_photo(rel, caption="from argument")
    row = index._conn.execute(
        "SELECT caption_search FROM photos WHERE rel_path = ?", (rel,)
    ).fetchone()
    assert row["caption_search"] == "from argument"


def test_rebuild_populates_captions(index, make_photo):
    rel, _ = make_photo("cap_creator", "e.jpg", meta={"caption": "mountain hike"})
    index.rebuild()
    row = index._conn.execute(
        "SELECT caption_search FROM photos WHERE rel_path = ?", (rel,)
    ).fetchone()
    assert "mountain hike" in row["caption_search"]


# ── search ───────────────────────────────────────────────────────────


def test_search_finds_a_word_only_in_the_caption(index, make_photo):
    """The whole point: a term that appears in no prompt still resolves."""
    rel, _ = make_photo(
        "cap_creator", "f.jpg", meta={"caption": "wearing a red bikini in Mykonos"}
    )
    make_photo("cap_creator", "g.jpg", meta={"caption": "coffee shop, knitwear"})

    photos, total = index.query_photos(search="mykonos")
    assert total == 1
    assert photos[0]["rel_path"] == rel


def test_search_matches_a_hashtag(index, make_photo):
    rel, _ = make_photo("cap_creator", "h.jpg", meta={"caption": "late night #neon"})
    photos, total = index.query_photos(search="#neon")
    assert total == 1 and photos[0]["rel_path"] == rel
    # The bare word still matches, because this is a substring scan.
    photos, total = index.query_photos(search="neon")
    assert total == 1


def test_search_is_case_insensitive(index, make_photo):
    make_photo("cap_creator", "i.jpg", meta={"caption": "Santorini Blue"})
    _photos, total = index.query_photos(search="SANTORINI")
    assert total == 1


def test_caption_search_does_not_break_prompt_search(index, make_photo):
    """Both columns are OR'd; a prompt-only hit must still resolve."""
    rel, _ = make_photo("cap_creator", "j.jpg", meta={"caption": "no useful words"})
    index.upsert_photo(rel, has_prompt=1, prompt_search="cinematic rim lighting")
    _photos, total = index.query_photos(search="rim lighting")
    assert total == 1


def test_photo_with_no_sidecar_is_searchable_by_filename(index, make_photo):
    """NULL caption_search must not swallow the row via IFNULL."""
    make_photo("cap_creator", "distinctive_name.jpg")
    _photos, total = index.query_photos(search="distinctive_name")
    assert total == 1


def test_search_still_matches_creator(index, make_photo):
    make_photo("unique_handle", "k.jpg", meta={"caption": "irrelevant"})
    _photos, total = index.query_photos(search="unique_handle")
    assert total == 1
