// DNA Studio GUI tests (4.71): the redesigned UI is served from
// dna_studio_ui.html and the bridge exposes composer/corpus/model/workspace
// endpoints that power the GUI flows. Runs with `node --test` (no deps).
import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// bridge reads DNA_OUT_DIR/DNA_WORK_DIR at import time -> env + dynamic import
let startBridge;
let workDir;
test.before(async () => {
  const td = fs.mkdtempSync(path.join(os.tmpdir(), 'dna-gui471-'));
  workDir = path.join(td, 'work');
  process.env.DNA_OUT_DIR = path.join(td, 'out');
  process.env.DNA_WORK_DIR = workDir;
  const mod = await import('../dna_bridge.mjs');
  startBridge = mod.startBridge;
});
test.after(() => {
  try { fs.rmSync(path.dirname(workDir), { recursive: true, force: true }); } catch { /* ok */ }
});

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

async function withServer(run) {
  const server = await startBridge('127.0.0.1', 0);
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await run(base);
  } finally {
    await new Promise((r) => server.close(r));
  }
}
const post = (base, p, body) => fetch(base + p, { method: 'POST',
  headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });

test('GUI 4.71: health reports studio engines and work dir', async () => {
  await withServer(async (base) => {
    const h = await (await fetch(base + '/api/health')).json();
    assert.equal(h.ok, true);
    assert.equal(h.version, '4.71');
    assert.ok(h.engines.includes('composer 4.70'));
    assert.ok(h.engines.includes('corpus 4.69'));
    assert.ok(h.engines.includes('gui 4.71'));
    assert.equal(h.modelReady, false);
    // workDir points at the workspace created for this test run
    assert.ok(h.workDir.includes('dna-gui471'), h.workDir);
    assert.ok(h.workDir.endsWith('work'), h.workDir);
    assert.ok(fs.existsSync(h.workDir), h.workDir);
  });
});

test('GUI 4.71: UI file is served with studio views and endpoints', async () => {
  assert.ok(fs.existsSync(path.join(ROOT, 'dna_studio_ui.html')), 'UI file present');
  const ui = fs.readFileSync(path.join(ROOT, 'dna_studio_ui.html'), 'utf8');
  for (const marker of ['v-compose', 'v-model', 'v-corpus', 'v-files', 'v-rules',
                        '/api/compose', '/api/model/status', '/api/model/train',
                        '/api/corpus/build', '/api/ws/list', '/api/ws/file',
                        'Kompozitor (4.70)', 'Model trening (S3)']) {
    assert.ok(ui.includes(marker), `ui marker missing: ${marker}`);
  }
  await withServer(async (base) => {
    const res = await fetch(base + '/');
    assert.equal(res.status, 200);
    const html = await res.text();
    assert.ok(html.includes('DNA Optimizer'));
    assert.ok(html.includes('/api/analyze'));
    assert.ok(html.includes('v-model'));
  });
});

test('GUI 4.71: compose info lists the ten composer styles', async () => {
  await withServer(async (base) => {
    const j = await (await fetch(base + '/api/compose/info')).json();
    const ids = j.styles.map((s) => s.id);
    for (const want of ['rock', 'funk', 'pop', 'ballad', 'folk', 'ballad12',
                        'waltz', 'latin12', 'disco', 'dance90']) {
      assert.ok(ids.includes(want), `missing style ${want}`);
    }
    const rock = j.styles.find((s) => s.id === 'rock');
    assert.equal(rock.bpm, 118);
    assert.equal(rock.sig, '4/4');
  });
});

