/**
 * C2 — carousel grouping in the gallery.
 *
 * Fixture (tests/ui/seed_carousels.py), creator `nadia`:
 *   c1  4 slides · c2 11 slides · 2 singles with no post_id  = 17 files, 4 tiles
 *
 * What is only testable in a browser:
 *
 * - The grid draws one tile per post while `state.photos` still holds every
 *   slide. Every other feature in the app (delete, favourite, triage, bulk
 *   select) indexes that array, so collapsing it instead of deriving tiles
 *   from it would break them all at once, silently.
 * - `←`/`→` walk the slides of a post *in order* before moving on — AGENTS.md
 *   rule 6. Slide 10 must not come after slide 1.
 * - The toggle is a persisted view pref, and review mode ignores it: triage
 *   adjudicates single files, so a collapsed carousel would hide rejects
 *   behind a tile.
 *
 * Paging is asserted in tests/test_post_grouping.py, where a fixture big
 * enough to page is cheap.
 */

const { Session, Report, sleep } = require('./cdp');

const CREATOR = 'nadia';

const selectCreator = `
  const row = document.querySelector('.creator-item[data-creator="${CREATOR}"]');
  if (!row) return { found: false };
  row.click();
  await new Promise(r => setTimeout(r, 900));
  return { found: true, creator: state.selectedCreator };
`;

const readGrid = `
  return {
    photos: state.photos.length,
    tiles: state.galleryTiles ? state.galleryTiles.length : null,
    cards: document.querySelectorAll('.photo-card').length,
    total: state.photoTotal,
    badges: [...document.querySelectorAll('.photo-card .group-count-badge')]
              .map(b => b.textContent.trim()),
    keys: state.photos.map(p => p.group_key || null),
  };
`;

const pressKey = (key) => `
  document.dispatchEvent(new KeyboardEvent('keydown', { key: '${key}', bubbles: true }));
  await new Promise(r => setTimeout(r, 250));
  return {
    file: elements.lightboxFilename.textContent,
    open: elements.lightboxModal.style.display === 'flex',
    counter: elements.lightboxSlideCount
      ? elements.lightboxSlideCount.textContent.trim() : null,
    counterShown: elements.lightboxSlideCount
      ? elements.lightboxSlideCount.style.display !== 'none' : null,
  };
`;

