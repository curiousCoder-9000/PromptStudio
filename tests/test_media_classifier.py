"""Ordinal keep/reject classifier.

Ollama is stubbed throughout — these tests are about the reduction from model
output to a tier, not about the model. What they pin down:

- **Max-over-panels is computed here, not trusted from the model.** The reel
  rollup (`reel_exposure`) is a single unauditable number; the panel array is
  the evidence. A reveal in panel 9 has to win over a modest panel 1, which is
  the entire reason contact sheets exist.
- **A repeated panel index cannot outvote the panels the model skipped.**
- **An unreadable sheet falls back to a ranked frame with the same vocabulary**,
  never to the cover still — the cover shows the "before" outfit.
- **Sheets are written inside `_classify/` or not at all.**
"""

import os
from unittest.mock import patch

import pytest

from promptstudio.scraping import media_classifier as mc

# ── tier -> verdict policy ───────────────────────────────────────────

@pytest.mark.parametrize(
    "tier,cut,expected",
    [
        (0, 1, True), (1, 1, True), (2, 1, False), (3, 1, False), (4, 1, False),
        (0, 0, True), (1, 0, False),
        (0, 2, True), (1, 2, True), (2, 2, True), (3, 2, False),
        (-1, 1, False),  # a failed attempt is not a reject
    ],
)
def test_tier_is_reject_follows_the_cut(tier, cut, expected):
    assert mc.tier_is_reject(tier, cut) is expected


def test_verdict_is_reject_requires_a_successful_read(monkeypatch):
    v = mc.MediaVerdict(path="x.jpg", exposure_tier=0, ok=False, error="timeout")
    assert v.is_reject(cut=1) is False
    v.ok = True
    assert v.is_reject(cut=1) is True


def test_every_tier_has_a_label():
    for tier in range(-1, 3):
        assert mc.TIER_LABELS[tier]
    assert mc.MediaVerdict(path="x", exposure_tier=2).tier_label == "Swim / lingerie"


# ── parsing model output ─────────────────────────────────────────────

def test_tier_data_round_trips():
    v = mc._verdict_from_tier_data(
        "x.jpg",
        {"has_woman": True, "exposure_tier": 1, "confidence": 0.9,
         "brief_reason": "crop top", "figure_visible": True},
        source="image",
        prompt_version="v",
    )
    assert (v.ok, v.exposure_tier, v.has_woman, v.figure_visible) == (True, 1, True, True)
    assert v.brief_reason == "crop top"


def test_v9_prompt_is_the_three_bucket_scale():
    prompt = mc.CLASSIFY_FRAME_PROMPT
    assert "integer 0-2" in prompt
    assert "Tight clothes are 1" in prompt
    assert "ALWAYS 2" in prompt
    assert "integer 0-4" not in prompt
    assert mc.CLASSIFY_FRAME_VERSION == "v4-ordinal-frame-v9"
    sheet = mc.reel_sheet_prompt(9)
    assert "integer 0-2" in sheet
    assert "Bikini/lingerie in any panel is 2" in sheet


def _keep_data(tier=1, **overrides):
    data = {
        "has_woman": True,
        "exposure_tier": tier,
        "confidence": 0.9,
        "brief_reason": "tight dress",
    }
    data.update(overrides)
    return mc._verdict_from_tier_data("x.jpg", data, source="image", prompt_version="v")


def test_bikini_reason_is_floored_to_t2():
    """The model names the garment then sits on 1; policy must not trust the int."""
    v = _keep_data(tier=1, brief_reason="bikini set")
    assert v.exposure_tier == 2


def test_a_bikini_on_an_event_flyer_is_still_tier_0():
    v = _keep_data(tier=1, brief_reason="swimsuit", is_graphic=True)
    assert v.exposure_tier == 0


def test_undress_class_flag_wins_over_a_modest_reason():
    v = _keep_data(tier=0, brief_reason="outfit", undress_class=True)
    assert v.exposure_tier == 2


def test_swim_without_undress_evidence_is_daywear():
    v = _keep_data(tier=2, brief_reason="tight dress", undress_class=False)
    assert v.exposure_tier == 1


