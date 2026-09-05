# Korg STY / jači MIDI projekti — istraživački nalazi (2026-09-05, verifikovano uživo)

Ovo su **verifikovane činjenice iz GitHub API-ja i izvora** (linkovi, licence, datumi).
Nije dokaz ponašanja našeg koda — to su nalazi istrage za 4.53 STY Mapper.

## Korg .sty — kako stvarno funkcioniše (više izvora: korgforums, kvraudio, reddit)

1. Pa800/Pa2x uvozi stil **isključivo kao SMF Type 0** sa markerima; Type 1 nije dozvoljen.
2. Kanali za pratnju: **MIDI kanali 9–16 (0-based 8–15)** — bas, bubanj, perkusija, ACC1–5.
3. Stil ima do 14 sekcija: Intro 1–3, Main/Variation 1–4, Fill 1–4, Break, Ending 1–3;
   svaka sekcija može imati više Chord Variation (CV1…).
4. U SMF exportu sekcije su **marker meta događaji** (`i1cv1`, `v2cv1`, `f1cv1`, `e2cv1`…),
   a Korg na početku svake CV šalje i bank/program setup (CC0/CC32/PC) + CC11.
   (Pa600/Pa5X dodaju CC114–119 i Korg SysEx; Pa800 export koji mi imamo ih ne koristi —
   potvrđeno: naš `baseline/reference-style.mid` ima markere i CC0/32/11, bez sysex.)
5. Pravi `.sty` na uređaju = SMF skelet + dodatni proprietarni blokovi (STS, pad, FX…);
   zato se stil **pravi tako što se marker-MIDI uveze u klavijaturu** — ne pišemo .sty binarno.
6. OC31 kompresija postoji u pojedinim blokovima (.set) — nije potrebna za SMF import rutu.

## Naši dokazni fajlovi (izvršeno, lokalno)

- `baseline/reference-style.mid`: markeri `i1cv1@0, i2cv1@3840, v1cv1@11520, v2cv1@15360,
  v3cv1@19200, v4cv1@23040, f1cv1@26880, f2cv1@28800, e1cv1@30720, e2cv1@34560`;
  CC0/CC32/CC11 ×80; bez sysex → **tačno ono što Pa800 očekuje pri uvozu**.
- `artifacts-max-4.51/arranged-4.51-fixture.mid`: ista marker struktura (naš izlaz već jeste
  stilski SMF) — odlična polazna tačka za exporter testove.

## Projekti koji rade na istom konceptu (nađeni code-searchom, ne repo-searchom)

| Repo | Licenca | Šta radi | Nivo |
|---|---|---|---|
| `mahfouzalsheikh/ostinato` (0★, aktivan) | **MIT** | Python: Korg marker-SMF → vendor-neutral model: `importers/korg/markers.py` (normalizacija i1cv1→(intro_1,cv)), `midi_style_importer.py` (574 linije, sekcije+track role), `pa80_smf.py` (Pa80 per-CV folderi), `native/korf_bank.py`, `korg_converter/` (export preko Win32 aplikacije u docker/wine) | srednji — ima **importer/model**, nema čist Python writer |
| `oliv7971/dev-workspace` (0★, lično) | bez licence (read-only) | dubok reverse engineering Korg/PA4x: `sty_analyzer`, `_dump_korg_detail.py`, kompletni `.SET` snimci (GLOBAL/MIDI.MPR, KEYBOADSET…) | sirovina za istraživanje formata |
| `Sari-raslan/universal-arranger-os` | bez licence | ogroman „arranger OS" sa korg research gate-om | nedostupan za kopiranje |
| `ltgcgo/octavia` (58★) | LGPL-3.0 | multi-standard MIDI tool chain (state tracking, parse/serialize) | referenca za kvalitet MIDI koda |
| `drbye78/syxg`/`vibexg` | — | **Yamaha SFF2** parser u Pythonu (`sff2_parser.py`) | dokaz da postoji samo Yamaha-ekvivalent |

## Korpusi „milijun dokaza" (pattern statistika)

- **MetaMIDI Dataset (MMD)** — 436.631 MIDI fajlova; `jeffreyjohnens/MetaMIDIDataset` (157★),
  `Metacreation-Lab/MetaMIDI-Dataset` (autori). Najbliže „milijunu"; pola miliona.
- Lakh (174k), MAESTRO (~2k), POP909 (909), GiantMIDI (10,8k), Groove MIDI (1.150 bubnjeva).
- Za 4.5x groove/pattern rad: Groove (mikrotajming ljudi) + uzorak MMD-a (gustine obrazaca).

## Zaključak za naš projekat

- **Ne postoji javni čist-Python Korg style WRITER** — to je praznina koju popunjavamo.
- Postoji MIT referenca (ostinato) za mapiranje markera/sekcija/kanala — možemo je proučiti
  i (uz atribuciju, MIT) koristiti kao osnovu za naš `sty_mapper`.
- Naš put: **4.53 Korg STY Mapper/Exporter** u `dna_midi_studio/` — SMF0 marker stil koji
  Pa800 uvozi: mapa „gdje šta ide" (sekcija i/v/f/e + CV, kanal 8–15 po ulozi, bank/prog
  setup po CV, CC11), verifikacija protiv `reference-style.mid` kao zlatnog izvora.
- Pattern-statistika (4.54+) iz korpusa — Groove + MMD uzorak, uz zadržavanje pravila
  „samo izvršeni dokazi".
