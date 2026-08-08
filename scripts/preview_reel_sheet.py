#!/usr/bin/env python3
"""
Render the contact sheet the reel classifier would send to Ollama — no vision call.

Use this to tune CLASSIFY_REEL_* thresholds by eye: if the reveal is not visible
in a panel, no prompt or model change will recover it.

    py scripts/preview_reel_sheet.py path/to/reel.mp4
    py scripts/preview_reel_sheet.py ~/Pictures/InstagramSaved/someone --limit 5
    py scripts/preview_reel_sheet.py reel.mp4 --panels 12 --out-dir /tmp/sheets
"""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.config import (
    CLASSIFY_REEL_CANDIDATES,
    CLASSIFY_REEL_SHEET_PANELS,
    CLASSIFY_REEL_SKIN_WEIGHT,
    CLASSIFY_REEL_SKIP_TAIL_FRAC,
    VIDEO_EXTENSIONS,
)
from promptstudio.scraping.video_frames import compose_contact_sheet


def _collect(target: str, limit: int) -> list[str]:
    if os.path.isfile(target):
        return [target]
    if not os.path.isdir(target):
        return []
    found = [
        os.path.join(target, name)
        for name in sorted(os.listdir(target))
        if name.lower().endswith(VIDEO_EXTENSIONS)
    ]
    return found[:limit] if limit else found


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", help="video file, or a creator folder")
    ap.add_argument("--panels", type=int, default=CLASSIFY_REEL_SHEET_PANELS)
    ap.add_argument("--candidates", type=int, default=CLASSIFY_REEL_CANDIDATES)
    ap.add_argument("--limit", type=int, default=10, help="max videos from a folder")
    ap.add_argument("--out-dir", default=os.path.join(_REPO_ROOT, "_reel_sheets"))
    args = ap.parse_args()

    videos = _collect(os.path.expanduser(args.target), args.limit)
    if not videos:
        print(f"No videos found at {args.target}")
        sys.exit(1)

    os.makedirs(args.out_dir, exist_ok=True)
    print(
        f"candidates={args.candidates} panels={args.panels} "
        f"tail_skip={CLASSIFY_REEL_SKIP_TAIL_FRAC} skin_weight={CLASSIFY_REEL_SKIN_WEIGHT}\n"
    )

    for video in videos:
        stem = os.path.splitext(os.path.basename(video))[0]
        out = os.path.join(args.out_dir, f"{stem}.sheet.jpg")
        sheet = compose_contact_sheet(
            video, panels=args.panels, candidates=args.candidates, out_path=out
        )
        if sheet is None:
            print(f"FAIL  {os.path.basename(video)}  (no decodable frames)")
            continue
        shots = sorted({p.shot for p in sheet.picks})
        print(f"{os.path.basename(video)}  ->  {out}")
        print(f"  {sheet.cols}x{sheet.rows} panels, {len(shots)} shot(s)")
        for i, pick in enumerate(sheet.picks, start=1):
            print(
                f"  panel {i:>2}  t={pick.t_sec:6.2f}s  shot={pick.shot}  "
                f"skin={pick.skin:.3f}  sharp={pick.sharp:8.1f}  rank={pick.rank:6.2f}"
            )
        print()


if __name__ == "__main__":
    main()
