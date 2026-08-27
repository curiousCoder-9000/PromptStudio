"""U13/U14 — bulk manual verdicts and the whole-pile path list."""

from promptstudio.storage.db import ArchiveIndex


def _seed(index, make_photo, rows):
    out = {}
    for name, tier in rows:
        rel, _full = make_photo(name=name)
        index.set_verdict(
            rel,
            creator="test_creator",
            tier=tier,
            reason=f"tier {tier}",
            media_kind="photo",
            verdict_source="image",
        )
        out[name] = rel
    return out


def test_single_verdict_shape_is_unchanged(api, make_photo):
    index = ArchiveIndex.get()
    rels = _seed(index, make_photo, [("a.jpg", 0)])

    status, payload = api(
        "POST", "/api/classify/verdict", {"rel_path": rels["a.jpg"], "verdict": "keep"}
    )

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["rel_path"] == rels["a.jpg"]
    assert payload["verdict"]["verdict"] == "keep"
    assert payload["verdict"]["manual"] == "keep"


def test_bulk_keep_updates_every_classified_path(api, make_photo):
    index = ArchiveIndex.get()
    rels = _seed(index, make_photo, [("a.jpg", 0), ("b.jpg", 1)])
    unclassified, _ = make_photo(name="never.jpg")

    status, payload = api(
        "POST",
        "/api/classify/verdict",
        {
            "rel_paths": [rels["a.jpg"], rels["b.jpg"], unclassified],
            "verdict": "keep",
        },
    )

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["verdict"] == "keep"
    assert set(payload["updated"]) == {rels["a.jpg"], rels["b.jpg"]}
    assert payload["missing"] == [unclassified]
    assert payload["verdicts"][rels["a.jpg"]]["verdict"] == "keep"
    assert index.get_verdict(rels["b.jpg"])["manual"] == "keep"


def test_bulk_empty_list_is_400(api):
    status, _payload = api("POST", "/api/classify/verdict", {"rel_paths": [], "verdict": "keep"})
    assert status == 400


def test_photos_ids_returns_the_whole_filtered_pile(api, make_photo):
    index = ArchiveIndex.get()
    rels = _seed(index, make_photo, [("a.jpg", 0), ("b.jpg", 0), ("keep.jpg", 4)])
    index.set_favorite(rels["b.jpg"], True)

    status, payload = api("GET", "/api/photos?verdict=reject&ids=1")

    assert status == 200
    assert payload["total"] == 2
    assert payload["truncated"] is False
    by_path = {row["rel_path"]: row["favorite"] for row in payload["paths"]}
    assert by_path[rels["a.jpg"]] is False
    assert by_path[rels["b.jpg"]] is True
    assert "photos" not in payload
    assert rels["keep.jpg"] not in by_path


def test_photos_accepts_keep_tier_filters(api, make_photo):
    """Reject already is T0+T1. t2/t3/t4 have to be first-class query values
    or the browse dropdown cannot split the keep pile."""
    index = ArchiveIndex.get()
    rels = _seed(index, make_photo, [("fashion.jpg", 2), ("swim.jpg", 4)])

    status, payload = api("GET", "/api/photos?verdict=t4")
    assert status == 200
    assert payload["total"] == 1
    assert payload["photos"][0]["rel_path"] == rels["swim.jpg"]

    status, payload = api("GET", "/api/photos?verdict=t2")
    assert status == 200
    assert payload["total"] == 1
    assert payload["photos"][0]["rel_path"] == rels["fashion.jpg"]
