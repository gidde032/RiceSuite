// RiceClipper review UI — vanilla JS, talks to the local FastAPI server.
//
// Bounded batch (SPEC §9): several clips are queued in one session, each gets a
// review card, and the human edits + approves every clip. Transcription and
// render run strictly one clip at a time — the server already serializes on a
// single Whisper model and CPU-bound ffmpeg, so this UI never fires overlapping
// work. State lives in the browser (a client-driven batch); the server keeps
// its existing per-job routes.
//
// Video preview follows RicePoster's proven pattern: play from a client-side
// blob (URL.createObjectURL) rather than a server stream, which sidesteps HTTP
// range / content-disposition / streaming quirks that break a server-pointed
// <video src>.

const $ = (id) => document.getElementById(id);

const clips = []; // clip objects (see makeClip)
let clipSeq = 0; // monotonically assigned ids for stable card identifiers
let ingesting = false; // upload+transcribe queue is draining
let batchBusy = false; // render-all in progress
let clearInProgress = false;
let batchSent = false; // the loaded batch has reached RicePoster
let sentBatchId = ""; // the handoff batch id it was sent as
let sentSnapshot = null; // batchSnapshot() at the moment it was sent
let sendInFlight = false;
let sendKey = ""; // reused when a send whose reply never came is retried
let sendKeySnapshot = null; // batchSnapshot() when sendKey was made

// DOM ids and radio-group names stay unique for this page lifetime, while the
// ordinal follows the clip's current position in the batch/handoff.
function allocateClipIdentity() {
  clipSeq += 1;
  return { localId: clipSeq, ord: clips.length + 1 };
}

// This tab's own pull state, kept in sessionStorage so it survives a reload of
// this tab and no other (W1-02, review S-1): the Searcher batch the workspace
// holds, and the key of a pull whose reply never came.
const TAB_STATE_KEY = "riceclipper.tab.v1";
const tabMemory = { pulledBatchId: "", pendingPullKey: "" };

function readTab() {
  try {
    const raw = window.sessionStorage.getItem(TAB_STATE_KEY);
    if (raw) return { ...tabMemory, ...JSON.parse(raw) };
  } catch {
    // no sessionStorage: this page load only
  }
  return { ...tabMemory };
}

function writeTab(patch) {
  Object.assign(tabMemory, readTab(), patch);
  try {
    window.sessionStorage.setItem(TAB_STATE_KEY, JSON.stringify(tabMemory));
  } catch {
    // no sessionStorage: this page load only
  }
}

const MEDIA_CACHE_INFO_ENDPOINT = "api/media-info";
const ACTIVE_JOB_STATUSES = new Set(["transcribing", "rendering"]);

// --- per-slot saved visual defaults -----------------------------------------
// Replaces the old universal pre-upload dropdown: each slot (the "Clip N"
// ordinal, which maps to RicePoster's handoff position) remembers its caption
// and header style in the browser (local-first, no server state). A clip in
// slot N is seeded from slot N's saved default; classic/plain when never set.
const SLOT_STYLE_KEY = "riceclipper.slotStyles.v1";

function loadSlotStyles() {
  try {
    return JSON.parse(localStorage.getItem(SLOT_STYLE_KEY)) || {};
  } catch {
    return {};
  }
}

function slotDefault(ord, kind, fallback) {
  const slot = loadSlotStyles()[String(ord)];
  return (slot && slot[kind]) || fallback;
}

// A saved on/off choice; ``slotDefault`` would read a saved false as unset.
function slotFlag(ord, kind, fallback) {
  const slot = loadSlotStyles()[String(ord)];
  return slot && typeof slot[kind] === "boolean" ? slot[kind] : fallback;
}

function rememberSlotStyle(ord, kind, value) {
  const all = loadSlotStyles();
  const key = String(ord);
  all[key] = { ...(all[key] || {}), [kind]: value };
  try {
    localStorage.setItem(SLOT_STYLE_KEY, JSON.stringify(all));
  } catch {
    // Storage may be unavailable/full; in-session seeding still works.
  }
}

// --- header look (RiceSuite #65) ---------------------------------------------
// Every header control, keyed like the server's HeaderLook. The block between
// the markers must equal render.ass.header_preset (a Clipper test compares
// them). A style card fills in the look keys below; position, size, font,
// alignment and spacing stay where the reviewer put them.
// HEADER_PRESETS_BEGIN
const HEADER_PRESETS = {
  "plain": {"font": "arial", "size": 42, "color": "FFFFFF", "outline": 2, "outline_color": "000000", "shadow": false, "plate": "none", "plate_color": "000000", "plate_opacity": 75, "plate_radius": 0, "plate_padding": 16, "align": "center", "line_spacing": 1.0, "y": 210},
  "black_plate": {"font": "arial", "size": 42, "color": "FFFFFF", "outline": 0, "outline_color": "000000", "shadow": false, "plate": "translucent", "plate_color": "000000", "plate_opacity": 75, "plate_radius": 0, "plate_padding": 16, "align": "center", "line_spacing": 1.0, "y": 210},
  "white_plate": {"font": "arial", "size": 42, "color": "FFFFFF", "outline": 2, "outline_color": "000000", "shadow": false, "plate": "solid", "plate_color": "FFFFFF", "plate_opacity": 75, "plate_radius": 0, "plate_padding": 16, "align": "center", "line_spacing": 1.0, "y": 210}
};
// HEADER_PRESETS_END
// The request bounds of each look key (app/models.py HeaderLook; a Clipper
// test compares them). A saved slot look outside them takes the preset value.
// HEADER_LOOK_RULES_BEGIN
const HEADER_LOOK_RULES = {
  "font": ["arial", "helvetica", "avenir", "futura", "impact", "arial_black", "din", "georgia"],
  "size": [24, 96], "y": [0, 1380], "outline": [0, 8], "plate_opacity": [10, 100],
  "plate_radius": [0, 40], "plate_padding": [0, 40], "line_spacing": [0.8, 2.0],
  "plate": ["none", "solid", "translucent"], "align": ["left", "center", "right"]
};
// HEADER_LOOK_RULES_END
const HEADER_COLOR_KEYS = ["color", "outline_color", "plate_color"];

function headerLookValueOk(key, value) {
  const rule = HEADER_LOOK_RULES[key];
  if (HEADER_COLOR_KEYS.includes(key)) return typeof value === "string" && /^[0-9A-Fa-f]{6}$/.test(value);
  if (!rule) return typeof value === "boolean";
  if (typeof rule[0] === "string") return rule.includes(value);
  return typeof value === "number" && Number.isFinite(value) && value >= rule[0] && value <= rule[1];
}

const HEADER_STYLE_KEYS = [
  "color", "outline", "outline_color", "shadow", "plate", "plate_color",
  "plate_opacity", "plate_radius", "plate_padding",
];
// [look key, control class, value kind]
const HEADER_CONTROLS = [
  ["y", "hc-y", "int"], ["size", "hc-size", "int"], ["font", "hc-font", "text"],
  ["color", "hc-color", "color"], ["outline_color", "hc-outline-color", "color"],
  ["outline", "hc-outline", "int"], ["shadow", "hc-shadow", "check"],
  ["plate", "hc-plate", "text"], ["plate_color", "hc-plate-color", "color"],
  ["plate_opacity", "hc-opacity", "int"], ["plate_radius", "hc-radius", "int"],
  ["plate_padding", "hc-padding", "int"], ["align", "hc-align", "text"],
  ["line_spacing", "hc-spacing", "float"],
];
// Wait this long after the last edit before asking for a header preview.
const HEADER_PREVIEW_DELAY_MS = 250;
let headerFontAvailability = null;

function headerPreset(name) {
  return { ...(HEADER_PRESETS[name] || HEADER_PRESETS.plain) };
}

// The look a slot starts from: its saved look, else its header style preset.
// A saved field of the wrong type or out of the request bounds (an older or
// edited store) takes the preset value, so it cannot fail every render.
function seedHeaderLook(ord, style) {
  const look = headerPreset(style);
  const saved = slotDefault(ord, "headerLook", null);
  if (!saved || typeof saved !== "object") return look;
  for (const key of Object.keys(look)) {
    if (typeof saved[key] === typeof look[key] && headerLookValueOk(key, saved[key])) {
      look[key] = HEADER_COLOR_KEYS.includes(key) ? saved[key].toUpperCase() : saved[key];
    }
  }
  return look;
}

function writeHeaderControls(clip) {
  for (const [key, , kind] of HEADER_CONTROLS) {
    const el = clip.hcEls[key];
    if (!el) continue;
    if (kind === "check") el.checked = Boolean(clip.headerLook[key]);
    else if (kind === "color") el.value = `#${String(clip.headerLook[key]).toLowerCase()}`;
    else el.value = String(clip.headerLook[key]);
  }
  updateHeaderOutputs(clip);
}

