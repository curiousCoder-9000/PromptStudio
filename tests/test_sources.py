"""Multi-source scraping: target parsing, gallery-dl argv, metadata mapping."""

import json
import os

import pytest

from promptstudio.config import (
    SAVED_DIR,
    instagram_backend,
    instagram_cookies_info,
    resolve_gallery_dl_cmd,
)
from promptstudio.scraping.results import SyncResult
from promptstudio.scraping.sources import get_source, known_sources, source_info
from promptstudio.scraping.sources.base import (
    ScrapeOptions,
    SourceContext,
    resolve_folder_name,
)
from promptstudio.scraping.sources.gallery_dl_source import (
    InstagramGalleryDlSource,
    RedditSource,
    XSource,
    _parse_dt,
)
from promptstudio.scraping.sources.instagram_source import InstagramSource
from promptstudio.storage.metadata import build_metadata_from_normalized

# ── registry ────────────────────────────────────────────────────────────

def test_registry_exposes_the_three_supported_sources():
    assert set(known_sources()) == {"instagram", "x", "reddit"}
    assert {s["name"] for s in source_info()} == {"instagram", "x", "reddit"}


@pytest.mark.parametrize(
    "alias,expected",
    [
        ("ig", "instagram"),
        ("instagram", "instagram"),
        ("twitter", "x"),
        ("X", "x"),
        ("reddit", "reddit"),
    ],
)
def test_source_aliases_resolve(alias, expected):
    assert get_source(alias).name == expected


def test_unknown_source_rejected():
    with pytest.raises(ValueError, match="Unknown source"):
        get_source("tiktok")


# ── folder naming ───────────────────────────────────────────────────────

def test_instagram_folders_keep_bare_handle():
    """The existing archive is keyed on bare handles — this must not change."""
    assert resolve_folder_name("instagram", "roxeuoon") == "roxeuoon"
    assert resolve_folder_name("instagram", "@roxeuoon") == "roxeuoon"


def test_non_instagram_folders_are_suffixed():
    assert resolve_folder_name("x", "nina") == "nina__x"
    assert resolve_folder_name("reddit", "fashion", kind="r") == "r_fashion__reddit"


def test_same_handle_on_two_platforms_gets_two_folders():
    """Different people can share a handle across platforms; don't merge them."""
    assert resolve_folder_name("instagram", "nina") != resolve_folder_name("x", "nina")


def test_reddit_sub_and_user_namespaces_do_not_collide():
    assert resolve_folder_name("reddit", "bob", kind="r") != resolve_folder_name(
        "reddit", "bob", kind="u"
    )


def test_folder_names_survive_ensure_creator_folder_sanitizer():
    """resolve_folder_name must not produce a name ensure_creator_folder rewrites."""
    from promptstudio.storage.archive import ensure_creator_folder

    for source, handle, kind in (("x", "nina_k", ""), ("reddit", "street.wear", "r")):
        name = resolve_folder_name(source, handle, kind=kind)
        assert ensure_creator_folder(name)["name"] == name


# ── target parsing ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,folder,url",
    [
        ("nina_k", "nina_k__x", "https://x.com/nina_k/media"),
        ("@nina_k", "nina_k__x", "https://x.com/nina_k/media"),
        ("https://x.com/nina_k", "nina_k__x", "https://x.com/nina_k/media"),
        ("https://twitter.com/nina_k/media", "nina_k__x", "https://x.com/nina_k/media"),
    ],
)
def test_x_target_parsing(raw, folder, url):
    target = XSource().parse_target(raw)
    assert (target.folder, target.url, target.source) == (folder, url, "x")


@pytest.mark.parametrize(
    "raw,folder,url",
    [
        ("r/fashion", "r_fashion__reddit", "https://www.reddit.com/r/fashion/"),
        ("fashion", "r_fashion__reddit", "https://www.reddit.com/r/fashion/"),
        ("/r/fashion", "r_fashion__reddit", "https://www.reddit.com/r/fashion/"),
        ("u/bob", "u_bob__reddit", "https://www.reddit.com/user/bob/submitted/"),
        ("user/bob", "u_bob__reddit", "https://www.reddit.com/user/bob/submitted/"),
        (
            "https://www.reddit.com/r/streetwear/",
            "r_streetwear__reddit",
            "https://www.reddit.com/r/streetwear/",
        ),
    ],
)
def test_reddit_target_parsing(raw, folder, url):
    target = RedditSource().parse_target(raw)
    assert (target.folder, target.url, target.source) == (folder, url, "reddit")


