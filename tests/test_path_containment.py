"""Containment is one implementation now, so test it once and at each caller.

`ArchiveStore.resolve_path` had the correct boundary check; two other copies
(`comfy.client.resolve_archive_file`, `TrashStore.restore`) still used a bare
`startswith(base)`, which passes for a sibling directory sharing the prefix.
`tests/test_paths.py` covers the ArchiveStore route; this covers the primitive
and the two callers that were wrong.
"""

import os

import pytest

from promptstudio.config import SAVED_DIR
from promptstudio.storage.paths import contains, safe_join

# ── the primitive ───────────────────────────────────────────────────────

def test_contains_accepts_the_base_itself():
    assert contains("/a/archive", "/a/archive") is True


def test_contains_accepts_a_child():
    assert contains("/a/archive", "/a/archive/creator/x.jpg") is True


def test_contains_rejects_a_sibling_sharing_the_prefix():
    """The whole reason this module exists."""
    assert contains("/a/archive", "/a/archive_backup/x.jpg") is False


def test_contains_normalizes_before_comparing():
    assert contains("/a/archive", "/a/archive/creator/../x.jpg") is True
    assert contains("/a/archive", "/a/archive/../x.jpg") is False


def test_safe_join_returns_none_for_absolute_paths():
    # os.path.join(base, "/etc/passwd") discards base entirely
    assert safe_join("/a/archive", "/etc/passwd") is None


def test_safe_join_returns_none_for_parent_escape():
    assert safe_join("/a/archive", "../../etc/passwd") is None


def test_safe_join_returns_none_for_sibling_prefix():
    assert safe_join("/a/archive", "../archive_backup/private.jpg") is None


def test_safe_join_allows_a_normal_relative_path():
    assert safe_join("/a/archive", "nina/x.jpg") == os.path.normpath(
        "/a/archive/nina/x.jpg"
    )


# ── caller: ComfyUI reference resolution ────────────────────────────────

def test_comfy_rejects_sibling_directory_sharing_the_archive_prefix():
    """Regression: this used `startswith(normpath(SAVED_DIR))`."""
    from promptstudio.comfy.client import resolve_archive_file

    sibling = SAVED_DIR + "_backup"
    os.makedirs(sibling, exist_ok=True)
    leaked = os.path.join(sibling, "private.jpg")
    with open(leaked, "w", encoding="utf-8") as f:
        f.write("PRIVATE")
    try:
        escape = "../" + os.path.basename(sibling) + "/private.jpg"
        with pytest.raises(ValueError):
            resolve_archive_file(escape)
    finally:
        os.remove(leaked)
        os.rmdir(sibling)


def test_comfy_resolves_a_real_archive_file(make_photo):
    from promptstudio.comfy.client import resolve_archive_file

    rel, full = make_photo(creator="comfytest", name="ref.jpg")
    assert resolve_archive_file(rel) == os.path.normpath(full)


def test_comfy_raises_not_found_for_a_contained_but_missing_file():
    from promptstudio.comfy.client import resolve_archive_file

    with pytest.raises(FileNotFoundError):
        resolve_archive_file("comfytest/nope.jpg")


# ── caller: trash restore ───────────────────────────────────────────────

def test_restore_refuses_a_manifest_pointing_outside_the_archive(make_photo):
    """Restore is a `move` *into* the target, so containment writes, not reads.

    The manifest is written by us, but a hand-edited or corrupted one must not
    be able to place a file next to the archive.
    """
    import json

    from promptstudio.storage.archive import ArchiveStore
    from promptstudio.storage.trash import TrashStore

    rel, _full = make_photo(creator="trashtest", name="a.jpg")
    result = ArchiveStore().delete_photo(rel)
    entry_id = result["trash_id"]
    assert entry_id

    trash = TrashStore()
    entry_dir = trash._entry_dir(entry_id)
    manifest = os.path.join(entry_dir, "entry.json")
    with open(manifest, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["rel_path"] = "../" + os.path.basename(SAVED_DIR) + "_backup/a.jpg"
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump(data, f)

    out = trash.restore(entry_id)
    assert out["status"] == "error"
    assert out["message"] == "unsafe target path"
    assert not os.path.exists(SAVED_DIR + "_backup/a.jpg")


def test_restore_still_works_for_a_normal_entry(make_photo):
    from promptstudio.storage.archive import ArchiveStore
    from promptstudio.storage.trash import TrashStore

    rel, full = make_photo(creator="trashtest", name="b.jpg")
    entry_id = ArchiveStore().delete_photo(rel)["trash_id"]
    assert not os.path.isfile(full)

    assert TrashStore().restore(entry_id)["status"] == "restored"
    assert os.path.isfile(full)
