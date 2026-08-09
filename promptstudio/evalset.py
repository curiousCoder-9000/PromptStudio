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
    0: "discard — no woman; any man present; unusable quality (blur/distortion); OR poster-like (event flyer, promo poster, graphic collage, heavy text layout, magazine cover graphic)",
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

# Short names for cards (full text is in the page legend).
_TIER_SHORT = {
    0: "discard (no woman / men / blur / poster)",
    1: "fully modest",
    2: "normal fashion",
    3: "revealing",
    4: "maximally revealing",
}
_GLAM_SHORT = {
    0: "reject / no keep",
    1: "modest keep",
    2: "Sexy filter (border)",
    3: "top glam bucket",
}

_COMPARE_HTML = """<!doctype html>
<meta charset="utf-8">
<title>Eval compare — __KIND_PLAIN__</title>
<style>
 :root{color-scheme:dark;--bg:#0d0d12;--card:#16161f;--line:#2a2a36;--text:#e8e8f0;
       --muted:#9a9ab0;--ok:#34d399;--bad:#f87171;--warn:#fbbf24;--accent:#8b5cf6;--cyan:#06b6d4;
       --purple:#c4b5fd}
 *{box-sizing:border-box}
 body{margin:0;font:14px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--text)}
 header{position:sticky;top:0;z-index:5;background:rgba(13,13,18,.94);backdrop-filter:blur(10px);
        border-bottom:1px solid var(--line);padding:14px 18px;max-height:72vh;overflow:auto}
 h1{margin:0 0 4px;font-size:18px;font-weight:650}
 .sub{color:var(--muted);font-size:12px;margin-bottom:10px}
 .legend{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;
         margin-bottom:12px;font-size:12.5px;line-height:1.55;color:#d4d4e0}
 .legend h2{margin:0 0 8px;font-size:13px;font-weight:650;color:#fff}
 .legend p{margin:0 0 8px}
 .legend ul{margin:0 0 8px;padding-left:1.15rem}
 .legend code{font:12px ui-monospace,monospace;background:#1b1b24;padding:1px 5px;border-radius:4px}
 .map{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:6px;margin:8px 0}
 .map div{background:#1b1b24;border:1px solid var(--line);border-radius:8px;padding:6px 8px;
          font:12px ui-monospace,monospace}
 .map b{color:var(--purple)}
 .map span{color:var(--muted);display:block;font:11px system-ui,sans-serif;margin-top:2px}
 .example{background:rgba(139,92,246,.1);border:1px solid rgba(139,92,246,.3);border-radius:8px;
          padding:8px 10px;margin-top:8px}
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
 .relabel-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:10px;
        padding:10px 12px;background:rgba(6,182,212,.08);border:1px solid rgba(6,182,212,.25);
        border-radius:10px;font-size:12.5px}
 .relabel-bar .hint{color:var(--muted);flex:1;min-width:200px}
 .relabel-bar kbd{background:#2a2a36;border-radius:4px;padding:1px 5px;font:11px ui-monospace,monospace}
 #dirtyCount{font-weight:650;color:var(--muted)}
 #dirtyCount.has-edits{color:var(--warn)}
 .btn-export{background:var(--cyan);color:#04222a;border:none;border-radius:8px;padding:7px 12px;
        font:650 12.5px system-ui,sans-serif;cursor:pointer}
 .btn-ghost{background:transparent;color:var(--text);border:1px solid var(--line);border-radius:8px;
        padding:7px 12px;font:12.5px system-ui,sans-serif;cursor:pointer}
 .tier-row{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-top:8px}
 .tier-lbl{font-size:11px;color:var(--muted);margin-right:2px}
 .tier-btn{width:32px;height:32px;border-radius:8px;border:1px solid var(--line);background:#1b1b24;
        color:var(--text);font:650 13px ui-monospace,monospace;cursor:pointer}
 .tier-btn:hover{border-color:var(--accent)}
 .tier-btn.on{background:var(--accent);border-color:var(--accent);color:#fff}
 .edited-badge{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:999px;font:11px system-ui,sans-serif;
        background:rgba(251,191,36,.15);color:var(--warn);border:1px solid rgba(251,191,36,.4);vertical-align:middle}
 .card.selected{outline:2px solid var(--cyan);outline-offset:2px;box-shadow:0 0 0 4px rgba(6,182,212,.15)}
 main{padding:16px;display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
 .card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;
       display:flex;flex-direction:column;cursor:pointer}
 .card.miss-both{border-color:rgba(248,113,113,.45)}
 .card.miss-leg{border-color:rgba(251,191,36,.4)}
 .card.miss-ord{border-color:rgba(6,182,212,.4)}
 .card.hit-both{border-color:rgba(52,211,153,.35)}
 .thumb{aspect-ratio:3/4;background:#0a0a0e;display:flex;align-items:center;justify-content:center;overflow:hidden}
 .thumb img{width:100%;height:100%;object-fit:cover}
 .body{padding:12px 12px 14px;display:flex;flex-direction:column;gap:8px}
 .path{font:11px ui-monospace,monospace;color:var(--muted);word-break:break-all;line-height:1.3}
 .block{border:1px solid var(--line);border-radius:10px;padding:8px 10px;background:#12121a}
 .block.truth{border-color:rgba(167,139,250,.4);background:rgba(139,92,246,.08)}
 .block.ok{border-color:rgba(52,211,153,.35)}
 .block.bad{border-color:rgba(248,113,113,.4)}
 .block-title{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);
              margin-bottom:4px;display:flex;justify-content:space-between;gap:8px;align-items:center}
 .block-title .who{font-weight:650;color:#e2e8f0;text-transform:none;letter-spacing:0;font-size:12px}
 .verdict{font-size:11px;font-weight:650;padding:1px 7px;border-radius:999px;border:1px solid var(--line)}
 .verdict.ok{color:var(--ok);border-color:rgba(52,211,153,.4);background:rgba(52,211,153,.1)}
 .verdict.bad{color:var(--bad);border-color:rgba(248,113,113,.4);background:rgba(248,113,113,.1)}
 .verdict.truth{color:var(--purple);border-color:rgba(167,139,250,.4);background:rgba(139,92,246,.12)}
 .mainline{font-size:13px;font-weight:600;color:#f1f5f9;margin-bottom:2px}
 .detail{font-size:12px;color:#cbd5e1}
 .why{font-size:11.5px;color:var(--muted);margin-top:4px;line-height:1.4}
 .codes{font:11.5px ui-monospace,monospace;color:#94a3b8;margin-top:3px}
 .note{font-size:11px;color:var(--muted);font-style:italic}
 .empty{grid-column:1/-1;text-align:center;color:var(--muted);padding:40px}
 details.legend-fold{margin-bottom:12px}
 details.legend-fold > summary{cursor:pointer;color:var(--cyan);font-size:13px;font-weight:600;
        list-style:none;margin-bottom:6px}
 details.legend-fold > summary::-webkit-details-marker{display:none}
</style>
<header>
  <h1>Eval compare · <span id="kind"></span></h1>
  <div class="sub" id="sub"></div>

  <details class="legend-fold" open>
    <summary>How to read this page (tier vs glam) ▾</summary>
    <div class="legend">
      <h2>Two different scales</h2>
      <p><b>Exposure tier (T0–T4)</b> — fine-grained outfit rating you labelled, and that the
         <b>ordinal</b> model predicts. This is the human/VLM vocabulary.</p>
      <p><b>Glam score (G0–G3)</b> — what the <b>gallery / Sexy filter</b> actually uses.
         Tier is <i>collapsed</i> into glam with a fixed map (never changes):</p>
      <div class="map" id="map"></div>
      <ul>
        <li><b>Your label</b> — ground truth. Shown as tier <i>and</i> the glam it maps to.</li>
        <li><b>Legacy model</b> — old 3-boolean prompt. It only outputs <b>glam</b> (no tier).
            Green = glam matches yours; red = wrong glam.</li>
        <li><b>Ordinal model</b> — predicts a tier, then glam is derived via the map.
            We score it on <b>glam match</b> (same axis as legacy). A wrong tier that still
            maps to the right glam counts as OK for the product filter.</li>
      </ul>
      <div class="example">
        <b>Example:</b> You said <code>T4 → G3</code> (maximally revealing → top glam bucket).<br>
        Legacy <code>G3</code> with green = correct product score.<br>
        Ordinal <code>T3 → G2</code> with red = model said “revealing daywear” (tier 3) which
        maps to glam 2, so the Sexy filter treats it milder than you wanted (under by 1 glam).
      </div>
      <p style="margin-top:10px"><b>Fix a wrong label here:</b> click a card, press
        <code>0</code>–<code>4</code> (or use the tier buttons). Then
        <b>Export labels.jsonl</b> and import with the CLI (file:// pages cannot write the archive).</p>
    </div>
  </details>

  <div class="metrics" id="metrics"></div>
  <div class="relabel-bar">
    <span id="dirtyCount">0 edits</span>
    <span class="hint">Click card · keys <kbd>0</kbd>–<kbd>4</kbd> set your tier · <kbd>j</kbd>/<kbd>k</kbd> prev/next · <kbd>e</kbd> export</span>
    <button type="button" id="exportLabelsBtn" class="btn-export">Export labels.jsonl</button>
    <button type="button" id="resetEditsBtn" class="btn-ghost">Reset edits</button>
  </div>
  <div class="filters">
    <button data-f="all" class="on">All</button>
    <button data-f="disagree">Models disagree</button>
    <button data-f="ord_better">Ordinal fixed</button>
    <button data-f="ord_worse">Ordinal broke</button>
    <button data-f="both_wrong">Both wrong</button>
    <button data-f="both_right">Both right</button>
    <button data-f="leg_wrong">Legacy wrong</button>
    <button data-f="ord_wrong">Ordinal wrong</button>
    <button data-f="edited">Edited only</button>
    <select id="trueTier"><option value="">Your tier: any</option>
      <option value="0">T0 discard (no woman / men / blur / poster)</option><option value="1">T1 modest</option>
      <option value="2">T2 fashion</option><option value="3">T3 revealing</option>
      <option value="4">T4 max</option></select>
    <input id="q" type="search" placeholder="Filter path…">
    <span class="count" id="count"></span>
  </div>
</header>
<main id="grid"></main>
<script>
const ROWS = __ROWS__;
const META = __META__;
const ANCHORS = __ANCHORS__;
const TIER_SHORT = __TIER_SHORT__;
const GLAM_SHORT = __GLAM_SHORT__;
const TIER_TO_GLAM = __TIER_TO_GLAM__;
const KIND = __KIND__;
const STORE_KEY = "eval-compare-edits-" + KIND + "-" + (META.ordinal_name || "run");

const el = id => document.getElementById(id);
el("kind").textContent = KIND;
el("sub").textContent =
  `${META.n} labelled photos · model ${META.model || "?"} · ` +
  `comparing ${META.legacy_name || "legacy"} (old) vs ${META.ordinal_name || "ordinal"} (new) · ` +
  `green = product glam matches your label`;

// Legend map tiles
el("map").innerHTML = Object.entries(TIER_TO_GLAM).map(([t, g]) =>
  `<div><b>T${t} → G${g}</b><span>${TIER_SHORT[t] || ""} → ${GLAM_SHORT[g] || ""}</span></div>`
).join("");

// ── mutable working copy + original snapshot for "edited" ──
const ORIG = {};
ROWS.forEach(r => {
  r.orig_true_tier = r.true_tier;
  ORIG[r.rel_path] = r.true_tier;
});
try {
  const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
  ROWS.forEach(r => {
    if (saved[r.rel_path] != null && saved[r.rel_path] !== "") {
      applyTier(r, Number(saved[r.rel_path]), false);
    }
  });
} catch (e) {}

function glamOfTier(t) {
  const g = TIER_TO_GLAM[String(t)];
  return g == null ? -1 : Number(g);
}

function applyTier(r, tier, persist) {
  tier = Math.max(0, Math.min(4, Number(tier)));
  r.true_tier = tier;
  r.true_glam = glamOfTier(tier);
  r.leg_ok = r.leg_glam >= 0 && r.leg_glam === r.true_glam;
  r.ord_ok = r.ord_glam >= 0 && r.ord_glam === r.true_glam;
  r.edited = r.true_tier !== ORIG[r.rel_path];
  if (persist !== false) persistEdits();
}

function persistEdits() {
  const out = {};
  ROWS.forEach(r => {
    if (r.true_tier !== ORIG[r.rel_path]) out[r.rel_path] = r.true_tier;
  });
  localStorage.setItem(STORE_KEY, JSON.stringify(out));
  updateDirty();
}

function updateDirty() {
  const n = ROWS.filter(r => r.true_tier !== ORIG[r.rel_path]).length;
  el("dirtyCount").textContent = n ? `${n} edit${n === 1 ? "" : "s"} (not saved to archive yet)` : "0 edits";
  el("dirtyCount").classList.toggle("has-edits", n > 0);
}

function liveMetrics() {
  const n = ROWS.length;
  let legOk = 0, ordOk = 0, ordTier = 0, ordNear = 0;
  ROWS.forEach(r => {
    if (r.leg_ok) legOk++;
    if (r.ord_ok) ordOk++;
    if (r.ord_tier >= 0 && r.ord_tier === r.true_tier) ordTier++;
    if (r.ord_tier >= 0 && Math.abs(r.ord_tier - r.true_tier) <= 1) ordNear++;
  });
  return {
    legacy_glam_acc: legOk / n,
    ordinal_glam_acc: ordOk / n,
    ordinal_tier_acc: ordTier / n,
    ordinal_within_one: ordNear / n,
    ord_better: ROWS.filter(r => !r.leg_ok && r.ord_ok).length,
    ord_worse: ROWS.filter(r => r.leg_ok && !r.ord_ok).length,
  };
}

function pct(x){ return x == null ? "—" : (100*x).toFixed(1) + "%"; }
function paintMetrics() {
  const m = liveMetrics();
  el("metrics").innerHTML = [
    ["Legacy right (glam)", pct(m.legacy_glam_acc), ""],
    ["Ordinal right (glam)", pct(m.ordinal_glam_acc),
      m.ordinal_glam_acc > m.legacy_glam_acc ? "up" :
      m.ordinal_glam_acc < m.legacy_glam_acc ? "down" : ""],
    ["Ordinal exact tier", pct(m.ordinal_tier_acc), ""],
    ["Ordinal within ±1 tier", pct(m.ordinal_within_one), ""],
    ["Legacy pile-up", pct(META.legacy_top), ""],
    ["Ordinal pile-up", pct(META.ordinal_top), ""],
    ["Ordinal fixed", m.ord_better, "up"],
    ["Ordinal broke", m.ord_worse, "down"],
  ].map(([l,v,c]) => `<div class="metric ${c}"><b>${v}</b><span>${l}</span></div>`).join("");
}

let filter = "all";
let selectedPath = ROWS[0] ? ROWS[0].rel_path : null;

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
  if (filter === "edited") return r.true_tier !== ORIG[r.rel_path];
  return true;
}

function cardClass(r) {
  if (r.leg_ok && r.ord_ok) return "hit-both";
  if (!r.leg_ok && !r.ord_ok) return "miss-both";
  if (!r.leg_ok) return "miss-leg";
  return "miss-ord";
}

function glamDeltaText(pred, truth) {
  if (pred < 0 || truth < 0) return "";
  const d = pred - truth;
  if (d === 0) return "Matches your product score.";
  if (d > 0) return `Too high by ${d} glam step${d>1?"s":""} (model more revealing than you).`;
  return `Too low by ${-d} glam step${d<-1?"s":""} (model milder than you).`;
}

function tierDeltaText(pred, truth) {
  if (pred < 0 || truth == null) return "";
  const d = pred - truth;
  if (d === 0) return "Exact tier match.";
  if (d > 0) return `Tier too high by ${d} (over-scored).`;
  return `Tier too low by ${-d} (under-scored).`;
}

function esc(s) {
  return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function rowByPath(path) {
  return ROWS.find(r => r.rel_path === path);
}

function setSelected(path) {
  selectedPath = path;
  document.querySelectorAll(".card").forEach(c => {
    c.classList.toggle("selected", c.dataset.path === path);
  });
}

function relabelSelected(tier) {
  const r = rowByPath(selectedPath);
  if (!r) return;
  applyTier(r, tier, true);
  paintMetrics();
  render();
  setSelected(selectedPath);
}

function visibleRows() {
  return ROWS.filter(match);
}

function moveSelection(delta) {
  const vis = visibleRows();
  if (!vis.length) return;
  let i = vis.findIndex(r => r.rel_path === selectedPath);
  if (i < 0) i = 0;
  else i = Math.max(0, Math.min(vis.length - 1, i + delta));
  selectedPath = vis[i].rel_path;
  render();
  setSelected(selectedPath);
  const node = document.querySelector(`.card[data-path="${CSS.escape(selectedPath)}"]`);
  if (node) node.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function exportLabels() {
  // Full set with current tiers (including unedited).
  const body = ROWS.map(r => JSON.stringify({
    rel_path: r.rel_path,
    kind: KIND,
    sheet: r.sheet || "",
    stratum: r.stratum || "",
    prior_glam: r.prior_glam != null ? r.prior_glam : -1,
    true_exposure: r.true_tier,
    reveal_at_end: false,
    peak_time_sec: null,
    note: r.note || "",
  })).join("\\n") + "\\n";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([body], { type: "application/x-ndjson" }));
  a.download = "labels-" + KIND + ".jsonl";
  a.click();
  const n = ROWS.filter(r => r.true_tier !== ORIG[r.rel_path]).length;
  alert(
    "Downloaded labels-" + KIND + ".jsonl" +
    (n ? ` (${n} changed)` : "") +
    "\\n\\nImport into the archive with:\\n" +
    "py scripts/eval_reel_classifier.py label --kind " + KIND +
    " --import %USERPROFILE%\\\\Downloads\\\\labels-" + KIND + ".jsonl"
  );
}

function render() {
  paintMetrics();
  updateDirty();
  const rows = visibleRows();
  el("count").textContent = `${rows.length} / ${ROWS.length} shown`;
  const g = el("grid");
  if (!rows.length) {
    g.innerHTML = `<div class="empty">No items match this filter</div>`;
    return;
  }
  if (!rows.some(r => r.rel_path === selectedPath)) {
    selectedPath = rows[0].rel_path;
  }
  g.innerHTML = rows.map(r => {
    const src = r.sheet ? ("sheets/" + r.sheet) : "";
    const tName = TIER_SHORT[r.true_tier] || ("tier " + r.true_tier);
    const gName = GLAM_SHORT[r.true_glam] || ("glam " + r.true_glam);
    const anchor = ANCHORS[String(r.true_tier)] || "";
    const edited = r.true_tier !== ORIG[r.rel_path];
    const sel = r.rel_path === selectedPath ? " selected" : "";

    const legOk = r.leg_ok;
    const ordOk = r.ord_ok;
    const legGlamName = r.leg_glam >= 0 ? (GLAM_SHORT[r.leg_glam] || "") : "failed";
    const ordTierName = r.ord_tier >= 0 ? (TIER_SHORT[r.ord_tier] || "") : "failed";
    const ordGlamName = r.ord_glam >= 0 ? (GLAM_SHORT[r.ord_glam] || "") : "";

    const tierBtns = [0,1,2,3,4].map(t =>
      `<button type="button" class="tier-btn${r.true_tier === t ? " on" : ""}" data-tier="${t}" data-path="${esc(r.rel_path)}" title="${esc(TIER_SHORT[t] || "")}">${t}</button>`
    ).join("");

    return `<article class="card ${cardClass(r)}${sel}" data-path="${esc(r.rel_path)}" tabindex="0">
      <div class="thumb">${src ? `<img loading="lazy" src="${esc(src)}" alt="">` : ""}</div>
      <div class="body">
        <div class="path">${esc(r.rel_path)}${edited ? ' <span class="edited-badge">edited</span>' : ""}</div>

        <div class="block truth">
          <div class="block-title">
            <span class="who">1 · Your label (truth)</span>
            <span class="verdict truth">${edited ? "edited" : "ground truth"}</span>
          </div>
          <div class="mainline">${esc(tName)}</div>
          <div class="detail">Exposure <b>tier ${r.true_tier}</b> → product <b>glam ${r.true_glam}</b> (${esc(gName)}).
            ${edited ? "Was T" + ORIG[r.rel_path] + "." : ""}</div>
          <div class="codes">code: T${r.true_tier} → G${r.true_glam}</div>
          <div class="why">${esc(anchor)}</div>
          <div class="tier-row" title="Click to relabel this photo">
            <span class="tier-lbl">Set your tier:</span>
            ${tierBtns}
          </div>
        </div>

        <div class="block ${legOk ? "ok" : "bad"}">
          <div class="block-title">
            <span class="who">2 · Legacy model (current default)</span>
            <span class="verdict ${legOk ? "ok" : "bad"}">${legOk ? "✓ glam match" : "✗ glam wrong"}</span>
          </div>
          <div class="mainline">${r.leg_glam >= 0 ? "Glam " + r.leg_glam + " — " + esc(legGlamName) : "No score"}</div>
          <div class="detail">Old boolean classifier. Only outputs glam 0–3 (no tier).</div>
          <div class="codes">code: G${r.leg_glam} &nbsp;·&nbsp; needed G${r.true_glam}</div>
          <div class="why">${esc(glamDeltaText(r.leg_glam, r.true_glam))}</div>
        </div>

        <div class="block ${ordOk ? "ok" : "bad"}">
          <div class="block-title">
            <span class="who">3 · Ordinal model (candidate)</span>
            <span class="verdict ${ordOk ? "ok" : "bad"}">${ordOk ? "✓ glam match" : "✗ glam wrong"}</span>
          </div>
          <div class="mainline">${r.ord_tier >= 0
            ? "Tier " + r.ord_tier + " — " + esc(ordTierName)
            : "No score"}</div>
          <div class="detail">Maps to <b>glam ${r.ord_glam >= 0 ? r.ord_glam : "—"}</b>
            ${ordGlamName ? "(" + esc(ordGlamName) + ")" : ""}.</div>
          <div class="codes">code: T${r.ord_tier >= 0 ? r.ord_tier : "?"} → G${r.ord_glam}
            &nbsp;·&nbsp; needed T${r.true_tier} → G${r.true_glam}</div>
          <div class="why">${esc(tierDeltaText(r.ord_tier, r.true_tier))}
            ${esc(glamDeltaText(r.ord_glam, r.true_glam))}</div>
        </div>

        ${r.note ? `<div class="note">Note: ${esc(r.note)}</div>` : ""}
      </div>
    </article>`;
  }).join("");

  // bind card select + tier buttons
  g.querySelectorAll(".card").forEach(card => {
    card.addEventListener("click", (e) => {
      if (e.target.closest(".tier-btn")) return;
      setSelected(card.dataset.path);
    });
  });
  g.querySelectorAll(".tier-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      selectedPath = btn.dataset.path;
      relabelSelected(Number(btn.dataset.tier));
    });
  });
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
el("exportLabelsBtn").onclick = exportLabels;
el("resetEditsBtn").onclick = () => {
  if (!confirm("Reset all in-page edits back to the original labels?")) return;
  ROWS.forEach(r => applyTier(r, ORIG[r.rel_path], false));
  localStorage.removeItem(STORE_KEY);
  paintMetrics();
  updateDirty();
  render();
};

addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;
  if (e.key >= "0" && e.key <= "4") {
    e.preventDefault();
    relabelSelected(Number(e.key));
  } else if (e.key === "j" || e.key === "ArrowLeft") {
    e.preventDefault();
    moveSelection(-1);
  } else if (e.key === "k" || e.key === "ArrowRight") {
    e.preventDefault();
    moveSelection(1);
  } else if (e.key === "e") {
    e.preventDefault();
    exportLabels();
  }
});

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
                "prior_glam": item.prior_glam,
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
    from promptstudio.scraping.outfit_classifier import TIER_TO_GLAM

    html = (
        _COMPARE_HTML.replace("__ROWS__", json.dumps(rows, ensure_ascii=False))
        .replace("__META__", json.dumps(meta, ensure_ascii=False))
        .replace(
            "__ANCHORS__",
            json.dumps({str(k): v for k, v in TIER_ANCHORS.items()}, ensure_ascii=False),
        )
        .replace(
            "__TIER_SHORT__",
            json.dumps({str(k): v for k, v in _TIER_SHORT.items()}, ensure_ascii=False),
        )
        .replace(
            "__GLAM_SHORT__",
            json.dumps({str(k): v for k, v in _GLAM_SHORT.items()}, ensure_ascii=False),
        )
        .replace(
            "__TIER_TO_GLAM__",
            json.dumps({str(k): v for k, v in TIER_TO_GLAM.items()}, ensure_ascii=False),
        )
        .replace("__KIND__", json.dumps(kind))
        .replace("__KIND_PLAIN__", kind)
    )
    atomic_write_text(out_path, html)
    return out_path
