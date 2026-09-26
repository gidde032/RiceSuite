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

function boot(routes) {
  const { fetch, calls } = scriptedFetch({
    "GET api/health": { ok: true },
    "GET api/media-info": { files: 0, bytes: 0 },
    ...routes,
  });
  const { ctx, timers } = context(fetch);
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
  });
  js(`clips.push({ jobId: "j1", status: "done", el: { remove() {} } }); batchSent = true;`);
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
