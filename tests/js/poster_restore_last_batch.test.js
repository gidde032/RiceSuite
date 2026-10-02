// Poster's Restore last batch (RiceSuite #30), run against the real functions
// in poster/frontend/index.html. "Last batch" is the drafts Review held just
// before New Run, a Pull or a Restore replaced them. Restore only fills
// drafts: it never posts, schedules, or acknowledges a pull, asks before
// overwriting unposted drafts, and says which drafts it could not bring back.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { element, scriptedFetch } = require("./harness");

const HTML = fs.readFileSync(
  path.join(__dirname, "..", "..", "poster", "frontend", "index.html"),
  "utf8",
);

function slice(from, to) {
  const start = HTML.indexOf(from);
  const end = HTML.indexOf(to, start);
  assert.ok(start >= 0 && end > start, `could not find ${from}`);
  return HTML.slice(start, end);
}

// Verbatim page code: the draft predicates and busy guards, the Restore block,
// and New Run.
const SOURCE = [
  slice("function knownStyle(", "function styleOptions("),
  slice("function draftsAtRisk()", "// Returns true when a batch was pulled"),
  slice("// --- Restore last batch", "// Capture a frame from a staged"),
  slice("function newRun()", "async function refreshMediaInfo()"),
].join("\n");

const KEY = "riceposter.lastBatch.v1";

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    data,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
  };
}

const draft = (fields = {}) => ({
  file: null, filename: "", mediaType: "", topic: "", caption: "", style: "",
  prevCaption: "", thumb: "", previewUrl: "", previewIsObjectUrl: false, ...fields,
});

// `disk` maps a staged filename to its identity; a name absent from it is gone.
function boot({
  accounts = ["A", "B"], slots, disk = {}, stored, confirmAnswer = true, defaults = {},
  captionStyles, onStat, thumb = async () => "data:thumb",
} = {}) {
  let js;
  const { fetch, calls } = scriptedFetch({
    "GET api/media-stat": async (call) => {
      if (onStat) await onStat(js);
      const names = new URL(`http://x/${call.url}`).searchParams.getAll("name");
      return [200, { files: Object.fromEntries(names.map((n) => [n, disk[n] || null])) }];
    },
  });
  const statuses = [];
  const asked = [];
  const button = element();
  const storage = memoryStorage(stored === undefined ? {} : { [KEY]: stored });
  const forbidden = (name) => () => { throw new Error(`${name} must never run from Restore`); };
  const ctx = vm.createContext({
    Date, URL, console, Promise, JSON, Set, Object,
    state: {
      accounts: accounts.map((slot) => ({ slot })),
      slots: slots || Object.fromEntries(accounts.map((a) => [a, draft()])),
      accountState: { caption_defaults: defaults },
      defaultCaptionStyle: "generic",
      captionStyles,
      runId: "",
    },
    localStorage: storage,
    fetchWithTimeout: fetch,
    handleFetchError: async (resp) => { if (!resp.ok) throw new Error(`HTTP ${resp.status}`); },
    el: () => element(),
    elOpt: (id) => (id === "btnRestore" ? button : element()),
    setPullStatus: (m, isError = false) => statuses.push({ m, isError }),
    confirm: (m) => { asked.push(m); return confirmAnswer; },
    clearSlotPreview() {}, setCaptionError() {}, updateThumbChip() {},
    renderSlots() {}, renderSummary() {}, updateButtons() {},
    captureThumbnailFromUrl: thumb,
    postAll: forbidden("postAll"), scheduleAll: forbidden("scheduleAll"),
    pullFromClipper: forbidden("pullFromClipper"),
  });
  vm.runInContext(SOURCE, ctx);
  js = (expr) => vm.runInContext(expr, ctx);
  const saved = () => (storage.data[KEY] ? JSON.parse(storage.data[KEY]) : null);
  return { js, ctx, calls, statuses, asked, button, storage, saved };
}

const ID = (n) => ({ size: 100 + n, mtime_ns: 1_000_000 + n });

function savedBatch(drafts) {
  return JSON.stringify({ version: 1, saved_at: "2026-09-29T12:00:00.000Z", reason: "new-run", drafts });
}

const savedDraft = (position, account, filename, extra = {}) => ({
  position, account, filename, mediaType: "video", topic: `topic ${position}`,
  caption: `caption ${position}`, style: "hype", media: filename ? ID(position) : null, ...extra,
});

