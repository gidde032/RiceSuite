"use strict";
// RiceSearcher Review UI. Vanilla JS: fetch scored slices, render Slate cards,
// preview the moment's video window, tighten in/out, and Select / Reject.
// Content strings go through textContent (via el()), never innerHTML; el() also
// refuses on* attributes, so no server string can become an event handler.

const listEl = document.getElementById("list");
const countEl = document.getElementById("count");
const statusEl = document.getElementById("status");
const filterEl = document.getElementById("statusFilter");
const profileEl = document.getElementById("profileSelect");
const handoffBtn = document.getElementById("handoffBtn");

// Which profile the review UI is scoped to. Persisted so a reload keeps it.
const PROFILE_KEY = "ricesearcher.profile";
let profiles = [];  // last /api/profiles payload, for the handoff-button count
let profileRequest = 0;
let sliceRequest = 0;
let pendingMutations = 0;
let handoffPending = false;

profileEl.addEventListener("change", () => {
  localStorage.setItem(PROFILE_KEY, profileEl.value);
  updateHandoffLabel();
  load();
});
filterEl.addEventListener("change", () => load());
document.getElementById("handoffBtn").addEventListener("click", handoff);

// Fill the profile select from /api/profiles and restore the saved choice.
async function loadProfiles() {
  const request = ++profileRequest;
  const previousProfile = profileEl.value;
  let nextProfiles;
  try {
    const res = await fetch("/api/profiles");
    if (!res.ok) throw new Error("HTTP " + res.status);
    nextProfiles = await res.json();
  } catch (err) {
    if (request !== profileRequest) return false;
    setStatusMsg("Failed to load profiles: " + err.message, true);
    updateHandoffLabel();
    return false;
  }
  if (request !== profileRequest) return false;
  profiles = nextProfiles;
  const saved = localStorage.getItem(PROFILE_KEY);
  const ids = profiles.map((p) => p.id);
  const active = ids.includes(saved) ? saved : ids[0] || "";
  profileEl.replaceChildren(
    ...profiles.map((p) => el("option", { value: p.id }, p.name + " (" + p.id + ")"))
  );
  profileEl.value = active;
  if (active) localStorage.setItem(PROFILE_KEY, active);
  updateHandoffLabel();
  return active !== previousProfile;
}

function updateHandoffLabel() {
  const btn = document.getElementById("handoffBtn");
  const p = profiles.find((x) => x.id === profileEl.value);
  const n = p ? p.selected : 0;
  const id = profileEl.value || "—";
  btn.textContent = "Send " + n + " selected (" + id + ") → RiceClipper";
  refreshInteractionState();
}

function refreshInteractionState() {
  handoffBtn.disabled = handoffPending || pendingMutations > 0 || !profileEl.value;
  listEl.inert = handoffPending;
  listEl.setAttribute("aria-busy", handoffPending ? "true" : "false");
}

function beginMutation() {
  pendingMutations += 1;
  refreshInteractionState();
}

function endMutation() {
  pendingMutations = Math.max(0, pendingMutations - 1);
  refreshInteractionState();
}

async function handoff() {
  const profile = profileEl.value;
  if (!profile) { setStatusMsg("choose a profile first", true); return; }
  if (pendingMutations > 0 || handoffPending) {
    setStatusMsg("wait for pending review changes before handoff", true);
    return;
  }
  handoffPending = true;
  refreshInteractionState();
  setStatusMsg("writing handoff batch…");
  try {
    const res = await fetch("/api/handoff", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile }),
    });
    const d = await res.json();
    if (!res.ok) { setStatusMsg("handoff failed: " + (d.detail || res.status), true); return; }
    if (!d.clip_count) { setStatusMsg("nothing selected to hand off", false); return; }
    setStatusMsg("handed off " + d.clip_count + " clip(s) as " + d.batch_id, false);
    await loadProfiles();  // refresh the selected count on the button
    await load(true);  // handed-off slices leave the selected/candidate views
  } catch (err) {
    setStatusMsg("handoff failed: " + err.message, true);
  } finally {
    handoffPending = false;
    refreshInteractionState();
  }
}

function setStatusMsg(text, isError) {
  statusEl.textContent = text || "";
  statusEl.classList.toggle("error", !!isError);
}

