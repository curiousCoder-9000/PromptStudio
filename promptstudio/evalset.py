"""Labelled evaluation sets for the glam classifier — reels and photos.

Every quality claim about the classifier is otherwise unverifiable: there is no
ground truth, so "did that prompt change help" is an opinion. This is the
missing half — sample, label once by hand, then score any pipeline version
against the same frozen set and compare numbers.

Four stages, each resumable, each per media kind:

    sample   stratified pick; contact sheet per reel, downscaled copy per photo
    label    static HTML page, keyboard-driven, exports JSONL
    run      score the labelled set with the live pipeline
    report   metrics vs labels, diffed against a saved baseline

Reels and photos are separate sets with separate labels. They run through
different pipelines, `reveal_at_end` only applies to video, and one blended
accuracy number would hide which half regressed.

**Two vocabularies, one axis.** The photo path still defaults to the legacy
three-boolean prompt (`CLASSIFY_PHOTO_ORDINAL=0`), which produces no
`exposure_tier` at all — so tier accuracy is undefined for it. Both vocabularies
produce a 0-3 `glam_score`, and `true_glam()` projects the label onto it, which
is what makes legacy-vs-ordinal an A/B rather than two unrelated numbers.

The labels are the durable asset. Sheets and results are regenerable; the
labels represent hours of human judgement and are the one thing worth backing
up. They live under ``<archive>/_eval/`` — outside the repo, because they encode
personal taste over personal media.
"""

from __future__ import annotations

import json
import os
import random
import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from promptstudio.config import SAVED_DIR
from promptstudio.logging_setup import get_logger
from promptstudio.storage.atomic import atomic_write_json, atomic_write_text

log = get_logger(__name__)

EVAL_DIR = os.path.join(SAVED_DIR, "_eval")
SHEETS_DIR = os.path.join(EVAL_DIR, "sheets")
RESULTS_DIR = os.path.join(EVAL_DIR, "results")

# Reels and photos are separate sets: they run through different pipelines, one
# of the labels only applies to video, and mixing them would make a single
# accuracy number that hides which half regressed.
KINDS = ("reel", "photo")


def labels_file(kind: str = "reel") -> str:
    return os.path.join(EVAL_DIR, f"labels-{kind}.jsonl")


def results_file(name: str, kind: str = "reel") -> str:
    return os.path.join(RESULTS_DIR, f"{kind}-{name}.json")


LABELS_FILE = labels_file("reel")

# What the labeller is asked for, mirroring the classifier's own vocabulary so
# predictions and truth are directly comparable.
TIER_ANCHORS = {
    0: "no woman — title card, logo, scenery, men only",
    1: "fully modest — everyday coverage, no skin beyond face/hands",
    2: "normal fashion — some skin (arms, neck, shoulders), fitted",
    3: "revealing — midriff, cleavage, back, thighs; short dress; tight fit",
    4: "maximally revealing — bikini, swimwear, lingerie, bodysuit, sheer",
}


@dataclass
class EvalItem:
    """One media file: what it is, what a human said, what the pipeline said."""

    rel_path: str
    kind: str = "reel"  # reel | photo
    sheet: str = ""
    # Stratum it was sampled from — recorded so a skewed sample is visible.
    stratum: str = ""
    # Pipeline's opinion at sampling time, for the "before" column.
    prior_glam: int = -1

    # ── human labels ──
    true_exposure: Optional[int] = None  # 0-4
    reveal_at_end: Optional[bool] = None
    peak_time_sec: Optional[float] = None
    note: str = ""

    def is_labelled(self) -> bool:
        return self.true_exposure is not None

    def true_glam(self) -> int:
        """The label on the product-visible 0-3 axis.

        Lets a legacy-boolean run and an ordinal run be compared at all: the
        legacy prompt never produces a tier, so tier accuracy is undefined for
        it, but both produce a glam score.
        """
        from promptstudio.scraping.outfit_classifier import TIER_TO_GLAM

        if self.true_exposure is None:
            return -1
        return TIER_TO_GLAM.get(int(self.true_exposure), 0)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvalItem":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


# ── labels file (JSONL, one item per line) ───────────────────────────


def load_labels(path: str = LABELS_FILE) -> List[EvalItem]:
    if not os.path.isfile(path):
        return []
    items: List[EvalItem] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(EvalItem.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TypeError) as e:
                log.warning("skipping unreadable label line: %s", e)
    return items


def save_labels(items: Sequence[EvalItem], path: str = LABELS_FILE) -> None:
    """Atomic: labels are hours of human work and must survive a crash."""
    body = "\n".join(json.dumps(i.to_dict(), ensure_ascii=False) for i in items)
    atomic_write_text(path, body + "\n" if body else "")


