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

const INDEX_HTML = `<!doctype html><html lang="sr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DNA Optimizer — Session Flow (4.62)</title>
<style>
:root{color-scheme:dark}
body{margin:0;font:14px/1.45 system-ui,sans-serif;background:#0d1117;color:#e6edf3}
header{padding:14px 22px;background:#161b22;border-bottom:1px solid #30363d;display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
header h1{font-size:16px;margin:0}
header span{color:#8b949e}
main{padding:18px 22px;max-width:1120px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 16px;margin:14px 0}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
button{background:#238636;border:0;color:#fff;border-radius:8px;padding:8px 14px;cursor:pointer;font-size:13px}
button.sec{background:#1f6feb}
button:disabled{opacity:.45;cursor:default}
button.small{background:#1f6feb;padding:4px 10px;margin:2px}
input[type=file]{color:#c9d1d9}
label{color:#8b949e;font-size:12px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
td,th{border:1px solid #30363d;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#21262d}
.badge{padding:2px 8px;border-radius:20px;font-size:11px;font-weight:600;color:#fff}
.APPLIED{background:#1a7f37}.READY{background:#1f6feb}.SKIPPED{background:#484f58}
.NEEDS_DECISION{background:#9e6a03}.LOCKED{background:#b62324}.GATE_FAILED{background:#b62324}
.step{display:inline-block;width:22px;height:22px;line-height:22px;text-align:center;border-radius:50%;
 background:#238636;color:#fff;font-weight:700;font-size:12px;margin-right:6px}
a{color:#58a6ff;text-decoration:none}
#status{margin-left:auto;color:#8b949e}
#err{color:#f85149;white-space:pre-wrap}
details{margin-top:6px}summary{cursor:pointer;color:#8b949e}
pre{font-size:11px;max-height:340px;overflow:auto}
.chip{display:inline-block;background:#21262d;border:1px solid #30363d;border-radius:20px;padding:4px 12px;margin:3px;font-size:12px}
.actrow input{transform:scale(1.25);margin-right:8px}
</style></head><body>
<header><h1>DNA Optimizer — Session Flow</h1><span>faza C jezgro · 4.62 · plan → potvrdi → primeni · Pa800 temelj</span><div id="status">…</div></header>
<main>
<div class="card"><div class="row"><span class="step">1</span>
<label>MIDI fajl (.mid/.midi/.kar)</label>
<input id="file" type="file" accept=".mid,.midi,.kar">
<label>uloge (evidencija, opciono):</label>
<input id="roles" size="40" placeholder="8:bass,9:drums,10:percussion,…">
<button id="bAnalyze" class="sec" onclick="stepAnalyze()">Analiziraj (plan)</button>
</div>
<div class="row" style="margin-top:8px"><span class="step" style="background:#1f6feb">2</span>
<label>Demo uzorci (korpus):</label><span id="samples"></span>
<button id="bApply" disabled onclick="applySelected()">Primeni izabrane (0)</button>
</div>
<div id="note" style="margin-top:6px;color:#8b949e;font-size:12px">Izvorni fajl se nikad ne menja — primena piše nove artefakte; DNC/slap/pop trigeri ostaju zaključani.</div>
<div id="err"></div></div>
<div id="out"></div>
</main>
<script>
var $=function(id){return document.getElementById(id)};
var state={kind:null,payload:null,roles:''};
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function badge(s){return '<span class="badge '+esc(s)+'">'+esc(s)+'</span>';}
function selIds(){var c=[].slice.call(document.querySelectorAll('.actrow input:checked'));return c.map(function(i){return i.getAttribute('data-id');});}
function updApplyBtn(){var n=selIds().length;var b=$('bApply');b.disabled=n===0;b.textContent='Primeni izabrane ('+n+')';}
function call(url,opt){return fetch(url,opt).then(function(r){return r.text().then(function(t){var j;try{j=JSON.parse(t)}catch(e){j={raw:t}}
 if(!r.ok){throw new Error(j.error||t||('HTTP '+r.status));}return j;});});}
function errShow(e){$('err').textContent='Greška: '+(e&&e.message||e);}
function init(){call('/api/presets').then(function(p){
 $('samples').innerHTML=p.presets.map(function(pr){return '<button class="small" onclick="preset(\''+pr.id+'\')">'+esc(pr.label)+'</button>';}).join('');
 $('status').textContent='bridge OK · '+p.presets.length+' preseta';}).catch(errShow);}
function preset(id){call('/api/presets').then(function(p){var pr=p.presets.filter(function(x){return x.id===id;})[0];
 state={kind:'preset',payload:id,roles:pr.roles||''};$('roles').value=pr.roles||'';
 $('status').textContent='uzorak: '+pr.file;stepAnalyze();}).catch(errShow);}
function stepAnalyze(){var apply=arguments.length?arguments[0]:false;var actions=arguments.length>1?arguments[1]:'';
 $('err').textContent='';$('out').innerHTML='<div class="card">Analiza u toku… (Session Pass 4.60/4.61)</div>';
 var f=$('file').files[0];
 var go=function(){var url,opt;
  if(state.kind==='preset'){url='/api/sample-analyze?p='+encodeURIComponent(state.payload)+'&apply='+(apply?1:0)+(actions?'&actions='+encodeURIComponent(actions):'');
    return call(url);}
  if(f){var buf=new Uint8Array(0);
    return f.arrayBuffer().then(function(b){var u=new Uint8Array(b);
      return call('/api/analyze',{method:'POST',headers:{'x-filename':f.name,'x-roles':$('roles').value.trim(),
        'x-apply':apply?'1':'0','x-actions':actions},body:u});});}
  return Promise.reject(new Error('Izaberi fajl ili demo uzorak.'));};
 if(!apply&&!state.kind&&!f){$('err').textContent='Izaberi fajl ili demo uzorak prvo.';$('out').innerHTML='';return;}
 go().then(function(j){if(!j.ok||!j.report){throw new Error(j.pythonErrorTail||'python nije vratio izveštaj');}
  if(apply)renderResult(j);else renderPlan(j);}).catch(errShow);}
function renderPlan(j){var r=j.report;state.kind=state.kind||'upload';
 var applyable=(r.actions||[]).filter(function(a){return a.status==='READY';});
 var rows=(r.actions||[]).map(function(a){
  var cb=(a.status==='READY')?'<input type="checkbox" class="actbox" data-id="'+esc(a.id)+'" checked>':'';
  var art=a.artifact?'<br><a href="/api/artifacts/'+esc(a.artifact.split('/').pop())+'">⬇ '+esc(a.artifact.split('/').pop())+'</a>':'';
  var gates=a.gates?'<details><summary>gates</summary><pre>'+esc(JSON.stringify(a.gates,null,1))+'</pre></details>':'';
  return '<tr class="actrow"><td>'+cb+'<b>'+esc(a.id)+'</b></td><td>'+esc(a.engine)+'</td><td>'+badge(a.status)+'</td><td>'+esc(a.effect||a.reason||'')+' '+art+gates+'</td></tr>';}).join('');
 var h='<div class="card"><b>Plan ('+esc(r.sourceName)+')</b> · markera: '+esc(r.fileFacts.markerCount)+
  ' · format '+esc(r.fileFacts.format)+' · kanali: '+esc(r.fileFacts.channels.length)+' · '+esc(j.durationMs)+' ms</div>';
 h+='<div class="card"><b>Akcije — označi koje da se primene</b><table><tr><th>izbor</th><th>engine</th><th>status</th><th>efekat / dokaz</th></tr>'+rows+'</table></div>';
 h+='<div class="card"><b>Role patterni</b><table><tr><th>uloga</th><th>kanal</th><th>note</th><th>registar</th><th>gustina/takt</th><th>vel q50</th><th>poly</th></tr>'+Object.entries(r.perRolePatterns||{}).map(function(e){var b=e[1],v=b.velocity||{},mp=b.melodyPattern||{};
  var mel=mp.meanAbsSemis!=null?'<br><span style="color:#8b949e">mel: up '+esc(mp.upShare)+' · sr.int '+esc(mp.meanAbsSemis)+'</span>':'';
  return '<tr><td>'+esc(b.role)+'</td><td>ch'+esc(e[0])+'</td><td>'+esc(b.noteCount)+'</td><td>'+esc((b.register||[]).join('–'))+'</td><td>'+esc(b.densityNotesPerBar)+'</td><td>'+esc(v.q50)+'</td><td>'+esc(b.polyphonyPeak)+mel+'</td></tr>';}).join('')+'</table></div>';
 if((r.grooveVsHuman||[]).length){h+='<div class="card"><b>Groove vs ljudska referenca</b><table><tr><th>kanal</th><th>note</th><th>std ms</th><th>na gridu</th></tr>'+r.grooveVsHuman.map(function(g){
  return '<tr><td>ch'+esc(g.channel)+' '+esc(g.role)+'</td><td>'+esc(g.noteCount)+'</td><td>'+esc(g.stdMs)+'</td><td>'+esc(g.exactOnGridShare)+'</td></tr>';}).join('')+'</table></div>';}
 h+='<div class="card"><details><summary>sirovi izveštaj (JSON)</summary><pre>'+esc(JSON.stringify(r,null,1))+'</pre></details></div>';
 $('out').innerHTML=h;updApplyBtn();}
function applySelected(){var ids=selIds();if(!ids.length)return;stepAnalyze(true,ids.join(','));}
function renderResult(j){var r=j.report;
 var arts=(r.actions||[]).filter(function(a){return a.artifact;}).map(function(a){return '<span class="chip"><a href="/api/artifacts/'+esc(a.artifact.split('/').pop())+'">⬇ '+esc(a.artifact.split('/').pop())+'</a></span>';});
 var h='<div class="card"><b>Primenjeno</b> · '+esc(r.sourceName)+'<br><div style="margin-top:8px">'+(arts.length?arts.join(''):'<span style="color:#8b949e">nema primenjenih artefakata</span>')+'</div></div>';
 h+='<div class="card"><b>Statusi posle primene</b><table><tr><th>akcija</th><th>status</th></tr>'+(r.actions||[]).map(function(a){
  return '<tr><td>'+esc(a.id)+'</td><td>'+badge(a.status)+'</td></tr>';}).join('')+'</table></div>';
 h+='<div class="card"><button class="sec" onclick="location.reload()">Novi fajl</button></div>';
 $('out').innerHTML=h;updApplyBtn();}
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
    const actions = u.searchParams.get('actions') || '';
    const r = await analyze(abs, { roles: pr.roles, melody: pr.melody || '', apply, actions });
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
