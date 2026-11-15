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
    this.classList = { toggle() { } };
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
  const frames = [];
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
    requestAnimationFrame(callback) { frames.push(callback); return frames.length; },
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
  const harness = { context, nodes, requests, timers, session, frames };

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

test("review preview opens at the saved start and a save never reloads the media", async () => {
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
  assert.equal(video.src, "/media/a.mp4#t=0.0000001");
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
  assert.equal(video.src, "/media/a.mp4#t=0.0000001"); // SPEC §14.3: no reload
  assert.equal(textOf(windowLabel), "selected 3.14–5.96s");
});

function button(harness, text) {
  return findNode(harness.nodes.list, (node) => node.tag === "button" && textOf(node) === text);
}

function playableVideo(harness, overrides = {}) {
  const video = findNode(harness.nodes.list, (node) => node.tag === "video");
  Object.assign(video, {
    readyState: 4, duration: 100, currentTime: 0, paused: true, plays: 0, pauses: 0,
    async play() { this.paused = false; this.plays += 1; this.listeners.play?.({}); },
    pause() { this.paused = true; this.pauses += 1; },
    ...overrides,
  });
  return video;
}

test("Preview window sits between Reset bounds and Preview ±10s and plays the visible interval without saving", async () => {
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4", target_in: 45, target_out: 55 })]);
  const video = playableVideo(harness, { currentTime: 80 });
  const editor = findNode(harness.nodes.list, (node) => node.attributes.class === "window-edit");
  assert.deepEqual(editor.children.filter((node) => node.tag === "button").map(textOf),
    ["Reset bounds", "Preview window", "Preview ±10s"]);
  cardInput(harness, "in").value = "50";
  cardInput(harness, "out").value = "65";
  const src = video.src;
  await button(harness, "Preview window").listeners.click({});
  assert.equal(video.currentTime, 50); // rewind from after the interval
  assert.equal(video.paused, false);
  video.currentTime = 64.9;
  video.listeners.timeupdate({});
  assert.equal(video.paused, false);
  video.currentTime = 65;
  video.listeners.timeupdate({});
  assert.equal(video.paused, true);
  assert.match(textOf(cardMessage(harness)), /window preview ended/);
  video.currentTime = 5;
  await button(harness, "Preview window").listeners.click({});
  assert.equal(video.currentTime, 50); // fast-forward from before the interval
  assert.equal(video.src, src);
  assert.equal(harness.requests.length, 0);
});

test("Preview window refuses invalid or unavailable media and remains available on terminal cards", async () => {
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4", status: "handed_off" })]);
  const video = playableVideo(harness, { currentTime: 7 });
  assert.equal(button(harness, "Preview window").disabled, false);
  cardInput(harness, "in").value = "9";
  cardInput(harness, "out").value = "8";
  await button(harness, "Preview window").listeners.click({});
  assert.equal(video.currentTime, 7);
  assert.equal(video.plays, 0);
  assert.match(textOf(cardMessage(harness)), /out must exceed in/);
  video.listeners.error({});
  assert.equal(button(harness, "Preview window").disabled, true);
});

test("switching previews ignores an older play rejection and keeps the latest stop boundary", async () => {
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4", target_in: 20, target_out: 30 })]);
  const first = deferred();
  const video = playableVideo(harness);
  let calls = 0;
  video.play = function () {
    this.paused = false;
    this.listeners.play({});
    return ++calls === 1 ? first.promise : Promise.resolve();
  };
  const older = button(harness, "Preview ±10s").listeners.click({});
  await button(harness, "Preview window").listeners.click({});
  first.reject(new Error("interrupted by a new seek"));
  await older;
  assert.match(textOf(cardMessage(harness)), /previewing window 20–30s/);
  video.currentTime = 30;
  video.listeners.timeupdate({});
  assert.equal(video.paused, true);
  assert.equal(harness.requests.length, 0);
});

