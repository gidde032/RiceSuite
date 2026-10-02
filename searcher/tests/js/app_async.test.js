"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const APP_JS = fs.readFileSync(
  path.join(__dirname, "../../ricesearcher/web/static/app.js"),
  "utf8",
);

class FakeNode {
  constructor(tag, id = "") {
    this.tag = tag;
    this.id = id;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.value = "";
    this.textContent = "";
    this.disabled = false;
    this.inert = false;
    this.classList = { toggle() {} };
  }

  addEventListener(name, callback) {
    this.listeners[name] = callback;
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "value") this.value = String(value);
  }
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function response(payload, ok = true, status = ok ? 200 : 500) {
  return { ok, status, json: async () => payload };
}

function textOf(node) {
  return node.textContent + node.children.map(textOf).join("");
}

function findNode(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children) {
    const found = findNode(child, predicate);
    if (found) return found;
  }
  return null;
}

function cardInput(harness, label) {
  return findNode(
    harness.nodes.list,
    (node) => node.tag === "input" && node.attributes["aria-label"] === "intended " + label + " (seconds)",
  );
}

function cardMessage(harness) {
  return findNode(harness.nodes.list, (node) => node.attributes.class === "card-msg");
}

async function nextRequest(harness) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (harness.requests.length) return harness.requests.shift();
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.fail("expected a fetch request");
}

async function boot(initialSlices = [], savedOperation = null) {
  const nodes = Object.fromEntries(
    ["list", "count", "status", "progress", "statusFilter", "profileSelect", "handoffBtn"].map(
      (id) => [id, new FakeNode("div", id)],
    ),
  );
  nodes.statusFilter.value = "candidate";
  const requests = [];
  const storage = new Map();
  const session = new Map();
  if (savedOperation) session.set("ricesearcher.handoff.operation", JSON.stringify(savedOperation));
  const timers = new Map();
  let timerId = 0;
  const document = {
    getElementById: (id) => nodes[id],
    createElement: (tag) => new FakeNode(tag),
    createTextNode: (value) => {
      const node = new FakeNode("#text");
      node.textContent = String(value);
      return node;
    },
  };
  const context = vm.createContext({
    console,
    document,
    setTimeout(callback) { const id = ++timerId; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); },
    sessionStorage: { getItem: (key) => session.get(key) ?? null, setItem: (key, value) => session.set(key, value) },
    encodeURIComponent,
    fetch(url, options) {
      const pending = deferred();
      requests.push({ url, options, ...pending });
      return pending.promise;
    },
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
    },
  });
  vm.runInContext(APP_JS, context);
  const harness = { context, nodes, requests, timers, session };

  let request = await nextRequest(harness);
  assert.equal(request.url, "api/profiles");
  request.resolve(
    response([
      { id: "alpha", name: "Alpha", selected: 0 },
      { id: "beta", name: "Beta", selected: 0 },
    ]),
  );
  request = await nextRequest(harness);
  assert.match(request.url, /profile=alpha/);
  request.resolve(response(initialSlices));
  await new Promise((resolve) => setImmediate(resolve));
  return harness;
}

function slice(id, profile, overrides = {}) {
  return {
    id,
    profile_id: profile,
    status: "candidate",
    media_url: null,
    pad_in: 0,
    pad_out: 10,
    target_in: 2,
    target_out: 8,
    score: 0.8,
    source_title: profile + " source",
    rights_risk: "med",
    heuristic_score: 0.4,
    stale: false,
    dup_of: null,
    rationale: "",
    transcript_span: "moment",
    ...overrides,
  };
}

test("only the latest profile load may render cards", async () => {
  const harness = await boot();
  harness.nodes.profileSelect.value = "alpha";
  const alphaLoad = harness.context.load();
  const alphaRequest = await nextRequest(harness);

  harness.nodes.profileSelect.value = "beta";
  const betaLoad = harness.context.load();
  assert.match(textOf(harness.nodes.list), /Loading slices/);
  const betaRequest = await nextRequest(harness);

  betaRequest.resolve(response([]));
  await betaLoad;
  assert.equal(harness.nodes.count.textContent, "0 slices");

  alphaRequest.resolve(response([slice("a", "alpha")]));
  await alphaLoad;
  assert.equal(harness.nodes.count.textContent, "0 slices");
  assert.doesNotMatch(textOf(harness.nodes.list), /alpha source/i);
});

