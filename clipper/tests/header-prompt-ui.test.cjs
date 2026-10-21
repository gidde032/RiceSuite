// Header prompt style dropdown (RiceSuite #92): the card lists the prompt
// styles from api/header-options, seeds each slot's saved choice or the
// configured default, and ✨ Generate sends the selected style.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');

const PROMPTS = [
  { name: 'generic-header', display_name: 'Generic / neutral' },
  { name: 'punchy', display_name: 'Punchy' },
];

// A <select> that holds real option values, like the browser's: setting a
// value no option has leaves it at "".
function fakeSelect() {
  return {
    options: [{ value: '', textContent: 'Default prompt' }],
    current: '',
    get value() { return this.current; },
    set value(v) { this.current = this.options.some((o) => o.value === v) ? v : ''; },
    replaceChildren(...options) { this.options = options; this.current = options.length ? options[0].value : ''; },
    listeners: {},
    attrs: {},
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    setAttribute(k, v) { this.attrs[k] = v; },
    // The user picks an option: the value changes, then "change" fires.
    choose(v) { this.value = v; (this.listeners.change || []).forEach((fn) => fn({ target: this })); },
  };
}

function fakeClip(ord) {
  return {
    ord, localId: ord, jobId: `job${ord}`, status: 'ready', words: [{ text: 'hello', start: 0, end: 0.4 }],
    transcriptEl: { querySelectorAll: () => [{ dataset: { index: '0' }, textContent: 'hello' }] },
    headerEl: { value: `Header ${ord}` },
    headerGenerateEl: { disabled: false },
    headerGenStatusEl: { className: '', textContent: '' },
    headerFeedbackEl: { value: '' },
    headerPromptEl: fakeSelect(),
  };
}

