# GUI → dokazi svih uloga (i sola) — 4.59 (vodič, ne dokaz)

Lanac koji je ovde dokazan izvršeno (artefakti: `artifacts-max-4.59/`,
`reports-max-4.59/`):

```
React GUI (src/musicAnalysis.js, analyzeUploadedFile)
   │  gui_chain_4.59.mjs (pravi GUI ulaz, ista funkcija koju app koristi)
   ▼
artifacts-max-4.59/gui-analysis-*.json     ← GUI analiza: note/kanali/tempo/
   │                                           key/chords/markeri/score
   ▼
dna_midi_studio/role_patterns.py (Role Pattern Evidence Engine)
   │  isti fajlovi + mapa kanal→uloga iz evidencije aranžmana
   ▼
artifacts-max-4.59/role-patterns-*.json    ← izmereni patterni po ulozi
artifacts-max-4.59/role-patterns-corpus.json ← agregat po ulogama (korpus)
```

## Šta je izmereno po ulozi (sve iz bajtova, ništa se ne menja)

| uloga | metrike |
|---|---|
| bass | noteCount, registar, gustina/takt, velocity (q20/q50/q80), gate, dužine, onset gap, polyphony peak, kratke/grace note |
| drums / percussion | iste baze + gustina po taktu (provereno i groove engine-om 4.54) |
| accompaniment | iste baze na svakom akordnom kanalu (ch11–15) |
| **solo** | baza + **melody pattern**: up/down/same share, srednji interval (semis), chromatic/third/leap share, broj fraza, dužina fraze u notama, pokriveni taktovi |

## Izmerene činjenice (2026-09-05)

Style fajlovi (reference-style, arranged-fixture, session35):
- **bass**: 3 kanala, 351 nota, registar 28–55, gustina ~5–7/takt, srednja
  jačina 116–117;
- **drums**: 3 kanala, 879 nota, gustina 12–17/takt, jačina ~113;
- **percussion**: 3 kanala, 995 nota, gustina 11–27/takt (ch10 reference =
  26,8 — najgušći sloj);
- **accompaniment**: 14 kanala (5+5+4), 3.028 nota, registar 43–96, gustina
  5,3–20,7/takt, srednja jačina 72–123 (različiti akordni karakteri: pad vs
  komp vs akcent — vidi po-kanalne artefakte).

Song19 benchmark korpus (20 pesama, Lead Solo na ch2):
- **solo**: 20 fajlova / 320 nota; registar i fraze po pesmi;
  **melodijski profil (medijan kroz korpus)**: srednji interval 5,33 semisa,
  kretanje nagore 53 % / nadole 40 %, bez hromatike, terce 13 %, skokovi ≥7
  20 % → fabrički solo je „skakutav" (intervali 4–7), dijatonski, fraziran;
- **bass**: 320 nota (16/pesma), **drums**: 640 (32/pesma),
  **accompaniment**: 1.080 (54/pesma).

## Kapije i poštenje

- GUI analiza je READ-ONLY; role_patterns je READ-ONLY — ništa se ne upisuje
  u izvorne fajlove (sha256 svakog izvora u artefaktu);
- role mape su eksplicitne (iz evidencije 4.51 za style fajlove; iz fixture
  specifikacije `session19_fixture.py` za song19: ch0 Harmony, ch1 Bass,
  ch2 Lead Solo, ch9 Drums) — engine ne nagađa uloge;
- solo „pattern" je mera postojeće fabrike, ne generisani redosled nota.

## Dokazi

- `artifacts-max-4.59/gui-run.json` + 5× `gui-analysis-*.json`;
- `artifacts-max-4.59/role-patterns-*.json` (za svaki od 23 fajla) +
  `role-patterns-corpus.json`;
- `reports-max-4.59/01-gui-chain-summary.json`, `02-tests.json`;
- `test_role_patterns_v459.py` (7 testova); GUI stress `npm test` 7/7;
  puna Python suita 87 + legacy 5 OK.

## Reprodukcija

```bash
node gui_chain_4.59.mjs
python3.11 dna_midi_studio/role_patterns.py --input baseline/reference-style.mid \
  --roles 8:bass,9:drums,10:percussion,11:accompaniment,12:accompaniment,13:accompaniment,14:accompaniment,15:accompaniment
python3.11 -m unittest test_role_patterns_v459
npm test
```
