// RiceSuite shell: tab switching and the read-only home view.
"use strict";

const TABS = ["home", "search", "clip", "post"];
const REFRESH_MS = 5000;

// Every pillar page loads once, up front, and stays alive: batches move from
// Search into Clip, and from Clip to Post's inbox, automatically only while
// the Clip page is open (ADR-001 Q12); Post keeps its inbox count current
// (Post itself pulls only on a click); and switching tabs must never
// reload a page mid-task (e.g. Post during a run).
function loadPillarPages() {
  for (const frame of document.querySelectorAll("iframe[data-src]")) {
    if (!frame.getAttribute("src")) frame.setAttribute("src", frame.dataset.src);
  }
}

function showTab(name) {
  if (!TABS.includes(name)) name = "home";
  for (const view of document.querySelectorAll(".view")) {
    view.classList.toggle("active", view.dataset.view === name);
  }
  for (const link of document.querySelectorAll("[data-tab]")) {
    if (link.dataset.tab === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (name === "home") refreshHome();
}

function setCount(field, value, unavailable = false) {
  const el = document.querySelector(`[data-field="${field}"]`);
  el.textContent = value;
  el.classList.toggle("unavailable", unavailable);
}

function plural(count, singular, pluralForm = `${singular}s`) {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

function setBatches(field, batches) {
  const list = document.querySelector(`[data-list="${field}"]`);
  list.replaceChildren();
  if (!Array.isArray(batches) || batches.length === 0) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = batches ? "No batches waiting" : "Unavailable";
    list.append(item);
    return;
  }
  for (const batch of batches) {
    const item = document.createElement("li");
    const name = batch.batch_id || "Unnamed batch";
    const count = batch.clip_count;
    item.textContent = count == null ? name : `${name} · ${plural(count, "clip")}`;
    list.append(item);
  }
}

function setClipBatches(incoming, open) {
  const list = document.querySelector('[data-list="clip"]');
  list.replaceChildren();
  if (!Array.isArray(incoming) || !Array.isArray(open)) {
    setCount("clip-waiting", "Clip is not answering", true);
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = "Unavailable";
    list.append(item);
    return;
  }
  const batches = [
    ...incoming.map(batch => ({ batch, label: "Incoming" })),
    ...open.map(batch => ({ batch, label: "In Clip" }))
  ];
  setCount("clip-waiting", plural(batches.length, "batch", "batches"));
  if (!batches.length) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = "No batches waiting";
    list.append(item);
  }
  for (const { batch, label } of batches) {
    const item = document.createElement("li");
    const name = batch.batch_id || "Unnamed batch";
    const count = batch.clip_count;
    item.textContent = `${label} · ${name}${count == null ? "" : ` · ${plural(count, "clip")}`}`;
    list.append(item);
  }
}

function setSchedule(batches) {
  const list = document.querySelector('[data-list="scheduled"]');
  list.replaceChildren();
  if (!Array.isArray(batches) || batches.length === 0) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = batches ? "No scheduled batches" : "Post is not answering";
    list.append(item);
    return;
  }
  for (const batch of batches) {
    const item = document.createElement("li");
    const information = document.createElement("div");
    const id = document.createElement("strong");
    id.textContent = batch.id || "Unnamed batch";
    information.append(id);
    const instant = batch.fire_time ? new Date(batch.fire_time) : null;
    const hasTime = instant && !Number.isNaN(instant.getTime());
    const when = document.createElement(hasTime ? "time" : "span");
    when.className = "time-text";
    if (hasTime) {
      when.dateTime = batch.fire_time;
      when.textContent = new Intl.DateTimeFormat(undefined, {
        month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
      }).format(instant);
    } else {
      when.textContent = "Time unavailable";
    }
    information.append(when);
    const status = document.createElement("span");
    status.className = "status";
    status.textContent = batch.status || "Unknown";
    item.append(information, status);
    list.append(item);
  }
}

function updateHome(data) {
  const search = data.search;
  if (search && Number.isFinite(search.candidates) && Number.isFinite(search.selected)) {
    setCount("search", search.candidates);
    setCount("selected", search.selected);
    setCount("search-waiting", plural(search.candidates, "candidate"));
    setCount("search-detail", `${search.selected} selected`);
  } else {
    setCount("search", "Unavailable", true);
    setCount("selected", "Unavailable", true);
    setCount("search-waiting", "Search is not answering", true);
    setCount("search-detail", "—", true);
  }

  setClipBatches(data.to_clipper, data.clip_open);
  const postBatches = data.to_poster;
  setCount("to_poster", Array.isArray(postBatches) ? plural(postBatches.length, "batch", "batches") : "Post is not answering", !Array.isArray(postBatches));
  setBatches("to_poster", postBatches);
  const handoff = data.to_clipper;
  setCount("handoff", Array.isArray(handoff) && Array.isArray(postBatches) ? handoff.length + postBatches.length : "Unavailable", !Array.isArray(handoff) || !Array.isArray(postBatches));

  const scheduled = data.scheduled;
  setCount("scheduled", Array.isArray(scheduled) ? scheduled.length : "Unavailable", !Array.isArray(scheduled));
  setSchedule(scheduled);
}

async function refreshHome() {
  const freshness = document.querySelector("[data-freshness]");
  try {
    const response = await fetch("api/suite/home");
    if (!response.ok) throw new Error("Home unavailable");
    updateHome(await response.json());
    freshness.textContent = `Updated ${new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(new Date())}`;
  } catch {
    updateHome({});
    freshness.textContent = "Unable to refresh";
  }
}

async function refreshStatus() {
  let children = {};
  try {
    const response = await fetch("api/suite/status");
    if (!response.ok) throw new Error("Status unavailable");
    const data = await response.json();
    children = (data.state && data.state.children) || {};
  } catch {
    // Unknown state is shown explicitly, rather than leaving stale health data.
  }
  for (const dot of document.querySelectorAll("[data-status]")) {
    const child = children[dot.dataset.status];
    const state = child && child.state;
    dot.classList.toggle("ok", state === "running");
    dot.classList.toggle("bad", Boolean(state && state !== "running"));
    dot.title = child ? `${state}, ${child.restarts} restart(s)` : "unknown";
  }
  for (const label of document.querySelectorAll("[data-health]")) {
    const state = children[label.dataset.health]?.state;
    label.textContent = state ? state.charAt(0).toUpperCase() + state.slice(1) : "Unknown";
  }
}

window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
loadPillarPages();
showTab(location.hash.slice(1));
refreshStatus();
setInterval(() => {
  refreshStatus();
  if (document.querySelector('[data-view="home"]').classList.contains("active")) refreshHome();
}, REFRESH_MS);
