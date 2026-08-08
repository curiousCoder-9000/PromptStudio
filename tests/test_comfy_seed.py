"""The seed used to generate an image must be the seed that gets recorded.

`build_pro_workflow` used to roll its own random seed when handed None. The
graph got a real value, the saved record got None, and the UI leaves the seed
lock off by default — so every generation made the normal way was
unreproducible. These tests pin the contract at both ends: the builders refuse
to invent a seed, and the value in the graph is the value in the record.
"""

import json
import os

import pytest

from promptstudio.comfy import client as comfy
from promptstudio.comfy.client import (
    PRO_NODE_FACE_DETAILER,
    PRO_NODE_SAMPLER,
    ComfyJobManager,
    build_pro_workflow,
    build_txt2img_workflow,
    resolve_seed,
)

# ── resolve_seed ─────────────────────────────────────────────────────


def test_resolve_seed_materialises_none():
    seed = resolve_seed(None)
    assert isinstance(seed, int)
    assert 0 <= seed < 2**32


def test_resolve_seed_passes_through_explicit_value():
    assert resolve_seed(1234) == 1234
    assert resolve_seed("777") == 777  # handler may hand over a JSON string


def test_resolve_seed_is_not_constant():
    # Guards against a "fix" that returns a fixed fallback.
    assert len({resolve_seed(None) for _ in range(20)}) > 1


# ── builders require a seed ──────────────────────────────────────────


def test_txt2img_builder_requires_a_seed():
    with pytest.raises(TypeError):
        build_txt2img_workflow("a", "b")  # type: ignore[call-arg]


def test_pro_builder_requires_a_seed():
    with pytest.raises(TypeError):
        build_pro_workflow(  # type: ignore[call-arg]
            image_name="x.jpg", positive="a", negative="b"
        )


def test_txt2img_builder_injects_the_given_seed():
    graph = build_txt2img_workflow("a", "b", seed=4242)
    assert graph["3"]["inputs"]["seed"] == 4242


def test_pro_builder_injects_the_given_seed_everywhere():
    graph = build_pro_workflow(
        image_name="ref.jpg", positive="a", negative="b", seed=4242
    )
    assert graph[PRO_NODE_SAMPLER]["inputs"]["seed"] == 4242
    # The FaceDetailer pass has its own seed input and must not diverge.
    if PRO_NODE_FACE_DETAILER in graph:
        fd = graph[PRO_NODE_FACE_DETAILER]["inputs"]
        if "seed" in fd:
            assert fd["seed"] == 4242


# ── end to end: graph seed == recorded seed ──────────────────────────


@pytest.fixture
def fake_comfy(monkeypatch, tmp_path):
    """Run a job without ComfyUI, capturing the graph that would be queued."""
    captured = {}

    def fake_upload(local_path, *, filename=None, overwrite=True):
        return "uploaded_ref.jpg"

    def fake_queue(self, workflow, client_id):
        captured["graph"] = workflow
        return "prompt-1"

    def fake_wait(self, prompt_id, timeout_sec=600):
        return [{"filename": "out.png", "subfolder": "", "type": "output"}]

    def fake_download(self, meta):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 32

    monkeypatch.setattr(comfy, "upload_image_to_comfy", fake_upload)
    monkeypatch.setattr(ComfyJobManager, "_queue_prompt", fake_queue)
    monkeypatch.setattr(ComfyJobManager, "_wait_for_images", fake_wait)
    monkeypatch.setattr(ComfyJobManager, "_download_image", fake_download)
    return captured


def _run_to_completion(manager, **kwargs):
    import time

    assert manager.start(**kwargs) is True
    for _ in range(200):
        if not manager.is_running():
            break
        time.sleep(0.01)
    assert not manager.is_running(), "job did not finish"
    return manager.get_status()


def _fresh_manager():
    # The singleton carries status across tests; a bare instance is isolated.
    return ComfyJobManager()


def test_unpinned_seed_is_recorded(make_photo, fake_comfy):
    rel, _ = make_photo(creator="seedtest", name="a.jpg")
    manager = _fresh_manager()

    status = _run_to_completion(
        manager,
        source_rel=rel,
        positive="a photo",
        negative="blurry",
        workflow="pro",
        seed=None,  # the default path — this is the case that was broken
    )

    assert status["error"] is None, status["error"]
    recorded = status["result"]["seed"]
    assert isinstance(recorded, int)

    graph_seed = fake_comfy["graph"][PRO_NODE_SAMPLER]["inputs"]["seed"]
    assert recorded == graph_seed, "recorded seed differs from the one rendered with"
    assert status["seed"] == recorded


def test_pinned_seed_is_honoured_and_recorded(make_photo, fake_comfy):
    rel, _ = make_photo(creator="seedtest", name="b.jpg")
    manager = _fresh_manager()

    status = _run_to_completion(
        manager,
        source_rel=rel,
        positive="a photo",
        negative="blurry",
        workflow="pro",
        seed=99887766,
    )

    assert status["result"]["seed"] == 99887766
    assert fake_comfy["graph"][PRO_NODE_SAMPLER]["inputs"]["seed"] == 99887766


def test_txt2img_seed_is_recorded(make_photo, fake_comfy):
    rel, _ = make_photo(creator="seedtest", name="c.jpg")
    manager = _fresh_manager()

    status = _run_to_completion(
        manager,
        source_rel=rel,
        positive="a photo",
        negative="blurry",
        workflow="txt2img",
        seed=None,
    )

    recorded = status["result"]["seed"]
    assert isinstance(recorded, int)
    assert recorded == fake_comfy["graph"]["3"]["inputs"]["seed"]


def test_seed_reaches_the_generations_index(make_photo, fake_comfy):
    """The record on disk — not just the in-memory status — carries the seed."""
    rel, _ = make_photo(creator="seedtest", name="d.jpg")
    manager = _fresh_manager()

    status = _run_to_completion(
        manager,
        source_rel=rel,
        positive="a photo",
        negative="blurry",
        workflow="pro",
        seed=None,
    )

    from promptstudio.config import GENERATIONS_INDEX_FILE

    assert os.path.isfile(GENERATIONS_INDEX_FILE)
    with open(GENERATIONS_INDEX_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    entries = data[rel]
    assert entries[0]["seed"] == status["result"]["seed"]
    assert isinstance(entries[0]["seed"], int)
