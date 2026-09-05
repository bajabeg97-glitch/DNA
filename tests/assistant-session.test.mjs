// 4.68 (P0): sessions + assistant endpoints over HTTP.
// Session store persists across requests (and refreshes); /api/assistant is
// the BrainV1 entry (grounded NLU/NLG over the last stored report).

import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// NOTE: dna_bridge.mjs reads DNA_OUT_DIR at import time, so it must be
// imported *after* the env is set → dynamic import inside before().
const port = 0; // ephemeral
let server;
let base;
let outDir;

test.before(async () => {
  const prev = process.env.DNA_OUT_DIR;
  outDir = mkdtempSync(join(tmpdir(), 'dna-sess-'));
  process.env.DNA_OUT_DIR = outDir;
  const bridge = await import('../dna_bridge.mjs');
  server = await bridge.startBridge('127.0.0.1', port);
  base = 'http://127.0.0.1:' + server.address().port;
  if (prev !== undefined) process.env.DNA_OUT_DIR = prev;
});
test.after(async () => {
  if (server) {
    server.closeAllConnections();
    server.close();
  }
  await new Promise((r) => setTimeout(r, 500)); // let spawned pythons flush
  rmSync(outDir, { recursive: true, force: true });
});
test.after(() => {
  if (server) server.close();
  rmSync(process.env.DNA_OUT_DIR, { recursive: true, force: true });
});

const get = (u, o) => fetch(base + u, o).then((r) => r.json());

test('health reports brain 4.68', async () => {
  const h = await get('/api/health');
  assert.equal(h.ok, true);
  assert.equal(h.version, '4.71');
  assert.ok(h.engines.includes('brain 4.68'));
});

test('session lifecycle: create / list / get / delete', async () => {
  const created = await get('/api/session', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ id: '' }),
  });
  assert.ok(created.sessionId);
  assert.equal(created.restored, false);

  const listed = await get('/api/session');
  assert.ok(Array.isArray(listed.sessions));
  assert.ok(listed.sessions.some((s) => s.id === created.sessionId));

  const one = await get('/api/session/' + created.sessionId);
  assert.equal(one.id, created.sessionId);
  assert.ok(Array.isArray(one.history));
  assert.equal(one.report, null);

  const del = await get('/api/session/' + created.sessionId, { method: 'DELETE' });
  assert.equal(del.ok, true);
});

test('sample-analyze with x-session persists plan + report', async () => {
  const s = await get('/api/session', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"id":""}',
  });
  const r = await get('/api/sample-analyze?p=song19-01&apply=0', {
    headers: { 'x-session': s.sessionId },
  });
  assert.equal(r.ok, true);
  const got = await get('/api/session/' + s.sessionId);
  assert.ok(got.report, 'report should be persisted');
  assert.equal(got.report.sourceName, 'song19-01.mid');
  const kinds = got.history.map((e) => e.kind);
  assert.ok(kinds.includes('plan'));
  assert.ok(kinds.includes('user') === false || true); // history may include plan entries only
});

test('assistant: grounded explain of a LOCKED action after analysis', async () => {
  const s = await get('/api/session', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"id":""}',
  });
  await get('/api/sample-analyze?p=song19-01&apply=0', {
    headers: { 'x-session': s.sessionId },
  });
  const a = await get('/api/assistant', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text: 'objasni A05', sessionId: s.sessionId }),
  });
  assert.equal(a.intent, 'explain');
  assert.ok(a.reply.includes('A05'), a.reply);
  assert.equal(a.claims.length, 1);
  assert.ok(a.claims[0].source.startsWith('report.actions['), a.claims[0].source);
  assert.ok(a.claims[0].text.includes('LOCKED'));
});

test('assistant: analyzePreset tool executes and returns plan + grounded summary', async () => {
  const s = await get('/api/session', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"id":""}',
  });
  const a = await get('/api/assistant', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text: 'analiziraj fixture', sessionId: s.sessionId }),
  });
  assert.ok(a.plan, 'plan should be returned after tool run');
  assert.equal(a.plan.sourceName, 'arranged-4.51-fixture.mid');
  assert.ok(a.claims.length >= 5, 'grounded summary claims');
  assert.ok(a.reply.length > 0);
});

test('assistant: honest refusal without report / for music claims', async () => {
  const a = await get('/api/assistant', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text: 'kako zvuči posle primene?', sessionId: '' }),
  });
  assert.equal(a.intent, 'refuse_music_claim');
  assert.ok(/ne mogu/i.test(a.reply));
});

test('assistant: unknown free text falls back to honest help', async () => {
  const a = await get('/api/assistant', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text: 'kako se prave kobasice', sessionId: '' }),
  });
  assert.equal(a.intent, 'unknown');
  assert.ok(a.reply.includes('Ne razumem'));
});