@pytest.mark.parametrize("bad", ["", "   ", "@", "!!!", "https://x.com/"])
def test_invalid_targets_rejected(bad):
    for source in (XSource(), RedditSource()):
        with pytest.raises(ValueError):
            source.parse_target(bad)


# ── argv construction ───────────────────────────────────────────────────

def _argv(source, target_raw, options):
    target = source.parse_target(target_raw)
    return source._build_argv(target, options, "/tmp/dest"), target


def test_argv_sets_exact_directory_and_metadata():
    argv, target = _argv(XSource(), "nina", ScrapeOptions())
    assert "--directory" in argv
    assert argv[argv.index("--directory") + 1] == "/tmp/dest"
    assert "--write-metadata" in argv
    # Never --download-archive: ArchiveIndex + tombstones are the authority.
    assert "--download-archive" not in argv
    assert argv[-1] == target.url


def test_argv_filename_matches_archive_convention():
    """`taken_at_for_image` parses _YYYY-MM-DD_HH-MM-SS_UTC out of filenames."""
    argv, _ = _argv(XSource(), "nina", ScrapeOptions())
    fmt = argv[argv.index("--filename") + 1]
    assert fmt.startswith("nina__x_")
    assert "{date:%Y-%m-%d_%H-%M-%S}_UTC" in fmt
    assert fmt.endswith(".{extension}")


def test_argv_applies_pacing_flags():
    argv, _ = _argv(RedditSource(), "r/fashion", ScrapeOptions())
    for flag in ("--sleep", "--sleep-request", "--sleep-429", "--retries"):
        assert flag in argv, flag


def test_bounded_mode_sets_range_ceiling():
    argv, _ = _argv(XSource(), "nina", ScrapeOptions(mode="bounded", max_posts=25))
    assert argv[argv.index("--range") + 1] == "1-25"


def test_full_deep_scrape_has_no_ceiling_and_no_catch_up():
    """A true archive run must not stop early."""
    argv, _ = _argv(XSource(), "nina", ScrapeOptions(mode="full", deep=True))
    assert "--range" not in argv
    assert "--abort" not in argv


def test_catch_up_mode_sets_abort_streak():
    argv, _ = _argv(XSource(), "nina", ScrapeOptions(mode="full", deep=False))
    assert "--abort" in argv


def test_excluding_videos_adds_filter():
    argv, _ = _argv(XSource(), "nina", ScrapeOptions(include_videos=False))
    assert "--filter" in argv
    assert "mp4" in argv[argv.index("--filter") + 1]
    argv_with, _ = _argv(XSource(), "nina", ScrapeOptions(include_videos=True))
    assert "--filter" not in argv_with


# ── datetime parsing ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw",
    [
        "2026-03-04 11:22:33",          # gallery-dl's JSON datetime rendering
        "2026-03-04T11:22:33",
        "2026-03-04T11:22:33+00:00",
        1772623353,                      # reddit created_utc
        1772623353.0,
        "1772623353",
    ],
)
def test_parse_dt_handles_gallery_dl_shapes(raw):
    assert _parse_dt(raw) is not None


@pytest.mark.parametrize("raw", [None, "", "None", "null", "not-a-date"])
def test_parse_dt_rejects_junk(raw):
    assert _parse_dt(raw) is None


def test_parse_dt_always_returns_aware_datetimes():
    """Naive datetimes would break isoformat comparisons against IG's UTC values."""
    assert _parse_dt("2026-03-04 11:22:33").tzinfo is not None


# ── metadata mapping ────────────────────────────────────────────────────

# Key names verified against gallery_dl/extractor/twitter.py (1.32.9):
#   tweet_id/date/author/user/lang/favorite_count  -> _transform_tweet tdata
#   count = len(files)                             -> set per tweet
#   content = tweet text                           -> set after URL expansion
#   num    = enumerate(files, 1)                   -> 1-based
X_RAW = {
    "tweet_id": 1772623353000,
    "date": "2026-03-04 11:22:33",
    "content": "spring editorial shoot",
    "author": {"name": "nina_k", "nick": "Nina K"},
    "user": {"name": "nina_k"},
    "count": 3,
    "num": 2,
    "extension": "jpg",
    "favorite_count": 812,
    "category": "twitter",
}

