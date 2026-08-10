"""A4 — workflows are data, and the data must reproduce the code it replaced.

**The gate** (design_generation_loop.md success criterion 9) is byte-identity:
the graph the registry builds for `pro` must equal, key for key and type for
type, what the hand-written `build_pro_workflow` injector produced for the same
inputs. Anything less and A4 is a rewrite of the generation path dressed up as
a refactor.

The comparison was first written against the live legacy builders and watched
to fail, then to pass. `tests/fixtures/comfy_graph_*.json` are those builders'
output, frozen at that moment — so the gate survived the deletion of the code
it was checking, which is what §4 asks for ("a regression fixture, not a
judgement"). Regenerating a fixture to make a test pass is therefore always the
wrong move: it is the only remaining record of the behaviour.
"""

import copy
import json
import os

import pytest

from promptstudio.comfy import registry
from promptstudio.comfy.params import GenerationParams
from promptstudio.comfy.registry import UnknownWorkflowError, WorkflowError
from promptstudio.config import COMFY_WORKFLOWS_DIR

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _golden(name):
    with open(os.path.join(FIXTURES, f"comfy_graph_{name}.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def _params(**over):
    base = dict(
        rel_path="creator/photo_1.jpg",
        positive="POS text",
        negative="NEG text",
        workflow="pro",
        variant="pro",
        aspect="4:5",
        steps=41,
        cfg=5.5,
        denoise=0.63,
        seed=None,
        checkpoint=None,
        mode_e=True,
        prompt_version="engine-x",
    )
    base.update(over)
    return GenerationParams(**base)


def _txt_params(**over):
    return _params(
        workflow="txt2img",
        variant="sdxl",
        steps=30,
        cfg=7.0,
        denoise=None,
        checkpoint="pinned_ckpt.safetensors",
        **over,
    )


def _canon(graph):
    return json.dumps(graph, sort_keys=True, indent=1)


def _build_pro(**over):
    kwargs = dict(
        seed=777333,
        image_name="ps_creator_photo_1.jpg",
        filename_prefix="promptstudio/creator/photo_1",
    )
    params = over.pop("params", None) or _params()
    kwargs.update(over)
    return registry.build_graph("pro", params, **kwargs)


def _write_workflow(name, slots_doc, graph, *, root=None):
    """Drop a workflow into the user registry root the way an import would."""
    directory = os.path.join(root or COMFY_WORKFLOWS_DIR, name)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "slots.json"), "w", encoding="utf-8") as f:
        json.dump(slots_doc, f)
    if graph is not None:
        with open(os.path.join(directory, "graph.json"), "w", encoding="utf-8") as f:
            json.dump(graph, f)
    return directory


MINI_GRAPH = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "a.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}},
    "3": {"class_type": "KSampler", "inputs": {"seed": 0, "steps": 20, "cfg": 8.0}},
}
MINI_SLOTS = {
    "name": "mini",
    "label": "Mini",
    "kind": "txt2img",
    "slots": {
        "positive": {"node": "2", "field": "text"},
        "seed": {"node": "3", "field": "seed"},
    },
}


# ── THE GATE ─────────────────────────────────────────────────────────


def test_registry_pro_graph_is_byte_identical_to_the_deleted_builder():
    assert _canon(_build_pro()) == _canon(_golden("pro"))


def test_registry_txt2img_graph_is_byte_identical_to_the_deleted_builder():
    built = registry.build_graph(
        "txt2img", _txt_params(), seed=4242, checkpoint="pinned_ckpt.safetensors"
    )
    assert _canon(built) == _canon(_golden("txt2img"))


def test_an_unset_checkpoint_leaves_the_export_alone_and_a_set_one_changes_only_that():
    """`checkpoint=None` must mean "keep the graph's own", not "blank it"."""
    override = _build_pro(params=_params(checkpoint="someOther_ckpt.safetensors"))
    expected = copy.deepcopy(_golden("pro"))
    assert expected["1"]["inputs"]["ckpt_name"] != "someOther_ckpt.safetensors"
    expected["1"]["inputs"]["ckpt_name"] = "someOther_ckpt.safetensors"
    assert _canon(override) == _canon(expected)