test("pending card mutation blocks handoff and repeated status writes", async () => {
  const harness = await boot([slice("a", "alpha")]);
  const select = findNode(
    harness.nodes.list,
    (node) => node.tag === "button" && textOf(node) === "Select",
  );
  const reject = findNode(
    harness.nodes.list,
    (node) => node.tag === "button" && textOf(node) === "Reject",
  );
  assert.ok(select && reject);

  const mutation = select.listeners.click();
  const statusRequest = await nextRequest(harness);
  assert.match(statusRequest.url, /\/status$/);
  assert.equal(harness.nodes.handoffBtn.disabled, true);

  await reject.listeners.click();
  assert.equal(harness.requests.length, 0);
  await harness.nodes.handoffBtn.listeners.click();
  assert.equal(harness.requests.length, 0);

  statusRequest.resolve(response({ id: "a", status: "selected" }));
  const profilesRequest = await nextRequest(harness);
  profilesRequest.resolve(
    response([
      { id: "alpha", name: "Alpha", selected: 1 },
      { id: "beta", name: "Beta", selected: 0 },
    ]),
  );
  await mutation;
  assert.equal(harness.nodes.handoffBtn.disabled, false);
  assert.match(harness.nodes.handoffBtn.textContent, /Send 1 selected/);
});

test("review preview starts at the saved target interval and follows a successful save", async () => {
  const harness = await boot([
    slice("a", "alpha", {
      media_url: "/media/a.mp4",
      pad_in: 1.5,
      pad_out: 9.5,
      target_in: 1e-7,
      target_out: 2e-7,
    }),
  ]);
  const video = findNode(harness.nodes.list, (node) => node.tag === "video");
  const windowLabel = findNode(
    harness.nodes.list,
    (node) => node.attributes.class === "win",
  );
  assert.equal(video.src, "/media/a.mp4#t=0.0000001,0.0000002");
  assert.equal(textOf(windowLabel), "selected 0.0000001–0.0000002s");

  const inInput = cardInput(harness, "in");
  const outInput = cardInput(harness, "out");
  assert.equal(inInput.value, "0.0000001");
  assert.equal(outInput.value, "0.0000002");
  assert.equal(inInput.attributes.step, "any");
  assert.equal(outInput.attributes.step, "any");
  inInput.value = "3";
  outInput.value = "6";
  const saving = inInput.listeners.change({ type: "change", target: inInput });
  const request = await nextRequest(harness);
  assert.equal(request.options.method, "PATCH");
  assert.deepEqual(JSON.parse(request.options.body), { target_in: 3, target_out: 6 });

  request.resolve(response({ target_in: 3.14, target_out: 5.96 }));
  await saving;
  assert.equal(inInput.value, "3.14");
  assert.equal(outInput.value, "5.96");
  assert.equal(video.src, "/media/a.mp4#t=3.14,5.96");
  assert.equal(textOf(windowLabel), "selected 3.14–5.96s");
});

test("invalid window input stays typed so the reviewer can correct it", async () => {
  const harness = await boot([slice("a", "alpha")]);
  const inInput = cardInput(harness, "in");
  const outInput = cardInput(harness, "out");
  inInput.value = "not-a-number";
  outInput.value = "6";

  await inInput.listeners.change({ type: "change", target: inInput });

  assert.equal(inInput.value, "not-a-number");
  assert.equal(outInput.value, "6");
  assert.equal(cardMessage(harness).textContent, "in/out must be numbers");
  assert.equal(harness.requests.length, 0);
});

test("window validation detail is shown verbatim without resetting typed values", async () => {
  const harness = await boot([slice("a", "alpha")]);
  const inInput = cardInput(harness, "in");
  const outInput = cardInput(harness, "out");
  assert.equal(inInput.attributes.step, "any");
  assert.equal(outInput.attributes.step, "any");
  inInput.value = "3";
  outInput.value = "6";
  const saving = inInput.listeners.change({ type: "change", target: inInput });
  const request = await nextRequest(harness);

  request.resolve(response({ detail: "target interval must be inside source" }, false, 422));
  await saving;

  assert.equal(inInput.value, "3");
  assert.equal(outInput.value, "6");
  assert.equal(cardMessage(harness).textContent, "target interval must be inside source");
});

test("window failures use a status fallback for non-string detail and preserve typed values", async () => {
  const harness = await boot([slice("a", "alpha")]);
  const inInput = cardInput(harness, "in");
  const outInput = cardInput(harness, "out");
  inInput.value = "3";
  outInput.value = "6";
  const saving = inInput.listeners.change({ type: "change", target: inInput });
  const request = await nextRequest(harness);

  request.resolve(response({ detail: { reason: "bad interval" } }, false, 400));
  await saving;

  assert.equal(inInput.value, "3");
  assert.equal(outInput.value, "6");
  assert.equal(cardMessage(harness).textContent, "couldn't save window (400)");
});

