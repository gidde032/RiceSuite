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
const progressEl = document.getElementById("progress");

// Which profile the review UI is scoped to. Persisted so a reload keeps it.
const PROFILE_KEY = "ricesearcher.profile";
let profiles = [];  // last /api/profiles payload, for the handoff-button count
let profileRequest = 0;
let sliceRequest = 0;
let pendingMutations = 0;
let handoffPending = false;
const OPERATION_KEY = "ricesearcher.handoff.operation";
let observed = null;
let pollTimer = null;
let observationGeneration = 0;
let mutationInFlight = false;
let lastAnnouncement = "";
let refreshedOperation = "";
const cardProgress = new Map();

function rememberOperation() {
  try { sessionStorage.setItem(OPERATION_KEY, JSON.stringify(observed)); } catch (_err) { /* storage can be disabled */ }
}

function stopObservation() {
  observationGeneration += 1;
  if (pollTimer !== null) clearTimeout(pollTimer);
  pollTimer = null;
}

function renderOperation() {
  if (!observed || !progressEl) return;
  const snapshot = observed.snapshot;
  const unknown = observed.unknown;
  const complete = snapshot.status === "complete";
  const failed = snapshot.status === "failed";
  const unconfirmed = snapshot.status === "unconfirmed";
  const committing = snapshot.stage === "committing" || snapshot.stage === "published";
  const state = unknown || unconfirmed ? "? Result unknown" : failed ? "! Failed / held" : complete ? "✓ Complete" : committing ? "→ Handing off" : "• Working";
  const count = snapshot.total ? snapshot.completed + " / " + snapshot.total + (complete && snapshot.batch_id ? " handed off" : " prepared") : "";
  const current = snapshot.current;
  let detail = unknown ? (snapshot.detail.startsWith("Operation unavailable") ? snapshot.detail : "Connection lost / result unknown. Last known progress retained; checking this operation only.") : snapshot.detail;
  if (unconfirmed && snapshot.published) detail = "Batch " + snapshot.batch_id + " was published; final library confirmation failed. Check the batch before retry. " + snapshot.detail;
  if (failed) {
    const failedItem = snapshot.items.find((item) => item.state === "preparing" || item.state === "checking");
    if (failedItem) detail = "Clip " + failedItem.position + " of " + snapshot.total + " — " + failedItem.title + ": " + snapshot.detail + ". Batch not handed off; remaining clips not attempted.";
    else detail += " · Batch not handed off.";
  }
  if (!detail && current) detail = "Now: " + (snapshot.stage === "checking" ? "checking" : "preparing") + " clip " + current.position + " of " + snapshot.total + " — " + current.title;
  if (!detail && committing) detail = "Handing off batch…";
  if (!detail) detail = "Preparing selected clips…";
  const text = state + count + detail + observed.profile;
  if (text !== lastAnnouncement) {
    progressEl.hidden = false;
    progressEl.classList.toggle("error", failed || unknown || unconfirmed);
    progressEl.replaceChildren(
      el("div", { class: "progress-top" }, [
        el("strong", {}, "Send selected to Clipper · " + observed.profile),
        el("span", {}, state), el("span", { class: "progress-count" }, count),
      ]), el("div", { class: "progress-detail" }, detail)
    );
    lastAnnouncement = text;
  }
  for (const [id, entry] of cardProgress) {
    const item = snapshot.items.find((candidate) => candidate.id === id);
    let text = "";
    if (item && entry.profile === observed.profile && profileEl.value === observed.profile) {
      if (unknown || unconfirmed) text = "Last known: " + item.state + " · result unknown";
      else if (failed) text = item.state === "prepared" ? "Prepared · batch not confirmed handed off" : item.state === "waiting" || item.state === "checked" ? "Not attempted · batch held" : "Failed / held · " + snapshot.detail;
      else text = complete ? (snapshot.batch_id ? "Handed off to Clipper" : "No batch handed off") : item.state === "prepared" ? "Prepared · waiting for batch handoff" : item.state === "waiting" || item.state === "checked" ? "Waiting for preparation" : "Preparing clip…";
    }
    if (entry.node.textContent !== text) entry.node.textContent = text;
  }
}

async function refreshCompletedOperation() {
  if (!observed || observed.snapshot.status !== "complete" || refreshedOperation === observed.id) return;
  refreshedOperation = observed.id;
  await loadProfiles();
  await load(true);
}

async function pollOperation(generation = observationGeneration) {
  const operation = observed;
  if (!operation || generation !== observationGeneration) return;
  try {
    const res = await fetch("api/handoff/progress/" + encodeURIComponent(operation.id) + "?profile=" + encodeURIComponent(operation.profile));
    if (generation !== observationGeneration) return;
    if (res.status === 404 && !mutationInFlight) {
      operation.unknown = true;
      operation.snapshot.detail = "Operation unavailable; result unknown. Work is not retried.";
      renderOperation();
      // Unlike a transient read failure, an expired/restarted record cannot reconnect.
      rememberOperation();
      stopObservation();
      return;
    }
    if (!res.ok) throw new Error("HTTP " + res.status);
    const snapshot = await res.json();
    if (generation !== observationGeneration || snapshot.operation_id !== operation.id || snapshot.scope !== operation.profile) return;
    operation.snapshot = snapshot;
    operation.unknown = false;
    rememberOperation();
    renderOperation();
    if (snapshot.status !== "active") {
      stopObservation();
      if (!mutationInFlight) { handoffPending = false; refreshInteractionState(); await refreshCompletedOperation(); }
      return;
    }
  } catch (_err) {
    if (generation !== observationGeneration) return;
    operation.unknown = true;
    rememberOperation();
    renderOperation();
  }
  if (generation === observationGeneration) pollTimer = setTimeout(() => pollOperation(generation), 700);
}

