// Per-slot render and Render all for changed slots only (RiceSuite #86).
// A slot is current when its render's settings fingerprint matches the live
// settings; Render all renders only slots that are not current.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');

function classes() {
  const set = new Set();
  return {
    add: (...names) => names.forEach((n) => set.add(n)),
    remove: (...names) => names.forEach((n) => set.delete(n)),
    toggle: (name, force) => {
      const on = force === undefined ? !set.has(name) : Boolean(force);
      if (on) set.add(name); else set.delete(name);
      return on;
    },
    contains: (name) => set.has(name),
  };
}

// A radio group whose checked value is `value`.
const radios = (value) => ({ value, querySelector() { return { value: this.value }; }, querySelectorAll: () => [] });

const TRACK = { name: 'track.mp3', size: 1000, lastModified: 1 };

function fakeClip(ord, { photo = false } = {}) {
  const words = [{ text: 'hello', start: 0, end: 0.4, line_start: false }, { text: 'world', start: 0.4, end: 0.8, line_start: false }];
  const spans = words.map((w, i) => ({ dataset: { index: String(i) }, textContent: w.text }));
  return {
    ord, localId: ord, jobId: `job${ord}`, status: 'ready', name: `Clip ${ord}`, words, spans,
    isPhoto: photo ? true : undefined,
    transcriptEl: { querySelectorAll: () => spans },
    headerEl: { value: `Header ${ord}` },
    captionsToggleEl: { checked: true }, motionToggleEl: { checked: true }, emojiToggleEl: { checked: false },
    captionStyleEl: radios('montserrat'), headerStyleEl: radios('plain'),
    geometryEl: radios('auto'), contentEl: radios('speech'),
    headerLook: { y: 200, size: 64 },
    emojiPicks: {},
    musicModeEl: { value: 'none' }, musicInputEl: { files: [] },
    musicVolumeEl: { value: '0.3' }, musicStartEl: { value: '0' },
    photoLengthEl: { value: '10' },
    outputVideoEl: { pause() {}, load() {}, removeAttribute() {}, inert: false, src: '' },
    downloadEl: { removeAttribute() {}, href: '', download: '' },
    resultEl: { classList: classes() },
    lyricsAlignEl: { disabled: false }, lyricsRestoreEl: { disabled: false },
    statusEl: { className: '', textContent: '', setAttribute() {} },
    renderOneEl: { disabled: false, textContent: '', addEventListener() {} },
  };
}

// Load app.js with `count` ready clips. `h.renders` lists the job of every
// render request, in order. A job in `h.fail` gets a failed render; while
// `h.hold` is a pending promise, render requests wait on it.
function harness(count = 3, opts = {}) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, { textContent: '', disabled: false, title: '',
      classList: classes(), addEventListener() {}, setAttribute() {} });
    return elements.get(id);
  };
  const storage = new Map();
  const store = { getItem: (k) => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
  const h = { renders: [], uploads: [], fail: new Set(), hold: null, element };
  const context = vm.createContext({ document: { getElementById: element }, localStorage: store,
    sessionStorage: store, window: { sessionStorage: store, confirm: () => true }, setInterval() {},
    setTimeout() { return 0; }, clearTimeout() {}, console, FormData: class { append() {} },
    URL: { revokeObjectURL() {}, createObjectURL: () => 'blob:x' },
    fetch: async (url) => {
      const render = /^api\/jobs\/([^/]+)\/render$/.exec(url);
      if (render) {
        h.renders.push(render[1]);
        if (h.hold) await h.hold;
        if (h.fail.has(render[1])) return { ok: false, status: 500, json: async () => ({ detail: 'render failed' }) };
        return { ok: true, status: 200, json: async () => ({}) };
      }
      const music = /^api\/jobs\/([^/]+)\/music$/.exec(url);
      if (music) { h.uploads.push(music[1]); return { ok: true, json: async () => ({ filename: 'music.mp3' }) }; }
      if (url === 'api/handoff') { h.sent = (h.sent || 0) + 1; return { ok: true, status: 200, json: async () => ({ batch_id: 'b1', clip_count: 1 }) }; }
      return { ok: true, status: 200, json: async () => ({ header: 'A generated header' }) };
    } });
  vm.runInContext(source, context);
  h.context = context;
  h.run = (code) => vm.runInContext(code, context);
  h.run('scheduleHeaderPreview = () => {}; refreshEmoji = () => {};');
  context.__clips = Array.from({ length: count }, (_, i) => fakeClip(i + 1, opts));
  h.run('clips.push(...__clips); updateRenderAllButton();');
  h.clip = (ord) => context.__clips[ord - 1];
  h.button = () => element('render-all-btn');
  return h;
}

