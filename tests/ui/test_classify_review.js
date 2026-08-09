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

  await s.eval(`window.fetch = window.__realFetch; exitReviewMode(); return true;`);
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