# Key names verified against gallery_dl/extractor/reddit.py (1.32.9):
#   id/title/author/subreddit/score come from Reddit's own API JSON
#   date = parse_timestamp(created_utc); permalink is RELATIVE ("/r/...")
REDDIT_RAW = {
    "id": "1b8xyz",
    "title": "Street style, Milan",
    "author": "someuser",
    "subreddit": "streetwear",
    "date": "2026-03-04 11:22:33",
    "permalink": "/r/streetwear/comments/1b8xyz/street_style_milan/",
    "num": 1,
    "extension": "jpg",
    "score": 1420,
}


def test_x_mapping_populates_identity_and_url():
    target = XSource().parse_target("nina_k")
    post = XSource()._map_raw(X_RAW, target)
    assert post.source == "x"
    assert post.creator == "nina_k__x"        # folder, not raw handle
    assert post.post_id == "1772623353000"
    assert post.author == "nina_k"
    assert post.media_count == 3
    assert post.is_video is False
    assert post.post_url == "https://x.com/nina_k/status/1772623353000"
    assert post.taken_at is not None


def test_x_mapping_detects_video_from_extension():
    post = XSource()._map_raw(
        {**X_RAW, "extension": "mp4"}, XSource().parse_target("nina_k")
    )
    assert post.is_video is True


def test_reddit_mapping_keeps_submitter_separate_from_folder():
    """Subreddit is the folder; the human author must survive in `author`."""
    target = RedditSource().parse_target("r/streetwear")
    post = RedditSource()._map_raw(REDDIT_RAW, target)
    assert post.creator == "r_streetwear__reddit"
    assert post.author == "someuser"
    assert post.post_id == "1b8xyz"
    assert post.post_url == (
        "https://www.reddit.com/r/streetwear/comments/1b8xyz/street_style_milan/"
    )
    assert post.extra["subreddit"] == "streetwear"


def test_mapping_survives_empty_metadata():
    """A missing gallery-dl sidecar must not crash ingestion."""
    for source, raw_target in ((XSource(), "nina"), (RedditSource(), "r/fashion")):
        post = source._map_raw({}, source.parse_target(raw_target))
        assert post.source == source.name
        assert post.taken_at is None  # caller substitutes mtime


def test_sidecar_shape_matches_instagram_sidecar():
    """The gallery/prompt/classify layers read one format for every source."""
    from promptstudio.storage.metadata import build_metadata_from_post

    class FakeIGPost:
        mediaid = 123
        shortcode = "ABC"
        owner_username = "nina"
        caption = "hi"
        is_video = False

        class date_utc:
            @staticmethod
            def isoformat():
                return "2026-03-04T11:22:33"

    ig_keys = set(build_metadata_from_post(FakeIGPost()))
    x_keys = set(
        build_metadata_from_normalized(
            XSource()._map_raw(X_RAW, XSource().parse_target("nina_k"))
        )
    )
    assert ig_keys <= x_keys, f"missing from multi-source sidecar: {ig_keys - x_keys}"


def test_carousel_index_is_zero_based():
    """gallery-dl `num` is 1-based; the archive's carousel_index is 0-based."""
    src = XSource()
    assert src._carousel_index({"num": 1}) == 0
    assert src._carousel_index({"num": 4}) == 3
    assert src._carousel_index({}) == 0
    assert src._carousel_index({"num": "bad"}) == 0


def test_author_omitted_when_same_as_creator():
    from promptstudio.scraping.sources.base import NormalizedPost

    meta = build_metadata_from_normalized(
        NormalizedPost(source="x", creator="nina__x", post_id="1", author="nina__x")
    )
    assert "author" not in meta


# ── exit-status classification ──────────────────────────────────────────

def _classify(code, lines=(), options=None, downloaded=0, rate_hits=0):
    source = XSource()
    result = SyncResult(job_type="creator", source="x")
    result.downloaded = downloaded
    result.rate_limit_hits = rate_hits
    ctx = SourceContext(save_dir=SAVED_DIR, log=lambda _m: None)
    source._classify_outcome(
        code, list(lines), result, ctx, options=options or ScrapeOptions()
    )
    return result


def test_clean_exit_with_downloads_is_success():
    assert _classify(0, downloaded=5).stop_reason == "end_of_feed"


def test_clean_exit_with_nothing_new():
    assert _classify(0).stop_reason == "nothing_new"