// Settle pending promise callbacks (a render request in flight).
const tick = () => new Promise((resolve) => setImmediate(resolve));

async function renderedHarness(count = 3, opts = {}) {
  const h = harness(count, opts);
  await h.run('handleRenderAll()');
  assert.deepEqual(h.renders, h.context.__clips.map((c) => c.jobId));
  h.renders.length = 0;
  return h;
}

test('Render all re-renders only the slots changed since their render', async () => {
  const h = await renderedHarness(3);
  h.clip(1).headerEl.value = 'A new header';
  h.run('noteClipEdited(__clips[0])');
  h.clip(3).captionStyleEl.value = 'bebas';
  h.run('noteClipEdited(__clips[2])');
  await h.run('handleRenderAll()');
  assert.deepEqual(h.renders, ['job1', 'job3']);
  assert.equal(h.element('progress-count').textContent, '3 / 3 rendered');
  assert.match(h.element('batch-status').textContent, /Rendered 2 changed clips/);
});

test('never-rendered and failed slots are Render all targets', async () => {
  const h = harness(3);
  h.fail.add('job2');
  await h.run('handleRenderAll()');
  h.renders.length = 0;
  h.fail.clear();
  h.context.__more = fakeClip(4);
  h.run('clips.push(__more); updateRenderAllButton();');
  assert.equal(h.button().textContent, 'Render 2 changed');
  await h.run('handleRenderAll()');
  assert.deepEqual(h.renders, ['job2', 'job4']);
});

test('with nothing changed Render all is disabled and sends nothing', async () => {
  const h = await renderedHarness(3);
  h.run('updateRenderAllButton()');
  assert.equal(h.button().disabled, true);
  assert.equal(h.button().textContent, 'Render all');
  await h.run('handleRenderAll()');
  assert.deepEqual(h.renders, []);
});

test('an edit that is put back counts as unchanged', async () => {
  const h = await renderedHarness(2);
  const clip = h.clip(1);
  clip.headerEl.value = 'Something else';
  h.run('noteClipEdited(__clips[0])');
  assert.equal(h.button().disabled, false);
  assert.equal(h.button().textContent, 'Render 1 changed');
  assert.equal(clip.resultEl.classList.contains('is-stale'), true);
  assert.match(clip.statusEl.textContent, /Edited since its render/);
  clip.headerEl.value = 'Header 1';
  h.run('noteClipEdited(__clips[0])');
  assert.equal(h.button().disabled, true);
  assert.equal(clip.resultEl.classList.contains('is-stale'), false);
  assert.equal(clip.statusEl.textContent, 'Rendered ✓');
  assert.equal(h.run('clipCurrent(__clips[0])'), true);
});

test('an edit made during a render leaves that slot stale', async () => {
  const h = harness(1);
  let release;
  h.hold = new Promise((resolve) => { release = resolve; });
  const running = h.run('handleRenderAll()');
  await tick();
  assert.deepEqual(h.renders, ['job1']);
  h.clip(1).headerEl.value = 'Typed while rendering';
  h.run('noteClipEdited(__clips[0])');
  release();
  await running;
  assert.equal(h.run('clipCurrent(__clips[0])'), false);
  assert.equal(h.clip(1).resultEl.classList.contains('is-stale'), true);
  assert.match(h.clip(1).statusEl.textContent, /Render all will re-render it/);
  assert.equal(h.button().textContent, 'Render all');
  assert.equal(h.button().disabled, false);
});

