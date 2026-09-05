# Iskreni izveštaj — šta radi, šta ne radi (4.65)

Datum: 2026-09-05 · Autor: DNA Optimizer (arena agent, uz tvoje reference i preuzeti korpus)
Mašinski proverljiva verzija: `dna-honest-report-4.65.json` · Dokazi: `reports-max-4.64/`, `artifacts-max-4.6x/`, test suite (Python 133 + 5 legacy, npm 14).

> Pišem bez uvijanja: šta je stvarno dokazano, šta radi uslovno, šta **ne** radi, i šta je namerno zaključano. Svaka tvrdnja ima pokazivač na dokaz.

---

## 1. Šta RADI — dokazano i ponovljivo

| # | Tvrdnja | Dokaz |
|---|---|---|
| 1.1 | **Ceo lanac**: tvoj MIDI → razumevanje (identitet, markeri, role po kanalima, gustina, velocity, groove) → plan akcija sa kapijama → ti potvrdiš → primena → novi artefakti | Session Pass 4.60–4.62, bridge 4.61/4.63/4.65; HTTP e2e u `artifacts-max-4.62/bridge-run.json`, `artifacts-max-4.63/bridge-run.json`; testovi `test_session_pass_v460/462`, `tests/bridge-session.test.mjs` |
| 1.2 | **Original se nikad ne menja**: primene pišu samo nove fajlove; sha256 izvora se proverava u testovima | `test_session_pass_v462.py` (source-bytes-never-modified), gates `readOnlyNoInputBytesWritten` |
| 1.3 | **Samo READY akcije se primenjuju**; NEEDS_DECISION/LOCKED se ne mogu primeniti ni CLI-jem ni UI-jem | `--apply-actions` (4.62), checkbox disabled u UI (4.63); e2e: song19-01 → 0 artefakata |
| 1.4 | **A01 STY izvoz**: SMF sa Korg markerima (i1cv1…e2cv1), format 0, struktura proverena gates-ima | `structure_gates` + `session-sty-*.mid` artefakti; markeri iz tvojih fajlova se prepoznaju (10/10) |
| 1.5 | **A02 CC11 miks**: perc kanal dobija CC11 plan (samo ako fajl ima CC11 tok); velocity se ne dira | `session-mixed-*.mid` artefakti; test per-action A02 → samo mixed artefakt |
| 1.6 | **Groove naspram ljudi**: std tajminga tvog bubanj kanala se poredi sa referencom izvedenom iz 110.313 primera pravih bubnjara (wobblemidi, MIT, atribucija u artifacts) | `grooveVsHuman` u svakom izveštaju; 4.54 evidencija |
| 1.7 | **Pattern biblioteka (4.64)**: 468 obrazaaca iz knjiga „200/260 Drum Machine Patterns” (MIT, `gvellut/dmp_midi`) vend-ovano sa NOTICE + sha256 manifestom | `vendor-max-4.64/dmp-midi/`, `test_pattern_library_v464.py` (16 testova) |
| 1.8 | **Tvoje reference su sad klasifikovane kao korpus (4.65)**: reference-style (22 takta, 51 motiv, 15 poklapanja sa korpusom), session35 (18 taktova, 76 motiva, 8 poklapanja), song19-01 (8 taktova, 2 motiva, 2 poklapanja); **6 redova je deljeno između tvojih fajlova** (npr. snare `0000100000001000` u reference-style i song19-01; kick `1000100010001000` u reference-style i session35) | `artifacts-max-4.65/user-reference-*.json`, `recognition-overview.json`, `test_user_reference_bank_v465.py` |
| 1.9 | **Prepoznavanje je u full upotrebi**: svaki Session Pass izveštaj sada nosi `patternRecognition` (korpus + tvoje reference), i UI ga prikazuje kao karticu „Prepoznavanje obrazaca (4.65)” | `session_pass.py` (pattern_recognition u izveštaju), `dna_bridge.mjs` (addRecognitionCard) |
| 1.10 | **Determinizam**: isti ulaz → isti izlaz (bajt po bajt), digests 64-char | testovi determinizma u 4.64/4.65 |
| 1.11 | **Licence**: sve prekopirano je MIT uz atribuciju; GPL/CC-NC nije dirano | `reports-max-4.64/01-03`, NOTICE.md |
| 1.12 | **Testovi trenutno zeleni**: Python 133 (102 + 16 + 15) + legacy 5 + npm 14 | `reports-max-4.65/03-tests.json` |

## 2. Šta radi DELIMIČNO ili pod uslovima

