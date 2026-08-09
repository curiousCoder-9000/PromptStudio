# UI & product review — gaps, features, and the fix log

| Field | Value |
|-------|--------|
| **Date** | 2026-08-09 |
| **Scope** | Frontend (`index.html` · `app.js` · `style.css`), UX, and product gaps not covered elsewhere |
| **Companions** | [`product_review.md`](product_review.md) — value chain, Themes A/B/C/E · [`review_backend_architecture.md`](review_backend_architecture.md) — durability, storage, observability |
| **Why a third review** | Neither companion audits the UI, and both rest on one premise (§0) that turned out to be false |
| **Status** | Stage 1 shipped — see the fix log in §5 |

---

## 0. A correction to a load-bearing assumption

`product_review.md` §3 records an accepted risk: *"No auth, CORS `*`. Correct for
**localhost-only**."*

It was not localhost-only.

```
promptstudio/config.py:53    HOST = os.environ.get("PROMPTSTUDIO_HOST", "")
handler.py                   ThreadingHTTPServer((host, port), …)   # "" → INADDR_ANY
handler.py                   log.info("PromptStudio running at http://localhost:%s")
```

An empty bind address listens on **every interface**. With `Access-Control-Allow-Origin: *`,
no auth, `GET /media/...` serving the whole archive and `DELETE /api/photo` live, any host on
the network could read and delete the archive. The startup log said `localhost`, which hid it.

The empty case is the whole bug: `.env.example` shipped `PROMPTSTUDIO_HOST=`, and
`os.environ.get(name, "127.0.0.1")` returns `""` for a set-but-empty variable — **not** the
default. Changing the default alone would have fixed nothing for anyone who copied the
template. Fixed in §5.

---

## 1. UI gaps