// --- saving ---------------------------------------------------------------

test("New Run saves the drafts it discards, in roster order, with each file's identity", async () => {
  const slots = {
    A: draft({ filename: "A_clip.mp4", mediaType: "video", topic: "t1", caption: "c1", style: "hype" }),
    B: draft({ filename: "B_pic.jpg", mediaType: "image", topic: "t2", caption: "c2", style: "calm" }),
  };
  const { js, saved, asked } = boot({ slots, disk: { "A_clip.mp4": ID(1), "B_pic.jpg": ID(2) } });
  js("newRun()");
  await js("lastBatchSave");
  assert.equal(asked.length, 1, "New Run still asks before discarding drafts");
  assert.equal(slots.A.filename, "", "New Run still clears Review");
  const batch = saved();
  assert.equal(batch.reason, "new-run");
  assert.deepEqual(
    batch.drafts.map(({ position, account, filename, mediaType, topic, caption, style, media }) =>
      [position, account, filename, mediaType, topic, caption, style, media]),
    [
      [0, "A", "A_clip.mp4", "video", "t1", "c1", "hype", ID(1)],
      [1, "B", "B_pic.jpg", "image", "t2", "c2", "calm", ID(2)],
    ],
  );
});

test("an empty New Run never replaces the saved batch", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_clip.mp4")]);
  const { js, storage } = boot({ stored });
  js("newRun()");
  await js("lastBatchSave");
  assert.equal(storage.data[KEY], stored);
});

test("a draft whose upload has not finished, with no caption, is not saved", async () => {
  const slots = { A: draft({ file: {}, filename: "" }), B: draft({ filename: "B.mp4", caption: "c" }) };
  const { js, saved } = boot({ slots, disk: { "B.mp4": ID(2) } });
  js("newRun()");
  await js("lastBatchSave");
  assert.deepEqual(saved().drafts.map((d) => d.account), ["B"]);
});

// --- restoring --------------------------------------------------------------

test("Restore puts media and captions back into an empty Review, reading only media identity", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_clip.mp4"), savedDraft(1, "B", "B_clip.mp4")]);
  const { js, ctx, calls, asked } = boot({ stored, disk: { "A_clip.mp4": ID(0), "B_clip.mp4": ID(1) } });
  assert.equal(await js("restoreLastBatch()"), true);
  assert.equal(asked.length, 0, "nothing to overwrite, nothing partial: no question");
  const { A, B } = ctx.state.slots;
  assert.deepEqual([A.filename, A.caption, A.topic, A.mediaType, A.style], ["A_clip.mp4", "caption 0", "topic 0", "video", "hype"]);
  assert.deepEqual([B.filename, B.caption], ["B_clip.mp4", "caption 1"]);
  assert.equal(A.thumb, "data:thumb", "the caption frame is captured again from the media");
  assert.ok(calls.every((c) => c.method === "GET" && c.path === "api/media-stat"), JSON.stringify(calls));
});

test("Restore asks before overwriting drafts; Cancel changes nothing", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_clip.mp4")]);
  const slots = { A: draft({ caption: "typed now" }), B: draft() };
  const { js, asked, storage } = boot({ slots, stored, disk: { "A_clip.mp4": ID(0) }, confirmAnswer: false });
  assert.equal(await js("restoreLastBatch()"), false);
  assert.equal(asked.length, 1);
  assert.match(asked[0], /unposted draft/);
  assert.equal(slots.A.caption, "typed now");
  assert.equal(storage.data[KEY], stored);
});

test("the drafts Restore replaces become the last batch, so a second Restore undoes it", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_old.mp4")]);
  const slots = { A: draft({ filename: "A_new.mp4", mediaType: "video", caption: "new" }), B: draft() };
  const disk = { "A_old.mp4": ID(0), "A_new.mp4": ID(9) };
  const { js, ctx, asked } = boot({ slots, stored, disk });
  assert.equal(await js("restoreLastBatch()"), true);
  assert.equal(ctx.state.slots.A.caption, "caption 0");
  assert.equal(await js("restoreLastBatch()"), true);
  assert.equal(asked.length, 2);
  assert.deepEqual([ctx.state.slots.A.filename, ctx.state.slots.A.caption], ["A_new.mp4", "new"]);
});