def merge_labels(
    existing: Sequence[EvalItem],
    incoming: Sequence[EvalItem],
) -> List[EvalItem]:
    """Fold a labelling export back in, keyed on rel_path.

    Incoming labels win, but only where they exist — re-running `sample` must
    never blank a reel someone already judged.
    """
    by_path = {item.rel_path: item for item in existing}
    for fresh in incoming:
        current = by_path.get(fresh.rel_path)
        if current is None:
            by_path[fresh.rel_path] = fresh
            continue
        if fresh.true_exposure is not None:
            current.true_exposure = fresh.true_exposure
            current.reveal_at_end = fresh.reveal_at_end
            current.peak_time_sec = fresh.peak_time_sec
            current.note = fresh.note or current.note
        if fresh.sheet:
            current.sheet = fresh.sheet
    return sorted(by_path.values(), key=lambda i: i.rel_path)


# ── sampling ─────────────────────────────────────────────────────────


def stratify(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    """
    Bucket candidate reels by what the *current* pipeline thinks of them.

    True strata (reveal / stable-glam / modest) are what the labelling is meant
    to discover, so they cannot be used to select. Current score is the
    available proxy, and sampling across it guarantees the set spans the
    pipeline's whole output range instead of only the cases it already handles.
    """
    buckets: Dict[str, List[str]] = {
        "unscored": [],
        "low": [],
        "mid": [],
        "high": [],
    }
    for row in rows:
        # `or -1` would be wrong here: glam 0 is falsy but is a real verdict
        # ("no woman"), and folding it into `unscored` drops a whole stratum.
        raw = row.get("glam_score")
        glam = -1 if raw is None else int(raw)
        rel = row.get("rel_path") or ""
        if not rel:
            continue
        if glam < 0:
            buckets["unscored"].append(rel)
        elif glam <= 1:
            buckets["low"].append(rel)
        elif glam == 2:
            buckets["mid"].append(rel)
        else:
            buckets["high"].append(rel)
    return buckets


def choose_sample(
    buckets: Dict[str, List[str]],
    total: int = 120,
    seed: int = 20260809,
) -> List[tuple]:
    """Even split across non-empty buckets, deterministic for a given seed.

    Returns [(rel_path, stratum)]. Short buckets give their slack back to the
    others, so a total of 120 still yields 120 when one stratum is thin.
    """
    rng = random.Random(seed)
    active = {name: list(paths) for name, paths in buckets.items() if paths}
    if not active:
        return []
    for paths in active.values():
        rng.shuffle(paths)

    quota = {name: total // len(active) for name in active}
    picked: List[tuple] = []
    for name, paths in active.items():
        take = min(quota[name], len(paths))
        picked.extend((p, name) for p in paths[:take])
        quota[name] = take

    # Redistribute whatever the thin buckets could not fill.
    shortfall = total - len(picked)
    if shortfall > 0:
        for name, paths in active.items():
            if shortfall <= 0:
                break
            spare = paths[quota[name] :]
            extra = spare[:shortfall]
            picked.extend((p, name) for p in extra)
            shortfall -= len(extra)

    picked.sort(key=lambda item: item[0])
    return picked


# ── metrics ──────────────────────────────────────────────────────────


@dataclass
class EvalResult:
    """One reel scored by the pipeline."""

    rel_path: str
    ok: bool = False
    predicted_tier: int = -1
    glam_score: int = -1
    vision_calls: int = 0
    ms: int = 0
    peak_time_sec: Optional[float] = None
    error: str = ""
    prompt_version: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Metrics:
    labelled: int = 0
    scored: int = 0
    # How many scored items produced a tier at all. The legacy boolean prompt
    # produces none, so tier metrics are undefined for it and glam is the only
    # axis on which the two vocabularies can be compared.
    tier_scored: int = 0
    glam_accuracy: float = 0.0
    exact_accuracy: float = 0.0
    within_one: float = 0.0
    reveal_recall: float = 0.0
    reveal_n: int = 0
    top_score_share: float = 0.0
    confusion_axis: str = "glam"
    score_hist: Dict[str, int] = field(default_factory=dict)
    unscored_rate: float = 0.0
    median_vision_calls: float = 0.0
    median_ms: float = 0.0
    mean_signed_error: float = 0.0
    confusion: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Design doc §5. Kept here so `report` and the doc cannot drift apart.
TARGETS = {
    "reveal_recall": (">=", 0.85),
    "exact_accuracy": (">=", 0.75),
    "within_one": (">=", 0.95),
    "top_score_share": ("<=", 0.60),
    "unscored_rate": ("<=", 0.02),
    "median_vision_calls": ("<=", 2.0),
}


def meets_target(name: str, value: float) -> Optional[bool]:
    target = TARGETS.get(name)
    if target is None:
        return None
    op, threshold = target
    return value >= threshold if op == ">=" else value <= threshold


def compute_metrics(
    labels: Sequence[EvalItem],
    results: Sequence[EvalResult],
) -> Metrics:
    """Score a run against the labels. Only labelled reels count."""
    by_path = {r.rel_path: r for r in results}
    pairs = [
        (item, by_path[item.rel_path])
        for item in labels
        if item.is_labelled() and item.rel_path in by_path
    ]
    m = Metrics(labelled=sum(1 for i in labels if i.is_labelled()), scored=len(pairs))
    if not pairs:
        return m

    ok_pairs = [(i, r) for i, r in pairs if r.ok]
    m.unscored_rate = round(1 - len(ok_pairs) / len(pairs), 4)

    if ok_pairs:
        # Glam is the common axis: every vocabulary produces one, so this is
        # what makes legacy-vs-ordinal an A/B rather than two unrelated numbers.
        glam_hit = sum(1 for i, r in ok_pairs if r.glam_score == i.true_glam())
        m.glam_accuracy = round(glam_hit / len(ok_pairs), 4)

        tier_pairs = [(i, r) for i, r in ok_pairs if r.predicted_tier >= 0]
        m.tier_scored = len(tier_pairs)
        if tier_pairs:
            exact = sum(1 for i, r in tier_pairs if r.predicted_tier == i.true_exposure)
            near = sum(
                1 for i, r in tier_pairs if abs(r.predicted_tier - i.true_exposure) <= 1
            )
            m.exact_accuracy = round(exact / len(tier_pairs), 4)
            m.within_one = round(near / len(tier_pairs), 4)
            m.mean_signed_error = round(
                sum(r.predicted_tier - i.true_exposure for i, r in tier_pairs)
                / len(tier_pairs),
                3,
            )

        # The headline for reels: the real outfit only appears at the end.
        reveals = [(i, r) for i, r in ok_pairs if i.reveal_at_end and r.predicted_tier >= 0]
        m.reveal_n = len(reveals)
        if reveals:
            hit = sum(1 for i, r in reveals if r.predicted_tier >= i.true_exposure)
            m.reveal_recall = round(hit / len(reveals), 4)

        hist: Dict[str, int] = {}
        for _, r in ok_pairs:
            hist[str(r.glam_score)] = hist.get(str(r.glam_score), 0) + 1
        m.score_hist = dict(sorted(hist.items()))
        m.top_score_share = round(max(hist.values()) / len(ok_pairs), 4)

        # Tier when the run produced one — 0-4 is finer grained and directly
        # actionable for prompt tuning. Glam otherwise, so a legacy run still
        # gets a matrix instead of nothing.
        confusion: Dict[str, int] = {}
        if tier_pairs:
            m.confusion_axis = "tier"
            source = [(i.true_exposure, r.predicted_tier) for i, r in tier_pairs]
        else:
            m.confusion_axis = "glam"
            source = [(i.true_glam(), r.glam_score) for i, r in ok_pairs]
        for truth, pred in source:
            confusion[f"{truth}->{pred}"] = confusion.get(f"{truth}->{pred}", 0) + 1
        m.confusion = dict(sorted(confusion.items()))

    m.median_vision_calls = round(statistics.median([r.vision_calls for _, r in pairs]), 2)
    m.median_ms = round(statistics.median([r.ms for _, r in pairs]), 1)
    return m


# ── running the pipeline ─────────────────────────────────────────────


def score_item(
    rel_path: str,
    kind: str = "reel",
    base_dir: str = SAVED_DIR,
    *,
    ordinal: Optional[bool] = None,
) -> EvalResult:
    """Score one item with the *current* pipeline. Requires Ollama.

    Photos go through `classify_image`. Pass ``ordinal=True/False`` to force
    the v4 tier vocabulary or the legacy booleans for an A/B run without
    mutating the process-wide ``CLASSIFY_PHOTO_ORDINAL`` env flag. ``None``
    keeps whatever the env currently says.
    """
    from promptstudio.scraping.outfit_classifier import classify_image, classify_video

    full = os.path.join(base_dir, *rel_path.split("/"))
    result = EvalResult(rel_path=rel_path)
    if not os.path.isfile(full):
        result.error = "missing_file"
        return result

    started = time.monotonic()
    try:
        if kind == "reel":
            verdict = classify_video(full)
        else:
            verdict = classify_image(full, ordinal=ordinal)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"[:200]
        result.ms = int((time.monotonic() - started) * 1000)
        return result

    evidence = verdict.evidence or {}
    result.ok = verdict.ok
    result.predicted_tier = verdict.exposure_tier
    result.glam_score = verdict.glam_score
    # The photo path makes exactly one call and records no counter for it.
    result.vision_calls = int(evidence.get("frames_sent_to_vision") or 0) or 1
    result.peak_time_sec = evidence.get("peak_time_sec")
    result.error = verdict.error
    result.prompt_version = verdict.prompt_version
    result.ms = int((time.monotonic() - started) * 1000)
    return result


def save_results(
    name: str,
    results: Sequence[EvalResult],
    meta: Dict[str, Any],
    kind: str = "reel",
) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = results_file(name, kind)
    atomic_write_json(
        path, {"meta": meta, "results": [r.to_dict() for r in results]}
    )
    return path


def load_results(name: str, kind: str = "reel") -> tuple:
    path = results_file(name, kind)
    if not os.path.isfile(path):
        return [], {}
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    results = [EvalResult(**r) for r in payload.get("results", [])]
    return results, payload.get("meta", {})


def list_results(kind: str = "reel") -> List[str]:
    prefix = f"{kind}-"
    try:
        return sorted(
            n[len(prefix) : -len(".json")]
            for n in os.listdir(RESULTS_DIR)
            if n.startswith(prefix) and n.endswith(".json")
        )
    except OSError:
        return []


def eval_status(kind: str = "reel") -> Dict[str, Any]:
    """Snapshot of sample / label / results progress for one media kind."""
    path = labels_file(kind)
    items = load_labels(path)
    labelled = [i for i in items if i.is_labelled()]
    with_sheet = [i for i in items if i.sheet]
    tier_hist: Dict[str, int] = {}
    for i in labelled:
        key = str(i.true_exposure)
        tier_hist[key] = tier_hist.get(key, 0) + 1
    return {
        "kind": kind,
        "eval_dir": EVAL_DIR,
        "labels_file": path,
        "sample_size": len(items),
        "with_preview": len(with_sheet),
        "labelled": len(labelled),
        "remaining": max(0, len(items) - len(labelled)),
        "true_tiers": dict(sorted(tier_hist.items(), key=lambda kv: int(kv[0]))),
        "label_page": os.path.join(EVAL_DIR, f"label-{kind}.html"),
        "results": list_results(kind),
    }


def file_url(path: str) -> str:
    """Browser-openable file:// URL (Windows-safe)."""
    abs_path = os.path.abspath(path)
    # pathlib would add an extra slash on Windows as file:///C:/...
    return "file:///" + abs_path.replace("\\", "/")


def render_photo_preview(full_path: str, out_path: str, max_edge: int = 900) -> bool:
    """Downscaled copy of a photo for the labelling page.

    A copy rather than a link to the original: paging through 120 full-size
    photos over file:// is slow, and the eval directory stays self-contained
    and disposable.
    """
    try:
        from PIL import Image

        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with Image.open(full_path) as img:
            img = img.convert("RGB")
            img.thumbnail((max_edge, max_edge))
            img.save(out_path, "JPEG", quality=85, optimize=True)
        return os.path.isfile(out_path)
    except Exception as e:
        log.debug("preview failed for %s: %s", full_path, e)
        return False


# ── labelling page ───────────────────────────────────────────────────

_LABEL_HTML = """<!doctype html>
<meta charset="utf-8">
<title>Eval labelling</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;font:14px/1.5 system-ui,sans-serif;background:#14141a;color:#e6e6ee;
      display:grid;grid-template-columns:1fr 320px;height:100vh;overflow:hidden}
 #stage{display:flex;align-items:center;justify-content:center;background:#0d0d12;overflow:auto}
 #sheet{max-height:96vh;max-width:100%;object-fit:contain}
 aside{padding:18px;border-left:1px solid #2a2a36;overflow:auto}
 h1{font-size:15px;margin:0 0 4px}
 .path{font:12px ui-monospace,monospace;color:#9a9ab0;word-break:break-all;margin-bottom:12px}
 .bar{height:6px;background:#2a2a36;border-radius:3px;overflow:hidden;margin:8px 0 16px}
 .bar i{display:block;height:100%;background:#8b5cf6}
 .tier{display:flex;gap:6px;margin-bottom:6px;align-items:flex-start;cursor:pointer;
       padding:7px 9px;border-radius:7px;border:1px solid #2a2a36;background:#1b1b24}
 .tier:hover{border-color:#8b5cf6}
 .tier.on{background:#8b5cf6;border-color:#8b5cf6;color:#fff}
 .tier b{min-width:14px}
 .tier span{font-size:12px;color:#b9b9cc}
 .tier.on span{color:#f0e9ff}
 .toggle{margin:12px 0;padding:9px;border-radius:7px;border:1px solid #2a2a36;
         background:#1b1b24;cursor:pointer;text-align:center}
 .toggle.on{background:#ec4899;border-color:#ec4899;color:#fff}
 button{width:100%;padding:9px;margin-top:8px;border-radius:7px;border:1px solid #2a2a36;
        background:#1b1b24;color:#e6e6ee;cursor:pointer;font:inherit}
 button.primary{background:#06b6d4;border-color:#06b6d4;color:#04222a;font-weight:600}
 kbd{background:#2a2a36;border-radius:4px;padding:1px 5px;font:11px ui-monospace,monospace}
 .keys{margin-top:14px;font-size:12px;color:#9a9ab0;line-height:1.9}
 .meta{font-size:12px;color:#9a9ab0;margin-top:10px}
 textarea{width:100%;background:#1b1b24;color:#e6e6ee;border:1px solid #2a2a36;
          border-radius:7px;padding:7px;font:inherit;resize:vertical}
</style>
<div id="stage"><img id="sheet" alt="contact sheet"></div>
<aside>
  <h1><span id="kindlabel"></span> eval <span id="count"></span></h1>
  <div class="bar"><i id="prog"></i></div>
  <div class="path" id="path"></div>
  <div id="tiers"></div>
  <div class="toggle" id="reveal">reveal only at the end — <kbd>r</kbd></div>
  <textarea id="note" rows="2" placeholder="note (optional)"></textarea>
  <div class="meta" id="prior"></div>
  <button id="prev">&larr; previous <kbd>j</kbd></button>
  <button id="next">next <kbd>k</kbd> &rarr;</button>
  <button class="primary" id="export">Export labels.jsonl <kbd>e</kbd></button>
  <div class="keys">
    <kbd>0</kbd>–<kbd>4</kbd> set tier &amp; advance · <kbd>r</kbd> reveal ·
    <kbd>j</kbd>/<kbd>k</kbd> move · <kbd>e</kbd> export<br>
    Progress is saved in this browser as you go. Export when done and drop the
    file at <code>__LABELS__</code>.
  </div>
</aside>
<script>
const ITEMS = __ITEMS__;
const ANCHORS = __ANCHORS__;
const KIND = __KIND__;
// "reveal at the end" is a property of video. Asking it of a photo would be
// noise, and a field nobody can answer gets answered at random.
if (KIND !== "reel") document.getElementById("reveal").style.display = "none";
const KEY = "eval-labels-" + __KIND__;
const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
ITEMS.forEach(it => Object.assign(it, saved[it.rel_path] || {}));
let idx = Math.max(0, ITEMS.findIndex(i => i.true_exposure === null));
if (idx === -1) idx = 0;

const el = id => document.getElementById(id);
el("kindlabel").textContent = KIND;
el("tiers").innerHTML = Object.entries(ANCHORS).map(([t, d]) =>
  `<div class="tier" data-t="${t}"><b>${t}</b><span>${d}</span></div>`).join("");

function persist() {
  const out = {};
  ITEMS.forEach(i => {
    if (i.true_exposure !== null) out[i.rel_path] =
      {true_exposure: i.true_exposure, reveal_at_end: i.reveal_at_end, note: i.note};
  });
  localStorage.setItem(KEY, JSON.stringify(out));
}

function render() {
  const it = ITEMS[idx];
  el("sheet").src = "sheets/" + it.sheet;
  el("path").textContent = it.rel_path;
  const done = ITEMS.filter(i => i.true_exposure !== null).length;
  el("count").textContent = `${idx + 1}/${ITEMS.length} — ${done} labelled`;
  el("prog").style.width = (100 * done / ITEMS.length) + "%";
  el("prior").textContent =
    `sampled from: ${it.stratum} · pipeline said glam ${it.prior_glam}`;
  el("note").value = it.note || "";
  document.querySelectorAll(".tier").forEach(n =>
    n.classList.toggle("on", Number(n.dataset.t) === it.true_exposure));
  el("reveal").classList.toggle("on", !!it.reveal_at_end);
}

function setTier(t) {
  ITEMS[idx].true_exposure = t;
  if (ITEMS[idx].reveal_at_end === null) ITEMS[idx].reveal_at_end = false;
  persist();
  if (idx < ITEMS.length - 1) idx++;
  render();
}

document.querySelectorAll(".tier").forEach(n =>
  n.onclick = () => setTier(Number(n.dataset.t)));
el("reveal").onclick = () => {
  ITEMS[idx].reveal_at_end = !ITEMS[idx].reveal_at_end; persist(); render();
};
el("note").oninput = e => { ITEMS[idx].note = e.target.value; persist(); };
el("prev").onclick = () => { if (idx > 0) { idx--; render(); } };
el("next").onclick = () => { if (idx < ITEMS.length - 1) { idx++; render(); } };
el("export").onclick = () => {
  const body = ITEMS.map(i => JSON.stringify(i)).join("\\n") + "\\n";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([body], {type: "application/x-ndjson"}));
  a.download = "labels.jsonl";
  a.click();
};

addEventListener("keydown", e => {
  if (e.target.tagName === "TEXTAREA") return;
  if (e.key >= "0" && e.key <= "4") setTier(Number(e.key));
  else if (e.key === "r" && KIND === "reel") el("reveal").click();
  else if (e.key === "j" || e.key === "ArrowLeft") el("prev").click();
  else if (e.key === "k" || e.key === "ArrowRight") el("next").click();
  else if (e.key === "e") el("export").click();
});
render();
</script>
"""


def render_label_page(
    items: Sequence[EvalItem],
    out_path: str,
    kind: str = "reel",
) -> str:
    """
    Write a self-contained labelling page next to the sheets.

    Static file, opened over file:// — no server, no build step, and it works
    on whatever machine holds the real archive. Progress lives in localStorage
    so closing the tab mid-way costs nothing; Export writes the JSONL back.

    peak_time_sec is deliberately not asked for: no metric in the design doc
    uses it, and every extra field slows 120 judgements down.
    """
    payload = [
        {
            "rel_path": i.rel_path,
            "kind": i.kind,
            "sheet": os.path.basename(i.sheet) if i.sheet else "",
            "stratum": i.stratum,
            "prior_glam": i.prior_glam,
            "true_exposure": i.true_exposure,
            "reveal_at_end": i.reveal_at_end,
            "note": i.note,
        }
        for i in items
    ]
    html = (
        _LABEL_HTML.replace("__ITEMS__", json.dumps(payload))
        .replace("__ANCHORS__", json.dumps({str(k): v for k, v in TIER_ANCHORS.items()}))
        .replace("__KIND__", json.dumps(kind))
        .replace("__LABELS__", labels_file(kind))
    )
    atomic_write_text(out_path, html)
    return out_path


# ── visual compare report ────────────────────────────────────────────

_COMPARE_HTML = """<!doctype html>
<meta charset="utf-8">
<title>Eval compare — __KIND_PLAIN__</title>
<style>
 :root{color-scheme:dark;--bg:#0d0d12;--card:#16161f;--line:#2a2a36;--text:#e8e8f0;
       --muted:#9a9ab0;--ok:#34d399;--bad:#f87171;--warn:#fbbf24;--accent:#8b5cf6;--cyan:#06b6d4}
 *{box-sizing:border-box}
 body{margin:0;font:14px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--text)}
 header{position:sticky;top:0;z-index:5;background:rgba(13,13,18,.92);backdrop-filter:blur(10px);
        border-bottom:1px solid var(--line);padding:14px 18px}
 h1{margin:0 0 4px;font-size:18px;font-weight:650}
 .sub{color:var(--muted);font-size:12px;margin-bottom:12px}
 .metrics{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
 .metric{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px;min-width:110px}
 .metric b{display:block;font-size:18px;font-variant-numeric:tabular-nums}
 .metric span{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
 .metric.up b{color:var(--ok)} .metric.down b{color:var(--bad)}
 .filters{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
 .filters button,.filters select{background:var(--card);color:var(--text);border:1px solid var(--line);
        border-radius:8px;padding:6px 10px;cursor:pointer;font:inherit}
 .filters button.on{background:var(--accent);border-color:var(--accent)}
 .filters input{background:var(--card);color:var(--text);border:1px solid var(--line);
        border-radius:8px;padding:6px 10px;min-width:180px;font:inherit}
 .count{margin-left:auto;color:var(--muted);font-size:12px}
 main{padding:16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;
       display:flex;flex-direction:column}
 .card.miss-both{border-color:rgba(248,113,113,.45)}
 .card.miss-leg{border-color:rgba(251,191,36,.4)}
 .card.miss-ord{border-color:rgba(6,182,212,.4)}
 .card.hit-both{border-color:rgba(52,211,153,.35)}
 .thumb{aspect-ratio:3/4;background:#0a0a0e;display:flex;align-items:center;justify-content:center;overflow:hidden}
 .thumb img{width:100%;height:100%;object-fit:cover}
 .body{padding:10px 12px 12px;display:flex;flex-direction:column;gap:6px}
 .path{font:11px ui-monospace,monospace;color:var(--muted);word-break:break-all;line-height:1.3}
 .row{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:12px}
 .lbl{color:var(--muted)}
 .pill{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;
       font:12px ui-monospace,monospace;border:1px solid var(--line);background:#1b1b24}
 .pill.ok{color:var(--ok);border-color:rgba(52,211,153,.35);background:rgba(52,211,153,.08)}
 .pill.bad{color:var(--bad);border-color:rgba(248,113,113,.4);background:rgba(248,113,113,.08)}
 .pill.na{color:var(--muted)}
 .true{font-weight:650;color:#c4b5fd}
 .note{font-size:11px;color:var(--muted);font-style:italic}
 .empty{grid-column:1/-1;text-align:center;color:var(--muted);padding:40px}
 kbd{background:#2a2a36;border-radius:4px;padding:1px 5px;font:11px ui-monospace,monospace}
</style>
<header>
  <h1>Eval compare · <span id="kind"></span></h1>
  <div class="sub" id="sub"></div>
  <div class="metrics" id="metrics"></div>
  <div class="filters">
    <button data-f="all" class="on">All</button>
    <button data-f="disagree">Models disagree</button>
    <button data-f="ord_better">Ordinal fixed</button>
    <button data-f="ord_worse">Ordinal broke</button>
    <button data-f="both_wrong">Both wrong</button>
    <button data-f="both_right">Both right</button>
    <button data-f="leg_wrong">Legacy wrong</button>
    <button data-f="ord_wrong">Ordinal wrong</button>
    <select id="trueTier"><option value="">True tier: any</option>
      <option value="0">0</option><option value="1">1</option><option value="2">2</option>
      <option value="3">3</option><option value="4">4</option></select>
    <input id="q" type="search" placeholder="Filter path…">
    <span class="count" id="count"></span>
  </div>
</header>
<main id="grid"></main>
<script>
const ROWS = __ROWS__;
const META = __META__;
const ANCHORS = __ANCHORS__;
const KIND = __KIND__;

const el = id => document.getElementById(id);
el("kind").textContent = KIND;
el("sub").textContent =
  `${META.n} labelled · model ${META.model || "?"} · ` +
  `legacy ${META.legacy_name || "legacy"} vs ${META.ordinal_name || "ordinal"} · ` +
  `open over file:// next to sheets/`;

function pct(x){ return x == null ? "—" : (100*x).toFixed(1) + "%"; }
el("metrics").innerHTML = [
  ["Legacy glam acc", pct(META.legacy_glam_acc), ""],
  ["Ordinal glam acc", pct(META.ordinal_glam_acc),
    META.ordinal_glam_acc > META.legacy_glam_acc ? "up" :
    META.ordinal_glam_acc < META.legacy_glam_acc ? "down" : ""],
  ["Ordinal tier exact", pct(META.ordinal_tier_acc), ""],
  ["Ordinal within±1", pct(META.ordinal_within_one), ""],
  ["Legacy top share", pct(META.legacy_top), ""],
  ["Ordinal top share", pct(META.ordinal_top),
    META.ordinal_top < META.legacy_top ? "up" :
    META.ordinal_top > META.legacy_top ? "down" : ""],
  ["Ordinal fixed", META.ord_better, "up"],
  ["Ordinal broke", META.ord_worse, "down"],
].map(([l,v,c]) => `<div class="metric ${c}"><b>${v}</b><span>${l}</span></div>`).join("");

let filter = "all";
function match(r) {
  const q = el("q").value.trim().toLowerCase();
  if (q && !r.rel_path.toLowerCase().includes(q)) return false;
  const tt = el("trueTier").value;
  if (tt !== "" && String(r.true_tier) !== tt) return false;
  if (filter === "all") return true;
  if (filter === "disagree") return r.leg_glam !== r.ord_glam;
  if (filter === "ord_better") return !r.leg_ok && r.ord_ok;
  if (filter === "ord_worse") return r.leg_ok && !r.ord_ok;
  if (filter === "both_wrong") return !r.leg_ok && !r.ord_ok;
  if (filter === "both_right") return r.leg_ok && r.ord_ok;
  if (filter === "leg_wrong") return !r.leg_ok;
  if (filter === "ord_wrong") return !r.ord_ok;
  return true;
}

function pill(val, ok, na) {
  if (na || val == null || val < 0) return `<span class="pill na">—</span>`;
  return `<span class="pill ${ok ? "ok" : "bad"}">${val}</span>`;
}

function cardClass(r) {
  if (r.leg_ok && r.ord_ok) return "hit-both";
  if (!r.leg_ok && !r.ord_ok) return "miss-both";
  if (!r.leg_ok) return "miss-leg";
  return "miss-ord";
}

function render() {
  const rows = ROWS.filter(match);
  el("count").textContent = `${rows.length} / ${ROWS.length}`;
  const g = el("grid");
  if (!rows.length) {
    g.innerHTML = `<div class="empty">No items match this filter</div>`;
    return;
  }
  g.innerHTML = rows.map(r => {
    const anchor = ANCHORS[String(r.true_tier)] || "";
    const src = r.sheet ? ("sheets/" + r.sheet) : "";
    return `<article class="card ${cardClass(r)}" data-path="${r.rel_path}">
      <div class="thumb">${src ? `<img loading="lazy" src="${src}" alt="">` : ""}</div>
      <div class="body">
        <div class="path">${r.rel_path}</div>
        <div class="row"><span class="lbl">You (tier → glam)</span>
          <span class="true">T${r.true_tier} → G${r.true_glam}</span></div>
        <div class="row"><span class="lbl">Legacy glam</span>
          ${pill("G"+r.leg_glam, r.leg_ok, r.leg_glam < 0)}</div>
        <div class="row"><span class="lbl">Ordinal tier→glam</span>
          ${pill("T"+(r.ord_tier>=0?r.ord_tier:"?")+" → G"+r.ord_glam, r.ord_ok, r.ord_glam < 0)}</div>
        ${r.note ? `<div class="note">${r.note}</div>` : ""}
        <div class="note">${anchor}</div>
      </div>
    </article>`;
  }).join("");
}

document.querySelectorAll(".filters button[data-f]").forEach(b => {
  b.onclick = () => {
    document.querySelectorAll(".filters button[data-f]").forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    filter = b.dataset.f;
    render();
  };
});
el("q").oninput = render;
el("trueTier").onchange = render;
render();
</script>
"""


def render_compare_page(
    kind: str,
    primary: str,
    against: str,
    out_path: str,
) -> str:
    """Build a visual card grid: truth vs two named runs side by side.

    Images load from ``sheets/`` relative to the HTML file (same layout as the
    labelling page), so open it over ``file://`` from ``_eval/``.
    """
    labels = [i for i in load_labels(labels_file(kind)) if i.is_labelled()]
    primary_results, primary_meta = load_results(primary, kind)
    against_results, against_meta = load_results(against, kind)
    if not labels:
        raise ValueError(f"no labelled {kind}s — run sample + label first")
    if not primary_results:
        raise ValueError(f"no results '{primary}' for {kind}")
    if not against_results:
        raise ValueError(f"no results '{against}' for {kind}")

    by_pri = {r.rel_path: r for r in primary_results}
    by_agt = {r.rel_path: r for r in against_results}
    m_pri = compute_metrics(labels, primary_results)
    m_agt = compute_metrics(labels, against_results)

    # primary = ordinal (the candidate), against = legacy (baseline) is the
    # usual call shape; card fields are named for that, but any two names work.
    rows: List[Dict[str, Any]] = []
    ord_better = ord_worse = 0
    for item in labels:
        leg = by_agt.get(item.rel_path)
        ord_r = by_pri.get(item.rel_path)
        true_glam = item.true_glam()
        leg_glam = int(leg.glam_score) if leg and leg.ok else -1
        ord_glam = int(ord_r.glam_score) if ord_r and ord_r.ok else -1
        ord_tier = int(ord_r.predicted_tier) if ord_r and ord_r.ok else -1
        leg_ok = leg_glam == true_glam and leg_glam >= 0
        ord_ok = ord_glam == true_glam and ord_glam >= 0
        if ord_ok and not leg_ok:
            ord_better += 1
        if leg_ok and not ord_ok:
            ord_worse += 1
        rows.append(
            {
                "rel_path": item.rel_path,
                "sheet": os.path.basename(item.sheet) if item.sheet else "",
                "true_tier": item.true_exposure,
                "true_glam": true_glam,
                "leg_glam": leg_glam,
                "ord_glam": ord_glam,
                "ord_tier": ord_tier,
                "leg_ok": leg_ok,
                "ord_ok": ord_ok,
                "note": item.note or "",
                "stratum": item.stratum,
            }
        )

    # Sort: disagreements first, then path — easiest to browse failures.
    rows.sort(
        key=lambda r: (
            0 if (r["leg_ok"] != r["ord_ok"]) else (1 if not r["leg_ok"] else 2),
            r["rel_path"],
        )
    )

    meta = {
        "n": len(rows),
        "model": primary_meta.get("model") or against_meta.get("model"),
        "legacy_name": against,
        "ordinal_name": primary,
        "legacy_glam_acc": m_agt.glam_accuracy,
        "ordinal_glam_acc": m_pri.glam_accuracy,
        "ordinal_tier_acc": m_pri.exact_accuracy if m_pri.tier_scored else None,
        "ordinal_within_one": m_pri.within_one if m_pri.tier_scored else None,
        "legacy_top": m_agt.top_score_share,
        "ordinal_top": m_pri.top_score_share,
        "ord_better": ord_better,
        "ord_worse": ord_worse,
    }
    html = (
        _COMPARE_HTML.replace("__ROWS__", json.dumps(rows, ensure_ascii=False))
        .replace("__META__", json.dumps(meta, ensure_ascii=False))
        .replace(
            "__ANCHORS__",
            json.dumps({str(k): v for k, v in TIER_ANCHORS.items()}, ensure_ascii=False),
        )
        .replace("__KIND__", json.dumps(kind))
        .replace("__KIND_PLAIN__", kind)
    )
    atomic_write_text(out_path, html)
    return out_path
