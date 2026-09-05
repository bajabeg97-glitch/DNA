# Arranger Pro 4.49 — vodič

> **Cilj:** aranžer koji zaista poznaje svaki instrument — kako svira, koji registar
> koristi, kako diše (gate), koliko glasova kanal sme imati — i koji to znanje
> primenjuje: **Strumming**, **Best Instruments**, **Headroom** i još produkcijskih stavki.
> Status: **ACTIVE_OPT_IN**, dodatno uz MAX 4.48 (ne dira produkcijski tok).

## Šta aranžer sada zna o instrumentima

`dna_midi_studio/arranger_pro.py` + `complete-instrument-profiles-4.44.json` (katalog „musician profile"):
svaki kanal pesme dobija **intelligence brief**:

| Kanal | Uloga | Player model | Tehnike (kako svira) |
|---|---|---|---|
| ch8 | bass | bassist | root, fifth, octave, third |
| ch9 | drums | drummer | kick-pocket, backbeat, ghost, hat-subdivision |
| ch10 | percussion | percussionist | interlock, offbeat-color, pickup, sparse-fill |
| ch11–15 | accompaniment | arranger | stab, sustain, voice-lead, answer |

Uz to — **zapaženo stanje** po kanalu (registar, prosečan gate ratio, dinamika) da
aranžer uporedi part sa prirodom instrumenta.

## Urađene stavke (sve rade, dokazano na `session35-partial-preview.mid`)

1. **Strumming** — Factory-strum ritam-gitara (iz `factory-strumming.json` preko
   `PerformanceDNAEngine`, anti-copy kapija sa varijantnim fallback-om), glasovi po
   akordima pesme, **registar 48–72**, velocity isključivo iz Factory krive.
2. **Best Instruments** — savetodavno rangiranje fabričkih zvukova po ulozi kanala
   (banka/program po evidenciji uzoraka); **bez** automatske program change
   (pravilo `NO_UNVERIFIED_BANK_PROGRAM_CHANGE`).
3. **Headroom** — provera polifonije po kanalu prema PA800 limitima
   (npr. ch15 → max 3 glasa) + dinamički headroom do 127; aranžer **svoje** glasove
   sam ograničava na budžet, a postojeći materijal samo prijavljuje.
4. **Još stavki koje rade:** register-fit (48–72), dynamics iz isključivo Factory
   krive (strong/highMid/optimal), odbijanje zauzetog kanala, zaštita ostalih
   kanala (diff), reparse MIDI-ja.
5. **Buduće stavke (matrica):** comping po sekcijama, call-response fill-ovi,
   voice-leading kroz promene akorda, bass/chord register separation, style-format
   izvoz sa CV markerima.

## Rezultat vožnje (fixture, ch15, taktovi 1–18)

- Strum part: **410 nota**, peak polifonija **3 = budžet**, register 48–72,
  velocityMax 114 → **headroom 13** do 127.
- Sve kapije: reparse ✓, ostali kanali nepromenjeni ✓, factory-only velocity ✓,
  kanal u limitu ✓.
- MIDI: `artifacts-max-4.49/arranged.15.bar1-18.mid`, izveštaj:
  `artifacts-max-4.49/arranger-run-4.49.json`, dokazi agenata: `reports-max-4.49/01..08`.

## Komande

```bash
# Brief — kako instrumenti sviraju u fajlu
python3.11 dna_midi_studio/arranger_pro.py brief --input session35-partial-preview.mid

# Best instruments (advisory)
python3.11 dna_midi_studio/arranger_pro.py instruments --input session35-partial-preview.mid

# Strum part za kanal 15, taktovi 3–6
python3.11 dna_midi_studio/arranger_pro.py strum --input session35-partial-preview.mid \
    --target-channel 15 --start-bar 3 --end-bar 6

# Headroom audit
python3.11 dna_midi_studio/arranger_pro.py headroom --input session35-partial-preview.mid

# Arrangement (sve zajedno, artefakti + izveštaj)
python3.11 dna_midi_studio/arranger_pro.py arrange --input session35-partial-preview.mid \
    --target-channel 15 --out-dir artifacts-max-4.49
```

## Tim

`agent-team-max-4.49.json` (10 agenata, svi `done`), `task-plan-max-4.49.json` (T16–T25).
Testovi: `python3.11 -m unittest test_arranger_pro_v449` → **6/6 zeleno** (bez torch-a).
