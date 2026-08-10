"""C2 — post / carousel grouping in the gallery.

`photos.post_id` has been populated and indexed since the multi-source work, so
collapsing a carousel into one tile is a query change, not a data change. What
is easy to get wrong is the *paging*: `total` and `has_more` drive an
infinite-scroll sentinel, and a `total` that counts rows while the grid renders
groups makes the sentinel skip content silently. Half of this file is about
that one number.

Not covered here on purpose: `storage/metadata.py::group_by_post_id`, which
walks sidecars per creator. It is the scraper's grouping and is a filesystem
scan; the gallery must never use it.
"""

from promptstudio.storage.db import ArchiveIndex


def _rels(photos):
    return [p["rel_path"] for p in photos]


def _carousel(make_photo, creator, post_id, n, *, ext="jpg", start=1):
    """n slides of one post, named so lexicographic != natural past slide 9."""
    out = []
    for i in range(start, start + n):
        rel, _ = make_photo(
            creator=creator,
            name=f"{post_id}_{i}.{ext}",
            meta={"post_id": post_id, "shortcode": post_id},
        )
        out.append(rel)
    return out


# ── grouping shape ───────────────────────────────────────────────────


def test_carousel_collapses_to_one_row_with_a_count(make_photo):
    _carousel(make_photo, "ana", "post_a", 3)
    solo, _ = make_photo(creator="ana", name="solo.jpg", meta={"post_id": "post_b"})

    photos, total = ArchiveIndex.get().query_photos(group_posts=True)

    assert total == 2, "two posts, not four files"
    by_key = {p["group_key"]: p for p in photos}
    # Keys are creator-scoped: post ids come from three platforms that share no
    # namespace, and the group key is also what delimits slides client-side.
    assert set(by_key) == {"ana/post_a", "ana/post_b"}
    assert by_key["ana/post_a"]["group_count"] == 3
    assert by_key["ana/post_a"]["group_members"] == [
        "ana/post_a_1.jpg",
        "ana/post_a_2.jpg",
        "ana/post_a_3.jpg",
    ]
    assert by_key["ana/post_b"]["group_count"] == 1
    assert by_key["ana/post_b"]["group_members"] == [solo]


def test_grouping_off_by_default_and_unchanged(make_photo):
    _carousel(make_photo, "ana", "post_a", 3)

    photos, total = ArchiveIndex.get().query_photos()

    assert total == 3
    assert len(photos) == 3
    assert "group_count" not in photos[0]
    assert "group_members" not in photos[0]


def test_photos_without_a_post_id_group_to_themselves(make_photo):
    # NULL and '' are both "no post" — an empty string must not collapse every
    # manual upload in the archive into a single tile.
    a, _ = make_photo(creator="ana", name="a.jpg")
    b, _ = make_photo(creator="ana", name="b.jpg", meta={"post_id": ""})
    c, _ = make_photo(creator="ana", name="c.jpg", meta={"post_id": None})

    photos, total = ArchiveIndex.get().query_photos(group_posts=True)

    assert total == 3
    assert sorted(p["group_key"] for p in photos) == sorted([a, b, c])
    assert all(p["group_count"] == 1 for p in photos)
    assert sorted(_rels(photos)) == sorted([a, b, c])


def test_members_sort_naturally_not_lexicographically(make_photo):
    # The lightbox walks slides in order. 'post_10' sorts before 'post_2' as
    # text, so group_concat order (which SQLite does not guarantee anyway)
    # cannot be trusted — the sort happens in Python.
    _carousel(make_photo, "ana", "post_a", 11)

    photos, _ = ArchiveIndex.get().query_photos(group_posts=True)

    assert photos[0]["group_count"] == 11
    assert photos[0]["group_members"] == [
        f"ana/post_a_{i}.jpg" for i in range(1, 12)
    ]


def test_representative_row_is_stable_across_calls(make_photo):
    # Bare columns under GROUP BY are otherwise whichever row SQLite happened
    # to visit last, so the tile thumbnail could change between two identical
    # queries.
    _carousel(make_photo, "ana", "post_a", 5)
    index = ArchiveIndex.get()

    first = index.query_photos(group_posts=True)[0][0]["rel_path"]
    for _ in range(4):
        assert index.query_photos(group_posts=True)[0][0]["rel_path"] == first
    assert first == "ana/post_a_1.jpg", "the tile should be slide 1"


