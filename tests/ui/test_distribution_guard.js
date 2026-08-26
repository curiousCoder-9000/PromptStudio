/**
 * B4 — the pass-rate badge on every verdict filter.
 *
 * The rule and its arithmetic are covered in Python (`tests/test_stats.py`,
 * `tests/test_distribution_guard.py`). What only a browser can prove:
 *
 * - The badge and the page agree. The share is computed server-side from the
 *   same predicate `/api/photos?verdict=` filters with; if the two ever drift,
 *   a chip advertises a pass rate for a filter nobody is running. Asserted by
 *   asking both endpoints from the page and comparing.
 * - It costs one round trip, not one per chip. The badge exists to be glanced
 *   at, and twelve extra requests on the init path is how it would get taken
 *   back out again.
 * - Saturation is *visible*, not just present in a payload. The whole point of
 *   B4 is that the warning is where the user already is, rather than in a
 *   panel they stopped opening.
 * - A garbage share cannot become markup, and cannot become a confident "0%".
 *   The badge takes its value from a server response; rule 7 applies, and
 *   "unknown" has to render as absent rather than as a measurement.
 */
const { Session, Report, sleep } = require('./cdp');

const SATURATED = {
  total: 100,
  warn_above: 0.6,
  counts: { keep: 5, reject: 95, unusable: 90, modest: 5, unclassified: 0, error: 0 },
  shares: { keep: 0.05, reject: 0.95, unusable: 0.9, modest: 0.05, unclassified: 0, error: 0 }
};

