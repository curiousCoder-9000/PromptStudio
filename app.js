// State Management
const state = {
    creators: [],
    photos: [],
    selectedCreator: null,
    searchQuery: '',
    creatorSearchQuery: '',
    unanalyzedOnly: false,
    favoritesOnly: false,
    mediaType: 'all',
    sortMode: 'name',
    // C2 — collapse a post's slides into one tile. state.photos stays FLAT
    // (every slide, members adjacent); galleryTiles is what the grid draws.
    // Deriving the tiles instead of collapsing the array is what keeps delete,
    // favourite, triage, bulk select and the lightbox indexing the same list
    // they always did.
    groupPosts: false,
    galleryTiles: [],
    // 'normal' | 'large' — was a bare DOM class, so it couldn't be restored
    gridSize: 'normal',
    selectMode: false,
    selectedPaths: new Set(),
    photosToDelete: null,
    // Soft delete: server moves media to _trash/ and returns a trash_id for Undo
    trashEnabled: true,
    trashCount: 0,
    ollamaOnline: null,
    comfyOnline: null,
    comfyPollTimer: null,
    // A4 registry, from /api/workflows. Which graph runs is a user choice now,
    // not something inferred from which of three buttons was pressed.
    workflows: [],
    workflowDefault: 'pro',
    compareMode: false,
    currentGenerations: [],
    // The generation currently shown in the compare pane, and its rating.
    // Deliberately not a view pref: it is derived from whatever is on screen.
    currentGenId: null,
    currentGenRating: 0,
    // Outputs gallery (A1). Its own paging state so switching views does not
    // discard the photo gallery's, and vice versa.
    outputsView: false,
    outputs: [],
    outputsOffset: 0,
    outputsTotal: 0,
    outputsHasMore: false,
    outputsRequest: null,
    outputDetail: null,
    // One batch, viewed as a contact sheet. Not a <select> like the other
    // filters: batch ids are opaque hex, so the only sensible way in is the
    // completion toast, and the only sensible way out is a clear button.
    outputsBatch: null,
    generatePollTimer: null,
    generateWasRunning: false,
    promptDirty: false,
    lightboxIndex: -1,
    currentPromptData: null,
    photoToDelete: null,
    syncPollTimer: null,
    scrapePollTimer: null,
    scrapeStatus: null,
    // Lane names whose chip the user hid. Per lane, not one flag: dismissing
    // a stuck X chip must not also hide a live Instagram job.
    scrapeChipDismissed: new Set(),
    scrapeNotifiedFinished: null,
    scrapeWasActive: false,
    batchPollTimer: null,
    // Tracks the running → stopped transition so completion is announced once
    batchWasRunning: false,
    classifyPollTimer: null,
    classifyStatus: null,
    // Review mode is deliberately NOT in PREF_FIELDS: a refresh must never drop
    // you into a delete-oriented mode. Same rule as selection and select mode.
    reviewMode: false,
    verdictFilter: 'reject',
    // Verdict as a *browse* filter, outside review mode. Unlike reviewMode this
    // IS a view pref: it filters, it never deletes, so restoring it on refresh
    // is helpful rather than hostile.
    browseVerdict: '',
    // Platform provenance filter — '' means every source. Cross-filters the
    // sidebar AND the gallery, so a merged folder shows only its X half when
    // X is picked. Comes from photos.source, never the folder-name suffix.
    sourceFilter: '',
    // [{name, label}] from /api/sources; null until the first fetch lands.
    knownSources: null,
    // Archive-wide unclassified count from /api/stats. Deliberately separate
    // from the sidebar's per-creator counters, which the source filter narrows.
    archiveUnclassified: 0,
    // {total, counts, shares, warn_above} from /api/stats — the B4 pass rate
    // of every verdict filter. Archive-wide on purpose: the badge answers
    // "does this filter still tell me anything", which is a property of the
    // classifier, not of whichever creator happens to be selected.
    verdictFacets: null,
    tierLabels: null,
    healthPollTimer: null,
    // Poller keys stopped because the tab went to the background, so only
    // those get restarted — resuming a poller that had already finished
    // would revive a chip for a job that is long done.
    pausedPollers: [],
    photoOffset: 0,
    photoLimit: 60,
    photoTotal: 0,
    photoHasMore: false,
    photosLoading: false,
    // In-flight AbortControllers — a newer request supersedes the older one
    photosRequest: null,
    creatorStyleRequest: null,
    followingRequest: null,
    // Creator sidebar action panel (Sync / Style) — independent of filter selection
    creatorPanelOpen: false,
    // Video player
    videoLoadToken: 0,
    videoSeekHudTimer: null,
    videoInFullscreenShell: false,
    fsScrubbing: false,
    fsUiWired: false,
    // Photo viewer zoom/pan state
    viewerZoom: 1,
    viewerPanX: 0,
    viewerPanY: 0,
    viewerDragging: false,
    viewerMoved: false,
    viewerDragStartX: 0,
    viewerDragStartY: 0,
    viewerLastPanX: 0,
    viewerLastPanY: 0
};

// DOM Elements
const elements = {
    statTotalPhotos: document.getElementById('statTotalPhotos'),
    statTotalVideos: document.getElementById('statTotalVideos'),
    statCreators: document.getElementById('statCreators'),
    statPersonPhotos: document.getElementById('statPersonPhotos'),
    creatorCount: document.getElementById('creatorCount'),
    creatorList: document.getElementById('creatorList'),
    galleryGrid: document.getElementById('galleryGrid'),
    galleryTitle: document.getElementById('galleryTitle'),
    galleryCount: document.getElementById('galleryCount'),
    searchInput: document.getElementById('searchInput'),
    clearSearch: document.getElementById('clearSearch'),
    emptyState: document.getElementById('emptyState'),
    refreshBtn: document.getElementById('refreshBtn'),
    gridNormal: document.getElementById('gridNormal'),
    gridLarge: document.getElementById('gridLarge'),
    newCreatorBtn: document.getElementById('newCreatorBtn'),
    uploadPhotoBtn: document.getElementById('uploadPhotoBtn'),
    // Trash (soft delete)
    trashBtn: document.getElementById('trashBtn'),
    trashCountBadge: document.getElementById('trashCountBadge'),
    trashModal: document.getElementById('trashModal'),
    trashList: document.getElementById('trashList'),
    trashEmpty: document.getElementById('trashEmpty'),
    trashSummary: document.getElementById('trashSummary'),
    trashEmptyBtn: document.getElementById('trashEmptyBtn'),
    trashPurgeExpiredBtn: document.getElementById('trashPurgeExpiredBtn'),
    closeTrashModalBtn: document.getElementById('closeTrashModalBtn'),
    // Quality insights (Phase 13 B1)
    insightsBtn: document.getElementById('insightsBtn'),
    insightsModal: document.getElementById('insightsModal'),
    insightsModalOverlay: document.getElementById('insightsModalOverlay'),
    insightsBody: document.getElementById('insightsBody'),
    closeInsightsBtn: document.getElementById('closeInsightsBtn'),
    refreshInsightsBtn: document.getElementById('refreshInsightsBtn'),
    doneInsightsBtn: document.getElementById('doneInsightsBtn'),
    // Lightbox Modal
    lightboxModal: document.getElementById('lightboxModal'),
    lightboxOverlay: document.getElementById('lightboxOverlay'),
    lightboxClose: document.getElementById('lightboxClose'),
    lightboxImg: document.getElementById('lightboxImg'),
    lightboxVideo: document.getElementById('lightboxVideo'),
    lightboxCreator: document.getElementById('lightboxCreator'),
    lightboxFilename: document.getElementById('lightboxFilename'),
    lightboxSlideCount: document.getElementById('lightboxSlideCount'),
    lightboxPrev: document.getElementById('lightboxPrev'),
    lightboxNext: document.getElementById('lightboxNext'),
    lightboxDeleteBtn: document.getElementById('lightboxDeleteBtn'),
    // Generate Prompt Section
    generatePromptSection: document.getElementById('generatePromptSection'),
    generatePromptBtn: document.getElementById('generatePromptBtn'),
    regeneratePromptBtn: document.getElementById('regeneratePromptBtn'),
    promptContent: document.getElementById('promptContent'),
    inspectorPanelTitle: document.getElementById('inspectorPanelTitle'),
    inspectorModelTag: document.getElementById('inspectorModelTag'),
    mediaDetailPanel: document.getElementById('mediaDetailPanel'),
    mediaDetailThumb: document.getElementById('mediaDetailThumb'),
    mediaDetailHandle: document.getElementById('mediaDetailHandle'),
    mediaDetailFile: document.getElementById('mediaDetailFile'),
    mediaDetailPills: document.getElementById('mediaDetailPills'),
    mediaDetailGrid: document.getElementById('mediaDetailGrid'),
    mediaDetailCaption: document.getElementById('mediaDetailCaption'),
    mediaOpenIgBtn: document.getElementById('mediaOpenIgBtn'),
    mediaExpandFromPanelBtn: document.getElementById('mediaExpandFromPanelBtn'),
    mediaCopyPathBtn: document.getElementById('mediaCopyPathBtn'),
    // Inspector Controls
    promptTagsContainer: document.getElementById('promptTagsContainer'),
    positivePromptText: document.getElementById('positivePromptText'),
    negativePromptText: document.getElementById('negativePromptText'),
    paramSampler: document.getElementById('paramSampler'),
    paramSteps: document.getElementById('paramSteps'),
    paramCFG: document.getElementById('paramCFG'),
    paramAspect: document.getElementById('paramAspect'),
    copyPositiveBtn: document.getElementById('copyPositiveBtn'),
    copyNegativeBtn: document.getElementById('copyNegativeBtn'),
    copyFullBundleBtn: document.getElementById('copyFullBundleBtn'),
    copyFluxBtn: document.getElementById('copyFluxBtn'),
    copySdxlBtn: document.getElementById('copySdxlBtn'),
    copyPonyBtn: document.getElementById('copyPonyBtn'),
    // Fullscreen Photo / Video Viewer
    photoViewerOverlay: document.getElementById('photoViewerOverlay'),
    photoViewerClose: document.getElementById('photoViewerClose'),
    photoViewerContainer: document.getElementById('photoViewerContainer'),
    photoViewerImg: document.getElementById('photoViewerImg'),
    photoViewerHint: document.getElementById('photoViewerHint'),
    videoExpandBtn: document.getElementById('videoExpandBtn'),
    lightboxMediaPane: document.getElementById('lightboxMediaPane'),
    videoBuffering: document.getElementById('videoBuffering'),
    videoSeekHud: document.getElementById('videoSeekHud'),
    fsVideoLayout: document.getElementById('fsVideoLayout'),
    fsVideoStage: document.getElementById('fsVideoStage'),
    fsVideoBar: document.getElementById('fsVideoBar'),
    fsPlayPauseBtn: document.getElementById('fsPlayPauseBtn'),
    fsPlayPauseIcon: document.getElementById('fsPlayPauseIcon'),
    fsMuteBtn: document.getElementById('fsMuteBtn'),
    fsMuteIcon: document.getElementById('fsMuteIcon'),
    fsSeekWrap: document.getElementById('fsSeekWrap'),
    fsSeekRange: document.getElementById('fsSeekRange'),
    fsSeekPlayed: document.getElementById('fsSeekPlayed'),
    fsSeekBuffered: document.getElementById('fsSeekBuffered'),
    fsSeekThumb: document.getElementById('fsSeekThumb'),
    fsTimeCurrent: document.getElementById('fsTimeCurrent'),
    fsTimeDuration: document.getElementById('fsTimeDuration'),
    // Modals
    deleteConfirmModal: document.getElementById('deleteConfirmModal'),
    deleteFilenamePreview: document.getElementById('deleteFilenamePreview'),
    cancelDeleteBtn: document.getElementById('cancelDeleteBtn'),
    confirmDeleteBtn: document.getElementById('confirmDeleteBtn'),
    newCreatorModal: document.getElementById('newCreatorModal'),
    newCreatorInput: document.getElementById('newCreatorInput'),
    cancelNewCreatorBtn: document.getElementById('cancelNewCreatorBtn'),
    confirmNewCreatorBtn: document.getElementById('confirmNewCreatorBtn'),
    uploadModal: document.getElementById('uploadModal'),
    uploadCreatorSelect: document.getElementById('uploadCreatorSelect'),
    uploadFileInput: document.getElementById('uploadFileInput'),
    cancelUploadBtn: document.getElementById('cancelUploadBtn'),
    confirmUploadBtn: document.getElementById('confirmUploadBtn'),
    syncModal: document.getElementById('syncModal'),
    syncSavedBtn: document.getElementById('syncSavedBtn'),
    syncCreatorInput: document.getElementById('syncCreatorInput'),
    syncCreatorBtn: document.getElementById('syncCreatorBtn'),
    syncFollowingBtn: document.getElementById('syncFollowingBtn'),
    syncFollowMaxAccounts: document.getElementById('syncFollowMaxAccounts'),
    syncFollowMaxPosts: document.getElementById('syncFollowMaxPosts'),
    syncFollowKeywords: document.getElementById('syncFollowKeywords'),
    syncIncludeVideos: document.getElementById('syncIncludeVideos'),
    syncStatusText: document.getElementById('syncStatusText'),
    syncProgressFill: document.getElementById('syncProgressFill'),
    syncRateMeta: document.getElementById('syncRateMeta'),
    closeSyncModalBtn: document.getElementById('closeSyncModalBtn'),
    syncInstagramBtn: document.getElementById('syncInstagramBtn'),
    scrapeCreatorInput: document.getElementById('scrapeCreatorInput'),
    scrapeSourceSelect: document.getElementById('scrapeSourceSelect'),
    scrapeSourceHint: document.getElementById('scrapeSourceHint'),
    scrapeEnqueueBtn: document.getElementById('scrapeEnqueueBtn'),
    scrapeModeGroup: document.getElementById('scrapeModeGroup'),
    scrapeMaxPosts: document.getElementById('scrapeMaxPosts'),
    scrapeMaxPostsRow: document.getElementById('scrapeMaxPostsRow'),
    syncLatestCreatorBtn: document.getElementById('syncLatestCreatorBtn'),
    scrapePauseBtn: document.getElementById('scrapePauseBtn'),
    scrapeResumeBtn: document.getElementById('scrapeResumeBtn'),
    scrapeCancelBtn: document.getElementById('scrapeCancelBtn'),
    scrapeClearPendingBtn: document.getElementById('scrapeClearPendingBtn'),
    scrapeQueueStatus: document.getElementById('scrapeQueueStatus'),
    scrapeQueueList: document.getElementById('scrapeQueueList'),
    syncOneShotBanner: document.getElementById('syncOneShotBanner'),
    // Scrape chips are built per lane at render time, so they are looked up
    // through renderScrapeLaneChips rather than cached here.
    scrapeLaneChips: document.getElementById('scrapeLaneChips'),
    scrapeLaneChipTemplate: document.getElementById('scrapeLaneChipTemplate'),
    // Batch job chip (same shape as the scrape chip)
    jobChipStack: document.getElementById('jobChipStack'),
    batchJobChip: document.getElementById('batchJobChip'),
    batchJobChipIcon: document.getElementById('batchJobChipIcon'),
    batchJobChipTitle: document.getElementById('batchJobChipTitle'),
    batchJobChipSub: document.getElementById('batchJobChipSub'),
    batchJobChipFill: document.getElementById('batchJobChipFill'),
    batchJobChipCancel: document.getElementById('batchJobChipCancel'),
    // Classify job chip — same ids-by-convention shape, so renderJobChip works
    classifyJobChip: document.getElementById('classifyJobChip'),
    classifyJobChipIcon: document.getElementById('classifyJobChipIcon'),
    classifyJobChipTitle: document.getElementById('classifyJobChipTitle'),
    classifyJobChipSub: document.getElementById('classifyJobChipSub'),
    classifyJobChipFill: document.getElementById('classifyJobChipFill'),
    classifyJobChipCancel: document.getElementById('classifyJobChipCancel'),
    // Sidebar classify section
    verdictMeter: document.getElementById('verdictMeter'),
    verdictMeterKeep: document.getElementById('verdictMeterKeep'),
    verdictMeterReject: document.getElementById('verdictMeterReject'),
    verdictMeterTodo: document.getElementById('verdictMeterTodo'),
    verdictMeterLegend: document.getElementById('verdictMeterLegend'),
    classifyCreatorBtn: document.getElementById('classifyCreatorBtn'),
    classifyAllBtn: document.getElementById('classifyAllBtn'),
    reviewRejectsBtn: document.getElementById('reviewRejectsBtn'),
    rescoreStaleBtn: document.getElementById('rescoreStaleBtn'),
    cancelClassifyBtn: document.getElementById('cancelClassifyBtn'),
    // Review mode strip
    reviewBar: document.getElementById('reviewBar'),
    reviewBarTitle: document.getElementById('reviewBarTitle'),
    reviewBarFilters: document.getElementById('reviewBarFilters'),
    reviewBarCount: document.getElementById('reviewBarCount'),
    reviewBarHint: document.getElementById('reviewBarHint'),
    reviewSelectToggleBtn: document.getElementById('reviewSelectToggleBtn'),
    reviewSelectAllBtn: document.getElementById('reviewSelectAllBtn'),
    reviewClearBtn: document.getElementById('reviewClearBtn'),
    reviewDeleteBtn: document.getElementById('reviewDeleteBtn'),
    reviewExitBtn: document.getElementById('reviewExitBtn'),
    // Triage block in the lightbox inspector
    triageBlock: document.getElementById('triageBlock'),
    triageTierChip: document.getElementById('triageTierChip'),
    triageMeta: document.getElementById('triageMeta'),
    triageReason: document.getElementById('triageReason'),
    triageSheetWrap: document.getElementById('triageSheetWrap'),
    triageSheet: document.getElementById('triageSheet'),
    triageKeepBtn: document.getElementById('triageKeepBtn'),
    triageRejectBtn: document.getElementById('triageRejectBtn'),
    triageAutoBtn: document.getElementById('triageAutoBtn'),
    batchPromptBtn: document.getElementById('batchPromptBtn'),
    unanalyzedFilterBtn: document.getElementById('unanalyzedFilterBtn'),
    groupPostsBtn: document.getElementById('groupPostsBtn'),
    favoritesFilterBtn: document.getElementById('favoritesFilterBtn'),
    sortSelect: document.getElementById('sortSelect'),
    mediaTypeSelect: document.getElementById('mediaTypeSelect'),
    verdictFilterSelect: document.getElementById('verdictFilterSelect'),
    favoritePhotoBtn: document.getElementById('favoritePhotoBtn'),
    promptHistory: document.getElementById('promptHistory'),
    promptHistoryList: document.getElementById('promptHistoryList'),
    lightboxGenImg: document.getElementById('lightboxGenImg'),
    genRating: document.getElementById('genRating'),
    outputsBtn: document.getElementById('outputsBtn'),
    outputsView: document.getElementById('outputsView'),
    outputsGrid: document.getElementById('outputsGrid'),
    outputsEmpty: document.getElementById('outputsEmpty'),
    outputsCount: document.getElementById('outputsCount'),
    outputsKeepRate: document.getElementById('outputsKeepRate'),
    outputsSort: document.getElementById('outputsSort'),
    outputsRating: document.getElementById('outputsRating'),
    outputsWorkflow: document.getElementById('outputsWorkflow'),
    outputsCheckpoint: document.getElementById('outputsCheckpoint'),
    outputsCreator: document.getElementById('outputsCreator'),
    outputsBatchChip: document.getElementById('outputsBatchChip'),
    outputsBatchLabel: document.getElementById('outputsBatchLabel'),
    outputsBatchClear: document.getElementById('outputsBatchClear'),
    generateJobChip: document.getElementById('generateJobChip'),
    generateJobChipTitle: document.getElementById('generateJobChipTitle'),
    generateJobChipSub: document.getElementById('generateJobChipSub'),
    generateJobChipFill: document.getElementById('generateJobChipFill'),
    generateJobChipIcon: document.getElementById('generateJobChipIcon'),
    generateJobChipCancel: document.getElementById('generateJobChipCancel'),
    bulkGenerateBtn: document.getElementById('bulkGenerateBtn'),
    bulkWorkflowSelect: document.getElementById('bulkWorkflowSelect'),
    outputDetailModal: document.getElementById('outputDetailModal'),
    outputDetailImage: document.getElementById('outputDetailImage'),
    outputDetailSource: document.getElementById('outputDetailSource'),
    outputDetailMeta: document.getElementById('outputDetailMeta'),
    outputDetailPositive: document.getElementById('outputDetailPositive'),
    outputDetailNegative: document.getElementById('outputDetailNegative'),
    closeOutputDetail: document.getElementById('closeOutputDetail'),
    outputCopyParams: document.getElementById('outputCopyParams'),
    outputRegenSameSeed: document.getElementById('outputRegenSameSeed'),
    outputRegenNewSeed: document.getElementById('outputRegenNewSeed'),
    outputDelete: document.getElementById('outputDelete'),
    generatedPane: document.getElementById('generatedPane'),
    mediaCompare: document.getElementById('mediaCompare'),
    compareToggleBtn: document.getElementById('compareToggleBtn'),
    comfySdxlBtn: document.getElementById('comfySdxlBtn'),
    comfyFluxBtn: document.getElementById('comfyFluxBtn'),
    comfyProBtn: document.getElementById('comfyProBtn'),
    comfyWorkflowSelect: document.getElementById('comfyWorkflowSelect'),
    comfyDenoiseInput: document.getElementById('comfyDenoiseInput'),
    comfyStepsInput: document.getElementById('comfyStepsInput'),
    comfyCfgInput: document.getElementById('comfyCfgInput'),
    comfySeedLock: document.getElementById('comfySeedLock'),
    comfySeedInput: document.getElementById('comfySeedInput'),
    comfyModeECheck: document.getElementById('comfyModeECheck'),
    applyModeEBtn: document.getElementById('applyModeEBtn'),
    comfyStatusText: document.getElementById('comfyStatusText'),
    selectModeBtn: document.getElementById('selectModeBtn'),
    creatorStylePanel: document.getElementById('creatorStylePanel'),
    creatorStylePrefix: document.getElementById('creatorStylePrefix'),
    creatorStyleTerms: document.getElementById('creatorStyleTerms'),
    rebuildStyleBtn: document.getElementById('rebuildStyleBtn'),
    bulkBar: document.getElementById('bulkBar'),
    bulkCount: document.getElementById('bulkCount'),
    bulkReanalyzeBtn: document.getElementById('bulkReanalyzeBtn'),
    bulkDeleteBtn: document.getElementById('bulkDeleteBtn'),
    bulkClearBtn: document.getElementById('bulkClearBtn'),
    deleteConfirmTitle: document.getElementById('deleteConfirmTitle'),
    deleteConfirmBody: document.getElementById('deleteConfirmBody'),
    followingSearchInput: document.getElementById('followingSearchInput'),
    followingList: document.getElementById('followingList'),
    followingEmpty: document.getElementById('followingEmpty'),
    creatorSearchInput: document.getElementById('creatorSearchInput'),
    sourcePillRow: document.getElementById('sourcePillRow'),
    ollamaBadge: document.getElementById('ollamaBadge'),
    ollamaStatusLabel: document.getElementById('ollamaStatusLabel'),
    savePromptBtn: document.getElementById('savePromptBtn'),
    toastContainer: document.getElementById('toastContainer')
};

/**
 * Escape a value before interpolating it into innerHTML.
 *
 * Nearly everything this UI renders is third-party text: Instagram handles,
 * captions, full names, bios, filenames, and Ollama-generated tags. Any of
 * those can contain a quote or an angle bracket, which breaks the surrounding
 * markup at best and injects at worst. Prefer textContent where practical;
 * use this wherever a template literal builds HTML.
 */
function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/** Debounce a function by `wait` ms, keeping the latest call's arguments. */
function debounce(fn, wait) {
    let timer = null;
    return function debounced(...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), wait);
    };
}

// ── View preferences ─────────────────────────────────────────────────
// How you're looking at the archive should survive a reload; *where* you are
// (creator, review mode, selection) should not — those are navigation, and
// silently restoring them is disorienting. A destructive sweep in particular is a
// destructive mode nobody should land in from a refresh.
const PREFS_KEY = 'promptstudio.viewPrefs.v1';
const PREF_FIELDS = [
    'sortMode',
    'mediaType',
    'gridSize',
    'favoritesOnly',
    'unanalyzedOnly',
    'browseVerdict',
    // A filter, never a destructive mode, so restoring it on refresh is
    // helpful rather than hostile — same reasoning as browseVerdict.
    'sourceFilter',
    // How the grid is shaped, not where you are — a view pref like grid size.
    'groupPosts'
];

function loadViewPrefs() {
    let saved = null;
    try {
        saved = JSON.parse(localStorage.getItem(PREFS_KEY) || 'null');
    } catch (err) {
        saved = null; // corrupt or storage blocked (private mode) — use defaults
    }
    if (!saved || typeof saved !== 'object') return;
    PREF_FIELDS.forEach((key) => {
        if (saved[key] === undefined) return;
        const fallback = state[key];
        // Only accept a value of the same type as the default, so a hand-edited
        // or stale payload can't put state into a shape the UI can't render.
        if (typeof fallback === 'boolean') state[key] = Boolean(saved[key]);
        else if (typeof fallback === 'string' && typeof saved[key] === 'string') {
            state[key] = saved[key];
        }
    });
}

function saveViewPrefs() {
    try {
        const payload = {};
        PREF_FIELDS.forEach((key) => {
            payload[key] = state[key];
        });
        localStorage.setItem(PREFS_KEY, JSON.stringify(payload));
    } catch (err) {
        /* storage full or blocked — preferences are a nicety, never fatal */
    }
}

/** Push restored prefs into the controls so the UI matches state on first paint. */
function applyViewPrefsToControls() {
    if (elements.sortSelect) elements.sortSelect.value = state.sortMode || 'name';
    if (elements.mediaTypeSelect) elements.mediaTypeSelect.value = state.mediaType || 'all';
    if (elements.verdictFilterSelect) {
        elements.verdictFilterSelect.value = state.browseVerdict || '';
        elements.verdictFilterSelect.classList.toggle('is-active', Boolean(state.browseVerdict));
    }
    applyGridSize(state.gridSize);

    const chips = [
        [elements.favoritesFilterBtn, state.favoritesOnly],
        [elements.unanalyzedFilterBtn, state.unanalyzedOnly],
        [elements.groupPostsBtn, state.groupPosts]
    ];
    chips.forEach(([btn, on]) => {
        if (btn) btn.classList.toggle('active', Boolean(on));
    });
}

function applyGridSize(size) {
    const large = size === 'large';
    state.gridSize = large ? 'large' : 'normal';
    if (elements.galleryGrid) elements.galleryGrid.classList.toggle('large', large);
    if (elements.gridNormal) elements.gridNormal.classList.toggle('active', !large);
    if (elements.gridLarge) elements.gridLarge.classList.toggle('active', large);
}

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    // Prefs must land before the first fetchPhotos() so the initial request
    // already carries the restored sort/filters instead of re-querying.
    loadViewPrefs();
    applyViewPrefsToControls();
    initApp();
    setupEventListeners();
    wireLightboxVideoEvents();
});

function ensureHealthPolling() {
    if (state.healthPollTimer) return;
    state.healthPollTimer = setInterval(fetchHealth, 30000);
}

/* ── Poller visibility ─────────────────────────────────────────────
   Six independent intervals (health 30s, comfy 2.5s, scrape 2.5s,
   sync 2.5s, batch 4s, classify 3s) used to run forever in a
   backgrounded tab. Nothing they watch can change in a way the user
   sees while the tab is hidden, and the jobs live server-side, so
   the state is still correct on return.

   Every poller is self-arming: the polled function re-creates its own
   interval while there is work to watch and clears it when there is
   not. So pausing only has to *stop* them, and resuming is one
   immediate call each — the poller decides for itself whether to keep
   going. That also means resume doubles as a refresh, which is what
   you want the instant a tab comes back. */
const PAUSABLE_POLLERS = [
    // Health is the one poller whose "ensure" does not itself hit the network,
    // so resume fetches explicitly. Coming back to a 30s-stale Ollama badge
    // would defeat the point of resuming at all.
    { key: 'healthPollTimer', resume: () => { fetchHealth(); ensureHealthPolling(); } },
    { key: 'comfyPollTimer', resume: () => pollComfyStatus() },
    { key: 'scrapePollTimer', resume: () => ensureScrapePolling() },
    { key: 'syncPollTimer', resume: () => pollSyncStatus() },
    { key: 'batchPollTimer', resume: () => pollBatchStatus() },
    { key: 'classifyPollTimer', resume: () => pollClassifyStatus() },
];

function pausePollers() {
    state.pausedPollers = [];
    PAUSABLE_POLLERS.forEach(({ key }) => {
        if (!state[key]) return;
        clearInterval(state[key]);
        state[key] = null;
        state.pausedPollers.push(key);
    });
}

function resumePollers() {
    const paused = state.pausedPollers || [];
    state.pausedPollers = [];
    PAUSABLE_POLLERS
        .filter(({ key }) => paused.includes(key))
        .forEach(({ resume }) => {
            try {
                resume();
            } catch (err) {
                console.error('poller resume failed', err);
            }
        });
}

function handleVisibilityChange() {
    if (document.hidden) {
        pausePollers();
    } else {
        resumePollers();
    }
}

async function initApp() {
    await fetchHealth();
    await fetchStats();
    // Before fetchCreators, so the first sidebar render already has pills
    // rather than flashing them in a frame later.
    await fetchKnownSources();
    await fetchWorkflows();
    await fetchCreators();
    await fetchPhotos();
    // Resume job chips if work is mid-flight — jobs live on the server, so a
    // browser refresh must not orphan a running batch/scrape.
    pollBatchStatus();
    pollGenerateStatus();
    pollClassifyStatus();
    // Restore scrape/sync chip after refresh (queue lives on the server)
    await hydrateScrapeUiFromServer();
    ensureHealthPolling();
}

async function fetchHealth() {
    try {
        const res = await fetch('/api/health');
        const data = await res.json();
        state.ollamaOnline = Boolean(data.ollama);
        updateOllamaBadge(data);
    } catch (err) {
        state.ollamaOnline = false;
        updateOllamaBadge({ ollama: false, model: '' });
    }
}

function updateOllamaBadge(data) {
    if (!elements.ollamaBadge || !elements.ollamaStatusLabel) return;
    const online = Boolean(data.ollama);
    state.comfyOnline = data.comfy == null ? state.comfyOnline : Boolean(data.comfy);
    elements.ollamaBadge.classList.toggle('offline', !online);
    elements.ollamaBadge.classList.toggle('online', online);
    const model = data.model ? ` ${data.model}` : '';
    const comfyBit = state.comfyOnline ? ' · Comfy' : '';
    elements.ollamaStatusLabel.textContent = online
        ? `Online${model}${comfyBit}`
        : `Offline${comfyBit}`;
    if (elements.batchPromptBtn) {
        elements.batchPromptBtn.disabled = !online;
        elements.batchPromptBtn.title = online
            ? 'Analyze all uncached photos with Ollama Vision'
            : 'Ollama is offline';
    }
    if (elements.regeneratePromptBtn) {
        elements.regeneratePromptBtn.disabled = !online;
        elements.regeneratePromptBtn.title = online
            ? 'Re-Analyze photo with Ollama Vision Body & Beauty Engine'
            : 'Ollama is offline';
    }
    updateComfyButtons();
}

/**
 * A4 workflow registry — `<archive>/_workflows/<name>/{graph,slots}.json`, plus
 * the two that ship with the package.
 *
 * The picker is the whole point: before this, the graph was inferred from which
 * of three buttons you pressed, so a workflow the server could run perfectly
 * well had no way of being asked for.
 */
async function fetchWorkflows() {
    try {
        const res = await fetch('/api/workflows');
        const data = await res.json();
        state.workflows = Array.isArray(data.workflows) ? data.workflows : [];
        state.workflowDefault = data.default || state.workflowDefault;
    } catch (err) {
        // Degrade to the built-in name rather than to an empty picker: an empty
        // <select> means the Generate button posts nothing and says nothing.
        console.error('Error fetching workflows:', err);
        state.workflows = [];
    }
    renderWorkflowPickers();
    return state.workflows;
}

function workflowOptions() {
    if (state.workflows.length) return state.workflows;
    return [{ name: state.workflowDefault || 'pro', label: 'Pro (reference)', kind: 'img2img' }];
}

function workflowKind(name) {
    const found = workflowOptions().find((w) => w.name === name);
    return found ? found.kind : 'img2img';
}

function workflowLabel(name) {
    const found = workflowOptions().find((w) => w.name === name);
    return found ? found.label : name;
}

function selectedWorkflow() {
    const picked = elements.comfyWorkflowSelect && elements.comfyWorkflowSelect.value;
    return picked || state.workflowDefault || 'pro';
}

function renderWorkflowPickers() {
    const entries = workflowOptions();
    const fallback = entries.some((w) => w.name === state.workflowDefault)
        ? state.workflowDefault
        : entries[0].name;
    [elements.comfyWorkflowSelect, elements.bulkWorkflowSelect].forEach((el) => {
        if (!el) return;
        // Keep whatever the user already picked across a refresh of the list.
        const current = el.value;
        el.innerHTML = '';
        entries.forEach((wf) => {
            const opt = document.createElement('option');
            opt.value = wf.name;
            // textContent, not innerHTML: the label comes out of a JSON file the
            // user wrote, which is third-party text (hard rule 7).
            opt.textContent = wf.label || wf.name;
            opt.title = wf.kind;
            el.appendChild(opt);
        });
        el.value = entries.some((w) => w.name === current) ? current : fallback;
    });
}

function updateComfyButtons() {
    const on = state.comfyOnline !== false;
    const hasPrompt = Boolean(state.currentPromptData);
    [elements.comfySdxlBtn, elements.comfyFluxBtn, elements.comfyProBtn].forEach((btn) => {
        if (!btn) return;
        btn.disabled = !on || !hasPrompt;
        btn.title = on ? btn.title.replace(/ \(offline\)/, '') : 'ComfyUI offline';
    });
    if (elements.applyModeEBtn) {
        elements.applyModeEBtn.disabled = !hasPrompt;
    }
    [
        elements.comfyWorkflowSelect,
        elements.comfyDenoiseInput,
        elements.comfyStepsInput,
        elements.comfyCfgInput,
        elements.comfySeedLock,
        elements.comfyModeECheck,
    ].forEach((el) => {
        if (el) el.disabled = !on;
    });
    syncComfyWorkflowControls();
    syncComfySeedInput();
}

