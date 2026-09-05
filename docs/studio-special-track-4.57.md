# Special Track Engine 4.57 — echo / terca optimizacija (vodič, ne dokaz)

Dokazi su izvršeni artefakti: `artifacts-max-4.57/`, `reports-max-4.57/`,
testovi `test_special_track_engine_v457.py` + legacy spec 4/4. Ovaj fajl samo
objašnjava šta engine radi i zašto.

## Zašto

Istraživanje 4.56 je pokazalo: niko javno nema rešenje za optimizaciju
echo/terca treka aranžerskih stilova (`optimize_existing_echo_terca` = 0
GitHub pogodaka), a u našem repou je spec živeo samo kao v413 test bez
implementacije (`special_track_engine` nije postojao nigde, ni u git
istoriji). 4.57 implementira taj spec u paket.

## Šta engine radi

`dna_midi_studio/special_track_engine.py` —
`optimize_existing_echo_terca(groups, harmony, bar_ticks, ppq, profiles, extras, stats)`:

1. **Glavni deo (main)** = solo grupa sa najvišim confidenc-om (ili imenovana
   „*main*"). Glavni deo se **nikada ne menja** (kapija dokazuje).
2. **Echo track (eksplicitan role `echo`)**:
   - sličnost se meri na *onset pozicijama* (unikatni tick-ovi) — najbolji
     delay τ koji poklapa ≥50 % echo onset-a na main onset (tolerancija 30
     tick-ova) + pitch match ≥50 %; polifoni akordni main ne vara metriku;
   - **REPAIR** (slično): snap na echo grid (ppq/2), jačina ≤ 80 % main
     note (uvek bar 5 tiša), trajanje ≤ 80 % main dura;
   - **REBUILD** (neslično): sporne note se uklanjaju (`remove=True`), echo
     se generiše od main dela na delay τ (fallback 240), velocity po
     factory krivoj kanala (ceiling profil), 0 kontrolera/trigera.
3. **Terca (role `third`)**:
   - uz **harmony dokaz**: REPAIR — snap na main grid + korekcija pitch-a na
     **dijatonsku tercu** (C-dur: E→G, ne G#; molska skala posebno);
   - bez harmony dokaza: **PRESERVE** — bez pitch autoriteta, ključ se ne
     nagađa.
4. **Neimenovani aux trekovi** (dva „sola"): ako je drugi konzistentno
   odložena (τ ≥ 60) i tiša kopija prvog → inferiše se kao echo
   (`inferred: True`).

Autoriteti: velocity samo nadole + ceiling factory profila; bez
bank/program/CC/triger događaja; main netaknut; DNC/slap/pop trigeri ostaju
zaključani (4.55 politika).

## Dokazi (izvršeno 2026-09-05)

- `test_special_track_engine_v457.py` — 9 testova: 4 legacy-spec slučaja +
  5 garancija (main untouched, velocity ratio, PRESERVE bez ključa, ceiling
  clamp, polifoni echo ≠ REBUILD);
- legacy `test_echo_terca_engine_v413.py` — 4/4 funkcije prolaze (runner);
- demo na stvarnim notama: `artifacts-max-4.57/special-track-demo-reference-style.json`
  — main = pravi ch12 acc deo `reference-style.mid` (272 note), echo sloj je
  sintetička kopija (+240, ×0.8) označena kao takva; rezultat: REPAIR
  (τ=210, ratio 1.0), 139/272 nota poravnato/utišano, **main untouched:
  true**, 0 trigera;
- puna suita: 80 testova OK + legacy 5 OK (`reports-max-4.57/02-tests.json`).

## OSS nalazi iz istraživanja (4.56/4.57)

- za echo/terca optimizaciju: ništa javno → naš spec je jedinstven;
- za auto-akompanjman: Cadenza (GPL-3.0, C++/JUCE), BandInMuseScore (GPL-3.0),
  dave4mpls/autoaccompany (MIT, JS) — MIT se može proučavati/ugraditi uz
  atribuciju, GPL se ne ugrađuje (samo studija);
- bass line generator: SuperInstance/fleet-midi-bass (MIT, Python);
- Korg DNC: 0 javnih repoa; slide u MIDI 1.0 = pitch bend / portamento /
  legato-overlap (vidi `docs/finesse-special-tracks-research.md`).

## Reprodukcija

```bash
python3.11 -m unittest test_special_track_engine_v457
python3.11 - <<EOF  # legacy spec runner
import test_echo_terca_engine_v413 as spec
for n in sorted(x for x in dir(spec) if x.startswith("test_")):
    getattr(spec, n)()
EOF
```