async function load(preserveStatus = false) {
  const request = ++sliceRequest;
  const profile = profileEl.value;
  if (!profile) {
    listEl.replaceChildren(el("div", { class: "empty" }, "No profiles found."));
    countEl.textContent = "";
    return;
  }
  const status = filterEl.value;
  listEl.replaceChildren(el("div", { class: "empty" }, "Loading slices…"));
  countEl.textContent = "";
  let url = "/api/slices?profile=" + encodeURIComponent(profile);
  if (status) url += "&status=" + encodeURIComponent(status);
  let slices;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error("HTTP " + res.status);
    slices = await res.json();
  } catch (err) {
    if (!isCurrentLoad(request, profile, status)) return;
    listEl.replaceChildren(el("div", { class: "empty" }, "Failed to load slices: " + err.message));
    countEl.textContent = "";
    return;
  }
  if (!isCurrentLoad(request, profile, status)) return;
  slices.sort((a, b) => b.score - a.score);
  countEl.textContent = slices.length + (slices.length === 1 ? " slice" : " slices");
  if (!preserveStatus) setStatusMsg("");
  if (!slices.length) {
    listEl.replaceChildren(
      el("div", { class: "empty" }, [
        el("p", {}, "No slices to review here."),
        el("p", {}, [document.createTextNode("Pull and "), el("code", {}, "ricesearcher score <id>"), document.createTextNode(" first, then refresh.")]),
      ])
    );
    return;
  }
  listEl.replaceChildren(...slices.map(card));
}

function isCurrentLoad(request, profile, status) {
  return request === sliceRequest && profile === profileEl.value && status === filterEl.value;
}

