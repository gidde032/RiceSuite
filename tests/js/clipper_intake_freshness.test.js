"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { element, context, run, scriptedFetch, settle } = require("./harness");
const read = (file) => fs.readFileSync(path.join(__dirname, "../..", file), "utf8");

function page(routes = {}, fetchOverride) {
  const ids = { "intake-status": element() }, events = {};
  const on = (type, fn) => { (events[type] ||= []).push(fn); };
  const document = {
    visibilityState: "visible", body: element(), addEventListener: on,
    getElementById: (id) => (ids[id] ||= element()),
    querySelector: () => element(), querySelectorAll: () => [],
    createElement: () => element(), createTextNode: () => element(),
  };
  const { fetch, calls } = scriptedFetch({
    "GET api/health": {}, "GET api/media-info": {}, "GET api/workspace": {},
    "GET api/searcher-inbox": { batches: [] }, ...routes,
  });
  let now = 100000;
  class Clock extends Date { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } }
  const { ctx, timers } = context(fetchOverride || fetch, { document, Date: Clock, addEventListener: on,
    location: { origin: "http://localhost:8790" }, setTimeout: () => 0 });
  const js = run(read("clipper/web/app.js"), ctx);
  return { js, ctx, ids, calls, timers, document, advance: (ms) => { now += ms; },
    emit: async (event) => { for (const fn of events[event] || []) fn(); await settle(); } };
}
const inboxCalls = (p) => p.calls.filter(c => c.path === "api/searcher-inbox");

test("automatic check starts honest, records success, and preserves it after HTTP, JSON and network failures", async () => {
  let mode = "success";
  const p = page({}, async (url) => {
    if (url !== "api/searcher-inbox") return { ok: true, json: async () => ({}) };
    if (mode === "network") throw Error("offline");
    return { ok: mode !== "http", json: async () => { if (mode === "json") throw SyntaxError("bad JSON"); return { batches: [] }; } };
  });
  assert.match(p.ids["intake-status"].textContent, /not checked yet/i);
  await p.js("autoPullFromSearcher()");
  const good = p.ids["intake-status"].textContent;
  assert.match(good, /last checked/i);
  for (mode of ["http", "json", "network"]) {
    p.advance(5000);
    await p.js("autoPullFromSearcher()");
    assert.ok(p.ids["intake-status"].textContent.includes(good));
    assert.match(p.ids["intake-status"].textContent, /unavailable/i);
  }
});

test("a swallowed workspace lookup failure cannot become a successful automatic check", async () => {
  const p = page({ "GET api/workspace": () => [503, {}] });
  await p.js("autoPullFromSearcher()");
  assert.match(p.ids["intake-status"].textContent, /not checked yet.*unavailable/i);
});

test("held workspace return checks only the inbox, coalesces events, and keeps pull errors", async () => {
  const p = page({ "GET api/searcher-inbox": { batches: [{ batch_id: "b2", clip_count: 1 }] } });
  p.js('clips.push({ status: "ready" }); setPullStatus("Automatic pull failed: fixture", true)');
  await p.emit("visibilitychange");
  await p.emit("focus");
  await p.emit("resume");
  assert.equal(inboxCalls(p).length, 1);
  assert.equal(p.calls.some(c => c.method === "POST"), false);
  assert.match(p.ids["intake-status"].textContent, /last checked/i);
  assert.match(p.ids["pull-status"].textContent, /Automatic pull failed/);
  assert.match(p.ids["searcher-waiting"].textContent, /b2/);
});

