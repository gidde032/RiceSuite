// Clipper's transport, run against the real clipper/web/app.js: a complete
// Searcher batch is pulled without a click only when nothing unsent would be
// displaced (RiceSuite ADR-001 Q12). A batch goes to RicePoster only on the
// reviewer's Send click (ADR-001 "Manual Clipper send", Issue #59).
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
  // Observation timers are advanced explicitly in progress tests. Running every
  // delayed reconnect immediately would busy-loop after the lost-reply cases.
  const { ctx, timers } = context(fetch, {
    setTimeout: (fn, ms) => { if (ms === 3000) fn(); return 0; },
    ...extra,
  });
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

test("#59: a fully rendered batch waits for the Send click", async () => {
  const { js, calls } = boot({
    "POST api/jobs/jA/render": { status: "done" },
    "POST api/jobs/jB/render": { status: "done" },
    "POST api/handoff": { batch_id: "out1", clip_count: 2 },
  });
  stubRender(js);
  js(`clips.push(${renderable("jA", 1)}, ${renderable("jB", 2)})`);
  await js("handleRenderAll()");
  await settle();
  assert.equal(js("clips.every(clipCurrent)"), true, "every clip rendered");
  assert.equal(posts(calls, "api/handoff").length, 0, "nothing is sent without a click");
  assert.equal(js("batchSent"), false);
  await js("sendBatch()"); // the Send button
  const sent = posts(calls, "api/handoff");
  assert.equal(sent.length, 1);
  assert.deepEqual(JSON.parse(sent[0].body).clips.map((c) => c.job_id), ["jA", "jB"]);
  assert.equal(js("batchSent"), true);
});

