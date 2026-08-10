"""The seed used to generate an image must be the seed that gets recorded.

`build_pro_workflow` used to roll its own random seed when handed None. The
graph got a real value, the saved record got None, and the UI leaves the seed
lock off by default — so every generation made the normal way was
unreproducible. These tests pin the contract at both ends: the builder refuses
to invent a seed, and the value in the graph is the value in the record.

Since A4 the builder is `registry.build_graph` and the node ids come from the
slot map rather than from `PRO_NODE_*` constants — the assertions below read
them out of the registry so a re-slotted graph cannot make this file lie.
"""

import json
import os

import pytest

from promptstudio.comfy import registry
from promptstudio.comfy.client import resolve_seed
from promptstudio.comfy.params import GenerationParams


def _pro_seed_nodes():
    return [ref.node for ref in registry.get_workflow("pro").slots["seed"]]


PRO_SAMPLER = "9"


def _params(workflow="pro", **over):
    base = dict(
        rel_path="creator/photo.jpg",
        positive="a",
        negative="b",
        workflow=workflow,
        variant=workflow,
        aspect="4:5",
        steps=32,
        cfg=6.0,
        denoise=0.7 if workflow == "pro" else None,
        seed=None,
        checkpoint=None,
        mode_e=False,
        prompt_version=None,
    )
    base.update(over)
    return GenerationParams(**base)


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


# ── the builder requires a seed ──────────────────────────────────────


@pytest.mark.parametrize("workflow", ["pro", "txt2img"])
def test_the_builder_requires_a_seed(workflow):
    with pytest.raises(TypeError):
        registry.build_graph(  # type: ignore[call-arg]
            workflow, _params(workflow), image_name="x.jpg"
        )


def test_txt2img_builder_injects_the_given_seed():
    graph = registry.build_graph("txt2img", _params("txt2img"), seed=4242)
    assert graph["3"]["inputs"]["seed"] == 4242


def test_pro_builder_injects_the_given_seed_everywhere():
    graph = registry.build_graph(
        "pro", _params(), seed=4242, image_name="ref.jpg", filename_prefix="p"
    )
    # Every node the slot map points at, not just the sampler: the FaceDetailer
    # pass has its own seed input and must not diverge.
    nodes = _pro_seed_nodes()
    assert len(nodes) > 1, "the multi-node seed case is what this test is for"
    for node in nodes:
        assert graph[node]["inputs"]["seed"] == 4242


# ── end to end: graph seed == recorded seed ──────────────────────────


def test_unpinned_seed_is_recorded(make_photo, fake_comfy, run_comfy_job):
    rel, _ = make_photo(creator="seedtest", name="a.jpg")
    status = run_comfy_job(
        source_rel=rel,
        positive="a photo",
        negative="blurry",
        workflow="pro",
        seed=None,  # the default path — this is the case that was broken
    )

    assert status["error"] is None, status["error"]
    recorded = status["result"]["seed"]
    assert isinstance(recorded, int)

    graph_seed = fake_comfy["graph"][PRO_SAMPLER]["inputs"]["seed"]
    assert recorded == graph_seed, "recorded seed differs from the one rendered with"
    assert status["seed"] == recorded


def test_pinned_seed_is_honoured_and_recorded(make_photo, fake_comfy, run_comfy_job):
    rel, _ = make_photo(creator="seedtest", name="b.jpg")
    status = run_comfy_job(
        source_rel=rel,
        positive="a photo",
        negative="blurry",
        workflow="pro",
        seed=99887766,
    )

    assert status["result"]["seed"] == 99887766
    assert fake_comfy["graph"][PRO_SAMPLER]["inputs"]["seed"] == 99887766


def test_txt2img_seed_is_recorded(make_photo, fake_comfy, run_comfy_job):
    rel, _ = make_photo(creator="seedtest", name="c.jpg")
    status = run_comfy_job(
        source_rel=rel,
        positive="a photo",
        negative="blurry",
        workflow="txt2img",
        seed=None,
    )

    recorded = status["result"]["seed"]
    assert isinstance(recorded, int)
    assert recorded == fake_comfy["graph"]["3"]["inputs"]["seed"]


def test_seed_reaches_the_generations_index(make_photo, fake_comfy, run_comfy_job):
    """The record on disk — not just the in-memory status — carries the seed."""
    rel, _ = make_photo(creator="seedtest", name="d.jpg")
    status = run_comfy_job(
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
