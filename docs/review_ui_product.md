# UI & product review — gaps, features, and the fix log

| Field | Value |
|-------|--------|
| **Date** | 2026-08-09 |
| **Scope** | Frontend (`index.html` · `app.js` · `style.css`), UX, and product gaps not covered elsewhere |
| **Companions** | [`product_review.md`](product_review.md) — value chain, Themes A/B/C/E · [`review_backend_architecture.md`](review_backend_architecture.md) — durability, storage, observability |
| **Why a third review** | Neither companion audits the UI, and both rest on one premise (§0) that turned out to be false |
| **Status** | Stage 1 shipped (§5) · Stage 2 shipped (§6) · review-mode trap fixed (§8) · U13–U14 shipped (§9) · U15–U16 deferred |

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
| **U12** | 🔴 | **Review mode could not review** — see §8. Entry forced select mode on, which routes a card click to a checkbox instead of the lightbox; the lightbox is the only home of Keep/Reject/Auto and `K`/`R`/`X`. Fixed in §8. | `app.js:5974`, `:1988` |
| **U13** | 🟠 | **Bulk-delete with no bulk-keep.** Reviewing a reject pile is mostly about *rescuing* false rejects, and the only bulk verb in the strip is Delete. `POST /api/classify/verdict` takes a single `rel_path`, so rescuing 30 good shots is 30 open-decide-close cycles against one click to destroy all 30. The destructive path is the ergonomic one. | `handler.py:1153-1162`, `index.html:220-232` |
| **U14** | 🟡 | **"Select non-favourites" silently means "on this page."** It sweeps `state.photos`, which holds only what infinite scroll has loaded. On a 400-item pile the button reads as a pile-wide action, selects the loaded 60, and the count beside it (`N selected of <photoTotal>`) invites reading the rest as covered. | `app.js:6048-6059` |
| **U15** | 🟡 | **Switching verdict chips discards the selection without a word.** `setVerdictFilter` calls `clearSelection()`; ten minutes of curation vanishes on a mis-click. | `app.js:5992` |

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
axis · U5/F2 archive-wide classify · U2 `IntersectionObserver` + `content-visibility`.
**Shipped — see §6, with measurements.**

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

---

## 6. Fix log — Stage 2

