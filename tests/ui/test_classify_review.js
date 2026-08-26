/**
 * Keep/reject review mode.
 *
 * What is worth asserting in a browser and nowhere else:
 *
 * - Review mode reaches the *server* with the right filter. The verdict is
 *   derived from a tier against a server-side threshold, so a client that
 *   guessed which cards are rejects would drift the moment the threshold moved.
 * - "Select non-favourites" really skips a favourite. It is the only guard
 *   between a machine verdict and a bulk delete.
 * - K/R in triage issue exactly one POST and do NOT refetch the gallery. A
 *   refetch would pull the item out of the filtered page under the cursor and
 *   lose scroll position — the same class of bug the delete flow suite exists
 *   to prevent.
 * - Escaping holds on a model-authored reason string, which is the one field
 *   here that is not typed by the user.
 * - Triage is reachable by *clicking a card*. This suite used to reach the
 *   panel with a direct openLightbox(0) call, which tested the destination and
 *   never the route — so it passed for months while review mode force-enabled
 *   select mode and made the click open nothing at all. Drive the real handler.
 * - Select mode inside review mode is opt-in, reversible by the same control,
 *   and unwindable by Escape. A mode with no advertised exit is a trap.
 */
const { Session, Report, sleep } = require('./cdp');

(async () => {
  const s = new Session();
  const r = new Report('classify review mode');
  await s.connect();
  await s.load();
  await sleep(600);

  // ── entering review mode ───────────────────────────────────────────
  r.section('entering review mode');

  const creator = await s.eval(`
    return (state.creators[0] && state.creators[0].name) || null;
  `);
  r.check('fixture creator present', Boolean(creator), String(creator));

  const counts = await s.eval(`
    const c = state.creators[0] || {};
    return { keep: c.keep_count, reject: c.reject_count, unusable: c.unusable_count,
             modest: c.modest_count, total: c.photo_count };
  `);
  r.check('creator carries verdict counters', counts.reject > 0 && counts.keep > 0,
    JSON.stringify(counts));
  r.check('reject splits into unusable + modest',
    counts.unusable + counts.modest === counts.reject,
    `${counts.unusable}+${counts.modest} vs ${counts.reject}`);

  await s.startRecordingFetches();
  await s.eval(`enterReviewMode(${JSON.stringify(creator)}); return true;`);
  await sleep(800);

  const enterLog = (await s.fetchLog()).calls;
  const photosCall = enterLog.filter((u) => u.includes('/api/photos'));
  r.check('review entry issues one photos request', photosCall.length === 1,
    photosCall.join(' | '));
  r.check('request carries verdict=reject',
    photosCall.some((u) => u.includes('verdict=reject')), photosCall[0] || '');
  r.check('request sorts harshest-first',
    photosCall.some((u) => u.includes('sort=tier')), photosCall[0] || '');

  const barVisible = await s.eval(`
    return getComputedStyle(document.getElementById('reviewBar')).display;
  `);
  r.check('review strip is visible', barVisible === 'flex', barVisible);

  const bulkHidden = await s.eval(`
    return getComputedStyle(document.getElementById('bulkBar')).display;
  `);
  r.check('normal bulk bar stays hidden', bulkHidden === 'none', bulkHidden);

  // ── triage is reachable by clicking a card ─────────────────────────
  //
  // The whole point of review mode. Entry used to call setSelectMode(true),
  // and a card click in select mode toggles a checkbox instead of opening the
  // lightbox — so Keep/Reject/Auto and every K/R/X shortcut sat behind a door
  // the mode itself locked. Bulk delete was the only reachable verb.
  r.section('triage reachable by click');

  r.check('review mode does not force select mode on',
    (await s.eval(`return state.selectMode;`)) === false);

  const entryHint = await s.eval(`
    return document.querySelector('.review-bar-hint').textContent.trim();
  `);
  r.check('hint tells you to click a card', /click/i.test(entryHint) && /triage/i.test(entryHint),
    entryHint);

  const opened = await s.eval(`
    document.querySelector('.photo-card').click();
    return {
      lightbox: getComputedStyle(document.getElementById('lightboxModal')).display,
      triage: getComputedStyle(document.getElementById('triageBlock')).display,
      index: state.lightboxIndex,
      selected: state.selectedPaths.size
    };
  `);
  await sleep(400);
  r.check('clicking a card opens the lightbox', opened.lightbox === 'flex', opened.lightbox);
  r.check('triage panel comes with it', opened.triage === 'flex', opened.triage);
  r.check('and the click selected nothing', opened.selected === 0, String(opened.selected));

  // handleTriageKey() bails unless lightboxIndex >= 0, so the click has to set
  // it for the advertised K/R/X to exist at all. Assert the precondition, not
  // the keys — firing one here would pin a real verdict and skew the counts the
  // card-rendering section below depends on. The triage section covers the keys.
  r.check('the click arms the triage keys', opened.index >= 0, String(opened.index));

  await s.eval(`closeLightbox(); return true;`);
  await sleep(300);

  // ── select mode is opt-in and reversible ──────────────────────────
  r.section('select mode is opt-in and reversible');

  const toggleExists = await s.eval(`
    const b = document.getElementById('reviewSelectToggleBtn');
    return b ? { present: true, active: b.classList.contains('active') } : { present: false };
  `);
  r.check('review bar offers a select toggle', toggleExists.present === true,
    JSON.stringify(toggleExists));
  r.check('toggle starts inactive', toggleExists.active === false, JSON.stringify(toggleExists));

  await s.eval(`document.getElementById('reviewSelectToggleBtn').click(); return true;`);
  await sleep(300);
  const turnedOn = await s.eval(`
    return {
      selectMode: state.selectMode,
      active: document.getElementById('reviewSelectToggleBtn').classList.contains('active'),
      hint: document.querySelector('.review-bar-hint').textContent.trim(),
      checkboxes: document.querySelectorAll('.card-select-cb').length
    };
  `);
  r.check('toggle turns select mode on', turnedOn.selectMode === true, JSON.stringify(turnedOn));
  r.check('toggle reads as active', turnedOn.active === true, String(turnedOn.active));
  r.check('cards grow checkboxes', turnedOn.checkboxes > 0, String(turnedOn.checkboxes));
  r.check('hint switches to the selecting story', /select/i.test(turnedOn.hint), turnedOn.hint);

  const clickSelects = await s.eval(`
    document.querySelector('.photo-card').click();
    return {
      selected: state.selectedPaths.size,
      lightbox: getComputedStyle(document.getElementById('lightboxModal')).display
    };
  `);
  r.check('a click now selects instead of opening', clickSelects.selected === 1,
    String(clickSelects.selected));
  r.check('and the lightbox stays shut', clickSelects.lightbox === 'none', clickSelects.lightbox);

  const clearBtn = await s.eval(`
    const b = document.getElementById('reviewClearBtn');
    return b ? getComputedStyle(b).display : '(missing)';
  `);
  r.check('a Clear control appears with a selection', clearBtn !== 'none' && clearBtn !== '(missing)',
    clearBtn);

  // The reported bug in one assertion: the same button must let you back out.
  await s.eval(`document.getElementById('reviewSelectToggleBtn').click(); return true;`);
  await sleep(300);
  const turnedOff = await s.eval(`
    return {
      selectMode: state.selectMode,
      active: document.getElementById('reviewSelectToggleBtn').classList.contains('active'),
      selected: state.selectedPaths.size,
      checkboxes: document.querySelectorAll('.card-select-cb').length,
      reviewMode: state.reviewMode
    };
  `);
  r.check('clicking the toggle again leaves select mode', turnedOff.selectMode === false,
    JSON.stringify(turnedOff));
  r.check('toggle reads as inactive again', turnedOff.active === false, String(turnedOff.active));
  r.check('selection is dropped on the way out', turnedOff.selected === 0, String(turnedOff.selected));
  r.check('checkboxes go away', turnedOff.checkboxes === 0, String(turnedOff.checkboxes));
  r.check('leaving select mode does NOT leave review mode', turnedOff.reviewMode === true,
    String(turnedOff.reviewMode));

  const reopened = await s.eval(`
    document.querySelector('.photo-card').click();
    return getComputedStyle(document.getElementById('lightboxModal')).display;
  `);
  r.check('clicking a card opens triage again', reopened === 'flex', reopened);
  await s.eval(`closeLightbox(); return true;`);
  await sleep(300);

  // ── card rendering ─────────────────────────────────────────────────
  r.section('card rendering');

  const cards = await s.eval(`
    const els = [...document.querySelectorAll('.photo-card')];
    return {
      total: els.length,
      rejectTinted: els.filter((e) => e.classList.contains('verdict-reject')).length,
      pills: document.querySelectorAll('.verdict-pill').length,
      loud: [...document.querySelectorAll('.verdict-pill')].filter((p) => !p.classList.contains('quiet')).length
    };
  `);
  r.check('every returned card is a reject', cards.total > 0 && cards.rejectTinted === cards.total,
    `${cards.rejectTinted}/${cards.total}`);
  r.check('every card carries a verdict pill', cards.pills === cards.total,
    `${cards.pills}/${cards.total}`);
  r.check('pills are loud inside review mode', cards.loud === cards.pills,
    `${cards.loud}/${cards.pills}`);

  const escaped = await s.eval(`
    // A model-authored reason is untrusted text; assert it never becomes markup.
    const p = state.photos.find((x) => x.verdict && x.verdict.reason);
    if (!p) return 'no-reason';
    p.verdict.reason = '"><img src=x onerror=window.__xss=1>';
    renderGallery();
    return String(window.__xss || 'clean');
  `);
  r.check('reason text cannot inject markup', escaped === 'clean' || escaped === 'no-reason',
    escaped);

  // ── filter chips ───────────────────────────────────────────────────
  r.section('filter chips');

  await s.resetFetchLog();
  await s.eval(`setVerdictFilter('unusable'); return true;`);
  await sleep(600);
  const unusableLog = (await s.fetchLog()).calls.filter((u) => u.includes('/api/photos'));
  r.check('unusable chip filters to tier 0 only',
    unusableLog.some((u) => u.includes('verdict=unusable')), unusableLog[0] || '');

  const unusableCount = await s.eval(`
    return { shown: state.photos.length, allTier0: state.photos.every((p) => p.verdict && p.verdict.tier === 0) };
  `);
  r.check('unusable page holds only tier 0',
    unusableCount.shown > 0 && unusableCount.allTier0, JSON.stringify(unusableCount));

  await s.eval(`setVerdictFilter('reject'); return true;`);
  await sleep(600);

  // ── favourite guard ────────────────────────────────────────────────
  r.section('favourite guard on bulk select');

  const favPath = await s.eval(`
    const p = state.photos[0];
    p.favorite = true;
    renderGallery();
    return p.rel_path;
  `);
  await s.eval(`selectNonFavourites(); return true;`);
  await sleep(300);
  const selection = await s.eval(`
    return {
      size: state.selectedPaths.size,
      total: state.photos.length,
      favSelected: state.selectedPaths.has(${JSON.stringify(favPath)})
    };
  `);
  r.check('favourite is excluded from the sweep', selection.favSelected === false,
    JSON.stringify(selection));
  r.check('every non-favourite is selected', selection.size === selection.total - 1,
    `${selection.size} of ${selection.total - 1}`);

  const deleteBtn = await s.eval(`
    const b = document.getElementById('reviewDeleteBtn');
    return { disabled: b.disabled, label: b.textContent.trim() };
  `);
  r.check('delete button enables with a selection', deleteBtn.disabled === false,
    JSON.stringify(deleteBtn));

  await s.eval(`clearSelection(); updateReviewBar(); return true;`);

  // ── bulk keep + select-all honesty (U13/U14) ───────────────────────
  r.section('bulk keep and select-all honesty');

  const keepIdle = await s.eval(`
    const b = document.getElementById('reviewKeepBtn');
    const pile = document.getElementById('reviewSelectPileBtn');
    const count = document.getElementById('reviewBarCount').textContent.trim();
    return {
      disabled: b.disabled,
      label: b.textContent.trim(),
      pileDisplay: getComputedStyle(pile).display,
      count
    };
  `);
  r.check('keep is disabled with an empty selection', keepIdle.disabled === true,
    JSON.stringify(keepIdle));
  r.check('select-all-pile is hidden when the page is the whole pile',
    keepIdle.pileDisplay === 'none', keepIdle.pileDisplay);

  const forcedPile = await s.eval(`
    state.photoHasMore = true;
    state.photoTotal = 400;
    updateReviewBar();
    const pile = document.getElementById('reviewSelectPileBtn');
    const count = document.getElementById('reviewBarCount').textContent.trim();
    const loaded = document.getElementById('reviewSelectAllBtn').textContent.trim();
    return { pileLabel: pile.textContent.trim(), pileDisplay: getComputedStyle(pile).display, count, loaded };
  `);
  r.check('count names loaded vs total when the pile is larger',
    /loaded/.test(forcedPile.count) && forcedPile.count.includes('400'),
    forcedPile.count);
  r.check('select-all-pile offers the true total',
    forcedPile.pileDisplay !== 'none' && /Select all 400/.test(forcedPile.pileLabel),
    JSON.stringify(forcedPile));
  r.check('loaded-page sweep is labelled as loaded, not as the pile',
    /loaded/i.test(forcedPile.loaded), forcedPile.loaded);

  await s.eval(`
    state.photoHasMore = false;
    state.photoTotal = state.photos.length;
    updateReviewBar();
    return true;
  `);

  await s.resetFetchLog();
  const pileFetch = await s.eval(`
    return selectEntirePile().then(() => true);
  `);
  r.check('selectEntirePile runs', pileFetch === true, String(pileFetch));
  await sleep(600);
  const pileLog = (await s.fetchLog()).calls.filter((u) => u.includes('/api/photos'));
  r.check('select-all-pile requests ids=1',
    pileLog.some((u) => u.includes('ids=1') && u.includes('verdict=reject')),
    pileLog[0] || '');

  const onePath = await s.eval(`
    const fav = ${JSON.stringify(favPath)};
    const p = state.photos.find((x) => x.rel_path !== fav) || state.photos[0];
    clearSelection();
    state.selectedPaths.add(p.rel_path);
    setSelectMode(true);
    updateReviewBar();
    return p.rel_path;
  `);
  const keepArmed = await s.eval(`
    const b = document.getElementById('reviewKeepBtn');
    return { disabled: b.disabled, label: b.textContent.trim() };
  `);
  r.check('keep enables with a selection', keepArmed.disabled === false,
    JSON.stringify(keepArmed));

  await s.resetFetchLog();
  await s.eval(`window.confirm = () => true; return applyBulkManualVerdict('keep');`);
  await sleep(800);
  const keepLog = (await s.fetchLog()).calls;
  const keepPosts = keepLog.filter((u) => u.includes('/api/classify/verdict'));
  r.check('Keep selected POSTs the verdict endpoint', keepPosts.length >= 1,
    keepLog.join(' | '));
  const stillThere = await s.eval(`
    return state.photos.some((p) => p.rel_path === ${JSON.stringify(onePath)});
  `);
  r.check('kept item leaves the reject pile', stillThere === false, String(stillThere));

  // ── triage panel ───────────────────────────────────────────────────
  r.section('triage panel');

  await s.eval(`openLightbox(0); return true;`);
  await sleep(500);

  const triage = await s.eval(`
    return {
      visible: getComputedStyle(document.getElementById('triageBlock')).display,
      tier: document.getElementById('triageTierChip').textContent.trim(),
      reason: document.getElementById('triageReason').textContent.trim(),
      sheet: getComputedStyle(document.getElementById('triageSheetWrap')).display
    };
  `);
  r.check('triage block opens with the lightbox', triage.visible === 'flex', triage.visible);
  r.check('tier chip names the tier', /^Tier \d · \w/.test(triage.tier), triage.tier);
  r.check('reason is shown', triage.reason.length > 0, triage.reason);
  r.check('no contact sheet for a photo', triage.sheet === 'none', triage.sheet);

  await s.resetFetchLog();
  const before = await s.eval(`return state.photos[state.lightboxIndex].rel_path;`);
  await s.key('k');
  await sleep(700);

  const afterKeep = (await s.fetchLog()).calls;
  const verdictPosts = afterKeep.filter((u) => u.includes('/api/classify/verdict'));
  const refetches = afterKeep.filter((u) => u.includes('/api/photos'));
  r.check('K issues exactly one verdict POST', verdictPosts.length === 1,
    `${verdictPosts.length}: ${afterKeep.join(' | ')}`);
  r.check('K does not refetch the gallery', refetches.length === 0, refetches.join(' | '));

  const patched = await s.eval(`
    const p = state.photos.find((x) => x.rel_path === ${JSON.stringify(before)});
    const card = document.querySelector('.photo-card[data-rel-path="' + CSS.escape(${JSON.stringify(before)}) + '"]');
    return {
      manual: p && p.verdict && p.verdict.manual,
      verdict: p && p.verdict && p.verdict.verdict,
      cardClass: card ? (card.classList.contains('verdict-keep') ? 'keep' : card.className) : 'no-card',
      advanced: state.lightboxIndex
    };
  `);
  r.check('K pins the manual override to keep', patched.manual === 'keep',
    JSON.stringify(patched));
  r.check('derived verdict flips to keep', patched.verdict === 'keep', String(patched.verdict));
  r.check('card is repainted in place', patched.cardClass === 'keep', String(patched.cardClass));
  r.check('K advances to the next item', patched.advanced === 1, String(patched.advanced));

  await s.eval(`closeLightbox(); return true;`);
  await sleep(200);

  // ── leaving review mode ────────────────────────────────────────────
  r.section('leaving review mode');

  await s.resetFetchLog();
  await s.eval(`document.getElementById('reviewExitBtn').click(); return true;`);
  await sleep(700);

  const exited = await s.eval(`
    return {
      reviewMode: state.reviewMode,
      selectMode: state.selectMode,
      selected: state.selectedPaths.size,
      bar: getComputedStyle(document.getElementById('reviewBar')).display,
      bodyClass: document.body.classList.contains('review-mode')
    };
  `);
  r.check('review mode is off', exited.reviewMode === false, JSON.stringify(exited));
  r.check('select mode is cleared', exited.selectMode === false, String(exited.selectMode));
  r.check('selection is cleared', exited.selected === 0, String(exited.selected));
  r.check('review strip is hidden', exited.bar === 'none', exited.bar);
  r.check('body flag is removed', exited.bodyClass === false, String(exited.bodyClass));

  const exitLog = (await s.fetchLog()).calls.filter((u) => u.includes('/api/photos'));
  r.check('exit refetches without the verdict filter',
    exitLog.length === 1 && !exitLog[0].includes('verdict='), exitLog.join(' | '));

  const quietPills = await s.eval(`
    const pills = [...document.querySelectorAll('.verdict-pill')];
    return { n: pills.length, quiet: pills.filter((p) => p.classList.contains('quiet')).length };
  `);
  r.check('badges stay in the normal gallery but go quiet',
    quietPills.n > 0 && quietPills.quiet === quietPills.n, JSON.stringify(quietPills));

  // ── an archive-wide run finishing ──────────────────────────────────
  //
  // creator === "" is a scope, not an absent value. The completion path used to
  // fall back to the literal string 'creator', so an overnight Classify All
  // ended on "Classify done @creator" and its Review button navigated to a
  // folder of that name: always empty, and it threw away the real selection.
  // Only reachable through the poller, so it has to be driven from a browser.
  r.section('archive-wide classify finishing');

  await s.eval(`
    ${JSON.stringify(creator)} && (state.selectedCreator = ${JSON.stringify(creator)});
    state.classifyStatus = { running: true, creator: '', completed: 4, total: 4 };
    window.__realFetch = window.fetch;
    window.fetch = (url, opts) => {
      if (String(url).includes('/api/classify/status')) {
        return Promise.resolve(new Response(JSON.stringify({
          running: false, creator: '', current_creator: '',
          completed: 4, total: 4, kept: 1, rejected: 3, failed: 0,
          cancelled: false, cancel_requested: false
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
      }
      return window.__realFetch(url, opts);
    };
    return true;
  `);
  await s.eval(`pollClassifyStatus(); return true;`);
  await sleep(1200);

  const doneToast = await s.eval(`
    const t = document.querySelector('.toast');
    return {
      title: t ? (t.querySelector('.toast-title') || t).textContent.trim() : '(none)',
      action: t && t.querySelector('.toast-action-btn')
        ? t.querySelector('.toast-action-btn').textContent.trim() : '(none)'
    };
  `);
  r.check('toast names the scope, not a placeholder creator',
    doneToast.title.includes('all creators') && !doneToast.title.includes('@creator'),
    doneToast.title);
  r.check('review is offered for the whole pile', doneToast.action === 'Review 3 rejects',
    doneToast.action);

  await s.eval(`document.querySelector('.toast-action-btn').click(); return true;`);
  await sleep(1000);

  const landed = await s.eval(`
    const urls = window.__calls || [];
    return {
      reviewMode: state.reviewMode,
      selectedCreator: state.selectedCreator,
      title: document.getElementById('reviewBarTitle').textContent.trim(),
      lastPhotos: urls.filter((u) => u.includes('/api/photos')).pop() || ''
    };
  `);
  r.check('review opens archive-wide', landed.reviewMode === true
    && !landed.selectedCreator, JSON.stringify(landed));
  r.check('and says so', landed.title === 'Reviewing all creators', landed.title);

  const scoped = await s.eval(`
    const u = performance.getEntriesByType('resource').map((e) => e.name)
      .filter((n) => n.includes('/api/photos')).pop() || '';
    return u.split('?')[1] || '';
  `);
  r.check('and never invents a creator=creator filter',
    !scoped.includes('creator=creator'), scoped);

  // ── Escape unwinds one layer at a time ────────────────────────────
  //
  // Driven from the toast path on purpose: that path sets creatorPanelOpen,
  // and the creator-panel Escape branch used to `return` before the select-mode
  // branch was ever reached. So on the exact route a user takes out of a
  // finished classify, the keyboard escape hatch was dead.
  r.section('escape unwinds review mode');

  await s.eval(`window.fetch = window.__realFetch; return true;`);

  await s.eval(`
    state.creatorPanelOpen = true;
    document.getElementById('reviewSelectToggleBtn').click();
    return true;
  `);
  await sleep(300);
  await s.eval(`document.querySelector('.photo-card').click(); return true;`);
  await sleep(200);
  r.check('primed with a selection',
    (await s.eval(`return state.selectedPaths.size;`)) === 1);

  await s.key('Escape');
  const esc1 = await s.eval(`
    return { selected: state.selectedPaths.size, selectMode: state.selectMode,
             reviewMode: state.reviewMode, panel: state.creatorPanelOpen };
  `);
  r.check('Esc #1 clears the selection', esc1.selected === 0, JSON.stringify(esc1));
  r.check('Esc #1 keeps select mode', esc1.selectMode === true, JSON.stringify(esc1));
  r.check('Esc #1 does not close the creator panel first', esc1.panel === true,
    JSON.stringify(esc1));

  await s.key('Escape');
  const esc2 = await s.eval(`
    return { selectMode: state.selectMode, reviewMode: state.reviewMode };
  `);
  r.check('Esc #2 leaves select mode', esc2.selectMode === false, JSON.stringify(esc2));
  r.check('Esc #2 stays in review mode', esc2.reviewMode === true, JSON.stringify(esc2));

  await s.key('Escape');
  await sleep(500);
  const esc3 = await s.eval(`
    return { reviewMode: state.reviewMode,
             bar: getComputedStyle(document.getElementById('reviewBar')).display };
  `);
  r.check('Esc #3 leaves review mode', esc3.reviewMode === false, JSON.stringify(esc3));
  r.check('and the strip goes with it', esc3.bar === 'none', esc3.bar);

  await s.key('Escape');
  r.check('Esc #4 finally closes the creator panel',
    (await s.eval(`return state.creatorPanelOpen;`)) === false);

  await s.eval(`exitReviewMode(); return true;`);
  await sleep(500);

  // Review mode must never survive a reload — landing in a delete-oriented
  // mode from a refresh is the hostility docs/context.md calls out.
  await s.load();
  await sleep(700);
  const afterReload = await s.eval(`return { reviewMode: state.reviewMode, selectMode: state.selectMode };`);
  r.check('review mode does not persist across reload',
    afterReload.reviewMode === false && afterReload.selectMode === false,
    JSON.stringify(afterReload));

  r.finish(s);
  process.exit(process.exitCode || 0);
})().catch((e) => {
  console.error('HARNESS ERROR:', e.message);
  process.exit(2);
});
