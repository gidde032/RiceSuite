// Clipper caption style families (RiceSuite #79), run against the real
// clipper/web/app.js: a slot that saved a cut caption style seeds the default
// style and forgets the cut one.
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { scriptedFetch, context, run } = require("./harness");

const SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "..", "clipper", "web", "app.js"),
  "utf8",
);
const SLOT_KEY = "riceclipper.slotStyles.v1";

function boot(saved) {
  const data = new Map([[SLOT_KEY, JSON.stringify(saved)]]);
  const local = {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
  };
  const { fetch } = scriptedFetch({ "GET api/health": { ok: true } });
  const { ctx } = context(fetch, { localStorage: local });
  return { js: run(SOURCE, ctx), slots: () => JSON.parse(data.get(SLOT_KEY)) };
}

for (const cut of ["classic", "clean", "sunset", "mono", "baskerville"]) {
  test(`a slot that saved the cut ${cut} style seeds Montserrat`, () => {
    const { js, slots } = boot({ 1: { caption: cut, header: "black_plate" } });
    assert.equal(js("slotCaptionStyle(1)"), "montserrat");
    assert.deepEqual(slots()["1"], { caption: "montserrat", header: "black_plate" });
  });
}

test("a slot keeps a live saved style, and an unset slot gets Montserrat", () => {
  const { js, slots } = boot({ 1: { caption: "pop_lime" } });
  assert.equal(js("slotCaptionStyle(1)"), "pop_lime");
  assert.equal(js("slotCaptionStyle(2)"), "montserrat");
  assert.deepEqual(slots(), { 1: { caption: "pop_lime" } });
});

test("the clip card seeds its caption style through the slot helper", () => {
  assert.match(SOURCE, /setRadioValue\(clip\.captionStyleEl, slotCaptionStyle\(clip\.ord\)\)/);
});
