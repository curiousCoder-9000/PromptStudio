"""Seed classify verdicts into a UI-test archive.

`test_classify_review.js` asserts on keep/reject badges, tinting and the review
strip, all of which need rows in `media_verdicts`. There is no API to write one
without running the vision model, so the fixture is written directly — same
reason `run.sh` seeds JPEGs rather than uploading them.

Tiers are spread across the whole 0-4 range on purpose: a fixture where every
row lands on one value would pass a test that a saturated classifier also
passes, which is the exact failure this feature exists to make visible.
"""

from __future__ import annotations

import os
import sys

# Must be set before promptstudio.config is imported.
ARCHIVE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PROMPTSTUDIO_ARCHIVE", "")
if ARCHIVE:
    os.environ["PROMPTSTUDIO_ARCHIVE"] = ARCHIVE

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from promptstudio.scraping.media_classifier import CLASSIFY_FRAME_VERSION
from promptstudio.storage.db import ArchiveIndex

# (tier, reason) cycled over the seeded photos in filename order.
_PATTERN = [
    (0, "event flyer, heavy typography"),
    (1, "crewneck sweater, no skin"),
    (3, "crop top + jeans, bare midriff"),
    (2, "sundress, normal length"),
    (4, "bikini set"),
    (-1, ""),  # a failed attempt, so the error state is covered too
]


def main() -> int:
    index = ArchiveIndex.get()
    index.ensure_ready()
    photos, _total = index.query_photos(sort="name")
    if not photos:
        print("seed_verdicts: no photos indexed", file=sys.stderr)
        return 1

    for i, photo in enumerate(photos):
        tier, reason = _PATTERN[i % len(_PATTERN)]
        index.set_verdict(
            photo["rel_path"],
            creator=photo["creator"],
            tier=tier,
            reason=reason,
            media_kind="photo",
            verdict_source="image",
            confidence=0.81,
            prompt_version=CLASSIFY_FRAME_VERSION,
            error=None if tier >= 0 else "vision timeout",
        )

    counts = index.creator_verdict_counts()
    print(f"seed_verdicts: {len(photos)} verdicts -> {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