function beginObservation(id, profile) {
  stopObservation();
  observed = { id, profile, unknown: false, snapshot: { status: "active", stage: "starting", items: [], total: 0, completed: 0, detail: "", batch_id: "" } };
  rememberOperation();
  renderOperation();
  pollTimer = setTimeout(() => pollOperation(), 0);
}

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
    const res = await fetch("api/profiles");
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
  mutationInFlight = true;
  refreshInteractionState();
  const id = typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : "attempt-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
  beginObservation(id, profile);
  try {
    const res = await fetch("api/handoff", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ profile, observation_id: id }),
    });
    const d = await res.json();
    if (res.ok) {
      stopObservation();
      observed.snapshot = { ...observed.snapshot, status: "complete", completed: d.clip_count, total: d.clip_count, batch_id: d.batch_id || "", detail: d.clip_count ? "Batch handed off to Clipper." : "Nothing selected or selection changed; no batch handed off." };
      observed.unknown = false;
      handoffPending = false;
      rememberOperation();
      renderOperation();
      await refreshCompletedOperation();
    } else {
      // Even a server error may follow publication; read the exact attempt.
      observed.unknown = observed.snapshot.status === "active";
      renderOperation();
    }
  } catch (_err) {
    observed.unknown = observed.snapshot.status === "active";
    rememberOperation();
    renderOperation();
  } finally {
    mutationInFlight = false;
    if (observed.snapshot.status === "active") {
      stopObservation();
      await pollOperation();
    } else { handoffPending = false; await refreshCompletedOperation(); }
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
  cardProgress.clear();
  listEl.replaceChildren(el("div", { class: "empty" }, "Loading slices…"));
  countEl.textContent = "";
  let url = "api/slices?profile=" + encodeURIComponent(profile);
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
  cardProgress.clear();
  listEl.replaceChildren(...slices.map(card));
  renderOperation();
}

function isCurrentLoad(request, profile, status) {
  return request === sliceRequest && profile === profileEl.value && status === filterEl.value;
}

function card(s) {
  const c = el("article", { class: "card", "data-status": s.status, "data-slice-id": s.id });
  const content = el("div", { class: "card-content" });

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
  content.append(preview);

  // Right: metadata + gate controls.
  const meta = el("div", { class: "meta" });
  // Offline slices were ranked by the heuristic prefilter only, never by the LLM.
  const offline = s.scorer_model === "heuristic-offline";
  meta.append(el("div", { class: "meta-top" }, [
    el("span", { class: "score", title: offline ? "offline heuristic score (not LLM-scored)" : "LLM clippability score" }, s.score.toFixed(2)),
    el("span", { class: "title" }, s.source_title),
  ]));

  const badges = el("div", { class: "badges" });
  const renderBadges = () => {
    const kids = [
      el("span", { class: "badge" }, "rights: " + s.rights_risk),
      el("span", { class: "badge" }, "heur " + s.heuristic_score.toFixed(2)),
    ];
    if (offline) {
      kids.push(el("span", { class: "badge offline", title: "scored offline by the heuristic prefilter; not LLM-scored" },
        "offline score"));
    }
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
      const res = await fetch("api/slices/" + encodeURIComponent(s.id) + "/window", {
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
      const res = await fetch("api/slices/" + encodeURIComponent(s.id) + "/status", {
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
  const operationMsg = el("div", { class: "card-progress" });
  cardProgress.set(s.id, { node: operationMsg, profile: s.profile_id });
  meta.append(msg, operationMsg, actions);

  content.append(meta);
  c.append(content);
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

// Preserve the server's finite numeric value without quantising it or emitting
// exponent notation, which is not valid media-fragment timestamp syntax.
function fmt(n) {
  const raw = String(n);
  if (!/[eE]/.test(raw)) return raw;
  const negative = raw.startsWith("-");
  const [coefficient, exponentText] = raw.replace(/^-/, "").toLowerCase().split("e");
  const [whole, fraction = ""] = coefficient.split(".");
  const digits = whole + fraction;
  const decimalAt = whole.length + Number(exponentText);
  let expanded;
  if (decimalAt <= 0) expanded = "0." + "0".repeat(-decimalAt) + digits;
  else if (decimalAt >= digits.length) expanded = digits + "0".repeat(decimalAt - digits.length);
  else expanded = digits.slice(0, decimalAt) + "." + digits.slice(decimalAt);
  return (negative ? "-" : "") + expanded;
}

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
  try {
    const saved = JSON.parse(sessionStorage.getItem(OPERATION_KEY));
    if (saved && /^[A-Za-z0-9_-]{1,64}$/.test(saved.id) && typeof saved.profile === "string" && saved.snapshot) {
      observed = saved;
      renderOperation();
      if (saved.snapshot.status === "active" || saved.unknown) {
        handoffPending = true;
        refreshInteractionState();
        await pollOperation();
      }
    }
  } catch (_err) { /* absent or invalid temporary observation */ }
}

init();
