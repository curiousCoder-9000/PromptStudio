"""Reel classifier: failure paths that let a bad reading through unnoticed.

Companion to test_reel_classifier.py, which covers the happy path (reveal at the
end gets scored on the reveal). These cover three defects where the pipeline
either silently trusted a reading it should not have, or fell back to a path
that cost far more than it needed to:

* a rejected ``format`` parameter left every item permanently unscored, because
  the code that was supposed to drop the constraint tested the wrong string;
* a sheet reply with repeated or missing panel indices was scored as though the
  model had read all nine;
* an unclassified video decoded its whole timeline to make a thumbnail, even
  when a companion cover still was sitting next to it.

No Ollama: the vision call and the HTTP layer are both stubbed.
"""

import json
import os
import urllib.error

import cv2
import numpy as np
import pytest

from promptstudio.scraping import outfit_classifier as oc
from promptstudio.storage import thumbs


def _make_clip(path, *, seconds=2.0, fps=10.0, size=(120, 214)):
    w, h = size
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    assert writer.isOpened(), "mp4v VideoWriter unavailable"
    for i in range(int(seconds * fps)):
        frame = np.full((h, w, 3), (40, 40, 40), dtype=np.uint8)
        cv2.rectangle(frame, (10, 10 + i), (w - 10, h - 10), (200, 180, 160), -1)
        writer.write(frame)
    writer.release()
    return str(path)


# ── structured-output rejection ──────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_400(body=b'{"error":"format: unsupported value"}'):
    import io

    return urllib.error.HTTPError(
        "http://localhost:11434/api/generate", 400, "Bad Request", {}, io.BytesIO(body)
    )


