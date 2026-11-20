import { api } from "./api.js";

const state = {
  bootstrap: null,
  activeProfile: localStorage.getItem("kidtune_profile") || "Alex",
  requestFilter: "all",
  searchResults: [],
  lastQuery: "",
  requests: [],
  parentViewOpen: false,
  activeParentTab: "approvals",
  previewAudio: null,
  previewTrackId: null,
};

const els = {
  profileSwitcher: document.getElementById("profile-switcher"),
  parentModeBtn: document.getElementById("parent-mode-btn"),
  parentModeLabel: document.getElementById("parent-mode-label"),
  pendingBadge: document.getElementById("pending-badge"),

  kidView: document.getElementById("kid-view"),
  parentView: document.getElementById("parent-view"),
  backToKidsBtn: document.getElementById("back-to-kids-btn"),
  parentLogoutBtn: document.getElementById("parent-logout-btn"),
  defaultPinWarning: document.getElementById("default-pin-warning"),

  searchForm: document.getElementById("search-form"),
  searchInput: document.getElementById("search-input"),
  clearSearchBtn: document.getElementById("clear-search-btn"),
  searchSubmitBtn: document.getElementById("search-submit-btn"),
  voiceHint: document.getElementById("voice-hint"),

  resultsSection: document.getElementById("results-section"),
  resultsMeta: document.getElementById("results-meta"),
  resultsGrid: document.getElementById("results-grid"),
  customRequestBanner: document.getElementById("custom-request-banner"),

  requestsList: document.getElementById("requests-list"),
  filterTabs: document.querySelectorAll(".filter-tab"),

  parentTabs: document.querySelectorAll(".parent-tab"),
  tabPendingCount: document.getElementById("tab-pending-count"),
  approvalsList: document.getElementById("approvals-list"),
  parentDirectForm: document.getElementById("parent-direct-form"),
  settingsForm: document.getElementById("settings-form"),
  pinForm: document.getElementById("pin-form"),

  pinModal: document.getElementById("pin-modal"),
  closePinModal: document.getElementById("close-pin-modal"),
  pinLoginForm: document.getElementById("pin-login-form"),
  pinLoginInput: document.getElementById("pin-login-input"),
  pinLoginError: document.getElementById("pin-login-error"),

  toastRegion: document.getElementById("toast-region"),
};

function showToast(message) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  els.toastRegion.appendChild(node);
  setTimeout(() => node.remove(), 3600);
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

function togglePreview(track) {
  if (!track.preview_url || track.is_explicit) return;
  if (state.previewTrackId === track.track_id && state.previewAudio) {
    stopPreview();
    renderSearchResults();
    return;
  }
  stopPreview();
  const audio = new Audio(track.preview_url);
  state.previewAudio = audio;
  state.previewTrackId = track.track_id;
  audio.addEventListener("ended", () => {
    stopPreview();
    renderSearchResults();
  });
  audio.play().catch(() => {
    stopPreview();
    showToast("Preview unavailable on this device");
  });
  renderSearchResults();
}