def test_the_group_key_expression_index_exists(make_photo):
    # The GROUP BY is spelled as two terms *because* this index exists — it is
    # what keeps a grouped page within ~1.1x of an ungrouped one. Losing the
    # migration would show up only as the gallery quietly getting slower.
    make_photo(creator="ana", name="a.jpg")
    index = ArchiveIndex.get()

    with index._lock:
        row = index._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
            ("idx_photos_group_key",),
        ).fetchone()
        plan = [
            r["detail"]
            for r in index._conn.execute(
                "EXPLAIN QUERY PLAN SELECT p.rel_path FROM photos p "
                "GROUP BY p.creator, IFNULL(NULLIF(p.post_id, ''), p.filename)"
            ).fetchall()
        ]

    assert row is not None, "idx_photos_group_key was not migrated in"
    assert "IFNULL" in row["sql"]
    assert any("idx_photos_group_key" in d for d in plan), plan


def test_a_group_never_spans_creators(make_photo):
    # Two platforms can mint the same post id. Grouping across folders would
    # merge unrelated media into one tile.
    make_photo(creator="ana", name="x.jpg", meta={"post_id": "dup"})
    make_photo(creator="bea", name="y.jpg", meta={"post_id": "dup"})

    photos, total = ArchiveIndex.get().query_photos(group_posts=True)

    assert total == 2
    assert all(p["group_count"] == 1 for p in photos)


# ── filters compose with grouping ────────────────────────────────────


def test_filters_narrow_the_group_too(make_photo):
    _carousel(make_photo, "ana", "post_a", 2)
    make_photo(creator="ana", name="post_a_3.mp4", meta={"post_id": "post_a"})

    index = ArchiveIndex.get()
    all_media, _ = index.query_photos(group_posts=True)
    assert all_media[0]["group_count"] == 3

    stills, total = index.query_photos(group_posts=True, media_type="photo")
    assert total == 1
    assert stills[0]["group_count"] == 2, "the reel is filtered out of the group"
    assert stills[0]["group_members"] == ["ana/post_a_1.jpg", "ana/post_a_2.jpg"]


def test_creator_filter_and_grouping_compose(make_photo):
    _carousel(make_photo, "ana", "post_a", 3)
    _carousel(make_photo, "bea", "post_b", 2)

    photos, total = ArchiveIndex.get().query_photos(group_posts=True, creator="bea")

    assert total == 1
    assert photos[0]["group_count"] == 2
    assert photos[0]["creator"] == "bea"


# ── the paging trap ──────────────────────────────────────────────────


def _seed_five_groups(make_photo):
    """11 files, 5 groups: three 3-slide carousels and two singles."""
    for name in ("post_a", "post_b", "post_c"):
        _carousel(make_photo, "ana", name, 3)
    make_photo(creator="ana", name="solo_1.jpg", meta={"post_id": "post_d"})
    make_photo(creator="ana", name="solo_2.jpg")
    return 11, 5


def test_total_counts_groups_not_rows(make_photo):
    files, groups = _seed_five_groups(make_photo)
    index = ArchiveIndex.get()

    assert index.query_photos()[1] == files
    assert index.query_photos(group_posts=True)[1] == groups


def test_total_is_the_group_count_even_when_a_page_is_short(make_photo):
    # `has_more` is derived from `total` upstream, so a `total` that counted
    # files here would keep the sentinel asking for pages that do not exist.
    _seed_five_groups(make_photo)

    photos, total = ArchiveIndex.get().query_photos(group_posts=True, limit=2)

    assert len(photos) == 2
    assert total == 5


def test_paging_walks_every_group_exactly_once(make_photo):
    # The actual failure mode: page in groups against a row-counted total and
    # the walk either repeats or skips. Assert the whole walk, not one page.
    _, groups = _seed_five_groups(make_photo)
    index = ArchiveIndex.get()

    seen, offset, pages = [], 0, 0
    while True:
        page, total = index.query_photos(
            group_posts=True, sort="name", limit=2, offset=offset
        )
        assert total == groups
        if not page:
            break
        seen.extend(p["group_key"] for p in page)
        offset += len(page)
        pages += 1
        assert pages < 10, "paging did not terminate"

    assert len(seen) == groups, seen
    assert len(set(seen)) == groups, "a group was returned on two pages"
    assert offset == groups


