"""Archive path containment — every media route resolves through here.

`resolve_path` guards `/media/<path>`, `/api/media/detail`, and
`DELETE /api/photo`. The server sends `Access-Control-Allow-Origin: *` and has
no auth, so any page open in the browser can call these; a containment bug is
readable-by-any-website, and with soft delete it can also *move* files.
"""

import os

import pytest

from promptstudio.config import SAVED_DIR
from promptstudio.storage.db import normalize_rel_path


def test_resolves_a_file_inside_the_archive(store, make_photo):
    rel, full = make_photo()
    assert store.resolve_path(rel) == full


def test_returns_none_for_missing_file(store):
    assert store.resolve_path("test_creator/nope.jpg") is None


def test_rejects_parent_directory_escape(store):
    outside = os.path.join(os.path.dirname(SAVED_DIR), "outside_secret.txt")
    with open(outside, "w", encoding="utf-8") as f:
        f.write("SECRET")
    try:
        assert store.resolve_path("../outside_secret.txt") is None
        assert store.resolve_path("creator/../../outside_secret.txt") is None
    finally:
        os.remove(outside)


def test_rejects_sibling_directory_sharing_the_archive_prefix(store):
    """Regression: `startswith(base)` is not containment.

    With base `/x/InstagramSaved`, the path `../InstagramSaved_backup/f.jpg`
    normalizes to `/x/InstagramSaved_backup/f.jpg`, which passes a naive
    prefix check. A media archive with a `_backup` / `_old` / `2` sibling is
    an ordinary setup, so this was reachable in practice.
    """
    sibling = SAVED_DIR + "_backup"
    os.makedirs(sibling, exist_ok=True)
    leaked = os.path.join(sibling, "private.jpg")
    with open(leaked, "w", encoding="utf-8") as f:
        f.write("PRIVATE")
    try:
        assert store.resolve_path("../" + os.path.basename(sibling) + "/private.jpg") is None
    finally:
        os.remove(leaked)
        os.rmdir(sibling)


@pytest.mark.parametrize(
    "hostile",
    [
        "/etc/hosts",
        "../../../../../../etc/passwd",
        "..",
        "../",
        "creator/../../..",
    ],
)
def test_rejects_assorted_hostile_paths(store, hostile):
    assert store.resolve_path(hostile) is None


def test_backslashes_do_not_bypass_containment(store):
    # Windows-style separators arrive from older clients and meta sidecars
    assert store.resolve_path("..\\..\\etc\\passwd") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("creator/file.jpg", "creator/file.jpg"),
        ("creator\\file.jpg", "creator/file.jpg"),
        ("/creator/file.jpg", "creator/file.jpg"),
        ("//creator/file.jpg", "creator/file.jpg"),
        ("", ""),
    ],
)
def test_normalize_rel_path(raw, expected):
    assert normalize_rel_path(raw) == expected
