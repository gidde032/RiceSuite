// Clipper's automatic transport (RiceSuite ADR-001 Q12), run against the real
// clipper/web/app.js: a complete Searcher batch is pulled without a click only
// when nothing unsent would be displaced, and a batch sends itself only once
// every clip has rendered successfully.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { scriptedFetch, context, run, settle } = require("./harness");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "..", "clipper", "web", "app.js"),
  "utf8",
);

function job(id) {
  return { id, status: "ready", title: `clip ${id}`, words: [] };
}

// A clip the send path can serialise, so a test that expects "no send" fails
// only because of the rule under test, never because the send would throw.
function sendable(jobId, status) {
  return `{ jobId: "${jobId}", status: "${status}", headerEl: { value: "" }, captionStyleEl: {}, headerStyleEl: {}, transcriptEl: {} }`;
}

function boot(routes, extra = {}) {
  const { fetch, calls } = scriptedFetch({
    "GET api/health": { ok: true },
    "GET api/media-info": { files: 0, bytes: 0 },
    ...routes,
  });
  const { ctx, timers } = context(fetch, extra);
  const js = run(SOURCE, ctx);
  return { js, calls, timers };
}

const posts = (calls, p) => calls.filter((c) => c.method === "POST" && c.path === p);

test("the page polls for Searcher batches on a timer", () => {
  const { timers } = boot({});
  assert.ok(timers.some((t) => t.fn.name === "autoPullFromSearcher"));
});

test("an empty workspace pulls a waiting batch and starts transcribing it", async () => {
  const { js, calls } = boot({
    "GET api/searcher-inbox": { batches: [{ batch_id: "b1", clip_count: 2 }] },
    "POST api/pull-from-searcher": { batch_id: "b1", clip_count: 2, jobs: [job("j1"), job("j2")] },
    "POST api/jobs/j1/transcribe": { status: "ready", words: [] },
    "POST api/jobs/j2/transcribe": { status: "ready", words: [] },
  });
  await js("autoPullFromSearcher()");
  await settle();
  assert.equal(posts(calls, "api/pull-from-searcher").length, 1);
  assert.equal(js("clips.length"), 2);
  assert.equal(posts(calls, "api/jobs/j1/transcribe").length, 1);
  assert.equal(posts(calls, "api/jobs/j2/transcribe").length, 1);
  // Rendering stays a human action.
  assert.equal(calls.filter((c) => c.path.endsWith("/render")).length, 0);
  assert.equal(posts(calls, "api/handoff").length, 0);
});

test("nothing is pulled when the inbox is empty", async () => {
  const { js, calls } = boot({ "GET api/searcher-inbox": { batches: [] } });
  await js("autoPullFromSearcher()");
  assert.equal(posts(calls, "api/pull-from-searcher").length, 0);
});

test("a loaded, unsent batch is never displaced by the next one", async () => {
  const { js, calls } = boot({
    "GET api/searcher-inbox": { batches: [{ batch_id: "b2", clip_count: 1 }] },
    "POST api/pull-from-searcher": () => [500, { detail: "must not be called" }],
  });
  js(`clips.push({ jobId: "j1", status: "done" }, { jobId: "j2", status: "ready" })`);
  await js("autoPullFromSearcher()");
  assert.equal(posts(calls, "api/pull-from-searcher").length, 0);
  js(`clips[1].status = "done"`); // all rendered but not sent yet: still held
  await js("autoPullFromSearcher()");
  assert.equal(posts(calls, "api/pull-from-searcher").length, 0);
});

