import { api } from "./api.js";

const FALLBACK_ART = "/static/icons/icon-192.png";
const FINISHED_STATUSES = new Set(["completed", "blocked", "declined", "failed"]);
const ACTIVE_STATUSES = new Set(["queued", "downloading", "tagging"]);

function safeReadStorage(key, fallback = "") {
  try {
    return window.localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

function safeWriteStorage(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Ignore storage restrictions
  }
}

const state = {
  bootstrap: null,
  activeProfile: safeReadStorage("kidtune_profile", "Alex"),

  // Kid search & requests
  kidSearchMode: "songs", // "songs" | "albums"
  lastQuery: "",
  searchTrackResults: [],
  searchAlbumResults: [],
  requestFilter: "all",
  requests: [],

  // Parent view, search & downloads
  parentViewOpen: false,
  activeParentTab: "approvals",
  parentSearchMode: "songs", // "songs" | "albums" | "direct"
  parentLastQuery: "",
  parentTrackResults: [],
  parentAlbumResults: [],
  parentDownloads: [],
  parentDownloadFilter: "all",
  parentQueueTarget: "kid", // "kid" | "parent"
  parentQueueFilter: "all", // "all" | "active" | "completed" | "errors"

  // Floating ambient covers
  libraryCovers: [],
  floatingSignature: "",

  // Album modal state
  albumModalOpen: false,
  albumModalForParent: false,
  albumModalData: null,

  // Audio preview
  previewAudio: null,
  previewTrackId: null,
};

const els = {
  floatingCoversLayer: document.getElementById("floating-covers-layer"),
  profileSwitcher: document.getElementById("profile-switcher"),
  parentModeBtn: document.getElementById("parent-mode-btn"),
  parentModeLabel: document.getElementById("parent-mode-label"),
  pendingBadge: document.getElementById("pending-badge"),

  kidView: document.getElementById("kid-view"),
  parentView: document.getElementById("parent-view"),
  backToKidsBtn: document.getElementById("back-to-kids-btn"),
  parentLogoutBtn: document.getElementById("parent-logout-btn"),
  defaultPinWarning: document.getElementById("default-pin-warning"),

  // Kid search
  kidSearchModeBtns: document.querySelectorAll("#kid-search-mode .mode-pill"),
  searchForm: document.getElementById("search-form"),
  searchInput: document.getElementById("search-input"),
  clearSearchBtn: document.getElementById("clear-search-btn"),
  searchSubmitBtn: document.getElementById("search-submit-btn"),
  voiceHint: document.getElementById("voice-hint"),
  resultsSection: document.getElementById("results-section"),
  resultsHeading: document.getElementById("results-heading"),
  resultsMeta: document.getElementById("results-meta"),
  resultsGrid: document.getElementById("results-grid"),
  customRequestBanner: document.getElementById("custom-request-banner"),

  // Kid requests (read-only list; cleanup is restricted to Parent Mode)
  requestsList: document.getElementById("requests-list"),
  filterTabs: document.querySelectorAll("#kid-view .filter-tab"),

  // Parent tabs & panels
  parentTabs: document.querySelectorAll(".parent-tab"),
  tabPendingCount: document.getElementById("tab-pending-count"),
  tabParentDlCount: document.getElementById("tab-parent-dl-count"),
  approvalsList: document.getElementById("approvals-list"),

  // Parent search & downloads
  parentSearchModeBtns: document.querySelectorAll("#parent-search-mode .mode-pill"),
  parentSearchPanel: document.getElementById("parent-search-panel"),
  parentDirectPanel: document.getElementById("parent-direct-panel"),
  parentSearchForm: document.getElementById("parent-search-form"),
  parentSearchInput: document.getElementById("parent-search-input"),
  parentClearSearchBtn: document.getElementById("parent-clear-search-btn"),
  parentSearchSubmitBtn: document.getElementById("parent-search-submit-btn"),
  parentResultsSection: document.getElementById("parent-results-section"),
  parentResultsHeading: document.getElementById("parent-results-heading"),
  parentResultsMeta: document.getElementById("parent-results-meta"),
  parentResultsGrid: document.getElementById("parent-results-grid"),
  parentDirectForm: document.getElementById("parent-direct-form"),
  parentDownloadsList: document.getElementById("parent-downloads-list"),
  parentDlFilterTabs: document.querySelectorAll(".parent-dl-filter-tab"),
  parentCleanupBtn: document.getElementById("parent-cleanup-btn"),
  parentCleanupMenu: document.getElementById("parent-cleanup-menu"),

  // Parent queue manager (Kid / Parent switch)
  parentQueueSwitchBtns: document.querySelectorAll("#parent-queue-switch .mode-pill"),
  queueMgrFilterTabs: document.querySelectorAll(".queue-mgr-filter-tab"),
  queueMgrCleanupBtn: document.getElementById("queue-mgr-cleanup-btn"),
  queueMgrCleanupMenu: document.getElementById("queue-mgr-cleanup-menu"),
  queueMgrList: document.getElementById("queue-mgr-list"),

  // Settings & PIN
  settingsForm: document.getElementById("settings-form"),
  pinForm: document.getElementById("pin-form"),

  // PIN modal
  pinModal: document.getElementById("pin-modal"),
  closePinModal: document.getElementById("close-pin-modal"),
  pinLoginForm: document.getElementById("pin-login-form"),
  pinLoginInput: document.getElementById("pin-login-input"),
  pinLoginError: document.getElementById("pin-login-error"),

  // Album modal
  albumModal: document.getElementById("album-modal"),
  closeAlbumModal: document.getElementById("close-album-modal"),
  albumModalTitle: document.getElementById("album-modal-title"),
  albumModalHero: document.getElementById("album-modal-hero"),
  albumModalTracks: document.getElementById("album-modal-tracks"),
  albumModalFooter: document.getElementById("album-modal-footer"),

  toastRegion: document.getElementById("toast-region"),
};

// Global image error fallback so broken-image icons never appear
document.addEventListener(
  "error",
  (event) => {
    const target = event.target;
    if (!(target instanceof HTMLImageElement)) return;
    if (target.dataset.fallbackApplied) return;
    target.dataset.fallbackApplied = "1";
    target.src = FALLBACK_ART;
  },
  true
);

function proxyArtworkUrl(rawUrl) {
  const cleaned = String(rawUrl || "").trim();
  if (!cleaned) return FALLBACK_ART;
  if (cleaned.startsWith("/")) return cleaned;
  return `/api/artwork?url=${encodeURIComponent(cleaned)}`;
}

function requestArtworkUrl(rec) {
  if (!rec || !rec.id) return FALLBACK_ART;
  return `/api/requests/${rec.id}/artwork`;
}

function showToast(message) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  els.toastRegion.appendChild(node);
  setTimeout(() => node.remove(), 3800);
}

function formatDuration(sec) {
  if (!sec || sec <= 0) return "--:--";
  const mins = Math.floor(sec / 60);
  const rem = sec % 60;
  return `${mins}:${String(rem).padStart(2, "0")}`;
}