def test_abort_streak_exits_zero_and_reads_as_catch_up():
    """`--abort N` raises StopExtraction, whose code is 0 — not an error."""
    result = _classify(
        0, downloaded=3, options=ScrapeOptions(mode="full", deep=False)
    )
    assert result.stop_reason == "catch_up"
    assert result.aborted is False


def test_auth_status_aborts_so_the_queue_pauses():
    """16 = Authentication/AuthorizationError. Every later job would fail too."""
    result = _classify(16)
    assert result.aborted is True
    assert result.stop_reason == "abort"


def test_challenge_status_aborts():
    """8 = ChallengeError — a captcha/bot check is the abuse signal."""
    assert _classify(8).aborted is True


def test_status_is_treated_as_a_bit_mask():
    """job.py does `status |= exc.code`, so 4|16 must still be seen as auth."""
    assert _classify(4 | 16).aborted is True
    assert _classify(20).aborted is True


def test_input_error_is_our_bug_and_does_not_pause_the_queue():
    """32 = bad filter/format/-o option. Pausing over our own bug is wrong."""
    result = _classify(32)
    assert result.aborted is False
    assert result.stop_reason == "error"


def test_unsupported_url_is_reported_as_our_bug():
    """64 = NoExtractorError: the URL we built isn't one gallery-dl handles."""
    result = _classify(64)
    assert result.aborted is False
    assert result.stop_reason == "error"
    assert "no extractor" in " ".join(result.messages).lower()


def test_extraction_error_refined_to_not_found():
    result = _classify(4, ["[twitter][error] 404 Not Found"])
    assert result.stop_reason == "not_found"


def test_extraction_error_refined_to_private():
    result = _classify(4, ["[twitter][error] account is protected"])
    assert result.stop_reason == "private"


def test_partial_download_with_error_is_not_a_failure():
    result = _classify(4, ["[twitter][error] boom"], downloaded=7)
    assert result.stop_reason == "end_of_feed"
    assert result.aborted is False


def test_rate_limited_with_nothing_downloaded_aborts():
    assert _classify(1, rate_hits=3, downloaded=0).aborted is True


def test_rate_limited_but_productive_does_not_abort():
    assert _classify(0, rate_hits=3, downloaded=10).aborted is False


# ── ingestion ───────────────────────────────────────────────────────────

def test_ingest_writes_sidecar_indexes_and_drops_raw_metadata():
    from PIL import Image

    from promptstudio.storage.db import ArchiveIndex
    from promptstudio.storage.metadata import load_post_metadata

    source = XSource()
    target = source.parse_target("nina_k")
    folder = os.path.join(SAVED_DIR, target.folder)
    os.makedirs(folder, exist_ok=True)

    name = "nina_k__x_2026-03-04_11-22-33_UTC_02.jpg"
    full = os.path.join(folder, name)
    Image.new("RGB", (32, 32), (10, 20, 30)).save(full, "JPEG")
    with open(full + ".json", "w", encoding="utf-8") as fh:
        json.dump(X_RAW, fh)

    ctx = SourceContext(save_dir=SAVED_DIR, log=lambda _m: None)
    converted, errors, _newest = source._ingest([name], target, SAVED_DIR, ctx)

    assert (converted, errors) == (1, 0)
    # gallery-dl's raw sidecar is consumed, ours replaces it
    assert not os.path.exists(full + ".json")
    meta = load_post_metadata(full)
    assert meta["source"] == "x"
    assert meta["post_id"] == "1772623353000"
    assert meta["carousel_index"] == 1
    assert meta["author"] == "nina_k"
    # ...and the row is indexed under the right platform
    rel = f"{target.folder}/{name}"
    assert ArchiveIndex.get().get_photo_source(rel) == "x"


# ── resume checkpoints (docs/design_source_filter.md §6) ────────────────

def _run_ingest_for(source, handle, name, raw):
    """Ingest one fake file for `handle` and return its SourceTarget."""
    from PIL import Image

    target = source.parse_target(handle)
    folder = os.path.join(SAVED_DIR, target.folder)
    os.makedirs(folder, exist_ok=True)
    full = os.path.join(folder, name)
    Image.new("RGB", (16, 16), (4, 5, 6)).save(full, "JPEG")
    with open(full + ".json", "w", encoding="utf-8") as fh:
        json.dump(raw, fh)
    ctx = SourceContext(save_dir=SAVED_DIR, log=lambda _m: None)
    converted, _errors, newest = source._ingest([name], target, SAVED_DIR, ctx)
    source._record_checkpoint(target, converted, newest)
    return target


