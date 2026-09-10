"use strict";
// RiceSearcher Media management. Lists every stored source (url + cached media)
// and lets the maintainer full-purge one source or the whole cache. Deletes are
// LOCAL only — no external surface is ever contacted. Content strings go through
// textContent (via el()); el() refuses on* attributes, and source URLs are
// rendered as text (never an href) so a hostile ref can't become javascript:.

const listEl = document.getElementById("list");
const countEl = document.getElementById("count");
const statusEl = document.getElementById("status");

document.getElementById("clearAllBtn").addEventListener("click", confirmClearAll);

function setStatusMsg(text, isError) {
  statusEl.textContent = text || "";
  statusEl.classList.toggle("error", !!isError);
}

async function load() {
  let sources;
  try {
    const res = await fetch("/api/sources");
    if (!res.ok) throw new Error("HTTP " + res.status);
    sources = await res.json();
  } catch (err) {
    listEl.replaceChildren(el("div", { class: "empty" }, "Failed to load media: " + err.message));
    countEl.textContent = "";
    return;
  }
  countEl.textContent = sources.length + (sources.length === 1 ? " source" : " sources");
  if (!sources.length) {
    listEl.replaceChildren(
      el("div", { class: "empty" }, [
        el("p", {}, "No stored media."),
        el("p", {}, [document.createTextNode("Pull a source with "), el("code", {}, "ricesearcher pull <url>"), document.createTextNode(" first.")]),
      ])
    );
    return;
  }
  listEl.replaceChildren(...sources.map(row));
}

function row(s) {
  const r = el("article", { class: "media-row", "data-id": s.id });

  const main = el("div", { class: "media-main" });
  main.append(el("div", { class: "media-title" }, s.title || "(untitled)"));
  main.append(el("div", { class: "media-ref" }, s.ref));  // text, never a link
  const facts = [
    s.slice_count + (s.slice_count === 1 ? " slice" : " slices"),
    s.size_bytes == null ? "media missing" : fmtBytes(s.size_bytes),
  ];
  if (s.duration_s) facts.push(fmtDur(s.duration_s));
  if (s.acquired_at) facts.push(s.acquired_at.slice(0, 10));
  main.append(el("div", { class: "media-facts" }, facts.join("  ·  ")));

  const msg = el("div", { class: "card-msg", role: "status", "aria-live": "polite" });
  const actions = el("div", { class: "media-actions" });

  const delBtn = el("button", { class: "danger", type: "button" }, "Delete");
  delBtn.addEventListener("click", () => armDelete(r, actions, delBtn, s, msg));
  actions.append(delBtn);

  r.append(main, actions, msg);
  return r;
}

// Two-step confirm: a destructive full-purge (row + transcript + slices + file)
// should never fire on a single stray click.
function armDelete(r, actions, delBtn, s, msg) {
  const confirmBtn = el("button", { class: "danger", type: "button" }, "Confirm delete");
  const cancelBtn = el("button", { type: "button" }, "Cancel");
  confirmBtn.addEventListener("click", () => doDelete(r, s, msg, confirmBtn, cancelBtn));
  cancelBtn.addEventListener("click", () => actions.replaceChildren(delBtn));
  actions.replaceChildren(confirmBtn, cancelBtn);
  confirmBtn.focus();
}

async function doDelete(r, s, msg, confirmBtn, cancelBtn) {
  confirmBtn.disabled = true;
  cancelBtn.disabled = true;
  cardMsg(msg, "deleting…", false);
  try {
    const res = await fetch("/api/sources/" + encodeURIComponent(s.id) + "/delete", { method: "POST" });
    if (!res.ok) {
      confirmBtn.disabled = false;
      cancelBtn.disabled = false;
      cardMsg(msg, "delete failed (" + res.status + ")", true);
      return;
    }
    r.remove();
    await load();  // refresh the count and any shared-media state
    setStatusMsg("deleted " + (s.title || s.id.slice(0, 8)), false);
  } catch (err) {
    confirmBtn.disabled = false;
    cancelBtn.disabled = false;
    cardMsg(msg, "delete failed: " + err.message, true);
  }
}

function confirmClearAll() {
  const btn = document.getElementById("clearAllBtn");
  if (btn.dataset.armed === "1") return;
  btn.dataset.armed = "1";
  btn.textContent = "Click again to purge everything";
  setStatusMsg("clearing the cache deletes every source, transcript, and scored slice — click again to confirm.", true);
  const disarm = () => {
    btn.dataset.armed = "";
    btn.textContent = "Clear entire cache…";
    btn.removeEventListener("click", clearAll);
  };
  // second click within 5s purges; otherwise disarm
  btn.addEventListener("click", clearAll, { once: true });
  setTimeout(disarm, 5000);
}

async function clearAll() {
  const btn = document.getElementById("clearAllBtn");
  btn.dataset.armed = "";
  btn.textContent = "Clear entire cache…";
  btn.disabled = true;
  setStatusMsg("purging cache…", false);
  try {
    const res = await fetch("/api/cache/clear", { method: "POST" });
    const d = await res.json();
    if (!res.ok) { setStatusMsg("clear failed: " + (d.detail || res.status), true); return; }
    setStatusMsg("purged " + d.sources_deleted + " source(s), removed " + d.files_removed + " file(s)", false);
    await load();
  } catch (err) {
    setStatusMsg("clear failed: " + err.message, true);
  } finally {
    btn.disabled = false;
  }
}

function cardMsg(node, text, isError) {
  node.textContent = text || "";
  node.classList.toggle("error", !!isError);
}

function fmtBytes(n) {
  if (n < 1024) return n + " B";
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return v.toFixed(v < 10 ? 1 : 0) + " " + units[i];
}

function fmtDur(s) {
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  return m + ":" + String(sec).padStart(2, "0");
}

// Tiny DOM helper (mirrors app.js): children may be a string, node, or array;
// attribute names starting with "on" are refused so a stray value can't become
// an event handler.
function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const k in (attrs || {})) {
    if (/^on/i.test(k)) continue;
    node.setAttribute(k, attrs[k]);
  }
  if (children != null) {
    for (const ch of Array.isArray(children) ? children : [children]) {
      node.append(typeof ch === "string" ? document.createTextNode(ch) : ch);
    }
  }
  return node;
}

load();
