"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../ricesearcher/web/static/workspace-run.js"), "utf8");
function boot(fetcher) {
  const storage = new Map();
  const timers = [];
  const context = vm.createContext({
    document: {getElementById: () => null},
    crypto: {randomUUID: () => "attempt"},
    localStorage: {getItem: (k) => storage.get(k), setItem: (k, v) => storage.set(k, v)},
    setTimeout: (fn) => {timers.push(fn); return timers.length;}, clearTimeout: () => {},
    fetch: fetcher,
  });
  // Discovery itself is tested separately, so keep the mutation tests precise.
  vm.runInContext(source.replace(/wsDiscover\(\);\s*$/, ""), context);
  return {context, timers, storage};
}
function response(data, status = 200) { return {ok: status === 200, status, json: async () => data}; }
function run(status = "active") { return {id: "attempt", profile: "original", status, stage: "scoring", items: []}; }

test("lost start response reads the exact key and duplicate clicks never repeat mutations", async () => {
  const calls = [];
  const h = boot(async (url, options) => {
    calls.push({url, options});
    if (options) throw new Error("lost response");
    return response(run());
  });
  await vm.runInContext('wsSubmit("api/runs", {profile: "original", score: true})', h.context);
  await vm.runInContext('wsSubmit("api/runs", {profile: "changed", score: true})', h.context);
  assert.equal(calls.filter((c) => c.options).length, 1);
  assert.equal(calls[1].url, "api/runs/attempt");
  assert.equal(h.storage.get("ricesearcher.processing.attempt"), "attempt");
  assert.equal(JSON.parse(calls[0].options.body).profile, "original");
});

test("read loss preserves last progress and unavailable restart records never mutate", async () => {
  let status = 200;
  const calls = [];
  const h = boot(async (url, options) => {calls.push({url, options}); return status === 500 ? Promise.reject(new Error("offline")) : response(run(), status);});
  await vm.runInContext('wsObserve("attempt")', h.context);
  status = 500;
  await vm.runInContext('wsObserve("attempt")', h.context);
  assert.equal(vm.runInContext('workspaceRun.profile', h.context), "original");
  status = 404;
  await vm.runInContext('wsObserve("attempt")', h.context);
  assert.equal(vm.runInContext('workspaceRun', h.context), null);
  assert.ok(calls.every((c) => !c.options));
});

test("late observations cannot overwrite a newer generation", async () => {
  let resolve;
  const h = boot(() => new Promise((r) => {resolve = r;}));
  const pending = vm.runInContext('wsObserve("old")', h.context);
  vm.runInContext('++workspaceGeneration; workspaceRun = {id:"new"}', h.context);
  resolve(response({id: "old", status: "active"}));
  await pending;
  assert.equal(vm.runInContext('workspaceRun.id', h.context), "new");
});

test("a quickly completed admitted run refreshes once and reload discovery is read-only", async () => {
  const calls = [];
  const h = boot(async (url, options) => {calls.push({url, options}); return response(url.endsWith("current") ? {run: run("complete")} : run("complete"));});
  vm.runInContext('var refreshed = 0; function wsBatchFinished() { ++refreshed; }', h.context);
  await vm.runInContext('wsSubmit("api/runs", {profile: "original"})', h.context);
  await vm.runInContext('wsObserve("attempt")', h.context);
  assert.equal(vm.runInContext('refreshed', h.context), 1);
  await vm.runInContext('wsDiscover()', h.context);
  assert.equal(calls.filter((c) => c.options).length, 1);
});

test("known admission rejection keeps its reason without pretending a run expired", async () => {
  const calls = [];
  const h = boot(async (url, options) => { calls.push({url, options}); return response({detail: "ANTHROPIC_API_KEY is missing"}, 409); });
  vm.runInContext('var messages = []; wsRenderRun = message => messages.push(message)', h.context);
  await vm.runInContext('wsSubmit("api/runs", {profile: "original", score:true})', h.context);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.method, "POST");
  assert.match(vm.runInContext('messages.at(-1)', h.context), /Batch not started: ANTHROPIC_API_KEY is missing/);
});
