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

test("an unacknowledged batch counts as waiting and is replayed", async () => {
  const inbox = { batches: [], unacknowledged: "batch_0" };
  const { js, pulls, panel } = boot({ inbox, slots: { A: emptySlot() } });
  await js("pollClipperInbox()");
  assert.equal(pulls.length, 1);
  assert.match(panel.innerHTML, /batch_0/);
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

test("the manual pull still confirms before overwriting drafts", () => {
  const body = slice("async function pullFromClipper()", "// --- Automatic ingest from Clip");
  assert.match(body, /const atRisk = draftsAtRisk\(\);/);
  assert.match(body, /atRisk\.length &&\s*!confirm\(/);
});
