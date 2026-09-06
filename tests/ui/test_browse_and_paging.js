/**
 * Stage 2: verdict as a browse axis, and the paging/render changes.
 *
 * Worth asserting in a browser and nowhere else:
 *
 * - The verdict filter reaches the *server* outside review mode. The verdict
 *   is derived from a tier against a server-side threshold, so a client that
 *   guessed would drift the moment the cut moved.
 * - Review mode and the browse filter do not both drive the grid at once. Two
 *   verdict controls disagreeing on screen is worse than either.
 * - The sentinel still pages in. It moved from a scroll listener to an
 *   IntersectionObserver; a broken observer fails silently as "the gallery
 *   just stops loading", which no python test can see.
 * - `content-visibility` is actually applied. It is the whole render win, it
 *   is one CSS declaration, and nothing else would notice it disappearing.
 */
const { Session, Report, sleep } = require('./cdp');

(async () => {
  const s = new Session();
  const r = new Report('browse axes + paging');
  await s.connect();
  await s.load();
  await sleep(700);

  // ── verdict as a browse filter ─────────────────────────────────────
  r.section('verdict browse filter');

  const hasControl = await s.eval(`
    const sel = document.getElementById('verdictFilterSelect');
    return sel ? [...sel.options].map((o) => o.value) : null;
  `);
  r.check('verdict filter exists in the normal view controls',
    Array.isArray(hasControl) && hasControl.includes('reject'),
    JSON.stringify(hasControl));
  r.check('keep is split into t1/t2 — reject is t0',
    Array.isArray(hasControl)
      && hasControl.includes('t1')
      && hasControl.includes('t2'),
    JSON.stringify(hasControl));
  r.check('default is "any verdict"', hasControl && hasControl[0] === '',
    String(hasControl && hasControl[0]));

  const countLabels = await s.eval(`
    const sel = document.getElementById('verdictFilterSelect');
    return [...sel.options].map((o) => ({ value: o.value, label: o.textContent }));
  `);
  r.check('every valued option carries a count',
    countLabels.filter((o) => o.value).every((o) => /· \d/.test(o.label)),
    JSON.stringify(countLabels.map((o) => o.label)));
  const withPct = countLabels.filter((o) => /· \d+%$/.test(o.label));
  r.check('% is only on a bucket that dominates this view, not a blanket',
    withPct.length < countLabels.filter((o) => o.value).length
      && withPct.every((o) => /· (6[1-9]|[7-9]\d|100)%$/.test(o.label)),
    JSON.stringify(countLabels.map((o) => o.label)));

  const tierOption = await s.eval(`
    return [...document.getElementById('sortSelect').options].map((o) => o.value);
  `);
  r.check('tier is offered as a sort', tierOption.includes('tier'), tierOption.join(','));

  await s.startRecordingFetches();
  await s.eval(`
    const sel = document.getElementById('verdictFilterSelect');
    sel.value = 'reject';
    sel.dispatchEvent(new Event('change'));
    return true;
  `);
  await sleep(700);

  let calls = (await s.fetchLog()).calls.filter((u) => u.includes('/api/photos'));
  r.check('browse filter reaches the server',
    calls.some((u) => u.includes('verdict=reject')), calls[0] || '(none)');
  r.check('it does NOT force the triage sort',
    calls.every((u) => !u.includes('sort=tier')), calls[0] || '');
  r.check('state records it as a browse filter, not review mode', await s.eval(`
    return state.browseVerdict === 'reject' && state.reviewMode === false;
  `) === true);

  const persisted = await s.eval(`
    return JSON.parse(localStorage.getItem('promptstudio.viewPrefs.v1') || '{}')
      .browseVerdict ?? null;
  `);
  r.check('browse verdict is a persisted view pref', persisted === 'reject', String(persisted));

  const loudPills = await s.eval(`
    // Filtering by verdict makes the verdict the thing being looked at, so the
    // pills stop being the hover-only quiet variant.
    state.photos = [{ rel_path: 'a/b.jpg', creator: 'a', filename: 'b.jpg',
                      verdict: { tier: 0, verdict: 'reject', reason: 'x' } }];
    renderGallery();
    const pill = document.querySelector('.verdict-pill');
    return pill ? !pill.classList.contains('quiet') : 'no-pill';
  `);
  r.check('pills are loud while filtering by verdict', loudPills === true, String(loudPills));

  await s.resetFetchLog();
  await s.eval(`
    const sel = document.getElementById('verdictFilterSelect');
    sel.value = 't2';
    sel.dispatchEvent(new Event('change'));
    return true;
  `);
  await sleep(700);
  calls = (await s.fetchLog()).calls.filter((u) => u.includes('/api/photos'));
  r.check('t2 browse filter reaches the server',
    calls.some((u) => u.includes('verdict=t2')), calls[0] || '(none)');

  // Native <select> on Windows composites a translucent background against
  // white, so lavender/yellow text on 18% purple vanished. Both active and
  // saturated states have to stay opaque with readable text.
  const contrast = await s.eval(`
    function parseRgba(s) {
      const m = String(s).match(/rgba?\\((\\d+)[, ]+(\\d+)[, ]+(\\d+)(?:[, /]+([\\d.]+))?/);
      if (!m) return null;
      return { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4] };
    }
    function lum(c) {
      const lin = [c.r, c.g, c.b].map((v) => {
        v /= 255;
        return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
      });
      return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
    }
    function ratio(fg, bg) {
      if (!fg || !bg) return 0;
      const hi = Math.max(lum(fg), lum(bg));
      const lo = Math.min(lum(fg), lum(bg));
      return (hi + 0.05) / (lo + 0.05);
    }
    const sel = document.getElementById('verdictFilterSelect');
    const active = getComputedStyle(sel);
    const activeBgStr = active.backgroundColor;
    const activeFgStr = active.color;
    const activeBg = parseRgba(activeBgStr);
    const activeFg = parseRgba(activeFgStr);
    sel.classList.add('is-saturated');
    const sat = getComputedStyle(sel);
    const satBgStr = sat.backgroundColor;
    const satFgStr = sat.color;
    const satBg = parseRgba(satBgStr);
    const satFg = parseRgba(satFgStr);
    sel.classList.remove('is-saturated');
    return {
      activeAlpha: activeBg && activeBg.a,
      satAlpha: satBg && satBg.a,
      activeRatio: ratio(activeFg, activeBg),
      satRatio: ratio(satFg, satBg),
      activeColor: activeFgStr,
      activeBg: activeBgStr,
      satColor: satFgStr,
      satBg: satBgStr
    };
  `);
  r.check('active select background is opaque (Windows native compositing)',
    contrast.activeAlpha === 1, JSON.stringify(contrast));
  r.check('saturated select background is opaque',
    contrast.satAlpha === 1, JSON.stringify(contrast));
  r.check('active label contrast is at least 4.5:1 against its own background',
    contrast.activeRatio >= 4.5, JSON.stringify(contrast));
  r.check('saturated label contrast is at least 4.5:1 against its own background',
    contrast.satRatio >= 4.5, JSON.stringify(contrast));

  await s.eval(`
    const sel = document.getElementById('verdictFilterSelect');
    sel.value = ''; sel.dispatchEvent(new Event('change'));
    return true;
  `);
  await sleep(600);

  // ── select all on a browse filter (T1/T2 piles) ─────────────────
  r.section('select all on a browse filter');

  const optionOrder = await s.eval(`
    return [...document.getElementById('verdictFilterSelect').options].map((o) => o.value);
  `);
  const t2At = optionOrder.indexOf('t2');
  const t1At = optionOrder.indexOf('t1');
  r.check('T2 is listed before T1 — that is the keep-hot pile',
    t2At > 0 && t2At < t1At,
    JSON.stringify(optionOrder));

  const barBefore = await s.eval(`
    return getComputedStyle(document.getElementById('bulkBar')).display;
  `);
  r.check('bulk bar is hidden before Select is on', barBefore === 'none', barBefore);

  const barOn = await s.eval(`
    const sel = document.getElementById('verdictFilterSelect');
    sel.value = 't2';
    sel.dispatchEvent(new Event('change'));
    setSelectMode(true);
    const bar = document.getElementById('bulkBar');
    const loaded = document.getElementById('bulkSelectLoadedBtn');
    const pile = document.getElementById('bulkSelectPileBtn');
    const del = document.getElementById('bulkDeleteBtn');
    return {
      display: getComputedStyle(bar).display,
      count: document.getElementById('bulkCount').textContent.trim(),
      loaded: loaded ? loaded.textContent.trim() : null,
      pileDisplay: pile ? getComputedStyle(pile).display : null,
      deleteDisabled: del ? del.disabled : null,
      selectMode: state.selectMode,
      browse: state.browseVerdict
    };
  `);
  await sleep(400);
  r.check('Select shows the bulk bar at 0 selected', barOn.display === 'flex',
    JSON.stringify(barOn));
  r.check('Select all is on the bar immediately',
    /select all/i.test(barOn.loaded || ''), barOn.loaded);
  r.check('Select all-of-pile is hidden when the page is the whole filter',
    barOn.pileDisplay === 'none', barOn.pileDisplay);
  r.check('Delete stays disabled until something is selected',
    barOn.deleteDisabled === true, String(barOn.deleteDisabled));

  const forced = await s.eval(`
    state.photoHasMore = true;
    state.photoTotal = 400;
    updateBulkBar();
    const pile = document.getElementById('bulkSelectPileBtn');
    const loaded = document.getElementById('bulkSelectLoadedBtn');
    return {
      pileLabel: pile.textContent.trim(),
      pileDisplay: getComputedStyle(pile).display,
      loaded: loaded.textContent.trim(),
      count: document.getElementById('bulkCount').textContent.trim()
    };
  `);
  r.check('a large T2 pile offers Select all of the true total',
    forced.pileDisplay !== 'none' && /Select all 400/.test(forced.pileLabel),
    JSON.stringify(forced));
  r.check('the loaded-page sweep is labelled as loaded, not as the pile',
    /loaded/i.test(forced.loaded), forced.loaded);

  await s.resetFetchLog();
  const piled = await s.eval(`
    return selectEntirePile().then(() => true);
  `);
  r.check('selectEntirePile runs outside review mode', piled === true, String(piled));
  await sleep(600);
  const pileLog = (await s.fetchLog()).calls.filter((u) => u.includes('/api/photos'));
  r.check('browse Select all requests ids=1 with the T2 filter',
    pileLog.some((u) => u.includes('ids=1') && u.includes('verdict=t2')),
    pileLog[0] || '');

  await s.eval(`
    state.photoHasMore = false;
    state.photoTotal = state.photos.length;
    setSelectMode(false);
    const sel = document.getElementById('verdictFilterSelect');
    sel.value = '';
    sel.dispatchEvent(new Event('change'));
    return true;
  `);
  await sleep(500);
  r.check('leaving Select hides the bulk bar', await s.eval(`
    return getComputedStyle(document.getElementById('bulkBar')).display;
  `) === 'none');

  // ── review mode replaces the view controls ─────────────────────────
  r.section('review mode vs browse filter');

  await s.eval(`enterReviewMode(null); return true;`);
  await sleep(700);

  const controlsHidden = await s.eval(`
    return getComputedStyle(document.querySelector('.view-controls')).display;
  `);
  r.check('view controls hide in review mode so only one verdict control shows',
    controlsHidden === 'none', controlsHidden);

  await s.resetFetchLog();
  await s.eval(`fetchPhotos(); return true;`);
  await sleep(700);
  calls = (await s.fetchLog()).calls.filter((u) => u.includes('/api/photos'));
  r.check('review mode still drives verdict + tier sort',
    calls.some((u) => u.includes('verdict=') && u.includes('sort=tier')),
    calls[0] || '(none)');

  await s.eval(`exitReviewMode(); return true;`);
  await sleep(600);
  r.check('exiting restores the view controls', await s.eval(`
    return getComputedStyle(document.querySelector('.view-controls')).display;
  `) !== 'none');

  // ── paging ─────────────────────────────────────────────────────────
  r.section('sentinel paging');

  const noScrollListener = await s.eval(`
    // The sentinel is the mechanism now; assert it exists and is observed.
    return Boolean(document.getElementById('galleryLoadMore')) || !state.photoHasMore;
  `);
  r.check('sentinel present while more pages remain', noScrollListener === true);

  const paged = await s.eval(`
    const before = state.photos.length;
    if (!state.photoHasMore) return { skipped: true };
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise((r) => setTimeout(r, 1400));
    return { before, after: state.photos.length };
  `);
  if (paged.skipped) {
    r.check('fixture too small to page (skipped)', true, 'needs > 60 photos');
  } else {
    r.check('scrolling to the bottom pages in more',
      paged.after > paged.before, `${paged.before} -> ${paged.after}`);
  }

  // ── render cost ────────────────────────────────────────────────────
  r.section('render cost');

  const cv = await s.eval(`
    const card = document.querySelector('.photo-card');
    if (!card) return 'no-card';
    const cs = getComputedStyle(card);
    return { cv: cs.contentVisibility, intrinsic: cs.containIntrinsicSize };
  `);
  r.check('cards opt into content-visibility',
    cv && cv.cv === 'auto', JSON.stringify(cv));
  r.check('an intrinsic size is supplied so the scrollbar cannot jump',
    cv && /\d/.test(cv.intrinsic || ''), JSON.stringify(cv));

  r.finish(s);
  process.exit(process.exitCode || 0);
})().catch((e) => {
  console.error('HARNESS ERROR:', e.message);
  process.exit(2);
});
