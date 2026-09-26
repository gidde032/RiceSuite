// RiceSuite shell: tab switching and the home view. Read-only — it never
// calls a pillar's API that changes anything.
"use strict";

const TABS = ["home", "search", "clip", "post"];
const REFRESH_MS = 5000;

function showTab(name) {
  if (!TABS.includes(name)) name = "home";
  for (const view of document.querySelectorAll(".view")) {
    const active = view.dataset.view === name;
    view.classList.toggle("active", active);
    const frame = view.querySelector("iframe");
    // Load a pillar page on first visit, then keep it alive: switching tabs
    // must never reload a page mid-task (e.g. Post during a run).
    if (active && frame && !frame.getAttribute("src")) frame.setAttribute("src", frame.dataset.src);
  }
  for (const link of document.querySelectorAll("[data-tab]")) {
    if (link.dataset.tab === name) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (name === "home") refreshHome();
}

function setCount(field, text, unavailable) {
  const el = document.querySelector(`[data-field="${field}"]`);
  el.textContent = text;
  el.classList.toggle("unavailable", Boolean(unavailable));
}

function setList(field, batches) {
  const list = document.querySelector(`[data-list="${field}"]`);
  list.replaceChildren();
  for (const b of batches || []) {
    const item = document.createElement("li");
    item.textContent = `${b.batch_id} · ${b.clips} clip${b.clips === 1 ? "" : "s"}`;
    list.append(item);
  }
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

async function refreshHome() {
  let data;
  try {
    data = await (await fetch("api/suite/home")).json();
  } catch {
    return;
  }
  if (data.search) {
    setCount("search", `${plural(data.search.candidates, "candidate")} · ${data.search.selected} selected`);
  } else {
    setCount("search", "Search is not answering", true);
  }
  for (const field of ["to_clipper", "to_poster"]) {
    if (Array.isArray(data[field])) setCount(field, plural(data[field].length, "batch").replace("batchs", "batches"));
    else setCount(field, "handoff folder unavailable", true);
    setList(field, data[field]);
  }
  if (Array.isArray(data.scheduled)) {
    setCount("scheduled", `${plural(data.scheduled.length, "scheduled batch").replace("batchs", "batches")} in Post's queue`);
  } else {
    setCount("scheduled", "Post is not answering", true);
  }
}

async function refreshStatus() {
  let data;
  try {
    data = await (await fetch("api/suite/status")).json();
  } catch {
    return;
  }
  const children = (data.state && data.state.children) || {};
  for (const dot of document.querySelectorAll("[data-status]")) {
    const child = children[dot.dataset.status];
    dot.classList.toggle("ok", Boolean(child && child.state === "running"));
    dot.classList.toggle("bad", Boolean(child && child.state !== "running"));
    dot.title = child ? `${child.state}, ${child.restarts} restart(s)` : "unknown";
  }
}

window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
showTab(location.hash.slice(1));
refreshStatus();
setInterval(() => {
  refreshStatus();
  if (document.querySelector('[data-view="home"]').classList.contains("active")) refreshHome();
}, REFRESH_MS);
