#!/usr/bin/env node
// DNA Optimizer bridge 4.63 — AI Studio UI (assistant + sidebar) over Session Pass. From 4.61 (Phase B): stdlib node:http, no npm deps.
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
<title>DNA Optimizer — AI Studio (4.63)</title>
<style>
:root{color-scheme:dark;--bg:#0a0e14;--panel:#11161f;--panel2:#0f141c;--line:#1e2733;--line2:#283442;
 --text:#dbe4ee;--muted:#8b98a9;--acc:#3d8bff;--acc2:#7c5cff;--ok:#2ecc71;--warn:#f0b429;--bad:#e74c3c;
 --grad:linear-gradient(135deg,#3d8bff,#7c5cff);--r:14px}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 "Inter",system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:
 radial-gradient(1200px 600px at 85% -10%,rgba(61,139,255,.10),transparent 60%),
 radial-gradient(900px 500px at -10% 110%,rgba(124,92,255,.08),transparent 55%),var(--bg);color:var(--text);
 height:100vh;overflow:hidden}
#app{display:flex;height:100vh}
#side{width:262px;min-width:262px;background:var(--panel);border-right:1px solid var(--line);
 display:flex;flex-direction:column;overflow-y:auto}
.brand{display:flex;gap:10px;align-items:center;padding:16px 16px 12px}
.logo{width:34px;height:34px;border-radius:10px;background:var(--grad);display:flex;align-items:center;
 justify-content:center;font-weight:800;font-size:16px;box-shadow:0 4px 14px rgba(61,139,255,.35)}
.brand b{font-size:15px;letter-spacing:.2px}
.brand small{display:block;color:var(--muted);font-size:10.5px;font-weight:500}
.menu{padding:6px 10px}
.mi{display:flex;gap:10px;align-items:center;width:100%;text-align:left;background:none;border:1px solid transparent;
 color:var(--text);padding:9px 12px;border-radius:10px;cursor:pointer;font-size:13.5px;margin:1px 0}
.mi:hover{background:#18202c}
.mi .ic{width:20px;text-align:center}
.sec{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:1.2px;padding:14px 16px 4px}
.pchip{display:flex;gap:8px;align-items:center;width:100%;text-align:left;background:#131a25;border:1px solid var(--line);
 color:var(--text);padding:7px 10px;border-radius:9px;margin:2px 0;cursor:pointer;font-size:12px}
.pchip:hover{border-color:var(--acc)}
.pchip .dot{width:7px;height:7px;border-radius:50%;background:var(--acc2)}
#side footer{padding:12px 16px;color:var(--muted);font-size:10.5px;border-top:1px solid var(--line);margin-top:auto}
#side footer .alive{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ok);
 box-shadow:0 0 8px var(--ok);margin-right:6px;animation:pulse 2s infinite}
