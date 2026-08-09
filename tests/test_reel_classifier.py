"""Reel classifier: frame selection, contact sheets, and tier aggregation.

The behaviour under test is "a reel that reveals its real outfit in the final
seconds gets scored on that outfit" — the failure mode the v1 pipeline could not
see, because it sampled only to 89.7% of the clip and ranked frames by sharpness.

No Ollama here: the vision call is stubbed, so these cover selection and
aggregation logic only.
"""

import os

import cv2
import numpy as np
import pytest

from promptstudio.scraping import outfit_classifier as oc
from promptstudio.scraping import video_frames as vf

MODEST_BGR = (90, 60, 40)  # dark blue-ish, no skin tones
SKIN_BGR = (140, 170, 215)  # inside the YCrCb skin envelope


def _make_reel(path, *, seconds=4.0, fps=10.0, reveal_frac=0.72, size=(240, 426)):
    """Synthetic reel: modest+sharp for most of it, skin-filled at the end.

    Deliberately makes the reveal *blurrier* than the intro — that is what
    defeats a sharpness-ranked picker.
    """
    w, h = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert writer.isOpened(), "mp4v VideoWriter unavailable"
    total = int(seconds * fps)
    for i in range(total):
        if i < total * reveal_frac:
            frame = np.full((h, w, 3), MODEST_BGR, dtype=np.uint8)
            # High-contrast edges => high Laplacian variance (a sharp intro)
            cv2.rectangle(frame, (30, 60), (w - 30, h - 60), (255, 255, 255), 3)
            cv2.putText(
                frame, "WAIT", (40, h // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                (255, 255, 255), 3, cv2.LINE_AA,
            )
        else:
            # A figure against a dark set: lots of skin, real edges, but softer
            # than the intro once blurred.
            frame = np.full((h, w, 3), (35, 30, 30), dtype=np.uint8)
            cv2.ellipse(
                frame, (w // 2, h // 2), (w // 3, int(h * 0.42)), 0, 0, 360,
                SKIN_BGR, -1,
            )
            cv2.rectangle(
                frame, (w // 2 - 40, h // 2 - 30), (w // 2 + 40, h // 2 + 10),
                (60, 50, 120), -1,
            )
            frame = cv2.GaussianBlur(frame, (5, 5), 0)  # motion blur
        writer.write(frame)
    writer.release()
    assert os.path.isfile(path) and os.path.getsize(path) > 0
    return str(path)


# ── frame sampling ───────────────────────────────────────────────────


def test_sampling_reaches_the_end_of_the_clip():
    times = vf._sample_times_sec(10.0, 10, 0.02, 0.0)
    assert times[0] == pytest.approx(0.2, abs=0.01)
    # v1 stopped at 8.97s here and never saw the reveal.
    assert times[-1] >= 9.9


def test_sampling_respects_a_configured_tail_skip():
    times = vf._sample_times_sec(10.0, 8, 0.0, 0.10)
    assert times[-1] == pytest.approx(9.0, abs=0.01)


def test_sampling_handles_degenerate_durations():
    assert vf._sample_times_sec(0.0, 8, 0.02, 0.0) == [0.0]
    assert len(vf._sample_times_sec(5.0, 1, 0.02, 0.0)) == 1


def test_adequately_sharp_skin_frame_outranks_razor_sharp_title_card():
    intro = vf.frame_rank_score(bright=120, sharp=400, skin=0.04)
    reveal = vf.frame_rank_score(bright=120, sharp=60, skin=0.38)
    assert reveal > intro


def test_smeared_frames_still_lose():
    smear = vf.frame_rank_score(bright=120, sharp=3, skin=0.40)
    decent = vf.frame_rank_score(bright=120, sharp=150, skin=0.20)
    assert decent > smear


def test_skin_weight_zero_disables_the_skin_term():
    high = vf.frame_rank_score(bright=120, sharp=100, skin=0.9, skin_weight=0.0)
    low = vf.frame_rank_score(bright=120, sharp=100, skin=0.0, skin_weight=0.0)
    assert high == pytest.approx(low)


def test_skin_fraction_separates_skin_from_non_skin():
    skin = np.full((40, 40, 3), SKIN_BGR, dtype=np.uint8)
    modest = np.full((40, 40, 3), MODEST_BGR, dtype=np.uint8)
    assert vf.skin_fraction(skin) > 0.9
    assert vf.skin_fraction(modest) < 0.1


# ── cover resolution ─────────────────────────────────────────────────


def test_carousel_sibling_is_not_treated_as_a_cover(tmp_path):
    """A sibling slide is a different image; scoring it mis-attributed reels."""
    video = tmp_path / "2026-08-05_09-12-43_UTC_3.mp4"
    video.write_bytes(b"x")
    (tmp_path / "2026-08-05_09-12-43_UTC_1.jpg").write_bytes(b"x")
    assert vf.find_video_cover_image(str(video)) is None


def test_own_cover_still_is_found(tmp_path):
    video = tmp_path / "2026-08-05_09-12-43_UTC_3.mp4"
    video.write_bytes(b"x")
    cover = tmp_path / "2026-08-05_09-12-43_UTC_3.jpg"
    cover.write_bytes(b"x")
    assert vf.find_video_cover_image(str(video)) == str(cover)


# ── contact sheet ────────────────────────────────────────────────────


def test_contact_sheet_is_chronological_and_covers_the_reveal(tmp_path):
    path = _make_reel(tmp_path / "reel.mp4")
    sheet = vf.compose_contact_sheet(path, panels=9)
    assert sheet is not None
    try:
        assert len(sheet.picks) == 9
        assert sheet.cols == 3 and sheet.rows == 3

        times = [p.t_sec for p in sheet.picks]
        assert times == sorted(times), "panels must read left-to-right in time order"

        # The reveal starts at ~72% of a 4s clip; at least one panel must be there.
        assert any(t >= 2.9 for t in times)

        img = cv2.imread(sheet.path)
        assert img.shape[0] == sheet.rows * sheet.panel_h
        assert img.shape[1] == sheet.cols * sheet.panel_w
    finally:
        os.remove(sheet.path)


def test_sheet_panels_carry_the_skin_signal_at_the_end(tmp_path):
    path = _make_reel(tmp_path / "reel.mp4")
    selected = vf.select_timeline_frames(path, panels=9)
    assert selected
    late = [pick for pick, _ in selected if pick.t_sec >= 2.9]
    early = [pick for pick, _ in selected if pick.t_sec < 2.5]
    assert late and early
    assert max(p.skin for p in late) > max(p.skin for p in early)


def test_panels_are_allocated_in_proportion_to_screen_time():
    # A 3:1 split of candidates over two shots, 8 panels.
    assert vf._allocate_panels([12, 4], 8) == [6, 2]


def test_every_shot_gets_at_least_one_panel():
    assert vf._allocate_panels([30, 1, 1], 6) == [4, 1, 1]


def test_allocation_never_exceeds_available_frames():
    alloc = vf._allocate_panels([2, 20], 9)
    assert alloc[0] <= 2
    assert sum(alloc) == 9


def test_reveal_shot_gets_proportional_panels(tmp_path):
    """A uniform time grid straddles cuts and starves the shot that matters."""
    path = _make_reel(tmp_path / "reel.mp4", reveal_frac=0.70)
    selected = vf.select_timeline_frames(path, panels=9)
    shots = [pick.shot for pick, _ in selected]
    assert len(set(shots)) == 2, "intro and reveal must be distinct shots"
    assert shots.count(max(shots)) >= 2, "reveal shot needs more than a token panel"


def test_ranked_picks_include_the_final_shot(tmp_path):
    path = _make_reel(tmp_path / "reel.mp4")
    picks = vf.select_best_video_frames(path, top_n=2, write_jpeg=False)
    try:
        assert picks
        assert any(p.t_sec >= 2.9 for p in picks)
    finally:
        for p in picks:
            if p.path and os.path.isfile(p.path):
                os.remove(p.path)


def test_extract_frame_at_returns_that_moment(tmp_path):
    path = _make_reel(tmp_path / "reel.mp4")
    late = vf.extract_frame_at(path, 3.5)
    early = vf.extract_frame_at(path, 1.0)
    assert late and early
    try:
        skin_late = vf.skin_fraction(cv2.imread(late))
        skin_early = vf.skin_fraction(cv2.imread(early))
        assert skin_late > 0.3, "3.5s is inside the reveal"
        assert skin_early < 0.05, "1.0s is the modest intro"
    finally:
        for p in (late, early):
            os.remove(p)


# ── tier vocabulary ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tier,expected", [(0, 0), (1, 0), (2, 1), (3, 2), (4, 3)]
)
def test_exposure_tier_maps_onto_the_glam_column(tier, expected):
    v = oc.PostVerdict(path="x", ok=True, has_woman=True, exposure_tier=tier)
    assert v.compute_glam_score() == expected


def test_tier_scores_below_the_sexy_threshold_until_tier_3():
    from promptstudio.config import GLAM_SEXY_MIN

    passing = [t for t in range(5) if oc.TIER_TO_GLAM[t] >= GLAM_SEXY_MIN]
    assert passing == [3, 4]


def test_legacy_boolean_scoring_is_unchanged():
    """scripts/backfill_glam_scores.py rebuilds verdicts from stored booleans."""
    v = oc.PostVerdict(path="x", ok=True, has_woman=True)
    v.sexy_revealing_outfit = True
    v.good_breasts = True
    assert v.exposure_tier == -1
    assert v.compute_glam_score() == 3

    v.good_breasts = False
    assert v.compute_glam_score() == 2
    v.sexy_revealing_outfit = False
    assert v.compute_glam_score() == 1
    v.has_woman = False
    assert v.compute_glam_score() == 0


def test_unscored_verdict_stays_negative():
    assert oc.PostVerdict(path="x", ok=False, exposure_tier=4).compute_glam_score() == -1


# ── sheet aggregation ────────────────────────────────────────────────


def test_aggregate_takes_the_max_panel_not_the_first():
    data = {
        "panels": [
            {"i": 1, "has_woman": True, "exposure_tier": 1},
            {"i": 2, "has_woman": True, "exposure_tier": 1},
            {"i": 9, "has_woman": True, "exposure_tier": 4},
        ]
    }
    tier, peak, panels = oc._aggregate_sheet_panels(data, 9)
    assert (tier, peak) == (4, 9)
    assert len(panels) == 3


def test_aggregate_ignores_tiers_on_panels_without_a_woman():
    data = {
        "panels": [
            {"i": 1, "has_woman": False, "exposure_tier": 4},
            {"i": 2, "has_woman": True, "exposure_tier": 2},
        ]
    }
    tier, peak, _ = oc._aggregate_sheet_panels(data, 9)
    assert (tier, peak) == (2, 2)


def test_aggregate_breaks_ties_toward_the_later_panel():
    data = {
        "panels": [
            {"i": 2, "has_woman": True, "exposure_tier": 3},
            {"i": 7, "has_woman": True, "exposure_tier": 3},
        ]
    }
    _, peak, _ = oc._aggregate_sheet_panels(data, 9)
    assert peak == 7


def test_aggregate_drops_out_of_range_and_malformed_panels():
    data = {
        "panels": [
            {"i": 99, "has_woman": True, "exposure_tier": 4},
            {"i": "x", "has_woman": True, "exposure_tier": 4},
            "junk",
            {"i": 3, "has_woman": True, "exposure_tier": 2},
        ]
    }
    tier, peak, panels = oc._aggregate_sheet_panels(data, 9)
    assert (tier, peak) == (2, 3)
    assert len(panels) == 1


def test_no_woman_anywhere_scores_zero():
    data = {"panels": [{"i": 1, "has_woman": False, "exposure_tier": 0}]}
    tier, peak, _ = oc._aggregate_sheet_panels(data, 9)
    assert (tier, peak) == (0, 0)


@pytest.mark.parametrize("tier,conf,expected", [
    (2, 0.9, True),    # glam 1 / glam 2 boundary — worth full resolution
    (3, 0.9, True),
    (4, 0.9, False),   # unambiguous, don't spend a second call
    (1, 0.9, False),
    (4, 0.3, True),    # model unsure
])
def test_confirm_escalation_targets_the_boundary(tier, conf, expected):
    assert oc._needs_confirm(tier, conf) is expected


def test_legacy_path_keeps_looking_after_a_confident_low_score():
    """The v1 stop rule ended the search on exactly the reveal case."""
    v = oc.PostVerdict(path="x", ok=True, has_woman=True, confidence=0.8)
    v.glam_score = 1
    assert oc._needs_second_reel_frame(v) is True


# ── end to end, vision stubbed ───────────────────────────────────────

def _panels(peak_tier, *, peak=9, base=1, n=9):
    """Full panel set with one peak — the guard rejects sparse replies."""
    return [
        {"i": i, "has_woman": True, "exposure_tier": peak_tier if i == peak else base}
        for i in range(1, n + 1)
    ]




@pytest.fixture
def stub_vision(monkeypatch):
    """Record calls and reply per prompt type."""
    calls = []

    def install(sheet_reply, frame_reply=None):
        def fake(image_path, prompt=oc.CLASSIFY_PROMPT, **kwargs):
            calls.append({"path": image_path, "prompt": prompt, "kwargs": kwargs})
            if "CONTACT SHEET" in prompt:
                return dict(sheet_reply)
            return dict(frame_reply or {})

        monkeypatch.setattr(oc, "_ollama_vision_json", fake)
        return calls

    return install


def test_reveal_in_the_final_panel_drives_the_score(tmp_path, stub_vision):
    path = _make_reel(tmp_path / "reel.mp4")
    calls = stub_vision({
        "panels": [
            {"i": i, "has_woman": True, "exposure_tier": 1} for i in range(1, 8)
        ] + [
            {"i": 8, "has_woman": True, "exposure_tier": 4},
            {"i": 9, "has_woman": True, "exposure_tier": 4},
        ],
        "peak_panel": 9,
        "reel_exposure": 4,
        "outfit_changes": True,
        "figure_visible": True,
        "confidence": 0.82,
        "brief_reason": "panel 9 bikini",
    },
        frame_reply={
            "has_woman": True,
            "exposure_tier": 4,
            "figure_visible": True,
            "confidence": 0.88,
            "brief_reason": "bikini, full resolution",
        },
    )

    verdict = oc.classify_video(path)

    assert verdict.ok
    assert verdict.exposure_tier == 4
    assert verdict.glam_score == 3
    assert verdict.source == "video_sheet"
    assert verdict.matches_keep()
    # A high tier read off the FINAL shot is the reveal case the pipeline exists
    # for, so it is confirmed at full resolution before being trusted — a 256px
    # panel is not enough to tell a bikini from a skin-toned dress.
    assert len(calls) == 2
    assert verdict.evidence["frames_sent_to_vision"] == 2
    assert verdict.evidence["confirm"]["tier"] == 4
    assert verdict.evidence["peak_in_last_shot"] is True
    assert verdict.evidence["outfit_changes"] is True
    assert verdict.evidence["peak_time_sec"] >= 2.9


def test_high_tier_away_from_the_final_shot_needs_no_confirm(tmp_path, stub_vision):
    """One call is the budget when an unambiguous peak sits mid-clip.

    The companion to the reveal case: confirming everything would double the
    cost of the whole archive, so the escalation has to be selective.
    """
    path = _make_reel(tmp_path / "reel.mp4")
    calls = stub_vision({
        "panels": _panels(4, peak=1),
        "peak_panel": 1,
        "reel_exposure": 4,
        "confidence": 0.9,
        "brief_reason": "panel 1 bikini",
    })

    verdict = oc.classify_video(path)
    assert verdict.exposure_tier == 4
    assert verdict.evidence["peak_in_last_shot"] is False
    assert len(calls) == 1
    assert verdict.evidence["frames_sent_to_vision"] == 1


def test_modest_reel_is_not_inflated(tmp_path, stub_vision):
    path = _make_reel(tmp_path / "reel.mp4")
    stub_vision({
        "panels": [
            {"i": i, "has_woman": True, "exposure_tier": 1} for i in range(1, 10)
        ],
        "peak_panel": 1,
        "reel_exposure": 1,
        "confidence": 0.9,
        "brief_reason": "coat and jeans throughout",
    })

    verdict = oc.classify_video(path)
    assert verdict.exposure_tier == 1
    assert verdict.glam_score == 0
    assert not verdict.matches_keep()


def test_boundary_tier_confirms_at_full_resolution(tmp_path, stub_vision):
    path = _make_reel(tmp_path / "reel.mp4")
    calls = stub_vision(
        {
            "panels": _panels(3),
            "peak_panel": 9,
            "reel_exposure": 3,
            "confidence": 0.6,
            "brief_reason": "panel 9 short dress",
        },
        frame_reply={
            "has_woman": True,
            "exposure_tier": 4,
            "figure_visible": True,
            "confidence": 0.88,
            "brief_reason": "sheer bodysuit",
        },
    )

    verdict = oc.classify_video(path)

    assert len(calls) == 2, "sheet then full-resolution confirm"
    assert "CONTACT SHEET" in calls[0]["prompt"]
    assert verdict.exposure_tier == 4, "the higher-resolution read supersedes"
    assert verdict.glam_score == 3
    assert verdict.confidence == pytest.approx(0.88)
    assert verdict.evidence["confirm"]["tier"] == 4
    assert verdict.evidence["frames_sent_to_vision"] == 2


def test_confirm_losing_the_subject_keeps_the_sheet_reading(tmp_path, stub_vision):
    path = _make_reel(tmp_path / "reel.mp4")
    stub_vision(
        {
            "panels": _panels(3),
            "peak_panel": 9,
            "reel_exposure": 3,
            "confidence": 0.6,
            "brief_reason": "panel 9 crop top",
        },
        frame_reply={"has_woman": False, "exposure_tier": 0, "confidence": 0.4},
    )

    verdict = oc.classify_video(path)
    assert verdict.exposure_tier == 3
    assert verdict.glam_score == 2
    assert verdict.evidence["confirm_lost_subject"] is True


def test_vision_error_leaves_the_reel_unscored(tmp_path, stub_vision):
    path = _make_reel(tmp_path / "reel.mp4")
    stub_vision({"_error": "connection refused"})

    verdict = oc.classify_video(path)
    assert not verdict.ok
    assert verdict.glam_score == -1, "retryable, not a silent zero"
    assert "connection refused" in verdict.error


def test_undecodable_video_falls_back_to_its_own_cover(tmp_path, stub_vision):
    video = tmp_path / "2026-08-05_09-12-43_UTC.mp4"
    video.write_bytes(b"not a video")
    cover = tmp_path / "2026-08-05_09-12-43_UTC.jpg"
    cv2.imwrite(str(cover), np.full((80, 60, 3), SKIN_BGR, dtype=np.uint8))

    stub_vision(
        {"_error": "unused"},
        frame_reply={
            "has_woman": True,
            "exposure_tier": 4,
            "figure_visible": True,
            "confidence": 0.7,
            "brief_reason": "bikini",
        },
    )

    verdict = oc.classify_video(str(video))
    assert verdict.ok
    assert verdict.source == "video_cover"
    assert verdict.glam_score == 3
    assert verdict.evidence["used_cover"] is True


def test_vision_outage_does_not_also_burn_a_cover_call(tmp_path, stub_vision):
    """A second call during an outage just doubles the cost of the outage."""
    video = tmp_path / "2026-08-05_09-12-43_UTC.mp4"
    _make_reel(video)
    cv2.imwrite(
        str(tmp_path / "2026-08-05_09-12-43_UTC.jpg"),
        np.full((80, 60, 3), SKIN_BGR, dtype=np.uint8),
    )
    calls = stub_vision({"_error": "connection refused"})

    verdict = oc.classify_video(str(video))
    assert not verdict.ok
    assert len(calls) == 1, "sheet only — no cover retry"


def test_sheet_temp_files_are_cleaned_up(tmp_path, stub_vision, monkeypatch):
    path = _make_reel(tmp_path / "reel.mp4")
    made = []
    real_compose = vf.compose_contact_sheet

    def spy(*args, **kwargs):
        sheet = real_compose(*args, **kwargs)
        if sheet:
            made.append(sheet.path)
        return sheet

    monkeypatch.setattr(oc, "compose_contact_sheet", spy)
    stub_vision({
        "panels": _panels(4, peak=1),
        "peak_panel": 1,
        "reel_exposure": 4,
        "confidence": 0.9,
    })

    oc.classify_video(path)
    assert made
    assert not any(os.path.isfile(p) for p in made)


def test_structured_output_schema_is_sent(tmp_path, stub_vision):
    path = _make_reel(tmp_path / "reel.mp4")
    calls = stub_vision({
        "panels": _panels(4, peak=1),
        "peak_panel": 1,
        "reel_exposure": 4,
        "confidence": 0.9,
    })

    oc.classify_video(path)
    assert calls[0]["kwargs"]["schema"] is oc.SHEET_SCHEMA


def test_photo_path_still_uses_the_legacy_prompt(monkeypatch, tmp_path):
    """Legacy vocabulary when ordinal is forced off (env may default to 1)."""
    seen = {}

    def fake(image_path, prompt=oc.CLASSIFY_PROMPT, **kwargs):
        seen["prompt"] = prompt
        seen["schema"] = kwargs.get("schema")
        return {
            "has_woman": True,
            "sexy_revealing_outfit": True,
            "good_breasts": True,
            "confidence": 0.9,
        }

    monkeypatch.setattr(oc, "_ollama_vision_json", fake)
    photo = tmp_path / "p.jpg"
    cv2.imwrite(str(photo), np.full((40, 40, 3), SKIN_BGR, dtype=np.uint8))

    verdict = oc.classify_image(str(photo), ordinal=False)
    assert seen["prompt"] == oc.CLASSIFY_PROMPT
    assert seen["schema"] is oc.LEGACY_FRAME_SCHEMA
    assert verdict.prompt_version == oc.CLASSIFY_PROMPT_VERSION
    assert verdict.exposure_tier == -1
    assert verdict.glam_score == 3


def test_photo_ordinal_opt_in_switches_vocabulary(monkeypatch, tmp_path):
    seen = {}

    def fake(image_path, prompt=oc.CLASSIFY_PROMPT, **kwargs):
        seen["prompt"] = prompt
        return {
            "has_woman": True,
            "exposure_tier": 2,
            "figure_visible": False,
            "confidence": 0.8,
        }

    monkeypatch.setattr(oc, "_ollama_vision_json", fake)
    photo = tmp_path / "p.jpg"
    cv2.imwrite(str(photo), np.full((40, 40, 3), SKIN_BGR, dtype=np.uint8))

    verdict = oc.classify_image(str(photo), ordinal=True)
    assert seen["prompt"] == oc.CLASSIFY_FRAME_V4_PROMPT
    assert verdict.exposure_tier == 2
    assert verdict.glam_score == 1
    assert verdict.prompt_version == oc.CLASSIFY_FRAME_V4_VERSION


# ── retries ──────────────────────────────────────────────────────────


def test_transient_failure_is_retried(monkeypatch, tmp_path):
    photo = tmp_path / "p.jpg"
    cv2.imwrite(str(photo), np.full((40, 40, 3), SKIN_BGR, dtype=np.uint8))

    attempts = {"n": 0}

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("connection reset")
        import json as _json

        return FakeResponse(
            _json.dumps({"response": '{"has_woman": true, "confidence": 0.9}'}).encode()
        )

    monkeypatch.setattr(oc.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(oc.time, "sleep", lambda _s: None)

    data = oc._ollama_vision_json(str(photo), prompt="x", schema=oc.LEGACY_FRAME_SCHEMA)
    assert attempts["n"] == 2
    assert data["has_woman"] is True


def test_retries_are_bounded_and_surface_the_error(monkeypatch, tmp_path):
    photo = tmp_path / "p.jpg"
    cv2.imwrite(str(photo), np.full((40, 40, 3), SKIN_BGR, dtype=np.uint8))

    attempts = {"n": 0}

    def always_fail(req, timeout=None):
        attempts["n"] += 1
        raise OSError("ollama down")

    monkeypatch.setattr(oc.urllib.request, "urlopen", always_fail)
    monkeypatch.setattr(oc.time, "sleep", lambda _s: None)

    data = oc._ollama_vision_json(str(photo), prompt="x")
    assert attempts["n"] == oc.CLASSIFY_RETRIES + 1
    assert "ollama down" in data["_error"]