test("Mark start saves the playhead immediately without touching playback", async () => {
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4", target_in: 2, target_out: 8 })]);
  const video = playableVideo(harness, { currentTime: 4.25, paused: false });
  const src = video.src;
  const saving = button(harness, "Mark start").listeners.click({});
  const request = await nextRequest(harness);
  assert.equal(request.options.method, "PATCH");
  assert.deepEqual(JSON.parse(request.options.body), { target_in: 4.25, target_out: 8 });
  assert.equal(button(harness, "Mark start").disabled, true); // pending write
  request.resolve(response({ target_in: 4.25, target_out: 8 }));
  await new Promise((resolve) => setImmediate(resolve));
  await saving;
  assert.equal(video.src, src);
  assert.equal(video.currentTime, 4.25);
  assert.equal(video.paused, false);
  assert.equal(video.pauses, 0);
  assert.equal(cardInput(harness, "in").value, "4.25");
  assert.equal(button(harness, "Mark start").disabled, false);
});

test("Mark end failure keeps the marked value and the precise server error", async () => {
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4", target_in: 2, target_out: 8 })]);
  playableVideo(harness, { currentTime: 1.5 });
  button(harness, "Mark end").listeners.click({});
  const request = await nextRequest(harness);
  assert.deepEqual(JSON.parse(request.options.body), { target_in: 2, target_out: 1.5 });
  request.resolve(response({ detail: "target_out must exceed target_in" }, false, 422));
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(cardInput(harness, "out").value, "1.5");
  assert.equal(textOf(cardMessage(harness)), "target_out must exceed target_in");
});

test("marks wait for loaded media and never write without it", async () => {
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4" }), slice("b", "alpha")]);
  playableVideo(harness, { readyState: 0 });
  button(harness, "Mark start").listeners.click({});
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.requests.length, 0);
  assert.match(textOf(cardMessage(harness)), /media is not ready/);
  const cards = harness.nodes.list.children;
  const noMedia = (text) => findNode(cards[1], (node) => node.tag === "button" && textOf(node) === text);
  assert.equal(noMedia("Mark start").disabled, true);
  assert.equal(noMedia("Preview ±10s").disabled, true);
});

test("context preview plays clamped ±10s around the interval and stops at its end", async () => {
  const harness = await boot([
    slice("a", "alpha", { media_url: "/media/a.mp4", target_in: 4, target_out: 95 }),
  ]);
  const video = playableVideo(harness, { duration: 100 });
  await button(harness, "Preview ±10s").listeners.click({});
  assert.equal(video.currentTime, 0); // clamped at the source start
  assert.equal(video.paused, false);
  assert.equal(harness.requests.length, 0); // no write of any kind
  video.currentTime = 99.9;
  video.listeners.timeupdate({});
  assert.equal(video.paused, false);
  video.currentTime = 100;
  video.listeners.timeupdate({});
  assert.equal(video.paused, true); // clamped at the source end, then stopped
  assert.match(textOf(cardMessage(harness)), /context preview ended; saved bounds unchanged/);
  assert.equal(cardInput(harness, "in").value, "4");
  assert.equal(cardInput(harness, "out").value, "95");
});

test("context preview stops 10s after the end and the saved interval stops at its end", async () => {
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4", target_in: 20, target_out: 30 })]);
  const video = playableVideo(harness, { duration: 100 });
  await button(harness, "Preview ±10s").listeners.click({});
  assert.equal(video.currentTime, 10);
  video.currentTime = 39.9;
  harness.frames.shift()();
  assert.equal(video.paused, false);
  video.currentTime = 40.02;
  harness.frames.shift()();
  assert.equal(video.paused, true);

  video.currentTime = 20;
  await video.play(); // ordinary playback from inside the saved interval
  video.currentTime = 30.01;
  video.listeners.timeupdate({});
  assert.equal(video.paused, true);

  video.currentTime = 50; // a native seek past the interval plays freely
  await video.play();
  video.currentTime = 70;
  video.listeners.timeupdate({});
  assert.equal(video.paused, false);
});

