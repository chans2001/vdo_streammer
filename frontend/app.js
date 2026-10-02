"use strict";
const $ = id => document.getElementById(id);
const configuredBase = (window.MOUSECAM_CONFIG?.API_BASE_URL || "").replace(/\/$/, "");
const configuredOrigin = configuredBase ? new URL(configuredBase).origin : "";
// Keep local requests relative; only an explicitly different backend uses an absolute URL.
const base = configuredOrigin && configuredOrigin !== location.origin ? configuredOrigin : "";
let token = null, generation = 0, previewAbort = null, previewURL = null, mediaURL = null;
let latestStatus = null, busy = false, lastGallery = 0, lastRecordState = "", noticeTimer;
let previewAt = 0, previewError = "";
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

function notify(text, error = false) {
  clearTimeout(noticeTimer);
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
  $("message").hidden = false;
  noticeTimer = setTimeout(() => { $("message").hidden = true; }, error ? 15000 : 7000);
}

async function api(path, options = {}) {
  const headers = { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(options.headers || {}) };
  if (options.method === "POST") headers["Content-Type"] = "application/json";
  const response = await fetch(base + path, { ...options, headers, credentials: "omit", cache: "no-store", signal: options.signal || AbortSignal.timeout(15000) });
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try { message = (await response.json()).error || message; } catch (_) { /* non-JSON proxy error */ }
    if (response.status === 401 && path !== "/api/login") signedOut();
    throw new Error(message);
  }
  return response;
}

function offline(title, detail) {
  $("offline").hidden = false;
  $("offline-title").textContent = title;
  $("offline-detail").textContent = detail || "Waiting for a fresh camera frame.";
}

function signedOut() {
  token = null;
  generation++;
  previewAbort?.abort();
  if (previewURL) URL.revokeObjectURL(previewURL);
  previewURL = null;
  previewAt = 0;
  previewError = "";
  $("preview").removeAttribute("src");
  $("preview").hidden = true;
  $("workspace").hidden = true;
  $("logout").hidden = true;
  $("login-panel").hidden = false;
  $("gallery").replaceChildren();
  latestStatus = null;
  closeViewer();
}

function buttons() {
  const rec = latestStatus?.recording.state;
  const fresh = latestStatus?.camera.fresh && previewFresh();
  $("photo").disabled = busy || !fresh || rec === "saving";
  $("start").disabled = busy || !fresh || rec === "recording" || rec === "saving";
  $("stop").disabled = busy || rec !== "recording";
}

function previewFresh() {
  return previewAt > 0 && performance.now() - previewAt < 3000 && !previewError;
}

function renderCamera() {
  const camera = latestStatus?.camera;
  const live = camera?.fresh && previewFresh();
  $("status-dot").classList.toggle("live", Boolean(live));
  if (!camera) {
    $("camera-status").textContent = "Server unavailable";
    offline("Cannot reach the lab laptop", "Checking the connection…");
  } else if (!camera.fresh) {
    $("camera-status").textContent = camera.state.charAt(0).toUpperCase() + camera.state.slice(1);
    offline(camera.state === "stale" ? "Camera frames are stale" : "Camera is offline", camera.error);
  } else if (!live) {
    $("camera-status").textContent = previewError ? "Preview unavailable" : "Waiting for preview";
    offline(previewError ? "Preview unavailable" : "Waiting for a fresh preview", previewError || "The camera is connected; waiting for an image to load on this device.");
  } else {
    $("camera-status").textContent = "Live camera";
    $("offline").hidden = true;
  }
  buttons();
}

