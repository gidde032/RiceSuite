// Caption Motion and emoji controls in the review UI (RiceSuite #66).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');

function harness(saved = {}) {
  const storage = new Map(Object.entries(saved));
  const store = { getItem: (k) => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
  const element = () => ({ textContent: '', disabled: false, classList: { toggle() {}, add() {}, remove() {} }, addEventListener() {}, setAttribute() {} });
  const context = vm.createContext({ document: { getElementById: element }, localStorage: store,
    sessionStorage: store, window: { sessionStorage: store }, setInterval() {}, setTimeout() { return 0; },
    clearTimeout() {}, console, fetch: async () => ({ ok: true, json: async () => ({}) }) });
  vm.runInContext(source, context);
  return { run: (code) => vm.runInContext(code, context), storage, context };
}

test('Motion is on for a slot that never saved it', () => {
  const h = harness();
  assert.equal(h.run('slotFlag(1, "motion", true)'), true);
});

test('a slot that saved Motion off keeps it off', () => {
  const h = harness({ 'riceclipper.slotStyles.v1': JSON.stringify({ 2: { motion: false } }) });
  assert.equal(h.run('slotFlag(2, "motion", true)'), false);
  assert.equal(h.run('slotFlag(3, "motion", true)'), true);
});

test('saving Motion off is read back as off', () => {
  const h = harness();
  h.run('rememberSlotStyle(4, "motion", false)');
  assert.equal(h.run('slotFlag(4, "motion", true)'), false);
});

// --- caption emoji: the inline transcript editor (option B, 2026-10-06) ----

const W = (texts, step = 0.3) => texts.map((text, i) => ({ text, start: i * step, end: i * step + step, line_start: false }));

test('phrases group like transcribe.phrasing.group_words, by global word index', () => {
  const h = harness();
  const words = W(['a', 'b', '', 'c', 'd', 'e', 'f', 'g']);
  assert.deepEqual(JSON.parse(h.run(`JSON.stringify(phraseGroups(${JSON.stringify(words)}))`)), [[0, 1, 3, 4, 5], [6, 7]]);
  const paused = [...W(['a', 'b']), { text: 'c', start: 2, end: 2.3, line_start: false }];
  assert.deepEqual(JSON.parse(h.run(`JSON.stringify(phraseGroups(${JSON.stringify(paused)}))`)), [[0, 1], [2]]);
});

test('a phrase shows the pick of its first anchor', () => {
  const h = harness();
  const words = JSON.stringify(W(['a', 'b', 'c', 'd', 'e', 'f']));
  const out = JSON.parse(h.run(`JSON.stringify(emojiAnchors(${words}, { 3: ['🔥'], 1: ['😂'], 5: ['🍕'] }))`));
  assert.deepEqual(out, [
    { phrase: [0, 1, 2, 3, 4], anchor: 1, emoji: ['😂'] },
    { phrase: [5], anchor: 5, emoji: ['🍕'] },
  ]);
});

test('adding, replacing, moving and removing emoji', () => {
  const h = harness();
  h.run('var P = [0, 1, 2, 3, 4]');
  let picks = JSON.parse(h.run(`JSON.stringify(addPickEmoji({}, P, 2, '🔥'))`));
  assert.deepEqual(picks, { 2: ['🔥'] });
  picks = JSON.parse(h.run(`JSON.stringify(addPickEmoji(${JSON.stringify(picks)}, P, 4, '🎉'))`));
  assert.deepEqual(picks, { 2: ['🔥', '🎉'] }, 'a second emoji joins the anchor');
  picks = JSON.parse(h.run(`JSON.stringify(addPickEmoji(${JSON.stringify(picks)}, P, 4, '💯'))`));
  assert.deepEqual(picks, { 2: ['🔥', '💯'] }, 'two at most: the second is replaced');
  picks = JSON.parse(h.run(`JSON.stringify(movePick(${JSON.stringify(picks)}, P, 0))`));
  assert.deepEqual(picks, { 0: ['🔥', '💯'] });
  picks = JSON.parse(h.run(`JSON.stringify(removePickEmoji(${JSON.stringify(picks)}, 0, '🔥'))`));
  assert.deepEqual(picks, { 0: ['💯'] });
  picks = JSON.parse(h.run(`JSON.stringify(removePickEmoji(${JSON.stringify(picks)}, 0, '💯'))`));
  assert.deepEqual(picks, {});
});

test('the render payload lists picks in word order', () => {
  const h = harness();
  assert.deepEqual(JSON.parse(h.run(`JSON.stringify(emojiPayload({ 12: ['🍕'], 3: ['🔥', '🎉'] }))`)), [
    { word: 3, emoji: ['🔥', '🎉'] },
    { word: 12, emoji: ['🍕'] },
  ]);
});

function emojiClip(h, extra = {}) {
  h.run('refreshEmoji = () => {}; noteClipEdited = (c) => { c.edits = (c.edits || 0) + 1; }');
  h.run(`var clip = { jobId: 'j1', status: 'ready', words: ${JSON.stringify(W(['pizza', 'night']))},
    emojiPicks: { 1: ['🌙'] }, emojiToggleEl: { checked: true }, captionsToggleEl: { checked: true },
    emojiSuggestEl: { disabled: false }, emojiStatusEl: { textContent: '', className: '' }, edits: 0 };
    collectWords = (c) => c.words; ${extra.setup || ''}`);
}

test('Suggest emoji sends the reviewed words once and replaces the picks', async () => {
  const h = harness();
  emojiClip(h);
  h.run('fetch = async (url, opts) => { __calls.push([url, JSON.parse(opts.body)]); return { ok: true, json: async () => ({ picks: [{ word: 0, emoji: ["🍕"] }] }) }; }');
  vm.runInContext('var __calls = []', h.context);
  await h.run('requestEmoji(clip)');
  const sent = JSON.parse(h.run('JSON.stringify(__calls)'));
  assert.equal(sent.length, 1);
  assert.equal(sent[0][0], 'api/jobs/j1/emoji');
  assert.deepEqual(sent[0][1].words.map((w) => w.text), ['pizza', 'night']);
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(clip.emojiPicks)')), { 0: ['🍕'] });
  assert.equal(h.run('clip.edits'), 1);
  assert.equal(h.run('clip.emojiSuggestEl.disabled'), false);
  assert.match(h.run('clip.emojiStatusEl.textContent'), /1 phrase/);
});

test('a failed suggestion keeps the current picks and says why', async () => {
  const h = harness();
  emojiClip(h);
  h.run('fetch = async () => ({ ok: false, json: async () => ({ detail: "ANTHROPIC_API_KEY is not set" }) })');
  await h.run('requestEmoji(clip)');
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(clip.emojiPicks)')), { 1: ['🌙'] });
  assert.match(h.run('clip.emojiStatusEl.textContent'), /ANTHROPIC_API_KEY is not set/);
  assert.equal(h.run('clip.emojiSuggestEl.disabled'), false);
  assert.equal(h.run('clip.edits'), 0);
});

test('a suggestion for words that were since replaced is dropped', async () => {
  const h = harness();
  emojiClip(h);
  h.run('fetch = async () => { resetEmojiPicks(clip); return { ok: true, json: async () => ({ picks: [{ word: 0, emoji: ["🍕"] }] }) }; }');
  await h.run('requestEmoji(clip)');
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(clip.emojiPicks)')), {});
});

test('Suggest sends nothing while Emoji is off or the clip is not ready', async () => {
  const h = harness();
  emojiClip(h);
  vm.runInContext('var __n = 0; fetch = async () => { __n++; return { ok: true, json: async () => ({ picks: [] }) }; }', h.context);
  h.run('clip.emojiToggleEl.checked = false');
  await h.run('requestEmoji(clip)');
  h.run('clip.emojiToggleEl.checked = true; clip.jobId = null');
  await h.run('requestEmoji(clip)');
  assert.equal(h.run('__n'), 0);
});

test('replacing the words clears the picks', () => {
  const h = harness();
  emojiClip(h);
  h.run('resetEmojiPicks(clip)');
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(clip.emojiPicks)')), {});
  assert.equal(h.run('clip.emojiFocus'), null);
});

test('the rendered status names a render without emoji', () => {
  const h = harness();
  h.run('var shown = []; setClipStatus = (c, text, warn) => shown.push([text, Boolean(warn)]); clipCurrent = () => true');
  h.run('setRenderedStatus({}, "", "no colour-emoji font")');
  assert.deepEqual(JSON.parse(h.run('JSON.stringify(shown[0])')), ['Rendered without the caption emoji. no colour-emoji font', true]);
});