def test_tight_reason_on_a_reject_is_floored_to_t1():
    v = _keep_data(tier=0, brief_reason="crop top + jeans")
    assert v.exposure_tier == 1
    assert "floored 0→1" in v.brief_reason


def test_bare_midriff_floors_a_reject_to_t1():
    v = _keep_data(tier=0, brief_reason="yellow top + pants", bare_midriff=True)
    assert v.exposure_tier == 1


def test_tight_daywear_is_not_capped_by_figure():
    """v8 required curvy+body-focus for the keep bucket; v9 keeps tightness."""
    v = _keep_data(tier=1, figure="slim", body_focus=False)
    assert v.exposure_tier == 1


def test_no_woman_forces_tier_zero():
    v = mc._verdict_from_tier_data(
        "x.jpg",
        {"has_woman": False, "exposure_tier": 2, "confidence": 0.9},
        source="image",
        prompt_version="v",
    )
    assert v.ok is True
    assert v.exposure_tier == 0


def test_out_of_range_tier_is_clamped():
    high = mc._verdict_from_tier_data(
        "x.jpg",
        {"has_woman": True, "exposure_tier": 9, "undress_class": True},
        source="image",
        prompt_version="v",
    )
    assert high.exposure_tier == 2
    low = mc._verdict_from_tier_data(
        "x.jpg",
        {"has_woman": True, "exposure_tier": -3},
        source="image",
        prompt_version="v",
    )
    assert low.exposure_tier == 0


def test_garbage_tier_does_not_crash():
    v = mc._verdict_from_tier_data(
        "x.jpg", {"has_woman": True, "exposure_tier": "very"}, source="image",
        prompt_version="v",
    )
    assert v.ok is True and v.exposure_tier == 0


def test_transport_error_produces_a_failed_verdict():
    v = mc._verdict_from_tier_data(
        "x.jpg", {"_error": "connection refused"}, source="image", prompt_version="v"
    )
    assert v.ok is False
    assert v.exposure_tier == -1
    assert "connection refused" in v.error


def test_empty_response_produces_a_failed_verdict():
    v = mc._verdict_from_tier_data("x.jpg", None, source="image", prompt_version="v")
    assert v.ok is False and v.error


def test_reason_is_capped():
    v = mc._verdict_from_tier_data(
        "x.jpg",
        {"has_woman": True, "exposure_tier": 2, "brief_reason": "z" * 500},
        source="image",
        prompt_version="v",
    )
    assert len(v.brief_reason) == 160


def test_json_is_scraped_out_of_prose():
    """The regex fallback for models that reject constrained decoding."""
    raw = '```json\n{"has_woman": true, "exposure_tier": 4}\n```'
    assert mc._parse_json_object(raw) == {"has_woman": True, "exposure_tier": 4}
    assert mc._parse_json_object("no json here") is None
    assert mc._parse_json_object('{"broken": ') is None


# ── contact sheet aggregation ────────────────────────────────────────

def test_max_over_panels_catches_a_final_seconds_reveal():
    data = {
        "panels": [
            {"i": 1, "has_woman": True, "exposure_tier": 1},
            {"i": 2, "has_woman": True, "exposure_tier": 1},
            {"i": 9, "has_woman": True, "exposure_tier": 2},
        ],
        # The model's own rollup is wrong; the panel array wins.
        "reel_exposure": 1,
    }
    tier, peak, panels = mc._aggregate_sheet_panels(data, 9)
    assert tier == 2
    assert peak == 9
    assert len(panels) == 3


def test_ties_break_to_the_later_panel():
    data = {"panels": [
        {"i": 2, "has_woman": True, "exposure_tier": 1},
        {"i": 7, "has_woman": True, "exposure_tier": 1},
    ]}
    tier, peak, _panels = mc._aggregate_sheet_panels(data, 9)
    assert (tier, peak) == (1, 7)


def test_a_repeated_panel_index_cannot_stuff_the_ballot():
    """First reading per index wins — a model repeating `i` has lost the captions."""
    data = {"panels": [
        {"i": 3, "has_woman": True, "exposure_tier": 0},
        {"i": 3, "has_woman": True, "exposure_tier": 4},
        {"i": 3, "has_woman": True, "exposure_tier": 4},
    ]}
    tier, _peak, panels = mc._aggregate_sheet_panels(data, 9)
    assert tier == 0
    assert len(panels) == 1