/**
 * Denoise and Mode E only mean anything to a workflow that takes a reference
 * image. Leaving them live on a txt2img pick offers two controls the server
 * will ignore, which reads as a bug in the graph rather than in the UI.
 */
function syncComfyWorkflowControls() {
    const isRef = workflowKind(selectedWorkflow()) === 'img2img';
    const off = state.comfyOnline === false;
    [elements.comfyDenoiseInput, elements.comfyModeECheck].forEach((el) => {
        if (!el) return;
        el.disabled = off || !isRef;
        const label = el.closest('.comfy-denoise-label');
        if (label) label.style.opacity = isRef ? '' : '0.45';
    });
}

function syncComfySeedInput() {
    if (!elements.comfySeedInput || !elements.comfySeedLock) return;
    const locked = elements.comfySeedLock.checked;
    elements.comfySeedInput.disabled = !locked || state.comfyOnline === false;
    if (!locked) {
        elements.comfySeedInput.value = '';
        elements.comfySeedInput.placeholder = 'random';
    } else if (!elements.comfySeedInput.value) {
        elements.comfySeedInput.value = String(Math.floor(Math.random() * 2 ** 31));
    }
}

function readComfyProControls() {
    let denoise = 0.70;
    let steps = 32;
    let cfg = 6.0;
    let seed = null;
    if (elements.comfyDenoiseInput) {
        const parsed = parseFloat(elements.comfyDenoiseInput.value);
        if (!Number.isNaN(parsed)) denoise = Math.min(1, Math.max(0.45, parsed));
    }
    if (elements.comfyStepsInput) {
        const parsed = parseInt(elements.comfyStepsInput.value, 10);
        if (!Number.isNaN(parsed)) steps = Math.min(60, Math.max(10, parsed));
    }
    if (elements.comfyCfgInput) {
        const parsed = parseFloat(elements.comfyCfgInput.value);
        if (!Number.isNaN(parsed)) cfg = Math.min(15, Math.max(1, parsed));
    }
    if (elements.comfySeedLock && elements.comfySeedLock.checked && elements.comfySeedInput) {
        const parsed = parseInt(elements.comfySeedInput.value, 10);
        if (!Number.isNaN(parsed)) seed = parsed;
    }
    const useModeE = !(elements.comfyModeECheck && !elements.comfyModeECheck.checked);
    return { denoise, steps, cfg, seed, useModeE };
}

async function applyModeEToEditor({ save = false } = {}) {
    if (state.lightboxIndex === -1 || !state.currentPromptData) return;
    const photo = state.photos[state.lightboxIndex];
    const positive = elements.positivePromptText.innerText.trim()
        || state.currentPromptData.positive_prompt;
    const negative = elements.negativePromptText.innerText.trim()
        || state.currentPromptData.negative_prompt;
    try {
        const res = await fetch('/api/prompt/mode-e', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                path: photo.rel_path,
                positive_prompt: positive,
                negative_prompt: negative,
                apply: Boolean(save),
            }),
        });
        const data = await res.json();
        if (!res.ok) {
            showToast(data.message || 'Mode E rewrite failed');
            return;
        }
        elements.positivePromptText.innerText = data.positive_prompt || '';
        elements.negativePromptText.innerText = data.negative_prompt || '';
        state.currentPromptData = {
            ...state.currentPromptData,
            ...(data.prompt || {}),
            positive_prompt: data.positive_prompt,
            negative_prompt: data.negative_prompt,
        };
        if (data.prompt && data.prompt.exports) {
            state.currentPromptData.exports = data.prompt.exports;
        }
        state.promptDirty = !save;
        if (elements.savePromptBtn) {
            elements.savePromptBtn.style.display = state.promptDirty ? 'inline-flex' : 'none';
        }
        const anti = (data.anti_terms || []).slice(0, 4).join(', ');
        showToast(
            anti
                ? `Mode E applied (${data.source}) · anti: ${anti}`
                : `Mode E applied (${data.source})`
        );
        if (elements.comfyStatusText) {
            elements.comfyStatusText.textContent = `Mode E ready (${data.source})`;
        }
    } catch (err) {
        showToast('Mode E request failed');
    }
}

function requireOllama() {
    if (state.ollamaOnline === false) {
        showToast('Ollama is offline — start Ollama to generate prompts');
        return false;
    }
    return true;
}

// API Calls
async function fetchStats() {
    try {
        const res = await fetch('/api/stats');
        const data = await res.json();
        elements.statTotalPhotos.textContent = data.total_photos.toLocaleString();
        elements.statTotalVideos.textContent = (data.total_videos ?? 0).toLocaleString();
        elements.statCreators.textContent = data.total_creators.toLocaleString();
        elements.statPersonPhotos.textContent = (data.prompts_ready ?? 0).toLocaleString();
        if (typeof data.trash_enabled === 'boolean') {
            state.trashEnabled = data.trash_enabled;
        }
        state.trashCount = data.trash_count ?? 0;
        // Archive-wide, never narrowed by the source filter — the navbar
        // Classify All button needs the number its job actually covers.
        state.archiveUnclassified = Number(data.unclassified_total) || 0;
        // Pass rates for the verdict filters (B4). Rides on /api/stats rather
        // than a chip-by-chip round trip, and refreshes exactly where it
        // matters: app init and the end of a classify run both call this.
        state.verdictFacets = data.verdict_facets || null;
        updateTrashButtonUi();
        updateClassifyAllButton();
        renderVerdictPassRates();
    } catch (err) {
        console.error('Error fetching stats:', err);
    }
}

function _fmtRate(rate) {
    if (rate == null || Number.isNaN(rate)) return '—';
    return `${(rate * 100).toFixed(1)}%`;
}

function _fmtDist(dist) {
    if (!dist || typeof dist !== 'object') return '—';
    return Object.keys(dist)
        .sort((a, b) => Number(a) - Number(b))
        .map((k) => `<span class="insights-chip"><b>${escapeHtml(k)}</b> ${Number(dist[k]).toLocaleString()}</span>`)
        .join(' ');
}

// Above this share of classified media on one tier, the classifier is barely
// discriminating whatever the prompt claims. The previous one shipped at 0.85
// and nothing was reading the distribution — see docs/design_media_classifier.md
// §5. Matches the B4 platform rule in docs/product_review.md.
const TOP_TIER_SHARE_WARN = 0.6;

/** Reject threshold, defaulting to the server's own default if absent. */
function classifyRejectCut(c) {
    const cut = Number(c.reject_max_tier);
    return Number.isFinite(cut) ? cut : 1;
}

/** One tier: chip, label, proportional bar, count and share. */
function classifyTierRow({ tier, count, total, label, isReject }) {
    const pct = total > 0 ? (count / total) * 100 : 0;
    const tag = isReject ? '<span class="insights-tier-tag">reject</span>' : '';
    return `
        <div class="insights-tier-row">
            <span class="tier-chip t${tier}">T${tier}</span>
            <span class="insights-tier-name">
                <span class="insights-tier-text" title="${escapeHtml(label)}">${escapeHtml(label)}</span>${tag}
            </span>
            <span class="insights-tier-bar">
                <span class="insights-tier-fill t${tier}" style="width:${pct.toFixed(1)}%;"></span>
            </span>
            <span class="insights-tier-n">${count.toLocaleString()}<em>${pct.toFixed(0)}%</em></span>
        </div>`;
}

/** Tier -1 is "the vision call failed", not a measurement — never a bar. */
function classifyErrorRow(errors, errorRate) {
    if (!errors) return '';
    return `
        <div class="insights-tier-row is-error">
            <span class="tier-chip terr">!</span>
            <span class="insights-tier-name">
                <span class="insights-tier-text">Failed to classify</span>
            </span>
            <span class="insights-tier-bar"></span>
            <span class="insights-tier-n">${errors.toLocaleString()}<em>${_fmtRate(errorRate)}</em></span>
        </div>`;
}

function classifyTierRows(c, classified) {
    const dist = c.distribution || {};
    const labels = c.labels || {};
    const cut = classifyRejectCut(c);
    return Object.keys(dist)
        .map(Number)
        .filter(tier => Number.isFinite(tier) && tier >= 0)
        .sort((a, b) => a - b)
        .map(tier => classifyTierRow({
            tier,
            count: Number(dist[String(tier)] || 0),
            total: classified,
            label: String(labels[String(tier)] || `Tier ${tier}`),
            isReject: tier <= cut,
        }))
        .join('');
}

function classifyMetrics(c, { classified, errors, cut, saturated }) {
    const rejectHelp = 'Share judged reject at the current cut. Only the tier is '
        + 'stored, so changing CLASSIFY_REJECT_MAX_TIER re-thresholds instantly.';
    const shareHelp = 'Share of classified media on the single most common tier. '
        + `Above ${TOP_TIER_SHARE_WARN} the filter is barely discriminating.`;
    return `
        <div class="insights-metrics">
            <div class="insights-metric">
                <span class="insights-metric-value">${classified.toLocaleString()}</span>
                <span class="insights-metric-label">Classified</span>
                <span class="insights-metric-sub">${errors.toLocaleString()} failed</span>
            </div>
            <div class="insights-metric" title="${rejectHelp}">
                <span class="insights-metric-value">${_fmtRate(c.reject_rate)}</span>
                <span class="insights-metric-label">Reject rate</span>
                <span class="insights-metric-sub">cut: tier ≤ ${cut}</span>
            </div>
            <div class="insights-metric${saturated ? ' is-warn' : ''}" title="${shareHelp}">
                <span class="insights-metric-value">${_fmtRate(c.top_tier_share)}</span>
                <span class="insights-metric-label">Top tier share</span>
                <span class="insights-metric-sub">${saturated ? 'saturated' : `under ${TOP_TIER_SHARE_WARN}`}</span>
            </div>
        </div>`;
}

function classifySaturationWarning(topShare) {
    return `
        <div class="insights-warn" role="status">
            <i class="fa-solid fa-triangle-exclamation"></i>
            ${_fmtRate(topShare)} of classified media is on one tier. A filter this flat
            is close to a no-op — treat the verdicts as unreliable until the
            distribution spreads.
        </div>`;
}

/**
 * Is the tier distribution saturated?
 *
 * Prefer the server's answer: /api/insights runs the same B4 rule the pytest
 * gate does, minimum-N and all, so the panel cannot contradict the check that
 * fails the build. Falls back to the raw share for payloads from before the
 * rule existed — the panel is also rendered directly from tests and tools.
 */
function classifySaturated(data, topShare) {
    const guard = data && data.saturation;
    if (guard && typeof guard.saturated === 'boolean') return guard.saturated;
    return Number(topShare || 0) > TOP_TIER_SHARE_WARN;
}

/** Tier distribution as labelled bars — the guard metric, made legible. */
function renderClassifyInsights(c) {
    const data = c || {};
    if (data.error) {
        return `<span class="insights-muted">Unavailable: ${escapeHtml(String(data.error))}</span>`;
    }
    const classified = Number(data.classified || 0);
    if (!classified) {
        return '<span class="insights-muted">Nothing classified yet — '
            + 'pick a creator and run <b>Classify</b>.</span>';
    }
    const errors = Number(data.errors || 0);
    const cut = classifyRejectCut(data);
    const topShare = Number(data.top_tier_share || 0);
    const saturated = classifySaturated(data, topShare);

    return classifyMetrics(data, { classified, errors, cut, saturated })
        + (saturated ? classifySaturationWarning(topShare) : '')
        + '<div class="insights-sublabel">Tier distribution'
        + ` <span class="insights-hint">— reject cut at tier ≤ ${cut}</span></div>`
        + `<div class="insights-tiers">${classifyTierRows(data, classified)}`
        + `${classifyErrorRow(errors, data.error_rate)}</div>`;
}

function renderInsights(data) {
    if (!elements.insightsBody) return;
    const p = data.prompts || {};
    const gen = data.generations || {};

    const pipeBlocks = Object.keys(p.by_pipeline_version || {}).length
        ? Object.entries(p.by_pipeline_version).map(([ver, n]) =>
            `<span class="insights-chip"><b>${escapeHtml(ver)}</b> ${Number(n).toLocaleString()}</span>`).join(' ')
        : '<span class="insights-muted">—</span>';

    elements.insightsBody.innerHTML = `
        <div class="insights-grid">
            <section class="insights-section">
                <h4><i class="fa-solid fa-wand-magic-sparkles"></i> Prompts</h4>
                <div class="insights-metrics">
                    <div class="insights-metric">
                        <span class="insights-metric-value">${(p.total ?? 0).toLocaleString()}</span>
                        <span class="insights-metric-label">Prompt bundles</span>
                    </div>
                    <div class="insights-metric" title="Share of prompts the user edited by hand (manual_edit)">
                        <span class="insights-metric-value">${_fmtRate(p.edit_rate)}</span>
                        <span class="insights-metric-label">Edit rate</span>
                        <span class="insights-metric-sub">${(p.manual_edits ?? 0).toLocaleString()} edited</span>
                    </div>
                    <div class="insights-metric" title="Share of prompts with at least one history snapshot (regenerated or re-saved)">
                        <span class="insights-metric-value">${_fmtRate(p.regenerate_rate)}</span>
                        <span class="insights-metric-label">Regenerate rate</span>
                        <span class="insights-metric-sub">avg history ${p.avg_history_depth ?? 0}</span>
                    </div>
                </div>
                <div class="insights-sublabel">By pipeline version</div>
                <div class="insights-dist">${pipeBlocks}</div>
            </section>

            <section class="insights-section">
                <h4><i class="fa-solid fa-image"></i> Generations</h4>
                <div class="insights-metrics">
                    <div class="insights-metric">
                        <span class="insights-metric-value">${(gen.total_outputs ?? 0).toLocaleString()}</span>
                        <span class="insights-metric-label">Outputs</span>
                    </div>
                    <div class="insights-metric">
                        <span class="insights-metric-value">${(gen.sources_with_gens ?? 0).toLocaleString()}</span>
                        <span class="insights-metric-label">Source photos</span>
                        <span class="insights-metric-sub">avg ${gen.avg_per_source ?? 0} each</span>
                    </div>
                    <div class="insights-metric">
                        <span class="insights-metric-value">${(gen.sources_with_multiple ?? 0).toLocaleString()}</span>
                        <span class="insights-metric-label">Retried sources</span>
                        <span class="insights-metric-sub">keep rate: ${_fmtRate(gen.keep_rate)} (needs rating)</span>
                    </div>
                </div>
            </section>

            <section class="insights-section">
                <h4><i class="fa-solid fa-wand-sparkles"></i> Keep / reject classifier</h4>
                ${renderClassifyInsights(data.classify)}
            </section>
        </div>
    `;
}

async function loadInsights() {
    if (!elements.insightsBody) return;
    elements.insightsBody.innerHTML = '<div class="insights-loading">Loading…</div>';
    try {
        const res = await fetch('/api/insights');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        renderInsights(data);
    } catch (err) {
        console.error('insights failed', err);
        elements.insightsBody.innerHTML = `<div class="insights-error">Could not load insights: ${escapeHtml(err.message || String(err))}</div>`;
    }
}

function openInsightsModal() {
    if (!elements.insightsModal) return;
    elements.insightsModal.style.display = 'flex';
    loadInsights();
}

function closeInsightsModal() {
    if (elements.insightsModal) elements.insightsModal.style.display = 'none';
}

async function fetchCreators() {
    try {
        const params = new URLSearchParams();
        if (state.sourceFilter) params.append('source', state.sourceFilter);
        const qs = params.toString();
        const res = await fetch(`/api/creators${qs ? '?' + qs : ''}`);
        if (res.status === 400) {
            // A stored pref naming a source that is no longer registered.
            // Drop it rather than leaving the sidebar permanently empty.
            console.warn('Unknown source filter, clearing:', state.sourceFilter);
            state.sourceFilter = '';
            saveViewPrefs();
            return fetchCreators();
        }
        state.creators = await res.json();
        elements.creatorCount.textContent = state.creators.length;
        renderSourcePills();
        renderCreatorList();
        populateUploadCreators();
        // Verdict counters ride along on /api/creators, so the review strip's
        // chip counts refresh whenever the sidebar does.
        if (state.reviewMode) updateReviewBar();
        // The navbar Classify All count is archive-wide, so it only changes
        // when these counters do.
        updateClassifyAllButton();
    } catch (err) {
        console.error('Error fetching creators:', err);
    }
}

async function fetchPhotos({ append = false } = {}) {
    // Infinite-scroll appends must not stack, or the same page loads twice.
    // A new filter/search/sort, by contrast, must never be dropped — it
    // supersedes whatever is in flight (the old guard silently discarded it,
    // so the grid could end up not matching the active filters).
    if (append && state.photosLoading) return;
    if (state.photosRequest) state.photosRequest.abort();
    const controller = new AbortController();
    state.photosRequest = controller;
    state.photosLoading = true;
    // Skeletons only on a fresh query; an append keeps the real cards visible.
    setGalleryLoading(true, { skeletons: !append });
    try {
        // Tiles, not photos: grouped, one tile is a whole post, and the
        // sentinel appends after the last *tile*.
        const prevLen = append ? state.galleryTiles.length : 0;
        // Offset is only committed to state once the response lands, so an
        // aborted request cannot corrupt paging or blank out state.photos.
        const requestOffset = append ? state.photoOffset : 0;

        let url = '/api/photos';
        const params = new URLSearchParams();

        if (state.selectedCreator) {
            params.append('creator', state.selectedCreator);
        }
        if (state.searchQuery) {
            params.append('search', state.searchQuery);
        }
        if (state.unanalyzedOnly) {
            params.append('unanalyzed', '1');
        }
        if (state.favoritesOnly) {
            params.append('favorite', '1');
        }
        params.append('media_type', state.mediaType || 'all');
        if (state.sourceFilter) {
            params.append('source', state.sourceFilter);
        }
        if (state.reviewMode && state.verdictFilter) {
            params.append('verdict', state.verdictFilter);
            // Harshest first while triaging — the files most likely to be
            // deleted should be the ones on screen without scrolling.
            params.append('sort', 'tier');
        } else {
            // Outside review, the verdict is a browse filter like any other:
            // "show me every tier-4 shot" should not require entering a
            // delete-oriented mode first.
            if (state.browseVerdict) {
                params.append('verdict', state.browseVerdict);
            }
            params.append('sort', state.sortMode || 'name');
        }
        // Review mode deliberately opts out: triage adjudicates one file at a
        // time, and a collapsed carousel would hide rejects behind a tile.
        if (state.groupPosts && !state.reviewMode) {
            params.append('group', 'post');
        }
        params.append('offset', String(requestOffset));
        params.append('limit', String(state.photoLimit));

        url += '?' + params.toString();

        const res = await fetch(url, { signal: controller.signal });
        const data = await res.json();
        const page = Array.isArray(data) ? data : (data.photos || []);
        state.photoTotal = Array.isArray(data) ? page.length : (data.total || page.length);
        state.photoHasMore = Array.isArray(data)
            ? false
            : Boolean(data.has_more);
        state.photos = append ? state.photos.concat(page) : page;
        // THE paging unit. Grouped, `total` counts posts while `page` carries
        // every slide, so neither array length is it — the server names it.
        // Advancing by state.photos.length here is exactly how an infinite
        // scroll starts skipping content one page at a time.
        const consumed = Array.isArray(data)
            ? page.length
            : Number(data.rows ?? page.length);
        state.photoOffset = (append ? state.photoOffset : 0) + consumed;
        renderGallery({ append, fromIndex: prevLen });
    } catch (err) {
        // A superseded request is expected, not a failure — the newer one wins.
        if (err.name === 'AbortError') return;
        console.error('Error fetching photos:', err);
        showToast('Could not load photos');
    } finally {
        // Only the newest request owns the loading flag; an aborted one must
        // not clear the state its successor just set.
        if (state.photosRequest === controller) {
            state.photosRequest = null;
            state.photosLoading = false;
            setGalleryLoading(false);
            // renderGallery replaces them on success; on error they'd linger
            clearGallerySkeletons();
            if (!state.photos.length) {
                elements.emptyState.style.display = 'flex';
            }
            // photoTotal is only known once the page lands, and the review
            // strip prints it.
            if (state.reviewMode) updateReviewBar();
        }
    }
}

/** Visible + assistive-tech signal that a gallery fetch is in flight. */
function setGalleryLoading(on, { skeletons = false } = {}) {
    if (elements.galleryGrid) {
        elements.galleryGrid.setAttribute('aria-busy', on ? 'true' : 'false');
    }
    const wrap = document.querySelector('.search-bar-container');
    if (wrap) wrap.classList.toggle('is-searching', Boolean(on));
    if (skeletons) showGallerySkeletons();
}

const SKELETON_COUNT = 12;

/**
 * Placeholder cards while the first page loads.
 *
 * Only for a fresh query — an infinite-scroll append keeps the real cards on
 * screen, and replacing them with skeletons would look like a reset.
 */
function showGallerySkeletons() {
    if (!elements.galleryGrid) return;
    elements.emptyState.style.display = 'none';
    const cards = Array.from({ length: SKELETON_COUNT })
        .map(() => '<div class="photo-card skeleton" aria-hidden="true"></div>')
        .join('');
    elements.galleryGrid.innerHTML = cards;
}

function clearGallerySkeletons() {
    if (!elements.galleryGrid) return;
    elements.galleryGrid.querySelectorAll('.photo-card.skeleton').forEach((el) => el.remove());
}

async function loadMorePhotos() {
    if (!state.photoHasMore || state.photosLoading) return;
    await fetchPhotos({ append: true });
}

async function fetchPromptForPhoto(relPath, forceRefresh = false) {
    try {
        setPromptEditable(false);
        elements.positivePromptText.textContent = "Analyzing photo with Ollama Vision Body & Beauty Engine...";
        elements.negativePromptText.textContent = "Loading negative prompt...";
        elements.promptTagsContainer.innerHTML = '';

        let apiUrl = `/api/prompt?path=${encodeURIComponent(relPath)}`;
        if (forceRefresh) {
            apiUrl += '&refresh=true';
        }

        const res = await fetch(apiUrl);
        const data = await res.json();
        applyPromptData(data);

        // Mark gallery card as ready after successful generate
        const photo = state.photos[state.lightboxIndex];
        if (photo && photo.rel_path === relPath) {
            photo.has_prompt = true;
            photo.prompt_stale = false;
        }
    } catch (err) {
        console.error('Error fetching prompt:', err);
        elements.positivePromptText.textContent = "Error loading AI prompt";
        setPromptEditable(false);
    }
}

/**
 * Drop photos from the current view without refetching.
 *
 * Deleting used to call initApp(), which reset photoOffset to 0 and threw away
 * every loaded page. This splices state,
 * removes the cards, and adjusts the counters in place so scroll position and
 * all loaded pages survive.
 *
 * Returns the number of photos actually removed from the view.
 */
function removePhotosFromView(relPaths) {
    const targets = new Set(relPaths || []);
    if (!targets.size) return 0;

    const removedByCreator = new Map();
    const tilesBefore = state.galleryTiles.length;
    let removed = 0;

    state.photos = state.photos.filter((p) => {
        if (!targets.has(p.rel_path)) return true;
        removed += 1;
        removedByCreator.set(p.creator, (removedByCreator.get(p.creator) || 0) + 1);
        return false;
    });
    if (!removed) return 0;

    targets.forEach((rel) => {
        state.selectedPaths.delete(rel);
        const card = elements.galleryGrid.querySelector(
            `.photo-card[data-rel-path="${CSS.escape(rel)}"]`
        );
        if (card) card.remove();
    });

    // Grouped, a card is a post: deleting slide 3 of 11 leaves the tile there
    // with a stale badge, and deleting slide 1 removes a card whose other ten
    // slides are still loaded. Cheaper to redraw the grid than to patch it.
    rebuildGalleryTiles();
    if (state.groupPosts) renderGallery();

    // Paging counters are in the same unit the server pages in — tiles, which
    // is posts when grouping and photos when not. Deleting one slide of a
    // carousel removes no post, so neither counter moves.
    const tilesRemoved = Math.max(0, tilesBefore - state.galleryTiles.length);
    state.photoOffset = Math.max(0, state.photoOffset - tilesRemoved);
    state.photoTotal = Math.max(0, (state.photoTotal || 0) - tilesRemoved);

    // Sidebar + stats counters, without hitting the O(archive) /api/stats route
    removedByCreator.forEach((n, creatorName) => {
        const creator = state.creators.find((c) => c.name === creatorName);
        if (!creator) return;
        creator.photo_count = Math.max(0, (creator.photo_count || 0) - n);
        if (typeof creator.scored_count === 'number') {
            creator.scored_count = Math.max(0, creator.scored_count - n);
        }
    });
    if (removedByCreator.size) renderCreatorList();

    const totalEl = elements.statTotalPhotos;
    if (totalEl) {
        const current = parseInt(String(totalEl.textContent).replace(/[^0-9]/g, ''), 10);
        if (Number.isFinite(current)) {
            totalEl.textContent = Math.max(0, current - removed).toLocaleString();
        }
    }

    elements.galleryCount.textContent = galleryCountLabel();
    elements.emptyState.style.display = state.photos.length === 0 ? 'flex' : 'none';
    updateBulkBar();
    return removed;
}

/**
 * Keep the lightbox usable after the photo it was showing disappears:
 * slide to whatever now occupies that index, or close when nothing is left.
 */
function reconcileLightboxAfterRemoval(removedIndex) {
    if (state.lightboxIndex === -1) return;
    if (!state.photos.length) {
        closeLightbox();
        return;
    }
    if (removedIndex < 0) return;
    const next = Math.min(removedIndex, state.photos.length - 1);
    openLightbox(next);
}

async function restoreFromTrash(trashIds, { label = '' } = {}) {
    const ids = (trashIds || []).filter(Boolean);
    if (!ids.length) return;
    try {
        const res = await fetch('/api/trash/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids })
        });
        const data = await res.json();
        if (!res.ok) {
            showToast('Restore failed');
            return;
        }
        const conflicts = (data.results || []).filter((r) => r.status === 'conflict').length;
        if (data.restored) {
            showToast({
                title: `Restored ${data.restored}${label ? ` ${label}` : ''}`,
                body: conflicts
                    ? `${conflicts} skipped — a file already exists at that path`
                    : 'Back in the archive with prompts and favorites intact',
                variant: 'success'
            });
        } else {
            showToast(conflicts ? 'Nothing restored — paths already occupied' : 'Nothing restored');
        }
        // Restored items can sort anywhere in the current filter, so resync the view.
        await fetchStats();
        await fetchCreators();
        await fetchPhotos();
    } catch (err) {
        console.error('Restore failed:', err);
        showToast('Restore request failed');
    }
}

function updateTrashButtonUi() {
    if (!elements.trashBtn) return;
    elements.trashBtn.style.display = state.trashEnabled ? 'inline-flex' : 'none';
    if (elements.trashCountBadge) {
        elements.trashCountBadge.textContent = String(state.trashCount || 0);
        elements.trashCountBadge.style.display = state.trashCount ? 'inline-flex' : 'none';
    }
}

function formatTrashSize(bytes) {
    const n = Number(bytes) || 0;
    if (n <= 0) return '';
    return ` · ${formatBytes(n)}`;
}

async function loadTrashList() {
    if (!elements.trashList) return;
    elements.trashList.innerHTML = '';
    if (elements.trashEmpty) {
        elements.trashEmpty.textContent = 'Loading…';
        elements.trashList.appendChild(elements.trashEmpty);
    }
    try {
        const res = await fetch('/api/trash?limit=200');
        const data = await res.json();
        const entries = data.entries || [];
        state.trashCount = data.total ?? entries.length;
        updateTrashButtonUi();

        if (elements.trashSummary) {
            elements.trashSummary.textContent = entries.length
                ? `${data.total} item(s)${formatTrashSize(data.bytes)} · auto-purged after ${data.retention_days} days`
                : 'Trash is empty. Deleted media lands here so you can put it back.';
        }

        elements.trashList.innerHTML = '';
        if (!entries.length) {
            const empty = document.createElement('div');
            empty.className = 'trash-empty';
            empty.textContent = 'Nothing in the Trash.';
            elements.trashList.appendChild(empty);
            return;
        }

        entries.forEach((entry) => {
            const row = document.createElement('div');
            row.className = 'trash-row';

            const info = document.createElement('div');
            info.className = 'trash-row-info';
            const title = document.createElement('div');
            title.className = 'trash-row-title';
            title.textContent = entry.rel_path || entry.filename || entry.id;
            const meta = document.createElement('div');
            meta.className = 'trash-row-meta';
            const bits = [formatRelativeTime(entry.deleted_at)];
            if (entry.file_size) bits.push(formatBytes(entry.file_size));
            if (entry.favorite) bits.push('★ favorite');
            if (entry.prompt_bundle) bits.push('has prompt');
            if (!entry.media_present) bits.push('⚠ file missing');
            meta.textContent = bits.join(' · ');
            info.appendChild(title);
            info.appendChild(meta);

            const actions = document.createElement('div');
            actions.className = 'trash-row-actions';

            const restoreBtn = document.createElement('button');
            restoreBtn.type = 'button';
            restoreBtn.className = 'btn btn-secondary btn-sm';
            restoreBtn.innerHTML = '<i class="fa-solid fa-rotate-left"></i> Restore';
            restoreBtn.disabled = !entry.media_present;
            restoreBtn.addEventListener('click', async () => {
                restoreBtn.disabled = true;
                await restoreFromTrash([entry.id], { label: 'photo' });
                await loadTrashList();
            });

            const purgeBtn = document.createElement('button');
            purgeBtn.type = 'button';
            purgeBtn.className = 'btn btn-danger btn-sm';
            purgeBtn.innerHTML = '<i class="fa-solid fa-xmark"></i> Delete forever';
            purgeBtn.addEventListener('click', async () => {
                purgeBtn.disabled = true;
                await purgeTrash({ ids: [entry.id] });
                await loadTrashList();
            });

            actions.appendChild(restoreBtn);
            actions.appendChild(purgeBtn);
            row.appendChild(info);
            row.appendChild(actions);
            elements.trashList.appendChild(row);
        });
    } catch (err) {
        console.error('Trash list failed:', err);
        elements.trashList.innerHTML = '';
        const failed = document.createElement('div');
        failed.className = 'trash-empty';
        failed.textContent = 'Could not load the Trash.';
        elements.trashList.appendChild(failed);
    }
}

async function purgeTrash(payload) {
    try {
        const res = await fetch('/api/trash/purge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (!res.ok) {
            showToast('Purge failed');
            return;
        }
        showToast(
            data.purged
                ? `Permanently removed ${data.purged} item(s)`
                : 'Nothing to purge'
        );
        await fetchStats();
    } catch (err) {
        console.error('Purge failed:', err);
        showToast('Purge request failed');
    }
}

function openTrashModal() {
    if (!elements.trashModal) return;
    elements.trashModal.style.display = 'flex';
    loadTrashList();
}

function closeTrashModal() {
    if (elements.trashModal) elements.trashModal.style.display = 'none';
}

async function deletePhoto(relPath) {
    const removedIndex = state.photos.findIndex((p) => p.rel_path === relPath);
    try {
        const res = await fetch(`/api/photo?path=${encodeURIComponent(relPath)}`, { method: 'DELETE' });
        if (!res.ok) {
            showToast('Error deleting photo');
            return;
        }
        const data = await res.json().catch(() => ({}));
        removePhotosFromView([relPath]);
        reconcileLightboxAfterRemoval(removedIndex);
        if (data.trash_id) {
            showToast({
                title: 'Moved to Trash',
                body: data.filename || relPath,
                actionLabel: 'Undo',
                onAction: () => restoreFromTrash([data.trash_id], { label: 'photo' }),
                duration: 8000
            });
        } else {
            showToast('Photo permanently deleted');
        }
    } catch (err) {
        showToast('Delete request failed');
    }
}

function togglePhotoSelection(relPath, selected) {
    if (selected) state.selectedPaths.add(relPath);
    else state.selectedPaths.delete(relPath);
    updateBulkBar();
}

function updateBulkBar() {
    const count = state.selectedPaths.size;
    if (!elements.bulkBar) return;
    // Review mode owns its own action strip; two floating bars stacked on each
    // other was the old behaviour and it hid the one that mattered.
    if (state.reviewMode) {
        elements.bulkBar.style.display = 'none';
        updateReviewBar();
        return;
    }
    if (!state.selectMode || count === 0) {
        elements.bulkBar.style.display = 'none';
        return;
    }
    elements.bulkBar.style.display = 'flex';
    elements.bulkCount.textContent = `${count} selected`;
}





function setSelectMode(enabled) {
    state.selectMode = enabled;
    if (!enabled) state.selectedPaths.clear();
    if (elements.selectModeBtn) {
        elements.selectModeBtn.classList.toggle('active', enabled);
    }
    elements.galleryGrid.classList.toggle('select-mode', enabled);
    renderGallery();
}

function clearSelection() {
    state.selectedPaths.clear();
    updateBulkBar();
    if (state.selectMode) renderGallery();
}

/**
 * One-time load of the source registry. Pills are built from this rather than
 * hardcoded, so registering a source in `sources/__init__.py` is enough.
 */
async function fetchKnownSources() {
    if (state.knownSources) return state.knownSources;
    try {
        const res = await fetch('/api/sources');
        const data = await res.json();
        state.knownSources = Array.isArray(data.sources) ? data.sources : [];
    } catch (err) {
        // Without the registry the pills degrade to "All" only — the gallery
        // still works, it just cannot be filtered.
        console.error('Error fetching sources:', err);
        state.knownSources = [];
    }
    return state.knownSources;
}

/**
 * Source pills above the creator list.
 *
 * Counts come from the `sources` map on each creator, which /api/creators keeps
 * UNFILTERED on purpose — so every pill can show its true total even while a
 * different pill is active. A pill with no media anywhere is hidden rather than
 * shown as a permanent zero.
 */
