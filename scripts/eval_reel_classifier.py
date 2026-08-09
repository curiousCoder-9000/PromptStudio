#!/usr/bin/env python3
"""
Glam classifier evaluation: sample -> label -> run -> report.

Without this every quality claim about the classifier is an opinion. Run it on
the machine that holds the real archive. `--kind reel` (default) or
`--kind photo`; the two are separate sets with separate labels.

    # 1. stratified sample; contact sheets for reels, previews for photos
    py scripts/eval_reel_classifier.py sample --kind photo --count 120 --open

    # 2. label with 0-4 in the browser (progress auto-saves), Export, then:
    py scripts/eval_reel_classifier.py label --kind photo --import ~/Downloads/labels.jsonl

    # 3. A/B the photo vocabularies (needs Ollama). Compare on glam_accuracy.
    py scripts/eval_reel_classifier.py run --kind photo --name legacy --legacy
    py scripts/eval_reel_classifier.py run --kind photo --name ordinal --ordinal
    py scripts/eval_reel_classifier.py report --kind photo --name ordinal --against legacy

    # Flip CLASSIFY_PHOTO_ORDINAL=1 only if ordinal wins glam_accuracy AND
    # top_score_share drops (the saturated glam-3 problem).

    # Reels (contact-sheet pipeline is already the default production path):
    py scripts/eval_reel_classifier.py sample --kind reel --count 120 --open
    py scripts/eval_reel_classifier.py run --kind reel --name baseline
    py scripts/eval_reel_classifier.py report --kind reel --name baseline

Everything lives under <archive>/_eval/ — never in the repo, because the labels
encode personal taste over personal media. Back that folder up.
"""

import argparse
import os
import sys
import webbrowser

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from promptstudio.config import (
    CLASSIFY_PHOTO_ORDINAL,
    GLAM_SEXY_MIN,
    MODEL_NAME,
    SAVED_DIR,
)
from promptstudio.evalset import (
    EVAL_DIR,
    KINDS,
    SHEETS_DIR,
    SPLITS,
    EvalItem,
    assign_splits,
    choose_sample,
    compute_metrics,
    eval_status,
    file_url,
    filter_split,
    labels_file,
    list_results,
    load_labels,
    load_results,
    meets_target,
    merge_labels,
    render_compare_page,
    render_label_page,
    render_photo_preview,
    save_labels,
    save_results,
    score_item,
    split_histogram,
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


def _open_page(path: str) -> None:
    if not path or not os.path.isfile(path):
        print(f"(no page at {path})")
        return
    url = file_url(path)
    print(f"Opening: {url}")
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"Could not open browser ({e}). Paste the file:// URL above manually.")


def cmd_status(args) -> None:
    print(f"archive: {SAVED_DIR}")
    print(f"eval:    {EVAL_DIR}")
    print(f"model:   {MODEL_NAME}")
    print(f"CLASSIFY_PHOTO_ORDINAL={1 if CLASSIFY_PHOTO_ORDINAL else 0}")
    print()
    kinds = [args.kind] if args.kind else list(KINDS)
    for kind in kinds:
        st = eval_status(kind)
        print(f"=== {kind} ===")
        print(f"  sample:   {st['sample_size']}  (previews: {st['with_preview']})")
        print(f"  labelled: {st['labelled']}/{st['sample_size']}  remaining {st['remaining']}")
        if st["true_tiers"]:
            print("  true tiers: " + ", ".join(f"{k}={v}" for k, v in st["true_tiers"].items()))
        for half, hist in (st["splits"] or {}).items():
            counts = ", ".join(f"T{t}={n}" for t, n in hist.items())
            print(f"  {half:<10} n={sum(hist.values()):<4} {counts}")
        page = st["label_page"]
        if os.path.isfile(page):
            print(f"  label UI: {file_url(page)}")
        else:
            print("  label UI: (run sample first)")
        print(f"  results:  {', '.join(st['results']) or '(none yet)'}")
        print()


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
    print(f"labels:  {labels_file(kind)}")
    print(f"\nOpen:   {file_url(page)}")
    keys = "0-4, 'r' for reveal-at-end" if kind == "reel" else "0-4"
    print(f"Label with {keys}, then Export and:")
    print(f"  py scripts/eval_reel_classifier.py label --kind {kind} --import <labels.jsonl>")
    if kind == "photo":
        print("\nAfter labelling, A/B the photo vocabularies:")
        print(f"  py scripts/eval_reel_classifier.py run --kind photo --name legacy --legacy")
        print(f"  py scripts/eval_reel_classifier.py run --kind photo --name ordinal --ordinal")
        print(f"  py scripts/eval_reel_classifier.py report --kind photo --name ordinal --against legacy")
    if args.open:
        _open_page(page)


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
    page = os.path.join(EVAL_DIR, f"label-{kind}.html")
    if len(done) < len(items):
        print(f"\nKeep going: {file_url(page)}")
    else:
        print("\nAll labelled. Next:")
        if kind == "photo":
            print("  py scripts/eval_reel_classifier.py run --kind photo --name legacy --legacy")
            print("  py scripts/eval_reel_classifier.py run --kind photo --name ordinal --ordinal")
            print("  py scripts/eval_reel_classifier.py report --kind photo --name ordinal --against legacy")
        else:
            print(f"  py scripts/eval_reel_classifier.py run --kind {kind} --name baseline")
    if args.open:
        _open_page(page)