test("visual review P2: context uses the visible edited interval after a refused save", async () => {
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4", target_in: 45, target_out: 55 })]);
  const video = playableVideo(harness, { duration: 100 });
  cardInput(harness, "in").value = "50";
  cardInput(harness, "out").value = "65";
  const saving = cardInput(harness, "out").listeners.change({});
  const request = await nextRequest(harness);
  request.resolve(response({ detail: "source duration could not be verified" }, false, 422));
  await saving;
  await button(harness, "Preview ±10s").listeners.click({});
  assert.equal(video.currentTime, 40);
  video.currentTime = 65;
  video.listeners.timeupdate({});
  assert.equal(video.paused, false);
  video.currentTime = 75;
  video.listeners.timeupdate({});
  assert.equal(video.paused, true);
  assert.equal(harness.requests.length, 0); // preview is not a save
});

test("visual review P2: context rejects invalid visible bounds without playing", async () => {
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4" })]);
  const video = playableVideo(harness, { duration: 100, currentTime: 7 });
  cardInput(harness, "in").value = "50";
  cardInput(harness, "out").value = "40";
  await button(harness, "Preview ±10s").listeners.click({});
  assert.equal(video.currentTime, 7);
  assert.equal(video.plays, 0);
  assert.match(textOf(cardMessage(harness)), /out must exceed in/);
  cardInput(harness, "in").value = "101";
  cardInput(harness, "out").value = "120";
  await button(harness, "Preview ±10s").listeners.click({});
  assert.equal(video.plays, 0);
  assert.match(textOf(cardMessage(harness)), /source duration/);
});

test("focused review P2: context clamps an ASR end beyond the container duration", async () => {
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4", target_in: 90, target_out: 100.4 })]);
  const video = playableVideo(harness, { duration: 100 });
  await button(harness, "Preview ±10s").listeners.click({});
  assert.equal(video.currentTime, 80);
  assert.equal(video.paused, false);
  video.currentTime = 100;
  video.listeners.timeupdate({});
  assert.equal(video.paused, true);
  assert.equal(harness.requests.length, 0);
});

test("focused review P2: Reset bounds on the original interval makes no write or false edit", async () => {
  const row = slice("a", "alpha", { window_edited: false, original_window: [2, 8] });
  const harness = await boot([row]);
  cardInput(harness, "in").value = "3"; // failed or unsaved visible edit
  button(harness, "Reset bounds").listeners.click({});
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.requests.length, 0);
  assert.equal(cardInput(harness, "in").value, "2");
  assert.equal(row.window_edited, false);
});

test("visual review P2: Reset bounds restores the server original after reload without resetting status or playback", async () => {
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4", target_in: 50, target_out: 65,
    original_window: [45, 55], window_edited: true, status: "selected" })]);
  const video = playableVideo(harness, { currentTime: 70, paused: false });
  const src = video.src;
  const reset = button(harness, "Reset bounds");
  assert.ok(reset);
  reset.listeners.click({});
  const request = await nextRequest(harness);
  assert.equal(request.options.method, "PATCH");
  assert.match(request.url, /\/window$/);
  assert.deepEqual(JSON.parse(request.options.body), { target_in: 45, target_out: 55 });
  await reset.listeners.click({});
  assert.equal(harness.requests.length, 0);
  request.resolve(response({ target_in: 45, target_out: 55 }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(cardInput(harness, "in").value, "45");
  assert.equal(cardInput(harness, "out").value, "55");
  assert.equal(harness.nodes.list.children[0].attributes["data-status"], "selected");
  assert.equal(video.currentTime, 70);
  assert.equal(video.paused, false);
  assert.equal(video.src, src);
  assert.ok(button(harness, "Reset status"));
});

test("visual review P2: Reset bounds is unavailable for terminal or unknown originals", async () => {
  const harness = await boot([
    slice("a", "alpha", { window_edited: true, original_window: null }),
    slice("b", "alpha", { status: "handed_off", original_window: [2, 8] }),
  ]);
  for (const card of harness.nodes.list.children) {
    const reset = findNode(card, (node) => node.tag === "button" && textOf(node) === "Reset bounds");
    assert.ok(reset);
    assert.equal(reset.disabled, true);
    await reset.listeners.click({});
  }
  assert.equal(harness.requests.length, 0);
});