function escapeHtml(str) {
  return String(str || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function stopPreview() {
  if (state.previewAudio) {
    state.previewAudio.pause();
    state.previewAudio = null;
  }
  state.previewTrackId = null;
}

function rerenderActiveSearchViews() {
  renderKidSearchResults();
  if (state.parentViewOpen) {
    renderParentSearchResults();
  }
  if (state.albumModalOpen && state.albumModalData) {
    renderAlbumModalContent();
  }
}

function togglePreview(track, { allowExplicit = false } = {}) {
  if (!track.preview_url || (track.is_explicit && !allowExplicit)) return;
  const key = `${track.provider || "itunes"}:${track.track_id}`;
  if (state.previewTrackId === key && state.previewAudio) {
    stopPreview();
    rerenderActiveSearchViews();
    return;
  }
  stopPreview();
  const audio = new Audio(track.preview_url);
  state.previewAudio = audio;
  state.previewTrackId = key;
  audio.addEventListener("ended", () => {
    stopPreview();
    rerenderActiveSearchViews();
  });
  audio.play().catch(() => {
    stopPreview();
    showToast("Preview unavailable on this device");
  });
  rerenderActiveSearchViews();
}

// ============================================================================
// Floating Album Covers Ambient Background (Left -> Right)
// ============================================================================

function getViewportBucket() {
  const w = window.innerWidth || 1024;
  if (w <= 600) return "mobile";
  if (w <= 900) return "tablet";
  return "desktop";
}

function renderFloatingCovers(force = false) {
  const covers = state.libraryCovers || [];
  const bucket = getViewportBucket();
  const signature = `${bucket}|${covers.map((c) => c.url).join(",")}`;
  if (!force && signature === state.floatingSignature) return;
  state.floatingSignature = signature;

  els.floatingCoversLayer.innerHTML = "";
  if (covers.length === 0) return;

  const laneConfigs =
    bucket === "mobile"
      ? [
          { top: 12, duration: 38, perLane: 2, scale: 0.92 },
          { top: 48, duration: 48, perLane: 2, scale: 1.05 },
          { top: 82, duration: 42, perLane: 2, scale: 0.96 },
        ]
      : bucket === "tablet"
      ? [
          { top: 10, duration: 44, perLane: 3, scale: 0.92 },
          { top: 34, duration: 54, perLane: 3, scale: 1.06 },
          { top: 60, duration: 48, perLane: 3, scale: 0.96 },
          { top: 83, duration: 58, perLane: 2, scale: 1.02 },
        ]
      : [
          { top: 8, duration: 46, perLane: 3, scale: 0.92 },
          { top: 27, duration: 58, perLane: 3, scale: 1.08 },
          { top: 47, duration: 50, perLane: 3, scale: 0.98 },
          { top: 67, duration: 62, perLane: 3, scale: 1.04 },
          { top: 85, duration: 52, perLane: 3, scale: 0.9 },
        ];

  const tilts = [-4, 3, -2.5, 4, -3, 2];
  let coverIndex = 0;

  laneConfigs.forEach((lane, laneIdx) => {
    const count = Math.min(lane.perLane, Math.max(1, covers.length >= 2 ? lane.perLane : 1));
    for (let slot = 0; slot < count; slot += 1) {
      const cover = covers[coverIndex % covers.length];
      coverIndex += 1;

      const tile = document.createElement("div");
      tile.className = "floating-cover-tile";

      // Evenly distribute across the horizontal travel timeline using negative delays
      const fraction = (slot + (laneIdx * 0.37) % 1) / count;
      const delaySec = -(fraction * lane.duration);
      const tilt = tilts[(laneIdx + slot) % tilts.length];
      const bobDuration = 6.5 + ((laneIdx * 3 + slot * 2) % 5);
      const staticLeft = Math.round(((slot + 0.5) / count) * 84 + 6);

      tile.style.setProperty("--lane-top", `${lane.top}%`);
      tile.style.setProperty("--drift-duration", `${lane.duration}s`);
      tile.style.setProperty("--drift-delay", `${delaySec.toFixed(2)}s`);
      tile.style.setProperty("--tile-scale", String(lane.scale));
      tile.style.setProperty("--tile-tilt", `${tilt}deg`);
      tile.style.setProperty("--bob-duration", `${bobDuration}s`);
      tile.style.setProperty("--static-left", `${staticLeft}%`);

      const img = document.createElement("img");
      img.className = "floating-cover-img";
      img.src = cover.url;
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";

      tile.appendChild(img);
      els.floatingCoversLayer.appendChild(tile);
    }
  });
}

let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => renderFloatingCovers(), 250);
});

// ============================================================================
// Header & Profiles
// ============================================================================

function renderProfiles() {
  const profiles = state.bootstrap?.kid_profiles || ["Alex", "Sam", "Family"];
  if (!profiles.includes(state.activeProfile)) {
    state.activeProfile = profiles[0];
    safeWriteStorage("kidtune_profile", state.activeProfile);
  }
  els.profileSwitcher.innerHTML = "";
  for (const name of profiles) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `profile-chip ${name === state.activeProfile ? "active" : ""}`;
    btn.textContent = name;
    btn.addEventListener("click", () => {
      state.activeProfile = name;
      safeWriteStorage("kidtune_profile", name);
      renderProfiles();
      renderRequests();
    });
    els.profileSwitcher.appendChild(btn);
  }
}

function renderHeaderStatus() {
  const b = state.bootstrap;
  if (!b) return;
  const pending = b.pending_approval_count || 0;
  els.pendingBadge.textContent = String(pending);
  els.pendingBadge.classList.toggle("hidden", pending === 0);
  els.tabPendingCount.textContent = String(pending);

  const parentActive = b.parent_active_downloads_count || 0;
  els.tabParentDlCount.textContent = String(parentActive);
  els.tabParentDlCount.classList.toggle("hidden", parentActive === 0);

  els.parentModeLabel.textContent = b.parent_authenticated ? "Parent Controls" : "Parent Mode";
  els.defaultPinWarning.classList.toggle("hidden", !(b.parent_authenticated && b.parent_pin_is_default));

  if (b.google_keep_enabled && b.google_keep_list_name) {
    els.voiceHint.textContent = `Voice Tip: Say “Hey Google, add [Song by Artist] to my ${b.google_keep_list_name} list”`;
    els.voiceHint.classList.remove("hidden");
  } else {
    els.voiceHint.classList.add("hidden");
  }
}

// ============================================================================
// Shared Song & Album Card Builders (Kid View & Parent Mode)
// ============================================================================

function buildSafetyBadgeHtml(verdict, safety, { forParent = false } = {}) {
  if (safety.blocked) {
    return `<span class="tag tag-explicit">${forParent ? "Explicit" : "Explicit Blocked"}</span>`;
  }
  if (verdict === "cleaned_edit") {
    return `<span class="tag tag-clean">Clean Edit</span>`;
  }
  if (safety.allowed_auto) {
    return `<span class="tag tag-clean">Kid-Safe</span>`;
  }
  return `<span class="tag tag-pending">${forParent ? "Unverified" : "Needs Parent Check"}</span>`;
}