def cmd_split(args) -> None:
    """Assign a dev/test half to every labelled item. Idempotent by default."""
    kind = args.kind
    path = labels_file(kind)
    items = load_labels(path)
    labelled = [i for i in items if i.is_labelled()]
    if not labelled:
        print(f"Nothing labelled for {kind} yet — run `sample --kind {kind}` first.")
        sys.exit(1)

    if args.reassign:
        already = sum(1 for i in labelled if i.split)
        if already:
            print(
                f"--reassign re-draws all {already} existing assignments. Any `test`\n"
                "number you have already looked at becomes a dev number, because you\n"
                "tuned against items that are about to move into test."
            )
            if input("Type 'reassign' to confirm: ").strip() != "reassign":
                print("Aborted; nothing written.")
                return

    changed = assign_splits(
        items, seed=args.seed, test_frac=args.test_frac, reassign=args.reassign
    )
    save_labels(items, path)
    print(f"{changed} item(s) assigned (seed {args.seed}, test_frac {args.test_frac})")
    for half, hist in split_histogram(items).items():
        counts = ", ".join(f"T{t}={n}" for t, n in hist.items())
        print(f"  {half:<10} n={sum(hist.values()):<4} {counts}")
    print(
        "\nTune on dev only:\n"
        f"  py scripts/eval_reel_classifier.py run --kind {kind} --name <cand> --split dev\n"
        "Score test ONCE, on the candidate you intend to ship. If you look at test\n"
        "and then tune, it is a dev set now and you need a fresh sample."
    )


def cmd_run(args) -> None:
    from promptstudio.config import CLASSIFY_PHOTO_ORDINAL as env_ordinal
    from promptstudio.scraping.outfit_classifier import ollama_reachable

    kind = args.kind
    all_labelled = [i for i in load_labels(labels_file(kind)) if i.is_labelled()]
    labelled = filter_split(all_labelled, args.split)
    if not all_labelled:
        print(f"Nothing labelled for {kind} yet.")
        print(f"  py scripts/eval_reel_classifier.py sample --kind {kind} --open")
        sys.exit(1)
    if not labelled:
        print(
            f"No labelled {kind}s in split '{args.split}' "
            f"({len(all_labelled)} labelled overall)."
        )
        print(f"  py scripts/eval_reel_classifier.py split --kind {kind}")
        sys.exit(1)
    if args.split in SPLITS:
        print(f"split: {args.split} ({len(labelled)}/{len(all_labelled)} labelled items)")
    if not ollama_reachable():
        print(f"Ollama not reachable (need {MODEL_NAME}).")
        sys.exit(1)

    # Resolve vocabulary for photos. Reels always use the live video path.
    ordinal_flag = None  # None = env default for photos
    if kind == "photo":
        if args.ordinal and args.legacy:
            print("Pass only one of --ordinal / --legacy.")
            sys.exit(2)
        if args.ordinal:
            ordinal_flag = True
        elif args.legacy:
            ordinal_flag = False
        else:
            ordinal_flag = bool(env_ordinal)

    vocab = (
        "n/a (reel pipeline)"
        if kind == "reel"
        else ("ordinal/v4" if ordinal_flag else "legacy/boolean")
    )
    print(f"Scoring {len(labelled)} labelled {kind}(s) with {MODEL_NAME} [{vocab}]...")
    results = []
    for n, item in enumerate(labelled, start=1):
        results.append(score_item(item.rel_path, kind=kind, ordinal=ordinal_flag))
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
            "photo_ordinal": ordinal_flag if kind == "photo" else None,
            "vocab": vocab,
            # Recorded so a dev-only run can never be mistaken for a full-set
            # number six weeks later when only the metrics survive.
            "split": args.split or "all",
        },
        kind=kind,
    )
    print(f"Wrote {path}")
    _print_report(args.name, kind=kind, split=args.split)


