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
  return { run: (code) => vm.runInContext(code, context), storage };
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