function renderSourcePills() {
    const row = elements.sourcePillRow;
    if (!row) return;
    const registry = state.knownSources || [];
    if (!registry.length && !state.sourceFilter) {
        row.innerHTML = '';
        return;
    }

    const totals = new Map();
    let grand = 0;
    state.creators.forEach((c) => {
        const map = (c && c.sources) || {};
        Object.keys(map).forEach((src) => {
            const n = Number(map[src]) || 0;
            totals.set(src, (totals.get(src) || 0) + n);
            grand += n;
        });
    });

    const pills = [{ name: '', label: 'All', count: grand }];
    registry.forEach((s) => {
        const count = totals.get(s.name) || 0;
        // Keep the active pill visible even at zero, or clicking it would make
        // the control that undoes the filter disappear.
        if (count > 0 || state.sourceFilter === s.name) {
            pills.push({ name: s.name, label: s.label || s.name, count });
        }
    });
    // A filter with no matching pill would be inescapable: an active filter, an
    // empty sidebar, and no control to clear it. Reachable whenever the
    // registry is unavailable (/api/sources failed) while a filter is
    // persisted in localStorage from a previous session.
    if (state.sourceFilter && !pills.some((p) => p.name === state.sourceFilter)) {
        pills.push({
            name: state.sourceFilter,
            label: laneLabel(state.sourceFilter),
            count: totals.get(state.sourceFilter) || 0,
        });
    }

    // A single source is not a choice — don't spend sidebar height on it.
    if (pills.length <= 2 && !state.sourceFilter) {
        row.innerHTML = '';
        return;
    }

    row.innerHTML = pills
        .map((p) => {
            const active = state.sourceFilter === p.name ? ' active' : '';
            return (
                `<button type="button" class="source-pill${active}" ` +
                `data-source="${escapeHtml(p.name)}" ` +
                `aria-pressed="${state.sourceFilter === p.name}">` +
                `${escapeHtml(p.label)}` +
                `<span class="source-pill-count">${Number(p.count)}</span>` +
                `</button>`
            );
        })
        .join('');

    row.querySelectorAll('.source-pill').forEach((btn) => {
        btn.addEventListener('click', () => setSourceFilter(btn.dataset.source || ''));
    });
}

/**
 * Switch platform. Clears the selected creator when that creator has nothing
 * from the new source — otherwise the gallery would sit empty with a creator
 * highlighted in a sidebar that no longer lists it.
 */
function setSourceFilter(source) {
    if (state.sourceFilter === source) return;
    state.sourceFilter = source;
    saveViewPrefs();

    if (state.selectedCreator && source) {
        const current = state.creators.find((c) => c.name === state.selectedCreator);
        const has = current && current.sources && Number(current.sources[source]) > 0;
        if (!has) {
            state.selectedCreator = null;
            state.creatorPanelOpen = false;
            elements.galleryTitle.textContent = 'All Photos';
            hideCreatorStylePanel();
        }
    }
    clearSelection();
    fetchCreators();
    fetchPhotos();
}

// Render Functions
function renderCreatorList() {
    elements.creatorList.innerHTML = '';
    const q = (state.creatorSearchQuery || '').toLowerCase();

    const allItem = document.createElement('div');
    allItem.className = `creator-item all-creators ${!state.selectedCreator ? 'active' : ''}`;
    allItem.innerHTML = `
        <span class="creator-name"><i class="fa-solid fa-layer-group"></i> All Creators</span>
        <span class="creator-badge">${state.creators.reduce((acc, c) => acc + Number(c.photo_count || 0), 0)}</span>
    `;
    allItem.addEventListener('click', () => {
        state.selectedCreator = null;
        state.creatorPanelOpen = false;
        elements.galleryTitle.textContent = 'All Photos';
        fetchPhotos();
        renderCreatorList();
        updateCreatorStylePanel();
    });
    elements.creatorList.appendChild(allItem);

    const classifySt = state.classifyStatus;
    const classifyRunning = !!(classifySt && classifySt.running);

    const filtered = state.creators.filter(c => !q || c.name.toLowerCase().includes(q));
    filtered.forEach(c => {
        const item = document.createElement('div');
        item.className = `creator-item ${state.selectedCreator === c.name ? 'active' : ''}`;
        // Lets the classify poll patch one row's pill instead of re-rendering
        // the sidebar (which would refetch creator style every 3 seconds).
        item.dataset.creator = c.name;
        item.innerHTML = `
            <span class="creator-name">@${escapeHtml(c.name)}${sourceMarkHtml(c)}${syncBadgeHtml(c)}</span>
            <span style="display:inline-flex;align-items:center;gap:6px;">
                ${rejectPillHtml(c, classifyRunning && classifySt.creator === c.name ? classifySt : null)}
                <span class="creator-badge">${escapeHtml(c.photo_count)}</span>
            </span>
        `;
        item.addEventListener('click', () => {
            // Same creator + panel open → collapse options (keep gallery filter)
            if (state.selectedCreator === c.name && state.creatorPanelOpen) {
                hideCreatorStylePanel();
                return;
            }
            const creatorChanged = state.selectedCreator !== c.name;
            state.selectedCreator = c.name;
            state.creatorPanelOpen = true;
            elements.galleryTitle.textContent = `@${c.name}`;
            if (creatorChanged) fetchPhotos();
            renderCreatorList();
            updateCreatorStylePanel();
        });
        elements.creatorList.appendChild(item);
    });
    updateCreatorStylePanel();
}

/**
 * Marker for a folder holding media from more than one platform.
 *
 * Only rendered when there is genuinely a mix — the common case is one source
 * per folder, and a badge on every row would be noise. The tooltip carries the
 * per-source breakdown, which is why `sources` stays unfiltered on the API.
 */
function sourceMarkHtml(creator) {
    const map = (creator && creator.sources) || {};
    const names = Object.keys(map);
    if (names.length < 2) return '';
    const detail = names
        .sort()
        .map((s) => `${s}: ${Number(map[s]) || 0}`)
        .join(' · ');
    return (
        `<span class="creator-source-mark" title="Multi-source — ${escapeHtml(detail)}">` +
        `<i class="fa-solid fa-code-branch"></i></span>`
    );
}

/**
 * Sidebar pill: live progress while this creator is being classified, then the
 * reject count if there is anything to clean up. Nothing at all when the pile
 * is empty — a "0" on every row is noise, and its absence is the signal.
 */
function rejectPillHtml(creator, runningStatus) {
    if (runningStatus) {
        const done = Number(runningStatus.completed) || 0;
        const total = Number(runningStatus.total) || 0;
        return `<span class="creator-reject-pill running" title="Classifying…">${done}/${total}</span>`;
    }
    const rejects = Number(creator.reject_count) || 0;
    if (!rejects) return '';
    // Full breakdown in the tooltip: the pill answers "is there cleanup here?",
    // the tooltip answers "is this creator worth opening?" — both from counters
    // already riding along on /api/creators, so no extra request.
    const parts = [
        `${rejects} reject${rejects === 1 ? '' : 's'} to review`,
        `${Number(creator.unusable_count) || 0} unusable · ${Number(creator.modest_count) || 0} modest`,
        `${Number(creator.keep_count) || 0} keep`,
    ];
    const todo = Number(creator.unclassified_count) || 0;
    if (todo) parts.push(`${todo} not classified`);
    const stale = Number(creator.stale_count) || 0;
    if (stale) parts.push(`${stale} outdated`);
    return `<span class="creator-reject-pill" title="${escapeHtml(parts.join('\n'))}">${rejects}</span>`;
}

function syncBadgeHtml(creator) {
    let html = '';
    const name = (creator.name || '').toLowerCase();
    const scrape = state.scrapeStatus;
    if (scrape) {
        const running = scrape.running_job;
        const pending = scrape.pending || [];
        if (running && (running.username || '').toLowerCase() === name) {
            html += ` <span class="creator-sync-pill" title="Syncing new posts">syncing</span>`;
        } else if (pending.some((j) => (j.username || '').toLowerCase() === name)) {
            html += ` <span class="creator-sync-pill queued" title="Queued for sync">queued</span>`;
        }
    }
    if (!creator.last_synced_at) return html;
    const label = formatRelativeTime(creator.last_synced_at);
    return `${html} <span class="sync-pill" title="Last synced ${escapeHtml(creator.last_synced_at)}">${escapeHtml(label)}</span>`;
}

function updateSyncLatestButtonUi() {
    if (!elements.syncLatestCreatorBtn) return;
    const creator = state.selectedCreator;
    const scrape = state.scrapeStatus;
    const running = scrape && scrape.running_job;
    const runningHere =
        !!(running && creator && (running.username || '').toLowerCase() === creator.toLowerCase());
    const pendingHere = !!(
        scrape &&
        creator &&
        (scrape.pending || []).some(
            (j) => (j.username || '').toLowerCase() === creator.toLowerCase()
        )
    );
    elements.syncLatestCreatorBtn.disabled = !creator;
    if (runningHere) {
        elements.syncLatestCreatorBtn.innerHTML =
            '<i class="fa-solid fa-spinner fa-spin"></i> Syncing…';
    } else if (pendingHere) {
        elements.syncLatestCreatorBtn.innerHTML =
            '<i class="fa-solid fa-clock"></i> Queued';
    } else {
        elements.syncLatestCreatorBtn.innerHTML =
            '<i class="fa-solid fa-arrows-rotate"></i> Sync new posts';
    }
}

function formatRelativeTime(iso) {
    try {
        const then = new Date(iso).getTime();
        if (Number.isNaN(then)) return 'synced';
        const diff = Date.now() - then;
        const mins = Math.floor(diff / 60000);
        if (mins < 60) return mins <= 1 ? 'just now' : `${mins}m`;
        const hours = Math.floor(mins / 60);
        if (hours < 48) return `${hours}h`;
        const days = Math.floor(hours / 24);
        if (days < 14) return `${days}d`;
        return new Date(iso).toLocaleDateString();
    } catch (err) {
        return 'synced';
    }
}

function selectedCreatorMeta() {
    if (!state.selectedCreator) return null;
    return state.creators.find((c) => c.name === state.selectedCreator) || null;
}

const VERDICT_COUNT_FIELDS = [
    'photo_count', 'keep_count', 'reject_count', 'unusable_count',
    'modest_count', 'unclassified_count', 'stale_count', 'error_count',
];

/**
 * Verdict counters for the current scope: one creator, or the whole archive
 * when none is selected. Summed client-side from /api/creators, which already
 * carries per-creator counters — an archive-wide endpoint would be a second
 * source of truth for the same numbers.
 */
/** Unclassified items across every creator — drives the navbar action. */
/**
 * Unclassified media across the whole archive.
 *
 * From /api/stats, not from state.creators: /api/creators is narrowed by the
 * active source filter, and Classify All is not. Summing the sidebar made the
 * button report a platform's backlog as the archive's, and disable itself
 * saying "every creator is already classified" while another platform's was
 * still pending. Falls back to the sidebar sum only before stats land.
 */
function archiveUnclassifiedTotal() {
    if (state.archiveUnclassified) return state.archiveUnclassified;
    return state.creators.reduce(
        (sum, c) => sum + (Number(c.unclassified_count) || 0), 0
    );
}

function scopedVerdictCounts() {
    const meta = selectedCreatorMeta();
    const source = meta ? [meta] : state.creators;
    const out = {};
    VERDICT_COUNT_FIELDS.forEach((field) => {
        out[field] = source.reduce((sum, c) => sum + (Number(c[field]) || 0), 0);
    });
    return out;
}


function hideCreatorStylePanel() {
    state.creatorPanelOpen = false;
    if (elements.creatorStylePanel) {
        elements.creatorStylePanel.style.display = 'none';
    }
}

function isDisplayFlex(el) {
    return !!(el && el.style.display === 'flex');
}

/** True when a blocking overlay/modal is open (skip outside-click dismiss for sidebar panel). */
function isBlockingOverlayOpen() {
    return (
        isDisplayFlex(elements.photoViewerOverlay) ||
        isDisplayFlex(elements.deleteConfirmModal) ||
        isDisplayFlex(elements.lightboxModal) ||
        isDisplayFlex(elements.syncModal) ||
        isDisplayFlex(elements.newCreatorModal) ||
        isDisplayFlex(elements.uploadModal) ||
        isDisplayFlex(elements.trashModal)
    );
}

async function updateCreatorStylePanel() {
    if (!elements.creatorStylePanel) return;
    const creator = state.selectedCreator;
    // Panel is dismissible chrome; gallery filter can stay while panel is closed
    if (!creator || !state.creatorPanelOpen) {
        elements.creatorStylePanel.style.display = 'none';
        return;
    }
    elements.creatorStylePanel.style.display = 'flex';
    updateSyncLatestButtonUi();
    updateClassifyPanelUi();
    elements.creatorStylePrefix.textContent = 'Loading…';
    elements.creatorStyleTerms.innerHTML = '';
    // Clicking through creators quickly would otherwise let a slow earlier
    // response overwrite the panel for the creator now selected.
    if (state.creatorStyleRequest) state.creatorStyleRequest.abort();
    const controller = new AbortController();
    state.creatorStyleRequest = controller;
    try {
        const res = await fetch(`/api/creator/style?creator=${encodeURIComponent(creator)}`, {
            signal: controller.signal
        });
        const data = await res.json();
        // Selection may have changed or panel closed while fetch was in flight
        if (state.selectedCreator !== creator || !state.creatorPanelOpen) return;
        if (data.style_prefix) {
            elements.creatorStylePrefix.textContent = data.style_prefix;
        } else {
            elements.creatorStylePrefix.textContent = data.exists
                ? 'Empty style prefix'
                : 'No style yet — analyze more photos, then Rebuild';
        }
        const terms = data.top_terms || [];
        elements.creatorStyleTerms.innerHTML = terms
            .map((t) => {
                const safe = escapeHtml(t);
                return `<button type="button" class="tag-pill tag-clickable" data-tag="${safe}">#${safe}</button>`;
            })
            .join('');
        elements.creatorStyleTerms.querySelectorAll('.tag-clickable').forEach((btn) => {
            btn.addEventListener('click', () => {
                const tag = btn.getAttribute('data-tag') || '';
                elements.searchInput.value = tag;
                state.searchQuery = tag;
                elements.clearSearch.style.display = 'block';
                fetchPhotos();
            });
        });
    } catch (err) {
        if (err.name === 'AbortError') return;
        if (state.selectedCreator === creator && state.creatorPanelOpen) {
            elements.creatorStylePrefix.textContent = 'Failed to load style';
        }
    } finally {
        if (state.creatorStyleRequest === controller) {
            state.creatorStyleRequest = null;
        }
    }
}

async function rebuildSelectedCreatorStyle() {
    if (!state.selectedCreator) return;
    try {
        const res = await fetch('/api/creator/style/rebuild', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ creator: state.selectedCreator })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            showToast(`Style rebuilt for @${state.selectedCreator}`);
            updateCreatorStylePanel();
        } else {
            showToast(data.message || 'Not enough prompts to rebuild style');
        }
    } catch (err) {
        showToast('Style rebuild failed');
    }
}

function promptStatusMeta(photo) {
    if (photo.prompt_stale) {
        return { cls: 'stale', label: 'Stale', icon: 'fa-clock-rotate-left' };
    }
    if (photo.has_prompt) {
        return { cls: 'ready', label: 'Ready', icon: 'fa-check' };
    }
    return { cls: 'missing', label: 'Missing', icon: 'fa-bolt' };
}

/**
 * Watch the "scroll for more" sentinel and page in when it comes near the
 * viewport. Replaces a scroll listener that measured `document.body
 * .offsetHeight` every frame — a forced layout on a document that only grows.
 *
 * The sentinel is destroyed and recreated by every render, so the observer is
 * created once and re-pointed rather than re-created (a new observer per page
 * would leak one per scroll).
 */
let loadMoreObserver = null;

function observeLoadMoreSentinel(sentinel) {
    if (!('IntersectionObserver' in window)) {
        // No polyfill: the sentinel is a real element, so clicking it still works.
        sentinel.addEventListener('click', () => loadMorePhotos());
        sentinel.style.cursor = 'pointer';
        return;
    }
    if (!loadMoreObserver) {
        loadMoreObserver = new IntersectionObserver((entries) => {
            if (entries.some((e) => e.isIntersecting)) loadMorePhotos();
        // 600px matches the old near-bottom threshold, so the next page still
        // starts loading before the user reaches the end.
        }, { rootMargin: '600px 0px' });
    }
    loadMoreObserver.disconnect();
    loadMoreObserver.observe(sentinel);
}

/**
 * Derive the tiles the grid draws from the flat `state.photos`.
 *
 * Grouped, the server returns a post's slides adjacent and in order, tagged
 * with a shared `group_key` — so one pass over the array is enough, and no
 * second list has to be kept in sync with it. Ungrouped, every photo is its
 * own tile and the rest of the renderer never learns the difference.
 */
function rebuildGalleryTiles() {
    const tiles = [];
    state.photos.forEach((photo, index) => {
        const key = photo.group_key || null;
        const previous = tiles[tiles.length - 1];
        if (key && previous && previous.key === key) {
            previous.count += 1;
            return;
        }
        tiles.push({ key, index, count: 1 });
    });
    state.galleryTiles = tiles;
    return tiles;
}

/** Label under the grid — posts and files are different units, so say which. */
function galleryCountLabel() {
    const shown = state.galleryTiles.length;
    const total = state.photoTotal ? ` / ${state.photoTotal}` : '';
    if (!state.groupPosts) return `${shown}${total} photos`;
    return `${shown}${total} posts · ${state.photos.length} photos`;
}

function renderGallery({ append = false, fromIndex = 0 } = {}) {
    if (!append) {
        elements.galleryGrid.innerHTML = '';
    }
    // Always derived, never maintained. The scan is left to right, so the
    // leading tiles are stable and an append can still slice from fromIndex.
    rebuildGalleryTiles();
    elements.galleryCount.textContent = galleryCountLabel();

    if (state.photos.length === 0) {
        elements.emptyState.style.display = 'flex';
        updateBulkBar();
        return;
    }

    elements.emptyState.style.display = 'none';

    const sliceStart = append ? fromIndex : 0;
    const toRender = state.galleryTiles.slice(sliceStart);

    toRender.forEach((tile) => {
        const index = tile.index;
        const p = state.photos[index];
        if (!p) return;
        const card = document.createElement('div');
        const selected = state.selectedPaths.has(p.rel_path);
        // Reject cards are tinted and desaturated so the grid scans without
        // reading a single badge — only in review mode, where that is the job.
        const verdictCls = verdictCardClass(p);
        card.className = `photo-card${state.selectMode ? ' select-mode' : ''}${selected ? ' selected' : ''}${p.favorite ? ' is-favorite' : ''}${verdictCls}`;
        card.dataset.relPath = p.rel_path;
        const imgSrc = p.thumb_url || p.url;
        const status = promptStatusMeta(p);
        const favMark = p.favorite
            ? '<span class="card-fav-mark" title="Favorite"><i class="fa-solid fa-star"></i></span>'
            : '';
        // Single source of truth for video detection (was a divergent inline list)
        const isVideo = isVideoFilename(p.filename);
        const videoBadge = isVideo ? '<div class="video-badge"><i class="fa-solid fa-play"></i></div>' : '';
        const bottomHint = isVideo
            ? `<div class="photo-card-prompt-hint"><i class="fa-solid fa-clapperboard"></i> Click for reel details</div>`
            : `<div class="photo-card-prompt-hint"><i class="fa-solid fa-wand-magic-sparkles"></i> Click for AI Prompt</div>`;
        const topBadge = isVideo
            ? `<span class="prompt-status-badge ready" title="Reel"><i class="fa-solid fa-film"></i> Reel</span>`
            : `<span class="prompt-status-badge ${status.cls}"><i class="fa-solid ${status.icon}"></i> ${status.label}</span>`;
        // Count comes from the tile (what is on screen), not p.group_count
        // (what the server found) — a deleted slide has to change the badge.
        const groupBadge = tile.count > 1
            ? `<span class="group-count-badge" title="${Number(tile.count)} slides in this post"><i class="fa-solid fa-layer-group"></i> ${Number(tile.count)}</span>`
            : '';
        card.innerHTML = `
            <img src="${escapeHtml(imgSrc)}" alt="${escapeHtml(p.filename)}" loading="lazy" data-full="${escapeHtml(p.url)}">
            ${videoBadge}
            ${groupBadge}
            <div class="photo-card-overlay">
                <div class="overlay-top-actions">
                    ${state.selectMode
                        ? `<label class="card-select-wrap" title="Select"><input type="checkbox" class="card-select-cb" ${selected ? 'checked' : ''}></label>`
                        : topBadge}
                    ${favMark}
                    <button class="card-trash-btn" title="Delete Photo"><i class="fa-solid fa-trash-can"></i></button>
                </div>
                <div class="overlay-bottom-info">
                    <div class="photo-card-creator">@${escapeHtml(p.creator)}</div>
                    ${state.selectMode
                        ? topBadge
                        : bottomHint}
                </div>
            </div>
            ${verdictBadgeHtml(p)}
        `;

        card.querySelector('.card-trash-btn').addEventListener('click', (e) => {
            e.stopPropagation();
            promptDeletePhoto(p);
        });

        const checkbox = card.querySelector('.card-select-cb');
        if (checkbox) {
            checkbox.addEventListener('click', (e) => e.stopPropagation());
            checkbox.addEventListener('change', (e) => {
                togglePhotoSelection(p.rel_path, e.target.checked);
                card.classList.toggle('selected', e.target.checked);
            });
        }

        card.addEventListener('click', () => {
            if (state.selectMode) {
                const next = !state.selectedPaths.has(p.rel_path);
                togglePhotoSelection(p.rel_path, next);
                card.classList.toggle('selected', next);
                const cb = card.querySelector('.card-select-cb');
                if (cb) cb.checked = next;
            } else {
                // Resolved at click time, not captured: removePhotosFromView()
                // shifts state.photos under cards it does not re-render, so a
                // closed-over index opens the wrong photo after a delete.
                const at = state.photos.findIndex((x) => x.rel_path === p.rel_path);
                openLightbox(at >= 0 ? at : index);
            }
        });
        elements.galleryGrid.appendChild(card);
    });

    let sentinel = document.getElementById('galleryLoadMore');
    if (sentinel) sentinel.remove();
    if (state.photoHasMore) {
        sentinel = document.createElement('div');
        sentinel.id = 'galleryLoadMore';
        sentinel.className = 'gallery-load-more';
        sentinel.textContent = state.photosLoading ? 'Loading…' : 'Scroll for more';
        elements.galleryGrid.appendChild(sentinel);
        observeLoadMoreSentinel(sentinel);
    }
    updateBulkBar();
}

function populateUploadCreators() {
    elements.uploadCreatorSelect.innerHTML = '';
    state.creators.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.name;
        opt.textContent = `@${c.name}`;
        elements.uploadCreatorSelect.appendChild(opt);
    });
}

const SEARCH_DEBOUNCE_MS = 250;
const CREATOR_FILTER_DEBOUNCE_MS = 120;

const VIDEO_SEEK_SECONDS = 5;
const VIDEO_SEEK_HOLD_SECONDS = 2;

function isVideoFilename(name) {
    const f = String(name || '').toLowerCase();
    return f.endsWith('.mp4') || f.endsWith('.webm') || f.endsWith('.mov');
}

function isVideoPhoto(photo) {
    return !!(photo && isVideoFilename(photo.filename));
}

function getInspectorMediaEl() {
    return elements.lightboxVideo
        ? elements.lightboxVideo.closest('.inspector-media')
        : document.querySelector('.inspector-media');
}

function setLightboxMediaMode(isVideo) {
    const media = getInspectorMediaEl();
    if (media) media.classList.toggle('is-video', !!isVideo);
}

function resolveMediaUrl(url) {
    if (!url) return '';
    try {
        return new URL(url, window.location.href).href;
    } catch (_) {
        return String(url);
    }
}

function isLightboxVideoActive() {
    const v = elements.lightboxVideo;
    return !!(v && v.style.display !== 'none' && (v.currentSrc || v.src));
}

function isFullscreenVideoActive() {
    return !!(
        state.videoInFullscreenShell &&
        isDisplayFlex(elements.photoViewerOverlay) &&
        elements.photoViewerOverlay.classList.contains('is-video')
    );
}

/** Single active player element (lightbox video is always the decoder). */
function activeVideoEl() {
    if (!elements.lightboxVideo) return null;
    if (isFullscreenVideoActive() || isLightboxVideoActive()) return elements.lightboxVideo;
    return null;
}

function setVideoBuffering(show) {
    if (!elements.videoBuffering) return;
    elements.videoBuffering.classList.toggle('show', !!show);
    elements.videoBuffering.setAttribute('aria-hidden', show ? 'false' : 'true');
}

function flashSeekHud(deltaSec) {
    const hud = elements.videoSeekHud;
    if (!hud) return;
    const sign = deltaSec > 0 ? '+' : '';
    hud.textContent = `${sign}${deltaSec}s`;
    hud.classList.add('show');
    hud.setAttribute('aria-hidden', 'false');
    if (state.videoSeekHudTimer) clearTimeout(state.videoSeekHudTimer);
    state.videoSeekHudTimer = setTimeout(() => {
        hud.classList.remove('show');
        hud.setAttribute('aria-hidden', 'true');
    }, 450);
}

/** Best-effort media duration (NaN/Infinity common until metadata; use seekable). */
function getMediaDuration(video) {
    if (!video) return NaN;
    const d = Number(video.duration);
    if (Number.isFinite(d) && d > 0) return d;
    try {
        if (video.seekable && video.seekable.length > 0) {
            const end = video.seekable.end(video.seekable.length - 1);
            if (Number.isFinite(end) && end > 0) return end;
        }
    } catch (_) { /* ignore */ }
    return NaN;
}

/**
 * Seek media to an absolute time. Avoids fastSeek (often inaccurate / no-op).
 * Requires HTTP Range on the server for Chrome to honor currentTime jumps.
 */
function seekMediaTo(video, timeSec) {
    if (!video) return false;
    const dur = getMediaDuration(video);
    let t = Number(timeSec);
    if (!Number.isFinite(t)) return false;
    if (Number.isFinite(dur) && dur > 0) {
        t = Math.max(0, Math.min(dur, t));
    } else {
        t = Math.max(0, t);
    }
    try {
        // Clamp into seekable range when available (Chrome is picky)
        if (video.seekable && video.seekable.length > 0) {
            const s0 = video.seekable.start(0);
            const s1 = video.seekable.end(video.seekable.length - 1);
            if (Number.isFinite(s0) && Number.isFinite(s1) && s1 > s0) {
                t = Math.max(s0, Math.min(s1, t));
            }
        }
        video.currentTime = t;
        return true;
    } catch (_) {
        return false;
    }
}

function seekVideoElement(video, deltaSec, { hud = true } = {}) {
    if (!video) return;
    const cur = Number(video.currentTime) || 0;
    seekMediaTo(video, cur + deltaSec);
    if (hud) flashSeekHud(deltaSec);
    if (state.videoInFullscreenShell) updateFsSeekUi();
}

function ensureVideoAudible(video) {
    if (!video) return;
    // User gesture path — unmute for a real player feel
    if (video.muted) {
        video.muted = false;
    }
    if (video.volume === 0) video.volume = 1;
}

function toggleVideoPlayback(video) {
    if (!video) return;
    if (video.paused) {
        ensureVideoAudible(video);
        video.play().catch(() => {});
    } else {
        video.pause();
    }
}

/**
 * Load/play without double-decode jank:
 * - skip reload if same URL already buffered
 * - wait for canplay (no play() race right after src=)
 * - cancel stale loads when user navigates quickly
 */
function loadAndPlayLightboxVideo(url) {
    const v = elements.lightboxVideo;
    if (!v || !url) return;
    const token = ++state.videoLoadToken;
    const abs = resolveMediaUrl(url);

    elements.lightboxImg.style.display = 'none';
    v.style.display = 'block';
    setLightboxMediaMode(true);
    v.preload = 'auto';

    const already =
        (v.currentSrc && v.currentSrc === abs) ||
        (v.src && v.src === abs);

    const tryPlay = () => {
        if (token !== state.videoLoadToken) return;
        setVideoBuffering(false);
        v.play().catch(() => {
            // Autoplay with sound blocked — keep muted and retry once
            if (!v.muted) {
                v.muted = true;
                v.play().catch(() => {});
            }
        });
    };

    if (already && v.readyState >= 2) {
        setVideoBuffering(false);
        tryPlay();
        return;
    }

    setVideoBuffering(true);
    v.pause();
    if (!already) {
        v.src = url;
        // Do NOT call load() after setting src — browsers load automatically;
        // an extra load() causes a visible restart stutter.
    }

    if (v.readyState >= 3) {
        tryPlay();
    } else {
        const onCanPlay = () => {
            if (token !== state.videoLoadToken) return;
            tryPlay();
        };
        v.addEventListener('canplay', onCanPlay, { once: true });
        // Fallback if canplay already fired between checks
        if (v.readyState >= 3) onCanPlay();
    }
}

function stopAndClearLightboxVideo() {
    const v = elements.lightboxVideo;
    if (!v) return;
    state.videoLoadToken += 1; // invalidate pending canplay handlers
    setVideoBuffering(false);
    v.pause();
    v.removeAttribute('src');
    try {
        v.load();
    } catch (_) { /* ignore */ }
    v.style.display = 'none';
    setLightboxMediaMode(false);
}

function restoreLightboxVideoHome() {
    const v = elements.lightboxVideo;
    if (!v) return;
    v.classList.remove('fs-active-video');
    // Preview always uses native controls
    v.controls = true;
    const home = elements.lightboxMediaPane;
    if (home && v.parentElement !== home) {
        // Keep expand button after the video
        const expand = elements.videoExpandBtn;
        if (expand && expand.parentElement === home) {
            home.insertBefore(v, expand);
        } else {
            home.appendChild(v);
        }
    }
    state.videoInFullscreenShell = false;
}

function wireLightboxVideoEvents() {
    const v = elements.lightboxVideo;
    if (!v || v.dataset.wired === '1') return;
    v.dataset.wired = '1';

    const showBuf = () => {
        if (isLightboxVideoActive() || isFullscreenVideoActive()) setVideoBuffering(true);
    };
    const hideBuf = () => setVideoBuffering(false);

    v.addEventListener('waiting', showBuf);
    v.addEventListener('stalled', showBuf);
    v.addEventListener('seeking', () => {
        // Only show spinner if seek is slow (browser will fire seeked quickly when buffered)
        if (v.readyState < 3) showBuf();
    });
    v.addEventListener('seeked', hideBuf);
    v.addEventListener('playing', hideBuf);
    v.addEventListener('canplay', hideBuf);
    v.addEventListener('error', hideBuf);
}

// Lightbox
/**
 * "3 / 11" while walking a carousel, hidden otherwise.
 *
 * Without it, `→` inside a post is indistinguishable from `→` to the next
 * post — the grid said "11 slides" and then gave no sign of where you are.
 */
function renderSlideCounter(photo) {
    const el = elements.lightboxSlideCount;
    if (!el) return;
    const count = Number(photo && photo.group_count) || 1;
    if (!state.groupPosts || count < 2) {
        el.style.display = 'none';
        el.textContent = '';
        return;
    }
    el.textContent = `${(Number(photo.group_index) || 0) + 1} / ${count}`;
    el.style.display = '';
}

function openLightbox(index) {
    if (index < 0 || index >= state.photos.length) return;

    // Leave fullscreen shell cleanly before swapping media
    if (state.videoInFullscreenShell) {
        state.fsScrubbing = false;
        if (elements.lightboxVideo) elements.lightboxVideo.controls = true;
        restoreLightboxVideoHome();
        if (elements.fsVideoLayout) {
            elements.fsVideoLayout.hidden = true;
            elements.fsVideoLayout.style.display = 'none';
        }
        if (elements.photoViewerOverlay) {
            elements.photoViewerOverlay.classList.remove('is-video');
            elements.photoViewerOverlay.style.display = 'none';
        }
    }

    state.lightboxIndex = index;
    const photo = state.photos[index];

    const isVideo = isVideoPhoto(photo);
    if (isVideo) {
        loadAndPlayLightboxVideo(photo.url);
    } else {
        stopAndClearLightboxVideo();
        elements.lightboxImg.style.display = 'block';
        elements.lightboxImg.src = photo.url;
    }

    elements.lightboxCreator.textContent = `@${photo.creator}`;
    elements.lightboxFilename.textContent = photo.filename;
    renderSlideCounter(photo);

    resetPromptPanel();
    updateFavoriteButton(photo);
    // Triage sits above both inspector modes, so photos and reels adjudicate
    // the same way. resetPromptPanel() runs first and does not touch it.
    renderTriageBlock(photo);
    state.compareMode = false;
    setCompareMode(false);
    elements.lightboxModal.style.display = 'flex';
    
    // Always load the media details for both photos and videos
    loadMediaDetailPanel(photo);

    if (isVideo) {
        // Skip Comfy generations / prompt auto-load for videos
        if (elements.compareToggleBtn) elements.compareToggleBtn.style.display = 'none';
        state.currentGenerations = [];
    } else {
        loadGenerationsForPhoto(photo.rel_path);
        // Auto-load Ready cached prompts for stills only
        if (photo.has_prompt && !photo.prompt_stale) {
            handleGeneratePrompt(false);
        }
    }
}

async function loadGenerationsForPhoto(relPath) {
    state.currentGenerations = [];
    if (elements.compareToggleBtn) elements.compareToggleBtn.style.display = 'none';
    if (elements.lightboxGenImg) elements.lightboxGenImg.src = '';
    setCurrentGeneration(null);
    try {
        const res = await fetch(`/api/generations?path=${encodeURIComponent(relPath)}`);
        const data = await res.json();
        const gens = data.generations || [];
        state.currentGenerations = gens;
        const primary = gens[0] && (gens[0].primary_url || (gens[0].files && gens[0].files[0] && gens[0].files[0].url));
        if (primary && elements.lightboxGenImg) {
            elements.lightboxGenImg.src = primary;
            if (elements.compareToggleBtn) {
                elements.compareToggleBtn.style.display = 'inline-flex';
            }
        }
        setCurrentGeneration(gens[0] || null);
    } catch (err) {
        console.error('Generations load failed', err);
    }
}