// Each change to a rendered setting makes only that slot a target, even with
// no edit event: the fingerprint, not an event count, decides.
const EDITS = {
  'header text': (c) => { c.headerEl.value += '!'; },
  'a transcript word': (c) => { c.spans[0].textContent = 'jello'; },
  'caption style': (c) => { c.captionStyleEl.value = 'bebas'; },
  'captions off': (c) => { c.captionsToggleEl.checked = false; },
  'motion off': (c) => { c.motionToggleEl.checked = false; },
  'emoji on': (c) => { c.emojiToggleEl.checked = true; },
  'an emoji pick': (c) => { c.emojiPicks = { 0: ['🔥'] }; },
  'header style': (c) => { c.headerStyleEl.value = 'boxed'; },
  'header look': (c) => { c.headerLook = { ...c.headerLook, y: 260 }; },
  geometry: (c) => { c.geometryEl.value = 'blur'; },
  content: (c) => { c.contentEl.value = 'music'; },
  'a music track': (c) => { c.musicInputEl.files = [TRACK]; c.musicModeEl.value = 'mix'; },
};

for (const [name, edit] of Object.entries(EDITS)) {
  test(`changing ${name} makes only that slot a Render all target`, async () => {
    const h = await renderedHarness(2);
    edit(h.clip(1));
    h.run('updateRenderAllButton()');
    assert.equal(h.button().textContent, 'Render 1 changed');
    await h.run('handleRenderAll()');
    assert.deepEqual(h.renders, ['job1']);
  });
}

const MUSIC_EDITS = {
  'a different track': (c) => { c.musicInputEl.files = [{ ...TRACK, name: 'other.mp3' }]; },
  'the music volume': (c) => { c.musicVolumeEl.value = '0.6'; },
  'the music start': (c) => { c.musicStartEl.value = '12'; },
  'the music mode': (c) => { c.musicModeEl.value = 'replace'; },
  'removing the track': (c) => { c.musicInputEl.files = []; },
};

for (const [name, edit] of Object.entries(MUSIC_EDITS)) {
  test(`with music, changing ${name} makes the slot a target`, async () => {
    const h = harness(2);
    for (const c of h.context.__clips) { c.musicInputEl.files = [TRACK]; c.musicModeEl.value = 'mix'; }
    await h.run('handleRenderAll()');
    h.renders.length = 0;
    edit(h.clip(2));
    await h.run('handleRenderAll()');
    assert.deepEqual(h.renders, ['job2']);
  });
}

test('without a music track, the volume and start do not change the render', async () => {
  const h = await renderedHarness(1);
  h.clip(1).musicVolumeEl.value = '0.9';
  h.clip(1).musicStartEl.value = '30';
  h.run('noteClipEdited(__clips[0])');
  assert.equal(h.button().disabled, true);
  assert.equal(h.run('clipCurrent(__clips[0])'), true);
});

test('changing a photo length makes the photo slot a target', async () => {
  const h = await renderedHarness(2, { photo: true });
  h.clip(2).photoLengthEl.value = '15';
  await h.run('handleRenderAll()');
  assert.deepEqual(h.renders, ['job2']);
});

test('a generated header updates Render all and the stale note at once', async () => {
  const h = await renderedHarness(2);
  const clip = h.clip(2);
  Object.assign(clip, { headerGenerateEl: { disabled: false }, headerFeedbackEl: { value: '' },
    headerGenStatusEl: { className: '', textContent: '' } });
  await h.run('regenerateHeader(__clips[1])');
  assert.equal(clip.headerEl.value, 'A generated header');
  assert.equal(h.button().textContent, 'Render 1 changed');
  assert.equal(clip.resultEl.classList.contains('is-stale'), true);
});

