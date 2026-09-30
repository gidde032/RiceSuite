// Clipper's rendered-clip column (RiceSuite #20, variant C), run against the
// real clipper/web/app.js: the 9:16 frame is always present, empty until a
// render lands; a re-render keeps the last render in place, dimmed, with no
// live download; a failed render or a stale edit returns it to empty.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { element, scriptedFetch, context, run, settle } = require("./harness");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "..", "clipper", "web", "app.js"),
  "utf8",
);
const OUTPUT = "api/jobs/j1/output";

function classes(...initial) {
  const set = new Set(initial);
  return {
    add: (...names) => names.forEach((n) => set.add(n)),
    remove: (...names) => names.forEach((n) => set.delete(n)),
    toggle(name, force) {
      const on = force === undefined ? !set.has(name) : Boolean(force);
      if (on) set.add(name);
      else set.delete(name);
      return on;
    },
    contains: (name) => set.has(name),
  };
}

function video(src = "") {
  return {
    src,
    paused: false,
    pause() { this.paused = true; },
    load() {},
    setAttribute() {},
    removeAttribute(name) { if (name === "src") this.src = ""; },
  };
}

function link(href = "") {
  return {
    href,
    download: "",
    setAttribute(name, value) { this[name] = value; },
    removeAttribute(name) { if (name === "href") this.href = ""; },
  };
}

// A clip card as buildCard leaves it; `rendered` means a render is on show.
function card(rendered) {
  const clip = {
    jobId: "j1",
    ord: 1,
    status: rendered ? "done" : "ready",
    words: [],
    renders: rendered ? 1 : 0,
    resultEl: { classList: rendered ? classes() : classes("is-empty") },
    outputVideoEl: video(rendered ? OUTPUT : ""),
    downloadEl: link(rendered ? OUTPUT : ""),
    musicModeEl: { value: "none" },
    musicInputEl: { files: [] },
    headerEl: { value: "Hook" },
    captionsToggleEl: { checked: true },
  };
  return new Proxy(clip, {
    get(target, prop) {
      if (!(prop in target) && typeof prop === "string") target[prop] = element();
      return target[prop];
    },
  });
}

function boot(routes) {
  const { fetch, calls } = scriptedFetch({
    "GET api/health": { ok: true },
    "GET api/media-info": { files: 0, bytes: 0 },
    ...routes,
  });
  const { ctx } = context(fetch);
  const js = run(SOURCE, ctx);
  return { js, ctx, calls };
}

function held() {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  return { gate, release };
}

const state = (clip) => ({
  empty: clip.resultEl.classList.contains("is-empty"),
  busy: clip.resultEl.classList.contains("is-busy"),
  video: clip.outputVideoEl.src,
  download: clip.downloadEl.href,
});

test("a first render fills the empty 9:16 frame", async () => {
  const hold = held();
  const { js, ctx } = boot({
    "POST api/jobs/j1/render": async () => { await hold.gate; return [200, { status: "done" }]; },
  });
  ctx.clip = card(false);
  assert.deepEqual(state(ctx.clip), { empty: true, busy: false, video: "", download: "" });
  const done = js("renderClip(clip)");
  await settle();
  assert.deepEqual(state(ctx.clip), { empty: true, busy: true, video: "", download: "" });
  hold.release();
  assert.equal(await done, true);
  assert.deepEqual(state(ctx.clip), { empty: false, busy: false, video: OUTPUT, download: OUTPUT });
});

test("a re-render keeps the last render in place, paused, with no live download", async () => {
  const hold = held();
  const { js, ctx } = boot({
    "POST api/jobs/j1/render": async () => { await hold.gate; return [200, { status: "done" }]; },
  });
  ctx.clip = card(true);
  const done = js("renderClip(clip)");
  await settle();
  assert.deepEqual(state(ctx.clip), { empty: false, busy: true, video: OUTPUT, download: "" });
  assert.equal(ctx.clip.outputVideoEl.paused, true);
  hold.release();
  assert.equal(await done, true);
  assert.deepEqual(state(ctx.clip), { empty: false, busy: false, video: OUTPUT, download: OUTPUT });
});

test("a failed render returns the frame to empty and stays retryable", async () => {
  const { js, ctx } = boot({
    "POST api/jobs/j1/render": () => [500, { detail: "ffmpeg failed" }],
  });
  ctx.clip = card(true);
  assert.equal(await js("renderClip(clip)"), false);
  assert.deepEqual(state(ctx.clip), { empty: true, busy: false, video: "", download: "" });
  assert.equal(ctx.clip.status, "ready");
});

test("restoring the transcript clears the render it no longer matches", async () => {
  const { js, ctx } = boot({
    "POST api/jobs/j1/restore-transcript": { words: [] },
  });
  ctx.clip = card(true);
  await js("restoreTranscript(clip)");
  await settle();
  assert.deepEqual(state(ctx.clip), { empty: true, busy: false, video: "", download: "" });
});

test("aligning lyrics clears the render it no longer matches", async () => {
  const { js, ctx } = boot({
    "POST api/jobs/j1/lyrics": { words: [], method: "anchors", anchor_rate: 1 },
  });
  ctx.clip = card(true);
  ctx.clip.lyricsInputEl.value = "line one\nline two";
  await js("alignLyrics(clip)");
  await settle();
  assert.deepEqual(state(ctx.clip), { empty: true, busy: false, video: "", download: "" });
});
