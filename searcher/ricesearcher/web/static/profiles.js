"use strict";
// RiceSearcher Profiles. Lists every saved profile with its row counts. Click a
// row to make it the active profile and go to the review page. Content strings go
// through textContent (via el()); el() refuses on* attributes. No create/edit/
// delete here — profiles are files, edited on disk (ADR-002 boundary).

const listEl = document.getElementById("list");
const countEl = document.getElementById("count");
const statusEl = document.getElementById("status");

// Must match the review page's persisted key so a pick here scopes the review UI.
const PROFILE_KEY = "ricesearcher.profile";

function setStatusMsg(text, isError) {
  statusEl.textContent = text || "";
  statusEl.classList.toggle("error", !!isError);
}

async function load() {
  let profiles;
  try {
    const res = await fetch("/api/profiles");
    if (!res.ok) throw new Error("HTTP " + res.status);
    profiles = await res.json();
  } catch (err) {
    listEl.replaceChildren(el("div", { class: "empty" }, "Failed to load profiles: " + err.message));
    countEl.textContent = "";
    return;
  }
  countEl.textContent = profiles.length + (profiles.length === 1 ? " profile" : " profiles");
  if (!profiles.length) {
    listEl.replaceChildren(
      el("div", { class: "empty" }, [
        el("p", {}, "No profiles found."),
        el("p", {}, [document.createTextNode("Add a JSON file under the "), el("code", {}, "profiles"), document.createTextNode(" directory.")]),
      ])
    );
    return;
  }
  listEl.replaceChildren(...profiles.map(row));
}

// Each row is a <button> so keyboard activation (Enter/Space) and focus come free.
function row(p) {
  const b = el("button", { type: "button", class: "profile-row", "data-id": p.id });
  const main = el("div", { class: "profile-main" });
  main.append(el("div", { class: "profile-name" }, p.name));
  main.append(el("div", { class: "profile-id" }, p.id + "  ·  v" + p.version));
  const facts = [
    p.sources + (p.sources === 1 ? " source" : " sources"),
    p.candidates + " candidate" + (p.candidates === 1 ? "" : "s"),
    p.selected + " selected",
  ];
  main.append(el("div", { class: "profile-facts" }, facts.join("  ·  ")));
  b.append(main);
  b.addEventListener("click", () => {
    localStorage.setItem(PROFILE_KEY, p.id);
    window.location.assign("/");
  });
  return b;
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
