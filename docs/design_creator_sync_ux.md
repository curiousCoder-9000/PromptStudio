# Creator “Sync new posts” — refined UX plan

| Field | Value |
|-------|--------|
| **Status** | Implemented (+ refresh hydrate; full deep enqueue) |
| **Date** | 2026-08-08 |
| **Problem** | Sidebar → Sync opens the full Instagram Sync modal; feels cluttered and wrong for a one-click catch-up |

---

## Current UX (pain)

```
Select creator → Sync new posts
  → toast (brief)
  → openSyncModal()  ← full panel: scrape form, saved, following, keywords, status, queue list
```

Issues:

1. **Wrong surface for the task.** User intent is “refresh this folder,” not “open the IG control room.”
2. **Modal steals focus** and covers the gallery while a background job runs.
3. **Information overload** — full archive, bounded, following bulk, pause/resume — irrelevant to “sync latest for @x.”
4. **Second creator** already returns `queued` from API, but that message is buried under the same heavy modal.
5. **Sidebar is already dense** (Sync + Classify + Style). Progress belongs as a *light* global affordance, not another wall of form controls.

Backend is fine: enqueue → `started` | `queued` | `already_*`. The fix is almost entirely presentation + progressive disclosure.

---

## UX principles (research-backed)

| Principle | Application |
|-----------|-------------|
| **Progressive disclosure** | One-click primary path; advanced tools stay behind the main Instagram button |
| **Non-blocking background work** | Don’t modal-block for multi-minute IG jobs (same idea as Dropbox/OS sync chips, not install wizards) |
| **Immediate outcome language** | First creator: “Syncing @x…”; second: “Queued @y (position 2)” — match API statuses exactly |
| **Status at glance, detail on demand** | Ephemeral toast + optional compact floating job chip; deep log only if user expands |
| **Don’t compete with Classify** | Sync feedback should not look like another full control panel in the sidebar |

Patterns to mirror (conceptually):

- **Toast / snackbar** for the *moment of the click* (started vs queued).
- **Floating job pill** (bottom or corner) while *any* creator scrape is active — dismissible, non-modal.
- **Creator row micro-state** (optional): spinner or “syncing” pill only on the active handle — like Classify’s `3/40` but lighter.

Anti-patterns to drop:

- Auto-opening Sync modal on sidebar Sync.
- Duplicating full queue UI inside the creator style panel.
- Progress that requires reading raw downloader log lines by default.

---

## Proposed experience

### Happy path A — first creator (idle queue)

1. Select `@jenna_chew` in sidebar.
2. Click **Sync new posts**.
3. **No modal.**
4. **Toast / compact popup** (3–4s, or until next toast):

   > **Syncing @jenna_chew**  
   > Fetching new posts…

5. Optional **floating job chip** appears (stays until job ends):

   ```
   [⟳] Syncing @jenna_chew · catch-up     [Cancel] [×]
   ```

6. On complete: toast  
   > **@jenna_chew done** — 4 new, 12 skipped  
   Chip disappears; gallery soft-refreshes if that creator is selected.

### Happy path B — second creator while first runs

1. Select `@other`, click **Sync new posts**.
2. Toast:

   > **Queued @other**  
   > Waiting behind @jenna_chew · position 2

3. Chip updates to show depth, e.g.:

   ```
   [⟳] Syncing @jenna_chew · +1 queued     [Open queue]
   ```

4. When `@other` starts: toast  
   > **Syncing @other**  
   Chip switches current handle.

### Already pending / running

- `already_running` → toast: “Already syncing @x”
- `already_pending` → toast: “@x already queued”
- No modal.

### Error / rate-limit pause

- Toast (error style): “Sync paused — rate limited. Resume from Instagram when ready.”
- Chip: `Paused · rate limit` + **Resume** / **Dismiss**.

---

## UI inventory (what goes where)

| Surface | Role after redesign |
|---------|---------------------|
| Sidebar **Sync new posts** | Single primary action; optional busy/disabled while *this* creator is running |
| **Toast** | Immediate result of click + terminal result (done / fail) |
| **Floating job chip** | Living status for active scrape queue (current @handle, queued count, cancel) |
| **Creator list pill** (optional, light) | Tiny `sync` / spinner on running creator only |
| **Main Instagram Sync modal** | Advanced only: Saved, Following bulk, full archive enqueue, multi-handle tools — **never auto-opened by sidebar Sync** |
| Sidebar scrape mini-panel | **Remove** Instagram subsection clutter if chip covers it; keep only one button |

### Suggested toast copy (exact)

| API `status` | Toast title | Subtitle |
|--------------|-------------|----------|
| `started` | Syncing @{user} | Fetching new posts… |
| `queued` | Queued @{user} | Waiting · position {n} |
| `already_running` | Already syncing @{user} | — |
| `already_pending` | @{user} already queued | — |
| HTTP error / busy | Can’t sync | {message} |
| job `done` | @{user} sync complete | {downloaded} new · {skipped} skipped |
| job `error` / abort | @{user} sync stopped | {reason} |