function createTrackCard(item, { forParent = false } = {}) {
  const { track, safety } = item;
  const card = document.createElement("article");
  card.className = "track-card";

  const artSrc = proxyArtworkUrl(track.artwork_url);
  const badgeHtml = buildSafetyBadgeHtml(track.explicit_verdict, safety, { forParent });
  const previewKey = `${track.provider || "itunes"}:${track.track_id}`;
  const artClass = !forParent && safety.blocked ? "track-art art-blurred" : "track-art";
  const reasonLine =
    safety.blocked && safety.reasons?.length
      ? `<p class="track-reason">${escapeHtml(safety.reasons[0])}</p>`
      : "";

  card.innerHTML = `
    <div class="track-main">
      <img class="${artClass}" src="${escapeHtml(artSrc)}" alt="${escapeHtml(track.album || track.title)} album art" loading="lazy" />
      <div class="track-info">
        <h3 class="track-title">${escapeHtml(track.title)}</h3>
        <p class="track-artist">${escapeHtml(track.artist)}</p>
        <p class="track-album">${escapeHtml(track.album)}${track.year ? ` • ${escapeHtml(track.year)}` : ""}</p>
        <div class="track-meta-row">
          ${badgeHtml}
          <span class="tag tag-neutral">${formatDuration(track.duration_sec)}</span>
        </div>
        ${reasonLine}
      </div>
    </div>
    <div class="track-actions"></div>
  `;

  const actions = card.querySelector(".track-actions");
  const canPreview = track.preview_url && (forParent || (!track.is_explicit && !safety.blocked));
  if (canPreview) {
    const previewBtn = document.createElement("button");
    previewBtn.type = "button";
    previewBtn.className = "btn btn-secondary btn-small";
    previewBtn.textContent = state.previewTrackId === previewKey ? "Stop" : "Preview";
    previewBtn.addEventListener("click", () => togglePreview(track, { allowExplicit: forParent }));
    actions.appendChild(previewBtn);
  }

  if (forParent) {
    if (safety.blocked && safety.clean_alternative) {
      const cleanBtn = document.createElement("button");
      cleanBtn.type = "button";
      cleanBtn.className = "btn btn-teal btn-small";
      cleanBtn.textContent = "Clean Edit";
      cleanBtn.addEventListener("click", () =>
        submitSelectedTrack(track, { forParent: true, useCleanAlternative: true })
      );
      actions.appendChild(cleanBtn);
    }
    const dlBtn = document.createElement("button");
    dlBtn.type = "button";
    dlBtn.className = "btn btn-primary btn-small";
    dlBtn.textContent = safety.blocked ? "Download Anyway" : "Download";
    dlBtn.addEventListener("click", () =>
      submitSelectedTrack(track, { forParent: true, parentOverride: safety.blocked })
    );
    actions.appendChild(dlBtn);
  } else if (safety.blocked) {
    if (safety.clean_alternative) {
      const cleanBtn = document.createElement("button");
      cleanBtn.type = "button";
      cleanBtn.className = "btn btn-teal btn-small";
      cleanBtn.textContent = "Request Clean Edit";
      cleanBtn.addEventListener("click", () =>
        submitSelectedTrack(track, { useCleanAlternative: true })
      );
      actions.appendChild(cleanBtn);
    } else {
      const lockedBtn = document.createElement("button");
      lockedBtn.type = "button";
      lockedBtn.className = "btn btn-danger btn-small";
      lockedBtn.disabled = true;
      lockedBtn.textContent = "Explicit Blocked";
      actions.appendChild(lockedBtn);
    }
  } else {
    const reqBtn = document.createElement("button");
    reqBtn.type = "button";
    reqBtn.className = "btn btn-primary btn-small";
    reqBtn.textContent = safety.requires_approval ? "Ask Parent" : "Request Song";
    reqBtn.addEventListener("click", () => submitSelectedTrack(track));
    actions.appendChild(reqBtn);
  }

  return card;
}

function createAlbumCard(item, { forParent = false } = {}) {
  const { album, safety } = item;
  const card = document.createElement("article");
  card.className = "track-card";

  const artSrc = proxyArtworkUrl(album.artwork_url);
  const badgeHtml = buildSafetyBadgeHtml(album.explicit_verdict, safety, { forParent });
  const artClass = !forParent && safety.blocked ? "track-art art-blurred" : "track-art";
  const trackLabel = album.track_count
    ? `${album.track_count} track${album.track_count === 1 ? "" : "s"}`
    : "Album";

  card.innerHTML = `
    <div class="track-main">
      <img class="${artClass}" src="${escapeHtml(artSrc)}" alt="${escapeHtml(album.title)} album cover" loading="lazy" />
      <div class="track-info">
        <h3 class="track-title">${escapeHtml(album.title)}</h3>
        <p class="track-artist">${escapeHtml(album.artist)}</p>
        <p class="track-album">${escapeHtml(album.genre || "Album")}${album.year ? ` • ${escapeHtml(album.year)}` : ""}</p>
        <div class="track-meta-row">
          ${badgeHtml}
          <span class="tag tag-neutral">${escapeHtml(trackLabel)}</span>
        </div>
      </div>
    </div>
    <div class="track-actions"></div>
  `;

  const actions = card.querySelector(".track-actions");

  const viewBtn = document.createElement("button");
  viewBtn.type = "button";
  viewBtn.className = "btn btn-secondary btn-small";
  viewBtn.textContent = "View Tracks";
  viewBtn.addEventListener("click", () => openAlbumModal(album, { forParent }));
  actions.appendChild(viewBtn);

  if (forParent) {
    const dlBtn = document.createElement("button");
    dlBtn.type = "button";
    dlBtn.className = "btn btn-primary btn-small";
    dlBtn.textContent = "Download Album";
    dlBtn.addEventListener("click", () =>
      submitAlbumDownload(album, {
        forParent: true,
        parentOverride: safety.blocked,
        skipExplicit: false,
        triggerBtn: dlBtn,
      })
    );
    actions.appendChild(dlBtn);
  } else if (safety.blocked) {
    if (album.clean_alternative_id && album.clean_alternative_id.includes(":")) {
      const [altProvider, altId] = album.clean_alternative_id.split(":", 2);
      const cleanBtn = document.createElement("button");
      cleanBtn.type = "button";
      cleanBtn.className = "btn btn-teal btn-small";
      cleanBtn.textContent = "Clean Edition";
      cleanBtn.addEventListener("click", () =>
        openAlbumModal({ ...album, provider: altProvider, album_id: altId }, { forParent: false })
      );
      actions.appendChild(cleanBtn);
    } else {
      const lockedBtn = document.createElement("button");
      lockedBtn.type = "button";
      lockedBtn.className = "btn btn-danger btn-small";
      lockedBtn.disabled = true;
      lockedBtn.textContent = "Explicit Album";
      actions.appendChild(lockedBtn);
    }
  } else {
    const reqBtn = document.createElement("button");
    reqBtn.type = "button";
    reqBtn.className = "btn btn-primary btn-small";
    reqBtn.textContent = safety.requires_approval ? "Ask for Album" : "Request Album";
    reqBtn.addEventListener("click", () =>
      submitAlbumDownload(album, { forParent: false, skipExplicit: true, triggerBtn: reqBtn })
    );
    actions.appendChild(reqBtn);
  }

  return card;
}

// ============================================================================
// Kid Search Results
// ============================================================================

function renderKidSearchResults() {
  if (!state.lastQuery) {
    els.resultsSection.classList.add("hidden");
    return;
  }
  els.resultsSection.classList.remove("hidden");
  const isAlbums = state.kidSearchMode === "albums";
  const list = isAlbums ? state.searchAlbumResults : state.searchTrackResults;

  els.resultsHeading.textContent = isAlbums ? "Album Results" : "Song Results";
  els.resultsMeta.textContent = `${list.length} ${isAlbums ? "album" : "song"}${list.length === 1 ? "" : "s"} for “${state.lastQuery}”`;
  els.resultsGrid.innerHTML = "";

  if (list.length === 0) {
    els.resultsGrid.innerHTML = `<div class="empty-state">No matching ${isAlbums ? "albums" : "songs"} found for “${escapeHtml(state.lastQuery)}”.</div>`;
  } else {
    for (const item of list) {
      els.resultsGrid.appendChild(
        isAlbums
          ? createAlbumCard(item, { forParent: false })
          : createTrackCard(item, { forParent: false })
      );
    }
  }

  // Offer custom parent-approval fallback when searching songs
  if (!isAlbums) {
    els.customRequestBanner.classList.remove("hidden");
    els.customRequestBanner.innerHTML = `
      <div>
        <strong>Can't find the exact song above?</strong>
        <div class="section-sub">Send “${escapeHtml(state.lastQuery)}” to a parent to check and add manually.</div>
      </div>
    `;
    const customBtn = document.createElement("button");
    customBtn.type = "button";
    customBtn.className = "btn btn-secondary btn-small";
    customBtn.textContent = "Send to Parent";
    customBtn.addEventListener("click", async () => {
      customBtn.disabled = true;
      try {
        const rec = await api.submitRequest({
          query: state.lastQuery,
          requester_name: state.activeProfile,
          source: "ipad",
        });
        showToast(rec.status === "blocked" ? rec.status_detail : `Sent “${state.lastQuery}”!`);
        await refreshState();
      } catch (err) {
        showToast(err.message);
      } finally {
        customBtn.disabled = false;
      }
    });
    els.customRequestBanner.appendChild(customBtn);
  } else {
    els.customRequestBanner.classList.add("hidden");
  }
}

