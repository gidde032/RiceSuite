// Poster's automatic ingest (RiceSuite ADR-001 Q12), run against the real
// functions in poster/frontend/index.html: a waiting Clip batch is pulled
// without a click only when no unposted draft is at risk; otherwise it waits in
// the visible inbox. The automatic path ends where Pull ends — it never posts,
// schedules, or confirms away drafts.
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
// whole automatic-ingest block.
const SOURCE = [
  slice("function draftsAtRisk()", "// Returns true when a batch was pulled"),
  slice("// --- Automatic ingest from Clip", "function assertPulledTargets"),
].join("\n");

function boot({ inbox, slots = {}, pullResult = true }) {
  const { fetch, calls } = scriptedFetch({ "GET api/handoff/inbox": inbox });
  const panel = element();
  const pulls = [];
  const forbidden = (name) => () => {
    throw new Error(`${name} must never run from the automatic path`);
  };
  const ctx = vm.createContext({
    Date,
    state: { accounts: [{ slot: "A" }, { slot: "B" }], slots },
    fetchWithTimeout: fetch,
    elOpt: (id) => (id === "clipperInbox" ? panel : null),
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
  return { js, calls, pulls, panel };
}

const emptySlot = () => ({ file: null, filename: "", caption: "" });
const ready = { batches: [{ batch_id: "batch_1", clip_count: 2 }], unacknowledged: null };

test("an empty draft workspace pulls a waiting batch automatically", async () => {
  const { js, pulls, panel } = boot({ inbox: ready, slots: { A: emptySlot(), B: emptySlot() } });
  await js("pollClipperInbox()");
  assert.equal(pulls.length, 1);
  assert.equal(panel.hidden, false);
  assert.match(panel.innerHTML, /batch_1/);
});

for (const [label, draft] of [
  ["staged media", { file: null, filename: "A_batch_0_clip.mp4", caption: "" }],
  ["an uploaded file", { file: {}, filename: "", caption: "" }],
  ["a caption", { file: null, filename: "", caption: "written by hand" }],
]) {
  test(`a draft holding ${label} is never overwritten: the batch waits in the inbox`, async () => {
    const { js, pulls, panel } = boot({ inbox: ready, slots: { A: draft, B: emptySlot() } });
    await js("pollClipperInbox()");
    assert.equal(pulls.length, 0);
    assert.equal(panel.hidden, false);
    assert.match(panel.innerHTML, /Pull from Clipper/);
  });
}

test("a draft on an inactive account slot does not block", async () => {
  const draft = { file: null, filename: "old.mp4", caption: "x" };
  const { js, pulls } = boot({ inbox: ready, slots: { A: emptySlot(), Z: draft } });
  await js("pollClipperInbox()");
  assert.equal(pulls.length, 1);
});

test("an empty inbox pulls nothing and hides the panel", async () => {
  const { js, pulls, panel } = boot({ inbox: { batches: [], unacknowledged: null } });
  await js("pollClipperInbox()");
  assert.equal(pulls.length, 0);
  assert.equal(panel.hidden, true);
});

test("an unacknowledged batch is shown but never replayed automatically", async () => {
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

test("no second pull starts while one is in flight", async () => {
  const { js, pulls } = boot({ inbox: ready });
  js("pullInFlight = true");
  await js("pollClipperInbox()");
  assert.equal(pulls.length, 0);
});

test("a failed pull backs off instead of retrying every poll", async () => {
  const { js, pulls } = boot({ inbox: ready, pullResult: false });
  await js("pollClipperInbox()");
  await js("pollClipperInbox()");
  assert.equal(pulls.length, 1);
});

test("the page polls the inbox on a timer", () => {
  assert.match(HTML, /setInterval\(pollClipperInbox, INBOX_POLL_MS\)/);
});

// --- the real pullFromClipper and generateAll ---------------------------------

const PULL_SOURCE = [
  slice("function draftsAtRisk()", "// --- Automatic ingest from Clip"),
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

test("the automatic pull never asks for replay", async () => {
  const slots = { A: { file: null, filename: "", caption: "" } };
  const { js, calls } = bootPull({ slots });
  js("generateAll = async () => { state.slots.A.caption = 'generated'; }");
  await js("pullFromClipper({ automatic: true })");
  const pull = calls.find((c) => c.method === "POST" && c.path === "api/pull-from-clipper");
  assert.ok(pull, "no pull request");
  assert.equal(pull.url, "api/pull-from-clipper?replay=0");
});

test("a draft started while an automatic pull is in flight is kept", async () => {
  const slots = { A: { file: null, filename: "", caption: "" } };
  const { js, applied, asked } = bootPull({
    slots,
    onPull: () => { slots.A.caption = "typed during the pull"; },
  });
  assert.equal(await js("pullFromClipper({ automatic: true })"), false);
  assert.deepEqual(applied, []);
  assert.equal(asked.length, 0);
  assert.equal(slots.A.caption, "typed during the pull");
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
