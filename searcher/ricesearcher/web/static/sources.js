"use strict";
const wsProfile = document.getElementById("workspaceProfile");
const wsInspector = document.getElementById("sourceInspector");
const wsWorkspace = document.getElementById("sourceWorkspace");
const wsSelected = new Set();
let wsSources = [];
let wsOpened = null;
let wsDetailGeneration = 0;
let wsFiles = [];
function wsSourcesLoaded(sources) {
  wsSources = sources;
  for (const id of wsSelected) if (!sources.some((s) => s.id === id)) wsSelected.delete(id);
  if (wsOpened && !sources.some((s) => s.id === wsOpened.id)) wsBack();
}
function wsSourceControls(source) {
  const controls = wsNode("div", null, {class: "source-choices"});
  const check = wsNode("input", null, {type: "checkbox", "aria-label": "Choose " + (source.title || source.id)});
  check.checked = wsSelected.has(source.id);
  check.addEventListener("change", () => { if (check.checked) wsSelected.add(source.id); else wsSelected.delete(source.id); });
  const label = wsNode("label", null, {class: "check-label"});
  label.append(check, document.createTextNode("Choose"));
  const open = wsNode("button", "Open source");
  open.addEventListener("click", () => wsOpen(source));
  controls.append(label, open);
  return controls;
}
function wsSettings(score = false, duplicates = false) {
  return {profile: wsProfile.value, score, duplicates_only: duplicates,
    offline: document.getElementById("offlineScore").checked,
    model: document.getElementById("scoringModel").value.trim() || null,
    transcription_model: document.getElementById("transcriptionModel").value,
    threshold: Number(document.getElementById("duplicateThreshold").value)};
}
async function wsImport(score) {
  const requests = document.getElementById("sourceRequests").value.split("\n").map((s) => s.trim()).filter(Boolean);
  if (wsFiles.length && requests.length) { wsRenderRun("Add files and URL/path entries as separate batches."); return; }
  await wsSubmit(wsFiles.length ? "api/runs/files" : "api/runs", wsFiles.length ? wsSettings(score) : {...wsSettings(score), requests}, wsFiles.length ? wsFiles : null);
}
function wsBack() {
  wsOpened = null; ++wsDetailGeneration;
  wsWorkspace.classList.remove("detail-open");
  wsInspector.replaceChildren(wsNode("p", "Open a source to inspect its media and transcript.", {class: "empty"}));
}
async function wsOpen(source) {
  const generation = ++wsDetailGeneration;
  const profile = wsProfile.value;
  wsOpened = source;
  wsWorkspace.classList.add("detail-open");
  wsInspector.replaceChildren(wsNode("p", "Loading source…"));
  try {
    const [detail, slices] = await Promise.all([
      wsRequest("api/sources/" + encodeURIComponent(source.id)),
      wsRequest("api/slices?profile=" + encodeURIComponent(profile) + "&source=" + encodeURIComponent(source.id)),
    ]);
    if (generation !== wsDetailGeneration || profile !== wsProfile.value) return;
    const back = wsNode("button", "← Back to sources", {class: "source-back"});
    back.addEventListener("click", wsBack);
    const title = wsNode("h2", source.title || "Untitled source", {class: "title"});
    const video = wsNode("video", null, {id: "fullSourceVideo", controls: "", preload: "metadata"});
    const error = wsNode("p", "", {class: "operational error", role: "status"});
    if (source.media_url && source.size_bytes != null) video.src = source.media_url;
    else error.textContent = "Cached media is missing. Add the original source again to restore playback.";
    video.addEventListener("error", () => { error.textContent = "This source cannot play in this browser. Check the cached file or import a browser-compatible encoding; automatic conversion is unavailable."; });
    const player = wsNode("div", null, {class: "source-player"}); player.append(video);
    const form = wsNode("div", null, {class: "segment-form"});
    const start = wsNode("input", null, {id: "manualStart", type: "number", min: "0", step: "any", value: "0", "aria-label": "Manual start seconds"});
    const end = wsNode("input", null, {id: "manualEnd", type: "number", min: "0", step: "any", value: String(source.duration_s || 0), "aria-label": "Manual end seconds"});
    for (const [name, input] of [["Start (seconds)", start], ["End (seconds)", end]]) {
      const label = wsNode("label", name); label.append(input); form.append(label);
    }
    for (const [name, input] of [["Mark start", start], ["Mark end", end]]) {
      const b = wsNode("button", name); b.addEventListener("click", () => { input.value = String(video.currentTime); }); form.append(b);
    }
    let intervalEnd = null;
    video.addEventListener("timeupdate", () => { if (intervalEnd !== null && video.currentTime >= intervalEnd) { video.pause(); intervalEnd = null; } });
    const msg = wsNode("p", "", {class: "operational", role: "status"});
    function interval() {
      const a = Number(start.value), b = Number(end.value);
      if (!start.value || !end.value || !Number.isFinite(a) || !Number.isFinite(b) || a < 0 || a >= b || (Number.isFinite(video.duration) && b > video.duration)) throw new Error("Choose 0 ≤ start < end within the source.");
      return [a, b];
    }
    const preview = wsNode("button", "Preview interval");
    preview.addEventListener("click", async () => { try { const [a, b] = interval(); video.currentTime = a; intervalEnd = b; await video.play(); msg.textContent = "Previewing interval"; } catch (err) { msg.textContent = err.message; } });
    const save = wsNode("button", "Save manual segment", {class: "primary"});
    save.addEventListener("click", async () => {
      if (save.disabled) return;
      try {
        const [target_in, target_out] = interval(); save.disabled = true;
        const result = await wsRequest("api/sources/" + encodeURIComponent(source.id) + "/manual", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({profile, target_in, target_out})});
        if (generation !== wsDetailGeneration) return;
        msg.replaceChildren(document.createTextNode("Saved manual segment · reviewed, unselected. "), wsNode("a", "View in Review", {href: "./?source=" + encodeURIComponent(result.source_id) + "&kind=manual&status=reviewed&profile=" + encodeURIComponent(profile) + "&slice=" + encodeURIComponent(result.id)}));
      } catch (err) { msg.textContent = err.message; } finally { save.disabled = false; }
    });
    form.append(preview, save);
    const transcript = wsNode("div", detail.transcript || "No transcript text. Manual segments are still available.", {class: "source-transcript"});
    const summary = wsSegmentSummary(slices, profile, source);
    wsInspector.replaceChildren(back, title, player, error, form, msg, wsNode("h3", "Full transcript · " + detail.transcription_state, {id: "sourceTranscriptHeading"}), transcript, summary);
  } catch (err) {
    if (generation === wsDetailGeneration) {
      const back = wsNode("button", "← Back to sources", {class: "source-back"});
      back.addEventListener("click", wsBack);
      wsInspector.replaceChildren(back, wsNode("p", err.message, {class: "error"}));
    }
  }
}
function wsSegmentSummary(slices, profile, source) {
  const summary = wsNode("div", null, {class: "source-segments"});
  summary.append(wsNode("h3", "Segments · " + profile));
  for (const slice of slices) summary.append(wsNode("p", (slice.origin === "manual" ? "Manual / unscored" : "Score " + slice.score.toFixed(2)) + " · " + slice.target_in + "–" + slice.target_out + "s · " + slice.status, {class: "operational"}));
  if (!slices.length) summary.append(wsNode("p", "No segments under this profile.", {class: "operational"}));
  summary.append(wsNode("a", "Review this source", {href: "./?source=" + encodeURIComponent(source.id) + "&status="}));
  return summary;
}
async function wsBatchFinished() {
  await load();
  if (!wsOpened) return;
  const generation = wsDetailGeneration, profile = wsProfile.value, id = wsOpened.id;
  try {
    const [detail, slices] = await Promise.all([
      wsRequest("api/sources/" + encodeURIComponent(id)),
      wsRequest("api/slices?profile=" + encodeURIComponent(profile) + "&source=" + encodeURIComponent(id)),
    ]);
    if (generation !== wsDetailGeneration || profile !== wsProfile.value || wsOpened?.id !== id) return;
    const transcript = wsInspector.querySelector(".source-transcript");
    if (transcript) transcript.textContent = detail.transcript || "No transcript text. Manual segments are still available.";
    const heading = document.getElementById("sourceTranscriptHeading");
    if (heading) heading.textContent = "Full transcript · " + detail.transcription_state;
    wsInspector.querySelector(".source-segments")?.replaceWith(wsSegmentSummary(slices, profile, wsOpened));
  } catch (err) { wsRenderRun("Source results could not refresh: " + err.message); }
}
wsProfile.addEventListener("change", () => { localStorage.setItem("ricesearcher.profile", wsProfile.value); if (wsOpened) wsOpen(wsOpened); });
document.getElementById("addOnly").addEventListener("click", () => wsImport(false));
document.getElementById("addScore").addEventListener("click", () => wsImport(true));
document.getElementById("scoreSources").addEventListener("click", () => wsSubmit("api/runs", {...wsSettings(true), source_ids: [...wsSelected]}));
document.getElementById("checkDuplicates").addEventListener("click", () => wsSubmit("api/runs", wsSettings(false, true)));
document.getElementById("clearSourceFiles").addEventListener("click", () => { wsFiles = []; document.getElementById("sourceFiles").value = ""; });
document.getElementById("sourceFiles").addEventListener("change", (event) => { wsFiles = [...event.target.files]; });
const drop = document.getElementById("fileDrop");
drop.addEventListener("dragover", (event) => event.preventDefault());
drop.addEventListener("drop", (event) => { event.preventDefault(); wsFiles = [...event.dataTransfer.files]; document.getElementById("sourceFiles").files = event.dataTransfer.files; });
(async () => {
  try {
    const [profiles, settings] = await Promise.all([wsRequest("api/profiles"), wsRequest("api/settings")]);
    wsProfile.replaceChildren(...profiles.map((p) => wsNode("option", p.name + " (" + p.id + ")", {value: p.id})));
    document.getElementById("transcriptionModel").value = settings.transcription_model;
    document.getElementById("duplicateThreshold").value = settings.threshold;
    const saved = localStorage.getItem("ricesearcher.profile");
    if (profiles.some((p) => p.id === saved)) wsProfile.value = saved;
    document.getElementById("credentialsNote").textContent = settings.scoring_available ? "Scoring uses the configured API model, or choose offline scoring." : "Scoring credentials are missing. Add only and manual segments work; choose offline scoring to score without an API key.";
  } catch (err) { wsRenderRun(err.message); }
})();
