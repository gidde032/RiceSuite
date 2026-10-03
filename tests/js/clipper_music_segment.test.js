// Clipper music segment (Issue #55), run against the real clipper/web/app.js:
// the start slider fits the segment inside the track, the preview plays from
// the start point for the clip's length, and the render sends the start.
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

function media(duration = NaN) {
  return {
    duration,
    currentTime: -1,
    volume: 1,
    muted: false,
    src: "",
    plays: 0,
    paused: true,
    play() { this.plays += 1; this.paused = false; return Promise.resolve(); },
    pause() { this.paused = true; },
    removeAttribute(name) { if (name === "src") this.src = ""; },
  };
}

function card(extra = {}) {
  const clip = {
    jobId: "j1",
    ord: 1,
    status: "ready",
    words: [],
    isPhoto: false,
    geoState: { duration: 20 },
    musicInputEl: { files: [{ name: "song.mp3" }] },
    musicModeEl: { value: "mix" },
    musicVolumeEl: { value: "0.35" },
    musicStartEl: { value: "0", max: "0", disabled: true },
    musicStartLabelEl: { textContent: "" },
    musicPlayEl: { textContent: "", disabled: true },
    musicHintEl: { textContent: "" },
    musicPreviewEl: media(180),
    sourceVideoEl: media(20),
    photoLengthEl: { value: "10" },
    headerEl: { value: "" },
    captionsToggleEl: { checked: true },
    segmentTimer: null,
    musicUrl: null,
    ...extra,
  };
  return new Proxy(clip, {
    get(target, prop) {
      if (!(prop in target) && typeof prop === "string") target[prop] = element();
      return target[prop];
    },
  });
}

function boot(routes = {}) {
  const timers = [];
  const { fetch, calls } = scriptedFetch({
    "GET api/health": { ok: true },
    "GET api/media-info": { files: 0, bytes: 0 },
    ...routes,
  });
  const { ctx } = context(fetch, {
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout() {},
  });
  const js = run(SOURCE, ctx);
  return { js, ctx, calls, timers };
}

test("the start slider keeps the whole segment inside the track", () => {
  const { js, ctx } = boot();
  ctx.clip = card({ musicStartEl: { value: "175", max: "0", disabled: true } });
  js("syncMusicStart(clip)");
  assert.equal(ctx.clip.musicStartEl.max, "160");
  assert.equal(ctx.clip.musicStartEl.value, "160");
  assert.equal(ctx.clip.musicStartEl.disabled, false);
  assert.equal(ctx.clip.musicPlayEl.disabled, false);
  assert.equal(ctx.clip.musicStartLabelEl.textContent, "2:40");
});

test("a track no longer than the clip plays from the start", () => {
  const { js, ctx } = boot();
  ctx.clip = card({ musicPreviewEl: media(15) });
  js("syncMusicStart(clip)");
  assert.equal(ctx.clip.musicStartEl.max, "0");
  assert.equal(ctx.clip.musicStartEl.disabled, true);
  assert.equal(ctx.clip.musicPlayEl.disabled, false);
  assert.match(ctx.clip.musicHintEl.textContent, /plays from the start/);
});

test("a photo bounds the start by its length field", () => {
  const { js, ctx } = boot();
  ctx.clip = card({ isPhoto: true, photoLengthEl: { value: "30" } });
  js("syncMusicStart(clip)");
  assert.equal(ctx.clip.musicStartEl.max, "150");
});

test("the video preview plays the segment from its start over the muted video", () => {
  const { js, ctx, timers } = boot();
  ctx.clip = card({ musicStartEl: { value: "42.5", max: "160", disabled: false } });
  js("playSegmentPreview(clip)");
  const { musicPreviewEl: audio, sourceVideoEl: video } = ctx.clip;
  assert.equal(audio.currentTime, 42.5);
  assert.equal(audio.plays, 1);
  assert.equal(audio.volume, 0.35);
  assert.equal(video.currentTime, 0);
  assert.equal(video.muted, true);
  assert.equal(video.plays, 1);
  assert.equal(timers.at(-1).ms, 20000);
  assert.equal(ctx.clip.musicPlayEl.textContent, "■ Stop");
  timers.at(-1).fn();
  assert.equal(audio.paused, true);
  assert.equal(video.paused, true);
  assert.equal(ctx.clip.musicPlayEl.textContent, "▶ Play segment");
});

