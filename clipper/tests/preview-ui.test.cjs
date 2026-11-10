const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { context, run, element } = require('../../tests/js/harness.js');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');

function harness({ saved = null, brokenStorage = false } = {}) {
  const storage = new Map(saved === null ? [] : [['riceclipper.tiktok-preview.v1', saved]]);
  const requests = [];
  const { ctx } = context(async (url) => { requests.push(url); return { ok: true, json: async () => ({}) }; }, {
    setTimeout() {}, localStorage: {
      getItem(k) { if (brokenStorage) throw Error('blocked'); return storage.get(k) ?? null; },
      setItem(k, v) { if (brokenStorage) throw Error('blocked'); storage.set(k, v); },
    },
  });
  const evaluate = run(source, ctx);
  return { ctx, evaluate, storage, requests };
}
function clipFixture() {
  const image = element();
  let src = null;
  image.getAttribute = () => src;
  Object.defineProperty(image, 'src', { set(v) { src = v; }, get() { return src; } });
  image.removeAttribute = () => { src = null; };
  const overlays = [element(), element()];
  const clip = { el: { querySelectorAll: () => overlays }, headerPreviewEl: image,
    headerPreviewWindowEl: element(), sourceVideoEl: element(), headerPreviewBaseEl: element(),
    previewStatusEl: element(), resultEl: element(), ord: 1 };
  clip.sourceVideoEl.videoWidth = 1080; clip.sourceVideoEl.videoHeight = 1920;
  clip.sourceVideoEl.clientWidth = 270; clip.sourceVideoEl.clientHeight = 480;
  clip.sourceVideoEl.clientLeft = clip.sourceVideoEl.clientTop = 0;
  clip.sourceVideoEl.offsetLeft = clip.sourceVideoEl.offsetTop = 0;
  clip.geoState = { width: 1080, height: 1920 };
  return { clip, overlays };
}
function stageFixture(source = true) {
  const button = element();
  const stage = element();
  const listeners = {};
  stage.classList.contains = (name) => source && name === 'source-preview';
  stage.querySelector = () => button;
  stage.addEventListener = (name, fn) => { listeners[name] = fn; };
  button.addEventListener = (name, fn) => { listeners[name] = fn; };
  return { stage, button, listeners };
}