test('GUI 4.71: compose one song -> files in workspace, downloadable', async () => {
  await withServer(async (base) => {
    const r = await (await post(base, '/api/compose',
      { styles: ['rock'], seeds: [7], humanize: true })).json();
    assert.equal(r.ok, true);
    assert.equal(r.summary.songs, 1);
    assert.equal(r.summary.valid, 1);
    assert.equal(r.summary.totalBars, 26);
    assert.equal(r.summary.failures.length, 0);
    const mid = r.files.find((f) => f.name === 'song-rock-s7.mid');
    assert.ok(mid, 'mid file listed');
    const card = r.files.find((f) => f.name === 'song-rock-s7.scorecard.json');
    assert.ok(card, 'scorecard listed');
    // download via ws/file
    const dl = await fetch(base + '/api/ws/file?f=' + encodeURIComponent(mid.rel));
    assert.equal(dl.status, 200);
    assert.equal(dl.headers.get('content-type'), 'audio/midi');
    const buf = new Uint8Array(await dl.arrayBuffer());
    assert.ok(buf.length > 1000);
    // recent runs + workspace listing know the files
    const runs = await (await fetch(base + '/api/compose/list')).json();
    assert.equal(runs.runs.length, 1);
    assert.equal(runs.runs[0].valid, 1);
    const list = await (await fetch(base + '/api/ws/list')).json();
    assert.ok(list.work.some((f) => f.rel === mid.rel));
  });
});

test('GUI 4.71: compose rejects unknown styles and bad seeds', async () => {
  await withServer(async (base) => {
    const bad = await (await post(base, '/api/compose', { styles: ['nosuch'], seeds: [1] })).json();
    assert.equal(bad.ok, false);
    assert.match(bad.error, /nema poznatih stilova/);
    const badSeed = await (await post(base, '/api/compose', { styles: ['rock'], seeds: ['x'] })).json();
    assert.equal(badSeed.ok, false);
    assert.match(badSeed.error, /nema validnih semena/);
  });
});

test('GUI 4.71: corpus status reads committed S1 corpus evidence', async () => {
  await withServer(async (base) => {
    const j = await (await fetch(base + '/api/corpus/status')).json();
    assert.equal(j.committed.items, 779);
    assert.equal(j.committed.tokens, 843545);
    assert.equal(j.committed.validatorTotal, 779);
    assert.equal(j.committed.validatorOk, true);
  });
});

test('GUI 4.71: model status is honest (S3 not yet installed)', async () => {
  await withServer(async (base) => {
    const s = await (await fetch(base + '/api/model/status')).json();
    assert.equal(s.ready, false);
    assert.equal(s.version, '4.71');
    assert.ok(s.module.includes('model_train.py'));
    assert.equal(s.corpus.items, 779);
    const r = await (await post(base, '/api/model/train', {
      layer: 'stat', order: 3, smoothing: 0.01, context: 256, epochs: 3,
      seed: 471, threads: 4, device: 'cpu', corpusSource: 'committed', limit: 0, outDir: '',
    })).json();
    assert.equal(r.status, 'not_ready');
    assert.equal(r.ok, false);
    assert.equal(r.echo.layer, 'stat');
    assert.equal(r.echo.seed, 471);
    assert.deepEqual(r.errors, []);
  });
});

test('GUI 4.71: model train validates options server-side', async () => {
  await withServer(async (base) => {
    const r = await (await post(base, '/api/model/train', {
      layer: 'micro', order: 99, smoothing: 5, context: 5, epochs: 0,
      seed: 0, threads: 64, device: 'cuda', corpusSource: 'workspace', limit: -3, outDir: '../x',
    })).json();
    assert.equal(r.status, 'not_ready');
    assert.equal(r.echo.layer, 'micro');
    assert.equal(r.echo.order, 6);       // clamped
    assert.equal(r.echo.smoothing, 1);   // clamped
    assert.equal(r.echo.context, 8);     // clamped to min
    assert.equal(r.echo.threads, 32);    // clamped
    assert.equal(r.echo.device, 'cpu');  // cuda refused -> cpu
    assert.ok(r.errors.some((e) => /samo CPU/.test(e)));
    assert.ok(r.errors.length >= 4);
  });
});
