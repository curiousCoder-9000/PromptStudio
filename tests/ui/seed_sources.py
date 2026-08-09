"""Seed multi-source media into a UI-test archive.

`test_source_filter.js` asserts that the source pills cross-filter the sidebar
and the gallery, which needs media whose `photos.source` is something other than
`instagram`. Nothing in the HTTP API can produce that without running a real
gallery-dl scrape, so the fixture is written directly — same reason
`seed_verdicts.py` exists.

Three shapes, because they are the three the filter has to get right:

* `test_creator`   — Instagram only (what run.sh already seeded)
* `kaya__x`        — X only, suffixed folder, the normal non-default case
* `mira`           — one bare folder holding BOTH platforms, i.e. the
  `SCRAPE_FOLDER_SUFFIX=0` case that makes folder-name parsing wrong
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

# (folder, filename, source)
_SEED = [
    ("kaya__x", "x_01.jpg", "x"),
    ("kaya__x", "x_02.jpg", "x"),
    ("kaya__x", "x_03.jpg", "x"),
    ("mira", "ig_01.jpg", "instagram"),
    ("mira", "ig_02.jpg", "instagram"),
    ("mira", "x_01.jpg", "x"),
    ("mira", "x_02.jpg", "x"),
    ("mira", "x_03.jpg", "x"),
    ("mira", "x_04.jpg", "x"),
]


def main() -> int:
    if not ARCHIVE:
        print("seed_sources: PROMPTSTUDIO_ARCHIVE not set", file=sys.stderr)
        return 1

    index = ArchiveIndex.get()
    index.ensure_ready()

    for i, (folder, name, source) in enumerate(_SEED):
        directory = os.path.join(ARCHIVE, folder)
        os.makedirs(directory, exist_ok=True)
        full = os.path.join(directory, name)
        Image.new("RGB", (400, 500), (40, 30 * i % 256, 170)).save(full, "JPEG")
        index.upsert_photo(
            f"{folder}/{name}",
            source=source,
            post_id=f"{source}_{i:03d}",
        )

    creators = {c["name"]: c["sources"] for c in index.list_creators()}
    print(f"seed_sources: {len(_SEED)} rows -> {creators}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
