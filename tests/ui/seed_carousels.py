"""Seed carousel posts into a UI-test archive.

`test_post_grouping.js` needs media that shares a `post_id`, which only a real
Instagram scrape produces — so the fixture is written straight into the index,
the same way `seed_verdicts.py` and `seed_sources.py` do it.

One creator, four posts, deliberately shaped:

* `c1` —  4 slides, the ordinary carousel
* `c2` — 11 slides, so lexicographic order (1, 10, 11, 2 …) is visibly wrong
         and the natural sort has something to prove
* two singles with no post_id at all — a group of one, which must render as a
  plain tile with no badge

17 files, 4 tiles. Everything the suite asserts is scoped to this creator,
because earlier suites delete photos out of `test_creator`.
"""

from __future__ import annotations

import os
import sys

# Must be set before promptstudio.config is imported.
ARCHIVE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PROMPTSTUDIO_ARCHIVE", "")
if ARCHIVE:
    os.environ["PROMPTSTUDIO_ARCHIVE"] = ARCHIVE

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from PIL import Image

from promptstudio.storage.db import ArchiveIndex

CREATOR = "nadia"
CAROUSELS = {"c1": 4, "c2": 11}
SINGLES = ("alone_a.jpg", "alone_b.jpg")


def main() -> int:
    if not ARCHIVE:
        sys.stderr.write("seed_carousels: PROMPTSTUDIO_ARCHIVE not set\n")
        return 1

    index = ArchiveIndex.get()
    index.ensure_ready()

    directory = os.path.join(ARCHIVE, CREATOR)
    os.makedirs(directory, exist_ok=True)

    seeded = 0
    for post_id, slides in CAROUSELS.items():
        for n in range(1, slides + 1):
            name = f"{post_id}_{n}.jpg"
            Image.new("RGB", (400, 500), (30 * n % 256, 90, 200)).save(
                os.path.join(directory, name), "JPEG"
            )
            index.upsert_photo(
                f"{CREATOR}/{name}", post_id=post_id, shortcode=post_id
            )
            seeded += 1

    for name in SINGLES:
        Image.new("RGB", (400, 500), (200, 40, 90)).save(
            os.path.join(directory, name), "JPEG"
        )
        index.upsert_photo(f"{CREATOR}/{name}", post_id="", shortcode="")
        seeded += 1

    grouped, posts = index.query_photos(creator=CREATOR, group_posts=True)
    sys.stdout.write(
        f"seed_carousels: {seeded} files -> {posts} posts "
        f"({[g['group_count'] for g in grouped]})\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
