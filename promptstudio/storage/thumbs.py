"""Thumbnail generation for gallery performance."""

import os
from typing import Optional

from promptstudio.config import THUMB_DIR, THUMB_MAX_SIZE
from promptstudio.logging_setup import get_logger

log = get_logger(__name__)


def thumb_rel_path(rel_path: str) -> str:
    # Keep folder structure under _thumbs/
    safe = rel_path.replace("\\", "/").lstrip("/")
    return safe


def thumb_disk_path(rel_path: str, thumb_dir: str = THUMB_DIR) -> str:
    return os.path.join(thumb_dir, thumb_rel_path(rel_path))


# A 1x1 GIF the colour of an empty card, served when a tile's thumbnail does
# not exist yet and the worker did not finish inside the request. Bytes rather
# than a file: it has to work before `_thumbs/` exists, without Pillow, and
# without a CDN (rule 15). The GET path used to fall back to the *original*
# here — a 3.5 MB decode inside a 220 px box.
PLACEHOLDER_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x1a\x1a$\x00\x00\x00!\xf9\x04\x00"
    b"\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)
PLACEHOLDER_CONTENT_TYPE = "image/gif"


def ensure_thumbnail(
    full_path: str,
    rel_path: str,
    max_size: int = THUMB_MAX_SIZE,
    thumb_dir: str = THUMB_DIR,
) -> Optional[str]:
    """
    Create a JPEG thumbnail if missing. Returns absolute thumb path or None.
    Uses Pillow when available; falls back to OpenCV.
    Videos use quality-ranked mid-clip frames (not first frame only).

    **This is worker-side work.** Callers are `storage.thumb_queue`'s pool and
    `scripts/backfill_thumbnails.py`. `GET /media/thumb/` only reaches it when
    `THUMB_WORKERS=0`, because the video branch below decodes an entire timeline
    to rank frames and that has no business happening on a request thread —
    see the module docstring in `thumb_queue.py`.

    `optimize=True` is gone from the JPEG saves. Measured on a 1.85 MB source:
    28.6 ms with it, 25.7 ms without, for 2.8 KB more output over loopback.
    Pillow's `thumbnail()` already applies `draft()` DCT scaling, so there is no
    cheaper decode left to find here — the win was never in the encoder flags,
    it was in not doing this sixty times inside sixty HTTP requests.
    """
    out_path = thumb_disk_path(rel_path, thumb_dir)
    # Always store thumbs as .jpg for consistency
    root, _ = os.path.splitext(out_path)
    out_path = root + ".jpg"

    if os.path.isfile(out_path) and os.path.getmtime(out_path) >= os.path.getmtime(full_path):
        return out_path

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if full_path.lower().endswith((".mp4", ".webm")):
        try:
            from promptstudio.scraping.video_frames import (
                find_video_cover_image,
                write_best_video_frame_jpeg,
            )

            # There used to be a cheap first branch here: seek straight to the
            # peak timestamp the glam classifier had recorded in the sidecar. It
            # went out with the classifier — nothing writes `peak_time_sec` any
            # more, so the branch was permanently dead and only cost a sidecar
            # read per tile. The cover-still branch below is now the cheap path.
            #
            # Companion cover still (this video's own, not a carousel sibling)
            cover = find_video_cover_image(full_path)
            if cover and os.path.isfile(cover):
                try:
                    from PIL import Image

                    with Image.open(cover) as img:
                        img = img.convert("RGB")
                        img.thumbnail((max_size, max_size))
                        img.save(out_path, "JPEG", quality=82)
                    if os.path.isfile(out_path):
                        return out_path
                except Exception:
                    pass

            # No cover — rank frames. Last because it is the
            # only branch that decodes the whole timeline.
            if write_best_video_frame_jpeg(
                full_path, out_path, max_edge=max_size, jpeg_quality=82
            ):
                return out_path
        except Exception as e:
            log.warning("video thumbnail failed for %s: %s", rel_path, e)

        # Last resort: first frame
        try:
            import cv2

            cap = cv2.VideoCapture(full_path)
            if not cap.isOpened():
                return None
            ret, frame = cap.read()
            cap.release()
            if not ret or frame is None:
                return None
            h, w = frame.shape[:2]
            scale = min(max_size / max(w, 1), max_size / max(h, 1), 1.0)
            if scale < 1.0:
                frame = cv2.resize(
                    frame,
                    (int(w * scale), int(h * scale)),
                    interpolation=cv2.INTER_AREA,
                )
            cv2.imwrite(out_path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            return out_path
        except Exception as e:
            log.warning("video thumbnail fallback failed for %s: %s", rel_path, e)
            return None

    try:
        from PIL import Image

        with Image.open(full_path) as img:
            img = img.convert("RGB")
            img.thumbnail((max_size, max_size))
            img.save(out_path, "JPEG", quality=82)
        return out_path
    except Exception:
        pass

    try:
        import cv2

        img = cv2.imread(full_path)
        if img is None:
            return None
        h, w = img.shape[:2]
        scale = min(max_size / max(w, 1), max_size / max(h, 1), 1.0)
        if scale < 1.0:
            img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        cv2.imwrite(out_path, img, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
        return out_path
    except Exception as e:
        log.warning("thumbnail failed for %s: %s", rel_path, e)
        return None


def resolve_thumb_file(rel_path: str, thumb_dir: str = THUMB_DIR) -> Optional[str]:
    """Return existing thumb path for a media rel_path, or None."""
    root, _ = os.path.splitext(thumb_disk_path(rel_path, thumb_dir))
    candidate = root + ".jpg"
    if os.path.isfile(candidate):
        return candidate
    return None


def thumb_url(rel_path: str) -> str:
    import urllib.parse

    return f"/media/thumb/{urllib.parse.quote(rel_path.replace(chr(92), '/'))}"
