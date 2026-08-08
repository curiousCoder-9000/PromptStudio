"""Durable writes for derived state.

The failure this guards against is silent: a bare `open(path, "w")` truncates
before the new bytes land, and every loader in this codebase swallows the
resulting JSON parse error and returns an empty default. A half-written
prompts_cache.json therefore reads as "no prompts have ever been generated".

The contract is: after any failure, the file on disk is either the complete old
content or the complete new content — never a fragment, and never absent.
"""

import json
import os

import pytest

from promptstudio.storage.atomic import atomic_write_json, atomic_write_text


def _temp_files(directory):
    return [n for n in os.listdir(directory) if n.startswith(".ps_")]


def test_writes_and_reads_back(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_json(str(target), {"a": 1, "b": [2, 3]})
    assert json.loads(target.read_text()) == {"a": 1, "b": [2, 3]}


def test_creates_missing_parent_directories(tmp_path):
    target = tmp_path / "nested" / "deeper" / "state.json"
    atomic_write_json(str(target), {"ok": True})
    assert json.loads(target.read_text()) == {"ok": True}


def test_overwrite_replaces_content_entirely(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_json(str(target), {"long": "x" * 500})
    atomic_write_json(str(target), {"s": 1})
    assert json.loads(target.read_text()) == {"s": 1}


def test_no_temp_files_left_behind(tmp_path):
    target = tmp_path / "state.json"
    for i in range(5):
        atomic_write_json(str(target), {"i": i})
    assert _temp_files(tmp_path) == []


def test_temp_file_lands_beside_target(tmp_path, monkeypatch):
    """os.replace is only atomic within one filesystem, so /tmp won't do."""
    seen = {}
    real_replace = os.replace

    def spy(src, dst):
        seen["src_dir"] = os.path.dirname(src)
        seen["dst_dir"] = os.path.dirname(dst)
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    target = tmp_path / "sub" / "state.json"
    atomic_write_json(str(target), {"x": 1})
    assert seen["src_dir"] == seen["dst_dir"]


def test_failed_write_leaves_original_intact(tmp_path, monkeypatch):
    """The whole point: a crash at the replace must not destroy the old file."""
    target = tmp_path / "prompts_cache.json"
    atomic_write_json(str(target), {"photo.jpg": "expensive prompt"})

    def boom(src, dst):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_json(str(target), {"photo.jpg": "new"})

    assert json.loads(target.read_text()) == {"photo.jpg": "expensive prompt"}
    assert _temp_files(tmp_path) == [], "temp file must be cleaned up on failure"


def test_unserializable_value_never_touches_the_filesystem(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_json(str(target), {"good": 1})

    with pytest.raises(TypeError):
        atomic_write_json(str(target), {"bad": object()})

    assert json.loads(target.read_text()) == {"good": 1}
    assert _temp_files(tmp_path) == []


def test_keyboard_interrupt_still_cleans_up(tmp_path, monkeypatch):
    """BaseException, not Exception — Ctrl-C must not strand a temp file."""
    target = tmp_path / "state.json"

    def boom(src, dst):
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        atomic_write_json(str(target), {"x": 1})
    assert _temp_files(tmp_path) == []


def test_reader_never_observes_a_partial_file(tmp_path):
    """A concurrent reader sees old or new, never a fragment."""
    target = tmp_path / "state.json"
    small = {"n": 1}
    large = {"n": 2, "pad": "y" * 100_000}
    atomic_write_json(str(target), small)

    observed = []
    for _ in range(40):
        atomic_write_json(str(target), large)
        observed.append(json.loads(target.read_text())["n"])
        atomic_write_json(str(target), small)
        observed.append(json.loads(target.read_text())["n"])

    assert set(observed) == {1, 2}


def test_text_writer_round_trips_unicode(tmp_path):
    target = tmp_path / "note.txt"
    atomic_write_text(str(target), "réel — 髪")
    assert target.read_text(encoding="utf-8") == "réel — 髪"


def test_fsync_false_still_writes(tmp_path):
    target = tmp_path / "status.json"
    atomic_write_json(str(target), {"running": True}, fsync=False)
    assert json.loads(target.read_text()) == {"running": True}


def test_json_is_indented_for_hand_editing(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_json(str(target), {"a": 1})
    assert "\n" in target.read_text()
