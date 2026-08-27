/**
 * Phase 15 — activity, duplicates, trash grid, saved views, For You.
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
  r.check('C5 facet chip row is gone', wired.facets === false);
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

  r.section('duplicates workspace layout');
  const dupGroup = {
    kind: 'phash',
    size: 2,
    keeper: 'alice/a.jpg',
    members: [
      {
        rel_path: 'alice/a.jpg', filename: 'a.jpg', creator: 'alice',
        url: '/media/alice/a.jpg', thumb_url: '/media/thumb/alice/a.jpg',
        favorite: true, file_size: 800000, keeper: true, preselected: false,
      },
      {
        rel_path: 'bob/b.jpg', filename: 'b.jpg', creator: 'bob',
        url: '/media/bob/b.jpg', thumb_url: '/media/thumb/bob/b.jpg',
        favorite: false, file_size: 400000, keeper: false, preselected: true,
      },
    ],
  };
  const dups = await s.eval(`
    const group = ${JSON.stringify(dupGroup)};
    const modal = document.getElementById('duplicatesModal');
    modal.style.display = 'flex';
    renderDuplicateGroups([group]);
    const opened = getComputedStyle(modal).display;
    const card = modal.querySelector('.modal-card');
    const body = document.getElementById('duplicatesBody');
    const sweep = document.getElementById('duplicatesSweepBtn');
    const img = modal.querySelector('.dup-preview img');
    const preview = modal.querySelector('.dup-preview');
    const favCheck = modal.querySelector('.is-favorite input[type="checkbox"]');
    const extraCheck = modal.querySelector('.dup-card:not(.is-keeper) input[type="checkbox"]');
    const keeperCheck = modal.querySelector('.is-keeper input[type="checkbox"]');
    const selectBtn = modal.querySelector('.dup-select-btn');
    const tile = modal.querySelector('.dup-card');
    const checkRect = extraCheck.getBoundingClientRect();
    const tileRect = tile.getBoundingClientRect();
    const label = document.getElementById('duplicatesSweepLabel');
    const summary = document.getElementById('duplicatesSummary');
    const cardRect = card.getBoundingClientRect();
    const imgRect = img.getBoundingClientRect();
    const sweepRect = sweep.getBoundingClientRect();
    const objectFit = getComputedStyle(img).objectFit;
    const initiallyChecked = Boolean(extraCheck && extraCheck.checked);
    const initialSweep = label ? label.textContent : '';
    const selectLabel = selectBtn ? selectBtn.textContent : '';
    selectBtn.click();
    const afterSelect = {
      extra: Boolean(extraCheck && extraCheck.checked),
      keeper: Boolean(keeperCheck && keeperCheck.checked),
      fav: Boolean(favCheck && favCheck.checked),
      sweep: label ? label.textContent : '',
      btn: selectBtn ? selectBtn.textContent : '',
    };
    selectBtn.click();
    const afterClear = Boolean(extraCheck && extraCheck.checked);
    preview.click();
    const viewer = document.getElementById('photoViewerOverlay');
    const viewerOpen = getComputedStyle(viewer).display !== 'none';
    if (typeof closePhotoViewer === 'function') closePhotoViewer();

    const many = Array.from({ length: 8 }, () => group);
    renderDuplicateGroups(many);
    const bodyScrolls = body.scrollHeight > body.clientHeight + 8;
    const bodyOverflow = getComputedStyle(body).overflowY;

    renderDuplicateGroups([{
      kind: 'phash', size: 2, keeper: 'a/x.jpg',
      members: [
        {
          rel_path: 'a/<img>.jpg', filename: '<img src=x onerror=alert(1)>',
          creator: '"><b>x', url: '', thumb_url: '', favorite: false,
          file_size: 1, keeper: true, preselected: false,
        },
        {
          rel_path: 'b/y.jpg', filename: 'y.jpg', creator: 'b',
          url: '', thumb_url: '', favorite: false,
          file_size: 2, keeper: false, preselected: true,
        },
      ],
    }]);
    const injectedImgs = modal.querySelectorAll('.dup-card img').length;
    const pathText = modal.querySelector('.dup-path').textContent;

    closeDuplicatesModal();
    return {
      opened,
      closed: getComputedStyle(modal).display === 'none',
      cardW: Math.round(cardRect.width),
      cardH: Math.round(cardRect.height),
      imgW: Math.round(imgRect.width),
      imgH: Math.round(imgRect.height),
      objectFit,
      bodyOverflow,
      bodyScrolls,
      sweepVisible: sweepRect.bottom <= window.innerHeight - 2 && sweepRect.top >= 0,
      sweepText: label ? label.textContent : '',
      summary: summary ? summary.textContent : '',
      favDisabled: Boolean(favCheck && favCheck.disabled),
      initiallyChecked,
      initialSweep,
      selectLabel,
      afterSelect,
      afterClear,
      checkOnRight: checkRect.left > tileRect.left + tileRect.width / 2,
      viewerOpen,
      injectedImgs,
      pathText,
    };
  `);
  r.check('duplicates modal opens as a flex overlay', dups.opened === 'flex', dups.opened);
  r.check('closeDuplicatesModal hides the overlay', dups.closed === true);
  r.check('duplicates card is lightbox-wide (>= 1100px on 1280)', dups.cardW >= 1100, String(dups.cardW));
  r.check('duplicates card fills most of the viewport height (>= 680px on 800)', dups.cardH >= 680, String(dups.cardH));
  r.check('comparison tile is large enough to judge (>= 280px tall)', dups.imgH >= 280, String(dups.imgH));
  r.check('comparison tile is wide enough for a 2-up (>= 280px)', dups.imgW >= 280, String(dups.imgW));
  r.check('photos use object-fit: contain (crop is the signal)', dups.objectFit === 'contain', dups.objectFit);
  r.check('duplicates body is the scroll container',
    dups.bodyOverflow === 'auto' || dups.bodyOverflow === 'scroll', dups.bodyOverflow);
  r.check('a tall group list scrolls inside the body', dups.bodyScrolls === true);
  r.check('Trash selected stays in the viewport', dups.sweepVisible === true);
  r.check('rows are not preselected on open', dups.initiallyChecked === false);
  r.check('sweep starts empty', (dups.initialSweep || '').includes('selected'), dups.initialSweep);
  r.check('row button offers Select copies', /Select 1 copy/.test(dups.selectLabel || ''), dups.selectLabel);
  r.check('Select copies queues extras only',
    dups.afterSelect && dups.afterSelect.extra === true
      && dups.afterSelect.keeper === false
      && dups.afterSelect.fav === false,
    JSON.stringify(dups.afterSelect));
  r.check('sweep label counts the queued extras', (dups.afterSelect.sweep || '').includes('1'), dups.afterSelect.sweep);
  r.check('row button flips to Clear selection', (dups.afterSelect.btn || '').includes('Clear'), dups.afterSelect.btn);
  r.check('Clear selection unchecks the extras', dups.afterClear === false);
  r.check('favourite copy cannot be queued', dups.favDisabled === true);
  r.check('trash checkbox sits on the top-right of the copy', dups.checkOnRight === true);
  r.check('clicking a copy opens the full-res viewer', dups.viewerOpen === true);
  r.check('filenames are text, not HTML', dups.injectedImgs === 2 && (dups.pathText || '').includes('<img'),
    JSON.stringify({ injectedImgs: dups.injectedImgs, pathText: dups.pathText }));

  await s.send('Emulation.setDeviceMetricsOverride', {
    width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
  });
  const dupsMobile = await s.eval(`
    const group = ${JSON.stringify(dupGroup)};
    const modal = document.getElementById('duplicatesModal');
    modal.style.display = 'flex';
    renderDuplicateGroups([group]);
    const card = modal.querySelector('.modal-card');
    const img = modal.querySelector('.dup-preview img');
    const sweep = document.getElementById('duplicatesSweepBtn');
    const cardRect = card.getBoundingClientRect();
    const imgRect = img.getBoundingClientRect();
    const sweepRect = sweep.getBoundingClientRect();
    const cols = getComputedStyle(modal.querySelector('.dup-members')).gridTemplateColumns;
    closeDuplicatesModal();
    return {
      cardW: Math.round(cardRect.width),
      imgH: Math.round(imgRect.height),
      sweepVisible: sweepRect.bottom <= window.innerHeight - 2 && sweepRect.top >= 0,
      cols,
      singleCol: (cols.match(/px/g) || []).length <= 1,
    };
  `);
  await s.send('Emulation.setDeviceMetricsOverride', {
    width: 1280, height: 800, deviceScaleFactor: 1, mobile: false,
  });
  r.check('phone: duplicates card still fills the width (>= 360px)', dupsMobile.cardW >= 360, String(dupsMobile.cardW));
  r.check('phone: comparison image stays large (>= 240px tall)', dupsMobile.imgH >= 240, String(dupsMobile.imgH));
  r.check('phone: copies stack in one column', dupsMobile.singleCol === true, dupsMobile.cols);
  r.check('phone: sweep button stays on screen', dupsMobile.sweepVisible === true);

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