| ID | Severity | Gap | Evidence |
|----|----------|-----|----------|
| **U1** | 🔴 | **Breaks offline** — the entire icon set and typography came from cdnjs + Google Fonts, with no local fallback. No network → every icon is an empty box, and 6 buttons have no text at all (`#lightboxClose`, `#lightboxPrev`, `#lightboxNext`, `#gridNormal`, `#gridLarge`, `#photoViewerClose`), so lightbox nav and grid toggles vanish. Also two third-party requests per load from a tool whose premise is that nothing leaves the machine. | `index.html:8-12` |
| **U2** | 🔴 | **No virtualization** on a 4,400+ item archive. `renderGallery` appends a card per photo and never removes one: ~53k DOM nodes and ~8,800 listeners at the bottom of an unfiltered scroll. `loading="lazy"` defers the fetch, not the node. The scroll handler also reads `document.body.offsetHeight` every frame on a document that keeps growing. | `app.js:1450`, `:1512`, `:1526`, `:3367` |
| **U3** | 🟠 | **Three finished subsystems have no UI** — see §2. Highest ROI in the repo: the expensive half is already written and tested. | — |
| **U4** | 🟠 | **Verdict data is trapped in review mode.** `sort=tier` exists in the backend but is not among the 5 options in `#sortSelect`; `verdict=` is only sent when `state.reviewMode` is true, so the normal gallery has no keep/reject chip. "Show me every tier-4 shot" requires entering a delete-oriented mode. | `db.py:1685`, `index.html:156-176` |
| **U5** | 🟠 | **Classify is per-creator only.** `start()` rejects an empty creator and the panel only exists inside `#creatorStylePanel`. Batch Analyze is archive-wide; Classify isn't — and the archive-wide job is the one you'd leave running overnight. | `classify_job.py:164` |
| **U6** | 🟡 | **No shortcut discovery.** `←/→/Esc/Space/F/G/C/S/K/R/X`, shift-seek and double-click fullscreen exist, documented only in `title` attributes and one legend visible during triage. No `?` overlay. | — |
| **U7** | 🟡 | **Thin accessibility.** 16 `aria-*` across 84 buttons, 5 `alt=`. Modals lack `role="dialog"`/`aria-modal`, focus trap, and focus restore on `Esc`. Icon-only buttons have `title` but no `aria-label`. `.filter-chip` toggles never set `aria-pressed`. (The CSS side is good — 3 `prefers-reduced-motion` blocks, 7 `focus-visible` rules.) | `index.html` |
| **U8** | 🟡 | **Upload is the weakest surface.** Single file, no drag-and-drop anywhere, images only (can't add a reel you already have), behind a modal requiring a creator `<select>` first — in an app entirely about bulk media. | `index.html:725` |
| **U9** | 🟡 | **Responsive half-commits.** Three breakpoints plus a `viewport` meta, but the lightbox is a two-pane layout that can't degrade to a phone. Commit to tablet or drop the breakpoints. | `style.css:3363,3383,3419` |
| **U10** | 🟡 | **One empty state for every empty.** Same copy for a fresh install, a filter that matched nothing, and a review pile with zero rejects. The first-run case is where a next action matters most and offers none. Not the onboarding investment §7 cut — one conditional string and a button. | `index.html:216-220` |
| **U11** | 🟢 | **Card overlay density.** Four affordances in a 200px tile, three hover-only. Survivable now; tips when U4 puts verdict badges in the normal gallery. | `app.js:1491` |

### Built, tested, and invisible (U3 detail)

| Subsystem | Where it lives | Surfaced |
|---|---|---|
| pHash near-duplicate detection | `storage/dedupe.py`, `phashes` table, `scripts/find_duplicates.py` | **nothing** — zero refs in `handler.py` or `app.js` |
| Run journal | `storage/journal.py`, `GET /api/journal` (`handler.py:1663`) | **API only** — zero refs in `app.js` |
| Classify tier distribution | `insights.py:_classify_insights` → `distribution`, `top_tier_share`, `error_rate` | **API only** until §5 — `renderInsights` read `data.prompts` and `data.generations`, never `data.classify` |

The third was the sharpest: [`design_media_classifier.md`](design_media_classifier.md) §5 calls
`top_tier_share` "the one number to check after the first real run," and it was computed,
journalled, served over HTTP, and invisible. That is precisely the failure the feature exists to
prevent — the previous classifier shipped at 85% on a single tier with nothing reading the
distribution.

---

## 2. Product features

Summary table. **Full per-item detail — outcome, files touched, pitfalls, done-when — is in
[`backlog_features.md`](backlog_features.md).**

Ordered by value ÷ cost, and deliberately distinct from Themes A/B/C/E already accepted in
[`product_review.md`](product_review.md).

| ID | Size | Feature | Why |
|----|------|---------|-----|
| **F1** ⭐ | S | **Make captions searchable** | `prompt_search_blob` indexes positive prompt, negative prompt, raw vision description and tags — all four LLM-generated. The caption is downloaded and written to every sidecar (`metadata.py:41`) and indexed **nowhere**. The only human-written text in the archive is unsearchable, which makes `product_review` P4 partly self-inflicted. One field plus a reindex — far cheaper than C1 semantic search, and doesn't block it. |
| **F2** ⭐ | S/M | **Archive-wide classify + global review queue** | Extends U5. A keep/reject filter is worth what it covers. |
| **F3** ⭐ | S/M | **Duplicate review UI over the existing pHash work** | `dedupe.py` already groups near-duplicates and only a CLI can see it. Show groups, preselect all but the best copy, route through soft delete + Undo. Reuses review mode. Doubles as a cost win — classify one representative instead of five. |
| **F4** | S | **Activity view over the journal** | `product_review` E2, cheaper than listed because `GET /api/journal` already ships. This is a read-only JSONL render. |
| **F5** | S | **Verdict/tier as browse axes** | The other half of U4: tier in the sort dropdown, verdict chips in the normal gallery, per-creator tier summary in the sidebar. |
| **F6** | S | **Trash as a review surface** | 30 days of the user's explicit negatives — the signal `product_review` P2 says is discarded — rendered as a text list. A pre-labelled negative set for B3. |
| **F7** | M | **Creator lifecycle** | `POST /api/creator/create` and nothing else. No rename, merge, or remove. Merge is a real data operation — `photos.creator` and `media_verdicts.creator` are both denormalised. |
| **F8** | S | **Saved views** | The cheap 80% of C4: save creator + verdict + media type + sort + search under a name. |

---

## 3. Engineering notes

Summary table. **Full detail in [`backlog_engineering.md`](backlog_engineering.md).**

| ID | Note |
|----|------|
| **E1** | **Six pollers, none visibility-aware** — health 30s, comfy 2.5s, scrape 2.5s, sync 2.5s, batch 4s, classify 3s. No `visibilitychange` listener, so a backgrounded tab polls forever; none back off on error. |
| **E2** | **`app.js` (5,516 lines) is now the bigger monolith**, past `handler.py` (1,751). Both reviews prescribe "incremental, inside feature PRs" for S7, right for the handler. `app.js` has one 60-key `state` object mutated by six independent pollers and no module boundaries — nothing declares who owns which key. |
| **E3** | **`CLASSIFY_REJECT_MAX_TIER` is the one env var that belongs in the UI.** Not relitigating the settings-UI cut: this knob differs in kind, because the design deliberately made it runtime-changeable so the user can revise the cut *after* seeing the distribution — and the only way to turn it is editing `.env` and restarting. |
| **E4** | **The recall risk is documented and invisible.** `design_media_classifier.md` §2 states the `1↔2` boundary is unmeasured. Triage shows confidence and prompt version, but nothing distinguishes a T1 verdict (unmeasured boundary) from a T0 one (quality gate). |
| **E5** | **Testing is strong with two holes** — 27 Python modules, 4 browser suites, ruff, CI. Nothing *fails* when `top_tier_share` exceeds 0.6 (B4 is still Todo), and there is no perf regression test, which matters given U2 and AGENTS.md rule 13. |

---

## 4. Sequence

**Stage 1 — bugs and free wins (~1 day).** §0 bind · U3 classify insights · U1 vendored assets ·
E1 poller visibility. **Shipped — see §5.**

**Stage 2 — cheap features with disproportionate effect.** F1 captions · U4/F5 tier as a browse
axis · U5/F2 archive-wide classify · U2 `IntersectionObserver` + `content-visibility`
(measure before and after, per AGENTS.md rule 13).

**Stage 3.** F3 duplicate review · F4 activity view · U6 `?` overlay · U7 a11y pass · U8 drag-drop.

Then Phase 13 as planned. Nothing here competes with closing the generation loop; Stage 1 + 2 is
roughly a week and makes Phase 13 easier to evaluate.

---

## 5. Fix log — Stage 1

| Item | Status | What changed |
|------|--------|--------------|
| §0 **Bind to loopback** | ✅ | `config.resolve_host()` treats blank/absent as `127.0.0.1`; `.env.example` ships `127.0.0.1` with the reason; `run_server` logs the address actually bound and warns on a non-loopback bind instead of printing "localhost". Tests: `tests/test_bind_host.py` (8) — covers the empty-string case and asserts the shipped template never resolves to a non-loopback address. |
| **U1** **Offline assets** | ✅ | Fonts and Font Awesome vendored under `assets/` (613 KB, 12 files); both CDN `<link>`s removed. `scripts/vendor_web_assets.py` regenerates them (`--check` verifies without network) and derives the required-file list from the CSS, so a version bump can't silently drop a webfont. Tests: `tests/test_offline_assets.py` (5). |
| **U3** **Classify insights** | ✅ | `renderInsights` now renders `data.classify`: classified count, reject rate with the active cut, `top_tier_share` with a saturation warning above 0.6, per-tier distribution bars, and a separate failed-to-classify row (tier `-1` is not a measurement, so it never gets a bar). |
| **E1** **Poller visibility** | ✅ | The six intervals pause on `visibilitychange` → hidden and resume with an immediate refresh on visible. |

**Not done in Stage 1, deliberately:** U2 needs a measurement first; U4–U11 and all of §2 are
Stage 2+.