test("a batch sends itself once every clip has rendered, exactly once", async () => {
  const { js, calls } = boot({
    "POST api/handoff": { batch_id: "out1", clip_count: 2 },
  });
  js(`clips.push(${sendable("j1", "done")}, ${sendable("j2", "done")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("maybeAutoSend()");
  await js("maybeAutoSend()");
  const sent = posts(calls, "api/handoff");
  assert.equal(sent.length, 1);
  assert.deepEqual(JSON.parse(sent[0].body).clips.map((c) => c.job_id), ["j1", "j2"]);
  assert.equal(js("batchSent"), true);
});

test("a failed or unrendered clip holds the whole batch", async () => {
  const { js, calls } = boot({ "POST api/handoff": { batch_id: "x", clip_count: 1 } });
  js(`clips.push(${sendable("j1", "done")}, ${sendable("j2", "ready")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("maybeAutoSend()");
  js(`clips[1].status = "error"`);
  await js("maybeAutoSend()");
  assert.equal(posts(calls, "api/handoff").length, 0);
});

test("no automatic send while a render-all is still running", async () => {
  const { js, calls } = boot({ "POST api/handoff": { batch_id: "x", clip_count: 1 } });
  js(`clips.push(${sendable("j1", "done")}); batchBusy = true;`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("maybeAutoSend()");
  assert.equal(posts(calls, "api/handoff").length, 0);
});

test("after a batch is sent, the next Searcher batch replaces it", async () => {
  const { js, calls } = boot({
    "GET api/searcher-inbox": { batches: [{ batch_id: "b2", clip_count: 1 }] },
    "POST api/pull-from-searcher": { batch_id: "b2", clip_count: 1, jobs: [job("j9")] },
    "POST api/jobs/j9/transcribe": { status: "ready", words: [] },
    "POST api/handoff": { batch_id: "out1", clip_count: 1 },
  });
  js(`clips.push(${sendable("j1", "done")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("maybeAutoSend()"); // sent exactly as it stands
  await js("autoPullFromSearcher()");
  await settle();
  assert.equal(posts(calls, "api/pull-from-searcher").length, 1);
  assert.deepEqual(JSON.parse(js("JSON.stringify(clips.map((c) => c.jobId))")), ["j9"]);
  assert.equal(js("batchSent"), false);
});

test("a failed pull backs off instead of retrying every poll", async () => {
  const { js, calls } = boot({
    "GET api/searcher-inbox": { batches: [{ batch_id: "bad", clip_count: 1 }] },
    "POST api/pull-from-searcher": () => [400, { detail: "malformed manifest" }],
  });
  await js("autoPullFromSearcher()");
  await js("autoPullFromSearcher()");
  assert.equal(posts(calls, "api/pull-from-searcher").length, 1);
  assert.equal(js("clips.length"), 0);
});

// --- review repairs: nothing unsent is ever displaced; each batch sends once --

test("work done after a send holds the workspace: nothing unsent is wiped", async () => {
  const { js, calls } = boot({
    "POST api/handoff": { batch_id: "out1", clip_count: 1 },
    "GET api/searcher-inbox": { batches: [{ batch_id: "b2", clip_count: 1 }] },
    "POST api/pull-from-searcher": () => [500, { detail: "must not be called" }],
  });
  js(`clips.push(${sendable("j1", "done")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("maybeAutoSend()");
  assert.equal(posts(calls, "api/handoff").length, 1);
  // The reviewer edits the header and re-renders after the automatic send.
  js(`clips[0].headerEl.value = "a better header"; clips[0].renders = 1;`);
  await js("autoPullFromSearcher()");
  assert.equal(posts(calls, "api/pull-from-searcher").length, 0);
  assert.equal(js("clips.length"), 1);
  // And the edited batch is not sent again behind the reviewer's back.
  await js("maybeAutoSend()");
  assert.equal(posts(calls, "api/handoff").length, 1);
});

test("a send already in flight is never started twice", async () => {
  const { js, calls } = boot({ "POST api/handoff": { batch_id: "out1", clip_count: 1 } });
  js(`clips.push(${sendable("j1", "done")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  // A click on Send and the automatic send racing, neither awaited first.
  await js("Promise.all([sendBatch(), maybeAutoSend()])");
  assert.equal(posts(calls, "api/handoff").length, 1);
});

test("sending an already-sent batch again needs the reviewer's confirmation", async () => {
  const asked = [];
  const { js, calls } = boot(
    { "POST api/handoff": { batch_id: "out1", clip_count: 1 } },
    { confirm: (message) => { asked.push(message); return false; } },
  );
  js(`clips.push(${sendable("j1", "done")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("maybeAutoSend()");
  await js("sendBatch()"); // the Send button, clicked out of habit
  assert.equal(posts(calls, "api/handoff").length, 1);
  assert.equal(asked.length, 1);
  assert.match(asked[0], /out1/);
});

test("two Send clicks racing post one batch", async () => {
  const { js, calls } = boot({ "POST api/handoff": { batch_id: "out1", clip_count: 1 } });
  js(`clips.push(${sendable("j1", "done")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("Promise.all([sendBatch(), sendBatch()])");
  assert.equal(posts(calls, "api/handoff").length, 1);
});

test("any edit inside a clip card after a send holds the workspace", async () => {
  // A card whose own listeners are recorded, so the test can play the part of
  // the reviewer flipping captions off or pasting lyrics after the send.
  const { element } = require("./harness");
  const cardListeners = [];
  const card = element();
  card.addEventListener = (type, fn) => cardListeners.push({ type, fn });
  const template = { content: { firstElementChild: { cloneNode: () => card } } };
  const document = {
    getElementById: (id) => (id === "clip-card-template" ? template : element()),
    querySelector: () => element(), querySelectorAll: () => [],
    createElement: () => element(), createTextNode: () => element(),
    addEventListener() {}, body: element(),
  };
  const { js, calls } = boot(
    {
      "POST api/handoff": { batch_id: "out1", clip_count: 1 },
      "GET api/searcher-inbox": { batches: [{ batch_id: "b2", clip_count: 1 }] },
      "POST api/pull-from-searcher": () => [500, { detail: "must not be called" }],
    },
    { document },
  );
  js("collectWords = () => []; radioValue = () => 'x';");
  js(`clips.push({ jobId: "j1", status: "done" }); buildCard(clips[0]);`);
  await js("maybeAutoSend()");
  assert.equal(posts(calls, "api/handoff").length, 1);
  const edits = cardListeners.filter((l) => l.type === "input" || l.type === "change");
  assert.ok(edits.length >= 2, "the card must watch input and change events");
  edits.find((l) => l.type === "change").fn({ target: {} }); // e.g. captions toggled off
  await js("autoPullFromSearcher()");
  assert.equal(posts(calls, "api/pull-from-searcher").length, 0);
  assert.equal(js("clips.length"), 1);
});
