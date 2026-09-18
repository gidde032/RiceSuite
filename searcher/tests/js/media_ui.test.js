"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const MEDIA_JS = fs.readFileSync(
  path.join(__dirname, "../../ricesearcher/web/static/media.js"),
  "utf8",
);

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  toggle(name, force) {
    const present = force === undefined ? !this.values.has(name) : !!force;
    if (present) this.values.add(name);
    else this.values.delete(name);
    return present;
  }

  contains(name) {
    return this.values.has(name);
  }
}

class FakeNode {
  constructor(tag, id = "") {
    this.tag = tag;
    this.id = id;
    this.children = [];
    this.parentNode = null;
    this.listeners = new Map();
    this.attributes = {};
    this.dataset = {};
    this.value = "";
    this._textContent = "";
    this.disabled = false;
    this.inert = false;
    this.focused = false;
    this.classList = new FakeClassList();
    this.lastDispatch = Promise.resolve();
  }

  get textContent() {
    return this._textContent;
  }

  set textContent(value) {
    this._textContent = String(value);
    this.replaceChildren();
  }

  addEventListener(name, callback, options = {}) {
    const once = !!(options && typeof options === "object" && options.once);
    const listeners = this.listeners.get(name) || [];
    listeners.push({ callback, once });
    this.listeners.set(name, listeners);
  }

  removeEventListener(name, callback) {
    const listeners = this.listeners.get(name) || [];
    this.listeners.set(name, listeners.filter((listener) => listener.callback !== callback));
  }

  dispatchEvent(event) {
    const type = typeof event === "string" ? event : event.type;
    if (type === "click" && this.disabled) {
      this.lastDispatch = Promise.resolve();
      return true;
    }
    const listeners = [...(this.listeners.get(type) || [])];
    const pending = [];
    for (const listener of listeners) {
      if (!(this.listeners.get(type) || []).includes(listener)) continue;
      if (listener.once) this.removeEventListener(type, listener.callback);
      const result = listener.callback({ type, target: this });
      if (result && typeof result.then === "function") pending.push(result);
    }
    this.lastDispatch = Promise.all(pending);
    return true;
  }

  async click() {
    this.dispatchEvent({ type: "click" });
    await this.lastDispatch;
  }

  append(...children) {
    for (const child of children) {
      child.parentNode = this;
      this.children.push(child);
    }
  }

  replaceChildren(...children) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    this.append(...children);
  }

  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }

  focus() {
    this.focused = true;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "value") this.value = String(value);
    if (name.startsWith("data-")) {
      const key = name.slice(5).replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
      this.dataset[key] = String(value);
    }
    if (name === "class") {
      for (const token of String(value).split(/\s+/).filter(Boolean)) this.classList.values.add(token);
    }
  }
}

class TimerScheduler {
  constructor() {
    this.now = 0;
    this.nextId = 1;
    this.timers = new Map();
  }

  setTimeout(callback, delay) {
    const id = this.nextId++;
    this.timers.set(id, { at: this.now + delay, callback });
    return id;
  }

  clearTimeout(id) {
    this.timers.delete(id);
  }

  advance(ms) {
    const target = this.now + ms;
    while (true) {
      const due = [...this.timers.entries()]
        .filter(([, timer]) => timer.at <= target)
        .sort((a, b) => a[1].at - b[1].at || a[0] - b[0]);
      if (!due.length) break;
      const [id, timer] = due[0];
      this.timers.delete(id);
      this.now = timer.at;
      timer.callback();
    }
    this.now = target;
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

function source(id = "source-1", overrides = {}) {
  return {
    id,
    title: "A source",
    ref: "https://example.test/source",
    slice_count: 2,
    size_bytes: 2 * 1024 * 1024,
    duration_s: 125.5,
    acquired_at: "2026-09-18T12:34:56Z",
    ...overrides,
  };
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

function findButton(node, label) {
  return findNode(node, (child) => child.tag === "button" && textOf(child) === label);
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
}

function takeRequest(harness, expectedUrl) {
  assert.ok(harness.requests.length, "expected a fetch request");
  const request = harness.requests.shift();
  if (expectedUrl) assert.equal(request.url, expectedUrl);
  return request;
}

async function boot(initialSources = [], initialResult = "success") {
  const nodes = Object.fromEntries(
    ["list", "count", "status", "clearAllBtn"].map((id) => [id, new FakeNode("div", id)]),
  );
  nodes.clearAllBtn.tag = "button";
  nodes.clearAllBtn.textContent = "Clear entire cache…";

  const requests = [];
  const allRequests = [];
  const scheduler = new TimerScheduler();
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
      const request = { url, options, ...pending };
      requests.push(request);
      allRequests.push(request);
      return request.promise;
    },
    setTimeout: scheduler.setTimeout.bind(scheduler),
    clearTimeout: scheduler.clearTimeout.bind(scheduler),
  });
  vm.runInContext(MEDIA_JS, context);
  const harness = { context, nodes, requests, allRequests, scheduler };
  const request = takeRequest(harness, "/api/sources");
  if (initialResult === "success") request.resolve(response(initialSources));
  else if (initialResult === "http-error") request.resolve(response({}, false, 503));
  else request.reject(new Error("network down"));
  await flush();
  return harness;
}

