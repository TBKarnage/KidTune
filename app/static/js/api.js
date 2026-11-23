async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const isJson = (response.headers.get("content-type") || "").includes("application/json");
  const body = isJson ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof body === "object" && body?.detail ? body.detail : `Request failed (${response.status})`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return body;
}

export const api = {
  bootstrap: () => requestJson("/api/bootstrap"),
  search: (query) => requestJson(`/api/search?q=${encodeURIComponent(query)}`),
  searchAlbums: (query) => requestJson(`/api/search/albums?q=${encodeURIComponent(query)}`),
  getAlbumDetails: (provider, albumId) =>
    requestJson(`/api/albums/${encodeURIComponent(provider)}/${encodeURIComponent(albumId)}`),
  listLibraryCovers: () => requestJson("/api/library/covers?limit=36"),

  listRequests: () => requestJson("/api/requests?limit=100"),
  submitRequest: (payload) =>
    requestJson("/api/requests", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  submitAlbumRequest: (payload) =>
    requestJson("/api/requests/album", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  cleanupRequests: (scope = "finished", queue = "kid") =>
    requestJson(`/api/requests/cleanup?queue=${encodeURIComponent(queue)}`, {
      method: "POST",
      body: JSON.stringify({ scope }),
    }),
  deleteRequest: (id) =>
    requestJson(`/api/requests/${id}`, {
      method: "DELETE",
    }),

  parentLogin: (pin) =>
    requestJson("/api/parent/login", {
      method: "POST",
      body: JSON.stringify({ pin }),
    }),
  parentLogout: () => requestJson("/api/parent/logout", { method: "POST" }),
  changePin: (currentPin, newPin) =>
    requestJson("/api/parent/pin", {
      method: "POST",
      body: JSON.stringify({ current_pin: currentPin, new_pin: newPin }),
    }),
  getSettings: () => requestJson("/api/parent/settings"),
  saveSettings: (payload) =>
    requestJson("/api/parent/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  listParentDownloads: () => requestJson("/api/parent/downloads?limit=100"),
  cleanupParentDownloads: (scope = "finished") =>
    requestJson("/api/parent/downloads/cleanup", {
      method: "POST",
      body: JSON.stringify({ scope }),
    }),

  approveRequest: (id, payload) =>
    requestJson(`/api/parent/requests/${id}/approve`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  declineRequest: (id, reason) =>
    requestJson(`/api/parent/requests/${id}/decline`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  retryRequest: (id) =>
    requestJson(`/api/parent/requests/${id}/retry`, {
      method: "POST",
    }),

  testPlex: () => requestJson("/api/parent/plex/test", { method: "POST" }),
  scanPlex: () => requestJson("/api/parent/plex/scan", { method: "POST" }),
  discoverCast: () => requestJson("/api/parent/cast/discover"),
  announceCast: (message, devices = null) =>
    requestJson("/api/parent/cast/announce", {
      method: "POST",
      body: JSON.stringify({ message, devices }),
    }),
  syncKeepNow: () => requestJson("/api/parent/keep/sync", { method: "POST" }),
};
