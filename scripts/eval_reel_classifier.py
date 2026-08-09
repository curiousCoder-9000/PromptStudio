#!/usr/bin/env python3
"""
Glam classifier evaluation: sample -> label -> run -> report.

Without this every quality claim about the classifier is an opinion. Run it on
the machine that holds the real archive. `--kind reel` (default) or
`--kind photo`; the two are separate sets with separate labels.

    # 1. stratified sample; contact sheets for reels, previews for photos
    py scripts/eval_reel_classifier.py sample --kind reel  --count 120
    py scripts/eval_reel_classifier.py sample --kind photo --count 120

    # 2. open the page it prints, label with 0-4 (and `r` for reels), Export
    py scripts/eval_reel_classifier.py label --kind reel --import ~/Downloads/labels.jsonl

    # 3. record the current pipeline as a baseline (needs Ollama)
    py scripts/eval_reel_classifier.py run --kind reel --name baseline

    # 4. change something, re-run, diff
    py scripts/eval_reel_classifier.py report --kind reel --name v4-sheet --against baseline

To decide CLASSIFY_PHOTO_ORDINAL, run the photo set twice under both settings
and compare `glam_accuracy` — the one axis both vocabularies share:

    py scripts/eval_reel_classifier.py run --kind photo --name legacy
    CLASSIFY_PHOTO_ORDINAL=1 py scripts/eval_reel_classifier.py run --kind photo --name ordinal
    py scripts/eval_reel_classifier.py report --kind photo --name ordinal --against legacy

Everything lives under <archive>/_eval/ — never in the repo, because the labels
encode personal taste over personal media.
"""

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.config import MODEL_NAME, SAVED_DIR
from promptstudio.evalset import (
    EVAL_DIR,
    KINDS,
    SHEETS_DIR,
    EvalItem,
    choose_sample,
    compute_metrics,
    labels_file,
    list_results,
    load_labels,
    load_results,
    meets_target,
    merge_labels,
    render_label_page,
    render_photo_preview,
    save_labels,
    save_results,
    score_item,
    stratify,
)
from promptstudio.storage.db import ArchiveIndex


def _candidate_rows(kind: str):
    index = ArchiveIndex.get()
    index.ensure_ready()
    rows, _ = index.query_photos(
        media_type="video" if kind == "reel" else "photo", limit=None
    )
    return [{"rel_path": r["rel_path"], "glam_score": r.get("glam_score")} for r in rows]


def cmd_sample(args) -> None:
    from promptstudio.scraping.video_frames import compose_contact_sheet

    kind = args.kind
    rows = _candidate_rows(kind)
    if not rows:
        print(f"No {kind}s in the index. Run `py server.py` once to build it.")
        return

    buckets = stratify(rows)
    print("candidates by current score: " + ", ".join(
        f"{k}={len(v)}" for k, v in buckets.items() if v))

    picked = choose_sample(buckets, total=args.count, seed=args.seed)
    # Not `or -1`: glam 0 is falsy but a real verdict, and showing it as
    # "unscored" in the labelling page would mislead the person labelling.
    prior = {
        r["rel_path"]: (-1 if r.get("glam_score") is None else int(r["glam_score"]))
        for r in rows
    }

    existing = {i.rel_path: i for i in load_labels(labels_file(kind))}
    os.makedirs(SHEETS_DIR, exist_ok=True)

    items, failed = [], 0
    for n, (rel, stratum) in enumerate(picked, start=1):
        item = existing.get(rel) or EvalItem(rel_path=rel, kind=kind)
        item.kind = kind
        item.stratum = stratum
        item.prior_glam = prior.get(rel, -1)

        sheet_name = rel.replace("/", "__") + ".sheet.jpg"
        sheet_path = os.path.join(SHEETS_DIR, sheet_name)
        if args.resheet or not os.path.isfile(sheet_path):
            full = os.path.join(SAVED_DIR, *rel.split("/"))
            # A reel needs its whole timeline in one image; a photo is already
            # the thing being judged and only needs downscaling.
            made = (
                compose_contact_sheet(full, out_path=sheet_path) is not None
                if kind == "reel"
                else render_photo_preview(full, sheet_path)
            )
            if not made:
                failed += 1
                continue
        item.sheet = sheet_name
        items.append(item)
        if n % 25 == 0:
            print(f"  {n}/{len(picked)} sheets")

    merged = merge_labels(list(existing.values()), items)
    save_labels(merged, labels_file(kind))

    page = render_label_page(
        [i for i in merged if i.sheet],
        os.path.join(EVAL_DIR, f"label-{kind}.html"),
        kind=kind,
    )
    done = sum(1 for i in merged if i.is_labelled())
    noun = "sheet" if kind == "reel" else "preview"
    print(f"\n{len(items)} {noun}(s) ready" + (f", {failed} unreadable" if failed else ""))
    print(f"{done}/{len(merged)} already labelled")
    print(f"\nOpen:   file://{page}")
    keys = "0-4, 'r' for reveal-at-end" if kind == "reel" else "0-4"
    print(f"Label with {keys}, then Export and:")
    print(f"  py scripts/eval_reel_classifier.py label --kind {kind} --import <labels.jsonl>")