// ============================================================================
// Parent Search Results
// ============================================================================

function renderParentSearchResults() {
  if (!state.parentLastQuery || state.parentSearchMode === "direct") {
    els.parentResultsSection.classList.add("hidden");
    return;
  }
  els.parentResultsSection.classList.remove("hidden");
  const isAlbums = state.parentSearchMode === "albums";
  const list = isAlbums ? state.parentAlbumResults : state.parentTrackResults;

  els.parentResultsHeading.textContent = isAlbums ? "Album Results (Parent)" : "Song Results (Parent)";
  els.parentResultsMeta.textContent = `${list.length} ${isAlbums ? "album" : "song"}${list.length === 1 ? "" : "s"} for “${state.parentLastQuery}”`;
  els.parentResultsGrid.innerHTML = "";

  if (list.length === 0) {
    els.parentResultsGrid.innerHTML = `<div class="empty-state">No matching ${isAlbums ? "albums" : "songs"} found for “${escapeHtml(state.parentLastQuery)}”.</div>`;
    return;
  }

  for (const item of list) {
    els.parentResultsGrid.appendChild(
      isAlbums
        ? createAlbumCard(item, { forParent: true })
        : createTrackCard(item, { forParent: true })
    );
  }
}

// ============================================================================
// Album Tracklist Modal
// ============================================================================

async function openAlbumModal(album, { forParent = false } = {}) {
  state.albumModalOpen = true;
  state.albumModalForParent = forParent;
  state.albumModalData = null;

  els.albumModalTitle.textContent = album.title || "Album Tracks";
  els.albumModalHero.innerHTML = `
    <img class="album-modal-art" src="${escapeHtml(proxyArtworkUrl(album.artwork_url))}" alt="" />
    <div class="album-modal-meta">
      <h3>${escapeHtml(album.title)}</h3>
      <p>${escapeHtml(album.artist)}${album.year ? ` • ${escapeHtml(album.year)}` : ""}</p>
      <p class="section-sub">Checking track list &amp; lyrics safety...</p>
    </div>
  `;
  els.albumModalTracks.innerHTML = `<div class="empty-state">Loading album tracks...</div>`;
  els.albumModalFooter.innerHTML = "";
  els.albumModal.classList.remove("hidden");

  try {
    const details = await api.getAlbumDetails(album.provider || "itunes", album.album_id);
    if (!state.albumModalOpen) return;
    state.albumModalData = details;
    renderAlbumModalContent();
  } catch (err) {
    els.albumModalTracks.innerHTML = `<div class="empty-state">${escapeHtml(err.message)}</div>`;
  }
}

function closeAlbumModal() {
  state.albumModalOpen = false;
  state.albumModalData = null;
  els.albumModal.classList.add("hidden");
}

function renderAlbumModalContent() {
  const details = state.albumModalData;
  if (!details) return;
  const { album, tracks } = details;
  const forParent = state.albumModalForParent;

  const safeCount = tracks.filter((t) => !t.safety.blocked).length;
  const explicitCount = tracks.length - safeCount;

  els.albumModalTitle.textContent = album.title;
  els.albumModalHero.innerHTML = `
    <img class="album-modal-art" src="${escapeHtml(proxyArtworkUrl(album.artwork_url))}" alt="" />
    <div class="album-modal-meta">
      <h3>${escapeHtml(album.title)}</h3>
      <p>${escapeHtml(album.artist)}${album.year ? ` • ${escapeHtml(album.year)}` : ""} • ${tracks.length} track${tracks.length === 1 ? "" : "s"}</p>
      <div class="track-meta-row">
        <span class="tag tag-clean">${safeCount} Safe</span>
        ${explicitCount > 0 ? `<span class="tag tag-explicit">${explicitCount} Explicit</span>` : ""}
      </div>
    </div>
  `;

  els.albumModalTracks.innerHTML = "";
  if (tracks.length === 0) {
    els.albumModalTracks.innerHTML = `<div class="empty-state">No tracks were returned for this album.</div>`;
  } else {
    for (const item of tracks) {
      const { track, safety } = item;
      const row = document.createElement("div");
      row.className = "album-track-row";
      const previewKey = `${track.provider || "itunes"}:${track.track_id}`;
      const badgeHtml = buildSafetyBadgeHtml(track.explicit_verdict, safety, { forParent });

      row.innerHTML = `
        <div class="album-track-left">
          <span class="album-track-num">${track.track_number}</span>
          <div class="album-track-info">
            <div class="album-track-title">${escapeHtml(track.title)}</div>
            <div class="album-track-sub">
              ${badgeHtml}
              <span class="tag tag-neutral">${formatDuration(track.duration_sec)}</span>
            </div>
          </div>
        </div>
        <div class="album-track-actions"></div>
      `;

      const rowActions = row.querySelector(".album-track-actions");
      if (track.preview_url && (forParent || (!track.is_explicit && !safety.blocked))) {
        const pv = document.createElement("button");
        pv.type = "button";
        pv.className = "btn btn-ghost btn-small";
        pv.textContent = state.previewTrackId === previewKey ? "Stop" : "Preview";
        pv.addEventListener("click", () => togglePreview(track, { allowExplicit: forParent }));
        rowActions.appendChild(pv);
      }

      if (forParent) {
        const dlBtn = document.createElement("button");
        dlBtn.type = "button";
        dlBtn.className = "btn btn-secondary btn-small";
        dlBtn.textContent = "Download";
        dlBtn.addEventListener("click", () =>
          submitSelectedTrack(track, { forParent: true, parentOverride: safety.blocked })
        );
        rowActions.appendChild(dlBtn);
      } else if (!safety.blocked) {
        const rqBtn = document.createElement("button");
        rqBtn.type = "button";
        rqBtn.className = "btn btn-secondary btn-small";
        rqBtn.textContent = safety.requires_approval ? "Ask" : "Request";
        rqBtn.addEventListener("click", () => submitSelectedTrack(track));
        rowActions.appendChild(rqBtn);
      }

      els.albumModalTracks.appendChild(row);
    }
  }

  els.albumModalFooter.innerHTML = "";
  if (forParent) {
    const leftWrap = document.createElement("label");
    leftWrap.className = "form-checkbox";
    leftWrap.innerHTML = `
      <input id="album-include-explicit" type="checkbox" checked />
      <span>Include explicit tracks (${explicitCount})</span>
    `;
    els.albumModalFooter.appendChild(leftWrap);

    const dlAllBtn = document.createElement("button");
    dlAllBtn.type = "button";
    dlAllBtn.className = "btn btn-primary";
    dlAllBtn.textContent = `Download Full Album (${tracks.length})`;
    dlAllBtn.addEventListener("click", async () => {
      const includeExp = Boolean(document.getElementById("album-include-explicit")?.checked);
      await submitAlbumDownload(album, {
        forParent: true,
        parentOverride: includeExp,
        skipExplicit: !includeExp,
        triggerBtn: dlAllBtn,
      });
      closeAlbumModal();
    });
    els.albumModalFooter.appendChild(dlAllBtn);
  } else {
    const note = document.createElement("span");
    note.className = "section-sub";
    note.textContent =
      explicitCount > 0
        ? `${explicitCount} explicit track${explicitCount === 1 ? "" : "s"} will be skipped automatically.`
        : `All ${tracks.length} tracks will be checked & added to Plex.`;
    els.albumModalFooter.appendChild(note);

    const reqAllBtn = document.createElement("button");
    reqAllBtn.type = "button";
    reqAllBtn.className = "btn btn-primary";
    reqAllBtn.disabled = safeCount === 0;
    reqAllBtn.textContent =
      safeCount === 0
        ? "No Kid-Safe Tracks"
        : `Request ${safeCount} Safe Track${safeCount === 1 ? "" : "s"}`;
    reqAllBtn.addEventListener("click", async () => {
      await submitAlbumDownload(album, {
        forParent: false,
        skipExplicit: true,
        triggerBtn: reqAllBtn,
      });
      closeAlbumModal();
    });
    els.albumModalFooter.appendChild(reqAllBtn);
  }
}

