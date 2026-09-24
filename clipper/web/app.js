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
let clipSeq = 0; // monotonic counter for stable ordinals
let ingesting = false; // upload+transcribe queue is draining
let batchBusy = false; // render-all in progress
let clearInProgress = false;

const MEDIA_CACHE_INFO_ENDPOINT = "/api/media-info";
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
    const h = await (await fetch("/api/health")).json();
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

function setBatchStatus(text, isError = false) {
  const s = $("batch-status");
  s.className = isError ? "status error" : "status";
  s.setAttribute("aria-live", isError ? "assertive" : "polite");
  s.textContent = text;
}

function updateRenderAllButton() {
  const ready = clips.some((c) => c.jobId && (c.status === "ready" || c.status === "done"));
  $("render-all-btn").disabled = batchBusy || ingesting || !ready;
  // Handoff is offered once at least one clip has a rendered output.
  const anyDone = clips.some((c) => c.jobId && c.status === "done");
  $("send-handoff-btn").disabled = batchBusy || ingesting || !anyDone;
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

// --- clip cards -------------------------------------------------------------

function buildCard(clip) {
  const node = $("clip-card-template").content.firstElementChild.cloneNode(true);
  clip.el = node;
  clip.reviewGridEl = node.querySelector(".review-grid");
  clip.titleEl = node.querySelector(".clip-title");
  clip.statusEl = node.querySelector(".clip-status");
  clip.previewStatusEl = node.querySelector(".preview-status");
  clip.geoEl = node.querySelector(".geo-note");
  clip.sourceVideoEl = node.querySelector(".source-video");
  clip.headerEl = node.querySelector(".header-input");
  clip.headerGenerateEl = node.querySelector(".header-generate");
  clip.headerFeedbackEl = node.querySelector(".header-feedback");
  clip.headerGenStatusEl = node.querySelector(".header-gen-status");
  clip.headerStyleEl = node.querySelector(".header-style");
  clip.contentEl = node.querySelector(".content");
  clip.geometryEl = node.querySelector(".geometry");
  clip.captionsToggleEl = node.querySelector(".captions-toggle");
  clip.captionStyleEl = node.querySelector(".caption-style");
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
  const headerHelp = node.querySelector("#header-help");
  headerHelp.id = `header-help-${clip.localId}`;
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
  clip.headerStyleEl.addEventListener("change", () => {
    rememberSlotStyle(clip.ord, "header", radioValue(clip.headerStyleEl));
  });
  clip.contentEl.addEventListener("change", () => {
    const isMusic = radioValue(clip.contentEl) === "music";
    clip.lyricsEl.hidden = !isMusic;
    clip.reviewGridEl.classList.toggle("music-review", isMusic);
    if (clip.geoState) applyGeometry(clip, clip.geoState);
  });

  clip.musicVolumeEl.addEventListener("input", (e) => {
    clip.volLabelEl.textContent = Number(e.target.value).toFixed(2);
  });
  // First music pick defaults the mode to "mix under original" — but only while
  // the mode is still untouched, so a deliberate "replace" (or "none") stands.
  clip.musicInputEl.addEventListener("change", () => {
    if (clip.musicInputEl.files.length && !clip.musicModeTouched) {
      clip.musicModeEl.value = "mix";
    }
  });
  clip.musicModeEl.addEventListener("change", () => { clip.musicModeTouched = true; });
  clip.captionsToggleEl.addEventListener("change", () => {
    setRadioDisabled(clip.captionStyleEl, !clip.captionsToggleEl.checked);
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
    clip.sourceVideoEl.src = clip.sourceUrl;
  } else if (clip.jobId) {
    // Pulled clip: no local blob — preview from the server's stored source.
    clip.sourceVideoEl.src = `/api/jobs/${clip.jobId}/source`;
  }
  clip.previewStatusEl.textContent = "Preview starts muted (unmute with the player controls).";

  clip.outputVideoEl.onerror = () => {
    setClipStatus(clip, `Playback failed (${mediaErrText(clip.outputVideoEl)}). The file downloaded fine — use Download to save it.`, true);
  };

  $("clips").appendChild(node);
}

function setGeoNote(clip, info) {
  if (info.width === 1080 && info.height === 1920) {
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

  const warning = plan ? plan.warning : null;
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
  if (!clip.jobId) return;
  clip.lyricsAlignEl.disabled = true;
  setClipStatus(clip, "Aligning lyrics…");
  try {
    const res = await fetch(`/api/jobs/${clip.jobId}/lyrics`, {
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
    clip.resultEl.classList.add("hidden");
    setClipStatus(clip, "Ready — review & render");
  } catch (err) {
    setClipStatus(clip, err.message, true);
  } finally {
    clip.lyricsAlignEl.disabled = false;
  }
}

async function restoreTranscript(clip) {
  if (!clip.jobId) return;
  clip.lyricsRestoreEl.disabled = true;
  setClipStatus(clip, "Restoring transcript…");
  try {
    const res = await fetch(`/api/jobs/${clip.jobId}/restore-transcript`, {
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
    clip.resultEl.classList.add("hidden");
    setClipStatus(clip, "Ready — review & render");
  } catch (err) {
    setClipStatus(clip, err.message, true);
  } finally {
    clip.lyricsRestoreEl.disabled = false;
  }
}

function removeClip(clip) {
  if (ACTIVE_JOB_STATUSES.has(clip.status) || batchBusy) return;
  if (clip.sourceUrl) URL.revokeObjectURL(clip.sourceUrl);
  if (clip.outputUrl) URL.revokeObjectURL(clip.outputUrl);
  clip.el.remove();
  const idx = clips.indexOf(clip);
  if (idx >= 0) clips.splice(idx, 1);
  if (clips.length === 0) {
    $("batch-panel").classList.add("hidden");
    $("upload-panel").classList.remove("hidden");
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
    const res = await fetch(`/api/jobs/${clip.jobId}/header`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "header generation failed");
    if (data.header) clip.headerEl.value = data.header;
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
    clipSeq += 1;
    const clip = {
      localId: clipSeq,
      ord: clips.length + 1,
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
  const el = $("pull-status");
  el.textContent = text || "";
  el.className = isError ? "status error" : "status";
}

// Add jobs pulled from RiceSearcher as review cards. They already have a server
// job (source stored + geometry probed), so they skip the upload and go straight
// to transcription via the normal ingest queue.
function addPulledJobs(pulled) {
  if (!pulled.length) return;
  $("upload-panel").classList.add("hidden");
  $("batch-panel").classList.remove("hidden");
  for (const state of pulled) {
    clipSeq += 1;
    const clip = {
      localId: clipSeq,
      ord: clips.length + 1,
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

$("pull-searcher-btn").addEventListener("click", async () => {
  const btn = $("pull-searcher-btn");
  btn.disabled = true;
  setPullStatus("Pulling the next batch from RiceSearcher…");
  try {
    const res = await fetch("/api/pull-from-searcher", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      setPullStatus(data.detail || "pull failed", true);
      return;
    }
    if (!data.clip_count) {
      setPullStatus("No batches waiting in ~/ricesearcher-handoff.");
      return;
    }
    setPullStatus(`Pulled ${data.clip_count} clip(s) from batch ${data.batch_id}.`);
    addPulledJobs(data.jobs || []);
  } catch (err) {
    setPullStatus("pull failed: " + err.message, true);
  } finally {
    btn.disabled = false;
  }
});

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
    }
  } finally {
    ingesting = false;
    updateRenderAllButton();
    updateCacheControls();
    await refreshCacheInfo();
  }
}

async function ingestClip(clip) {
  try {
    if (!clip.jobId) {
      // Normal upload path. A pulled clip already has a server job, so skip it.
      clip.status = "uploading";
      setClipStatus(clip, "Uploading…");
      updateCacheControls();
      const form = new FormData();
      form.append("file", clip.file);
      const up = await fetch("/api/upload", { method: "POST", body: form });
      const updata = await up.json();
      if (!up.ok || updata.status === "error") {
        throw new Error(updata.error || updata.detail || "upload failed");
      }
      clip.jobId = updata.id;
      setGeoNote(clip, updata);
    }

    clip.status = "transcribing";
    setClipStatus(clip, "Transcribing… (first run downloads the model)");
    clip.lyricsBadgeEl.textContent = "";
    clip.lyricsBadgeEl.classList.remove("lyrics-badge-warn");
    clip.lyricsBadgeEl.removeAttribute("title");
    clip.transcriptEl.innerHTML = '<span class="hint">Transcribing…</span>';
    const tr = await fetch(`/api/jobs/${clip.jobId}/transcribe`, { method: "POST" });
    const trdata = await tr.json();
    if (!tr.ok || trdata.status === "error") {
      throw new Error(trdata.error || trdata.detail || "transcription failed");
    }
    clip.words = trdata.words || [];
    clip.geoState = trdata;
    renderTranscript(clip);
    applyGeometry(clip, trdata);
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
  for (let i = 0; i < targets.length; i++) {
    setBatchStatus(`Rendering clip ${i + 1} of ${targets.length}…`);
    if (await renderClip(targets[i])) ok += 1;
  }

  batchBusy = false;
  updateRenderAllButton();
  updateCacheControls();
  await refreshCacheInfo();
  setBatchStatus(
    ok === targets.length
      ? `Rendered ${ok} clip${ok === 1 ? "" : "s"}. Download below.`
      : `Rendered ${ok} of ${targets.length}; see the per-clip errors above.`,
    ok !== targets.length,
  );
}

// Poll GET /api/jobs/{id} after a dropped render fetch (Issue #30). Return true
// once the job reaches done with output, throw on error, return false on
// timeout so the caller can report the original drop.
async function pollRenderCompletion(clip) {
  const duration = Number(clip.geoState && clip.geoState.duration) || 0;
  const timeoutMs = Math.max(120, duration * 10) * 1000;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    let state;
    try {
      const resp = await fetch(`/api/jobs/${clip.jobId}`);
      if (!resp.ok) continue;
      state = await resp.json();
    } catch {
      continue; // transient error while polling — keep trying until the deadline
    }
    if (state.status === "done" && state.has_output) return true;
    if (state.status === "error") throw new Error(state.error || "render failed");
  }
  return false;
}

async function renderClip(clip) {
  clip.status = "rendering";
  clip.resultEl.classList.add("hidden");
  setClipStatus(clip, "Rendering…");
  updateCacheControls();

  const mode = clip.musicModeEl.value;
  const musicFile = clip.musicInputEl.files[0];
  try {
    let filename = null;
    if (mode !== "none" && musicFile) {
      setClipStatus(clip, "Uploading music…");
      const form = new FormData();
      form.append("file", musicFile);
      const mres = await fetch(`/api/jobs/${clip.jobId}/music`, { method: "POST", body: form });
      const mdata = await mres.json();
      if (!mres.ok) throw new Error(mdata.detail || "music upload failed");
      filename = mdata.filename;
    }

    setClipStatus(clip, "Rendering… (captions, header & audio)");
    const payload = {
      words: collectWords(clip),
      header: clip.headerEl.value,
      captions_on: clip.captionsToggleEl.checked,
      caption_style: radioValue(clip.captionStyleEl),
      header_style: radioValue(clip.headerStyleEl),
      geometry: radioValue(clip.geometryEl),
      content: radioValue(clip.contentEl),
      music: { mode: musicFile ? mode : "none", volume: Number(clip.musicVolumeEl.value), filename },
    };
    let res;
    try {
      res = await fetch(`/api/jobs/${clip.jobId}/render`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (netErr) {
      // The render fetch dropped before a response. A long batch can outlast the
      // browser's patience while the server still finishes (Issue #30). Poll job
      // state; treat done+output as success, error as failure, timeout as drop.
      if (await pollRenderCompletion(clip)) {
        clip.status = "done";
        setClipStatus(clip, "Rendered ✓");
        await showResult(clip);
        return true;
      }
      throw netErr;
    }
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "render failed");

    clip.status = "done";
    setClipStatus(clip, "Rendered ✓");
    await showResult(clip);
    return true;
  } catch (err) {
    clip.status = "ready"; // keep failed renders retryable
    clip.error = err.message;
    setClipStatus(clip, err.message, true);
    return false;
  }
}

async function showResult(clip) {
  const resp = await fetch(`/api/jobs/${clip.jobId}/output`);
  if (!resp.ok) throw new Error(`could not load output (${resp.status})`);
  const blob = await resp.blob();

  if (clip.outputUrl) URL.revokeObjectURL(clip.outputUrl);
  clip.outputUrl = URL.createObjectURL(blob);
  clip.outputVideoEl.src = clip.outputUrl;

  clip.downloadEl.href = clip.outputUrl;
  clip.downloadEl.download = `riceclipper-${clip.jobId}.mp4`;
  clip.resultEl.classList.remove("hidden");
}

// --- send to RicePoster (handoff) -------------------------------------------

$("send-handoff-btn").addEventListener("click", async () => {
  const done = clips.filter((c) => c.jobId && c.status === "done");
  if (done.length === 0) {
    setBatchStatus("Render clips before sending to RicePoster.", true);
    return;
  }

  $("send-handoff-btn").disabled = true;
  setBatchStatus(`Sending ${done.length} clip${done.length === 1 ? "" : "s"} to RicePoster…`);
  try {
    const payload = {
      clips: done.map((c, i) => ({
        job_id: c.jobId,
        position: i + 1, // handoff order → RicePoster slot order
        transcript: collectWords(c).map((w) => w.text).join(" ").trim(),
        header: c.headerEl.value,
        caption_style: radioValue(c.captionStyleEl),
        header_style: radioValue(c.headerStyleEl),
      })),
    };
    const res = await fetch("/api/handoff", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "handoff failed");
    const n = data.clip_count;
    setBatchStatus(`Sent batch ${data.batch_id} (${n} clip${n === 1 ? "" : "s"}) to RicePoster.`);
  } catch (err) {
    setBatchStatus(err.message, true);
  } finally {
    updateRenderAllButton();
  }
});

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
  });
  clips.length = 0;
  $("clips").innerHTML = "";
  $("batch-panel").classList.add("hidden");
  $("upload-panel").classList.remove("hidden");
  $("file-input").value = "";
  $("upload-status").textContent = "";
  setBatchStatus("");
  updateRenderAllButton();
  updateCacheControls();
}

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
    const response = await fetch("/api/media/clear", { method: "POST" });
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
refreshCacheInfo();
updateCacheControls();