test('overlay is initially off; a remembered true is restored, malformed values remain off', () => {
  for (const [saved, expected] of [[null, false], ['true', true], ['false', false], ['broken', false]]) {
    assert.equal(harness({ saved }).evaluate('tiktokPreviewEnabled'), expected);
  }
});
test('shared toggle updates every preview and new cards without making network requests', () => {
  const h = harness();
  const fixtures = [clipFixture(), clipFixture()];
  h.ctx.items = fixtures.map((f) => f.clip);
  h.evaluate('clips.push(...items); setTikTokPreview(true)');
  for (const f of fixtures) {
    assert.ok(f.overlays.every((el) => !el.hidden));
    assert.equal(f.clip.headerPreviewWindowEl.hidden, false);
  }
  assert.equal(h.storage.get('riceclipper.tiktok-preview.v1'), 'true');
  const fresh = clipFixture(); h.ctx.fresh = fresh.clip;
  h.evaluate('syncTikTokPreview(fresh)');
  assert.ok(fresh.overlays.every((el) => !el.hidden));
  const before = h.requests.length;
  h.evaluate('setTikTokPreview(false)');
  assert.equal(h.requests.length, before);
  assert.ok(fixtures.every((f) => f.overlays.every((el) => el.hidden)));
  assert.ok(fixtures.every((f) => f.clip.headerPreviewWindowEl.hidden));
});
test('storage failure keeps a functional in-session toggle', () => {
  const h = harness({ brokenStorage: true });
  h.evaluate('setTikTokPreview(true)'); assert.equal(h.evaluate('tiktokPreviewEnabled'), true);
  h.evaluate('setTikTokPreview(false)'); assert.equal(h.evaluate('tiktokPreviewEnabled'), false);
});
test('empty or failed header leaves an enabled overlay visible; disabling overlay preserves a header', () => {
  const h = harness(); const { clip } = clipFixture(); h.ctx.clip = clip;
  h.evaluate('setTikTokPreview(true); showHeaderPreview(clip, null)');
  assert.equal(clip.headerPreviewWindowEl.hidden, false);
  assert.equal(clip.headerPreviewEl.hidden, true);
  h.evaluate('showHeaderPreview(clip, "data:image/png;base64,header"); setTikTokPreview(false); syncTikTokPreview(clip)');
  assert.equal(clip.headerPreviewWindowEl.hidden, false);
  assert.equal(clip.headerPreviewEl.hidden, false);
  h.evaluate('showHeaderPreview(clip, null)');
  assert.equal(clip.headerPreviewWindowEl.hidden, true);
});
test('fullscreen requests the composed ancestor and exits without changing playback', async () => {
  const h = harness(); const { clip } = clipFixture(); const { stage, button } = stageFixture();
  clip.previewStages = [stage]; h.ctx.clip = clip; h.ctx.stage = stage;
  let entered = 0, exited = 0;
  stage.requestFullscreen = async () => { entered++; h.ctx.document.fullscreenElement = stage; };
  h.ctx.document.exitFullscreen = async () => { exited++; h.ctx.document.fullscreenElement = null; };
  clip.sourceVideoEl.currentTime = 7.2; clip.sourceVideoEl.muted = false;
  await h.evaluate('togglePreviewFullscreen(clip, stage)');
  assert.equal(entered, 1); assert.equal(button.textContent, 'Exit fullscreen');
  await h.evaluate('togglePreviewFullscreen(clip, stage)');
  assert.equal(exited, 1); assert.equal(button.textContent, 'Fullscreen');
  assert.equal(clip.sourceVideoEl.currentTime, 7.2); assert.equal(clip.sourceVideoEl.muted, false);
});
test('fullscreen rejection and unavailable API are handled; Escape updates the button and restores focus', async () => {
  for (const reject of [false, true]) {
    const h = harness(); const { clip } = clipFixture(); const f = stageFixture();
    clip.el.querySelectorAll = () => [f.stage]; h.ctx.clip = clip; h.ctx.stage = f.stage;
    if (reject) f.stage.requestFullscreen = async () => { throw Error('denied'); };
    else f.stage.requestFullscreen = undefined;
    h.evaluate('setupPreviewStages(clip)');
    await h.evaluate('togglePreviewFullscreen(clip, stage)');
    assert.match(clip.previewStatusEl.textContent, /Fullscreen unavailable/);
    let focused = 0; f.button.focus = () => { focused++; };
    h.ctx.document.fullscreenElement = f.stage; f.listeners.fullscreenchange();
    assert.equal(f.button.textContent, 'Exit fullscreen');
    h.ctx.document.fullscreenElement = null; f.listeners.fullscreenchange();
    assert.equal(f.button.textContent, 'Fullscreen'); assert.equal(focused, 2);
  }
});
test('empty and busy rendered previews cannot enter fullscreen, but can exit if already fullscreen', () => {
  const h = harness(); const { clip } = clipFixture(); const f = stageFixture(false);
  clip.previewStages = [f.stage]; h.ctx.clip = clip;
  for (const state of ['is-empty', 'is-busy', 'is-stale', '']) {
    clip.resultEl.classList.contains = (s) => s === state;
    h.evaluate('syncPreviewFullscreen(clip)');
    assert.equal(f.button.disabled, ['is-empty', 'is-busy'].includes(state));
    h.ctx.document.fullscreenElement = f.stage;
    h.evaluate('syncPreviewFullscreen(clip)'); assert.equal(f.button.disabled, false);
    h.ctx.document.fullscreenElement = null;
  }
});