// ============================================================================
// Submit Single Track & Full Album Requests
// ============================================================================

async function submitSelectedTrack(
  track,
  { useCleanAlternative = false, forParent = false, parentOverride = false } = {}
) {
  try {
    const rec = await api.submitRequest({
      query: `${track.title} by ${track.artist}`,
      requester_name: forParent ? "Parent" : state.activeProfile,
      source: forParent ? "parent" : "ipad",
      catalog_track: track,
      use_clean_alternative: useCleanAlternative,
      parent_override: forParent && parentOverride,
    });
    if (forParent) {
      showToast(`Queued “${rec.title}” in Parent Downloads!`);
    } else if (rec.status === "completed" || rec.status === "queued") {
      showToast(`Added “${rec.title}” to the Plex queue!`);
    } else if (rec.status === "pending_approval") {
      showToast(`Sent “${rec.title}” to Parent Approval`);
    } else {
      showToast(rec.status_detail || "Request blocked");
    }
    await refreshState();
  } catch (err) {
    showToast(err.message);
  }
}

async function submitAlbumDownload(
  album,
  { forParent = false, parentOverride = false, skipExplicit = true, triggerBtn = null } = {}
) {
  const prevLabel = triggerBtn ? triggerBtn.textContent : "";
  if (triggerBtn) {
    triggerBtn.disabled = true;
    triggerBtn.textContent = "Queueing...";
  }
  try {
    const res = await api.submitAlbumRequest({
      provider: album.provider || "itunes",
      album_id: album.album_id,
      requester_name: forParent ? "Parent" : state.activeProfile,
      source: forParent ? "parent" : "ipad",
      skip_explicit: skipExplicit,
      parent_override: forParent && parentOverride,
    });
    const parts = [];
    if (res.queued_count > 0) parts.push(`${res.queued_count} queued`);
    if (res.pending_count > 0) parts.push(`${res.pending_count} sent to Parent Approval`);
    if (res.skipped_explicit_count > 0) parts.push(`${res.skipped_explicit_count} explicit skipped`);
    if (res.blocked_count > 0) parts.push(`${res.blocked_count} blocked`);
    const summary = parts.length ? parts.join(", ") : "no tracks queued";
    showToast(`${res.album_title}: ${summary}`);
    await refreshState();
  } catch (err) {
    showToast(err.message);
  } finally {
    if (triggerBtn) {
      triggerBtn.disabled = false;
      triggerBtn.textContent = prevLabel;
    }
  }
}

// ============================================================================
// Request Rows (Kid Song Requests & Separate Parent Downloads)
// ============================================================================

function statusLabel(status) {
  const labels = {
    pending_approval: "Waiting for Parent",
    queued: "In Queue",
    downloading: "Downloading",
    tagging: "Tagging Metadata",
    completed: "Ready in Plexamp",
    blocked: "Blocked (Explicit)",
    declined: "Declined",
    failed: "Error",
  };
  return labels[status] || status;
}

function createRequestRow(r, { isParentList = false, allowManage = false } = {}) {
  const row = document.createElement("div");
  row.className = "request-row";
  const art = requestArtworkUrl(r);
  const artClass = !isParentList && r.status === "blocked" ? "request-art art-blurred" : "request-art";
  const sourceText =
    r.source === "google_home" ? "Google Home" : r.source === "parent" ? r.requester_name || "Parent" : r.requester_name;

  row.innerHTML = `
    <div class="request-left">
      <img class="${artClass}" src="${escapeHtml(art)}" alt="" loading="lazy" />
      <div class="request-text">
        <div class="request-title">${escapeHtml(r.title || r.query)}</div>
        <p class="request-sub">
          ${escapeHtml(r.artist || "Custom Request")}${r.album ? ` • ${escapeHtml(r.album)}` : ""}${isParentList ? "" : ` • For ${escapeHtml(sourceText)}`}
        </p>
        ${r.status_detail ? `<p class="request-sub">${escapeHtml(r.status_detail)}</p>` : ""}
      </div>
    </div>
    <div class="request-right">
      <span class="status-pill status-${escapeHtml(r.status)}">${escapeHtml(statusLabel(r.status))}</span>
    </div>
  `;

  const right = row.querySelector(".request-right");

  if (allowManage && r.status === "failed") {
    const retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "btn btn-secondary btn-small";
    retryBtn.textContent = "Retry";
    retryBtn.addEventListener("click", async () => {
      try {
        await api.retryRequest(r.id);
        showToast("Re-queued download");
        await refreshState();
      } catch (err) {
        showToast(err.message);
      }
    });
    right.appendChild(retryBtn);
  }

  if (allowManage && FINISHED_STATUSES.has(r.status)) {
    const dismissBtn = document.createElement("button");
    dismissBtn.type = "button";
    dismissBtn.className = "btn-icon row-dismiss-btn";
    dismissBtn.title = "Remove from queue";
    dismissBtn.setAttribute("aria-label", `Remove ${r.title || r.query} from queue`);
    dismissBtn.innerHTML = "&times;";
    dismissBtn.addEventListener("click", async () => {
      try {
        await api.deleteRequest(r.id);
        await refreshState();
      } catch (err) {
        showToast(err.message);
      }
    });
    right.appendChild(dismissBtn);
  }

  return row;
}

function renderRequests() {
  let items = (state.requests || []).filter((r) => r.source !== "parent");
  if (state.requestFilter === "mine") {
    items = items.filter((r) => r.requester_name === state.activeProfile);
  } else if (state.requestFilter === "completed") {
    items = items.filter((r) => r.status === "completed");
  }

  if (items.length === 0) {
    els.requestsList.innerHTML = `<div class="empty-state">No song requests here yet. Search for a favorite song or album above!</div>`;
    return;
  }

  els.requestsList.innerHTML = "";
  for (const r of items) {
    els.requestsList.appendChild(createRequestRow(r, { isParentList: false, allowManage: false }));
  }
}

function renderParentDownloads() {
  if (!state.bootstrap?.parent_authenticated) return;
  let items = state.parentDownloads || [];
  if (state.parentDownloadFilter === "active") {
    items = items.filter((r) => ACTIVE_STATUSES.has(r.status));
  } else if (state.parentDownloadFilter === "completed") {
    items = items.filter((r) => r.status === "completed");
  }

  if (items.length === 0) {
    els.parentDownloadsList.innerHTML = `<div class="empty-state">No parent downloads yet. Search for songs or full albums above — they stay hidden from the kids' Song Requests area.</div>`;
    return;
  }

  els.parentDownloadsList.innerHTML = "";
  for (const r of items) {
    els.parentDownloadsList.appendChild(createRequestRow(r, { isParentList: true, allowManage: true }));
  }
}

function renderParentQueues() {
  if (!state.bootstrap?.parent_authenticated) return;
  const isParentQueue = state.parentQueueTarget === "parent";
  let items = isParentQueue
    ? state.parentDownloads || []
    : (state.requests || []).filter((r) => r.source !== "parent");

  if (state.parentQueueFilter === "active") {
    items = items.filter((r) => ACTIVE_STATUSES.has(r.status) || r.status === "pending_approval");
  } else if (state.parentQueueFilter === "completed") {
    items = items.filter((r) => r.status === "completed");
  } else if (state.parentQueueFilter === "errors") {
    items = items.filter((r) => ["blocked", "declined", "failed"].includes(r.status));
  }

  if (items.length === 0) {
    els.queueMgrList.innerHTML = `<div class="empty-state">No ${isParentQueue ? "parent download" : "kid request"} items match this filter.</div>`;
    return;
  }

  els.queueMgrList.innerHTML = "";
  for (const r of items) {
    els.queueMgrList.appendChild(
      createRequestRow(r, { isParentList: isParentQueue, allowManage: true })
    );
  }
}