test("shell return immediately checks hidden Clip while Search stays selected and Home keeps separate freshness", async () => {
  let release;
  const p = page({
    "GET api/searcher-inbox": () => new Promise(resolve => { release = resolve; }),
    "POST api/pull-from-searcher": { batch_id: "b1", clip_count: 1, jobs: [{ id: "j1", status: "ready", words: [] }] },
    "POST api/jobs/j1/transcribe": { status: "ready", words: [] },
  });
  p.document.visibilityState = "hidden"; // display:none Clip gets no visibility return
  const events = {}, fields = { "[data-intake-freshness]": element() }, views = ["home", "search", "clip", "post"].map(name => {
    const el = element(); el.dataset.view = name;
    el.active = name === "search";
    el.classList.toggle = (_, value) => { el.active = value; };
    el.classList.contains = () => el.active;
    return el;
  });
  const frame = element(); frame.contentWindow = p.ctx;
  const document = { visibilityState: "visible", addEventListener: (type, fn) => { events[type] = fn; },
    querySelectorAll: selector => selector === ".view" ? views : [],
    querySelector: selector => selector === '[data-view="clip"] iframe' ? frame : selector === '[data-view="home"]' ? views[0] : (fields[selector] ||= element()),
    createElement: () => element() };
  const { ctx } = context(async () => ({ ok: true, json: async () => ({}) }), {
    document, addEventListener: (type, fn) => { events[type] = fn; }, location: { hash: "#search" },
  });
  run(read("ricesuite/shell/shell.js"), ctx);
  assert.match(fields["[data-intake-freshness]"].textContent, /not checked yet/i);
  events.visibilitychange(); events.focus();
  await settle();
  assert.equal(inboxCalls(p).length, 1, "one immediate check through the shell, before any timer");
  release([200, { batches: [{ batch_id: "b1", clip_count: 1 }] }]); await settle();
  assert.equal(p.calls.filter(c => c.method === "POST" && c.path === "api/pull-from-searcher").length, 1);
  assert.equal(p.calls.filter(c => c.path === "api/jobs/j1/transcribe").length, 1);
  assert.match(fields["[data-intake-freshness]"].textContent, /last checked/i);
  assert.equal(fields["[data-freshness]"]?.textContent || "", "");
  assert.equal(views.find(v => v.active).dataset.view, "search");
});

test("overlapping return and timer checks perform only one pull/transcription and respect backoff", async () => {
  let release;
  const p = page({
    "GET api/searcher-inbox": () => new Promise(resolve => { release = resolve; }),
    "POST api/pull-from-searcher": { batch_id: null, clip_count: 0, jobs: [] },
  });
  const first = p.js("autoPullFromSearcher()"); await settle();
  const second = p.js("autoPullFromSearcher()"); await settle();
  assert.equal(inboxCalls(p).length, 1);
  release([200, { batches: [{ batch_id: "b1", clip_count: 1 }] }]);
  await Promise.all([first, second]);
  assert.equal(p.calls.filter(c => c.method === "POST" && c.path === "api/pull-from-searcher").length, 1);
  p.advance(1000); p.js("autoPullPausedUntil = Date.now() + 60000");
  await p.emit("focus");
  assert.equal(p.calls.filter(c => c.method === "POST" && c.path === "api/pull-from-searcher").length, 1);
});


test("a failed automatic pull keeps the last successful check and exposes failure through backoff", async () => {
  let waiting = false;
  const p = page({
    "GET api/searcher-inbox": () => [200, { batches: waiting ? [{ batch_id: "b1", clip_count: 1 }] : [] }],
    "POST api/pull-from-searcher": () => [500, { detail: "fixture failure" }],
  });
  await p.js("autoPullFromSearcher()");
  const good = p.ids["intake-status"].textContent;
  waiting = true; p.advance(5000);
  await p.js("autoPullFromSearcher()");
  assert.ok(p.ids["intake-status"].textContent.includes(good));
  assert.match(p.ids["intake-status"].textContent, /unavailable/);
  assert.match(p.ids["pull-status"].textContent, /Automatic pull failed: fixture failure/);
  p.advance(1000); await p.emit("focus");
  assert.match(p.ids["intake-status"].textContent, /unavailable/);
  assert.equal(p.calls.filter(c => c.method === "POST").length, 1);
});