// ── generation rating (A3) ───────────────────────────────────────────
// The rating is the only judgement the generation loop captures, so pressing
// it has to be cheap: one ordinal, four keys, no dialog.

function setCurrentGeneration(gen) {
    const file = gen && gen.files && gen.files[0];
    state.currentGenId = (gen && gen.gen_id) || (file && file.gen_id) || null;
    const raw = gen && gen.rating !== undefined ? gen.rating : (file && file.rating);
    state.currentGenRating = Number(raw) || 0;
    renderGenRating();
}

function renderGenRating() {
    if (!elements.genRating) return;
    const enabled = Boolean(state.currentGenId);
    elements.genRating.style.display = enabled ? 'flex' : 'none';
    elements.genRating.classList.toggle('has-rating', enabled && state.currentGenRating !== 0);
    elements.genRating.querySelectorAll('.gen-rate-btn').forEach((btn) => {
        const value = Number(btn.dataset.rating);
        btn.classList.toggle('active', enabled && value === state.currentGenRating);
        btn.disabled = !enabled;
    });
}

async function rateCurrentGeneration(rating) {
    const genId = state.currentGenId;
    if (!genId) return;
    const previous = state.currentGenRating;
    // Optimistic: the control is meant to feel like a keypress, and a failed
    // rating rolls back rather than leaving the UI ahead of the store.
    state.currentGenRating = rating;
    renderGenRating();
    // Nothing may be applied if the user has moved on. Two quick presses put
    // two writes in flight, and a late reply from the first must not overwrite
    // the second — the same stale-response rule the gallery fetches follow,
    // just without an AbortController, since the write has already happened
    // server-side and cancelling the read would not undo it.
    const superseded = () =>
        state.currentGenId !== genId || state.currentGenRating !== rating;
    try {
        const res = await fetch('/api/generation/rate', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ gen_id: genId, rating }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        if (superseded()) return;
        const gen = state.currentGenerations[0];
        if (gen && (gen.gen_id === genId || !gen.gen_id)) {
            gen.rating = rating;
            if (gen.files && gen.files[0]) gen.files[0].rating = rating;
        }
    } catch (err) {
        console.error('Rating failed', err);
        if (superseded()) return;
        state.currentGenRating = previous;
        renderGenRating();
        showToast('Could not save rating');
    }
}

function handleGenerationRatingKey(e) {
    // Only while the generated pane is actually on screen — otherwise these
    // four keys would be dead weight over the whole lightbox.
    if (!state.compareMode || !state.currentGenId) return false;
    const map = { '1': 1, '2': 2, '0': 0, x: -1 };
    const rating = map[e.key.toLowerCase()];
    if (rating === undefined) return false;
    rateCurrentGeneration(rating);
    return true;
}

// ── outputs gallery (A1) ─────────────────────────────────────────────
// A sibling view rather than a modal: it is a gallery in its own right, with
// its own scroll position, filters and paging. Switching views deliberately
// does not reset the other one.

const OUTPUTS_PAGE = 60;
let outputsObserver = null;

function outputsFilters() {
    const rating = elements.outputsRating ? elements.outputsRating.value : '';
    const params = new URLSearchParams();
    params.set('limit', String(OUTPUTS_PAGE));
    params.set('offset', String(state.outputsOffset));
    params.set('sort', elements.outputsSort ? elements.outputsSort.value : 'newest');
    // "rated" is a different question from any single rating value, so it maps
    // to its own parameter rather than a magic rating number.
    if (rating === 'rated') params.set('rated_only', '1');
    else if (rating !== '') params.set('rating', rating);
    for (const [key, el] of [
        ['workflow', elements.outputsWorkflow],
        ['checkpoint', elements.outputsCheckpoint],
        ['creator', elements.outputsCreator],
    ]) {
        if (el && el.value) params.set(key, el.value);
    }
    if (state.outputsBatch) params.set('batch_id', state.outputsBatch);
    return params;
}

/**
 * Show one batch's outputs as a contact sheet.
 *
 * The endpoint has filtered on `batch_id` since A1; what was missing was any
 * way to reach it. A completion toast that lands you in an unfiltered grid of
 * every output ever made is not a result — you still have to go find the
 * fifty images you just waited for.
 */
function openBatchContactSheet(batchId) {
    if (!batchId) return;
    state.outputsBatch = String(batchId);
    renderOutputsBatchChip();
    showOutputsView(true);
}

function clearOutputsBatch() {
    state.outputsBatch = null;
    renderOutputsBatchChip();
    fetchOutputs();
}

function renderOutputsBatchChip() {
    const chip = elements.outputsBatchChip;
    if (!chip) return;
    chip.style.display = state.outputsBatch ? '' : 'none';
    if (elements.outputsBatchLabel) {
        // textContent, not innerHTML: the id is server-generated hex today, but
        // it arrives from a response body like everything else (rule 7).
        elements.outputsBatchLabel.textContent = state.outputsBatch
            ? `run ${state.outputsBatch}`
            : '';
    }
}

async function fetchOutputs({ append = false } = {}) {
    if (!append) state.outputsOffset = 0;
    if (state.outputsRequest) state.outputsRequest.abort();
    const controller = new AbortController();
    state.outputsRequest = controller;
    try {
        const res = await fetch(`/api/generations/list?${outputsFilters()}`, {
            signal: controller.signal,
        });
        const data = await res.json();
        const rows = data.generations || [];
        state.outputs = append ? state.outputs.concat(rows) : rows;
        // Offset commits only on a response, so an aborted page cannot corrupt
        // paging — the same rule fetchPhotos follows.
        state.outputsOffset = state.outputs.length;
        state.outputsHasMore = Boolean(data.has_more);
        state.outputsTotal = Number(data.total) || 0;
        populateOutputFacets(data.facets || {});
        renderOutputs({ append });
    } catch (err) {
        if (err.name === 'AbortError') return;
        console.error('Outputs load failed', err);
    } finally {
        if (state.outputsRequest === controller) state.outputsRequest = null;
    }
}

function populateOutputFacets(facets) {
    for (const [key, el] of [
        ['workflows', elements.outputsWorkflow],
        ['checkpoints', elements.outputsCheckpoint],
        ['creators', elements.outputsCreator],
    ]) {
        const values = facets[key] || [];
        if (!el) continue;
        // Preserve the active choice across refreshes; rebuilding the list
        // under the user mid-filter would silently reset it.
        const current = el.value;
        const first = el.options[0];
        el.innerHTML = '';
        el.appendChild(first);
        values.forEach((v) => {
            const opt = document.createElement('option');
            opt.value = v;
            opt.textContent = v;
            el.appendChild(opt);
        });
        if (values.includes(current)) el.value = current;
    }
}

function renderOutputs({ append = false } = {}) {
    const grid = elements.outputsGrid;
    if (!grid) return;
    if (!append) grid.innerHTML = '';
    const start = append ? grid.querySelectorAll('.output-card').length : 0;
    const frag = document.createDocumentFragment();
    state.outputs.slice(start).forEach((gen) => frag.appendChild(outputCard(gen)));
    grid.appendChild(frag);

    if (elements.outputsCount) {
        elements.outputsCount.textContent = `${state.outputsTotal} item${state.outputsTotal === 1 ? '' : 's'}`;
    }
    if (elements.outputsEmpty) {
        elements.outputsEmpty.style.display = state.outputs.length ? 'none' : 'flex';
    }
    attachOutputsSentinel();
}

function outputCard(gen) {
    const card = document.createElement('div');
    card.className = 'photo-card output-card';
    card.dataset.genId = gen.gen_id;
    const rating = Number(gen.rating) || 0;
    // Third-party text: the creator handle and the prompt both originate
    // outside this app, so neither reaches innerHTML unescaped.
    card.innerHTML = `
        <div class="photo-thumb-wrap">
            <img class="photo-thumb" loading="lazy" src="${escapeHtml(gen.thumb_url)}" alt="">
            ${rating ? `<span class="output-rating-badge r${rating}">${ratingGlyph(rating)}</span>` : ''}
            ${gen.seed_recorded ? '' : '<span class="output-flag" title="Seed was never recorded — cannot be reproduced">no seed</span>'}
        </div>
        <div class="photo-meta">
            <span class="photo-creator">@${escapeHtml(gen.creator || '')}</span>
            <span class="photo-filename">${escapeHtml(gen.workflow || '')}</span>
        </div>`;
    card.addEventListener('click', () => openOutputDetail(gen.gen_id));
    return card;
}

function ratingGlyph(rating) {
    return { '-1': '✕', 1: '✓', 2: '★' }[String(rating)] || '';
}

function attachOutputsSentinel() {
    const grid = elements.outputsGrid;
    if (!grid) return;
    const existing = grid.querySelector('#outputsLoadMore');
    if (existing) existing.remove();
    if (!state.outputsHasMore) return;
    // Same element and class the photo grid uses, so it inherits the existing
    // "Scroll for more" styling rather than needing a parallel rule.
    const sentinel = document.createElement('div');
    sentinel.id = 'outputsLoadMore';
    sentinel.className = 'gallery-load-more';
    sentinel.textContent = 'Scroll for more';
    grid.appendChild(sentinel);
    if (!('IntersectionObserver' in window)) {
        sentinel.style.cursor = 'pointer';
        sentinel.addEventListener('click', () => fetchOutputs({ append: true }));
        return;
    }
    if (!outputsObserver) {
        outputsObserver = new IntersectionObserver((entries) => {
            if (entries.some((e) => e.isIntersecting)) fetchOutputs({ append: true });
        }, { rootMargin: '600px 0px' });
    }
    outputsObserver.disconnect();
    outputsObserver.observe(sentinel);
}

function showOutputsView(on) {
    // Outputs and review mode are mutually exclusive: review mode's strip and
    // its `body.review-mode` rules belong to the photo gallery, which is
    // hidden here. Leaving both on gives review mode no surface and blanks the
    // outputs filter bar, since both use `.view-controls`.
    if (on && state.reviewMode) exitReviewMode();
    state.outputsView = on;
    const gallery = document.querySelector('.gallery-container:not(.outputs-container)');
    if (gallery) gallery.style.display = on ? 'none' : 'flex';
    if (elements.outputsView) elements.outputsView.style.display = on ? 'flex' : 'none';
    if (elements.outputsBtn) elements.outputsBtn.classList.toggle('active', on);
    if (on) {
        // Always refetch on open, not only when empty. The gesture this view
        // exists for is "generate something, then go look at it" — a cached
        // grid would be missing exactly the output you came to see, and the
        // keep-rate badge beside it refreshes regardless, so a stale grid next
        // to a fresh number is worse than both being stale.
        fetchOutputs();
        refreshOutputsKeepRate();
    }
}

async function refreshOutputsKeepRate() {
    if (!elements.outputsKeepRate) return;
    try {
        const res = await fetch('/api/insights');
        const data = await res.json();
        const g = (data && data.generations) || {};
        elements.outputsKeepRate.textContent = g.keep_rate === null || g.keep_rate === undefined
            ? 'unrated'
            : `keep ${Math.round(g.keep_rate * 100)}% of ${g.rated}`;
    } catch (err) {
        console.error('keep rate load failed', err);
    }
}

function setupOutputsListeners() {
    if (elements.outputsBtn) {
        elements.outputsBtn.addEventListener('click', () => showOutputsView(!state.outputsView));
    }
    [elements.outputsSort, elements.outputsRating, elements.outputsWorkflow,
     elements.outputsCheckpoint, elements.outputsCreator].forEach((el) => {
        if (el) el.addEventListener('change', () => fetchOutputs());
    });
}

// ── output detail (A1) ───────────────────────────────────────────────

function openOutputDetail(genId) {
    const gen = state.outputs.find((g) => g.gen_id === genId);
    if (!gen || !elements.outputDetailModal) return;
    state.outputDetail = gen;

    elements.outputDetailImage.src = gen.url;
    // The source may have been deleted since; /media 404s and the browser
    // shows a broken pane, which is honest — the provenance panel still says
    // which file it was.
    elements.outputDetailSource.src = '/media/' + gen.source_rel
        .split('/').map(encodeURIComponent).join('/');
    elements.outputDetailPositive.textContent = gen.positive_prompt || '';
    elements.outputDetailNegative.textContent = gen.negative_prompt || '';

    const rows = [
        ['Source', gen.source_rel],
        ['Created', gen.created_at],
        ['Workflow', gen.workflow],
        ['Checkpoint', gen.checkpoint || '—'],
        ['Seed', gen.seed_recorded ? String(gen.seed) : 'not recorded'],
        ['Steps', gen.steps],
        ['CFG', gen.cfg],
        ['Denoise', gen.denoise === null ? '—' : gen.denoise],
        ['Mode E', gen.mode_e ? 'on' : 'off'],
        ['Prompt engine', gen.prompt_version || '—'],
        ['Batch', gen.batch_id || '—'],
    ];
    elements.outputDetailMeta.innerHTML = rows
        .map(([k, v]) => `<div class="output-meta-row"><span>${escapeHtml(k)}</span>` +
            `<strong>${escapeHtml(String(v === null || v === undefined ? '—' : v))}</strong></div>`)
        .join('');

    // Legacy rows imported from the pre-A0 JSON index never had their seed
    // written down. Offering "same seed" there would be a button that cannot
    // do what it says, so it is disabled and says why.
    if (elements.outputRegenSameSeed) {
        elements.outputRegenSameSeed.disabled = !gen.seed_recorded;
        elements.outputRegenSameSeed.title = gen.seed_recorded
            ? `Re-run with seed ${gen.seed}`
            : 'This generation predates seed recording and cannot be reproduced';
    }
    elements.outputDetailModal.style.display = 'flex';
}

function closeOutputDetail() {
    if (elements.outputDetailModal) elements.outputDetailModal.style.display = 'none';
    state.outputDetail = null;
}

function copyOutputParams() {
    const gen = state.outputDetail;
    if (!gen) return;
    const text = [
        `positive: ${gen.positive_prompt || ''}`,
        `negative: ${gen.negative_prompt || ''}`,
        `workflow: ${gen.workflow}`,
        `checkpoint: ${gen.checkpoint || ''}`,
        `seed: ${gen.seed_recorded ? gen.seed : 'not recorded'}`,
        `steps: ${gen.steps}  cfg: ${gen.cfg}  denoise: ${gen.denoise ?? ''}`,
        `mode_e: ${gen.mode_e ? 'on' : 'off'}`,
    ].join('\n');
    copyToClipboard(text, 'Copied parameters');
}

async function regenerateOutput({ sameSeed }) {
    const gen = state.outputDetail;
    if (!gen) return;
    if (sameSeed && !gen.seed_recorded) return;
    try {
        const res = await fetch('/api/comfy/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                path: gen.source_rel,
                workflow: gen.workflow,
                positive_prompt: gen.positive_prompt,
                negative_prompt: gen.negative_prompt,
                checkpoint: gen.checkpoint || undefined,
                steps: gen.steps,
                cfg_scale: gen.cfg,
                denoise: gen.denoise,
                // Omitted entirely for a new roll — sending null would be
                // indistinguishable from "no opinion" and is what the server
                // already treats as unpinned.
                seed: sameSeed ? gen.seed : undefined,
                use_mode_e: gen.mode_e,
            }),
        });
        const data = await res.json();
        if (res.status === 409) {
            showToast(data.message || 'ComfyUI is busy');
            return;
        }
        if (!res.ok) throw new Error(data.message || `HTTP ${res.status}`);
        showToast(sameSeed ? `Re-running seed ${gen.seed}` : 'Re-running with a new seed');
        closeOutputDetail();
        // pollComfyStatus is self-arming — one call re-creates its own interval
        // while a job is running and clears it when there is not.
        pollComfyStatus();
    } catch (err) {
        console.error('Regenerate failed', err);
        showToast('Could not start the generation');
    }
}

async function deleteOutput() {
    const gen = state.outputDetail;
    if (!gen) return;
    // Permanent, and the copy says so — this is the one delete in the app that
    // does not route through _trash, because the row can rebuild the image.
    const ok = window.confirm(
        'Delete this generation permanently?\n\n'
        + 'Generations are not moved to Trash — the prompt, seed and checkpoint '
        + 'are all recorded, so it can be generated again.'
    );
    if (!ok) return;
    try {
        const res = await fetch(`/api/generation?gen_id=${encodeURIComponent(gen.gen_id)}`, {
            method: 'DELETE',
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        state.outputs = state.outputs.filter((g) => g.gen_id !== gen.gen_id);
        state.outputsTotal = Math.max(0, state.outputsTotal - 1);
        // Offset tracks how many rows have been consumed from the server, so it
        // has to come down with them — otherwise the next page starts one row
        // late and silently skips a generation. Same rule as
        // removePhotosFromView in the photo gallery.
        state.outputsOffset = Math.max(0, state.outputsOffset - 1);
        // Optimistic removal, like the photo gallery: no full refetch, so the
        // scroll position and loaded pages survive.
        const card = elements.outputsGrid
            && elements.outputsGrid.querySelector(`[data-gen-id="${CSS.escape(gen.gen_id)}"]`);
        if (card) card.remove();
        if (elements.outputsCount) {
            elements.outputsCount.textContent = `${state.outputsTotal} item${state.outputsTotal === 1 ? '' : 's'}`;
        }
        if (elements.outputsEmpty && !state.outputs.length) {
            elements.outputsEmpty.style.display = 'flex';
        }
        closeOutputDetail();
        showToast('Generation deleted');
        refreshOutputsKeepRate();
    } catch (err) {
        console.error('Delete failed', err);
        showToast('Could not delete the generation');
    }
}

function setupOutputDetailListeners() {
    if (elements.closeOutputDetail) {
        elements.closeOutputDetail.addEventListener('click', closeOutputDetail);
    }
    // Clicking the backdrop closes, like every other dialog here.
    const overlay = document.getElementById('outputDetailOverlay');
    if (overlay) overlay.addEventListener('click', closeOutputDetail);
    if (elements.outputCopyParams) {
        elements.outputCopyParams.addEventListener('click', copyOutputParams);
    }
    if (elements.outputRegenSameSeed) {
        elements.outputRegenSameSeed.addEventListener('click', () => regenerateOutput({ sameSeed: true }));
    }
    if (elements.outputRegenNewSeed) {
        elements.outputRegenNewSeed.addEventListener('click', () => regenerateOutput({ sameSeed: false }));
    }
    if (elements.outputDelete) {
        elements.outputDelete.addEventListener('click', deleteOutput);
    }
}

function setupGenRatingListeners() {
    if (!elements.genRating) return;
    elements.genRating.addEventListener('click', (e) => {
        const btn = e.target.closest('.gen-rate-btn');
        if (!btn || btn.disabled) return;
        rateCurrentGeneration(Number(btn.dataset.rating));
    });
}

function setCompareMode(on) {
    state.compareMode = on;
    if (elements.mediaCompare) {
        elements.mediaCompare.classList.toggle('compare-on', on);
    }
    if (elements.generatedPane) {
        elements.generatedPane.style.display = on ? 'flex' : 'none';
    }
    if (elements.compareToggleBtn) {
        elements.compareToggleBtn.classList.toggle('active', on);
    }
}

async function sendToComfy(variant) {
    if (state.lightboxIndex === -1 || !state.currentPromptData) return;
    if (state.comfyOnline === false) {
        showToast('ComfyUI is offline — start ComfyUI on :8188');
        return;
    }
    const photo = state.photos[state.lightboxIndex];
    const positive = elements.positivePromptText.innerText.trim()
        || state.currentPromptData.positive_prompt;
    const negative = elements.negativePromptText.innerText.trim()
        || state.currentPromptData.negative_prompt;
    // The picker decides the graph. This used to be inferred from which button
    // was pressed, which is why a third workflow was unreachable from the UI no
    // matter what the server could run (A4).
    const workflow = selectedWorkflow();
    const isRef = workflowKind(workflow) === 'img2img';
    const controls = readComfyProControls();
    if (elements.comfyStatusText) {
        elements.comfyStatusText.textContent = isRef
            ? (controls.useModeE
                ? 'Mode E + uploading reference…'
                : `Uploading reference + queueing ${workflowLabel(workflow)}…`)
            : `Queueing ${workflowLabel(workflow)}…`;
    }
    try {
        const body = {
            path: photo.rel_path,
            variant,
            workflow,
            positive_prompt: positive,
            negative_prompt: negative,
        };
        if (isRef) {
            body.denoise = controls.denoise;
            body.steps = controls.steps;
            body.cfg_scale = controls.cfg;
            body.use_mode_e = controls.useModeE;
            if (controls.seed != null) body.seed = controls.seed;
        } else {
            body.aspect_ratio = (state.currentPromptData.parameters || {}).aspect_ratio;
            body.steps = (state.currentPromptData.parameters || {}).steps;
            body.cfg_scale = (state.currentPromptData.parameters || {}).cfg_scale;
        }
        const res = await fetch('/api/comfy/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await res.json();
        if (res.status === 503) {
            showToast('ComfyUI offline');
            state.comfyOnline = false;
            updateComfyButtons();
            return;
        }
        if (!res.ok) {
            showToast(data.message || 'ComfyUI busy or failed');
            return;
        }
        if (data.seed != null && elements.comfySeedInput) {
            // Echo the seed the server actually used. Left unlocked, so the next
            // generate still rolls fresh — but the value is now sitting in the
            // box, so ticking the lock reuses this exact seed.
            elements.comfySeedInput.value = String(data.seed);
        }
        const seedBit = data.seed != null ? ` · seed ${data.seed}` : '';
        const label = isRef
            ? `${workflowLabel(workflow)} d=${data.denoise ?? controls.denoise}`
                + `${data.use_mode_e ? ' ModeE' : ''}`
            : `${workflowLabel(workflow)} · ${variant.toUpperCase()}`;
        showToast(`ComfyUI ${label} started${seedBit}`);
        if (elements.comfyStatusText && data.positive_prompt) {
            elements.comfyStatusText.textContent =
                `Queued · ${(data.positive_prompt || '').slice(0, 80)}…`;
        }
        pollComfyStatus();
    } catch (err) {
        showToast('ComfyUI request failed');
    }
}

async function pollComfyStatus() {
    try {
        const res = await fetch('/api/comfy/status');
        const data = await res.json();
        if (elements.comfyStatusText) {
            elements.comfyStatusText.textContent = data.progress
                + (data.error ? ` — ${data.error}` : '');
        }
        if (data.running) {
            if (!state.comfyPollTimer) {
                state.comfyPollTimer = setInterval(pollComfyStatus, 2500);
            }
            return;
        }
        if (state.comfyPollTimer) {
            clearInterval(state.comfyPollTimer);
            state.comfyPollTimer = null;
        }
        if (data.result && data.result.primary_url) {
            showToast('Generation complete');
            if (elements.lightboxGenImg) {
                elements.lightboxGenImg.src = data.result.primary_url;
            }
            if (elements.compareToggleBtn) {
                elements.compareToggleBtn.style.display = 'inline-flex';
            }
            setCompareMode(true);
            // Rateable immediately, before the reload below lands — the moment
            // you want to press "star" is the moment the image appears.
            setCurrentGeneration(data.result);
            if (state.lightboxIndex !== -1) {
                loadGenerationsForPhoto(state.photos[state.lightboxIndex].rel_path);
            }
        } else if (data.error) {
            showToast(`ComfyUI: ${data.error}`);
        }
    } catch (err) {
        console.error('Comfy status error', err);
    }
}

function updateFavoriteButton(photo) {
    if (!elements.favoritePhotoBtn || !photo) return;
    const on = Boolean(photo.favorite);
    elements.favoritePhotoBtn.classList.toggle('active', on);
    elements.favoritePhotoBtn.innerHTML = on
        ? '<i class="fa-solid fa-star"></i> Favorited'
        : '<i class="fa-regular fa-star"></i> Favorite';
}

async function toggleFavoriteCurrent() {
    if (state.lightboxIndex === -1) return;
    const photo = state.photos[state.lightboxIndex];
    try {
        const res = await fetch('/api/favorite', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: photo.rel_path })
        });
        const data = await res.json();
        if (!res.ok) {
            showToast('Favorite toggle failed');
            return;
        }
        photo.favorite = Boolean(data.favorite);
        updateFavoriteButton(photo);
        showToast(photo.favorite ? 'Added to favorites' : 'Removed from favorites');
        // Refresh visible card star without full reload
        const card = elements.galleryGrid.querySelector(`[data-rel-path="${CSS.escape(photo.rel_path)}"]`);
        if (card) {
            card.classList.toggle('is-favorite', photo.favorite);
            let mark = card.querySelector('.card-fav-mark');
            if (photo.favorite && !mark) {
                mark = document.createElement('span');
                mark.className = 'card-fav-mark';
                mark.title = 'Favorite';
                mark.innerHTML = '<i class="fa-solid fa-star"></i>';
                const actions = card.querySelector('.overlay-top-actions');
                const trash = actions && actions.querySelector('.card-trash-btn');
                if (actions && trash) actions.insertBefore(mark, trash);
            } else if (!photo.favorite && mark) {
                mark.remove();
            }
        }
        if (state.favoritesOnly && !photo.favorite) {
            fetchPhotos();
        }
    } catch (err) {
        showToast('Favorite toggle failed');
    }
}

function formatBytes(n) {
    const num = Number(n) || 0;
    if (num < 1024) return `${num} B`;
    if (num < 1024 * 1024) return `${(num / 1024).toFixed(1)} KB`;
    if (num < 1024 * 1024 * 1024) return `${(num / (1024 * 1024)).toFixed(1)} MB`;
    return `${(num / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatTakenAt(iso) {
    if (!iso) return '—';
    try {
        const d = new Date(iso);
        if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16);
        return d.toLocaleDateString(undefined, {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
        });
    } catch (_) {
        return String(iso).slice(0, 16);
    }
}


/**
 * Photos: show media details + prompt generator. 
 * Videos: show media details only.
 */
function setInspectorMode(mode) {
    const panel = document.querySelector('.inspector-panel');
    const isVideo = mode === 'video';
    if (panel) panel.classList.toggle('is-video-mode', isVideo);

    if (elements.inspectorPanelTitle) {
        elements.inspectorPanelTitle.innerHTML = isVideo
            ? '<i class="fa-solid fa-clapperboard text-gradient"></i> Reel details'
            : '<i class="fa-solid fa-wand-magic-sparkles text-gradient"></i> AI Image Prompt Generator';
    }
    if (elements.inspectorModelTag) {
        elements.inspectorModelTag.textContent = isVideo ? 'Archive' : 'Ollama Vision';
    }
    // Media details panel is now shown for both photos and videos
    if (elements.mediaDetailPanel) {
        elements.mediaDetailPanel.style.display = 'flex';
    }
    if (elements.generatePromptSection) {
        elements.generatePromptSection.style.display = isVideo ? 'none' : 'flex';
    }
    if (elements.promptContent) {
        if (isVideo) elements.promptContent.classList.remove('visible');
    }
    if (elements.regeneratePromptBtn) {
        elements.regeneratePromptBtn.style.display = isVideo ? 'none' : '';
    }
    if (elements.copyFullBundleBtn) {
        elements.copyFullBundleBtn.style.display = isVideo ? 'none' : '';
    }
}

function resetPromptPanel() {
    setInspectorMode('photo');
    // Show generate section, hide prompt content
    if (elements.generatePromptSection) elements.generatePromptSection.style.display = 'flex';
    if (elements.promptContent) elements.promptContent.classList.remove('visible');
    state.currentPromptData = null;
    setPromptEditable(false);
    clearPromptDirty();

    // Reset generate button state
    const btn = elements.generatePromptBtn;
    if (btn) {
        btn.classList.remove('loading');
        btn.innerHTML = '<i class="fa-solid fa-bolt"></i> Generate AI Prompt';
    }

    // Clear previous prompt data
    if (elements.positivePromptText) {
        elements.positivePromptText.textContent = 'Analyzing image with Ollama Vision...';
    }
    if (elements.negativePromptText) {
        elements.negativePromptText.textContent = 'deformed, bad anatomy, blurry...';
    }
    if (elements.promptTagsContainer) elements.promptTagsContainer.innerHTML = '';
    if (elements.promptHistory) elements.promptHistory.style.display = 'none';
    if (elements.promptHistoryList) elements.promptHistoryList.innerHTML = '';
    if (elements.paramSampler) elements.paramSampler.textContent = 'DPM++ 2M Karras';
    if (elements.paramSteps) elements.paramSteps.textContent = '30';
    if (elements.paramCFG) elements.paramCFG.textContent = '7.0';
    if (elements.paramAspect) elements.paramAspect.textContent = '4:5';
}

function metaCard(label, value) {
    return `<div class="video-meta-card">
        <span class="vm-label">${escapeHtml(label)}</span>
        <span class="vm-value">${escapeHtml(value)}</span>
    </div>`;
}


async function loadMediaDetailPanel(photo) {
    if (!elements.mediaDetailPanel || !photo) return;
    const isVideo = isVideoPhoto(photo);

    // Immediate shell from gallery card data
    if (elements.mediaDetailHandle) {
        elements.mediaDetailHandle.textContent = `@${photo.creator || 'unknown'}`;
    }
    if (elements.mediaDetailFile) {
        elements.mediaDetailFile.textContent = photo.filename || photo.rel_path || '';
    }
    if (elements.mediaDetailThumb) {
        elements.mediaDetailThumb.src = photo.thumb_url || photo.url || '';
        elements.mediaDetailThumb.alt = photo.filename || 'Media';
    }
    const badge = document.getElementById('mediaDetailTypeBadge');
    if (badge) {
        badge.innerHTML = isVideo ? '<i class="fa-solid fa-clapperboard"></i> Reel' : '<i class="fa-solid fa-image"></i> Photo';
    }
    if (elements.mediaDetailCaption) {
        elements.mediaDetailCaption.textContent = 'Loading metadata…';
        elements.mediaDetailCaption.classList.add('empty');
    }
    if (elements.mediaDetailGrid) {
        elements.mediaDetailGrid.innerHTML = metaCard('Type', isVideo ? 'Reel / video' : 'Photo');
    }
    if (elements.mediaOpenIgBtn) elements.mediaOpenIgBtn.style.display = 'none';
    if (elements.mediaExpandFromPanelBtn) {
        elements.mediaExpandFromPanelBtn.style.display = isVideo ? 'inline-flex' : 'none';
    }

    // Live duration when video element has metadata
    const paintDuration = () => {
        if (!isVideo) return;
        const v = elements.lightboxVideo;
        const dur = getMediaDuration(v);
        if (!Number.isFinite(dur) || !elements.mediaDetailGrid) return;
        const cards = elements.mediaDetailGrid.querySelectorAll('.video-meta-card');
        // Update or append duration card
        let found = false;
        cards.forEach((c) => {
            const lab = c.querySelector('.vm-label');
            if (lab && lab.textContent === 'Duration') {
                const val = c.querySelector('.vm-value');
                if (val) val.textContent = formatVideoTime(dur);
                found = true;
            }
        });
        if (!found) {
            elements.mediaDetailGrid.insertAdjacentHTML(
                'beforeend',
                metaCard('Duration', formatVideoTime(dur))
            );
        }
    };
    if (isVideo && elements.lightboxVideo) {
        if (elements.lightboxVideo.readyState >= 1) paintDuration();
        else {
            elements.lightboxVideo.addEventListener('loadedmetadata', paintDuration, { once: true });
        }
    }

    try {
        const res = await fetch(`/api/media/detail?path=${encodeURIComponent(photo.rel_path)}`);
        if (!res.ok) throw new Error(`detail ${res.status}`);
        const data = await res.json();
        // Stale response if user navigated away
        if (state.lightboxIndex === -1) return;
        const cur = state.photos[state.lightboxIndex];
        if (!cur || cur.rel_path !== photo.rel_path) return;

        if (elements.mediaDetailHandle) {
            elements.mediaDetailHandle.textContent = `@${data.creator || photo.creator || 'unknown'}`;
        }
        if (elements.mediaDetailFile) {
            elements.mediaDetailFile.textContent = data.filename || photo.filename || '';
        }
        if (elements.mediaDetailThumb && (data.thumb_url || photo.thumb_url)) {
            elements.mediaDetailThumb.src = data.thumb_url || photo.thumb_url;
        }

        if (elements.mediaDetailPills) {
            const pills = [];
            if (data.favorite || photo.favorite) {
                pills.push('<span class="video-pill is-favorite">Favorite</span>');
            }
            if (data.shortcode) {
                pills.push(`<span class="video-pill">${escapeHtml(data.shortcode)}</span>`);
            }
            elements.mediaDetailPills.innerHTML = pills.join('');
        }

        const gridParts = [
            metaCard('Type', isVideo ? 'Reel / video' : 'Photo'),
            metaCard('Size', formatBytes(data.file_size)),
            metaCard('Posted', formatTakenAt(data.taken_at))
        ];
        
        if (isVideo) {
            const durationLive = getMediaDuration(elements.lightboxVideo);
            gridParts.push(metaCard(
                'Duration',
                Number.isFinite(durationLive) ? formatVideoTime(durationLive) : '…'
            ));
        }
        
        if (elements.mediaDetailGrid) {
            elements.mediaDetailGrid.innerHTML = gridParts.join('');
        }

        const caption = (data.caption || '').trim();
        if (elements.mediaDetailCaption) {
            if (caption) {
                elements.mediaDetailCaption.textContent = caption;
                elements.mediaDetailCaption.classList.remove('empty');
            } else {
                elements.mediaDetailCaption.textContent =
                    isVideo ? 'No caption stored for this reel (metadata missing or empty).'
                            : 'No caption stored for this photo.';
                elements.mediaDetailCaption.classList.add('empty');
            }
        }

        if (elements.mediaOpenIgBtn) {
            if (data.post_url) {
                elements.mediaOpenIgBtn.href = data.post_url;
                elements.mediaOpenIgBtn.style.display = 'inline-flex';
            } else {
                elements.mediaOpenIgBtn.style.display = 'none';
            }
        }

    } catch (err) {
        console.error('Media detail load failed', err);
        if (elements.mediaDetailCaption) {
            elements.mediaDetailCaption.textContent =
                'Could not load media metadata.';
            elements.mediaDetailCaption.classList.add('empty');
        }
    }
}

function setPromptEditable(enabled) {
    elements.positivePromptText.contentEditable = enabled ? 'true' : 'false';
    elements.negativePromptText.contentEditable = enabled ? 'true' : 'false';
    elements.positivePromptText.classList.toggle('is-editable', enabled);
    elements.negativePromptText.classList.toggle('is-editable', enabled);
}

function clearPromptDirty() {
    state.promptDirty = false;
    if (elements.savePromptBtn) {
        elements.savePromptBtn.style.display = 'none';
    }
}

function markPromptDirty() {
    if (!state.currentPromptData) return;
    state.promptDirty = true;
    if (elements.savePromptBtn) {
        elements.savePromptBtn.style.display = 'inline-flex';
    }
}

function applyPromptData(data) {
    state.currentPromptData = data;
    elements.positivePromptText.textContent = data.positive_prompt || '';
    elements.negativePromptText.textContent = data.negative_prompt || '';

    if (data.parameters) {
        elements.paramSampler.textContent = data.parameters.sampler || 'DPM++ 2M Karras';
        elements.paramSteps.textContent = data.parameters.steps || 30;
        elements.paramCFG.textContent = data.parameters.cfg_scale || 7.0;
        elements.paramAspect.textContent = data.parameters.aspect_ratio || '4:5';
    }

    if (data.visual_tags) {
        elements.promptTagsContainer.innerHTML = data.visual_tags.map(t => {
            const safe = escapeHtml(t);
            return `<button type="button" class="tag-pill tag-clickable" data-tag="${safe}">#${safe}</button>`;
        }).join('');
        elements.promptTagsContainer.querySelectorAll('.tag-clickable').forEach((btn) => {
            btn.addEventListener('click', () => {
                const tag = btn.getAttribute('data-tag') || '';
                if (!tag) return;
                elements.searchInput.value = tag;
                state.searchQuery = tag;
                elements.clearSearch.style.display = 'block';
                closeLightbox();
                fetchPhotos();
                showToast(`Searching: ${tag}`);
            });
        });
    } else {
        elements.promptTagsContainer.innerHTML = '';
    }

    setPromptEditable(true);
    clearPromptDirty();
    renderPromptHistory(data);
    updateComfyButtons();
}

