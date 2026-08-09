#!/usr/bin/env python3
"""
Reel classifier evaluation: sample -> label -> run -> report.

Without this every quality claim about the classifier is an opinion. Run it on
the machine that holds the real archive.

    # 1. pick a stratified sample and render one contact sheet per reel
    py scripts/eval_reel_classifier.py sample --count 120

    # 2. open the page it prints, label with 0-4 / r, hit Export
    py scripts/eval_reel_classifier.py label --import ~/Downloads/labels.jsonl

    # 3. score the labelled set with the current pipeline (needs Ollama)
    py scripts/eval_reel_classifier.py run --name baseline

    # 4. metrics, and the diff after you change something
    py scripts/eval_reel_classifier.py report --name v4-sheet --against baseline

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
    SHEETS_DIR,
    EvalItem,
    choose_sample,
    compute_metrics,
    list_results,
    load_labels,
    load_results,
    meets_target,
    merge_labels,
    render_label_page,
    save_labels,
    save_results,
    score_item,
    stratify,
)
from promptstudio.storage.db import ArchiveIndex


def _reel_rows():
    index = ArchiveIndex.get()
    index.ensure_ready()
    rows, _ = index.query_photos(media_type="video", limit=None)
    return [{"rel_path": r["rel_path"], "glam_score": r.get("glam_score", -1)} for r in rows]


def cmd_sample(args) -> None:
    from promptstudio.scraping.video_frames import compose_contact_sheet

    rows = _reel_rows()
    if not rows:
        print("No videos in the index. Run the server once, or py server.py, to build it.")
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

    existing = {i.rel_path: i for i in load_labels()}
    os.makedirs(SHEETS_DIR, exist_ok=True)

    items, failed = [], 0
    for n, (rel, stratum) in enumerate(picked, start=1):
        item = existing.get(rel) or EvalItem(rel_path=rel)
        item.stratum = stratum
        item.prior_glam = prior.get(rel, -1)

        sheet_name = rel.replace("/", "__") + ".sheet.jpg"
        sheet_path = os.path.join(SHEETS_DIR, sheet_name)
        if args.resheet or not os.path.isfile(sheet_path):
            full = os.path.join(SAVED_DIR, *rel.split("/"))
            if compose_contact_sheet(full, out_path=sheet_path) is None:
                failed += 1
                continue
        item.sheet = sheet_name
        items.append(item)
        if n % 25 == 0:
            print(f"  {n}/{len(picked)} sheets")

    merged = merge_labels(list(existing.values()), items)
    save_labels(merged)

    page = render_label_page(
        [i for i in merged if i.sheet], os.path.join(EVAL_DIR, "label.html")
    )
    done = sum(1 for i in merged if i.is_labelled())
    print(f"\n{len(items)} sheet(s) ready" + (f", {failed} undecodable" if failed else ""))
    print(f"{done}/{len(merged)} already labelled")
    print(f"\nOpen:   file://{page}")
    print("Label with 0-4, 'r' for reveal-at-end, then Export and:")
    print("  py scripts/eval_reel_classifier.py label --import <downloaded labels.jsonl>")


def cmd_label(args) -> None:
    items = load_labels()
    if not items:
        print("No sample yet — run `sample` first.")
        return

    if args.import_path:
        src = os.path.expanduser(args.import_path)
        if not os.path.isfile(src):
            print(f"Not found: {src}")
            sys.exit(1)
        merged = merge_labels(items, load_labels(src))
        save_labels(merged)
        items = merged
        print(f"Imported {src}")

    done = [i for i in items if i.is_labelled()]
    reveals = [i for i in done if i.reveal_at_end]
    print(f"{len(done)}/{len(items)} labelled · {len(reveals)} marked reveal-at-end")
    if done:
        hist = {}
        for i in done:
            hist[i.true_exposure] = hist.get(i.true_exposure, 0) + 1
        print("true tiers: " + ", ".join(f"{t}={hist.get(t, 0)}" for t in range(5)))
    if len(done) < len(items):
        print(f"\nKeep going: file://{os.path.join(EVAL_DIR, 'label.html')}")


def cmd_run(args) -> None:
    from promptstudio.scraping.outfit_classifier import ollama_reachable

    labelled = [i for i in load_labels() if i.is_labelled()]
    if not labelled:
        print("Nothing labelled yet.")
        return
    if not ollama_reachable():
        print(f"Ollama not reachable (need {MODEL_NAME}).")
        sys.exit(1)

    print(f"Scoring {len(labelled)} labelled reel(s) with {MODEL_NAME}...")
    results = []
    for n, item in enumerate(labelled, start=1):
        results.append(score_item(item.rel_path))
        if n % 10 == 0 or n == len(labelled):
            print(f"  {n}/{len(labelled)}")

    versions = sorted({r.prompt_version for r in results if r.prompt_version})
    path = save_results(
        args.name,
        results,
        {"model": MODEL_NAME, "prompt_versions": versions, "count": len(results)},
    )
    print(f"Wrote {path}")
    _print_report(args.name)


def _fmt(name, value, unit=""):
    ok = meets_target(name, value)
    mark = "" if ok is None else ("  PASS" if ok else "  FAIL")
    return f"{value}{unit}{mark}"


def _print_report(name: str, against: str = "") -> None:
    labels = load_labels()
    results, meta = load_results(name)
    if not results:
        print(f"No results named '{name}'. Available: {', '.join(list_results()) or 'none'}")
        return
    m = compute_metrics(labels, results)

    base = None
    if against:
        base_results, _ = load_results(against)
        if base_results:
            base = compute_metrics(labels, base_results)
        else:
            print(f"(no baseline '{against}')")

    print(f"\n=== {name} ===")
    print(f"model {meta.get('model', '?')} · prompts {', '.join(meta.get('prompt_versions') or []) or '?'}")
    print(f"{m.scored} scored of {m.labelled} labelled\n")

    rows = [
        ("reveal_recall", m.reveal_recall, f"  (n={m.reveal_n})"),
        ("exact_accuracy", m.exact_accuracy, ""),
        ("within_one", m.within_one, ""),
        ("top_score_share", m.top_score_share, ""),
        ("unscored_rate", m.unscored_rate, ""),
        ("median_vision_calls", m.median_vision_calls, ""),
    ]
    for key, value, extra in rows:
        line = f"  {key:<22} {_fmt(key, value)}"
        if base is not None:
            prev = getattr(base, key)
            delta = round(value - prev, 4)
            arrow = "=" if delta == 0 else ("+" if delta > 0 else "")
            line += f"   (was {prev}, {arrow}{delta})"
        print(line + extra)

    print(f"  {'median_ms':<22} {m.median_ms}")
    print(f"  {'mean_signed_error':<22} {m.mean_signed_error}  (>0 = over-scoring)")
    print(f"\n  glam distribution: {m.score_hist}")
    if m.confusion:
        print(f"  true->predicted:   {m.confusion}")

    failures = [k for k, v, _ in rows if meets_target(k, v) is False]
    print("\n" + ("All targets met." if not failures else "Missing targets: " + ", ".join(failures)))


def cmd_report(args) -> None:
    _print_report(args.name, args.against)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="pick reels and render contact sheets")
    s.add_argument("--count", type=int, default=120)
    s.add_argument("--seed", type=int, default=20260809, help="same seed = same sample")
    s.add_argument("--resheet", action="store_true", help="re-render existing sheets")
    s.set_defaults(func=cmd_sample)

    lab = sub.add_parser("label", help="show progress / import an export")
    lab.add_argument("--import", dest="import_path", default="", help="labels.jsonl to merge")
    lab.set_defaults(func=cmd_label)

    r = sub.add_parser("run", help="score the labelled set (needs Ollama)")
    r.add_argument("--name", default="run", help="name this run, e.g. baseline")
    r.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="metrics for a run")
    rep.add_argument("--name", default="run")
    rep.add_argument("--against", default="", help="baseline run to diff against")
    rep.set_defaults(func=cmd_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
