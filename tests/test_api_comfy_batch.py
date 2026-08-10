"""A2's three routes, plus a regression gate on the one-shot route beside them.

`/api/comfy/generate` had no Python coverage at all — its ninety lines of prompt
assembly were reachable only through the browser. Moving that logic into
`comfy/params.py` for A2 to share is exactly the kind of change that can break
a route silently, so success criterion 8 ("existing one-shot generate: behaviour
unchanged") gets assertions here rather than trust.
"""

import time

import pytest

from promptstudio.jobs import COMFY, LEASES
from promptstudio.prompts.cache import PromptCache


@pytest.fixture(autouse=True)
def _comfy_up(monkeypatch):
    """Pretend ComfyUI is reachable, and hand every test a clean lease table.

    The handler holds module-level singletons, and the api fixture's server
    shares this process, so state does leak between tests without this.
    """
    from promptstudio.server import handler

    monkeypatch.setattr(
        handler, "check_comfy_health", lambda *a, **k: {"comfy": True, "url": "x"}
    )
    LEASES.reset()
    handler._comfy_batch._status = handler._comfy_batch._idle_status()
    handler._comfy._status["running"] = False
    yield
    LEASES.reset()


def _with_prompt(make_photo, name="a.jpg", prompt="a woman"):
    rel, _ = make_photo(creator="nina", name=name)
    PromptCache().set(rel, {"positive_prompt": prompt}, push_history=False)
    return rel


def _drain():
    from promptstudio.server import handler

    for _ in range(600):
        if not handler._comfy_batch.is_running() and not handler._comfy.is_running():
            return
        time.sleep(0.01)
    raise AssertionError("a job did not finish")


# ── POST /api/comfy/batch ────────────────────────────────────────────


def test_batch_starts_and_reports_a_batch_id(api, make_photo, fake_comfy):
    rel = _with_prompt(make_photo)
    status, body = api("POST", "/api/comfy/batch", {"paths": [rel]})
    assert status == 200
    assert body["status"] == "started"
    assert body["batch_id"]
    assert body["pending"] == 1
    _drain()


def test_batch_reports_skips_rather_than_analyzing(api, make_photo, fake_comfy):
    rel = _with_prompt(make_photo)
    make_photo(creator="nina", name="unanalyzed.jpg")
    status, body = api(
        "POST", "/api/comfy/batch", {"paths": [rel, "nina/unanalyzed.jpg"]}
    )
    assert status == 200
    assert body["skipped_no_prompt"] == 1
    _drain()


def test_batch_with_no_targets_is_not_an_error(api, make_photo):
    make_photo(creator="nina", name="unanalyzed.jpg")
    status, body = api("POST", "/api/comfy/batch", {"creator": "nina"})
    assert status == 200
    assert body["status"] == "nothing_to_do"


def test_batch_is_409_when_comfyui_is_held(api, make_photo):
    rel = _with_prompt(make_photo)
    LEASES.acquire([COMFY], "a_one_shot_generate")
    status, body = api("POST", "/api/comfy/batch", {"paths": [rel]})
    assert status == 409
    assert body["status"] == "busy"
    assert "a_one_shot_generate" in body["message"]


def test_batch_is_503_when_comfyui_is_offline(api, make_photo, monkeypatch):
    from promptstudio.server import handler

    rel = _with_prompt(make_photo)
    monkeypatch.setattr(
        handler, "check_comfy_health", lambda *a, **k: {"comfy": False, "url": "x"}
    )
    status, body = api("POST", "/api/comfy/batch", {"paths": [rel]})
    assert status == 503
    assert body["status"] == "offline"


def test_batch_accepts_the_photos_filter_vocabulary(api, make_photo, fake_comfy):
    _with_prompt(make_photo, name="a.jpg")
    _with_prompt(make_photo, name="b.jpg")
    status, body = api("POST", "/api/comfy/batch", {"creator": "nina", "limit": 1})
    assert status == 200
    assert body["pending"] == 1
    _drain()


# ── GET /api/comfy/batch/status ──────────────────────────────────────