(async () => {
  const s = new Session();
  const r = new Report('post grouping');
  await s.connect();
  await s.load();
  await sleep(700);

  // ── the control ────────────────────────────────────────────────────
  r.section('toggle lives in the view controls and defaults off');

  const control = await s.eval(`
    const btn = document.getElementById('groupPostsBtn');
    if (!btn) return null;
    return {
      inControls: Boolean(btn.closest('.view-controls')),
      active: btn.classList.contains('active'),
      state: state.groupPosts,
    };
  `);
  r.check('a Group posts control exists', control !== null, JSON.stringify(control));
  r.check('it sits with the other view controls', control && control.inControls);
  r.check('off by default', control && control.active === false
    && control.state === false, JSON.stringify(control));

  const picked = await s.eval(selectCreator);
  r.check(`fixture creator @${CREATOR} is in the sidebar`, picked.found === true,
    JSON.stringify(picked));

  const flat = await s.eval(readGrid);
  console.log('    ungrouped', JSON.stringify({ ...flat, keys: undefined }));
  r.check('ungrouped, every slide is its own tile',
    flat.photos === 17 && flat.cards === 17, JSON.stringify(flat));
  r.check('and no slide-count badges are drawn', flat.badges.length === 0,
    flat.badges.join(','));

  // ── grouping on ────────────────────────────────────────────────────
  r.section('grouping collapses posts but keeps every slide in state');

  await s.startRecordingFetches();
  await s.eval(`
    document.getElementById('groupPostsBtn').click();
    await new Promise(r => setTimeout(r, 900));
    return true;
  `);

  const calls = (await s.fetchLog()).calls.filter((u) => u.includes('/api/photos'));
  r.check('the toggle reaches the server', calls.some((u) => u.includes('group=post')),
    calls[calls.length - 1] || '(none)');

  const grouped = await s.eval(readGrid);
  console.log('    grouped  ', JSON.stringify({ ...grouped, keys: undefined }));
  r.check('the grid draws one tile per post', grouped.cards === 4,
    String(grouped.cards));
  r.check('state.galleryTiles agrees with the DOM', grouped.tiles === 4,
    String(grouped.tiles));
  r.check('state.photos still holds every slide', grouped.photos === 17,
    String(grouped.photos));
  r.check('the total is now counted in posts', grouped.total === 4,
    String(grouped.total));
  r.check('slides carry a group key', grouped.keys.every(Boolean));

  r.check('badges appear only on real carousels',
    grouped.badges.length === 2, grouped.badges.join(','));
  r.check('and report the slide count', grouped.badges.sort().join(',') === '11,4',
    grouped.badges.join(','));

  // ── the lightbox walks the post ────────────────────────────────────
  r.section('arrow keys walk a post in slide order, then move on');

  const opened = await s.eval(`
    // The 11-slide post: lexicographic order would go 1, 10, 11, 2 here.
    const card = [...document.querySelectorAll('.photo-card')]
      .find(c => (c.dataset.relPath || '').includes('c2_1.jpg'));
    if (!card) return { found: false };
    card.click();
    await new Promise(r => setTimeout(r, 800));
    return {
      found: true,
      file: elements.lightboxFilename.textContent,
      open: elements.lightboxModal.style.display === 'flex',
      counter: elements.lightboxSlideCount
        ? elements.lightboxSlideCount.textContent.trim() : null,
    };
  `);
  r.check('clicking a carousel tile opens its first slide',
    opened.found && opened.open && opened.file === 'c2_1.jpg',
    JSON.stringify(opened));
  r.check('the lightbox says which slide this is', opened.counter === '1 / 11',
    String(opened.counter));

  const walked = [];
  for (let i = 0; i < 11; i += 1) {
    walked.push(await s.eval(pressKey('ArrowRight')));
  }
  const files = walked.map((w) => w.file);
  console.log('    walk', files.join(' → '));
  r.check('slides advance naturally, not lexicographically',
    files.slice(0, 10).join(',') === [2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
      .map((n) => `c2_${n}.jpg`).join(','),
    files.slice(0, 10).join(','));
  r.check('the counter tracks the walk', walked[0].counter === '2 / 11'
    && walked[9].counter === '11 / 11',
    `${walked[0].counter} .. ${walked[9].counter}`);
  r.check('past the last slide it moves on to the next post',
    !files[10].startsWith('c2_'), files[10]);

  const back = await s.eval(pressKey('ArrowLeft'));
  r.check('left comes back into the post', back.file === 'c2_11.jpg', back.file);

  const single = await s.eval(`
    closeLightbox();
    await new Promise(r => setTimeout(r, 200));
    const card = [...document.querySelectorAll('.photo-card')]
      .find(c => (c.dataset.relPath || '').includes('alone_a.jpg'));
    card.click();
    await new Promise(r => setTimeout(r, 700));
    return {
      file: elements.lightboxFilename.textContent,
      counterShown: elements.lightboxSlideCount.style.display !== 'none',
    };
  `);
  r.check('a group of one opens normally', single.file === 'alone_a.jpg', single.file);
  r.check('and shows no slide counter', single.counterShown === false,
    String(single.counterShown));

  await s.key('Escape');
  r.check('Escape still closes the lightbox', await s.eval(`
    return elements.lightboxModal.style.display !== 'flex';
  `) === true);

  // ── it is a view pref ──────────────────────────────────────────────
  r.section('the choice persists, navigation does not');

  const persisted = await s.eval(`
    return JSON.parse(localStorage.getItem('promptstudio.viewPrefs.v1') || '{}')
      .groupPosts ?? null;
  `);
  r.check('grouping is written to the view prefs', persisted === true,
    String(persisted));

  await s.load();
  await sleep(900);
  const reloaded = await s.eval(`
    return {
      state: state.groupPosts,
      active: document.getElementById('groupPostsBtn').classList.contains('active'),
      creator: state.selectedCreator,
    };
  `);
  r.check('restored on reload', reloaded.state === true && reloaded.active === true,
    JSON.stringify(reloaded));
  r.check('the selected creator is NOT restored', reloaded.creator === null,
    String(reloaded.creator));

  // ── review mode opts out ───────────────────────────────────────────
  r.section('review mode ignores grouping');

  await s.eval(selectCreator);
  // Re-patch rather than reset: the reload above threw the old patch away, and
  // resetFetchLog() on an unpatched page records nothing and asserts nothing.
  await s.startRecordingFetches();
  await s.eval(`enterReviewMode(null); return true;`);
  await sleep(900);
  const reviewCalls = (await s.fetchLog()).calls.filter((u) => u.includes('/api/photos'));
  r.check('triage asks for files, not posts',
    reviewCalls.length > 0 && reviewCalls.every((u) => !u.includes('group=post')),
    reviewCalls.join(' | ') || '(none)');

  await s.eval(`exitReviewMode(); return true;`);
  await sleep(900);
  const afterReview = await s.eval(`
    return { grouped: state.groupPosts, tiles: state.galleryTiles.length };
  `);
  r.check('leaving review restores the grouped view',
    afterReview.grouped === true && afterReview.tiles === 4,
    JSON.stringify(afterReview));

  // ── back off ───────────────────────────────────────────────────────
  r.section('turning it off restores the flat grid');

  const off = await s.eval(`
    document.getElementById('groupPostsBtn').click();
    await new Promise(r => setTimeout(r, 900));
    return { photos: state.photos.length, cards: document.querySelectorAll('.photo-card').length,
             badges: document.querySelectorAll('.group-count-badge').length,
             total: state.photoTotal };
  `);
  r.check('every slide is a tile again', off.cards === 17 && off.photos === 17,
    JSON.stringify(off));
  r.check('total is back to files', off.total === 17, String(off.total));
  r.check('badges are gone', off.badges === 0, String(off.badges));

  // Leave the app as the next suite expects it.
  await s.eval(`
    state.groupPosts = false; saveViewPrefs();
    state.selectedCreator = null;
    await fetchPhotos();
    await new Promise(r => setTimeout(r, 400));
    return true;
  `);

  r.finish(s) || process.exit(1);
  process.exit(0);
})().catch((err) => {
  console.error('SUITE ERROR:', err);
  process.exit(1);
});
