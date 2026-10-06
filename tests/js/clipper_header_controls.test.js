// Clipper header controls and live preview (RiceSuite #65, variant A), run
// against the real clipper/web/app.js: the look is saved per slot, a style
// card fills in the look but keeps the layout, preview requests are debounced
// and a stale reply never wins, the preview sits over the source's 9:16
// frame, and the render sends the look.
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
const SLOT_KEY = "riceclipper.slotStyles.v1";

function storage() {
  const data = new Map();
  return {
    data,
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
  };
}

function boot(routes = {}) {
  const timers = [];
  const local = storage();
  const { fetch, calls } = scriptedFetch({
    "GET api/health": { ok: true },
    "GET api/media-info": { files: 0, bytes: 0 },
    ...routes,
  });
  const { ctx } = context(fetch, {
    localStorage: local,
    setTimeout: (fn, ms) => { timers.push({ fn, ms, live: true }); return timers.length; },
    clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].live = false; },
  });
  const js = run(SOURCE, ctx);
  return { js, ctx, calls, timers, local };
}

function card(extra = {}) {
  const clip = {
    jobId: "j1",
    ord: 2,
    status: "ready",
    words: [],
    isPhoto: false,
    headerEl: { value: "Hook" },
    headerStyleEl: { querySelector: () => ({ value: "plain" }) },
    captionsToggleEl: { checked: true },
    musicInputEl: { files: [] },
    musicModeEl: { value: "none" },
    musicVolumeEl: { value: "0.35" },
    musicStartEl: { value: "0" },
    hcEls: {},
    headerPreviewWindowEl: { hidden: true, style: {}, classList: { toggle() {} } },
    headerPreviewEl: { src: "", removeAttribute(name) { if (name === "src") this.src = ""; } },
    headerPreviewNoteEl: { textContent: "" },
    ...extra,
  };
  return new Proxy(clip, {
    get(target, prop) {
      if (!(prop in target) && typeof prop === "string") target[prop] = element();
      return target[prop];
    },
  });
}

function slotStore(local) {
  return JSON.parse(local.getItem(SLOT_KEY) || "{}");
}

// --- per-slot persistence -------------------------------------------------------

test("a slot with no saved look starts from its style preset", () => {
  const { js } = boot();
  const look = js('seedHeaderLook(3, "black_plate")');
  assert.equal(look.plate, "translucent");
  assert.equal(look.outline, 0);
  assert.equal(look.y, 210);
});

test("an edited look is saved for its slot and seeds the next clip in that slot", () => {
  const { js, ctx, local } = boot();
  ctx.clip = card();
  ctx.clip.headerLook = js('seedHeaderLook(2, "plain")');
  js("setHeaderLook(clip, { ...clip.headerLook, y: 640, size: 60, color: 'FFD60A' })");

  assert.deepEqual(
    { y: slotStore(local)["2"].headerLook.y, color: slotStore(local)["2"].headerLook.color },
    { y: 640, color: "FFD60A" },
  );
  const next = js('seedHeaderLook(2, "plain")');
  assert.equal(next.y, 640);
  assert.equal(next.size, 60);
  assert.equal(js('seedHeaderLook(1, "plain")').y, 210); // other slots untouched
});

test("a saved field of the wrong type falls back to the preset", () => {
  const { js, local } = boot();
  local.setItem(SLOT_KEY, JSON.stringify({ 2: { headerLook: { y: "low", size: 50 } } }));
  const look = js('seedHeaderLook(2, "plain")');
  assert.equal(look.y, 210);
  assert.equal(look.size, 50);
});

test("a style card fills in the look and keeps position, size, font and alignment", () => {
  const { js, ctx } = boot();
  ctx.clip = card();
  ctx.clip.headerLook = { ...js('headerPreset("plain")'), y: 700, size: 64, font: "impact", align: "left" };
  js('applyHeaderStylePreset(clip, "white_plate")');
  const look = ctx.clip.headerLook;
  assert.equal(look.plate, "solid");
  assert.equal(look.plate_color, "FFFFFF");
  assert.deepEqual([look.y, look.size, look.font, look.align], [700, 64, "impact", "left"]);
});

