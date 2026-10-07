// Clipper photo cards (Issue #54), run against the real clipper/web/app.js: a
// photo skips transcription, hides the caption controls, offers music as
// "No music" or "Add music", and sends its length with the render request.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { element, scriptedFetch, context, run } = require("./harness");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "..", "clipper", "web", "app.js"),
  "utf8",
);

function classes() {
  const set = new Set();
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

function card(extra = {}) {
  const clip = {
    jobId: null,
    ord: 1,
    status: "queued",
    words: [],
    isPhoto: false,
    file: { name: "quote.jpg", type: "image/jpeg" },
    sourceUrl: "blob:quote",
    el: { classList: classes() },
    musicModeEl: { value: "none", innerHTML: "" },
    musicInputEl: { files: [] },
    headerEl: { value: "Hook" },
    captionsToggleEl: { checked: true }, motionToggleEl: { checked: true }, emojiToggleEl: { checked: false },
    photoLengthEl: { value: "10" },
    sourcePhotoEl: { src: "", alt: "" },
    sourceVideoEl: { src: "", removeAttribute(name) { if (name === "src") this.src = ""; } },
    resultEl: { classList: classes() },
    ...extra,
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

test("photo files are recognised by type or by name", () => {
  const { js, ctx } = boot({});
  ctx.files = [
    { name: "a.png", type: "image/png" },
    { name: "b.WEBP", type: "" },
    { name: "c.jpeg", type: "" },
    { name: "d.mp4", type: "video/mp4" },
  ];
  assert.deepEqual(Array.from(js("files.map(isPhotoFile)")), [true, true, true, false]);
});

test("a photo card hides captions and offers music as no music or add music", () => {
  const { js, ctx } = boot({});
  ctx.clip = card();
  js("applyPhotoCard(clip)");
  assert.equal(ctx.clip.isPhoto, true);
  assert.equal(ctx.clip.el.classList.contains("photo-card"), true);
  assert.equal(ctx.clip.captionsToggleEl.checked, false);
  assert.equal(ctx.clip.sourcePhotoEl.src, "blob:quote");
  assert.match(ctx.clip.musicModeEl.innerHTML, /value="none">No music</);
  assert.match(ctx.clip.musicModeEl.innerHTML, /value="replace">Add music</);
  assert.doesNotMatch(ctx.clip.musicModeEl.innerHTML, /mix/);
});

test("a photo upload is ready without a transcription request", async () => {
  const { js, ctx, calls } = boot({
    "POST api/upload": { id: "p1", status: "ready", kind: "photo", width: 40, height: 20 },
  });
  ctx.clip = card();
  ctx.clips = [ctx.clip];
  js("clips.push(clip)");
  await js("ingestClip(clip)");
  assert.equal(ctx.clip.status, "ready");
  assert.equal(ctx.clip.jobId, "p1");
  assert.equal(ctx.clip.isPhoto, true);
  // The header controls read their font list and ask for a header preview
  // (RiceSuite #65); both are read-only and local. Nothing is transcribed.
  const readOnly = new Set(["api/health", "api/media-info", "api/header-options", "api/jobs/p1/header-preview"]);
  assert.deepEqual(calls.map((c) => c.path).filter((p) => p.startsWith("api/") && !readOnly.has(p)), ["api/upload"]);
});

test("a photo render sends its length and drops nothing else", async () => {
  const { js, ctx, calls } = boot({
    "POST api/jobs/p1/render": () => [200, { status: "done" }],
  });
  ctx.clip = card({ jobId: "p1", status: "ready", photoLengthEl: { value: "12" } });
  js("applyPhotoCard(clip)");
  assert.equal(await js("renderClip(clip)"), true);
  const body = JSON.parse(calls.find((c) => c.path === "api/jobs/p1/render").body);
  assert.equal(body.photo_duration, 12);
  assert.equal(body.captions_on, false);
  assert.equal(body.header, "Hook");
});

test("a photo length outside 3-60 whole seconds stops the render", async () => {
  for (const value of ["2", "61", "4.5", ""]) {
    const { js, ctx, calls } = boot({
      "POST api/jobs/p1/render": () => [200, { status: "done" }],
    });
    ctx.clip = card({ jobId: "p1", status: "ready", photoLengthEl: { value } });
    js("applyPhotoCard(clip)");
    assert.equal(await js("renderClip(clip)"), false, value);
    assert.equal(calls.some((c) => c.path === "api/jobs/p1/render"), false, value);
    assert.match(ctx.clip.error, /3 to 60/);
  }
});

// --- review repairs (PR #57) ---------------------------------------------------

test("only PNG, JPEG, and WebP types count as photos", () => {
  const { js, ctx } = boot({});
  ctx.files = [
    { name: "anim.gif", type: "image/gif" },
    { name: "phone.heic", type: "image/heic" },
    { name: "IMG", type: "image/jpeg" },
  ];
  assert.deepEqual(Array.from(js("files.map(isPhotoFile)")), [false, false, true]);
});

test("an invalid length keeps the last render on show", async () => {
  const { js, ctx, calls } = boot({
    "POST api/jobs/p1/render": () => [200, { status: "done" }],
  });
  const resultEl = { classList: classes() };
  const outputVideoEl = { src: "api/jobs/p1/output", pause() {}, load() {}, removeAttribute() {} };
  ctx.clip = card({ jobId: "p1", status: "done", renders: 1, resultEl, outputVideoEl, photoLengthEl: { value: "99" } });
  js("applyPhotoCard(clip)");
  assert.equal(await js("renderClip(clip)"), false);
  assert.equal(calls.some((c) => c.path === "api/jobs/p1/render"), false);
  assert.equal(ctx.clip.status, "done");
  assert.equal(ctx.clip.resultEl.classList.contains("is-empty"), false);
  assert.equal(ctx.clip.outputVideoEl.src, "api/jobs/p1/output");
  assert.match(ctx.clip.error, /3 to 60/);
});

test("a photo card starts its music at full volume", () => {
  const { js, ctx } = boot({});
  ctx.clip = card({ musicVolumeEl: { value: "0.35" }, volLabelEl: { textContent: "0.35" } });
  js("applyPhotoCard(clip)");
  assert.equal(ctx.clip.musicVolumeEl.value, "1");
  assert.equal(ctx.clip.volLabelEl.textContent, "1.00");
});