def test_gallery_dl_run_writes_a_folder_keyed_checkpoint():
    """Without this, X and Reddit creators show no sync badge at all."""
    from promptstudio.scraping.checkpoints import SyncCheckpoints

    target = _run_ingest_for(XSource(), "nina_k", "x_ck_1.jpg", X_RAW)

    state = SyncCheckpoints().load()
    assert target.folder == "nina_k__x"
    assert target.folder in state, "checkpoint must be keyed on the archive folder"
    assert state[target.folder]["downloaded_count"] == 1
    assert state[target.folder]["last_post_id"] == "1772623353000"
    assert state[target.folder]["updated_at"]


def test_same_handle_on_two_platforms_keeps_separate_checkpoints():
    """Folder keying is what stops @nina on IG overwriting @nina on X."""
    from promptstudio.scraping.checkpoints import SyncCheckpoints

    # Instagram writes under the bare handle (folder == handle there).
    SyncCheckpoints().update("nina_k", shortcode="IGSHORT", post_id="111")
    _run_ingest_for(XSource(), "nina_k", "x_ck_2.jpg", X_RAW)

    state = SyncCheckpoints().load()
    assert state["nina_k"]["last_post_id"] == "111"
    assert state["nina_k__x"]["last_post_id"] == "1772623353000"


def test_checkpoint_is_not_written_when_nothing_downloaded():
    from promptstudio.scraping.checkpoints import SyncCheckpoints

    source = XSource()
    target = source.parse_target("nina_k")
    source._record_checkpoint(target, 0, {})
    assert target.folder not in SyncCheckpoints().load()


def test_checkpoint_update_is_atomic_under_concurrent_writers():
    """Per-source lanes make this reachable — see docs/design_scrape_lanes.md §6."""
    import threading

    from promptstudio.scraping.checkpoints import SyncCheckpoints

    ck = SyncCheckpoints()
    names = [f"creator_{i}" for i in range(12)]

    def write(handle):
        ck.update(handle, shortcode=handle, post_id=handle, downloaded_delta=1)

    threads = [threading.Thread(target=write, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    state = ck.load()
    missing = [n for n in names if n not in state]
    assert not missing, f"lost updates for {missing}"


def test_null_date_sidecar_is_handled():
    """gallery-dl's missing-date sentinel (util.NONE) serializes to JSON null."""
    post = XSource()._map_raw(
        {**X_RAW, "date": None}, XSource().parse_target("nina_k")
    )
    assert post.taken_at is None


def test_ingest_substitutes_mtime_when_extractor_gave_no_date():
    from PIL import Image

    from promptstudio.storage.metadata import load_post_metadata

    source = RedditSource()
    target = source.parse_target("r/fashion")
    folder = os.path.join(SAVED_DIR, target.folder)
    os.makedirs(folder, exist_ok=True)
    name = "no_date.jpg"
    full = os.path.join(folder, name)
    Image.new("RGB", (16, 16), (1, 2, 3)).save(full, "JPEG")
    with open(full + ".json", "w", encoding="utf-8") as fh:
        json.dump({"id": "abc", "extension": "jpg"}, fh)

    ctx = SourceContext(save_dir=SAVED_DIR, log=lambda _m: None)
    converted, _errors, _newest = source._ingest([name], target, SAVED_DIR, ctx)
    assert converted == 1
    # Empty taken_at would sort this to the top of the gallery forever.
    assert load_post_metadata(full)["taken_at"]


def test_resolve_gallery_dl_cmd_default_is_runnable():
    """pip --user on Windows leaves gallery-dl.exe off PATH; we still find it."""
    cmd = resolve_gallery_dl_cmd("gallery-dl")
    assert cmd
    assert cmd[0] != "gallery-dl" or os.path.isfile(cmd[0])
    joined = " ".join(cmd)
    assert "gallery-dl" in joined or "gallery_dl" in joined


def test_resolve_gallery_dl_cmd_custom_name_is_literal():
    assert resolve_gallery_dl_cmd("definitely-not-installed-xyz") == [
        "definitely-not-installed-xyz"
    ]


def test_missing_gallery_dl_binary_reports_cleanly(monkeypatch):
    """A missing binary must be an actionable message, not a traceback."""
    monkeypatch.setattr(
        "promptstudio.scraping.sources.gallery_dl_source.GALLERY_DL_BIN",
        "definitely-not-installed-xyz",
    )
    source = XSource()
    result = source.run(
        source.parse_target("nina"),
        ScrapeOptions(),
        SourceContext(save_dir=SAVED_DIR, log=lambda _m: None),
    )
    assert result.stop_reason == "error"
    assert result.errors == 1
    assert "pip install gallery-dl" in " ".join(result.messages)


# ── Instagram gallery-dl backend ────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", "instaloader"),
        ("instaloader", "instaloader"),
        ("il", "instaloader"),
        ("gallery-dl", "gallery-dl"),
        ("gdl", "gallery-dl"),
        ("gallerydl", "gallery-dl"),
        ("nope", "instaloader"),
    ],
)
def test_instagram_backend_aliases(monkeypatch, raw, expected):
    if raw:
        monkeypatch.setenv("IG_BACKEND", raw)
    else:
        monkeypatch.setenv("IG_BACKEND", "")
    assert instagram_backend() == expected


