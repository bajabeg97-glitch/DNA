# Mix Engineer 4.52 — dodato u postojeći DNA MIDI Studio (vodič, ne dokaz)

> Vodič kroz kod. Dokazi: `artifacts-max-4.52/*.json` + `reports-max-4.52/01-mix-run-summary.json`
> + test izveštaj (39/39 + mido nezavisni verifikator).

## Šta je dodato i gde (ništa novo osmišljeno — proširenje postojećeg)

| Fajl | Sadržaj |
|---|---|
| `dna_midi_studio/mix_engine.py` | Mix Engineer 4.52 — gain staging ceo ciklus |
| `test_mix_engine_v452.py` | 12 testova (stdlib) |
| `test_mido_independent_v452.py` | nezavisni verifikator (mido, dev-only, skip ako ga nema) |
| `artifacts-max-4.52/` | izvršeni rezultati na oba fajla (MIDI + JSON) |
| `reports-max-4.52/` | sažetak izvršenih činjenica |

## Kako radi (mikserska logika, ne nagađanje)

1. **Analiza (samo činjenice):** po kanalu — zvuk `(bankMsb,bankLsb,program)`, broj nota,
   registar, prosečna dužina, prosečna brzina, uloga iz `CHANNEL_ROLES`
   (8 bas, 9 bubanj, 10 perkusija, 11–15 pratnja).
2. **Preklapanje/maskiranje:** `masking_windows()` meri kolizije (vreme × registar ≤ 12
   polutonova) za svaki par kanala — to je „šta se dešava na preklapanju".
3. **Perkusija na bubanj kanalu:** `percussion_accents_on_drums()` objektivno nalazi
   akcente koji nisu core set (kick/snare/hat) ili se dupliraju sa ch10 —
   **izveštaj, ne promena** (brzina na zaštićenim kanalima je FACTORY_ONLY).
4. **Plan (−45% = ×0.55):** skalira se isključivo CC11 (expression) automatika
   ciljanih kanala: `127 → 70` na svakom bar-start događaju.
   Note, velocity i CC7 (master fader) se ne diraju.
5. **Kapije (sve mora da prođe):** `reparsed`, `noteGeometryUnchanged`,
   `velocitiesUntouched`, `onlyTargetChannelCCScaled`, `exactScaleApplied`,
   `maskingWindowsUnchanged`.

## Zašto CC11 a ne velocity

Korg stilovi već nose per-bar CC11=127 automatiku. CC11 je *expression*: skalar
glasnoće koji ne menja timbar/artikulaciju (velocity bi menjao i velocity-layer
sampla). Ovo je kako pravi mikser spušta sloj — fader, ne prepravljanje nota.

## Izvršeni rezultati (2026-09-05)

- `arranged-4.51-fixture.mid`: ciljane podloge ch11+ch13+ch14 i perkusija ch10,
  40 CC11 događaja 127→70, sve kapije true, efektivno maskiranje −36.1%.
- `baseline/reference-style.mid`: podloge ch13+ch14 (string par (120,0,64)) i
  perkusija ch10, 30 CC11 događaja 127→70, sve kapije true, efektivno maskiranje −28.2%.
- Conga/perkusija akcenti na bubanj kanalu: fixture 10 nota, baseline 70 nota —
  izveštaj u JSON, nisu menjane na nivou nota (factory velocity pravilo);
  cela perc grupa (ch10) jeste smanjena −45%.
