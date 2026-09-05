# 4.64 — GitHub najjači projekti: sken + dozvoljeno kopiranje (patterns/datasets)

Datum: 2026-09-05 · Status: `dna-github-copy-status-4.64.json` · Evidence: `reports-max-4.64/`, `vendor-max-4.64/`, `artifacts-max-4.64/`, `test_pattern_library_v464.py`

## Šta je rađeno

1. **Sken** 9 GitHub upita (`topic:midi`, `topic:drum-patterns`, `topic:midi-dataset`, `midi drum patterns`, `midi dataset`, `arranger style midi`, `topic:groove`, `midi python`, `drum machine patterns`) → **95 unikatnih repoa** sa zvezdama, licencama i opisima; + 7 „velikih” referenci (magenta 19.8k★ Apache-2.0, music21 2.6k★ BSD-3, mido 1.6k★ MIT, pretty-midi MIT, JZZ MIT, Tone.js MIT…). Ceo spisak: `reports-max-4.64/01-github-top-scan.json`.
2. **Politika kopiranja** (poštovana, stoji od ranije): MIT/Apache/BSD/CC0/Unlicense → kopira se uz atribuciju; GPL/AGPL/LGPL i CC BY-NC-* → samo studija.
3. **Odabir za kopiju**: `gvellut/dmp_midi` (MIT) — digitalne transkripcije bubnjarskih obrazaca iz knjiga **„200 Drum Machine Patterns”** i **„260 Drum Machine Patterns”** (René-Pierre Bardet): **200 + 268 = 468 patterna** u JSON-u (16- ili 12-step grid po imenovanom delu: BassDrum, SnareDrum, ClosedHiHat…, plus accent linija).

## Šta je prekopirano i kako je integrisano

| Šta | Gde | Licenca | sha256 manifest |
|---|---|---|---|
| LICENSE, README, input/*.json (2 korpusa) + input/README | `vendor-max-4.64/dmp-midi/` | MIT (verbatim, bez izmena) | `manifest.json` + NOTICE.md |

Na vrhu korpusa napravljen **naš** stdlib modul `dna_midi_studio/pattern_library.py` (bez mido/numpy/torch — čist stdlib):

- validacija i normalizacija svih 468 patterna (tokeni Note/Rest/Accent/Flam; kanonske „1010…” linije; GM note + role kick/snare/hats/toms/cymbals/percussion; accent linije);
- determinističke statistike + digest (468 patterna: 4/4 ×429, 12/8 ×30, 3/4 ×9; 16-step ×390, 12-step ×78; ukupno hitova po delu: ClosedHiHat 2209, BassDrum 1711, SnareDrum 1443, Cymbal 407…);
- **prepoznavanje obrazaca**: kvantizacija pravog MIDI bubanj kanala na 16-step liniju i tačan lookup po korpusu. Na našim fajlovima:
  - `reference-style.mid` → kick linija `1000001010100010` = **Rock1MeasureB / Rock7**; cymbal = EndingsMeasureA / AfroCubBreak2…;
  - `arranged-4.51-fixture.mid` → snare `1000100010001000` = **Rock4MeasureB / Rhythm&Blues2MeasureB / Pop11**;
  - `song19-01.mid` i `session35-partial-preview.mid` → takođe tačni pogodci (detalji u `dmp-pattern-matches-demo.json`).

## Zašto ostali „najjači” nisu kopirani (sa razlogom)

- `scribbletune/pydrums` (MIT): generativni deo traži torch/ollama; patterni dupliraju dmp korpus.
- `ideoforms/isobar` (MIT, 431★): odlična pattern biblioteka, ali paket vuče mido/rtmidi/numpy — engine je mido-free; ostaje referenca.
- `realsigmamusic/openarranger` (MIT): najbliži MIT aranžer (Mains/Fills/Intros/Endings/Breaks — ista arhitektura kao Pa800 elementi); 37MB sa asset-ima, patterni iz fajlova → referenca za arhitekturu.
- `asigalov61/Tegridy-MIDI-Dataset` (Apache-2.0): ~1GB repo, a .zip dataset-i unutra su **CC-BY-NC-SA** — mešovite licence → preskočeno.
- `MarkCWirt/MIDIUtil`: GitHub ne prepoznaje licencu (NOASSERTION) → nije proverljivo.
- `stephenhandley/DrumMachinePatterns`: **nema LICENSE** → preskočeno direktno (korpus je uzet preko MIT repoa koji ga je transkribovao).
- GPL familija (Cadenza, Favorites-with-Style, Yamaha-Style-Studio, helio, LMMS, crbm-drum-patterns…): study-only.
- CC BY-NC dataset-i (MAESTRO, Groove MIDI…): non-commercial → ne kopira se.

## Dokazi

- `vendor-max-4.64/dmp-midi/` — LICENSE + NOTICE + manifest.json (sha256 svih fajlova, pin na upstream commit `81f926a`)
- `artifacts-max-4.64/dmp-library-stats.json`, `dmp-patterns-normalized.json`, `dmp-pattern-matches-demo.json`
- `test_pattern_library_v464.py` — 16 testova: MIT allow-list, sha256 svih vendored fajlova, NOTICE, 468 patterna, šema, distribucija, determinizam, round-trip lookup, probe na pravim fajlovima, stdlib-only
- `reports-max-4.64/01-github-top-scan.json` + `02-copied.json` + `03-tests.json`
