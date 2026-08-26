/**
 * Phase 10–12: job chips, persisted view preferences, skeletons, sync modes.
 *
 * The job chips replace progress-by-toast (one toast every 3–4s for the whole
 * run), so the assertions are: a chip appears with a progress bar and a cancel
 * button, and repeated polls do *not* stack up toasts.
 *
 * Batch needs Ollama, which isn't available here, so job state is driven
 * by stubbing the status endpoints — the chip rendering and the preference
 * plumbing are what's under test, not the vision pipeline.
 */

const { Session, Report, sleep } = require('./cdp');

(async () => {
  const s = new Session();
  const r = new Report('jobs / prefs / polish');
  await s.connect();
  await s.load();

  // ── job chips ──────────────────────────────────────────────────────
  r.section('batch chip renders progress instead of toasts');
  const batch = await s.eval(`
    const real = window.fetch;
    window.__stub = { running: true, total: 100, completed: 40, failed: 2,
                      cancel_requested: false, cancelled: false, pending: 58 };
    window.fetch = async (u, o) => u.toString().includes('/api/prompt/batch/status')
      ? new Response(JSON.stringify(window.__stub), { headers: { 'Content-Type': 'application/json' } })
      : real(u, o);
    document.querySelectorAll('.toast').forEach(t => t.remove());
    await pollBatchStatus();
    await new Promise(r => setTimeout(r, 200));
    const chip = document.getElementById('batchJobChip');
    return {
      visible: getComputedStyle(chip).display !== 'none',
      title: document.getElementById('batchJobChipTitle').textContent,
      sub: document.getElementById('batchJobChipSub').textContent,
      fill: document.getElementById('batchJobChipFill').style.width,
      cancelVisible: getComputedStyle(document.getElementById('batchJobChipCancel')).display !== 'none',
      toasts: document.querySelectorAll('.toast').length,
    };
  `);
  console.log('   ', JSON.stringify(batch));
  r.check('chip is visible while running', batch.visible === true);
  r.check('title names the job', /Batch analyze/.test(batch.title), batch.title);
  r.check('sub shows counts + percent', /42\/100 \(42%\)/.test(batch.sub), batch.sub);
  r.check('failures surfaced in sub', /2 failed/.test(batch.sub), batch.sub);
  r.check('progress bar reflects percent', batch.fill === '42%', batch.fill);
  r.check('cancel button offered', batch.cancelVisible === true);
  r.check('no progress toast emitted', batch.toasts === 0, `${batch.toasts} toasts`);

  r.section('repeated polls do not stack toasts (the old behaviour)');
  const repeated = await s.eval(`
    for (let i = 0; i < 5; i++) {
      window.__stub.completed += 5;
      await pollBatchStatus();
    }
    await new Promise(r => setTimeout(r, 200));
    return { toasts: document.querySelectorAll('.toast').length,
             fill: document.getElementById('batchJobChipFill').style.width };
  `);
  console.log('   ', JSON.stringify(repeated));
  r.check('still zero toasts after 5 polls', repeated.toasts === 0, `${repeated.toasts}`);
  r.check('progress advanced in place', repeated.fill === '67%', repeated.fill);

  r.section('cancel-requested state');
  const cancelling = await s.eval(`
    window.__stub.cancel_requested = true;
    await pollBatchStatus();
    await new Promise(r => setTimeout(r, 150));
    const btn = document.getElementById('batchJobChipCancel');
    return { title: document.getElementById('batchJobChipTitle').textContent,
             btnText: btn.textContent, disabled: btn.disabled,
             spinning: document.getElementById('batchJobChipIcon').classList.contains('spinning') };
  `);
  console.log('   ', JSON.stringify(cancelling));
  r.check('title shows stopping', /stopping/i.test(cancelling.title), cancelling.title);
  r.check('button becomes Stopping… and disables', /Stopping/.test(cancelling.btnText) && cancelling.disabled);
  r.check('spinner stops once cancelling', cancelling.spinning === false);

  r.section('completion hides the chip and announces once');
  const finished = await s.eval(`
    document.querySelectorAll('.toast').forEach(t => t.remove());
    window.__stub = { running: false, total: 100, completed: 95, failed: 2,
                      cancel_requested: false, cancelled: true, pending: 3 };
    await pollBatchStatus();
    await new Promise(r => setTimeout(r, 400));
    const chip = document.getElementById('batchJobChip');
    return { hidden: getComputedStyle(chip).display === 'none',
             toasts: document.querySelectorAll('.toast').length,
             text: Array.from(document.querySelectorAll('.toast')).map(t => t.textContent).join(' | ') };
  `);
  console.log('   ', JSON.stringify(finished));
  r.check('chip hidden when idle', finished.hidden === true);
  r.check('exactly one completion toast', finished.toasts === 1, `${finished.toasts}`);
  r.check('cancelled wording used', /cancelled/i.test(finished.text), finished.text);

  r.section('scrape chip shows Resume when queue is paused');
  const scrapePaused = await s.eval(`
    const real = window.__originalFetch || window.fetch;
    window.fetch = async (u, o) => {
      const url = u.toString();
      if (url.includes('/api/scrape/status')) {
        return new Response(JSON.stringify({
          paused: true,
          pause_reason: 'Rate-limit streak reached 3 (threshold 3)',
          pending: [{ username: 'model_sera', mode: 'full' }],
          history: [],
          running_job: null,
          sync: { running: false },
          stats: { completed_today: 1, downloaded_today: 10 },
        }), { headers: { 'Content-Type': 'application/json' } });
      }
      return real(u, o);
    };
    state.scrapeChipDismissed.clear();
    await pollScrapeStatus();
    await new Promise(r => setTimeout(r, 200));
    const chip = document.getElementById('scrapeJobChip');
    const resume = document.getElementById('scrapeJobChipResume');
    const cancel = document.getElementById('scrapeJobChipCancel');
    return {
      visible: getComputedStyle(chip).display !== 'none',
      pausedClass: chip.classList.contains('paused'),
      title: document.getElementById('scrapeJobChipTitle').textContent,
      sub: document.getElementById('scrapeJobChipSub').textContent,
      resumeVisible: resume && getComputedStyle(resume).display !== 'none',
      cancelVisible: cancel && getComputedStyle(cancel).display !== 'none',
      modalResumeDisabled: document.getElementById('scrapeResumeBtn').disabled,
      modalPauseDisabled: document.getElementById('scrapePauseBtn').disabled,
    };
  `);
  console.log('   ', JSON.stringify(scrapePaused));
  r.check('scrape chip visible while paused', scrapePaused.visible === true);
  r.check('paused styling applied', scrapePaused.pausedClass === true);
  r.check('title names pause reason', /Rate-limit streak/.test(scrapePaused.title), scrapePaused.title);
  r.check('sub points at Resume button', /press Resume/i.test(scrapePaused.sub), scrapePaused.sub);
  r.check('Resume button visible on chip', scrapePaused.resumeVisible === true);
  r.check('Cancel hidden when nothing is running', scrapePaused.cancelVisible === false);
  r.check('modal Resume enabled while paused', scrapePaused.modalResumeDisabled === false);
  r.check('modal Pause disabled while paused', scrapePaused.modalPauseDisabled === true);

  r.section('scrape chip Resume hidden while actively running');
  const scrapeRunning = await s.eval(`
    const real = window.__originalFetch || window.fetch;
    window.fetch = async (u, o) => {
      const url = u.toString();
      if (url.includes('/api/scrape/status')) {
        return new Response(JSON.stringify({
          paused: false,
          pause_reason: '',
          pending: [{ username: 'next_creator', mode: 'full' }],
          history: [],
          running_job: { username: 'model_sera', mode: 'full', deep: true },
          sync: { running: true, job_type: 'creator_queue', progress: 'Downloading…' },
          stats: {},
        }), { headers: { 'Content-Type': 'application/json' } });
      }
      return real(u, o);
    };
    await pollScrapeStatus();
    await new Promise(r => setTimeout(r, 200));
    const resume = document.getElementById('scrapeJobChipResume');
    const cancel = document.getElementById('scrapeJobChipCancel');
    return {
      title: document.getElementById('scrapeJobChipTitle').textContent,
      resumeVisible: resume && getComputedStyle(resume).display !== 'none',
      cancelVisible: cancel && getComputedStyle(cancel).display !== 'none',
      modalResumeDisabled: document.getElementById('scrapeResumeBtn').disabled,
    };
  `);
  console.log('   ', JSON.stringify(scrapeRunning));
  r.check('title shows running creator', /@model_sera/.test(scrapeRunning.title), scrapeRunning.title);
  r.check('Resume hidden while running', scrapeRunning.resumeVisible === false);
  r.check('Cancel shown while running', scrapeRunning.cancelVisible === true);
  r.check('modal Resume disabled while not paused', scrapeRunning.modalResumeDisabled === true);

  r.section('chips stack rather than overlap');
  const stack = await s.eval(`
    const stack = document.getElementById('jobChipStack');
    const rects = Array.from(stack.children)
      .filter(c => getComputedStyle(c).display !== 'none')
      .map(c => c.getBoundingClientRect());
    let overlap = false;
    for (let i = 0; i < rects.length; i++)
      for (let j = i + 1; j < rects.length; j++)
        if (!(rects[i].bottom <= rects[j].top || rects[j].bottom <= rects[i].top)) overlap = true;
    return { visibleChips: rects.length, overlap };
  `);
  r.check('multiple chips can be visible', stack.visibleChips >= 1, `${stack.visibleChips}`);
  r.check('no vertical overlap between chips', stack.overlap === false);

  // ── view preferences ───────────────────────────────────────────────
  r.section('view preferences persist across reload');
  await s.load();
  await s.eval(`
    localStorage.clear();
    document.getElementById('sortSelect').value = 'newest';
    document.getElementById('sortSelect').dispatchEvent(new Event('change'));
    document.getElementById('mediaTypeSelect').value = 'video';
    document.getElementById('mediaTypeSelect').dispatchEvent(new Event('change'));
    document.getElementById('favoritesFilterBtn').click();
    document.getElementById('gridLarge').click();
    await new Promise(r => setTimeout(r, 900));
    return true;
  `);
  const stored = await s.eval(`return localStorage.getItem('promptstudio.viewPrefs.v1');`);
  r.check('prefs written to localStorage', /newest/.test(stored || ''), String(stored));

  await s.load();
  const restored = await s.eval(`
    return {
      sortState: state.sortMode, sortControl: document.getElementById('sortSelect').value,
      mediaState: state.mediaType, mediaControl: document.getElementById('mediaTypeSelect').value,
      favState: state.favoritesOnly,
      favChipActive: document.getElementById('favoritesFilterBtn').classList.contains('active'),
      gridState: state.gridSize,
      gridClass: document.getElementById('galleryGrid').classList.contains('large'),
      gridBtnActive: document.getElementById('gridLarge').classList.contains('active'),
      selectMode: state.selectMode,
    };
  `);
  console.log('   ', JSON.stringify(restored));
  r.check('sort restored in state + control', restored.sortState === 'newest' && restored.sortControl === 'newest');
  r.check('media type restored', restored.mediaState === 'video' && restored.mediaControl === 'video');
  r.check('favorites filter restored + chip active', restored.favState === true && restored.favChipActive === true);
  r.check('grid size restored (was unrecoverable before)',
    restored.gridState === 'large' && restored.gridClass === true && restored.gridBtnActive === true);
  r.check('select mode NOT restored', restored.selectMode === false);

  r.section('restored prefs are in the very first request');
  const firstReq = await s.eval(`
    const entries = performance.getEntriesByType('resource')
      .map(e => e.name).filter(n => n.includes('/api/photos'));
    return { first: entries[0] || '' };
  `);
  r.check('initial fetch already carries them',
    /sort=newest/.test(firstReq.first) && /media_type=video/.test(firstReq.first),
    firstReq.first.slice(-70));

  r.section('corrupt prefs fall back to defaults');
  await s.eval(`localStorage.setItem('promptstudio.viewPrefs.v1', '{{{not json'); return true;`);
  await s.load();
  const corrupt = await s.eval(`
    return { sort: state.sortMode, media: state.mediaType, cards: document.querySelectorAll('.photo-card').length };
  `);
  r.check('bad JSON does not break boot', corrupt.sort === 'name' && corrupt.media === 'all',
    JSON.stringify(corrupt));
  r.check('gallery still renders', corrupt.cards > 0, `${corrupt.cards} cards`);

  r.section('wrong-typed prefs are rejected per field');
  await s.eval(`
    localStorage.setItem('promptstudio.viewPrefs.v1',
      JSON.stringify({ sortMode: 42, mediaType: 'photo', favoritesOnly: 'yes' }));
    return true;
  `);
  await s.load();
  const typed = await s.eval(`return { sort: state.sortMode, media: state.mediaType, fav: state.favoritesOnly };`);
  console.log('   ', JSON.stringify(typed));
  r.check('numeric sortMode ignored, default kept', typed.sort === 'name', String(typed.sort));
  r.check('valid string field still applied', typed.media === 'photo');
  r.check('truthy string coerced to boolean', typed.fav === true);

  // ── skeletons ──────────────────────────────────────────────────────
  r.section('skeletons while the first page loads');
  await s.eval(`localStorage.clear(); return true;`);
  await s.load();
  const skel = await s.eval(`
    const real = window.__originalFetch || window.fetch;
    window.fetch = (u, o) => u.toString().includes('/api/photos')
      ? new Promise(res => setTimeout(() => res(real(u, o)), 1200))
      : real(u, o);
    state.searchQuery = 'x';
    fetchPhotos();
    await new Promise(r => setTimeout(r, 300));
    const during = {
      skeletons: document.querySelectorAll('.photo-card.skeleton').length,
      busy: document.getElementById('galleryGrid').getAttribute('aria-busy'),
      emptyVisible: getComputedStyle(document.getElementById('emptyState')).display !== 'none',
    };
    await new Promise(r => setTimeout(r, 1600));
    const after = {
      skeletons: document.querySelectorAll('.photo-card.skeleton').length,
      busy: document.getElementById('galleryGrid').getAttribute('aria-busy'),
    };
    window.fetch = real;
    return { during, after };
  `);
  console.log('   ', JSON.stringify(skel));
  r.check('skeletons shown during load', skel.during.skeletons > 0, `${skel.during.skeletons}`);
  r.check('aria-busy true during load', skel.during.busy === 'true');
  r.check('empty state hidden while loading', skel.during.emptyVisible === false);
  r.check('skeletons cleared after load', skel.after.skeletons === 0);
  r.check('aria-busy false after load', skel.after.busy === 'false');

  r.section('appends do not replace real cards with skeletons');
  const appendSkel = await s.eval(`
    state.searchQuery = ''; await fetchPhotos();
    const before = document.querySelectorAll('.photo-card:not(.skeleton)').length;
    state.photoHasMore = true;
    const p = fetchPhotos({ append: true });
    await new Promise(r => setTimeout(r, 120));
    const during = document.querySelectorAll('.photo-card.skeleton').length;
    await p;
    return { before, during };
  `);
  r.check('no skeletons during append', appendSkel.during === 0, `${appendSkel.during}`);

  // ── sync mode segmented control ────────────────────────────────────
  r.section('segmented sync mode maps 1:1 to the API');
  const modes = await s.eval(`
    document.getElementById('syncInstagramBtn').click();
    await new Promise(r => setTimeout(r, 500));
    const out = {};
    out.optionCount = document.querySelectorAll('#scrapeModeGroup input[name=scrapeMode]').length;
    out.defaultMode = document.querySelector('input[name=scrapeMode]:checked').value;
    out.maxPostsHiddenForFull = getComputedStyle(document.getElementById('scrapeMaxPostsRow')).display === 'none';
    out.fullPayload = scrapeModePayload();

    document.getElementById('scrapeModeCatchUp').checked = true;
    document.getElementById('scrapeModeCatchUp').dispatchEvent(new Event('change'));
    out.catchUpPayload = scrapeModePayload();
    out.maxPostsShownForCatchUp = getComputedStyle(document.getElementById('scrapeMaxPostsRow')).display !== 'none';

    document.getElementById('scrapeModeBounded').checked = true;
    document.getElementById('scrapeModeBounded').dispatchEvent(new Event('change'));
    out.boundedPayload = scrapeModePayload();
    out.activeHighlight = document.querySelectorAll('#scrapeModeGroup .segmented-option.active').length;
    closeSyncModal();
    return out;
  `);
  console.log('   ', JSON.stringify(modes));
  r.check('exactly 3 mutually exclusive options', modes.optionCount === 3, `${modes.optionCount}`);
  r.check('defaults to Full archive', modes.defaultMode === 'full');
  r.check('max posts hidden for Full', modes.maxPostsHiddenForFull === true);
  r.check('Full → mode=full, deep=true, no ceiling',
    modes.fullPayload.mode === 'full' && modes.fullPayload.deep === true &&
    modes.fullPayload.max_posts === undefined, JSON.stringify(modes.fullPayload));
  r.check('Catch-up finally sends catch_up_only (was unreachable)',
    modes.catchUpPayload.mode === 'latest' && modes.catchUpPayload.catch_up_only === true &&
    modes.catchUpPayload.deep === false, JSON.stringify(modes.catchUpPayload));
  r.check('max posts shown for Catch-up', modes.maxPostsShownForCatchUp === true);
  r.check('Bounded → mode=bounded with ceiling',
    modes.boundedPayload.mode === 'bounded' && modes.boundedPayload.max_posts > 0,
    JSON.stringify(modes.boundedPayload));
  r.check('exactly one option highlighted', modes.activeHighlight === 1, `${modes.activeHighlight}`);

  // ── empty states (U10) ─────────────────────────────────────────────
  r.section('empty states distinguish first-run from a filter miss');
  const empty = await s.eval(`
    const snapshot = {
      photos: state.photos,
      creators: state.creators,
      archivePhotoTotal: state.archivePhotoTotal,
      selectedCreator: state.selectedCreator,
      searchQuery: state.searchQuery,
      reviewMode: state.reviewMode,
      browseVerdict: state.browseVerdict,
    };
    state.photos = [];
    state.creators = [];
    state.archivePhotoTotal = 0;
    state.selectedCreator = null;
    state.searchQuery = '';
    state.reviewMode = false;
    state.browseVerdict = '';
    updateEmptyState();
    const first = {
      title: document.getElementById('emptyStateTitle').textContent,
      form: getComputedStyle(document.getElementById('emptyScrapeForm')).display,
      clear: getComputedStyle(document.getElementById('emptyClearFiltersBtn')).display,
    };
    const parsedIg = parsePastedTarget('https://www.instagram.com/some.model/');
    const parsedX = parsePastedTarget('https://x.com/someone');
    state.creators = [{ name: 'someone', photo_count: 12 }];
    state.archivePhotoTotal = 12;
    state.searchQuery = 'zzzz-no-match';
    updateEmptyState();
    const filtered = {
      title: document.getElementById('emptyStateTitle').textContent,
      form: getComputedStyle(document.getElementById('emptyScrapeForm')).display,
      clear: getComputedStyle(document.getElementById('emptyClearFiltersBtn')).display,
    };
    state.photos = snapshot.photos;
    state.creators = snapshot.creators;
    state.archivePhotoTotal = snapshot.archivePhotoTotal;
    state.selectedCreator = snapshot.selectedCreator;
    state.searchQuery = snapshot.searchQuery;
    state.reviewMode = snapshot.reviewMode;
    state.browseVerdict = snapshot.browseVerdict;
    updateEmptyState();
    return { first, filtered, parsedIg, parsedX };
  `);
  r.check('first-run title invites adding media',
    empty.first.title === 'Your studio is empty', empty.first.title);
  r.check('first-run shows the scrape field', empty.first.form !== 'none', empty.first.form);
  r.check('first-run hides clear-filters', empty.first.clear === 'none', empty.first.clear);
  r.check('filter miss is not the first-run copy',
    empty.filtered.title === 'No matches', empty.filtered.title);
  r.check('filter miss offers a clear-filters action',
    empty.filtered.clear !== 'none', empty.filtered.clear);
  r.check('filter miss hides the scrape field', empty.filtered.form === 'none', empty.filtered.form);
  r.check('instagram URL parses to a handle',
    empty.parsedIg.source === 'instagram' && empty.parsedIg.handle === 'some.model',
    JSON.stringify(empty.parsedIg));
  r.check('x.com URL parses to the x source',
    empty.parsedX.source === 'x' && empty.parsedX.handle === 'someone',
    JSON.stringify(empty.parsedX));

  r.finish(s);
  process.exit(process.exitCode || 0);
})().catch((e) => {
  console.error('HARNESS ERROR:', e.message);
  process.exit(2);
});