test("controls read back into a look with the server's types", () => {
  const { js, ctx } = boot();
  ctx.clip = card({
    hcEls: {
      y: { value: "640" }, size: { value: "60" }, font: { value: "georgia" },
      color: { value: "#ffd60a" }, outline: { value: "3" }, shadow: { checked: true },
      line_spacing: { value: "1.2500001" }, plate: { value: "translucent" },
    },
  });
  ctx.clip.headerLook = js('headerPreset("plain")');
  const look = js("readHeaderControls(clip)");
  assert.equal(look.y, 640);
  assert.equal(look.font, "georgia");
  assert.equal(look.color, "FFD60A");
  assert.equal(look.shadow, true);
  assert.equal(look.line_spacing, 1.25);
  assert.equal(look.plate_padding, 16); // no control given: unchanged
});

// --- preview requests ------------------------------------------------------------

test("a burst of edits sends one preview request", async () => {
  const { js, ctx, calls, timers } = boot({
    "POST api/jobs/j1/header-preview": () => [200, { image: "data:image/png;base64,AA", box: [1, 2, 3, 4], warnings: {}, note: null }],
  });
  ctx.clip = card();
  ctx.clip.headerLook = js('headerPreset("plain")');
  js("scheduleHeaderPreview(clip)");
  js("scheduleHeaderPreview(clip)");
  js("scheduleHeaderPreview(clip)");
  const live = timers.filter((t) => t.live);
  assert.equal(live.length, 1);
  assert.equal(live[0].ms, 250);
  live[0].fn();
  await settle();

  const previews = calls.filter((c) => c.path === "api/jobs/j1/header-preview");
  assert.equal(previews.length, 1);
  const body = JSON.parse(previews[0].body);
  assert.equal(body.header, "Hook");
  assert.equal(body.header_look.y, 210);
  assert.equal(ctx.clip.headerPreviewEl.src, "data:image/png;base64,AA");
  assert.equal(ctx.clip.headerPreviewWindowEl.hidden, false);
});

test("no preview is requested before the clip has a job", () => {
  const { js, ctx, timers } = boot();
  ctx.clip = card({ jobId: null });
  js("scheduleHeaderPreview(clip)");
  assert.equal(timers.length, 0);
});

test("a slow reply that a newer request overtook is dropped", async () => {
  const replies = [];
  const { js, ctx } = boot({
    "POST api/jobs/j1/header-preview": () => new Promise((resolve) => replies.push(resolve)),
  });
  ctx.clip = card();
  ctx.clip.headerLook = js('headerPreset("plain")');
  const first = js("requestHeaderPreview(clip)");
  const second = js("requestHeaderPreview(clip)");
  await settle();
  replies[1]([200, { image: "data:new", warnings: { crop_plan: null, music_plan: null } }]);
  await settle();
  replies[0]([200, { image: "data:old", warnings: { crop_plan: "header_zone", music_plan: null } }]);
  await Promise.all([first, second]);
  assert.equal(ctx.clip.headerPreviewEl.src, "data:new");
  assert.deepEqual({ ...ctx.clip.headerWarnings }, { crop_plan: null, music_plan: null });
});

test("a failed preview hides the image and says why", async () => {
  const { js, ctx } = boot({
    "POST api/jobs/j1/header-preview": () => [422, { detail: [{ msg: "too big" }] }],
  });
  ctx.clip = card();
  ctx.clip.headerLook = js('headerPreset("plain")');
  await js("requestHeaderPreview(clip)");
  assert.equal(ctx.clip.headerPreviewWindowEl.hidden, true);
  assert.match(ctx.clip.headerPreviewNoteEl.textContent, /Header preview unavailable: a header setting is out of range/);
});