// --- blur-pad zoom (RiceSuite #98) -------------------------------------------
function radios(value) { const group = element(); group.querySelector = () => ({ value }); return group; }
function zoomClip({ geometry = 'blur_pad', content = 'speech', plan = null, width = 1920, height = 1080, photo = false, value = '150' } = {}) {
  const clip = { geometryEl: radios(geometry), contentEl: radios(content), isPhoto: photo,
    geoState: { width, height, crop_plan: plan, music_plan: null },
    blurPadZoomEl: element(), blurPadZoomInputEl: element(), blurPadZoomOutEl: element() };
  clip.blurPadZoomInputEl.value = value;
  return clip;
}
test('the zoom maximum is the fill-height zoom of a landscape clip, as render.geometry computes it', () => {
  const h = harness();
  for (const [w, hgt, max] of [[1920, 1080, 317], [1440, 1080, 238], [1081, 1080, 178], [1080, 1920, 100], [1080, 1350, 100], [0, 0, 100]]) {
    assert.equal(h.evaluate(`blurPadZoomMax(${w}, ${hgt})`), max);
  }
});
test('the zoom slider shows, and its value is sent, only while a landscape video resolves to blur-pad', () => {
  const h = harness();
  const crop = { decision: 'crop' }; const blur = { decision: 'blur_pad' };
  for (const [opts, shown] of [
    [{ geometry: 'blur_pad' }, true],
    [{ geometry: 'auto', plan: blur }, true],
    [{ geometry: 'auto', plan: crop }, false],
    [{ geometry: 'auto', plan: null }, true],
    [{ geometry: 'crop', plan: crop }, false],
    [{ geometry: 'crop', plan: null }, true],
    [{ geometry: 'blur_pad', photo: true }, false],
    [{ geometry: 'blur_pad', width: 1080, height: 1350 }, false],
    [{ geometry: 'blur_pad', width: 1080, height: 1920 }, false],
  ]) {
    const clip = zoomClip(opts); h.ctx.clip = clip;
    h.evaluate('syncBlurPadZoom(clip)');
    assert.equal(clip.blurPadZoomEl.hidden, !shown, JSON.stringify(opts));
    assert.equal(h.evaluate('blurPadZoom(clip)'), shown ? 150 : 100, JSON.stringify(opts));
    assert.equal(clip.blurPadZoomOutEl.textContent, shown ? '150%' : '100%');
  }
});
test('the zoom slider range follows the clip and a stale value is clamped to it', () => {
  const h = harness();
  const clip = zoomClip({ value: '900' }); h.ctx.clip = clip;
  h.evaluate('syncBlurPadZoom(clip)');
  assert.equal(clip.blurPadZoomInputEl.max, '317');
  assert.equal(h.evaluate('blurPadZoom(clip)'), 317);
  clip.blurPadZoomInputEl.value = 'junk';
  assert.equal(h.evaluate('blurPadZoom(clip)'), 100);
});
test('the header preview request carries the zoom the render will use', () => {
  const h = harness();
  const clip = zoomClip({ value: '220' });
  Object.assign(clip, { headerEl: element(), headerStyleEl: radios('plain'), headerLook: null, emojiToggleEl: null });
  h.ctx.clip = clip;
  assert.equal(h.evaluate('headerPreviewPayload(clip)').blur_pad_zoom, 220);
  clip.geometryEl = radios('crop'); clip.geoState.crop_plan = { decision: 'crop' };
  assert.equal(h.evaluate('headerPreviewPayload(clip)').blur_pad_zoom, 100);
});
test('the blur-pad mock zooms the centred foreground up to the frame height', () => {
  const h = harness();
  const draws = [];
  const canvas = { getContext: () => ({ save() {}, restore() {}, set filter(_v) {}, drawImage: (...args) => draws.push(args.slice(1)) }) };
  h.ctx.canvas = canvas; h.ctx.media = { videoWidth: 1920, videoHeight: 1080 };
  for (const [zoom, fg] of [[100, [0, 54.6875, 90, 50.625]], [200, [-45, 29.375, 180, 101.25]], [1000, [-97.2222, 0, 284.4444, 160]]]) {
    draws.length = 0;
    h.evaluate(`drawBlurPadMock(canvas, media, 90, 160, ${zoom})`);
    assert.equal(draws.length, 2);
    draws[1].forEach((v, i) => assert.ok(Math.abs(v - fg[i]) < 1e-3, `${zoom}: ${draws[1]}`));
  }
});