function renderPromptHistory(data) {
    if (!elements.promptHistory || !elements.promptHistoryList) return;
    const hist = data.history || [];
    if (!hist.length) {
        elements.promptHistory.style.display = 'none';
        elements.promptHistoryList.innerHTML = '';
        return;
    }
    elements.promptHistory.style.display = 'block';
    elements.promptHistoryList.innerHTML = hist.map((h, i) => {
        const full = h.positive_prompt || '';
        const preview = escapeHtml(full.slice(0, 80));
        const when = escapeHtml(h.saved_at ? formatRelativeTime(h.saved_at) : `#${i + 1}`);
        return `<button type="button" class="history-item" data-index="${i}" title="Restore this version">
            <span class="history-when">${when}</span>
            <span class="history-preview">${preview}${full.length > 80 ? '…' : ''}</span>
        </button>`;
    }).join('');
    elements.promptHistoryList.querySelectorAll('.history-item').forEach((btn) => {
        btn.addEventListener('click', () => {
            const idx = parseInt(btn.getAttribute('data-index') || '0', 10);
            restorePromptHistory(idx);
        });
    });
}

async function restorePromptHistory(index) {
    if (state.lightboxIndex === -1) return;
    const photo = state.photos[state.lightboxIndex];
    try {
        const res = await fetch('/api/prompt/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: photo.rel_path, index })
        });
        if (!res.ok) {
            showToast('Could not restore history');
            return;
        }
        const data = await res.json();
        applyPromptData(data);
        showToast('Restored previous prompt');
    } catch (err) {
        showToast('Restore failed');
    }
}

async function handleGeneratePrompt(forceRefresh = false) {
    if (state.lightboxIndex === -1) return;
    const photo = state.photos[state.lightboxIndex];
    // Vision prompt pipeline is for still photos only
    if (isVideoPhoto(photo)) {
        loadVideoDetailPanel(photo);
        showToast('Reels use the details panel — image prompts are for photos');
        return;
    }
    if ((forceRefresh || !photo.has_prompt) && !requireOllama()) return;

    const btn = elements.generatePromptBtn;
    btn.classList.add('loading');
    btn.innerHTML = '<i class="fa-solid fa-spinner"></i> Generating...';

    await fetchPromptForPhoto(photo.rel_path, forceRefresh);

    elements.generatePromptSection.style.display = 'none';
    elements.promptContent.classList.add('visible');
}

async function savePromptEdits() {
    if (state.lightboxIndex === -1 || !state.currentPromptData) return;
    const photo = state.photos[state.lightboxIndex];
    const positive = elements.positivePromptText.innerText.trim();
    const negative = elements.negativePromptText.innerText.trim();
    try {
        const res = await fetch('/api/prompt', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                path: photo.rel_path,
                positive_prompt: positive,
                negative_prompt: negative,
                visual_tags: state.currentPromptData.visual_tags || []
            })
        });
        if (!res.ok) {
            showToast('Failed to save prompt edits');
            return;
        }
        const data = await res.json();
        applyPromptData(data);
        photo.has_prompt = true;
        photo.prompt_stale = false;
        showToast('Prompt edits saved');
        renderGallery();
    } catch (err) {
        console.error('Save prompt failed:', err);
        showToast('Failed to save prompt edits');
    }
}

function closeLightbox() {
    // Collapse fullscreen shell first (moves video home), then stop media
    if (state.videoInFullscreenShell || isDisplayFlex(elements.photoViewerOverlay)) {
        closePhotoViewer({ keepVideoPlaying: false });
    }
    elements.lightboxModal.style.display = 'none';
    state.lightboxIndex = -1;
    state.currentPromptData = null;
    stopAndClearLightboxVideo();
}

function navigateLightbox(direction) {
    if (state.lightboxIndex === -1) return;
    let newIndex = state.lightboxIndex + direction;
    if (newIndex < 0) newIndex = state.photos.length - 1;
    if (newIndex >= state.photos.length) newIndex = 0;
    openLightbox(newIndex);
}

// Fullscreen Photo / Video Viewer
function openPhotoViewer() {
    if (state.lightboxIndex === -1) return;
    const photo = state.photos[state.lightboxIndex];
    if (isVideoPhoto(photo)) {
        openVideoViewer();
        return;
    }

    // Image mode — ensure video is not stuck in the shell
    if (state.videoInFullscreenShell) {
        restoreLightboxVideoHome();
    }

    if (elements.photoViewerImg) {
        elements.photoViewerImg.style.display = 'block';
        elements.photoViewerImg.src = photo.url;
    }
    elements.photoViewerOverlay.classList.remove('is-video');
    elements.photoViewerOverlay.style.display = 'flex';

    // Reset zoom/pan state
    state.viewerZoom = 1;
    state.viewerPanX = 0;
    state.viewerPanY = 0;
    applyViewerTransform();

    if (elements.photoViewerHint) {
        elements.photoViewerHint.innerHTML =
            '<i class="fa-solid fa-magnifying-glass-plus"></i> Scroll to zoom · Drag to pan';
        elements.photoViewerHint.classList.remove('hidden');
        setTimeout(() => {
            elements.photoViewerHint.classList.add('hidden');
        }, 3000);
    }
}

function formatVideoTime(sec) {
    if (!Number.isFinite(sec) || sec < 0) return '0:00';
    const s = Math.floor(sec % 60);
    const m = Math.floor(sec / 60) % 60;
    const h = Math.floor(sec / 3600);
    const pad = (n) => String(n).padStart(2, '0');
    if (h > 0) return `${h}:${pad(m)}:${pad(s)}`;
    return `${m}:${pad(s)}`;
}

function updateFsPlayPauseUi() {
    const v = elements.lightboxVideo;
    const icon = elements.fsPlayPauseIcon;
    if (!icon || !v) return;
    icon.className = v.paused ? 'fa-solid fa-play' : 'fa-solid fa-pause';
}

function updateFsMuteUi() {
    const v = elements.lightboxVideo;
    const icon = elements.fsMuteIcon;
    if (!icon || !v) return;
    if (v.muted || v.volume === 0) icon.className = 'fa-solid fa-volume-xmark';
    else if (v.volume < 0.4) icon.className = 'fa-solid fa-volume-low';
    else icon.className = 'fa-solid fa-volume-high';
}

/** Paint custom seek bar from video currentTime / buffered ranges. */
function updateFsSeekUi() {
    const v = elements.lightboxVideo;
    if (!v || !state.videoInFullscreenShell) return;
    const dur = getMediaDuration(v);
    const cur = Number(v.currentTime) || 0;
    const ratio = Number.isFinite(dur) && dur > 0 ? Math.max(0, Math.min(1, cur / dur)) : 0;
    const pct = `${(ratio * 100).toFixed(3)}%`;

    if (elements.fsSeekPlayed) elements.fsSeekPlayed.style.width = pct;
    if (elements.fsSeekThumb) elements.fsSeekThumb.style.left = pct;
    if (elements.fsTimeCurrent) elements.fsTimeCurrent.textContent = formatVideoTime(cur);
    if (elements.fsTimeDuration) {
        elements.fsTimeDuration.textContent = Number.isFinite(dur) ? formatVideoTime(dur) : '0:00';
    }
    if (elements.fsSeekRange && !state.fsScrubbing) {
        elements.fsSeekRange.value = String(Math.round(ratio * 1000));
    }
    if (elements.fsSeekWrap) {
        elements.fsSeekWrap.setAttribute('aria-valuenow', String(Math.round(ratio * 100)));
        if (Number.isFinite(dur)) {
            elements.fsSeekWrap.setAttribute('aria-valuetext', `${formatVideoTime(cur)} of ${formatVideoTime(dur)}`);
        }
    }

    // Buffered end
    let bufRatio = 0;
    try {
        if (v.buffered && v.buffered.length && Number.isFinite(dur) && dur > 0) {
            bufRatio = Math.max(0, Math.min(1, v.buffered.end(v.buffered.length - 1) / dur));
        }
    } catch (_) { /* ignore */ }
    if (elements.fsSeekBuffered) {
        elements.fsSeekBuffered.style.width = `${(bufRatio * 100).toFixed(3)}%`;
    }
    updateFsPlayPauseUi();
    updateFsMuteUi();
}

/**
 * Seek video from pointer X relative to the custom seek strip.
 * Server must support HTTP Range (206) or Chrome will ignore currentTime.
 */
function seekFsFromClientX(clientX) {
    const wrap = elements.fsSeekWrap;
    const v = elements.lightboxVideo;
    if (!wrap || !v) return;
    const rect = wrap.getBoundingClientRect();
    if (rect.width <= 0) return;
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    const dur = getMediaDuration(v);
    if (!Number.isFinite(dur) || dur <= 0) {
        // Metadata not ready — still paint the thumb so UI feels responsive
        if (elements.fsSeekPlayed) elements.fsSeekPlayed.style.width = `${(ratio * 100).toFixed(3)}%`;
        if (elements.fsSeekThumb) elements.fsSeekThumb.style.left = `${(ratio * 100).toFixed(3)}%`;
        return;
    }
    const next = ratio * dur;
    seekMediaTo(v, next);
    if (elements.fsSeekPlayed) elements.fsSeekPlayed.style.width = `${(ratio * 100).toFixed(3)}%`;
    if (elements.fsSeekThumb) elements.fsSeekThumb.style.left = `${(ratio * 100).toFixed(3)}%`;
    if (elements.fsTimeCurrent) elements.fsTimeCurrent.textContent = formatVideoTime(next);
    if (elements.fsSeekRange) elements.fsSeekRange.value = String(Math.round(ratio * 1000));
}

function seekFsFromRangeValue(rawVal) {
    const v = elements.lightboxVideo;
    if (!v) return;
    const dur = getMediaDuration(v);
    if (!Number.isFinite(dur) || dur <= 0) return;
    const ratio = Math.max(0, Math.min(1, Number(rawVal) / 1000));
    seekMediaTo(v, ratio * dur);
    if (elements.fsSeekPlayed) elements.fsSeekPlayed.style.width = `${(ratio * 100).toFixed(3)}%`;
    if (elements.fsSeekThumb) elements.fsSeekThumb.style.left = `${(ratio * 100).toFixed(3)}%`;
    if (elements.fsTimeCurrent) elements.fsTimeCurrent.textContent = formatVideoTime(ratio * dur);
}

function endFsScrub() {
    state.fsScrubbing = false;
    if (elements.fsSeekWrap) elements.fsSeekWrap.classList.remove('is-scrubbing');
    updateFsSeekUi();
}

function wireFsVideoControls() {
    if (state.fsUiWired) return;
    state.fsUiWired = true;

    const wrap = elements.fsSeekWrap;
    const range = elements.fsSeekRange;
    const bar = elements.fsVideoBar;
    const stage = elements.fsVideoStage;

    // Prevent image-viewer backdrop handlers from seeing control events
    const stopBubble = (e) => e.stopPropagation();
    if (bar) {
        ['mousedown', 'mouseup', 'click', 'dblclick', 'pointerdown', 'pointerup'].forEach((ev) => {
            bar.addEventListener(ev, stopBubble);
        });
    }
    if (elements.fsVideoLayout) {
        ['mousedown', 'click', 'dblclick'].forEach((ev) => {
            elements.fsVideoLayout.addEventListener(ev, stopBubble);
        });
    }

    // Document-level move/up while scrubbing — survives leaving the tiny track
    const onDocPointerMove = (e) => {
        if (!state.fsScrubbing) return;
        e.preventDefault();
        seekFsFromClientX(e.clientX);
    };
    const onDocPointerUp = (e) => {
        if (!state.fsScrubbing) return;
        seekFsFromClientX(e.clientX);
        endFsScrub();
    };
    document.addEventListener('pointermove', onDocPointerMove, { passive: false });
    document.addEventListener('pointerup', onDocPointerUp);
    document.addEventListener('pointercancel', onDocPointerUp);
    // Mouse fallbacks (some environments synthesize inconsistently)
    document.addEventListener('mousemove', (e) => {
        if (!state.fsScrubbing) return;
        seekFsFromClientX(e.clientX);
    });
    document.addEventListener('mouseup', (e) => {
        if (!state.fsScrubbing) return;
        seekFsFromClientX(e.clientX);
        endFsScrub();
    });

    const beginScrub = (clientX, e) => {
        if (e) {
            e.preventDefault();
            e.stopPropagation();
        }
        state.fsScrubbing = true;
        if (wrap) wrap.classList.add('is-scrubbing');
        seekFsFromClientX(clientX);
    };

    if (wrap) {
        wrap.addEventListener('pointerdown', (e) => {
            if (e.button != null && e.button !== 0) return;
            // Prefer range input when it is the target — it fires input events
            if (e.target === range) return;
            beginScrub(e.clientX, e);
            try {
                wrap.setPointerCapture(e.pointerId);
            } catch (_) { /* ignore */ }
        });
        wrap.addEventListener('mousedown', (e) => {
            if (e.button !== 0) return;
            if (e.target === range) return;
            beginScrub(e.clientX, e);
        });
        wrap.addEventListener('keydown', (e) => {
            const v = elements.lightboxVideo;
            if (!v) return;
            if (e.key === 'ArrowLeft') {
                e.preventDefault();
                seekVideoElement(v, -VIDEO_SEEK_SECONDS);
            } else if (e.key === 'ArrowRight') {
                e.preventDefault();
                seekVideoElement(v, VIDEO_SEEK_SECONDS);
            }
        });
    }

    if (range) {
        range.addEventListener('pointerdown', (e) => {
            e.stopPropagation();
            state.fsScrubbing = true;
            if (wrap) wrap.classList.add('is-scrubbing');
        });
        range.addEventListener('input', (e) => {
            state.fsScrubbing = true;
            if (wrap) wrap.classList.add('is-scrubbing');
            seekFsFromRangeValue(e.target.value);
        });
        range.addEventListener('change', (e) => {
            seekFsFromRangeValue(e.target.value);
            endFsScrub();
        });
        range.addEventListener('pointerup', () => endFsScrub());
    }

    if (elements.fsPlayPauseBtn) {
        elements.fsPlayPauseBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            toggleVideoPlayback(elements.lightboxVideo);
            updateFsPlayPauseUi();
        });
    }
    if (elements.fsMuteBtn) {
        elements.fsMuteBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const vid = elements.lightboxVideo;
            if (!vid) return;
            vid.muted = !vid.muted;
            if (!vid.muted && vid.volume === 0) vid.volume = 1;
            updateFsMuteUi();
        });
    }
    if (stage) {
        stage.addEventListener('click', (e) => {
            if (e.target.closest('.fs-video-bar')) return;
            e.preventDefault();
            e.stopPropagation();
            toggleVideoPlayback(elements.lightboxVideo);
            updateFsPlayPauseUi();
        });
        stage.addEventListener('dblclick', (e) => {
            e.preventDefault();
            e.stopPropagation();
            closePhotoViewer();
        });
    }

    // Bind media events once (element is stable)
    const bindMediaUi = (vid) => {
        if (!vid || vid.dataset.fsUi === '1') return;
        vid.dataset.fsUi = '1';
        vid.addEventListener('timeupdate', () => {
            if (!state.fsScrubbing) updateFsSeekUi();
        });
        vid.addEventListener('progress', updateFsSeekUi);
        vid.addEventListener('loadedmetadata', updateFsSeekUi);
        vid.addEventListener('durationchange', updateFsSeekUi);
        vid.addEventListener('seeked', updateFsSeekUi);
        vid.addEventListener('play', updateFsPlayPauseUi);
        vid.addEventListener('pause', updateFsPlayPauseUi);
        vid.addEventListener('volumechange', updateFsMuteUi);
    };
    bindMediaUi(elements.lightboxVideo);
}

/**
 * Fullscreen without rebuffering: teleport the same <video> node into the
 * stage and use a custom scrub bar (mouse drag/click always works).
 */
function openVideoViewer() {
    const v = elements.lightboxVideo;
    if (!v || !isLightboxVideoActive()) return;
    if (state.videoInFullscreenShell) return;

    wireFsVideoControls();

    if (elements.photoViewerImg) {
        elements.photoViewerImg.style.display = 'none';
    }

    // Show layout + move live element into stage (no src reload)
    if (elements.fsVideoLayout) {
        elements.fsVideoLayout.hidden = false;
        elements.fsVideoLayout.style.display = 'flex';
    }
    const stage = elements.fsVideoStage || elements.photoViewerContainer;
    stage.appendChild(v);
    v.classList.add('fs-active-video');
    v.style.display = 'block';
    // Native controls are unreliable in the overlay shell — use custom bar
    v.controls = false;
    state.videoInFullscreenShell = true;
    state.fsScrubbing = false;

    elements.photoViewerOverlay.classList.add('is-video');
    elements.photoViewerOverlay.style.display = 'flex';

    // User gesture path — unmute
    ensureVideoAudible(v);
    if (v.paused) {
        v.play().catch(() => {});
    }

    state.viewerZoom = 1;
    state.viewerPanX = 0;
    state.viewerPanY = 0;
    state.viewerDragging = false;

    updateFsSeekUi();
    // Second paint after layout for correct track width
    requestAnimationFrame(() => updateFsSeekUi());

    if (elements.photoViewerHint) {
        elements.photoViewerHint.innerHTML =
            '<i class="fa-solid fa-film"></i> Drag the bar to scrub · ←/→ · Space · Esc';
        elements.photoViewerHint.classList.remove('hidden');
        setTimeout(() => {
            elements.photoViewerHint.classList.add('hidden');
        }, 2600);
    }
}

function closePhotoViewer(opts = {}) {
    const keepVideoPlaying = opts.keepVideoPlaying !== false;
    const wasVideo = state.videoInFullscreenShell ||
        elements.photoViewerOverlay.classList.contains('is-video');

    if (wasVideo && elements.lightboxVideo) {
        const v = elements.lightboxVideo;
        const playing = !v.paused;
        state.fsScrubbing = false;
        // Restore native controls for lightbox preview
        v.controls = true;
        restoreLightboxVideoHome();
        if (elements.fsVideoLayout) {
            elements.fsVideoLayout.hidden = true;
            elements.fsVideoLayout.style.display = 'none';
        }
        // Playback continues on the same element — no seek/reload handoff
        if (keepVideoPlaying && playing && isDisplayFlex(elements.lightboxModal)) {
            v.play().catch(() => {});
        } else if (!keepVideoPlaying) {
            v.pause();
        }
    }

    if (elements.photoViewerImg) {
        elements.photoViewerImg.style.display = 'block';
    }
    elements.photoViewerOverlay.classList.remove('is-video');
    elements.photoViewerOverlay.style.display = 'none';
    state.viewerZoom = 1;
    state.viewerPanX = 0;
    state.viewerPanY = 0;
    state.viewerDragging = false;
}

function applyViewerTransform() {
    elements.photoViewerImg.style.transform =
        `translate(${state.viewerPanX}px, ${state.viewerPanY}px) scale(${state.viewerZoom})`;
}

function handleViewerWheel(e) {
    // Don't hijack scroll/wheel over video (volume / page) — only zoom images
    if (elements.photoViewerOverlay.classList.contains('is-video')) return;
    e.preventDefault();
    const delta = e.deltaY > 0 ? -0.15 : 0.15;
    const newZoom = Math.min(Math.max(state.viewerZoom + delta, 0.5), 8);

    // Zoom towards mouse position
    if (newZoom !== state.viewerZoom) {
        const rect = elements.photoViewerContainer.getBoundingClientRect();
        const mouseX = e.clientX - rect.left - rect.width / 2;
        const mouseY = e.clientY - rect.top - rect.height / 2;
        const scaleFactor = newZoom / state.viewerZoom;

        state.viewerPanX = mouseX - scaleFactor * (mouseX - state.viewerPanX);
        state.viewerPanY = mouseY - scaleFactor * (mouseY - state.viewerPanY);
        state.viewerZoom = newZoom;

        applyViewerTransform();
    }
}

function handleViewerMouseDown(e) {
    if (e.target === elements.photoViewerClose || e.target.closest('.photo-viewer-close')) return;
    // Video mode: never capture pointer — native scrub bar / controls need it
    if (elements.photoViewerOverlay.classList.contains('is-video')) return;
    if (e.target.closest('video')) return;
    state.viewerDragging = true;
    state.viewerMoved = false;
    state.viewerDragStartX = e.clientX;
    state.viewerDragStartY = e.clientY;
    state.viewerLastPanX = state.viewerPanX;
    state.viewerLastPanY = state.viewerPanY;
    elements.photoViewerOverlay.classList.add('dragging');
}

function handleViewerMouseMove(e) {
    if (!state.viewerDragging) return;
    const dx = e.clientX - state.viewerDragStartX;
    const dy = e.clientY - state.viewerDragStartY;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) state.viewerMoved = true;
    state.viewerPanX = state.viewerLastPanX + dx;
    state.viewerPanY = state.viewerLastPanY + dy;
    applyViewerTransform();
}

function handleViewerMouseUp() {
    state.viewerDragging = false;
    elements.photoViewerOverlay.classList.remove('dragging');
}

/** Click empty dark chrome (not the image/video/player) closes viewer when not panning/zoomed. */
function handleViewerBackdropClick(e) {
    if (state.viewerMoved) return;
    if (state.fsScrubbing) return;
    if (e.target.closest('.photo-viewer-close')) return;
    // Never close while interacting with the custom player or video surface
    if (
        e.target.closest('#photoViewerImg') ||
        e.target.closest('#lightboxVideo') ||
        e.target.closest('video') ||
        e.target.closest('#fsVideoLayout') ||
        e.target.closest('.fs-video-bar') ||
        e.target.closest('.fs-seek-wrap')
    ) {
        return;
    }
    // Image mode: only close when not zoomed in
    if (!elements.photoViewerOverlay.classList.contains('is-video') && state.viewerZoom > 1.05) {
        return;
    }
    // Backdrop / container padding only
    if (
        e.target === elements.photoViewerOverlay ||
        e.target === elements.photoViewerContainer ||
        e.target === elements.photoViewerHint
    ) {
        closePhotoViewer();
    }
}

function handleViewerDblClick(e) {
    if (e.target === elements.photoViewerClose || e.target.closest('.photo-viewer-close')) return;
    if (elements.photoViewerOverlay.classList.contains('is-video')) return;
    // Toggle between 1x and 2.5x zoom on double-click
    if (state.viewerZoom > 1.2) {
        state.viewerZoom = 1;
        state.viewerPanX = 0;
        state.viewerPanY = 0;
    } else {
        state.viewerZoom = 2.5;
        // Center zoom on double-click position
        const rect = elements.photoViewerContainer.getBoundingClientRect();
        const mouseX = e.clientX - rect.left - rect.width / 2;
        const mouseY = e.clientY - rect.top - rect.height / 2;
        state.viewerPanX = -mouseX * 1.5;
        state.viewerPanY = -mouseY * 1.5;
    }
    applyViewerTransform();
}

// Deletion Confirmation Modal
/** Confirm-button label + copy depend on whether the server keeps a Trash copy. */
function applyDeleteConfirmMode() {
    if (!elements.confirmDeleteBtn) return;
    elements.confirmDeleteBtn.innerHTML = state.trashEnabled
        ? '<i class="fa-solid fa-trash-can"></i> Move to Trash'
        : '<i class="fa-solid fa-trash-can"></i> Permanently Delete';
}

function promptDeletePhoto(photo) {
    state.photoToDelete = photo;
    state.photosToDelete = null;
    if (elements.deleteConfirmTitle) {
        elements.deleteConfirmTitle.textContent = state.trashEnabled
            ? 'Move Photo to Trash?'
            : 'Delete Photo?';
    }
    if (elements.deleteConfirmBody) {
        elements.deleteConfirmBody.textContent = state.trashEnabled
            ? 'This photo moves to the archive Trash folder. You can undo right after, or restore it later.'
            : 'Are you sure you want to permanently delete this photo from your storage folder?';
    }
    elements.deleteFilenamePreview.textContent = `${photo.creator}/${photo.filename}`;
    applyDeleteConfirmMode();
    elements.deleteConfirmModal.style.display = 'flex';
}

function promptBulkDelete() {
    const paths = Array.from(state.selectedPaths);
    if (!paths.length) return;
    state.photoToDelete = null;
    state.photosToDelete = paths;
    const names = paths.map((p) => p.split('/').pop());
    const preview = names.slice(0, 5).join('\n') +
        (names.length > 5 ? `\nand ${names.length - 5} more` : '');
    const favCount = state.photos.filter(
        (p) => state.selectedPaths.has(p.rel_path) && p.favorite
    ).length;
    if (elements.deleteConfirmTitle) {
        const noun = 'Photos';
        elements.deleteConfirmTitle.textContent = state.trashEnabled
            ? `Move ${paths.length} ${noun} to Trash?`
            : `Delete ${paths.length} ${noun}?`;
    }
    if (elements.deleteConfirmBody) {
        const who = state.selectedCreator ? ` for @${state.selectedCreator}` : '';
        // Favorites inside a bulk sweep are the classic mistake — call it out.
        const favNote = favCount
            ? ` Includes ${favCount} favorite${favCount === 1 ? '' : 's'}.`
            : '';
        elements.deleteConfirmBody.textContent = state.trashEnabled
            ? `${paths.length} item(s)${who} move to the archive Trash folder and can be restored.${favNote}`
            : `Permanently delete ${paths.length} item(s)${who}? This cannot be undone.${favNote}`;
    }
    elements.deleteFilenamePreview.textContent = preview;
    applyDeleteConfirmMode();
    elements.deleteConfirmModal.style.display = 'flex';
}

function closeDeleteModal() {
    elements.deleteConfirmModal.style.display = 'none';
    state.photoToDelete = null;
    state.photosToDelete = null;
}

function closeNewCreatorModal() {
    if (elements.newCreatorModal) elements.newCreatorModal.style.display = 'none';
}

function closeUploadModal() {
    if (elements.uploadModal) elements.uploadModal.style.display = 'none';
}

/** Wire .modal-overlay click → close (overlay is a sibling of .modal-card). */
function bindModalOverlayDismiss(modalEl, closeFn) {
    if (!modalEl || typeof closeFn !== 'function') return;
    const overlay = modalEl.querySelector('.modal-overlay');
    if (overlay) overlay.addEventListener('click', closeFn);
}

async function executeBulkDelete(paths) {
    const total = paths.length;
    let ok = 0;
    let fail = 0;
    const deletedPaths = [];
    const trashIds = [];

    // Long bulk deletes used to run silently; keep a live count on screen.
    const progress = total > 5
        ? showToast({ title: `Deleting 0/${total}…`, duration: 0 })
        : null;

    for (const relPath of paths) {
        try {
            const res = await fetch(`/api/photo?path=${encodeURIComponent(relPath)}`, {
                method: 'DELETE'
            });
            if (res.ok) {
                ok += 1;
                const data = await res.json().catch(() => ({}));
                deletedPaths.push(relPath);
                if (data.trash_id) trashIds.push(data.trash_id);
                state.selectedPaths.delete(relPath);
            } else {
                fail += 1;
            }
        } catch (err) {
            fail += 1;
        }
        if (progress) {
            progress.textContent = `Deleting ${ok + fail}/${total}…`;
        }
    }
    if (progress) progress.remove();

    removePhotosFromView(deletedPaths);
    if (state.lightboxIndex !== -1 && !state.photos.length) closeLightbox();
    clearSelection();
    setSelectMode(false);

    if (trashIds.length) {
        showToast({
            title: `Moved ${ok} to Trash`,
            body: fail ? `${fail} failed` : 'Recoverable until you empty the Trash',
            actionLabel: `Undo all (${trashIds.length})`,
            onAction: () => restoreFromTrash(trashIds, { label: 'photos' }),
            duration: 12000
        });
    } else {
        showToast(`Deleted ${ok}${fail ? `, ${fail} failed` : ''}`);
    }
}

async function startBulkReanalyze() {
    const paths = Array.from(state.selectedPaths);
    if (!paths.length) return;
    if (!requireOllama()) return;
    try {
        const res = await fetch('/api/prompt/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ paths, force: true })
        });
        const data = await res.json();
        if (res.ok && data.status === 'started') {
            showToast(`Re-analyze started — ${data.pending} photos`);
            pollBatchStatus();
        } else if (data.status === 'nothing_to_do') {
            showToast('Nothing to re-analyze');
        } else {
            showToast(data.message || 'Batch busy or failed');
        }
    } catch (err) {
        showToast('Bulk re-analyze failed');
    }
}

// 1-Click Copy Helpers
function copyToClipboard(text, message) {
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
        showToast(message);
    }).catch(err => {
        console.error('Clipboard copy failed:', err);
    });
}

