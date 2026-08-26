"""B3 — `/api/labels` and `?label=` on `/api/photos`."""

from __future__ import annotations

from promptstudio.storage.archive import ArchiveStore
from promptstudio.storage.db import ArchiveIndex
from promptstudio.storage.favorites import FavoritesStore


def test_put_label_persists_and_returns_the_row(api, make_photo):
    rel, _ = make_photo()

    status, payload = api("PUT", "/api/labels", {"path": rel, "label": 1})

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["label"]["label"] == 1
    assert ArchiveIndex.get().get_label(rel)["label"] == 1


def test_put_label_zero_clears(api, make_photo):
    rel, _ = make_photo()
    ArchiveIndex.get().set_label(rel, 1)

    status, payload = api("PUT", "/api/labels", {"path": rel, "label": 0})

    assert status == 200
    assert payload["label"] is None
    assert ArchiveIndex.get().get_label(rel) is None


def test_put_label_off_the_scale_is_400(api, make_photo):
    rel, _ = make_photo()

    status, _payload = api("PUT", "/api/labels", {"path": rel, "label": 3})

    assert status == 400


def test_put_unknown_path_is_404(api):
    status, _ = api("PUT", "/api/labels", {"path": "no/such.jpg", "label": 1})
    assert status == 404


def test_get_summary_and_one_row(api, make_photo):
    rel, _ = make_photo()
    ArchiveIndex.get().set_label(rel, -1)

    status, summary = api("GET", "/api/labels")
    assert status == 200
    assert summary["discard"] == 1
    assert summary["unlabeled"] == 0

    status, row = api("GET", f"/api/labels?path={rel}")
    assert status == 200
    assert row["label"] == -1


def test_photos_label_filter(api, make_photo):
    keep, _ = make_photo(name="keep.jpg")
    make_photo(name="other.jpg")
    ArchiveIndex.get().set_label(keep, 1)

    _s, payload = api("GET", "/api/photos?label=keep")
    assert payload["total"] == 1
    assert payload["photos"][0]["rel_path"] == keep
    assert payload["photos"][0]["taste_label"] == 1

    _s2, unlabeled = api("GET", "/api/photos?label=unlabeled")
    assert unlabeled["total"] == 1


def test_seed_copies_favorites_and_trash(api, make_photo):
    fav, _ = make_photo(name="fav.jpg")
    doomed, _full = make_photo(name="doomed.jpg")
    FavoritesStore().set_favorite(fav, True)
    ArchiveStore().delete_photo(doomed)

    status, payload = api("POST", "/api/labels/seed", {})

    assert status == 200
    assert payload["inserted_keep"] == 1
    assert payload["inserted_discard"] == 1
    assert ArchiveIndex.get().get_label(fav)["label"] == 1
    assert ArchiveIndex.get().get_label(doomed)["label"] == -1
