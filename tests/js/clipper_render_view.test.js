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
  // Lifecycle checks compare the route; freshness is tested separately below.
  video: clip.outputVideoEl.src.split("?")[0],
  download: clip.downloadEl.href.split("?")[0],
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

// --- review repairs (PR #39) --------------------------------------------------

test("a re-render makes the old video inert while its file is rewritten", async () => {
  const hold = held();
  const { js, ctx } = boot({
    "POST api/jobs/j1/render": async () => { await hold.gate; return [200, { status: "done" }]; },
  });
  ctx.clip = card(true);
  const done = js("renderClip(clip)");
  await settle();
  assert.equal(ctx.clip.outputVideoEl.inert, true);
  hold.release();
  await done;
  assert.equal(ctx.clip.outputVideoEl.inert, false);
});

test("an edit after a render marks the render on show as stale", async () => {
  const { js, ctx } = boot({});
  ctx.clip = card(true);
  ctx.clip.renderedEdits = 0;
  assert.equal(ctx.clip.resultEl.classList.contains("is-stale"), false);
  js("noteClipEdited(clip)");
  assert.equal(ctx.clip.resultEl.classList.contains("is-stale"), true);
});

test("an edit during a render leaves the landed render marked stale", async () => {
  const hold = held();
  const { js, ctx } = boot({
    "POST api/jobs/j1/render": async () => { await hold.gate; return [200, { status: "done" }]; },
  });
  ctx.clip = card(true);
  const done = js("renderClip(clip)");
  await settle();
  js("noteClipEdited(clip)");
  hold.release();
  await done;
  assert.equal(ctx.clip.resultEl.classList.contains("is-stale"), true);
  // Rendering again brings it current.
  const again = js("renderClip(clip)");
  hold.release();
  await again;
  assert.equal(ctx.clip.resultEl.classList.contains("is-stale"), false);
});

for (const action of ["alignLyrics", "restoreTranscript"]) {
  test(`${action} waits while the clip renders, keeping the Rendering note`, async () => {
    const hold = held();
    const { js, ctx, calls } = boot({
      "POST api/jobs/j1/render": async () => { await hold.gate; return [200, { status: "done" }]; },
      "POST api/jobs/j1/lyrics": { words: [], method: "anchors", anchor_rate: 1 },
      "POST api/jobs/j1/restore-transcript": { words: [] },
    });
    ctx.clip = card(true);
    const done = js("renderClip(clip)");
    await settle();
    await js(`${action}(clip)`);
    assert.equal(calls.filter((c) => c.path.endsWith("/lyrics") || c.path.endsWith("/restore-transcript")).length, 0);
    assert.deepEqual(state(ctx.clip), { empty: false, busy: true, video: OUTPUT, download: "" });
    hold.release();
    await done;
  });
}

test("#59: Render all ends on its own bar and asks for the Send click", async () => {
  const { js, ctx, calls } = boot({ "POST api/jobs/j1/render": { status: "done" } });
  ctx.clip = card(false);
  js("messages = []; localProgress = (operation, state, completed, total, label, text) => messages.push({operation, state, count: `${completed} / ${total} ${label}`, text});");
  js("clips.push(clip)");
  await js("handleRenderAll()");
  const last = js("messages[messages.length - 1]");
  assert.equal(last.operation, "Render all");
  assert.equal(last.state, "✓ Complete");
  assert.equal(last.count, "1 / 1 rendered");
  assert.equal(last.text, "Send the batch to Poster when ready.");
  assert.equal(calls.filter((c) => c.path === "api/handoff").length, 0);
});

for (const dropped of [false, true]) {
  test(`changed caption styles refresh preview and download${dropped ? " after a lost render response" : ""}`, async () => {
    const requests = [];
    let job = { status: "ready", has_output: false, render_id: null };
    const { js, ctx } = boot({
      // The render completes; with `dropped`, only its reply is lost.
      "POST api/jobs/j1/render": (call) => {
        requests.push(JSON.parse(call.body));
        job = { status: "done", has_output: true, render_id: requests.at(-1).render_id };
        if (dropped && requests.length > 1) throw new Error("response lost");
        return [200, job];
      },
      "GET api/jobs/j1": () => [200, job],
    });
    ctx.clip = card(false);
    let selected = "clean";
    ctx.clip.captionStyleEl = { querySelector: () => ({ value: selected }) };
    const urls = [];
    for (const style of ["clean", "punch", "editorial"]) {
      selected = style;
      if (urls.length) js("noteClipEdited(clip)");
      assert.equal(await js("renderClip(clip)"), true);
      urls.push(ctx.clip.outputVideoEl.src);
      assert.equal(ctx.clip.downloadEl.href, urls.at(-1));
      assert.equal(urls.at(-1).split("?")[0], OUTPUT);
      assert.equal(js("clipCurrent(clip)"), true);
    }
    assert.deepEqual(requests.map((r) => r.caption_style), ["clean", "punch", "editorial"]);
    assert.equal(new Set(urls).size, 3, "each completed render needs a fresh media URL");
  });
}

// --- Issue #49: after a lost reply, only this render's completion counts ----
// Clipper records the render id of the render it accepts. A request that never
// arrived leaves an earlier render's id (and output) in the job state.

const RENDER_ID = /^[A-Za-z0-9_-]{8,64}$/;
const jobPolls = (calls) => calls.filter((c) => c.method === "GET" && c.path === "api/jobs/j1").length;