test("the photo preview plays the music alone for the photo length", () => {
  const { js, ctx, timers } = boot();
  ctx.clip = card({ isPhoto: true, photoLengthEl: { value: "12" } });
  js("playSegmentPreview(clip)");
  assert.equal(ctx.clip.musicPreviewEl.plays, 1);
  assert.equal(ctx.clip.sourceVideoEl.plays, 0);
  assert.equal(timers.at(-1).ms, 12000);
});

test("a render sends the segment start with the music", async () => {
  const { js, ctx, calls } = boot({
    "POST api/jobs/j1/music": { ok: true, filename: "music.mp3" },
    "POST api/jobs/j1/render": () => [200, { status: "done" }],
  });
  ctx.clip = card({ musicStartEl: { value: "31.4", max: "160", disabled: false } });
  assert.equal(await js("renderClip(clip)"), true);
  const body = JSON.parse(calls.find((c) => c.path === "api/jobs/j1/render").body);
  assert.deepEqual(body.music, { mode: "mix", volume: 0.35, filename: "music.mp3", start: 31.4 });
});

test("without a track the render sends start 0", async () => {
  const { js, ctx, calls } = boot({
    "POST api/jobs/j1/render": () => [200, { status: "done" }],
  });
  ctx.clip = card({ musicInputEl: { files: [] }, musicStartEl: { value: "31.4" } });
  assert.equal(await js("renderClip(clip)"), true);
  const body = JSON.parse(calls.find((c) => c.path === "api/jobs/j1/render").body);
  assert.equal(body.music.start, 0);
  assert.equal(body.music.mode, "none");
});

// --- review repairs (PR #58) ---------------------------------------------------

test("a track the browser cannot decode explains itself and renders from 0", () => {
  const { js, ctx } = boot();
  ctx.clip = card({ musicPreviewEl: media(NaN), musicStartEl: { value: "12", max: "0", disabled: true } });
  js("clip.musicPreviewFailed = true; syncMusicStart(clip)");
  assert.equal(ctx.clip.musicStartEl.value, "0");
  assert.equal(ctx.clip.musicStartEl.disabled, true);
  assert.equal(ctx.clip.musicPlayEl.disabled, true);
  assert.match(ctx.clip.musicHintEl.textContent, /cannot preview/);
});

test("play waits until the clip length is known", () => {
  const { js, ctx } = boot();
  ctx.clip = card({ geoState: null, sourceVideoEl: media(NaN) });
  js("syncMusicStart(clip)");
  assert.equal(ctx.clip.musicPlayEl.disabled, true);
  assert.equal(ctx.clip.musicStartEl.disabled, true);
  assert.match(ctx.clip.musicHintEl.textContent, /clip length/);
});

test("stopping right after play is not reported as a playback failure", async () => {
  const { js, ctx } = boot();
  const abort = Object.assign(new Error("interrupted"), { name: "AbortError" });
  const audio = Object.assign(media(180), { play() { return Promise.reject(abort); } });
  ctx.clip = card({ musicPreviewEl: audio });
  js("syncMusicStart(clip); playSegmentPreview(clip)");
  await new Promise((resolve) => setImmediate(resolve));
  assert.doesNotMatch(ctx.clip.musicHintEl.textContent, /cannot/);
});

test("only one card previews at a time", () => {
  const { js, ctx } = boot();
  ctx.a = card();
  ctx.b = card({ ord: 2 });
  js("clips.push(a, b); playSegmentPreview(a); playSegmentPreview(b)");
  assert.equal(ctx.a.musicPreviewEl.paused, true);
  assert.equal(ctx.a.segmentTimer, null);
  assert.equal(ctx.b.musicPreviewEl.paused, false);
});

test("removing a card stops its preview", () => {
  const { js, ctx } = boot();
  ctx.clip = card({ el: { remove() {} } });
  js("clips.push(clip); playSegmentPreview(clip); removeClip(clip)");
  assert.equal(ctx.clip.musicPreviewEl.paused, true);
  assert.equal(ctx.clip.segmentTimer, null);
});