function renderProfiles() {
  const profiles = state.bootstrap?.kid_profiles || ["Alex", "Sam", "Family"];
  if (!profiles.includes(state.activeProfile)) {
    state.activeProfile = profiles[0];
    localStorage.setItem("kidtune_profile", state.activeProfile);
  }
  els.profileSwitcher.innerHTML = "";
  for (const name of profiles) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `profile-chip ${name === state.activeProfile ? "active" : ""}`;
    btn.textContent = name;
    btn.addEventListener("click", () => {
      state.activeProfile = name;
      localStorage.setItem("kidtune_profile", name);
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
  els.parentModeLabel.textContent = b.parent_authenticated ? "Parent Controls" : "Parent Mode";
  els.defaultPinWarning.classList.toggle("hidden", !(b.parent_authenticated && b.parent_pin_is_default));

  if (b.google_keep_enabled && b.google_keep_list_name) {
    els.voiceHint.textContent = `Voice Tip: Say “Hey Google, add [Song by Artist] to my ${b.google_keep_list_name} list”`;
    els.voiceHint.classList.remove("hidden");
  } else {
    els.voiceHint.classList.add("hidden");
  }
}

function renderSearchResults() {
  if (!state.lastQuery) {
    els.resultsSection.classList.add("hidden");
    return;
  }
  els.resultsSection.classList.remove("hidden");
  els.resultsMeta.textContent = `${state.searchResults.length} result${state.searchResults.length === 1 ? "" : "s"} for “${state.lastQuery}”`;
  els.resultsGrid.innerHTML = "";

  for (const item of state.searchResults) {
    const { track, safety } = item;
    const card = document.createElement("article");
    card.className = "track-card";

    const artSrc = track.artwork_url || "/static/icons/icon-192.png";
    let badgeHtml = `<span class="tag tag-pending">Needs Parent Check</span>`;
    if (safety.blocked) {
      badgeHtml = `<span class="tag tag-explicit">Explicit Blocked</span>`;
    } else if (track.explicit_verdict === "cleaned_edit") {
      badgeHtml = `<span class="tag tag-clean">Clean Radio Edit</span>`;
    } else if (safety.allowed_auto) {
      badgeHtml = `<span class="tag tag-clean">Kid-Safe</span>`;
    }

    card.innerHTML = `
      <div class="track-main">
        <img class="track-art" src="${escapeHtml(artSrc)}" alt="${escapeHtml(track.album || track.title)} album art" loading="lazy" />
        <div class="track-info">
          <h3 class="track-title">${escapeHtml(track.title)}</h3>
          <p class="track-artist">${escapeHtml(track.artist)}</p>
          <p class="track-album">${escapeHtml(track.album)}${track.year ? ` • ${escapeHtml(track.year)}` : ""}</p>
          <div class="track-meta-row">
            ${badgeHtml}
            <span class="tag tag-neutral">${formatDuration(track.duration_sec)}</span>
          </div>
        </div>
      </div>
      <div class="track-actions"></div>
    `;

    const actions = card.querySelector(".track-actions");

    if (track.preview_url && !track.is_explicit && !safety.blocked) {
      const previewBtn = document.createElement("button");
      previewBtn.type = "button";
      previewBtn.className = "btn btn-secondary btn-small";
      previewBtn.textContent = state.previewTrackId === track.track_id ? "Stop" : "Preview";
      previewBtn.addEventListener("click", () => togglePreview(track));
      actions.appendChild(previewBtn);
    }

    if (safety.blocked) {
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

    els.resultsGrid.appendChild(card);
  }

  // Always offer custom parent-approval fallback when searching text
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
}

async function submitSelectedTrack(track, { useCleanAlternative = false } = {}) {
  try {
    const rec = await api.submitRequest({
      query: `${track.title} by ${track.artist}`,
      requester_name: state.activeProfile,
      source: "ipad",
      catalog_track: track,
      use_clean_alternative: useCleanAlternative,
    });
    if (rec.status === "completed" || rec.status === "queued") {
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

function renderRequests() {
  let items = state.requests || [];
  if (state.requestFilter === "mine") {
    items = items.filter((r) => r.requester_name === state.activeProfile);
  } else if (state.requestFilter === "completed") {
    items = items.filter((r) => r.status === "completed");
  }

  if (items.length === 0) {
    els.requestsList.innerHTML = `<div class="empty-state">No song requests here yet. Search for a favorite song above!</div>`;
    return;
  }

  els.requestsList.innerHTML = "";
  for (const r of items) {
    const row = document.createElement("div");
    row.className = "request-row";
    const art = r.artwork_url || "/static/icons/icon-192.png";
    const sourceText = r.source === "google_home" ? "Google Home" : r.source === "parent" ? "Parent" : r.requester_name;

    row.innerHTML = `
      <div class="request-left">
        <img class="request-art" src="${escapeHtml(art)}" alt="" loading="lazy" />
        <div class="request-text">
          <div class="request-title">${escapeHtml(r.title || r.query)}</div>
          <p class="request-sub">
            ${escapeHtml(r.artist || "Custom Request")}${r.album ? ` • ${escapeHtml(r.album)}` : ""} • For ${escapeHtml(sourceText)}
          </p>
          ${r.status_detail ? `<p class="request-sub">${escapeHtml(r.status_detail)}</p>` : ""}
        </div>
      </div>
      <div class="request-right">
        <span class="status-pill status-${escapeHtml(r.status)}">${escapeHtml(statusLabel(r.status))}</span>
      </div>
    `;
    els.requestsList.appendChild(row);
  }
}

function renderApprovals() {
  if (!state.bootstrap?.parent_authenticated) return;
  const items = (state.requests || []).filter((r) =>
    ["pending_approval", "blocked", "failed"].includes(r.status)
  );
  if (items.length === 0) {
    els.approvalsList.innerHTML = `<div class="empty-state">All caught up! No songs waiting for parent review.</div>`;
    return;
  }

  els.approvalsList.innerHTML = "";
  for (const r of items) {
    const card = document.createElement("div");
    card.className = "approval-card";
    const reasons = (r.safety_reasons || []).join(" • ") || r.status_detail || "Needs review";

    card.innerHTML = `
      <div class="request-left">
        <img class="request-art" src="${escapeHtml(r.artwork_url || "/static/icons/icon-192.png")}" alt="" />
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
  if (open) {
    renderApprovals();
    loadParentSettings().catch((err) => showToast(err.message));
  }
}

async function refreshState() {
  const [bootstrap, requests] = await Promise.all([api.bootstrap(), api.listRequests()]);
  state.bootstrap = bootstrap;
  state.requests = requests;
  renderProfiles();
  renderHeaderStatus();
  renderRequests();
  if (state.parentViewOpen) {
    renderApprovals();
  }
}

// Event bindings
els.searchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = els.searchInput.value.trim();
  if (!q) return;
  stopPreview();
  state.lastQuery = q;
  els.searchSubmitBtn.disabled = true;
  els.searchSubmitBtn.textContent = "Searching...";
  try {
    state.searchResults = await api.search(q);
    renderSearchResults();
  } catch (err) {
    showToast(err.message);
  } finally {
    els.searchSubmitBtn.disabled = false;
    els.searchSubmitBtn.textContent = "Search";
  }
});

els.searchInput.addEventListener("input", () => {
  els.clearSearchBtn.classList.toggle("hidden", !els.searchInput.value);
});

els.clearSearchBtn.addEventListener("click", () => {
  stopPreview();
  els.searchInput.value = "";
  els.clearSearchBtn.classList.add("hidden");
  state.lastQuery = "";
  state.searchResults = [];
  renderSearchResults();
});

for (const tab of els.filterTabs) {
  tab.addEventListener("click", () => {
    state.requestFilter = tab.dataset.filter;
    for (const t of els.filterTabs) t.classList.toggle("active", t === tab);
    renderRequests();
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
    document.getElementById("tab-settings").classList.toggle("hidden", state.activeParentTab !== "settings");
  });
}

els.parentDirectForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const query = document.getElementById("direct-query").value.trim();
  const sourceUrl = document.getElementById("direct-url").value.trim();
  const requester = document.getElementById("direct-requester").value.trim() || "Parent";
  try {
    await api.submitRequest({
      query,
      requester_name: requester,
      source: "parent",
      source_url: sourceUrl || null,
    });
    document.getElementById("direct-query").value = "";
    document.getElementById("direct-url").value = "";
    showToast("Parent download queued!");
    await refreshState();
  } catch (err) {
    showToast(err.message);
  }
});

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