def cmd_label(args) -> None:
    kind = args.kind
    path = labels_file(kind)
    items = load_labels(path)
    if not items:
        print(f"No {kind} sample yet — run `sample --kind {kind}` first.")
        return

    if args.import_path:
        src = os.path.expanduser(args.import_path)
        if not os.path.isfile(src):
            print(f"Not found: {src}")
            sys.exit(1)
        merged = merge_labels(items, load_labels(src))
        save_labels(merged, path)
        items = merged
        print(f"Imported {src}")

    done = [i for i in items if i.is_labelled()]
    print(f"{len(done)}/{len(items)} {kind}s labelled", end="")
    if kind == "reel":
        print(f" · {sum(1 for i in done if i.reveal_at_end)} marked reveal-at-end")
    else:
        print()
    if done:
        hist = {}
        for i in done:
            hist[i.true_exposure] = hist.get(i.true_exposure, 0) + 1
        print("true tiers: " + ", ".join(f"{t}={hist.get(t, 0)}" for t in range(5)))
    if len(done) < len(items):
        print(f"\nKeep going: file://{os.path.join(EVAL_DIR, f'label-{kind}.html')}")


def cmd_run(args) -> None:
    from promptstudio.config import CLASSIFY_PHOTO_ORDINAL
    from promptstudio.scraping.outfit_classifier import ollama_reachable

    kind = args.kind
    labelled = [i for i in load_labels(labels_file(kind)) if i.is_labelled()]
    if not labelled:
        print(f"Nothing labelled for {kind} yet.")
        return
    if not ollama_reachable():
        print(f"Ollama not reachable (need {MODEL_NAME}).")
        sys.exit(1)

    print(f"Scoring {len(labelled)} labelled {kind}(s) with {MODEL_NAME}...")
    if kind == "photo":
        print(f"  CLASSIFY_PHOTO_ORDINAL={'1' if CLASSIFY_PHOTO_ORDINAL else '0'}"
              " — set it and re-run under another name to A/B the vocabulary")
    results = []
    for n, item in enumerate(labelled, start=1):
        results.append(score_item(item.rel_path, kind=kind))
        if n % 10 == 0 or n == len(labelled):
            print(f"  {n}/{len(labelled)}")

    versions = sorted({r.prompt_version for r in results if r.prompt_version})
    path = save_results(
        args.name,
        results,
        {
            "model": MODEL_NAME,
            "prompt_versions": versions,
            "count": len(results),
            "kind": kind,
            "photo_ordinal": bool(CLASSIFY_PHOTO_ORDINAL) if kind == "photo" else None,
        },
        kind=kind,
    )
    print(f"Wrote {path}")
    _print_report(args.name, kind=kind)


def _fmt(name, value, unit=""):
    ok = meets_target(name, value)
    mark = "" if ok is None else ("  PASS" if ok else "  FAIL")
    return f"{value}{unit}{mark}"


