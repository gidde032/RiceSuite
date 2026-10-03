const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
function harness(saved = {}) {
  const elements = new Map();
  const storage = new Map(Object.entries(saved));
  const timers = new Map();
  let timer = 0;
  function element(id) {
    if (!elements.has(id)) elements.set(id, { textContent: '', disabled: false,
      classList: { toggle() {}, add() {}, remove() {} }, addEventListener() {}, setAttribute() {} });
    return elements.get(id);
  }
  const store = { getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) };
  const context = vm.createContext({ document: { getElementById: element },
    sessionStorage: store, localStorage: store, window: { sessionStorage: store },
    setInterval() {}, setTimeout(fn) { timers.set(++timer, fn); return timer; },
    clearTimeout(id) { timers.delete(id); }, console,
    fetch: async () => ({ ok: true, json: async () => ({ ffmpeg: true, libass: true }) }) });
  vm.runInContext(source, context);
  const run = (code) => vm.runInContext(code, context);
  const next = async () => { const [id, fn] = timers.entries().next().value; timers.delete(id); await fn(); };
  return { context, element, run, next, timers, storage };
}
const snapshot = (status = 'active') => ({ operation: 'send', operation_id: 'attempt-0001', status,
  stage: 'copying', current: { position: 2, title: '<img onerror=alert(1)>' }, total: 3, completed: 1,
  items: [], batch_id: 'batch_fixture', detail: '', published: false });

