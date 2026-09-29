// Poster's Clipper inbox (RiceSuite ADR-001 Q12 as amended 2026-09-29), run
// against the real functions in poster/frontend/index.html: a waiting Clip
// batch is only ever pulled by the maintainer's Pull from Clipper click. The
// inbox poll reports waiting batches and never pulls, posts, schedules, or
// confirms away drafts.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { element, scriptedFetch } = require("./harness");

const HTML = fs.readFileSync(
  path.join(__dirname, "..", "..", "poster", "frontend", "index.html"),
  "utf8",
);

function slice(from, to) {
  const start = HTML.indexOf(from);
  const end = HTML.indexOf(to, start);
  assert.ok(start >= 0 && end > start, `could not find ${from}`);
  return HTML.slice(start, end);
}

// Verbatim page code: the at-risk predicate shared with manual Pull, and the
// whole Clipper inbox block.
const SOURCE = [
  slice("function draftsAtRisk()", "// Returns true when a batch was pulled"),
  slice("// --- Clipper inbox", "function assertPulledTargets"),
].join("\n");

function boot({ inbox, slots = {}, pullResult = true }) {
  const { fetch, calls } = scriptedFetch({ "GET api/handoff/inbox": inbox });
  const panel = element();
  const button = element();
  button.textContent = "Pull from Clipper";
  const pulls = [];
  const forbidden = (name) => () => {
    throw new Error(`${name} must never run from the automatic path`);
  };
  const ctx = vm.createContext({
    Date,
    state: { accounts: [{ slot: "A" }, { slot: "B" }], slots },
    fetchWithTimeout: fetch,
    elOpt: (id) => ({ clipperInbox: panel, btnPullClipper: button })[id] || null,
    esc: (s) => String(s),
    pullFromClipper: async () => {
      pulls.push(1);
      return pullResult;
    },
    postAll: forbidden("postAll"),
    scheduleAll: forbidden("scheduleAll"),
    confirm: forbidden("confirm"),
  });
  vm.runInContext(SOURCE, ctx);
  const js = (expr) => vm.runInContext(expr, ctx);
  return { js, calls, pulls, panel, button };
}

const emptySlot = () => ({ file: null, filename: "", caption: "" });
const ready = { batches: [{ batch_id: "batch_1", clip_count: 2 }], unacknowledged: null };

const onlyInboxReads = (calls) =>
  assert.deepEqual(calls.map((c) => `${c.method} ${c.path}`), ["GET api/handoff/inbox"]);

test("a waiting batch is never pulled without a click, even with no drafts", async () => {
  const { js, pulls, calls, panel } = boot({ inbox: ready, slots: { A: emptySlot(), B: emptySlot() } });
  await js("pollClipperInbox()");
  await js("pollClipperInbox()");
  assert.equal(pulls.length, 0);
  assert.equal(calls.length, 2);
  onlyInboxReads(calls.slice(0, 1));
  assert.equal(panel.hidden, false);
  assert.match(panel.innerHTML, /batch_1/);
  assert.match(panel.innerHTML, /Pull from Clipper/);
  assert.doesNotMatch(panel.innerHTML, /automatically/i);
});

for (const [label, draft] of [
  ["staged media", { file: null, filename: "A_batch_0_clip.mp4", caption: "" }],
  ["an uploaded file", { file: {}, filename: "", caption: "" }],
  ["a caption", { file: null, filename: "", caption: "written by hand" }],
]) {
  test(`a draft holding ${label} is untouched: the batch waits in the inbox`, async () => {
    const { js, pulls, panel } = boot({ inbox: ready, slots: { A: draft, B: emptySlot() } });
    await js("pollClipperInbox()");
    assert.equal(pulls.length, 0);
    assert.equal(panel.hidden, false);
    assert.match(panel.innerHTML, /Pull from Clipper/);
  });
}

test("the Pull button shows how many batches wait, and plain text when none do", async () => {
  const inbox = { batches: [{ batch_id: "batch_1" }, { batch_id: "batch_2" }], unacknowledged: "batch_0" };
  const { js, button } = boot({ inbox });
  await js("pollClipperInbox()");
  assert.equal(button.textContent, "Pull from Clipper · 3");
  js("renderClipperInbox({ batches: [], unacknowledged: null })");
  assert.equal(button.textContent, "Pull from Clipper");
});

test("an inbox error leaves the button count alone rather than claiming zero", async () => {
  const { js, button } = boot({ inbox: ready });
  await js("pollClipperInbox()");
  js("renderClipperInbox({ batches: [], unacknowledged: null, error: 'unreadable receipt' })");
  assert.equal(button.textContent, "Pull from Clipper · 1");
});

test("an empty inbox pulls nothing and hides the panel", async () => {
  const { js, pulls, panel } = boot({ inbox: { batches: [], unacknowledged: null } });
  await js("pollClipperInbox()");
  assert.equal(pulls.length, 0);
  assert.equal(panel.hidden, true);
});

