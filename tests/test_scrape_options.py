"""One place decides what a scrape request means.

mode/deep/max_posts used to be re-derived at four layers — the enqueue route,
the one-shot sync route, `CreatorScrapeQueue.enqueue`, and
`SyncManager.try_drain_creator_queue`. Three of the four had their answer
overwritten by the next layer down, so no single site told you what a request
would do, and the copies could drift without anything failing.

The property that makes one implementation safe across those layers is
**idempotence**: the queue stores a normalized job, and the drain normalizes it
again. If those two disagreed, a job would run differently than it was queued.
"""

import pytest

from promptstudio.config import DEFAULT_MAX_POSTS_PER_CREATOR, FULL_SCRAPE_MAX_POSTS
from promptstudio.scraping.creator_queue import CreatorScrapeQueue
from promptstudio.scraping.sources.base import ScrapeOptions


@pytest.fixture
def queue(tmp_path):
    return CreatorScrapeQueue(path=str(tmp_path / "queue.json"))


# ── the latest → full+deep upgrade ──────────────────────────────────────

def test_latest_is_upgraded_to_a_deep_full_walk():
    """The documented product rule: never stop at the 50 newest missing posts.

    The old default left partial archives behind (the Mikayla / roxeuoon
    ceiling bug).
    """
    opts = ScrapeOptions.normalize("latest")
    assert opts.mode == "full"
    assert opts.deep is True
    assert opts.max_posts is None
    assert opts.upgraded_from_latest is True
    assert opts.requested_mode == "latest"


def test_catch_up_only_keeps_a_true_latest():
    opts = ScrapeOptions.normalize("latest", catch_up_only=True)
    assert opts.mode == "latest"
    assert opts.deep is False
    assert opts.max_posts == DEFAULT_MAX_POSTS_PER_CREATOR
    assert opts.upgraded_from_latest is False
    assert opts.catch_up_only is True


def test_catch_up_only_is_ignored_unless_the_request_said_latest():
    assert ScrapeOptions.normalize("full", catch_up_only=True).catch_up_only is False


# ── per-mode rules ──────────────────────────────────────────────────────

def test_bounded_is_never_deep():
    assert ScrapeOptions.normalize("bounded", deep=True).deep is False


def test_full_honours_the_deep_flag():
    assert ScrapeOptions.normalize("full", deep=True).deep is True
    assert ScrapeOptions.normalize("full", deep=False).deep is False


def test_deep_full_treats_a_non_positive_ceiling_as_unlimited():
    assert ScrapeOptions.normalize("full", deep=True, max_posts=0).max_posts is None


def test_an_explicit_ceiling_survives():
    assert ScrapeOptions.normalize("full", deep=True, max_posts=25).max_posts == 25


def test_unknown_mode_coerces_to_full():
    assert ScrapeOptions.normalize("sideways").mode == "full"
    assert ScrapeOptions.normalize(None).mode == "full"


def test_strict_rejects_an_unknown_mode():
    with pytest.raises(ValueError):
        ScrapeOptions.normalize("sideways", strict=True)


# ── runtime ceiling ─────────────────────────────────────────────────────

def test_resolved_ceiling_for_a_deep_full_scrape():
    assert (
        ScrapeOptions.normalize("full", deep=True).resolved_max_posts()
        == FULL_SCRAPE_MAX_POSTS
    )


def test_resolved_ceiling_for_bounded():
    assert (
        ScrapeOptions.normalize("bounded").resolved_max_posts()
        == DEFAULT_MAX_POSTS_PER_CREATOR
    )


def test_resolved_ceiling_prefers_an_explicit_value():
    assert ScrapeOptions.normalize("bounded", max_posts=7).resolved_max_posts() == 7


# ── idempotence: what the layering depends on ───────────────────────────

@pytest.mark.parametrize(
    ("mode", "deep", "max_posts", "catch_up_only"),
    [
        ("full", True, None, False),
        ("full", False, 30, False),
        ("full", True, 0, False),
        ("bounded", True, 10, False),
        ("latest", True, None, False),
        ("latest", True, None, True),
        ("latest", False, 5, True),
        ("nonsense", True, None, False),
    ],
)
def test_normalize_is_idempotent(mode, deep, max_posts, catch_up_only):
    once = ScrapeOptions.normalize(
        mode, deep=deep, max_posts=max_posts, catch_up_only=catch_up_only
    )
    twice = ScrapeOptions.normalize(
        once.mode,
        deep=once.deep,
        max_posts=once.max_posts,
        include_videos=once.include_videos,
        catch_up_only=once.catch_up_only,
    )
    assert (twice.mode, twice.deep, twice.max_posts) == (
        once.mode,
        once.deep,
        once.max_posts,
    )


# ── the queue stores what the drain will re-derive ──────────────────────

def test_queue_stores_the_normalized_job(queue):
    job = queue.enqueue("nina", mode="latest")["job"]
    assert job["mode"] == "full"
    assert job["deep"] is True
    assert job["max_posts"] is None
    assert job["upgraded_from_latest"] is True
    assert job["requested_mode"] == "latest"


def test_queue_stores_a_catch_up_job_unchanged(queue):
    job = queue.enqueue("nina", mode="latest", catch_up_only=True)["job"]
    assert job["mode"] == "latest"
    assert job["deep"] is False
    assert job["catch_up_only"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"mode": "latest"},
        {"mode": "latest", "catch_up_only": True},
        {"mode": "bounded", "max_posts": 10},
        {"mode": "full", "deep": False},
    ],
)
def test_drain_derives_exactly_what_the_queue_stored(queue, kwargs):
    """Enqueue-time and run-time must agree, or a job runs differently than queued."""
    stored = queue.enqueue("nina", **kwargs)["job"]

    redrived = ScrapeOptions.normalize(
        stored["mode"],
        deep=bool(stored["deep"]),
        max_posts=stored["max_posts"],
        include_videos=bool(stored["include_videos"]),
        catch_up_only=bool(stored["catch_up_only"]),
    )

    assert redrived.mode == stored["mode"]
    assert redrived.deep == stored["deep"]
    assert redrived.max_posts == stored["max_posts"]


def test_enqueue_still_rejects_an_invalid_mode(queue):
    with pytest.raises(ValueError):
        queue.enqueue("nina", mode="sideways")
