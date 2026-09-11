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
  clip.captionsToggleEl = node.querySelector(".captions-toggle");
  clip.captionStyleEl = node.querySelector(".caption-style");
  clip.transcriptEl = node.querySelector(".transcript");
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
  const headerHelp = node.querySelector("#header-help");
  headerHelp.id = `header-help-${clip.localId}`;
  clip.headerEl.id = `header-input-${clip.localId}`;
  node.querySelector(".header-label").htmlFor = clip.headerEl.id;
  clip.headerEl.setAttribute("aria-describedby", headerHelp.id);

  const clipName = clip.file ? clip.file.name : (clip.name || "Searcher clip");
  clip.titleEl.textContent = `Clip ${clip.ord} — ${clipName}`;
  clip.titleEl.id = `clip-title-${clip.localId}`;
  node.setAttribute("aria-labelledby", clip.titleEl.id);
  node.querySelector(".clip-remove").setAttribute("aria-label", `Remove ${clipName}`);
  clip.sourceVideoEl.setAttribute("aria-label", `Source preview for ${clipName}`);
  clip.outputVideoEl.setAttribute("aria-label", `Rendered output for ${clipName}`);

  // Inherit the batch defaults; a manual change marks the field "touched" so a
  // later batch-default change no longer overrides this clip.
  setRadioValue(clip.captionStyleEl, $("batch-caption-style").value);
  setRadioValue(clip.headerStyleEl, $("batch-header-style").value);
  clip.captionStyleEl.addEventListener("change", () => { clip.captionStyleTouched = true; });
  clip.headerStyleEl.addEventListener("change", () => { clip.headerStyleTouched = true; });

  clip.musicVolumeEl.addEventListener("input", (e) => {
    clip.volLabelEl.textContent = Number(e.target.value).toFixed(2);
  });
  clip.captionsToggleEl.addEventListener("change", () => {
    setRadioDisabled(clip.captionStyleEl, !clip.captionsToggleEl.checked);
  });
  clip.headerGenerateEl.addEventListener("click", () => regenerateHeader(clip));
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
    clip.geoEl.textContent = `${info.width}×${info.height} — will be blur-padded to 1080×1920.`;
  } else {
    clip.geoEl.textContent = "";
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
    return { text: span.textContent.trim(), start: w.start, end: w.end };
  });
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

// Fired once per clip after transcription; never clobbers a header the user
// already typed.
function autoGenerateHeader(clip) {
  if (clip.headerEl.value.trim()) return Promise.resolve();
  return requestHeader(clip);
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
      captionStyleTouched: false,
      headerStyleTouched: false,
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
      captionStyleTouched: false,
      headerStyleTouched: false,
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
    clip.transcriptEl.innerHTML = '<span class="hint">Transcribing…</span>';
    const tr = await fetch(`/api/jobs/${clip.jobId}/transcribe`, { method: "POST" });
    const trdata = await tr.json();
    if (!tr.ok || trdata.status === "error") {
      throw new Error(trdata.error || trdata.detail || "transcription failed");
    }
    clip.words = trdata.words || [];
    renderTranscript(clip);
    clip.status = "ready";
    setClipStatus(clip, "Ready — review & render");
    // Auto-fill the header from the frame + transcript. Soft-fails on its own
    // status line, so a header hiccup never fails the clip.
    await autoGenerateHeader(clip);
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
      music: { mode: musicFile ? mode : "none", volume: Number(clip.musicVolumeEl.value), filename },
    };
    const res = await fetch(`/api/jobs/${clip.jobId}/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "render failed");

    clip.status = "done";
    setClipStatus(clip, "Rendered ✓");
    await showResult(clip);
    return true;
  } catch (err) {
    clip.status = "error";
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

// --- batch-default preset propagation ---------------------------------------

$("batch-caption-style").addEventListener("change", (e) => {
  clips.forEach((c) => {
    if (!c.captionStyleTouched && c.captionStyleEl) setRadioValue(c.captionStyleEl, e.target.value);
  });
});
$("batch-header-style").addEventListener("change", (e) => {
  clips.forEach((c) => {
    if (!c.headerStyleTouched && c.headerStyleEl) setRadioValue(c.headerStyleEl, e.target.value);
  });
});

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
