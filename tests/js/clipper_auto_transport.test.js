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

// --- functional audit repairs: lost replies and reloads strand nothing -------

const jobIds = (js) => JSON.parse(js("JSON.stringify(clips.map((c) => c.jobId))"));

test("W1-02: a pull whose reply was lost is restored on the next poll", async () => {
  let pulled = false;
  const { js, calls } = boot({
    "GET api/searcher-inbox": () => [200, { batches: pulled ? [] : [{ batch_id: "b1", clip_count: 1 }] }],
    "POST api/pull-from-searcher": () => {
      pulled = true; // the server took custody, then the reply was lost
      throw new TypeError("network connection lost");
    },
    "GET api/workspace": () => [200, pulled
      ? { batch_id: "b1", clip_count: 1, jobs: [job("j1")] }
      : { batch_id: null, clip_count: 0, jobs: [] }],
    "POST api/jobs/j1/transcribe": { status: "ready", words: [] },
  });
  await js("autoPullFromSearcher()");
  assert.equal(js("clips.length"), 0);
  await js("autoPullFromSearcher()");
  await settle();
  assert.deepEqual(jobIds(js), ["j1"]);
  assert.equal(posts(calls, "api/pull-from-searcher").length, 1);
  assert.equal(posts(calls, "api/jobs/j1/transcribe").length, 1);
});

test("W1-02: a reloaded page restores its pulled batch before it pulls another", async () => {
  const { js, calls } = boot({
    "GET api/workspace": { batch_id: "b1", clip_count: 1, jobs: [job("j1")] },
    "GET api/searcher-inbox": { batches: [{ batch_id: "b2", clip_count: 1 }] },
    "POST api/pull-from-searcher": () => [500, { detail: "must not be called" }],
    "POST api/jobs/j1/transcribe": { status: "ready", words: [] },
  });
  await js("autoPullFromSearcher()");
  await settle();
  assert.deepEqual(jobIds(js), ["j1"]);
  assert.equal(posts(calls, "api/pull-from-searcher").length, 0);
});

test("Start over discards the pulled batch, so it is not restored again", async () => {
  const { js, calls } = boot(
    {
      "POST api/pull-from-searcher": { batch_id: "b1", clip_count: 1, jobs: [job("j1")] },
      "GET api/searcher-inbox": { batches: [{ batch_id: "b1", clip_count: 1 }] },
      "POST api/jobs/j1/transcribe": { status: "ready", words: [] },
      "DELETE api/workspace": { discarded: true },
    },
    { confirm: () => true },
  );
  await js("autoPullFromSearcher()");
  await settle();
  js("resetAll()");
  await settle();
  const discards = calls.filter((c) => c.method === "DELETE" && c.path === "api/workspace");
  assert.equal(discards.length, 1);
  assert.match(discards[0].url, /batch_id=b1/);
});