function renderApprovals() {
  if (!state.bootstrap?.parent_authenticated) return;
  const items = (state.requests || []).filter(
    (r) => r.source !== "parent" && ["pending_approval", "blocked", "failed"].includes(r.status)
  );
  if (items.length === 0) {
    els.approvalsList.innerHTML = `<div class="empty-state">All caught up! No kid requests waiting for parent review.</div>`;
    return;
  }

  els.approvalsList.innerHTML = "";
  for (const r of items) {
    const card = document.createElement("div");
    card.className = "approval-card";
    const reasons = (r.safety_reasons || []).join(" • ") || r.status_detail || "Needs review";

    card.innerHTML = `
      <div class="request-left">
        <img class="request-art" src="${escapeHtml(requestArtworkUrl(r))}" alt="" />
        <div class="request-text">
          <div class="request-title">${escapeHtml(r.title || r.query)}</div>
          <p class="request-sub">Requested by <strong>${escapeHtml(r.requester_name)}</strong> via ${escapeHtml(r.source)} • Query: “${escapeHtml(r.query)}”</p>
          <p class="request-sub">${escapeHtml(reasons)}</p>
        </div>
      </div>
      <div class="approval-meta-grid">
        <div class="form-field">
          <label>Title</label>
          <input type="text" data-field="title" value="${escapeHtml(r.title || r.query)}" />
        </div>
        <div class="form-field">
          <label>Artist</label>
          <input type="text" data-field="artist" value="${escapeHtml(r.artist)}" />
        </div>
        <div class="form-field">
          <label>Album</label>
          <input type="text" data-field="album" value="${escapeHtml(r.album)}" />
        </div>
        <div class="form-field">
          <label>Year</label>
          <input type="text" data-field="year" value="${escapeHtml(r.year)}" />
        </div>
        <div class="form-field">
          <label>Optional YouTube URL</label>
          <input type="url" data-field="source_url" value="${escapeHtml(r.source_url)}" placeholder="Optional override URL" />
        </div>
      </div>
      <div class="form-actions"></div>
    `;

    const actions = card.querySelector(".form-actions");
    const getOverrides = () => {
      const payload = {};
      for (const input of card.querySelectorAll("[data-field]")) {
        const key = input.dataset.field;
        const val = input.value.trim();
        if (val) payload[key] = val;
      }
      return payload;
    };

    if (r.status === "failed") {
      const retryBtn = document.createElement("button");
      retryBtn.type = "button";
      retryBtn.className = "btn btn-primary btn-small";
      retryBtn.textContent = "Retry Download";
      retryBtn.addEventListener("click", async () => {
        await api.retryRequest(r.id);
        showToast("Re-queued download");
        await refreshState();
      });
      actions.appendChild(retryBtn);
    } else if (r.status === "blocked") {
      if (r.catalog_metadata?.clean_alternative) {
        const cleanBtn = document.createElement("button");
        cleanBtn.type = "button";
        cleanBtn.className = "btn btn-teal btn-small";
        cleanBtn.textContent = "Approve Clean Version";
        cleanBtn.addEventListener("click", async () => {
          await api.approveRequest(r.id, { use_clean_alternative: true });
          showToast("Clean version queued!");
          await refreshState();
        });
        actions.appendChild(cleanBtn);
      }
      const overrideBtn = document.createElement("button");
      overrideBtn.type = "button";
      overrideBtn.className = "btn btn-secondary btn-small";
      overrideBtn.textContent = "Override Explicit & Download";
      overrideBtn.addEventListener("click", async () => {
        await api.approveRequest(r.id, { ...getOverrides(), override_explicit: true });
        showToast("Parent override queued");
        await refreshState();
      });
      actions.appendChild(overrideBtn);
    } else {
      const approveBtn = document.createElement("button");
      approveBtn.type = "button";
      approveBtn.className = "btn btn-primary btn-small";
      approveBtn.textContent = "Approve & Download";
      approveBtn.addEventListener("click", async () => {
        await api.approveRequest(r.id, getOverrides());
        showToast("Approved and queued!");
        await refreshState();
      });
      actions.appendChild(approveBtn);
    }

    const declineBtn = document.createElement("button");
    declineBtn.type = "button";
    declineBtn.className = "btn btn-danger btn-small";
    declineBtn.textContent = "Decline";
    declineBtn.addEventListener("click", async () => {
      await api.declineRequest(r.id, "Declined by parent");
      showToast("Request declined");
      await refreshState();
    });
    actions.appendChild(declineBtn);

    els.approvalsList.appendChild(card);
  }
}

// ============================================================================
// Parent Settings & State Refresh
// ============================================================================

async function loadParentSettings() {
  const s = await api.getSettings();
  document.getElementById("set-policy-mode").value = s.policy_mode;
  document.getElementById("set-max-duration").value = String(s.max_track_duration_sec);
  document.getElementById("set-auto-clean").checked = s.auto_substitute_clean_edit;
  document.getElementById("set-kid-profiles").value = (s.kid_profiles || []).join(", ");
  document.getElementById("set-blocked-words").value = (s.blocked_words || []).join(", ");
  document.getElementById("set-blocked-artists").value = (s.blocked_artists || []).join(", ");

  document.getElementById("set-plex-url").value = s.plex_url || "";
  document.getElementById("set-plex-token").value = "";
  document.getElementById("set-plex-token").placeholder = s.plex_token_configured
    ? "Configured (leave blank to keep)"
    : "X-Plex-Token";
  document.getElementById("set-plex-section").value = s.plex_music_section_id || "";
  document.getElementById("set-plex-prefix").value = s.plex_library_path_prefix || "";

  document.getElementById("set-keep-enabled").checked = s.google_keep_enabled;
  document.getElementById("set-keep-email").value = s.google_keep_email || "";
  document.getElementById("set-keep-token").value = "";
  document.getElementById("set-keep-token").placeholder = s.google_keep_token_configured
    ? "Configured (leave blank to keep)"
    : "aas_et/...";
  document.getElementById("set-keep-list").value = s.google_keep_list_name || "Music Requests";
  document.getElementById("set-keep-interval").value = String(s.google_keep_poll_seconds || 45);

  document.getElementById("set-cast-enabled").checked = s.google_cast_enabled;
  document.getElementById("set-cast-devices").value = (s.google_cast_devices || []).join(", ");
  document.getElementById("set-public-url").value = s.public_base_url || "";
}

function setParentViewVisible(open) {
  state.parentViewOpen = open;
  els.kidView.classList.toggle("hidden", open);
  els.parentView.classList.toggle("hidden", !open);
  closeAllPopovers();
  if (open) {
    renderApprovals();
    renderParentDownloads();
    renderParentQueues();
    renderParentSearchResults();
    loadParentSettings().catch((err) => showToast(err.message));
  }
}

async function refreshState() {
  const [bootstrap, requests, covers] = await Promise.all([
    api.bootstrap(),
    api.listRequests(),
    api.listLibraryCovers().catch(() => state.libraryCovers),
  ]);
  state.bootstrap = bootstrap;
  state.requests = requests;
  state.libraryCovers = covers || [];

  if (bootstrap.parent_authenticated) {
    try {
      state.parentDownloads = await api.listParentDownloads();
    } catch {
      state.parentDownloads = [];
    }
  } else {
    state.parentDownloads = [];
  }

  renderProfiles();
  renderHeaderStatus();
  renderRequests();
  renderFloatingCovers();
  if (state.parentViewOpen) {
    renderApprovals();
    renderParentDownloads();
    renderParentQueues();
  }
}

// ============================================================================
// Cleanup Popover Helpers (Parent Mode Only)
// ============================================================================