async function armDelete(harness) {
  const row = harness.nodes.list.children[0];
  assert.ok(row, "expected a media row");
  const deleteBtn = findButton(row, "Delete");
  assert.ok(deleteBtn);
  await deleteBtn.click();
  return { row, deleteBtn, actions: findNode(row, (node) => node.attributes.class === "media-actions") };
}

test("a row delete first click arms only, and cancel restores the original action", async () => {
  const harness = await boot([source()]);
  const { row, deleteBtn, actions } = await armDelete(harness);

  assert.equal(harness.requests.length, 0);
  assert.equal(actions.children.length, 2);
  assert.equal(textOf(actions), "Confirm deleteCancel");
  assert.equal(findButton(row, "Confirm delete").focused, true);

  await findButton(row, "Cancel").click();
  assert.equal(actions.children.length, 1);
  assert.equal(actions.children[0], deleteBtn);
  assert.equal(harness.requests.length, 0);
});

test("confirming a row delete posts once, ignores repeated clicks while pending, and refreshes after removal", async () => {
  const harness = await boot([source("a")]);
  const { row } = await armDelete(harness);
  const confirmBtn = findButton(row, "Confirm delete");
  const deleting = confirmBtn.click();
  const request = takeRequest(harness, "/api/sources/a/delete");
  assert.equal(request.options.method, "POST");
  assert.equal(confirmBtn.disabled, true);

  await confirmBtn.click();
  assert.equal(harness.requests.length, 0);

  request.resolve(response({ id: "a", deleted: true }));
  await flush();
  assert.equal(row.parentNode, null);
  const refresh = takeRequest(harness, "/api/sources");
  refresh.resolve(response([]));
  await deleting;

  assert.equal(harness.nodes.count.textContent, "0 sources");
  assert.match(harness.nodes.status.textContent, /deleted A source/);
  assert.equal(
    harness.allRequests.filter((item) => item.url === "/api/sources/a/delete").length,
    1,
  );
});

test("row delete HTTP and network failures retain the row and re-enable both controls", async () => {
  for (const failure of ["http", "network"]) {
    const harness = await boot([source("a")]);
    const { row } = await armDelete(harness);
    const confirmBtn = findButton(row, "Confirm delete");
    const cancelBtn = findButton(row, "Cancel");
    const deleting = confirmBtn.click();
    const request = takeRequest(harness, "/api/sources/a/delete");
    if (failure === "http") request.resolve(response({}, false, 503));
    else request.reject(new Error("connection lost"));
    await deleting;

    assert.equal(row.parentNode, harness.nodes.list);
    assert.equal(confirmBtn.disabled, false);
    assert.equal(cancelBtn.disabled, false);
    assert.match(textOf(row), failure === "http" ? /delete failed \(503\)/ : /delete failed: connection lost/);
    assert.equal(harness.requests.length, 0);
  }
});

test("clear-all first click arms without posting, and its timer disarms it after five seconds", async () => {
  const harness = await boot([]);
  const btn = harness.nodes.clearAllBtn;

  await btn.click();
  assert.equal(harness.requests.length, 0);
  assert.equal(btn.dataset.armed, "1");
  assert.equal(btn.textContent, "Click again to purge everything");

  harness.scheduler.advance(4999);
  assert.equal(btn.dataset.armed, "1");
  harness.scheduler.advance(1);
  assert.equal(btn.dataset.armed, "");
  assert.equal(btn.textContent, "Clear entire cache…");
  assert.equal(harness.requests.length, 0);
});

test("clear-all confirmation posts once, ignores repeated clicks while pending, and refreshes on success", async () => {
  const harness = await boot([source("a"), source("b")]);
  const btn = harness.nodes.clearAllBtn;

  await btn.click();
  const clearing = btn.click();
  const request = takeRequest(harness, "/api/cache/clear");
  assert.equal(request.options.method, "POST");
  assert.equal(btn.disabled, true);

  await btn.click();
  assert.equal(harness.requests.length, 0);

  request.resolve(response({ sources_deleted: 2, files_removed: 2 }));
  await flush();
  const refresh = takeRequest(harness, "/api/sources");
  refresh.resolve(response([]));
  await clearing;

  assert.equal(btn.disabled, false);
  assert.equal(harness.nodes.count.textContent, "0 sources");
  assert.equal(harness.nodes.status.textContent, "purged 2 source(s), removed 2 file(s)");
  assert.equal(
    harness.allRequests.filter((item) => item.url === "/api/cache/clear").length,
    1,
  );
});