test("network window failures preserve typed values and explain the failure", async () => {
  const harness = await boot([slice("a", "alpha")]);
  const inInput = cardInput(harness, "in");
  const outInput = cardInput(harness, "out");
  inInput.value = "3";
  outInput.value = "6";
  const saving = inInput.listeners.change({ type: "change", target: inInput });
  const request = await nextRequest(harness);

  request.reject(new Error("offline"));
  await saving;

  assert.equal(inInput.value, "3");
  assert.equal(outInput.value, "6");
  assert.equal(cardMessage(harness).textContent, "couldn't save window: offline");
});

test("offline-scored slices carry an offline badge; LLM-scored slices do not (#2)", async () => {
  const harness = await boot([
    slice("a", "alpha", { scorer_model: "heuristic-offline" }),
    slice("b", "alpha", { scorer_model: "claude-haiku-4-5" }),
  ]);
  const offlineBadges = [];
  const collect = (node) => {
    if (node.attributes.class === "badge offline") offlineBadges.push(node);
    node.children.forEach(collect);
  };
  collect(harness.nodes.list);
  assert.equal(offlineBadges.length, 1);
  assert.equal(textOf(offlineBadges[0]), "offline score");
  const scores = [];
  const collectScores = (node) => {
    if (node.attributes.class === "score") scores.push(node.attributes.title);
    node.children.forEach(collectScores);
  };
  collectScores(harness.nodes.list);
  assert.deepEqual(scores, ["offline heuristic score (not LLM-scored)", "LLM clippability score"]);
});

function operationSnapshot(id, overrides = {}) {
  return {
    operation_id: id, operation: "handoff", scope: "alpha", status: "active", stage: "preparing",
    items: [{ id: "a", title: "alpha source", position: 1, state: "preparing" }],
    current: { id: "a", title: "alpha source", position: 1, state: "preparing" },
    completed: 0, total: 1, detail: "", batch_id: "", published: false,
    ...overrides,
  };
}

function runTimer(harness) {
  const entry = harness.timers.entries().next().value;
  assert.ok(entry, "a read-only poll is scheduled");
  harness.timers.delete(entry[0]);
  return entry[1]();
}

async function finishRefresh(harness) {
  const profiles = await nextRequest(harness);
  assert.equal(profiles.url, "api/profiles");
  profiles.resolve(response([{ id: "alpha", name: "Alpha", selected: 0 }, { id: "beta", name: "Beta", selected: 0 }]));
  const slices = await nextRequest(harness);
  assert.match(slices.url, /api\/slices/);
  slices.resolve(response([]));
}

test("actual handoff progress preserves validation, reports preparation before commit, and stops on success", async () => {
  const harness = await boot([slice("a", "alpha")]);
  cardMessage(harness).textContent = "window validation remains here";
  const sending = harness.context.handoff();
  const post = await nextRequest(harness);
  assert.equal(post.options.method, "POST");
  const attempt = JSON.parse(post.options.body).observation_id;
  assert.ok(attempt);
  assert.equal(harness.nodes.list.inert, true);
  const polling = runTimer(harness);
  const poll = await nextRequest(harness);
  assert.equal(poll.options, undefined);
  assert.ok(poll.url.includes(attempt));
  poll.resolve(response(operationSnapshot(attempt)));
  await polling;
  assert.match(textOf(harness.nodes.progress), /preparing clip 1 of 1/);
  assert.doesNotMatch(textOf(harness.nodes.progress), /handed off/);
  assert.equal(cardMessage(harness).textContent, "window validation remains here");
  assert.match(textOf(harness.nodes.list), /Preparing clip/);

  const committing = runTimer(harness);
  const commitPoll = await nextRequest(harness);
  commitPoll.resolve(response(operationSnapshot(attempt, { stage: "committing", completed: 1, current: null, items: [{ id: "a", state: "prepared" }] })));
  await committing;
  assert.match(textOf(harness.nodes.progress), /1 \/ 1 prepared/);
  assert.match(textOf(harness.nodes.progress), /Handing off batch/);
  assert.doesNotMatch(textOf(harness.nodes.progress), /handed off/);

  post.resolve(response({ batch_id: "batch_fixture", clip_count: 1 }));
  await finishRefresh(harness);
  await sending;
  assert.match(textOf(harness.nodes.progress), /1 \/ 1 handed off/);
  assert.equal(harness.nodes.list.inert, false);
  assert.equal(harness.timers.size, 0);
});

