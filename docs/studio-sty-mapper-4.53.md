# Korg STY Mapper 4.53 (vodič — dokazi su artifacts + testovi)

Dodato u postojeći `dna_midi_studio/`. Cilj: **mapa „gdje šta ide" + export
SMF0 stila kojeg Korg Pa800 može uvesti kao stil**.

## Fajlovi
- `dna_midi_studio/sty_mapper.py` — mapa + exporter + kapije + CLI
- `test_sty_mapper_v453.py` — 11 testova (stdlib + mido cross-check)
- `artifacts-max-4.53/` — izlazni MIDI (`korg-style-*.mid`) + JSON runde
- `reports-max-4.53/01-sty-runs.json` — sažetak

## Šta radi (pravila iz dokaza, ne nagađanje)
1. `parse_markers`/`normalize_marker` — čita Korg marker meta događaje
   (`i1cv1`, `v2cv1`, `f1cv1`, `e2cv1` …); nepoznate oznake se odbacuju.
2. `section_map` — elementi (intro/variation/fill/ending + broj + CV) sa
   trajanjem u taktovima (PPQ 480, 4/4 → takt = 1920 tickova — dokazano u oba fajla).
3. `role_map` — za svaki kanal dokaz gde sedi: Korg konvencija 8 bas, 9 bubanj,
   10 perkusija, 11–15 pratnja (za kanale van 8–15 daje registarski predlog,
   označen kao predlog, nikad tih).
4. `export_korg_style` — SMF0 + markeri + per-element setup (CC0/CC32/PC i CC11)
   **samo ako fali**, i to isključivo iz postojećeg zvučnog dokaza u fajlu —
   zvuk se nikad ne izmišlja (kanal bez dokaza → u izveštaju, bez izmene).
5. Kapije: SMF0, jedan track, markeri prepoznati/rastući/na grid-u/layout,
   geometrija nota netaknuta, reparse.

## Izvršeno (2026-09-05)
- `reference-style.mid` (fabrički zlatni stil): export dodaje **0** događaja —
  fajl je već u Pa800 konvenciji (10 markera i1cv1…e2cv1, per-element setup).
- `arranged-4.51-fixture.mid`: dodato tačno **10 CC11=127** događaja (ch15 na
  svakom element-startu — jedini kanal bez CC11); nijedan zvuk nije nagađan
  (ch15 nema bank/program dokaz → preskočeno uz izveštaj). Sve kapije true.
- Testovi: 50/50 uključujući 4.53 (11) + mido nezavisna provera exporta
  (markeri i note-on po kanalu jednaki); legacy 5/5.