test("#59: only the Send button calls sendBatch", () => {
  const calls = SOURCE.match(/\bsendBatch\(/g) || [];
  // The definition and the button's click listener.
  assert.equal(calls.length, 2, "a second caller of sendBatch would send without a click");
  assert.match(SOURCE, /\$\("send-handoff-btn"\)\.addEventListener\("click", \(\) => sendBatch\(\)\)/);
  assert.doesNotMatch(SOURCE, /maybeAutoSend/);
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
  await js("sendBatch()"); // sent exactly as it stands
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
  await js("sendBatch()");
  assert.equal(posts(calls, "api/handoff").length, 1);
  // The reviewer edits the header and re-renders after the send.
  js(`clips[0].headerEl.value = "a better header"; clips[0].renders = 1;`);
  await js("autoPullFromSearcher()");
  assert.equal(posts(calls, "api/pull-from-searcher").length, 0);
  assert.equal(js("clips.length"), 1);
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
  await js("sendBatch()");
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
  await js("sendBatch()");
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

// sessionStorage for one tab: it survives a reload of that tab only.
function session(initial = {}) {
  const store = { "riceclipper.tab.v1": JSON.stringify(initial) };
  return {
    store,
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  };
}

const pullKey = (call) => new URL(call.url, "http://page/").searchParams.get("pull_key");

test("W1-02: a pull whose reply was lost is retried with its key", async () => {
  const keys = [];
  const { js } = boot({
    // Clipper took custody on the first attempt, so the inbox is empty after it.
    "GET api/searcher-inbox": () => [200, { batches: keys.length ? [] : [{ batch_id: "b1", clip_count: 1 }] }],
    "POST api/pull-from-searcher": (call) => {
      keys.push(pullKey(call));
      if (keys.length === 1) throw new TypeError("network connection lost");
      return [200, { batch_id: "b1", clip_count: 1, jobs: [job("j1")], replayed: true }];
    },
    "POST api/jobs/j1/transcribe": { status: "ready", words: [] },
  });
  await js("autoPullFromSearcher()");
  assert.equal(js("clips.length"), 0);
  await js("autoPullFromSearcher()");
  await settle();
  assert.deepEqual(jobIds(js), ["j1"]);
  assert.equal(keys.length, 2);
  assert.ok(keys[0], "each pull carries a key");
  assert.equal(keys[1], keys[0]);
});

test("W1-02: a reloaded tab restores the batch it had pulled", async () => {
  const { js, calls } = boot(
    {
      "GET api/workspace": (call) => [200, call.url.includes("batch_id=b1")
        ? { batch_id: "b1", clip_count: 1, jobs: [job("j1")] }
        : { batch_id: null, clip_count: 0, jobs: [] }],
      "GET api/searcher-inbox": { batches: [{ batch_id: "b2", clip_count: 1 }] },
      "POST api/pull-from-searcher": () => [500, { detail: "must not be called" }],
      "POST api/jobs/j1/transcribe": { status: "ready", words: [] },
    },
    { sessionStorage: session({ pulledBatchId: "b1" }) },
  );
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
  await js("sendBatch()");
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
  await js("sendBatch()");
  await js("sendBatch()");
  assert.equal(bodies.length, 2);
  assert.notEqual(bodies[1].send_key, bodies[0].send_key);
  assert.equal(bodies[0].resend, false);
  assert.equal(bodies[1].resend, true, "only a confirmed second send may resend clips");
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
        if (id === "progress-state") el.textContent = "○ Idle";
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
    captionsToggleEl: { checked: true }, motionToggleEl: { checked: true }, emojiToggleEl: { checked: false }, musicModeEl: { value: "none" },
    musicInputEl: { files: [] }, musicVolumeEl: { value: "0.5" },
    resultEl: { classList: { add() {}, remove() {} } },
    outputVideoEl: { pause() {}, load() {}, removeAttribute() {} },
    downloadEl: { removeAttribute() {} }, lyricsAlignEl: {}, lyricsRestoreEl: {} }`;
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
  const { js, calls, byId } = bootWithButtons({
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
  await js("sendBatch()"); // the Send button refuses the stale render
  assert.equal(posts(calls, "api/handoff").length, 0);
  assert.match(byId["batch-status"].textContent, /Clip 1 changed after its render/);
});

test("W1-06: an edit during a clip's own render holds the send until it renders again", async () => {
  const reply = held();
  const renderedHeaders = [];
  const { js, calls, byId } = bootWithButtons({
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
  await js("sendBatch()"); // the Send button refuses the stale render
  assert.equal(posts(calls, "api/handoff").length, 0);
  assert.match(byId["batch-status"].textContent, /Clip 1 changed after its render/);
  // Rendered again, the MP4 matches what is sent.
  await js("handleRenderAll()");
  await settle();
  await js("sendBatch()");
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
  await js("sendBatch()");
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

test("W3-02, #59: once the failed clip is removed, Send sends the rest", async () => {
  const { js, calls } = boot({ "POST api/handoff": { batch_id: "x", clip_count: 1 } });
  js(`clips.push(${sendable("j1", "done")}, ${sendable("j2", "ready")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  js("clips[1].el = { remove() {} }; updateCacheControls = () => {};");
  js("removeClip(clips[1])");
  await settle();
  assert.equal(posts(calls, "api/handoff").length, 0, "a removal does not send");
  await js("sendBatch()");
  const sent = posts(calls, "api/handoff");
  assert.equal(sent.length, 1);
  assert.deepEqual(JSON.parse(sent[0].body).clips.map((c) => c.job_id), ["j1"]);
});

// --- review S-1: a second tab, or a reload, never sends a batch twice --------

test("S-1: a second tab never takes another tab's open batch by itself", async () => {
  const { js, byId } = bootWithButtons(
    {
      "GET api/workspace": { batch_id: "b1", clip_count: 1, jobs: [job("j1")] },
      "GET api/searcher-inbox": { batches: [{ batch_id: "b2", clip_count: 1 }] },
      "POST api/pull-from-searcher": { batch_id: "b2", clip_count: 1, jobs: [job("j2")] },
      "POST api/jobs/j2/transcribe": { status: "ready", words: [] },
    },
    { sessionStorage: session() },
  );
  await js("autoPullFromSearcher()");
  await settle();
  assert.deepEqual(jobIds(js), ["j2"]);
  assert.match(byId["pull-status"].textContent, /b1/, "the open batch is named");
});

test("S-1: a Pull click opens an open batch before it pulls a new one", async () => {
  const { js, calls, click } = bootWithButtons(
    {
      "GET api/workspace": { batch_id: "b1", clip_count: 1, jobs: [job("j1")] },
      "GET api/searcher-inbox": { batches: [{ batch_id: "b2", clip_count: 1 }] },
      "POST api/pull-from-searcher": () => [500, { detail: "must not be called" }],
      "POST api/jobs/j1/transcribe": { status: "ready", words: [] },
    },
    { sessionStorage: session() },
  );
  await click("pull-searcher-btn");
  await settle();
  assert.deepEqual(jobIds(js), ["j1"]);
  assert.equal(posts(calls, "api/pull-from-searcher").length, 0);
});

test("S-1: a send refused as already sent holds the batch and names it", async () => {
  const { js, calls, byId } = bootWithButtons({
    "POST api/handoff": () => [409, {
      detail: "These clips already went to RicePoster as batch out1.",
      already_sent: "out1",
    }],
    "GET api/searcher-inbox": { batches: [{ batch_id: "b2", clip_count: 1 }] },
    "POST api/pull-from-searcher": () => [500, { detail: "must not be called" }],
  });
  js(`clips.push(${sendable("j1", "done")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("sendBatch()");
  assert.equal(js("batchSent"), true);
  assert.equal(js("sentBatchId"), "out1");
  assert.match(byId["batch-status"].textContent, /out1/);
  await js("autoPullFromSearcher()");
  assert.equal(posts(calls, "api/handoff").length, 1);
  assert.equal(posts(calls, "api/pull-from-searcher").length, 0, "the workspace stays held");
});

// --- correctness review repairs ---------------------------------------------

test("C-1: an edit after a lost send reply goes out as a new send, never a replay", async () => {
  const bodies = [];
  const { js } = boot({
    "POST api/handoff": (call) => {
      bodies.push(JSON.parse(call.body));
      if (bodies.length === 1) throw new TypeError("network connection lost"); // written; reply lost
      return [409, {
        detail: "These clips already went to RicePoster as batch out1.",
        already_sent: "out1",
      }];
    },
  });
  js(`clips.push(${sendable("j1", "done")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("sendBatch()");
  // The reviewer fixes the header and renders the clip again.
  js(`clips[0].headerEl.value = "fixed header"; clips[0].edits = 1;
      clips[0].renderedEdits = 1; clips[0].renders = 1;`);
  await js("sendBatch()");
  assert.equal(bodies.length, 2);
  assert.notEqual(bodies[1].send_key, bodies[0].send_key, "a changed batch is a new send");
  assert.equal(js("sentBatchId"), "out1");
  assert.equal(js("workspaceFree()"), false, "the fixed header is not claimed as sent");
});

test("C-3: a render found done by polling after an edit is not shown as current", async () => {
  let js;
  let sent = null;
  ({ js } = boot({
    "POST api/jobs/jA/render": (call) => {
      sent = JSON.parse(call.body).render_id; // it renders; the reply is lost
      throw new TypeError("network connection lost");
    },
    "GET api/jobs/jA": () => {
      js("clips[0].edits = 1"); // the reviewer edits while the page polls
      return [200, { status: "done", has_output: true, render_id: sent }];
    },
  }));
  stubRender(js);
  js("statuses = []; setClipStatus = (c, t) => statuses.push(t);");
  js(`clips.push(${renderable("jA", 1)})`);
  assert.equal(await js("renderClip(clips[0])"), true);
  const shown = JSON.parse(js("JSON.stringify(statuses)"));
  assert.match(shown[shown.length - 1], /Edited since/);
});

// --- Issue #61: name the Searcher batch waiting behind an unsent batch -------
// The cue has its own line in the batch panel. It never pulls, and it leaves
// the progress bar (Render all's outcome, #59) and the pull status alone.

function heldWorkspace(inbox) {
  let batches = inbox;
  const booted = bootWithButtons({
    "GET api/searcher-inbox": () => [200, { batches }],
    "POST api/pull-from-searcher": () => [500, { detail: "must not be called" }],
  });
  booted.js(`clips.push(${sendable("j1", "done")})`); // rendered, not sent
  booted.js("collectWords = () => []; radioValue = () => 'x';");
  booted.js(`showProgress("Render all", "✓ Complete", "1 / 1 rendered", "Send the batch to Poster when ready.")`);
  return { ...booted, setInbox: (next) => { batches = next; } };
}

const cue = (byId) => ({ hidden: byId["searcher-waiting"].hidden, text: byId["searcher-waiting"].textContent });

test("#61: a held batch names the Searcher batch waiting behind it", async () => {
  const { js, calls, byId } = heldWorkspace([{ batch_id: "b2", clip_count: 3 }]);
  await js("autoPullFromSearcher()");
  const shown = cue(byId);
  assert.equal(shown.hidden, false);
  assert.match(shown.text, /RiceSearcher batch b2 \(3 clips\) is waiting/);
  assert.match(shown.text, /after you send this batch or start over/);
  assert.equal(posts(calls, "api/pull-from-searcher").length, 0, "the cue never pulls");
  assert.equal(byId["progress-current"].textContent, "Send the batch to Poster when ready.");
  assert.equal(byId["progress-state"].textContent, "✓ Complete");
  assert.equal(byId["pull-status"]?.textContent ?? "", "", "the cue does not use the pull status");
});

test("#61: several waiting batches are counted, oldest first", async () => {
  const { js, byId } = heldWorkspace([
    { batch_id: "b2", clip_count: 1 },
    { batch_id: "b3", clip_count: 2 },
  ]);
  await js("autoPullFromSearcher()");
  assert.match(cue(byId).text, /2 RiceSearcher batches are waiting \(b2, b3\)/);
  assert.match(cue(byId).text, /oldest opens after you send this batch or start over/);
});

test("#61: the cue clears when nothing waits, and when the workspace is cleared", async () => {
  const { js, byId, setInbox } = heldWorkspace([{ batch_id: "b2", clip_count: 1 }]);
  await js("autoPullFromSearcher()");
  assert.equal(cue(byId).hidden, false);
  setInbox([]);
  await js("autoPullFromSearcher()");
  assert.deepEqual(cue(byId), { hidden: true, text: "" });

  setInbox([{ batch_id: "b2", clip_count: 1 }]);
  await js("autoPullFromSearcher()");
  assert.equal(cue(byId).hidden, false);
  js("clearWorkspace()"); // Start over
  assert.deepEqual(cue(byId), { hidden: true, text: "" });
});

test("#61: an unreadable inbox keeps the last cue", async () => {
  let reachable = true;
  const { js, byId } = bootWithButtons({
    "GET api/searcher-inbox": () => {
      if (!reachable) throw new TypeError("Failed to fetch");
      return [200, { batches: [{ batch_id: "b2", clip_count: 1 }] }];
    },
  });
  js(`clips.push(${sendable("j1", "ready")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("autoPullFromSearcher()");
  const before = cue(byId);
  assert.equal(before.hidden, false);
  reachable = false;
  await js("autoPullFromSearcher()");
  assert.deepEqual(cue(byId), before);
});

test("#61: a sent batch shows no cue: the next batch is pulled, not held", async () => {
  const { js, calls, byId } = bootWithButtons({
    "GET api/searcher-inbox": { batches: [{ batch_id: "b2", clip_count: 1 }] },
    "POST api/handoff": { batch_id: "out1", clip_count: 1 },
  });
  js(`clips.push(${sendable("j1", "ready")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("autoPullFromSearcher()");
  assert.equal(cue(byId).hidden, false, "held while j1 is not rendered");
  js(`clips[0].status = "done"`);
  await js("sendBatch()");
  js("autoPullPausedUntil = Date.now() + 60000"); // an earlier pull failed
  await js("autoPullFromSearcher()");
  assert.equal(js("clips.length"), 1, "the sent batch stays until the next pull");
  assert.equal(posts(calls, "api/pull-from-searcher").length, 0);
  assert.deepEqual(cue(byId), { hidden: true, text: "" });
});

test("#61: the cue line lives in the batch panel, apart from the pull status", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "..", "clipper", "web", "index.html"), "utf8");
  const panel = html.slice(html.indexOf('id="batch-panel"'), html.indexOf("</section>", html.indexOf('id="batch-panel"')));
  assert.match(panel, /<p id="searcher-waiting" class="status" role="status" hidden><\/p>/);
  assert.doesNotMatch(SOURCE, /setPullStatus\([^)]*waiting/i);
});

test("#61: a successful send clears the cue at once", async () => {
  const { js, byId } = bootWithButtons({
    "GET api/searcher-inbox": { batches: [{ batch_id: "b2", clip_count: 1 }] },
    "POST api/handoff": { batch_id: "out1", clip_count: 1 },
  });
  js(`clips.push(${sendable("j1", "done")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  await js("autoPullFromSearcher()");
  assert.equal(cue(byId).hidden, false);
  await js("sendBatch()");
  assert.deepEqual(cue(byId), { hidden: true, text: "" }, "no tick needed");
});

test("#61: an inbox read overtaken by a send and the next pull does not name the loaded batch", async () => {
  let releaseSlow;
  let reads = 0;
  const waiting = { batches: [{ batch_id: "b2", clip_count: 1 }] };
  const { js, byId } = bootWithButtons({
    "GET api/searcher-inbox": () => {
      reads += 1;
      if (reads === 1) return new Promise((resolve) => { releaseSlow = () => resolve([200, waiting]); });
      return [200, waiting];
    },
    "POST api/handoff": { batch_id: "out1", clip_count: 1 },
    "POST api/pull-from-searcher": { batch_id: "b2", clip_count: 1, jobs: [job("j2")] },
    "POST api/jobs/j2/transcribe": { status: "ready", words: [] },
  });
  js(`clips.push(${sendable("j1", "done")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  const slow = js("autoPullFromSearcher()"); // held: this inbox read is slow
  await settle();
  await js("sendBatch()");
  await js("autoPullFromSearcher()"); // the next tick: sent, so it pulls b2
  await settle();
  assert.deepEqual(jobIds(js), ["j2"]);
  releaseSlow();
  await slow;
  await settle();
  assert.deepEqual(cue(byId), { hidden: true, text: "" }, "b2 is open here, not waiting");
});

test("#61: inbox replies slower than the timer still show the line", async () => {
  const replies = [];
  const { js, byId } = bootWithButtons({
    "GET api/searcher-inbox": () => new Promise((resolve) => replies.push(resolve)),
  });
  js(`clips.push(${sendable("j1", "ready")})`); // held: not rendered
  js("collectWords = () => []; radioValue = () => 'x';");
  const first = js("autoPullFromSearcher()");
  await settle();
  const second = js("autoPullFromSearcher()"); // the next tick starts before the reply
  await settle();
  replies[0]([200, { batches: [{ batch_id: "b2", clip_count: 1 }] }]);
  await first;
  await settle();
  assert.equal(cue(byId).hidden, false, "a held read is not dropped for a newer one");
  assert.match(cue(byId).text, /b2/);
  replies[1]([200, { batches: [{ batch_id: "b2", clip_count: 1 }, { batch_id: "b3", clip_count: 2 }] }]);
  await second;
  await settle();
  assert.match(cue(byId).text, /2 RiceSearcher batches/);
});

test("#61: an older reply never overwrites a newer one", async () => {
  const replies = [];
  const { js, byId } = bootWithButtons({
    "GET api/searcher-inbox": () => new Promise((resolve) => replies.push(resolve)),
  });
  js(`clips.push(${sendable("j1", "ready")})`);
  js("collectWords = () => []; radioValue = () => 'x';");
  const first = js("autoPullFromSearcher()");
  await settle();
  const second = js("autoPullFromSearcher()");
  await settle();
  replies[1]([200, { batches: [{ batch_id: "b3", clip_count: 2 }] }]);
  await second;
  await settle();
  replies[0]([200, { batches: [{ batch_id: "b2", clip_count: 1 }] }]);
  await first;
  await settle();
  assert.match(cue(byId).text, /b3/);
});