// Event Listeners
function setupEventListeners() {
    // Stop polling a tab nobody is looking at; refresh the moment it returns.
    document.addEventListener('visibilitychange', handleVisibilityChange);

    // Search hits the DB across prompt text — one request per keystroke was
    // both wasteful and racy. Debounce, then let AbortController settle order.
    const runSearch = debounce(() => fetchPhotos(), SEARCH_DEBOUNCE_MS);
    elements.searchInput.addEventListener('input', (e) => {
        state.searchQuery = e.target.value.trim();
        elements.clearSearch.style.display = state.searchQuery ? 'block' : 'none';
        runSearch();
    });
    // Enter searches immediately instead of waiting out the debounce
    elements.searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            fetchPhotos();
        }
    });

    elements.clearSearch.addEventListener('click', () => {
        elements.searchInput.value = '';
        state.searchQuery = '';
        elements.clearSearch.style.display = 'none';
        elements.searchInput.focus();
        fetchPhotos();
    });

    if (elements.unanalyzedFilterBtn) {
        elements.unanalyzedFilterBtn.addEventListener('click', () => {
            state.unanalyzedOnly = !state.unanalyzedOnly;
            elements.unanalyzedFilterBtn.classList.toggle('active', state.unanalyzedOnly);
            saveViewPrefs();
            fetchPhotos();
        });
    }

    if (elements.favoritesFilterBtn) {
        elements.favoritesFilterBtn.addEventListener('click', () => {
            state.favoritesOnly = !state.favoritesOnly;
            elements.favoritesFilterBtn.classList.toggle('active', state.favoritesOnly);
            saveViewPrefs();
            fetchPhotos();
        });
    }

    if (elements.groupPostsBtn) {
        elements.groupPostsBtn.addEventListener('click', () => {
            state.groupPosts = !state.groupPosts;
            elements.groupPostsBtn.classList.toggle('active', state.groupPosts);
            saveViewPrefs();
            // A full refetch, not a re-render: `total` and the paging unit both
            // change with the grouping, and only the server knows them.
            fetchPhotos();
        });
    }

    if (elements.syncLatestCreatorBtn) {
        elements.syncLatestCreatorBtn.addEventListener('click', syncLatestSelectedCreator);
    }
    if (elements.batchJobChipCancel) {
        elements.batchJobChipCancel.addEventListener('click', cancelBatchAnalyze);
    }
    if (elements.generateJobChipCancel) {
        elements.generateJobChipCancel.addEventListener('click', cancelBatchGenerate);
    }
    if (elements.outputsBatchClear) {
        elements.outputsBatchClear.addEventListener('click', clearOutputsBatch);
    }
    if (elements.favoritePhotoBtn) {
        elements.favoritePhotoBtn.addEventListener('click', toggleFavoriteCurrent);
    }

    if (elements.compareToggleBtn) {
        elements.compareToggleBtn.addEventListener('click', () => {
            if (!state.currentGenerations.length && !(elements.lightboxGenImg && elements.lightboxGenImg.src)) {
                showToast('No generation yet — Send to ComfyUI first');
                return;
            }
            setCompareMode(!state.compareMode);
        });
    }
    if (elements.comfyProBtn) {
        elements.comfyProBtn.addEventListener('click', () => sendToComfy('pro'));
    }
    if (elements.applyModeEBtn) {
        elements.applyModeEBtn.addEventListener('click', () => applyModeEToEditor({ save: false }));
    }
    if (elements.comfySeedLock) {
        elements.comfySeedLock.addEventListener('change', syncComfySeedInput);
    }
    if (elements.comfyWorkflowSelect) {
        elements.comfyWorkflowSelect.addEventListener('change', syncComfyWorkflowControls);
    }
    if (elements.comfySdxlBtn) {
        elements.comfySdxlBtn.addEventListener('click', () => sendToComfy('sdxl'));
    }
    if (elements.comfyFluxBtn) {
        elements.comfyFluxBtn.addEventListener('click', () => sendToComfy('flux'));
    }

    if (elements.sortSelect) {
        elements.sortSelect.value = state.sortMode || 'name';
        elements.sortSelect.addEventListener('change', () => {
            state.sortMode = elements.sortSelect.value || 'name';
            saveViewPrefs();
            fetchPhotos();
        });
    }

    if (elements.mediaTypeSelect) {
        elements.mediaTypeSelect.value = state.mediaType || 'all';
        elements.mediaTypeSelect.addEventListener('change', () => {
            state.mediaType = elements.mediaTypeSelect.value || 'all';
            saveViewPrefs();
            fetchPhotos();
        });
    }

    if (elements.verdictFilterSelect) {
        elements.verdictFilterSelect.value = state.browseVerdict || '';
        elements.verdictFilterSelect.addEventListener('change', () => {
            state.browseVerdict = elements.verdictFilterSelect.value || '';
            elements.verdictFilterSelect.classList.toggle('is-active', Boolean(state.browseVerdict));
            // The saturated styling follows the *selected* option, so it has
            // to be repainted on every change, not only when stats land.
            renderVerdictSelectPassRates();
            saveViewPrefs();
            fetchPhotos();
        });
    }

    if (elements.rebuildStyleBtn) {
        elements.rebuildStyleBtn.addEventListener('click', rebuildSelectedCreatorStyle);
    }

    if (elements.selectModeBtn) {
        elements.selectModeBtn.addEventListener('click', () => {
            setSelectMode(!state.selectMode);
        });
    }
    if (elements.bulkClearBtn) {
        elements.bulkClearBtn.addEventListener('click', clearSelection);
    }
    if (elements.bulkDeleteBtn) {
        elements.bulkDeleteBtn.addEventListener('click', promptBulkDelete);
    }
    if (elements.bulkGenerateBtn) {
        elements.bulkGenerateBtn.addEventListener('click', startBulkGenerate);
    }
    if (elements.bulkReanalyzeBtn) {
        elements.bulkReanalyzeBtn.addEventListener('click', startBulkReanalyze);
    }

    if (elements.creatorSearchInput) {
        // Filtering is local, but renderCreatorList() rebuilds the whole
        // sidebar — no need to do that on every keystroke.
        const runCreatorFilter = debounce(renderCreatorList, CREATOR_FILTER_DEBOUNCE_MS);
        elements.creatorSearchInput.addEventListener('input', (e) => {
            state.creatorSearchQuery = e.target.value.trim();
            runCreatorFilter();
        });
    }

    elements.gridNormal.addEventListener('click', () => {
        applyGridSize('normal');
        saveViewPrefs();
    });

    elements.gridLarge.addEventListener('click', () => {
        applyGridSize('large');
        saveViewPrefs();
    });

    elements.refreshBtn.addEventListener('click', () => {
        initApp();
        showToast('Refreshed gallery archive');
    });

    if (elements.trashBtn) {
        elements.trashBtn.addEventListener('click', openTrashModal);
    }
    if (elements.closeTrashModalBtn) {
        elements.closeTrashModalBtn.addEventListener('click', closeTrashModal);
    }
    bindModalOverlayDismiss(elements.trashModal, closeTrashModal);

    if (elements.insightsBtn) {
        elements.insightsBtn.addEventListener('click', openInsightsModal);
    }
    if (elements.closeInsightsBtn) {
        elements.closeInsightsBtn.addEventListener('click', closeInsightsModal);
    }
    if (elements.doneInsightsBtn) {
        elements.doneInsightsBtn.addEventListener('click', closeInsightsModal);
    }
    if (elements.refreshInsightsBtn) {
        elements.refreshInsightsBtn.addEventListener('click', loadInsights);
    }
    bindModalOverlayDismiss(elements.insightsModal, closeInsightsModal);
    if (elements.trashEmptyBtn) {
        elements.trashEmptyBtn.addEventListener('click', async () => {
            if (!state.trashCount) {
                showToast('Trash is already empty');
                return;
            }
            await purgeTrash({ all: true });
            await loadTrashList();
        });
    }
    if (elements.trashPurgeExpiredBtn) {
        elements.trashPurgeExpiredBtn.addEventListener('click', async () => {
            await purgeTrash({ expired: true });
            await loadTrashList();
        });
    }

    // Infinite scroll is driven by an IntersectionObserver on the sentinel —
    // see observeLoadMoreSentinel. The old handler read document.body
    // .offsetHeight on every animation frame of every scroll, which forces a
    // synchronous layout of a document that only ever grows.

    elements.syncInstagramBtn.addEventListener('click', openSyncModal);
    elements.closeSyncModalBtn.addEventListener('click', closeSyncModal);
    elements.syncSavedBtn.addEventListener('click', startSyncSaved);
    elements.syncCreatorBtn.addEventListener('click', startSyncCreator);
    elements.syncFollowingBtn.addEventListener('click', startSyncFollowing);
    if (elements.scrapeEnqueueBtn) {
        elements.scrapeEnqueueBtn.addEventListener('click', enqueueCreatorScrape);
    }
    if (elements.scrapePauseBtn) {
        elements.scrapePauseBtn.addEventListener('click', pauseScrapeQueue);
    }
    if (elements.scrapeResumeBtn) {
        elements.scrapeResumeBtn.addEventListener('click', resumeScrapeQueue);
    }
    if (elements.scrapeCancelBtn) {
        elements.scrapeCancelBtn.addEventListener('click', cancelRunningSyncJob);
    }
    if (elements.scrapeClearPendingBtn) {
        elements.scrapeClearPendingBtn.addEventListener('click', clearPendingScrapeJobs);
    }
    document.querySelectorAll('input[name="scrapeMode"]').forEach((radio) => {
        radio.addEventListener('change', updateScrapeModeUi);
    });
    updateScrapeModeUi();
    if (elements.scrapeSourceSelect) {
        elements.scrapeSourceSelect.addEventListener('change', updateScrapeSourceUI);
    }
    updateScrapeSourceUI();
    if (elements.scrapeCreatorInput) {
        elements.scrapeCreatorInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') enqueueCreatorScrape();
        });
    }
    elements.batchPromptBtn.addEventListener('click', startBatchAnalyze);

    if (elements.followingSearchInput) {
        const runFollowingSearch = debounce((q) => loadFollowingPicker(q), 200);
        elements.followingSearchInput.addEventListener('input', (e) => {
            runFollowingSearch(e.target.value.trim());
        });
    }

    setupClassifyListeners();
    setupGenRatingListeners();
    setupOutputsListeners();
    setupOutputDetailListeners();

    // Lightbox Actions
    elements.lightboxDeleteBtn.addEventListener('click', () => {
        if (state.lightboxIndex !== -1) {
            promptDeletePhoto(state.photos[state.lightboxIndex]);
        }
    });

    // Generate Prompt Button
    elements.generatePromptBtn.addEventListener('click', () => {
        handleGeneratePrompt(false);
    });

    if (elements.regeneratePromptBtn) {
        elements.regeneratePromptBtn.addEventListener('click', () => {
            handleGeneratePrompt(true);
            if (state.ollamaOnline !== false) {
                showToast('Re-analyzing with Body & Beauty Vision Engine...');
            }
        });
    }

    if (elements.savePromptBtn) {
        elements.savePromptBtn.addEventListener('click', savePromptEdits);
    }

    elements.positivePromptText.addEventListener('input', markPromptDirty);
    elements.negativePromptText.addEventListener('input', markPromptDirty);

    // Click photo in lightbox to open fullscreen viewer
    elements.lightboxImg.addEventListener('click', (e) => {
        e.stopPropagation();
        openPhotoViewer();
    });

    // Video: expand button + double-click → fullscreen player (scrub + seek keys)
    if (elements.videoExpandBtn) {
        elements.videoExpandBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            openPhotoViewer();
        });
    }
    if (elements.videoExpandFromPanelBtn) {
        elements.videoExpandFromPanelBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            openPhotoViewer();
        });
    }
    if (elements.videoCopyPathBtn) {
        elements.videoCopyPathBtn.addEventListener('click', () => {
            if (state.lightboxIndex === -1) return;
            const photo = state.photos[state.lightboxIndex];
            if (!photo) return;
            copyToClipboard(photo.rel_path, 'Copied archive path');
        });
    }
    if (elements.lightboxVideo) {
        elements.lightboxVideo.addEventListener('dblclick', (e) => {
            e.preventDefault();
            e.stopPropagation();
            openPhotoViewer();
        });
    }

    // Copy Buttons
    elements.copyPositiveBtn.addEventListener('click', () => {
        if (state.currentPromptData) {
            const text = elements.positivePromptText.innerText.trim() || state.currentPromptData.positive_prompt;
            copyToClipboard(text, 'Copied Positive Prompt to Clipboard!');
        }
    });

    elements.copyNegativeBtn.addEventListener('click', () => {
        if (state.currentPromptData) {
            const text = elements.negativePromptText.innerText.trim() || state.currentPromptData.negative_prompt;
            copyToClipboard(text, 'Copied Negative Prompt to Clipboard!');
        }
    });

    elements.copyFullBundleBtn.addEventListener('click', () => {
        if (state.currentPromptData) {
            const p = state.currentPromptData;
            const positive = elements.positivePromptText.innerText.trim() || p.positive_prompt;
            const negative = elements.negativePromptText.innerText.trim() || p.negative_prompt;
            const bundle = `POSITIVE PROMPT:\n${positive}\n\nNEGATIVE PROMPT:\n${negative}\n\nPARAMETERS:\nSampler: ${p.parameters.sampler}\nSteps: ${p.parameters.steps}\nCFG Scale: ${p.parameters.cfg_scale}\nAspect Ratio: ${p.parameters.aspect_ratio}\nVision Engine: ${p.parameters.vision_engine}`;
            copyToClipboard(bundle, 'Copied Full Generation Bundle to Clipboard!');
        }
    });

    const bindExportCopy = (btn, key, label) => {
        if (!btn) return;
        btn.addEventListener('click', () => {
            if (!state.currentPromptData) return;
            const exports = state.currentPromptData.exports || {};
            const text = exports[key] || state.currentPromptData.positive_prompt;
            copyToClipboard(text, `Copied ${label} prompt!`);
        });
    };
    bindExportCopy(elements.copyFluxBtn, 'flux', 'Flux');
    bindExportCopy(elements.copySdxlBtn, 'sdxl', 'SDXL');
    bindExportCopy(elements.copyPonyBtn, 'pony', 'Pony');

    // Lightbox Navigation
    elements.lightboxOverlay.addEventListener('click', closeLightbox);
    elements.lightboxClose.addEventListener('click', closeLightbox);
    elements.lightboxPrev.addEventListener('click', () => navigateLightbox(-1));
    elements.lightboxNext.addEventListener('click', () => navigateLightbox(1));

    // Fullscreen Photo Viewer Events
    elements.photoViewerClose.addEventListener('click', closePhotoViewer);
    elements.photoViewerOverlay.addEventListener('wheel', handleViewerWheel, { passive: false });
    elements.photoViewerOverlay.addEventListener('mousedown', handleViewerMouseDown);
    elements.photoViewerOverlay.addEventListener('click', handleViewerBackdropClick);
    document.addEventListener('mousemove', handleViewerMouseMove);
    document.addEventListener('mouseup', handleViewerMouseUp);
    elements.photoViewerOverlay.addEventListener('dblclick', handleViewerDblClick);

    // Delete Modal Actions
    elements.cancelDeleteBtn.addEventListener('click', closeDeleteModal);
    bindModalOverlayDismiss(elements.deleteConfirmModal, closeDeleteModal);
    elements.confirmDeleteBtn.addEventListener('click', async () => {
        if (state.photosToDelete && state.photosToDelete.length) {
            const paths = state.photosToDelete.slice();
            closeDeleteModal();
            await executeBulkDelete(paths);
            return;
        }
        if (state.photoToDelete) {
            const rel = state.photoToDelete.rel_path;
            closeDeleteModal();
            await deletePhoto(rel);
        }
    });

    // Create New Creator Modal Actions
    elements.newCreatorBtn.addEventListener('click', () => {
        elements.newCreatorInput.value = '';
        elements.newCreatorModal.style.display = 'flex';
        setTimeout(() => elements.newCreatorInput && elements.newCreatorInput.focus(), 0);
    });

    elements.cancelNewCreatorBtn.addEventListener('click', closeNewCreatorModal);
    bindModalOverlayDismiss(elements.newCreatorModal, closeNewCreatorModal);

    elements.confirmNewCreatorBtn.addEventListener('click', async () => {
        const name = elements.newCreatorInput.value.trim();
        if (!name) return;

        try {
            const res = await fetch('/api/creator/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name })
            });
            if (res.ok) {
                showToast(`Created folder @${name}`);
                closeNewCreatorModal();
                initApp();
            } else {
                showToast('Error creating folder');
            }
        } catch (err) {
            showToast('Request failed');
        }
    });

    if (elements.newCreatorInput) {
        elements.newCreatorInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                elements.confirmNewCreatorBtn.click();
            }
        });
    }

    // Upload Modal Actions
    elements.uploadPhotoBtn.addEventListener('click', () => {
        elements.uploadModal.style.display = 'flex';
    });

    elements.cancelUploadBtn.addEventListener('click', closeUploadModal);
    bindModalOverlayDismiss(elements.uploadModal, closeUploadModal);

    elements.confirmUploadBtn.addEventListener('click', async () => {
        const creator = elements.uploadCreatorSelect.value;
        const file = elements.uploadFileInput.files[0];

        if (!creator || !file) {
            showToast('Please select a creator and pick an image file');
            return;
        }

        const formData = new FormData();
        formData.append('creator', creator);
        formData.append('file', file);

        try {
            const res = await fetch('/api/photo/upload', {
                method: 'POST',
                body: formData
            });

            if (res.ok) {
                showToast(`Uploaded ${file.name} to @${creator}`);
                closeUploadModal();
                elements.uploadFileInput.value = '';
                initApp();
            } else {
                showToast('Error uploading image');
            }
        } catch (err) {
            showToast('Upload failed');
        }
    });

    // Sync modal: overlay dismiss (Close button already wired)
    bindModalOverlayDismiss(elements.syncModal, closeSyncModal);

    // Creator options panel: click outside sidebar actions dismisses options (keeps filter)
    document.addEventListener('pointerdown', (e) => {
        if (!state.creatorPanelOpen) return;
        if (isBlockingOverlayOpen()) return;
        const t = e.target;
        if (!(t instanceof Element)) return;
        if (t.closest('#creatorStylePanel')) return;
        if (t.closest('.creator-item')) return;
        hideCreatorStylePanel();
    });

    // Keyboard Navigation — Escape priority:
    // Photo Viewer > Delete > Lightbox > Sync > Upload > New Creator
    //   > Selection > Select mode > Review mode > Creator panel
    // Video: ←/→ seek (hold repeats smoothly), Space play/pause, Shift+←/→ prev/next media
    document.addEventListener('keydown', (e) => {
        if (elements.photoViewerOverlay.style.display === 'flex') {
            if (e.key === 'Escape') {
                e.stopPropagation();
                closePhotoViewer();
                return;
            }
            // Fullscreen video — same element as lightbox (teleported)
            if (isFullscreenVideoActive() && !e.ctrlKey && !e.metaKey && !e.altKey) {
                const v = activeVideoEl();
                if (v && e.key === 'ArrowLeft') {
                    e.preventDefault();
                    const step = e.repeat ? VIDEO_SEEK_HOLD_SECONDS : VIDEO_SEEK_SECONDS;
                    seekVideoElement(v, -step);
                    updateFsSeekUi();
                    return;
                }
                if (v && e.key === 'ArrowRight') {
                    e.preventDefault();
                    const step = e.repeat ? VIDEO_SEEK_HOLD_SECONDS : VIDEO_SEEK_SECONDS;
                    seekVideoElement(v, step);
                    updateFsSeekUi();
                    return;
                }
                if (v && (e.key === ' ' || e.code === 'Space')) {
                    e.preventDefault();
                    toggleVideoPlayback(v);
                    updateFsPlayPauseUi();
                    return;
                }
            }
            return;
        }

        if (elements.deleteConfirmModal.style.display === 'flex') {
            if (e.key === 'Escape') {
                closeDeleteModal();
                return;
            }
        }

        // Above the lightbox: the output detail can be opened from the outputs
        // view with no lightbox behind it, and a modal with no keyboard exit is
        // the same trap review mode was.
        if (elements.outputDetailModal
            && elements.outputDetailModal.style.display === 'flex') {
            if (e.key === 'Escape') {
                closeOutputDetail();
                return;
            }
        }

        if (elements.lightboxModal.style.display === 'flex') {
            if (e.key === 'Escape') {
                closeLightbox();
                return;
            }
            // Don't steal typing when editing prompts
            const editing = document.activeElement === elements.positivePromptText
                || document.activeElement === elements.negativePromptText
                || document.activeElement === elements.searchInput
                || (document.activeElement && document.activeElement.isContentEditable);
            if (editing) return;

            // Video seek takes precedence over gallery navigation
            if (isLightboxVideoActive() && !e.ctrlKey && !e.metaKey && !e.altKey) {
                const v = activeVideoEl();
                if (v && e.key === 'ArrowLeft' && !e.shiftKey) {
                    e.preventDefault();
                    const step = e.repeat ? VIDEO_SEEK_HOLD_SECONDS : VIDEO_SEEK_SECONDS;
                    seekVideoElement(v, -step);
                    return;
                }
                if (v && e.key === 'ArrowRight' && !e.shiftKey) {
                    e.preventDefault();
                    const step = e.repeat ? VIDEO_SEEK_HOLD_SECONDS : VIDEO_SEEK_SECONDS;
                    seekVideoElement(v, step);
                    return;
                }
                if (v && (e.key === ' ' || e.code === 'Space') && !e.shiftKey) {
                    e.preventDefault();
                    toggleVideoPlayback(v);
                    return;
                }
                // Shift+arrows still jump to prev/next media while a video is open
                if (e.key === 'ArrowLeft' && e.shiftKey) {
                    e.preventDefault();
                    navigateLightbox(-1);
                    return;
                }
                if (e.key === 'ArrowRight' && e.shiftKey) {
                    e.preventDefault();
                    navigateLightbox(1);
                    return;
                }
            } else {
                if (e.key === 'ArrowLeft') navigateLightbox(-1);
                if (e.key === 'ArrowRight') navigateLightbox(1);
            }

            if (!e.ctrlKey && !e.metaKey && !e.altKey) {
                // Triage shortcuts come first while reviewing: K/R/X are the
                // whole point of the mode, and 'c' (copy prompt) would
                // otherwise be the only letter doing real work in a pile of
                // forty rejects.
                if (handleTriageKey(e)) {
                    e.preventDefault();
                    return;
                }
                // After triage, which owns K/R/X in review mode — a reject
                // sweep must not be reinterpreted as rating a generation.
                if (handleGenerationRatingKey(e)) {
                    e.preventDefault();
                    return;
                }
                const key = e.key.toLowerCase();
                if (key === 'g') {
                    e.preventDefault();
                    handleGeneratePrompt(false);
                } else if (key === 'c' && state.currentPromptData) {
                    e.preventDefault();
                    const text = elements.positivePromptText.innerText.trim()
                        || state.currentPromptData.positive_prompt;
                    copyToClipboard(text, 'Copied Positive Prompt');
                } else if (key === 's' && state.currentPromptData && state.promptDirty) {
                    e.preventDefault();
                    savePromptEdits();
                } else if (key === 'f') {
                    e.preventDefault();
                    toggleFavoriteCurrent();
                }
            }
            return;
        }

        if (e.key === 'Escape' && isDisplayFlex(elements.syncModal)) {
            closeSyncModal();
            return;
        }
        if (e.key === 'Escape' && isDisplayFlex(elements.uploadModal)) {
            closeUploadModal();
            return;
        }
        if (e.key === 'Escape' && isDisplayFlex(elements.newCreatorModal)) {
            closeNewCreatorModal();
            return;
        }
        if (e.key === 'Escape' && isDisplayFlex(elements.trashModal)) {
            closeTrashModal();
            return;
        }
        // Transient gallery modes unwind before the persistent side panel.
        // The creator-panel branch used to sit above this one and `return`, and
        // enterReviewMode() sets creatorPanelOpen — so arriving from the
        // classify toast killed the only keyboard way out of select mode.
        // Peel one layer per press: selection → select mode → review mode.
        if (e.key === 'Escape' && state.selectMode) {
            if (state.selectedPaths.size) clearSelection();
            else setSelectMode(false);
            updateReviewBar();
            return;
        }
        if (e.key === 'Escape' && state.reviewMode) {
            exitReviewMode();
            return;
        }

        if (e.key === 'Escape' && state.creatorPanelOpen) {
            hideCreatorStylePanel();
            return;
        }
    });
}

/**
 * Toast: showToast('message') or showToast({ title, body, variant, duration })
 */
function showToast(messageOrOpts, duration = 3000) {
    if (!elements.toastContainer) return null;
    const toast = document.createElement('div');
    let durationMs = duration;
    let actionLabel = null;
    let onAction = null;
    if (messageOrOpts && typeof messageOrOpts === 'object') {
        const { title, body, variant, duration: d } = messageOrOpts;
        durationMs = d != null ? d : 3500;
        actionLabel = messageOrOpts.actionLabel || null;
        onAction = typeof messageOrOpts.onAction === 'function' ? messageOrOpts.onAction : null;
        toast.className = `toast${variant ? ` ${variant}` : ''}`;
        if (title && body) {
            toast.innerHTML = `<div class="toast-title"></div><div class="toast-body"></div>`;
            toast.querySelector('.toast-title').textContent = title;
            toast.querySelector('.toast-body').textContent = body;
        } else {
            toast.textContent = title || body || '';
        }
    } else {
        toast.className = 'toast';
        toast.textContent = String(messageOrOpts ?? '');
    }

    if (actionLabel && onAction) {
        const actionRow = document.createElement('div');
        actionRow.className = 'toast-actions';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'toast-action-btn';
        btn.textContent = actionLabel;
        btn.addEventListener('click', () => {
            toast.remove();
            onAction();
        });
        actionRow.appendChild(btn);
        toast.appendChild(actionRow);
    }

    elements.toastContainer.appendChild(toast);
    // Keep the stack readable — batch polls can push many toasts
    while (elements.toastContainer.children.length > 4) {
        elements.toastContainer.firstElementChild.remove();
    }
    if (durationMs > 0) {
        setTimeout(() => {
            toast.remove();
        }, durationMs);
    }
    return toast;
}

// Instagram sync helpers
function openSyncModal() {
    elements.syncModal.style.display = 'flex';
    pollSyncStatus();
    ensureScrapePolling();
    loadFollowingPicker();
}

/**
 * One-shot jobs (Saved / Following) need the exclusive SyncManager worker.
 * They stay disabled while a creator-queue job is running or pending.
 * "Sync Feed" is NOT one-shot — it enqueues like Enqueue and stays enabled.
 */
function setOneShotSyncEnabled(enabled, reason) {
    const tip = enabled ? '' : (reason || 'Creator scrape queue is busy — pause, clear pending, or wait');
    [elements.syncSavedBtn, elements.syncFollowingBtn].forEach((btn) => {
        if (!btn) return;
        btn.disabled = !enabled;
        btn.title = tip;
    });
    // Sync Feed = enqueue path; always leave usable for new handles
    if (elements.syncCreatorBtn) {
        elements.syncCreatorBtn.disabled = false;
        elements.syncCreatorBtn.title = 'Enqueue this creator (full archive) — works even if the queue is busy';
    }
    if (elements.syncOneShotBanner) {
        if (enabled) {
            elements.syncOneShotBanner.style.display = 'none';
            elements.syncOneShotBanner.textContent = '';
        } else {
            elements.syncOneShotBanner.style.display = 'block';
            elements.syncOneShotBanner.textContent =
                tip + ' · Use Enqueue (top) for new handles. Clear pending or Cancel job to free Saved/Following.';
        }
    }
}

/** Every lane chip currently in the DOM, keyed by source. */
const _laneChips = new Map();

/**
 * Element ids for one lane's chip.
 *
 * Instagram keeps the original unsuffixed ids. It is the primary lane, the
 * only one a single-source archive has, and every existing selector — the UI
 * suites included — is written against those names.
 */
function laneChipId(source, part) {
    const base = source === 'instagram' ? 'scrapeJobChip' : `scrapeJobChip_${source}`;
    return part ? base + part : base;
}

function hideScrapeJobChip(source) {
    if (source) {
        const chip = _laneChips.get(source);
        if (chip) chip.root.style.display = 'none';
        return;
    }
    _laneChips.forEach((chip) => { chip.root.style.display = 'none'; });
}

/**
 * Create (or fetch) the chip for one lane.
 *
 * Listeners are bound once at creation and close over the lane name, which is
 * what makes Cancel and Resume lane-scoped — the thing the shared static chip
 * could not express.
 */
function ensureLaneChip(source) {
    const existing = _laneChips.get(source);
    if (existing) return existing;
    const tpl = elements.scrapeLaneChipTemplate;
    const host = elements.scrapeLaneChips;
    if (!tpl || !host) return null;

    const root = tpl.content.firstElementChild.cloneNode(true);
    root.id = laneChipId(source);
    root.dataset.source = source;
    const pick = (role) => root.querySelector(`[data-role="${role}"]`);
    const chip = {
        source,
        root,
        icon: pick('icon'),
        title: pick('title'),
        sub: pick('sub'),
        resume: pick('resume'),
        cancel: pick('cancel'),
        dismiss: pick('dismiss'),
    };
    // Stable ids per part, so tests and any external selector can address a
    // specific lane rather than "whichever chip is first".
    if (chip.icon) chip.icon.id = laneChipId(source, 'Icon');
    if (chip.title) chip.title.id = laneChipId(source, 'Title');
    if (chip.sub) chip.sub.id = laneChipId(source, 'Sub');
    if (chip.resume) chip.resume.id = laneChipId(source, 'Resume');
    if (chip.cancel) chip.cancel.id = laneChipId(source, 'Cancel');
    if (chip.dismiss) chip.dismiss.id = laneChipId(source, 'Dismiss');

    if (chip.resume) {
        chip.resume.addEventListener('click', () => resumeScrapeQueue(source));
    }
    if (chip.cancel) {
        chip.cancel.addEventListener('click', () => cancelRunningSyncJob(source));
    }
    if (chip.dismiss) {
        chip.dismiss.addEventListener('click', () => {
            state.scrapeChipDismissed.add(source);
            hideScrapeJobChip(source);
        });
    }
    host.appendChild(root);
    _laneChips.set(source, chip);
    return chip;
}

function isLaneActive(lane) {
    if (!lane) return false;
    return !!(
        lane.running_job ||
        (lane.pending || []).length ||
        lane.paused ||
        (lane.sync && lane.sync.running)
    );
}

function isScrapeOrSyncActive(data) {
    if (!data) return false;
    return laneViews(data).some(isLaneActive);
}

/**
 * Normalise `/api/scrape/status` into one view per lane.
 *
 * Falls back to synthesising a single Instagram lane from the flat keys, so a
 * response from a pre-lane server (or a stubbed one) still renders.
 */
function laneViews(data) {
    if (!data) return [];
    const sync = data.sync || {};
    const lanes = data.lanes;
    if (lanes && typeof lanes === 'object' && Object.keys(lanes).length) {
        const syncLanes = (sync && sync.lanes) || {};
        return Object.keys(lanes).sort().map((name) => ({
            source: name,
            ...lanes[name],
            sync: syncLanes[name] || {},
        }));
    }
    return [
        {
            source: 'instagram',
            pending: data.pending || [],
            running_job: data.running_job || null,
            paused: Boolean(data.paused),
            pause_reason: data.pause_reason || '',
            sync,
        },
    ];
}

function laneLabel(source) {
    const meta = (state.knownSources || []).find((s) => s.name === source);
    if (meta && meta.label) return meta.label;
    return source.charAt(0).toUpperCase() + source.slice(1);
}

/** Render one chip per active lane, removing chips for lanes that went idle. */
function updateScrapeJobChip(data) {
    const views = laneViews(data);
    const seen = new Set();

    views.forEach((lane) => {
        if (!isLaneActive(lane)) return;
        seen.add(lane.source);
        renderOneLaneChip(lane);
    });

    _laneChips.forEach((chip, source) => {
        if (!seen.has(source)) {
            chip.root.style.display = 'none';
            state.scrapeChipDismissed.delete(source);
        }
    });

    updateQueueModalButtons(data);
}

function renderOneLaneChip(lane) {
    const chip = ensureLaneChip(lane.source);
    if (!chip) return;

    const pending = lane.pending || [];
    const running = lane.running_job;
    const paused = Boolean(lane.paused);
    const sync = lane.sync || {};

    // A dismissed chip stays hidden only while the lane is merely queued —
    // it comes back the moment that lane actually starts work again.
    if (state.scrapeChipDismissed.has(lane.source)) {
        if (!running && !sync.running && !paused) {
            chip.root.style.display = 'none';
            return;
        }
        state.scrapeChipDismissed.delete(lane.source);
    }

    chip.root.style.display = 'flex';
    chip.root.classList.toggle('paused', paused);

    if (chip.icon) {
        chip.icon.className =
            'fa-solid scrape-job-chip-icon ' +
            (paused ? 'fa-pause' : 'fa-arrows-rotate spinning');
    }

    const label = laneLabel(lane.source);
    let title = `${label} idle`;
    if (paused) {
        title = `${label} paused${lane.pause_reason ? ' — ' + lane.pause_reason : ''}`;
    } else if (running) {
        title = `${label} — @${running.username}`;
    } else if (sync.running && sync.scrape_username) {
        title = `${label} — @${sync.scrape_username}`;
    } else if (sync.running) {
        const jt = sync.job_type ? String(sync.job_type).replace(/_/g, ' ') : 'sync';
        title = sync.progress || `${label} ${jt} running…`;
    } else if (pending.length) {
        title = `${label} queued — @${pending[0].username}`;
    }

    let sub = '';
    let fullSub = '';
    if (running) {
        const mode = running.mode || 'full';
        const deep = running.deep === true ? ' deep' : '';
        sub = mode + deep;
        fullSub = sub;
        if (pending.length) {
            sub += ` · +${pending.length} queued`;
            fullSub += ` · +${pending.length} queued`;
        }
        if (sync.progress) {
            const p = String(sync.progress);
            fullSub += ` · ${p}`;
            sub += ` · ${p.length > 80 ? '…' + p.slice(-79) : p}`;
        }
    } else if (sync.running) {
        sub = sync.progress || sync.job_type || 'in progress';
        fullSub = sub;
        if (pending.length) {
            sub += ` · +${pending.length} queued`;
            fullSub += ` · +${pending.length} queued`;
        }
    } else if (paused) {
        sub = pending.length
            ? `${pending.length} waiting — press Resume to continue`
            : 'Press Resume to continue';
        fullSub = sub;
    } else if (pending.length) {
        sub = `position 1 · ${pending.length} in queue`;
        fullSub = sub;
    }

    // textContent, not innerHTML: usernames and pause reasons are third-party
    // text (a pause reason can be a verbatim gallery-dl line).
    if (chip.title) chip.title.textContent = title;
    if (chip.sub) chip.sub.textContent = sub;
    chip.root.title = `${title}\n${fullSub}`;

    // Resume only when this lane is paused. Cancel only while it is running.
    if (chip.resume) chip.resume.style.display = paused ? '' : 'none';
    if (chip.cancel) {
        chip.cancel.style.display =
            running || (sync.running && sync.job_type === 'creator_queue') ? '' : 'none';
    }
}

/** Modal Pause/Resume reflect the whole queue, so they use the union view. */
function updateQueueModalButtons(data) {
    const views = laneViews(data);
    const anyRunning = views.some((l) => l.running_job || (l.sync && l.sync.running));
    const anyPending = views.some((l) => (l.pending || []).length);
    const anyPaused = views.some((l) => l.paused);
    const allPaused = views.length > 0 && views.every((l) => l.paused);

    if (elements.scrapePauseBtn) {
        elements.scrapePauseBtn.disabled =
            allPaused || (!anyRunning && !anyPending);
        elements.scrapePauseBtn.title = allPaused
            ? 'Every queue is already paused'
            : 'Pause after current job';
    }
    if (elements.scrapeResumeBtn) {
        elements.scrapeResumeBtn.disabled = !anyPaused;
        elements.scrapeResumeBtn.title = anyPaused
            ? 'Resume paused queues and start the next job'
            : 'No queue is paused';
    }
}

function toastForEnqueueResult(username, data) {
    const st = data.status || 'queued';
    if (st === 'started') {
        showToast({
            title: `Syncing @${username}`,
            body: 'Full feed — all missing posts (skips existing)…',
            variant: 'info',
            duration: 4500,
        });
    } else if (st === 'queued') {
        const pos = data.position != null ? data.position : '?';
        showToast({
            title: `Queued @${username}`,
            body: `Waiting · position ${pos}`,
            variant: 'info',
            duration: 4000,
        });
    } else if (st === 'already_running') {
        showToast({
            title: `Already syncing @${username}`,
            body: 'This creator is already running',
            variant: 'info',
        });
    } else if (st === 'already_pending') {
        showToast({
            title: `@${username} already queued`,
            body: 'Waiting for its turn',
            variant: 'info',
        });
    } else {
        showToast({ title: `@${username}`, body: st, variant: 'info' });
    }
}

function toastForFinishedJob(job, result) {
    if (!job) return;
    const user = job.username || 'creator';
    const st = job.status || '';
    if (st === 'done') {
        const r = result || job.result || {};
        const n = r.downloaded != null ? r.downloaded : 0;
        const sk = r.skipped != null ? r.skipped : 0;
        const del = r.skipped_deleted ? ` · ${r.skipped_deleted} deleted` : '';
        showToast({
            title: `@${user} sync complete`,
            body: `${n} new · ${sk} skipped${del}`,
            variant: 'success',
            duration: 4500,
        });
    } else if (st === 'cancelled') {
        showToast({
            title: `@${user} sync cancelled`,
            body: job.error || 'Stopped',
            variant: 'info',
        });
    } else if (st === 'error') {
        showToast({
            title: `@${user} sync stopped`,
            body: job.error || job.stop_reason || 'Error',
            variant: 'error',
            duration: 5000,
        });
    }
}