def _print_report(name: str, against: str = "", kind: str = "reel") -> None:
    labels = load_labels(labels_file(kind))
    results, meta = load_results(name, kind)
    if not results:
        print(f"No {kind} results '{name}'. Have: {', '.join(list_results(kind)) or 'none'}")
        return
    m = compute_metrics(labels, results)

    base = None
    if against:
        base_results, _ = load_results(against, kind)
        if base_results:
            base = compute_metrics(labels, base_results)
        else:
            print(f"(no baseline '{against}')")

    print(f"\n=== {kind}: {name} ===")
    print(f"model {meta.get('model', '?')} · prompts {', '.join(meta.get('prompt_versions') or []) or '?'}")
    print(f"{m.scored} scored of {m.labelled} labelled\n")

    rows = [("glam_accuracy", m.glam_accuracy, "  (0-3 axis, both vocabularies)")]
    # Reveal only means something for video; a photo run would show a
    # meaningless 0.0 FAIL.
    if m.reveal_n:
        rows.append(("reveal_recall", m.reveal_recall, f"  (n={m.reveal_n})"))
    if m.tier_scored:
        rows += [
            ("exact_accuracy", m.exact_accuracy, f"  (tier, n={m.tier_scored})"),
            ("within_one", m.within_one, ""),
        ]
    rows += [
        ("top_score_share", m.top_score_share, ""),
        ("unscored_rate", m.unscored_rate, ""),
        ("median_vision_calls", m.median_vision_calls, ""),
    ]
    tier_metrics = {"exact_accuracy", "within_one", "mean_signed_error"}
    for key, value, extra in rows:
        line = f"  {key:<22} {_fmt(key, value)}"
        if base is not None:
            if key in tier_metrics and not base.tier_scored:
                # A legacy-boolean baseline produces no tier at all. Printing
                # "was 0.0" would read as "it scored zero" rather than
                # "this axis does not exist for it".
                line += "   (baseline has no tier — compare on glam_accuracy)"
            else:
                prev = getattr(base, key)
                delta = round(value - prev, 4)
                arrow = "=" if delta == 0 else ("+" if delta > 0 else "")
                line += f"   (was {prev}, {arrow}{delta})"
        print(line + extra)

    print(f"  {'median_ms':<22} {m.median_ms}")
    if m.tier_scored:
        print(f"  {'mean_signed_error':<22} {m.mean_signed_error}  (>0 = over-scoring)")
    else:
        print("  (no exposure_tier in this run — legacy boolean prompt)")
    print(f"\n  glam distribution: {m.score_hist}")
    if m.confusion:
        print(f"  true->predicted ({m.confusion_axis}): {m.confusion}")

    failures = [k for k, v, _ in rows if meets_target(k, v) is False]
    print("\n" + ("All targets met." if not failures else "Missing targets: " + ", ".join(failures)))


def cmd_report(args) -> None:
    _print_report(args.name, args.against, kind=args.kind)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_kind(parser):
        parser.add_argument(
            "--kind", choices=KINDS, default="reel",
            help="reel (contact sheets) or photo (the image itself)",
        )

    s = sub.add_parser("sample", help="pick reels and render contact sheets")
    s.add_argument("--count", type=int, default=120)
    s.add_argument("--seed", type=int, default=20260809, help="same seed = same sample")
    s.add_argument("--resheet", action="store_true", help="re-render existing sheets")
    add_kind(s)
    s.set_defaults(func=cmd_sample)

    lab = sub.add_parser("label", help="show progress / import an export")
    lab.add_argument("--import", dest="import_path", default="", help="labels.jsonl to merge")
    add_kind(lab)
    lab.set_defaults(func=cmd_label)

    r = sub.add_parser("run", help="score the labelled set (needs Ollama)")
    r.add_argument("--name", default="run", help="name this run, e.g. baseline")
    add_kind(r)
    r.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="metrics for a run")
    rep.add_argument("--name", default="run")
    rep.add_argument("--against", default="", help="baseline run to diff against")
    add_kind(rep)
    rep.set_defaults(func=cmd_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
