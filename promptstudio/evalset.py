"""Labelled evaluation set for the reel classifier.

Every quality claim about the classifier is currently unverifiable: there is no
ground truth, so "did that prompt change help" is an opinion. This is the
missing half — sample reels, label them once by hand, then score any pipeline
version against the same frozen set and compare numbers.

Four stages, each resumable:

    sample   pick a stratified set, render one contact sheet per reel
    label    static HTML page, keyboard-driven, exports JSONL
    run      score the labelled set with the live pipeline
    report   metrics vs labels, diffed against a saved baseline

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
LABELS_FILE = os.path.join(EVAL_DIR, "labels.jsonl")
SHEETS_DIR = os.path.join(EVAL_DIR, "sheets")
RESULTS_DIR = os.path.join(EVAL_DIR, "results")

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
    """One reel: what it is, what a human said, what the pipeline said."""

    rel_path: str
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
    exact_accuracy: float = 0.0
    within_one: float = 0.0
    reveal_recall: float = 0.0
    reveal_n: int = 0
    top_score_share: float = 0.0
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
        exact = sum(1 for i, r in ok_pairs if r.predicted_tier == i.true_exposure)
        near = sum(1 for i, r in ok_pairs if abs(r.predicted_tier - i.true_exposure) <= 1)
        m.exact_accuracy = round(exact / len(ok_pairs), 4)
        m.within_one = round(near / len(ok_pairs), 4)
        m.mean_signed_error = round(
            sum(r.predicted_tier - i.true_exposure for i, r in ok_pairs) / len(ok_pairs), 3
        )

        # The headline: reels whose real outfit only appears at the end.
        reveals = [(i, r) for i, r in ok_pairs if i.reveal_at_end]
        m.reveal_n = len(reveals)
        if reveals:
            hit = sum(1 for i, r in reveals if r.predicted_tier >= i.true_exposure)
            m.reveal_recall = round(hit / len(reveals), 4)

        hist: Dict[str, int] = {}
        for _, r in ok_pairs:
            hist[str(r.glam_score)] = hist.get(str(r.glam_score), 0) + 1
        m.score_hist = dict(sorted(hist.items()))
        m.top_score_share = round(max(hist.values()) / len(ok_pairs), 4)

        confusion: Dict[str, int] = {}
        for i, r in ok_pairs:
            confusion[f"{i.true_exposure}->{r.predicted_tier}"] = (
                confusion.get(f"{i.true_exposure}->{r.predicted_tier}", 0) + 1
            )
        m.confusion = dict(sorted(confusion.items()))

    m.median_vision_calls = round(statistics.median([r.vision_calls for _, r in pairs]), 2)
    m.median_ms = round(statistics.median([r.ms for _, r in pairs]), 1)
    return m


# ── running the pipeline ─────────────────────────────────────────────


def score_item(rel_path: str, base_dir: str = SAVED_DIR) -> EvalResult:
    """Score one reel with the *current* pipeline. Requires Ollama."""
    from promptstudio.scraping.outfit_classifier import classify_video

    full = os.path.join(base_dir, *rel_path.split("/"))
    result = EvalResult(rel_path=rel_path)
    if not os.path.isfile(full):
        result.error = "missing_file"
        return result

    started = time.monotonic()
    try:
        verdict = classify_video(full)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"[:200]
        result.ms = int((time.monotonic() - started) * 1000)
        return result

    evidence = verdict.evidence or {}
    result.ok = verdict.ok
    result.predicted_tier = verdict.exposure_tier
    result.glam_score = verdict.glam_score
    result.vision_calls = int(evidence.get("frames_sent_to_vision") or 0)
    result.peak_time_sec = evidence.get("peak_time_sec")
    result.error = verdict.error
    result.prompt_version = verdict.prompt_version
    result.ms = int((time.monotonic() - started) * 1000)
    return result


def save_results(name: str, results: Sequence[EvalResult], meta: Dict[str, Any]) -> str:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"{name}.json")
    atomic_write_json(
        path, {"meta": meta, "results": [r.to_dict() for r in results]}
    )
    return path


def load_results(name: str) -> tuple:
    path = os.path.join(RESULTS_DIR, f"{name}.json")
    if not os.path.isfile(path):
        return [], {}
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    results = [EvalResult(**r) for r in payload.get("results", [])]
    return results, payload.get("meta", {})


def list_results() -> List[str]:
    try:
        return sorted(
            n[: -len(".json")] for n in os.listdir(RESULTS_DIR) if n.endswith(".json")
        )
    except OSError:
        return []


# ── labelling page ───────────────────────────────────────────────────

_LABEL_HTML = """<!doctype html>
<meta charset="utf-8">
<title>Reel eval labelling</title>
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
  <h1>Reel eval <span id="count"></span></h1>
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
const KEY = "reel-eval-labels";
const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
ITEMS.forEach(it => Object.assign(it, saved[it.rel_path] || {}));
let idx = Math.max(0, ITEMS.findIndex(i => i.true_exposure === null));
if (idx === -1) idx = 0;

const el = id => document.getElementById(id);
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
  else if (e.key === "r") el("reveal").click();
  else if (e.key === "j" || e.key === "ArrowLeft") el("prev").click();
  else if (e.key === "k" || e.key === "ArrowRight") el("next").click();
  else if (e.key === "e") el("export").click();
});
render();
</script>
"""


def render_label_page(items: Sequence[EvalItem], out_path: str) -> str:
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
        .replace("__LABELS__", LABELS_FILE)
    )
    atomic_write_text(out_path, html)
    return out_path