test('a new emoji pick updates Render all at once', async () => {
  const h = await renderedHarness(2);
  h.run('setEmojiPicks(__clips[0], { 1: ["🎉"] })');
  assert.equal(h.button().textContent, 'Render 1 changed');
});

test('the slot button renders exactly its own slot, even when unchanged', async () => {
  const h = await renderedHarness(3);
  await h.run('renderOne(__clips[1])');
  assert.deepEqual(h.renders, ['job2']);
  assert.equal(h.clip(2).status, 'done');
  assert.equal(h.element('progress-count').textContent, '1 / 1 rendered');
});

test('the slot button names a first render and a re-render', async () => {
  const h = harness(2);
  assert.equal(h.clip(1).renderOneEl.textContent, 'Render this clip');
  assert.equal(h.clip(1).renderOneEl.disabled, false);
  await h.run('renderOne(__clips[0])');
  assert.equal(h.clip(1).renderOneEl.textContent, 'Re-render this clip');
  assert.equal(h.clip(2).renderOneEl.textContent, 'Render this clip');
  assert.equal(h.button().textContent, 'Render 1 changed');
});

test('a slot that is not ready has its button disabled', () => {
  const h = harness(2);
  h.clip(2).status = 'transcribing';
  h.run('updateRenderAllButton()');
  assert.equal(h.clip(2).renderOneEl.disabled, true);
  assert.equal(h.clip(1).renderOneEl.disabled, false);
});

for (const [name, start] of [['a slot render', 'renderOne(__clips[0])'], ['Render all', 'handleRenderAll()']]) {
  test(`during ${name}, every slot button and Render all are disabled`, async () => {
    const h = harness(3);
    let release;
    h.hold = new Promise((resolve) => { release = resolve; });
    const running = h.run(start);
    await tick();
    assert.equal(h.button().disabled, true);
    for (const c of h.context.__clips) assert.equal(c.renderOneEl.disabled, true, `slot ${c.ord}`);
    await h.run('renderOne(__clips[2])');
    await h.run('handleRenderAll()');
    assert.equal(h.renders.length, 1);
    release();
    await running;
    for (const c of h.context.__clips) assert.equal(c.renderOneEl.disabled, false, `slot ${c.ord}`);
  });
}

test('the Render all label follows the target count', async () => {
  const h = harness(3);
  assert.equal(h.button().textContent, 'Render all');
  assert.equal(h.button().disabled, false);
  await h.run('handleRenderAll()');
  assert.equal(h.button().textContent, 'Render all');
  assert.equal(h.button().disabled, true);
  for (const [ord, label] of [[1, 'Render 1 changed'], [2, 'Render 2 changed'], [3, 'Render all']]) {
    h.clip(ord).headerEl.value = 'changed';
    h.run(`noteClipEdited(__clips[${ord - 1}])`);
    assert.equal(h.button().textContent, label);
  }
});

test('Send holds a changed slot and not one that was put back', async () => {
  const h = await renderedHarness(1);
  h.clip(1).headerEl.value = 'changed';
  h.run('noteClipEdited(__clips[0])');
  await h.run('sendBatch()');
  assert.equal(h.sent, undefined);
  assert.match(h.element('batch-status').textContent, /Clip 1 changed after its render/);
  h.clip(1).headerEl.value = 'Header 1';
  h.run('noteClipEdited(__clips[0])');
  await h.run('sendBatch()');
  assert.equal(h.sent, 1);
});

test('the sent-batch snapshot ignores an edit that is put back', async () => {
  const h = await renderedHarness(2);
  const before = h.run('batchSnapshot()');
  h.clip(2).headerEl.value = 'changed';
  h.run('noteClipEdited(__clips[1])');
  assert.notEqual(h.run('batchSnapshot()'), before);
  h.clip(2).headerEl.value = 'Header 2';
  h.run('noteClipEdited(__clips[1])');
  assert.equal(h.run('batchSnapshot()'), before);
});
