#!/usr/bin/env node
// DNA Optimizer bridge 4.61 — Phase B: Suno-like loop without npm deps.
//
// One node:http server (stdlib only) that
//   * serves a minimal dark UI at /
//   * POST /api/analyze  — upload a .mid/.midi/.kar, run the Session Pass
//     (python3.11 dna_midi_studio/session_pass.py) with the whole engine set,
//     return the consolidated report + downloadable artifacts
//   * GET /api/sample-analyze?p=<preset>&apply=1 — demo samples
//   * GET /api/artifacts/<name> — download generated .mid/.json
//   * GET /api/presets, GET /api/health
//
// Invariants: the uploaded source file is only ever copied into
// artifacts-max-4.61/uploads/ and never modified; every mutation happens in
// NEW artifacts by the gated engines; the bridge itself never edits bytes.
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const OUT_DIR = path.join(ROOT, 'artifacts-max-4.61');
const PY = process.env.PYTHON || 'python3.11';
const MAX_BYTES = 50 * 1024 * 1024;
const PY_TIMEOUT_MS = 240_000;

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

function runPython(args, timeoutMs = PY_TIMEOUT_MS) {
  return new Promise((resolve) => {
    const child = spawn(PY, args, { cwd: ROOT });
    let out = '';
    let err = '';
    let done = false;
    const timer = setTimeout(() => { if (!done) child.kill('SIGKILL'); }, timeoutMs);
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { err += d; });
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

async function analyze(absFile, { roles = '', melody = '', apply = false }) {
  const stem = stemOf(path.basename(absFile));
  const args = ['dna_midi_studio/session_pass.py', '--input', absFile, '--out-dir', OUT_DIR];
  if (roles) args.push('--roles', roles);
  if (melody) args.push('--melody-channels', melody);
  if (apply) args.push('--apply-safe');
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

const INDEX_HTML = `<!doctype html><html lang="sr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DNA Optimizer — Session Pass (4.61)</title>
<style>
:root{color-scheme:dark}
body{margin:0;font:14px/1.45 system-ui,sans-serif;background:#0d1117;color:#e6edf3}
header{padding:14px 22px;background:#161b22;border-bottom:1px solid #30363d;display:flex;align-items:baseline;gap:14px}
header h1{font-size:16px;margin:0}
header span{color:#8b949e}
main{padding:18px 22px;max-width:1100px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 16px;margin:14px 0}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
button{background:#238636;border:0;color:#fff;border-radius:8px;padding:8px 14px;cursor:pointer;font-size:13px}
button.small{background:#1f6feb;padding:4px 10px;margin:2px}
button:hover{filter:brightness(1.1)}
input[type=file]{color:#c9d1d9}
label{color:#8b949e;font-size:12px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
td,th{border:1px solid #30363d;padding:4px 8px;text-align:left}
th{background:#21262d}
.badge{padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600}
.APPLIED{background:#1a7f37}.READY{background:#1f6feb}.SKIPPED{background:#484f58}
.NEEDS_DECISION{background:#9e6a03}.LOCKED{background:#b62324}.GATE_FAILED{background:#b62324}
a{color:#58a6ff;text-decoration:none}
#status{margin-left:auto;color:#8b949e}
#err{color:#f85149;white-space:pre-wrap}
details{margin-top:6px}summary{cursor:pointer;color:#8b949e}
code,pre{font-size:11px}
</style></head><body>
<header><h1>DNA Optimizer — Session Pass</h1><span>faza B: bridge 4.61 · optimizacija na Pa800 temelju · bez generisanja</span><div id="status">…</div></header>
<main>
<div class="card"><div class="row">
<label>1. MIDI fajl (.mid/.midi/.kar)</label>
<input id="file" type="file" accept=".mid,.midi,.kar">
<label>uloge (opciono, evidencija):</label>
<input id="roles" size="44" placeholder="8:bass,9:drums,10:percussion,…">
</div>
<div class="row" style="margin-top:8px">
<label><input id="apply" type="checkbox"> primeni bezbedne akcije (--apply-safe, piše NOVE fajlove)</label>
<button onclick="doUpload()">2. Analiziraj / Optimizuj</button>
</div>
<div id="samples" class="row" style="margin-top:10px"></div>
<div id="err"></div></div>
<div id="out"></div>
</main>
<script>
const $=(id)=>document.getElementById(id);
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function badge(s){return '<span class="badge '+esc(s)+'">'+esc(s)+'</span>';}
async function call(url,opt){const r=await fetch(url,opt);const t=await r.text();let j;try{j=JSON.parse(t)}catch{j={raw:t}}if(!r.ok)throw new Error((j.error||t||('HTTP '+r.status)));return j;}
async function init(){try{
 $('status').textContent='bridge OK';
 const p=await call('/api/presets');
 $('samples').innerHTML='<label>Demo (uzorci iz korpusa):</label>'+p.presets.map(pr=>
   '<button class="small" onclick="sample(\''+pr.id+'\')">'+esc(pr.label)+'</button>').join('');
}catch(e){$('status').textContent='bridge nedostupan';$('err').textContent=String(e);}}
async function sample(id){$('err').textContent='';const pr=(await call('/api/presets')).presets.find(p=>p.id===id);
 const j=await call('/api/sample-analyze?p='+encodeURIComponent(id)+'&apply='+($('apply').checked?1:0));render(j);
 if(pr&&pr.roles)$('roles').value=pr.roles;}
async function doUpload(){const f=$('file').files[0];if(!f){$('err').textContent='Izaberi fajl prvo.';return}$('err').textContent='';
 const buf=new Uint8Array(await f.arrayBuffer());
 const j=await call('/api/analyze',{method:'POST',headers:{'x-filename':f.name,'x-roles':$('roles').value,'x-apply':$('apply').checked?'1':'0'},body:buf});render(j);}
function render(j){
 const r=j.report;if(!r){$('out').innerHTML='<div class="card"><b>Nema izveštaja</b><pre>'+esc(j.pythonErrorTail||JSON.stringify(j,null,1))+'</pre></div>';return}
 let h='';
 h+='<div class="card"><b>'+esc(r.sourceName)+'</b> · markera: '+esc(r.fileFacts.markerCount)+
    ' · ppq '+esc(r.fileFacts.ppq)+' · format '+esc(r.fileFacts.format)+' · kanali '+esc(r.fileFacts.channels.length)+
    ' · trajanje: '+esc(j.durationMs)+' ms'+(j.actionStatuses||[]).map(a=>badge(a.status)).join(' ')+'</div>';
 const acts=r.actions||[];
 if(acts.length){h+='<div class="card"><b>Akcije</b><table><tr><th>ID</th><th>Engine</th><th>Status</th><th>Dokaz / efekat</th></tr>';
 for(const a of acts){const art=(a.artifact?'<br><a href="/api/artifacts/'+esc(a.artifact.split('/').pop())+'">⬇ '+esc(a.artifact.split('/').pop())+'</a>':'');
  h+='<tr><td>'+esc(a.id)+'</td><td>'+esc(a.engine)+'</td><td>'+badge(a.status)+'</td><td>'+esc(a.effect||a.reason||'')+' '+art+
     (a.gates?'<details><summary>gates</summary><pre>'+esc(JSON.stringify(a.gates,null,1))+'</pre></details>':'')+'</td></tr>';}
 h+='</table></div>';}
 const ch=r.perRolePatterns||{};
 const rows=Object.entries(ch).map(([c,b])=>{const v=b.velocity||{};const mp=b.melodyPattern?' · mel '+esc(JSON.stringify({up:mp?mp.upShare:null,int:mp?mp.meanAbsSemis:null})):'';
  return '<tr><td>'+esc(b.role)+'</td><td>ch'+esc(c)+'</td><td>'+esc(b.noteCount)+'</td><td>'+esc((b.register||[]).join('–'))+'</td><td>'+esc(b.densityNotesPerBar)+'</td><td>'+esc(v.q50)+'</td><td>'+esc(b.polyphonyPeak)+'</td></tr>';});
 if(rows.length){h+='<div class="card"><b>Role patterni</b><table><tr><th>uloga</th><th>kanal</th><th>note</th><th>registar</th><th>gustina/takt</th><th>vel q50</th><th>poly</th></tr>'+rows.join('')+'</table></div>';}
 if((r.grooveVsHuman||[]).length){h+='<div class="card"><b>Groove vs ljudska referenca</b><table><tr><th>kanal</th><th>note</th><th>std ms</th><th>na gridu</th></tr>'+
   r.grooveVsHuman.map(g=>'<tr><td>ch'+esc(g.channel)+' '+esc(g.role)+'</td><td>'+esc(g.noteCount)+'</td><td>'+esc(g.stdMs)+'</td><td>'+esc(g.exactOnGridShare)+'</td></tr>').join('')+'</table></div>';}
 h+='<div class="card"><details open><summary>sirovi izveštaj (JSON)</summary><pre>'+esc(JSON.stringify(r,null,1))+'</pre></details></div>';
 $('out').innerHTML=h;
 window.scrollTo({top:0,behavior:'smooth'});
}
init();
</script></body></html>`;

async function route(req, res) {
  const u = new URL(req.url, 'http://localhost');
  const p = u.pathname;
  if (req.method === 'GET' && p === '/') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    return res.end(INDEX_HTML);
  }
  if (req.method === 'GET' && p === '/api/health') {
    return json(res, 200, { ok: true, version: '4.61', python: PY, outDir: OUT_DIR,
                            engines: ['session_pass 4.60', 'mix 4.52', 'sty 4.53',
                                      'groove 4.54', 'techniques 4.55', 'special 4.57',
                                      'roles 4.59'] });
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
    const r = await analyze(abs, { roles: pr.roles, melody: pr.melody || '', apply });
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
    });
    return json(res, r.ok ? 200 : 500, r);
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
