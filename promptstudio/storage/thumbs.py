"""Thumbnail generation for gallery performance."""

import os
from typing import Optional

from promptstudio.config import THUMB_DIR, THUMB_MAX_SIZE


def thumb_rel_path(rel_path: str) -> str:
    # Keep folder structure under _thumbs/
    safe = rel_path.replace("\\", "/").lstrip("/")
    return safe


def thumb_disk_path(rel_path: str, thumb_dir: str = THUMB_DIR) -> str:
    return os.path.join(thumb_dir, thumb_rel_path(rel_path))


def _classified_peak_time(full_path: str) -> Optional[float]:
    """Peak-outfit timestamp the reel classifier recorded, if it has run."""
    try:
        from promptstudio.storage.metadata import load_post_metadata

        glam = (load_post_metadata(full_path) or {}).get("glam") or {}
        peak = glam.get("peak_time_sec")
        return float(peak) if peak is not None and float(peak) > 0 else None
    except Exception:
        return None


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

            # The classifier already found the most interesting moment — reuse it
            # so the grid tile shows the reveal rather than the intro title card.
            if write_best_video_frame_jpeg(
                full_path,
                out_path,
                max_edge=max_size,
                jpeg_quality=82,
                at_sec=_classified_peak_time(full_path),
            ):
                return out_path

            # Companion cover still (this video's own, not a carousel sibling)
            cover = find_video_cover_image(full_path)
            if cover and os.path.isfile(cover):
                try:
                    from PIL import Image

                    with Image.open(cover) as img:
                        img = img.convert("RGB")
                        img.thumbnail((max_size, max_size))
                        img.save(out_path, "JPEG", quality=82, optimize=True)
                    if os.path.isfile(out_path):
                        return out_path
                except Exception:
                    pass
        except Exception as e:
            print(f"Video thumbnail error for {rel_path}: {e}")

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
            print(f"Video thumbnail fallback error for {rel_path}: {e}")
            return None

    try:
        from PIL import Image

        with Image.open(full_path) as img:
            img = img.convert("RGB")
            img.thumbnail((max_size, max_size))
            img.save(out_path, "JPEG", quality=82, optimize=True)
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
        print(f"Thumbnail error for {rel_path}: {e}")
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