def test_the_aspect_ratio_reaches_the_latent_and_nothing_else():
    """txt2img has no width/height parameter — it has an aspect, and the slot
    map is where that turns into two integers."""
    wide = registry.build_graph(
        "txt2img",
        _txt_params(aspect="16:9"),
        seed=4242,
        checkpoint="pinned_ckpt.safetensors",
    )
    expected = copy.deepcopy(_golden("txt2img"))
    assert (expected["5"]["inputs"]["width"], expected["5"]["inputs"]["height"]) == (816, 1024)
    expected["5"]["inputs"]["width"] = 1024
    expected["5"]["inputs"]["height"] = 576
    assert _canon(wide) == _canon(expected)


def test_the_seed_list_form_replaces_the_face_detailer_special_case():
    """`pro` declares seed at nodes 9 and 22. The old injector had an `if
    PRO_NODE_FACE_DETAILER in workflow` for exactly this and nothing else."""
    graph = _build_pro(seed=131313)
    assert graph["9"]["inputs"]["seed"] == 131313
    assert graph["22"]["inputs"]["seed"] == 131313
    spec = registry.get_workflow("pro")
    assert [r.node for r in spec.slots["seed"]] == ["9", "22"]


def test_building_twice_does_not_leak_state_between_graphs():
    first = _build_pro(seed=1)
    first["9"]["inputs"]["steps"] = 999
    second = _build_pro(seed=1)
    assert second["9"]["inputs"]["steps"] == 41


# ── the built-in entries ─────────────────────────────────────────────


def test_pro_and_txt2img_are_registry_entries_not_code():
    names = {spec.name: spec for spec in registry.list_workflows()}
    assert {"pro", "txt2img"} <= set(names)
    assert names["pro"].kind == "img2img"
    assert names["txt2img"].kind == "txt2img"
    assert names["pro"].label and names["txt2img"].label
    assert names["pro"].builtin and names["txt2img"].builtin


def test_only_an_img2img_workflow_needs_a_reference_upload():
    assert registry.get_workflow("pro").needs_image is True
    assert registry.get_workflow("txt2img").needs_image is False


def test_summary_is_what_the_picker_gets_and_no_node_ids():
    summary = registry.get_workflow("pro").summary()
    assert set(summary) == {"name", "label", "kind"}
    assert "9" not in json.dumps(summary)


# ── user workflows ───────────────────────────────────────────────────


def test_a_user_workflow_is_discovered_alongside_the_built_ins():
    _write_workflow("mini", MINI_SLOTS, MINI_GRAPH)
    names = [spec.name for spec in registry.list_workflows()]
    assert names == sorted(names), "list must be stable for a picker"
    assert "mini" in names
    spec = registry.get_workflow("mini")
    assert spec.builtin is False
    assert spec.label == "Mini"


def test_a_user_workflow_shadows_a_built_in_of_the_same_name():
    shadow = dict(MINI_SLOTS, name="txt2img", label="My own txt2img")
    _write_workflow("txt2img", shadow, MINI_GRAPH)
    spec = registry.get_workflow("txt2img")
    assert spec.label == "My own txt2img"
    assert spec.builtin is False
    assert [s.name for s in registry.list_workflows()].count("txt2img") == 1


def test_one_broken_workflow_does_not_take_the_picker_down():
    _write_workflow("broken", dict(MINI_SLOTS, name="broken", kind="sideways"), MINI_GRAPH)
    _write_workflow("mini", MINI_SLOTS, MINI_GRAPH)
    names = [spec.name for spec in registry.list_workflows()]
    assert "mini" in names and "pro" in names
    assert "broken" not in names
    # ...but asking for it by name still says exactly what is wrong with it.
    with pytest.raises(WorkflowError, match="sideways"):
        registry.get_workflow("broken")


# ── validation names the fault, never "invalid workflow" ─────────────


def test_unknown_workflow_lists_what_is_available():
    with pytest.raises(UnknownWorkflowError) as err:
        registry.get_workflow("flux_ref")
    assert "flux_ref" in str(err.value)
    assert "pro" in str(err.value)


@pytest.mark.parametrize("name", ["", "   "])
def test_a_blank_workflow_name_is_unknown_not_a_crash(name):
    with pytest.raises(UnknownWorkflowError):
        registry.get_workflow(name)