test("media that is gone or was replaced under the same name is named, and the rest is restored after a confirm", async () => {
  const stored = savedBatch([
    savedDraft(0, "A", "A_gone.mp4"),
    savedDraft(1, "B", "B_reused.mp4"),
    savedDraft(2, "C", "C_ok.mp4"),
  ]);
  const disk = { "B_reused.mp4": { size: 5, mtime_ns: 42 }, "C_ok.mp4": ID(2) };
  const slots = { A: draft({ caption: "keep me" }), B: draft(), C: draft() };
  const { js, ctx, asked } = boot({ accounts: ["A", "B", "C"], slots, stored, disk });
  assert.equal(await js("restoreLastBatch()"), true);
  assert.equal(asked.length, 1);
  assert.match(asked[0], /A_gone\.mp4/);
  assert.match(asked[0], /B_reused\.mp4/);
  assert.match(asked[0], /1 of 3/);
  assert.equal(ctx.state.slots.A.caption, "keep me", "a slot whose draft cannot come back is left as it is");
  assert.equal(ctx.state.slots.B.filename, "", "an old caption is never paired with a new upload");
  assert.equal(ctx.state.slots.C.caption, "caption 2");
});

test("a partial restore the user cancels changes nothing", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_gone.mp4"), savedDraft(1, "B", "B_ok.mp4")]);
  const { js, ctx, asked } = boot({ stored, disk: { "B_ok.mp4": ID(1) }, confirmAnswer: false });
  assert.equal(await js("restoreLastBatch()"), false);
  assert.equal(asked.length, 1);
  assert.equal(ctx.state.slots.B.filename, "");
});

test("when no saved media remains, nothing is restored and the reason is shown", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_gone.mp4")]);
  const { js, ctx, asked, statuses } = boot({ stored });
  assert.equal(await js("restoreLastBatch()"), false);
  assert.equal(asked.length, 0);
  assert.equal(ctx.state.slots.A.filename, "");
  assert.match(statuses.at(-1).m, /A_gone\.mp4/);
});

test("a restored draft whose style left prompts/ takes its account's default style (#50)", async () => {
  const stored = savedBatch([
    savedDraft(0, "A", "A_clip.mp4", { style: "retired" }),
    savedDraft(1, "B", "B_clip.mp4", { style: "retired" }),
  ]);
  const disk = { "A_clip.mp4": ID(0), "B_clip.mp4": ID(1) };
  const captionStyles = ["generic", "calm"].map((name) => ({ name, display_name: name }));
  const { js, ctx } = boot({ stored, disk, defaults: { A: "calm" }, captionStyles });
  assert.equal(await js("restoreLastBatch()"), true);
  const { A, B } = ctx.state.slots;
  assert.deepEqual([A.caption, A.style], ["caption 0", "calm"]);
  assert.deepEqual([B.caption, B.style], ["caption 1", "generic"]);
});

test("a changed roster is filled by position; the caption stays and takes the new account's style", async () => {
  const stored = savedBatch([
    savedDraft(0, "A", "A_clip.mp4"),
    savedDraft(1, "B", "B_clip.mp4"),
    savedDraft(2, "C", "C_clip.mp4"),
  ]);
  const disk = { "A_clip.mp4": ID(0), "B_clip.mp4": ID(1), "C_clip.mp4": ID(2) };
  const { js, ctx, asked } = boot({ accounts: ["D", "A"], stored, disk, defaults: { D: "calm" } });
  assert.equal(await js("restoreLastBatch()"), true);
  assert.equal(asked.length, 1);
  assert.match(asked[0], /C_clip\.mp4/, "the draft beyond the roster is named");
  const { D, A } = ctx.state.slots;
  assert.deepEqual([D.filename, D.caption, D.style], ["A_clip.mp4", "caption 0", "calm"]);
  assert.deepEqual([A.filename, A.caption, A.style], ["B_clip.mp4", "caption 1", "generic"]);
});

test("a caption-only draft comes back without a media check", async () => {
  const stored = savedBatch([savedDraft(0, "A", "", { mediaType: "", caption: "just words" })]);
  const { js, ctx, calls } = boot({ stored });
  assert.equal(await js("restoreLastBatch()"), true);
  assert.equal(ctx.state.slots.A.caption, "just words");
  assert.equal(calls.length, 0);
});

