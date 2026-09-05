# Ljudski groove dokazi — 4.54

Svrha: izmeriti *kvantitativne činjenice* o bubnjarskom/percussion sloju u našim
fabričkim i aranžiranim fajlovima i uporediti ih sa statistikom pravih bubnjara.
Nijedna nota se ne menja — modul meri i izveštava.

## 1. Odakle ljudska referenca

Izvorni korpus za realne bubnjarske performanse je **Magenta Groove MIDI
Dataset** (Google, CC-BY-4.0, ~1.150 snimaka pravih bubnjara). Njegovi sirovi
zipovi i HF/archive.org ogledala su **nedostupni iz ove sandbox okoline**
(2026-09-05; `storage.googleapis.com`, `huggingface.co`, `archive.org`,
`raw.githubusercontent.com` ne vraćaju sadržaj; samo `github.com` API i
`codeload.github.com` rade).

Zato je ljudska referenca u repou **derivirana statistika**, a ne sirovi
korpus:

- izvor: `JakebGutierrez/wobblemidi` (MIT), `wobblemidi/profiles/rock.json`;
- fajl sadrži 311 distribucija sa 422.650 redova uzoraka
  `(offset_ms, velocity-residual)` po instrumentu / `beat|fill` / tier / poziciji
  u 16-tini; autor u README-u i `_meta` navodi da su distribucije naučene sa
  Magenta Groove MIDI Dataset-a (pravi bubnjari);
- `global` grupa (bez tier/pozicije): **110.313 uzoraka**, kick/snare/hihat/
  ride/crash/tom-ovi;
- sha256 izvornog fajla i datum preuzimanja upisani su u
  `baseline/human-groove-rock-derived.json` (provenijencija se može proveriti).

### Ključni ljudski brojevi (izvedeni, globalno)

| metrika | vrednost |
|---|---|
| uzoraka | 110.313 |
| mean offset | −2,53 ms (lagano ispred grida) |
| **std offset** | **27,96 ms** |
| p5 / p95 | −48,1 / +46,9 ms |
| 16-tinske pozicije | kick mean od −20,3 ms (poz. 11) do +24,3 ms (poz. 1); snare je mirniji, ≈ −6…+8 ms |

## 2. Šta merimo kod nas

`dna_midi_studio/groove_engine.py`, `drum_groove_stats(raw, channel)`:
- grid = 16-tina (PPQ/4 ticka), offset svakog nastupa od najbližeg grid ticka;
- pretvorba u ms preko tempo meta samog fajla (default 120 BPM kad nema);
- po pitch-u: count, velMean/min/max, udeo akcenata ≥ 110;
- gustina (nota po taktu), `timingMs` (n/mean/std/p5/p50/p95 + udeo tačno na gridu);
- mean offset po 16-tinskoj poziciji u taktu.

## 3. Izmerene činjenice (2026-09-05, izvršeno)

| fajl | ch | note | gustina/takt | std ms | tačno na gridu |
|---|---|---|---|---|---|
| arranged-4.51-fixture | 9 | 308 | 17,1 | 7,4 | 28,9 % |
| arranged-4.51-fixture | 10 | 203 | 14,5 | 12,6 | 22,7 % |
| reference-style | 9 | 263 | 12,0 | 6,6 | **98,9 %** |
| reference-style | 10 | 589 | 26,8 | 22,2 | 28,2 % |
| **ljudska referenca** | — | — | — | **28,0** | — |

Čitanje:
- fabrički bubanj (ch9) u `reference-style.mid` je praktično kvantizovan
  (98,9 % tačno na gridu, std 6,6 ms dolazi od kratkih flam/grace pomaka);
- fabrički **percussion (ch10)** u istom fajlu već nosi ljudsku razmeru
  nestalnosti (std 22,2 ms — pravi bubnjari 28 ms), dakle taj sloj nije čisto
  mašinski;
- aranžirani 4.51 fajl: oba kanala su između (std 7,4 / 12,6 ms) — sada se to
  vidi kao broj, ne kao utisak;
- naši bubnjevi su po dizajnu kvantizovani (FACTORY_ONLY: nota se ne menja),
  pa je jaz prema ljudskoj referenci **očekivano stanje, sada dokumentovano**,
  a ne kvar.

## 4. Izvršni dokazi

- `artifacts-max-4.54/groove-ch{9,10}-{arranged-4.51-fixture,reference-style}.json`
  — kompletna merna evidencija po kanalu (uključuje ljudsku referencu i tabelu
  poređenja);
- `reports-max-4.54/01-groove-summary.json` — sažetak svih kanala;
- `reports-max-4.54/02-tests.json` — rezultati testova;
- `test_groove_engine_v454.py` — 9 testova (provenijencija, razmere, pozicione
  sredine, merenja fajlova, poređenje, artefakt).

## 5. Ponovljivost

```bash
python3.11 dna_midi_studio/groove_engine.py \
  --input artifacts-max-4.51/arranged-4.51-fixture.mid --channel 9 --out-dir artifacts-max-4.54
```

Proveru autentičnosti izvorne distribucije omogućava `sourceSha256` u
`baseline/human-groove-rock-derived.json` — bez ponovnog preuzimanja.
