"use strict";
// RiceSearcher Review UI. Vanilla JS: fetch scored slices, render Slate cards,
// preview the moment's video window, tighten in/out, and Select / Reject.
// Content strings go through textContent (via el()), never innerHTML; el() also
// refuses on* attributes, so no server string can become an event handler.

const listEl = document.getElementById("list");
const countEl = document.getElementById("count");
const statusEl = document.getElementById("status");
const filterEl = document.getElementById("statusFilter");

filterEl.addEventListener("change", load);

function setStatusMsg(text, isError) {
  statusEl.textContent = text || "";
  statusEl.classList.toggle("error", !!isError);
}

async function load() {
  const status = filterEl.value;
  const url = "/api/slices" + (status ? "?status=" + encodeURIComponent(status) : "");
  let slices;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error("HTTP " + res.status);
    slices = await res.json();
  } catch (err) {
    listEl.replaceChildren(el("div", { class: "empty" }, "Failed to load slices: " + err.message));
    countEl.textContent = "";
    return;
  }
  slices.sort((a, b) => b.score - a.score);
  countEl.textContent = slices.length + (slices.length === 1 ? " slice" : " slices");
  setStatusMsg("");
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

function card(s) {
  const c = el("article", { class: "card", "data-status": s.status });

  // Left: video preview of the padded window.
  const preview = el("div", { class: "preview" });
  if (s.media_url) {
    const v = el("video", { controls: "", preload: "metadata" });
    v.src = s.media_url + "#t=" + fmt(s.pad_in) + "," + fmt(s.pad_out);
    preview.append(v);
  } else {
    preview.append(el("div", { class: "empty" }, "media unavailable"));
  }
  preview.append(el("div", { class: "win" },
    "padded " + fmt(s.pad_in) + "–" + fmt(s.pad_out) + "s"));
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

  // Tighten the intended in/out (clamped server-side to the padded window).
  const inIn = numInput("in", s.target_in);
  const outIn = numInput("out", s.target_out);
  const winEdit = el("div", { class: "window-edit" }, [
    el("label", {}, [document.createTextNode("in"), inIn]),
    el("label", {}, [document.createTextNode("out"), outIn]),
  ]);
  const applyWin = async () => {
    const ti = parseFloat(inIn.value), to = parseFloat(outIn.value);
    if (!Number.isFinite(ti) || !Number.isFinite(to)) {
      inIn.value = fmt(s.target_in); outIn.value = fmt(s.target_out);  // restore
      cardMsg(msg, "in/out must be numbers", true);
      return;
    }
    try {
      const res = await fetch("/api/slices/" + encodeURIComponent(s.id) + "/window", {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_in: ti, target_out: to }),
      });
      if (!res.ok) {
        inIn.value = fmt(s.target_in); outIn.value = fmt(s.target_out);
        cardMsg(msg, "couldn't save window (" + res.status + ")", true);
        return;
      }
      const d = await res.json();
      s.target_in = d.target_in; s.target_out = d.target_out;
      inIn.value = fmt(d.target_in); outIn.value = fmt(d.target_out);
      cardMsg(msg, "window saved", false);
    } catch (err) {
      inIn.value = fmt(s.target_in); outIn.value = fmt(s.target_out);
      cardMsg(msg, "couldn't save window: " + err.message, true);
    }
  };
  inIn.addEventListener("change", applyWin);
  outIn.addEventListener("change", applyWin);
  meta.append(winEdit);

  // The select-and-approve gate. Update the card in place (no full re-render) so
  // keyboard focus stays on the control the reviewer just used.
  const actions = el("div", { class: "actions" });
  const setStatus = async (status) => {
    try {
      const res = await fetch("/api/slices/" + encodeURIComponent(s.id) + "/status", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      if (!res.ok) { cardMsg(msg, "couldn't set status (" + res.status + ")", true); return; }
      s.status = status;
      c.setAttribute("data-status", status);
      renderBadges();
      cardMsg(msg, "marked " + status, false);
    } catch (err) {
      cardMsg(msg, "couldn't set status: " + err.message, true);
    }
  };
  const selectBtn = el("button", { class: "primary" }, "Select");
  const rejectBtn = el("button", { class: "danger" }, "Reject");
  const resetBtn = el("button", {}, "Reset");
  selectBtn.addEventListener("click", () => setStatus("selected"));
  rejectBtn.addEventListener("click", () => setStatus("rejected"));
  resetBtn.addEventListener("click", () => setStatus("candidate"));
  actions.append(selectBtn, rejectBtn, resetBtn);
  meta.append(actions, msg);

  c.append(meta);
  return c;
}

function cardMsg(node, text, isError) {
  node.textContent = text || "";
  node.classList.toggle("error", !!isError);
}

function numInput(label, value) {
  const i = el("input", { type: "number", step: "0.1", min: "0", "aria-label": "intended " + label + " (seconds)" });
  i.value = fmt(value);
  return i;
}

function fmt(n) { return (Math.round(n * 10) / 10).toString(); }

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

load();