@pytest.fixture
def fake_ollama(monkeypatch, tmp_path):
    """Drive urlopen by script; record the payload of every request."""
    image = tmp_path / "frame.jpg"
    cv2.imwrite(str(image), np.full((64, 64, 3), 128, dtype=np.uint8))
    sent = []

    def install(*responses):
        queue = list(responses)
        # Once the script runs out the last outcome repeats, so a test that
        # scripts a single failure gets that failure on every retry.
        last = [responses[-1]]

        def fake_urlopen(req, timeout=None):
            sent.append(json.loads(req.data.decode("utf-8")))
            if queue:
                last[0] = queue.pop(0)
            outcome = last[0]
            # Exceptions are passed as factories: urllib raises a fresh
            # HTTPError per request, and reading one's body consumes it.
            if callable(outcome):
                outcome = outcome()
            if isinstance(outcome, Exception):
                raise outcome
            return _FakeResponse({"response": json.dumps(outcome)})

        monkeypatch.setattr(oc.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(oc.time, "sleep", lambda _s: None)
        return sent

    install.image = str(image)
    return install


def test_schema_rejection_retries_without_structured_output(fake_ollama):
    reply = {"has_woman": True, "exposure_tier": 4, "confidence": 0.9}
    sent = fake_ollama(_http_400, reply)

    data = oc._ollama_vision_json(
        fake_ollama.image, prompt="x", schema=oc.FRAME_V4_SCHEMA
    )

    assert data == reply
    assert len(sent) == 2, "should have retried"
    assert "format" in sent[0], "first attempt sends the schema"
    assert "format" not in sent[1], (
        "a 400 means the schema was rejected — the retry must drop it, or every "
        "item stays unscored forever"
    )


def test_error_message_includes_the_response_body(fake_ollama):
    fake_ollama(lambda: _http_400(b'{"error":"model requires more memory"}'))

    data = oc._ollama_vision_json(
        fake_ollama.image, prompt="x", schema=oc.FRAME_V4_SCHEMA
    )

    assert "_error" in data
    # str(HTTPError) is only "HTTP Error 400: Bad Request" — useless on its own.
    assert "model requires more memory" in data["_error"]


def test_non_http_failures_keep_the_schema(fake_ollama):
    reply = {"has_woman": True, "exposure_tier": 2, "confidence": 0.8}
    sent = fake_ollama(lambda: TimeoutError("timed out"), reply)

    data = oc._ollama_vision_json(
        fake_ollama.image, prompt="x", schema=oc.FRAME_V4_SCHEMA
    )

    assert data == reply
    assert "format" in sent[1], "a timeout says nothing about the schema"


# ── panel-array validation ───────────────────────────────────────────


def test_repeated_panel_index_counts_once():
    data = {
        "panels": [
            {"i": 1, "has_woman": True, "exposure_tier": 1},
            {"i": 1, "has_woman": True, "exposure_tier": 4},
            {"i": 1, "has_woman": True, "exposure_tier": 4},
        ]
    }
    tier, peak, panels = oc._aggregate_sheet_panels(data, 9)

    assert len(panels) == 1, "one index, one reading"
    assert tier == 1, "first reading wins; re-reads must not outvote unseen panels"
    assert peak == 1


def test_partial_panel_coverage_is_recorded_but_trusted():
    # Omitting empty panels is sensible model behaviour: max-over-panels treats
    # an absent panel exactly like tier 0, so the reading is still correct.
    data = {"panels": [{"i": 8, "has_woman": True, "exposure_tier": 4}]}
    tier, peak, panels = oc._aggregate_sheet_panels(data, 9)

    assert tier == 4
    assert peak == 8
    assert len(panels) == 1


@pytest.fixture
def stub_sheet(monkeypatch):
    def install(sheet_reply, frame_reply=None):
        calls = []

        def fake(image_path, prompt=oc.CLASSIFY_PROMPT, **kwargs):
            calls.append(prompt)
            if "CONTACT SHEET" in prompt:
                return dict(sheet_reply)
            return dict(frame_reply or {})

        monkeypatch.setattr(oc, "_ollama_vision_json", fake)
        return calls

    return install


def test_unreadable_sheet_falls_back_to_an_ordinal_frame(tmp_path, stub_sheet):
    path = _make_clip(tmp_path / "reel.mp4")
    # Schema satisfied, but not one usable panel row — and a rollup that would
    # have scored 4 if we trusted it.
    calls = stub_sheet(
        {"panels": [], "reel_exposure": 4, "confidence": 0.9},
        frame_reply={"has_woman": True, "exposure_tier": 1, "confidence": 0.8},
    )

    verdict = oc.classify_video(path)

    assert verdict.ok
    assert verdict.exposure_tier == 1, "must score the frame, not the unaudited rollup"
    assert verdict.glam_score == 0
    assert verdict.evidence["mode"] == "sheet_fallback_frame"
    assert any("CONTACT SHEET" in c for c in calls)
    assert any("CONTACT SHEET" not in c for c in calls)


def test_sheet_fallback_keeps_the_ordinal_vocabulary(tmp_path, stub_sheet):
    """The legacy per-frame prompt is the generous one that saturated the scale."""
    path = _make_clip(tmp_path / "reel.mp4")
    calls = stub_sheet(
        {"panels": [], "confidence": 0.5},
        frame_reply={"has_woman": True, "exposure_tier": 3, "confidence": 0.7},
    )

    verdict = oc.classify_video(path)

    assert verdict.prompt_version == oc.CLASSIFY_FRAME_V4_VERSION
    assert not any("GENEROUS" in c for c in calls)


# ── confirm escalation ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "tier, conf, last_shot, expected",
    [
        (2, 0.9, False, True),  # Sexy-filter boundary
        (3, 0.9, False, True),  # Sexy-filter boundary
        (1, 0.9, False, False),  # clearly modest, nothing to settle
        (4, 0.9, False, False),  # unambiguous, and not from the reveal shot
        (4, 0.9, True, True),  # the reveal case — confirm before trusting
        (1, 0.3, False, True),  # unsure at any tier
        (0, 0.9, True, False),  # no woman: last shot is irrelevant
    ],
)
def test_confirm_escalation_includes_the_final_shot(tier, conf, last_shot, expected):
    assert oc._needs_confirm(tier, conf, last_shot) is expected


