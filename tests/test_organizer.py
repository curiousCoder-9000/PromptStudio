"""Archive organization: empty creator-folder prune."""

import os

from promptstudio.config import SAVED_DIR
from promptstudio.scraping.organizer import prune_empty_creator_folders


def test_prunes_a_truly_empty_folder():
    empty = os.path.join(SAVED_DIR, "ghost")
    os.makedirs(empty)
    pruned = prune_empty_creator_folders()
    assert "ghost" in pruned
    assert not os.path.isdir(empty)


def test_keeps_a_folder_with_a_photo(make_photo):
    make_photo(creator="nina", name="a.jpg")
    pruned = prune_empty_creator_folders()
    assert "nina" not in pruned
    assert os.path.isfile(os.path.join(SAVED_DIR, "nina", "a.jpg"))


def test_prunes_sidecar_only_folder():
    folder = os.path.join(SAVED_DIR, "leftover")
    os.makedirs(folder)
    with open(os.path.join(folder, "x.jpg.meta.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    pruned = prune_empty_creator_folders()
    assert "leftover" in pruned
    assert not os.path.isdir(folder)


def test_keeps_a_video_only_folder():
    folder = os.path.join(SAVED_DIR, "reels")
    os.makedirs(folder)
    open(os.path.join(folder, "clip.mp4"), "wb").close()
    pruned = prune_empty_creator_folders()
    assert "reels" not in pruned
    assert os.path.isfile(os.path.join(folder, "clip.mp4"))


def test_keeps_uppercase_media_extension():
    folder = os.path.join(SAVED_DIR, "caps")
    os.makedirs(folder)
    open(os.path.join(folder, "a.JPG"), "wb").close()
    pruned = prune_empty_creator_folders()
    assert "caps" not in pruned


def test_keeps_nested_media():
    nested = os.path.join(SAVED_DIR, "nested", "sub")
    os.makedirs(nested)
    open(os.path.join(nested, "a.jpg"), "wb").close()
    pruned = prune_empty_creator_folders()
    assert "nested" not in pruned


def test_skips_excluded_and_dot_folders():
    for name in ("_trash", "_thumbs", ".hidden"):
        os.makedirs(os.path.join(SAVED_DIR, name), exist_ok=True)
    pruned = prune_empty_creator_folders()
    assert "_trash" not in pruned
    assert "_thumbs" not in pruned
    assert ".hidden" not in pruned
    assert os.path.isdir(os.path.join(SAVED_DIR, "_trash"))
    assert os.path.isdir(os.path.join(SAVED_DIR, ".hidden"))


def test_dry_run_does_not_delete():
    empty = os.path.join(SAVED_DIR, "ghost")
    os.makedirs(empty)
    pruned = prune_empty_creator_folders(dry_run=True)
    assert pruned == ["ghost"]
    assert os.path.isdir(empty)


def test_clears_matching_thumbs_folder():
    os.makedirs(os.path.join(SAVED_DIR, "ghost"))
    thumbs = os.path.join(SAVED_DIR, "_thumbs", "ghost")
    os.makedirs(thumbs)
    open(os.path.join(thumbs, "x.jpg"), "wb").close()
    prune_empty_creator_folders()
    assert not os.path.isdir(os.path.join(SAVED_DIR, "ghost"))
    assert not os.path.isdir(thumbs)
