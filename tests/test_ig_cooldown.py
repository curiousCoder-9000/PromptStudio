"""Instagram automation cooldown: refuse scrapes until `until`."""

from datetime import datetime, timedelta, timezone

from promptstudio.config import SAVED_DIR, clamp_ig_posts, ig_posts_hard_cap
from promptstudio.scraping.ig_cooldown import (
    block_message,
    clear,
    engage,
    refuse_instagram_scrape,
    status,
)
from promptstudio.scraping.sources.base import ScrapeOptions, SourceContext
from promptstudio.scraping.sources.instagram_source import InstagramSource
from promptstudio.scraping.sync_manager import SyncManager


def test_no_file_means_not_active():
    clear()
    snap = status()
    assert snap["active"] is False
    assert block_message() is None
    assert refuse_instagram_scrape() is None


def test_engage_blocks_until_expiry(monkeypatch):
    clear()
    snap = engage(hours=2, reason="test warning")
    assert snap["active"] is True
    assert snap["remaining_sec"] > 3500
    msg = block_message()
    assert msg and "cooling down" in msg
    refused = refuse_instagram_scrape()
    assert refused is not None
    assert refused.stop_reason == "cooldown"
    assert refused.aborted is True


def test_expired_cooldown_is_inactive():
    from promptstudio.config import IG_COOLDOWN_FILE
    from promptstudio.storage.atomic import atomic_write_json

    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    atomic_write_json(
        IG_COOLDOWN_FILE,
        {"until": past, "reason": "expired", "set_at": past, "hours": 1},
    )
    snap = status()
    assert snap["active"] is False
    assert block_message() is None


def test_instagram_source_run_refuses_during_cooldown(monkeypatch):
    engage(hours=1, reason="unit test")
    seen = {}

    class FakeDL:
        def __init__(self, **_kw):
            seen["init"] = True

        def sync_creator_feed(self, username, **_kw):
            seen["user"] = username

    monkeypatch.setenv("IG_BACKEND", "instaloader")
    monkeypatch.setattr(
        "promptstudio.scraping.downloader.InstagramDownloader", FakeDL
    )
    src = InstagramSource()
    result = src.run(
        src.parse_target("nina"),
        ScrapeOptions(),
        SourceContext(save_dir=SAVED_DIR, log=lambda _m: None),
    )
    assert result.stop_reason == "cooldown"
    assert "user" not in seen
    clear()


def test_start_job_refuses_instagram_during_cooldown():
    engage(hours=1, reason="unit test")
    try:
        mgr = SyncManager.get()
        called = []

        def job(log, on_rate_limit=None):
            called.append(True)

        assert mgr.start_job("creator", job, source="instagram") is False
        assert "cooling down" in mgr.last_refusal
        assert called == []
    finally:
        clear()


def test_hard_cap_clamps_a_5000_request(monkeypatch):
    monkeypatch.setenv("IG_POSTS_HARD_CAP", "80")
    assert ig_posts_hard_cap() == 80
    assert clamp_ig_posts(5000) == 80
    assert clamp_ig_posts(24) == 24
    assert clamp_ig_posts(0) == 80
    monkeypatch.setenv("IG_POSTS_HARD_CAP", "0")
    assert clamp_ig_posts(5000) == 5000