for (const [label, busy] of [
  ["an upload or caption request", "draftWork = 1"],
  ["a pull", "pullInFlight = true"],
  ["an account change", "accountChangeInFlight = true"],
]) {
  test(`Restore waits for ${label}`, async () => {
    const stored = savedBatch([savedDraft(0, "A", "A_clip.mp4")]);
    const { js, ctx, calls, statuses } = boot({ stored, disk: { "A_clip.mp4": ID(0) } });
    js(busy);
    assert.equal(await js("restoreLastBatch()"), false);
    assert.equal(ctx.state.slots.A.filename, "");
    assert.equal(calls.length, 0);
    assert.equal(statuses.at(-1).isError, true);
  });
}

test("a Restore in progress blocks draft moves and a second Restore", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_clip.mp4")]);
  const { js } = boot({ stored, disk: { "A_clip.mp4": ID(0) } });
  const first = js("restoreLastBatch()");
  assert.equal(js("draftWorkBusy()"), true);
  assert.equal(await js("restoreLastBatch()"), false);
  assert.equal(await first, true);
  assert.equal(js("draftWorkBusy()"), false);
});

for (const [label, stored] of [
  ["nothing saved", undefined],
  ["unreadable storage", "{not json"],
  ["an unknown format", JSON.stringify({ version: 99, drafts: [] })],
]) {
  test(`${label}: Restore explains and changes nothing`, async () => {
    const { js, ctx, statuses } = boot({ stored });
    assert.equal(await js("restoreLastBatch()"), false);
    assert.equal(ctx.state.slots.A.filename, "");
    assert.match(statuses.at(-1).m, /Nothing to restore/);
  });
}

// --- the button -------------------------------------------------------------

test("the button is disabled, with a reason, when nothing is saved or no saved media remains", async () => {
  const empty = boot();
  await empty.js("refreshRestoreButton()");
  assert.equal(empty.button.disabled, true);
  assert.match(empty.button.title, /Nothing saved/);

  const gone = boot({ stored: savedBatch([savedDraft(0, "A", "A_gone.mp4")]) });
  await gone.js("refreshRestoreButton()");
  assert.equal(gone.button.disabled, true);
  assert.match(gone.button.title, /no longer available/);

  const ok = boot({ stored: savedBatch([savedDraft(0, "A", "A.mp4")]), disk: { "A.mp4": ID(0) } });
  await ok.js("refreshRestoreButton()");
  assert.equal(ok.button.disabled, false);
});

test("the page has the button next to Pull and keeps its state current", () => {
  assert.match(HTML, /id="btnRestore"[^>]*onclick="restoreLastBatch\(\)"/);
  assert.match(HTML, /setInterval\(refreshRestoreButton, INBOX_POLL_MS\)/);
  const callers = HTML.match(/restoreLastBatch\(/g) || [];
  assert.equal(callers.length, 2, "only the button starts a Restore");
});

// --- review repairs (PR #36) ------------------------------------------------

test("an upload or caption request started while Restore checks media aborts the Restore", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_old.mp4")]);
  const { js, ctx, statuses } = boot({
    stored, disk: { "A_old.mp4": ID(0) },
    onStat: (page) => { page("draftWork = 1"); },
  });
  assert.equal(await js("restoreLastBatch()"), false);
  assert.equal(ctx.state.slots.A.filename, "", "nothing applied under a running upload");
  assert.equal(statuses.at(-1).isError, true);
  assert.equal(js("restoreInFlight"), false);
});

test("New Run waits while a Restore is running", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_old.mp4")]);
  const slots = { A: draft({ caption: "mine" }), B: draft() };
  let during;
  const { js, asked } = boot({
    slots, stored, disk: { "A_old.mp4": ID(0) }, confirmAnswer: false,
    onStat: (page) => { if (during === undefined) during = page("newRun()"); },
  });
  await js("restoreLastBatch()");
  assert.equal(during, false);
  assert.equal(slots.A.caption, "mine");
  assert.equal(asked.filter((m) => /Start a new run/.test(m)).length, 0);
});

