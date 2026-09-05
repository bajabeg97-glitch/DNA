// Bridge 4.61 tests (Phase B): the stdlib node:http bridge serves the UI and
// runs the full Python Session Pass over HTTP, returning the consolidated
// report + downloadable artifacts. Runs with `node --test` (no deps).
import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startBridge } from '../dna_bridge.mjs';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const STYLE_ROLES = '8:bass,9:drums,10:percussion,11:accompaniment,12:accompaniment,13:accompaniment,14:accompaniment,15:accompaniment';

async function withServer(run) {
  const server = await startBridge('127.0.0.1', 0);
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await run(base);
  } finally {
    await new Promise((r) => server.close(r));
  }
}

test('bridge health reports engines and python', async () => {
  await withServer(async (base) => {
    const h = await (await fetch(base + '/api/health')).json();
    assert.equal(h.ok, true);
    assert.equal(h.version, '4.61');
    assert.ok(h.engines.includes('session_pass 4.60'));
  });
});

test('bridge serves the optimizer UI at /', async () => {
  await withServer(async (base) => {
    const res = await fetch(base + '/');
    assert.equal(res.status, 200);
    const html = await res.text();
    assert.ok(html.includes('DNA Optimizer'));
    assert.ok(html.includes('/api/analyze'));
  });
});

test('bridge presets list known corpus samples', async () => {
  await withServer(async (base) => {
    const p = await (await fetch(base + '/api/presets')).json();
    const ids = p.presets.map((x) => x.id);
    for (const want of ['reference-style', 'fixture', 'session35', 'song19-01']) {
      assert.ok(ids.includes(want), `missing preset ${want}`);
    }
  });
});

test('bridge analyze applies safe actions and serves the artifact', async () => {
  await withServer(async (base) => {
    const bytes = fs.readFileSync(path.join(ROOT, 'baseline/reference-style.mid'));
    const res = await fetch(base + '/api/analyze', {
      method: 'POST',
      headers: { 'x-filename': 'reference-style.mid', 'x-roles': STYLE_ROLES, 'x-apply': '1' },
      body: new Uint8Array(bytes),
    });
    assert.equal(res.status, 200);
    const j = await res.json();
    assert.equal(j.ok, true);
    assert.equal(j.pythonCode, 0);
    assert.equal(j.markerCount, 10);
    assert.equal(j.report.schema, 'dna-session-pass');
    const statuses = new Map(j.actionStatuses.map((a) => [a.id, a.status]));
    assert.equal(statuses.get('A01_STY_EXPORT'), 'APPLIED');
    assert.equal(statuses.get('A02_PERCUSSION_CC11_GAIN'), 'APPLIED');
    assert.equal(statuses.get('A05_DEVICE_LOCKED_TRIGGERS'), 'LOCKED');
    assert.ok(j.artifactNames.length >= 2);
    const name = j.artifactNames.find((a) => a.name.endsWith('.mid')).name;
    const dl = await fetch(base + '/api/artifacts/' + encodeURIComponent(name));
    assert.equal(dl.status, 200);
    assert.equal(dl.headers.get('content-type'), 'audio/midi');
    const buf = new Uint8Array(await dl.arrayBuffer());
    assert.ok(buf.length > 1000);
  });
});

test('bridge upload rejects non-midi extension', async () => {
  await withServer(async (base) => {
    const res = await fetch(base + '/api/analyze', {
      method: 'POST',
      headers: { 'x-filename': 'song.txt' },
      body: new Uint8Array([1, 2, 3]),
    });
    assert.equal(res.status, 400);
  });
});

test('bridge applies only user-selected action A01 (STY export)', async () => {
  await withServer(async (base) => {
    const j = await (await fetch(
      base + '/api/sample-analyze?p=reference-style&apply=1&actions=A01_STY_EXPORT'
    )).json();
    assert.equal(j.ok, true);
    const names = j.artifactNames.map((a) => a.name);
    assert.ok(names.some((n) => n.includes('session-sty-')));
    assert.ok(!names.some((n) => n.includes('session-mixed-')));
    const st = new Map(j.actionStatuses.map((a) => [a.id, a.status]));
    assert.equal(st.get('A01_STY_EXPORT'), 'APPLIED');
    assert.equal(st.get('A02_PERCUSSION_CC11_GAIN'), 'READY');
  });
});

test('bridge applies only user-selected action A02 (CC11 gain)', async () => {
  await withServer(async (base) => {
    const j = await (await fetch(
      base + '/api/sample-analyze?p=reference-style&apply=1&actions=A02_PERCUSSION_CC11_GAIN'
    )).json();
    assert.equal(j.ok, true);
    const names = j.artifactNames.map((a) => a.name);
    assert.ok(names.some((n) => n.includes('session-mixed-')));
    assert.ok(!names.some((n) => n.includes('session-sty-')));
    const st = new Map(j.actionStatuses.map((a) => [a.id, a.status]));
    assert.equal(st.get('A01_STY_EXPORT'), 'READY');
    assert.equal(st.get('A02_PERCUSSION_CC11_GAIN'), 'APPLIED');
  });
});
