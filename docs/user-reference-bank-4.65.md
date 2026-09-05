# 4.65 — Tvoje reference klasifikovane kao korpus + prepoznavanje u full upotrebi

Datum: 2026-09-05 · Status: `dna-user-reference-status-4.65.json` · Iskreni izveštaj: `docs/honest-report-4.65.md` + `dna-honest-report-4.65.json`

## Šta je urađeno

Tvoje tri glavne reference sada prolaze kroz **isti kanonski 16-step format** kao preuzeti korpus iz 4.64:

| Fajl | Taktova | Markera | Različitih motiva | Poklapanja sa 468 korpus |
|---|---|---|---|---|
| `baseline/reference-style.mid` | 22 | 10 (i1cv1…e2cv1) | 51 | 15 |
| `session35-partial-preview.mid` | 18 | 10 | 76 | 8 |
| `song19-benchmark/song19-01.mid` | 8 | 4 | 2 | 2 |
| **Ukupno** | **48** | | **129** | **25** |

Modul `dna_midi_studio/user_reference_bank.py` (čist stdlib):
- `bars_of()` — kvantizuje kanale 9/10 u 16-step redove po taktu (4/4, poravnato na tick 0), sa elementom (marker) po taktu;
- `build_bank()` — banka sa sha256 svakog fajla, markerima, bar rows, motifima i corpus-match-evima;
- `classification_report()` — **iskreno razdvaja** redove koji se samo ponavljaju unutar fajla (`rowsRepeatedOverall` = 54) od redova **stvarno deljenih između tvojih fajlova** (`rowsSharedAcrossFiles` = 6), npr.:
  - snare backbeat `0000100000001000` → `reference-style.mid` + `song19-01.mid`
  - kick `1000100010001000` → `reference-style.mid` + `session35-partial-preview.mid`

## Full use — prepoznavanje u Session Pass-u i UI-ju

- `session_pass()` sada **svaki** izveštaj završava sa `patternRecognition` sekcijom (`artifacts-max-4.65/session-pass-reference-style.json` je primer): koliko taktova analizirano, koji redovi se poklapaju sa korpusom (sa naslovima iz knjiga), koji redovi se poklapaju sa tvojim referencama (sa imenima fajlova — uključujući i sam fajl ako je on u banci, da se vidi).
- Bridge UI (4.65) u plan kartici prikazuje **„Prepoznavanje obrazaca (4.65)”**: badge-eve sa naslovima (npr. `Patterns_200:Rock1MeasureB`) ili poštenu poruku „nema tačnih poklapanja” za swing/human kanale.
- Deterministički digest na svakoj sekciji; akcije/gates se ne menjaju (test).

## Primeri prepoznavanja (recognition-overview.json)

- `reference-style.mid` — kick `1000001010100010` = **Rock1MeasureB / Rock7**; snare/cymbals pogodci Afro-Cuban/Ballad familije…
- `session35-partial-preview.mid` — Disco/Ballad familije (Disco1MeasureA…)
- `song19-01.mid` — Afro-Cuban familija (kick/snare generični redovi)
- `arranged-4.51-fixture.mid` — isti bubanj kao session35 (fixture je DNA-aranžman session35, pa je to očekivano)

## Granice (pošteno)

Tačno 16-step, 4/4, GM note, kanali 9/10, **bez swing tolerancije**: tvoj perc kanal (ljudski swing, ~28% na gridu) i note van GM opsega (reference-style ima pitch 16–21 na ch9/10) se bankuju ali se ne poklapaju sa korpusom — očekivano i dokumentovano u iskrenom izveštaju (stavke 2.5, 3.9). Banka sadrži 3 fajla; song19-02…20 mogu da se dodaju na zahtev.

## Testovi i dokazi

- `test_user_reference_bank_v465.py` — 15 testova (pinned brojevi po fajlu, 16-step šema, determinizam, iskreno deljeni redovi, session-pass integracija bez promene akcija, stdlib-only).
- `artifacts-max-4.65/user-reference-bank.json`, `user-reference-classification.json`, `recognition-overview.json`, `session-pass-reference-style.json`.
- Cela suita: Python 133 (102+16+15) OK, legacy 5 OK, npm 14/14 OK.