test("a native seek outside the context preview releases its stop", async () => {
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4", target_in: 20, target_out: 30 })]);
  const video = playableVideo(harness, { duration: 100 });
  await button(harness, "Preview ±10s").listeners.click({});
  video.currentTime = 80;
  video.listeners.seeking({});
  video.currentTime = 81;
  video.listeners.timeupdate({});
  assert.equal(video.paused, false);
});

test("handed-off cards keep context preview but refuse marks", async () => {
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4", status: "handed_off" })]);
  assert.equal(button(harness, "Mark start").disabled, true);
  assert.equal(button(harness, "Mark end").disabled, true);
  assert.equal(button(harness, "Preview ±10s").disabled, false);
});

test("method badges name the actual method and unresolved refinement needs review", async () => {
  const harness = await boot([
    slice("a", "alpha", { extraction: { methods: ["model-full"], refinement: ["refined"], needs_review: false, model: "m" } }),
    slice("b", "alpha", { extraction: { methods: ["model-chunk", "heuristic-fallback"], refinement: ["refinement_failed"], needs_review: true } }),
    slice("c", "alpha", { extraction: {} }),
  ]);
  const [a, b, c] = harness.nodes.list.children;
  const badge = (card, cls) => findNode(card, (node) => node.attributes.class === "badge " + cls);
  assert.equal(textOf(badge(a, "method")), "full-transcript discovery + boundary refinement");
  assert.equal(badge(a, "needs-review"), null);
  assert.equal(textOf(badge(b, "method")), "chunked discovery + heuristic fallback");
  assert.equal(textOf(badge(b, "needs-review")), "unrefined · needs review (refinement failed)");
  assert.equal(badge(c, "method"), null); // legacy rows claim no method
  assert.doesNotMatch(textOf(harness.nodes.list), /\b[XYZ]\b/);
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
    slice("b", "alpha", { scorer_model: "claude-haiku-5-5" }),
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

test("Mark end during playback saves without pausing the video", async () => {
  // Skeptic MEDIUM (PR #100): the interval stop followed the new end at the playhead.
  const harness = await boot([slice("a", "alpha", { media_url: "/media/a.mp4", target_in: 2, target_out: 8 })]);
  const video = playableVideo(harness, { currentTime: 3 });
  await video.play();
  video.currentTime = 5;
  const saving = button(harness, "Mark end").listeners.click({});
  const request = await nextRequest(harness);
  request.resolve(response({ target_in: 2, target_out: 5 }));
  await new Promise((resolve) => setImmediate(resolve));
  await saving;
  video.currentTime = 5.1;
  video.listeners.timeupdate({});
  while (harness.frames.length) harness.frames.shift()();
  assert.equal(video.paused, false);
  assert.equal(video.pauses, 0);
});

test("a hand-edited window says its provenance describes the original interval", async () => {
  // Skeptic + correctness LOW (PR #100): method badges went stale after an edit.
  const extraction = { methods: ["model-full"], refinement: ["refined"], needs_review: false };
  const harness = await boot([
    slice("a", "alpha", { extraction, window_edited: true }),
    slice("b", "alpha", { extraction, media_url: "/media/b.mp4" }),
  ]);
  const [a, b] = harness.nodes.list.children;
  const edited = (card) => findNode(card, (node) => node.attributes.class === "badge edited");
  assert.equal(textOf(edited(a)), "edited by hand");
  assert.equal(edited(b), null);
  playableVideo(harness, { currentTime: 4 });
  const saving = findNode(b, (node) => node.tag === "button" && textOf(node) === "Mark start").listeners.click({});
  const request = await nextRequest(harness);
  request.resolve(response({ target_in: 4, target_out: 8 }));
  await new Promise((resolve) => setImmediate(resolve));
  await saving;
  assert.equal(textOf(edited(b)), "edited by hand");
});