def _fmt(name, value, unit=""):
    ok = meets_target(name, value)
    mark = "" if ok is None else ("  PASS" if ok else "  FAIL")
    return f"{value}{unit}{mark}"


def _print_report(name: str, against: str = "", kind: str = "reel", split: str = "") -> None:
    all_labels = load_labels(labels_file(kind))
    labels = filter_split(all_labels, split)
    results, meta = load_results(name, kind)
    if not results:
        print(f"No {kind} results '{name}'. Have: {', '.join(list_results(kind)) or 'none'}")
        return
    # Without this, an unassigned split reports every metric as 0.0 FAIL, which
    # reads as "the classifier collapsed" rather than "you filtered to nothing".
    if not labels and any(i.is_labelled() for i in all_labels):
        print(
            f"No labelled {kind}s in split '{split}'. Assign halves first:\n"
            f"  py scripts/eval_reel_classifier.py split --kind {kind}"
        )
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
    if meta.get("vocab"):
        print(f"vocab {meta.get('vocab')}")
    ran_on = meta.get("split") or "all"
    scored_on = split or "all"
    print(f"scored on: {scored_on}   (run recorded split: {ran_on})")
    if scored_on != ran_on and ran_on != "all":
        print(
            f"  ! this run only scored the '{ran_on}' half — metrics on "
            f"'{scored_on}' cover just the overlap"
        )
    print(f"{m.scored} scored of {m.labelled} labelled\n")

    # The product decision first: the Sexy filter asks exactly one binary
    # question, `glam_score >= GLAM_SEXY_MIN`, and these are it. glam_accuracy
    # is 4-way exact match, which no surface in the app asks for.
    rows = [
        ("keep_f1", m.keep_f1, f"  <- HEADLINE (filter @ glam>={GLAM_SEXY_MIN})"),
        ("keep_recall", m.keep_recall, f"  ({m.keep_true} true keeps in set)"),
        ("keep_precision", m.keep_precision, f"  ({m.keep_pred} predicted keeps)"),
        ("glam_accuracy", m.glam_accuracy, "  (0-3 exact; secondary sanity check)"),
    ]
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
        ("top_score_share", m.top_score_share, "  (weak: a perfect run is ~0.36)"),
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

    if m.per_tier:
        axis = m.confusion_axis
        # Only diff against the baseline when both ran on the same axis. A
        # legacy-boolean baseline buckets by glam, so "tier 3 precision was X"
        # would be quoting the glam-3 number — the same apples-to-oranges trap
        # the tier metrics above already guard against.
        comparable = base is not None and base.confusion_axis == axis
        print(f"\n  per-{axis}:  {'':<4}{'prec':>7}{'recall':>8}{'n_true':>8}{'n_pred':>8}")
        if base is not None and not comparable:
            print(f"    (baseline scored on the {base.confusion_axis} axis — not comparable)")
        for cls, s in m.per_tier.items():
            base_s = (base.per_tier.get(cls) if comparable else None) or {}
            was = f"   (prec was {base_s['precision']})" if base_s else ""
            print(
                f"    {axis[0].upper()}{cls:<8}{s['precision']:>7}{s['recall']:>8}"
                f"{int(s['n_true']):>8}{int(s['n_pred']):>8}{was}"
            )

    failures = [k for k, v, _ in rows if meets_target(k, v) is False]
    print("\n" + ("All targets met." if not failures else "Missing targets: " + ", ".join(failures)))

    if kind == "photo" and against:
        # plan_photo_ordinal_holdout_v7.md §4.1. The old rule (glam_accuracy >=
        # legacy AND top_score_share drops) is satisfiable by a classifier that
        # is worse at the only question the product asks, so it no longer
        # decides. Both are printed; only the first one is the gate.
        print(
            f"\nShip rule: keep_f1 improves over the baseline AND keep_precision "
            f">= 0.90 AND\n  true-tier-2 recall >= 0.85 (see per-tier above) — "
            f"confirmed on the held-out half."
        )
        if base is not None:
            better = m.keep_f1 > base.keep_f1
            print(
                f"  keep_f1 {m.keep_f1} vs {base.keep_f1} "
                f"({'improves' if better else 'does NOT improve'})"
                f" · keep_precision {m.keep_precision}"
            )
        print(
            "  (legacy rule, for continuity only: glam_accuracy >= baseline "
            "AND top_score_share drops)"
        )