function renderStatus(status) {
  latestStatus = status;
  const { camera, recording: rec } = status;
  $("simulation").hidden = !camera.simulated;
  $("source-label").textContent = camera.simulated ? "Simulated source" : "Arducam · Pi Zero W";
  renderCamera();
  $("record-indicator").classList.toggle("recording", rec.state === "recording");
  const seconds = Math.floor(rec.wall_seconds || 0);
  $("timer").textContent = [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map(n => String(n).padStart(2, "0")).join(":");
  const labels = { idle: "Ready when you are.", recording: "Recording · shared with everyone", saving: "Saving MP4… Please wait.", completed: "Recording saved to the library.", interrupted: "Interrupted recording saved. See details below.", failed: "Recording failed. See details below." };
  $("record-status").textContent = rec.error || labels[rec.state] || rec.state;
  buttons();
}

async function poll(myGeneration) {
  while (token && myGeneration === generation) {
    try {
      const status = await (await api("/api/status")).json();
      if (myGeneration !== generation) return;
      renderStatus(status);
      if (Date.now() - lastGallery > 10000 || status.recording.state !== lastRecordState) await gallery();
      lastRecordState = status.recording.state;
    } catch (error) {
      if (myGeneration !== generation) return;
      latestStatus = null;
      renderCamera();
      offline("Cannot reach the lab laptop", error.message);
    }
    await pause(1000);
  }
}

async function showPreview(blob, myGeneration, signal) {
  if (myGeneration !== generation) return;
  const url = URL.createObjectURL(blob), old = previewURL;
  try {
    // A successful HTTP response is insufficient: wait for the actual <img> to decode.
    await new Promise((resolve, reject) => {
      const img = $("preview");
      const finish = error => {
        img.removeEventListener("load", loaded);
        img.removeEventListener("error", failed);
        signal.removeEventListener("abort", aborted);
        error ? reject(error) : resolve();
      };
      const loaded = () => finish(img.naturalWidth ? null : new Error("Preview image is empty."));
      const failed = () => finish(new Error("Could not decode the preview image."));
      const aborted = () => finish(new Error("Preview request timed out or was cancelled."));
      if (signal.aborted) { aborted(); return; }
      img.addEventListener("load", loaded, { once: true });
      img.addEventListener("error", failed, { once: true });
      signal.addEventListener("abort", aborted, { once: true });
      img.src = url;
    });
    if (myGeneration !== generation) { URL.revokeObjectURL(url); return; }
    previewURL = url;
    previewAt = performance.now();
    previewError = "";
    $("preview").hidden = false;
    if (old) URL.revokeObjectURL(old);
    renderCamera();
  } catch (error) {
    URL.revokeObjectURL(url);
    throw error;
  }
}

async function preview(myGeneration) {
  while (token && myGeneration === generation) {
    const controller = new AbortController();
    previewAbort = controller;
    // Timer covers headers, body, and image decoding; no AbortSignal.any dependency.
    const timeout = setTimeout(() => controller.abort(), 5000);
    const started = performance.now();
    let retry = 200;
    try {
      // WebKit cancels multipart/x-mixed-replace fetched as a raw resource.
      // Finite authenticated JPEGs work locally and across configured origins.
      const response = await api("/api/preview.jpg", { signal: controller.signal });
      if (response.headers.get("Content-Type")?.split(";")[0].trim().toLowerCase() !== "image/jpeg") throw new Error("Server did not return a JPEG preview.");
      await showPreview(await response.blob(), myGeneration, controller.signal);
    } catch (error) {
      if (myGeneration !== generation) return;
      previewError = controller.signal.aborted ? "Preview request timed out. Retrying…" : error.message;
      previewAt = 0;
      renderCamera();
      retry = 1000;
    } finally {
      clearTimeout(timeout);
      if (previewAbort === controller) previewAbort = null;
    }
    // One request in flight; never build up frames when a device or network is slow.
    await pause(Math.max(0, retry - (performance.now() - started)));
  }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function gallery() {
  const myGeneration = generation;
  const data = await (await api("/api/captures")).json();
  if (myGeneration !== generation) return;
  lastGallery = Date.now();
  $("capture-count").textContent = data.captures.length;
  $("empty").hidden = data.captures.length > 0;
  const cards = data.captures.map(item => {
    const card = element("article", "capture");
    const heading = element("div", "capture-top");
    heading.append(element("span", "capture-type", item.type === "photo" ? "▧  PHOTO" : "▷  VIDEO"), element("span", "capture-state", item.state.toUpperCase()));
    card.append(heading, element("h3", "", new Date(item.started_at).toLocaleString()), element("p", "", `${((item.size_bytes || 0) / 1048576).toFixed(2)} MB · 1280 × 720${item.simulated ? " · Simulated" : ""}`));
    if (item.type === "video") card.append(element("p", "", `Wall time: ${item.wall_seconds?.toFixed(1) ?? "unknown"}s · Encoded: ${item.encoded_seconds?.toFixed(1) ?? "unknown"}s`));
    if (item.error) card.append(element("p", "", item.error));
    card.append(element("p", "filename", item.name));
    const actions = element("div", "capture-actions");
    for (const [label, download] of [["View", false], ["Download ↓", true]]) {
      const button = element("button", "quiet", label);
      button.disabled = !item.available;
      button.addEventListener("click", async () => {
        button.disabled = true;
        try { await openMedia(item, download); } catch (error) { notify(error.message, true); }
        finally { button.disabled = !item.available; }
      });
      actions.append(button);
    }
    card.append(actions);
    return card;
  });
  $("gallery").replaceChildren(...cards);
}

function closeViewer() {
  $("viewer").close();
  $("viewer-content").replaceChildren();
  if (mediaURL) URL.revokeObjectURL(mediaURL);
  mediaURL = null;
}

async function openMedia(item, download) {
  const myGeneration = generation;
  // Streaming to a user-selected file avoids buffering large downloads in Chromium.
  let target = null;
  if (download && window.showSaveFilePicker) {
    try { target = await window.showSaveFilePicker({ suggestedName: item.name }); }
    catch (error) { if (error.name === "AbortError") return; throw error; }
  }
  notify(download ? "Preparing download…" : "Loading capture…");
  const response = await api(`/api/media/${encodeURIComponent(item.name)}${download ? "?download=1" : ""}`, { signal: AbortSignal.timeout(300000) });
  if (target) {
    await response.body.pipeTo(await target.createWritable());
    notify("Download saved.");
    return;
  }
  const blob = await response.blob();
  if (myGeneration !== generation) return;
  const url = URL.createObjectURL(blob);
  if (download) {
    const link = element("a"); link.href = url; link.download = item.name;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    notify("Download ready.");
  } else {
    closeViewer(); mediaURL = url;
    const media = element(item.type === "video" ? "video" : "img");
    if (item.type === "video") { media.controls = true; media.playsInline = true; }
    else media.alt = "Saved camera photo";
    media.src = url;
    $("viewer-title").textContent = item.type === "video" ? "Saved recording" : "Saved photo";
    $("viewer-note").textContent = item.name;
    $("viewer-content").append(media);
    $("viewer").showModal();
    $("message").hidden = true;
  }
}

$("login-form").addEventListener("submit", async event => {
  event.preventDefault(); $("login-button").disabled = true;
  try {
    if (location.protocol === "https:" && base.startsWith("http:")) throw new Error("An HTTPS frontend requires an HTTPS backend URL.");
    const data = await (await api("/api/login", { method: "POST", body: JSON.stringify({ code: $("access-code").value }) })).json();
    token = data.token; $("access-code").value = "";
    $("login-panel").hidden = true; $("workspace").hidden = false; $("logout").hidden = false;
    $("message").hidden = true; lastGallery = 0; lastRecordState = "";
    const mine = ++generation; poll(mine); preview(mine);
  } catch (error) { notify(error.message, true); }
  finally { $("login-button").disabled = false; }
});
$("logout").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST", body: "{}" }); }
  catch (_) { /* Clear memory even when the backend is unreachable. */ }
  signedOut();
});
for (const [id, path] of [["photo", "/api/photos"], ["start", "/api/recording/start"], ["stop", "/api/recording/stop"]]) {
  $(id).addEventListener("click", async () => {
    busy = true; buttons();
    try {
      const data = await (await api(path, { method: "POST", body: "{}" })).json();
      if (id === "photo") { notify(`Photo saved: ${data.name}`); await gallery(); }
      else if (latestStatus) renderStatus({ ...latestStatus, recording: data });
    } catch (error) { notify(error.message, true); }
    finally { busy = false; buttons(); }
  });
}
$("refresh").addEventListener("click", () => gallery().catch(error => notify(error.message, true)));
$("close-viewer").addEventListener("click", closeViewer);
$("viewer").addEventListener("cancel", event => { event.preventDefault(); closeViewer(); });