@pytest.mark.parametrize(
    "name",
    ["../secrets", "..", "a/b", "a\\b", "/etc", "./pro"],
    ids=["parent", "dotdot", "slash", "backslash", "absolute", "dotslash"],
)
def test_a_workflow_name_is_a_directory_name_not_a_path(name):
    """`workflow` arrives straight off an HTTP body and is joined onto the
    registry root. A name with a separator in it would read files outside it —
    the same containment rule the rest of the archive is held to."""
    with pytest.raises(UnknownWorkflowError):
        registry.get_workflow(name)


def test_a_missing_required_slot_is_named():
    doc = copy.deepcopy(MINI_SLOTS)
    del doc["slots"]["positive"]
    _write_workflow("mini", doc, MINI_GRAPH)
    with pytest.raises(WorkflowError, match="positive"):
        registry.get_workflow("mini")


def test_an_img2img_workflow_without_an_image_slot_is_named():
    doc = dict(MINI_SLOTS, name="refless", kind="img2img")
    _write_workflow("refless", doc, MINI_GRAPH)
    with pytest.raises(WorkflowError, match="image"):
        registry.get_workflow("refless")


def test_a_txt2img_workflow_that_declares_an_image_slot_is_refused():
    doc = copy.deepcopy(MINI_SLOTS)
    doc["slots"]["image"] = {"node": "2", "field": "text"}
    _write_workflow("mini", doc, MINI_GRAPH)
    with pytest.raises(WorkflowError, match="img2img"):
        registry.get_workflow("mini")


def test_an_unknown_slot_name_is_named_with_the_known_ones():
    doc = copy.deepcopy(MINI_SLOTS)
    doc["slots"]["lora"] = {"node": "1", "field": "ckpt_name"}
    _write_workflow("mini", doc, MINI_GRAPH)
    with pytest.raises(WorkflowError) as err:
        registry.get_workflow("mini")
    assert "lora" in str(err.value)
    assert "denoise" in str(err.value)


def test_a_slot_pointing_at_a_node_the_graph_lacks_names_the_node():
    doc = copy.deepcopy(MINI_SLOTS)
    doc["slots"]["seed"] = {"node": "77", "field": "seed"}
    _write_workflow("mini", doc, MINI_GRAPH)
    with pytest.raises(WorkflowError) as err:
        registry.get_workflow("mini")
    assert "'77'" in str(err.value) and "seed" in str(err.value)


def test_a_slot_pointing_at_an_input_the_node_lacks_names_the_input():
    doc = copy.deepcopy(MINI_SLOTS)
    doc["slots"]["cfg"] = {"node": "2", "field": "cfg"}
    _write_workflow("mini", doc, MINI_GRAPH)
    with pytest.raises(WorkflowError) as err:
        registry.get_workflow("mini")
    assert "cfg" in str(err.value) and "CLIPTextEncode" in str(err.value)


def test_one_bad_entry_in_a_seed_list_is_still_named():
    doc = copy.deepcopy(MINI_SLOTS)
    doc["slots"]["seed"] = [{"node": "3", "field": "seed"}, {"node": "99", "field": "seed"}]
    _write_workflow("mini", doc, MINI_GRAPH)
    with pytest.raises(WorkflowError, match="'99'"):
        registry.get_workflow("mini")


def test_a_slot_missing_its_node_key_is_named():
    doc = copy.deepcopy(MINI_SLOTS)
    doc["slots"]["seed"] = {"field": "seed"}
    _write_workflow("mini", doc, MINI_GRAPH)
    with pytest.raises(WorkflowError, match="node"):
        registry.get_workflow("mini")


def test_a_directory_whose_slots_name_disagrees_is_refused():
    """The directory name is the id the API and the picker use; a slots.json
    claiming a different one would be addressable under neither."""
    _write_workflow("mini", dict(MINI_SLOTS, name="something_else"), MINI_GRAPH)
    with pytest.raises(WorkflowError, match="something_else"):
        registry.get_workflow("mini")


def test_a_missing_graph_names_the_file():
    _write_workflow("mini", MINI_SLOTS, None)
    with pytest.raises(WorkflowError, match="graph.json"):
        registry.get_workflow("mini")


def test_unparseable_json_is_named_as_such():
    directory = os.path.join(COMFY_WORKFLOWS_DIR, "mini")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "slots.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    with pytest.raises(WorkflowError, match="slots.json"):
        registry.get_workflow("mini")


def test_an_img2img_build_without_an_uploaded_reference_names_the_slot():
    with pytest.raises(WorkflowError, match="image"):
        registry.build_graph("pro", _params(), seed=1, filename_prefix="p")


