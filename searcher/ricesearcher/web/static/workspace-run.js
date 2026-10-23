"use strict";
// A request is sent once. Every recovery path only observes that exact key.
let workspaceRun = null;
let workspaceBusy = false;
let workspaceTimer = null;
let workspaceGeneration = 0;
let workspaceRefreshed = "";
const WORKSPACE_ATTEMPT = "ricesearcher.processing.attempt";
function wsNode(tag, text, attrs = {}) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) if (!/^on/i.test(k)) n.setAttribute(k, v);
  if (text != null) n.textContent = text;
  return n;
}
function wsRenderRun(message = "") {
  const strip = document.getElementById("batchStrip");
  if (!strip) return;
  const run = workspaceRun;
  strip.hidden = !run && !message;
  strip.replaceChildren();
  if (message) strip.append(wsNode("p", message, {class: "operational"}));
  if (!run) return;
  const done = run.items.filter((i) => i.state === "complete").length;
  strip.append(wsNode("strong", "Processing · " + run.profile + " · " + done + " / " + run.items.length + " · " + run.stage));
  const details = wsNode("details");
  details.append(wsNode("summary", "Batch details · " + run.status));
  for (const item of run.items) details.append(wsNode("p", item.request + " · " + item.state + " · " + item.stage + (item.error ? ": " + item.error : ""), {class: "operational"}));
  if (run.detail) details.append(wsNode("p", run.detail, {class: "error"}));
  if (run.duplicate_error) details.append(wsNode("p", "Duplicate check failed: " + run.duplicate_error, {class: "error"}));
  strip.append(details);
  if (run.status === "active") {
    const stop = wsNode("button", run.stop_requested ? "Stopping after current source" : "Stop after current source");
    stop.disabled = run.stop_requested;
    stop.addEventListener("click", async () => {
      stop.disabled = true;
      try { workspaceRun = await wsRequest("api/runs/" + run.id + "/stop", {method: "POST"}); wsRenderRun(); }
      catch (err) { wsRenderRun(err.message); }
    });
    strip.append(stop);
  } else if (run.items.some((i) => i.state === "failed") || run.duplicate_error) {
    const retry = wsNode("button", "Retry failed work");
    retry.addEventListener("click", () => wsSubmit("api/runs/" + run.id + "/retry", {}));
    strip.append(retry);
  }
}
async function wsRequest(url, init) {
  const res = await fetch(url, init);
  const data = await res.json();
  if (!res.ok) { const error = new Error(typeof data.detail === "string" ? data.detail : "HTTP " + res.status); error.status = res.status; throw error; }
  return data;
}
function wsAttempt() { return crypto.randomUUID(); }
async function wsSubmit(url, data, files = null) {
  if (workspaceBusy || (workspaceRun && workspaceRun.status === "active")) return;
  workspaceBusy = true;
  const key = wsAttempt();
  try { localStorage.setItem(WORKSPACE_ATTEMPT, key); } catch (_) { /* optional persistence */ }
  let init;
  if (files) {
    const form = new FormData();
    form.append("attempt_key", key);
    form.append("settings_json", JSON.stringify(data));
    for (const file of files) form.append("files", file);
    init = {method: "POST", body: form};
  } else init = {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({...data, attempt_key: key})};
  wsRenderRun("Starting batch…");
  try {
    workspaceRun = await wsRequest(url, init);
    wsRenderRun();
    await wsObserve(key);
  } catch (err) {
    if (err.status >= 400 && err.status < 500) {
      try { localStorage.removeItem(WORKSPACE_ATTEMPT); } catch (_) { /* optional persistence */ }
      wsRenderRun("Batch not started: " + err.message);
      return;
    }
    // A lost response must never replay a paid operation. Recovery reads only.
    wsRenderRun("Start response unavailable: " + err.message + ". Checking this attempt only.");
    await wsObserve(key);
  } finally { workspaceBusy = false; }
}
async function wsObserve(key, generation = ++workspaceGeneration) {
  clearTimeout(workspaceTimer);
  try {
    const res = await fetch("api/runs/" + encodeURIComponent(key));
    if (generation !== workspaceGeneration) return;
    if (res.status === 404) {
      workspaceRun = null;
      wsRenderRun("Run unavailable after expiry or restart. Nothing was replayed. Inspect retained sources and explicitly start unfinished work.");
      return;
    }
    if (!res.ok) throw new Error("HTTP " + res.status);
    const run = await res.json();
    if (generation !== workspaceGeneration) return;
    workspaceRun = run;
    wsRenderRun();
    if (run.status !== "active") {
      if (workspaceRefreshed !== run.id && typeof wsBatchFinished === "function") { workspaceRefreshed = run.id; wsBatchFinished(); }
      return;
    }
  } catch (err) { wsRenderRun("Connection lost. Last known progress retained; observing only. " + err.message); }
  if (generation === workspaceGeneration) workspaceTimer = setTimeout(() => wsObserve(key, generation), 700);
}
async function wsDiscover() {
  let remembered;
  try { remembered = localStorage.getItem(WORKSPACE_ATTEMPT); } catch (_) { /* unavailable */ }
  try {
    const data = await wsRequest("api/runs/current");
    if (data.run) { workspaceRun = data.run; wsRenderRun(); if (data.run.status === "active") await wsObserve(data.run.id); }
    else if (remembered) await wsObserve(remembered);
  } catch (err) { wsRenderRun("Processing progress unavailable: " + err.message); }
}
wsDiscover();