test("the server's fallback note is shown under the controls", async () => {
  const { js, ctx } = boot({
    "POST api/jobs/j1/header-preview": () => [200, { image: null, warnings: {}, note: "The header will use the basic text renderer: no font" }],
  });
  ctx.clip = card();
  ctx.clip.headerLook = js('headerPreset("plain")');
  await js("requestHeaderPreview(clip)");
  assert.equal(ctx.clip.headerPreviewNoteEl.textContent, "The header will use the basic text renderer: no font");
});

// --- placement over the source ------------------------------------------------------

test("on a 9:16 source the preview covers the whole picture", () => {
  const { js } = boot();
  const box = js(`headerPreviewBox({ videoWidth: 1080, videoHeight: 1920, clientWidth: 400,
    clientHeight: 360, offsetLeft: 10, offsetTop: 20, clientLeft: 1, clientTop: 1 })`);
  // 360 px tall picture, 202.5 px wide, centred in the 400 px box.
  assert.equal(box.height, 360);
  assert.equal(box.width, 202.5);
  assert.equal(box.left, 11 + (400 - 202.5) / 2);
  assert.equal(box.top, 21);
  assert.equal(box.approx, false);
});

test("on a landscape source the preview is its centred 9:16 window, marked approximate", () => {
  const { js } = boot();
  const box = js(`headerPreviewBox({ videoWidth: 1920, videoHeight: 1080, clientWidth: 640,
    clientHeight: 360, offsetLeft: 0, offsetTop: 0 })`);
  assert.equal(box.height, 360);
  assert.equal(box.width, 202.5);
  assert.equal(box.left, (640 - 202.5) / 2);
  assert.equal(box.approx, true);
});

test("an unloaded source has no preview box", () => {
  const { js } = boot();
  assert.equal(js("headerPreviewBox({ videoWidth: 0, videoHeight: 0, clientWidth: 400, clientHeight: 360 })"), null);
});

// --- render and warnings ------------------------------------------------------------

test("a render sends the header look", async () => {
  const { js, ctx, calls } = boot({
    "POST api/jobs/j1/render": () => [200, { status: "done" }],
  });
  ctx.clip = card();
  ctx.clip.headerLook = { ...js('headerPreset("black_plate")'), y: 500 };
  assert.equal(await js("renderClip(clip)"), true);
  const body = JSON.parse(calls.find((c) => c.path === "api/jobs/j1/render").body);
  assert.equal(body.header_look.y, 500);
  assert.equal(body.header_look.plate, "translucent");
});

test("a render that fell back to the basic header says so on the card", async () => {
  const { js, ctx } = boot({
    "POST api/jobs/j1/render": () => [200, { status: "done", header_note: "The header used the basic text renderer: no font" }],
  });
  ctx.clip = card({ statusEl: { textContent: "", className: "", setAttribute() {}, removeAttribute() {} } });
  ctx.clip.headerLook = js('headerPreset("plain")');
  assert.equal(await js("renderClip(clip)"), true);
  assert.match(ctx.clip.statusEl.textContent, /Rendered with a basic header\. The header used the basic text renderer: no font/);
});

test("the face warning follows the header preview, not the ingest plan", () => {
  const { js, ctx } = boot();
  const warn = { textContent: "", hidden: true, classList: { toggle() {} } };
  ctx.clip = card({
    contentEl: { querySelector: () => ({ value: "speech" }) },
    geometryEl: {
      hidden: true,
      querySelector: (sel) => (sel === ".geometry-warning" ? warn : { textContent: "" }),
    },
  });
  const plan = { decision: "crop", reason: "ok", face_rate: 1, safe_rate: 1, warning: "header_zone" };
  const state = { width: 1920, height: 1080, crop_plan: plan, music_plan: plan };
  ctx.state = state;
  js("applyGeometry(clip, state)");
  assert.equal(warn.textContent, "face near header");
  ctx.clip.headerWarnings = { crop_plan: null, music_plan: "header_zone" };
  js("applyGeometry(clip, state)");
  assert.equal(warn.hidden, true);
});