(async () => {
  const s = new Session();
  const r = new Report('distribution guard badges');
  await s.connect();
  await s.load();
  await sleep(800);

  // ── the facet arrives with the stats the app already fetches ───────
  r.section('facets arrive on /api/stats');

  const facets = await s.eval(`
    const f = state.verdictFacets;
    return f ? {
      total: f.total,
      warn: f.warn_above,
      counts: f.counts,
      shares: f.shares,
      keys: Object.keys(f.shares || {}).sort()
    } : null;
  `);
  r.check('stats carries verdict facets', facets !== null, JSON.stringify(facets));
  r.check('every filter has a share', facets
    && facets.keys.join(',') === 'error,keep,modest,reject,unclassified,unusable',
    facets && facets.keys.join(','));
  r.check('the guard limit is served, not hardcoded in the page',
    facets && facets.warn === 0.6, String(facets && facets.warn));
  r.check('shares are counts over the archive total', facets
    && Object.keys(facets.counts).every((k) =>
      Math.abs(facets.shares[k] - facets.counts[k] / facets.total) < 0.0005),
    JSON.stringify(facets && facets.shares));

  // ── the badge describes the filter it labels ───────────────────────
  //
  // The one assertion that catches a facet quietly measuring something else.
  r.section('badge agrees with the page');

  const agree = await s.eval(`
    const out = [];
    for (const key of ['reject', 'keep', 'unusable', 'modest', 'unclassified']) {
      const res = await fetch('/api/photos?limit=1&verdict=' + key);
      const data = await res.json();
      out.push({ key, page: data.total, facet: state.verdictFacets.counts[key] });
    }
    return out;
  `);
  agree.forEach((row) => {
    r.check(`${row.key}: badge count matches /api/photos total`,
      row.page === row.facet, `${row.facet} vs ${row.page}`);
  });

  // ── one round trip, not one per chip ───────────────────────────────
  r.section('one round trip');

  await s.startRecordingFetches();
  const painted = await s.eval(`
    renderVerdictPassRates();
    return document.querySelectorAll('.review-chip-share').length;
  `);
  await sleep(300);
  const renderCalls = (await s.fetchLog()).calls;
  r.check('there is a badge on every review chip', painted === 5, String(painted));
  r.check('painting them issues no requests at all', renderCalls.length === 0,
    renderCalls.join(' | '));

  // ── the chips show it ──────────────────────────────────────────────
  r.section('chips show the pass rate');

  await s.eval(`enterReviewMode(null); return true;`);
  await sleep(700);

  const chips = await s.eval(`
    return [...document.querySelectorAll('#reviewBarFilters .review-chip')].map((chip) => ({
      key: chip.dataset.verdict,
      count: chip.querySelector('.review-chip-n').textContent.trim(),
      share: chip.querySelector('.review-chip-share').textContent.trim(),
      hidden: chip.querySelector('.review-chip-share').hidden,
      title: chip.querySelector('.review-chip-share').title,
      warn: chip.classList.contains('is-saturated')
    }));
  `);
  r.check('every chip renders a percentage', chips.length === 5
    && chips.every((c) => !c.hidden && /^\d+%$/.test(c.share)),
    JSON.stringify(chips.map((c) => `${c.key}:${c.share}`)));
  r.check('the tooltip says what the number is a share of',
    chips.every((c) => /share|selects/i.test(c.title)), chips[0] && chips[0].title);
  r.check('the seeded fixture spreads, so nothing is flagged',
    chips.every((c) => c.warn === false),
    JSON.stringify(chips.filter((c) => c.warn).map((c) => c.key)));

  // The count is scoped to what is being reviewed; the badge is archive-wide.
  // Two different questions, and conflating them is what made the old
  // archive-wide review show zeroes on every chip.
  const stillCounts = chips.every((c) => /^\d+$/.test(c.count));
  r.check('the scoped count survives beside it', stillCounts,
    JSON.stringify(chips.map((c) => c.count)));

  // ── saturation is visible where the user already is ────────────────
  r.section('saturation is loud');

  const flagged = await s.eval(`
    state.verdictFacets = ${JSON.stringify(SATURATED)};
    renderVerdictPassRates();
    const byKey = {};
    document.querySelectorAll('#reviewBarFilters .review-chip').forEach((chip) => {
      byKey[chip.dataset.verdict] = {
        share: chip.querySelector('.review-chip-share').textContent.trim(),
        warn: chip.classList.contains('is-saturated'),
        badgeWarn: chip.querySelector('.review-chip-share').classList.contains('is-saturated'),
        title: chip.querySelector('.review-chip-share').title
      };
    });
    return byKey;
  `);
  r.check('a 95% filter is flagged', flagged.reject.warn === true && flagged.reject.badgeWarn,
    JSON.stringify(flagged.reject));
  r.check('and says 95%', flagged.reject.share === '95%', flagged.reject.share);
  r.check('the tooltip explains why it matters',
    /no-op/i.test(flagged.reject.title), flagged.reject.title);
  r.check('a 90% filter is flagged too', flagged.unusable.warn === true,
    JSON.stringify(flagged.unusable));
  r.check('filters under the limit are left alone',
    flagged.keep.warn === false && flagged.modest.warn === false,
    JSON.stringify([flagged.keep.warn, flagged.modest.warn]));

  // ── the browse dropdown carries the same number ────────────────────
  //
  // Review mode is a deliberate detour. A guard only visible once you have
  // gone looking for it is the insights panel all over again.
  r.section('browse dropdown');

  await s.eval(`exitReviewMode(); return true;`);
  await sleep(600);

  const options = await s.eval(`
    renderVerdictPassRates();
    const sel = document.getElementById('verdictFilterSelect');
    return [...sel.options].map((o) => ({ value: o.value, label: o.textContent }));
  `);
  const valued = options.filter((o) => o.value);
  r.check('verdict options carry a count, not a blanket percentage',
    valued.every((o) => /· \d/.test(o.label)),
    JSON.stringify(options.map((o) => o.label)));
  r.check('archive-wide saturation does not leak onto this view\'s labels',
    valued.every((o) => !o.label.includes('%')),
    JSON.stringify(valued.map((o) => `${o.value}:${o.label}`)));
  r.check('"any verdict" does not pretend to be a filter',
    options[0].value === '' && !options[0].label.includes('·'), options[0].label);

  const mixedDenom = await s.eval(`
    const prevCreators = state.creators;
    const prevSelected = state.selectedCreator;
    state.creators = [{
      name: 'fully_done',
      photo_count: 40,
      keep_count: 40,
      reject_count: 0,
      unusable_count: 0,
      modest_count: 0,
      unclassified_count: 0,
      error_count: 0,
      stale_count: 0
    }];
    state.selectedCreator = 'fully_done';
    renderVerdictSelectPassRates();
    const sel = document.getElementById('verdictFilterSelect');
    const byValue = Object.fromEntries(
      [...sel.options].map((o) => [o.value, o.textContent])
    );
    state.creators = prevCreators;
    state.selectedCreator = prevSelected;
    renderVerdictSelectPassRates();
    return byValue;
  `);
  r.check('a creator with nothing left to classify does not inherit the archive 62%',
    mixedDenom.unclassified === 'Not classified · 0'
      && !String(mixedDenom.unclassified).includes('%'),
    JSON.stringify(mixedDenom));

  const selWarn = await s.eval(`
    const prevCreators = state.creators;
    const prevSelected = state.selectedCreator;
    state.creators = [{
      name: 'sat',
      photo_count: 100,
      keep_count: 5,
      reject_count: 95,
      unusable_count: 90,
      modest_count: 5,
      unclassified_count: 0,
      error_count: 0,
      stale_count: 0
    }];
    state.selectedCreator = 'sat';
    renderVerdictSelectPassRates();
    const sel = document.getElementById('verdictFilterSelect');
    sel.value = 'reject';
    sel.dispatchEvent(new Event('change'));
    const out = {
      warn: sel.classList.contains('is-saturated'),
      label: sel.options[sel.selectedIndex].textContent
    };
    sel.value = '';
    sel.dispatchEvent(new Event('change'));
    state.creators = prevCreators;
    state.selectedCreator = prevSelected;
    renderVerdictSelectPassRates();
    return out;
  `);
  r.check('picking a filter that dominates THIS view marks the control',
    selWarn.warn === true, JSON.stringify(selWarn));
  r.check('that label is this view\'s count and this view\'s share',
    selWarn.label === 'Rejects · 95 · 95%', selWarn.label);

  const relabelled = await s.eval(`
    const sel = document.getElementById('verdictFilterSelect');
    const before = [...sel.options].map((o) => o.textContent);
    renderVerdictPassRates();
    renderVerdictPassRates();
    return {
      before,
      after: [...sel.options].map((o) => o.textContent)
    };
  `);
  r.check('re-rendering does not stack suffixes',
    JSON.stringify(relabelled.before) === JSON.stringify(relabelled.after),
    JSON.stringify(relabelled.after));

  await s.eval(`
    const sel = document.getElementById('verdictFilterSelect');
    sel.value = ''; sel.dispatchEvent(new Event('change'));
    return true;
  `);
  await sleep(500);

  // ── a bad payload is absent, not markup and not zero ───────────────
  r.section('hostile and missing values');

  const hostile = await s.eval(`
    window.__xss = 0;
    state.verdictFacets = {
      total: 10, warn_above: 0.6,
      counts: { reject: 1 },
      shares: { reject: '"><img src=x onerror=window.__xss=1>' }
    };
    renderVerdictPassRates();
    const badge = document.querySelector('.review-chip-share[data-share="reject"]');
    return {
      xss: String(window.__xss),
      html: badge.innerHTML,
      text: badge.textContent,
      hidden: badge.hidden
    };
  `);
  r.check('a string share cannot inject markup', hostile.xss === '0', hostile.xss);
  r.check('nor land in the DOM as markup', hostile.html === '', hostile.html);
  r.check('an unreadable share hides rather than reading 0%',
    hostile.hidden === true && hostile.text === '', JSON.stringify(hostile));

  const missing = await s.eval(`
    state.verdictFacets = null;
    renderVerdictPassRates();
    const badges = [...document.querySelectorAll('.review-chip-share')];
    const sel = document.getElementById('verdictFilterSelect');
    const labels = [...sel.options].map((o) => o.textContent);
    return {
      hidden: badges.every((b) => b.hidden && b.textContent === ''),
      flagged: [...document.querySelectorAll('.review-chip.is-saturated')].length,
      labels
    };
  `);
  r.check('before stats land, no badge claims anything', missing.hidden === true,
    JSON.stringify(missing));
  r.check('and no chip is left flagged', missing.flagged === 0, String(missing.flagged));
  r.check('the dropdown drops the percentage without stats',
    (missing.labels || []).every((l) => !String(l).includes('%')),
    JSON.stringify(missing.labels));
  r.check('but keeps the scoped count — that number still means something',
    (missing.labels || []).filter((_, i) => i > 0).every((l) => /· \d/.test(l)),
    JSON.stringify(missing.labels));

  await sleep(200);
  r.finish(s) || process.exit(1);
  process.exit(0);
})().catch((err) => {
  console.error('SUITE ERROR:', err);
  process.exit(1);
});
