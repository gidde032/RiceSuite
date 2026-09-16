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

function response(payload, ok = true) {
  return { ok, status: ok ? 200 : 500, json: async () => payload };
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

async function nextRequest(harness) {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    if (harness.requests.length) return harness.requests.shift();
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.fail("expected a fetch request");
}

async function boot(initialSlices = []) {
  const nodes = Object.fromEntries(
    ["list", "count", "status", "statusFilter", "profileSelect", "handoffBtn"].map(
      (id) => [id, new FakeNode("div", id)],
    ),
  );
  nodes.statusFilter.value = "candidate";
  const requests = [];
  const storage = new Map();
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
  const harness = { context, nodes, requests };

  let request = await nextRequest(harness);
  assert.equal(request.url, "/api/profiles");
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

function slice(id, profile) {
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
