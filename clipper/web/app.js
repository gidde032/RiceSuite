// RiceClipper review UI — vanilla JS, talks to the local FastAPI server.
//
// Video preview follows RicePoster's proven pattern: play from a client-side
// blob (URL.createObjectURL) rather than a server stream. The source comes
// straight from the selected File; the rendered output is fetched once into a
// blob. This sidesteps HTTP range / content-disposition / streaming quirks that
// break a <video src> pointed at a server endpoint.

const $ = (id) => document.getElementById(id);

let job = null; // { id, words: [...], ... }
let selectedFile = null; // the uploaded source File, for local preview
let sourceUrl = null; // active object URLs, revoked on replace/restart
let outputUrl = null;
let operationBusy = false;
let clearInProgress = false;

const MEDIA_CACHE_INFO_ENDPOINT = "/api/media-info";
const ACTIVE_JOB_STATUSES = new Set(["transcribing", "rendering"]);

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
  const activeJob = job && ACTIVE_JOB_STATUSES.has(job.status);
  button.disabled = operationBusy || clearInProgress || activeJob;
  button.title = activeJob
    ? "Wait for the current transcription or render to finish"
    : "Remove server-side media cache files";
}

// --- upload + transcribe ----------------------------------------------------

$("file-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  selectedFile = file;
  operationBusy = true;
  updateCacheControls();

  // Preview immediately from the File (RicePoster's blob pattern).
  showSourcePreview(file);
  $("transcript").innerHTML = '<span class="hint">Uploading…</span>';

  const form = new FormData();
  form.append("file", file);
  try {
    const up = await fetch("/api/upload", { method: "POST", body: form });
    const updata = await up.json();
    if (!up.ok || updata.status === "error") {
      throw new Error(updata.error || updata.detail || "upload failed");
    }
    job = updata;
    setGeoNote();

    $("transcript").innerHTML =
      '<span class="hint">Transcribing… (first run downloads the model)</span>';
    const tr = await fetch(`/api/jobs/${job.id}/transcribe`, { method: "POST" });
    const trdata = await tr.json();
    if (!tr.ok || trdata.status === "error") {
      throw new Error(trdata.error || trdata.detail || "transcription failed");
    }
    job = trdata;
    renderTranscript();
  } catch (err) {
    // Revert to the upload panel and surface the error.
    $("review-panel").classList.add("hidden");
    $("upload-panel").classList.remove("hidden");
    const status = $("upload-status");
    status.className = "status error";
    status.textContent = err.message;
  } finally {
    operationBusy = false;
    updateCacheControls();
    await refreshCacheInfo();
  }
});

// --- review gate ------------------------------------------------------------

function showSourcePreview(file) {
  $("upload-panel").classList.add("hidden");
  $("review-panel").classList.remove("hidden");

  // Fresh <video controls> each time, started MUTED. Muted is deliberate: some
  // re-encoded source clips (e.g. social re-uploads) have audio tracks Chrome
  // can't decode mid-playback and it throws MEDIA_ERR_DECODE — muting avoids
  // that entirely (it's the same trick RicePoster relies on). Controls let you
  // pause / scrub / unmute. Clean-audio review happens on the rendered output.
  const video = document.createElement("video");
  video.id = "source-video";
  video.controls = true;
  video.muted = true;
  video.playsInline = true;
  video.onerror = () => {
    const s = $("preview-status");
    s.className = "status error";
    s.textContent = `Can't preview this file in-browser (${mediaErrText(video)}). The rendered output is always H.264/AAC and will play here regardless.`;
  };
  $("source-video").replaceWith(video);

  const s = $("preview-status");
  s.className = "status";
  s.textContent = "Preview starts muted (unmute with the player controls). Audio review happens on the rendered output.";

  if (sourceUrl) URL.revokeObjectURL(sourceUrl);
  sourceUrl = URL.createObjectURL(file);
  video.src = sourceUrl;
}

function setGeoNote() {
  const geo = $("geo-note");
  if (job.width === 1080 && job.height === 1920) {
    geo.textContent = `${job.width}×${job.height} — perfect 9:16, passthrough.`;
  } else {
    geo.textContent = `${job.width}×${job.height} — will be blur-padded to 1080×1920.`;
  }
}

function renderTranscript() {
  const box = $("transcript");
  box.innerHTML = "";
  job.words.forEach((w, i) => {
    const span = document.createElement("span");
    span.className = "word";
    span.contentEditable = "true";
    span.textContent = w.text;
    span.dataset.index = i;
    box.appendChild(span);
    box.appendChild(document.createTextNode(" "));
  });
}