function closeAllPopovers() {
  els.parentCleanupMenu.classList.add("hidden");
  els.parentCleanupBtn.setAttribute("aria-expanded", "false");
  els.queueMgrCleanupMenu.classList.add("hidden");
  els.queueMgrCleanupBtn.setAttribute("aria-expanded", "false");
}

document.addEventListener("click", (event) => {
  if (!event.target.closest(".cleanup-menu-wrap")) {
    closeAllPopovers();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeAllPopovers();
    if (!els.pinModal.classList.contains("hidden")) {
      els.pinModal.classList.add("hidden");
    }
    if (state.albumModalOpen) {
      closeAlbumModal();
    }
  }
});

els.parentCleanupBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  const willOpen = els.parentCleanupMenu.classList.contains("hidden");
  closeAllPopovers();
  if (willOpen) {
    els.parentCleanupMenu.classList.remove("hidden");
    els.parentCleanupBtn.setAttribute("aria-expanded", "true");
  }
});

els.parentCleanupMenu.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-cleanup]");
  if (!btn) return;
  const scope = btn.dataset.cleanup;
  closeAllPopovers();
  try {
    const res = await api.cleanupParentDownloads(scope);
    showToast(
      res.deleted_count > 0
        ? `Cleared ${res.deleted_count} parent download${res.deleted_count === 1 ? "" : "s"}`
        : "No finished parent downloads to clear"
    );
    await refreshState();
  } catch (err) {
    showToast(err.message);
  }
});

els.queueMgrCleanupBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  const willOpen = els.queueMgrCleanupMenu.classList.contains("hidden");
  closeAllPopovers();
  if (willOpen) {
    els.queueMgrCleanupMenu.classList.remove("hidden");
    els.queueMgrCleanupBtn.setAttribute("aria-expanded", "true");
  }
});

els.queueMgrCleanupMenu.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-cleanup]");
  if (!btn) return;
  const scope = btn.dataset.cleanup;
  const queueTarget = state.parentQueueTarget;
  closeAllPopovers();
  try {
    const res =
      queueTarget === "parent"
        ? await api.cleanupParentDownloads(scope)
        : await api.cleanupRequests(scope, "kid");
    const label = queueTarget === "parent" ? "parent download" : "kid request";
    showToast(
      res.deleted_count > 0
        ? `Cleared ${res.deleted_count} ${label}${res.deleted_count === 1 ? "" : "s"} (music stays in Plex)`
        : "Nothing to clear in this queue"
    );
    await refreshState();
  } catch (err) {
    showToast(err.message);
  }
});

for (const pill of els.parentQueueSwitchBtns) {
  pill.addEventListener("click", () => {
    const target = pill.dataset.queue;
    if (target === state.parentQueueTarget) return;
    state.parentQueueTarget = target;
    for (const p of els.parentQueueSwitchBtns) {
      const active = p === pill;
      p.classList.toggle("active", active);
      p.setAttribute("aria-selected", String(active));
    }
    renderParentQueues();
  });
}

for (const tab of els.queueMgrFilterTabs) {
  tab.addEventListener("click", () => {
    state.parentQueueFilter = tab.dataset.filter;
    for (const t of els.queueMgrFilterTabs) t.classList.toggle("active", t === tab);
    renderParentQueues();
  });
}

// ============================================================================
// Kid Search Events
// ============================================================================

async function runKidSearch(query) {
  const q = query.trim();
  if (!q) return;
  stopPreview();
  state.lastQuery = q;
  els.searchSubmitBtn.disabled = true;
  els.searchSubmitBtn.textContent = "Searching...";
  try {
    if (state.kidSearchMode === "albums") {
      state.searchAlbumResults = await api.searchAlbums(q);
    } else {
      state.searchTrackResults = await api.search(q);
    }
    renderKidSearchResults();
  } catch (err) {
    showToast(err.message);
  } finally {
    els.searchSubmitBtn.disabled = false;
    els.searchSubmitBtn.textContent = "Search";
  }
}

for (const pill of els.kidSearchModeBtns) {
  pill.addEventListener("click", () => {
    const mode = pill.dataset.mode;
    if (mode === state.kidSearchMode) return;
    state.kidSearchMode = mode;
    for (const p of els.kidSearchModeBtns) {
      const active = p === pill;
      p.classList.toggle("active", active);
      p.setAttribute("aria-selected", String(active));
    }
    els.searchInput.placeholder =
      mode === "albums"
        ? "Search an album, e.g. “Encanto” or “Teenage Dream”..."
        : "Try “Shake It Off” or “Encanto”...";
    if (els.searchInput.value.trim()) {
      runKidSearch(els.searchInput.value);
    } else {
      renderKidSearchResults();
    }
  });
}

els.searchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  await runKidSearch(els.searchInput.value);
});

els.searchInput.addEventListener("input", () => {
  els.clearSearchBtn.classList.toggle("hidden", !els.searchInput.value);
});

els.clearSearchBtn.addEventListener("click", () => {
  stopPreview();
  els.searchInput.value = "";
  els.clearSearchBtn.classList.add("hidden");
  state.lastQuery = "";
  state.searchTrackResults = [];
  state.searchAlbumResults = [];
  renderKidSearchResults();
});

for (const tab of els.filterTabs) {
  tab.addEventListener("click", () => {
    state.requestFilter = tab.dataset.filter;
    for (const t of els.filterTabs) t.classList.toggle("active", t === tab);
    renderRequests();
  });
}

// ============================================================================
// Parent Mode Events & Parent Search
// ============================================================================

async function runParentSearch(query) {
  const q = query.trim();
  if (!q || state.parentSearchMode === "direct") return;
  stopPreview();
  state.parentLastQuery = q;
  els.parentSearchSubmitBtn.disabled = true;
  els.parentSearchSubmitBtn.textContent = "Searching...";
  try {
    if (state.parentSearchMode === "albums") {
      state.parentAlbumResults = await api.searchAlbums(q);
    } else {
      state.parentTrackResults = await api.search(q);
    }
    renderParentSearchResults();
  } catch (err) {
    showToast(err.message);
  } finally {
    els.parentSearchSubmitBtn.disabled = false;
    els.parentSearchSubmitBtn.textContent = "Search";
  }
}

for (const pill of els.parentSearchModeBtns) {
  pill.addEventListener("click", () => {
    const mode = pill.dataset.mode;
    state.parentSearchMode = mode;
    for (const p of els.parentSearchModeBtns) {
      const active = p === pill;
      p.classList.toggle("active", active);
      p.setAttribute("aria-selected", String(active));
    }
    const isDirect = mode === "direct";
    els.parentSearchPanel.classList.toggle("hidden", isDirect);
    els.parentDirectPanel.classList.toggle("hidden", !isDirect);

    if (!isDirect) {
      els.parentSearchInput.placeholder =
        mode === "albums"
          ? "Search any album to download all tracks..."
          : "Search any song or artist to download...";
      if (els.parentSearchInput.value.trim()) {
        runParentSearch(els.parentSearchInput.value);
      } else {
        renderParentSearchResults();
      }
    }
  });
}

els.parentSearchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  await runParentSearch(els.parentSearchInput.value);
});

els.parentSearchInput.addEventListener("input", () => {
  els.parentClearSearchBtn.classList.toggle("hidden", !els.parentSearchInput.value);
});

els.parentClearSearchBtn.addEventListener("click", () => {
  stopPreview();
  els.parentSearchInput.value = "";
  els.parentClearSearchBtn.classList.add("hidden");
  state.parentLastQuery = "";
  state.parentTrackResults = [];
  state.parentAlbumResults = [];
  renderParentSearchResults();
});

for (const tab of els.parentDlFilterTabs) {
  tab.addEventListener("click", () => {
    state.parentDownloadFilter = tab.dataset.filter;
    for (const t of els.parentDlFilterTabs) t.classList.toggle("active", t === tab);
    renderParentDownloads();
  });
}

