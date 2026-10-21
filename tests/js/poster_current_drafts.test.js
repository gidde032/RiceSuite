// Current Review recovery (RiceSuite #35), executing the real page functions
// with local storage and media fixtures; no posting or scheduling is allowed.
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
const CURRENT = "riceposter.currentDrafts.v1";

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
  const storage = memoryStorage(stored === undefined ? {} : { [CURRENT]: stored });
  const forbidden = (name) => () => { throw new Error(`${name} must never run from Restore`); };
  const ctx = vm.createContext({
    Date, URL, console, Promise, JSON, Set, Object,
    esc: String,
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
  const saved = () => (storage.data[CURRENT] ? JSON.parse(storage.data[CURRENT]) : null);
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


test("current drafts save text immediately and retain checked identities when stat is unavailable", async () => {
  const slots = { A: draft({ filename: "one.mp4", caption: "first", topic: "manual", style: "hype" }), B: draft() };
  const b = boot({ slots, disk: { "one.mp4": ID(1) } });
  b.js("initCurrentDrafts()");
  await b.js("saveCurrentDrafts()");
  assert.deepEqual(b.saved().drafts[0].media, ID(1));
  slots.A.caption = "latest typed text";
  b.ctx.fetchWithTimeout = async () => { throw new Error("offline"); };
  b.js("saveCurrentDrafts()");
  assert.equal(b.saved().drafts[0].caption, "latest typed text");
  assert.deepEqual(b.saved().drafts[0].media, ID(1));
  assert.equal(b.js("currentDraftsUnsaved()"), false);
});

test("startup preserves the recovery offer until Resume or explicit replacement", async () => {
  const stored = savedBatch([savedDraft(0, "A", "one.mp4")]);
  const b = boot({ stored, disk: { "one.mp4": ID(0) }, confirmAnswer: false });
  b.js("initCurrentDrafts()");
  await b.js("saveCurrentDrafts()");
  assert.equal(b.storage.data[CURRENT], stored);
  assert.equal(b.ctx.state.slots.A.filename, "");
  assert.equal(b.js("allowCurrentReplacement()"), false);
  assert.equal(b.storage.data[CURRENT], stored);
  assert.equal(await b.js("resumeCurrentDrafts()"), true);
  assert.equal(b.ctx.state.slots.A.caption, "caption 0");
  assert.equal(b.calls.filter(c => c.method === "POST").length, 0);
});

test("Resume validates identities, confirms partial recovery and uses receiving default style", async () => {
  const stored = savedBatch([savedDraft(0, "old", "one.mp4"), savedDraft(1, "B", "missing.mp4")]);
  const b = boot({ stored, disk: { "one.mp4": ID(0) }, defaults: { A: "calm" } });
  b.js("initCurrentDrafts()");
  assert.equal(await b.js("resumeCurrentDrafts()"), true);
  assert.equal(b.ctx.state.slots.A.style, "calm");
  assert.match(b.asked.join(" "), /missing.mp4/);
  assert.equal(b.ctx.state.slots.B.filename, "");
});

test("a pending identity lookup cannot resurrect cleared drafts or overwrite newer edits", async () => {
  let release;
  const wait = new Promise(resolve => { release = resolve; });
  const slots = { A: draft({ filename: "one.mp4", caption: "old" }), B: draft() };
  const b = boot({ slots, disk: { "one.mp4": ID(0) }, onStat: async () => wait });
  b.js("initCurrentDrafts()");
  const pending = b.js("saveCurrentDrafts()");
  slots.A.caption = "new";
  b.js("saveCurrentDrafts()");
  slots.A.filename = ""; slots.A.caption = "";
  b.js("saveCurrentDrafts()");
  release(); await pending;
  assert.equal(b.saved(), null);
});

test("an unfinished upload stays explicitly unsaved and is never recoverable as checked media", async () => {
  const b = boot({ slots: { A: draft({ file: { name: "upload.mp4" } }), B: draft() } });
  b.js("initCurrentDrafts()"); await b.js("saveCurrentDrafts()");
  assert.equal(b.js("currentDraftsUnsaved()"), true);
  assert.match(b.saved().unsaved.join(" "), /upload.mp4/);
});

test("storage failure warns before leaving, and a later good save removes the warning", async () => {
  const b = boot({ slots: { A: draft({ caption: "keep this" }), B: draft() } });
  b.js("initCurrentDrafts()");
  const set = b.storage.setItem;
  b.storage.setItem = () => { throw new Error("quota"); };
  await b.js("saveCurrentDrafts()");
  let prevented = false;
  b.ctx.event = { preventDefault() { prevented = true; } };
  b.js("warnUnsavedDrafts(event)");
  assert.equal(prevented, true);
  b.storage.setItem = set;
  await b.js("saveCurrentDrafts()");
  assert.equal(b.js("currentDraftsUnsaved()"), false);
});

test("Resume cancel preserves every saved entry, current drafts and last-batch copy", async () => {
  const stored = savedBatch([savedDraft(0, "A", "one.mp4"), savedDraft(1, "B", "gone.mp4")]);
  const b = boot({ stored, disk: { "one.mp4": ID(0) }, confirmAnswer: false,
    slots: { A: draft({ caption: "current" }), B: draft() } });
  b.js("initCurrentDrafts()");
  assert.equal(await b.js("resumeCurrentDrafts()"), false);
  assert.equal(b.storage.data[CURRENT], stored);
  assert.equal(b.ctx.state.slots.A.caption, "current");
  assert.equal(b.storage.data[KEY], undefined);
});

test("current saves exactly the active roster and excludes posting intent and blobs", async () => {
  const b = boot({ accounts: ["B"], slots: { A: draft({ caption: "inactive" }),
    B: draft({ caption: "active", topic: "edited", reviewStyleChanged: true, thumb: "data:frame", previewUrl: "blob:preview", prevCaption: "undo" }) } });
  b.js("initCurrentDrafts()"); await b.js("saveCurrentDrafts()");
  assert.deepEqual(b.saved().drafts.map(d => d.account), ["B"]);
  const record = JSON.stringify(b.saved());
  assert.doesNotMatch(record, /inactive|data:frame|blob:preview|undo|schedule|headless|platform/i);
  assert.equal(b.saved().drafts[0].reviewStyleChanged, true);
});

test("Resume refuses a draft edited while its media check was pending", async () => {
  let release;
  const wait = new Promise(resolve => { release = resolve; });
  const stored = savedBatch([savedDraft(0, "A", "one.mp4")]);
  const b = boot({ stored, disk: { "one.mp4": ID(0) }, onStat: async () => wait });
  b.js("initCurrentDrafts()");
  const pending = b.js("resumeCurrentDrafts()");
  b.ctx.state.slots.A.caption = "newer";
  release();
  assert.equal(await pending, false);
  assert.equal(b.ctx.state.slots.A.caption, "newer");
  assert.equal(b.storage.data[CURRENT], stored);
});

test("an explicit style-only draft survives reload and Resume", async () => {
  const b = boot({ slots: { A: draft({ style: "hype", reviewStyleChanged: true }), B: draft() } });
  b.js("initCurrentDrafts()"); await b.js("saveCurrentDrafts()");
  const saved = b.saved();
  assert.equal(saved.drafts[0].style, "hype");
  assert.equal(saved.drafts[0].reviewStyleChanged, true);
  const reloaded = boot({ stored: JSON.stringify(saved) });
  reloaded.js("initCurrentDrafts()");
  assert.equal(await reloaded.js("resumeCurrentDrafts()"), true);
  assert.equal(reloaded.ctx.state.slots.A.style, "hype");
  assert.equal(reloaded.ctx.state.slots.A.reviewStyleChanged, true);
});

test("New Run clears current recovery while retaining the picker choice and last batch", async () => {
  const b = boot({ slots: { A: draft({ style: "hype", reviewStyleChanged: true }), B: draft() } });
  b.js("initCurrentDrafts()"); await b.js("saveCurrentDrafts()");
  b.js("newRun()"); await b.js("lastBatchSave");
  assert.equal(b.saved(), null);
  assert.equal(b.ctx.state.slots.A.style, "hype");
  assert.equal(JSON.parse(b.storage.data[KEY]).drafts[0].style, "hype");
});
