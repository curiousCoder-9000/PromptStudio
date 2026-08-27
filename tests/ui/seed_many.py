"""Seed enough photos that the gallery window is smaller than the pile.

`test_gallery_windowing.js` cannot prove anything at run.sh's default 12
photos: the window covers everything, so a windowed grid and the old
append-everything grid look identical. This adds a few hundred so the mounted
card count has to be a strict subset.

Written straight into the archive + index rather than uploaded, for the same
reason `seed_verdicts.py` is: 240 HTTP uploads would dominate the suite's
runtime and prove nothing extra.
"""

from __future__ import annotations

import os
import sys

ARCHIVE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PROMPTSTUDIO_ARCHIVE", "")
if ARCHIVE:
    os.environ["PROMPTSTUDIO_ARCHIVE"] = ARCHIVE

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from PIL import Image

from promptstudio.storage.db import ArchiveIndex

COUNT = int(sys.argv[2]) if len(sys.argv) > 2 else 240
CREATOR = "window_creator"


def main() -> int:
    folder = os.path.join(ARCHIVE, CREATOR)
    os.makedirs(folder, exist_ok=True)
    # Small and distinguishable: the suite reads `data-rel-path`, never pixels,
    # and 240 large JPEGs would just be slower.
    for n in range(COUNT):
        path = os.path.join(folder, f"win_{n:04d}.jpg")
        if not os.path.isfile(path):
            Image.new("RGB", (40, 50), (n % 256, 80, 160)).save(path, "JPEG")
    index = ArchiveIndex.get()
    index.rebuild()
    # Pre-generate the thumbnails. `rebuild()` deliberately does not enqueue
    # them (that is the backfill CLI's job), so without this every card the
    # window mounts is a miss that waits on a worker — which would make the
    # suite a measurement of the thumbnailer rather than of windowing, and slow
    # enough to look like a hang.
    from promptstudio.storage.thumbs import ensure_thumbnail

    made = 0
    for rel in index.all_photo_paths():
        creator, _, filename = rel.partition("/")
        full = os.path.join(ARCHIVE, creator, filename)
        if os.path.isfile(full) and ensure_thumbnail(full, rel):
            made += 1
    total = index.count()
    print(
        f"seed_many: {COUNT} photos under {CREATOR}/ · index now {total} "
        f"· {made} thumbs ready"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