def test_panels_without_a_woman_do_not_contribute_a_tier():
    data = {"panels": [
        {"i": 1, "has_woman": False, "exposure_tier": 4},
        {"i": 2, "has_woman": True, "exposure_tier": 2},
    ]}
    tier, peak, panels = mc._aggregate_sheet_panels(data, 9)
    assert (tier, peak) == (2, 2)
    assert panels[0]["exposure_tier"] == 0


def test_out_of_range_panel_indices_are_dropped():
    data = {"panels": [
        {"i": 0, "has_woman": True, "exposure_tier": 4},
        {"i": 99, "has_woman": True, "exposure_tier": 4},
        {"i": 2, "has_woman": True, "exposure_tier": 1},
    ]}
    tier, peak, panels = mc._aggregate_sheet_panels(data, 9)
    assert (tier, peak) == (1, 2)
    assert len(panels) == 1


def test_no_panels_at_all_reports_zero():
    tier, peak, panels = mc._aggregate_sheet_panels({"panels": []}, 9)
    assert (tier, peak, panels) == (0, 0, [])
    assert mc._aggregate_sheet_panels({}, 9) == (0, 0, [])


def test_malformed_panel_entries_are_skipped():
    data = {"panels": ["nope", {"i": "x"}, {"i": 4, "has_woman": True, "exposure_tier": 1}]}
    tier, peak, panels = mc._aggregate_sheet_panels(data, 9)
    assert (tier, peak, len(panels)) == (1, 4, 1)


# ── confirm cascade ──────────────────────────────────────────────────

def test_boundary_tiers_get_a_full_resolution_confirm():
    """256px panels can't tell sheer from opaque; the keep/reject cut must."""
    assert mc._needs_confirm(1, 0.95) is True


def test_unambiguous_tiers_skip_the_confirm():
    assert mc._needs_confirm(0, 0.95) is False
    assert mc._needs_confirm(2, 0.95) is False


def test_low_confidence_always_confirms():
    assert mc._needs_confirm(2, 0.3) is True


def test_a_high_tier_in_the_final_shot_confirms():
    assert mc._needs_confirm(2, 0.95, peak_in_last_shot=True) is True


# ── sheet paths stay inside _classify/ ───────────────────────────────

def test_sheet_path_mirrors_the_media_layout():
    assert mc.sheet_rel_path("nina/reel_1.mp4") == "nina/reel_1.sheet.jpg"
    assert mc.sheet_rel_path("") == ""


def test_sheet_full_path_is_under_the_sheet_dir():
    full = mc.sheet_full_path("nina/reel_1.mp4")
    assert full
    assert os.path.normpath(full).startswith(
        os.path.normpath(os.path.expanduser(mc.CLASSIFY_SHEET_DIR)) + os.sep
    )


@pytest.mark.parametrize("hostile", ["../../etc/passwd.mp4", "/etc/passwd.mp4", "a/../../b.mp4"])
def test_traversing_sheet_paths_are_refused(hostile):
    assert mc.sheet_full_path(hostile) == ""


# ── dispatch ─────────────────────────────────────────────────────────

def test_media_kind_splits_photos_from_reels():
    assert mc.media_kind_for("a/b.jpg") == "photo"
    assert mc.media_kind_for("a/b.MP4") == "reel"
    assert mc.media_kind_for("a/b.webm") == "reel"


def test_classify_media_routes_by_extension():
    with patch.object(mc, "classify_image") as img, patch.object(mc, "classify_video") as vid:
        mc.classify_media("a/b.jpg")
        assert img.called and not vid.called
    with patch.object(mc, "classify_image") as img, patch.object(mc, "classify_video") as vid:
        mc.classify_media("a/b.mp4", rel_path="a/b.mp4")
        assert vid.called and not img.called
        assert vid.call_args.kwargs["rel_path"] == "a/b.mp4"