function readHeaderControls(clip) {
  const look = { ...clip.headerLook };
  for (const [key, , kind] of HEADER_CONTROLS) {
    const el = clip.hcEls[key];
    if (!el) continue;
    if (kind === "check") look[key] = Boolean(el.checked);
    else if (kind === "color") look[key] = String(el.value).replace(/^#/, "").toUpperCase();
    else if (kind === "int") look[key] = Math.round(Number(el.value));
    else if (kind === "float") look[key] = Math.round(Number(el.value) * 100) / 100;
    else look[key] = String(el.value);
  }
  return look;
}

function updateHeaderOutputs(clip) {
  if (!clip.el || !clip.el.querySelector) return;
  const look = clip.headerLook;
  const out = (cls, text) => {
    const el = clip.el.querySelector(`.${cls}`);
    if (el) el.textContent = text;
  };
  out("hc-y-out", `${look.y} px`);
  out("hc-size-out", `${look.size} px`);
  out("hc-outline-out", `${look.outline} px`);
  out("hc-opacity-out", `${look.plate_opacity}%`);
  out("hc-radius-out", `${look.plate_radius} px`);
  out("hc-padding-out", `${look.plate_padding} px`);
  out("hc-spacing-out", `${Number(look.line_spacing).toFixed(2)}×`);
  clip.el.querySelectorAll(".hc-plate-only").forEach((el) => {
    const off = look.plate === "none" || (el.classList.contains("hc-opacity-only") && look.plate !== "translucent");
    el.classList.toggle("hc-off", off);
  });
}

// One header edit: keep the look, save it for the slot, and refresh the preview.
function setHeaderLook(clip, look) {
  clip.headerLook = look;
  rememberSlotStyle(clip.ord, "headerLook", look);
  updateHeaderOutputs(clip);
  scheduleHeaderPreview(clip);
}

// A style card fills in the look keys and leaves the layout keys alone.
function applyHeaderStylePreset(clip, style) {
  const preset = headerPreset(style);
  const look = { ...clip.headerLook };
  for (const key of HEADER_STYLE_KEYS) look[key] = preset[key];
  setHeaderLook(clip, look);
  writeHeaderControls(clip);
}

function labelHeaderFonts(clip) {
  if (!headerFontAvailability || !clip.hcEls || !clip.hcEls.font) return;
  const options = clip.hcEls.font.querySelectorAll ? clip.hcEls.font.querySelectorAll("option") : [];
  for (const option of options) {
    if (!option.dataset.label) option.dataset.label = option.textContent;
    const available = headerFontAvailability[option.value] !== false;
    option.textContent = available ? option.dataset.label : `${option.dataset.label} (not installed: uses Arial Bold)`;
  }
}

async function loadHeaderOptions() {
  try {
    const res = await fetch("api/header-options");
    if (!res.ok) return;
    const data = await res.json();
    headerFontAvailability = Object.fromEntries((data.fonts || []).map((f) => [f.key, Boolean(f.available)]));
    clips.forEach(labelHeaderFonts);
  } catch {
    /* the list still works; unavailable fonts just are not marked */
  }
}

function headerPreviewPayload(clip) {
  return {
    header: clip.headerEl.value,
    header_style: radioValue(clip.headerStyleEl),
    header_look: clip.headerLook,
    geometry: clip.geometryEl ? radioValue(clip.geometryEl) : "auto",
  };
}

// Debounced: only the last edit in a burst asks the server.
function scheduleHeaderPreview(clip) {
  if (!clip.jobId) return;
  clearTimeout(clip.headerPreviewTimer);
  clip.headerPreviewTimer = setTimeout(() => requestHeaderPreview(clip), HEADER_PREVIEW_DELAY_MS);
}

function setHeaderPreviewNote(clip, text) {
  if (clip.headerPreviewNoteEl) clip.headerPreviewNoteEl.textContent = text || "";
}

// Ask for the exact header PNG (RiceSuite #65). A reply that a newer request
// overtook is dropped, so a slow reply cannot show an older look.
async function requestHeaderPreview(clip) {
  const seq = (clip.headerPreviewSeq || 0) + 1;
  clip.headerPreviewSeq = seq;
  let data;
  try {
    const res = await fetch(`api/jobs/${clip.jobId}/header-preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(headerPreviewPayload(clip)),
    });
    data = await res.json();
    if (!res.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "a header setting is out of range");
    }
  } catch (err) {
    if (seq !== clip.headerPreviewSeq) return;
    showHeaderPreview(clip, null);
    setHeaderPreviewNote(clip, `Header preview unavailable: ${err.message}`);
    return;
  }
  if (seq !== clip.headerPreviewSeq) return;
  clip.headerWarnings = data.warnings || null;
  showHeaderPreview(clip, data.image || null);
  setHeaderPreviewNote(clip, data.note || (clip.headerPreviewApprox
    ? "Preview drawn in the centre 9:16 of this source; the render follows the subject crop."
    : ""));
  if (clip.geoState) applyGeometry(clip, clip.geoState);
}

function showHeaderPreview(clip, image) {
  const win = clip.headerPreviewWindowEl;
  if (!win || !clip.headerPreviewEl) return;
  if (!image) {
    win.hidden = true;
    clip.headerPreviewEl.removeAttribute("src");
    return;
  }
  clip.headerPreviewEl.src = image;
  win.hidden = false;
  placeHeaderPreview(clip);
}

// How the render will frame this clip, as render.geometry.resolve_geometry
// decides it: exact 1080x1920 passes through, a photo always blur-pads, and
// otherwise the Geometry choice and the content's plan decide.
function previewGeometry(clip) {
  const state = clip.geoState || {};
  if (state.width === 1080 && state.height === 1920) return "pass";
  if (clip.isPhoto === true) return "blur_pad";
  const requested = clip.geometryEl ? radioValue(clip.geometryEl) : "auto";
  const content = clip.contentEl ? radioValue(clip.contentEl) : "speech";
  const plan = content === "music" ? state.music_plan : state.crop_plan;
  if (requested === "blur_pad") return "blur_pad";
  if (requested === "crop") return plan ? "crop" : "blur_pad";
  return plan && plan.decision === "crop" ? "crop" : "blur_pad";
}

// The blur-pad output as the render builds it (render.geometry): the frame
// blurred to cover 9:16, with the whole picture fitted and centred on top.
function drawBlurPadMock(canvas, media, width, height) {
  const w = media.videoWidth || media.naturalWidth;
  const h = media.videoHeight || media.naturalHeight;
  canvas.width = Math.max(1, Math.round(width));
  canvas.height = Math.max(1, Math.round(height));
  const c2d = canvas.getContext("2d");
  if (!c2d || !w || !h) return;
  const cover = Math.max(width / w, height / h);
  c2d.save();
  // boxblur=20:2 on the 1080 px frame, scaled to this canvas.
  c2d.filter = `blur(${Math.max(1, (16 * width) / 1080)}px)`;
  c2d.drawImage(media, (width - w * cover) / 2, (height - h * cover) / 2, w * cover, h * cover);
  c2d.restore();
  const fit = Math.min(width / w, height / h);
  c2d.drawImage(media, (width - w * fit) / 2, (height - h * fit) / 2, w * fit, h * fit);
}

// The output frame over the source as shown. A blur-pad clip shows the whole
// output frame fitted in the source box (drawn by drawBlurPadMock); otherwise
// the frame is the whole picture of a 9:16 source, else its centred 9:16
// window, an approximation of the moving subject crop.
function headerPreviewBox(media, mode = "crop") {
  const w = media.videoWidth || media.naturalWidth;
  const h = media.videoHeight || media.naturalHeight;
  const bw = media.clientWidth;
  const bh = media.clientHeight;
  if (!(w > 0 && h > 0 && bw > 0 && bh > 0)) return null; // not loaded or not laid out
  const blurPad = mode === "blur_pad";
  const scale = Math.min(bw / w, bh / h);
  const vw = blurPad ? bw : w * scale;
  const vh = blurPad ? bh : h * scale;
  const vx = media.offsetLeft + (media.clientLeft || 0) + (bw - vw) / 2;
  const vy = media.offsetTop + (media.clientTop || 0) + (bh - vh) / 2;
  const fh = Math.min(vh, (vw * 16) / 9);
  const fw = (fh * 9) / 16;
  return {
    left: vx + (vw - fw) / 2,
    top: vy + (vh - fh) / 2,
    width: fw,
    height: fh,
    approx: !blurPad && Math.abs(w / h - 9 / 16) > 0.01,
  };
}

function placeHeaderPreview(clip) {
  const win = clip.headerPreviewWindowEl;
  if (!win || win.hidden) return;
  const media = clip.isPhoto === true ? clip.sourcePhotoEl : clip.sourceVideoEl;
  const mode = previewGeometry(clip);
  const box = headerPreviewBox(media, mode);
  if (!box) {
    win.style.visibility = "hidden";
    return;
  }
  const base = clip.headerPreviewBaseEl;
  if (base) {
    base.hidden = mode !== "blur_pad";
    if (mode === "blur_pad") drawBlurPadMock(base, media, box.width, box.height);
  }
  win.style.visibility = "";
  win.style.left = `${box.left}px`;
  win.style.top = `${box.top}px`;
  win.style.width = `${box.width}px`;
  win.style.height = `${box.height}px`;
  win.classList.toggle("is-approx", box.approx);
  clip.headerPreviewApprox = box.approx;
}

function anyClipActive() {
  return clips.some((c) => ACTIVE_JOB_STATUSES.has(c.status));
}

function mediaErrText(video) {
  const e = video.error;
  if (!e) return "unknown media error";
  return (
    { 1: "aborted", 2: "network error", 3: "decode error", 4: "codec/format not supported by this browser" }[e.code] ||
    `code ${e.code}`
  );
}

// --- capability badge -------------------------------------------------------

async function checkHealth() {
  try {
    const h = await (await fetch("api/health")).json();
    const badge = $("capbadge");
    if (h.ffmpeg && h.libass) {
      badge.textContent = "ffmpeg + libass ready";
      badge.className = "badge ok";
    } else if (h.ffmpeg) {
      badge.textContent = "ffmpeg has no libass — burn-in will fail";
      badge.className = "badge bad";
    } else {
      badge.textContent = "ffmpeg not found";
      badge.className = "badge bad";
    }
  } catch {
    /* ignore */
  }
}

// --- media cache ------------------------------------------------------------

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "unknown size";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function renderCacheInfo(info) {
  const jobDirs = Number(info.job_dirs ?? 0);
  const files = Number(info.files ?? 0);
  const totalBytes = Number(info.total_bytes ?? 0);
  const jobLabel = jobDirs === 1 ? "job directory" : "job directories";
  const fileLabel = files === 1 ? "file" : "files";
  $("cache-info").textContent =
    `Media cache: ${jobDirs} ${jobLabel}, ${files} ${fileLabel}, ${formatBytes(totalBytes)}`;
}

async function refreshCacheInfo() {
  try {
    const response = await fetch(MEDIA_CACHE_INFO_ENDPOINT);
    if (!response.ok) throw new Error(`cache info unavailable (${response.status})`);
    renderCacheInfo(await response.json());
  } catch {
    // Cache info is optional; an unavailable read must not block the review UI.
    $("cache-info").textContent = "Media cache info unavailable";
  }
}

function updateCacheControls() {
  const button = $("clear-cache-btn");
  const active = anyClipActive() || batchBusy;
  button.disabled = active || ingesting || clearInProgress;
  button.title = active
    ? "Wait for the current transcription or render to finish"
    : "Remove server-side media cache files";
}

// --- status helpers ---------------------------------------------------------

function setClipStatus(clip, text, isError = false) {
  clip.statusEl.className = isError ? "clip-status status error" : "clip-status status";
  clip.statusEl.setAttribute("aria-live", isError ? "assertive" : "polite");
  clip.statusEl.textContent = text;
}

// Operation observation is independent of transport keys and never starts work.
let progressAttempt = null;
let progressTimer = null;
let progressGeneration = 0;
const PROGRESS_TAB_KEY = "riceclipper.progress.v1";
const PROGRESS_VIEW_KEY = "riceclipper.progressView.v1";

function showProgress(operation, state, count, detail, isError = false) {
  const bar = $("operation-progress");
  bar.classList.toggle("error", isError);
  for (const [id, value] of [["progress-operation", operation], ["progress-state", state],
    ["progress-count", count], ["progress-current", detail]]) {
    if ($(id).textContent !== value) $(id).textContent = value;
  }
  try { sessionStorage.setItem(PROGRESS_VIEW_KEY, JSON.stringify({ operation, state, count, detail, isError })); } catch { /* optional */ }
}

function stopProgress() {
  clearTimeout(progressTimer);
  progressTimer = null;
  progressGeneration += 1;
  progressAttempt = null;
  try { sessionStorage.removeItem(PROGRESS_TAB_KEY); } catch { /* optional */ }
}

function setBatchStatus(text, isError = false) {
  $("batch-status").textContent = text;
  if (text) showProgress($("progress-operation").textContent, isError ? "! Held / failed" : "✓ Complete",
    $("progress-count").textContent, text, isError);
}

function localProgress(operation, state, completed, total, label, detail, isError = false) {
  stopProgress();
  showProgress(operation, state, `${completed} / ${total} ${label}`, detail, isError);
}

function snapshotProgress(data) {
  const sending = data.operation === "send";
  const operation = sending ? "Send to Poster" : "Pull from Searcher";
  const label = sending ? "copied" : "imported";
  const current = data.current;
  let state = "↻ Working";
  let detail = current
    ? `Now: ${{copying: "copying", copied: "copied", importing: "importing", imported: "imported"}[data.stage] || data.stage} clip ${current.position} of ${data.total} — ${current.title}`
    : sending ? "Preparing batch…" : "Finding the next batch…";
  let count = data.total ? `${data.completed} / ${data.total} ${label}` : "";
  if (["committing", "published"].includes(data.stage)) {
    state = "↻ Handing off";
    detail = sending ? "Handing off batch…" : "Recording imported batch custody…";
  }
  if (data.status === "complete") {
    state = "✓ Complete";
    count = `${data.total} / ${data.total} ${sending ? "handed off" : "imported"}`;
    detail = sending ? `Batch ${data.batch_id} is waiting in Poster's inbox.`
      : data.total ? `Imported batch ${data.batch_id}; ready for transcription.` : "No batches waiting.";
  } else if (data.status === "failed") {
    state = "! Failed";
    const failure = data.items.find((item) => item.state === "failed");
    const notAttempted = data.items.filter((item) => item.state === "waiting").length;
    detail = `${failure ? `Clip ${failure.position} — ${failure.title}: ` : ""}${data.detail}. ${notAttempted ? `${notAttempted} not attempted. ` : ""}${sending ? "Batch was not handed off." : "Pull was not confirmed; existing custody recovery still applies."}`;
  } else if (data.status === "unconfirmed") {
    state = "? Result unknown";
    detail = data.published
      ? `Batch ${data.batch_id} reached the handoff boundary; confirmation is unavailable. ${data.detail}`
      : `An earlier send may still be running; result unknown. ${data.detail}`;
  }
  showProgress(operation, state, count, detail, ["failed", "unconfirmed"].includes(data.status));
}

function watchProgress(operation, id, restoring = false) {
  stopProgress();
  const attempt = { operation, id };
  progressAttempt = attempt;
  const generation = progressGeneration;
  let unavailableReads = 0;
  try { sessionStorage.setItem(PROGRESS_TAB_KEY, JSON.stringify(attempt)); } catch { /* optional */ }
  if (!restoring) showProgress(operation === "send" ? "Send to Poster" : "Pull from Searcher", "↻ Working", "",
    operation === "send" ? "Preparing batch…" : "Finding the next batch…");
  async function poll() {
    if (generation !== progressGeneration) return;
    try {
      const response = await fetch(`api/progress/${operation}/${encodeURIComponent(id)}`);
      if (generation !== progressGeneration) return;
      if (!response.ok) {
        if (response.status === 404 && (restoring || ++unavailableReads >= 3)) {
          showProgress($("progress-operation").textContent, "? Result unknown", $("progress-count").textContent,
            "Operation progress unavailable after restart or expiry; outcome unknown.", true);
          stopProgress();
          return;
        }
        throw new Error("Progress connection lost");
      }
      const data = await response.json();
      if (generation !== progressGeneration) return;
      unavailableReads = 0;
      snapshotProgress(data);
      if (data.status !== "active") { stopProgress(); return; }
    } catch {
      if (generation !== progressGeneration) return;
      showProgress($("progress-operation").textContent, "? Connection lost / result unknown",
        $("progress-count").textContent, $("progress-current").textContent, true);
    }
    if (generation === progressGeneration) progressTimer = setTimeout(poll, 750);
  }
  progressTimer = setTimeout(poll, 350);
  return id;
}

function lostProgress(message) {
  showProgress($("progress-operation").textContent, "? Connection lost / result unknown",
    $("progress-count").textContent, `${$("progress-current").textContent} ${message}`, true);
}

function updateRenderAllButton() {
  const ready = clips.some((c) => c.jobId && (c.status === "ready" || c.status === "done"));
  $("render-all-btn").disabled = batchBusy || ingesting || !ready;
  // Handoff is offered once at least one clip has a rendered output.
  const anyDone = clips.some((c) => c.jobId && c.status === "done");
  $("send-handoff-btn").disabled = batchBusy || ingesting || !anyDone;
  // One Searcher batch stays one Clipper batch (W2-01).
  $("pull-searcher-btn").disabled = !workspaceFree();
}

// Rendered, and nothing in the card changed since its render request (W1-06):
// the MP4 matches the header and transcript that would be sent with it.
function clipCurrent(c) {
  return Boolean(c.jobId) && c.status === "done" && (c.edits || 0) === (c.renderedEdits || 0);
}

function radioValue(group) {
  return group.querySelector('input[type="radio"]:checked').value;
}

function setRadioValue(group, value) {
  const option = group.querySelector(`input[type="radio"][value="${value}"]`);
  if (option) option.checked = true;
}

function setRadioDisabled(group, disabled) {
  group.querySelectorAll('input[type="radio"]').forEach((option) => {
    option.disabled = disabled;
  });
}

function handleGeometryChange(clip) {
  scheduleHeaderPreview(clip);
  placeHeaderPreview(clip);
}

function handleContentChange(clip) {
  const isMusic = radioValue(clip.contentEl) === "music";
  clip.lyricsEl.hidden = !isMusic;
  clip.reviewGridEl.classList.toggle("music-review", isMusic);
  if (clip.geoState) applyGeometry(clip, clip.geoState);
  scheduleHeaderPreview(clip);
  placeHeaderPreview(clip); // the music plan may frame it differently
}

// --- clip cards -------------------------------------------------------------

// Any edit after a render leaves that render on show but marks it stale, in
// the status line and on the rendered frame (Issue #20).
function noteClipEdited(clip) {
  clip.edits = (clip.edits || 0) + 1;
  if (clip.status === "done") {
    setClipStatus(clip, "Edited since its render. Render it again before it is sent.");
  }
  syncResultStale(clip);
}

function syncResultStale(clip) {
  clip.resultEl.classList.toggle("is-stale", clip.status === "done" && !clipCurrent(clip));
}

// A seek or volume drag on a player's built-in controls reaches the card as an
// input event whose target is the <video> or <audio> element. Moving through
// or listening to a clip changes nothing that renders, so it is not an edit.
function isReviewEdit(event) {
  const tag = String((event && event.target && event.target.tagName) || "").toUpperCase();
  return tag !== "VIDEO" && tag !== "AUDIO";
}

function buildCard(clip) {
  const node = $("clip-card-template").content.firstElementChild.cloneNode(true);
  clip.el = node;
  // Every control in the card (captions, geometry, content, music, lyrics…)
  // bubbles its input/change events here, so any edit made after a send makes
  // the batch differ from what was sent and holds the workspace.
  const markEdited = (event) => {
    if (isReviewEdit(event)) noteClipEdited(clip);
  };
  node.addEventListener("input", markEdited);
  node.addEventListener("change", markEdited);
  clip.reviewGridEl = node.querySelector(".review-grid");
  clip.titleEl = node.querySelector(".clip-title");
  clip.statusEl = node.querySelector(".clip-status");
  clip.previewStatusEl = node.querySelector(".preview-status");
  clip.geoEl = node.querySelector(".geo-note");
  clip.sourceVideoEl = node.querySelector(".source-video");
  clip.sourcePhotoEl = node.querySelector(".source-photo");
  clip.photoLengthEl = node.querySelector(".photo-length-input");
  clip.headerEl = node.querySelector(".header-input");
  clip.headerGenerateEl = node.querySelector(".header-generate");
  clip.headerFeedbackEl = node.querySelector(".header-feedback");
  clip.headerGenStatusEl = node.querySelector(".header-gen-status");
  clip.headerStyleEl = node.querySelector(".header-style");
  clip.headerPreviewWindowEl = node.querySelector(".header-preview-window");
  clip.headerPreviewEl = node.querySelector(".header-preview");
  clip.headerPreviewBaseEl = node.querySelector(".header-preview-base");
  clip.headerPreviewNoteEl = node.querySelector(".header-preview-note");
  clip.hcEls = {};
  for (const [key, cls] of HEADER_CONTROLS) clip.hcEls[key] = node.querySelector(`.${cls}`);
  clip.contentEl = node.querySelector(".content");
  clip.geometryEl = node.querySelector(".geometry");
  clip.captionsToggleEl = node.querySelector(".captions-toggle");
  clip.captionStyleEl = node.querySelector(".caption-style");
  clip.motionToggleEl = node.querySelector(".motion-toggle");
  clip.transcriptEl = node.querySelector(".transcript");
  clip.lyricsEl = node.querySelector(".lyrics");
  clip.lyricsInputEl = node.querySelector(".lyrics-input");
  clip.lyricsAlignEl = node.querySelector(".lyrics-align");
  clip.lyricsRestoreEl = node.querySelector(".lyrics-restore");
  clip.lyricsBadgeEl = node.querySelector(".lyrics-badge");
  clip.musicInputEl = node.querySelector(".music-input");
  clip.musicModeEl = node.querySelector(".music-mode");
  clip.musicVolumeEl = node.querySelector(".music-volume");
  clip.volLabelEl = node.querySelector(".vol-label");
  clip.musicStartEl = node.querySelector(".music-start");
  clip.musicStartLabelEl = node.querySelector(".music-start-label");
  clip.musicPlayEl = node.querySelector(".music-play");
  clip.musicPreviewEl = node.querySelector(".music-preview");
  clip.musicHintEl = node.querySelector(".music-segment-hint");
  clip.resultEl = node.querySelector(".clip-result");
  clip.outputVideoEl = node.querySelector(".output-video");
  clip.downloadEl = node.querySelector(".download-link");

  node.querySelectorAll('.header-style input[type="radio"]').forEach((option) => {
    option.name = `header-style-${clip.localId}`;
  });
  node.querySelectorAll('.caption-style input[type="radio"]').forEach((option) => {
    option.name = `caption-style-${clip.localId}`;
  });
  node.querySelectorAll('.content input[type="radio"]').forEach((option) => {
    option.name = `content-${clip.localId}`;
  });
  node.querySelectorAll('.geometry input[type="radio"]').forEach((option) => {
    option.name = `geometry-${clip.localId}`;
  });
  const lengthHelp = node.querySelector("#photo-length-help");
  lengthHelp.id = `photo-length-help-${clip.localId}`;
  clip.photoLengthEl.id = `photo-length-${clip.localId}`;
  clip.photoLengthEl.setAttribute("aria-describedby", lengthHelp.id);
  node.querySelector(".photo-length-label").htmlFor = clip.photoLengthEl.id;
  const headerHelp = node.querySelector("#header-help");
  headerHelp.id = `header-help-${clip.localId}`;
  clip.headerHelpEl = headerHelp;
  clip.headerEl.id = `header-input-${clip.localId}`;
  clip.lyricsInputEl.id = `lyrics-input-${clip.localId}`;
  node.querySelector(".lyrics .field-label").htmlFor = clip.lyricsInputEl.id;
  node.querySelector(".header-label").htmlFor = clip.headerEl.id;
  clip.headerEl.setAttribute("aria-describedby", headerHelp.id);

  const clipName = clip.file ? clip.file.name : (clip.name || "Searcher clip");
  clip.titleEl.textContent = `Clip ${clip.ord} — ${clipName}`;
  clip.titleEl.id = `clip-title-${clip.localId}`;
  node.setAttribute("aria-labelledby", clip.titleEl.id);
  node.querySelector(".clip-remove").setAttribute("aria-label", `Remove ${clipName}`);
  clip.sourceVideoEl.setAttribute("aria-label", `Source preview for ${clipName}`);
  clip.outputVideoEl.setAttribute("aria-label", `Rendered output for ${clipName}`);

  // Seed the visual choices from this slot's saved default (SLOT ordinal =
  // clip.ord), falling back to the v1 defaults. Changing a clip writes that
  // slot's default back so it carries to the next batch/session.
  setRadioValue(clip.captionStyleEl, slotDefault(clip.ord, "caption", "classic"));
  setRadioValue(clip.headerStyleEl, slotDefault(clip.ord, "header", "plain"));
  clip.captionStyleEl.addEventListener("change", () => {
    rememberSlotStyle(clip.ord, "caption", radioValue(clip.captionStyleEl));
  });
  // Caption motion (RiceSuite #66): on unless this slot saved it off.
  clip.motionToggleEl.checked = slotFlag(clip.ord, "motion", true);
  clip.motionToggleEl.addEventListener("change", () => {
    rememberSlotStyle(clip.ord, "motion", clip.motionToggleEl.checked);
  });
  clip.headerStyleEl.addEventListener("change", (event) => {
    if (!event.target || event.target.type !== "radio") return; // a header control
    rememberSlotStyle(clip.ord, "header", radioValue(clip.headerStyleEl));
    applyHeaderStylePreset(clip, radioValue(clip.headerStyleEl));
  });
  // Header controls (RiceSuite #65): seeded from the slot, saved per slot.
  clip.headerLook = seedHeaderLook(clip.ord, radioValue(clip.headerStyleEl));
  writeHeaderControls(clip);
  labelHeaderFonts(clip);
  for (const [key] of HEADER_CONTROLS) {
    const el = clip.hcEls[key];
    if (!el) continue;
    const onEdit = () => setHeaderLook(clip, readHeaderControls(clip));
    el.addEventListener("input", onEdit);
    el.addEventListener("change", onEdit);
  }
  node.querySelector(".hc-reset").addEventListener("click", () => {
    setHeaderLook(clip, headerPreset(radioValue(clip.headerStyleEl)));
    writeHeaderControls(clip);
    noteClipEdited(clip);
  });
  clip.headerEl.addEventListener("input", () => scheduleHeaderPreview(clip));
  clip.sourceVideoEl.addEventListener("loadedmetadata", () => placeHeaderPreview(clip));
  // A blur-pad mock shows the frame the video is on.
  for (const event of ["loadeddata", "seeked", "timeupdate", "pause"]) {
    clip.sourceVideoEl.addEventListener(event, () => placeHeaderPreview(clip));
  }
  clip.geometryEl.addEventListener("change", () => handleGeometryChange(clip));
  clip.sourcePhotoEl.addEventListener("load", () => placeHeaderPreview(clip));
  // The source box resizes with the window and with the settings column (it
  // grows when Adjust header opens), so the preview follows its size.
  if (typeof ResizeObserver === "function") {
    const follow = new ResizeObserver(() => placeHeaderPreview(clip));
    follow.observe(clip.sourceVideoEl);
    follow.observe(clip.sourcePhotoEl);
  }
  clip.contentEl.addEventListener("change", () => handleContentChange(clip));

  clip.musicVolumeEl.addEventListener("input", (e) => {
    clip.volLabelEl.textContent = Number(e.target.value).toFixed(2);
    clip.musicPreviewEl.volume = Math.min(1, Number(e.target.value));
  });
  clip.musicInputEl.addEventListener("change", () => loadMusicPreview(clip));
  clip.musicStartEl.addEventListener("input", () => {
    stopSegmentPreview(clip);
    clip.musicStartLabelEl.textContent = formatClock(Number(clip.musicStartEl.value));
  });
  clip.musicPlayEl.addEventListener("click", () => toggleSegmentPreview(clip));
  clip.musicPreviewEl.addEventListener("ended", () => stopSegmentPreview(clip));
  clip.photoLengthEl.addEventListener("input", () => {
    stopSegmentPreview(clip);
    syncMusicStart(clip);
  });
  // First music pick defaults the mode to "mix under original" — but only while
  // the mode is still untouched, so a deliberate "replace" (or "none") stands.
  clip.musicInputEl.addEventListener("change", () => {
    if (clip.musicInputEl.files.length && !clip.musicModeTouched) {
      clip.musicModeEl.value = clip.isPhoto === true ? "replace" : "mix";
    }
  });
  clip.musicModeEl.addEventListener("change", () => { clip.musicModeTouched = true; });
  clip.captionsToggleEl.addEventListener("change", () => {
    setRadioDisabled(clip.captionStyleEl, !clip.captionsToggleEl.checked);
    clip.motionToggleEl.disabled = !clip.captionsToggleEl.checked;
  });
  clip.headerGenerateEl.addEventListener("click", () => regenerateHeader(clip));
  clip.lyricsAlignEl.addEventListener("click", () => alignLyrics(clip));
  clip.lyricsRestoreEl.addEventListener("click", () => restoreTranscript(clip));
  node.querySelector(".clip-remove").addEventListener("click", () => removeClip(clip));

  // Preview from the File (blob), muted — some re-encoded sources throw
  // MEDIA_ERR_DECODE mid-play with audio; muting avoids it. Clean-audio review
  // happens on the rendered output.
  clip.sourceVideoEl.onerror = () => {
    clip.previewStatusEl.className = "preview-status status error";
    clip.previewStatusEl.textContent =
      `Can't preview this file in-browser (${mediaErrText(clip.sourceVideoEl)}). The rendered output is always H.264/AAC and will play here regardless.`;
  };
  if (clip.file) {
    clip.sourceUrl = URL.createObjectURL(clip.file);
    if (isPhotoFile(clip.file)) applyPhotoCard(clip);
    else clip.sourceVideoEl.src = clip.sourceUrl;
  } else if (clip.jobId) {
    // Pulled clip: no local blob — preview from the server's stored source.
    clip.sourceVideoEl.src = `api/jobs/${clip.jobId}/source`;
  }
  if (clip.isPhoto !== true) clip.previewStatusEl.textContent = "Preview starts muted (unmute with the player controls).";

  clip.outputVideoEl.onerror = () => {
    setClipStatus(clip, `Playback failed (${mediaErrText(clip.outputVideoEl)}). The file downloaded fine — use Download to save it.`, true);
  };

  $("clips").appendChild(node);
  if (clip.jobId) scheduleHeaderPreview(clip); // a pulled clip has a job already
}

// A still photo (Issue #54): a PNG, JPEG, or WebP by type, or by name when the
// browser gives no type. The server decides in the end; see applyPhotoCard.
function isPhotoFile(file) {
  return /^image\/(png|jpeg|webp)$/i.test(file.type || "") || /\.(png|jpe?g|webp)$/i.test(file.name || "");
}

// Turn a card into a photo card: a still preview and a length field, music as
// "No music" or "Add music" (a photo has no sound to mix under), and no
// caption, content, geometry, transcript, or lyric controls (SPEC.md D17).
// Only a photo card sets `isPhoto`, so every check compares it with `true`.
function applyPhotoCard(clip) {
  if (clip.isPhoto === true) return;
  clip.isPhoto = true;
  clip.el.classList.add("photo-card");
  clip.sourceVideoEl.removeAttribute("src");
  if (clip.sourceUrl) clip.sourcePhotoEl.src = clip.sourceUrl;
  else if (clip.jobId) clip.sourcePhotoEl.src = `api/jobs/${clip.jobId}/source`;
  clip.sourcePhotoEl.alt = `Source photo for ${clip.file ? clip.file.name : "this clip"}`;
  clip.captionsToggleEl.checked = false;
  clip.musicModeEl.innerHTML = '<option value="none">No music</option><option value="replace">Add music</option>';
  clip.musicModeEl.value = clip.musicInputEl.files.length ? "replace" : "none";
  // The music is a photo's only sound, so it starts at full level, not at
  // the level for mixing under a video's own audio.
  clip.musicVolumeEl.value = "1";
  clip.volLabelEl.textContent = "1.00";
  clip.previewStatusEl.textContent = "";
  clip.headerHelpEl.textContent =
    "1–2 lines, up to 200 characters, burned into the frame. Edit freely.";
}

// The photo's clip length in whole seconds, or null when it is out of range.
function photoLength(clip) {
  const raw = String(clip.photoLengthEl.value).trim();
  if (!/^\d+$/.test(raw)) return null;
  const seconds = Number(raw);
  return seconds >= 3 && seconds <= 60 ? seconds : null;
}

// --- music segment (Issue #55) ----------------------------------------------
// The user picks where the track starts; the segment runs for the clip's
// length. Preview plays the local file, so nothing uploads before render.

function formatClock(seconds) {
  const whole = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

// The clip's length in seconds: the photo length, or the probed video length.
function clipLength(clip) {
  if (clip.isPhoto === true) return photoLength(clip) || 10;
  return Number(clip.geoState && clip.geoState.duration) || Number(clip.sourceVideoEl.duration) || 0;
}

function loadMusicPreview(clip) {
  stopSegmentPreview(clip);
  if (clip.musicUrl) URL.revokeObjectURL(clip.musicUrl);
  clip.musicUrl = null;
  clip.musicPreviewFailed = false;
  clip.musicStartEl.value = "0";
  clip.musicStartLabelEl.textContent = formatClock(0);
  const file = clip.musicInputEl.files[0];
  if (file) {
    clip.musicUrl = URL.createObjectURL(file);
    clip.musicPreviewEl.onloadedmetadata = () => syncMusicStart(clip);
    // A format the browser cannot decode still renders; it starts at 0.
    clip.musicPreviewEl.onerror = () => {
      clip.musicPreviewFailed = true;
      syncMusicStart(clip);
    };
    clip.musicPreviewEl.src = clip.musicUrl;
  } else {
    clip.musicPreviewEl.removeAttribute("src");
  }
  syncMusicStart(clip);
}

// Bound the start so the whole segment fits inside the track. The controls
// wait for both lengths: the track's (from the browser) and the clip's.
function syncMusicStart(clip) {
  const chosen = Boolean(clip.musicInputEl.files[0]);
  const track = Number(clip.musicPreviewEl.duration);
  const length = clipLength(clip);
  const decoded = chosen && clip.musicPreviewFailed !== true && Number.isFinite(track) && track > 0;
  const ready = decoded && length > 0;
  const max = ready ? Math.max(0, Math.floor((track - length) * 10) / 10) : 0;
  clip.musicStartEl.max = String(max);
  if (Number(clip.musicStartEl.value) > max) clip.musicStartEl.value = String(max);
  clip.musicStartEl.disabled = !ready || max === 0;
  clip.musicPlayEl.disabled = !ready;
  clip.musicStartLabelEl.textContent = formatClock(Number(clip.musicStartEl.value));
  let hint;
  if (!chosen) hint = "Choose a track to pick where it starts.";
  else if (clip.musicPreviewFailed === true) hint = "This browser cannot preview the track. The render uses it from the start.";
  else if (!decoded) hint = "Reading the track…";
  else if (length <= 0) hint = "Waiting for the clip length…";
  else if (max === 0) hint = "The track is no longer than the clip, so it plays from the start.";
  else hint = `The segment plays for ${formatClock(length)}. The render fades it in and out.`;
  clip.musicHintEl.textContent = hint;
}

function toggleSegmentPreview(clip) {
  if (clip.segmentTimer) stopSegmentPreview(clip);
  else playSegmentPreview(clip);
}

// Play the chosen segment for the clip's length. A video plays muted from its
// start beside it, so the reviewer hears the music against the picture.
function playSegmentPreview(clip) {
  // One preview at a time across the batch.
  clips.forEach((other) => { if (other !== clip && other.segmentTimer) stopSegmentPreview(other); });
  const audio = clip.musicPreviewEl;
  audio.currentTime = Number(clip.musicStartEl.value) || 0;
  audio.volume = Math.min(1, Number(clip.musicVolumeEl.value));
  if (clip.isPhoto !== true) {
    clip.sourceVideoEl.muted = true;
    clip.sourceVideoEl.currentTime = 0;
    Promise.resolve(clip.sourceVideoEl.play()).catch(() => {});
  }
  clip.musicPlayEl.textContent = "■ Stop";
  clip.segmentTimer = setTimeout(() => stopSegmentPreview(clip), clipLength(clip) * 1000);
  Promise.resolve(audio.play()).catch((err) => {
    // A Stop right after Play interrupts play(); that is not a failure.
    if (err && err.name === "AbortError") return;
    stopSegmentPreview(clip);
    clip.musicHintEl.textContent = "This browser cannot play the track. The render still uses it.";
  });
}

function stopSegmentPreview(clip) {
  if (clip.segmentTimer) clearTimeout(clip.segmentTimer);
  clip.segmentTimer = null;
  clip.musicPreviewEl.pause();
  if (clip.isPhoto !== true) clip.sourceVideoEl.pause();
  clip.musicPlayEl.textContent = "▶ Play segment";
}

function setGeoNote(clip, info) {
  if (info.kind === "photo") {
    clip.geoEl.textContent = info.width === 1080 && info.height === 1920
      ? "1080×1920 photo — perfect 9:16, passthrough."
      : `${info.width}×${info.height} photo — will blur-pad to 1080×1920.`;
  } else if (info.width === 1080 && info.height === 1920) {
    clip.geoEl.textContent = `${info.width}×${info.height} — perfect 9:16, passthrough.`;
  } else if (info.width && info.height) {
    clip.geoEl.textContent = `${info.width}×${info.height} — will use subject crop or blur-pad to 1080×1920.`;
  } else {
    clip.geoEl.textContent = "";
  }
}

function _reasonLabel(reason) {
  if (reason === "low_safe_rate") return "low safe rate";
  if (reason === "low_face_rate") return "low face rate";
  if (reason === "no_samples") return "no face";
  return reason;
}

function _speechSummary(plan, pct) {
  if (!plan) return "";
  if (plan.reason === "analysis_failed") return "blur-pad · analysis failed";
  if (plan.decision === "crop")
    return `crop · face ${pct(plan.face_rate)}% · safe ${pct(plan.safe_rate)}%`;
  return `blur-pad · face ${pct(plan.face_rate)}% · ${_reasonLabel(plan.reason)}`;
}

function _musicSummary(plan) {
  if (!plan) return "";
  if (plan.reason === "analysis_failed") return "blur-pad · analysis failed";
  if (plan.reason === "hold_static") return "crop · static centered";
  return `crop · face ${Math.round(plan.face_rate * 100)}% · holds`;
}

function applyGeometry(clip, state) {
  if (!clip.geometryEl) return;
  const landscape = state.width > state.height;
  clip.geometryEl.hidden = !landscape;
  if (!landscape) return;

  const content = clip.contentEl ? radioValue(clip.contentEl) : "speech";
  const plan = content === "music" ? state.music_plan : state.crop_plan;
  const summaryEl = clip.geometryEl.querySelector(".geometry-summary");
  const warnEl = clip.geometryEl.querySelector(".geometry-warning");
  const pct = (rate) => Math.round(rate * 100);

  summaryEl.textContent =
    content === "music" ? _musicSummary(plan) : _speechSummary(plan, pct);

  // The header preview re-checks the zone for this clip's own header (#65).
  const warnings = clip.headerWarnings;
  const planKey = content === "music" ? "music_plan" : "crop_plan";
  const warning = warnings && planKey in warnings ? warnings[planKey] : plan ? plan.warning : null;
  warnEl.classList.toggle("header-warning", warning === "header_zone");
  if (warning === "header_zone") {
    warnEl.textContent = "face near header";
    warnEl.hidden = false;
  } else if (warning === "caption_zone") {
    warnEl.textContent = "face near captions";
    warnEl.hidden = false;
  } else {
    warnEl.textContent = "";
    warnEl.hidden = true;
  }
}

function renderTranscript(clip) {
  const box = clip.transcriptEl;
  box.innerHTML = "";
  clip.words.forEach((w, i) => {
    const span = document.createElement("span");
    span.className = "word";
    span.contentEditable = "true";
    span.textContent = w.text;
    span.dataset.index = i;
    box.appendChild(span);
    box.appendChild(document.createTextNode(" "));
  });
}

function collectWords(clip) {
  // Preserve locked timing (D4); only text is editable.
  const spans = clip.transcriptEl.querySelectorAll(".word");
  return Array.from(spans).map((span) => {
    const w = clip.words[Number(span.dataset.index)];
    return { text: span.textContent.trim(), start: w.start, end: w.end, line_start: w.line_start };
  });
}

async function alignLyrics(clip) {
  // A render in flight uses the words it was sent; changing them now would
  // land a render that no longer matches (Issue #20 review).
  if (!clip.jobId || clip.status === "rendering") return;
  clip.lyricsAlignEl.disabled = true;
  setClipStatus(clip, "Aligning lyrics…");
  try {
    const res = await fetch(`api/jobs/${clip.jobId}/lyrics`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lyrics: clip.lyricsInputEl.value }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "lyric alignment failed");
    clip.words = data.words || [];
    renderTranscript(clip);
    clip.status = "ready";
    clip.lyricsBadgeEl.textContent =
      data.method === "anchors"
        ? `aligned · ${Math.round(data.anchor_rate * 100)}% anchors`
        : "even fill";
    // Rare, large anchor shift (A1): timing is unchanged, but surface a signal
    // so the operator knows some lines had to move to fit the clip.
    clip.lyricsBadgeEl.classList.toggle(
      "lyrics-badge-warn",
      Boolean(data.anchor_drift_warning),
    );
    if (data.anchor_drift_warning) {
      const drift = Math.round((data.anchor_drift || 0) * 10) / 10;
      clip.lyricsBadgeEl.textContent += ` · ⚠ timing approx (±${drift}s)`;
      clip.lyricsBadgeEl.title =
        "Some lines shifted to fit the clip; highlight timing may be off by up to this much.";
    } else {
      clip.lyricsBadgeEl.removeAttribute("title");
    }
    clearResult(clip);
    setClipStatus(clip, "Ready — review & render");
  } catch (err) {
    setClipStatus(clip, err.message, true);
  } finally {
    clip.lyricsAlignEl.disabled = false;
  }
}

async function restoreTranscript(clip) {
  if (!clip.jobId || clip.status === "rendering") return;
  clip.lyricsRestoreEl.disabled = true;
  setClipStatus(clip, "Restoring transcript…");
  try {
    const res = await fetch(`api/jobs/${clip.jobId}/restore-transcript`, {
      method: "POST",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "restore failed");
    clip.words = data.words || [];
    renderTranscript(clip);
    clip.status = "ready";
    clip.lyricsBadgeEl.textContent = "";
    clip.lyricsBadgeEl.classList.remove("lyrics-badge-warn");
    clip.lyricsBadgeEl.removeAttribute("title");
    clearResult(clip);
    setClipStatus(clip, "Ready — review & render");
  } catch (err) {
    setClipStatus(clip, err.message, true);
  } finally {
    clip.lyricsRestoreEl.disabled = false;
  }
}

function compactClipOrdinals() {
  clips.forEach((clip, index) => {
    const ord = index + 1;
    if (clip.ord === ord) return;
    clip.ord = ord;

    const clipName = clip.file ? clip.file.name : (clip.name || "Searcher clip");
    if (clip.titleEl) clip.titleEl.textContent = `Clip ${ord} — ${clipName}`;

    // This clip now occupies a different handoff slot. Carry its current
    // choices into that slot so edits and the next batch use the visible look.
    if (clip.captionStyleEl) rememberSlotStyle(ord, "caption", radioValue(clip.captionStyleEl));
    if (clip.motionToggleEl) rememberSlotStyle(ord, "motion", clip.motionToggleEl.checked);
    if (clip.headerStyleEl) rememberSlotStyle(ord, "header", radioValue(clip.headerStyleEl));
    if (clip.headerLook) rememberSlotStyle(ord, "headerLook", clip.headerLook);
  });
}

function removeClip(clip) {
  if (ACTIVE_JOB_STATUSES.has(clip.status) || batchBusy) return;
  if (clip.sourceUrl) URL.revokeObjectURL(clip.sourceUrl);
  if (clip.outputUrl) URL.revokeObjectURL(clip.outputUrl);
  if (clip.musicUrl) URL.revokeObjectURL(clip.musicUrl);
  if (clip.segmentTimer) stopSegmentPreview(clip);
  clip.el.remove();
  const idx = clips.indexOf(clip);
  if (idx >= 0) {
    clips.splice(idx, 1);
    compactClipOrdinals();
  }
  if (clips.length === 0) {
    $("batch-panel").classList.add("hidden");
    $("upload-panel").classList.remove("hidden");
    // The reviewer removed every clip: the pulled batch is not restored.
    discardPulledBatch();
  }
  updateRenderAllButton();
  updateCacheControls();
}

// --- auto-header generation -------------------------------------------------

function setHeaderGenStatus(clip, text, isError = false) {
  if (!clip.headerGenStatusEl) return;
  clip.headerGenStatusEl.className = isError ? "header-gen-status status error" : "header-gen-status status";
  clip.headerGenStatusEl.textContent = text || "";
}

// Ask the server for a header (SPEC §6.2). The design's only outbound call —
// it generates text and posts nothing. Failures stay soft: the manual header
// field is untouched so a render is never blocked.
async function requestHeader(clip, { feedback = "", avoid = "" } = {}) {
  if (!clip.jobId || clip.headerGenBusy) return;
  clip.headerGenBusy = true;
  clip.headerGenerateEl.disabled = true;
  setHeaderGenStatus(clip, "Generating header…");
  try {
    const payload = {
      transcript: collectWords(clip).map((w) => w.text).join(" ").trim(),
      feedback,
      avoid,
    };
    const res = await fetch(`api/jobs/${clip.jobId}/header`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "header generation failed");
    if (data.header) {
      clip.headerEl.value = data.header;
      scheduleHeaderPreview(clip);
      clip.edits = (clip.edits || 0) + 1; // a new header needs a new render
    }
    setHeaderGenStatus(clip, "");
  } catch (err) {
    setHeaderGenStatus(clip, err.message, true);
  } finally {
    clip.headerGenBusy = false;
    clip.headerGenerateEl.disabled = false;
  }
}

// Manual button: regenerate a meaningfully different header, honoring the
// optional guidance field (mirrors RicePoster's caption regenerate-with-feedback).
function regenerateHeader(clip) {
  return requestHeader(clip, {
    feedback: clip.headerFeedbackEl.value.trim(),
    avoid: clip.headerEl.value.trim(),
  });
}

// --- upload + transcribe (sequential queue) ---------------------------------

$("file-input").addEventListener("change", (e) => {
  addFiles(e.target.files);
  e.target.value = ""; // let the same file / more files be added again
});

function addFiles(fileList) {
  const files = Array.from(fileList || []);
  if (files.length === 0) return;
  $("upload-panel").classList.add("hidden");
  $("batch-panel").classList.remove("hidden");
  for (const file of files) {
    const clip = {
      ...allocateClipIdentity(),
      file,
      jobId: null,
      status: "queued",
      words: [],
      sourceUrl: null,
      outputUrl: null,
      musicModeTouched: false,
    };
    clips.push(clip);
    buildCard(clip);
    setClipStatus(clip, "Queued…");
  }
  updateRenderAllButton();
  updateCacheControls();
  processIngestQueue();
}

function setPullStatus(text, isError = false) {
  $("pull-status").textContent = text || "";
  if (text) showProgress("Pull from Searcher", isError ? "! Failed" : "✓ Complete", $("progress-count").textContent, text, isError);
}

// Add jobs pulled from RiceSearcher as review cards. They already have a server
// job (source stored + geometry probed), so they skip the upload and go straight
// to transcription via the normal ingest queue.
function addPulledJobs(pulled, batchId = "") {
  if (!pulled.length) return;
  if (batchId) writeTab({ pulledBatchId: batchId });
  $("upload-panel").classList.add("hidden");
  $("batch-panel").classList.remove("hidden");
  for (const state of pulled) {
    const clip = {
      ...allocateClipIdentity(),
      file: null,
      name: state.title || "Searcher clip",
      jobId: state.id,
      status: "queued",
      words: [],
      sourceUrl: null,
      outputUrl: null,
      musicModeTouched: false,
    };
    clips.push(clip);
    buildCard(clip);
    setGeoNote(clip, state);
    setClipStatus(clip, "Queued…");
  }
  updateRenderAllButton();
  updateCacheControls();
  processIngestQueue();
}

$("pull-searcher-btn").addEventListener("click", pullFromSearcherClick);

// The Pull button follows the timer's rules (W2-01): one pull at a time, and
// only when nothing unsent would be displaced. A pull whose reply never came
// is retried first; then a batch pulled earlier and not sent (in another tab,
// or one that was closed) opens here; only then is a new batch pulled.
async function pullFromSearcherClick() {
  if (!workspaceFree()) {
    setPullStatus(
      pullInFlight
        ? "A pull is already running."
        : "Send this batch or start over first: one Searcher batch stays one Clipper batch.",
      true,
    );
    return;
  }
  if (!readTab().pendingPullKey && (await restoreOpenBatch())) return;
  if (!workspaceFree()) return;
  await pullNext({ automatic: false });
}

// One pull, from the timer or the button. It carries a key, kept until a
// reply arrives, so a retry after a lost reply gets the same batch back
// instead of the next one (W1-02).
async function pullNext({ automatic }) {
  pullInFlight = true;
  updateRenderAllButton();
  const key = readTab().pendingPullKey || newSendKey();
  writeTab({ pendingPullKey: key });
  if (!automatic) setPullStatus("Pulling the next batch from RiceSearcher…");
  const failed = (message) => {
    if (automatic) {
      autoPullPausedUntil = Date.now() + AUTO_PULL_BACKOFF_MS;
      setPullStatus(`Automatic pull failed: ${message}`, true);
    } else {
      setPullStatus(`pull failed: ${message}`, true);
    }
  };
  try {
    if (clips.length) clearWorkspace(); // only a fully sent batch gets here
    const observation = watchProgress("pull", newSendKey());
    const res = await fetch(`api/pull-from-searcher?pull_key=${encodeURIComponent(key)}&observation_id=${encodeURIComponent(observation)}`, {
      method: "POST",
    });
    writeTab({ pendingPullKey: "" }); // answered: nothing left to retry
    const data = await res.json();
    if (!res.ok) {
      failed(data.detail || "pull failed");
      return;
    }
    stopProgress();
    if (!data.clip_count) {
      localProgress("Pull from Searcher", "✓ Complete", 0, 0, "imported", "No batches waiting in ~/ricesearcher-handoff.");
      return;
    }
    localProgress("Pull from Searcher", "✓ Complete", data.clip_count, data.clip_count, "imported", `Imported batch ${data.batch_id}.`);
    addPulledJobs(data.jobs || [], data.batch_id);
    if (automatic) {
      $("batch-status").textContent = `Batch ${data.batch_id} arrived from RiceSearcher; transcribing ${data.clip_count} clip(s)…`;
    } else {
      $("pull-status").textContent = `Pulled ${data.clip_count} clip(s) from batch ${data.batch_id}.`;
    }
  } catch (err) {
    if (readTab().pendingPullKey) {
      // The reply was lost: the next poll retries with the same key.
      lostProgress(`The pull's reply was lost (${err.message}); existing keyed recovery remains available.`);
    } else {
      failed(err.message);
    }
  } finally {
    pullInFlight = false;
    updateRenderAllButton();
  }
}

async function processIngestQueue() {
  if (ingesting) return; // a running drain picks up newly-queued clips itself
  ingesting = true;
  updateRenderAllButton();
  updateCacheControls();
  try {
    while (true) {
      const clip = clips.find((c) => c.status === "queued");
      if (!clip) break;
      await ingestClip(clip);
      const ready = clips.filter((c) => ["ready", "done"].includes(c.status)).length;
      const failures = clips.filter((c) => c.status === "error");
      const unknown = failures.filter((c) => c.operationUnknown);
      localProgress("Transcribe clips", unknown.length ? "? Connection lost / result unknown" : failures.length ? "! Held / failed" : "↻ Working", ready, clips.length, "ready",
        unknown.length ? `Response lost for ${unknown.map((c) => `Clip ${c.ord}`).join(", ")}; work may still be running. No transcription was retried.` : failures.length ? `${failures.length} failed: ${failures.map((c) => `Clip ${c.ord} — ${c.error}`).join("; ")}` : `Clip ${clip.ord} is ready for review.`, failures.length > 0);
    }
  } finally {
    ingesting = false;
    if (clips.length && clips.every((c) => ["ready", "done"].includes(c.status)))
      localProgress("Transcribe clips", "✓ Ready", clips.length, clips.length, "ready", "Review each clip, then render.");
    updateRenderAllButton();
    updateCacheControls();
    await refreshCacheInfo();
  }
}

async function observedIngestRead(clip, response) {
  try { return await response.json(); } catch (error) {
    clip.operationUnknown = true;
    lostProgress(`The response for clip ${clip.ord} was lost; work may still be running.`);
    throw error;
  }
}

async function observedIngestFetch(clip, url, options) {
  try { return await fetch(url, options); } catch (error) {
    clip.operationUnknown = true;
    lostProgress(`The response for clip ${clip.ord} was lost; work may still be running.`);
    throw error;
  }
}

async function ingestClip(clip) {
  clip.operationUnknown = false;
  try {
    if (!clip.jobId) {
      // Normal upload path. A pulled clip already has a server job, so skip it.
      clip.status = "uploading";
      setClipStatus(clip, "Uploading…");
      localProgress("Transcribe clips", "↻ Working", clips.filter((c) => ["ready", "done"].includes(c.status)).length, clips.length, "ready", `Now: uploading clip ${clip.ord} — ${clip.name || clip.file.name}`);
      updateCacheControls();
      const form = new FormData();
      form.append("file", clip.file);
      const up = await observedIngestFetch(clip, "api/upload", { method: "POST", body: form });
      const updata = await observedIngestRead(clip, up);
      if (!up.ok || updata.status === "error") {
        throw new Error(updata.error || updata.detail || "upload failed");
      }
      clip.jobId = updata.id;
      setGeoNote(clip, updata);
      scheduleHeaderPreview(clip);
      if (updata.kind === "photo") {
        // A photo has nothing to transcribe: it is ready for review now.
        applyPhotoCard(clip);
        clip.geoState = updata;
        clip.status = "ready";
        setClipStatus(clip, "Ready — review & render");
        updateRenderAllButton();
        return;
      }
    }

    clip.status = "transcribing";
    setClipStatus(clip, "Transcribing… (first run downloads the model)");
    localProgress("Transcribe clips", "↻ Working", clips.filter((c) => ["ready", "done"].includes(c.status)).length, clips.length, "ready", `Now: transcribing clip ${clip.ord} of ${clips.length} — ${clip.name || clip.file.name}`);
    clip.lyricsBadgeEl.textContent = "";
    clip.lyricsBadgeEl.classList.remove("lyrics-badge-warn");
    clip.lyricsBadgeEl.removeAttribute("title");
    clip.transcriptEl.innerHTML = '<span class="hint">Transcribing…</span>';
    const tr = await observedIngestFetch(clip, `api/jobs/${clip.jobId}/transcribe`, { method: "POST" });
    const trdata = await observedIngestRead(clip, tr);
    if (!tr.ok || trdata.status === "error") {
      throw new Error(trdata.error || trdata.detail || "transcription failed");
    }
    clip.words = trdata.words || [];
    clip.geoState = trdata;
    renderTranscript(clip);
    applyGeometry(clip, trdata);
    scheduleHeaderPreview(clip); // the crop plans now exist: re-check the zone
    syncMusicStart(clip);
    clip.status = "ready";
    setClipStatus(clip, "Ready — review & render");
    // Header generation is opt-in: nothing is sent to Anthropic automatically
    // after transcription. The user triggers it explicitly with the header
    // "✨ Generate" button (SPEC §6.2). See requestHeader / regenerateHeader.
  } catch (err) {
    clip.status = "error";
    clip.error = err.message;
    clip.transcriptEl.innerHTML = "";
    setClipStatus(clip, err.message, true);
  }
  updateRenderAllButton();
}

// --- render all -------------------------------------------------------------

$("render-all-btn").addEventListener("click", handleRenderAll);

async function handleRenderAll() {
  if (batchBusy || ingesting) return;
  const targets = clips.filter((c) => c.jobId && (c.status === "ready" || c.status === "done"));
  if (targets.length === 0) {
    setBatchStatus("No clips are ready to render yet.", true);
    return;
  }

  batchBusy = true;
  updateRenderAllButton();
  updateCacheControls();

  let ok = 0;
  const failedRenders = [];
  for (let i = 0; i < targets.length; i++) {
    localProgress("Render all", "↻ Working", ok, clips.length, "rendered", `Now: rendering clip ${targets[i].ord} of ${clips.length} — ${targets[i].name || targets[i].file?.name || "clip"}`);
    if (await renderClip(targets[i])) ok += 1;
    else failedRenders.push(targets[i]);
  }

  batchBusy = false;
  updateRenderAllButton();
  updateCacheControls();
  await refreshCacheInfo();
  const heldAfterRender = clips.filter((c) => !clipCurrent(c));
  const unknownRenders = failedRenders.filter((c) => c.renderUnknown);
  const heldDetail = unknownRenders.length
    ? `Render result unknown for ${unknownRenders.map((c) => `Clip ${c.ord}`).join(", ")}; work may still be running.`
    : failedRenders.length
    ? `${failedRenders.length} failed: ${failedRenders.map((c) => `Clip ${c.ord} — ${c.error}`).join("; ")}.`
    : heldAfterRender.length ? `${heldAfterRender.map(heldReason).join("; ")}.`
      : "Send the batch to Poster when ready.";
  localProgress("Render all", unknownRenders.length ? "? Connection lost / result unknown" : heldAfterRender.length ? "! Held / failed" : "✓ Complete", ok, clips.length, "rendered", heldDetail, heldAfterRender.length > 0);
  $("batch-status").textContent =
    ok === targets.length
      ? `Rendered ${ok} clip${ok === 1 ? "" : "s"}. Download from each clip's frame.`
      : `Rendered ${ok} of ${targets.length}; see the per-clip errors above.`;
}

// Job reads that may show another render before this one counts as never
// received (RiceSuite #49): about 12 s at one read every 3 s. A request queued
// behind the job lock is not miscounted: job reads wait on that lock too.
const RENDER_ADMIT_POLLS = 4;
const RENDER_NOT_RECEIVED = "Clipper has no record of this render; render it again";

// Poll GET /api/jobs/{id} after a dropped render fetch (Issue #30). Job state
// names the render Clipper last accepted, and only this render's outcome
// counts: an earlier output or error proves nothing about it (RiceSuite #49).
// Return true once this render is done with output; throw on its error, or
// when reads keep showing another render (the request never arrived); return
// false on timeout so the caller can report the original drop.
// A finished render's status line. A header that fell back to libass says
// why, even when the clip was edited during the render, so it is never
// silent (RiceSuite #65).
function setRenderedStatus(clip, headerNote) {
  const basic = headerNote ? ` Rendered with a basic header. ${headerNote}` : "";
  if (!clipCurrent(clip)) {
    setClipStatus(clip, `Edited since its render. Render it again before it is sent.${basic}`, Boolean(basic));
  } else if (basic) {
    setClipStatus(clip, basic.trim(), true);
  } else {
    setClipStatus(clip, "Rendered ✓");
  }
}

// A request's error detail as text. FastAPI sends a list for a 422.
function requestErrorText(data, fallback) {
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((d) => `${(d.loc || []).filter((part) => part !== "body").join(".")}: ${d.msg}`)
      .join("; ");
  }
  return fallback;
}

async function pollRenderCompletion(clip, renderId) {
  const duration = clip.isPhoto === true
    ? photoLength(clip) || 60
    : Number(clip.geoState && clip.geoState.duration) || 0;
  const timeoutMs = Math.max(120, duration * 10) * 1000;
  const deadline = Date.now() + timeoutMs;
  let seen = false; // Clipper has accepted this render
  let otherReads = 0;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    let state;
    try {
      const resp = await fetch(`api/jobs/${clip.jobId}`);
      if (!resp.ok) continue;
      state = await resp.json();
    } catch {
      continue; // transient error while polling — keep trying until the deadline
    }
    if (state.render_id !== renderId) {
      if (!seen && ++otherReads >= RENDER_ADMIT_POLLS) throw new Error(RENDER_NOT_RECEIVED);
      continue;
    }
    seen = true;
    if (state.status === "done" && state.has_output) {
      clip.headerNote = state.header_note || ""; // the reply that carried it was lost
      return true;
    }
    if (state.status === "error") throw new Error(state.error || "render failed");
  }
  return false;
}

async function renderClip(clip) {
  // Check the photo length before touching the card, so a bad value leaves
  // the last render on show and sends nothing.
  const length = clip.isPhoto === true ? photoLength(clip) : null;
  if (clip.isPhoto === true && length === null) {
    clip.error = "Set the photo length to a whole number of seconds from 3 to 60.";
    setClipStatus(clip, clip.error, true);
    return false;
  }
  clip.renderUnknown = false;
  clip.status = "rendering";
  // Keep the last render in its frame, paused, dimmed and inert, with no
  // download while its file is being replaced in place (Issue #20).
  clip.outputVideoEl.pause();
  clip.outputVideoEl.inert = true;
  clip.downloadEl.removeAttribute("href");
  clip.resultEl.classList.add("is-busy");
  clip.lyricsAlignEl.disabled = true;
  clip.lyricsRestoreEl.disabled = true;
  setClipStatus(clip, "Rendering…");
  updateCacheControls();

  const mode = clip.musicModeEl.value;
  const musicFile = clip.musicInputEl.files[0];
  try {
    let filename = null;
    if (mode !== "none" && musicFile) {
      setClipStatus(clip, "Uploading music…");
      showProgress("Render all", "↻ Working", $("progress-count").textContent, `Now: uploading music for clip ${clip.ord}`);
      const form = new FormData();
      form.append("file", musicFile);
      const mres = await fetch(`api/jobs/${clip.jobId}/music`, { method: "POST", body: form });
      const mdata = await mres.json();
      if (!mres.ok) throw new Error(mdata.detail || "music upload failed");
      filename = mdata.filename;
    }

    setClipStatus(clip, "Rendering… (captions, header & audio)");
    showProgress("Render all", "↻ Working", $("progress-count").textContent, `Now: rendering clip ${clip.ord} — captions, header & audio`);
    // Any edit after this point is not in the MP4 (W1-06).
    const editsAtRender = clip.edits || 0;
    const payload = {
      words: collectWords(clip),
      header: clip.headerEl.value,
      captions_on: clip.captionsToggleEl.checked,
      caption_style: radioValue(clip.captionStyleEl),
      motion: clip.motionToggleEl.checked,
      header_style: radioValue(clip.headerStyleEl),
      header_look: clip.headerLook,
      geometry: radioValue(clip.geometryEl),
      content: radioValue(clip.contentEl),
      music: {
        mode: musicFile ? mode : "none",
        volume: Number(clip.musicVolumeEl.value),
        filename,
        start: musicFile ? Number(clip.musicStartEl.value) || 0 : 0,
      },
    };
    if (clip.isPhoto === true) payload.photo_duration = length;
    // Names this render in job state, for recovery after a lost reply (#49).
    const renderId = newSendKey();
    payload.render_id = renderId;
    let res;
    let data;
    try {
      res = await fetch(`api/jobs/${clip.jobId}/render`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      data = await res.json();
    } catch (netErr) {
      // The render fetch dropped before a response. A long batch can outlast the
      // browser's patience while the server still finishes (Issue #30). Poll job
      // state for this render: done+output is success, its error or a request
      // that never arrived is failure, timeout is a drop (#49).
      lostProgress(`Render response lost for clip ${clip.ord}; checking its existing job state.`);
      if (await pollRenderCompletion(clip, renderId)) {
        clip.renders = (clip.renders || 0) + 1;
        clip.renderedEdits = editsAtRender;
        clip.status = "done";
        setRenderedStatus(clip, clip.headerNote);
        await showResult(clip);
        return true;
      }
      clip.renderUnknown = true;
      throw netErr;
    }
    if (!res.ok) throw new Error(requestErrorText(data, "render failed"));

    clip.renders = (clip.renders || 0) + 1;
    clip.renderedEdits = editsAtRender;
    clip.status = "done";
    setRenderedStatus(clip, data.header_note);
    await showResult(clip);
    return true;
  } catch (err) {
    clip.status = "ready"; // keep failed renders retryable
    clip.error = err.message;
    clearResult(clip);
    setClipStatus(clip, err.message, true);
    return false;
  } finally {
    clip.lyricsAlignEl.disabled = false;
    clip.lyricsRestoreEl.disabled = false;
  }
}

async function showResult(clip) {
  // output.mp4 is replaced in place. A fresh URL also bypasses the browser's
  // media/range cache, which can keep playing the first render at a stable URL.
  const endpoint = `api/jobs/${clip.jobId}/output?render=${newSendKey()}`;
  // The video element requests playable ranges on demand. Fetching the whole
  // MP4 as a blob first can fail even after the server has finished rendering.
  if (clip.outputUrl) URL.revokeObjectURL(clip.outputUrl);
  clip.outputUrl = null;
  clip.outputVideoEl.src = endpoint;

  clip.downloadEl.href = endpoint;
  clip.downloadEl.download = `riceclipper-${clip.jobId}.mp4`;
  clip.outputVideoEl.inert = false;
  clip.resultEl.classList.remove("is-empty", "is-busy");
  syncResultStale(clip);
}

// The rendered column keeps its 9:16 frame (Issue #20). With no render to show
// (before the first, after a failed render, or once lyric alignment or a
// transcript restore replaces the words) the frame is empty: no video and no
// download. Other edits keep the render on show, marked stale.
function clearResult(clip) {
  clip.outputVideoEl.pause();
  clip.outputVideoEl.inert = false;
  clip.outputVideoEl.removeAttribute("src");
  clip.outputVideoEl.load();
  clip.downloadEl.removeAttribute("href");
  clip.resultEl.classList.remove("is-busy", "is-stale");
  clip.resultEl.classList.add("is-empty");
}

// --- send to RicePoster (handoff) -------------------------------------------

$("send-handoff-btn").addEventListener("click", () => sendBatch());

// Only the Send button sends (ADR-001 "Manual Clipper send", Issue #59). A
// failed or unrendered clip, or one edited since its render, holds the whole
// batch until it is rendered again (or removed): the button refuses it (W3-02).

// Everything that would reach RicePoster: comparing it with what was sent
// tells "nothing unsent" apart from "rendered or edited again after sending".
function batchSnapshot() {
  return JSON.stringify(
    clips.map((c) => [
      c.jobId,
      c.status,
      c.renders || 0,
      c.edits || 0,
      c.headerEl ? c.headerEl.value : "",
      c.captionStyleEl ? radioValue(c.captionStyleEl) : "",
      c.headerStyleEl ? radioValue(c.headerStyleEl) : "",
      c.transcriptEl ? collectWords(c).map((w) => w.text).join(" ") : "",
    ]),
  );
}

// Why a clip holds the batch, for the reviewer.
function heldReason(c) {
  if (c.status === "done") return `Clip ${c.ord} changed after its render`;
  if (c.error) return `Clip ${c.ord} is not rendered — ${c.error}`;
  return `Clip ${c.ord} is not rendered`;
}

async function sendBatch() {
  if (sendInFlight) return;
  if (!clips.some((c) => c.jobId && c.status === "done")) {
    setBatchStatus("Render clips before sending to RicePoster.", true);
    return;
  }
  // The whole batch goes together: nothing is left out silently (W3-02).
  const held = clips.filter((c) => !clipCurrent(c));
  if (held.length) {
    const them = held.length === 1 ? "it" : "them";
    setBatchStatus(
      `${held.map(heldReason).join("; ")}. Render ${them} or remove ${them} from the batch, then send.`,
      true,
    );
    return;
  }
  const done = clips;
  if (
    batchSent &&
    !window.confirm(
      `This batch was already sent to RicePoster as ${sentBatchId}. Send it again as a new batch?`,
    )
  ) {
    return;
  }
  const resend = batchSent; // the reviewer confirmed a second send above
  sendInFlight = true;
  const snapshot = batchSnapshot();
  // One key per send (W1-01). A retry of the unchanged batch after a reply
  // that never came reuses it, so Clipper returns the batch that attempt
  // wrote. A batch changed since then is a new send (review C-1): if the
  // first attempt did arrive, Clipper answers `already_sent` instead.
  if (!sendKey || snapshot !== sendKeySnapshot) {
    sendKey = newSendKey();
    sendKeySnapshot = snapshot;
  }
  let answered = false;

  $("send-handoff-btn").disabled = true;
  setBatchStatus(`Sending ${done.length} clip${done.length === 1 ? "" : "s"} to RicePoster…`);
  const observation = watchProgress("send", newSendKey());
  showProgress("Send to Poster", "↻ Working", `0 / ${done.length} copied`, "Preparing batch…");
  try {
    const payload = {
      observation_id: observation,
      send_key: sendKey,
      resend,
      clips: done.map((c, i) => ({
        job_id: c.jobId,
        position: i + 1, // handoff order → RicePoster slot order
        transcript: collectWords(c).map((w) => w.text).join(" ").trim(),
        header: c.headerEl.value,
        caption_style: radioValue(c.captionStyleEl),
        header_style: radioValue(c.headerStyleEl),
      })),
    };
    const res = await fetch("api/handoff", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    answered = true;
    const data = await res.json();
    if (res.status === 409 && data.send_in_progress) {
      showProgress("Send to Poster", "? Result unknown", "", data.detail, true);
      return;
    }
    if (res.status === 409 && data.already_sent) {
      stopProgress();
      // These clips already went (another tab, or before a reload). What went
      // is not known here, so the workspace stays held (review S-1).
      batchSent = true;
      sentBatchId = data.already_sent;
      sentSnapshot = null;
      sendKey = "";
      sendKeySnapshot = null;
      setBatchStatus(data.detail, true);
      return;
    }
    if (!res.ok) throw new Error(data.detail || "handoff failed");
    stopProgress();
    const n = data.clip_count;
    batchSent = true;
    sentBatchId = data.batch_id;
    // A replayed batch is what the earlier attempt sent, before any later edit.
    sentSnapshot = data.replayed ? sendKeySnapshot : snapshot;
    sendKey = "";
    sendKeySnapshot = null;
    if (nothingUnsent()) clearWaitingBatches(); // the next batch is pulled, not held
    const how = data.replayed ? "The earlier send had arrived: sent" : "Sent";
    localProgress("Send to Poster", "✓ Complete", n, n, "handed off", `${how} batch ${data.batch_id}; waiting in Poster's inbox.`);
  } catch (err) {
    const lost = answered
      ? ""
      : " The reply was lost. Send again: a batch that already arrived is not sent twice.";
    if (!answered) lostProgress(err.message + lost);
    else setBatchStatus(err.message, true);
  } finally {
    sendInFlight = false;
    updateRenderAllButton();
  }
}

function newSendKey() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

// --- start over -------------------------------------------------------------

$("restart-btn").addEventListener("click", resetAll);

function resetAll() {
  if (batchBusy || ingesting) return;
  // Guard the irreversible wipe: transcript edits, headers, and rendered-but-unsent
  // clips live only in the browser, so confirm before discarding a loaded batch.
  if (
    clips.length &&
    !window.confirm(
      "Start over? This discards the current batch — transcript edits, headers, and any rendered clips you haven't sent to RicePoster yet.",
    )
  ) {
    return;
  }
  const pulled = readTab().pulledBatchId;
  clearWorkspace();
  discardPulledBatch(pulled);
}

// Tell Clipper the reviewer let this pulled batch go, so the page does not
// restore it (W1-02). Best effort: a batch that stays open is only offered
// again, never lost.
function discardPulledBatch(batchId = readTab().pulledBatchId) {
  writeTab({ pulledBatchId: "" });
  if (!batchId) return;
  fetch(`api/workspace?batch_id=${encodeURIComponent(batchId)}`, { method: "DELETE" }).catch(
    () => {},
  );
}

function clearWorkspace() {
  clips.forEach((c) => {
    ["sourceVideoEl", "outputVideoEl"].forEach((k) => {
      const v = c[k];
      if (v) {
        v.pause();
        v.removeAttribute("src");
        v.load();
      }
    });
    if (c.sourceUrl) URL.revokeObjectURL(c.sourceUrl);
    if (c.outputUrl) URL.revokeObjectURL(c.outputUrl);
    if (c.musicUrl) URL.revokeObjectURL(c.musicUrl);
    if (c.segmentTimer) stopSegmentPreview(c);
  });
  clips.length = 0;
  $("clips").innerHTML = "";
  $("batch-panel").classList.add("hidden");
  $("upload-panel").classList.remove("hidden");
  $("file-input").value = "";
  $("upload-status").textContent = "";
  setBatchStatus("");
  stopProgress();
  showProgress("Clip progress", "○ Idle", "", "Choose clips or pull a batch to begin.");
  batchSent = false;
  sentBatchId = "";
  sentSnapshot = null;
  sendKey = "";
  sendKeySnapshot = null;
  writeTab({ pulledBatchId: "" });
  clearWaitingBatches();
  updateRenderAllButton();
  updateCacheControls();
}

// --- automatic pull from RiceSearcher (RiceSuite ADR-001 Q12) ---------------
// A complete Searcher batch is pulled without a click, and its clips start
// transcribing, whenever nothing unsent would be displaced: the workspace is
// empty, or the batch it holds was fully rendered and sent. One Searcher batch
// is one Clipper batch ("Send selected" stays the boundary).
const AUTO_PULL_MS = 5000;
const AUTO_PULL_BACKOFF_MS = 60000;
let pullInFlight = false; // one pull at a time, from the button or the timer
let autoPullPausedUntil = 0;

function workspaceFree() {
  if (ingesting || batchBusy || pullInFlight || sendInFlight) return false;
  return nothingUnsent();
}

function nothingUnsent() {
  if (clips.length === 0) return true;
  // Replace a batch only if exactly what it holds now is what was sent.
  return (
    batchSent &&
    clips.every((c) => c.status === "done") &&
    batchSnapshot() === sentSnapshot
  );
}

// Open a pulled batch that was not sent: ``batchId``'s (this tab's own, after
// a reload), or else the oldest (only on a Pull click: it may be another
// tab's, or a closed tab's).
async function restoreOpenBatch(batchId = "") {
  pullInFlight = true;
  try {
    const query = batchId ? `?batch_id=${encodeURIComponent(batchId)}` : "";
    const res = await fetch(`api/workspace${query}`);
    if (!res.ok) return false;
    const data = await res.json();
    const pulled = data.jobs || [];
    if (!data.clip_count || !pulled.length) {
      if (batchId) writeTab({ pulledBatchId: "" }); // sent or discarded meanwhile
      return false;
    }
    if (ingesting || batchBusy || sendInFlight || !nothingUnsent()) return false;
    const loaded = new Set(clips.map((c) => c.jobId));
    if (pulled.some((j) => loaded.has(j.id))) return false; // already here
    if (clips.length) clearWorkspace(); // only a fully sent batch gets here
    addPulledJobs(pulled, data.batch_id);
    const n = data.clip_count;
    $("batch-status").textContent =
      batchId
        ? `Batch ${data.batch_id} from RiceSearcher was restored; transcribing ${n} clip(s) again…`
        : `Opened batch ${data.batch_id}: it was pulled earlier and not sent. If another tab works on it, use that tab.`;
    return true;
  } catch {
    return false;
  } finally {
    pullInFlight = false;
  }
}

// Name an open batch this tab does not hold, so a batch stranded by a closed
// tab is not forgotten. It opens here only on a Pull click.
async function noteOpenBatch() {
  try {
    const res = await fetch("api/workspace");
    if (!res.ok) return;
    const data = await res.json();
    if (data.batch_id && $("progress-state").textContent === "○ Idle") {
      setPullStatus(
        `Batch ${data.batch_id} was pulled and not sent (in another tab, or one that was closed). Press Pull to open it here.`,
      );
    }
  } catch {
    // informational only
  }
}

// While the workspace holds a batch that is not sent, the next Searcher batch
// waits behind it. Name it on its own line in the batch panel (RiceSuite #61):
// the progress bar keeps the last operation's outcome, and the line never pulls.
function showWaitingBatches(waiting) {
  const line = $("searcher-waiting");
  const n = waiting.length;
  line.hidden = n === 0;
  line.textContent = n === 0 ? ""
    : n === 1
      ? `RiceSearcher batch ${waiting[0].batch_id} (${waiting[0].clip_count} clip${waiting[0].clip_count === 1 ? "" : "s"}) is waiting. It opens after you send this batch or start over.`
      : `${n} RiceSearcher batches are waiting (${waiting.map((b) => b.batch_id).join(", ")}). The oldest opens after you send this batch or start over.`;
}

// Timer ticks overlap. Clearing the line drops every read still in flight,
// so a late reply cannot name a batch pulled meanwhile; an older reply never
// replaces a newer one. A slow reply from a held workspace still shows.
let waitingEpoch = 0; // bumped whenever the line is cleared
let waitingReads = 0;
let waitingShown = 0; // the newest read shown

function clearWaitingBatches() {
  waitingEpoch += 1;
  showWaitingBatches([]);
}

async function noteWaitingBatches() {
  if (pullInFlight || nothingUnsent()) {
    clearWaitingBatches();
    return;
  }
  const epoch = waitingEpoch;
  const read = ++waitingReads;
  let waiting;
  try {
    const res = await fetch("api/searcher-inbox");
    if (!res.ok) return; // an unreadable inbox is not an empty one: keep the line
    waiting = (await res.json()).batches || [];
  } catch {
    return;
  }
  if (epoch !== waitingEpoch || read < waitingShown) return;
  // The batch may have been sent or cleared while the inbox was read.
  if (pullInFlight || nothingUnsent()) {
    clearWaitingBatches();
    return;
  }
  waitingShown = read;
  showWaitingBatches(waiting);
}

async function autoPullFromSearcher() {
  if (!workspaceFree()) {
    await noteWaitingBatches();
    return;
  }
  clearWaitingBatches(); // nothing unsent: the next batch is pulled, not held
  if (readTab().pendingPullKey) {
    await pullNext({ automatic: true }); // a lost reply: same key, same batch
    return;
  }
  if (clips.length === 0) {
    const own = readTab().pulledBatchId;
    if (own && (await restoreOpenBatch(own))) return;
    await noteOpenBatch();
  }
  if (!workspaceFree() || Date.now() < autoPullPausedUntil) return;
  let waiting = [];
  try {
    const res = await fetch("api/searcher-inbox");
    if (!res.ok) return;
    waiting = (await res.json()).batches || [];
  } catch {
    return;
  }
  if (!waiting.length || !workspaceFree()) return;
  await pullNext({ automatic: true });
}

setInterval(autoPullFromSearcher, AUTO_PULL_MS);

// --- clear media cache ------------------------------------------------------

$("clear-cache-btn").addEventListener("click", async () => {
  if (!window.confirm("Clear all server-side media cache files? Your original browser files will not be affected.")) {
    return;
  }

  const status = $("cache-status");
  status.className = "status";
  status.textContent = "Clearing media cache…";
  clearInProgress = true;
  updateCacheControls();

  try {
    const response = await fetch("api/media/clear", { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (response.status === 409) {
      status.className = "status error";
      status.textContent = "The cache is busy; wait for transcription or rendering to finish, then try again.";
      return;
    }
    if (!response.ok) {
      throw new Error(data.detail || data.error || `cache clear failed (${response.status})`);
    }

    resetAll();
    await refreshCacheInfo();
    status.className = "status";
    status.textContent = "Media cache cleared.";
  } catch (err) {
    status.className = "status error";
    status.textContent = err.message;
  } finally {
    clearInProgress = false;
    updateCacheControls();
  }
});

checkHealth();
loadHeaderOptions();
refreshCacheInfo();
updateCacheControls();

// Reload restores observation only; ordinary workspace recovery remains separate.
try {
  const view = JSON.parse(sessionStorage.getItem(PROGRESS_VIEW_KEY));
  if (view && ["operation", "state", "count", "detail"].every((key) => typeof view[key] === "string"))
    showProgress(view.operation, view.state, view.count, view.detail, Boolean(view.isError));
  const saved = JSON.parse(sessionStorage.getItem(PROGRESS_TAB_KEY));
  if (saved && ["pull", "send"].includes(saved.operation) && /^[A-Za-z0-9_-]{8,64}$/.test(saved.id))
    watchProgress(saved.operation, saved.id, true);
} catch { /* optional session storage */ }
