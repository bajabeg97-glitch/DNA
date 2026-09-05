# Instrumentalne tehnike — dokazi 4.55 (slap / pop / ghost / palm-mute)

Engine: `dna_midi_studio/instrument_techniques.py`. On **meri i klasifikuje**,
ne menja ni bajt ulaza i **nikada ne emituje trigere** (bez device snimka
`slap-trigger`/`pop-trigger`/guitar-mode ostaju zabranjeni tvrdim pravilima
profila). Klase se biraju isključivo iz rečnika koji već postoji u
`complete-instrument-profiles-4.44.json` — engine ne izmišlja nove nazive
tehnika.

## Kako klasifikuje

- pragovi su **relativni prema samom kanalu** (kvantili njegovih factory
  vrednosti): accent ≥ Q80, ghost ≤ Q20, kratko = gate < 0,45 (trajanje /
  razmak do sledećeg nastupa), dugo = gate ≥ 0,60;
- **bas (ch8)**: kratak+akcenat u niskom registru (< Q33 pitch) →
  `slap-candidate`; kratak+akcenat u visokom registru (> Q66 pitch) →
  `pop-candidate`; kratak+tiho → `ghost-candidate`; ostalo → merne oznake
  (`short-mid`, `sustain`, `short-accent-mid`) koje NISU profilne tehnike;
- **ritam gitara/kordovi (ch12)**: grupa nota na istom tick-u = udar;
  kratak udar sa srednjom/nižom jačinom → `palm-mute-candidate`; samo jedan
  ton + kratko → `single-string-candidate`; više tonova + dugo →
  `open-strum`.

## Izmerene činjenice (2026-09-05, izvršeno)

| fajl | uloga | ch | ukupno | tehnike (kandidati) |
|---|---|---|---|---|
| arranged-4.51-fixture | bas | 8 | 122 | pop 25, ghost 24, short-mid 34, sustain 30, slap 3 |
| arranged-4.51-fixture | ritam gitara | 12 | 228 udara | single-string 53, open-strum 24, palm-mute 7 |
| reference-style | bas | 8 | 107 | sustain 49, ghost 16, slap 5, pop 2 |
| reference-style | ritam gitara | 12 | 222 udara | single-string 100, open-strum 25, palm-mute 9 |

Čitanje:
- fabrika svira bas sa dosta **kratkih tonova** (fixture: >50 % kanala je
  kratko), što je upravo materijal iz kog bi se *generativno* gradile
  slap/pop fraze — ali za sada se to samo **dokazuje**, ne piše;
- `pop-candidate` u fixture basu (25) je dokaz da fabrika već akcentuje
  visoke kratke tonove (pop gest), dok je `slap-candidate` redak (3);
- u oba fajla ritam gitara ima 3–4 % udara koje evidencija čita kao
  palm-mute kandidate — fabrika dakle već pravi mute kontrast, a engine to
  sada vidi kao broj.

## Warranty (šta sme da postane stvarni triger)

Iz `warrantLedger` artefakata: svaka tehnika ima `emissionState:
SEMANTIC_ONLY`; `slap-trigger`/`pop-trigger`/`guitar-mode-trigger` imaju
`requiresDeviceEvidenceBeforeTrigger: true` i `deviceEvidenceCaptured: false`
(nijedan uređaj nije snimljen u ovom repou). Zato je 0 trigera, 0 CC, 0
keyswitch, 0 promena nota — kapije to dokazuju u svakom artefaktu.

## Dokazi

- `artifacts-max-4.55/techniques-{role}-ch{ch}-*.json` (4 fajla: po jedan za
  bas i ritam kanal u fixture i reference fajlu; sadrže counts, primere po
  klasi, sound evidence (cc0/cc32/program), warrant ledger, kapije);
- `reports-max-4.55/01-techniques-summary.json` (sažetak svih izvršavanja);
- `reports-max-4.55/02-tests.json` (test rezultati);
- `test_instrument_techniques_v455.py` — 12 testova (rečnik, zabrane,
  counts, kapije, warranty).

## Ponovljivost

```bash
python3.11 dna_midi_studio/instrument_techniques.py \
  --input baseline/reference-style.mid --channel 8 --role bass \
  --out-dir artifacts-max-4.55
```

Sledeći korak ka 5.0: kada generativna gramatika bude gradila fraze, sme da
koristi samo tehnike čiji je kandidat dokazan na tom kanalu i samo u prazne
slotove; trigeri ostaju zaključani do pravog device snimka.