test("an expired clear-all cycle can re-arm without an old timer disarming the fresh cycle", async () => {
  const harness = await boot([]);
  const btn = harness.nodes.clearAllBtn;

  await btn.click();
  harness.scheduler.advance(5000);
  await btn.click();
  assert.equal(btn.dataset.armed, "1");
  assert.equal(btn.textContent, "Click again to purge everything");

  harness.scheduler.advance(4999);
  assert.equal(btn.dataset.armed, "1");
  harness.scheduler.advance(1);
  assert.equal(btn.dataset.armed, "");
  assert.equal(harness.requests.length, 0);
});

test("a completed clear-all cycle cancels its old timer before a fresh cycle", async () => {
  const harness = await boot([]);
  const btn = harness.nodes.clearAllBtn;

  await btn.click();
  harness.scheduler.advance(1000);
  const clearing = btn.click();
  const request = takeRequest(harness, "/api/cache/clear");
  request.resolve(response({ sources_deleted: 0, files_removed: 0 }));
  await flush();
  const refresh = takeRequest(harness, "/api/sources");
  refresh.resolve(response([]));
  await clearing;

  await btn.click();
  assert.equal(btn.dataset.armed, "1");

  // The first timer was due at t=5000; the fresh cycle expires at t=6000.
  harness.scheduler.advance(4000);
  assert.equal(btn.dataset.armed, "1");
  assert.equal(harness.requests.length, 0);
  harness.scheduler.advance(1000);
  assert.equal(btn.dataset.armed, "");
});

test("clear-all HTTP and network failures always re-enable the control", async () => {
  for (const failure of ["http", "network"]) {
    const harness = await boot([]);
    const btn = harness.nodes.clearAllBtn;
    await btn.click();
    const clearing = btn.click();
    const request = takeRequest(harness, "/api/cache/clear");
    if (failure === "http") request.resolve(response({ detail: "cache busy" }, false, 409));
    else request.reject(new Error("connection lost"));
    await clearing;

    assert.equal(btn.disabled, false);
    assert.equal(btn.dataset.armed, "");
    assert.match(
      harness.nodes.status.textContent,
      failure === "http" ? /clear failed: cache busy/ : /clear failed: connection lost/,
    );
  }
});

test("media load errors are rendered for HTTP and network failures", async () => {
  for (const failure of ["http-error", "network-error"]) {
    const harness = await boot([], failure);
    assert.equal(harness.nodes.count.textContent, "");
    assert.match(
      textOf(harness.nodes.list),
      failure === "http-error" ? /Failed to load media: HTTP 503/ : /Failed to load media: network down/,
    );
  }
});

test("media rows retain representative byte, duration, missing-media, and text rendering behavior", async () => {
  const harness = await boot([
    source("a", { size_bytes: 0, duration_s: 125.5 }),
    source("b", {
      title: "",
      ref: "javascript:alert(1)",
      slice_count: 1,
      size_bytes: null,
      duration_s: 0,
      acquired_at: null,
    }),
    source("kb", { size_bytes: 1024 }),
    source("ten-kb", { size_bytes: 10 * 1024 }),
    source("mb", { size_bytes: 1024 * 1024 }),
    source("gb", { size_bytes: 1024 * 1024 * 1024 }),
  ]);

  assert.equal(harness.nodes.count.textContent, "6 sources");
  assert.match(textOf(findNode(harness.nodes.list, (node) => node.attributes["data-id"] === "a")), /0 B/);
  assert.match(textOf(findNode(harness.nodes.list, (node) => node.attributes["data-id"] === "a")), /2:06/);
  assert.match(textOf(findNode(harness.nodes.list, (node) => node.attributes["data-id"] === "b")), /media missing/);
  assert.match(textOf(findNode(harness.nodes.list, (node) => node.attributes["data-id"] === "kb")), /1\.0 KB/);
  assert.match(textOf(findNode(harness.nodes.list, (node) => node.attributes["data-id"] === "ten-kb")), /10 KB/);
  assert.match(textOf(findNode(harness.nodes.list, (node) => node.attributes["data-id"] === "mb")), /1\.0 MB/);
  assert.match(textOf(findNode(harness.nodes.list, (node) => node.attributes["data-id"] === "gb")), /1\.0 GB/);
  assert.match(textOf(harness.nodes.list), /javascript:alert\(1\)/);
  assert.equal(findNode(harness.nodes.list, (node) => node.tag === "a"), null);
  assert.match(textOf(harness.nodes.list), /\(untitled\)/);
});