test("a lost mutation reply reconnects read-only and never repeats the write", async () => {
  const harness = await boot([slice("a", "alpha")]);
  const sending = harness.context.handoff();
  const post = await nextRequest(harness);
  const attempt = JSON.parse(post.options.body).observation_id;
  post.reject(new Error("reply lost"));
  const reconnect = await nextRequest(harness);
  assert.equal(reconnect.options, undefined);
  assert.ok(reconnect.url.includes(attempt));
  assert.match(textOf(harness.nodes.progress), /Result unknown/);
  reconnect.resolve(response(operationSnapshot(attempt)));
  await sending;
  assert.equal(harness.nodes.list.inert, true);
  assert.equal(harness.timers.size, 1);
  const completion = runTimer(harness);
  const poll = await nextRequest(harness);
  poll.resolve(response(operationSnapshot(attempt, { status: "complete", completed: 1, batch_id: "batch_fixture", detail: "Batch handed off to Clipper.", current: null })));
  await finishRefresh(harness);
  await completion;
  assert.equal(harness.timers.size, 0);
  assert.equal(harness.nodes.list.inert, false);
  assert.match(textOf(harness.nodes.progress), /handed off/);
  assert.equal(harness.requests.length, 0);
});

test("read failures freeze the last snapshot and profile switches never attach its statuses", async () => {
  const harness = await boot([slice("a", "alpha")]);
  harness.context.beginObservation("attempt-scope", "alpha");
  const first = runTimer(harness);
  let request = await nextRequest(harness);
  request.resolve(response(operationSnapshot("attempt-scope", { completed: 1, current: null, items: [{ id: "a", state: "prepared" }] })));
  await first;
  assert.match(textOf(harness.nodes.progress), /1 \/ 1 prepared/);
  const failed = runTimer(harness);
  request = await nextRequest(harness);
  request.reject(new Error("offline"));
  await failed;
  assert.match(textOf(harness.nodes.progress), /1 \/ 1 prepared/);
  assert.match(textOf(harness.nodes.progress), /Result unknown/);
  assert.match(textOf(harness.nodes.list), /Last known: prepared/);
  harness.nodes.profileSelect.value = "beta";
  const loading = harness.context.load();
  request = await nextRequest(harness);
  request.resolve(response([slice("a", "beta")]));
  await loading;
  assert.match(textOf(harness.nodes.progress), /alpha/);
  assert.doesNotMatch(textOf(harness.nodes.list), /Last known|Prepared|Preparing clip/);
  harness.context.stopObservation();
  assert.equal(harness.timers.size, 0);
});

test("reload reconnects read-only and unavailable progress unlocks review without retrying", async () => {
  const snapshot = operationSnapshot("saved-attempt");
  const harness = await boot([], { id: "saved-attempt", profile: "alpha", unknown: true, snapshot });
  const reconnect = await nextRequest(harness);
  assert.match(reconnect.url, /progress\/saved-attempt\?profile=alpha/);
  assert.equal(reconnect.options, undefined);
  reconnect.resolve(response({ detail: "unavailable" }, false, 404));
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(textOf(harness.nodes.progress), /Operation unavailable; result unknown/);
  assert.equal(harness.nodes.list.inert, false);
  assert.equal(harness.nodes.handoffBtn.disabled, false);
  assert.equal(harness.timers.size, 0);
  assert.equal(harness.requests.length, 0);
});

test("late progress from an abandoned attempt cannot overwrite a newer operation", async () => {
  const harness = await boot();
  harness.context.beginObservation("old-attempt", "alpha");
  const oldPoll = runTimer(harness);
  const oldRequest = await nextRequest(harness);
  harness.context.beginObservation("new-attempt", "beta");
  oldRequest.resolve(response(operationSnapshot("old-attempt", { status: "complete", batch_id: "old-batch" })));
  await oldPoll;
  assert.match(textOf(harness.nodes.progress), /beta/);
  assert.doesNotMatch(textOf(harness.nodes.progress), /old-batch|handed off/);
  harness.context.stopObservation();
});

test("a terminal publication error names the published batch and stops observation", async () => {
  const harness = await boot([slice("a", "alpha")]);
  harness.context.beginObservation("published-error", "alpha");
  const polling = runTimer(harness);
  const request = await nextRequest(harness);
  request.resolve(response(operationSnapshot("published-error", { status: "unconfirmed", published: true, batch_id: "batch_published", detail: "marking unavailable", completed: 1, current: null })));
  await polling;
  assert.match(textOf(harness.nodes.progress), /Batch batch_published was published; final library confirmation failed/);
  assert.doesNotMatch(textOf(harness.nodes.progress), /1 \/ 1 handed off/);
  assert.equal(harness.timers.size, 0);
});