def test_status_is_idle_before_anything_runs(api):
    status, body = api("GET", "/api/comfy/batch/status")
    assert status == 200
    assert body["running"] is False
    assert body["total"] == 0


def test_status_survives_a_page_refresh(api, make_photo, fake_comfy):
    """Server-side status is the whole reason the chip comes back after F5."""
    rel = _with_prompt(make_photo)
    _, started = api("POST", "/api/comfy/batch", {"paths": [rel]})
    _drain()
    _, body = api("GET", "/api/comfy/batch/status")
    assert body["batch_id"] == started["batch_id"]
    assert body["completed"] == 1


# ── POST /api/comfy/batch/cancel ─────────────────────────────────────


def test_cancelling_nothing_says_idle(api):
    status, body = api("POST", "/api/comfy/batch/cancel")
    assert status == 200
    assert body["status"] == "idle"


def test_cancel_reports_cancelling_while_a_batch_runs(api, make_photo, fake_comfy):
    import threading

    from promptstudio.comfy.runner import ComfyRunner
    from promptstudio.server import handler

    for i in range(4):
        _with_prompt(make_photo, name=f"p{i}.jpg")

    entered, release = threading.Event(), threading.Event()
    real = ComfyRunner._wait_for_images

    def slow(self, prompt_id, timeout_sec=600):
        entered.set()
        release.wait(5)
        return real(self, prompt_id, timeout_sec)

    ComfyRunner._wait_for_images, saved = slow, ComfyRunner._wait_for_images
    try:
        assert api("POST", "/api/comfy/batch", {"creator": "nina"})[1]["status"] == (
            "started"
        )
        assert entered.wait(5)
        status, body = api("POST", "/api/comfy/batch/cancel")
        assert status == 200
        assert body["status"] == "cancelling"
        release.set()
        _drain()
    finally:
        ComfyRunner._wait_for_images = saved
    assert handler._comfy_batch.get_status()["cancelled"] is True


# ── regression: the one-shot route, unchanged (criterion 8) ──────────


def test_one_shot_generate_still_starts(api, make_photo, fake_comfy):
    rel = _with_prompt(make_photo)
    status, body = api("POST", "/api/comfy/generate", {"path": rel})
    assert status == 200
    assert body["status"] == "started"
    assert body["workflow"] == "pro"
    _drain()


def test_one_shot_generate_returns_the_resolved_seed(api, make_photo, fake_comfy):
    """The A0 defect: an unpinned seed used to come back None and be lost."""
    rel = _with_prompt(make_photo)
    _, body = api("POST", "/api/comfy/generate", {"path": rel})
    assert isinstance(body["seed"], int)
    _drain()


def test_one_shot_generate_honours_an_explicit_variant(api, make_photo, fake_comfy):
    rel = _with_prompt(make_photo)
    _, body = api("POST", "/api/comfy/generate", {"path": rel, "variant": "txt2img"})
    assert body["workflow"] == "txt2img"
    assert body["use_mode_e"] is False
    _drain()


def test_one_shot_generate_400s_without_a_path(api):
    status, _ = api("POST", "/api/comfy/generate", {})
    assert status == 400


def test_one_shot_generate_404s_for_an_unknown_photo(api):
    status, _ = api("POST", "/api/comfy/generate", {"path": "nobody/nothing.jpg"})
    assert status == 404


def test_one_shot_generate_400s_when_there_is_no_prompt(api, make_photo):
    """Mode E is off for txt2img, so this is the path that can actually be
    empty — and it must still say "generate one first" rather than render."""
    rel, _ = make_photo(creator="nina", name="raw.jpg")
    status, _ = api("POST", "/api/comfy/generate", {"path": rel, "variant": "txt2img"})
    assert status == 400


def test_one_shot_generate_is_409_when_comfy_is_held(api, make_photo):
    rel = _with_prompt(make_photo)
    LEASES.acquire([COMFY], "a_batch")
    status, body = api("POST", "/api/comfy/generate", {"path": rel})
    assert status == 409
    assert "a_batch" in body["message"]