Use a **richer toast** (title + line) or a **non-modal mini-dialog** that auto-dismisses — not the full Sync modal. Prefer extending existing `.toast` with `.toast-title` / multi-line rather than a blocking dialog.

---

## Interaction details

### Polling

- Start silent poll of `/api/scrape/status` when a job is started or queued from sidebar (no modal open).
- Stop poll when queue idle and no chip visible.
- Do **not** require Sync modal open for progress (today’s gap).

### Cancel

- Chip **Cancel** → `POST /api/sync/cancel` (running) or cancel pending job by id.
- Keep advanced pause/resume only in full Sync modal or chip overflow menu.

### Gallery refresh

- On terminal `done` for the selected creator: refresh photos once (existing `initApp` / `fetchPhotos` path).
- Don’t refresh whole app on every progress tick.

### Accessibility

- Toast is polite live region; chip is focusable with Cancel.
- Don’t trap focus (no modal).

---

## Sidebar declutter (layout)

**Before:** Instagram label + full-width Sync + Classify block + Style block.

**After (recommended):**

```
[ Sync new posts ]     ← one button; shows spinner when this creator is active

Classify & clean
  meta · Classify · Review rejects · Cancel

Creator style
  …
```

No queue list, no mode checkboxes, no “scrape status” text dump in the sidebar.

Optional: button subtitle when busy:

```
Sync new posts
@jenna_chew · running
```

---

## Full Sync modal (unchanged purpose, less default traffic)

Keep for power users:

- Full archive / bounded / latest enqueue for arbitrary handles  
- Saved / following bulk  
- Pause / resume / clear pending  

Entry: header **Instagram** button only.  
Sidebar Sync never routes here.

Optional later: link on chip “Open Instagram tools” for rare cases.

---

## Implementation plan (when approved)

### PR1 — Stop modal hijack + status toasts (minimal, high impact)

- Remove `openSyncModal()` from `syncLatestSelectedCreator`.
- Map API statuses to clear toast copy (started vs queued + position).
- Background poll when scrape active without modal; toast on completion; refresh gallery if selected creator matches.
- Files: `app.js` mainly.

### PR2 — Floating job chip

- HTML/CSS: `#scrapeJobChip` fixed bottom-right (glass style).
- Shows current `@user`, mode, queued count, Cancel, dismiss.
- Poll drives chip; hidden when idle.
- Files: `index.html`, `style.css`, `app.js`.

### PR3 — Sidebar polish + optional row pill

- Spinner on Sync button when selected creator is running.
- Optional small pill on creator row while that handle runs.
- Soften/remove redundant status blocks if any remain.

### PR4 — Toast component upgrade (if needed)

- Multi-line toast (`title` + `body`) and variants `info` / `success` / `error`.
- Auto-stack if two creators queued in quick succession.

No backend API changes required for PR1–PR3 (status endpoints already exist).

---

## Alternatives considered

| Option | Verdict |
|--------|---------|
| Keep opening Sync modal | Rejected — too heavy for primary path |
| Blocking “please wait” dialog until done | Rejected — multi-minute IG jobs; freezes UX |
| Only toast, no chip | OK for V1 (PR1 only); chip better for long jobs |
| Progress in creator style panel | Rejected — adds clutter next to Classify |
| Per-creator mini modal with progress bar % | Misleading (no known total); skip % |

---

## Follow-ups (2026-08-08)

| Change | Why |
|--------|-----|
| Sidebar enqueue `mode=full, deep=true` (no `max_posts: 50`) | `latest`+ceiling left Mikayla with only newest 50; older gaps never walked |
| `hydrateScrapeUiFromServer()` on `initApp` | Hard refresh wiped chip/pills even though queue still running on server |
| Reverted extra post soft-pauses / paced `do_sleep` | Instaloader request sleep + existing 4–12s post delay are enough |

---

## Success criteria

1. Click Sync on creator **never** opens full Sync modal.
2. First job → user sees clear **“Syncing @name”** feedback within 1s.
3. Second job → user sees clear **“Queued @name”** (with position if available).
4. Can keep browsing gallery while sync runs.
5. Done → short success feedback; new media appear when viewing that creator.
6. Power users still reach full tools via main Instagram button.
7. Hard refresh while a job runs → chip (and creator pill) restore from `/api/scrape/status`.
8. Sidebar Sync walks full feed for missing posts (does not stop after 50).

---

## Open choices (defaults)

| # | Choice | Default |
|---|--------|---------|
| 1 | Toast-only first, chip in PR2? | **Yes** — PR1 alone already fixes the main complaint |
| 2 | Chip position | Bottom-right, above toast stack |
| 3 | Auto-open chip when job from Sync modal too? | Yes, if any `creator_queue` job runs |
| 4 | Sidebar button label | Keep “Sync new posts” |

---

## Summary

Treat **sidebar Sync** as a lightweight “refresh this creator” action: **toast for started vs queued**, optional **floating chip** for ongoing status, **never** the full Instagram Sync modal. Keep the heavy modal for multi-tool / bulk workflows only.