def cmd_report(args) -> None:
    _print_report(args.name, args.against, kind=args.kind, split=args.split)


def cmd_view(args) -> None:
    """Write a visual card grid (truth vs two runs) and optionally open it."""
    kind = args.kind
    primary = args.name
    against = args.against or ("legacy" if primary == "ordinal" else "")
    if not against:
        print("--against is required (baseline run name, e.g. legacy)")
        sys.exit(2)
    out = os.path.join(EVAL_DIR, f"compare-{kind}-{primary}-vs-{against}.html")
    try:
        path = render_compare_page(kind, primary, against, out)
    except ValueError as e:
        print(e)
        sys.exit(1)
    print(f"Wrote {path}")
    print(f"Open:  {file_url(path)}")
    if args.open:
        _open_page(path)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_kind(parser, *, required=False, default="reel"):
        parser.add_argument(
            "--kind",
            choices=KINDS,
            default=None if required else default,
            required=required,
            help="reel (contact sheets) or photo (the image itself)",
        )

    st = sub.add_parser("status", help="show sample/label/results progress")
    st.add_argument(
        "--kind", choices=KINDS, default=None,
        help="one kind only (default: both)",
    )
    st.set_defaults(func=cmd_status)

    s = sub.add_parser("sample", help="pick items and render label previews")
    s.add_argument("--count", type=int, default=120)
    s.add_argument("--seed", type=int, default=20260809, help="same seed = same sample")
    s.add_argument("--resheet", action="store_true", help="re-render existing sheets")
    s.add_argument("--open", action="store_true", help="open the labelling page in a browser")
    add_kind(s)
    s.set_defaults(func=cmd_sample)

    lab = sub.add_parser("label", help="show progress / import an export")
    lab.add_argument("--import", dest="import_path", default="", help="labels.jsonl to merge")
    lab.add_argument("--open", action="store_true", help="open the labelling page")
    add_kind(lab)
    lab.set_defaults(func=cmd_label)

    def add_split(parser):
        parser.add_argument(
            "--split",
            choices=(*SPLITS, "all"),
            default="all",
            help="score one half only (default: all). Tune on dev; touch test once.",
        )

    sp = sub.add_parser("split", help="assign a dev/test half to each labelled item")
    sp.add_argument("--seed", type=int, default=20260811, help="same seed = same split")
    sp.add_argument(
        "--test-frac", dest="test_frac", type=float, default=0.5,
        help="fraction held out (default 0.5)",
    )
    sp.add_argument(
        "--reassign", action="store_true",
        help="re-draw existing assignments (invalidates any test number you have seen)",
    )
    add_kind(sp, default="photo")
    sp.set_defaults(func=cmd_split)

    r = sub.add_parser("run", help="score the labelled set (needs Ollama)")
    r.add_argument("--name", default="run", help="name this run, e.g. baseline")
    add_split(r)
    r.add_argument(
        "--ordinal", action="store_true",
        help="photos only: force v4 exposure_tier vocabulary",
    )
    r.add_argument(
        "--legacy", action="store_true",
        help="photos only: force the old three-boolean prompt",
    )
    add_kind(r)
    r.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="metrics for a run")
    rep.add_argument("--name", default="run")
    rep.add_argument("--against", default="", help="baseline run to diff against")
    add_split(rep)
    add_kind(rep)
    rep.set_defaults(func=cmd_report)

    v = sub.add_parser(
        "view",
        help="visual HTML compare: truth vs two runs (opens browser)",
    )
    v.add_argument("--name", default="ordinal", help="candidate run (default: ordinal)")
    v.add_argument("--against", default="legacy", help="baseline run (default: legacy)")
    v.add_argument(
        "--open", action="store_true", default=True,
        help="open in browser (default on)",
    )
    v.add_argument(
        "--no-open", action="store_false", dest="open",
        help="only write the HTML file",
    )
    add_kind(v, default="photo")
    v.set_defaults(func=cmd_view)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