| # | Stavka | Uslov / ograničenje |
|---|---|---|
| 2.1 | **A01 = „Pa800 spreman”** | Dokazan je **oblik** (markeri, kanali 8–15, FACTORY velocity, struktura). **Nije** testiran fizički uvoz na pravu Pa800 klavijaturu — to je jedini pravi dokaz koji fali; gates to pošteno ne tvrde. |
| 2.2 | **A02 CC11** | Radi samo ako perc kanal **već ima CC11 tok**; inače `channelsMissingCC` i ništa se ne primeni (song19-01 → 0 artefakata). |
| 2.3 | **A03 echo/terca** | Na svim tvojim trenutnim referencama status je SKIPPED — nema potvrđenog eha u njima, pa se ništa ne primenjuje. Mašina radi (kopije, scan), ali **na tvojim fajlovima nijednom nije APPLIED** — to je iskreno: feature postoji, primer ulaza još nije. |
| 2.4 | **A04 studio fills** | Uvek NEEDS_DECISION — nikad se ne primeni sam; čeka tvoju odluku (po dizajnu). |
| 2.5 | **Prepoznavanje obrazaca** | Tačno 16-step, 4/4, GM note, kanali 9/10. **Swing/human tajming (tvoj perc kanal, ~28% na gridu) uglavnom NEĆE dati poklapanje** — to nije bag, nego granica metode. Generični redovi (snare na 2 i 4) se poklapaju sa mnogo korpus naslova — nisu diskriminativni. Niske note van GM opsega (u reference-style: pitch 16–21 na ch9/10) se bankuju ali nikad ne match-uju GM korpus. |
| 2.6 | **Bank referenci** | Sadrži 3 tvoja fajla (reference-style, session35, song19-01). song19-02…20 **nisu** još u banci. Kada analiziraš fajl koji je već u banci, „poklapanje sa referencama” uključuje i njega samog — piše koji fajl, pa se vidi. |
| 2.7 | **UI „AI model”** | To je **imitacija AI četa**: komande su regex, odgovori su šabloni, a suština je deterministički engine. Nema LLM-a, nema generisanja. To je namerno i iskreno. |
| 2.8 | **Upload** | MIDI do 50 MB, SMF tip 0/1; tip 2 se odbija. |
| 2.9 | **Okruženje** | Engine treba numpy (posle reset-a sandbox-a instaliran `pip install --break-system-packages numpy`); bez toga Python suite ne radi. |

## 3. Šta NE radi / nije izgrađeno (još)

| # | Stavka | Status |
|---|---|---|
| 3.1 | **WebAudio A/B preslušavanje** (Faza D) | **Nije izgrađeno.** Nema zvuka u browseru; jedini „audition” je učitavanje na klavijaturu. |
| 3.2 | **React skin + pamćenje sesija/projekata** | Nije. UI je vanilla JS; refresh izgubi plan. |
| 3.3 | **Device capture / warrant ledger** (Pa800 snimanje) | Nije. Zbog toga su A05/DNC/slap/pop/program-bank emisija **trajno LOCKED** — ništa od toga se nikad ne emituje. To je po dizajnu, ali znači da pola potencijala (trigeri, keyswitch, pravi device profil) stoji dok se ne uradi hardverski snimak. |
| 3.4 | **FACTORY velocity kalibracija sa uređaja** | Autoritet je izveden iz fajlova (FACTORY_ONLY), ne izmeren sa Pa800. |
| 3.5 | **Generisanje od nule** | Namerno NE — projekat je optimizator, ne generator (tvoja vizija 5.0). |
| 3.6 | **Veliki dataset-i** (Lakh, MAESTRO, Groove MIDI…) | Nisu integrisani: licence (CC BY-NC) ili veličina; 4.58/4.64 istraživanje ih je dokumentovalo kao study-only. |
| 3.7 | **Kompletna stara test arhiva** | Neki drevni suite-ovi traže fajlove van repoa (`/home/user/data/…`) koji ne postoje u čistom checkout-u — ne mogu se pokrenuti posle reset-a. Pokreće se kurirana lista (102 + 16 + 13 + 5 + npm 14). To je rupa u „sve je testirano” narativu — priznajem. |
| 3.8 | **CI/CD na GitHub-u** | Nema — sve provere su lokalne (push dokaz: `git ls-remote`). |
| 3.9 | **Perc kanal kao korpus** | Korpus je za bubanj-mašinske obrasce; tvoj **perc** kanal (čovek-swing, 589 nota) nema pandan u korpusu — zato se tu očekuje 0 poklapanja. |

## 4. Šta preporučujem kao sledeće (redosled)

1. **Device capture sa tvoje Pa800** → otključava A05, DNC/slap/pop, pravi velocity profil, i **jedini pravi test A01** (učitaj `session-sty-*.mid` na klavijaturu i javi mi šta kaže).
2. **WebAudio A/B** (Faza D) — čuj pre nego što učitaš; najveća rupa u iskustvu.
3. **Bank na svih 20 song19 fajlova** + tolerancija na swing (najbliži red umesto tačnog) za perc kanal.
4. Perzistencija sesija u UI.

---

*Ovo je stanje na 2026-09-05, grana `arena/01a06f02-dna`, HEAD nakon 4.65. Ako nešto od „ne radi” želiš da rešim prvo — reci redni broj (npr. 3.1), pa deremo.*