async function refreshScrapeStatusOnce() {
    try {
        const res = await fetch('/api/scrape/status');
        if (res.status === 404) {
            // Queue disabled — still try generic sync status for chip
            return await refreshGenericSyncForChip();
        }
        const data = await res.json();
        state.scrapeStatus = data;

        const pending = data.pending || [];
        const running = data.running_job;
        const paused = Boolean(data.paused);
        const sync = data.sync || {};
        const active = isScrapeOrSyncActive(data);

        // Modal-only queue panel (if open)
        const lanes = laneViews(data);
        const activeLanes = lanes.filter(isLaneActive);
        if (elements.scrapeQueueStatus) {
            // One clause per busy lane, because "Running @nina" no longer
            // describes the queue when three platforms can run at once.
            const parts = activeLanes.map((lane) => {
                const label = laneLabel(lane.source);
                if (lane.paused) {
                    return `${label} paused — ${lane.pause_reason || 'paused'}`;
                }
                if (lane.running_job) {
                    return `${label} @${lane.running_job.username} (${lane.running_job.mode || 'full'})`;
                }
                const n = (lane.pending || []).length;
                return n ? `${label} ${n} pending` : `${label} idle`;
            });
            let line = parts.length ? parts.join(' · ') : 'Queues idle';
            if (sync.running && sync.progress) line += ` · ${sync.progress}`;
            if (data.stats) {
                line += ` · today ${data.stats.completed_today || 0} jobs / ${data.stats.downloaded_today || 0} files`;
            }
            elements.scrapeQueueStatus.textContent = line;
        }
        if (elements.scrapeQueueList) {
            const rows = [];
            // Grouped by lane so the reading order matches the chip stack.
            lanes.forEach((lane) => {
                const tag = laneLabel(lane.source);
                if (lane.running_job) {
                    const j = lane.running_job;
                    rows.push(
                        `▶ [${tag}] @${j.username} [${j.mode}${j.deep ? ' deep' : ''}] running`
                    );
                }
                (lane.pending || []).slice(0, 8).forEach((j, i) => {
                    rows.push(`${i + 1}. [${tag}] @${j.username} [${j.mode}] prio ${j.priority || 0}`);
                });
            });
            (data.history || []).slice(0, 5).forEach((j) => {
                const tag = laneLabel(j.source || 'instagram');
                rows.push(
                    `✓ [${tag}] @${j.username} → ${j.status}${j.stop_reason ? ' (' + j.stop_reason + ')' : ''}`
                );
            });
            elements.scrapeQueueList.innerHTML = rows.length
                ? rows.map((r) => `<div class="scrape-queue-row">${escapeHtml(r)}</div>`).join('')
                : '<div class="scrape-queue-row" style="opacity:0.55">No jobs yet — add a creator below</div>';
        }

        // Saved and Following are Instagram-only routes, so only the Instagram
        // lane can block them. A busy Reddit lane used to grey them out purely
        // because there was one global worker.
        const ig = lanes.find((l) => l.source === 'instagram') || {};
        const igPending = ig.pending || [];
        const igRunning = ig.running_job;
        const igSync = ig.sync || sync;
        const workerBusy =
            Boolean(igRunning) ||
            Boolean(igSync.running) ||
            (igPending.length > 0 && !ig.paused);
        let busyReason = '';
        if (igRunning) {
            busyReason = `Scraping @${igRunning.username} now` +
                (igPending.length ? ` · ${igPending.length} more queued` : '') +
                ' — wait, Cancel job, or Clear pending';
        } else if (igSync.running) {
            busyReason = `Sync busy (${igSync.job_type || 'job'}) — wait or cancel`;
        } else if (igPending.length > 0 && !ig.paused) {
            busyReason =
                `Instagram scrape queue has ${igPending.length} pending — Clear pending or Pause queue first`;
        }
        setOneShotSyncEnabled(!workerBusy, busyReason);

        updateScrapeJobChip(data);
        updateSyncLatestButtonUi();

        // Creator list pills only when the active set changes — across every
        // lane, or a Reddit job starting would not refresh the sidebar.
        const pillKey = lanes
            .map((l) => [
                l.source,
                l.running_job && l.running_job.username,
                (l.pending || []).map((j) => j.username).join(','),
                l.paused ? '1' : '0',
                l.sync && l.sync.running ? 's1' : 's0',
            ].join('~'))
            .join('|');
        if (pillKey !== state._scrapePillKey) {
            state._scrapePillKey = pillKey;
            if (elements.creatorList) renderCreatorList();
        }

        // Completion toast from history
        const hist = (data.history || [])[0];
        if (hist && hist.finished_at && hist.finished_at !== state.scrapeNotifiedFinished) {
            const isNewEvent =
                state.scrapeNotifiedFinished != null || state.scrapeWasActive;
            // First observation of any history: seed baseline only if we haven't
            // started a job this session (avoids toasting ancient history on load).
            if (state.scrapeNotifiedFinished == null && !state.scrapeWasActive) {
                state.scrapeNotifiedFinished = hist.finished_at;
            } else if (isNewEvent) {
                toastForFinishedJob(hist, hist.result);
                const finishedUser = (hist.username || '').toLowerCase();
                const selected = (state.selectedCreator || '').toLowerCase();
                if (hist.status === 'done' && finishedUser && finishedUser === selected) {
                    await fetchPhotos();
                } else if (hist.status === 'done') {
                    try {
                        const cr = await fetch('/api/creators');
                        state.creators = await cr.json();
                        renderCreatorList();
                    } catch (e) { /* ignore */ }
                }
                state.scrapeNotifiedFinished = hist.finished_at;
            }
        }

        if (active) state.scrapeWasActive = true;

        // Stop polling when fully idle (unless Sync modal open for status panel)
        if (!active && state.scrapePollTimer && !isSyncModalOpen()) {
            clearInterval(state.scrapePollTimer);
            state.scrapePollTimer = null;
            state.scrapeWasActive = false;
        }

        return data;
    } catch (err) {
        console.error('Scrape status error:', err);
        return null;
    }
}

/** Fallback when scrape queue API is off — still show one-shot sync chip. */
async function refreshGenericSyncForChip() {
    try {
        const res = await fetch('/api/sync/status');
        if (!res.ok) return null;
        const sync = await res.json();
        const data = {
            pending: [],
            running_job: null,
            paused: false,
            sync,
            history: [],
            stats: {},
        };
        state.scrapeStatus = data;
        updateScrapeJobChip(data);
        return data;
    } catch (err) {
        return null;
    }
}

/**
 * On page load / hard refresh: reattach to any in-flight scrape queue or sync job.
 * Without this, the floating chip and creator pills vanish until the user clicks Sync again.
 */
async function hydrateScrapeUiFromServer() {
    state.scrapeChipDismissed.clear();
    const data = await refreshScrapeStatusOnce();
    if (!data) return null;

    // Seed completion baseline so reloading does not re-toast old finished jobs
    if (state.scrapeNotifiedFinished == null) {
        const hist = (data.history || [])[0];
        if (hist && hist.finished_at) {
            state.scrapeNotifiedFinished = hist.finished_at;
        }
    }

    if (isScrapeOrSyncActive(data)) {
        state.scrapeWasActive = true;
        ensureScrapePolling();
    }
    return data;
}

function isSyncModalOpen() {
    return elements.syncModal && elements.syncModal.style.display === 'flex';
}

function ensureScrapePolling() {
    if (state.scrapePollTimer) return;
    refreshScrapeStatusOnce();
    state.scrapePollTimer = setInterval(() => {
        refreshScrapeStatusOnce();
    }, 2500);
}

/** @deprecated use ensureScrapePolling — kept for modal call sites */
async function pollScrapeStatus() {
    ensureScrapePolling();
    await refreshScrapeStatusOnce();
}

/**
 * Sidebar “Sync new posts”: full-feed gap fill (mode=full, deep=true).
 * Avoids mode=latest + max_posts=50 which stops early and leaves older gaps
 * (Mikayla case: 50 newest filled, ~117 older posts never walked).
 */
async function syncLatestSelectedCreator() {
    const username = (state.selectedCreator || '').trim().replace(/^@/, '');
    if (!username) {
        showToast({ title: 'Select a creator first', variant: 'error' });
        return;
    }
    try {
        const res = await fetch('/api/scrape/enqueue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username,
                mode: 'full',
                deep: true,
                include_videos: true,
                // omit max_posts → server uses FULL_SCRAPE_MAX_POSTS (default 5000)
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast({
                title: 'Can’t sync',
                body: data.message || data.error || `Failed (${res.status})`,
                variant: 'error',
            });
            return;
        }
        // Mark so completion toast fires even for first job this session
        state.scrapeWasActive = true;
        // Only this lane: enqueueing on X should not un-hide a chip the user
        // deliberately dismissed on another platform.
        state.scrapeChipDismissed.delete(data.source || 'instagram');
        toastForEnqueueResult(username, data);
        ensureScrapePolling();
        await refreshScrapeStatusOnce();
        // Never open full Sync modal — chip + toast only
    } catch (err) {
        showToast({ title: 'Failed to start sync', variant: 'error' });
    }
}

/** Currently selected scrape mode from the segmented control. */
function selectedScrapeMode() {
    const checked = document.querySelector('input[name="scrapeMode"]:checked');
    return checked ? checked.value : 'full';
}

/**
 * Map the segmented control to the enqueue API.
 *
 * The API takes `mode` + `deep` + `catch_up_only`, and `mode: "latest"` is
 * silently upgraded to full+deep unless `catch_up_only` is set — which the old
 * checkbox UI could not send, so "Latest" never actually meant latest.
 */
function scrapeModePayload() {
    const mode = selectedScrapeMode();
    const maxPosts = parseInt(elements.scrapeMaxPosts?.value || '50', 10) || 50;
    if (mode === 'catch_up') {
        return { mode: 'latest', deep: false, catch_up_only: true, max_posts: maxPosts };
    }
    if (mode === 'bounded') {
        return { mode: 'bounded', deep: false, max_posts: maxPosts };
    }
    // Full archive: no ceiling, so the server applies FULL_SCRAPE_MAX_POSTS
    return { mode: 'full', deep: true };
}

/** Max-posts only applies to Catch-up and Bounded. */
function updateScrapeModeUi() {
    const mode = selectedScrapeMode();
    if (elements.scrapeMaxPostsRow) {
        elements.scrapeMaxPostsRow.style.display = mode === 'full' ? 'none' : '';
    }
    document.querySelectorAll('#scrapeModeGroup .segmented-option').forEach((opt) => {
        const input = opt.querySelector('input[name="scrapeMode"]');
        opt.classList.toggle('active', Boolean(input && input.checked));
    });
}

/**
 * Per-source input guidance. Reddit is topic-scoped (a subreddit becomes the
 * archive folder), which is different enough from a handle to be worth saying.
 */
const SCRAPE_SOURCE_META = {
    instagram: {
        placeholder: '@creator handle',
        hint: 'Jobs never run in parallel (Instagram rate limits).',
    },
    x: {
        placeholder: '@handle  or  x.com/handle',
        hint: 'Pulls the /media timeline. Needs X_COOKIES_FILE in .env — use a throwaway account. Lands in <handle>__x.',
    },
    reddit: {
        placeholder: 'r/subreddit  or  u/username',
        hint: 'Works without login. A subreddit becomes the folder (r_<sub>__reddit); the original poster is kept in each file’s metadata.',
    },
};

function scrapeSourceValue() {
    return (elements.scrapeSourceSelect?.value || 'instagram').trim().toLowerCase();
}

function updateScrapeSourceUI() {
    const meta = SCRAPE_SOURCE_META[scrapeSourceValue()] || SCRAPE_SOURCE_META.instagram;
    if (elements.scrapeCreatorInput) {
        elements.scrapeCreatorInput.placeholder = meta.placeholder;
    }
    if (elements.scrapeSourceHint) {
        elements.scrapeSourceHint.textContent = meta.hint;
    }
}

async function enqueueCreatorScrape() {
    const username = (elements.scrapeCreatorInput?.value || '').trim().replace(/^@/, '');
    if (!username) {
        showToast({
            title: 'Enter a handle',
            body: 'Type a creator above, or click someone in “From accounts you follow”.',
            variant: 'error',
        });
        elements.scrapeCreatorInput?.focus();
        return;
    }
    const body = {
        username,
        source: scrapeSourceValue(),
        ...scrapeModePayload(),
        include_videos: syncIncludeVideosValue(),
    };
    if (elements.scrapeEnqueueBtn) elements.scrapeEnqueueBtn.disabled = true;
    try {
        const res = await fetch('/api/scrape/enqueue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            // BaseHTTPRequestHandler send_error often returns HTML, not JSON
            let msg = data.message || data.error;
            if (!msg && typeof data === 'object' && !Object.keys(data).length) {
                msg = `Failed (${res.status})`;
            }
            showToast({
                title: 'Can’t add to queue',
                body: msg || `Failed (${res.status})`,
                variant: 'error',
            });
            return;
        }
        state.scrapeWasActive = true;
        // Only this lane: enqueueing on X should not un-hide a chip the user
        // deliberately dismissed on another platform.
        state.scrapeChipDismissed.delete(data.source || 'instagram');
        toastForEnqueueResult(username, data);
        if (elements.scrapeCreatorInput) elements.scrapeCreatorInput.value = '';
        if (elements.syncCreatorInput) elements.syncCreatorInput.value = '';
        if (elements.syncSelectedChip) {
            elements.syncSelectedChip.style.display = 'none';
            elements.syncSelectedChip.textContent = '';
        }
        elements.followingList?.querySelectorAll('.following-row.active').forEach((r) => {
            r.classList.remove('active');
        });
        ensureScrapePolling();
        await refreshScrapeStatusOnce();
        pollSyncStatus();
    } catch (err) {
        showToast({ title: 'Failed to add to queue', variant: 'error' });
    } finally {
        if (elements.scrapeEnqueueBtn) elements.scrapeEnqueueBtn.disabled = false;
    }
}

async function pauseScrapeQueue(source) {
    try {
        const res = await fetch('/api/scrape/pause', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(
                source ? { reason: 'Paused by user', source } : { reason: 'Paused by user' }
            ),
        });
        if (res.ok) {
            showToast(source ? `${laneLabel(source)} queue paused` : 'Scrape queues paused');
            pollScrapeStatus();
        } else showToast('Pause failed');
    } catch (e) {
        showToast('Pause failed');
    }
}

async function resumeScrapeQueue(source) {
    try {
        const res = await fetch('/api/scrape/resume', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            // Omitted source means every lane — what the modal button means.
            body: JSON.stringify(source ? { source } : {}),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            const who = source ? `${laneLabel(source)} queue` : 'Queues';
            showToast(data.drain_started ? `${who} resumed — starting next job` : `${who} resumed`);
            pollScrapeStatus();
            pollSyncStatus();
        } else showToast('Resume failed');
    } catch (e) {
        showToast('Resume failed');
    }
}

async function cancelRunningSyncJob(source) {
    try {
        // Cancel is lane-scoped now: stopping X must not stop a running
        // Reddit job. No source = the user asked to stop everything.
        const res = await fetch('/api/sync/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(source ? { source } : {}),
        });
        const data = await res.json().catch(() => ({}));
        const who = source ? ` for ${laneLabel(source)}` : '';
        if (data.status === 'cancelling') showToast(`Cancel requested${who}…`);
        else showToast(`No running sync job${who}`);
        pollSyncStatus();
        pollScrapeStatus();
    } catch (e) {
        showToast('Cancel failed');
    }
}

async function clearPendingScrapeJobs() {
    try {
        const res = await fetch('/api/scrape/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scope: 'all_pending' }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            showToast(`Cleared ${data.cancelled_pending || 0} pending job(s)`);
            pollScrapeStatus();
        } else showToast('Clear pending failed');
    } catch (e) {
        showToast('Clear pending failed');
    }
}

/**
 * Fill the primary "Add a creator" handle from a following-list pick.
 * Also mirrors into the hidden legacy syncCreatorInput for any old callers.
 */
function selectFollowingHandle(username, { enqueue = false, source = 'instagram' } = {}) {
    const handle = String(username || '').trim().replace(/^@/, '');
    if (!handle) return;
    if (elements.scrapeSourceSelect) {
        elements.scrapeSourceSelect.value = source;
        updateScrapeSourceUI();
    }
    if (elements.scrapeCreatorInput) {
        elements.scrapeCreatorInput.value = handle;
        elements.scrapeCreatorInput.focus();
        elements.scrapeCreatorInput.select();
    }
    if (elements.syncCreatorInput) {
        elements.syncCreatorInput.value = handle;
    }
    if (elements.syncSelectedChip) {
        elements.syncSelectedChip.style.display = 'flex';
        elements.syncSelectedChip.innerHTML =
            `<i class="fa-solid fa-check"></i> Selected <strong>@${escapeHtml(handle)}</strong>` +
            (enqueue ? ' · adding…' : ' — click <strong>Add to queue</strong> or double‑click the row');
    }
    if (enqueue) {
        enqueueCreatorScrape();
    }
}

async function loadFollowingPicker(search = '') {
    if (!elements.followingList) return;
    elements.followingList.innerHTML = '<div class="following-empty">Loading…</div>';
    if (state.followingRequest) state.followingRequest.abort();
    const controller = new AbortController();
    state.followingRequest = controller;
    try {
        const params = new URLSearchParams();
        if (search) params.set('search', search);
        params.set('limit', '100');
        const res = await fetch('/api/following?' + params.toString(), {
            signal: controller.signal
        });
        const data = await res.json();
        const accounts = data.accounts || [];
        if (!accounts.length) {
            elements.followingList.innerHTML =
                `<div class="following-empty">${data.total === 0 && !search
                    ? 'No following_list.json — run export_following_list.py'
                    : 'No matches'}</div>`;
            return;
        }
        elements.followingList.innerHTML = '';
        const current =
            (elements.scrapeCreatorInput?.value || '').trim().replace(/^@/, '').toLowerCase();
        accounts.forEach((acct) => {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'following-row';
            const username = acct.username || '';
            if (username && username.toLowerCase() === current) {
                row.classList.add('active');
            }
            const privateBadge = acct.is_private
                ? '<span class="following-private">Private</span>'
                : '';
            // full_name is arbitrary Instagram-supplied text — always escape
            row.innerHTML = `
                <div class="following-main">
                    <span class="following-user">@${escapeHtml(username)}</span>
                    <span class="following-name">${escapeHtml(acct.full_name || '')}</span>
                    ${privateBadge}
                </div>
                <div class="following-meta">${escapeHtml(acct.media_count ?? '—')} posts · ${escapeHtml(formatFollowers(acct.followers_count))}</div>
                <span class="following-use" aria-hidden="true">Use</span>
            `;
            row.title = `Fill @${username} into Add a creator (double-click to queue now)`;
            row.addEventListener('click', () => {
                elements.followingList.querySelectorAll('.following-row').forEach((r) => {
                    r.classList.remove('active');
                });
                row.classList.add('active');
                selectFollowingHandle(username, { enqueue: false });
            });
            row.addEventListener('dblclick', (e) => {
                e.preventDefault();
                elements.followingList.querySelectorAll('.following-row').forEach((r) => {
                    r.classList.remove('active');
                });
                row.classList.add('active');
                selectFollowingHandle(username, { enqueue: true });
            });
            elements.followingList.appendChild(row);
        });
    } catch (err) {
        if (err.name === 'AbortError') return;
        elements.followingList.innerHTML =
            '<div class="following-empty">Failed to load following list</div>';
    } finally {
        if (state.followingRequest === controller) {
            state.followingRequest = null;
        }
    }
}

function formatFollowers(n) {
    if (n == null || n === '') return '—';
    const num = Number(n);
    if (Number.isNaN(num)) return String(n);
    if (num >= 1e6) return (num / 1e6).toFixed(1) + 'M';
    if (num >= 1e3) return (num / 1e3).toFixed(1) + 'K';
    return String(num);
}

function closeSyncModal() {
    elements.syncModal.style.display = 'none';
    if (state.syncPollTimer) {
        clearInterval(state.syncPollTimer);
        state.syncPollTimer = null;
    }
    // Keep scrape polling if a job is still active (chip UX); else stop
    const scrape = state.scrapeStatus;
    const stillActive =
        scrape &&
        (scrape.running_job ||
            (scrape.pending && scrape.pending.length) ||
            scrape.paused ||
            (scrape.sync && scrape.sync.running && scrape.sync.job_type === 'creator_queue'));
    if (!stillActive && state.scrapePollTimer) {
        clearInterval(state.scrapePollTimer);
        state.scrapePollTimer = null;
    }
}

async function pollSyncStatus() {
    const update = async () => {
        try {
            const res = await fetch('/api/sync/status');
            const data = await res.json();
            const cq = data.creator_queue || {};
            // Prefer scrape-status polling when active; only set one-shot lock here as fallback
            if (!state.scrapePollTimer) {
                const busy =
                    Boolean(data.running) ||
                    (cq.enabled !== false &&
                        ((cq.pending_count > 0 && !cq.paused) || cq.current_username));
                let reason = '';
                if (data.running && data.scrape_username) {
                    reason = `Scraping @${data.scrape_username} — wait or cancel`;
                } else if (cq.current_username) {
                    reason = `Scraping @${cq.current_username}` +
                        (cq.pending_count ? ` · ${cq.pending_count} queued` : '');
                } else if (cq.pending_count > 0 && !cq.paused) {
                    reason = `Creator scrape queue has ${cq.pending_count} pending — Clear pending or Pause first`;
                } else if (data.running) {
                    reason = `Sync busy (${data.job_type || 'job'})`;
                }
                setOneShotSyncEnabled(!busy, reason);
            }
            if (elements.syncRateMeta) {
                const hits = data.rate_limit_hits || 0;
                const streak = data.consecutive_rate_limits || 0;
                const backoff = data.last_backoff_sec || 0;
                let meta = hits
                    ? `Rate limits: ${hits} · streak ${streak}` + (backoff ? ` · last wait ${backoff}s` : '')
                    : '';
                if (cq.pending_count || cq.current_username) {
                    meta += (meta ? ' · ' : '') +
                        `scrape queue: ${cq.pending_count || 0} pending` +
                        (cq.current_username ? ` · @${cq.current_username}` : '') +
                        (cq.paused ? ' · PAUSED' : '');
                }
                if (data.cancel_requested) meta += (meta ? ' · ' : '') + 'cancel requested';
                elements.syncRateMeta.textContent = meta;
            }
            if (data.running) {
                elements.syncStatusText.textContent = data.progress || 'Running...';
                elements.syncProgressFill.style.width = '60%';
                elements.syncProgressFill.style.background = '';
            } else if (data.error) {
                elements.syncStatusText.textContent = `Failed: ${data.error}`;
                elements.syncProgressFill.style.width = '100%';
                elements.syncProgressFill.style.background = 'var(--accent-red)';
            } else if (data.result) {
                const r = data.result;
                let isNewResult = false;
                
                // Seed the baseline on first poll so we don't re-toast old finished jobs on page load
                if (state.lastSyncFinishedAt === undefined) {
                    state.lastSyncFinishedAt = data.finished_at;
                } else if (state.lastSyncFinishedAt !== data.finished_at) {
                    isNewResult = true;
                }

                if (r.aborted) {
                    elements.syncStatusText.textContent =
                        `Aborted — ${r.abort_reason || 'stopped for safety'} ` +
                        `(${r.downloaded || 0} new, ${r.accounts_processed || 0} accounts)`;
                    elements.syncProgressFill.style.width = '100%';
                    elements.syncProgressFill.style.background = 'var(--accent-red)';
                    if (isNewResult) {
                        showToast(r.abort_reason || 'Following sync aborted', 4000);
                    }
                } else {
                    const qs = r.queue_summary;
                    const queueBit = qs
                        ? ` · queue ${qs.pending ?? '?'} pending, today ${qs.accounts_today ?? 0}/${qs.daily_cap ?? '?'}`
                        : '';
                    const stopBit = r.stop_reason ? ` · ${r.stop_reason}` : '';
                    elements.syncStatusText.textContent =
                        `Done — ${r.downloaded || 0} new, ${r.skipped || 0} skipped` +
                        (r.accounts_processed ? `, ${r.accounts_processed} accounts` : '') +
                        (r.rate_limit_hits ? `, ${r.rate_limit_hits} rate limits` : '') +
                        queueBit + stopBit;
                    elements.syncProgressFill.style.width = '100%';
                }
                
                if (isNewResult) {
                    state.lastSyncFinishedAt = data.finished_at;
                    await initApp();
                }
            } else {
                elements.syncStatusText.textContent = 'Idle';
                elements.syncProgressFill.style.width = '0%';
            }
        } catch (err) {
            console.error('Sync status error:', err);
        }
    };
    await update();
    if (state.syncPollTimer) clearInterval(state.syncPollTimer);
    state.syncPollTimer = setInterval(update, 2500);
}