// A Clipper that renders every request it receives. `lose(n)` decides, per
// request, whether it is lost "before" it arrives, its reply is lost "after"
// it completes, or neither.
function renderServer(lose = () => null, routes = {}) {
  const received = [];
  let job = { status: "ready", has_output: false, render_id: null };
  const booted = boot({
    "POST api/jobs/j1/render": (call) => {
      const fate = lose(received.length + 1);
      if (fate === "before") throw new TypeError("Failed to fetch");
      received.push(JSON.parse(call.body));
      job = { status: "done", has_output: true, render_id: received.at(-1).render_id };
      if (fate === "after") throw new TypeError("network connection lost");
      return [200, job];
    },
    "GET api/jobs/j1": () => [200, job],
    ...routes,
  });
  return { ...booted, received, setJob: (next) => { job = next; } };
}

test("#49: each render request carries its own render id", async () => {
  const { js, ctx, received } = renderServer();
  ctx.clip = card(false);
  await js("renderClip(clip)");
  await js("renderClip(clip)");
  assert.equal(received.length, 2);
  assert.match(String(received[0].render_id), RENDER_ID);
  assert.match(String(received[1].render_id), RENDER_ID);
  assert.notEqual(received[0].render_id, received[1].render_id);
});

test("#49: a rerender lost before it reached Clipper never passes for its completion", async () => {
  // Clipper keeps the Clean render's id; the Punch request never arrives.
  const { js, ctx, calls, received } = renderServer((n) => (n === 2 ? "before" : null));
  ctx.clip = card(false);
  let selected = "clean";
  ctx.clip.captionStyleEl = { querySelector: () => ({ value: selected }) };
  assert.equal(await js("renderClip(clip)"), true);
  selected = "punch";
  js("noteClipEdited(clip)");

  assert.equal(await js("renderClip(clip)"), false, "the Clean output is not the Punch render");
  assert.equal(js("clipCurrent(clip)"), false);
  assert.equal(ctx.clip.status, "ready");
  assert.match(ctx.clip.error, /Clipper has no record of this render; render it again/);
  assert.equal(ctx.clip.renderUnknown, false, "job state shows no record of the request");
  assert.deepEqual(state(ctx.clip), { empty: true, busy: false, video: "", download: "" });
  assert.equal(jobPolls(calls), 10, "decided after ten job reads, about 30 s");
  assert.equal(received.length, 1, "the lost render is never re-sent on its own");

  // The Punch edit cannot reach Poster.
  js("clips.push(clip)");
  await js("sendBatch()");
  assert.equal(calls.filter((c) => c.path === "api/handoff").length, 0);
});

test("#49: an earlier render's error does not stand in for a lost request", async () => {
  const { js, ctx, setJob } = renderServer(() => "before");
  setJob({ status: "error", error: "an earlier failure", has_output: false, render_id: "earlier-0001" });
  ctx.clip = card(false);
  assert.equal(await js("renderClip(clip)"), false);
  assert.match(ctx.clip.error, /no record of this render/);
});

test("#49: a render that completed but lost its reply recovers with one request", async () => {
  const { js, ctx, received } = renderServer(() => "after");
  ctx.clip = card(false);
  assert.equal(await js("renderClip(clip)"), true);
  assert.equal(js("clipCurrent(clip)"), true);
  assert.equal(received.length, 1);
});

test("#49: a render still running after its reply was lost is followed to the end", async () => {
  let polls = 0;
  let sent = null;
  const { js, ctx } = boot({
    "POST api/jobs/j1/render": (call) => {
      sent = JSON.parse(call.body).render_id;
      throw new TypeError("network connection lost");
    },
    "GET api/jobs/j1": () => {
      polls += 1;
      return [200, polls <= 6
        ? { status: "rendering", has_output: false, render_id: sent }
        : { status: "done", has_output: true, render_id: sent }];
    },
  });
  ctx.clip = card(false);
  assert.equal(await js("renderClip(clip)"), true);
  assert.equal(polls, 7);
});

// A request can wait at Clipper behind the job lock (another tab's
// transcription, a header being generated) before Clipper accepts it.
test("#49: a request queued about 18 s behind the job lock is still recognised", async () => {
  let polls = 0;
  let sent = null;
  const { js, ctx } = boot({
    "POST api/jobs/j1/render": (call) => {
      sent = JSON.parse(call.body).render_id;
      throw new TypeError("network connection lost");
    },
    "GET api/jobs/j1": () => {
      polls += 1;
      return [200, polls <= 6
        ? { status: "done", has_output: true, render_id: "earlier-0001" }
        : { status: "done", has_output: true, render_id: sent }];
    },
  });
  ctx.clip = card(false);
  assert.equal(await js("renderClip(clip)"), true);
  assert.equal(polls, 7);
});

test("#49: a request Clipper accepts a moment late is still recognised", async () => {
  let polls = 0;
  let sent = null;
  const { js, ctx } = boot({
    "POST api/jobs/j1/render": (call) => {
      sent = JSON.parse(call.body).render_id;
      throw new TypeError("network connection lost");
    },
    "GET api/jobs/j1": () => {
      polls += 1;
      return [200, polls <= 2
        ? { status: "done", has_output: true, render_id: "earlier-0001" }
        : { status: "done", has_output: true, render_id: sent }];
    },
  });
  ctx.clip = card(false);
  assert.equal(await js("renderClip(clip)"), true);
  assert.equal(polls, 3);
});

// A seek or volume drag on a player's built-in controls reaches the card as an
// input event from the <video> or <audio> element. Moving through a clip is not
// a review edit, so it must not mark the render stale or hold the batch.
test("player controls are not review edits; card controls are", () => {
  const { js, ctx } = boot({});
  ctx.events = [
    { target: { tagName: "VIDEO" } },
    { target: { tagName: "AUDIO" } },
    { target: { tagName: "TEXTAREA" } },
    { target: { tagName: "INPUT" } },
    { target: { tagName: "SELECT" } },
  ];
  assert.deepEqual(Array.from(js("events.map(isReviewEdit)")), [false, false, true, true, true]);
});