test("Restore makes Review the saved batch: a slot with no saved draft is cleared, so Restore again is a true undo", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_old.mp4"), savedDraft(1, "B", "B_old.mp4")]);
  const slots = { A: draft({ filename: "A_now.mp4", mediaType: "video", caption: "a2" }), B: draft() };
  const disk = { "A_old.mp4": ID(0), "B_old.mp4": ID(1), "A_now.mp4": ID(7) };
  const { js, ctx, asked } = boot({ slots, stored, disk });
  assert.equal(await js("restoreLastBatch()"), true);
  assert.deepEqual([ctx.state.slots.A.caption, ctx.state.slots.B.caption], ["caption 0", "caption 1"]);
  assert.equal(await js("restoreLastBatch()"), true);
  assert.deepEqual(
    [ctx.state.slots.A.filename, ctx.state.slots.A.caption, ctx.state.slots.B.filename, ctx.state.slots.B.caption],
    ["A_now.mp4", "a2", "", ""],
  );
  assert.match(asked.at(-1), /Cleared.*account 2/);
});

test("a slot beyond the saved batch's drafts is cleared after the confirm names it, never left mixed in", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_old.mp4")]);
  const slots = { A: draft(), B: draft({ filename: "B_new.mp4", caption: "new b" }) };
  const { js, ctx, asked } = boot({ slots, stored, disk: { "A_old.mp4": ID(0) } });
  assert.equal(await js("restoreLastBatch()"), true);
  assert.equal(asked.length, 1);
  assert.match(asked[0], /account 2/);
  assert.equal(ctx.state.slots.B.filename, "");
  assert.equal(ctx.state.slots.B.caption, "");
});

test("the confirmation names the account slot beside each file it cannot bring back", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_gone.mp4"), savedDraft(1, "B", "B_ok.mp4")]);
  const { js, asked } = boot({ stored, disk: { "B_ok.mp4": ID(1) } });
  await js("restoreLastBatch()");
  assert.match(asked[0], /account 1 \(A_gone\.mp4\)/);
});

test("media that could not be checked when saved is reported as unchecked, not as deleted", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_clip.mp4", { media: null })]);
  const { js, statuses } = boot({ stored, disk: { "A_clip.mp4": ID(0) } });
  assert.equal(await js("restoreLastBatch()"), false);
  assert.match(statuses.at(-1).m, /could not be checked/);
  assert.doesNotMatch(statuses.at(-1).m, /deleted/);
});

test("a slow caption frame never keeps Restore busy", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_clip.mp4")]);
  const { js } = boot({ stored, disk: { "A_clip.mp4": ID(0) }, thumb: () => new Promise(() => {}) });
  const done = await Promise.race([
    js("restoreLastBatch()"),
    new Promise((resolve) => setTimeout(() => resolve("hung"), 50)),
  ]);
  assert.equal(done, true);
  assert.equal(js("draftWorkBusy()"), false);
});

test("a button refresh that finishes during a Restore leaves the button alone", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_clip.mp4")]);
  const { js, button } = boot({
    stored, disk: { "A_clip.mp4": ID(0) },
    onStat: (page) => { page("restoreInFlight = true"); },
  });
  button.disabled = true;
  button.title = "busy";
  await js("refreshRestoreButton()");
  assert.equal(button.disabled, true);
  assert.equal(button.title, "busy");
});

test("an older button refresh finishing last never overwrites a newer one", async () => {
  const stored = savedBatch([savedDraft(0, "A", "A_clip.mp4")]);
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let first = true;
  const { js, button, storage } = boot({
    stored, disk: { "A_clip.mp4": ID(0) },
    onStat: async () => { if (first) { first = false; await gate; } },
  });
  const older = js("refreshRestoreButton()");
  delete storage.data[KEY];
  await js("refreshRestoreButton()");
  assert.equal(button.disabled, true);
  release();
  await older;
  assert.equal(button.disabled, true, "the stale result for the old snapshot was dropped");
  assert.match(button.title, /Nothing saved/);
});

test("a restored image draft gets its caption frame from an image, not a video", async () => {
  const made = [];
  const ctx = vm.createContext({
    console,
    document: { createElement: (tag) => { made.push(tag); return element(); } },
    Image: class { set src(v) { this._src = v; setImmediate(() => this.onload()); } },
    drawThumb: () => "data:image-frame",
  });
  vm.runInContext(slice("function captureThumbnailFromUrl(", "function setPullStatus("), ctx);
  const frame = await vm.runInContext("captureThumbnailFromUrl('api/media/A.jpg', 'image')", ctx);
  assert.equal(frame, "data:image-frame");
  assert.deepEqual(made, []);
});
