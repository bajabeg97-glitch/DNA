# DNA Studio GUI 4.71 + Windows launcheri (priprema za S3)

Miljokaz: **4.71** · datum: 2026-09-06 · grana: `arena/01a06f02-dna`

## Šta je urađeno

### 1. GUI je potpuno redizajniran — DNA Studio

Stara „AI Studio” kartica (chat + Session Pass) je zamenjena višepanelnim
**DNA Studio** GUI-jem, koji se servira iz zasebnog fajla `dna_studio_ui.html`
(bridge ga čita pri startu; više nema 300+ linija HTML-a u `dna_bridge.mjs`).

Paneli (levi meni):

| Panel | Šta radi | Endpoint |
|---|---|---|
| 💬 **Studio — analiza** | Session Pass chat: upload/demo MIDI, plan sa kapijama, primena akcija, asistent 4.68 (sačuvane sesije) | `/api/analyze`, `/api/sample-analyze`, `/api/session`, `/api/assistant` |
| 🎹 **Kompozitor (4.70)** | izbor 10 stilova (bpm/takt iz `composer_engine.py`), semena, humanize on/off → pesme `.mid` + scorecard | `GET /api/compose/info`, `GET /api/compose/list`, `POST /api/compose` |
| 🧠 **Model trening (S3)** | **sve opcije treninga modela**: arhitektura (n-gram / mikro-GPT), red n-grama, smoothing, kontekst, epohe, seed, CPU niti, device (CPU-only), izvor korpusa, limit primera, checkpoint izlaz | `GET /api/model/status`, `POST /api/model/train` |
| 🗂 **Korpus** | statistika komitovanog S1 korpusa (779 predmeta / 843.545 tokena) + obnova korpusa u workspace (seed/sintetički/real limit) | `GET /api/corpus/status`, `POST /api/corpus/build` |
| 📦 **Fajlovi / artefakti** | pregled i preuzimanje svega generisanog (workspace-4.71 + Session Pass izlazi) | `GET /api/ws/list`, `GET /api/ws/file`, `/api/artifacts/<name>` |
| ⚖ **Pravila i verzije** | invarijante, licence, verzije engine-a, model plan (S3 → D2) | `GET /api/health` |

Iskrenost oko S3: `/api/model/status` javlja `ready:false` dok
`dna_midi_studio/model_train.py` ne postoji (stiže sa S3 slojem u sledećem
koraku istog lanca). GUI panel već sadrži sve opcije, a `/api/model/train`
ih **server-side validira** (clamp opsega, odbijanje `cuda` — samo CPU) i
vraća struktuirani `not_ready` odgovor sa echom parametara. Kad S3 modul
legne, isto dugme/launcher pokreće trening bez izmene GUI-ja.

### 2. Novi .bat launcheri (svi CRLF, čist ASCII, bez tabova)

- `compose-songs.bat` — DNA Kompozitor; bez argumenata pravi svih 10 stilova
  × seed 1,2,3 (30 pesama); podržava `--styles`, `--seeds`, `--no-humanize`;
  izlaz uvek u `workspace-4.71\songs-4.70` osim ako se prosledi `--out`.
- `build-corpus.bat` — obnova korpusa 4.69 u `workspace-4.71\corpus-4.69`.
- `validate-songs.bat` — validator suita `v469 + v470` (pesme/korpus).
- `model-train.bat` — S3 trening launcher; dok modul ne postoji ispisuje
  iskreni INFO („stiže sa S3 slojem”) — GUI Model kartica je spremna.
- Ažurirani: `start-bridge.bat` (otvara browser na `http://localhost:8123`),
  `run-tests.bat` (kurirana lista sada pokriva 4.67→4.71), `setup-windows.bat`
  (najavljuje svih 9 launcher-a).

Ukupno **9 .bat fajlova**: 5 iz 4.66 + 4 nova (4.71).

### 3. Workspace pravilo

Sve što GUI/launcheri generišu ide u **`workspace-4.71/`** (gitignorisano):
pesme, scorecards, korpus obnove, checkpoints. Komitovani miljokaz artefakti
(`artifacts-max-4.6x/4.7x`) se nikad ne diraju iz GUI-ja.

## Testovi (4.71)

- Python core: **226 OK (3 skipped)** — 21 fajl, od `v449` do `test_windows_bats_v471` (17 novih testova).
- Python legacy: `test_max_registry_v448` **5 OK**.
- Node (`npm test`): **29/29 OK** — nova suita `tests/gui-4.71.test.mjs` (8 testova) pokriva
  UI markere, `/api/compose` (prava kompozicija rock/seed 7 → .mid + scorecard + download),
  `/api/corpus/status` (779 predmeta), `/api/model/*` (iskreni not_ready + validacija opcija).

Evidencija: `reports-max-4.71/01-bats-manifest.json` (sha256 svih 9 .bat)
i `reports-max-4.71/02-tests.json`.

## Sledeći korak (S3 / 4.71 nastavak)

Statistički sloj (`dna_midi_studio/model_train.py`): n-gram/Markov nad
843.545 tokena, pa mikro-GPT opcija (numpy, ≤ ~2M parametara) — GUI opcije i
`model-train.bat` su već spremni i samo čekaju modul. Kapija D2: slepo A/B
korisnika (deterministički vs model).