def test_every_file_is_reachable_through_exactly_one_group(make_photo):
    files, _ = _seed_five_groups(make_photo)
    photos, _ = ArchiveIndex.get().query_photos(group_posts=True)

    members = [rel for p in photos for rel in p["group_members"]]
    assert len(members) == files
    assert len(set(members)) == files
    assert sum(p["group_count"] for p in photos) == files


# ── GET /api/photos?group=post ───────────────────────────────────────
#
# Grouped, the route returns the slides *flat* with a post's members adjacent
# and in order, tagged `group_key` / `group_index` / `group_count`. The grid
# draws one tile per key; the lightbox is handed real photo rows, so favourite,
# verdict and prompt state keep working on a slide the grid never drew.
#
# The unit the client pages in is therefore neither `len(photos)` nor `total` —
# it is `rows`. That is the whole reason the field exists.


def test_api_is_ungrouped_by_default(api, make_photo):
    _carousel(make_photo, "ana", "post_a", 3)

    status, data = api("GET", "/api/photos")

    assert status == 200
    assert data["group"] == ""
    assert data["total"] == 3
    assert data["rows"] == 3 == len(data["photos"])
    assert "group_key" not in data["photos"][0]


def test_api_group_post_keeps_a_posts_slides_adjacent_and_ordered(api, make_photo):
    _carousel(make_photo, "ana", "post_a", 3)
    make_photo(creator="ana", name="solo.jpg", meta={"post_id": "post_b"})

    status, data = api("GET", "/api/photos?group=post&sort=name")

    assert status == 200
    assert data["group"] == "post"
    assert data["total"] == 2, "two posts"
    assert data["rows"] == 2, "two paging units consumed"
    assert len(data["photos"]) == 4, "every slide is still returned"

    slides = [p for p in data["photos"] if p["group_key"] == "ana/post_a"]
    assert [p["rel_path"] for p in slides] == [
        "ana/post_a_1.jpg",
        "ana/post_a_2.jpg",
        "ana/post_a_3.jpg",
    ]
    assert [p["group_index"] for p in slides] == [0, 1, 2]
    assert {p["group_count"] for p in slides} == {3}
    # Adjacency is what the grid uses to know where a tile ends.
    keys = [p["group_key"] for p in data["photos"]]
    assert keys == sorted(keys, key=keys.index)
    assert keys.count("ana/post_a") == 3


def test_api_group_slides_are_whole_photo_rows(api, make_photo):
    _carousel(make_photo, "ana", "post_a", 2)

    _, data = api("GET", "/api/photos?group=post")

    for slide in data["photos"]:
        assert slide["url"].startswith("/media/ana/")
        assert slide["thumb_url"]
        assert slide["creator"] == "ana"
        assert "has_prompt" in slide and "favorite" in slide
        assert "full_path" not in slide, "server-side paths never leave the box"


def test_api_group_paging_counts_groups_not_files(api, make_photo):
    files, groups = _seed_five_groups(make_photo)

    _, first = api("GET", "/api/photos?group=post&sort=name&limit=2&offset=0")
    assert first["total"] == groups
    assert first["rows"] == 2
    assert len(first["photos"]) == 6, "two 3-slide carousels"
    assert first["has_more"] is True

    _, last = api("GET", "/api/photos?group=post&sort=name&limit=2&offset=4")
    assert last["rows"] == 1
    assert last["has_more"] is False, (
        "has_more must compare group offsets against a group total"
    )


def test_api_group_paging_walks_every_file_exactly_once(api, make_photo):
    # The end-to-end version of the trap: page the way the sentinel does and
    # assert the archive came back whole. A row-counted total shows up here as
    # a missing or duplicated slide, which no single-page assertion catches.
    files, groups = _seed_five_groups(make_photo)

    seen, offset, guard = [], 0, 0
    while True:
        _, page = api(
            "GET", f"/api/photos?group=post&sort=name&limit=2&offset={offset}"
        )
        assert page["total"] == groups
        seen.extend(p["rel_path"] for p in page["photos"])
        offset += page["rows"]
        guard += 1
        assert guard < 10, "paging did not terminate"
        if not page["has_more"]:
            break

    assert len(seen) == files, seen
    assert len(set(seen)) == files, "a slide was served on two pages"


def test_api_rejects_an_unrecognised_group_value(api, make_photo):
    # Falling back to ungrouped would be worse than a 400: the client would
    # page in files while believing it was paging in posts.
    make_photo(creator="ana", name="a.jpg")

    status, _ = api("GET", "/api/photos?group=creator")

    assert status == 400