def test_instagram_still_not_a_fourth_registry_source():
    assert "instagram-gdl" not in known_sources()
    assert InstagramGalleryDlSource().name == "instagram"


def _ig_argv(options=None, dest="/tmp/dest", handle="borabit1004"):
    src = InstagramGalleryDlSource()
    target = src.parse_target(handle)
    return src._build_argv(target, options or ScrapeOptions.normalize("full", deep=True), dest), target


def test_ig_gdl_argv_pins_search_web_and_never_web_profile_info(monkeypatch):
    monkeypatch.setenv("SCRAPE_COOKIES_FROM_BROWSER", "brave")
    monkeypatch.delenv("IG_GDL_SLEEP_REQUEST", raising=False)
    argv, target = _ig_argv()
    blob = " ".join(argv)
    assert "extractor.instagram.user-strategy=search,web" in blob
    assert "web_profile_info" not in blob
    assert "user-strategy=info" not in blob
    assert "extractor.instagram.include=posts" in blob
    assert "--cookies-from-browser" in argv
    assert argv[argv.index("--cookies-from-browser") + 1] == "brave"
    assert "--range" not in argv
    assert float(argv[argv.index("--sleep-request") + 1]) >= 6.0
    assert argv[-1] == "https://www.instagram.com/borabit1004/"
    assert target.folder == "borabit1004"


def test_ig_gdl_cookies_file_wins_over_browser(monkeypatch, tmp_path):
    cookies = tmp_path / "ig-cookies.txt"
    cookies.write_text("# Netscape\n", encoding="utf-8")
    monkeypatch.setenv("IG_COOKIES_FILE", str(cookies))
    monkeypatch.setenv("SCRAPE_COOKIES_FROM_BROWSER", "brave")
    argv, _ = _ig_argv()
    assert "--cookies" in argv
    assert argv[argv.index("--cookies") + 1] == str(cookies)
    assert "--cookies-from-browser" not in argv


def test_ig_gdl_missing_cookies_fails_without_spawning(monkeypatch):
    monkeypatch.setenv("IG_COOKIES_FILE", "")
    monkeypatch.setenv("SCRAPE_COOKIES_FROM_BROWSER", "")
    src = InstagramGalleryDlSource()
    result = src.run(
        src.parse_target("nina"),
        ScrapeOptions(),
        SourceContext(save_dir=SAVED_DIR, log=lambda _m: None),
    )
    assert result.stop_reason == "error"
    assert result.errors == 1
    joined = " ".join(result.messages)
    assert "IG_COOKIES_FILE" in joined
    assert "SCRAPE_COOKIES_FROM_BROWSER" in joined


def test_ig_gdl_saved_url_uses_session_user():
    target = InstagramGalleryDlSource().parse_saved_target("archi")
    assert target.kind == "saved"
    assert target.url == "https://www.instagram.com/archi/saved/"
    argv = InstagramGalleryDlSource()._build_argv(
        target, ScrapeOptions.normalize("full", deep=True), SAVED_DIR
    )
    assert "--base-directory" in argv
    assert "--directory" not in argv
    assert argv[-1].endswith("/archi/saved/")


