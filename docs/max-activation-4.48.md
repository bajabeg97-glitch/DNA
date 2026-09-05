# MAX 4.48 — Aktivacija (vodič za korišćenje)

> **Status: `ACTIVE_OPT_IN`** — MAX neuralna orchestraciona linija je sada izvršiva u ovom repou.
> Podrazumevani produkcijski tok (`unified_pipeline.py`) **nije menjan**: MAX se poziva eksplicitno.

## Šta je urađeno (od 4.47 → 4.48)

| Stavka | Pre (4.47) | Posle (4.48) |
|---|---|---|
| `MaxModelRegistry.scan()` na spljoštenom repou | 0/10 modela | **10/10** (layout-aware pretraga; klasični `data/ models/` layout takođe podržan i dokazan testom) |
| Izvršivost neuralnog REPLACE-a | testovi su očekivali `models/ learning_data/ data/ artifacts/` koji ne postoje | `dna_midi_studio/max_layout.py` + ažurirani testovi |
| Ulazna tačka | samo `TrackReplacementEngine` (nizak nivo) | `dna_midi_studio/max_activation.py` — opt-in executor sa kapijom odluke i CLI |
| Dokaz rada | — | 6 MIDI render-a (A/B/C × 2 runde) + 2 execution report-a + 10 agent izveštaja |

## Kako pokrenuti

```bash
# 1) Registar i status (ne traži torch)
python3.11 dna_midi_studio/max_activation.py status

# 2) REPLACE sa warrant kapijom (role-aware odluka mora biti REPLACE/AUGMENT;
#    inače executor odbija)
python3.11 dna_midi_studio/max_activation.py replace \
  --input session35-partial-preview.mid \
  --role accompaniment --track 0 --channel 15 \
  --start-bar 6 --end-bar 9 \
  --out-dir artifacts-max-4.48

# 3) Eksplicitni neuralni run (--force se upisuje u izveštaj; koristi se za
#    testove ili kada korisnik svesno traži neuralnu generaciju)
python3.11 dna_midi_studio/max_activation.py replace \
  --input session35-partial-preview.mid \
  --role bass --track 0 --channel 8 \
  --start-bar 6 --end-bar 9 \
  --out-dir artifacts-max-4.48/bass-neural --force
```

Izlaz po rundi: `*.REPLACE.A/B/C.max-4.48.mid` + `max-execution-4.48.json` (request,
decision gate, registry, authority, MAX score breakdowns po taktu, sha256, status).

## Kapije koje se uvek primenjuju (iz `application_contract`)

1. `SYMBOLIC_HARD_CHECK` — pre svakog rangiranja (kandidat mora biti `hard_valid`).
2. `FACTORY_VELOCITY_AUTHORITY` — nove note dobijaju velocity isključivo iz factory profila (`FACTORY_ONLY`); GOLD/neural ne diktiraju dinamiku.
3. `PROTECTED_EVENT_CHECK` — sve izvan ciljane regije mora ostati identično (hash-diff).
4. `MIDI_REPARSE` — izlaz se uvek ponovo parsira; neparsabilan izlaz = greška.
5. `FINAL_PRODUCTION_VALIDATOR` — obavezan pre izvoza; za song-format MIDI je SMF/PA800 provera dokumentovana (vidi `reports-max-4.48/06-pa800-gate.json`); za style fajlove se poziva `pa800_validator.validate_pa800_smf`.

## Rezultati (2026-09-05, fixture `session35-partial-preview.mid`)

- **Accompaniment ch15, taktovi 6–9** (prazan kanal → odluka REPLACE, bez `--force`): 3 render-a,
  izvori: GOLD evidencija; MAX rekompozicija 12/12 koraka tačna.
- **Bass ch8, taktovi 6–9** (`--force`, evidentirano): 3 render-a, izbor pada na **neuralne
  `FULL_SONG_MULTIBAR_EVENT_V3`** kandidate (ceo 4-taktni niz) — dokaz da neuralna linija
  stvarno prolazi kroz MAX rangiranje i produkcijske kapije.
- Testovi: **15/15 zeleno** (`test_max_registry_v448`, `test_max_activation_v448`,
  `test_max_orchestrator_v432`, `test_max_transition_application_v432`).
- Produkcijski moduli (`unified_pipeline`, `role_aware_repair`, `song_understanding`,
  `evidence_authority`, `pa800_validator`, …) — **diff prazan** (izveštaj 09).

## Tim i raspodela

- `agent-team-max-4.48.json` — 13 agenata sa odgovornostima i isporukama.
- `task-plan-max-4.48.json` — 15 zadataka (T1–T15), svi `done`.
- `reports-max-4.48/01..10` — dokazi po agentu (registry, layout, inventar, run, kapije,
  PA800, rank audit, testovi, regression scope, web integracija).

## Beleška o web integraciji (advisory)

`reports-max-4.48/10-web-integration-notes.json`: preporuka da se MAX izloži kao opcioni
`engine: max-neural` u stage konfiguraciji `execute_pipeline`, iza korisničkog flag-a i
REPLACE/AUGMENT warrant-a; ne uključivati u batch režim dok se hardware profili ne popune.