test("valid JSON with no inbox batch array is unavailable; an old success is explicitly delayed", async () => {
  let valid = true;
  const p = page({ "GET api/searcher-inbox": () => [200, valid ? { batches: [] } : {}] });
  await p.js("autoPullFromSearcher()");
  p.advance(16000);
  assert.match(p.js("automaticIntakeStatus()"), /delayed/);
  valid = false; await p.js("autoPullFromSearcher()");
  assert.match(p.ids["intake-status"].textContent, /last checked.*unavailable/);
});

test("a pull with truncated JSON keeps its key and recovers committed custody without a manual click", async () => {
  const keys = [];
  const batch = { batch_id: "b1", clip_count: 1, jobs: [{ id: "j1", status: "ready", words: [] }] };
  const p = page({}, async (url) => {
    const pathname = String(url).split("?")[0];
    if (pathname === "api/pull-from-searcher") {
      keys.push(new URL(url, "http://fixture/").searchParams.get("pull_key"));
      return { ok: true, json: async () => {
        if (keys.length === 1) throw SyntaxError("truncated response");
        return { ...batch, replayed: true };
      } };
    }
    const body = pathname === "api/searcher-inbox" ? { batches: keys.length ? [] : [{ batch_id: "b1", clip_count: 1 }] }
      : pathname === "api/workspace" ? (keys.length ? batch : {}) : { status: "ready", words: [] };
    return { ok: true, json: async () => body };
  });
  await p.js("autoPullFromSearcher()");
  assert.ok(p.js("readTab().pendingPullKey"), "unknown pull outcome must retain recovery key");
  assert.match(p.ids["intake-status"].textContent, /not checked yet.*unavailable/);
  await p.js("autoPullFromSearcher()"); await settle();
  assert.equal(keys.length, 2);
  assert.equal(keys[1], keys[0]);
  assert.equal(p.js("clips[0].jobId"), "j1");
  assert.match(p.ids["intake-status"].textContent, /last checked/);
});

test("malformed successful pull payloads do not advance freshness or discard their recovery key", async () => {
  for (const malformed of [
    {}, { clip_count: 1, batch_id: "b1", jobs: [] },
    { clip_count: 0, batch_id: null, jobs: [{ id: "j1" }] },
    { clip_count: 1, batch_id: "b1", jobs: [{}] },
  ]) {
    const p = page({
      "GET api/searcher-inbox": { batches: [{ batch_id: "b1", clip_count: 1 }] },
      "POST api/pull-from-searcher": malformed,
    });
    await p.js("autoPullFromSearcher()");
    assert.match(p.ids["intake-status"].textContent, /not checked yet.*unavailable/);
    assert.ok(p.js("readTab().pendingPullKey"));
    assert.equal(p.js("clips.length"), 0);
  }
});

test("restoring a remembered batch immediately checks the waiting inbox and never retranscribes held work", async () => {
  let available = false;
  const p = page({
    "GET api/workspace": { batch_id: "b1", clip_count: 1, jobs: [{ id: "j1", status: "ready", words: [] }] },
    "GET api/searcher-inbox": () => available ? [200, { batches: [{ batch_id: "b2", clip_count: 1 }] }] : [503, {}],
    "POST api/jobs/j1/transcribe": { status: "ready", words: [] },
  });
  p.js('writeTab({ pulledBatchId: "b1" })');
  await p.js("requestAutomaticIntakeCheck()"); await settle();
  assert.equal(inboxCalls(p).length, 1, "the restoration must still check the inbox on return");
  assert.match(p.ids["intake-status"].textContent, /not checked yet.*unavailable/);
  available = true; p.advance(1000);
  await p.js("requestAutomaticIntakeCheck()"); await settle();
  assert.match(p.ids["searcher-waiting"].textContent, /b2/);
  assert.match(p.ids["intake-status"].textContent, /last checked/);
  assert.equal(p.calls.filter(c => c.path === "api/jobs/j1/transcribe").length, 1);
  assert.equal(p.calls.some(c => c.path === "api/pull-from-searcher"), false);
});