test('polls exact read-only operation and stops terminal without writes', async () => {
  const h = harness(); const urls = [];
  h.context.fetch = async (url, options) => { assert.equal(options, undefined); urls.push(url); return { ok: true, json: async () => snapshot(urls.length === 1 ? 'active' : 'complete') }; };
  h.run('watchProgress("send", "attempt-0001")');
  await h.next();
  assert.equal(h.element('progress-count').textContent, '1 / 3 copied');
  assert.match(h.element('progress-current').textContent, /<img onerror=alert\(1\)>/);
  await h.next();
  assert.equal(h.element('progress-count').textContent, '3 / 3 handed off');
  assert.match(h.element('progress-current').textContent, /waiting in Poster's inbox/);
  assert.equal(h.timers.size, 0);
  assert.deepEqual(urls, ['api/progress/send/attempt-0001', 'api/progress/send/attempt-0001']);
});

test('failed progress read freezes last values and never retries mutations', async () => {
  const h = harness(); let reads = 0;
  h.context.fetch = async () => { if (++reads === 1) return { ok: true, json: async () => snapshot() }; throw Error('offline'); };
  h.run('watchProgress("send", "attempt-0001")'); await h.next();
  const detail = h.element('progress-current').textContent;
  await h.next();
  assert.equal(h.element('progress-count').textContent, '1 / 3 copied');
  assert.equal(h.element('progress-current').textContent, detail);
  assert.match(h.element('progress-state').textContent, /result unknown/);
  assert.equal(h.timers.size, 1);
});

test('stale response cannot attach to a newer attempt', async () => {
  const h = harness(); let resolve;
  h.context.fetch = () => new Promise((done) => { resolve = done; });
  h.run('watchProgress("send", "attempt-0001")'); const reading = h.next();
  h.run('watchProgress("pull", "attempt-0002")');
  resolve({ ok: true, json: async () => snapshot('complete') }); await reading;
  assert.equal(h.element('progress-operation').textContent, 'Pull from Searcher');
  assert.equal(h.element('progress-count').textContent, '');
});

test('same-process reload reconnects exact attempt; unavailable is unknown and stops', async () => {
  const h = harness({ 'riceclipper.progress.v1': JSON.stringify({operation: 'send', id: 'attempt-0001'}),
    'riceclipper.progressView.v1': JSON.stringify({operation: 'Send to Poster', state: '↻ Working', count: '1 / 3 copied', detail: 'Now copying clip two'}) });
  assert.equal(h.element('progress-count').textContent, '1 / 3 copied');
  h.context.fetch = async (url, options) => { assert.equal(url, 'api/progress/send/attempt-0001'); assert.equal(options, undefined); return { ok: false, status: 404 }; };
  await h.next();
  assert.match(h.element('progress-current').textContent, /outcome unknown/);
  assert.equal(h.timers.size, 0);
});

test('copy failure and published ambiguity use distinct outcomes', () => {
  const h = harness();
  h.context.data = { ...snapshot('failed'), detail: 'copy broke', items: [ {position:2,title:'Clip two',state:'failed'}, {state:'waiting'} ] };
  h.run('snapshotProgress(data)');
  assert.match(h.element('progress-current').textContent, /Clip 2 — Clip two/);
  assert.match(h.element('progress-current').textContent, /1 not attempted/);
  assert.match(h.element('progress-current').textContent, /not handed off/);
  h.context.data = { ...snapshot('unconfirmed'), published:true, detail:'receipt lost' };
  h.run('snapshotProgress(data)');
  assert.match(h.element('progress-current').textContent, /reached the handoff boundary/);
  assert.match(h.element('progress-state').textContent, /Result unknown/);
});

test('render failure preserves successful output and names the held clip (#59)', async () => {
  const h = harness();
  h.run(`clips.push({ord:1, jobId:'one',status:'ready'}, {ord:2,jobId:'two',status:'ready'});
    renderClip = async (clip) => { if (clip.ord === 1) {clip.status='done'; clip.renderedEdits=0; return true;} clip.error='synthetic render failure'; return false; };
    refreshCacheInfo = async () => {};`);
  h.context.fetch = async () => { throw Error('send must remain held'); };
  await h.run('handleRenderAll()');
  assert.equal(h.run('clips[0].status'), 'done');
  assert.equal(h.element('progress-count').textContent, '1 / 2 rendered');
  assert.match(h.element('progress-current').textContent, /Clip 2 — synthetic render failure/);
  assert.doesNotMatch(h.element('progress-current').textContent, /[Aa]utomatic send/);
});

test('lost send response keeps key; explicit retry confirms without duplicate send key', async () => {
  const h = harness();
  h.run(`clips.push({ord:1,jobId:'one',status:'done',headerEl:{value:'Title'}});
    batchSnapshot = () => 'unchanged'; radioValue = () => 'classic'; collectWords = () => [{text:'text'}];`);
  const payloads = [];
  h.context.fetch = async (url, options) => {
    assert.equal(url, 'api/handoff'); payloads.push(JSON.parse(options.body));
    if (payloads.length === 1) throw Error('response lost');
    return {ok:true,status:200,json:async()=>({batch_id:'batch_once',clip_count:1,replayed:true})};
  };
  await h.run('sendBatch()');
  assert.match(h.element('progress-state').textContent, /result unknown/);
  assert.equal(payloads.length, 1);
  await h.run('sendBatch()');
  assert.equal(payloads[0].send_key, payloads[1].send_key);
  assert.notEqual(payloads[0].observation_id, payloads[1].observation_id);
  assert.equal(h.element('progress-count').textContent, '1 / 1 handed off');
  assert.equal(h.timers.size, 0);
});

test('an expired active attempt becomes unavailable unknown after initial read race', async () => {
  const h = harness(); let reads = 0;
  h.context.fetch = async () => { reads += 1; return {ok:false,status:404}; };
  h.run('watchProgress("send", "attempt-0001")');
  await h.next(); await h.next(); await h.next();
  assert.equal(reads, 3);
  assert.match(h.element('progress-current').textContent, /outcome unknown/);
  assert.equal(h.timers.size, 0);
});

test('a lost transcription response is unknown without automatically repeating transcription', async () => {
  const h = harness(); let writes = 0;
  h.run(`clips.push({ord:1,jobId:'one',status:'queued',name:'First clip',
    lyricsBadgeEl:{textContent:'',classList:{remove(){}},removeAttribute(){}},transcriptEl:{innerHTML:''}});
    setClipStatus = () => {}; refreshCacheInfo = async () => {};`);
  h.context.fetch = async (url, options) => { assert.equal(url, 'api/jobs/one/transcribe'); assert.equal(options.method,'POST'); writes += 1; throw Error('connection lost'); };
  await h.run('processIngestQueue()');
  assert.equal(writes, 1);
  assert.equal(h.element('progress-count').textContent, '0 / 1 ready');
  assert.match(h.element('progress-state').textContent, /result unknown/);
  assert.match(h.element('progress-current').textContent, /work may still be running/);
});

test('edited output and unresolved renders give an honest final summary', async () => {
  for (const unknown of [false, true]) {
    const h = harness();
    h.context.unknown = unknown;
    h.run(`clips.push({ord:1,jobId:'one',status:'ready'});
      renderClip = async (clip) => { clip.status=unknown?'ready':'done'; clip.edits=1; clip.renderedEdits=0;
        clip.error='lost response'; clip.renderUnknown=unknown; return !unknown; };
      refreshCacheInfo = async () => {};`);
    h.context.fetch = async () => { throw Error('send must remain held'); };
    await h.run('handleRenderAll()');
    assert.doesNotMatch(h.element('progress-current').textContent, /[Aa]utomatic send/);
    assert.match(h.element('progress-current').textContent, unknown ? /result unknown/ : /changed after its render/);
  }
});

test('an upstream transcription failure is not reported as a render failure', async () => {
  const h = harness();
  h.run(`clips.push({ord:1, jobId:'one',status:'ready'},
    {ord:2,jobId:'two',status:'error',error:'Transcription decoder failed'});
    renderClip = async (clip) => { clip.status='done'; clip.renderedEdits=0; return true; };
    refreshCacheInfo = async () => {};`);
  h.context.fetch = async () => { throw Error('send must remain held'); };
  await h.run('handleRenderAll()');
  assert.equal(h.element('progress-count').textContent, '1 / 2 rendered');
  assert.match(h.element('progress-current').textContent, /Clip 2.*Transcription decoder failed/);
  assert.doesNotMatch(h.element('progress-current').textContent, /failed to render/);
  assert.doesNotMatch(h.element('progress-current').textContent, /[Aa]utomatic send/);
});

test('a pending keyed retry remains unknown before publication and later replay confirms once', async () => {
  const h = harness();
  h.run(`clips.push({ord:1,jobId:'one',status:'done',headerEl:{value:'Title'}});
    batchSnapshot = () => 'unchanged'; radioValue = () => 'classic'; collectWords = () => [{text:'text'}];`);
  const payloads = [];
  h.context.fetch = async (url, options) => {
    if (!options) return {ok:true,json:async()=>({...snapshot('unconfirmed'),total:0,completed:0,current:null,batch_id:'',published:false,detail:'A send of these clips has not finished.'})};
    assert.equal(url, 'api/handoff'); payloads.push(JSON.parse(options.body));
    if (payloads.length === 1) throw Error('reply lost while copying');
    if (payloads.length === 2) return {ok:false,status:409,json:async()=>({send_in_progress:true,detail:'A send of these clips has not finished.'})};
    return {ok:true,status:200,json:async()=>({batch_id:'batch_once',clip_count:1,replayed:true})};
  };
  await h.run('sendBatch()');
  await h.run('sendBatch()');
  assert.match(h.element('progress-state').textContent, /unknown/);
  await h.next();
  assert.match(h.element('progress-state').textContent, /unknown/);
  assert.doesNotMatch(h.element('progress-current').textContent, /not handed off|reached the handoff boundary/);
  assert.equal(h.timers.size, 0);
  await h.run('sendBatch()');
  assert.equal(payloads.length, 3);
  assert.equal(new Set(payloads.map(p=>p.send_key)).size, 1);
  assert.equal(h.element('progress-count').textContent, '1 / 1 handed off');
});