test("W1-01: a send whose reply was lost is retried with the same key", async () => {
  const bodies = [];
  const { js } = boot({
    "POST api/handoff": (call) => {
      bodies.push(JSON.parse(call.body));
      if (bodies.length === 1) throw new TypeError("network connection lost");
      return [200, { batch_id: "out1", clip_count: 1, replayed: true }];
    },
  });
  js(`clips.push(${sendable("j1", "done")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("maybeAutoSend()");
  assert.equal(js("batchSent"), false);
  await js("sendBatch()"); // no confirm: the harness's confirm() throws
  assert.equal(bodies.length, 2);
  assert.ok(bodies[0].send_key, "each send carries a key");
  assert.equal(bodies[1].send_key, bodies[0].send_key);
  assert.equal(js("batchSent"), true);
  assert.equal(js("sentBatchId"), "out1");
});

test("W1-01: a confirmed second send of a sent batch uses a new key", async () => {
  const bodies = [];
  const { js } = boot(
    {
      "POST api/handoff": (call) => {
        bodies.push(JSON.parse(call.body));
        return [200, { batch_id: `out${bodies.length}`, clip_count: 1 }];
      },
    },
    { confirm: () => true },
  );
  js(`clips.push(${sendable("j1", "done")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("maybeAutoSend()");
  await js("sendBatch()");
  assert.equal(bodies.length, 2);
  assert.notEqual(bodies[1].send_key, bodies[0].send_key);
});

// --- functional audit repairs: one pull at a time; only current renders send --

// Like boot(), but each element id is one element whose click listeners are
// kept, so a test can press a real button of the page.
function bootWithButtons(routes, extra = {}) {
  const { element } = require("./harness");
  const byId = {};
  const listeners = {};
  const document = {
    getElementById: (id) => {
      if (!byId[id]) {
        const el = element();
        el.addEventListener = (type, fn) => {
          (listeners[`${id} ${type}`] ||= []).push(fn);
        };
        byId[id] = el;
      }
      return byId[id];
    },
    querySelector: () => element(),
    querySelectorAll: () => [],
    createElement: () => element(),
    createTextNode: () => element(),
    addEventListener() {},
    body: element(),
  };
  const booted = boot(routes, { document, ...extra });
  const click = async (id) => {
    for (const fn of listeners[`${id} click`] || []) await fn({ target: byId[id] });
  };
  return { ...booted, click, byId };
}

function held() {
  let release;
  const promise = new Promise((resolve) => { release = resolve; });
  return { promise, release };
}

// A clip renderClip() can render under the harness.
function renderable(jobId, ord) {
  return `{ jobId: "${jobId}", ord: ${ord}, status: "ready", headerEl: { value: "first header" },
    captionStyleEl: {}, headerStyleEl: {}, transcriptEl: {}, geometryEl: {}, contentEl: {},
    captionsToggleEl: { checked: true }, musicModeEl: { value: "none" },
    musicInputEl: { files: [] }, musicVolumeEl: { value: "0.5" },
    resultEl: { classList: { add() {}, remove() {} } } }`;
}

function stubRender(js) {
  js("collectWords = () => []; radioValue = () => 'x'; setClipStatus = () => {};");
  js("showResult = async () => {};");
}

test("W2-01: a Pull click during an automatic pull does not merge two batches", async () => {
  const reply = held();
  let pulls = 0;
  const { js, calls, click } = bootWithButtons({
    "GET api/searcher-inbox": {
      batches: [{ batch_id: "b1", clip_count: 1 }, { batch_id: "b2", clip_count: 1 }],
    },
    "POST api/pull-from-searcher": async () => {
      pulls += 1;
      if (pulls === 1) {
        await reply.promise;
        return [200, { batch_id: "b1", clip_count: 1, jobs: [job("j1")] }];
      }
      return [200, { batch_id: "b2", clip_count: 1, jobs: [job("j2")] }];
    },
    "POST api/jobs/j1/transcribe": { status: "ready", words: [] },
    "POST api/jobs/j2/transcribe": { status: "ready", words: [] },
  });
  const automatic = js("autoPullFromSearcher()");
  await settle(); // the automatic pull waits for its reply
  await click("pull-searcher-btn");
  reply.release();
  await automatic;
  await settle();
  assert.equal(posts(calls, "api/pull-from-searcher").length, 1);
  assert.deepEqual(jobIds(js), ["j1"]);
});

test("W2-01: a Pull click with an unsent batch loaded pulls nothing", async () => {
  const { js, calls, click } = bootWithButtons({
    "GET api/searcher-inbox": { batches: [{ batch_id: "b2", clip_count: 1 }] },
    "POST api/pull-from-searcher": () => [500, { detail: "must not be called" }],
  });
  js(`clips.push(${sendable("j1", "ready")})`);
  await click("pull-searcher-btn");
  assert.equal(posts(calls, "api/pull-from-searcher").length, 0);
  assert.equal(js("clips.length"), 1);
});

test("W1-06: an edit to a rendered clip while another renders holds the send", async () => {
  const reply = held();
  const { js, calls } = boot({
    "POST api/jobs/jA/render": { status: "done" },
    "POST api/jobs/jB/render": async () => {
      await reply.promise;
      return [200, { status: "done" }];
    },
    "POST api/handoff": { batch_id: "out1", clip_count: 2 },
  });
  stubRender(js);
  js(`clips.push(${renderable("jA", 1)}, ${renderable("jB", 2)})`);
  const run = js("handleRenderAll()");
  await settle(); // clip A is rendered; clip B's render is in flight
  // What the card's input listener records when the reviewer edits A.
  js(`clips[0].headerEl.value = "a new header"; clips[0].edits = 1;`);
  reply.release();
  await run;
  await settle();
  assert.equal(posts(calls, "api/handoff").length, 0);
});

test("W1-06: an edit during a clip's own render holds the send until it renders again", async () => {
  const reply = held();
  const renderedHeaders = [];
  const { js, calls } = boot({
    "POST api/jobs/jA/render": async (call) => {
      renderedHeaders.push(JSON.parse(call.body).header);
      await reply.promise;
      return [200, { status: "done" }];
    },
    "POST api/handoff": { batch_id: "out1", clip_count: 1 },
  });
  stubRender(js);
  js(`clips.push(${renderable("jA", 1)})`);
  const run = js("handleRenderAll()");
  await settle(); // the render request carries "first header"
  js(`clips[0].headerEl.value = "a new header"; clips[0].edits = 1;`);
  reply.release();
  await run;
  await settle();
  assert.equal(posts(calls, "api/handoff").length, 0);
  // Rendered again, the MP4 matches what is sent.
  await js("handleRenderAll()");
  await settle();
  const sent = posts(calls, "api/handoff");
  assert.equal(sent.length, 1);
  assert.deepEqual(renderedHeaders, ["first header", "a new header"]);
  assert.equal(JSON.parse(sent[0].body).clips[0].header, "a new header");
});

test("W1-06: a generated header after a render counts as an edit", async () => {
  const { js, calls } = boot({
    "POST api/jobs/j1/header": { header: "a generated header" },
    "POST api/handoff": { batch_id: "out1", clip_count: 1 },
  });
  js(`clips.push(${sendable("j1", "done")})`);
  js("collectWords = () => []; radioValue = () => 'x'; setHeaderGenStatus = () => {};");
  js("clips[0].headerGenerateEl = {}; clips[0].headerFeedbackEl = { value: '' };");
  await js("regenerateHeader(clips[0])");
  await js("maybeAutoSend()");
  assert.equal(posts(calls, "api/handoff").length, 0);
});

test("W3-02: Send refuses a batch with a failed clip and names it", async () => {
  const { js, calls, byId } = bootWithButtons({
    "POST api/handoff": { batch_id: "x", clip_count: 1 },
  });
  js(`clips.push(${sendable("j1", "done")}, ${sendable("j2", "ready")})`);
  js(`clips[0].ord = 1; clips[1].ord = 2; clips[1].error = "render failed";`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("sendBatch()"); // the Send button
  assert.equal(posts(calls, "api/handoff").length, 0);
  assert.equal(js("batchSent"), false);
  assert.match(byId["batch-status"].textContent, /Clip 2/);
});

test("W3-02: once the failed clip is removed, the rest of the batch sends", async () => {
  const { js, calls } = boot({ "POST api/handoff": { batch_id: "x", clip_count: 1 } });
  js(`clips.push(${sendable("j1", "done")}, ${sendable("j2", "ready")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  js("clips[1].el = { remove() {} }; updateCacheControls = () => {};");
  js("removeClip(clips[1])");
  await settle();
  const sent = posts(calls, "api/handoff");
  assert.equal(sent.length, 1);
  assert.deepEqual(JSON.parse(sent[0].body).clips.map((c) => c.job_id), ["j1"]);
});
