"""Following-list filtering and feed post ranking.

These decide which accounts get crawled and which posts get pulled first, so a
wrong branch here silently changes what the scraper spends its daily budget on.
"""

from types import SimpleNamespace

from promptstudio.scraping.filters import (
    entry_matches_keywords,
    filter_following_entries,
    normalize_keywords,
    score_instagram_post,
)


def entry(**kw):
    base = {
        "username": "someone",
        "full_name": "",
        "biography": "",
        "is_private": False,
        "media_count": 100,
    }
    base.update(kw)
    return base


# ── normalize_keywords ───────────────────────────────────────────────

def test_none_keywords_falls_back_to_config_defaults():
    assert normalize_keywords(None), "expected config bio keyword defaults"


def test_keywords_are_lowercased_and_stripped():
    assert normalize_keywords(["  Model ", "FASHION"]) == ["model", "fashion"]


def test_blank_keywords_dropped():
    assert normalize_keywords(["model", "", "   ", None]) == ["model"]


def test_empty_list_means_no_filter_not_defaults():
    # An explicit empty list is "match everything" — distinct from None
    assert normalize_keywords([]) == []


# ── entry_matches_keywords ───────────────────────────────────────────

def test_empty_keywords_matches_everything():
    assert entry_matches_keywords(entry(biography="anything"), []) is True


def test_matches_on_biography():
    assert entry_matches_keywords(entry(biography="pro Model in Paris"), ["model"]) is True


def test_matches_on_full_name():
    assert entry_matches_keywords(entry(full_name="Jane Fashion"), ["fashion"]) is True


def test_matches_on_username():
    assert entry_matches_keywords(entry(username="fitness_jane"), ["fitness"]) is True


def test_no_match_returns_false():
    assert entry_matches_keywords(entry(biography="software engineer"), ["model"]) is False


def test_match_is_case_insensitive():
    assert entry_matches_keywords(entry(biography="MODEL"), ["model"]) is True


def test_missing_fields_do_not_crash():
    assert entry_matches_keywords({}, ["model"]) is False


# ── filter_following_entries ─────────────────────────────────────────

def test_private_accounts_excluded_by_default():
    out = filter_following_entries([entry(is_private=True)], keywords=[])
    assert out == []


def test_private_accounts_kept_when_public_only_off():
    out = filter_following_entries([entry(is_private=True)], keywords=[], public_only=False)
    assert len(out) == 1


def test_low_media_count_excluded():
    out = filter_following_entries([entry(media_count=2)], keywords=[], min_media_count=5)
    assert out == []


def test_media_count_at_threshold_is_kept():
    out = filter_following_entries([entry(media_count=5)], keywords=[], min_media_count=5)
    assert len(out) == 1


def test_unknown_media_count_is_not_rejected():
    """None means the export was edge-only, not that the account is empty."""
    out = filter_following_entries([entry(media_count=None)], keywords=[], min_media_count=5)
    assert len(out) == 1


def test_keyword_filter_applies():
    entries = [
        entry(username="a", biography="fashion model"),
        entry(username="b", biography="backend developer"),
    ]
    out = filter_following_entries(entries, keywords=["model"])
    assert [e["username"] for e in out] == ["a"]


def test_filters_compose():
    entries = [
        entry(username="keep", biography="model", media_count=50),
        entry(username="private", biography="model", is_private=True),
        entry(username="tiny", biography="model", media_count=1),
        entry(username="offtopic", biography="chef", media_count=50),
    ]
    out = filter_following_entries(entries, keywords=["model"], min_media_count=5)
    assert [e["username"] for e in out] == ["keep"]


def test_empty_input_returns_empty():
    assert filter_following_entries([], keywords=["model"]) == []


# ── score_instagram_post ─────────────────────────────────────────────

def post(caption="", is_video=False, mediacount=1):
    return SimpleNamespace(caption=caption, is_video=is_video, mediacount=mediacount)


def test_keyword_hit_scores_above_no_hit():
    hit = score_instagram_post(post(caption="beach bikini shoot"), caption_keywords=["bikini"])
    miss = score_instagram_post(post(caption="my lunch"), caption_keywords=["bikini"])
    assert hit > miss


def test_more_keyword_hits_score_higher():
    one = score_instagram_post(post(caption="bikini"), caption_keywords=["bikini", "beach"])
    two = score_instagram_post(post(caption="bikini beach"), caption_keywords=["bikini", "beach"])
    assert two > one


def test_keyword_hits_have_diminishing_returns():
    kws = ["a1", "a2", "a3", "a4", "a5", "a6", "a7"]
    many = score_instagram_post(post(caption=" ".join(kws)), caption_keywords=kws)
    five = score_instagram_post(post(caption="a1 a2 a3 a4 a5"), caption_keywords=kws)
    # capped at first hit + 4 more
    assert many == five


def test_video_scores_higher_than_photo():
    v = score_instagram_post(post(is_video=True))
    p = score_instagram_post(post(is_video=False))
    assert v > p


def test_carousel_scores_higher_than_single():
    multi = score_instagram_post(post(mediacount=4))
    single = score_instagram_post(post(mediacount=1))
    assert multi > single


def test_later_feed_position_scores_slightly_lower():
    first = score_instagram_post(post(caption="x"), feed_index=0)
    later = score_instagram_post(post(caption="x"), feed_index=50)
    assert first > later


def test_recency_penalty_is_capped():
    a = score_instagram_post(post(), feed_index=200)
    b = score_instagram_post(post(), feed_index=9999)
    assert a == b


def test_missing_attributes_do_not_crash():
    assert isinstance(score_instagram_post(SimpleNamespace()), float)


def test_non_numeric_mediacount_is_tolerated():
    assert isinstance(score_instagram_post(post(mediacount="lots")), float)


def test_none_caption_is_tolerated():
    assert isinstance(score_instagram_post(post(caption=None)), float)
