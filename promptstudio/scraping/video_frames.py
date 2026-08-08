"""Cheap OpenCV frame sampling / ranking for reels (classify + thumbs).

Reels are not stills: the payoff outfit is usually revealed in the *last*
seconds, mid-motion. Two consequences drive the design here:

* Sampling covers the whole timeline including the tail (see
  ``CLASSIFY_REEL_SKIP_TAIL_FRAC``, default 0.0).
* Ranking uses skin fraction as the primary signal and treats sharpness as an
  adequacy gate rather than a score, because the reveal frame is typically the
  blurriest moment of the clip.

Decoded frames are downscaled to ``_DECODE_MAX_EDGE`` before measurement, so
Laplacian variance is comparable across clips of different resolutions but is
not on the same absolute scale as full-resolution values.
"""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from promptstudio.config import (
    CLASSIFY_REEL_CANDIDATES,
    CLASSIFY_REEL_CUT_THRESHOLD,
    CLASSIFY_REEL_MIN_BRIGHT,
    CLASSIFY_REEL_MIN_SHARP,
    CLASSIFY_REEL_SHARP_REF,
    CLASSIFY_REEL_SHEET_PANEL_W,
    CLASSIFY_REEL_SHEET_PANELS,
    CLASSIFY_REEL_SKIN_WEIGHT,
    CLASSIFY_REEL_SKIP_HEAD_FRAC,
    CLASSIFY_REEL_SKIP_TAIL_FRAC,
)

# Frames are measured and composited at this size; the cascade re-seeks the
# original file when it needs full resolution.
_DECODE_MAX_EDGE = 720

# Classic YCrCb skin-tone envelope. Fires on sand/wood/warm walls too, which is
# why skin is a ranking term with a floor and never a rejection gate.
_SKIN_LO = (0, 133, 77)
_SKIN_HI = (255, 173, 127)


@dataclass
class FrameMetrics:
    t_sec: float
    bright: float
    sharp: float
    rank: float
    skin: float = 0.0
    frame_index: int = -1
    shot: int = 0


@dataclass
class FramePick:
    """A ranked frame optionally written to a JPEG path (caller may delete)."""

    t_sec: float
    bright: float
    sharp: float
    rank: float
    skin: float = 0.0
    path: str = ""  # empty if not written
    frame_index: int = -1
    shot: int = 0

    def to_dict(self) -> dict:
        return {
            "t": round(float(self.t_sec), 3),
            "bright": round(float(self.bright), 1),
            "sharp": round(float(self.sharp), 1),
            "skin": round(float(self.skin), 4),
            "rank": round(float(self.rank), 2),
            "frame_index": int(self.frame_index),
            "shot": int(self.shot),
        }


@dataclass
class ContactSheet:
    """A grid of chronologically ordered, labelled freeze-frames."""

    path: str
    picks: List[FramePick] = field(default_factory=list)
    cols: int = 0
    rows: int = 0
    panel_w: int = 0
    panel_h: int = 0

    def to_dict(self) -> dict:
        return {
            "cols": int(self.cols),
            "rows": int(self.rows),
            "panels": len(self.picks),
            "panel_w": int(self.panel_w),
            "panel_h": int(self.panel_h),
            "times": [round(float(p.t_sec), 2) for p in self.picks],
        }


def skin_fraction(frame) -> float:
    """Fraction of pixels inside the YCrCb skin envelope, 0.0–1.0."""
    import cv2

    try:
        ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
        mask = cv2.inRange(ycrcb, _SKIN_LO, _SKIN_HI)
        return float(mask.mean()) / 255.0
    except Exception:
        return 0.0


def frame_rank_score(
    bright: float,
    sharp: float,
    skin: float = 0.0,
    skin_weight: Optional[float] = None,
) -> float:
    """
    Rank a frame by how much scoreable subject it carries.

    Sharpness saturates at ``CLASSIFY_REEL_SHARP_REF`` so an adequately sharp
    reveal beats a razor-sharp title card, while genuinely smeared frames still
    fall away. Skin fraction carries the discriminating signal.
    """
    weight = CLASSIFY_REEL_SKIN_WEIGHT if skin_weight is None else float(skin_weight)
    ref = max(1.0, float(CLASSIFY_REEL_SHARP_REF))

    bright_penalty = abs(float(bright) - 120.0) / 120.0
    bright_factor = max(0.15, 1.0 - 0.65 * bright_penalty)
    sharp_factor = min(1.0, math.sqrt(max(0.0, float(sharp)) / ref))
    skin_factor = 0.15 + weight * max(0.0, min(1.0, float(skin)))

    return 100.0 * sharp_factor * skin_factor * bright_factor


