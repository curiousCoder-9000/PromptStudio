// State Management
const state = {
    creators: [],
    photos: [],
    selectedCreator: null,
    searchQuery: '',
    creatorSearchQuery: '',
    unanalyzedOnly: false,
    favoritesOnly: false,
    sexyOnly: false,
    rejectOnly: false,
    unscoredOnly: false,
    mediaType: 'all',
    sortMode: 'name',
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
    compareMode: false,
    currentGenerations: [],
    promptDirty: false,
    lightboxIndex: -1,
    currentPromptData: null,
    photoToDelete: null,
    syncPollTimer: null,
    scrapePollTimer: null,
    scrapeStatus: null,
    scrapeChipDismissed: false,
    scrapeNotifiedFinished: null,
    scrapeWasActive: false,
    batchPollTimer: null,
    // Tracks the running → stopped transition so completion is announced once
    batchWasRunning: false,
    classifyPollTimer: null,
    classifyStatus: null,
    healthPollTimer: null,
    photoOffset: 0,
    photoLimit: 60,
    photoTotal: 0,
    photoHasMore: false,
    photosLoading: false,
    // In-flight AbortControllers — a newer request supersedes the older one
    photosRequest: null,
    creatorStyleRequest: null,
    followingRequest: null,
    // Creator sidebar action panel (Sync / Classify / Style) — independent of filter selection
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
    // Lightbox Modal
    lightboxModal: document.getElementById('lightboxModal'),
    lightboxOverlay: document.getElementById('lightboxOverlay'),
    lightboxClose: document.getElementById('lightboxClose'),
    lightboxImg: document.getElementById('lightboxImg'),
    lightboxVideo: document.getElementById('lightboxVideo'),
    lightboxCreator: document.getElementById('lightboxCreator'),
    lightboxFilename: document.getElementById('lightboxFilename'),
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
    videoDetailPanel: document.getElementById('videoDetailPanel'),
    videoDetailThumb: document.getElementById('videoDetailThumb'),
    videoDetailHandle: document.getElementById('videoDetailHandle'),
    videoDetailFile: document.getElementById('videoDetailFile'),
    videoDetailPills: document.getElementById('videoDetailPills'),
    videoDetailGrid: document.getElementById('videoDetailGrid'),
    videoGlamBlock: document.getElementById('videoGlamBlock'),
    videoGlamRow: document.getElementById('videoGlamRow'),
    videoGlamReason: document.getElementById('videoGlamReason'),
    videoDetailCaption: document.getElementById('videoDetailCaption'),
    videoOpenIgBtn: document.getElementById('videoOpenIgBtn'),
    videoExpandFromPanelBtn: document.getElementById('videoExpandFromPanelBtn'),
    videoCopyPathBtn: document.getElementById('videoCopyPathBtn'),
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
    scrapeJobChip: document.getElementById('scrapeJobChip'),
    scrapeJobChipIcon: document.getElementById('scrapeJobChipIcon'),
    scrapeJobChipTitle: document.getElementById('scrapeJobChipTitle'),
    scrapeJobChipSub: document.getElementById('scrapeJobChipSub'),
    scrapeJobChipCancel: document.getElementById('scrapeJobChipCancel'),
    scrapeJobChipDismiss: document.getElementById('scrapeJobChipDismiss'),
    // Batch + classify job chips (same shape as the scrape chip)
    jobChipStack: document.getElementById('jobChipStack'),
    batchJobChip: document.getElementById('batchJobChip'),
    batchJobChipIcon: document.getElementById('batchJobChipIcon'),
    batchJobChipTitle: document.getElementById('batchJobChipTitle'),
    batchJobChipSub: document.getElementById('batchJobChipSub'),
    batchJobChipFill: document.getElementById('batchJobChipFill'),
    batchJobChipCancel: document.getElementById('batchJobChipCancel'),
    classifyJobChip: document.getElementById('classifyJobChip'),
    classifyJobChipIcon: document.getElementById('classifyJobChipIcon'),
    classifyJobChipTitle: document.getElementById('classifyJobChipTitle'),
    classifyJobChipSub: document.getElementById('classifyJobChipSub'),
    classifyJobChipFill: document.getElementById('classifyJobChipFill'),
    classifyJobChipCancel: document.getElementById('classifyJobChipCancel'),
    batchPromptBtn: document.getElementById('batchPromptBtn'),
    unanalyzedFilterBtn: document.getElementById('unanalyzedFilterBtn'),
    favoritesFilterBtn: document.getElementById('favoritesFilterBtn'),
    sexyFilterBtn: document.getElementById('sexyFilterBtn'),
    rejectFilterBtn: document.getElementById('rejectFilterBtn'),
    unscoredFilterBtn: document.getElementById('unscoredFilterBtn'),
    sortSelect: document.getElementById('sortSelect'),
    mediaTypeSelect: document.getElementById('mediaTypeSelect'),
    favoritePhotoBtn: document.getElementById('favoritePhotoBtn'),
    promptHistory: document.getElementById('promptHistory'),
    promptHistoryList: document.getElementById('promptHistoryList'),
    lightboxGenImg: document.getElementById('lightboxGenImg'),
    generatedPane: document.getElementById('generatedPane'),
    mediaCompare: document.getElementById('mediaCompare'),
    compareToggleBtn: document.getElementById('compareToggleBtn'),
    comfySdxlBtn: document.getElementById('comfySdxlBtn'),
    comfyFluxBtn: document.getElementById('comfyFluxBtn'),
    comfyProBtn: document.getElementById('comfyProBtn'),
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
    creatorClassifyMeta: document.getElementById('creatorClassifyMeta'),
    classifyCreatorBtn: document.getElementById('classifyCreatorBtn'),
    reviewRejectsBtn: document.getElementById('reviewRejectsBtn'),
    cancelClassifyBtn: document.getElementById('cancelClassifyBtn'),
    rebuildStyleBtn: document.getElementById('rebuildStyleBtn'),
    bulkBar: document.getElementById('bulkBar'),
    bulkCount: document.getElementById('bulkCount'),
    bulkReanalyzeBtn: document.getElementById('bulkReanalyzeBtn'),
    bulkDeleteBtn: document.getElementById('bulkDeleteBtn'),
    bulkClearBtn: document.getElementById('bulkClearBtn'),
    reviewRejectsBar: document.getElementById('reviewRejectsBar'),
    reviewRejectsCount: document.getElementById('reviewRejectsCount'),
    selectAllRejectsBtn: document.getElementById('selectAllRejectsBtn'),
    deleteSelectedRejectsBtn: document.getElementById('deleteSelectedRejectsBtn'),
    exitRejectsBtn: document.getElementById('exitRejectsBtn'),
    deleteConfirmTitle: document.getElementById('deleteConfirmTitle'),
    deleteConfirmBody: document.getElementById('deleteConfirmBody'),
    followingSearchInput: document.getElementById('followingSearchInput'),
    followingList: document.getElementById('followingList'),
    followingEmpty: document.getElementById('followingEmpty'),
    creatorSearchInput: document.getElementById('creatorSearchInput'),
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
// silently restoring them is disorienting. Reject review in particular is a
// destructive mode nobody should land in from a refresh.
const PREFS_KEY = 'promptstudio.viewPrefs.v1';
const PREF_FIELDS = [
    'sortMode',
    'mediaType',
    'gridSize',
    'favoritesOnly',
    'sexyOnly',
    'unscoredOnly',
    'unanalyzedOnly'
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
    applyGridSize(state.gridSize);

    const chips = [
        [elements.favoritesFilterBtn, state.favoritesOnly],
        [elements.sexyFilterBtn, state.sexyOnly],
        [elements.unscoredFilterBtn, state.unscoredOnly],
        [elements.unanalyzedFilterBtn, state.unanalyzedOnly]
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

async function initApp() {
    await fetchHealth();
    await fetchStats();
    await fetchCreators();
    await fetchPhotos();
    // Resume job chips if work is mid-flight — jobs live on the server, so a
    // browser refresh must not orphan a running batch/classify/scrape.
    pollClassifyStatus();
    pollBatchStatus();
    // Restore scrape/sync chip after refresh (queue lives on the server)
    await hydrateScrapeUiFromServer();
    if (!state.healthPollTimer) {
        state.healthPollTimer = setInterval(fetchHealth, 30000);
    }
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
        elements.comfyDenoiseInput,
        elements.comfyStepsInput,
        elements.comfyCfgInput,
        elements.comfySeedLock,
        elements.comfyModeECheck,
    ].forEach((el) => {
        if (el) el.disabled = !on;
    });
    syncComfySeedInput();
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
        elements.statCreators.textContent = data.total_creators.toLocaleString();
        elements.statPersonPhotos.textContent = (data.prompts_ready ?? 0).toLocaleString();
        if (typeof data.trash_enabled === 'boolean') {
            state.trashEnabled = data.trash_enabled;
        }
        state.trashCount = data.trash_count ?? 0;
        updateTrashButtonUi();
    } catch (err) {
        console.error('Error fetching stats:', err);
    }
}

async function fetchCreators() {
    try {
        const res = await fetch('/api/creators');
        state.creators = await res.json();
        elements.creatorCount.textContent = state.creators.length;
        renderCreatorList();
        populateUploadCreators();
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
        const prevLen = append ? state.photos.length : 0;
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
        if (state.sexyOnly) {
            params.append('sexy', '1');
        }
        if (state.rejectOnly) {
            params.append('reject', '1');
        }
        if (state.unscoredOnly) {
            params.append('unscored', '1');
        }
        params.append('media_type', state.mediaType || 'all');
        params.append('sort', state.sortMode || 'name');
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
        state.photoOffset = state.photos.length;
        renderGallery({ append, fromIndex: prevLen });
        updateReviewRejectsBar();
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
 * every loaded page — brutal in the reject-review loop. This splices state,
 * removes the cards, and adjusts the counters in place so scroll position and
 * all loaded pages survive.
 *
 * Returns the number of photos actually removed from the view.
 */
function removePhotosFromView(relPaths) {
    const targets = new Set(relPaths || []);
    if (!targets.size) return 0;

    const removedByCreator = new Map();
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

    // Paging counters: offset tracks how many rows we've consumed from the server
    state.photoOffset = Math.max(0, state.photoOffset - removed);
    state.photoTotal = Math.max(0, (state.photoTotal || 0) - removed);

    // Sidebar + stats counters, without hitting the O(archive) /api/stats route
    removedByCreator.forEach((n, creatorName) => {
        const creator = state.creators.find((c) => c.name === creatorName);
        if (!creator) return;
        creator.photo_count = Math.max(0, (creator.photo_count || 0) - n);
        if (typeof creator.scored_count === 'number') {
            creator.scored_count = Math.max(0, creator.scored_count - n);
        }
        if (typeof creator.reject_count === 'number') {
            creator.reject_count = Math.max(0, creator.reject_count - n);
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

    elements.galleryCount.textContent = `${state.photos.length}` +
        (state.photoTotal ? ` / ${state.photoTotal}` : '') + ' photos';
    elements.emptyState.style.display = state.photos.length === 0 ? 'flex' : 'none';
    updateBulkBar();
    updateReviewRejectsBar();
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
    // Prefer the reject-review bar when that filter is active
    if (state.rejectOnly) {
        elements.bulkBar.style.display = 'none';
        updateReviewRejectsBar();
        return;
    }
    if (!state.selectMode || count === 0) {
        elements.bulkBar.style.display = 'none';
        return;
    }
    elements.bulkBar.style.display = 'flex';
    elements.bulkCount.textContent = `${count} selected`;
}

function updateReviewRejectsBar() {
    if (!elements.reviewRejectsBar) return;
    if (!state.rejectOnly) {
        elements.reviewRejectsBar.style.display = 'none';
        return;
    }
    elements.reviewRejectsBar.style.display = 'flex';
    const total = state.photoTotal || state.photos.length;
    const sel = state.selectedPaths.size;
    if (elements.reviewRejectsCount) {
        elements.reviewRejectsCount.textContent = sel
            ? `Review rejects · ${total} items · ${sel} selected`
            : `Review rejects · ${total} items`;
    }
}

function enterRejectReviewMode() {
    state.rejectOnly = true;
    state.sexyOnly = false;
    state.unscoredOnly = false;
    if (elements.rejectFilterBtn) elements.rejectFilterBtn.classList.add('active');
    if (elements.sexyFilterBtn) elements.sexyFilterBtn.classList.remove('active');
    if (elements.unscoredFilterBtn) elements.unscoredFilterBtn.classList.remove('active');
    setSelectMode(true);
    fetchPhotos();
}

function exitRejectReviewMode() {
    state.rejectOnly = false;
    if (elements.rejectFilterBtn) elements.rejectFilterBtn.classList.remove('active');
    clearSelection();
    setSelectMode(false);
    updateReviewRejectsBar();
    fetchPhotos();
}

function selectNonFavoriteRejects() {
    if (!state.rejectOnly) return;
    setSelectMode(true);
    state.photos.forEach((p) => {
        if (p.favorite) {
            state.selectedPaths.delete(p.rel_path);
        } else {
            state.selectedPaths.add(p.rel_path);
        }
    });
    renderGallery();
    updateReviewRejectsBar();
    const n = state.selectedPaths.size;
    showToast(n ? `Selected ${n} non-favorite rejects` : 'No non-favorite rejects on this page');
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

// Render Functions
function renderCreatorList() {
    elements.creatorList.innerHTML = '';
    const q = (state.creatorSearchQuery || '').toLowerCase();

    const allItem = document.createElement('div');
    allItem.className = `creator-item ${!state.selectedCreator ? 'active' : ''}`;
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

    const classifyRunning = state.classifyStatus && state.classifyStatus.running;
    const classifyCreator = state.classifyStatus && state.classifyStatus.creator;

    const filtered = state.creators.filter(c => !q || c.name.toLowerCase().includes(q));
    filtered.forEach(c => {
        const item = document.createElement('div');
        item.className = `creator-item ${state.selectedCreator === c.name ? 'active' : ''}`;
        const scored = c.scored_count != null ? Number(c.scored_count) : null;
        const total = Number(c.photo_count) || 0;
        const rejects = Number(c.reject_count) || 0;
        const isJob = classifyRunning && classifyCreator === c.name;
        let scorePill = '';
        if (isJob) {
            const st = state.classifyStatus;
            scorePill = `<span class="creator-score-pill running" title="Classifying…">${Number(st.completed) || 0}/${Number(st.total) || 0}</span>`;
        } else if (scored != null && total > 0) {
            const cls = rejects > 0 ? 'creator-score-pill has-rejects' : 'creator-score-pill';
            const title = rejects
                ? `${scored}/${total} scored · ${rejects} rejects`
                : `${scored}/${total} scored`;
            scorePill = `<span class="${cls}" title="${escapeHtml(title)}">${scored}/${total}</span>`;
        }
        item.innerHTML = `
            <span class="creator-name">@${escapeHtml(c.name)}${syncBadgeHtml(c)}</span>
            <span style="display:inline-flex;align-items:center;gap:6px;">
                ${scorePill}
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

function updateClassifyPanelUi() {
    const creator = state.selectedCreator;
    const meta = selectedCreatorMeta();
    const st = state.classifyStatus;
    const runningHere = !!(st && st.running && st.creator === creator);
    const runningElsewhere = !!(st && st.running && st.creator && st.creator !== creator);

    if (elements.creatorClassifyMeta) {
        if (!creator) {
            elements.creatorClassifyMeta.textContent = '—';
        } else if (runningHere) {
            const pct = st.total ? Math.round((st.completed / st.total) * 100) : 0;
            elements.creatorClassifyMeta.textContent =
                `Classifying… ${st.completed}/${st.total} (${pct}%) · keep ${st.kept || 0} · reject ${st.rejected || 0}` +
                (st.failed ? ` · err ${st.failed}` : '');
        } else if (runningElsewhere) {
            elements.creatorClassifyMeta.textContent = `Job running on @${st.creator}…`;
        } else if (meta) {
            const scored = meta.scored_count != null ? meta.scored_count : 0;
            const unscored = meta.unscored_count != null ? meta.unscored_count : 0;
            const rejects = meta.reject_count != null ? meta.reject_count : 0;
            elements.creatorClassifyMeta.textContent =
                `${scored} scored · ${unscored} unscored · ${rejects} rejects`;
        } else {
            elements.creatorClassifyMeta.textContent = '—';
        }
    }

    if (elements.classifyCreatorBtn) {
        elements.classifyCreatorBtn.disabled = !creator || runningHere || runningElsewhere || state.ollamaOnline === false;
        elements.classifyCreatorBtn.innerHTML = runningHere
            ? '<i class="fa-solid fa-spinner fa-spin"></i> Classifying…'
            : '<i class="fa-solid fa-fire"></i> Classify unscored';
    }
    if (elements.reviewRejectsBtn) {
        const rejects = meta && meta.reject_count != null ? Number(meta.reject_count) || 0 : 0;
        elements.reviewRejectsBtn.disabled = !creator;
        elements.reviewRejectsBtn.innerHTML =
            `<i class="fa-solid fa-filter"></i> Review rejects${rejects ? ` (${rejects})` : ''}`;
    }
    if (elements.cancelClassifyBtn) {
        elements.cancelClassifyBtn.style.display = runningHere ? 'inline-flex' : 'none';
    }
    updateSyncLatestButtonUi();
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
    updateClassifyPanelUi();
    updateSyncLatestButtonUi();
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

function renderGallery({ append = false, fromIndex = 0 } = {}) {
    if (!append) {
        elements.galleryGrid.innerHTML = '';
    }
    elements.galleryCount.textContent = `${state.photos.length}` +
        (state.photoTotal ? ` / ${state.photoTotal}` : '') + ' photos';

    if (state.photos.length === 0) {
        elements.emptyState.style.display = 'flex';
        updateBulkBar();
        return;
    }

    elements.emptyState.style.display = 'none';

    const sliceStart = append ? fromIndex : 0;
    const toRender = state.photos.slice(sliceStart);

    toRender.forEach((p, i) => {
        const index = sliceStart + i;
        const card = document.createElement('div');
        const selected = state.selectedPaths.has(p.rel_path);
        card.className = `photo-card${state.selectMode ? ' select-mode' : ''}${selected ? ' selected' : ''}${p.favorite ? ' is-favorite' : ''}`;
        card.dataset.relPath = p.rel_path;
        const imgSrc = p.thumb_url || p.url;
        const status = promptStatusMeta(p);
        const favMark = p.favorite
            ? '<span class="card-fav-mark" title="Favorite"><i class="fa-solid fa-star"></i></span>'
            : '';
        // Single source of truth for video detection (was a divergent inline list)
        const isVideo = isVideoFilename(p.filename);
        const videoBadge = isVideo ? '<div class="video-badge"><i class="fa-solid fa-play"></i></div>' : '';
        const glam = typeof p.glam_score === 'number' ? p.glam_score : -1;
        const glamBadge = glam >= 0
            ? `<span class="glam-score-badge g${glam}" title="Glam score ${glam}">g${glam}</span>`
            : '';
        const bottomHint = isVideo
            ? `<div class="photo-card-prompt-hint"><i class="fa-solid fa-clapperboard"></i> Click for reel details</div>`
            : `<div class="photo-card-prompt-hint"><i class="fa-solid fa-wand-magic-sparkles"></i> Click for AI Prompt</div>`;
        const topBadge = isVideo
            ? `<span class="prompt-status-badge ready" title="Reel"><i class="fa-solid fa-film"></i> Reel</span>`
            : `<span class="prompt-status-badge ${status.cls}"><i class="fa-solid ${status.icon}"></i> ${status.label}</span>`;
        card.innerHTML = `
            <img src="${escapeHtml(imgSrc)}" alt="${escapeHtml(p.filename)}" loading="lazy" data-full="${escapeHtml(p.url)}">
            ${videoBadge}
            ${glamBadge}
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
                openLightbox(index);
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

    resetPromptPanel();
    updateFavoriteButton(photo);
    state.compareMode = false;
    setCompareMode(false);
    elements.lightboxModal.style.display = 'flex';

    if (isVideo) {
        // Reels: show metadata / glam panel — not image prompt generator
        loadVideoDetailPanel(photo);
        // Skip Comfy generations / prompt auto-load for videos
        if (elements.compareToggleBtn) elements.compareToggleBtn.style.display = 'none';
        state.currentGenerations = [];
    } else {
        setInspectorMode('photo');
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
    } catch (err) {
        console.error('Generations load failed', err);
    }
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
    const workflow = (variant === 'pro' || variant === 'ref') ? 'pro' : 'txt2img';
    const controls = readComfyProControls();
    if (elements.comfyStatusText) {
        elements.comfyStatusText.textContent = workflow === 'pro'
            ? (controls.useModeE
                ? 'Mode E + uploading reference…'
                : 'Uploading reference + queueing Pro…')
            : 'Queueing ComfyUI txt2img…';
    }
    try {
        const body = {
            path: photo.rel_path,
            variant,
            workflow,
            positive_prompt: positive,
            negative_prompt: negative,
        };
        if (workflow === 'pro') {
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
        const label = workflow === 'pro'
            ? `Pro d=${data.denoise ?? controls.denoise}${data.use_mode_e ? ' ModeE' : ''}`
            : variant.toUpperCase();
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

function glamScoreLabel(score) {
    const s = Number(score);
    if (!Number.isFinite(s) || s < 0) return { text: 'Unscored', cls: 'glam-unscored' };
    if (s >= 2) return { text: `Keep · g${s}`, cls: 'glam-keep' };
    return { text: `Reject · g${s}`, cls: 'glam-reject' };
}

/**
 * Photos: prompt generator. Videos: reel metadata / glam panel
 * (vision image prompts are unreliable on MP4s).
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
        elements.inspectorModelTag.textContent = isVideo ? 'Archive + Glam' : 'Ollama Vision';
    }
    if (elements.videoDetailPanel) {
        elements.videoDetailPanel.style.display = isVideo ? 'flex' : 'none';
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

function boolPill(label, on) {
    const cls = on ? 'glam-keep' : 'glam-reject';
    const mark = on ? 'yes' : 'no';
    return `<span class="video-pill ${cls}">${escapeHtml(label)}: ${mark}</span>`;
}

async function loadVideoDetailPanel(photo) {
    if (!elements.videoDetailPanel || !photo) return;
    setInspectorMode('video');

    // Immediate shell from gallery card data
    if (elements.videoDetailHandle) {
        elements.videoDetailHandle.textContent = `@${photo.creator || 'unknown'}`;
    }
    if (elements.videoDetailFile) {
        elements.videoDetailFile.textContent = photo.filename || photo.rel_path || '';
    }
    if (elements.videoDetailThumb) {
        elements.videoDetailThumb.src = photo.thumb_url || photo.url || '';
        elements.videoDetailThumb.alt = photo.filename || 'Reel';
    }
    if (elements.videoDetailCaption) {
        elements.videoDetailCaption.textContent = 'Loading metadata…';
        elements.videoDetailCaption.classList.add('empty');
    }
    if (elements.videoDetailGrid) {
        elements.videoDetailGrid.innerHTML =
            metaCard('Type', 'Reel / video') +
            metaCard('Glam', typeof photo.glam_score === 'number' && photo.glam_score >= 0
                ? `g${photo.glam_score}`
                : '—');
    }
    if (elements.videoGlamBlock) elements.videoGlamBlock.style.display = 'none';
    if (elements.videoOpenIgBtn) elements.videoOpenIgBtn.style.display = 'none';

    // Live duration when video element has metadata
    const paintDuration = () => {
        const v = elements.lightboxVideo;
        const dur = getMediaDuration(v);
        if (!Number.isFinite(dur) || !elements.videoDetailGrid) return;
        const cards = elements.videoDetailGrid.querySelectorAll('.video-meta-card');
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
            elements.videoDetailGrid.insertAdjacentHTML(
                'beforeend',
                metaCard('Duration', formatVideoTime(dur))
            );
        }
    };
    if (elements.lightboxVideo) {
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

        if (elements.videoDetailHandle) {
            elements.videoDetailHandle.textContent = `@${data.creator || photo.creator || 'unknown'}`;
        }
        if (elements.videoDetailFile) {
            elements.videoDetailFile.textContent = data.filename || photo.filename || '';
        }
        if (elements.videoDetailThumb && (data.thumb_url || photo.thumb_url)) {
            elements.videoDetailThumb.src = data.thumb_url || photo.thumb_url;
        }

        const glamInfo = glamScoreLabel(data.glam_score);
        if (elements.videoDetailPills) {
            const pills = [
                `<span class="video-pill ${glamInfo.cls}">${escapeHtml(glamInfo.text)}</span>`,
            ];
            if (data.favorite || photo.favorite) {
                pills.push('<span class="video-pill glam-keep">Favorite</span>');
            }
            if (data.shortcode) {
                pills.push(`<span class="video-pill">${escapeHtml(data.shortcode)}</span>`);
            }
            elements.videoDetailPills.innerHTML = pills.join('');
        }

        const durationLive = getMediaDuration(elements.lightboxVideo);
        const gridParts = [
            metaCard('Type', 'Reel / video'),
            metaCard('Size', formatBytes(data.file_size)),
            metaCard('Posted', formatTakenAt(data.taken_at)),
            metaCard(
                'Duration',
                Number.isFinite(durationLive) ? formatVideoTime(durationLive) : '…'
            ),
        ];
        if (elements.videoDetailGrid) {
            elements.videoDetailGrid.innerHTML = gridParts.join('');
        }

        const glam = data.glam || {};
        const hasGlamDetail =
            glam && (glam.brief_reason || glam.confidence != null || glam.has_woman != null);
        if (elements.videoGlamBlock && elements.videoGlamRow) {
            if (hasGlamDetail || (typeof data.glam_score === 'number' && data.glam_score >= 0)) {
                elements.videoGlamBlock.style.display = 'flex';
                const row = [];
                if (glam.has_woman != null) row.push(boolPill('Woman', !!glam.has_woman));
                if (glam.sexy_revealing_outfit != null) {
                    row.push(boolPill('Sexy outfit', !!glam.sexy_revealing_outfit));
                }
                if (glam.good_breasts != null) row.push(boolPill('Figure', !!glam.good_breasts));
                if (glam.confidence != null) {
                    row.push(
                        `<span class="video-pill">conf ${(Number(glam.confidence) * 100).toFixed(0)}%</span>`
                    );
                }
                if (glam.source) {
                    row.push(`<span class="video-pill">${escapeHtml(String(glam.source).slice(0, 24))}</span>`);
                }
                elements.videoGlamRow.innerHTML = row.join('') ||
                    `<span class="video-pill ${glamInfo.cls}">${escapeHtml(glamInfo.text)}</span>`;
                if (elements.videoGlamReason) {
                    elements.videoGlamReason.textContent =
                        glam.brief_reason ||
                        (data.glam_score >= 2
                            ? 'Classified as keep-worthy glam.'
                            : data.glam_score >= 0
                                ? 'Classified below keep threshold.'
                                : '');
                }
            } else {
                elements.videoGlamBlock.style.display = 'none';
            }
        }

        const caption = (data.caption || '').trim();
        if (elements.videoDetailCaption) {
            if (caption) {
                elements.videoDetailCaption.textContent = caption;
                elements.videoDetailCaption.classList.remove('empty');
            } else {
                elements.videoDetailCaption.textContent =
                    'No caption stored for this reel (metadata missing or empty).';
                elements.videoDetailCaption.classList.add('empty');
            }
        }

        if (elements.videoOpenIgBtn) {
            if (data.post_url) {
                elements.videoOpenIgBtn.href = data.post_url;
                elements.videoOpenIgBtn.style.display = 'inline-flex';
            } else {
                elements.videoOpenIgBtn.style.display = 'none';
            }
        }

        // Keep gallery card glam in sync when detail knows more
        if (typeof data.glam_score === 'number') {
            photo.glam_score = data.glam_score;
        }
    } catch (err) {
        console.error('Video detail load failed', err);
        if (elements.videoDetailCaption) {
            elements.videoDetailCaption.textContent =
                'Could not load reel metadata. File still plays on the left.';
            elements.videoDetailCaption.classList.add('empty');
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
        const noun = state.rejectOnly ? 'Rejects' : 'Photos';
        elements.deleteConfirmTitle.textContent = state.trashEnabled
            ? `Move ${paths.length} ${noun} to Trash?`
            : `Delete ${paths.length} ${noun}?`;
    }
    if (elements.deleteConfirmBody) {
        const who = state.selectedCreator ? ` for @${state.selectedCreator}` : '';
        // Favorites inside a reject sweep are the classic false-positive case — call it out.
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

    if (elements.sexyFilterBtn) {
        elements.sexyFilterBtn.addEventListener('click', () => {
            state.sexyOnly = !state.sexyOnly;
            elements.sexyFilterBtn.classList.toggle('active', state.sexyOnly);
            if (state.sexyOnly) {
                state.rejectOnly = false;
                state.unscoredOnly = false;
                if (elements.rejectFilterBtn) elements.rejectFilterBtn.classList.remove('active');
                if (elements.unscoredFilterBtn) elements.unscoredFilterBtn.classList.remove('active');
                updateReviewRejectsBar();
                // When turning Sexy on, prefer glam sort if still on default name
                if (state.sortMode === 'name' && elements.sortSelect) {
                    state.sortMode = 'glam';
                    elements.sortSelect.value = 'glam';
                }
            }
            saveViewPrefs();
            fetchPhotos();
        });
    }

    if (elements.rejectFilterBtn) {
        elements.rejectFilterBtn.addEventListener('click', () => {
            if (state.rejectOnly) {
                exitRejectReviewMode();
            } else {
                enterRejectReviewMode();
            }
        });
    }

    if (elements.unscoredFilterBtn) {
        elements.unscoredFilterBtn.addEventListener('click', () => {
            state.unscoredOnly = !state.unscoredOnly;
            elements.unscoredFilterBtn.classList.toggle('active', state.unscoredOnly);
            if (state.unscoredOnly) {
                state.sexyOnly = false;
                state.rejectOnly = false;
                if (elements.sexyFilterBtn) elements.sexyFilterBtn.classList.remove('active');
                if (elements.rejectFilterBtn) elements.rejectFilterBtn.classList.remove('active');
                updateReviewRejectsBar();
            }
            saveViewPrefs();
            fetchPhotos();
        });
    }

    if (elements.classifyCreatorBtn) {
        elements.classifyCreatorBtn.addEventListener('click', startCreatorClassify);
    }
    if (elements.syncLatestCreatorBtn) {
        elements.syncLatestCreatorBtn.addEventListener('click', syncLatestSelectedCreator);
    }
    if (elements.scrapeJobChipCancel) {
        elements.scrapeJobChipCancel.addEventListener('click', cancelRunningSyncJob);
    }
    if (elements.scrapeJobChipDismiss) {
        elements.scrapeJobChipDismiss.addEventListener('click', () => {
            state.scrapeChipDismissed = true;
            hideScrapeJobChip();
        });
    }
    if (elements.batchJobChipCancel) {
        elements.batchJobChipCancel.addEventListener('click', cancelBatchAnalyze);
    }
    if (elements.classifyJobChipCancel) {
        elements.classifyJobChipCancel.addEventListener('click', cancelCreatorClassify);
    }
    if (elements.reviewRejectsBtn) {
        elements.reviewRejectsBtn.addEventListener('click', () => {
            if (!state.selectedCreator) {
                showToast('Select a creator first');
                return;
            }
            enterRejectReviewMode();
        });
    }
    if (elements.cancelClassifyBtn) {
        elements.cancelClassifyBtn.addEventListener('click', cancelCreatorClassify);
    }
    if (elements.selectAllRejectsBtn) {
        elements.selectAllRejectsBtn.addEventListener('click', selectNonFavoriteRejects);
    }
    if (elements.deleteSelectedRejectsBtn) {
        elements.deleteSelectedRejectsBtn.addEventListener('click', () => {
            if (!state.selectedPaths.size) {
                showToast('Select rejects to delete first');
                return;
            }
            promptBulkDelete();
        });
    }
    if (elements.exitRejectsBtn) {
        elements.exitRejectsBtn.addEventListener('click', exitRejectReviewMode);
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

    // Infinite scroll — load next page near bottom of window
    let scrollTick = false;
    window.addEventListener('scroll', () => {
        if (scrollTick) return;
        scrollTick = true;
        requestAnimationFrame(() => {
            scrollTick = false;
            const nearBottom =
                window.innerHeight + window.scrollY >= document.body.offsetHeight - 600;
            if (nearBottom) loadMorePhotos();
        });
    });

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
    // Photo Viewer > Delete > Lightbox > Sync > Upload > New Creator > Creator panel > Select
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
        if (e.key === 'Escape' && state.creatorPanelOpen) {
            hideCreatorStylePanel();
            return;
        }

        if (e.key === 'Escape' && state.selectMode) {
            if (state.selectedPaths.size) clearSelection();
            else setSelectMode(false);
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
    // Keep the stack readable — batch/classify polls can push many toasts
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

function hideScrapeJobChip() {
    if (elements.scrapeJobChip) elements.scrapeJobChip.style.display = 'none';
}

function isScrapeOrSyncActive(data) {
    if (!data) return false;
    const pending = data.pending || [];
    const running = data.running_job;
    const paused = Boolean(data.paused);
    const sync = data.sync || {};
    return !!(running || pending.length || paused || sync.running);
}

function updateScrapeJobChip(data) {
    if (!elements.scrapeJobChip) return;
    const pending = data.pending || [];
    const running = data.running_job;
    const paused = Boolean(data.paused);
    const sync = data.sync || {};
    const active = isScrapeOrSyncActive(data);

    if (!active) {
        hideScrapeJobChip();
        return;
    }
    if (state.scrapeChipDismissed && !running && !sync.running) {
        // Allow hide while only pending if user dismissed; re-show if running again
        if (!pending.length) {
            hideScrapeJobChip();
            return;
        }
    }
    if (running || sync.running || paused) {
        state.scrapeChipDismissed = false;
    }

    elements.scrapeJobChip.style.display = 'flex';
    elements.scrapeJobChip.classList.toggle('paused', paused);

    if (elements.scrapeJobChipIcon) {
        elements.scrapeJobChipIcon.className =
            'fa-solid scrape-job-chip-icon ' +
            (paused ? 'fa-pause' : 'fa-arrows-rotate spinning');
    }

    let title = 'Sync idle';
    if (paused) {
        title = `Paused${data.pause_reason ? ' — ' + data.pause_reason : ''}`;
    } else if (running) {
        title = `Syncing @${running.username}`;
    } else if (sync.running && sync.scrape_username) {
        title = `Syncing @${sync.scrape_username}`;
    } else if (sync.running) {
        const jt = sync.job_type ? String(sync.job_type).replace(/_/g, ' ') : 'sync';
        title = sync.progress || `${jt} running…`;
    } else if (pending.length) {
        title = `Queued @${pending[0].username}`;
    }

    let sub = '';
    if (running) {
        const mode = running.mode || 'full';
        const deep = running.deep === true ? ' deep' : running.deep === false ? '' : '';
        sub = mode + deep;
        if (pending.length) sub += ` · +${pending.length} queued`;
        if (sync.progress) {
            // Keep chip readable — show last progress fragment
            const p = String(sync.progress);
            sub += ` · ${p.length > 80 ? p.slice(-80) : p}`;
        }
    } else if (sync.running && !running) {
        sub = sync.progress || sync.job_type || 'in progress';
        if (pending.length) sub += ` · +${pending.length} queued`;
    } else if (paused) {
        sub = pending.length
            ? `${pending.length} waiting — resume from IG Sync when ready`
            : 'Resume from IG Sync when ready';
    } else if (pending.length) {
        sub = `position 1 · ${pending.length} in queue`;
    }

    if (elements.scrapeJobChipTitle) elements.scrapeJobChipTitle.textContent = title;
    if (elements.scrapeJobChipSub) elements.scrapeJobChipSub.textContent = sub;
    if (elements.scrapeJobChipCancel) {
        elements.scrapeJobChipCancel.style.display =
            running || (sync.running && sync.job_type === 'creator_queue') ? '' : 'none';
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
        if (elements.scrapeQueueStatus) {
            let line = paused
                ? `Paused — ${data.pause_reason || 'queue paused'}`
                : running
                  ? `Running @${running.username} (${running.mode || 'full'})`
                  : pending.length
                    ? `${pending.length} pending`
                    : 'Queue idle';
            if (sync.running && sync.progress) line += ` · ${sync.progress}`;
            if (data.stats) {
                line += ` · today ${data.stats.completed_today || 0} jobs / ${data.stats.downloaded_today || 0} files`;
            }
            elements.scrapeQueueStatus.textContent = line;
        }
        if (elements.scrapeQueueList) {
            const rows = [];
            if (running) {
                rows.push(
                    `▶ @${running.username} [${running.mode}${running.deep ? ' deep' : ''}] running`
                );
            }
            pending.slice(0, 8).forEach((j, i) => {
                rows.push(`${i + 1}. @${j.username} [${j.mode}] prio ${j.priority || 0}`);
            });
            (data.history || []).slice(0, 5).forEach((j) => {
                rows.push(
                    `✓ @${j.username} → ${j.status}${j.stop_reason ? ' (' + j.stop_reason + ')' : ''}`
                );
            });
            elements.scrapeQueueList.innerHTML = rows.length
                ? rows.map((r) => `<div class="scrape-queue-row">${escapeHtml(r)}</div>`).join('')
                : '<div class="scrape-queue-row" style="opacity:0.55">No jobs yet — add a creator below</div>';
        }

        // Lock Saved/Following while anything holds the worker (running or unpaused pending).
        const workerBusy =
            Boolean(running) ||
            Boolean(sync.running) ||
            (pending.length > 0 && !paused);
        let busyReason = '';
        if (running) {
            busyReason = `Scraping @${running.username} now` +
                (pending.length ? ` · ${pending.length} more queued` : '') +
                ' — wait, Cancel job, or Clear pending';
        } else if (sync.running) {
            busyReason = `Sync busy (${sync.job_type || 'job'}) — wait or cancel`;
        } else if (pending.length > 0 && !paused) {
            busyReason =
                `Creator scrape queue has ${pending.length} pending — Clear pending or Pause queue first`;
        }
        setOneShotSyncEnabled(!workerBusy, busyReason);

        updateScrapeJobChip(data);
        updateSyncLatestButtonUi();

        // Creator list pills only when active set changes
        const pillKey = [
            running && running.username,
            pending.map((j) => j.username).join(','),
            paused ? '1' : '0',
            sync.running ? 's1' : 's0',
        ].join('|');
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
    state.scrapeChipDismissed = false;
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
        state.scrapeChipDismissed = false;
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
        state.scrapeChipDismissed = false;
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

async function pauseScrapeQueue() {
    try {
        const res = await fetch('/api/scrape/pause', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason: 'Paused by user' }),
        });
        if (res.ok) {
            showToast('Scrape queue paused');
            pollScrapeStatus();
        } else showToast('Pause failed');
    } catch (e) {
        showToast('Pause failed');
    }
}

async function resumeScrapeQueue() {
    try {
        const res = await fetch('/api/scrape/resume', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            showToast(data.drain_started ? 'Queue resumed — starting next job' : 'Queue resumed');
            pollScrapeStatus();
            pollSyncStatus();
        } else showToast('Resume failed');
    } catch (e) {
        showToast('Resume failed');
    }
}

async function cancelRunningSyncJob() {
    try {
        const res = await fetch('/api/sync/cancel', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (data.status === 'cancelling') showToast('Cancel requested…');
        else showToast('No running sync job');
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
                if (r.aborted) {
                    elements.syncStatusText.textContent =
                        `Aborted — ${r.abort_reason || 'stopped for safety'} ` +
                        `(${r.downloaded || 0} new, ${r.accounts_processed || 0} accounts)`;
                    elements.syncProgressFill.style.width = '100%';
                    elements.syncProgressFill.style.background = 'var(--accent-red)';
                    showToast(r.abort_reason || 'Following sync aborted', 4000);
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
                await initApp();
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

async function pollClassifyStatus() {
    try {
        const res = await fetch('/api/classify/status');
        const data = await res.json();
        const wasRunning = !!(state.classifyStatus && state.classifyStatus.running);
        state.classifyStatus = data;
        updateClassifyPanelUi();

        if (data.running) {
            renderJobChip('classify', {
                active: true,
                title: data.cancel_requested
                    ? `Classifying @${data.creator} — stopping`
                    : `Classifying @${data.creator}`,
                sub: `${data.completed}/${data.total} (${jobPct(data.completed, data.total)}%)` +
                    ` · keep ${data.kept || 0} · reject ${data.rejected || 0}` +
                    (data.failed ? ` · err ${data.failed}` : ''),
                completed: data.completed,
                total: data.total,
                cancellable: true,
                cancelled: Boolean(data.cancel_requested)
            });
            if (!state.classifyPollTimer) {
                state.classifyPollTimer = setInterval(pollClassifyStatus, 3000);
            }
            // Light sidebar refresh while running
            renderCreatorList();
        } else {
            if (state.classifyPollTimer) {
                clearInterval(state.classifyPollTimer);
                state.classifyPollTimer = null;
            }
            renderJobChip('classify', { active: false });
            // Only celebrate completion when we observed a running → stopped transition
            if (wasRunning) {
                const who = data.creator || 'creator';
                if (data.cancelled) {
                    showToast(
                        `Classify cancelled @${who} — ${data.completed}/${data.total} done · keep ${data.kept || 0} · reject ${data.rejected || 0}`
                    );
                } else {
                    showToast(
                        `Classify done @${who} — keep ${data.kept || 0} · reject ${data.rejected || 0}` +
                            (data.failed ? ` · err ${data.failed}` : '')
                    );
                }
                await fetchCreators();
                updateClassifyPanelUi();
                // Auto-open rejects review for that creator when any rejects found
                if ((data.rejected || 0) > 0 && data.creator) {
                    state.selectedCreator = data.creator;
                    elements.galleryTitle.textContent = `@${data.creator}`;
                    enterRejectReviewMode();
                } else {
                    await fetchPhotos();
                }
            }
        }
    } catch (err) {
        console.error('Classify status error:', err);
    }
}

async function startCreatorClassify() {
    if (!state.selectedCreator) {
        showToast('Select a creator first');
        return;
    }
    if (!requireOllama()) return;
    try {
        const res = await fetch('/api/classify/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                creator: state.selectedCreator,
                only_unscored: true,
                include_videos: true,
            }),
        });
        const data = await res.json();
        if (res.ok && data.status === 'started') {
            showToast(`Classify started — ${data.pending} media queued for @${data.creator}`);
            state.classifyStatus = {
                running: true,
                creator: data.creator,
                total: data.pending,
                completed: 0,
                kept: 0,
                rejected: 0,
                failed: 0,
            };
            updateClassifyPanelUi();
            pollClassifyStatus();
        } else if (data.status === 'nothing_to_do') {
            showToast('All media already scored for this creator');
            const meta = selectedCreatorMeta();
            if (meta && (meta.reject_count || 0) > 0) {
                enterRejectReviewMode();
            }
        } else if (data.status === 'ollama_down') {
            showToast(data.message || 'Ollama offline');
        } else {
            showToast(data.message || 'Classify busy or failed to start');
        }
    } catch (err) {
        showToast('Classify request failed');
    }
}

async function cancelCreatorClassify() {
    try {
        const res = await fetch('/api/classify/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        const data = await res.json();
        if (data.status === 'cancelling') {
            showToast('Cancelling classify after current item…');
        } else {
            showToast('No classify job running');
        }
        pollClassifyStatus();
    } catch (err) {
        showToast('Cancel failed');
    }
}
