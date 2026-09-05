# Studio Truth Engine 4.51 — applying engine (vodič, nije dokaz)

> Ovaj dokument je **vodič kroz kod**, ne dokaz ponašanja. Dokazi su isključivo
> izvršeni: `artifacts-max-4.51/*.json` (bytes + metrike), test izveštaj
> `reports-max-4.51/02-tests.json` i artefakt `studio-arranged.mid`.

## Pravila koja motor izvršava

1. **Nikada ne blokira kada postoji dokazana poboljšavajuća primena.**
   `run_studio` najpre planira (`plan_regions`), pa primenjuje svaki planerski
   nalog koji ima fakatsku osnovu; `STUDIO_NO_CHANGES_NEEDED` se vraća samo kada
   je to doslovno istina (svi ACC slotovi zauzeti, nijedna nota ne prelazi tačan
   factory ceiling, headroom u PA800 budžetu).
2. **Fillovi samo popunjavaju prazne legalne slotove.** Kanali sa materijalom se
   nikada ne prepisuju (`skippedSlots` = razlog `already has material`).
3. **Brzina je isključivo tvornička.** Nota se ispravlja samo kada za njen
   tačni zvuk `(bankMsb, bankLsb, program)` postoji tačan factory profil
   (`exact_factory_profile`); bez profila — nema korekcije, nema nagađanja.
   Nova nota u fillu dobija tvorničku brzinu iz dokaza generatora.
4. **Uglačane note su tačni tonovi živog akorda.** `build_strum_part` za svaku
   notu gleda `cell_at(start)` u *izvršnom* song mapu; vanćelijski/vanregistarski
   tonovi se ne generišu.
5. **Postojeća geometrija se nikada ne menja.** Kapija
   `protectedGeometryUnchanged` poredi (track, channel, pitch, start, end)
   svih postojećih nota pre/posle; `velocityPolishOnlyLoweredByExactCeiling`
   dokazuje da svaka promena brzine na zaštićenom kanalu dolazi iz planiranog
   klampa na tačan ceiling.

## Tok

`run_studio(raw)` →
`plan_regions` (odluke KEEP / FILL_EMPTY / skips sa razlozima) →
`build_strum_part`/`build_pad_part` na praznim ACC slotovima →
`plan_dynamic_corrections`/`apply_dynamic_corrections` (samo tačni profili) →
`arrangement_metrics` na reparsiranim bytes →
kapije → status `STUDIO_APPLIED` ili `STUDIO_NO_CHANGES_NEEDED`.

## Ključni fajlovi

- `dna_midi_studio/arranger_contract.py` — istina: role, budžeti, registri,
  tonovi akorda, tačni factory profili, ceiling.
- `dna_midi_studio/arranger_pro.py` — `build_strum_part` v4.51 (live-cell).
- `dna_midi_studio/studio_flow.py` — v4.51 primenjujući motor + kapije + CLI.
- `test_truth_engine_v451.py` — 8 torch-free testova (strum/pad fill, tačnost
  tonova akorda, ceiling politika, no-op na zauzetom stilu).