def _downscale(frame, max_edge: int = _DECODE_MAX_EDGE):
    import cv2

    h, w = frame.shape[:2]
    scale = min(1.0, float(max_edge) / float(max(w, h, 1)))
    if scale >= 1.0:
        return frame
    return cv2.resize(
        frame,
        (max(1, int(w * scale)), max(1, int(h * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _metrics_bgr(frame) -> Tuple[float, float, float]:
    """Return (brightness, Laplacian variance, skin fraction)."""
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    bright = float(gray.mean())
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return bright, sharp, skin_fraction(frame)


def _hsv_hist(frame):
    """Normalised HS histogram used for shot-cut detection."""
    import cv2

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
    return hist


def _sample_times_sec(
    duration: float,
    n: int,
    head_frac: float,
    tail_frac: float,
) -> List[float]:
    """
    Evenly spaced sample times spanning [head, 1-tail] **inclusive**.

    Endpoints are included deliberately: bin-*centre* sampling silently drops
    the final 1/2n of the clip, which is exactly where reels reveal the outfit.
    """
    if duration <= 0 or n <= 0:
        return [0.0]
    head = max(0.0, min(0.45, float(head_frac)))
    tail = max(0.0, min(0.45, float(tail_frac)))
    if head + tail >= 0.95:
        head, tail = 0.05, 0.05
    start = duration * head
    end = duration * (1.0 - tail)
    # Never ask for the frame past the last decodable one.
    end = min(end, max(start, duration - 0.05))
    if end <= start:
        return [duration * 0.5]
    if n == 1:
        return [(start + end) / 2.0]
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def _read_at_msec(cap, t_sec: float):
    """Seek by milliseconds (more reliable than frame index on H.264)."""
    import cv2

    msec = max(0.0, float(t_sec) * 1000.0)
    cap.set(cv2.CAP_PROP_POS_MSEC, msec)
    ret, frame = cap.read()
    if ret and frame is not None:
        return frame
    # Fallback: frame index from fps
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if fps > 0:
        idx = int(max(0, t_sec * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret and frame is not None:
            return frame
    return None


def _assign_shots(
    items: Sequence[Tuple[FrameMetrics, object]],
    threshold: Optional[float] = None,
) -> None:
    """Label time-ordered candidates with a shot id, in place.

    Samples are sparse, so this detects *scene changes between samples* rather
    than exact cut frames — enough to guarantee one representative per visually
    distinct segment.
    """
    if not items:
        return
    thr = CLASSIFY_REEL_CUT_THRESHOLD if threshold is None else float(threshold)
    try:
        import cv2
    except ImportError:
        return

    shot = 0
    prev_hist = None
    for metrics, frame in items:
        try:
            hist = _hsv_hist(frame)
        except Exception:
            hist = None
        if prev_hist is not None and hist is not None:
            try:
                corr = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL))
            except Exception:
                corr = 1.0
            if corr < thr:
                shot += 1
        metrics.shot = shot
        if hist is not None:
            prev_hist = hist


def _collect_candidates(
    video_path: str,
    *,
    candidates: int,
    head_frac: float,
    tail_frac: float,
) -> List[Tuple[FrameMetrics, object]]:
    """Return time-ordered [(metrics, downscaled_bgr_frame)] with shot ids."""
    import cv2

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if fps > 0 and frame_count > 0:
            duration = frame_count / fps
        elif frame_count > 0 and fps <= 0:
            duration = float(frame_count) / 30.0
            fps = 30.0
        else:
            # Unknown length — grab first frame only
            ret, frame = cap.read()
            if not ret or frame is None:
                return []
            frame = _downscale(frame)
            bright, sharp, skin = _metrics_bgr(frame)
            m = FrameMetrics(
                t_sec=0.0,
                bright=bright,
                sharp=sharp,
                skin=skin,
                rank=frame_rank_score(bright, sharp, skin),
                frame_index=0,
            )
            return [(m, frame)]

        times = _sample_times_sec(duration, max(1, int(candidates)), head_frac, tail_frac)
        out: List[Tuple[FrameMetrics, object]] = []
        for t in times:
            frame = _read_at_msec(cap, t)
            if frame is None:
                continue
            frame = _downscale(frame)
            bright, sharp, skin = _metrics_bgr(frame)
            idx = int(t * fps) if fps > 0 else -1
            m = FrameMetrics(
                t_sec=float(t),
                bright=bright,
                sharp=sharp,
                skin=skin,
                rank=frame_rank_score(bright, sharp, skin),
                frame_index=idx,
            )
            out.append((m, frame))

        # If seeks failed entirely, sequential subsample
        if not out:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            step = max(1, frame_count // max(1, candidates)) if frame_count > 0 else 10
            i = 0
            grabbed = 0
            while grabbed < candidates:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break
                if i % step == 0:
                    frame = _downscale(frame.copy() if hasattr(frame, "copy") else frame)
                    bright, sharp, skin = _metrics_bgr(frame)
                    t = (i / fps) if fps > 0 else float(i)
                    m = FrameMetrics(
                        t_sec=t,
                        bright=bright,
                        sharp=sharp,
                        skin=skin,
                        rank=frame_rank_score(bright, sharp, skin),
                        frame_index=i,
                    )
                    out.append((m, frame))
                    grabbed += 1
                i += 1

        _assign_shots(out)
        return out
    finally:
        cap.release()


def _dedupe_near(
    ranked: Sequence[Tuple[FrameMetrics, object]],
    min_dt: float = 0.35,
) -> List[Tuple[FrameMetrics, object]]:
    kept: List[Tuple[FrameMetrics, object]] = []
    for m, fr in ranked:
        if any(abs(m.t_sec - km.t_sec) < min_dt for km, _ in kept):
            continue
        kept.append((m, fr))
    return kept


def _spread_by_time(
    items: Sequence[Tuple[FrameMetrics, object]],
    k: int,
) -> List[Tuple[FrameMetrics, object]]:
    """Evenly spaced sample of a time-ordered run (endpoints included)."""
    n = len(items)
    if k >= n:
        return list(items)
    if k <= 1:
        return [max(items, key=lambda x: x[0].rank)]
    idxs = sorted({int(round(i * (n - 1) / (k - 1))) for i in range(k)})
    return [items[i] for i in idxs]


def _quality_pool(
    raw: Sequence[Tuple[FrameMetrics, object]],
    bright_floor: float,
    sharp_floor: float,
) -> List[Tuple[FrameMetrics, object]]:
    """
    Drop dark/smeared frames while keeping every shot represented.

    The gate exists to remove garbage, not to decide what matters. Applied
    globally, an absolute sharpness floor deletes a whole motion-blurred
    shot — and on a reveal reel that is the only shot worth scoring. So a shot
    where nothing clears the floor is thinned rather than erased.
    """
    if not raw:
        return []

    def passes(m: FrameMetrics) -> bool:
        return m.bright >= bright_floor and m.sharp >= sharp_floor

    if not any(passes(m) for m, _ in raw):
        return list(raw)

    by_shot: dict[int, List[Tuple[FrameMetrics, object]]] = {}
    for item in raw:
        by_shot.setdefault(item[0].shot, []).append(item)

    kept: List[Tuple[FrameMetrics, object]] = []
    for shot in sorted(by_shot):
        items = by_shot[shot]
        passed = [item for item in items if passes(item[0])]
        kept.extend(passed if passed else _spread_by_time(items, max(1, len(items) // 2)))

    kept.sort(key=lambda x: x[0].t_sec)
    return kept


def _to_pick(m: FrameMetrics, path: str = "") -> FramePick:
    return FramePick(
        t_sec=m.t_sec,
        bright=m.bright,
        sharp=m.sharp,
        skin=m.skin,
        rank=m.rank,
        path=path,
        frame_index=m.frame_index,
        shot=m.shot,
    )


def _write_pick_jpeg(frame, jpeg_quality: int) -> str:
    import cv2

    fd, tmp = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        cv2.imwrite(tmp, frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
        return tmp
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return ""


def select_best_video_frames(
    video_path: str,
    *,
    top_n: int = 1,
    candidates: Optional[int] = None,
    min_bright: Optional[float] = None,
    min_sharp: Optional[float] = None,
    head_frac: Optional[float] = None,
    tail_frac: Optional[float] = None,
    write_jpeg: bool = True,
    jpeg_quality: int = 85,
    guarantee_last_shot: bool = True,
) -> List[FramePick]:
    """
    Sample candidate frames, drop dark/blurry ones, return top_n ranked picks.

    Picks are drawn from distinct shots where possible. When ``top_n >= 2`` the
    final shot is always represented — that is where reveal reels land.

    When write_jpeg=True, each pick has a temp .jpg path the caller should delete
    (unless they adopt it as a thumbnail).
    """
    if not video_path or not os.path.isfile(video_path):
        return []

    n_cand = max(1, int(candidates if candidates is not None else CLASSIFY_REEL_CANDIDATES))
    top_n = max(1, int(top_n))
    bright_floor = float(min_bright if min_bright is not None else CLASSIFY_REEL_MIN_BRIGHT)
    sharp_floor = float(min_sharp if min_sharp is not None else CLASSIFY_REEL_MIN_SHARP)
    head = float(head_frac if head_frac is not None else CLASSIFY_REEL_SKIP_HEAD_FRAC)
    tail = float(tail_frac if tail_frac is not None else CLASSIFY_REEL_SKIP_TAIL_FRAC)

    raw = _collect_candidates(video_path, candidates=n_cand, head_frac=head, tail_frac=tail)
    if not raw:
        return []

    pool = _quality_pool(raw, bright_floor, sharp_floor)

    # Best frame per shot, so picks are visually distinct rather than three
    # samples of the same static take.
    by_shot: dict[int, Tuple[FrameMetrics, object]] = {}
    for m, fr in pool:
        current = by_shot.get(m.shot)
        if current is None or m.rank > current[0].rank:
            by_shot[m.shot] = (m, fr)

    ordered = sorted(by_shot.values(), key=lambda x: x[0].rank, reverse=True)
    # Any shot the per-shot reduction dropped is still usable filler. Compare by
    # identity — these tuples hold numpy frames, which have no scalar __eq__.
    chosen_ids = {id(item[1]) for item in ordered}
    leftovers = sorted(
        (item for item in pool if id(item[1]) not in chosen_ids),
        key=lambda x: x[0].rank,
        reverse=True,
    )
    ordered.extend(leftovers)

    span = max(0.001, pool[-1][0].t_sec - pool[0][0].t_sec)
    ordered = _dedupe_near(ordered, min_dt=min(0.35, span / 12.0))

    if guarantee_last_shot and top_n >= 2 and by_shot:
        last_item = by_shot[max(by_shot)]
        head_ids = {id(item[1]) for item in ordered[:top_n]}
        if id(last_item[1]) not in head_ids:
            # Demote the weakest of the current picks rather than the strongest.
            ordered = ordered[: top_n - 1] + [last_item] + ordered[top_n - 1 :]

    try:
        import cv2  # noqa: F401
    except ImportError:
        return []

    picks: List[FramePick] = []
    for m, fr in ordered[:top_n]:
        path = _write_pick_jpeg(fr, jpeg_quality) if write_jpeg else ""
        if write_jpeg and not path:
            continue
        picks.append(_to_pick(m, path))
    return picks


def _allocate_panels(sizes: Sequence[int], n_panels: int) -> List[int]:
    """
    Split n_panels across shots in proportion to screen time, at least 1 each.

    Uniform *time* buckets straddle cuts, letting a long intro shot win the
    bucket that contains the reveal. Allocating per shot instead gives every
    distinct segment representation proportional to how much of the reel it is.
    """
    n_shots = len(sizes)
    if n_shots == 0:
        return []
    total = sum(sizes) or 1
    exact = [n_panels * s / total for s in sizes]
    alloc = [1] * n_shots

    # Hand out the remaining panels by largest fractional shortfall, never
    # giving a shot more panels than it has candidate frames.
    remaining = n_panels - n_shots
    while remaining > 0:
        eligible = [i for i in range(n_shots) if alloc[i] < sizes[i]]
        if not eligible:
            break
        i = max(eligible, key=lambda j: exact[j] - alloc[j])
        alloc[i] += 1
        remaining -= 1

    return [min(a, s) for a, s in zip(alloc, sizes, strict=True)]


def select_timeline_frames(
    video_path: str,
    *,
    panels: Optional[int] = None,
    candidates: Optional[int] = None,
    min_bright: Optional[float] = None,
    min_sharp: Optional[float] = None,
) -> List[Tuple[FramePick, object]]:
    """
    Pick frames spanning the whole clip, **chronologically ordered**.

    Unlike ``select_best_video_frames`` this optimises for timeline coverage,
    not peak quality — the contact sheet needs to show the whole arc in order,
    so panels are allocated per shot and the final shot is always represented.
    The sharpness gate is deliberately not applied: a soft frame still tells the
    model what the outfit is, and on reveal reels the soft frames are the point.

    Returns [(pick, bgr_frame)]; picks have no JPEG path.
    """
    n_panels = max(1, int(panels if panels is not None else CLASSIFY_REEL_SHEET_PANELS))
    n_cand = max(
        n_panels, int(candidates if candidates is not None else CLASSIFY_REEL_CANDIDATES)
    )
    bright_floor = float(min_bright if min_bright is not None else CLASSIFY_REEL_MIN_BRIGHT)

    raw = _collect_candidates(
        video_path,
        candidates=n_cand,
        head_frac=CLASSIFY_REEL_SKIP_HEAD_FRAC,
        tail_frac=CLASSIFY_REEL_SKIP_TAIL_FRAC,
    )
    if not raw:
        return []

    by_shot: dict[int, List[Tuple[FrameMetrics, object]]] = {}
    for item in raw:
        by_shot.setdefault(item[0].shot, []).append(item)

    # Black frames carry nothing — but never let that empty a whole shot.
    shots: List[List[Tuple[FrameMetrics, object]]] = []
    for shot in sorted(by_shot):
        items = by_shot[shot]
        lit = [item for item in items if item[0].bright >= bright_floor]
        shots.append(lit or [max(items, key=lambda x: x[0].rank)])

    if len(shots) > n_panels:
        # More shots than panels: keep the longest ones, plus the final shot.
        keep = sorted(range(len(shots)), key=lambda i: len(shots[i]), reverse=True)
        keep = set(keep[:n_panels]) | {len(shots) - 1}
        shots = [shots[i] for i in sorted(keep)][-n_panels:]

    alloc = _allocate_panels([len(s) for s in shots], n_panels)

    chosen: List[Tuple[FrameMetrics, object]] = []
    for shot_items, k in zip(shots, alloc, strict=True):
        if k > 0:
            chosen.extend(_spread_by_time(shot_items, k))

    chosen.sort(key=lambda x: x[0].t_sec)
    return [(_to_pick(m), fr) for m, fr in chosen[:n_panels]]


def _label_panel(panel, text: str) -> None:
    """Burn a panel index / timestamp caption so the model can cite panels."""
    import cv2

    h, w = panel.shape[:2]
    bar_h = max(16, w // 11)
    cv2.rectangle(panel, (0, 0), (w, bar_h), (0, 0, 0), -1)
    scale = max(0.35, w / 560.0)
    cv2.putText(
        panel,
        text,
        (max(2, w // 60), int(bar_h * 0.78)),
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    # Border keeps adjacent panels visually separate in the montage.
    cv2.rectangle(panel, (0, 0), (w - 1, h - 1), (90, 90, 90), 1)


def compose_contact_sheet(
    video_path: str,
    *,
    panels: Optional[int] = None,
    panel_w: Optional[int] = None,
    candidates: Optional[int] = None,
    jpeg_quality: int = 88,
    out_path: str = "",
) -> Optional[ContactSheet]:
    """
    Render a labelled grid of chronological freeze-frames spanning the reel.

    One vision call over this sheet sees the whole timeline — including the
    final-seconds reveal that single-frame sampling misses. Caller owns the
    returned JPEG and should delete it.
    """
    if not video_path or not os.path.isfile(video_path):
        return None
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    n_panels = max(1, int(panels if panels is not None else CLASSIFY_REEL_SHEET_PANELS))
    width = max(96, int(panel_w if panel_w is not None else CLASSIFY_REEL_SHEET_PANEL_W))

    selected = select_timeline_frames(
        video_path, panels=n_panels, candidates=candidates
    )
    if not selected:
        return None

    src_h, src_w = selected[0][1].shape[:2]
    height = max(96, int(round(width * (src_h / max(1, src_w)))))

    cols = int(math.ceil(math.sqrt(len(selected))))
    rows = int(math.ceil(len(selected) / cols))
    sheet = np.zeros((rows * height, cols * width, 3), dtype=np.uint8)

    picks: List[FramePick] = []
    for i, (pick, frame) in enumerate(selected):
        panel = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        _label_panel(panel, f"{i + 1}  {pick.t_sec:.1f}s")
        r, c = divmod(i, cols)
        sheet[r * height : (r + 1) * height, c * width : (c + 1) * width] = panel
        picks.append(pick)

    path = out_path
    if not path:
        fd, path = tempfile.mkstemp(suffix=".jpg", prefix="reelsheet-")
        os.close(fd)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        cv2.imwrite(path, sheet, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    if not os.path.isfile(path):
        return None

    return ContactSheet(
        path=path,
        picks=picks,
        cols=cols,
        rows=rows,
        panel_w=width,
        panel_h=height,
    )


def extract_frame_at(
    video_path: str,
    t_sec: float,
    *,
    jpeg_quality: int = 90,
    max_edge: int = 0,
) -> str:
    """Decode one frame at t_sec to a temp JPEG. Returns "" on failure."""
    if not video_path or not os.path.isfile(video_path):
        return ""
    try:
        import cv2
    except ImportError:
        return ""

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return ""
    try:
        frame = _read_at_msec(cap, max(0.0, float(t_sec)))
    finally:
        cap.release()
    if frame is None:
        return ""
    if max_edge and max_edge > 0:
        frame = _downscale(frame, max_edge)
    return _write_pick_jpeg(frame, jpeg_quality)


def write_best_video_frame_jpeg(
    video_path: str,
    out_path: str,
    *,
    max_edge: int = 0,
    jpeg_quality: int = 82,
    at_sec: Optional[float] = None,
) -> bool:
    """
    Decode a representative frame and write JPEG to out_path.

    ``at_sec`` pins a known-good moment (e.g. the classifier's peak panel);
    otherwise the best-ranked frame is used. max_edge>0 downscales the longest
    side. Returns True on success.
    """
    import shutil

    import cv2

    temp_paths: List[str] = []
    if at_sec is not None:
        src = extract_frame_at(video_path, float(at_sec), jpeg_quality=jpeg_quality)
        if src:
            temp_paths.append(src)
    else:
        src = ""

    if not src:
        picks = select_best_video_frames(
            video_path, top_n=1, write_jpeg=True, jpeg_quality=jpeg_quality
        )
        temp_paths.extend(p.path for p in picks if p.path)
        if not picks or not picks[0].path:
            return False
        src = picks[0].path

    try:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        if max_edge and max_edge > 0:
            img = cv2.imread(src)
            if img is None:
                return False
            h, w = img.shape[:2]
            scale = min(1.0, float(max_edge) / float(max(w, h)))
            if scale < 1.0:
                img = cv2.resize(
                    img,
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            cv2.imwrite(out_path, img, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
        else:
            if os.path.abspath(src) != os.path.abspath(out_path):
                shutil.copyfile(src, out_path)
        return os.path.isfile(out_path)
    finally:
        for p in temp_paths:
            if p and os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def find_video_cover_image(video_path: str) -> Optional[str]:
    """
    Look for this video's own companion still (Instaloader cover).

    Only ``{stem}.{ext}`` counts. Carousel siblings (``..._UTC_1.jpg`` next to
    ``..._UTC_3.mp4``) are *different slides*, not this video's cover — matching
    them made reels inherit an unrelated photo's score.
    """
    if not video_path:
        return None
    base, _ = os.path.splitext(video_path)
    for ext in (".jpg", ".jpeg", ".webp", ".png"):
        candidate = base + ext
        if os.path.isfile(candidate):
            return candidate
    return None
