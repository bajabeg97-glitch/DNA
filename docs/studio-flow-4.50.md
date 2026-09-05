# Studio Flow 4.50 — vodič (rekonstrukcija „studio core-a")

> **Zašto 4.50:** iskren dizajn pregled (agent `design-critic`, `reports-max-4.50/01-design-review.json`)
> pokazao je da su 4.48/4.49 funkcionalno kompletni ali **fragmentisani**: iste činjenice
> (kanal→uloga, factory velocity, polifonijski limiti) bile su duplirane na više mesta,
> aranžer nije procenjivao da li postojeći part odgovara instrumentu, i nije postojao
> jedan „studio" ulaz. Zato je izvedena rekonstrukcija na zajednički ugovor.

## Novi arhitektonski sloj (4 modula umesto 1 duplog)

```
arranger_contract.py   single source of truth:
                       CHANNEL_ROLES, polyphony_limit() (PA800),
                       factory_velocity() (FACTORY_ONLY, jedina implementacija),
                       STRUM_REGISTER 48–72, chord_pc_set() (kvaliteti+simboli),
                       protected_snapshot() gate
        │
arranger_planner.py    per-channel “kako instrument svira” fit:
                       registerFit/densityFit/dynamicsFit/gateFit
                       + odluke KEEP | REPAIR_WARRANT | FILL_EMPTY | MANUAL_REVIEW
        │
arranger_pro.py        (rekonstruisan na ugovoru — isti API, 4.49 testovi zeleni)
        │
studio_flow.py         JEDAN ulaz: analyze → plan → execute (opravdani fill) →
                       metrics → gates → artefakti + JSON izveštaj
```

## Šta sada radi end-to-end (dokaz na `session35-partial-preview.mid`)

1. **Planer** (bez izvršenja): `bass KEEP (fit 1.0)`, `drums/perc MANUAL_REVIEW`,
   `acc KEEP`, `ch15 FILL_EMPTY` → najbolji kandidat za gitaru (PA800 budžet 3 glasa).
2. **Studio run**: popunjen ch15 (taktovi 1–18) Factory-strum ritam-gitarom —
   410 nota, **registar 48–72 (0 van okvira)**, peak polifonija 3/3, velocity
   factory-only (410 dokaza, autoritet FACTORY_ONLY), max velocity 114 → headroom 13.
3. **Metrika**: chord-tone pokrivenost **410/410** (0 nepoznatih ćelija; aliasi +
   parsiranje simbola poput `Fadd9/A`, `G#m7b5`), **chord-tone ratio 0.74** (ostatak su
   prolazni/kolor tonovi iz fabričke strum evidencije — autentično, ne nasumično).
4. **Kapije**: reparse ✓, ostali kanali nepromenjeni ✓ (diff na nivou nota),
   factory-only velocity ✓ (dokazi pre zapisa — MIDI bajtovi ne čuvaju profile ID),
   svi kanali u PA800 polifonijskim limitima ✓.

## Komande

```bash
# Plan — šta svaki kanal svira i koliko dobro odgovara instrumentu
python3.11 dna_midi_studio/studio_flow.py plan --input session35-partial-preview.mid

# Studio run (auto-izbor praznog acc kanala; nikad ne prepisuje zauzet kanal)
python3.11 dna_midi_studio/studio_flow.py run --input session35-partial-preview.mid \
    --out-dir artifacts-max-4.50

# Eksplicitni kanal / taktovi
python3.11 dna_midi_studio/studio_flow.py run --input session35-partial-preview.mid \
    --target-channel 15 --start-bar 3 --end-bar 6 --out-dir artifacts-max-4.50
```

## Tim i dokazi

- `agent-team-max-4.50.json` — **14 agenata** (design-critic, contract-architect,
  reconstruction-engineer, instrument-fit-planner, studio-orchestrator, metrics-agent,
  gate-validator, test-agent, scope-auditor, integration-advisor, roadmap-agent,
  docs-agent, release-agent + orchestrator-max), svi `done`.
- `task-plan-max-4.50.json` — T26–T39, svi `done`.
- `reports-max-4.50/01..10` — dizajn pregled, ugovor, plan, run, metrika, kapije,
  testovi, scope, pipeline integracija (advisory), roadmap.
- Testovi: `test_studio_flow_v450.py` (11/11) + `test_arranger_pro_v449.py` (6/6)
  — oba bez torch-a; MAX 4.48 neuralni lanac ostaje zelen (15/15).

## Ograničenja (iskrena)

- Popunjavaju se samo **prazni** acc slotovi evidencijom koja postoji (strum);
  zauzeti kanali sa nalazom idu na MANUAL_REVIEW / REPAIR_WARRANT (izvršenje
  REPAIR-a je sledeći korak, vidi roadmap `10-next.json`).
- Rezultat je **song-level** aranžman; PA800 style izvoz (SMF0 + CV markeri)
  ostaje zaseban, validirani cilj (ne tvrdimo style bez markera).