function card(s) {
  const c = el("article", { class: "card", "data-status": s.status });

  // Left: video preview of the exact saved export window.
  const preview = el("div", { class: "preview" });
  let video;
  if (s.media_url) {
    video = el("video", { controls: "", preload: "metadata" });
    video.src = s.media_url + "#t=" + fmt(s.target_in) + "," + fmt(s.target_out);
    preview.append(video);
  } else {
    preview.append(el("div", { class: "empty" }, "media unavailable"));
  }
  const windowLabel = el("div", { class: "win" },
    "selected " + fmt(s.target_in) + "–" + fmt(s.target_out) + "s");
  preview.append(windowLabel);
  c.append(preview);

  // Right: metadata + gate controls.
  const meta = el("div", { class: "meta" });
  meta.append(el("div", { class: "meta-top" }, [
    el("span", { class: "score", title: "LLM clippability score" }, s.score.toFixed(2)),
    el("span", { class: "title" }, s.source_title),
  ]));

  const badges = el("div", { class: "badges" });
  const renderBadges = () => {
    const kids = [
      el("span", { class: "badge" }, "rights: " + s.rights_risk),
      el("span", { class: "badge" }, "heur " + s.heuristic_score.toFixed(2)),
    ];
    if (s.status === "selected") kids.push(el("span", { class: "badge status-selected" }, "selected"));
    if (s.status === "rejected") kids.push(el("span", { class: "badge status-rejected" }, "rejected"));
    if (s.status === "reviewed") kids.push(el("span", { class: "badge" }, "reviewed"));
    if (s.status === "handed_off") kids.push(el("span", { class: "badge" }, "handed off"));
    if (s.stale) kids.push(el("span",
      { class: "badge stale", title: "scored with version " + s.beat_profile_version }, "stale"));
    if (s.dup_of) {
      kids.push(el("span", { class: "badge dup", title: "advisory only — nothing is filtered" },
        "possible dup (" + s.dup_kind + " " + s.dup_score.toFixed(2) + ") of " + (s.dup_label || s.dup_of)));
    }
    badges.replaceChildren(...kids);
  };
  renderBadges();
  meta.append(badges);

  if (s.rationale) meta.append(el("div", { class: "rationale" }, s.rationale));
  meta.append(el("div", { class: "transcript" }, s.transcript_span));

  const msg = el("div", { class: "card-msg", role: "status", "aria-live": "polite" });

  // Set the exact export interval; the server validates it against the source.
  const inIn = numInput("in", s.target_in);
  const outIn = numInput("out", s.target_out);
  const winEdit = el("div", { class: "window-edit" }, [
    el("label", {}, [document.createTextNode("in"), inIn]),
    el("label", {}, [document.createTextNode("out"), outIn]),
  ]);
  let windowPending = false;
  let statusPending = false;
  let terminal = s.status === "handed_off";
  let selectBtn;
  let rejectBtn;
  let resetBtn;
  const refreshCardControls = () => {
    const disabled = terminal || windowPending || statusPending;
    inIn.disabled = disabled;
    outIn.disabled = disabled;
    if (selectBtn) selectBtn.disabled = disabled;
    if (rejectBtn) rejectBtn.disabled = disabled;
    if (resetBtn) resetBtn.disabled = disabled;
  };
  const applyWin = async () => {
    if (windowPending || statusPending || terminal || handoffPending) return;
    const ti = parseFloat(inIn.value), to = parseFloat(outIn.value);
    if (!Number.isFinite(ti) || !Number.isFinite(to)) {
      cardMsg(msg, "in/out must be numbers", true);
      return;
    }
    windowPending = true;
    beginMutation();
    refreshCardControls();
    try {
      const res = await fetch("/api/slices/" + encodeURIComponent(s.id) + "/window", {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_in: ti, target_out: to }),
      });
      if (!res.ok) {
        let detail;
        try {
          const body = await res.json();
          if (typeof body.detail === "string") detail = body.detail;
        } catch (_err) {
          // A non-JSON error still gets the status fallback below.
        }
        cardMsg(msg, detail || "couldn't save window (" + res.status + ")", true);
        return;
      }
      const d = await res.json();
      s.target_in = d.target_in; s.target_out = d.target_out;
      inIn.value = fmt(d.target_in); outIn.value = fmt(d.target_out);
      if (video) video.src = s.media_url + "#t=" + fmt(d.target_in) + "," + fmt(d.target_out);
      windowLabel.replaceChildren(document.createTextNode(
        "selected " + fmt(d.target_in) + "–" + fmt(d.target_out) + "s"));
      cardMsg(msg, "window saved", false);
    } catch (err) {
      cardMsg(msg, "couldn't save window: " + err.message, true);
    } finally {
      windowPending = false;
      endMutation();
      refreshCardControls();
    }
  };
  inIn.addEventListener("change", applyWin);
  outIn.addEventListener("change", applyWin);
  meta.append(winEdit);

  // The select-and-approve gate. Update the card in place (no full re-render) so
  // keyboard focus stays on the control the reviewer just used.
  const actions = el("div", { class: "actions" });
  const setStatus = async (status) => {
    if (statusPending || windowPending || terminal || handoffPending) return;
    statusPending = true;
    beginMutation();
    refreshCardControls();
    try {
      const res = await fetch("/api/slices/" + encodeURIComponent(s.id) + "/status", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      if (!res.ok) { cardMsg(msg, "couldn't set status (" + res.status + ")", true); return; }
      s.status = status;
      terminal = status === "handed_off";
      c.setAttribute("data-status", status);
      renderBadges();
      cardMsg(msg, "marked " + status, false);
      const profileChanged = await loadProfiles();
      if (profileChanged) await load();
    } catch (err) {
      cardMsg(msg, "couldn't set status: " + err.message, true);
    } finally {
      statusPending = false;
      endMutation();
      refreshCardControls();
    }
  };
  selectBtn = el("button", { class: "primary" }, "Select");
  rejectBtn = el("button", { class: "danger" }, "Reject");
  resetBtn = el("button", {}, "Reset");
  refreshCardControls();
  if (terminal) {
    cardMsg(msg, "handed off — this slice is terminal", false);
  }
  selectBtn.addEventListener("click", () => setStatus("selected"));
  rejectBtn.addEventListener("click", () => setStatus("rejected"));
  resetBtn.addEventListener("click", () => setStatus("candidate"));
  actions.append(selectBtn, rejectBtn, resetBtn);
  // msg sits ABOVE the actions so the buttons anchor flush to the card bottom
  // (via .actions margin-top:auto) instead of leaving a dead gap beneath them.
  meta.append(msg, actions);

  c.append(meta);
  return c;
}

function cardMsg(node, text, isError) {
  node.textContent = text || "";
  node.classList.toggle("error", !!isError);
}

function numInput(label, value) {
  const i = el("input", { type: "number", step: "any", min: "0", "aria-label": "intended " + label + " (seconds)" });
  i.value = fmt(value);
  return i;
}

// Preserve the server's finite numeric value without quantising it to tenths.
// The browser input accepts arbitrary finite decimals; preview, label, and
// input all use this same representation so they cannot drift apart.
function fmt(n) { return String(n); }

// Tiny DOM helper. children may be a string, node, or array of them. Attribute
// names starting with "on" are refused so a stray attr value can never become an
// event handler.
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

async function init() {
  await loadProfiles();
  await load();
}

init();
