/*
 * P1 windowing — the grid mounts a window, not the whole loaded pile.
 *
 * docs/review_gallery_performance.md §5: `renderGallery({append})` never
 * unmounted a card, so a long session was 600-1,200 of them, each with overlay
 * markup and 2-3 listeners. `content-visibility: auto` skips their *paint*,
 * which is why 4.4k was survivable; it does not skip node creation.
 *
 * The interesting properties are all behavioural, which is why this is a
 * browser suite and not a unit test:
 *
 *   - the mounted set is a strict subset of what is loaded,
 *   - the scroll height still matches an un-windowed grid, or the scrollbar
 *     lies and the load-more sentinel moves,
 *   - scrolling changes *which* cards are mounted without growing the count,
 *   - and the things that index `state.photos` — clicking a card, selection,
 *     the lightbox — keep working across an unmount/remount.
 *
 * Needs more photos than one window holds; `run.sh` seeds them via
 * `seed_many.py` and runs this suite last, because those extra rows would
 * change the card counts every other suite asserts on.
 */

const { Session, Report, sleep } = require('./cdp');

(async () => {
  const s = new Session();
  const r = new Report('gallery windowing');
  await s.connect();
  await s.load();

  // One big page, so paging is not what limits the mounted count.
  const loaded = await s.eval(`
    state.selectedCreator = null;
    state.photoLimit = 200;
    await fetchPhotos();
    window.scrollTo(0, 0);
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    return {
      photos: state.photos.length,
      tiles: state.galleryTiles.length,
      mounted: document.querySelectorAll('.photo-card:not(.skeleton)').length,
      spacers: document.querySelectorAll('.gallery-spacer').length,
    };
  `);
  console.log('   ', JSON.stringify(loaded));
  r.check('a big page really loaded', loaded.photos >= 150, `${loaded.photos} photos`);
  r.check('the model keeps every photo', loaded.tiles === loaded.photos,
          `${loaded.tiles} tiles`);
  r.check('the grid has both spacers', loaded.spacers === 2, `${loaded.spacers}`);
  r.check('mounted cards are a strict subset', loaded.mounted < loaded.photos,
          `${loaded.mounted} of ${loaded.photos}`);
  r.check('and the window stays small', loaded.mounted <= 140, `${loaded.mounted}`);
  r.check('but the viewport is actually full', loaded.mounted >= 12,
          `${loaded.mounted}`);

  // ── the spacers have to carry the height the unmounted rows would ──
  r.section('scroll height matches an un-windowed grid');
  const geom = await s.eval(`
    const grid = document.getElementById('galleryGrid');
    const cs = getComputedStyle(grid);
    const tracks = cs.gridTemplateColumns.split(' ').filter(Boolean);
    const cols = tracks.length;
    const gap = parseFloat(cs.rowGap) || 0;
    const card = document.querySelector('.photo-card:not(.skeleton)');
    const cardH = card.offsetHeight;
    const rows = Math.ceil(state.galleryTiles.length / cols);
    const sentinel = document.getElementById('galleryLoadMore');
    return {
      cols, gap, cardH, rows,
      expected: rows * cardH + (rows - 1) * gap,
      actual: grid.getBoundingClientRect().height,
      sentinelBelowGrid: sentinel
        ? sentinel.getBoundingClientRect().top > card.getBoundingClientRect().top
        : null,
    };
  `);
  console.log('   ', JSON.stringify(geom));
  // The sentinel is a grid item of its own, so the grid is one row taller than
  // the pure card grid; allow for that plus sub-pixel track rounding.
  const slack = geom.cardH + geom.gap + 4;
  r.check('grid is as tall as all the rows, not just the mounted ones',
          geom.actual >= geom.expected - 4 && geom.actual <= geom.expected + slack,
          `${Math.round(geom.actual)} vs ${Math.round(geom.expected)} (+${Math.round(slack)})`);

  // ── scrolling swaps the mounted set without growing it ────────────
  r.section('scrolling recycles rather than accumulates');
  const scrolled = await s.eval(`
    // Pin paging off for this section. Scrolling to the bottom is exactly what
    // the load-more sentinel is for, and an appended page would change
    // state.photos underneath the assertions below.
    const hadMore = state.photoHasMore;
    state.photoHasMore = false;
    renderGallery();
    await new Promise(r => setTimeout(r, 200));
    const ids = () => [...document.querySelectorAll('.photo-card:not(.skeleton)')]
      .map(el => el.dataset.relPath);
    const before = ids();
    const beforeCount = before.length;
    window.scrollTo(0, document.body.scrollHeight / 2);
    await new Promise(r => setTimeout(r, 260));
    const mid = ids();
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise(r => setTimeout(r, 260));
    const bottom = ids();
    window.scrollTo(0, 0);
    await new Promise(r => setTimeout(r, 260));
    const back = ids();
    const overlap = mid.filter(p => before.includes(p)).length;
    state.photoHasMore = hadMore;
    return {
      beforeCount, midCount: mid.length, bottomCount: bottom.length,
      backCount: back.length,
      overlap,
      firstBefore: before[0], firstBack: back[0],
      lastTileMounted: bottom.includes(state.photos[state.photos.length - 1].rel_path),
      photos: state.photos.length,
    };
  `);
  console.log('   ', JSON.stringify(scrolled));
  r.check('the mounted set changed on scroll', scrolled.overlap < scrolled.beforeCount,
          `${scrolled.overlap} of ${scrolled.beforeCount} still mounted`);
  r.check('mid-scroll count did not grow', scrolled.midCount <= 140, `${scrolled.midCount}`);
  r.check('bottom count did not grow', scrolled.bottomCount <= 140, `${scrolled.bottomCount}`);
  r.check('the last tile mounts at the bottom', scrolled.lastTileMounted === true);
  r.check('scrolling back restores the first card',
          scrolled.firstBack === scrolled.firstBefore,
          `${scrolled.firstBack}`);
  r.check('the model never shrank', scrolled.photos === loaded.photos,
          `${scrolled.photos}`);

  // ── the model is still what everything else indexes ───────────────
  r.section('a card deep in the list still opens the right photo');
  const deep = await s.eval(`
    window.scrollTo(0, document.body.scrollHeight * 0.6);
    await new Promise(r => setTimeout(r, 260));
    const card = document.querySelector('.photo-card:not(.skeleton)');
    const wanted = card.dataset.relPath;
    card.click();
    await new Promise(r => setTimeout(r, 220));
    const opened = state.photos[state.lightboxIndex];
    const out = {
      wanted,
      opened: opened && opened.rel_path,
      index: state.lightboxIndex,
      matches: !!opened && opened.rel_path === wanted,
    };
    closeLightbox();
    await new Promise(r => setTimeout(r, 120));
    return out;
  `);
  console.log('   ', JSON.stringify(deep));
  r.check('lightbox opened the clicked photo, not a shifted index',
          deep.matches === true, `${deep.opened} vs ${deep.wanted}`);
  r.check('and its index is deep in the model', deep.index > 20, `${deep.index}`);

  // ── selection survives an unmount ─────────────────────────────────
  r.section('selection is model state, so it survives recycling');
  const sel = await s.eval(`
    window.scrollTo(0, 0);
    await new Promise(r => setTimeout(r, 260));
    setSelectMode(true);
    await new Promise(r => setTimeout(r, 120));
    const first = document.querySelector('.photo-card:not(.skeleton)');
    const rel = first.dataset.relPath;
    first.click();
    await new Promise(r => setTimeout(r, 120));
    const selectedAfterClick = state.selectedPaths.has(rel);
    // Scroll far enough that the card is unmounted, then come back.
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise(r => setTimeout(r, 260));
    const unmounted = !document.querySelector(
      '.photo-card[data-rel-path="' + CSS.escape(rel) + '"]'
    );
    window.scrollTo(0, 0);
    await new Promise(r => setTimeout(r, 260));
    const remounted = document.querySelector(
      '.photo-card[data-rel-path="' + CSS.escape(rel) + '"]'
    );
    const out = {
      selectedAfterClick,
      unmounted,
      stillSelectedInState: state.selectedPaths.has(rel),
      remountedShowsSelected: !!remounted && remounted.classList.contains('selected'),
      checkboxChecked: !!remounted && !!remounted.querySelector('.card-select-cb:checked'),
    };
    setSelectMode(false);
    return out;
  `);
  console.log('   ', JSON.stringify(sel));
  r.check('clicking selects in select mode', sel.selectedAfterClick === true);
  r.check('the card really did unmount', sel.unmounted === true);
  r.check('selection is kept in state, not the DOM', sel.stillSelectedInState === true);
  r.check('a remounted card renders as selected', sel.remountedShowsSelected === true);
  r.check('and its checkbox is checked', sel.checkboxChecked === true);

  // ── paging still reaches the end ──────────────────────────────────
  r.section('the sentinel still sits at the true bottom');
  const paging = await s.eval(`
    state.photoLimit = 60;
    await fetchPhotos();
    window.scrollTo(0, 0);
    await new Promise(r => setTimeout(r, 200));
    const firstPage = state.photos.length;
    const hadMore = state.photoHasMore;
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise(r => setTimeout(r, 900));
    return {
      firstPage, hadMore,
      afterScroll: state.photos.length,
      sentinelIsLastChild: document.getElementById('galleryGrid').lastElementChild
        ? document.getElementById('galleryGrid').lastElementChild.id
        : null,
    };
  `);
  console.log('   ', JSON.stringify(paging));
  r.check('a first page loaded', paging.firstPage === 60, `${paging.firstPage}`);
  r.check('more was available', paging.hadMore === true);
  r.check('scrolling to the bottom loaded another page',
          paging.afterScroll > paging.firstPage,
          `${paging.firstPage} -> ${paging.afterScroll}`);

  // ── select mode no longer redraws the whole pile ──────────────────
  r.section('select mode redraws a window, not 1,200 cards');
  const selectCost = await s.eval(`
    state.photoLimit = 200;
    await fetchPhotos();
    window.scrollTo(0, 0);
    await new Promise(r => setTimeout(r, 220));
    const before = document.querySelectorAll('.photo-card:not(.skeleton)').length;
    setSelectMode(true);
    await new Promise(r => setTimeout(r, 160));
    const during = document.querySelectorAll('.photo-card:not(.skeleton)').length;
    const boxes = document.querySelectorAll('.card-select-cb').length;
    setSelectMode(false);
    await new Promise(r => setTimeout(r, 160));
    return { before, during, boxes, photos: state.photos.length };
  `);
  console.log('   ', JSON.stringify(selectCost));
  r.check('select mode rebuilds only the window',
          selectCost.during <= 140 && selectCost.during < selectCost.photos,
          `${selectCost.during} cards for ${selectCost.photos} photos`);
  r.check('every mounted card got a checkbox',
          selectCost.boxes === selectCost.during,
          `${selectCost.boxes} boxes / ${selectCost.during} cards`);

  r.finish(s);
})();
