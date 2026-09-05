# Session Pass 4.60 — Faza A: jedan prolaz, svi engine-i (vodič, ne dokaz)

Dokazi: `artifacts-max-4.60/session-pass-*.json` (3 izvršena prolaza) +
primenjeni fajlovi `session-sty-*.mid`, `session-mixed-*.mid`; testovi
`test_session_pass_v460.py`; suita 98 + legacy 5 OK.

## Šta je Session Pass

Faza A proizvodne vizije (`docs/dna-optimizer-vision.md`): **jedan CLI poziv**
protera fajl kroz sve engine-e i vrati **jedan objedinjeni izveštaj** sa
statusima akcija — to je „mozak" koji će GUI (Faza C) samo prikazivati.

```
python3.11 dna_midi_studio/session_pass.py --input <fajl> \
  --roles 8:bass,9:drums,10:percussion,11:accompaniment,... \
  [--apply-safe] [--out-dir artifacts-max-4.60]
```

## Lanac u jednom pozivu

1. **Identitet** — sha256, format, PPQ, kanali, tempo + Korg markeri
   (`sty_mapper.parse_markers`/`section_map`);
2. **Per-role patterni** — `role_patterns.role_pattern_evidence` za svaki kanal
   iz mape (bass/drums/percussion/accompaniment/solo; solo dobija melodijski
   profil);
3. **Groove vs ljudska referenca** — `groove_engine` na drum/perc kanalima
   (std ms, udeo na gridu);
4. **Tehnike** — `instrument_techniques` na bas kanalu (bass) i prvom akordnom
   kanalu (rhythm-guitar); 0 trigera;
5. **Echo/terca sken** — `special_track_engine` na *kopijama* (pravi fajl se
   nikad ne menja): ako postoji solo kanal, proverava da li je neki drugi sloj
   konzistentno odložena kopija;
6. **Miks plan** — `mix_engine.plan_cc_gain` za percussion kanal(e) ×0,55
   (politika −45 % CC11, velocity netaknut);
7. **Akcije**:
   - `A01_STY_EXPORT` — Pa800-importabilan SMF0 iz postojećih markera;
   - `A02_PERCUSSION_CC11_GAIN` — CC11 balans percussion sloja;
   - `A03_ECHO_TERCA` — ako je struktura dokazana → `NEEDS_DECISION`;
   - `A04_STUDIO_FILLS` — legalno samo u praznim slotovima uz ljudsku potvrdu;
   - `A05_DEVICE_LOCKED_TRIGGERS` — uvek `LOCKED` (warrant).

`--apply-safe` primenjuje samo `READY` akcije u **nove** artefakte; izvor se
nikad ne prepisuje; svaka akcija nosi gate izveštaj.

## Izmereno (2026-09-05, izvršeno)

**reference-style.mid** (`--apply-safe`):
- 10 markera (i1cv1…e2cv1); role mape 8→bass … 15→accompaniment;
- per-role: bass 107, drums 263, perc 589, acc ch11–15 (141–272);
- groove: ch9 std 6,64 ms / 98,9 % na gridu; ch10 std 22,24 ms;
- tehnike: bass ch8 107 nota, akordni ch11 205 udara (rhythm-guitar rečnik);
- miks: ch10 ×0,55 = 10 CC11 događaja;
- `A01` APPLIED (STY gates svi true; addedTotal 0 — konforman izvoz),
  `A02` APPLIED (gates: note velocities untouched),
  `A03` SKIPPED, `A04` NEEDS_DECISION, `A05` LOCKED.

**session35-partial-preview.mid**: `A01` READY, `A02` READY (analiza bez
primene).

**song19-01.mid**: solo kanal ch2 (16 nota); echo sken pokrenut na 4 kanala —
0 nalaza (nema echo strukture); A01/A02 SKIPPED (format nije SMF0 / nema
percussion role).

## Reprodukcija

```bash
python3.11 dna_midi_studio/session_pass.py --input baseline/reference-style.mid \
  --roles 8:bass,9:drums,10:percussion,11:accompaniment,12:accompaniment,13:accompaniment,14:accompaniment,15:accompaniment \
  --apply-safe --out-dir artifacts-max-4.60
python3.11 -m unittest test_session_pass_v460
```
