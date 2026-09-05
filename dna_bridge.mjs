#!/usr/bin/env node
// DNA Optimizer bridge 4.71 — DNA Studio GUI (analiza + kompozitor + model
// learning opcije + korpus + fajlovi) over Session Pass / Composer engines.
// From 4.61 (Phase B): stdlib node:http, no npm deps. UI lives in
// dna_studio_ui.html (4.71) and is read at import time.
//
// One node:http server (stdlib only) that
//   * serves the DNA Studio UI at /            (dna_studio_ui.html)
//   * POST /api/analyze  — upload a .mid/.midi/.kar, run the Session Pass
//     (python3.11 dna_midi_studio/session_pass.py) with the whole engine set,
//     return the consolidated report + downloadable artifacts
//   * GET /api/sample-analyze?p=<preset>&apply=1 — demo samples
//   * GET /api/artifacts/<name> — download Session Pass artifacts
//   * GET /api/presets, GET /api/health
//   * 4.71 (DNA Studio):
//     GET  /api/compose/info  — styles from composer_engine.py (cached)
//     GET  /api/compose/list  — recent song runs in the workspace
//     POST /api/compose       — compose songs (composer_engine.py 4.70)
//     GET  /api/corpus/status — committed corpus stats (artifacts-max-4.69)
//     POST /api/corpus/build  — rebuild corpus into the workspace
//     GET  /api/model/status  — model layer readiness (S3)
//     POST /api/model/train   — training options; S3 dispatcher (4.71)
//     GET  /api/ws/list, GET /api/ws/file — workspace browsing/downloads
//
// Invariants: the uploaded source file is only ever copied into
// artifacts-max-4.61/uploads/ and never modified; every mutation happens in
// NEW artifacts by the gated engines; the bridge itself never edits bytes.
// Generated GUI outputs (songs, corpora, checkpoints) go to workspace-4.71/
// (gitignored) unless DNA_WORK_DIR is set.
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const BRIDGE_VERSION = '4.71';
const OUT_DIR = path.join(ROOT, process.env.DNA_OUT_DIR || 'artifacts-max-4.61');
const WORK_DIR = path.join(ROOT, process.env.DNA_WORK_DIR || 'workspace-4.71');
const PY = process.env.PYTHON || 'python3.11';
const MAX_BYTES = 50 * 1024 * 1024;
const PY_TIMEOUT_MS = 240_000;

const UI_FILE = path.join(ROOT, 'dna_studio_ui.html');
let INDEX_HTML = null;
try {
  INDEX_HTML = fs.readFileSync(UI_FILE, 'utf8');
} catch {
  INDEX_HTML = '<!doctype html><meta charset="utf-8"><title>DNA Optimizer</title>' +
    '<body style="background:#0a0e14;color:#dbe4ee;font:14px sans-serif;padding:30px">' +
    '<h2>DNA Optimizer</h2><p>UI fajl <code>dna_studio_ui.html</code> nije nađen pored bridge-a. ' +
    'API radi i dalje (npr. <code>/api/health</code>, <code>/api/analyze</code>).</p></body>';
}


const STYLE_ROLES = '8:bass,9:drums,10:percussion,11:accompaniment,12:accompaniment,13:accompaniment,14:accompaniment,15:accompaniment';
const SONG_ROLES = '0:accompaniment,1:bass,2:solo,9:drums';

const PRESETS = {
  'reference-style': { file: 'baseline/reference-style.mid', roles: STYLE_ROLES,
                       label: 'reference-style.mid (Pa800 style, 10 markera)' },
  'fixture': { file: 'artifacts-max-4.51/arranged-4.51-fixture.mid', roles: STYLE_ROLES,
               label: 'arranged-4.51-fixture.mid (aranžiran stil)' },
  'session35': { file: 'session35-partial-preview.mid', roles: STYLE_ROLES,
                 label: 'session35-partial-preview.mid (stil preview)' },
  'song19-01': { file: 'session19-benchmark/song19-01.mid', roles: SONG_ROLES, melody: '2',
                 label: 'song19-01.mid (solo corpus, ch2 Lead Solo)' },
};

function json(res, code, payload) {
  const body = JSON.stringify(payload, null, 1);
  res.writeHead(code, { 'content-type': 'application/json; charset=utf-8',
                        'content-length': Buffer.byteLength(body) });
  res.end(body);
}

