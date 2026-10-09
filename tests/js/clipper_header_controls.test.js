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
    captionsToggleEl: { checked: true }, motionToggleEl: { checked: true }, emojiToggleEl: { checked: false },
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

test("removing an earlier clip compacts the surviving look onto its handoff slot", () => {
  const { js, ctx, local } = boot();
  local.setItem(SLOT_KEY, JSON.stringify({
    1: { headerLook: { ...js('headerPreset("plain")'), y: 310 } },
    2: { headerLook: { ...js('headerPreset("plain")'), y: 640 } },
  }));
  const first = card({
    localId: 11, ord: 1, status: "ready", file: { name: "first.mp4" },
    el: element(), titleEl: element(),
  });
  first.titleEl.id = "clip-title-11";
  first.titleEl.textContent = "Clip 1 — first.mp4";
  const survivor = card({
    localId: 12, ord: 2, status: "ready", file: { name: "second.mp4" },
    el: element(), titleEl: element(),
  });
  survivor.titleEl.id = "clip-title-12";
  survivor.titleEl.textContent = "Clip 2 — second.mp4";
  survivor.headerLook = js('seedHeaderLook(2, "plain")');
  survivor.headerStyleEl = { querySelector: () => ({ value: "plain" }) };
  survivor.captionStyleEl = { querySelector: () => ({ value: "montserrat" }) };
  ctx.first = first;
  ctx.survivor = survivor;
  js("clips.push(first, survivor)");

  js("removeClip(first)");
  assert.equal(survivor.ord, 1);
  assert.equal(survivor.titleEl.textContent, "Clip 1 — second.mp4");
  assert.equal(survivor.titleEl.id, "clip-title-12");
  assert.equal(survivor.localId, 12);
  assert.equal(survivor.headerLook.y, 640);
  assert.equal(slotStore(local)["1"].headerLook.y, 640);
  js("clipSeq = 12");
  const nextIdentity = js("allocateClipIdentity()");
  assert.equal(nextIdentity.localId, 13);
  assert.equal(nextIdentity.ord, 2);

  // A subsequent edit should save against handoff position 1. The next batch's
  // first clip must pick up this surviving clip's look, not the old slot-1 look.
  js("setHeaderLook(survivor, { ...survivor.headerLook, y: 650 })");
  assert.equal(slotStore(local)["1"].headerLook.y, 650);
  assert.equal(js('seedHeaderLook(1, "plain")').y, 650);
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

test("a header preview request carries the clip's geometry choice", async () => {
  const { js, ctx, calls } = boot({
    "POST api/jobs/j1/header-preview": () => [200, { image: null, warnings: {} }],
  });
  ctx.clip = card({ geometryEl: { querySelector: () => ({ value: "crop" }) } });
  ctx.clip.headerLook = js('headerPreset("plain")');
  await js("requestHeaderPreview(clip)");
  const body = JSON.parse(calls.find((c) => c.path === "api/jobs/j1/header-preview").body);
  assert.equal(body.geometry, "crop");
});

test("geometry and content changes refresh one debounced preview", async () => {
  const { js, ctx, calls, timers } = boot({
    "POST api/jobs/j1/header-preview": () => [200, { image: null, warnings: {} }],
  });
  let geometry = "auto";
  let content = "speech";
  ctx.clip = card({
    geometryEl: { querySelector: () => ({ value: geometry }) },
    contentEl: { querySelector: () => ({ value: content }) },
    geoState: null,
  });

  js("handleGeometryChange(clip)");
  geometry = "crop";
  content = "music";
  js("handleContentChange(clip)");
  const live = timers.filter((timer) => timer.live);
  assert.equal(live.length, 1);
  live[0].fn();
  await settle();

  const preview = calls.find((call) => call.path === "api/jobs/j1/header-preview");
  assert.ok(preview);
  assert.equal(JSON.parse(preview.body).geometry, "crop");
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

// --- review repairs (PR #67) ------------------------------------------------------

test("a saved look out of the request bounds takes the preset value", () => {
  const { js, local } = boot();
  local.setItem(SLOT_KEY, JSON.stringify({
    2: { headerLook: { size: 120, font: "comic_sans", color: "red", plate_opacity: 0, y: 900, plate: "neon", line_spacing: 1.5 } },
  }));
  const look = js('seedHeaderLook(2, "plain")');
  assert.equal(look.size, 42);
  assert.equal(look.font, "arial");
  assert.equal(look.color, "FFFFFF");
  assert.equal(look.plate_opacity, 75);
  assert.equal(look.plate, "none");
  assert.equal(look.y, 900); // in bounds: kept
  assert.equal(look.line_spacing, 1.5);
});

test("a lost render reply still reports the basic-header fallback", async () => {
  let renderId = "";
  const { js, ctx } = boot({
    "POST api/jobs/j1/render": (call) => {
      renderId = JSON.parse(call.body).render_id;
      throw new TypeError("Failed to fetch");
    },
    "GET api/jobs/j1": () => [200, {
      render_id: renderId, status: "done", has_output: true,
      header_note: "The header used the basic text renderer: no font",
    }],
  });
  ctx.setTimeout = (fn) => { fn(); return 0; };
  ctx.clip = card({ statusEl: { textContent: "", className: "", setAttribute() {}, removeAttribute() {} } });
  ctx.clip.headerLook = js('headerPreset("plain")');
  assert.equal(await js("renderClip(clip)"), true);
  assert.match(ctx.clip.statusEl.textContent, /basic header.*no font/);
});

test("a rejected render request explains itself instead of [object Object]", async () => {
  const { js, ctx } = boot({
    "POST api/jobs/j1/render": () => [422, { detail: [{ loc: ["body", "header"], msg: "String should have at most 200 characters" }] }],
  });
  ctx.clip = card({ statusEl: { textContent: "", className: "", setAttribute() {}, removeAttribute() {} } });
  ctx.clip.headerLook = js('headerPreset("plain")');
  assert.equal(await js("renderClip(clip)"), false);
  assert.doesNotMatch(ctx.clip.statusEl.textContent, /object Object/);
  assert.match(ctx.clip.statusEl.textContent, /header: String should have at most 200 characters/);
});

// --- blur-pad preview (Finn's review decision on #65) ---------------------------------

function geoCard(js, ctx, state, extra = {}) {
  ctx.clip = card({
    geoState: state,
    geometryEl: { querySelector: () => ({ value: extra.geometry || "auto" }) },
    contentEl: { querySelector: () => ({ value: extra.content || "speech" }) },
    ...extra.card,
  });
  return js("previewGeometry(clip)");
}

test("the preview follows the render's framing", () => {
  const { js, ctx } = boot();
  const crop = { decision: "crop" };
  const pad = { decision: "blur_pad" };
  assert.equal(geoCard(js, ctx, { width: 1080, height: 1920 }), "pass");
  assert.equal(geoCard(js, ctx, { width: 1080, height: 1350 }), "blur_pad");
  assert.equal(geoCard(js, ctx, { width: 1920, height: 1080, crop_plan: crop }), "crop");
  assert.equal(geoCard(js, ctx, { width: 1920, height: 1080, crop_plan: pad }), "blur_pad");
  assert.equal(geoCard(js, ctx, { width: 1920, height: 1080, crop_plan: crop }, { geometry: "blur_pad" }), "blur_pad");
  assert.equal(geoCard(js, ctx, { width: 1920, height: 1080, crop_plan: pad, music_plan: crop }, { content: "music" }), "crop");
  assert.equal(geoCard(js, ctx, { width: 1920, height: 1080 }, { geometry: "crop" }), "blur_pad");
  assert.equal(geoCard(js, ctx, { width: 1920, height: 1080, crop_plan: crop }, { card: { isPhoto: true } }), "blur_pad");
});

test("a blur-pad preview is the whole output frame fitted in the source box", () => {
  const { js } = boot();
  const box = js(`headerPreviewBox({ videoWidth: 1920, videoHeight: 1080, clientWidth: 400,
    clientHeight: 360, offsetLeft: 0, offsetTop: 0 }, "blur_pad")`);
  assert.equal(box.height, 360);
  assert.equal(box.width, 202.5);
  assert.equal(box.left, (400 - 202.5) / 2);
  assert.equal(box.approx, false);
});

test("the blur-pad mock draws a cover fill and the fitted picture, like the render", () => {
  const { js, ctx } = boot();
  const calls = [];
  const c2d = {
    filter: "none",
    drawImage: (...args) => calls.push({ filter: c2d.filter, args }),
    fillRect() {},
    save() {},
    restore() { c2d.filter = "none"; },
  };
  ctx.canvas = { width: 0, height: 0, getContext: () => c2d };
  ctx.media = { videoWidth: 1920, videoHeight: 1080 };
  js("drawBlurPadMock(canvas, media, 180, 320)");
  assert.equal(ctx.canvas.width, 180);
  assert.equal(ctx.canvas.height, 320);
  assert.equal(calls.length, 2);
  // Background: scaled to cover (320/1080 tall), centred, blurred.
  const [, bx, by, bw, bh] = calls[0].args;
  assert.notEqual(calls[0].filter, "none");
  assert.equal(Math.round(bh), 320);
  assert.equal(Math.round(bw), Math.round((1920 * 320) / 1080));
  assert.equal(Math.round(bx), Math.round((180 - bw) / 2));
  assert.equal(by, 0);
  // Foreground: fitted (180 wide), centred vertically, sharp.
  const [, fx, fy, fw, fh] = calls[1].args;
  assert.equal(calls[1].filter, "none");
  assert.equal(fx, 0);
  assert.equal(fw, 180);
  assert.equal(Math.round(fh), Math.round((1080 * 180) / 1920));
  assert.equal(Math.round(fy), Math.round((320 - fh) / 2));
});