IG_RAW = {
    "post_id": 1234567890,
    "post_shortcode": "AbCdefGh",
    "date": "2026-03-04 11:22:33",
    "description": "editorial in milan",
    "username": "borabit1004",
    "count": 2,
    "num": 1,
    "extension": "jpg",
    "likes": 44,
}


def test_ig_gdl_mapping_matches_instagram_sidecar_shape():
    src = InstagramGalleryDlSource()
    target = src.parse_target("borabit1004")
    post = src._map_raw(IG_RAW, target)
    assert post.source == "instagram"
    assert post.creator == "borabit1004"
    assert post.post_id == "1234567890"
    assert post.shortcode == "AbCdefGh"
    assert post.post_url == "https://www.instagram.com/p/AbCdefGh/"
    assert post.caption == "editorial in milan"
    meta = build_metadata_from_normalized(post)
    assert meta["source"] == "instagram"
    assert meta["owner_username"] == "borabit1004"
    assert "shortcode" in meta and "post_id" in meta


def test_ig_gdl_caption_object_becomes_text():
    src = InstagramGalleryDlSource()
    post = src._map_raw(
        {**IG_RAW, "description": "", "caption": {"text": "from object"}},
        src.parse_target("borabit1004"),
    )
    assert post.caption == "from object"


def test_ig_gdl_tombstoned_post_is_unlinked_not_indexed():
    from PIL import Image

    from promptstudio.storage.db import ArchiveIndex
    from promptstudio.storage.metadata import load_post_metadata

    src = InstagramGalleryDlSource()
    target = src.parse_target("borabit1004")
    folder = os.path.join(SAVED_DIR, target.folder)
    os.makedirs(folder, exist_ok=True)
    name = "borabit1004_2026-03-04_11-22-33_UTC_01.jpg"
    full = os.path.join(folder, name)
    Image.new("RGB", (16, 16), (8, 8, 8)).save(full, "JPEG")
    with open(full + ".json", "w", encoding="utf-8") as fh:
        json.dump(IG_RAW, fh)

    ArchiveIndex.get().record_deleted_post(
        "borabit1004", post_id="1234567890", platform="instagram"
    )
    ctx = SourceContext(save_dir=SAVED_DIR, log=lambda _m: None)
    converted, errors, _newest = src._ingest([name], target, SAVED_DIR, ctx)
    assert (converted, errors) == (0, 0)
    assert not os.path.exists(full)
    assert load_post_metadata(full) is None


def test_instagram_source_default_uses_instaloader(monkeypatch):
    monkeypatch.setenv("IG_BACKEND", "instaloader")
    seen = {}

    class FakeDL:
        def __init__(self, **_kw):
            seen["init"] = True

        def sync_creator_feed(self, username, **_kw):
            seen["user"] = username
            result = SyncResult(job_type="creator")
            result.stop_reason = "nothing_new"
            return result

    monkeypatch.setattr(
        "promptstudio.scraping.downloader.InstagramDownloader", FakeDL
    )
    src = InstagramSource()
    result = src.run(
        src.parse_target("nina"),
        ScrapeOptions(),
        SourceContext(save_dir=SAVED_DIR, log=lambda _m: None),
    )
    assert seen.get("user") == "nina"
    assert result.source == "instagram"


def test_instagram_source_dispatches_to_gallery_dl(monkeypatch):
    monkeypatch.setenv("IG_BACKEND", "gallery-dl")
    monkeypatch.setenv("SCRAPE_COOKIES_FROM_BROWSER", "brave")
    seen = {}

    def fake_run(self, target, options, ctx):
        seen["url"] = target.url
        result = SyncResult(job_type="creator", source="instagram")
        result.stop_reason = "nothing_new"
        return result

    monkeypatch.setattr(
        "promptstudio.scraping.sources.gallery_dl_source.InstagramGalleryDlSource.run",
        fake_run,
    )
    src = InstagramSource()
    result = src.run(
        src.parse_target("nina"),
        ScrapeOptions(),
        SourceContext(save_dir=SAVED_DIR, log=lambda _m: None),
    )
    assert seen.get("url") == "https://www.instagram.com/nina/"
    assert result.source == "instagram"


def test_instagram_cookies_info_browser_mode(monkeypatch):
    monkeypatch.setenv("IG_COOKIES_FILE", "")
    monkeypatch.setenv("SCRAPE_COOKIES_FROM_BROWSER", "brave")
    info = instagram_cookies_info()
    assert info == {"mode": "browser", "browser": "brave", "ready": True}
