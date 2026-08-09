/**
 * Source pills — see docs/design_source_filter.md.
 *
 * Fixture (tests/ui/seed_sources.py):
 *   test_creator  12 instagram
 *   kaya__x        3 x
 *   mira           2 instagram + 4 x   <- the merged-folder case
 *
 * The interesting assertions are all about that merged folder: it must appear
 * under both pills, report only the matching half, and keep a complete
 * `sources` map so the sidebar can still mark it multi-source while a filter
 * is active. Folder-name parsing would get every one of those wrong.
 */

const { Session, Report, sleep } = require('./cdp');

const readSidebar = `
  return {
    filter: state.sourceFilter,
    creators: state.creators.map(c => ({
      name: c.name, count: c.photo_count, sources: c.sources,
    })),
    rows: [...document.querySelectorAll('.creator-item[data-creator]')]
            .map(el => el.dataset.creator),
    pills: [...document.querySelectorAll('.source-pill')].map(p => ({
      source: p.dataset.source,
      label: p.textContent.trim(),
      active: p.classList.contains('active'),
    })),
    marks: [...document.querySelectorAll('.creator-item[data-creator]')]
             .filter(el => el.querySelector('.creator-source-mark'))
             .map(el => el.dataset.creator),
    galleryTotal: state.photoTotal,
  };
`;

const clickPill = (source) => `
  const pill = document.querySelector('.source-pill[data-source="${source}"]');
  if (!pill) return { clicked: false };
  pill.click();
  await new Promise(r => setTimeout(r, 900));
  return { clicked: true };
`;