async function startSyncSaved() {
    try {
        const res = await fetch('/api/sync/saved', { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showToast('Instagram saved sync started');
            pollSyncStatus();
        } else {
            showToast(data.message || 'Sync already running');
        }
    } catch (err) {
        showToast('Failed to start sync');
    }
}

function syncIncludeVideosValue() {
    // Default ON when checkbox missing (matches IG_INCLUDE_VIDEOS default)
    if (!elements.syncIncludeVideos) return true;
    return Boolean(elements.syncIncludeVideos.checked);
}

async function startSyncCreator() {
    // Legacy "Sync Feed" — now aliases the primary enqueue path.
    // Prefer scrapeCreatorInput; fall back to hidden syncCreatorInput.
    const fromPrimary = (elements.scrapeCreatorInput?.value || '').trim().replace(/^@/, '');
    const fromLegacy = (elements.syncCreatorInput?.value || '').trim().replace(/^@/, '');
    const username = fromPrimary || fromLegacy;
    if (!username) {
        showToast('Enter a creator handle');
        return;
    }
    if (elements.scrapeCreatorInput) elements.scrapeCreatorInput.value = username;
    if (elements.scrapeSourceSelect) {
        elements.scrapeSourceSelect.value = 'instagram';
        updateScrapeSourceUI();
    }
    // Full archive when using this alias (matches prior Sync Feed behaviour)
    const fullRadio = document.getElementById('scrapeModeFull');
    if (fullRadio) {
        fullRadio.checked = true;
        updateScrapeModeUi();
    }
    await enqueueCreatorScrape();
}

async function startSyncFollowing() {
    const maxAccounts = parseInt(elements.syncFollowMaxAccounts?.value || '20', 10);
    const maxPosts = parseInt(elements.syncFollowMaxPosts?.value || '20', 10);
    const keywords = (elements.syncFollowKeywords?.value || '').trim();
    const body = {
        max_accounts: maxAccounts || 20,
        accounts_per_day: maxAccounts || 20,
        max_posts: maxPosts || 20,
        include_videos: syncIncludeVideosValue(),
    };
    if (keywords) body.keywords = keywords;
    try {
        const res = await fetch('/api/sync/following', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await res.json();
        if (res.ok) {
            showToast('Following sync started');
            pollSyncStatus();
        } else {
            showToast(data.message || 'Sync busy');
        }
    } catch (err) {
        showToast('Failed to start following sync');
    }
}

/**
 * Render one job chip. Long jobs used to report progress by firing a toast
 * every 3–4 seconds for their whole run — hours, for a big batch. A persistent
 * chip shows the same information without the spam, and gives cancel a home.
 */
function renderJobChip(kind, { active, title, sub, completed, total, cancellable, cancelled }) {
    const chip = elements[`${kind}JobChip`];
    if (!chip) return;
    if (!active) {
        chip.style.display = 'none';
        return;
    }
    chip.style.display = 'flex';

    const titleEl = elements[`${kind}JobChipTitle`];
    const subEl = elements[`${kind}JobChipSub`];
    const fillEl = elements[`${kind}JobChipFill`];
    const iconEl = elements[`${kind}JobChipIcon`];
    const cancelEl = elements[`${kind}JobChipCancel`];

    if (titleEl) titleEl.textContent = title;
    if (subEl) subEl.textContent = sub || '';
    if (fillEl) {
        const pct = total ? Math.min(100, Math.round((completed / total) * 100)) : 0;
        fillEl.style.width = `${pct}%`;
    }
    if (iconEl) iconEl.classList.toggle('spinning', !cancelled);
    if (cancelEl) {
        cancelEl.style.display = cancellable ? '' : 'none';
        cancelEl.disabled = Boolean(cancelled);
        cancelEl.textContent = cancelled ? 'Stopping…' : 'Cancel';
    }
}

function jobPct(completed, total) {
    return total ? Math.round((completed / total) * 100) : 0;
}

async function pollBatchStatus() {
    try {
        const res = await fetch('/api/prompt/batch/status');
        const data = await res.json();
        if (data.running) {
            const done = data.completed + (data.failed || 0);
            renderJobChip('batch', {
                active: true,
                title: data.cancel_requested ? 'Batch analyze — stopping' : 'Batch analyze',
                sub: `${done}/${data.total} (${jobPct(done, data.total)}%)` +
                    (data.failed ? ` · ${data.failed} failed` : ''),
                completed: done,
                total: data.total,
                cancellable: true,
                cancelled: Boolean(data.cancel_requested)
            });
            if (!state.batchPollTimer) {
                state.batchPollTimer = setInterval(pollBatchStatus, 4000);
            }
        } else {
            if (state.batchPollTimer) {
                clearInterval(state.batchPollTimer);
                state.batchPollTimer = null;
            }
            renderJobChip('batch', { active: false });
            // Only announce on a running → stopped transition we observed
            if (state.batchWasRunning) {
                if (data.cancelled) {
                    showToast({
                        title: 'Batch cancelled',
                        body: `${data.completed} analyzed · ${data.pending || 0} left unanalyzed`
                    });
                } else {
                    showToast({
                        title: 'Batch complete',
                        body: `${data.completed} analyzed` + (data.failed ? ` · ${data.failed} failed` : ''),
                        variant: 'success'
                    });
                }
                await fetchStats();
                await fetchPhotos();
            }
        }
        state.batchWasRunning = Boolean(data.running);
    } catch (err) {
        console.error('Batch status error:', err);
    }
}

async function cancelBatchAnalyze() {
    try {
        const res = await fetch('/api/prompt/batch/cancel', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        showToast(
            data.status === 'cancelling'
                ? 'Stopping after the current photo…'
                : 'No batch job running'
        );
        pollBatchStatus();
    } catch (err) {
        showToast('Cancel failed');
    }
}

async function startBatchAnalyze() {
    if (!requireOllama()) return;
    try {
        const body = {};
        if (state.selectedCreator) body.creator = state.selectedCreator;
        const res = await fetch('/api/prompt/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await res.json();
        if (res.ok && data.status === 'started') {
            // Mark so the completion toast fires even for the first job this session
            state.batchWasRunning = true;
            showToast({
                title: 'Batch analyze started',
                body: `${data.pending} photo(s) queued — progress in the corner chip`
            });
            pollBatchStatus();
        } else if (data.status === 'nothing_to_do') {
            showToast('All photos already analyzed');
        } else {
            showToast(data.message || 'Batch already running or failed to start');
        }
    } catch (err) {
        showToast('Batch analyze failed');
    }
}


// ─────────────────────────────────────────────────────────────────────
// A2 — batch generate.
//
// Deliberately a near-twin of the batch-analyze poller above rather than a
// shared abstraction: the two jobs report different counters (skips, a batch
// id) and end differently (this one leads somewhere), and the parts that are
// genuinely identical are already factored out into renderJobChip.
// ─────────────────────────────────────────────────────────────────────

function generateChipSub(data) {
    const done = (data.completed || 0) + (data.failed || 0);
    const bits = [`${done}/${data.total} (${jobPct(done, data.total)}%)`];
    if (data.failed) bits.push(`${data.failed} failed`);
    // Skips are the number that makes an unexpectedly short run explicable, so
    // they belong on the chip rather than only in the completion toast.
    const skipped = (data.skipped_no_prompt || 0) + (data.skipped_video || 0);
    if (skipped) bits.push(`${skipped} skipped`);
    return bits.join(' · ');
}

async function pollGenerateStatus() {
    try {
        const res = await fetch('/api/comfy/batch/status');
        const data = await res.json();
        if (data.running) {
            renderJobChip('generate', {
                active: true,
                title: data.cancel_requested ? 'Generating — stopping' : 'Generating',
                sub: generateChipSub(data),
                completed: (data.completed || 0) + (data.failed || 0),
                total: data.total,
                cancellable: true,
                cancelled: Boolean(data.cancel_requested)
            });
            if (!state.generatePollTimer) {
                state.generatePollTimer = setInterval(pollGenerateStatus, 4000);
            }
        } else {
            if (state.generatePollTimer) {
                clearInterval(state.generatePollTimer);
                state.generatePollTimer = null;
            }
            renderJobChip('generate', { active: false });
            // Only on a running → stopped transition this tab actually saw.
            // Announcing on the first poll after a page load would re-toast a
            // batch that finished yesterday.
            if (state.generateWasRunning) {
                announceGenerateFinished(data);
                if (state.outputsView) fetchOutputs();
            }
        }
        state.generateWasRunning = Boolean(data.running);
    } catch (err) {
        console.error('Batch generate status error:', err);
    }
}

function announceGenerateFinished(data) {
    const skipped = (data.skipped_no_prompt || 0) + (data.skipped_video || 0);
    const parts = [`${data.completed || 0} generated`];
    if (data.failed) parts.push(`${data.failed} failed`);
    if (skipped) parts.push(`${skipped} skipped`);
    if (data.cancelled && data.pending) parts.push(`${data.pending} not run`);
    showToast({
        title: data.cancelled ? 'Generate cancelled' : 'Generate complete',
        body: parts.join(' · '),
        variant: data.cancelled ? undefined : 'success',
        // The whole point of a batch is that you were not watching it. Landing
        // in an unfiltered grid of every output ever made would mean hunting
        // for the run you just waited on.
        actionLabel: data.batch_id ? 'View run' : null,
        onAction: data.batch_id ? () => openBatchContactSheet(data.batch_id) : null,
        // Longer than the default 3.5s: an action nobody is at the keyboard for
        // is the same as no action.
        duration: data.batch_id ? 15000 : undefined
    });
}

async function cancelBatchGenerate() {
    try {
        const res = await fetch('/api/comfy/batch/cancel', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        showToast(
            data.status === 'cancelling'
                ? 'Stopping — the image in flight is being interrupted'
                : 'No batch generate running'
        );
        pollGenerateStatus();
    } catch (err) {
        showToast('Cancel failed');
    }
}

async function startBatchGenerate(body, { label = 'Generate' } = {}) {
    try {
        const res = await fetch('/api/comfy/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.status === 'started') {
            // Set before the first poll so the completion toast fires even for
            // the first batch of the session.
            state.generateWasRunning = true;
            const skipped = (data.skipped_no_prompt || 0) + (data.skipped_video || 0);
            showToast({
                title: `${label} started`,
                body: `${data.pending} queued`
                    + (skipped ? ` · ${skipped} skipped (no prompt yet)` : '')
                    + (data.capped ? ' · capped by COMFY_BATCH_MAX' : '')
            });
            pollGenerateStatus();
        } else if (data.status === 'nothing_to_do') {
            const skipped = data.skipped_no_prompt || 0;
            showToast(skipped
                ? `Nothing to generate — ${skipped} need analyzing first`
                : 'Nothing to generate');
        } else {
            showToast(data.message || 'ComfyUI busy or unreachable');
        }
    } catch (err) {
        showToast('Batch generate failed');
    }
}

function startBulkGenerate() {
    const paths = Array.from(state.selectedPaths);
    if (!paths.length) return;
    // The batch runs the workflow the bulk bar's picker names, not whatever the
    // server would have defaulted to.
    const workflow = (elements.bulkWorkflowSelect && elements.bulkWorkflowSelect.value)
        || state.workflowDefault || 'pro';
    startBatchGenerate({ paths, workflow }, { label: workflowLabel(workflow) });
}


// ─────────────────────────────────────────────────────────────────────
// Keep/reject classification: sidebar section, job chip, review mode,
// and the triage panel in the lightbox.
//
// The model reports a 0-4 exposure tier; the *server* decides which tiers
// are rejects (CLASSIFY_REJECT_MAX_TIER) and sends `verdict` alongside it.
// Nothing here re-derives that — one threshold, one owner.
// ─────────────────────────────────────────────────────────────────────

const TIER_LABEL_FALLBACK = {
    '-1': 'Not classified',
    '0': 'Unusable',
    '1': 'Fully modest',
    '2': 'Normal fashion',
    '3': 'Revealing daywear',
    '4': 'Swim / lingerie'
};

// Order matters: it is the order of the chips in the review strip.
const REVIEW_FILTERS = ['reject', 'unusable', 'modest', 'keep', 'unclassified'];

function tierLabel(tier) {
    const key = String(tier);
    const labels = state.tierLabels || TIER_LABEL_FALLBACK;
    return labels[key] || TIER_LABEL_FALLBACK[key] || 'Unknown';
}

function verdictCardClass(photo) {
    if (!state.reviewMode) return '';
    const v = photo && photo.verdict;
    if (!v) return ' verdict-none';
    if (v.verdict === 'reject') return ' verdict-reject';
    if (v.verdict === 'error') return ' verdict-error';
    return ' verdict-keep';
}

/**
 * Corner pill on a card. Shown quietly outside review mode and prominently
 * inside it — the same information, weighted to the task at hand.
 */
function verdictBadgeHtml(photo) {
    const v = photo && photo.verdict;
    if (!v) return '';
    // Loud whenever the verdict is what the user is looking at — in review, or
    // when they have filtered the normal gallery by it.
    const quiet = (state.reviewMode || state.browseVerdict) ? '' : ' quiet';
    const manual = v.manual
        ? '<i class="fa-solid fa-hand-pointer verdict-pill-manual" title="Set by hand"></i> '
        : '';
    if (v.verdict === 'error') {
        return `<span class="verdict-pill error${quiet}" title="${escapeHtml(v.error || 'Classify failed')}">
            <i class="fa-solid fa-triangle-exclamation"></i> Error</span>`;
    }
    const isReject = v.verdict === 'reject';
    const title = `${tierLabel(v.tier)}${v.reason ? ' — ' + v.reason : ''}`;
    // Rejects name *why* (unusable vs modest are acted on separately); keeps
    // show the tier, where the number is the interesting part. "T0" on both
    // told you nothing you could act on.
    const face = isReject
        ? (v.tier === 0 ? 'Unusable' : v.tier === 1 ? 'Modest' : 'Reject')
        : `T${Number(v.tier)}`;
    return `<span class="verdict-pill ${isReject ? 'reject' : 'keep'}${quiet}" title="${escapeHtml(title)}">
        ${manual}<i class="fa-solid ${isReject ? 'fa-ban' : 'fa-check'}"></i> ${escapeHtml(face)}</span>`;
}

// ── sidebar panel ────────────────────────────────────────────────────

function updateClassifyPanelUi() {
    const creator = state.selectedCreator;
    const meta = selectedCreatorMeta();
    const st = state.classifyStatus;
    // creator === "" means an archive-wide run, which covers this creator too —
    // distinct from creator === null, which means no job at all.
    const runningAll = !!(st && st.running && st.creator === '');
    const runningHere = !!(st && st.running && (runningAll || st.creator === creator));
    const runningElsewhere =
        !!(st && st.running && !runningAll && st.creator && st.creator !== creator);

    const total = meta ? Number(meta.photo_count) || 0 : 0;
    const keep = meta ? Number(meta.keep_count) || 0 : 0;
    const reject = meta ? Number(meta.reject_count) || 0 : 0;
    const todo = meta ? Number(meta.unclassified_count) || 0 : 0;
    const stale = meta ? Number(meta.stale_count) || 0 : 0;
    const pct = (n) => (total > 0 ? (n / total) * 100 : 0);

    if (elements.verdictMeterKeep) elements.verdictMeterKeep.style.width = `${pct(keep)}%`;
    if (elements.verdictMeterReject) elements.verdictMeterReject.style.width = `${pct(reject)}%`;
    if (elements.verdictMeterTodo) elements.verdictMeterTodo.style.width = `${pct(todo)}%`;
    if (elements.verdictMeter) elements.verdictMeter.classList.toggle('running', runningHere);

    if (elements.verdictMeterLegend) {
        if (!creator) {
            elements.verdictMeterLegend.textContent = '—';
        } else if (runningHere) {
            elements.verdictMeterLegend.textContent =
                (runningAll ? 'Classifying all creators ' : 'Classifying ') +
                `${st.completed}/${st.total} · keep ${st.kept || 0} · reject ${st.rejected || 0}` +
                (st.failed ? ` · err ${st.failed}` : '');
        } else if (runningElsewhere) {
            elements.verdictMeterLegend.textContent = `Job running on @${st.creator}…`;
        } else if (!keep && !reject && !todo) {
            elements.verdictMeterLegend.textContent = 'Nothing indexed yet';
        } else {
            const parts = [`${keep} keep`, `${reject} reject`];
            if (todo) parts.push(`${todo} to do`);
            elements.verdictMeterLegend.textContent = parts.join(' · ');
        }
    }

    // Why a button is disabled is more useful than that it is disabled.
    let blockedWhy = '';
    if (!creator) blockedWhy = 'Select a creator first';
    else if (runningHere) blockedWhy = 'Already classifying this creator';
    else if (runningElsewhere) blockedWhy = `Classify is running on @${st.creator}`;
    else if (state.ollamaOnline === false) blockedWhy = 'Ollama is offline';

    if (elements.classifyCreatorBtn) {
        const btn = elements.classifyCreatorBtn;
        btn.disabled = Boolean(blockedWhy);
        btn.title = blockedWhy || 'Score photos and reels with the vision keep/reject filter';
        if (runningHere) {
            btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Classifying ${st.completed}/${st.total}`;
        } else if (todo > 0) {
            btn.innerHTML = `<i class="fa-solid fa-wand-sparkles"></i> Classify ${todo} unclassified`;
        } else if (total > 0) {
            btn.innerHTML = '<i class="fa-solid fa-rotate"></i> Re-classify everything';
            btn.title = blockedWhy || 'Everything is classified — this re-runs the whole creator';
        } else {
            btn.innerHTML = '<i class="fa-solid fa-wand-sparkles"></i> Classify';
        }
    }
    if (elements.reviewRejectsBtn) {
        // Hidden at zero rather than disabled: an always-present "Review 0" is
        // noise, and its absence is itself the "nothing to clean" signal.
        elements.reviewRejectsBtn.style.display = reject > 0 ? '' : 'none';
        elements.reviewRejectsBtn.innerHTML =
            `<i class="fa-solid fa-filter-circle-xmark"></i> Review ${reject} reject${reject === 1 ? '' : 's'}`;
    }
    if (elements.rescoreStaleBtn) {
        // Only appears after a prompt-version bump leaves older verdicts behind.
        elements.rescoreStaleBtn.style.display = stale > 0 ? '' : 'none';
        elements.rescoreStaleBtn.disabled = Boolean(blockedWhy);
        elements.rescoreStaleBtn.title = blockedWhy || 'Re-classify only what an older prompt judged';
        elements.rescoreStaleBtn.innerHTML =
            `<i class="fa-solid fa-clock-rotate-left"></i> Re-score outdated (${stale})`;
    }
    if (elements.cancelClassifyBtn) {
        elements.cancelClassifyBtn.style.display = runningHere ? '' : 'none';
        elements.cancelClassifyBtn.disabled = Boolean(st && st.cancel_requested);
    }
    updateClassifyAllButton();
}

/**
 * The navbar archive-wide action. Lives outside the creator panel, so its
 * state depends on the whole archive rather than the selection.
 */
function updateClassifyAllButton() {
    const btn = elements.classifyAllBtn;
    if (!btn) return;
    const st = state.classifyStatus;
    const running = !!(st && st.running);
    const todo = archiveUnclassifiedTotal();

    let why = '';
    if (running) {
        why = st.creator ? `Classify is running on @${st.creator}` : 'Already classifying';
    } else if (state.ollamaOnline === false) {
        why = 'Ollama is offline';
    } else if (!todo) {
        why = 'Every creator is already classified';
    }

    btn.disabled = Boolean(why);
    btn.title = why || `Classify ${todo} unclassified item(s) across all creators`;
    btn.innerHTML = running && !st.creator
        ? `<i class="fa-solid fa-spinner fa-spin"></i> Classifying ${st.completed}/${st.total}`
        : `<i class="fa-solid fa-wand-sparkles"></i> Classify All${todo ? ` (${todo})` : ''}`;
}

/**
 * Live progress on one sidebar row. A full renderCreatorList() would refetch
 * creator style on every 3s poll, so patch the single pill instead.
 */
function patchCreatorRejectPill(creator, status) {
    if (!creator || !elements.creatorList) return;
    const row = elements.creatorList.querySelector(
        `.creator-item[data-creator="${cssEscape(creator)}"]`
    );
    if (!row) return;
    const meta = state.creators.find((c) => c.name === creator) || { name: creator };
    const html = rejectPillHtml(meta, status);
    const existing = row.querySelector('.creator-reject-pill');
    if (existing) {
        if (html) existing.outerHTML = html;
        else existing.remove();
    } else if (html) {
        row.querySelector('.creator-badge')?.insertAdjacentHTML('beforebegin', html);
    }
}

// ── job lifecycle ────────────────────────────────────────────────────

async function pollClassifyStatus() {
    try {
        const res = await fetch('/api/classify/status');
        if (!res.ok) return;
        const data = await res.json();
        const wasRunning = !!(state.classifyStatus && state.classifyStatus.running);
        state.classifyStatus = data;
        if (data.tier_labels) state.tierLabels = data.tier_labels;
        updateClassifyPanelUi();

        if (data.running) {
            // creator === "" is archive-wide; "@" with nothing after it read as
            // a bug. Show which creator it is on instead.
            const scopeLabel = data.creator ? `@${data.creator}` : 'all creators';
            const at = !data.creator && data.current_creator
                ? ` · @${data.current_creator}`
                : '';
            renderJobChip('classify', {
                active: true,
                title: data.cancel_requested
                    ? `Classifying ${scopeLabel} — stopping`
                    : `Classifying ${scopeLabel}`,
                sub: `${data.completed}/${data.total} (${jobPct(data.completed, data.total)}%)` +
                    ` · keep ${data.kept || 0} · reject ${data.rejected || 0}` +
                    (data.failed ? ` · err ${data.failed}` : '') + at,
                completed: data.completed,
                total: data.total,
                cancellable: true,
                cancelled: Boolean(data.cancel_requested)
            });
            if (!state.classifyPollTimer) {
                state.classifyPollTimer = setInterval(pollClassifyStatus, 3000);
            }
            patchCreatorRejectPill(data.creator, data);
            return;
        }

        if (state.classifyPollTimer) {
            clearInterval(state.classifyPollTimer);
            state.classifyPollTimer = null;
        }
        renderJobChip('classify', { active: false });
        // Only announce on an observed running -> stopped transition.
        if (!wasRunning) return;
        await announceClassifyFinished(data);
    } catch (err) {
        console.error('Classify status error:', err);
    }
}

/**
 * Toast + refresh after an observed running -> stopped transition.
 *
 * creator === "" is an archive-wide run, exactly as it is on the running path.
 * This used to fall back to the literal string 'creator', so an overnight
 * Classify All ended on "Classify done @creator" whose Review button navigated
 * to a folder of that name — always empty, and it discarded the real selection.
 */
async function announceClassifyFinished(data) {
    const archiveWide = !data.creator;
    const scope = archiveWide ? 'all creators' : `@${data.creator}`;
    const rejected = Number(data.rejected) || 0;
    // Counters first, so the toast action opens a truthful pile.
    await fetchCreators();
    await fetchStats();
    updateClassifyPanelUi();

    if (data.cancelled) {
        showToast(`Classify cancelled — ${scope} — ${data.completed}/${data.total} done · keep ${data.kept || 0} · reject ${rejected}`);
    } else if (rejected > 0) {
        // Deliberately does NOT auto-enter review: dropping the user into a
        // delete-oriented mode unasked is hostile. Offer it instead.
        showToast({
            title: `Classify done — ${scope}`,
            body: `keep ${data.kept || 0} · reject ${rejected}` + (data.failed ? ` · err ${data.failed}` : ''),
            actionLabel: `Review ${rejected} reject${rejected === 1 ? '' : 's'}`,
            onAction: () => reviewAfterClassify(archiveWide ? '' : data.creator)
        }, 9000);
    } else {
        showToast(`Classify done — ${scope} — keep ${data.kept || 0}, nothing to clean up`);
    }
    // An archive-wide run can have re-scored anything on screen; a per-creator
    // run matters when that creator is showing, or when nothing is filtered.
    if (archiveWide || !state.selectedCreator || state.selectedCreator === data.creator) {
        await fetchPhotos();
    }
}

/**
 * Open the reject pile at the scope the run actually covered.
 *
 * An archive-wide run has to drop the creator selection first: the count on the
 * toast is archive-wide, so entering review with a creator still selected would
 * silently show a subset of the number the user just clicked.
 */
function reviewAfterClassify(creator) {
    if (!creator && state.selectedCreator) {
        state.selectedCreator = null;
        hideCreatorStylePanel();
        if (elements.galleryTitle) elements.galleryTitle.textContent = 'All Photos';
        renderCreatorList();
    }
    enterReviewMode(creator);
}

async function startCreatorClassify({
    rescoreStale = false,
    force = false,
    allCreators = false,
} = {}) {
    if (!allCreators && !state.selectedCreator) {
        showToast('Select a creator first');
        return;
    }
    if (!requireOllama()) return;
    // "" is the archive-wide scope the API understands; it is not "unset".
    const scope = allCreators ? '' : state.selectedCreator;
    try {
        const res = await fetch('/api/classify/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                creator: scope,
                only_unclassified: !force,
                force,
                include_videos: true,
                rescore_stale: rescoreStale
            })
        });
        const data = await res.json();
        if (res.ok && data.status === 'started') {
            showToast({
                title: allCreators ? 'Classifying all creators' : `Classifying @${data.creator}`,
                body: `${data.pending} item(s) queued — progress in the corner chip`
            });
            state.classifyStatus = {
                running: true,
                creator: allCreators ? '' : data.creator,
                total: data.pending,
                completed: 0,
                kept: 0,
                rejected: 0,
                failed: 0
            };
            updateClassifyPanelUi();
            pollClassifyStatus();
        } else if (data.status === 'nothing_to_do') {
            showToast(rescoreStale
                ? 'Everything already judged by the current prompt'
                : (allCreators
                    ? 'Every creator is already classified'
                    : 'Everything for this creator is already classified'));
        } else if (data.status === 'ollama_down') {
            showToast(data.message || 'Ollama offline');
        } else {
            showToast(data.message || 'Classify is busy or failed to start');
        }
    } catch (err) {
        showToast('Classify request failed');
    }
}

async function cancelCreatorClassify() {
    try {
        const res = await fetch('/api/classify/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}'
        });
        const data = await res.json();
        showToast(data.status === 'cancelling'
            ? 'Stopping classify after the current item…'
            : 'No classify job running');
        pollClassifyStatus();
    } catch (err) {
        showToast('Cancel failed');
    }
}

// ── review mode ──────────────────────────────────────────────────────

function enterReviewMode(creator, verdict = 'reject') {
    // Review mode's surface is the photo gallery. Entered from the Outputs view
    // — which the classify toast can do while the user is browsing generations
    // — it would otherwise turn on with nothing on screen to review.
    if (state.outputsView) showOutputsView(false);
    if (creator && creator !== state.selectedCreator) {
        state.selectedCreator = creator;
        state.creatorPanelOpen = true;
        if (elements.galleryTitle) elements.galleryTitle.textContent = `@${creator}`;
        renderCreatorList();
        updateCreatorStylePanel();
    }
    state.reviewMode = true;
    state.verdictFilter = REVIEW_FILTERS.includes(verdict) ? verdict : 'reject';
    clearSelection();
    // Entry used to force select mode ON, which was self-defeating: a click in
    // select mode toggles a checkbox instead of opening the lightbox, and the
    // lightbox is the *only* place Keep/Reject/Auto and K/R/X exist
    // (handleTriageKey bails unless lightboxIndex >= 0). So the one mode built
    // for triage could do everything except triage — bulk delete was the only
    // reachable verb, and the control that would have turned select off is
    // hidden by `body.review-mode .view-controls`. Select is opt-in now; the
    // review strip carries its own toggle.
    setSelectMode(false);
    updateReviewBar();
    fetchPhotos();
}

function exitReviewMode({ refetch = true } = {}) {
    if (!state.reviewMode) return;
    state.reviewMode = false;
    state.verdictFilter = 'reject';
    clearSelection();
    setSelectMode(false);
    updateReviewBar();
    if (refetch) fetchPhotos();
}

function setVerdictFilter(verdict) {
    if (!REVIEW_FILTERS.includes(verdict) || verdict === state.verdictFilter) return;
    state.verdictFilter = verdict;
    clearSelection();
    updateReviewBar();
    fetchPhotos();
}

/**
 * B4 — every verdict filter advertises the share of the archive it selects.
 *
 * The pass rate is the one number that would have caught the previous
 * classifier: 85% of the archive on one tier, the Sexy filter admitting ~92%,
 * three prompt versions shipped before anyone noticed. It was computable the
 * whole time; it just never appeared anywhere the user was looking. So it goes
 * on the chips themselves, not only in the insights panel.
 *
 * Archive-wide, from /api/stats — never the scoped sidebar sum. "Does this
 * filter still discriminate" is a property of the classifier over everything
 * it has judged; a number that moved as you clicked between creators could not
 * be compared against the guard limit at all.
 */
function verdictPassRate(key) {
    const shares = state.verdictFacets && state.verdictFacets.shares;
    const share = shares ? Number(shares[key]) : NaN;
    return Number.isFinite(share) ? share : null;
}

/** The single saturation limit, served rather than hardcoded here, so the
 *  badge, the insights panel and the pytest gate cannot drift apart. */
function saturationLimit() {
    const limit = Number(state.verdictFacets && state.verdictFacets.warn_above);
    return Number.isFinite(limit) && limit > 0 ? limit : TOP_TIER_SHARE_WARN;
}

function passRateTitle(share) {
    const pct = (share * 100).toFixed(1);
    const cap = Math.round(saturationLimit() * 100);
    return share > saturationLimit()
        ? `Selects ${pct}% of the archive — past the ${cap}% guard, so this filter`
          + ' is close to a no-op. Re-check the prompt or the reject cut.'
        : `Selects ${pct}% of the archive (guard: ${cap}%)`;
}

/** Paint every pass-rate badge. textContent only — the rule is the rule even
 *  when the value is a number we computed ourselves. */
function renderVerdictPassRates() {
    const limit = saturationLimit();
    document.querySelectorAll('.review-chip-share').forEach((el) => {
        const share = verdictPassRate(el.dataset.share);
        const chip = el.closest('.review-chip');
        if (share === null) {
            el.hidden = true;
            el.textContent = '';
            if (chip) chip.classList.remove('is-saturated');
            return;
        }
        el.hidden = false;
        el.textContent = `${Math.round(share * 100)}%`;
        el.title = passRateTitle(share);
        el.classList.toggle('is-saturated', share > limit);
        if (chip) chip.classList.toggle('is-saturated', share > limit);
    });
    renderVerdictSelectPassRates();
}

/**
 * The browse dropdown carries the same number. Review mode is a deliberate
 * detour, and a guard that only shows up once you have gone looking is the
 * insights panel all over again.
 */
function renderVerdictSelectPassRates() {
    const sel = elements.verdictFilterSelect;
    if (!sel) return;
    let activeSaturated = false;
    Array.from(sel.options).forEach((opt) => {
        if (!opt.dataset.baseLabel) opt.dataset.baseLabel = opt.textContent;
        const share = opt.value ? verdictPassRate(opt.value) : null;
        opt.textContent = share === null
            ? opt.dataset.baseLabel
            : `${opt.dataset.baseLabel} · ${Math.round(share * 100)}%`;
        if (share !== null && share > saturationLimit() && opt.value === sel.value) {
            activeSaturated = true;
        }
    });
    sel.classList.toggle('is-saturated', activeSaturated);
}

function updateReviewBar() {
    if (!elements.reviewBar) return;
    const on = Boolean(state.reviewMode);
    elements.reviewBar.style.display = on ? 'flex' : 'none';
    document.body.classList.toggle('review-mode', on);
    if (!on) return;

    if (elements.reviewBarTitle) {
        elements.reviewBarTitle.textContent = state.selectedCreator
            ? `Reviewing @${state.selectedCreator}`
            : 'Reviewing all creators';
    }

    // The hint has to track the mode, because the two modes answer a click in
    // opposite ways. One fixed string is guaranteed to be lying half the time.
    if (elements.reviewBarHint) {
        elements.reviewBarHint.textContent = state.selectMode
            ? 'Click cards to select · Esc or Select to go back to triage'
            : 'Click a card to triage · K keep · R reject · X delete';
    }

    // Archive-wide review used to show zeroes on every chip, because the counts
    // came from the selected creator and there wasn't one.
    const scoped = scopedVerdictCounts();
    const counts = {
        reject: scoped.reject_count,
        unusable: scoped.unusable_count,
        modest: scoped.modest_count,
        keep: scoped.keep_count,
        unclassified: scoped.unclassified_count
    };
    if (elements.reviewBarFilters) {
        elements.reviewBarFilters.querySelectorAll('.review-chip').forEach((chip) => {
            const key = chip.dataset.verdict;
            chip.classList.toggle('active', key === state.verdictFilter);
            const n = chip.querySelector('.review-chip-n');
            if (n) n.textContent = String(counts[key] ?? 0);
        });
        // The count is scoped to what you are reviewing; the badge beside it
        // is the archive-wide pass rate. Different questions, so they are
        // rendered by different code and the tooltip says which is which.
        renderVerdictPassRates();
    }

    const selected = state.selectedPaths.size;
    if (elements.reviewBarCount) {
        elements.reviewBarCount.textContent = selected
            ? `${selected} selected of ${state.photoTotal}`
            : `${state.photoTotal} item${state.photoTotal === 1 ? '' : 's'}`;
    }
    if (elements.reviewSelectToggleBtn) {
        elements.reviewSelectToggleBtn.classList.toggle('active', Boolean(state.selectMode));
        elements.reviewSelectToggleBtn.innerHTML = state.selectMode
            ? '<i class="fa-solid fa-check-double"></i> Selecting'
            : '<i class="fa-solid fa-check-double"></i> Select';
        elements.reviewSelectToggleBtn.setAttribute('aria-pressed', state.selectMode ? 'true' : 'false');
    }
    if (elements.reviewClearBtn) {
        elements.reviewClearBtn.style.display = selected ? '' : 'none';
    }
    if (elements.reviewDeleteBtn) {
        elements.reviewDeleteBtn.disabled = selected === 0;
        elements.reviewDeleteBtn.innerHTML = selected
            ? `<i class="fa-solid fa-trash-can"></i> Delete ${selected}`
            : '<i class="fa-solid fa-trash-can"></i> Delete selected';
    }
}

/**
 * Select everything on the page except favourites. Favourites are the one
 * signal that is unambiguously the user's own, so they are never swept into a
 * bulk delete by a machine verdict.
 */
function selectNonFavourites() {
    if (!state.reviewMode) return;
    setSelectMode(true);
    let skipped = 0;
    state.photos.forEach((p) => {
        if (p.favorite) {
            state.selectedPaths.delete(p.rel_path);
            skipped += 1;
        } else {
            state.selectedPaths.add(p.rel_path);
        }
    });
    renderGallery();
    updateReviewBar();
    const n = state.selectedPaths.size;
    if (!n) {
        showToast('Nothing to select on this page');
    } else {
        showToast(`Selected ${n}${skipped ? ` · skipped ${skipped} favourite${skipped === 1 ? '' : 's'}` : ''}`);
    }
}

// ── triage panel ─────────────────────────────────────────────────────

function currentTriagePhoto() {
    if (state.lightboxIndex < 0) return null;
    return state.photos[state.lightboxIndex] || null;
}

function renderTriageBlock(photo) {
    if (!elements.triageBlock) return;
    const v = photo && photo.verdict;
    if (!state.reviewMode || !v) {
        elements.triageBlock.style.display = 'none';
        return;
    }
    elements.triageBlock.style.display = 'flex';

    const tier = Number(v.tier);
    if (elements.triageTierChip) {
        elements.triageTierChip.textContent = tier >= 0
            ? `Tier ${tier} · ${tierLabel(tier)}`
            : 'Classify failed';
        elements.triageTierChip.className = `tier-chip t${tier >= 0 ? tier : 'err'}`;
    }
    if (elements.triageMeta) {
        const bits = [];
        if (v.confidence != null) bits.push(`conf ${Math.round(v.confidence * 100)}%`);
        if (v.prompt_version) bits.push(v.prompt_version);
        if (v.manual) bits.push(`set to ${v.manual} by hand`);
        elements.triageMeta.textContent = bits.join(' · ');
    }
    if (elements.triageReason) {
        elements.triageReason.textContent = v.error
            ? v.error
            : (v.reason || 'No reason recorded.');
    }

    // The contact sheet is what the model was actually shown. Without it a
    // wrong reel verdict is unexplainable, which is how the last classifier
    // burned trust.
    if (elements.triageSheetWrap && elements.triageSheet) {
        if (v.sheet_path) {
            elements.triageSheetWrap.style.display = '';
            const want = `/api/classify/sheet?rel_path=${encodeURIComponent(photo.rel_path)}`;
            if (elements.triageSheet.getAttribute('src') !== want) {
                elements.triageSheet.src = want;
            }
            elements.triageSheet.onerror = () => {
                elements.triageSheetWrap.style.display = 'none';
            };
        } else {
            elements.triageSheetWrap.style.display = 'none';
            elements.triageSheet.removeAttribute('src');
        }
    }

    const effective = v.verdict;
    if (elements.triageKeepBtn) elements.triageKeepBtn.classList.toggle('active', effective === 'keep');
    if (elements.triageRejectBtn) elements.triageRejectBtn.classList.toggle('active', effective === 'reject');
    if (elements.triageAutoBtn) elements.triageAutoBtn.style.display = v.manual ? '' : 'none';
}

/**
 * Pin the current item to keep/reject (or hand it back to the model).
 * Optimistic: the pill and panel update immediately, and the row is patched
 * from the server response rather than refetching the page.
 */
async function setManualVerdict(value, { advance = false } = {}) {
    const photo = currentTriagePhoto();
    if (!photo || !photo.verdict) {
        showToast('Nothing to judge here');
        return;
    }
    try {
        const res = await fetch('/api/classify/verdict', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rel_path: photo.rel_path, verdict: value })
        });
        const data = await res.json();
        if (!res.ok || data.status !== 'ok') {
            showToast(data.message || 'Could not save that verdict');
            return;
        }
        photo.verdict = data.verdict;
        renderTriageBlock(photo);
        // Patch just this card; a refetch would drop it out of the filtered
        // page under the user's cursor and lose their scroll position.
        const card = elements.galleryGrid.querySelector(
            `.photo-card[data-rel-path="${cssEscape(photo.rel_path)}"]`
        );
        if (card) {
            card.className = card.className
                .replace(/ verdict-(reject|keep|error|none)/g, '') + verdictCardClass(photo);
            const pill = card.querySelector('.verdict-pill');
            if (pill) pill.outerHTML = verdictBadgeHtml(photo);
        }
        if (advance) navigateLightbox(1);
    } catch (err) {
        showToast('Verdict request failed');
    }
}

/** Delete the item currently open in triage, then advance. */
async function triageDeleteCurrent() {
    const photo = currentTriagePhoto();
    if (!photo) return;
    const nextIndex = state.lightboxIndex;
    await executeBulkDelete([photo.rel_path]);
    // removePhotosFromView() already dropped it, so the same index is the next
    // item. Clamp when the deleted one was last.
    if (!state.photos.length) {
        closeLightbox();
        return;
    }
    openLightbox(Math.min(nextIndex, state.photos.length - 1));
}

/** CSS.escape with a fallback — attribute selectors on paths need quoting. */
function cssEscape(value) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
    return String(value).replace(/["\\]/g, '\\$&');
}

function handleTriageKey(e) {
    if (!state.reviewMode || state.lightboxIndex < 0) return false;
    // Never steal a keystroke from a text field — the prompt editor lives in
    // the same modal.
    const el = document.activeElement;
    if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) {
        return false;
    }
    const key = e.key.toLowerCase();
    if (key === 'k') {
        setManualVerdict('keep', { advance: true });
        return true;
    }
    if (key === 'r') {
        setManualVerdict('reject', { advance: true });
        return true;
    }
    if (key === 'x') {
        triageDeleteCurrent();
        return true;
    }
    return false;
}

function setupClassifyListeners() {
    if (elements.classifyAllBtn) {
        elements.classifyAllBtn.addEventListener('click', () => {
            const todo = archiveUnclassifiedTotal();
            if (todo === 0) {
                showToast('Every creator is already classified');
                return;
            }
            // Archive-wide is long and holds the vision model the whole time,
            // which blocks batch analyze. Worth one confirm.
            if (!window.confirm(
                `Classify ${todo} unclassified item(s) across all creators?\n\n`
                + 'This holds the vision model until it finishes — batch analyze '
                + 'cannot run alongside it. You can cancel from the chip.'
            )) return;
            startCreatorClassify({ allCreators: true });
        });
    }
    if (elements.classifyCreatorBtn) {
        elements.classifyCreatorBtn.addEventListener('click', () => {
            const meta = selectedCreatorMeta();
            const todo = meta ? Number(meta.unclassified_count) || 0 : 0;
            // Zero unclassified turns the button into a full re-run, which is
            // expensive enough to confirm rather than fire on a stray click.
            if (todo === 0 && meta && Number(meta.photo_count) > 0) {
                const n = Number(meta.photo_count);
                if (!window.confirm(`Re-classify all ${n} items for @${state.selectedCreator}? This re-runs the vision model on every file.`)) {
                    return;
                }
                startCreatorClassify({ force: true });
                return;
            }
            startCreatorClassify();
        });
    }
    if (elements.rescoreStaleBtn) {
        elements.rescoreStaleBtn.addEventListener('click', () => startCreatorClassify({ rescoreStale: true }));
    }
    if (elements.cancelClassifyBtn) {
        elements.cancelClassifyBtn.addEventListener('click', cancelCreatorClassify);
    }
    if (elements.classifyJobChipCancel) {
        elements.classifyJobChipCancel.addEventListener('click', cancelCreatorClassify);
    }
    if (elements.reviewRejectsBtn) {
        elements.reviewRejectsBtn.addEventListener('click', () => enterReviewMode(state.selectedCreator));
    }
    if (elements.reviewExitBtn) {
        elements.reviewExitBtn.addEventListener('click', () => exitReviewMode());
    }
    if (elements.reviewSelectToggleBtn) {
        elements.reviewSelectToggleBtn.addEventListener('click', () => {
            setSelectMode(!state.selectMode);
            updateReviewBar();
        });
    }
    if (elements.reviewSelectAllBtn) {
        elements.reviewSelectAllBtn.addEventListener('click', selectNonFavourites);
    }
    if (elements.reviewClearBtn) {
        elements.reviewClearBtn.addEventListener('click', () => {
            clearSelection();
            updateReviewBar();
        });
    }
    if (elements.reviewDeleteBtn) {
        elements.reviewDeleteBtn.addEventListener('click', promptBulkDelete);
    }
    if (elements.reviewBarFilters) {
        elements.reviewBarFilters.addEventListener('click', (e) => {
            const chip = e.target.closest('.review-chip');
            if (chip) setVerdictFilter(chip.dataset.verdict);
        });
    }
    if (elements.triageKeepBtn) {
        elements.triageKeepBtn.addEventListener('click', () => setManualVerdict('keep'));
    }
    if (elements.triageRejectBtn) {
        elements.triageRejectBtn.addEventListener('click', () => setManualVerdict('reject'));
    }
    if (elements.triageAutoBtn) {
        elements.triageAutoBtn.addEventListener('click', () => setManualVerdict(null));
    }
}
