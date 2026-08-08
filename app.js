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
    selectMode: false,
    selectedPaths: new Set(),
    photosToDelete: null,
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
    batchPollTimer: null,
    classifyPollTimer: null,
    classifyStatus: null,
    healthPollTimer: null,
    photoOffset: 0,
    photoLimit: 60,
    photoTotal: 0,
    photoHasMore: false,
    photosLoading: false,
    // Photo viewer zoom/pan state
    viewerZoom: 1,
    viewerPanX: 0,
    viewerPanY: 0,
    viewerDragging: false,
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
    // Fullscreen Photo Viewer
    photoViewerOverlay: document.getElementById('photoViewerOverlay'),
    photoViewerClose: document.getElementById('photoViewerClose'),
    photoViewerContainer: document.getElementById('photoViewerContainer'),
    photoViewerImg: document.getElementById('photoViewerImg'),
    photoViewerHint: document.getElementById('photoViewerHint'),
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
    scrapeEnqueueBtn: document.getElementById('scrapeEnqueueBtn'),
    scrapeLatestMode: document.getElementById('scrapeLatestMode'),
    scrapeFullDeep: document.getElementById('scrapeFullDeep'),
    scrapeBoundedMode: document.getElementById('scrapeBoundedMode'),
    scrapeMaxPosts: document.getElementById('scrapeMaxPosts'),
    syncLatestCreatorBtn: document.getElementById('syncLatestCreatorBtn'),
    scrapePauseBtn: document.getElementById('scrapePauseBtn'),
    scrapeResumeBtn: document.getElementById('scrapeResumeBtn'),
    scrapeCancelBtn: document.getElementById('scrapeCancelBtn'),
    scrapeClearPendingBtn: document.getElementById('scrapeClearPendingBtn'),
    scrapeQueueStatus: document.getElementById('scrapeQueueStatus'),
    scrapeQueueList: document.getElementById('scrapeQueueList'),
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

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initApp();
    setupEventListeners();
});