function collectWords() {
  // Preserve locked timing (D4); only text is editable.
  const spans = $("transcript").querySelectorAll(".word");
  return Array.from(spans).map((span) => {
    const w = job.words[Number(span.dataset.index)];
    return { text: span.textContent.trim(), start: w.start, end: w.end };
  });
}

$("music-volume").addEventListener("input", (e) => {
  $("vol-label").textContent = Number(e.target.value).toFixed(2);
});

// --- render -----------------------------------------------------------------

$("render-btn").addEventListener("click", async () => {
  const status = $("render-status");
  status.className = "status";
  operationBusy = true;
  updateCacheControls();

  const mode = $("music-mode").value;
  const musicFile = $("music-input").files[0];

  try {
    let filename = null;
    if (mode !== "none" && musicFile) {
      status.textContent = "Uploading music…";
      const form = new FormData();
      form.append("file", musicFile);
      const mres = await fetch(`/api/jobs/${job.id}/music`, { method: "POST", body: form });
      const mdata = await mres.json();
      if (!mres.ok) throw new Error(mdata.detail || "music upload failed");
      filename = mdata.filename;
    }

    status.textContent = "Rendering… (burning captions, header & audio)";
    const payload = {
      words: collectWords(),
      header: $("header-input").value,
      captions_on: $("captions-toggle").checked,
      caption_style: $("caption-style").value,
      header_style: $("header-style").value,
      music: { mode: musicFile ? mode : "none", volume: Number($("music-volume").value), filename },
    };
    const res = await fetch(`/api/jobs/${job.id}/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "render failed");

    status.textContent = "";
    await showResult();
  } catch (err) {
    status.className = "status error";
    status.textContent = err.message;
  } finally {
    operationBusy = false;
    updateCacheControls();
    await refreshCacheInfo();
  }
});

// --- result -----------------------------------------------------------------

async function showResult() {
  $("review-panel").classList.add("hidden");
  $("result-panel").classList.remove("hidden");

  const status = $("result-status");
  status.className = "status";
  status.textContent = "Loading rendered clip…";

  const resp = await fetch(`/api/jobs/${job.id}/output`);
  if (!resp.ok) throw new Error(`could not load output (${resp.status})`);
  const blob = await resp.blob();

  // Fresh <video> element + blob URL, same pattern as the source preview.
  const v = document.createElement("video");
  v.id = "output-video";
  v.controls = true;
  v.playsInline = true;
  v.onerror = () => {
    status.className = "status error";
    status.textContent = `Playback failed (${mediaErrText(v)}). The file downloaded fine — use Download to save it.`;
  };
  $("output-video").replaceWith(v);

  if (outputUrl) URL.revokeObjectURL(outputUrl);
  outputUrl = URL.createObjectURL(blob);
  v.src = outputUrl;

  const dl = $("download-link");
  dl.href = outputUrl;
  dl.download = `riceclipper-${job.id}.mp4`;
  status.textContent = "";
}

$("restart-btn").addEventListener("click", () => {
  resetClientState();
});

function resetClientState() {
  // Stop playback — hiding the panel doesn't pause the media element.
  ["source-video", "output-video"].forEach((id) => {
    const v = $(id);
    if (v) {
      v.pause();
      v.removeAttribute("src");
      v.load();
    }
  });
  if (sourceUrl) URL.revokeObjectURL(sourceUrl);
  if (outputUrl) URL.revokeObjectURL(outputUrl);
  sourceUrl = outputUrl = null;
  job = null;
  selectedFile = null;
  operationBusy = false;
  $("result-panel").classList.add("hidden");
  $("review-panel").classList.add("hidden");
  $("upload-panel").classList.remove("hidden");
  $("file-input").value = "";
  $("music-input").value = "";
  $("header-input").value = "";
  $("header-style").value = "plain";
  $("caption-style").value = "classic";
  $("captions-toggle").checked = true;
  $("music-mode").value = "none";
  $("music-volume").value = "0.35";
  $("vol-label").textContent = "0.35";
  $("transcript").innerHTML = "";
  $("upload-status").textContent = "";
  $("preview-status").textContent = "";
  $("render-status").textContent = "";
  $("result-status").textContent = "";
  updateCacheControls();
}

$("clear-cache-btn").addEventListener("click", async () => {
  if (!window.confirm("Clear all server-side media cache files? Your original browser file will not be affected.")) {
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

    resetClientState();
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
