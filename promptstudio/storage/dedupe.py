"""Perceptual hashing for near-duplicate detection.

The archive accumulates the same image more than once: a creator reposts, two
creators post the same shot, a carousel slide reappears cropped or recompressed.
Byte-identical matching (`scripts/deduplicate.py`) misses all of those, because
a re-encode changes every byte while looking identical.

A DCT perceptual hash survives re-encoding, resizing and mild cropping: it
throws away everything except the low-frequency structure, which is what the eye
actually reads. Two files whose hashes differ in a handful of bits are the same
picture.

No new dependencies — OpenCV and numpy are already here for the reel classifier.

    h = compute_phash("a.jpg")
    groups = find_near_duplicate_groups({"a.jpg": h1, "b.jpg": h2}, max_distance=8)

Deliberately report-only. Nothing here deletes: grouping is a heuristic, and the
archive has a trash/restore flow that a heuristic should go through, not around.
"""

from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from promptstudio.config import VIDEO_EXTENSIONS
from promptstudio.logging_setup import get_logger

log = get_logger(__name__)

# 32x32 greyscale -> DCT -> top-left 8x8 block -> 64 bits.
_DCT_INPUT = 32
_HASH_EDGE = 8
HASH_BITS = _HASH_EDGE * _HASH_EDGE

# Distance at which two files are treated as the same picture. 0-4 is a
# re-encode or resize, 5-10 usually a crop or a watermark, >12 drifts into
# "similar composition" and starts grouping distinct photos.
DEFAULT_MAX_DISTANCE = 8


def _grey_for_hash(path: str):
    """Load a media file as a small greyscale array, or None."""
    import cv2

    if path.lower().endswith(VIDEO_EXTENSIONS):
        # Reuse the classifier's ranker so a reel hashes on the same frame the
        # thumbnail shows — and so a repost trimmed by half a second still picks
        # the same content rather than the same timestamp.
        from promptstudio.scraping.video_frames import select_best_video_frames

        picks = select_best_video_frames(path, top_n=1, write_jpeg=True)
        try:
            if not picks or not picks[0].path:
                return None
            img = cv2.imread(picks[0].path, cv2.IMREAD_GRAYSCALE)
        finally:
            for p in picks:
                if p.path and os.path.isfile(p.path):
                    try:
                        os.remove(p.path)
                    except OSError:
                        pass
        return img

    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        # OpenCV is patchy on some webp/HEIC builds; Pillow reads what it won't.
        try:
            import numpy as np
            from PIL import Image

            with Image.open(path) as pil:
                img = np.array(pil.convert("L"))
        except Exception:
            return None
    return img


def compute_phash(path: str) -> Optional[int]:
    """64-bit DCT perceptual hash of an image or video. None if unreadable."""
    if not path or not os.path.isfile(path):
        return None
    try:
        import cv2
        import numpy as np

        grey = _grey_for_hash(path)
        if grey is None or getattr(grey, "size", 0) == 0:
            return None

        small = cv2.resize(
            grey, (_DCT_INPUT, _DCT_INPUT), interpolation=cv2.INTER_AREA
        ).astype(np.float32)
        block = cv2.dct(small)[:_HASH_EDGE, :_HASH_EDGE]

        # The DC term carries overall brightness, which is exactly what we want
        # to be invariant to — excluded from the median so a lighter copy of the
        # same photo hashes the same.
        flat = block.flatten()
        median = float(np.median(flat[1:]))

        bits = 0
        for i, value in enumerate(flat):
            if value > median:
                bits |= 1 << i
        return bits
    except Exception as e:
        log.debug("phash failed for %s: %s", path, e)
        return None


def phash_hex(value: int) -> str:
    """16-char hex. Stored as TEXT: a 64-bit hash overflows SQLite's signed int."""
    return f"{int(value) & ((1 << HASH_BITS) - 1):016x}"