def test_confirm_defaults_to_ignoring_shot_position():
    assert oc._needs_confirm(4, 0.9) is False


# ── evidence accuracy ────────────────────────────────────────────────


def test_frames_considered_counts_decoded_candidates_not_panels(
    tmp_path, stub_sheet
):
    path = _make_clip(tmp_path / "reel.mp4", seconds=3.0)
    stub_sheet(
        {
            "panels": [{"i": 1, "has_woman": True, "exposure_tier": 1}],
            "confidence": 0.9,
        }
    )

    verdict = oc.classify_video(path)

    considered = verdict.evidence["frames_considered"]
    panels = verdict.evidence["sheet"]["panels"]
    assert considered > panels, (
        "frames_considered must report the candidates ranked, not the panels "
        "kept — otherwise the sheet path looks like it saw far less than it did"
    )
    assert considered == verdict.evidence["sheet"]["considered"]


# ── keep semantics ───────────────────────────────────────────────────


def _verdict(**kw):
    v = oc.PostVerdict(path="x", ok=True, **kw)
    v.glam_score = v.compute_glam_score()
    return v


def test_keep_agrees_with_the_gallery_filter_at_low_confidence():
    """The v4 prompt reports sub-0.5 confidence whenever it is ambiguous.

    The old confidence gate made those reads show up in the Sexy filter while
    every other surface called them rejects.
    """
    v = _verdict(has_woman=True, exposure_tier=3, confidence=0.4)

    assert v.glam_score >= oc.GLAM_SEXY_MIN
    assert v.matches_keep(), "gallery shows it; keep must agree"


def test_keep_still_rejects_below_the_threshold():
    v = _verdict(has_woman=True, exposure_tier=2, confidence=0.95)

    assert v.glam_score < oc.GLAM_SEXY_MIN
    assert not v.matches_keep(), "high confidence must not promote a low tier"


def test_keep_matches_the_filter_across_every_tier():
    for tier in range(5):
        v = _verdict(has_woman=True, exposure_tier=tier, confidence=0.3)
        assert v.matches_keep() == (v.glam_score >= oc.GLAM_SEXY_MIN), tier


def test_legacy_verdicts_keep_their_meaning():
    # glam 2 under the boolean vocabulary: one flag set.
    v = _verdict(has_woman=True, sexy_revealing_outfit=True, confidence=0.4)
    assert v.glam_score == 2
    assert v.matches_keep()

    modest = _verdict(has_woman=True, confidence=0.9)
    assert modest.glam_score == 1
    assert not modest.matches_keep()


def test_unscored_verdict_is_never_a_keep():
    assert not oc.PostVerdict(path="x", error="boom").matches_keep()


# ── thumbnails ───────────────────────────────────────────────────────


def test_unclassified_video_with_a_cover_does_not_decode_the_timeline(
    tmp_path, monkeypatch
):
    path = _make_clip(tmp_path / "2026-01-01_00-00-00_UTC.mp4")
    cover = tmp_path / "2026-01-01_00-00-00_UTC.jpg"
    cv2.imwrite(str(cover), np.full((214, 120, 3), 90, dtype=np.uint8))

    from promptstudio.scraping import video_frames as vf

    decoded = []
    real = vf.select_best_video_frames
    monkeypatch.setattr(
        vf,
        "select_best_video_frames",
        lambda *a, **k: (decoded.append(a), real(*a, **k))[1],
    )

    out = thumbs.ensure_thumbnail(
        path, "c/2026-01-01_00-00-00_UTC.mp4", thumb_dir=str(tmp_path / "_t")
    )

    assert out and os.path.isfile(out)
    assert not decoded, (
        "a companion cover is a file copy; ranking the whole timeline for it "
        "puts a 16-frame decode on every gallery scroll"
    )


def test_video_without_a_cover_still_gets_a_thumbnail(tmp_path):
    path = _make_clip(tmp_path / "solo.mp4")

    out = thumbs.ensure_thumbnail(path, "c/solo.mp4", thumb_dir=str(tmp_path / "_t"))

    assert out and os.path.isfile(out)
