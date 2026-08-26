"""Phase 15 HTTP: duplicates, views, collections, trash thumbs, taste."""

from __future__ import annotations

from promptstudio.prompts.cache import PromptCache
from promptstudio.storage.archive import ArchiveStore
from promptstudio.storage.db import ArchiveIndex
from promptstudio.storage.dedupe import compute_phash
from promptstudio.storage.favorites import FavoritesStore

PROMPT = {
    "positive_prompt": "studio portrait, red bikini, golden hour",
    "visual_tags": ["studio"],
    "structured_vision": {
        "clothing": "red bikini",
        "pose": "standing",
        "lighting": "golden hour",
        "background": "white studio seamless",
        "face": "",
        "hair": "",
        "body": "",
        "expression": "",
    },
    "parameters": {"pipeline_version": "v2-structured"},
}


def test_trash_list_includes_media_urls(api, make_photo):
    rel, _ = make_photo(name="gone.jpg")
    ArchiveStore().delete_photo(rel)

    status, payload = api("GET", "/api/trash")
    assert status == 200
    assert payload["total"] == 1
    entry = payload["entries"][0]
    assert entry["media_present"] is True
    assert entry["url"].startswith("/media/_trash/")
    assert entry["thumb_url"].startswith("/media/thumb/_trash/")
    assert "gone.jpg" in entry["url"]


def test_views_round_trip(api):
    status, payload = api(
        "POST",
        "/api/views",
        {"name": "keeps newest", "filters": {"sortMode": "newest", "browseVerdict": "keep"}},
    )
    assert status == 200
    view_id = payload["view"]["id"]

    status, listed = api("GET", "/api/views")
    assert status == 200
    assert listed["views"][0]["name"] == "keeps newest"
    assert listed["views"][0]["filters"]["sortMode"] == "newest"

    status, _ = api("DELETE", f"/api/views?id={view_id}")
    assert status == 200
    _s, empty = api("GET", "/api/views")
    assert empty["views"] == []


def test_views_blank_name_is_400(api):
    status, _ = api("POST", "/api/views", {"name": "  ", "filters": {}})
    assert status == 400


def test_collections_membership_filters_photos(api, make_photo):
    a, _ = make_photo(name="in.jpg")
    make_photo(name="out.jpg")
    status, payload = api("POST", "/api/collections", {"name": "board"})
    assert status == 200
    cid = payload["collection"]["id"]

    status, added = api("POST", "/api/collections/items", {"id": cid, "paths": [a]})
    assert status == 200
    assert added["added"] == 1

    _s, photos = api("GET", f"/api/photos?collection={cid}")
    assert photos["total"] == 1
    assert photos["photos"][0]["rel_path"] == a

    api("DELETE", "/api/collections/items", {"id": cid, "paths": [a]})
    _s2, after = api("GET", f"/api/photos?collection={cid}")
    assert after["total"] == 0


def test_c5_facet_route_and_photo_fields_are_gone(api, make_photo):
    """C5 chips were chrome over freeform vision phrases — ripped, not hidden."""
    rel, _ = make_photo(name="studio.jpg")
    PromptCache().set(rel, dict(PROMPT), push_history=False)

    status, _payload = api("GET", "/api/facets")
    assert status == 404

    _s, photos = api("GET", "/api/photos?setting=studio")
    assert photos["total"] == 1
    row = photos["photos"][0]
    assert "setting" not in row
    assert "outfit" not in row
    assert "pose" not in row
    assert "lighting" not in row


def test_duplicates_exclude_carousel_and_never_preselect_favorite(api, make_photo):
    a, full_a = make_photo(name="dup_a.jpg", meta={"post_id": "111"})
    b, full_b = make_photo(name="dup_b.jpg", meta={"post_id": "222"})
    # Same bytes → identical pHash, different posts so it is a real dup group.
    from PIL import Image

    Image.new("RGB", (64, 80), (12, 40, 90)).save(full_a, "JPEG")
    Image.new("RGB", (64, 80), (12, 40, 90)).save(full_b, "JPEG")
    index = ArchiveIndex.get()
    index.set_phash(a, compute_phash(full_a))
    index.set_phash(b, compute_phash(full_b))
    FavoritesStore().set_favorite(a, True)
    index.upsert_photo(a, favorite=1)

    status, payload = api("GET", "/api/duplicates?kind=phash")
    assert status == 200
    assert payload["total_groups"] >= 1
    group = payload["groups"][0]
    by_path = {m["rel_path"]: m for m in group["members"]}
    assert by_path[a]["favorite"] is True
    assert by_path[a]["preselected"] is False
    assert by_path[b]["preselected"] is True
    assert group["keeper"] == a


def test_carousel_siblings_are_not_duplicates(api, make_photo):
    a, full_a = make_photo(name="c1.jpg", meta={"post_id": "samepost"})
    b, full_b = make_photo(name="c2.jpg", meta={"post_id": "samepost"})
    from PIL import Image

    Image.new("RGB", (64, 80), (9, 9, 9)).save(full_a, "JPEG")
    Image.new("RGB", (64, 80), (9, 9, 9)).save(full_b, "JPEG")
    index = ArchiveIndex.get()
    index.set_phash(a, compute_phash(full_a))
    index.set_phash(b, compute_phash(full_b))

    status, payload = api("GET", "/api/duplicates?kind=phash")
    assert status == 200
    assert payload["total_groups"] == 0


def test_taste_status_and_foryou_sort(api, make_photo):
    status, payload = api("GET", "/api/taste/status")
    assert status == 200
    assert "running" in payload

    rel, _ = make_photo(name="scored.jpg")
    ArchiveIndex.get().set_p_keeps([(rel, 0.81)])
    _s, photos = api("GET", "/api/photos?sort=foryou")
    assert photos["photos"][0]["p_keep"] == 0.81


def test_journal_kinds_endpoint_still_lists(api):
    status, payload = api("GET", "/api/journal")
    assert status == 200
    assert "kinds" in payload