def phash_from_hex(text: str) -> Optional[int]:
    try:
        return int(str(text), 16)
    except (TypeError, ValueError):
        return None


def hamming(a: int, b: int) -> int:
    """Number of differing bits — the distance between two hashes."""
    return int(a ^ b).bit_count()


def _distances(hashes, target: int):
    """Vectorised Hamming distance from `target` to every hash in the array."""
    import numpy as np

    xor = np.bitwise_xor(hashes, np.uint64(target))
    try:
        return np.bitwise_count(xor)  # numpy >= 2.0
    except AttributeError:
        table = np.unpackbits(xor.view(np.uint8).reshape(-1, 8), axis=1)
        return table.sum(axis=1)


def find_similar(
    hashes: Dict[str, int],
    target: int,
    max_distance: int = DEFAULT_MAX_DISTANCE,
) -> List[Tuple[str, int]]:
    """Paths within `max_distance` of `target`, closest first."""
    if not hashes:
        return []
    import numpy as np

    paths = list(hashes)
    packed = np.fromiter(
        (h & ((1 << HASH_BITS) - 1) for h in hashes.values()),
        dtype=np.uint64,
        count=len(paths),
    )
    dist = _distances(packed, target)
    hits = [(paths[i], int(dist[i])) for i in np.nonzero(dist <= max_distance)[0]]
    hits.sort(key=lambda item: (item[1], item[0]))
    return hits


def find_near_duplicate_groups(
    hashes: Dict[str, int],
    max_distance: int = DEFAULT_MAX_DISTANCE,
) -> List[List[str]]:
    """
    Cluster paths whose hashes are within `max_distance`.

    Transitive by design (union-find): a re-crop of a re-encode belongs in one
    group with the original even if those two are further apart than the
    threshold. Groups are returned largest first, paths sorted inside.
    """
    if len(hashes) < 2:
        return []
    import numpy as np

    paths = list(hashes)
    packed = np.fromiter(
        (h & ((1 << HASH_BITS) - 1) for h in hashes.values()),
        dtype=np.uint64,
        count=len(paths),
    )

    parent = list(range(len(paths)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    # One vectorised pass per row against the rows after it: O(n^2) comparisons
    # but only O(n) Python iterations, which is what actually costs at 4-5k.
    for i in range(len(paths) - 1):
        dist = _distances(packed[i + 1 :], int(packed[i]))
        for offset in np.nonzero(dist <= max_distance)[0]:
            union(i, i + 1 + int(offset))

    clusters: Dict[int, List[str]] = {}
    for i, path in enumerate(paths):
        clusters.setdefault(find(i), []).append(path)

    groups = [sorted(members) for members in clusters.values() if len(members) > 1]
    groups.sort(key=lambda g: (-len(g), g[0]))
    return groups


def pick_keeper(group: Sequence[str], base_dir: str = "") -> str:
    """
    Suggest which member of a duplicate group to keep: the largest file.

    A suggestion only — file size is a decent proxy for "least recompressed",
    but nothing here acts on it.
    """

    def size(rel: str) -> int:
        full = os.path.join(base_dir, *rel.split("/")) if base_dir else rel
        try:
            return os.path.getsize(full)
        except OSError:
            return 0

    return max(sorted(group), key=size)


def iter_media_paths(base_dir: str, excluded: Iterable[str] = ()) -> List[str]:
    """Archive-relative media paths, skipping the underscore-prefixed folders."""
    from promptstudio.storage.db import is_media_file, normalize_rel_path

    skip = set(excluded)
    out: List[str] = []
    if not os.path.isdir(base_dir):
        return out
    for creator in sorted(os.listdir(base_dir)):
        folder = os.path.join(base_dir, creator)
        if not os.path.isdir(folder) or creator in skip or creator.startswith((".", "_")):
            continue
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if is_media_file(name):
                out.append(normalize_rel_path(f"{creator}/{name}"))
    return out