test("an unacknowledged batch is shown and left for Pull from Clipper", async () => {
  const inbox = { batches: [{ batch_id: "batch_1", clip_count: 1 }], unacknowledged: "batch_0" };
  const { js, pulls, panel } = boot({ inbox, slots: { A: emptySlot() } });
  await js("pollClipperInbox()");
  assert.equal(pulls.length, 0);
  assert.equal(panel.hidden, false);
  assert.match(panel.innerHTML, /batch_0/);
  assert.match(panel.innerHTML, /Pull from Clipper/);
});

test("an inbox error is shown, not hidden", async () => {
  const inbox = { batches: [], unacknowledged: null, error: "handoff archive root must be a real directory" };
  const { js, pulls, panel } = boot({ inbox });
  await js("pollClipperInbox()");
  assert.equal(pulls.length, 0);
  assert.equal(panel.hidden, false);
  assert.match(panel.innerHTML, /archive root/);
});

test("the page polls the inbox on a timer, and nothing else starts a pull", () => {
  assert.match(HTML, /setInterval\(pollClipperInbox, INBOX_POLL_MS\)/);
  const callers = HTML.match(/pullFromClipper\(/g) || [];
  // The definition and the button's onclick, nothing more.
  assert.equal(callers.length, 2, "only the Pull from Clipper button may start a pull");
  assert.match(HTML, /onclick="pullFromClipper\(\)"/);
});

// --- the real pullFromClipper and generateAll ---------------------------------

const PULL_SOURCE = [
  slice("function draftsAtRisk()", "// --- Clipper inbox"),
  slice("async function generateAll()", "\n}\n") + "\n}\n",
].join("\n");

function bootPull({ slots, confirmAnswer = true, onPull }) {
  const applied = [];
  const statuses = [];
  const asked = [];
  const { fetch, calls } = scriptedFetch({
    "POST api/pull-from-clipper": () => {
      if (onPull) onPull();
      return [200, { pulled: true, batch_id: "batch_1", replayed: false,
                     slots: [{ slot: "A", filename: "A_batch_1_clip_1.mp4" }] }];
    },
    "POST api/pull-from-clipper/batch_1/ack": { status: "applied" },
  });
  const ctx = vm.createContext({
    Date, FormData: class { append() {} }, console,
    state: { accounts: [{ slot: "A" }], slots, defaultCaptionStyle: "generic" },
    fetchWithTimeout: fetch,
    handleFetchError: async () => {},
    elOpt: () => element(), el: () => element(), slotEl: () => element(),
    setPullStatus: (m) => statuses.push(m),
    confirm: (m) => { asked.push(m); return confirmAnswer; },
    assertPulledTargets() {},
    applyPulledSlot: (entry) => { applied.push(entry.slot); slots[entry.slot].filename = entry.filename; return "api/media/x"; },
    updateButtons() {}, updateThumbChip() {}, setCaptionError() {}, autoGrow() {}, updateCharCount() {},
    captureThumbnailFromUrl: async () => "",
    CAPTION_TIMEOUT_MS: 1000,
  });
  vm.runInContext(PULL_SOURCE, ctx);
  return { js: (e) => vm.runInContext(e, ctx), calls, applied, statuses, asked };
}

test("manual Pull asks before overwriting a draft, and Cancel pulls nothing", async () => {
  const slots = { A: { file: null, filename: "", caption: "my caption" } };
  const { js, calls, asked } = bootPull({ slots, confirmAnswer: false });
  assert.equal(await js("pullFromClipper()"), false);
  assert.equal(asked.length, 1);
  assert.equal(calls.filter((c) => c.path === "api/pull-from-clipper").length, 0);
  assert.equal(slots.A.caption, "my caption");
});

test("Pull from Clipper always asks the default pull, which recovers an unacknowledged batch", async () => {
  const slots = { A: { file: null, filename: "", caption: "" } };
  const { js, calls } = bootPull({ slots });
  js("generateAll = async () => { state.slots.A.caption = 'generated'; }");
  assert.equal(await js("pullFromClipper()"), true);
  const pull = calls.find((c) => c.method === "POST" && c.path === "api/pull-from-clipper");
  assert.ok(pull, "no pull request");
  assert.equal(pull.url, "api/pull-from-clipper");
});

test("a caption typed while generation runs is never overwritten", async () => {
  const slots = { A: { file: null, filename: "A.mp4", caption: "", mediaType: "video", topic: "t" } };
  const { fetch } = scriptedFetch({
    "POST api/generate-caption": () => {
      slots.A.caption = "typed by hand";
      return [200, { caption: "generated" }];
    },
  });
  const ctx = vm.createContext({
    FormData: class { append() {} }, console,
    state: { slots, defaultCaptionStyle: "generic" },
    fetchWithTimeout: fetch, handleFetchError: async () => {},
    el: () => element(), slotEl: () => element(), setCaptionError() {}, autoGrow() {},
    updateCharCount() {}, updateButtons() {}, CAPTION_TIMEOUT_MS: 1000,
    // page globals from the account-swap guard (#19)
    draftWork: 0, accountChangeInFlight: false,
  });
  vm.runInContext(slice("async function generateAll()", "\n}\n") + "\n}\n", ctx);
  await vm.runInContext("generateAll()", ctx);
  assert.equal(slots.A.caption, "typed by hand");
});