@keyframes pulse{50%{opacity:.45}}
#main{flex:1;display:flex;flex-direction:column;min-width:0}
#top{display:flex;align-items:center;gap:12px;padding:12px 20px;border-bottom:1px solid var(--line);background:rgba(17,22,31,.6);backdrop-filter:blur(8px)}
#top .crumb{color:var(--muted);font-size:12.5px}
#top .spacer{flex:1}
.tag{font-size:11px;padding:4px 10px;border-radius:20px;border:1px solid var(--line2);color:var(--muted)}
.tag.g{color:#9be8c4;border-color:rgba(46,204,113,.3);background:rgba(46,204,113,.08)}
#chat{flex:1;overflow-y:auto;padding:26px 22px 10px;scroll-behavior:smooth}
.wrap{max-width:880px;margin:0 auto}
.msg{display:flex;gap:12px;margin:0 0 22px;animation:fadein .25s ease}
@keyframes fadein{from{opacity:0;transform:translateY(6px)}}
.av{width:32px;height:32px;min-width:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700}
.av.u{background:#24344d;border:1px solid var(--line2)}
.av.a{background:var(--grad);box-shadow:0 3px 10px rgba(61,139,255,.3)}
.bub{flex:1;min-width:0}
.u-msg{background:#18233a;border:1px solid #24334d;border-radius:14px 14px 4px 14px;padding:9px 13px;max-width:640px;margin-left:auto;display:inline-block}
.bub .who{color:var(--muted);font-size:11px;margin-bottom:3px}
.card{background:linear-gradient(180deg,#141b27,#111722);border:1px solid var(--line);border-radius:var(--r);
 padding:14px 16px;margin:10px 0}
.card h4{margin:0 0 8px;font-size:13px;color:#fff}
.card p{margin:6px 0}
table{border-collapse:collapse;width:100%;font-size:12.3px;margin:6px 0}
td,th{border:1px solid var(--line);padding:5px 8px;text-align:left}
th{background:#192231;color:#c3d2e2;font-weight:600}
tr:nth-child(even) td{background:rgba(255,255,255,.015)}
.badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:10.5px;font-weight:700;color:#fff;letter-spacing:.3px}
.APPLIED{background:#1f8f50}.READY{background:#2563eb}.SKIPPED{background:#4a5568}
.NEEDS_DECISION{background:#b7791f}.LOCKED{background:#c0392b}.GATE_FAILED{background:#c0392b}
.facts{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.fc{background:#18202c;border:1px solid var(--line);border-radius:10px;padding:6px 12px;font-size:12px}
.fc b{display:block;font-size:15px}
.fc span{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.8px}
.actions label{display:flex;gap:10px;align-items:flex-start;padding:9px 10px;border:1px solid var(--line);
 border-radius:11px;margin:6px 0;cursor:pointer;background:#131a25;transition:border .15s}
.actions label:hover{border-color:var(--line2)}
.actions input{transform:scale(1.2);margin-top:3px;accent-color:var(--acc)}
.actions .a-id{font-weight:700;font-size:12.5px;color:#9db8dc}
.actions .a-e{color:var(--muted);font-size:11px}
.actions .a-x{color:#c9d5e3;font-size:12.5px;display:block;margin-top:2px}
.actrow-head{display:flex;justify-content:space-between;align-items:center}
.btn{background:var(--grad);color:#fff;border:0;border-radius:11px;padding:9px 16px;font-size:13px;font-weight:600;cursor:pointer;transition:filter .15s,transform .05s}
.btn:hover{filter:brightness(1.12)}.btn:active{transform:translateY(1px)}
.btn:disabled{opacity:.4;cursor:default;filter:none}
.btn.ghost{background:#1a2434;border:1px solid var(--line2);color:var(--text)}
.dl{display:inline-flex;align-items:center;gap:6px;background:#12202f;border:1px solid rgba(61,139,255,.4);color:#7db4ff;
 border-radius:9px;padding:6px 12px;margin:4px 6px 4px 0;font-size:12.5px;text-decoration:none;font-weight:600}
.dl:hover{border-color:var(--acc);background:#142a44}
.okline{display:flex;align-items:center;gap:8px;color:#9be8c4;font-weight:600}
details{border:1px solid var(--line);border-radius:9px;padding:5px 10px;margin-top:6px;background:#10151d}
summary{cursor:pointer;color:var(--muted);font-size:11.5px}
pre{font-size:10.6px;max-height:300px;overflow:auto;color:#9db8dc}
.sugg{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.sugg button{background:transparent;border:1px solid var(--line2);color:#a9c2e2;border-radius:20px;padding:6px 13px;
 font-size:12px;cursor:pointer}
.sugg button:hover{border-color:var(--acc);color:#fff}
.typing{display:inline-flex;gap:5px;padding:14px 16px}
.typing i{width:7px;height:7px;border-radius:50%;background:#6a7f9c;animation:tp 1s infinite}
.typing i:nth-child(2){animation-delay:.18s}.typing i:nth-child(3){animation-delay:.36s}
@keyframes tp{0%,60%,100%{transform:translateY(0);opacity:.5}30%{transform:translateY(-5px);opacity:1}}
#comp{border-top:1px solid var(--line);padding:14px 20px 18px;background:rgba(17,22,31,.7);backdrop-filter:blur(8px)}
.ci{max-width:880px;margin:0 auto;background:#131a25;border:1px solid var(--line2);border-radius:16px;
 display:flex;align-items:flex-end;gap:8px;padding:8px 8px 8px 14px;transition:border .2s}
.ci:focus-within{border-color:rgba(61,139,255,.55);box-shadow:0 0 0 3px rgba(61,139,255,.12)}
#inp{flex:1;background:none;border:0;outline:0;color:var(--text);font-size:14px;padding:8px 0;resize:none;
 max-height:110px;font-family:inherit}
#inp::placeholder{color:#5b6a7e}
.ib{width:38px;height:38px;min-width:38px;border-radius:11px;border:0;display:flex;align-items:center;
 justify-content:center;cursor:pointer;font-size:17px;background:#1c2736;color:#a9c2e2}
.ib:hover{color:#fff;background:#22314a}
.ib.send{background:var(--grad);color:#fff;font-weight:800}
#filetag{max-width:880px;margin:4px auto 0;display:none}
.chipf{display:inline-flex;align-items:center;gap:8px;background:#12202f;border:1px solid rgba(61,139,255,.35);
 border-radius:20px;padding:5px 8px 5px 13px;font-size:12px}
.chipf .x{background:none;border:0;color:var(--muted);cursor:pointer;font-size:14px;border-radius:50%;width:20px;height:20px}
.chipf .x:hover{color:#fff;background:#24344d}
.hint{max-width:880px;margin:4px auto 0;color:#55647a;font-size:11px}
::-webkit-scrollbar{width:9px;height:9px}
::-webkit-scrollbar-thumb{background:#243140;border-radius:9px}
::-webkit-scrollbar-track{background:transparent}
#hamb{display:none}
@media(max-width:860px){#side{position:fixed;z-index:50;height:100%;transform:translateX(-100%);transition:.2s}
 #side.open{transform:none}#hamb{display:flex!important}}
</style></head><body>
<div id="app">
<aside id="side">
 <div class="brand"><div class="logo">D</div><div><b>DNA Optimizer</b><small>AI Studio · Pa800 temelj</small></div></div>
 <div class="menu">
  <button class="mi" onclick="newSession()"><span class="ic">＋</span>Nova sesija</button>
  <button class="mi" onclick="help()"><span class="ic">?</span>Šta mogu da uradim?</button>
  <div class="sec">Demo korpus</div>
  <div id="presets"></div>
  <div class="sec">Pravila</div>
  <button class="mi" onclick="rulesInfo()"><span class="ic">⚖</span>Licence i invarijante</button>
 </div>
 <footer><span class="alive"></span>bridge 4.63 · <span id="vstats">…</span></footer>
</aside>
<main id="main">
 <div id="top"><button id="hamb" class="ib" onclick="document.getElementById('side').classList.toggle('open')">☰</button>
  <span class="crumb">DNA Optimizer / <span id="sessName">Nova sesija</span></span><div class="spacer"></div>
  <span class="tag g" id="bridgeTag">povezivanje…</span></div>
 <div id="chat"><div class="wrap" id="wrap"></div></div>
 <div id="comp">
  <div id="filetag"><span class="chipf"><span id="ftName"></span><button class="x" onclick="clearFile()">✕</button></span></div>
  <div class="ci">
   <button class="ib" id="clip" title="Priloži MIDI fajl">📎<input id="file" type="file" accept=".mid,.midi,.kar" onchange="pickedFile()"></button>
   <textarea id="inp" rows="1" placeholder="Npr. „analiziraj reference-style” ili „primeni sve” — ili priloži fajl…"></textarea>
   <button class="ib send" id="send" onclick="onSend()">➤</button>
  </div>
  <div class="hint" id="hint">Prikači MIDI (drag &amp; drop ili 📎) pa Enter — asistent radi Session Pass i nudi akcije sa kapijama.</div>
 </div>
</main>
</div>
<script>
var $=function(id){return document.getElementById(id)};
var state={kind:null,payload:null,roles:'',file:null,lastPlan:null,thinking:false};
var PRESETS=[];
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function badge(s){return '<span class="badge '+esc(s)+'">'+esc(s)+'</span>';}
function call(url,opt){return fetch(url,opt).then(function(r){return r.text().then(function(t){var j;try{j=JSON.parse(t)}catch(e){j={raw:t}}
 if(!r.ok)throw new Error(j.error||t||('HTTP '+r.status));return j;});});}
var wrap=$('wrap');
function addUser(html){wrap.insertAdjacentHTML('beforeend','<div class="msg"><div class="av u">👤</div><div class="bub"><div class="u-msg">'+html+'</div></div></div>');scroll();}
function addBot(html){wrap.insertAdjacentHTML('beforeend','<div class="msg"><div class="av a">D</div><div class="bub"><div class="who">DNA Optimizer · Session Pass 4.60</div>'+html+'</div></div>');scroll();}
function addThink(){wrap.insertAdjacentHTML('beforeend','<div class="msg" id="think"><div class="av a">D</div><div class="bub"><div class="who">DNA Optimizer</div><div class="card typing" style="display:inline-block"><i></i><i></i><i></i></div></div></div>');scroll();}
function rmThink(){var t=$('think');if(t)t.remove();}
function scroll(){$('chat').scrollTop=$('chat').scrollHeight;}
function card(inner){return '<div class="card">'+inner+'</div>';}
function fmtBytes(n){if(n>1048576)return (n/1048576).toFixed(1)+' MB';return Math.round(n/1024)+' KB';}
function init(){call('/api/health').then(function(h){
 $('bridgeTag').textContent='bridge OK · '+h.engines[0];
 $('vstats').textContent=h.python+' · '+h.version;
 addBot('<h4 style="margin:0 0 6px">Dobar dan! 👋</h4><p style="margin:0 0 4px">Ja sam <b>DNA Optimizer</b> — radi kao AI asistent, ali <b>ne generišem muziku od nule</b>: analiziram tvoj MIDI, izmerim šta fabrika svira po ulogama, pa ti ponudim <b>optimizacije sa kapijama</b> za Korg Pa800.</p>'
  +'<p style="margin:4px 0 0;color:var(--muted)">Prikači fajl ili klikni demo u meniju. Možeš i da kucaš: „analiziraj reference-style”, „primeni sve”.</p>'
  +'<div class="sugg"><button onclick="demo(\'reference-style\')">▶ demo: reference-style</button><button onclick="demo(\'fixture\')">▶ demo: fixture</button><button onclick="demo(\'session35\')">▶ demo: session35</button><button onclick="help()">Šta sve mogu?</button></div>');})
 .catch(function(e){$('bridgeTag').textContent='bridge nedostupan';});
 call('/api/presets').then(function(p){PRESETS=p.presets;
  $('presets').innerHTML=p.presets.map(function(pr){return '<button class="pchip" onclick="demo(\''+pr.id+'\')"><span class="dot"></span>'+esc(pr.label)+'</button>';}).join('');});}
function help(){$('sessName').textContent='Pomoć';
 addUser('šta sve možeš?');
 addBot('<h4 style="margin:0 0 8px">Šta mogu da uradim?</h4>'
  +card('<p>• <b>Analiza</b>: priloži <code>.mid/.midi/.kar</code> — prepoznajem Pa800 markere (i1cv1…e2cv1), uloge kanala (bass/drums/perc/acc/solo), groove naspram pravih bubnjara, tehnike i echo/terca strukturu.</p>'
  +'<p>• <b>Plan</b>: svaka akcija ima status — <span class="badge READY">READY</span> (bezbedno), <span class="badge NEEDS_DECISION">ODLUKA</span>, <span class="badge LOCKED">ZAKLJUČANO</span>.</p>'
  +'<p>• <b>Primena</b>: označi READY akcije → izlaz u <i>novim</i> fajlovima (STY za Pa800, CC11 miks). Original se nikad ne menja.</p>'
  +'<p style="margin-bottom:0">• <b>Rečenice</b>: „analiziraj X”, „primeni A01 A02”, „primeni sve”, „pomoć”.</p>'
  +'<div class="sugg"><button onclick="newSession()">Nova sesija</button><button onclick="rulesInfo()">Licence i pravila</button></div>'));}
function rulesInfo(){$('sessName').textContent='Pravila';
 addUser('koja su pravila?');
 addBot('<h4 style="margin:0 0 8px">Invarijante (sve izvršeno, dokazano testovima)</h4>'
  +card('<p>⚙️ <b>Izvorni fajl se nikada ne menja</b> — sve primene pišu nove artefakte u <code>artifacts-max-*</code>.</p>'
  +'<p>🎛 <b>Velocity autoritet: FACTORY_ONLY</b>; bank/program se ne emituje bez dokaza.</p>'
  +'<p>🔒 <b>DNC/slap/pop trigeri zaključani</b> dok ne postoji device snimak (warrant ledger).</p>'
  +'<p>📋 Svaka akcija ima <i>reason</i> + <i>gates</i>; izveštaj je dokaz.</p>'
  +'<p style="margin-bottom:0">📜 <b>Licence</b>: MIT/Apache/BSD uz atribuciju (wobblemidi već ugrađen, sha256 zabeležen); GPL/AGPL/LGPL projekti (Cadenza, JJazzLab…) samo studija — ništa od njih nije u repou.</p>'));}
function newSession(){state={kind:null,payload:null,roles:'',file:null,lastPlan:null,thinking:false};
 $('sessName').textContent='Nova sesija';clearFile();
 wrap.innerHTML='';
 addBot('<p style="margin:0">Nova sesija. 👇 Priloži MIDI fajl ili izaberi demo iz menija.</p>');}
function pickedFile(){var f=$('file').files[0];if(!f)return;
 state.file=f;state.kind='upload';state.payload=null;$('sessName').textContent=f.name;
 $('ftName').textContent='📄 '+f.name+' ('+fmtBytes(f.size)+')';$('filetag').style.display='block';
 $('hint').textContent='Enter = analiziraj „'+f.name+'”';}
function clearFile(){$('file').value='';state.file=null;state.kind=null;$('filetag').style.display='none';
 $('hint').textContent='Prikači MIDI (drag &amp; drop ili 📎) pa Enter.';}
document.addEventListener('dragover',function(e){e.preventDefault();});
document.addEventListener('drop',function(e){e.preventDefault();
 var f=e.dataTransfer.files&&e.dataTransfer.files[0];if(!f)return;
 var dt=new DataTransfer();dt.items.add(f);$('file').files=dt.files;pickedFile();});
$('inp').addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();onSend();}});
function autosize(){var t=$('inp');t.style.height='auto';t.style.height=Math.min(120,t.scrollHeight)+'px';}
$('inp').addEventListener('input',autosize);
function onSend(){var t=$('inp').value.trim();if(!t&&!state.file)return;
 if(state.thinking)return;
 $('inp').value='';autosize();
 var cmd=t.toLowerCase();
 if(t)addUser(esc(t));
 if(/pomoć|help|sta sve|šta sve/.test(cmd)){help();return;}
 if(/pravila|licenc|invarijant/.test(cmd)){rulesInfo();return;}
 if(/analiziraj|obradi|analize/.test(cmd)){
  if(state.file){analyze(false,'');return;}
  var pr=PRESETS.filter(function(p){return cmd.indexOf(p.id)>=0||cmd.indexOf(String(p.file).split('/').pop().toLowerCase())>=0;})[0];
  if(pr){demo(pr.id);return;}
  addBot(card('<p>Za analizu: <b>priloži fajl</b> (📎 ili drag&amp;drop) ili napiši npr. <i>„analiziraj reference-style”</i>.</p>'));return;}
 if(/^primeni/.test(cmd)){
  var ids=[];var mm=cmd.match(/A0\d/g);if(mm)ids=mm;
  else if(/sve/.test(cmd)){var all=((state.lastPlan&&state.lastPlan.actions)||[]).filter(function(a){return a.status==='READY';}).map(function(a){return a.id;});ids=all;}
  if(!ids.length){addBot(card('<p>Nema označenih akcija iz plana. Prvo pokreni analizu, pa „primeni A01 A02” ili „primeni sve”.</p>'));return;}
  analyze(true,ids.join(','));return;}
 addBot(card('<p>Ne razumem baš. Probaj: <b>„analiziraj reference-style”</b> · <b>„primeni sve”</b> · <b>„pomoć”</b>. 🙂</p>'));}
function demo(id){var pr=PRESETS.filter(function(p){return p.id===id;})[0];if(!pr)return;
 state={kind:'preset',payload:id,roles:pr.roles||'',file:null,lastPlan:null,thinking:false};
 clearFile();$('sessName').textContent=pr.file;
 addUser('▶ demo: '+pr.file);analyze(false,'');}
function analyze(apply,actions){if(state.thinking)return;state.thinking=true;$('send').disabled=true;
 addThink();
 var go;
 if(state.kind==='preset'){var url='/api/sample-analyze?p='+encodeURIComponent(state.payload)+'&apply='+(apply?1:0);
  if(actions)url+='&actions='+encodeURIComponent(actions);go=call(url);}
 else if(state.file){var f=state.file;go=f.arrayBuffer().then(function(b){return call('/api/analyze',{method:'POST',
  headers:{'x-filename':f.name,'x-roles':state.roles,'x-apply':apply?'1':'0','x-actions':actions},body:new Uint8Array(b)});});}
 else{state.thinking=false;$('send').disabled=false;return;}
 go.then(function(j){state.thinking=false;$('send').disabled=false;rmThink();
  if(!j.ok||!j.report)throw new Error(j.pythonErrorTail||'python nije vratio izveštaj');
  state.lastPlan=j.report;
  if(apply)renderApplied(j);else renderPlan(j);})
 .catch(function(e){state.thinking=false;$('send').disabled=false;rmThink();addBot(card('<p style="color:#f1948a">Greška: '+esc(e&&e.message||e)+'</p>'));});}
function planFacts(r){var f=r.fileFacts;
 var mk=(f.markers&&f.markers.length)?f.markers[0]+'…':'-';
 return '<div class="facts">'
 +'<div class="fc"><b>'+esc(f.markerCount)+'</b><span>markera</span></div>'
 +'<div class="fc"><b>'+esc(f.channels.length)+'</b><span>kanala</span></div>'
 +'<div class="fc"><b>'+esc(f.ppq)+'</b><span>ppq</span></div>'
 +'<div class="fc"><b>SMF '+esc(f.format)+'</b><span>format</span></div>'
 +'<div class="fc"><b>'+esc(mk)+'</b><span>prvi marker</span></div></div>';}
function roleTable(r){var rows=Object.entries(r.perRolePatterns||{}).map(function(e){var b=e[1],v=b.velocity||{},mp=b.melodyPattern||{};
 var mel=(mp.meanAbsSemis!=null)?'<br><span style="color:var(--muted)">mel: up '+esc(mp.upShare)+' · interval '+esc(mp.meanAbsSemis)+' semisa</span>':'';
 return '<tr><td><b>'+esc(b.role)+'</b></td><td>ch'+esc(e[0])+'</td><td>'+esc(b.noteCount)+'</td><td>'+esc((b.register||[]).join('–'))+'</td><td>'+esc(b.densityNotesPerBar)+'</td><td>'+(v.q50!=null?esc(v.q50):'–')+'</td><td>'+mel+'</td></tr>';}).join('');
 return '<table><tr><th>uloga</th><th>kanal</th><th>note</th><th>registar</th><th>nota/takt</th><th>vel q50</th><th>melodija</th></tr>'+rows+'</table>';}
function grooveTable(r){if(!((r.grooveVsHuman||[]).length))return '';
 return '<div style="margin-top:8px"><span style="color:var(--muted);font-size:11px">Groove vs ljudska referenca (pravi bubnjari: std ≈ 27,96 ms)</span><table><tr><th>kanal</th><th>note</th><th>std ms</th><th>na gridu</th></tr>'
 +r.grooveVsHuman.map(function(g){return '<tr><td>ch'+esc(g.channel)+' '+esc(g.role)+'</td><td>'+esc(g.noteCount)+'</td><td>'+esc(g.stdMs)+'</td><td>'+Math.round((g.exactOnGridShare||0)*100)+' %</td></tr>';}).join('')+'</table></div>';}
function countReady(r){return (r.actions||[]).filter(function(a){return a.status==='READY';}).length;}
function renderPlan(j){var r=j.report;
 var actRows=(r.actions||[]).map(function(a){
  var cb=(a.status==='READY')?'<input type="checkbox" data-id="'+esc(a.id)+'" checked>':'<input type="checkbox" disabled>';
  var gates=a.gates?'<details><summary>gates</summary><pre>'+esc(JSON.stringify(a.gates,null,1))+'</pre></details>':'';
  return '<label><span style="padding-top:2px">'+cb+'</span><span style="flex:1"><span class="a-id">'+esc(a.id)+' '+badge(a.status)+'</span>'
   +'<span class="a-e"> · '+esc(a.engine)+'</span><span class="a-x">'+esc(a.effect||a.reason||'')+'</span>'+gates+'</span></label>';}).join('');
 var html='<h4 style="margin:2px 0 4px">Plan optimizacije — '+esc(r.sourceName)+'</h4>'
  +planFacts(r)
  +card('<div class="actrow-head"><b style="font-size:13px">Akcije — označi šta da primenim</b><span style="color:var(--muted);font-size:11px">'+countReady(r)+' spremnih</span></div>'
   +'<div class="actions">'+actRows+'</div>'
   +'<div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">'
   +'<button class="btn" onclick="applySel()">Primeni izabrane ('+countReady(r)+')</button>'
   +'<button class="btn ghost" onclick="applyAllReady()">Primeni sve READY</button></div>');
 addBot(html);
 addBot(card('<div class="actrow-head"><b style="font-size:13px">Role patterni</b><span style="color:var(--muted);font-size:11px">+ melodijski profil za solo</span></div>'+roleTable(r)+grooveTable(r)));
 addBot(card('<details open><summary>sirovi izveštaj (JSON · Session Pass 4.60)</summary><pre>'+esc(JSON.stringify(r,null,1))+'</pre></details>'));
 window.__boxes=function(){var c=[].slice.call(document.querySelectorAll('.actions input[type=checkbox]:checked'));
  return c.map(function(i){return i.getAttribute('data-id');});};}
function applySel(){var ids=window.__boxes?window.__boxes():[];
 if(!ids.length){addBot(card('<p>Označi bar jednu READY akciju.</p>'));return;}analyze(true,ids.join(','));}
function applyAllReady(){var r=state.lastPlan;if(!r)return;
 var ids=(r.actions||[]).filter(function(a){return a.status==='READY';}).map(function(a){return a.id;});
 if(!ids.length){addBot(card('<p>Nema READY akcija.</p>'));return;}analyze(true,ids.join(','));}
function renderApplied(j){var r=j.report;
 var arts=(r.actions||[]).filter(function(a){return a.artifact&&a.status==='APPLIED';});
 var html='<h4 style="margin:0 0 6px">✓ Primenjeno</h4>'
  +card('<span class="okline">✔ '+esc(r.sourceName)+'</span><div style="margin-top:10px">'
  +(arts.length?arts.map(function(a){return '<a class="dl" href="/api/artifacts/'+esc(a.artifact.split('/').pop())+'" download>⬇ '+esc(a.artifact.split('/').pop())+'</a>';}).join('')
    :'<span style="color:var(--muted)">nema primenjenih artefakata</span>')+'</div>'
  +'<div style="margin-top:8px">'+(r.actions||[]).map(function(a){return esc(a.id)+' '+badge(a.status);}).join(' &nbsp; ')+'</div>');
 addBot(html);
 addBot(card('<p style="margin:0;color:var(--muted)">ℹ Izvorni fajl nije menjan — ovo su <b>novi</b> artefakti. <code>session-sty-*.mid</code> = Pa800 izvoz, <code>session-mixed-*.mid</code> = CC11 balans. Učitaj na klavijaturu i proveri.</p>'));}
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
    return json(res, 200, { ok: true, version: '4.63', python: PY, outDir: OUT_DIR,
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
