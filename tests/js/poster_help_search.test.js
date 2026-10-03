// Poster's Help search (RiceSuite #52), run against the real function in
// poster/frontend/index.html. Typing hides every card whose text does not hold
// each word of the query. A "No matching topics" line is announced when no card
// is left. Clearing the query shows every card again.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

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

const SOURCE = slice("// --- Help search", "// --- Slate client-side navigation");

function boot(texts) {
  const cards = texts.map((textContent) => ({ textContent, hidden: false }));
  const empty = { textContent: "" };
  const document = {
    querySelectorAll: (selector) => (selector === "#view-help .help-card" ? cards : []),
  };
  const el = (id) => {
    assert.equal(id, "helpEmpty");
    return empty;
  };
  const ctx = vm.createContext({ document, el });
  vm.runInContext(SOURCE, ctx);
  return { cards, empty, filter: (q) => vm.runInContext(`filterHelp(${JSON.stringify(q)})`, ctx) };
}

const TEXTS = [
  "Restore last batch Brings back the drafts",
  "Platform toggles Disabled leaves a platform out",
  "Result meanings Confirmed Skipped Disabled",
];

test("a query shows only the cards that hold every word, in any case", () => {
  const { cards, empty, filter } = boot(TEXTS);
  filter("  DISABLED platform ");
  assert.deepEqual(cards.map((c) => c.hidden), [true, false, true]);
  assert.equal(empty.textContent, "");
});

test("a query with no match hides every card and shows the empty line", () => {
  const { cards, empty, filter } = boot(TEXTS);
  filter("zebra");
  assert.deepEqual(cards.map((c) => c.hidden), [true, true, true]);
  assert.equal(empty.textContent, "No matching topics.");
});

test("clearing the query shows every card and hides the empty line", () => {
  const { cards, empty, filter } = boot(TEXTS);
  filter("zebra");
  filter("   ");
  assert.deepEqual(cards.map((c) => c.hidden), [false, false, false]);
  assert.equal(empty.textContent, "");
});

test("the search box calls the filter and the empty line exists", () => {
  assert.match(HTML, /aria-label="Search help topics"[^>]*oninput="filterHelp\(this\.value\)"/);
  // A live region announces a change to its text, so it stays in the page.
  assert.match(HTML, /<p class="help-empty" id="helpEmpty" aria-live="polite"><\/p>/);
  assert.match(HTML, /\.help-card\[hidden\]\s*\{\s*display:\s*none;/);
});

// Review findings on PR #53: the Help copy must describe the human gates and
// the account model as the code does.
test("the Help copy states when Post All and Schedule reach real accounts", () => {
  const help = slice('<section class="view" role="region" id="view-help"', "</section>");
  assert.doesNotMatch(help, /act on real accounts immediately/);
  assert.match(help, /Post All posts to real accounts at once\./);
  assert.match(help, /Confirm Schedule queues a batch that posts unattended at the chosen time\./);
  assert.doesNotMatch(help, /\bslot\b/i);
  assert.match(help, /A platform with no saved session cannot be switched off\./);
  assert.match(help, /If no account takes its place, Review asks before it discards the draft\./);
});
