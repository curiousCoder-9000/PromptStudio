"""Cheap OpenCV frame sampling / ranking for reels (classify + thumbs)."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from promptstudio.config import (
    CLASSIFY_REEL_CANDIDATES,
    CLASSIFY_REEL_MIN_BRIGHT,
    CLASSIFY_REEL_MIN_SHARP,
    CLASSIFY_REEL_SKIP_HEAD_FRAC,
    CLASSIFY_REEL_SKIP_TAIL_FRAC,
)


@dataclass
class FrameMetrics:
    t_sec: float
    bright: float
    sharp: float
    rank: float
    frame_index: int = -1


@dataclass
class FramePick:
    """A ranked frame optionally written to a JPEG path (caller may delete)."""

    t_sec: float
    bright: float
    sharp: float
    rank: float
    path: str = ""  # empty if not written
    frame_index: int = -1

    def to_dict(self) -> dict:
        return {
            "t": round(float(self.t_sec), 3),
            "bright": round(float(self.bright), 1),
            "sharp": round(float(self.sharp), 1),
            "rank": round(float(self.rank), 1),
            "frame_index": int(self.frame_index),
        }


def frame_rank_score(bright: float, sharp: float) -> float:
    """Prefer sharp frames with mid-range brightness (not crushed black/white)."""
    # Peak brightness preference around ~115–130 (typical well-exposed IG)
    bright_penalty = abs(float(bright) - 120.0) / 120.0
    bright_factor = max(0.15, 1.0 - 0.65 * bright_penalty)
    return max(0.0, float(sharp)) * bright_factor


def _metrics_bgr(frame) -> Tuple[float, float]:
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    bright = float(gray.mean())
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return bright, sharp


def _sample_times_sec(
    duration: float,
    n: int,
    head_frac: float,
    tail_frac: float,
) -> List[float]:
    if duration <= 0 or n <= 0:
        return [0.0]
    head = max(0.0, min(0.45, float(head_frac)))
    tail = max(0.0, min(0.45, float(tail_frac)))
    if head + tail >= 0.95:
        head, tail = 0.05, 0.05
    start = duration * head
    end = duration * (1.0 - tail)
    if end <= start:
        return [duration * 0.5]
    if n == 1:
        return [(start + end) / 2.0]
    return [start + (end - start) * (i + 0.5) / n for i in range(n)]


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


def _collect_candidates(
    video_path: str,
    *,
    candidates: int,
    head_frac: float,
    tail_frac: float,
) -> List[Tuple[FrameMetrics, object]]:
    """Return list of (metrics, bgr_frame)."""
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
            bright, sharp = _metrics_bgr(frame)
            m = FrameMetrics(
                t_sec=0.0,
                bright=bright,
                sharp=sharp,
                rank=frame_rank_score(bright, sharp),
                frame_index=0,
            )
            return [(m, frame)]

        times = _sample_times_sec(duration, max(1, int(candidates)), head_frac, tail_frac)
        out: List[Tuple[FrameMetrics, object]] = []
        for t in times:
            frame = _read_at_msec(cap, t)
            if frame is None:
                continue
            bright, sharp = _metrics_bgr(frame)
            idx = int(t * fps) if fps > 0 else -1
            m = FrameMetrics(
                t_sec=float(t),
                bright=bright,
                sharp=sharp,
                rank=frame_rank_score(bright, sharp),
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
                    bright, sharp = _metrics_bgr(frame)
                    t = (i / fps) if fps > 0 else float(i)
                    m = FrameMetrics(
                        t_sec=t,
                        bright=bright,
                        sharp=sharp,
                        rank=frame_rank_score(bright, sharp),
                        frame_index=i,
                    )
                    out.append((m, frame.copy() if hasattr(frame, "copy") else frame))
                    grabbed += 1
                i += 1
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
) -> List[FramePick]:
    """
    Sample candidate frames, drop dark/blurry ones, return top_n ranked picks.

    When write_jpeg=True, each pick has a temp .jpg path the caller should delete
    (unless they adopt it as a thumbnail).
    """
    if not video_path or not os.path.isfile(video_path):
        return []

    n_cand = int(candidates if candidates is not None else CLASSIFY_REEL_CANDIDATES)
    n_cand = max(1, n_cand)
    top_n = max(1, int(top_n))
    bright_floor = float(
        min_bright if min_bright is not None else CLASSIFY_REEL_MIN_BRIGHT
    )
    sharp_floor = float(min_sharp if min_sharp is not None else CLASSIFY_REEL_MIN_SHARP)
    head = float(
        head_frac if head_frac is not None else CLASSIFY_REEL_SKIP_HEAD_FRAC
    )
    tail = float(
        tail_frac if tail_frac is not None else CLASSIFY_REEL_SKIP_TAIL_FRAC
    )

    raw = _collect_candidates(
        video_path, candidates=n_cand, head_frac=head, tail_frac=tail
    )
    if not raw:
        return []

    # Prefer frames that pass quality gates; fall back to all if none pass
    passed = [
        (m, fr)
        for m, fr in raw
        if m.bright >= bright_floor and m.sharp >= sharp_floor
    ]
    pool = passed if passed else list(raw)
    pool.sort(key=lambda x: x[0].rank, reverse=True)
    pool = _dedupe_near(pool)

    picks: List[FramePick] = []
    try:
        import cv2
    except ImportError:
        return []

    for m, fr in pool[:top_n]:
        path = ""
        if write_jpeg:
            fd, tmp = tempfile.mkstemp(suffix=".jpg")
            os.close(fd)
            try:
                cv2.imwrite(tmp, fr, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
                path = tmp
            except Exception:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                continue
        picks.append(
            FramePick(
                t_sec=m.t_sec,
                bright=m.bright,
                sharp=m.sharp,
                rank=m.rank,
                path=path,
                frame_index=m.frame_index,
            )
        )
    return picks


def write_best_video_frame_jpeg(
    video_path: str,
    out_path: str,
    *,
    max_edge: int = 0,
    jpeg_quality: int = 82,
) -> bool:
    """
    Decode best-ranked frame and write JPEG to out_path.
    max_edge>0 downscales longest side. Returns True on success.
    """
    import shutil

    import cv2

    picks = select_best_video_frames(
        video_path, top_n=1, write_jpeg=True, jpeg_quality=jpeg_quality
    )
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
            cv2.imwrite(
                out_path, img, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)]
            )
        else:
            if os.path.abspath(src) != os.path.abspath(out_path):
                shutil.copyfile(src, out_path)
        return os.path.isfile(out_path)
    finally:
        for p in picks:
            if p.path and os.path.isfile(p.path):
                try:
                    os.remove(p.path)
                except OSError:
                    pass


def find_video_cover_image(video_path: str) -> Optional[str]:
    """
    Look for a companion still next to a reel (Instaloader cover / carousel jpg).
    """
    if not video_path:
        return None
    base, _ = os.path.splitext(video_path)
    parent = os.path.dirname(video_path)
    stem = os.path.basename(base)
    candidates = [
        base + ".jpg",
        base + ".jpeg",
        base + ".webp",
        base + ".png",
    ]
    # Carousel-style: ..._UTC_3.mp4 often has ..._UTC_1.jpg
    if "_UTC_" in stem:
        head = stem.rsplit("_UTC_", 1)[0] + "_UTC"
        for i in range(1, 6):
            for ext in (".jpg", ".jpeg", ".webp", ".png"):
                candidates.append(os.path.join(parent, f"{head}_{i}{ext}"))
        candidates.append(os.path.join(parent, head + ".jpg"))

    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None