# ── the registry is what "workflow" means everywhere else ────────────

REF_GRAPH = dict(
    MINI_GRAPH,
    **{"4": {"class_type": "LoadImage", "inputs": {"image": "example.png"}}},
)
REF_SLOTS = {
    "name": "myref",
    "label": "My reference graph",
    "kind": "img2img",
    "slots": {
        "positive": {"node": "2", "field": "text"},
        "image": {"node": "4", "field": "image"},
        "seed": {"node": "3", "field": "seed"},
        "steps": {"node": "3", "field": "steps"},
        "cfg": {"node": "3", "field": "cfg"},
    },
}


def test_an_unknown_workflow_in_a_request_is_refused_before_anything_runs(make_photo):
    from promptstudio.comfy.params import resolve_generation_params

    rel, _ = make_photo(creator="wf", name="a.jpg", meta=None)
    with pytest.raises(UnknownWorkflowError):
        resolve_generation_params(rel, {"workflow": "no_such_graph", "positive_prompt": "x"})


def test_defaults_follow_the_declared_kind_not_the_name_pro(make_photo):
    """A third img2img workflow must get the reference-graph defaults. Keying
    them off the literal name `pro` is exactly the ceiling A4 removes."""
    from promptstudio.comfy.params import resolve_generation_params
    from promptstudio.config import (
        COMFYUI_DEFAULT_CFG,
        COMFYUI_DEFAULT_DENOISE,
        COMFYUI_DEFAULT_STEPS,
    )

    _write_workflow("myref", REF_SLOTS, REF_GRAPH)
    rel, _ = make_photo(creator="wf", name="b.jpg")
    params = resolve_generation_params(rel, {"workflow": "myref", "positive_prompt": "x"})
    assert params.workflow == "myref"
    assert (params.steps, params.cfg, params.denoise) == (
        COMFYUI_DEFAULT_STEPS,
        COMFYUI_DEFAULT_CFG,
        COMFYUI_DEFAULT_DENOISE,
    )
    assert params.mode_e is True, "Mode E is a property of a reference graph, not of 'pro'"


def test_a_user_txt2img_workflow_gets_the_txt2img_defaults(make_photo):
    from promptstudio.comfy.params import resolve_generation_params

    _write_workflow("mini", MINI_SLOTS, MINI_GRAPH)
    rel, _ = make_photo(creator="wf", name="c.jpg")
    params = resolve_generation_params(rel, {"workflow": "mini", "positive_prompt": "x"})
    assert (params.steps, params.cfg, params.denoise) == (30, 7.0, None)
    assert params.mode_e is False


def test_the_runner_uploads_a_reference_only_for_a_workflow_that_asks_for_one(
    make_photo, fake_comfy, run_comfy_job
):
    _write_workflow("myref", REF_SLOTS, REF_GRAPH)
    rel, _ = make_photo(creator="wf", name="d.jpg")
    status = run_comfy_job(
        source_rel=rel,
        positive="a photo",
        negative="blurry",
        workflow="myref",
        seed=5150,
    )
    assert status["error"] is None, status["error"]
    graph = fake_comfy["graph"]
    assert graph["4"]["inputs"]["image"] == "uploaded_ref.jpg"
    assert graph["3"]["inputs"]["seed"] == 5150
    assert graph["2"]["inputs"]["text"] == "a photo"
    # And the row says which workflow produced it, by registry name.
    assert status["result"]["workflow"] == "myref"


# ── GET /api/workflows ───────────────────────────────────────────────


def test_api_workflows_lists_the_registry_for_the_picker(api):
    status, payload = api("GET", "/api/workflows")
    assert status == 200
    names = [w["name"] for w in payload["workflows"]]
    assert "pro" in names and "txt2img" in names
    assert payload["default"] in names
    for entry in payload["workflows"]:
        assert set(entry) == {"name", "label", "kind"}
        assert entry["kind"] in registry.KINDS


def test_api_workflows_picks_up_a_user_workflow(api):
    _write_workflow("myref", REF_SLOTS, REF_GRAPH)
    _, payload = api("GET", "/api/workflows")
    entry = next(w for w in payload["workflows"] if w["name"] == "myref")
    assert entry == {"name": "myref", "label": "My reference graph", "kind": "img2img"}