def test_prompt_versions_cover_both_reel_paths():
    """The sheet's single-frame fallback is a legitimate outcome, not staleness."""
    versions = mc.active_prompt_versions()
    assert mc.CLASSIFY_FRAME_VERSION in versions
    assert mc.current_prompt_version("x.mp4") in versions
    assert mc.current_prompt_version("x.jpg") == mc.CLASSIFY_FRAME_VERSION


# ── ranked-frame path (CLASSIFY_REEL_SHEET=0) ────────────────────────

def test_higher_tier_wins_between_two_frames():
    a = mc.MediaVerdict(path="x", ok=True, exposure_tier=1, confidence=0.9)
    b = mc.MediaVerdict(path="x", ok=True, exposure_tier=2, confidence=0.5)
    assert mc._prefer_verdict(a, b) is b


def test_confidence_breaks_a_tier_tie():
    a = mc.MediaVerdict(path="x", ok=True, exposure_tier=1, confidence=0.5)
    b = mc.MediaVerdict(path="x", ok=True, exposure_tier=1, confidence=0.9)
    assert mc._prefer_verdict(a, b) is b


def test_a_failed_read_never_wins():
    ok = mc.MediaVerdict(path="x", ok=True, exposure_tier=0, confidence=0.1)
    bad = mc.MediaVerdict(path="x", ok=False, exposure_tier=2, confidence=1.0)
    assert mc._prefer_verdict(ok, bad) is ok
    assert mc._prefer_verdict(bad, ok) is ok


def test_a_confident_low_tier_still_earns_a_second_frame():
    """The reveal case: the model is sure about the 'before' outfit."""
    v = mc.MediaVerdict(path="x", ok=True, has_woman=True, exposure_tier=0, confidence=0.99)
    assert mc._needs_second_reel_frame(v) is True


def test_a_confident_high_tier_stops_looking():
    v = mc.MediaVerdict(path="x", ok=True, has_woman=True, exposure_tier=2, confidence=0.99)
    assert mc._needs_second_reel_frame(v) is False


# ── persistence ──────────────────────────────────────────────────────

def test_persist_writes_the_index_row_and_the_sidecar(make_photo):
    from promptstudio.storage.db import ArchiveIndex
    from promptstudio.storage.metadata import load_post_metadata

    rel, full = make_photo(name="a.jpg")
    verdict = mc.MediaVerdict(
        path=full, ok=True, has_woman=True, exposure_tier=1,
        confidence=0.8, brief_reason="crop top", source="image",
        prompt_version=mc.CLASSIFY_FRAME_VERSION,
    )
    mc.persist_verdict(rel, verdict, full_path=full, duration_ms=900)

    row = ArchiveIndex.get().get_verdict(rel)
    assert row["tier"] == 1
    assert row["verdict"] == "keep"
    assert row["duration_ms"] == 900
    assert row["media_kind"] == "photo"

    block = (load_post_metadata(full) or {}).get("classify") or {}
    assert block["exposure_tier"] == 1
    assert block["tier_label"] == "Revealing / tight"
    assert block["reject"] is False


def test_a_failed_retry_does_not_clobber_a_good_sidecar(make_photo):
    from promptstudio.storage.db import ArchiveIndex
    from promptstudio.storage.metadata import load_post_metadata

    rel, full = make_photo(name="a.jpg")
    good = mc.MediaVerdict(path=full, ok=True, has_woman=True, exposure_tier=2)
    mc.persist_verdict(rel, good, full_path=full)
    bad = mc.MediaVerdict(path=full, ok=False, error="vision timeout")
    mc.persist_verdict(rel, bad, full_path=full)

    # The sidecar keeps the last good reading …
    assert (load_post_metadata(full) or {})["classify"]["exposure_tier"] == 2
    # … while the index records the failure, so the row is retryable.
    row = ArchiveIndex.get().get_verdict(rel)
    assert row["tier"] == -1
    assert row["error"] == "vision timeout"


# ── reel end-to-end (real video, stubbed vision) ─────────────────────