async function initApp() {
    await fetchHealth();
    await fetchStats();
    await fetchCreators();
    await fetchPhotos();
    // Resume classify progress UI if a job is mid-flight
    pollClassifyStatus();
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
    if (state.photosLoading) return;
    state.photosLoading = true;
    try {
        const prevLen = state.photos.length;
        if (!append) {
            state.photoOffset = 0;
            state.photos = [];
        }

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
        params.append('offset', String(state.photoOffset));
        params.append('limit', String(state.photoLimit));

        url += '?' + params.toString();

        const res = await fetch(url);
        const data = await res.json();
        const page = Array.isArray(data) ? data : (data.photos || []);
        state.photoTotal = Array.isArray(data) ? page.length : (data.total || page.length);
        state.photoHasMore = Array.isArray(data)
            ? false
            : Boolean(data.has_more);
        state.photos = append ? state.photos.concat(page) : page;
        state.photoOffset = state.photos.length;
        renderGallery({ append, fromIndex: append ? prevLen : 0 });
        updateReviewRejectsBar();
    } catch (err) {
        console.error('Error fetching photos:', err);
    } finally {
        state.photosLoading = false;
    }
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

async function deletePhoto(relPath) {
    try {
        const res = await fetch(`/api/photo?path=${encodeURIComponent(relPath)}`, { method: 'DELETE' });
        if (res.ok) {
            showToast('Photo permanently deleted');
            closeLightbox();
            initApp();
        } else {
            showToast('Error deleting photo');
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
        <span class="creator-badge">${state.creators.reduce((acc, c) => acc + c.photo_count, 0)}</span>
    `;
    allItem.addEventListener('click', () => {
        state.selectedCreator = null;
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
        const scored = c.scored_count != null ? c.scored_count : null;
        const total = c.photo_count || 0;
        const rejects = c.reject_count || 0;
        const isJob = classifyRunning && classifyCreator === c.name;
        let scorePill = '';
        if (isJob) {
            const st = state.classifyStatus;
            scorePill = `<span class="creator-score-pill running" title="Classifying…">${st.completed || 0}/${st.total || 0}</span>`;
        } else if (scored != null && total > 0) {
            const cls = rejects > 0 ? 'creator-score-pill has-rejects' : 'creator-score-pill';
            const title = rejects
                ? `${scored}/${total} scored · ${rejects} rejects`
                : `${scored}/${total} scored`;
            scorePill = `<span class="${cls}" title="${title}">${scored}/${total}</span>`;
        }
        item.innerHTML = `
            <span class="creator-name">@${c.name}${syncBadgeHtml(c)}</span>
            <span style="display:inline-flex;align-items:center;gap:6px;">
                ${scorePill}
                <span class="creator-badge">${c.photo_count}</span>
            </span>
        `;
        item.addEventListener('click', () => {
            state.selectedCreator = c.name;
            elements.galleryTitle.textContent = `@${c.name}`;
            fetchPhotos();
            renderCreatorList();
            updateCreatorStylePanel();
        });
        elements.creatorList.appendChild(item);
    });
    updateCreatorStylePanel();
}

function syncBadgeHtml(creator) {
    if (!creator.last_synced_at) return '';
    const label = formatRelativeTime(creator.last_synced_at);
    return ` <span class="sync-pill" title="Last synced ${creator.last_synced_at}">${label}</span>`;
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
        const rejects = meta && meta.reject_count != null ? meta.reject_count : 0;
        elements.reviewRejectsBtn.disabled = !creator;
        elements.reviewRejectsBtn.innerHTML =
            `<i class="fa-solid fa-filter"></i> Review rejects${rejects ? ` (${rejects})` : ''}`;
    }
    if (elements.cancelClassifyBtn) {
        elements.cancelClassifyBtn.style.display = runningHere ? 'inline-flex' : 'none';
    }
}

async function updateCreatorStylePanel() {
    if (!elements.creatorStylePanel) return;
    const creator = state.selectedCreator;
    if (!creator) {
        elements.creatorStylePanel.style.display = 'none';
        return;
    }
    elements.creatorStylePanel.style.display = 'flex';
    updateClassifyPanelUi();
    elements.creatorStylePrefix.textContent = 'Loading…';
    elements.creatorStyleTerms.innerHTML = '';
    try {
        const res = await fetch(`/api/creator/style?creator=${encodeURIComponent(creator)}`);
        const data = await res.json();
        if (data.style_prefix) {
            elements.creatorStylePrefix.textContent = data.style_prefix;
        } else {
            elements.creatorStylePrefix.textContent = data.exists
                ? 'Empty style prefix'
                : 'No style yet — analyze more photos, then Rebuild';
        }
        const terms = data.top_terms || [];
        elements.creatorStyleTerms.innerHTML = terms
            .map((t) => `<button type="button" class="tag-pill tag-clickable" data-tag="${String(t).replace(/"/g, '&quot;')}">#${t}</button>`)
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
        elements.creatorStylePrefix.textContent = 'Failed to load style';
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
        const isVideo = p.filename.toLowerCase().endsWith('.mp4') || p.filename.toLowerCase().endsWith('.webm');
        const videoBadge = isVideo ? '<div class="video-badge"><i class="fa-solid fa-play"></i></div>' : '';
        const glam = typeof p.glam_score === 'number' ? p.glam_score : -1;
        const glamBadge = glam >= 0
            ? `<span class="glam-score-badge g${glam}" title="Glam score ${glam}">g${glam}</span>`
            : '';
        card.innerHTML = `
            <img src="${imgSrc}" alt="${p.filename}" loading="lazy" data-full="${p.url}">
            ${videoBadge}
            ${glamBadge}
            <div class="photo-card-overlay">
                <div class="overlay-top-actions">
                    ${state.selectMode
                        ? `<label class="card-select-wrap" title="Select"><input type="checkbox" class="card-select-cb" ${selected ? 'checked' : ''}></label>`
                        : `<span class="prompt-status-badge ${status.cls}"><i class="fa-solid ${status.icon}"></i> ${status.label}</span>`}
                    ${favMark}
                    <button class="card-trash-btn" title="Delete Photo"><i class="fa-solid fa-trash-can"></i></button>
                </div>
                <div class="overlay-bottom-info">
                    <div class="photo-card-creator">@${p.creator}</div>
                    ${state.selectMode
                        ? `<span class="prompt-status-badge ${status.cls}"><i class="fa-solid ${status.icon}"></i> ${status.label}</span>`
                        : `<div class="photo-card-prompt-hint"><i class="fa-solid fa-wand-magic-sparkles"></i> Click for AI Prompt</div>`}
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

// Lightbox
function openLightbox(index) {
    if (index < 0 || index >= state.photos.length) return;
    state.lightboxIndex = index;
    const photo = state.photos[index];

    const isVideo = photo.filename.toLowerCase().endsWith('.mp4') || photo.filename.toLowerCase().endsWith('.webm');
    if (isVideo) {
        elements.lightboxImg.style.display = 'none';
        elements.lightboxVideo.style.display = 'block';
        elements.lightboxVideo.src = photo.url;
    } else {
        elements.lightboxVideo.style.display = 'none';
        elements.lightboxVideo.pause();
        elements.lightboxVideo.src = '';
        elements.lightboxImg.style.display = 'block';
        elements.lightboxImg.src = photo.url;
    }

    elements.lightboxCreator.textContent = `@${photo.creator}`;
    elements.lightboxFilename.textContent = photo.filename;

    resetPromptPanel();
    updateFavoriteButton(photo);
    state.compareMode = false;
    setCompareMode(false);
    loadGenerationsForPhoto(photo.rel_path);
    elements.lightboxModal.style.display = 'flex';

    // Auto-load Ready cached prompts
    if (photo.has_prompt && !photo.prompt_stale) {
        handleGeneratePrompt(false);
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
        if (workflow === 'pro' && data.seed != null && elements.comfySeedInput) {
            // Echo resolved seed when server used one — only if locked next time
        }
        const label = workflow === 'pro'
            ? `Pro d=${data.denoise ?? controls.denoise}${data.use_mode_e ? ' ModeE' : ''}`
            : variant.toUpperCase();
        showToast(`ComfyUI ${label} started`);
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

function resetPromptPanel() {
    // Show generate section, hide prompt content
    elements.generatePromptSection.style.display = 'flex';
    elements.promptContent.classList.remove('visible');
    state.currentPromptData = null;
    setPromptEditable(false);
    clearPromptDirty();

    // Reset generate button state
    const btn = elements.generatePromptBtn;
    btn.classList.remove('loading');
    btn.innerHTML = '<i class="fa-solid fa-bolt"></i> Generate AI Prompt';

    // Clear previous prompt data
    elements.positivePromptText.textContent = 'Analyzing image with Ollama Vision...';
    elements.negativePromptText.textContent = 'deformed, bad anatomy, blurry...';
    elements.promptTagsContainer.innerHTML = '';
    if (elements.promptHistory) elements.promptHistory.style.display = 'none';
    if (elements.promptHistoryList) elements.promptHistoryList.innerHTML = '';
    elements.paramSampler.textContent = 'DPM++ 2M Karras';
    elements.paramSteps.textContent = '30';
    elements.paramCFG.textContent = '7.0';
    elements.paramAspect.textContent = '4:5';
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
            const safe = String(t).replace(/"/g, '&quot;');
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
        const preview = (h.positive_prompt || '').slice(0, 80);
        const when = h.saved_at ? formatRelativeTime(h.saved_at) : `#${i + 1}`;
        return `<button type="button" class="history-item" data-index="${i}" title="Restore this version">
            <span class="history-when">${when}</span>
            <span class="history-preview">${preview}${(h.positive_prompt || '').length > 80 ? '…' : ''}</span>
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
    elements.lightboxModal.style.display = 'none';
    state.lightboxIndex = -1;
    state.currentPromptData = null;
}

function navigateLightbox(direction) {
    if (state.lightboxIndex === -1) return;
    let newIndex = state.lightboxIndex + direction;
    if (newIndex < 0) newIndex = state.photos.length - 1;
    if (newIndex >= state.photos.length) newIndex = 0;
    openLightbox(newIndex);
}

// Fullscreen Photo Viewer
function openPhotoViewer() {
    if (state.lightboxIndex === -1) return;
    const photo = state.photos[state.lightboxIndex];

    elements.photoViewerImg.src = photo.url;
    elements.photoViewerOverlay.style.display = 'flex';

    // Reset zoom/pan state
    state.viewerZoom = 1;
    state.viewerPanX = 0;
    state.viewerPanY = 0;
    applyViewerTransform();

    // Show hint, auto-hide after 3s
    elements.photoViewerHint.classList.remove('hidden');
    setTimeout(() => {
        elements.photoViewerHint.classList.add('hidden');
    }, 3000);
}

function closePhotoViewer() {
    elements.photoViewerOverlay.style.display = 'none';
    state.viewerZoom = 1;
    state.viewerPanX = 0;
    state.viewerPanY = 0;
}

function applyViewerTransform() {
    elements.photoViewerImg.style.transform =
        `translate(${state.viewerPanX}px, ${state.viewerPanY}px) scale(${state.viewerZoom})`;
}

function handleViewerWheel(e) {
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
    state.viewerDragging = true;
    state.viewerDragStartX = e.clientX;
    state.viewerDragStartY = e.clientY;
    state.viewerLastPanX = state.viewerPanX;
    state.viewerLastPanY = state.viewerPanY;
    elements.photoViewerOverlay.classList.add('dragging');
}

function handleViewerMouseMove(e) {
    if (!state.viewerDragging) return;
    state.viewerPanX = state.viewerLastPanX + (e.clientX - state.viewerDragStartX);
    state.viewerPanY = state.viewerLastPanY + (e.clientY - state.viewerDragStartY);
    applyViewerTransform();
}

function handleViewerMouseUp() {
    state.viewerDragging = false;
    elements.photoViewerOverlay.classList.remove('dragging');
}

function handleViewerDblClick(e) {
    if (e.target === elements.photoViewerClose || e.target.closest('.photo-viewer-close')) return;
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
function promptDeletePhoto(photo) {
    state.photoToDelete = photo;
    state.photosToDelete = null;
    if (elements.deleteConfirmTitle) {
        elements.deleteConfirmTitle.textContent = 'Delete Photo?';
    }
    if (elements.deleteConfirmBody) {
        elements.deleteConfirmBody.textContent =
            'Are you sure you want to permanently delete this photo from your storage folder?';
    }
    elements.deleteFilenamePreview.textContent = `${photo.creator}/${photo.filename}`;
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
    if (elements.deleteConfirmTitle) {
        elements.deleteConfirmTitle.textContent = state.rejectOnly
            ? `Delete ${paths.length} Rejects?`
            : `Delete ${paths.length} Photos?`;
    }
    if (elements.deleteConfirmBody) {
        const who = state.selectedCreator ? ` for @${state.selectedCreator}` : '';
        elements.deleteConfirmBody.textContent = state.rejectOnly
            ? `Permanently delete ${paths.length} reject(s)${who}? This cannot be undone.`
            : 'Are you sure you want to permanently delete these photos from your storage folder?';
    }
    elements.deleteFilenamePreview.textContent = preview;
    elements.deleteConfirmModal.style.display = 'flex';
}

function closeDeleteModal() {
    elements.deleteConfirmModal.style.display = 'none';
    state.photoToDelete = null;
    state.photosToDelete = null;
}

async function executeBulkDelete(paths) {
    let ok = 0;
    let fail = 0;
    for (const relPath of paths) {
        try {
            const res = await fetch(`/api/photo?path=${encodeURIComponent(relPath)}`, {
                method: 'DELETE'
            });
            if (res.ok) {
                ok += 1;
                state.selectedPaths.delete(relPath);
            } else {
                fail += 1;
            }
        } catch (err) {
            fail += 1;
        }
    }
    showToast(`Deleted ${ok}${fail ? `, ${fail} failed` : ''}`);
    closeLightbox();
    clearSelection();
    setSelectMode(false);
    await initApp();
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
    elements.searchInput.addEventListener('input', (e) => {
        state.searchQuery = e.target.value.trim();
        elements.clearSearch.style.display = state.searchQuery ? 'block' : 'none';
        fetchPhotos();
    });

    elements.clearSearch.addEventListener('click', () => {
        elements.searchInput.value = '';
        state.searchQuery = '';
        elements.clearSearch.style.display = 'none';
        fetchPhotos();
    });

    if (elements.unanalyzedFilterBtn) {
        elements.unanalyzedFilterBtn.addEventListener('click', () => {
            state.unanalyzedOnly = !state.unanalyzedOnly;
            elements.unanalyzedFilterBtn.classList.toggle('active', state.unanalyzedOnly);
            fetchPhotos();
        });
    }

    if (elements.favoritesFilterBtn) {
        elements.favoritesFilterBtn.addEventListener('click', () => {
            state.favoritesOnly = !state.favoritesOnly;
            elements.favoritesFilterBtn.classList.toggle('active', state.favoritesOnly);
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
            fetchPhotos();
        });
    }

    if (elements.classifyCreatorBtn) {
        elements.classifyCreatorBtn.addEventListener('click', startCreatorClassify);
    }
    if (elements.syncLatestCreatorBtn) {
        elements.syncLatestCreatorBtn.addEventListener('click', syncLatestSelectedCreator);
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
            fetchPhotos();
        });
    }

    if (elements.mediaTypeSelect) {
        elements.mediaTypeSelect.value = state.mediaType || 'all';
        elements.mediaTypeSelect.addEventListener('change', () => {
            state.mediaType = elements.mediaTypeSelect.value || 'all';
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
        elements.creatorSearchInput.addEventListener('input', (e) => {
            state.creatorSearchQuery = e.target.value.trim();
            renderCreatorList();
        });
    }

    elements.gridNormal.addEventListener('click', () => {
        elements.galleryGrid.classList.remove('large');
        elements.gridNormal.classList.add('active');
        elements.gridLarge.classList.remove('active');
    });

    elements.gridLarge.addEventListener('click', () => {
        elements.galleryGrid.classList.add('large');
        elements.gridLarge.classList.add('active');
        elements.gridNormal.classList.remove('active');
    });

    elements.refreshBtn.addEventListener('click', () => {
        initApp();
        showToast('Refreshed gallery archive');
    });

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
    function updateScrapeModeUi() {
        const latest = Boolean(elements.scrapeLatestMode?.checked);
        const bounded = Boolean(elements.scrapeBoundedMode?.checked);
        if (elements.scrapeMaxPosts) {
            elements.scrapeMaxPosts.disabled = !(latest || bounded);
        }
        if (elements.scrapeFullDeep) {
            elements.scrapeFullDeep.disabled = latest || bounded;
        }
        if (elements.scrapeBoundedMode && latest) {
            elements.scrapeBoundedMode.checked = false;
        }
        if (elements.scrapeLatestMode && bounded && elements.scrapeLatestMode.checked === false) {
            /* leave latest alone when only bounded toggled */
        }
    }
    if (elements.scrapeBoundedMode) {
        elements.scrapeBoundedMode.addEventListener('change', () => {
            if (elements.scrapeBoundedMode.checked && elements.scrapeLatestMode) {
                elements.scrapeLatestMode.checked = false;
            }
            updateScrapeModeUi();
        });
    }
    if (elements.scrapeLatestMode) {
        elements.scrapeLatestMode.addEventListener('change', () => {
            if (elements.scrapeLatestMode.checked && elements.scrapeBoundedMode) {
                elements.scrapeBoundedMode.checked = false;
            }
            updateScrapeModeUi();
        });
    }
    if (elements.scrapeCreatorInput) {
        elements.scrapeCreatorInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') enqueueCreatorScrape();
        });
    }
    elements.batchPromptBtn.addEventListener('click', startBatchAnalyze);

    if (elements.followingSearchInput) {
        elements.followingSearchInput.addEventListener('input', (e) => {
            const q = e.target.value.trim();
            clearTimeout(followingSearchTimer);
            followingSearchTimer = setTimeout(() => loadFollowingPicker(q), 200);
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
    document.addEventListener('mousemove', handleViewerMouseMove);
    document.addEventListener('mouseup', handleViewerMouseUp);
    elements.photoViewerOverlay.addEventListener('dblclick', handleViewerDblClick);

    // Delete Modal Actions
    elements.cancelDeleteBtn.addEventListener('click', closeDeleteModal);
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
    });

    elements.cancelNewCreatorBtn.addEventListener('click', () => {
        elements.newCreatorModal.style.display = 'none';
    });

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
                elements.newCreatorModal.style.display = 'none';
                initApp();
            } else {
                showToast('Error creating folder');
            }
        } catch (err) {
            showToast('Request failed');
        }
    });

    // Upload Modal Actions
    elements.uploadPhotoBtn.addEventListener('click', () => {
        elements.uploadModal.style.display = 'flex';
    });

    elements.cancelUploadBtn.addEventListener('click', () => {
        elements.uploadModal.style.display = 'none';
    });

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
                elements.uploadModal.style.display = 'none';
                elements.uploadFileInput.value = '';
                initApp();
            } else {
                showToast('Error uploading image');
            }
        } catch (err) {
            showToast('Upload failed');
        }
    });

    // Keyboard Navigation — Escape priority: Photo Viewer > Delete Modal > Lightbox > Select
    document.addEventListener('keydown', (e) => {
        if (elements.photoViewerOverlay.style.display === 'flex') {
            if (e.key === 'Escape') {
                e.stopPropagation();
                closePhotoViewer();
                return;
            }
        }

        if (elements.deleteConfirmModal.style.display === 'flex') {
            if (e.key === 'Escape') {
                closeDeleteModal();
                return;
            }
        }

        if (elements.lightboxModal.style.display === 'flex') {
            if (e.key === 'Escape') closeLightbox();
            if (e.key === 'ArrowLeft') navigateLightbox(-1);
            if (e.key === 'ArrowRight') navigateLightbox(1);
            // Don't steal typing when editing prompts
            const editing = document.activeElement === elements.positivePromptText
                || document.activeElement === elements.negativePromptText
                || document.activeElement === elements.searchInput;
            if (!editing && !e.ctrlKey && !e.metaKey && !e.altKey) {
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

        if (e.key === 'Escape' && state.selectMode) {
            if (state.selectedPaths.size) clearSelection();
            else setSelectMode(false);
        }
    });
}

function showToast(message, duration = 3000) {
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    elements.toastContainer.appendChild(toast);
    setTimeout(() => {
        toast.remove();
    }, duration);
}

// Instagram sync helpers
function openSyncModal() {
    elements.syncModal.style.display = 'flex';
    pollSyncStatus();
    pollScrapeStatus();
    loadFollowingPicker();
}

function setOneShotSyncEnabled(enabled, reason) {
    [elements.syncSavedBtn, elements.syncCreatorBtn, elements.syncFollowingBtn].forEach((btn) => {
        if (!btn) return;
        btn.disabled = !enabled;
        btn.title = enabled ? '' : (reason || 'Creator scrape queue has pending jobs');
    });
}

async function pollScrapeStatus() {
    const update = async () => {
        if (!elements.scrapeQueueStatus) return;
        try {
            const res = await fetch('/api/scrape/status');
            if (res.status === 404) {
                elements.scrapeQueueStatus.textContent = 'Scrape queue disabled';
                return;
            }
            const data = await res.json();
            const pending = data.pending || [];
            const running = data.running_job;
            const paused = Boolean(data.paused);
            const sync = data.sync || {};
            let line = paused
                ? `Paused — ${data.pause_reason || 'queue paused'}`
                : running
                  ? `Running @${running.username} (${running.mode || 'full'})`
                  : pending.length
                    ? `${pending.length} pending`
                    : 'Queue idle';
            if (sync.running && sync.progress) {
                line += ` · ${sync.progress}`;
            }
            if (data.stats) {
                line += ` · today ${data.stats.completed_today || 0} jobs / ${data.stats.downloaded_today || 0} files`;
            }
            elements.scrapeQueueStatus.textContent = line;
            if (elements.scrapeQueueList) {
                const rows = [];
                if (running) {
                    rows.push(`▶ @${running.username} [${running.mode}] running`);
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
                    ? rows.map((r) => `<div class="scrape-queue-row">${r}</div>`).join('')
                    : '';
            }
            // Fairness: disable one-shot when pending and not paused
            const block = pending.length > 0 && !paused;
            setOneShotSyncEnabled(
                !block,
                block
                    ? `Creator scrape queue has ${pending.length} pending — pause or empty first`
                    : ''
            );
            // Refresh gallery when a scrape job just finished
            if (
                !sync.running &&
                sync.job_type === 'creator_queue' &&
                sync.result &&
                !state._scrapeLastFinishedAt
            ) {
                state._scrapeLastFinishedAt = sync.finished_at;
                await initApp();
            } else if (sync.finished_at && sync.finished_at !== state._scrapeLastFinishedAt) {
                if (!sync.running && sync.job_type === 'creator_queue') {
                    state._scrapeLastFinishedAt = sync.finished_at;
                    await initApp();
                }
            }
        } catch (err) {
            console.error('Scrape status error:', err);
        }
    };
    await update();
    if (state.scrapePollTimer) clearInterval(state.scrapePollTimer);
    state.scrapePollTimer = setInterval(update, 2500);
}

async function syncLatestSelectedCreator() {
    const username = (state.selectedCreator || '').trim().replace(/^@/, '');
    if (!username) {
        showToast('Select a creator first');
        return;
    }
    try {
        const res = await fetch('/api/scrape/enqueue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username,
                mode: 'latest',
                max_posts: 50,
                include_videos: syncIncludeVideosValue(),
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(data.message || data.error || `Sync failed (${res.status})`);
            return;
        }
        const st = data.status || 'queued';
        if (st === 'started') showToast(`Syncing new posts for @${username}…`);
        else if (st === 'queued') showToast(`@${username} latest sync queued`);
        else if (st === 'already_pending' || st === 'already_running') {
            showToast(`@${username} already ${st.replace('already_', '')}`);
        } else showToast(`@${username}: ${st}`);
        openSyncModal();
    } catch (err) {
        showToast('Failed to start latest sync');
    }
}

async function enqueueCreatorScrape() {
    const username = (elements.scrapeCreatorInput?.value || '').trim().replace(/^@/, '');
    if (!username) {
        showToast('Enter a creator handle');
        return;
    }
    const latest = Boolean(elements.scrapeLatestMode?.checked);
    const bounded = Boolean(elements.scrapeBoundedMode?.checked);
    let mode = 'full';
    if (latest) mode = 'latest';
    else if (bounded) mode = 'bounded';
    const deep = mode === 'full' ? Boolean(elements.scrapeFullDeep?.checked ?? true) : false;
    const maxPosts = parseInt(elements.scrapeMaxPosts?.value || '50', 10);
    const body = {
        username,
        mode,
        deep,
        include_videos: syncIncludeVideosValue(),
    };
    if (mode === 'bounded' || mode === 'latest') body.max_posts = maxPosts || 50;
    try {
        const res = await fetch('/api/scrape/enqueue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(data.message || data.error || `Enqueue failed (${res.status})`);
            return;
        }
        const st = data.status || 'queued';
        if (st === 'started') showToast(`Scraping @${username} started`);
        else if (st === 'queued') showToast(`@${username} queued (#${data.position || '?'})`);
        else if (st === 'already_pending') showToast(`@${username} already pending`);
        else if (st === 'already_running') showToast(`@${username} already running`);
        else showToast(`@${username}: ${st}`);
        if (elements.scrapeCreatorInput) elements.scrapeCreatorInput.value = '';
        pollScrapeStatus();
        pollSyncStatus();
    } catch (err) {
        showToast('Failed to enqueue scrape');
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

let followingSearchTimer = null;

async function loadFollowingPicker(search = '') {
    if (!elements.followingList) return;
    elements.followingList.innerHTML = '<div class="following-empty">Loading…</div>';
    try {
        const params = new URLSearchParams();
        if (search) params.set('search', search);
        params.set('limit', '100');
        const res = await fetch('/api/following?' + params.toString());
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
        accounts.forEach((acct) => {
            const row = document.createElement('div');
            row.className = 'following-row';
            const username = acct.username || '';
            const privateBadge = acct.is_private
                ? '<span class="following-private">Private</span>'
                : '';
            row.innerHTML = `
                <div class="following-main">
                    <span class="following-user">@${username}</span>
                    <span class="following-name">${acct.full_name || ''}</span>
                    ${privateBadge}
                </div>
                <div class="following-meta">${acct.media_count ?? '—'} posts · ${formatFollowers(acct.followers_count)}</div>
            `;
            row.addEventListener('click', () => {
                elements.followingList.querySelectorAll('.following-row').forEach((r) => {
                    r.classList.remove('active');
                });
                row.classList.add('active');
                elements.syncCreatorInput.value = username;
            });
            row.addEventListener('dblclick', () => {
                elements.syncCreatorInput.value = username;
                startSyncCreator();
            });
            elements.followingList.appendChild(row);
        });
    } catch (err) {
        elements.followingList.innerHTML =
            '<div class="following-empty">Failed to load following list</div>';
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
    if (state.scrapePollTimer) {
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
            if (cq.enabled !== false && cq.pending_count > 0 && !cq.paused) {
                setOneShotSyncEnabled(
                    false,
                    `Creator scrape queue has ${cq.pending_count} pending — pause or empty first`
                );
            } else if (!state.scrapePollTimer) {
                setOneShotSyncEnabled(true, '');
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
    const username = elements.syncCreatorInput.value.trim().replace(/^@/, '');
    if (!username) {
        showToast('Enter a creator handle');
        return;
    }
    try {
        const res = await fetch('/api/sync/creator', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                username,
                max_posts: 50,
                include_videos: syncIncludeVideosValue(),
            })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(`Syncing @${username}...`);
            pollSyncStatus();
        } else {
            showToast(data.message || 'Sync busy');
        }
    } catch (err) {
        showToast('Failed to start creator sync');
    }
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

async function pollBatchStatus() {
    try {
        const res = await fetch('/api/prompt/batch/status');
        const data = await res.json();
        if (data.running) {
            const pct = data.total ? Math.round((data.completed / data.total) * 100) : 0;
            showToast(`Batch analyze: ${data.completed}/${data.total} (${pct}%)`, 1200);
            if (!state.batchPollTimer) {
                state.batchPollTimer = setInterval(pollBatchStatus, 4000);
            }
        } else {
            if (state.batchPollTimer) {
                clearInterval(state.batchPollTimer);
                state.batchPollTimer = null;
            }
            if (data.completed > 0) {
                showToast(`Batch done — ${data.completed} analyzed, ${data.failed} failed`);
                await fetchStats();
                await fetchPhotos();
            }
        }
    } catch (err) {
        console.error('Batch status error:', err);
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
            showToast(`Batch started — ${data.pending} photos queued`);
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
            const pct = data.total ? Math.round((data.completed / data.total) * 100) : 0;
            showToast(
                `Classify @${data.creator}: ${data.completed}/${data.total} (${pct}%) · keep ${data.kept || 0} · reject ${data.rejected || 0}`,
                1200
            );
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