els.parentModeBtn.addEventListener("click", () => {
  if (state.bootstrap?.parent_authenticated) {
    setParentViewVisible(!state.parentViewOpen);
  } else {
    els.pinLoginInput.value = "";
    els.pinLoginError.classList.add("hidden");
    els.pinModal.classList.remove("hidden");
    els.pinLoginInput.focus();
  }
});

els.closePinModal.addEventListener("click", () => els.pinModal.classList.add("hidden"));
els.closeAlbumModal.addEventListener("click", closeAlbumModal);
els.albumModal.addEventListener("click", (e) => {
  if (e.target === els.albumModal) closeAlbumModal();
});
els.pinModal.addEventListener("click", (e) => {
  if (e.target === els.pinModal) els.pinModal.classList.add("hidden");
});
els.backToKidsBtn.addEventListener("click", () => setParentViewVisible(false));

els.pinLoginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  els.pinLoginError.classList.add("hidden");
  try {
    await api.parentLogin(els.pinLoginInput.value.trim());
    els.pinModal.classList.add("hidden");
    await refreshState();
    setParentViewVisible(true);
  } catch (err) {
    els.pinLoginError.textContent = err.message;
    els.pinLoginError.classList.remove("hidden");
  }
});

els.parentLogoutBtn.addEventListener("click", async () => {
  await api.parentLogout();
  setParentViewVisible(false);
  await refreshState();
  showToast("Parent Mode locked");
});

for (const tab of els.parentTabs) {
  tab.addEventListener("click", () => {
    state.activeParentTab = tab.dataset.tab;
    for (const t of els.parentTabs) t.classList.toggle("active", t === tab);
    document.getElementById("tab-approvals").classList.toggle("hidden", state.activeParentTab !== "approvals");
    document.getElementById("tab-manual").classList.toggle("hidden", state.activeParentTab !== "manual");
    document.getElementById("tab-queues").classList.toggle("hidden", state.activeParentTab !== "queues");
    document.getElementById("tab-settings").classList.toggle("hidden", state.activeParentTab !== "settings");
    if (state.activeParentTab === "queues") {
      renderParentQueues();
    }
  });
}

els.parentDirectForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = document.getElementById("direct-query").value.trim();
  const sourceUrl = document.getElementById("direct-url").value.trim();
  const requester = document.getElementById("direct-requester").value.trim() || "Parent";
  const parentOverride = document.getElementById("direct-override-explicit").checked;
  try {
    await api.submitRequest({
      query,
      requester_name: requester,
      source: "parent",
      source_url: sourceUrl || null,
      parent_override: parentOverride,
    });
    document.getElementById("direct-query").value = "";
    document.getElementById("direct-url").value = "";
    showToast("Parent download queued!");
    await refreshState();
  } catch (err) {
    showToast(err.message);
  }
});

// ============================================================================
// Settings & Integrations Events
// ============================================================================

function splitCsv(val) {
  return String(val || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

els.settingsForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const statusEl = document.getElementById("settings-save-status");
  statusEl.textContent = "Saving...";
  const payload = {
    policy_mode: document.getElementById("set-policy-mode").value,
    max_track_duration_sec: Number(document.getElementById("set-max-duration").value || 600),
    auto_substitute_clean_edit: document.getElementById("set-auto-clean").checked,
    kid_profiles: splitCsv(document.getElementById("set-kid-profiles").value),
    blocked_words: splitCsv(document.getElementById("set-blocked-words").value),
    blocked_artists: splitCsv(document.getElementById("set-blocked-artists").value),
    plex_url: document.getElementById("set-plex-url").value.trim(),
    plex_music_section_id: document.getElementById("set-plex-section").value.trim(),
    plex_library_path_prefix: document.getElementById("set-plex-prefix").value.trim(),
    google_keep_enabled: document.getElementById("set-keep-enabled").checked,
    google_keep_email: document.getElementById("set-keep-email").value.trim(),
    google_keep_list_name: document.getElementById("set-keep-list").value.trim() || "Music Requests",
    google_keep_poll_seconds: Number(document.getElementById("set-keep-interval").value || 45),
    google_cast_enabled: document.getElementById("set-cast-enabled").checked,
    google_cast_devices: splitCsv(document.getElementById("set-cast-devices").value),
    public_base_url: document.getElementById("set-public-url").value.trim(),
  };

  const plexToken = document.getElementById("set-plex-token").value.trim();
  if (plexToken) payload.plex_token = plexToken;
  const keepToken = document.getElementById("set-keep-token").value.trim();
  if (keepToken) payload.google_keep_master_token = keepToken;

  try {
    await api.saveSettings(payload);
    statusEl.textContent = "Saved!";
    await refreshState();
    await loadParentSettings();
  } catch (err) {
    statusEl.textContent = err.message;
  }
});

els.pinForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const statusEl = document.getElementById("pin-status");
  try {
    await api.changePin(
      document.getElementById("pin-current").value.trim(),
      document.getElementById("pin-new").value.trim()
    );
    document.getElementById("pin-current").value = "";
    document.getElementById("pin-new").value = "";
    statusEl.textContent = "Parent PIN updated!";
    await refreshState();
  } catch (err) {
    statusEl.textContent = err.message;
  }
});

document.getElementById("test-plex-btn").addEventListener("click", async () => {
  const statusEl = document.getElementById("plex-test-status");
  statusEl.textContent = "Testing...";
  try {
    const res = await api.testPlex();
    if (res.sections?.length) {
      const first = res.sections[0];
      if (!document.getElementById("set-plex-section").value.trim()) {
        document.getElementById("set-plex-section").value = first.id;
      }
      statusEl.textContent = `Connected! Found: ${res.sections.map((s) => `${s.title} (#${s.id})`).join(", ")}`;
    } else {
      statusEl.textContent = "Connected, but no Music library section found.";
    }
  } catch (err) {
    statusEl.textContent = err.message;
  }
});

document.getElementById("scan-plex-btn").addEventListener("click", async () => {
  const statusEl = document.getElementById("plex-test-status");
  try {
    const res = await api.scanPlex();
    statusEl.textContent = res.ok ? "Plex library scan triggered!" : "Configure Plex URL & Token first.";
  } catch (err) {
    statusEl.textContent = err.message;
  }
});

document.getElementById("sync-keep-btn").addEventListener("click", async () => {
  const statusEl = document.getElementById("keep-sync-status");
  statusEl.textContent = "Syncing...";
  try {
    const res = await api.syncKeepNow();
    statusEl.textContent = `Imported ${res.imported} new request(s).`;
    await refreshState();
  } catch (err) {
    statusEl.textContent = err.message;
  }
});

document.getElementById("discover-cast-btn").addEventListener("click", async () => {
  const statusEl = document.getElementById("cast-status");
  statusEl.textContent = "Scanning Wi-Fi for Google Home speakers...";
  try {
    const res = await api.discoverCast();
    if (res.devices?.length) {
      const names = res.devices.map((d) => d.name).join(", ");
      statusEl.textContent = `Found: ${names}`;
      if (!document.getElementById("set-cast-devices").value.trim()) {
        document.getElementById("set-cast-devices").value = names;
      }
    } else {
      statusEl.textContent = "No speakers found (check host networking / same subnet).";
    }
  } catch (err) {
    statusEl.textContent = err.message;
  }
});

document.getElementById("test-cast-btn").addEventListener("click", async () => {
  const statusEl = document.getElementById("cast-status");
  statusEl.textContent = "Casting announcement...";
  try {
    const devices = splitCsv(document.getElementById("set-cast-devices").value);
    const res = await api.announceCast("KidTune is connected and ready for music requests!", devices);
    statusEl.textContent = res.announced_to?.length
      ? `Spoke on: ${res.announced_to.join(", ")}`
      : "No target speakers responded.";
  } catch (err) {
    statusEl.textContent = err.message;
  }
});

// Register Service Worker & start live polling
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

refreshState().catch((err) => showToast(err.message));
setInterval(() => {
  refreshState().catch(() => {});
}, 4000);