// Load app.js. `h.posts` lists every header request body; `options` is what
// api/header-options returns (null: the request fails).
function harness({ options = { prompts: PROMPTS, default_prompt: 'punchy', fonts: [] }, saved = null, storage = new Map() } = {}) {
  if (saved) storage.set('riceclipper.slotStyles.v1', JSON.stringify(saved));
  const store = { getItem: (k) => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
  const element = () => ({ textContent: '', disabled: false, classList: { add() {}, remove() {}, toggle() {}, contains: () => false }, addEventListener() {}, setAttribute() {} });
  const h = { posts: [], optionReads: 0, storage };
  const context = vm.createContext({
    document: { addEventListener() {}, getElementById: element, createElement: () => ({ value: '', textContent: '' }) },
    localStorage: store, sessionStorage: store, window: { addEventListener() {}, sessionStorage: store, confirm: () => true },
    setInterval() {}, setTimeout() { return 0; }, clearTimeout() {}, console,
    FormData: class { append() {} }, URL: { revokeObjectURL() {}, createObjectURL: () => 'blob:x' },
    fetch: async (url, init) => {
      if (url === 'api/header-options') {
        h.optionReads += 1;
        if (!options) throw new Error('offline');
        return { ok: true, status: 200, json: async () => options };
      }
      if (/^api\/jobs\/[^/]+\/header$/.test(url)) {
        h.posts.push(JSON.parse(init.body));
        return { ok: true, status: 200, json: async () => ({ header: 'A generated header' }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  });
  vm.runInContext(source, context);
  h.run = (code) => vm.runInContext(code, context);
  h.run('scheduleHeaderPreview = () => {}; noteClipEdited = () => {};');
  h.context = context;
  h.add = (...ords) => {
    context.__clips = ords.map(fakeClip);
    h.run('clips.push(...__clips);');
    return context.__clips;
  };
  return h;
}

const values = (select) => select.options.map((o) => [o.value, o.textContent]);
const savedStyles = (h) => JSON.parse(h.storage.get('riceclipper.slotStyles.v1') || '{}');

test('the dropdown lists every prompt style and preselects the configured default', async () => {
  const h = harness();
  const [clip] = h.add(1);
  await h.run('loadHeaderOptions()');
  assert.deepEqual(values(clip.headerPromptEl), [['generic-header', 'Generic / neutral'], ['punchy', 'Punchy']]);
  assert.equal(clip.headerPromptEl.value, 'punchy');
});

test("a slot's saved prompt style is restored", async () => {
  const h = harness({ saved: { 2: { headerPrompt: 'generic-header' } } });
  const [one, two] = h.add(1, 2);
  await h.run('loadHeaderOptions()');
  assert.equal(one.headerPromptEl.value, 'punchy');
  assert.equal(two.headerPromptEl.value, 'generic-header');
});

test('a saved style that no longer exists falls back to the default', async () => {
  const h = harness({ saved: { 1: { headerPrompt: 'deleted-style' } } });
  const [clip] = h.add(1);
  await h.run('loadHeaderOptions()');
  assert.equal(clip.headerPromptEl.value, 'punchy');
});

test('a configured default that is not on disk falls back to the neutral seed, then the first style', async () => {
  const listed = [{ name: 'alpha', display_name: 'Alpha' }, ...PROMPTS];
  const h = harness({ options: { prompts: listed, default_prompt: 'not-on-disk', fonts: [] } });
  const [clip] = h.add(1);
  await h.run('loadHeaderOptions()');
  assert.equal(clip.headerPromptEl.value, 'generic-header');

  const g = harness({ options: { prompts: [listed[0], PROMPTS[1]], default_prompt: 'not-on-disk', fonts: [] } });
  const [other] = g.add(1);
  await g.run('loadHeaderOptions()');
  assert.equal(other.headerPromptEl.value, 'alpha');
});

test('a style without a name is not listed', async () => {
  const h = harness({ options: { prompts: [{ name: '', display_name: 'Nameless' }, ...PROMPTS], default_prompt: 'punchy', fonts: [] } });
  const [clip] = h.add(1);
  await h.run('loadHeaderOptions()');
  assert.deepEqual(clip.headerPromptEl.options.map((o) => o.value), ['generic-header', 'punchy']);
});

test('a card wired after the list loads is filled and labelled for its clip', async () => {
  const h = harness();
  await h.run('loadHeaderOptions()');
  const [clip] = h.add(3);
  h.run('wireHeaderPrompt(__clips[0])');
  assert.equal(clip.headerPromptEl.options.length, 2);
  assert.equal(clip.headerPromptEl.value, 'punchy');
  assert.equal(clip.headerPromptEl.attrs['aria-label'], 'Header prompt style for clip 3');
});

test('choosing a style saves it for the slot, and a reload restores it', async () => {
  const h = harness();
  const [, two] = h.add(1, 2);
  h.run('__clips.forEach(wireHeaderPrompt)');
  await h.run('loadHeaderOptions()');
  two.headerPromptEl.choose('generic-header');
  assert.equal(savedStyles(h)['2'].headerPrompt, 'generic-header');
  assert.equal(savedStyles(h)['1'], undefined);
  assert.equal(h.posts.length, 0); // choosing sends nothing

  const reloaded = harness({ storage: h.storage });
  const [, again] = reloaded.add(1, 2);
  reloaded.run('__clips.forEach(wireHeaderPrompt)');
  await reloaded.run('loadHeaderOptions()');
  assert.equal(again.headerPromptEl.value, 'generic-header');
});

test('the "Default prompt" placeholder is never saved', () => {
  const h = harness();
  const [clip] = h.add(1);
  h.run('wireHeaderPrompt(__clips[0])');
  clip.headerPromptEl.choose('');
  assert.equal(h.storage.has('riceclipper.slotStyles.v1'), false);
});

test('Generate sends the selected prompt style', async () => {
  const h = harness();
  const [clip] = h.add(1);
  await h.run('loadHeaderOptions()');
  clip.headerPromptEl.value = 'generic-header';
  await h.run('regenerateHeader(__clips[0])');
  assert.equal(h.posts.length, 1);
  assert.equal(h.posts[0].style, 'generic-header');
  assert.equal(clip.headerEl.value, 'A generated header');
});

test('without a style list Generate leaves the choice to the server', async () => {
  const h = harness({ options: null });
  const [clip] = h.add(1);
  await h.run('loadHeaderOptions()');
  assert.deepEqual(values(clip.headerPromptEl), [['', 'Default prompt']]);
  await h.run('regenerateHeader(__clips[0])');
  assert.equal(h.posts[0].style, null);
});

test('an empty style list keeps the "Default prompt" option', async () => {
  const h = harness({ options: { prompts: [], default_prompt: 'generic-header', fonts: [] } });
  const [clip] = h.add(1);
  await h.run('loadHeaderOptions()');
  assert.deepEqual(values(clip.headerPromptEl), [['', 'Default prompt']]);
});

test('loading the list sends no header request', async () => {
  const h = harness();
  h.add(1, 2);
  await h.run('loadHeaderOptions()');
  assert.equal(h.posts.length, 0);
});

test('a clip that moves slot carries its prompt style to the new slot', async () => {
  const h = harness();
  const [, two] = h.add(1, 2);
  await h.run('loadHeaderOptions()');
  two.headerPromptEl.value = 'generic-header';
  h.run('clips.shift(); compactClipOrdinals();');
  assert.equal(two.ord, 1);
  assert.equal(savedStyles(h)['1'].headerPrompt, 'generic-header');
  assert.equal(two.headerPromptEl.attrs['aria-label'], 'Header prompt style for clip 1');
});