async function readJsonBody(req, maxBytes) {
  const chunks = [];
  let size = 0;
  for await (const c of req) {
    size += c.length;
    if (size > maxBytes) throw new Error('body too large');
    chunks.push(c);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function runPython(args, timeoutMs = PY_TIMEOUT_MS, input = null) {
  return new Promise((resolve) => {
    const child = spawn(PY, args, { cwd: ROOT });
    let out = '';
    let err = '';
    let done = false;
    const timer = setTimeout(() => { if (!done) child.kill('SIGKILL'); }, timeoutMs);
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { err += d; });
    if (input != null) child.stdin.end(input);
    child.on('error', (e) => { done = true; clearTimeout(timer); resolve({ code: -1, error: String(e), out, err }); });
    child.on('close', (code) => { done = true; clearTimeout(timer); resolve({ code, out, err }); });
  });
}

function sanitize(name) {
  return path.basename(String(name || 'upload.mid').replace(/\\/g, '/')).slice(0, 120);
}

function stemOf(name) {
  return name.replace(/\.[^.]+$/, '');
}

async function analyze(absFile, { roles = '', melody = '', apply = false, actions = '' }) {
  const stem = stemOf(path.basename(absFile));
  const args = ['dna_midi_studio/session_pass.py', '--input', absFile, '--out-dir', OUT_DIR];
  if (roles) args.push('--roles', roles);
  if (melody) args.push('--melody-channels', melody);
  if (apply) args.push('--apply-safe');
  if (actions) args.push('--apply-actions', actions);
  const startedMs = Date.now();
  const py = await runPython(args);
  const reportPath = path.join(OUT_DIR, `session-pass-${stem}.json`);
  let report = null;
  if (fs.existsSync(reportPath)) {
    try { report = JSON.parse(fs.readFileSync(reportPath, 'utf8')); } catch { report = null; }
  }
  const artifacts = [];
  if (report) {
    for (const a of report.actions || []) {
      if (a.artifact && a.status === 'APPLIED') artifacts.push({ name: path.basename(a.artifact) });
    }
  }
  return {
    ok: py.code === 0 && !!report,
    pythonCode: py.code,
    pythonErrorTail: (py.err || '').split('\n').slice(-4).join('\n').slice(0, 800),
    sourceName: path.basename(absFile),
    stem,
    rolesUsed: report?.roleMapUsed || null,
    markerCount: report?.fileFacts?.markerCount ?? null,
    actionStatuses: (report?.actions || []).map((a) => ({ id: a.id, status: a.status })),
    artifactNames: artifacts,
    report,
    durationMs: Date.now() - startedMs,
  };
}

function contentType(name) {
  if (/\.(mid|midi)$/i.test(name)) return 'audio/midi';
  if (/\.json$/i.test(name)) return 'application/json; charset=utf-8';
  return 'application/octet-stream';
}

// (4.71) UI se servira iz dna_studio_ui.html — videti INDEX_HTML iznad.


// ============================ 4.68 sessions + assistant ============================
const SESSION_DIR = path.join(OUT_DIR, 'sessions');
const sessionMeta = (sid) => path.join(SESSION_DIR, sid + '.meta.json');
const sessionLog = (sid) => path.join(SESSION_DIR, sid + '.jsonl');
const sessionReportFile = (sid) => path.join(SESSION_DIR, sid + '.report.json');
function ensureSessionDir() { fs.mkdirSync(SESSION_DIR, { recursive: true }); }
function sanitizeSid(v) { return String(v || '').replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 64); }
function ensureSession(id) {
  const sid = sanitizeSid(id) || (randomUUID().replace(/-/g, '').slice(0, 12));
  ensureSessionDir();
  const mf = sessionMeta(sid);
  if (!fs.existsSync(mf)) {
    fs.writeFileSync(mf, JSON.stringify({ id: sid, created: Date.now(), updated: Date.now(), msgs: 0 }));
    fs.writeFileSync(sessionLog(sid), '', 'utf8');
  }
  return sid;
}
function sessionTouch(sid) {
  const mf = sessionMeta(sid);
  try {
    const m = JSON.parse(fs.readFileSync(mf, 'utf8'));
    m.updated = Date.now();
    m.msgs = sessionHistory(sid).length;
    fs.writeFileSync(mf, JSON.stringify(m));
  } catch { /* ignore */ }
}
function sessionAppend(sid, entry) {
  const sid2 = ensureSession(sid);
  const log = sessionLog(sid2);
  const lines = fs.existsSync(log) ? fs.readFileSync(log, 'utf8') : '';
  const entries = lines ? lines.trim().split('\n').map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean) : [];
  entries.push(Object.assign({ t: Date.now() }, entry));
  const capped = entries.slice(-400);
  fs.writeFileSync(log, capped.map((e) => JSON.stringify(e)).join('\n') + '\n', 'utf8');
  sessionTouch(sid2);
  return sid2;
}
function sessionHistory(sid) {
  const log = sessionLog(sid);
  if (!fs.existsSync(log)) return [];
  return String(fs.readFileSync(log, 'utf8')).trim().split('\n')
    .map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
}
function sessionSaveReport(sid, report) {
  const sid2 = ensureSession(sid);
  fs.writeFileSync(sessionReportFile(sid2), JSON.stringify(report), 'utf8');
  sessionAppend(sid2, { role: 'assistant', kind: 'plan', text: 'Plan za ' + (report.sourceName || 'fajl'), sourceName: report.sourceName || null });
  return sid2;
}
function sessionLastReport(sid) {
  const f = sessionReportFile(sid);
  if (!fs.existsSync(f)) return null;
  try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return null; }
}
function sessionList() {
  ensureSessionDir();
  return fs.readdirSync(SESSION_DIR).filter((n) => n.endsWith('.meta.json')).map((n) => {
    try {
      const m = JSON.parse(fs.readFileSync(path.join(SESSION_DIR, n), 'utf8'));
      return { id: m.id, created: m.created, updated: m.updated, msgCount: m.msgs || 0, hasReport: fs.existsSync(sessionReportFile(m.id)) };
    } catch { return null; }
  }).filter(Boolean).sort((a, b) => b.updated - a.updated);
}
async function brainOnce(text, report, historyTail) {
  const payload = JSON.stringify({ text: String(text || ''), report: report || null, history: historyTail || [] });
  let py = await runPython(['dna_midi_studio/assistant_brain.py'], 30000, payload);
  // retry once: transient python/import hiccups must not surface as a user error
  if (py.code !== 0) {
    py = await runPython(['dna_midi_studio/assistant_brain.py'], 30000, payload);
  }
  if (py.code !== 0) {
    const tail = (py.err || '').split('\n').slice(-6).join('\n').slice(0, 500);
    console.error('[brain] python failed (code ' + py.code + '):\n' + tail);
    return { intent: 'error', reply: 'Asistent trenutno nije dostupan (python greška).', claims: [], tool: null, _err: tail };
  }
  try { return JSON.parse(py.out); } catch {
    console.error('[brain] bad JSON out:', (py.out || '').slice(0, 300));
    return { intent: 'error', reply: 'Asistent je vratio neispravan odgovor.', claims: [], tool: null };
  }
}
async function assistantTurn(text, sid) {
  const sid2 = ensureSession(sid);
  sessionAppend(sid2, { role: 'user', kind: 'text', text: String(text || '') });
  const hist = sessionHistory(sid2);
  let report = sessionLastReport(sid2);
  const brain = await brainOnce(text, report, hist);
  if (brain.intent === 'error') { sessionAppend(sid2, { role: 'assistant', kind: 'text', text: brain.reply }); return { sessionId: sid2, intent: brain.intent, reply: brain.reply, claims: [], error: brain._err }; }
  if (brain.reply === '__NEW_SESSION__') {
    const fresh = ensureSession(null);
    sessionAppend(fresh, { role: 'assistant', kind: 'text', text: 'Nova sesija je spremna. Priloži fajl ili reci „analiziraj reference-style”.' });
    return { sessionId: fresh, intent: 'session_new', reply: 'Nova sesija je spremna. Priloži fajl ili reci „analiziraj reference-style”.', claims: [], newSession: fresh };
  }
  if (brain.tool && brain.tool.type === 'analyzePreset') {
    const pr = PRESETS[brain.tool.presetId];
    if (pr) {
      const abs = path.join(ROOT, pr.file);
      const r = await analyze(abs, { roles: pr.roles, melody: pr.melody || '', apply: false, actions: '' });
      if (r.ok && r.report) { report = r.report; sessionSaveReport(sid2, report); }
      const final = await brainOnce('ukratko sažmi analizu', report, sessionHistory(sid2));
      sessionAppend(sid2, { role: 'assistant', kind: 'text', text: final.reply, claims: final.claims || [] });
      return { sessionId: sid2, intent: final.intent || 'summarize', reply: final.reply, claims: final.claims || [], plan: report };
    }
  }
  if (brain.tool && brain.tool.type === 'composeSong') {
    // 4.71: „napravi rock pesmu” — brain je prepoznao stil (+ opc. seed);
    // kompozitor 4.70 pravi .mid + scorecard u workspace-4.71.
    const style = String(brain.tool.style || '');
    const info = await composeInfo();
    const known = (info && Array.isArray(info)) ? info.map((s) => s.id) : [];
    const fallback = ['rock', 'funk', 'pop', 'ballad', 'folk', 'ballad12', 'waltz', 'latin12', 'disco', 'dance90'];
    const allowed = known.length ? known : fallback;
    if (!allowed.includes(style)) {
      const no = { intent: 'compose_request', sessionId: sid2,
        reply: 'Ne poznajem stil „' + style + '”. Stilovi: rock, funk, pop, ballad, folk, waltz, disco, dance90, ballad12, latin12. Npr. „napravi rock pesmu”.', claims: [] };
      sessionAppend(sid2, { role: 'assistant', kind: 'text', text: no.reply });
      return no;
    }
    let seed = parseInt(brain.tool.seed, 10);
    if (!Number.isInteger(seed) || seed < 1 || seed > 9999) {
      // deterministički seed po sesiji (ista sesija -> ista pesma)
      let h = 0;
      for (const ch of String(sid2)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
      seed = (h % 9973) + 1;
    }
    const r = await composeSongs({ styles: [style], seeds: [seed], humanize: true });
    if (!r.ok || !r.summary || !r.summary.valid) {
      const bad = { intent: 'error', sessionId: sid2,
        reply: 'Komponovanje nije uspelo: ' + (r.error || r.pythonErrorTail || 'nepoznata greška'), claims: [], error: r.pythonErrorTail || r.error };
      sessionAppend(sid2, { role: 'assistant', kind: 'text', text: bad.reply });
      return bad;
    }
    const fname = 'song-' + style + '-s' + seed + '.mid';
    const replyText = 'Evo! 🎵 Komponovao sam „' + style + '” (seed ' + seed +
      '): ' + r.summary.totalBars + ' taktova, humanize on (σ ≈ 27.96 ms), validacija prošla. ' +
      'Fajl: workspace-4.71/' + r.outRel + '/' + fname + ' — preuzmi ispod ili u kartici Fajlovi. ' +
      'Drugi seed = druga pesma; isti seed = ista pesma (npr. „napravi ' + style +
      ' pesmu sa seed 42”).';
    const claims = [{ text: 'Generisana pesma: ' + style + ', seed ' + seed + ' — ' +
                      r.summary.totalBars + ' taktova, valid ' + r.summary.valid +
                      ', bubanj iz evidencije vendor-max-4.64/dmp-midi (MIT).',
                      source: 'workspace-4.71/' + r.outRel + '/song-' + style + '-s' + seed + '.scorecard.json' }];
    sessionAppend(sid2, { role: 'assistant', kind: 'compose', text: replyText,
                          sourceName: fname, claims });
    return { sessionId: sid2, intent: 'composed', reply: replyText, claims,
             composed: { ok: true, summary: r.summary, outRel: r.outRel,
                         filesHtml: filesHtmlFor(r.files, r.outRel), files: r.files } };
  }
  if (brain.tool && brain.tool.type === 'applyActions' && report) {
    const srcAbs = metaSourceOf(sid2, report);
    if (srcAbs && fs.existsSync(srcAbs)) {
      const actions = (brain.tool.actions || []).map((a) => String(a).toUpperCase()).join(',');
      const r = await analyze(srcAbs, { roles: '', melody: '', apply: true, actions });
      if (r.ok && r.report) { report = r.report; sessionSaveReport(sid2, report); }
      const final = await brainOnce('ukratko sažmi', report, sessionHistory(sid2));
      sessionAppend(sid2, { role: 'assistant', kind: 'text', text: final.reply, claims: final.claims || [] });
      return { sessionId: sid2, intent: 'applied', reply: final.reply, claims: final.claims || [], applied: r };
    }
    const no = { intent: 'apply', sessionId: sid2, reply: 'Nemam izvor fajla za primenu u ovoj sesiji (upload fajlovi se ne pamte). Prvo uradi analizu preko UI-ja pa onda reci „primeni sve”.', claims: [] };
    sessionAppend(sid2, { role: 'assistant', kind: 'text', text: no.reply });
    return no;
  }
  sessionAppend(sid2, { role: 'assistant', kind: 'text', text: brain.reply || '', claims: brain.claims || [] });
  return { sessionId: sid2, intent: brain.intent, reply: brain.reply || '', claims: brain.claims || [] };
}
function metaSourceOf(sid, report) {
  // presets: map back by sourceName
  const name = (report && report.sourceName) || '';
  for (const id of Object.keys(PRESETS)) {
    if (PRESETS[id].file.endsWith(name)) return path.join(ROOT, PRESETS[id].file);
  }
  return null;
}

// ================= 4.71 DNA Studio: composer / corpus / model / workspace =================
const MODEL_MODULE = path.join(ROOT, 'dna_midi_studio', 'model_train.py');
const COMMITTED_CORPUS_STATS = path.join(ROOT, 'artifacts-max-4.69', 'corpus-4.69', 'corpus-stats-4.69.json');

function workRel(v) {
  const s = String(v == null ? '' : v).trim().replace(/\\/g, '/');
  if (!s || s.startsWith('/') || /^[a-zA-Z]:/.test(s)) return null;
  if (s.split('/').some((part) => part === '..' || part === '')) return null;
  return s;
}
function workAbs(rel) {
  return path.join(WORK_DIR, rel);
}
function modelReady() {
  return fs.existsSync(MODEL_MODULE);
}

const COMPOSE_INFO_TTL = 60_000;
let composeInfoCache = { at: 0, data: null };
async function composeInfo() {
  const now = Date.now();
  if (composeInfoCache.data && now - composeInfoCache.at < COMPOSE_INFO_TTL) return composeInfoCache.data;
  const py = await runPython(['-c',
    'import json,sys; sys.path.insert(0,"dna_midi_studio"); ' +
    'from composer_engine import STYLES; ' +
    'print(json.dumps([{"id": k, "bpm": v["bpm"], "sig": "%d/%d" % tuple(v["sig"])} ' +
    'for k, v in STYLES.items()]))'], 30_000);
  if (py.code === 0) {
    try {
      composeInfoCache = { at: now, data: JSON.parse(py.out) };
      return composeInfoCache.data;
    } catch { /* fallthrough */ }
  }
  return null;
}

function scanSongRuns() {
  // composer engine writes <out>/songs-4.70/songs-4.70-summary.json
  // -> either workspace-4.71/songs-4.70/... (GUI default) or
  //    workspace-4.71/<run>/songs-4.70/... (nested user runs)
  const runs = [];
  if (!fs.existsSync(WORK_DIR)) return runs;
  const trySum = (songsDir, label) => {
    const sumFile = path.join(songsDir, 'songs-4.70-summary.json');
    if (!fs.existsSync(sumFile)) return false;
    try {
      const s = JSON.parse(fs.readFileSync(sumFile, 'utf8'));
      runs.push({
        dir: label,
        mtime: fs.statSync(sumFile).mtimeMs,
        songs: s.songs, valid: s.valid, failures: (s.failures || []).length,
        totalBars: s.totalBars, patternChoices: s.patternChoices,
        humanization: s.humanization || '?', styles: (s.styles || []).length, seeds: (s.seeds || []).length,
      });
      return true;
    } catch { return false; }
  };
  for (const dirName of fs.readdirSync(WORK_DIR)) {
    const abs = path.join(WORK_DIR, dirName);
    let st;
    try { st = fs.statSync(abs); } catch { continue; }
    if (!st.isDirectory()) continue;
    if (dirName === 'songs-4.70') trySum(abs, dirName);
    else trySum(path.join(abs, 'songs-4.70'), dirName + '/songs-4.70');
  }
  return runs.sort((a, b) => b.mtime - a.mtime);
}

function corpusStatsOf(statsFile) {
  try {
    const s = JSON.parse(fs.readFileSync(statsFile, 'utf8'));
    return {
      items: s.items, bars: s.bars, notes: s.notes, tokens: s.tokens,
      moments: s.moments, arrangements: s.arrangements,
      vocabSize: s.vocab && s.vocab.size, vocabCap: s.vocab && s.vocab.cap,
      perOrigin: s.perOrigin || {},
      validatorOk: !!(s.validator && s.validator.ok), validatorTotal: (s.validator && s.validator.total) || 0,
    };
  } catch { return null; }
}

function walkDir(dir, prefix, out) {
  for (const name of fs.readdirSync(dir)) {
    const abs = path.join(dir, name);
    let st;
    try { st = fs.statSync(abs); } catch { continue; }
    if (st.isDirectory()) walkDir(abs, prefix + name + '/', out);
    else out.push({ rel: prefix + name, name, bytes: st.size, mtime: st.mtimeMs });
  }
}

async function composeSongs(body) {
  const info = await composeInfo();
  const known = info && Array.isArray(info) ? info.map((s) => s.id) : [];
  const fallback = ['rock', 'funk', 'pop', 'ballad', 'folk', 'ballad12', 'waltz', 'latin12', 'disco', 'dance90'];
  const allowed = known.length ? known : fallback;
  const styles = [...new Set((body.styles || []).map((s) => String(s)))].filter((s) => allowed.includes(s));
  const seeds = [...new Set((body.seeds || []).map((n) => parseInt(n, 10)).filter((n) => Number.isInteger(n) && n >= 1 && n <= 9999))].sort((a, b) => a - b);
  const rel = workRel(body.outDir || '') || '';
  if (!styles.length) return { ok: false, error: 'nema poznatih stilova u zahtevu' };
  if (!seeds.length) return { ok: false, error: 'nema validnih semena u zahtevu' };
  if (styles.length * seeds.length > 120) return { ok: false, error: 'previše kombinacija (max 120)' };
  const abs = workAbs(rel);
  fs.mkdirSync(abs, { recursive: true });
  const humanize = body.humanize !== false;
  const args = ['-m', 'dna_midi_studio.composer_engine', '--styles', styles.join(','),
    '--seeds', seeds.join(','), '--out', abs];
  if (!humanize) args.push('--no-humanize');
  const py = await runPython(args, 300_000);
  // engine prints the summary json on stdout
  let summary = null;
  const lines = (py.out || '').trim().split('\n');
  for (let i = lines.length - 1; i >= 0; i--) {
    try { summary = JSON.parse(lines.slice(i).join('\n')); break; } catch { /* keep scanning */ }
  }
  const songsDir = path.join(abs, 'songs-4.70');
  let files = [];
  if (fs.existsSync(songsDir)) {
    files = fs.readdirSync(songsDir).sort().map((n) => {
      const full = path.join(songsDir, n);
      const st = fs.statSync(full);
      return { name: n, bytes: st.size, rel: path.relative(WORK_DIR, full).split(path.sep).join('/') };
    });
  }
  const ok = py.code === 0 && !!summary;
  return {
    ok,
    pythonCode: py.code,
    pythonErrorTail: (py.err || '').split('\n').slice(-4).join('\n').slice(0, 900),
    summary,
    files,
    outRel: rel ? rel + '/songs-4.70' : 'songs-4.70',
    durationMs: 0,
  };
}

function filesHtmlFor(files, outRel) {
  const mid = files.filter((f) => /\.mid$/i.test(f.name));
  const cards = files.filter((f) => /\.scorecard\.json$/i.test(f.name));
  let html = '<div style="margin-top:6px;max-height:240px;overflow:auto;border:1px solid var(--line);border-radius:10px;padding:8px">';
  mid.forEach((f) => {
    html += '<div style="padding:3px 2px;border-bottom:1px solid var(--line)"><b style="font-size:12px">' + f.name + '</b>' +
      ' <a class="dl" href="/api/ws/file?f=' + encodeURIComponent(f.rel) + '" download style="padding:2px 9px">⬇ mid</a>' +
      ' <a class="dl" href="/api/ws/file?f=' + encodeURIComponent(f.rel.replace(/\.mid$/i, '.scorecard.json')) + '" download style="padding:2px 9px">⬇ scorecard</a>' +
      ' <span class="mini">' + f.bytes + ' B</span></div>';
  });
  html += '</div>';
  if (cards.length) {
    html += '<span class="mini">scorecards: ' + cards.length + ' (validacija, digest, drums.evidence po pesmi)</span>';
  }
  return html;
}

function validateModelBody(body) {
  const errors = [];
  const num = (v, lo, hi, label, dflt) => {
    const n = parseInt(v, 10);
    if (!Number.isInteger(n)) { errors.push(label + ': ceo broj, podrazumevano ' + dflt); return dflt; }
    if (n < lo || n > hi) { errors.push(label + ': van opsega ' + lo + '..' + hi); return Math.min(hi, Math.max(lo, n)); }
    return n;
  };
  const flt = (v, lo, hi, label, dflt) => {
    const n = parseFloat(v);
    if (!Number.isFinite(n)) { errors.push(label + ': broj, podrazumevano ' + dflt); return dflt; }
    if (n < lo || n > hi) { errors.push(label + ': van opsega'); return Math.min(hi, Math.max(lo, n)); }
    return n;
  };
  const layer = body.layer === 'micro' ? 'micro' : 'stat';
  let device = String(body.device || 'cpu').toLowerCase();
  if (device !== 'cpu') {
    errors.push('device: samo CPU (ciljna mašina i5-4460, bez GPU zavisnosti) — koristim cpu');
    device = 'cpu';
  }
  const echo = {
    layer,
    order: num(body.order, 1, 6, 'order', layer === 'micro' ? 3 : 3),
    smoothing: flt(body.smoothing, 0, 1, 'smoothing', 0.01),
    context: num(body.context, 8, 1024, 'context', 256),
    epochs: num(body.epochs, 1, 200, 'epochs', 3),
    seed: num(body.seed, 1, 999999, 'seed', 471),
    threads: num(body.threads, 1, 32, 'threads', 4),
    device,
    corpusSource: body.corpusSource === 'workspace' ? 'workspace' : 'committed',
    limit: num(body.limit, 0, 10000000, 'limit', 0),
    outDir: workRel(body.outDir || '') || (layer === 'stat' ? 'checkpoints/stat' : 'checkpoints/micro'),
  };
  return { echo, errors };
}

function workspaceList() {
  const work = [];
  if (fs.existsSync(WORK_DIR)) {
    for (const name of fs.readdirSync(WORK_DIR).sort()) {
      const abs = path.join(WORK_DIR, name);
      if (!fs.statSync(abs).isDirectory()) continue;
      walkDir(abs, name + '/', work);
    }
  }
  const out = [];
  if (fs.existsSync(OUT_DIR)) {
    for (const name of fs.readdirSync(OUT_DIR).sort()) {
      const abs = path.join(OUT_DIR, name);
      const st = fs.statSync(abs);
      if (st.isFile()) out.push({ name, bytes: st.size });
    }
  }
  return { work: work.sort((a, b) => a.rel.localeCompare(b.rel)), out };
}

async function route(req, res) {
  const u = new URL(req.url, 'http://localhost');
  const p = u.pathname;
  if (req.method === 'GET' && p === '/') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
    return res.end(INDEX_HTML);
  }
  if (req.method === 'GET' && p === '/api/health') {
    return json(res, 200, { ok: true, version: BRIDGE_VERSION, python: PY, outDir: OUT_DIR,
                            workDir: WORK_DIR, modelReady: modelReady(),
                            engines: ['session_pass 4.60', 'mix 4.52', 'sty 4.53',
                                      'groove 4.54', 'techniques 4.55', 'special 4.57',
                                      'roles 4.59', 'brain 4.68', 'composer 4.70',
                                      'corpus 4.69', 'gui 4.71'] });
  }
  if (req.method === 'GET' && p === '/api/compose/info') {
    const info = await composeInfo();
    if (!info) return json(res, 500, { error: 'composer_engine.py nije odgovorio', styles: [] });
    return json(res, 200, { styles: info });
  }
  if (req.method === 'GET' && p === '/api/compose/list') {
    return json(res, 200, { runs: scanSongRuns() });
  }
  if (req.method === 'POST' && p === '/api/compose') {
    let body = {};
    try { body = await readJsonBody(req, 8192); } catch { return json(res, 400, { error: 'očekuje se JSON telo' }); }
    const r = await composeSongs(body);
    if (!r.ok) return json(res, 500, r);
    return json(res, 200, {
      ok: true, summary: r.summary, outRel: r.outRel,
      filesHtml: filesHtmlFor(r.files, r.outRel), files: r.files, pythonCode: r.pythonCode,
    });
  }
  if (req.method === 'GET' && p === '/api/corpus/status') {
    return json(res, 200, { committed: corpusStatsOf(COMMITTED_CORPUS_STATS) });
  }
  if (req.method === 'POST' && p === '/api/corpus/build') {
    let body = {};
    try { body = await readJsonBody(req, 8192); } catch { return json(res, 400, { error: 'očekuje se JSON telo' }); }
    const seed = parseInt(body.seed, 10);
    const synthetic = parseInt(body.synthetic, 10);
    const realLimit = parseInt(body.realLimit, 10);
    if (!Number.isInteger(seed) || seed < 1 || seed > 999999) return json(res, 400, { error: 'seed: 1..999999' });
    if (!Number.isInteger(synthetic) || synthetic < 0 || synthetic > 5000) return json(res, 400, { error: 'synthetic: 0..5000' });
    if (!Number.isInteger(realLimit) || realLimit < 0 || realLimit > 500) return json(res, 400, { error: 'realLimit: 0..500' });
    const rel = workRel(body.outDir || '') || '';
    const abs = workAbs(rel);
    fs.mkdirSync(abs, { recursive: true });
    const py = await runPython(['-m', 'dna_midi_studio.composer_corpus', '--out', abs,
      '--seed', String(seed), '--synthetic', String(synthetic), '--real-limit', String(realLimit)], 300_000);
    let stats = corpusStatsOf(path.join(abs, 'corpus-4.69', 'corpus-stats-4.69.json'));
    const ok = py.code === 0 && !!stats;
    const relBase = (rel ? rel + '/' : '') + 'corpus-4.69';
    return json(res, ok ? 200 : 500, {
      ok,
      pythonCode: py.code,
      pythonErrorTail: (py.err || '').split('\n').slice(-4).join('\n').slice(0, 900),
      stats,
      relStats: relBase + '/corpus-stats-4.69.json',
      relManifest: relBase + '/corpus-manifest-4.69.json',
    });
  }
  if (req.method === 'GET' && p === '/api/model/status') {
    const corpus = corpusStatsOf(COMMITTED_CORPUS_STATS);
    return json(res, 200, {
      version: BRIDGE_VERSION,
      ready: modelReady(),
      module: 'dna_midi_studio/model_train.py',
      layer: 'statistički n-gram + mikro-GPT (S3)',
      corpus: {
        items: corpus ? corpus.items : null,
        tokens: corpus ? corpus.tokens : null,
        bars: corpus ? corpus.bars : null,
        vocabSize: corpus ? corpus.vocabSize : null,
      },
      note: modelReady()
        ? 'Model sloj je instaliran — GUI opcije pokreću trening.'
        : 'S3 (statistički sloj) se aktivira u sledećoj nadogradnji istog lanca. GUI opcije ispod su spremne i server ih već validira; pokretač je model-train.bat / isto dugme ovde.',
    });
  }
  if (req.method === 'POST' && p === '/api/model/train') {
    let body = {};
    try { body = await readJsonBody(req, 16384); } catch { return json(res, 400, { error: 'očekuje se JSON telo' }); }
    const { echo, errors } = validateModelBody(body);
    if (!modelReady()) {
      return json(res, 501, { ok: false, status: 'not_ready', echo, errors,
        reason: 'model_train.py još nije instaliran — S3 je sledeći korak lanca (4.71); GUI je pripremljen sa svim opcijama.',
        target: 'dna_midi_studio/model_train.py' });
    }
    // S3 dispatcher: when the module lands, train with the validated options.
    // model_train.py --layer <stat|micro> --order N --smoothing X --context N
    //   --epochs N --seed N --threads N --device cpu --corpus <committed|workspace>
    //   --limit N --out <dir>
    const args = ['dna_midi_studio/model_train.py', '--layer', echo.layer,
      '--order', String(echo.order), '--smoothing', String(echo.smoothing),
      '--context', String(echo.context), '--epochs', String(echo.epochs),
      '--seed', String(echo.seed), '--threads', String(echo.threads),
      '--device', echo.device, '--corpus', echo.corpusSource,
      '--limit', String(echo.limit), '--out', workAbs(echo.outDir)];
    const py = await runPython(args, 1_800_000);
    let result = null;
    const lines = (py.out || '').trim().split('\n');
    for (let i = lines.length - 1; i >= 0; i--) {
      try { result = JSON.parse(lines.slice(i).join('\n')); break; } catch { /* keep scanning */ }
    }
    return json(res, py.code === 0 ? 200 : 500, {
      ok: py.code === 0, pythonCode: py.code, echo,
      pythonErrorTail: (py.err || '').split('\n').slice(-5).join('\n').slice(0, 900),
      result,
    });
  }
  if (req.method === 'GET' && p === '/api/ws/list') {
    return json(res, 200, workspaceList());
  }
  if (req.method === 'GET' && p === '/api/ws/file') {
    const rel = workRel(decodeURIComponent(u.searchParams.get('f') || ''));
    if (!rel) return json(res, 400, { error: 'f?=rel path (workspace-4.71/...)' });
    const abs = path.resolve(WORK_DIR, rel);
    if (!abs.startsWith(path.resolve(WORK_DIR) + path.sep) || !fs.existsSync(abs)) {
      return json(res, 404, { error: 'nema fajla: ' + rel });
    }
    const data = fs.readFileSync(abs);
    res.writeHead(200, { 'content-type': contentType(rel),
                         'content-length': data.length,
                         'content-disposition': `attachment; filename="${path.basename(rel)}"` });
    return res.end(data);
  }
  if (req.method === 'GET' && p === '/api/presets') {
    return json(res, 200, { presets: Object.entries(PRESETS).map(([id, v]) => ({
      id, label: v.label, file: v.file, roles: v.roles, melody: v.melody || '' })) });
  }
  if (req.method === 'GET' && p === '/api/sample-analyze') {
    const id = u.searchParams.get('p');
    const pr = PRESETS[id];
    if (!pr) return json(res, 404, { error: 'unknown preset: ' + id });
    const abs = path.join(ROOT, pr.file);
    if (!fs.existsSync(abs)) return json(res, 404, { error: 'sample file missing' });
    const apply = u.searchParams.get('apply') === '1';
    const actions = u.searchParams.get('actions') || '';
    const r = await analyze(abs, { roles: pr.roles, melody: pr.melody || '', apply, actions });
    const sidA = sanitizeSid(req.headers['x-session']);
    if (sidA && r.ok && r.report) sessionSaveReport(sidA, r.report);
    return json(res, r.ok ? 200 : 500, r);
  }
  if (req.method === 'POST' && p === '/api/analyze') {
    const filename = sanitize(req.headers['x-filename']);
    if (!/\.(mid|midi|kar)$/i.test(filename)) {
      return json(res, 400, { error: 'očekuje se .mid/.midi/.kar fajl' });
    }
    const chunks = [];
    let size = 0;
    for await (const c of req) {
      size += c.length;
      if (size > MAX_BYTES) return json(res, 413, { error: 'fajl prevelik (>50MB)' });
      chunks.push(c);
    }
    fs.mkdirSync(path.join(OUT_DIR, 'uploads'), { recursive: true });
    const abs = path.join(OUT_DIR, 'uploads', randomUUID().slice(0, 8) + '-' + filename);
    fs.writeFileSync(abs, Buffer.concat(chunks));
    const r = await analyze(abs, {
      roles: String(req.headers['x-roles'] || '').trim(),
      melody: String(req.headers['x-melody'] || '').trim(),
      apply: String(req.headers['x-apply']) === '1',
      actions: String(req.headers['x-actions'] || '').trim(),
    });
    const sidB = sanitizeSid(req.headers['x-session']);
    if (sidB && r.ok && r.report) sessionSaveReport(sidB, r.report);
    return json(res, r.ok ? 200 : 500, r);
  }
  if (req.method === 'GET' && p === '/api/session') {
    return json(res, 200, { sessions: sessionList() });
  }
  if (req.method === 'POST' && p === '/api/session') {
    let sid = null;
    try {
      const body = await readJsonBody(req, 8192);
      if (body && body.id) sid = sanitizeSid(body.id);
    } catch { /* no body ok */ }
    const ensured = ensureSession(sid);
    const report = sessionLastReport(ensured);
    return json(res, 200, { sessionId: ensured, created: !sid, restored: !!report, report });
  }
  if (req.method === 'POST' && p === '/api/assistant') {
    const body = await readJsonBody(req, 65536);
    if (!body || typeof body.text !== 'string') return json(res, 400, { error: 'očekuje se {text}' });
    const out = await assistantTurn(body.text, body.sessionId || '');
    return json(res, 200, out);
  }
  if (req.method === 'GET' && p.startsWith('/api/session/')) {
    const sid = sanitizeSid(p.slice('/api/session/'.length));
    if (!sid) return json(res, 400, { error: 'session id?' });
    ensureSession(sid);
    return json(res, 200, { id: sid, history: sessionHistory(sid), report: sessionLastReport(sid) });
  }
  if (req.method === 'DELETE' && p.startsWith('/api/session/')) {
    const sid = sanitizeSid(p.slice('/api/session/'.length));
    for (const f of [sessionMeta(sid), sessionLog(sid), sessionReportFile(sid)]) {
      try { fs.unlinkSync(f); } catch { /* ok */ }
    }
    return json(res, 200, { ok: true, sessionId: sid });
  }
  if (req.method === 'GET' && p.startsWith('/api/artifacts/')) {
    const name = sanitize(decodeURIComponent(p.slice('/api/artifacts/'.length)));
    const abs = path.resolve(OUT_DIR, name);
    if (!abs.startsWith(path.resolve(OUT_DIR) + path.sep) || !fs.existsSync(abs)) {
      return json(res, 404, { error: 'nema artefakta' });
    }
    const data = fs.readFileSync(abs);
    res.writeHead(200, { 'content-type': contentType(name),
                         'content-length': data.length,
                         'content-disposition': `attachment; filename="${name}"` });
    return res.end(data);
  }
  return json(res, 404, { error: 'nepoznata ruta: ' + p });
}

export async function startBridge(host = '0.0.0.0', port = Number(process.env.PORT || 8123)) {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.mkdirSync(WORK_DIR, { recursive: true });
  const server = http.createServer((req, res) => {
    route(req, res).catch((e) => { try { json(res, 500, { error: String(e) }); } catch { /* socket gone */ } });
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, resolve);
  });
  return server;
}

const isMain = process.argv[1] && import.meta.url ===
  pathToFileURL(path.resolve(process.argv[1])).href;

if (isMain) {
  const server = await startBridge('0.0.0.0', Number(process.env.PORT || 8123));
  const addr = server.address();
  console.log(`[dna-bridge] listening on http://0.0.0.0:${addr.port} (out: ${OUT_DIR})`);
}
