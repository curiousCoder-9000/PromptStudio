/**
 * Phase 15 — activity, duplicates, trash grid, saved views, facets, For You.
 */
const { Session, Report, sleep } = require('./cdp');

(async () => {
  const s = new Session();
  const r = new Report('phase 15');
  await s.connect();
  await s.load();
  await sleep(600);

  const wired = await s.eval(`
    return {
      activity: Boolean(document.getElementById('activityBtn')),
      activityModal: Boolean(document.getElementById('activityModal')),
      duplicates: Boolean(document.getElementById('duplicatesBtn')),
      duplicatesModal: Boolean(document.getElementById('duplicatesModal')),
      trashGrid: Boolean(document.getElementById('trashList') && document.getElementById('trashList').classList.contains('trash-grid')),
      foryou: Boolean(document.querySelector('#sortSelect option[value="foryou"]')),
      semantic: Boolean(document.getElementById('semanticSearchBtn')),
      saveView: Boolean(document.getElementById('saveViewBtn')),
      boards: Boolean(document.getElementById('newCollectionBtn')),
      facets: Boolean(document.getElementById('facetChipRow')),
      train: Boolean(document.getElementById('tasteTrainBtn')),
      openActivity: typeof openActivityModal === 'function',
      openDups: typeof openDuplicatesModal === 'function',
      saveViewFn: typeof saveCurrentView === 'function',
    };
  `);
  r.check('Activity button exists', wired.activity === true);
  r.check('activity modal exists', wired.activityModal === true);
  r.check('Duplicates button exists', wired.duplicates === true);
  r.check('duplicates modal exists', wired.duplicatesModal === true);
  r.check('trash list is a grid', wired.trashGrid === true);
  r.check('For You sort option exists', wired.foryou === true);
  r.check('Semantic search chip exists', wired.semantic === true);
  r.check('save-view button exists', wired.saveView === true);
  r.check('new board button exists', wired.boards === true);
  r.check('facet chip row exists', wired.facets === true);
  r.check('Train For You exists', wired.train === true);
  r.check('openActivityModal is wired', wired.openActivity === true);
  r.check('openDuplicatesModal is wired', wired.openDups === true);
  r.check('saveCurrentView is wired', wired.saveViewFn === true);

  r.section('activity modal');
  const activity = await s.eval(`
    openActivityModal();
    await new Promise((res) => setTimeout(res, 400));
    const modal = document.getElementById('activityModal');
    const display = getComputedStyle(modal).display;
    closeActivityModal();
    return { display };
  `);
  r.check('activity modal opens', activity.display !== 'none', activity.display);

  r.section('duplicates modal');
  const dups = await s.eval(`
    openDuplicatesModal();
    await new Promise((res) => setTimeout(res, 400));
    const modal = document.getElementById('duplicatesModal');
    const display = getComputedStyle(modal).display;
    closeDuplicatesModal();
    return { display };
  `);
  r.check('duplicates modal opens', dups.display !== 'none', dups.display);

  r.section('For You sort + semantic search');
  const sort = await s.eval(`
    const seen = [];
    const realFetch = window.fetch;
    window.fetch = (url, opts) => { seen.push(String(url)); return realFetch(url, opts); };
    try {
      state.sortMode = 'foryou';
      document.getElementById('sortSelect').value = 'foryou';
      fetchPhotos();
      await new Promise((res) => setTimeout(res, 400));
      state.searchQuery = 'studio';
      state.searchMode = 'semantic';
      fetchPhotos();
      await new Promise((res) => setTimeout(res, 400));
    } finally {
      window.fetch = realFetch;
      state.searchQuery = '';
      state.searchMode = 'text';
      state.sortMode = 'name';
    }
    return {
      foryou: seen.filter((u) => u.includes('/api/photos') && u.includes('sort=foryou')).length,
      semantic: seen.filter((u) => u.includes('mode=semantic')).length,
    };
  `);
  r.check('gallery fetch sends sort=foryou', sort.foryou >= 1, String(sort.foryou));
  r.check('gallery fetch sends mode=semantic', sort.semantic >= 1, String(sort.semantic));

  r.section('trash modal stays on screen');
  const trashLayout = await s.eval(`
    const list = document.getElementById('trashList');
    const emptyBtn = document.getElementById('trashEmptyBtn');
    const card = document.querySelector('#trashModal .modal-card');
    openTrashModal();
    await new Promise((res) => setTimeout(res, 400));
    for (let i = 0; i < 36; i++) {
      const filler = document.createElement('div');
      filler.className = 'trash-card trash-row';
      filler.style.minHeight = '160px';
      list.appendChild(filler);
    }
    const btnRect = emptyBtn.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    const overflowY = getComputedStyle(list).overflowY;
    const layout = {
      overflowY,
      listScrolls: list.scrollHeight > list.clientHeight + 8,
      emptyVisible: btnRect.bottom <= window.innerHeight - 4 && btnRect.top >= 4,
      cardFits: cardRect.height <= window.innerHeight - 8,
      emptyDisplay: getComputedStyle(emptyBtn).display,
    };
    closeTrashModal();
    return layout;
  `);
  r.check('trash grid is a scroll container',
    trashLayout.overflowY === 'auto' || trashLayout.overflowY === 'scroll',
    trashLayout.overflowY);
  r.check('a full trash list scrolls inside the grid', trashLayout.listScrolls === true);
  r.check('Empty Trash stays in the viewport', trashLayout.emptyVisible === true,
    JSON.stringify({ top: trashLayout.emptyVisible, display: trashLayout.emptyDisplay }));
  r.check('trash card does not exceed the viewport', trashLayout.cardFits === true);

  r.section('saved views capture current filters');
  const view = await s.eval(`
    const payload = currentViewFilters();
    return {
      hasSort: typeof payload.sortMode === 'string',
      hasSearchMode: payload.searchMode === 'text' || payload.searchMode === 'semantic',
    };
  `);
  r.check('currentViewFilters includes sort', view.hasSort === true);
  r.check('currentViewFilters includes searchMode', view.hasSearchMode === true);

  await sleep(200);
  r.finish(s) || process.exit(1);
  process.exit(0);
})().catch((err) => {
  console.error('SUITE ERROR:', err);
  process.exit(1);
});