def _write_clip(path, seconds=6, fps=10, size=(240, 320)):
    """A short clip whose last shot differs sharply from the rest."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    w, h = size
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not writer.isOpened():
        pytest.skip("no mp4v encoder in this OpenCV build")
    for i in range(seconds * fps):
        frame = np.zeros((h, w, 3), np.uint8)
        frame[:, :] = (30 + i * 3, 60, 200 - i * 2)
        cv2.circle(frame, (w // 2, h // 2), 40 + i, (200, 180, 170), -1)
        writer.write(frame)
    writer.release()
    if not os.path.getsize(path):
        pytest.skip("OpenCV wrote an empty video")


def test_a_reel_is_judged_from_a_persisted_contact_sheet(tmp_path, make_photo):
    """The whole reel path: decode -> sheet -> panels -> confirm -> persist.

    Mocked at the vision boundary only, so sheet composition, panel labelling
    and the peak-frame timestamp are all really exercised. This is the path
    single-frame sampling could not do: the reveal is in the final panel.
    """
    from promptstudio.storage.db import ArchiveIndex

    # Live inside the archive so rel_path -> sheet path resolution is real.
    rel, photo_full = make_photo(name="cover.jpg")
    clip = os.path.join(os.path.dirname(photo_full), "clip.mp4")
    _write_clip(clip)
    clip_rel = "test_creator/clip.mp4"

    sheet_reply = {
        "panels": [
            {"i": i, "has_woman": True, "exposure_tier": 1 if i < 9 else 2}
            for i in range(1, 10)
        ],
        "reel_exposure": 1,  # wrong on purpose: the panel array must win
        "confidence": 0.88,
        "brief_reason": "reveal in the last panel",
    }
    frame_reply = {"has_woman": True, "exposure_tier": 2, "confidence": 0.9,
                   "brief_reason": "bikini set"}
    prompts_seen = []

    def fake_vision(image_path, prompt=mc.CLASSIFY_FRAME_PROMPT, **kw):
        prompts_seen.append("sheet" if "CONTACT SHEET" in prompt else "frame")
        return sheet_reply if "CONTACT SHEET" in prompt else frame_reply

    with patch.object(mc, "_ollama_vision_json", side_effect=fake_vision):
        verdict = mc.classify_video(clip, rel_path=clip_rel)

    assert verdict.ok is True
    assert verdict.source == "video_sheet"
    assert verdict.exposure_tier == 2
    # Sheet first, then a full-resolution confirm of the peak frame.
    assert prompts_seen == ["sheet", "frame"]
    assert verdict.evidence["peak_panel"] == 9
    assert verdict.evidence["peak_time_sec"] > 0
    assert verdict.evidence["frames_sent_to_vision"] == 2

    # The sheet survives the run, inside _classify/, so triage can show it.
    sheet = mc.sheet_full_path(clip_rel)
    assert verdict.sheet_path == "test_creator/clip.sheet.jpg"
    assert os.path.isfile(sheet)
    assert os.path.getsize(sheet) > 0

    ArchiveIndex.get().upsert_photo(clip_rel)
    mc.persist_verdict(clip_rel, verdict, full_path=clip, duration_ms=1200)
    row = ArchiveIndex.get().get_verdict(clip_rel)
    assert row["tier"] == 2
    assert row["verdict"] == "keep"
    assert row["media_kind"] == "reel"
    assert row["sheet_path"] == "test_creator/clip.sheet.jpg"

    mc.delete_sheet(clip_rel)
    assert not os.path.isfile(sheet)


def test_a_temp_sheet_is_cleaned_up_when_there_is_no_rel_path(make_photo):
    """Without a rel_path the sheet is a temp file and must not be left behind."""
    _rel, photo_full = make_photo(name="cover.jpg")
    clip = os.path.join(os.path.dirname(photo_full), "clip.mp4")
    _write_clip(clip, seconds=3)

    captured = {}
    real_compose = mc.compose_contact_sheet

    def spy(video_path, **kw):
        sheet = real_compose(video_path, **kw)
        captured["path"] = sheet.path if sheet else None
        return sheet

    reply = {"panels": [{"i": 1, "has_woman": True, "exposure_tier": 0}],
             "reel_exposure": 0, "confidence": 0.9}
    with patch.object(mc, "compose_contact_sheet", side_effect=spy), \
         patch.object(mc, "_ollama_vision_json", return_value=reply):
        verdict = mc.classify_video(clip)

    assert verdict.sheet_path == ""
    assert captured["path"]
    assert not os.path.isfile(captured["path"])
