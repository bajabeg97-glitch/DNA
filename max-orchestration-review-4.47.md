# Pregled MAX orchestration-a — DNA MIDI Studio Pa800 4.47

**Datum:** 2026-09-05
**Stanje koda:** `main` → commit `a4b6e72` („DNA MIDI Studio Pa800 4.47 project files"), spojeno u radni workspace (merge commit `f06a57f`).
**Zadatak:** „Počelo je na Max — pregledaj i ustanovi jel ima potrebe bilo što dodati više."

---

## 1. Šta MAX jeste u projektu

| Komponenta | Putanja | Opis |
|---|---|---|
| `MaxModelRegistry` | `dna_midi_studio/ai_learning/max_orchestrator.py` (165 linija) | Registar 10 modela (pattern_reconstructor, song_context, event_v1, event_ar_v2, phrase_planner, multibar_v3, section_arranger, transition_fill, relationship_ranker, relationship_sequence) sa provenancijom, ulogama, autoritetom i SHA-256 kontrolom prisutnosti. |
| `MaxCandidateOrchestrator` | ista datoteka | Ponderisano rangiranje kandidata (`evidence 1.25 / neural 0.55 / context 1.10 / phrase 1.20 / transition 1.10 / diversity 0.35`) uz sigmoid normalizaciju i kompletan `scoreBreakdown`. **Ne autorizuje MIDI** — hard validacija i finalni PA800 validator su obavezni („RANKING_ONLY_NOT_VALIDATION"). |
| `application_contract()` | ista datoteka | KEEP / REPAIR / REPLACE / TRANSITION_ONLY politika + 5 obaveznih kapija (`SYMBOLIC_HARD_CHECK`, `FACTORY_VELOCITY_AUTHORITY`, `PROTECTED_EVENT_CHECK`, `MIDI_REPARSE`, `FINAL_PRODUCTION_VALIDATOR`). |
| `build_max_status()` | ista datoteka | Generiše `max-orchestration-status` JSON. |
| Integracija | `dna_midi_studio/ai_learning/track_replacement.py` (505 linija) | Jedini korisnik: `rank_bar_candidates()` po taktu (linija 418) + `maxOrchestration` kontrakt u izlazu `replace()` (linija 503). |
| Testovi | `test_max_orchestrator_v432.py`, `test_max_transition_application_v432.py` | 3+2 testa (hard-invalid nikad ne rangira, score breakdown kompletan, transition gating, max kontrakt u izvještaju). |
| Status fajl | `max-orchestration-status-4.32.json` | Jedini status; verzija 1.0, iz faze 4.32. |

Principi MAX-a su konzistentni sa ostatkom sistema: velocity isključivo iz fabrike (`FACTORY_ONLY`), GOLD ne utiče na dinamiku, hard provere pre rangiranja.

---

## 2. Nalazi — šta fali / šta treba dodati

### 2.1 Putanje u kodu i statusu ne odgovaraju layoutu repoa (najkritičnije)
- `MaxModelRegistry` očekuje `{root}/data/...` i `{root}/models/...`, a `max-orchestration-status-4.32.json` tvrdi da su modeli prisutni na `data/ai_event_decoder/...` itd.
- **U repou ne postoje direktorijumi `data/`, `models/`, `learning_data/`, `artifacts/`** — fajlovi su rasuti po root-u: `ai_event_decoder/`, `ai_autoregressive_event/`, `ai_phrase_context/`, `ai_song_context/`, `dna-reconstructor-v2/`, `relationship-transformer-v1/`, `relationship-sequence-v2/`, `session*.mid` direktno u root-u.
- **Dokaz (pokrenuto na ovom workspace-u):** `build_max_status('.')` → `present: 0 / missing: 10` — iako svih 10 modela fizički postoji u repou (veličine/hash-evi se poklapaju sa statusom 4.32, npr. `song_context_model_v1.pt` = 41 440 B).
- Zbog toga **testovi v432 ne mogu da prođu iz svježeg clone-a** (`artifacts/session35-partial-preview.mid`, `models/dna-reconstructor-v2`, `learning_data`, `data` ne postoje).
- Zaključak: repozitorijum je „spljošteni" izvoz originalnog workspace-a (gdje su `data/`, `models/`, `artifacts/`, `learning_data/` postojali). Ako GitHub treba da bude izvor koji se može pokrenuti/testirati, treba dodati **ili** (a) pravi layout (`data/`, `models/`, `learning_data/`, `artifacts/`) **ili** (b) ispravku putanja u `MODEL_SPECS`-u i u testovima.

### 2.2 MAX nije u finalnom 4.47 toku — samo u testovima i re-exportima
- Jedini runtime korisnik MAX-a je `TrackReplacementEngine`, a **nijedan modul proizvodnog toka ga ne poziva**: `unified_pipeline.py`, `end_to_end_arranger.py`, `dnc_engine.py`, `role_aware_repair.py` — nijedan ne importuje `ai_learning` / `TrackReplacementEngine`. Koriste ga isključivo testovi (v419–v435) i `ai_learning/__init__.py`.
- `final-software-completion-4.46.json` navodi komponente: `factoryDevice`, `goldMelodic`, `relationships`, `drumElements`, `rhythmGuitar`, `coverage` — **nema MAX-a ni neuralnih kandidata**.
- Nijedan status od 4.33 do 4.46 ne pominje MAX (grep: samo `max-orchestration-status-4.32.json` + `module-reachability-audit-4.38.json` kao import PASS).
- Zaključak: neuralna linija (event decoder → autoregressive → multibar → phrase/section/transition → **MAX rangiranje** → track replacement) nastala u fazama 4.19–4.35 **nije priključena na finalnu arhitekturu 4.47**. Treba donijeti odluku:
  - **(A)** MAX ostaje aktivan → dodati poziv tamo gdje `role_aware_repair` odluči `REPLACE` (ili u `song_reconstruction_planner`), ili
  - **(B)** zvanično ga označiti kao legacy/arhivski sloj (status fajl sa `status: superseded`), da se zna da nije dio finalnog toka.

### 2.3 Status i verzija nisu osvježeni
- `max_orchestrator.VERSION = "1.0"` i status JSON su iz faze **4.32**; projekat je danas na **4.47**.
- Ako MAX ostaje dio sistema → dodati `max-orchestration-status-4.47.json` (osvježen registar sa stvarnim SHA-256 i putanjama), pomenuti ga u `final-software-completion` / release check izvještaju i podići verziju modula (npr. `1.1` uz ispravku putanja + novi audit).

### 2.4 Duplikat paketa `lib/dna_midi_studio` — drift
- `lib/` sadrži stariju instaliranu kopiju (76 fajlova): **nema `max_orchestrator.py`**, a `track_replacement.py` se razlikuje od `dna_midi_studio/` verzije.
- Rizik: neko uveze pogrešan paket i dobije starije ponašanje. Treba ukloniti ili sinhronizovati sa `dna_midi_studio/`.

### 2.5 Sitnice
- Testovi v432 ne provjeravaju `present/missing` brojač u registru — da su se pokretali na trenutnom layoutu, prošli bi i pored toga što registar vidi 0/10 modela (path bug ostaje neprimijećen).
- `module-reachability-audit-4.38.json` je import-only provera — ne hvata runtime putanje do modela/podataka.

---

## 3. Zaključak („jel ima potrebe bilo što dodati više?")

**Sam MAX modul ne traži novu funkcionalnost** — kao komponenta je logički zaokružen (registar + rangiranje + kontrakt + testovi + jasna autoritet pravila) i konzistentan sa 4.32 principima koji su i danas važeći (factory-only velocity, hard kapije, MAX ne autorizuje MIDI).

**Ali da bi MAX bio „gotov" u kontekstu finalne 4.47 verzije, nedostaje sljedeće (po prioritetu):**

1. **Uskladiti putanje/layout** — registar mora nalaziti svih 10 modela u repou (trenutno 0/10), a testovi v432 moraju biti izvršivi iz clone-a. *(Preduvjet svega ostalog.)*
2. **Priključiti ili arhivirati** — MAX + `TrackReplacementEngine` nisu u finalnom toku; ili ih povezati na `REPLACE` odluku `role_aware_repair`-a / `song_reconstruction_planner`-a, ili ih eksplicitno označiti kao legacy.
3. **Osvježiti status na 4.47** — novi `max-orchestration-status-4.47.json` (sa stvarnim hash-evima), pomen u `final-software-completion` / release izvještaju, bump verzije modula.
4. **Počistiti `lib/`** duplikat (ukloniti ili sinhronizovati).

Ako je namjera da MAX **ne** bude dio finalne verzije (jer su ga zamijenili hard-evidence engine-i 4.42–4.47), onda se **ništa ne dodaje u modul** — dovoljno je tačku 2 i 3 odraditi u smjeru „arhivirano", da se izvještaji i statusi ne proturječe.

---

## 4. Šta je urađeno u ovom prolazu

- `git fetch origin main` + merge u radni workspace (`f06a57f`, „Preuzmi…") — kompletan projekat DNA MIDI Studio Pa800 4.47 je sada lokalno dostupan uz web aplikaciju (README i .gitignore spojeni).
- Pokrenuta provera `MaxModelRegistry.scan()` → **0/10 modela prisutno** (dokaz nalaza 2.1).
- Ovaj izvještaj.