| Item | Status | What changed |
|------|--------|--------------|
| **E5b** benchmark harness | ✅ | `scripts/benchmark_queries.py` — seeds a synthetic archive and times the gallery hot paths, reusing S5's methodology (3000-word vocabulary, median of 7) so the numbers stay comparable. Not a CI gate; timings on a shared runner are noise. |
| **F1** captions searchable | ✅ | New `caption_search` column (separate from `prompt_search`: the caption is fixed for the file's life, the prompt blob is rewritten on every regenerate). Populated in `rebuild()` and `upsert_photo` from the sidecar read that already happens, passed explicitly by both downloaders so the hot path gains zero file opens, and backfilled once for existing archives. 17 tests. |
| **F5 / U4** tier + verdict as browse axes | ✅ | `Tier (harshest first)` in the sort dropdown; a verdict `<select>` beside the filter chips; `browseVerdict` added to `PREF_FIELDS`; verdict pills go loud while filtering; the creator pill's tooltip carries the full keep/reject/to-do breakdown. `body.review-mode` now hides `.view-controls` — the class was toggled with no rule attached, so review mode stacked under the normal controls instead of replacing them. |
| **F2 / U5** archive-wide classify | ✅ | `creator=""` means the whole archive in `list_unclassified`, `list_pending`, `start()` and `POST /api/classify/start`. New navbar **Classify All (N)** beside Batch Analyze, with a confirm (it holds the vision model) and a live count. The chip reports `all creators · @current`. 12 new tests plus one existing test updated — `""` used to be `bad_creator`. |
| **U2** gallery render cost | ✅ | `content-visibility: auto` + `contain-intrinsic-size` on `.photo-card`, and the infinite-scroll probe replaced with an `IntersectionObserver` on the existing sentinel (the old handler read `document.body.offsetHeight` every animation frame of every scroll). |

## 7. Fix log — bugs found reviewing Stage 2

Found by driving the UI in a browser rather than reading it. Both shipped in
`2697495`, where the archive-wide classify and the source filter landed in the same
commit from two sessions and neither was reconciled against the other.

| Bug | Status | What changed |
|-----|--------|--------------|
| Archive-wide classify finished at `@creator` | ✅ | `pollClassifyStatus` fell back to the literal string `'creator'` when `data.creator` was `""` — the archive-wide scope the *running* path already handles. So an overnight Classify All ended on a toast reading **"Classify done @creator"** whose Review button navigated to a folder of that name: `?creator=creator&verdict=reject`, always empty, and it discarded the real selection on the way. Completion split into `announceClassifyFinished()` + `reviewAfterClassify()`, which drops the creator selection so review covers the scope the toast counted. Also refetches the gallery after an archive-wide run — verdict badges on screen were left stale. |
| **Classify All** lied under a source filter | ✅ | The count came from `state.creators`, which `/api/creators?source=` narrows; the job it starts is archive-wide regardless. Filter to a platform whose backlog happens to be clear and the button **disabled itself** saying "every creator is already classified" while another platform's sat untouched. New `ArchiveIndex.unclassified_total()` on `/api/stats` — never scoped to anything — replaces the sidebar sum. |

Regression tests: 4 checks in `tests/ui/test_classify_review.js`, 3 in
`tests/ui/test_source_filter.js`, 4 in `tests/test_stats.py`. All seven browser checks were
confirmed to fail against pre-fix `HEAD` in a throwaway worktree before being kept.

**Harness bug found the same way** — `tests/ui/run.sh` hardcoded ports 5099/9222 and its
readiness probe only asked "is anything answering?". With a leftover run or a second agent
holding those ports (routine in this repo), our server and Chrome failed to bind and every
suite silently drove *the stranger's* browser against *the stranger's* archive: 20 bogus
failures across 4 suites, and it can produce a false pass just as easily. Ports are now
auto-picked from what is free, an explicitly pinned port that is taken is a hard error, and
`wait_for` takes the child PID so a dead server fails fast instead of adopting a squatter.

## 8. Fix log — review mode was a trap (U12)

Reported from use: *"after classification, clicking Review rejects lands on a page where
auto-select is on by default, I can't review the posts, and there's no way out."*

**One line caused all of it.** `enterReviewMode()` ended with `setSelectMode(true)`, and
select mode is precisely what makes a card click stop opening anything:

```
app.js:5974   enterReviewMode()  → setSelectMode(true)
app.js:1988   card click         → if (state.selectMode) toggle checkbox; else openLightbox()
app.js:6194   handleTriageKey()  → returns false unless state.lightboxIndex >= 0
index.html    #triageKeepBtn / #triageRejectBtn / #triageAutoBtn live *inside* the lightbox
```

So the one mode built for triage could do everything except triage. Keep, Reject, Auto and
every `K`/`R`/`X` shortcut sat behind a door the mode itself locked, and bulk delete was the
only reachable verb — in a mode entered straight from a machine-generated reject list. The
escape hatch was hidden too: `body.review-mode .view-controls { display: none }` (added in
§6 for a good reason) hides `#selectModeBtn`, the only toggle select mode had.

The line was vestigial from `1dd161c`, which introduced the classifier. `selectNonFavourites()`
calls `setSelectMode(true)` itself, so nothing ever depended on entry-time select mode — and
the strip's own hint read *"Click any card to open triage"*, documenting the behaviour the
code prevented.

| Fix | Status | What changed |
|-----|--------|--------------|
| **U12a** Triage reachable | ✅ | `enterReviewMode()` sets select mode **off**. A click opens the lightbox and triage, which is what the strip always claimed. |
| **U12b** A way out | ✅ | New `#reviewSelectToggleBtn` in the strip — same button in and out, `Select` ⇄ `Selecting`, cyan `.filter-chip.active` matching `#selectModeBtn` so "selecting" looks identical in both surfaces. Plus `#reviewClearBtn`, which appears only with a live selection (the normal `#bulkBar` had a Clear; the review strip did not). |
| **U12c** Escape unwinds | ✅ | The select-mode branch used to sit *below* `if (state.creatorPanelOpen) { …; return; }`, and `enterReviewMode` sets `creatorPanelOpen` — so on the exact route out of a finished classify, the keyboard escape hatch was dead. Transient gallery modes now unwind before the persistent side panel, one layer per press: selection → select mode → review mode → creator panel. |
| **U12d** Honest hint | ✅ | `#reviewBarHint` is rewritten per state by `updateReviewBar()`. One fixed string had to be lying in one of the two modes. |

Regression tests: 30 new checks in `tests/ui/test_classify_review.js` (72 total, all passing).
The suite had covered this area for months and passed throughout, because it reached the
panel with a direct `openLightbox(0)` — **it tested the destination and never the route.**
The new checks click a real `.photo-card` and drive the real handler; they were confirmed
failing against pre-fix `HEAD` (`clicking a card opens the lightbox (none)`, click selected 1
item instead) before the fix was written.

### Deferred — the same review surface, still rough

U13 is the one worth doing next: it is the difference between a reject pile you curate and
one you rubber-stamp.

| ID | Plan | Cost |
|----|------|------|
| **U13** bulk keep/reject | ✅ shipped §9 | S/M |
| **U14** paged select honesty | ✅ shipped §9 (and the real pile-wide select) | S |
| **U15** selection-loss guard | Keep the selection across a verdict-chip switch (paths are stable), or confirm before dropping a non-empty one. Prefer keeping: the chips are views over one pile. | S |
| **U16** grid-level triage | Keep/Reject directly on the card in review mode, so a decision costs one click instead of open → decide → close. Deliberately last: it lands on `.photo-card`, which U11 already calls crowded at four affordances in a 200px tile, and U13 removes most of the pressure for it. | M |

### U2 measurement

Required by AGENTS.md rule 13. A/B'd in one browser session at **780 cards** by
overriding the property at runtime, median of 5:

| | `content-visibility: visible` | `auto` | |
|---|---:|---:|---|
| full `renderGallery()` | 69.2 ms | **27.2 ms** | 2.5× |
| grid-size relayout | 18.5 ms | **2.7 ms** | 6.9× |
| `document.body.scrollHeight` | 93,091 px | 93,091 px | unchanged |

The identical scroll height is the part that mattered: `contain-intrinsic-size: auto 260px`
uses the *remembered* size for anything already rendered, so skipping off-screen cards does
not move the scrollbar. Extrapolated to a 4,400-item archive the re-render goes from ~390 ms
to ~154 ms.

**Not done:** true windowing. The DOM-node count is unchanged (9,925 at 780 cards) — this
buys the layout and paint cost, not the memory. Revisit only if a measurement says the node
count itself is the problem; the current fix keeps selection, keyboard nav and Ctrl-F working,
which windowing would break.

### F1 measurement

| | prompt only | + caption | |
|---|---:|---:|---|
| 4,400 rows, worst-case search | 8.3 ms | **9.4 ms** | +1.1 ms |
| 40,000 rows, worst-case search | 119.5 ms | **125.6 ms** | +5% |

Imperceptible at real archive size. Worth noting from the same run: at 40k rows search is
already 65–120 ms, which is the scale at which S5's FTS5 decision deserves revisiting.

## 9. Fix log — U13/U14 bulk rescue + U10 empty states

| Item | Status | What changed |
|------|--------|--------------|
| **U13** bulk keep | ✅ | `POST /api/classify/verdict` accepts `rel_paths[]`; `ArchiveIndex.set_manual_verdicts()` writes one transaction. Review strip gained **Keep selected** beside Delete. Kept items leave the current filter (they are rescued, not still sitting in the reject pile). Unclassified paths are reported in `missing`, never invented. Single-path clients are unchanged. |
| **U14** select-all honesty | ✅ | Count reads `N selected of 400 · 60 loaded` when the pile is larger than the page. **Select loaded (N)** is the page sweep; **Select all 400** fetches `GET /api/photos?ids=1` (same filters, `{rel_path, favorite}` only) and still skips favourites. |
| **U10** empty states | ✅ | First-run, a filter miss, an empty review pile, and an empty creator each have their own copy. First-run shows a scrape field (handle or profile URL → Instagram / X / Reddit) rather than "try a different creator." Not the onboarding wizard §7 cut. |