(async () => {
  const s = new Session();
  const r = new Report('source filter');
  await s.connect();
  await s.load();

  // ── pills render ───────────────────────────────────────────────────
  r.section('pills are built from the registry, with real counts');
  const initial = await s.eval(readSidebar);
  console.log('   ', JSON.stringify(initial.pills));

  const pillSources = initial.pills.map((p) => p.source);
  r.check('an "All" pill exists', pillSources.includes(''), pillSources.join(','));
  r.check('instagram pill offered', pillSources.includes('instagram'));
  r.check('x pill offered', pillSources.includes('x'));
  r.check(
    'reddit pill hidden — no reddit media in the archive',
    !pillSources.includes('reddit'),
    pillSources.join(',')
  );
  r.check(
    '"All" is active by default',
    initial.pills.find((p) => p.source === '')?.active === true
  );

  // Counts are derived from the fixture rather than hardcoded: earlier suites
  // delete photos from test_creator, so only the folders this seed owns
  // (kaya__x, mira) have counts that are safe to assert literally.
  const sumSource = (creators, src) =>
    creators.reduce((n, c) => n + Number((c.sources || {})[src] || 0), 0);
  const igTotal = sumSource(initial.creators, 'instagram');
  const xTotal = sumSource(initial.creators, 'x');

  const allPill = initial.pills.find((p) => p.source === '');
  r.check(
    'All pill counts every source',
    Number(allPill.label.replace(/\D/g, '')) === igTotal + xTotal,
    `${allPill.label} vs ${igTotal}+${xTotal}`
  );
  const xPill = initial.pills.find((p) => p.source === 'x');
  r.check('x pill counts 3 (kaya__x) + 4 (mira) = 7', xTotal === 7, String(xTotal));

  r.section('unfiltered sidebar shows every creator with total counts');
  const mira = initial.creators.find((c) => c.name === 'mira');
  r.check('mira total is 6', mira && mira.count === 6, JSON.stringify(mira));
  r.check(
    'mira sources map has both platforms',
    mira && mira.sources.instagram === 2 && mira.sources.x === 4,
    JSON.stringify(mira && mira.sources)
  );
  r.check('multi-source marker only on mira', initial.marks.join(',') === 'mira',
    initial.marks.join(','));

  // ── filtering ──────────────────────────────────────────────────────
  r.section('picking X cross-filters the sidebar and the gallery');
  await s.eval(clickPill('x'));
  const xView = await s.eval(readSidebar);
  console.log('   ', JSON.stringify(xView.creators));

  r.check('state records the filter', xView.filter === 'x', xView.filter);
  r.check(
    'instagram-only creator drops out',
    !xView.rows.includes('test_creator'),
    xView.rows.join(',')
  );
  r.check('x-only creator stays', xView.rows.includes('kaya__x'), xView.rows.join(','));
  r.check('merged folder stays', xView.rows.includes('mira'), xView.rows.join(','));

  const miraX = xView.creators.find((c) => c.name === 'mira');
  r.check('merged folder reports only its X half (4)', miraX && miraX.count === 4,
    JSON.stringify(miraX));
  r.check(
    'sources map is NOT narrowed by the filter',
    miraX && miraX.sources.instagram === 2 && miraX.sources.x === 4,
    JSON.stringify(miraX && miraX.sources)
  );
  r.check('multi-source marker survives filtering', xView.marks.includes('mira'),
    xView.marks.join(','));
  r.check('gallery total is X-only (3 + 4 = 7)', xView.galleryTotal === xTotal,
    `${xView.galleryTotal} vs ${xTotal}`);

  r.section('picking Instagram shows the other half of the merged folder');
  await s.eval(clickPill('instagram'));
  const igView = await s.eval(readSidebar);
  const miraIg = igView.creators.find((c) => c.name === 'mira');
  r.check('merged folder appears under instagram too', Boolean(miraIg));
  r.check('and reports its IG half (2)', miraIg && miraIg.count === 2,
    JSON.stringify(miraIg));
  r.check('x-only creator drops out', !igView.rows.includes('kaya__x'),
    igView.rows.join(','));
  r.check('gallery total matches the sidebar IG sum', igView.galleryTotal === igTotal,
    `${igView.galleryTotal} vs ${igTotal}`);

  // ── creator + source interaction ───────────────────────────────────
  r.section('a selected creator with nothing in the new source is cleared');
  const cleared = await s.eval(`
    // Select the instagram-only creator, then switch to X.
    const row = document.querySelector('.creator-item[data-creator="test_creator"]');
    row.click();
    await new Promise(r => setTimeout(r, 700));
    const selectedBefore = state.selectedCreator;
    document.querySelector('.source-pill[data-source="x"]').click();
    await new Promise(r => setTimeout(r, 900));
    return { selectedBefore, selectedAfter: state.selectedCreator,
             title: elements.galleryTitle.textContent, total: state.photoTotal };
  `);
  console.log('   ', JSON.stringify(cleared));
  r.check('creator was selected first', cleared.selectedBefore === 'test_creator');
  r.check('selection cleared on switch', cleared.selectedAfter === null,
    String(cleared.selectedAfter));
  r.check('title falls back to All Photos', /All Photos/.test(cleared.title),
    cleared.title);
  r.check('gallery is not left empty', cleared.total === xTotal,
    `${cleared.total} vs ${xTotal}`);

  r.section('a creator present in both sources keeps its selection');
  const kept = await s.eval(`
    document.querySelector('.source-pill[data-source=""]').click();
    await new Promise(r => setTimeout(r, 900));
    document.querySelector('.creator-item[data-creator="mira"]').click();
    await new Promise(r => setTimeout(r, 700));
    document.querySelector('.source-pill[data-source="x"]').click();
    await new Promise(r => setTimeout(r, 900));
    return { selected: state.selectedCreator, total: state.photoTotal };
  `);
  console.log('   ', JSON.stringify(kept));
  r.check('mira stays selected across the switch', kept.selected === 'mira',
    String(kept.selected));
  r.check('creator + source are ANDed (4)', kept.total === 4, String(kept.total));

  // ── persistence ────────────────────────────────────────────────────
  r.section('the choice is a view pref and survives a reload');
  await s.eval(`
    document.querySelector('.source-pill[data-source="x"]').click();
    await new Promise(r => setTimeout(r, 600));
    return true;
  `);
  await s.load();
  await sleep(600);
  const reloaded = await s.eval(readSidebar);
  r.check('sourceFilter restored', reloaded.filter === 'x', reloaded.filter);
  r.check(
    'x pill is active after reload',
    reloaded.pills.find((p) => p.source === 'x')?.active === true
  );
  r.check(
    'selected creator NOT restored (navigation never is)',
    await s.eval('return state.selectedCreator === null;')
  );

  // ── error handling ─────────────────────────────────────────────────
  r.section('an active filter is always escapable, even with no registry');
  const stranded = await s.eval(`
    // What a failed /api/sources leaves behind, with a filter restored from a
    // previous session: without a fallback pill the user has an active filter,
    // an empty sidebar, and no control to clear it.
    state.sourceFilter = 'x'; saveViewPrefs();
    state.knownSources = [];
    await fetchCreators();
    await new Promise(r => setTimeout(r, 400));
    const pills = [...document.querySelectorAll('.source-pill')];
    return { filter: state.sourceFilter, sources: pills.map(p => p.dataset.source) };
  `);
  console.log('   ', JSON.stringify(stranded));
  r.check('an "All" pill is still offered', stranded.sources.includes(''),
    stranded.sources.join(','));
  r.check('the active filter still has a pill', stranded.sources.includes('x'),
    stranded.sources.join(','));

  const escaped = await s.eval(`
    document.querySelector('.source-pill[data-source=""]').click();
    await new Promise(r => setTimeout(r, 700));
    return { filter: state.sourceFilter, creators: state.creators.length };
  `);
  r.check('clicking All clears the stranded filter', escaped.filter === '',
    escaped.filter);
  r.check('and the sidebar comes back', escaped.creators === 3,
    String(escaped.creators));

  await s.eval('return fetchKnownSources();');

  r.section('a stale pref naming an unregistered source self-heals');
  const healed = await s.eval(`
    state.sourceFilter = 'myspace';
    saveViewPrefs();
    await fetchCreators();
    await new Promise(r => setTimeout(r, 400));
    return { filter: state.sourceFilter, creators: state.creators.length };
  `);
  console.log('   ', JSON.stringify(healed));
  r.check('unknown source cleared rather than stranding the sidebar',
    healed.filter === '', healed.filter);
  r.check('creators reloaded', healed.creators === 3, String(healed.creators));

  // ── cross-effect on an archive-wide action ─────────────────────────
  //
  // "Classify All" starts an archive-wide job whatever is filtered, but it used
  // to count from state.creators, which /api/creators has already narrowed. Pick
  // a platform whose backlog happens to be clear and the button disabled itself
  // saying "every creator is already classified" while another platform's was
  // untouched — a false statement blocking a valid action. The count has to come
  // from /api/stats, which is never scoped.
  r.section('Classify All stays archive-wide under a source filter');

  const unfiltered = await s.eval(`
    state.sourceFilter = '';
    state.ollamaOnline = true;
    state.classifyStatus = { running: false };
    await fetchStats();
    await fetchCreators();
    const b = document.getElementById('classifyAllBtn');
    return { total: state.archiveUnclassified, label: b.textContent.trim(), disabled: b.disabled };
  `);
  r.check('button counts the whole archive', unfiltered.total > 0
    && unfiltered.label.includes(String(unfiltered.total)), JSON.stringify(unfiltered));

  const filtered = await s.eval(`
    setSourceFilter('x');
    await new Promise(r => setTimeout(r, 600));
    const sidebarSum = state.creators.reduce(
      (n, c) => n + (Number(c.unclassified_count) || 0), 0);
    const b = document.getElementById('classifyAllBtn');
    return { sidebarSum, total: state.archiveUnclassified,
             label: b.textContent.trim(), disabled: b.disabled, title: b.title };
  `);
  r.check('the sidebar sum really is narrower (else this proves nothing)',
    filtered.sidebarSum < unfiltered.total, JSON.stringify(filtered));
  r.check('but the button still reports the archive total',
    filtered.total === unfiltered.total && filtered.label.includes(String(unfiltered.total)),
    JSON.stringify(filtered));
  r.check('and stays enabled', filtered.disabled === false, filtered.title);

  // Reset so the next suite starts unfiltered.
  await s.eval(`setSourceFilter(''); state.sourceFilter = ''; saveViewPrefs(); return true;`);

  r.finish(s) || process.exit(1);
  process.exit(0);
})().catch((err) => {
  console.error('SUITE ERROR:', err);
  process.exit(1);
});
