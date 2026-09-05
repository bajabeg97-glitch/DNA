# DNA Composer — plan generisanja i rekonstrukcije MIDI-ja (4.69, planska sesija)

Datum: 2026-09-06 · Status: **plan — dogovoren sa korisnikom** · Mašinski čitljiv: `reports-max-4.69/midi-generation-plan.json` · Dokaz plana: `test_generation_plan_v469.py`

> Odluke korisnika (ova sesija): **(1) puna generacija od nule + rekonstrukcija** (vizija 5.0 „samo optimizacija” se širi),
> **(2) bez API ključeva — sve lokalno u sandboxu (CPU)**, **(3) ulaz = MIDI skica + tekst opis zajedno**,
> **(4) izlaz = linearni „pesma” MIDI (SMF)**, Pa800-kompatibilan.
> Ova sesija pravi SAMO plan. Izvršenje počinje od sesije S1 (miljokaz 4.69).

---

## 0. Broj sesija — odgovor na pitanje

**8 radnih sesija (S1–S8, miljokazi 4.69–4.76) + ova planska (S0)**. Prvi slušljiv rezultat (generisana pesma) stiže
na kraju **S2**; Suno-like rekonstrukcija na kraju **S4**; tekst kontrola + UI na kraju **S5**.
Opciono: **+1 sesija** za verifikaciju na pravom Pa800 hardveru (tvoj uređaj, snimak stilova → najveći skok autentičnosti)
i **+1 sesija** (kasnije, ako dozvoliš cloud/GPU) za fine-tune otvorenih baza — planirano kao jasna, neblokirajuća nadogradnja.

Zašto baš 8: svaka sesija = jedan miljokaz sa commit-om, testovima, JSON dokazima i kapijom; koraci su podeljeni tako da
svaka sesija ima samostalnu vrednost i da kapija može da zaustavi/okrene pravac pre nego što se potroši sledeća sesija.

---

## 1. Cilj (merljivo)

Generativni sloj **„DNA Composer”** koji, iz (a) proste/loše MIDI skice i/ili (b) tekst opisa (sr/bs/en),
pravi **kompleksan linearni aranžman kao pesmu (SMF)** — uvod → stih/refren → fill → kraj — koji:

1. **prolazi Pa800 validator 100%** (kanali uloga po fabričkoj mapi, GM bubnjevi na kanalima 9/10, 16-grid, CC11 opseg, bez neverifikovanih program/bank/DNC/slap/pop/keyswitch trigera);
2. je **kompleksan**: ≥ 4 uloge istovremeno (bass, drums, perc, acc/harmony, opciono solo), sekcije, fill-evi, dinamika (CC11), humanizovan groove (std u granicama ljudske reference 4.54), opciono echo/terca gde dokaz postoji;
3. **pamti kontekst sesije** (BrainV1 + session store) i dozvoljava iteraciju tekstom: „pojačaj bubanj”, „duži uvod”, „u stilu reference-style”;
4. o svakoj odluci daje **scorecard sa izvorima** (koji templejt, koji pattern, koja evidencija — ista grounding disciplina kao BrainV1);
5. **nikad ne menja original** (upload se ne modifikuje; rezultat je uvek novi artefakt);
6. radi **bez API ključeva**, na 2 vCPU / ~4 GB RAM, Python 3.11 (+numpy) i Node 22.

**Šta „Suno-like” znači ovde (pošteno):** Suno nivo zahteva GPU/cloud korpuse — to lokalno ne dostižemo.
Cilj je *isti tok iskustva* (doneseš nešto → opišeš → dobiješ kompletnu pesmu → iteriraš rečima), sa garancijom
Pa800-valjanosti i stila iz fabričke evidencije koju Suno nema. Kvalitet mere rubrika slušanja + metrike; v1 je
„dobar aranžer koji svira po pravilima”, a modeli (S3+) dodaju varijaciju koju pravila ne znaju.

## 2. Šta već imamo (gradimo na ovome)

- **Engine lanac (12 modula, istina projekta):** `session_pass` → role patterni (4.59), groove vs ljudska referenca
  (4.54, 110.313 uzoraka, std 27,96 ms), miks CC7/CC11 i masking (4.52), tehnike i warrant ledger (4.55),
  echo/terca + special track (4.57), STY/SMF0 izvoz (4.53), reference bank korisnika (4.65).
- **Materijal:** 460 fabričkih drum patterna (`vendor-max-4.64/dmp-midi`, MIT, atribucija u NOTICE),
  reference korisnika, izlazi engine-a u `artifacts-max-4.48..4.65`, brain + sesije + REST (4.68),
  UI bridge (node, bez npm zavisnosti).
- **Kontrolni sloj:** BrainV1 deterministički (NLU/NLG, grounding, sesije); BrainLLM adapter projektovan
  (Ollama/OpenAI-compatible, IZA istog interfejsa) — **ne koristi se dok ne dozvoliš ključeve**.
- **Ograničenja sandboxa:** 2 vCPU, ~4 GB RAM → veličine modela i korpusa se planiraju za CPU.

## 3. Arhitektura cilja

```
Ulaz:  MIDI skica (upload)  +  tekst (sr/bs/en)        ← sesija pamti istoriju
   │
   ▼
┌──────────────────────────────────────────────────────────────┐
│ 1. RAZUMEVANJE (postojeće)                                     │
│    session_pass analiza (uloge, sekcije, groove, tehnike)      │
│    + tekst→parametri: žanr, BPM, energija, sekcije, instrum.   │
│    (v1: leksikon+rules; BrainLLM kasnije, isti izlazni format) │
└──────────────────────────────┬───────────────────────────────┘
                               ▼  „condition” (kanonski JSON)
┌──────────────────────────────────────────────────────────────┐
│ 2. GENERISANJE (novo, 3 nivoa, hijerarhijski)                  │
│    a) Deterministički kompozitor (S2): templejti + pattern     │
│       evidencija + groove/CC11 pravila  → uvek validan izlaz   │
│    b) Statistički sloj (S3): n-gram/Markov nad token korpusom  │
│       → varijacije koje predlaže, kompozitor ih potvrđuje      │
│    c) Mikro-GPT (S3, numpy, ≤ ~2M param): kreativni predlog    │
│       (sampler), uvek kroz kapiju validatora                   │
│    REKONSTRUKCIJA (S4): ulazni MIDI se čuva netaknut; model    │
│    dodaje/popunjava samo dozvoljene uloge/sekcije (infill po   │
│    ulogama, vođeno analizom)                                    │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. Pa800 VALIDATOR + SKLAPANJE (S2/S7)                         │
│    struktura pesme, kanali, GM set, 16-grid, CC11,           │
│    zabranjeni trigeri → SMF0 (pesma) + scorecard (izvori)     │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
  Izlaz: novi .mid + scorecard + auto-analiza kroz session-pass
         (akcije/READY kapije važe i dalje; UI „analiziraj generisano”)
```

Princip: **model predlaže, kapije odlučuju, engine je istina.** Generisano se uvek može provući kroz session-pass
i tretirati kao svaki drugi materijal (READY akcije, optimizacija, izvoz STY).

## 4. Raspored po sesijama

Legenda: `[commit]` = dokazi obavezni (testovi + JSON artefakti + docs, pravilo „.md nije dokaz”); `▶ kapija` = tačka odluke.

### S1 — Korpus + tokenski jezik + Pa800 validator (4.69)
- `composer_corpus.py` (stdlib): kanonski „score language” (JSONL događaji: sekcija/uloga/korak/tick/velocity/CC11/izvor) + `tokenizer_v1` (vocab ≤ 1024) sa **roundtrip testom** (tokeni→događaji==ulaz; MIDI→tokeni→MIDI≈fajl).
- `pa800_validator.py`: provera strukture/kanala/GM/CC11/zabranjenih trigera; **100% korpusa mora proći**.
- Korpus (manifest sa sha256 + licencom + `originKind`): (a) izlazi engine-a i reference korisnika u `artifacts-*`, (b) dmp-midi 460 patterna, (c) sintetički curriculum generisan kompozitorom-v0, (d) opciono permissive korpusi sa neta (Magenta Groove CC-BY i sl., provera licence pri preuzimanju, ≤ 200 MB), (e) korisnikovi Pa800 stilovi (opt-in folder, gitignorisan).
- Cilj: ≥ 300 kompletnih aranžmana / ≥ 2.000 „song-momenata”, sve validirano; `corpus-stats-4.69.json`.
- ▶ **D1**: korpus + validator + roundtrip zeleni; korisnik čuje 3 sintetičke probe (S1 kraj) i potvrđuje smer.

### S2 — Deterministički kompozitor, prvi slušljiv rezultat (4.70)
- `composer_engine.py`: strukturna gramatika pesme (Intro/Verse/Chorus/Bridge/Fill/Outro + broj taktova),
  uloge iz pattern evidencije (dmp-midi + user bank + role patterni), harmonija/akordi iz role evidencije,
  humanizacija (ljudska std 4.54), CC11 oblikovanje (4.52), izlaz SMF0 + **scorecard JSON**.
- 10 stilskih templejta (rock/funk/ballad/folk… po evidenciji fabričkih patterna) × seed → reproduktibilno.
- Prihvatanje: 30 pesama generisano, validator 100%, regresija zelena, korisnik sluša na Pa800 (ili MIDI preview).
- ▶ **D2a** (prva polovina): kvalitet baze — da li je vredno ići u neuro modele.

### S3 — Statistički + mikro-neuro sloj (4.71)
- n-gram/Markov nad token korpusom (CPU, trenutno) → predlozi varijacija; potom **mikro-GPT u čistom numpy**
  (≤ ~2M parametara, ctx ≤ 1024, trening korpus 1–5M tokena; checkpoint u `artifacts-max-4.71/`, gitignorisano ako je veliko).
- Hibridna petlja: neuro/statistički predlog → validator → fallback na deterministički ako ne prođe → rezultat.
- Eval: validnost 100% (kroz kapiju), stilsko poklapanje, raznovrsnost (distancija između generacija), loss krive.
- ▶ **D2**: slepo A/B korisnika (deterministički vs neuro, 5 parova) → bira smer (nastavak treninga / statistički dovoljan).

### S4 — Rekonstrukcija, Suno-like jezgro (4.72)
- `reconstruct`: bilo kakav MIDI → session-pass analiza → **kept trekovi se ne diraju** (fidelity 100% na njima),
  model+kompozitor popunjavaju dozvoljene uloge/sekcije: prazne uloge, fill-evi, produkžetak na celu strukturu pesme,
  re-orchestracija; echo/terca/fills samo gde analiza daje dokaz (kapije 4.57/4.59).
- Trening ciljevi: sintetički parovi (prosto→kompletno) iz korpusa S1; evaluacija: zadržanost melodije/harmonije
  (pitch-class preklapanje), groove u granicama, struktura.
- Prihvatanje: 5 zlatnih ulaza (uključujući 1 pravi Pa800 izvoz korisnika) → izlazi validni + scorecard;
  korisnik sluša i daje fidbek.
- ▶ **D3**: rekonstrukcija korisna? (ako nije — S6 se fokusira na korpus korisnikovih stilova).

### S5 — Tekst kontrola + UI (4.73)
- `text_to_condition` (sr/bs/en): žanr/BPM/energija/sekcije/instrumentacija/„u stilu X” (X = prethodno analiziran fajl);
  integracija sa BrainV1: novi intenti `generiši`/`rekonstruiši`/`produži`/`promeni stil` + grounded scorecard odgovori.
- Bridge: `POST /api/generate` {text?, midi?, seed?, style?} → rezultat u sesiju; UI panel „Generiši” + preview + download +
  dugme **„analiziraj generisano”** (auto session-pass na rezultat, READY kapije važe).
- Prihvatanje: zlatni tekst→parametri testovi; e2e node test; korisnik proba u live preview.
- ▶ **D3b**: iskustvo toka (generiši→analiziraj→primeni) radi od kraja do kraja.

### S6 — Nadogradnja modela podacima korisnika (4.74)
- Korisnik donese svoje Pa800 stilove/exporte (folder, opt-in) → najveći autentični skok („na temelju Pa800” bukvalno);
  curriculum: teški primeri + augmentacije; duži trening ako CPU vreme dozvoli; A/B pre/posle.
- (Povezano sa 4.67 P03 device capture: snimak fabričkih stilova sa tvog uređaja otključava i A05/DNC evidenciju.)
- Prihvatanje: A/B poboljšanje po rubrici; regresija kompletna.

### S7 — Kvalitet, rubni slučajevi, isporuka (4.75)
- Gustina (izbegavanje kolizija nota — masking 4.52), CC11 krive, završeci, opsezi voicinga, reproduktibilnost seed-a,
  performanse (generacija < 30 s tipično), memorija; Windows launcher-i; UI srpski; kompletna regresija.
- Prihvatanje: stres set (50 generacija + 20 rekonstrukcija) bez grešaka, sve suite zelene.

### S8 — Završna evaluacija + zlatni set + paket (4.76)
- Golden eval: ≥ 30 generacija + ≥ 20 rekonstrukcija sa očekivanim svojstvima (asserti); metrike JSON;
  rubrika slušanja na Pa800; docs; demo u preview; uporedba sa ciljem (šta jeste, šta je ograničeno CPU/korpusom);
  **dokumentovana opcija cloud/GPU nadogradnje** (MuPT/Magenta fine-tune) ako kasnije dozvoliš.

## 5. Kapije i zaustavljanje

| Kapija | Kada | Odluka |
|---|---|---|
| D1 | kraj S1 | korpus/validator dovoljno dobri → S2; inače još podataka u S1 |
| D2 | kraj S3 | neuro vredi? → S4; inače statistički+deterministički je v1 |
| D3 | kraj S4/S5 | rekonstrukcija korisna? → S6; inače fokus na generaciju |
| D4 | kraj S6 | korisnikov korpus pomaže? → S7; inače manji S7+S8 |

Svaka kapija ima dokaz u JSON artefaktu te sesije; korisnik odlučuje na osnovu slušanja + metrika, ne osećaja.

## 6. Pravila koja ostaju na snazi (iz prethodnih sesija)

1. Bez API ključeva u repou/sandboxu; sve mora raditi i offline.
2. Upload se nikad ne menja; rezultati su uvek novi artefakti (`artifacts-max-*`).
3. Statusi/akcije menja samo engine + korisnik; model ne menja stanje; UI bira samo READY akcije.
4. Zabranjeni neprovereni program/bank/CC/DNC/slap/pop/keyswitch trigeri → generisani izlaz ih nikad ne sadrži.
5. Licence: samo permissive uz atribuciju (MIT/CC-BY/CC0); GPL/AGPL samo za proučavanje; svaki korpus ima manifest.
6. Svaka tvrdnja modela ima izvor (scorecard/grounding); model ne izmišlja brojeve.
7. Izlaz na srpskom (ekavica/ijekavica); deterministički proverljivo; regresija pre svakog commita.
8. BrainLLM/Ollama adapter ostaje projektovan (4.67) i NE koristi se dok ne odobriš ključeve.

## 7. Rizici (pošteno)

- **CPU trening je spor**: mikro-GPT ≤ ~2M parametara; ako ne stane u sesiju, statistički sloj je v1 (kapija D2).
- **Korpus je mali** u poređenju sa Suno skalom → kvalitet varijacije raste sa korisnikovim stilovima (S6), ne magijom.
- **Rizik „sve zvuči isto”** (templejti) → mere se raznovrsnost (S3) i korisnik A/B.
- **Nema audio previewa** (MIDI-only) → slušanje na Pa800 ili MIDI playeru; WebAudio A/B (4.67 P05) ostaje opciona nadogradnja.
- **Overfit na fabričke pattern-e** → evaluacija stratifikovana po `originKind`.

---

*Sledeći korak: S1 (4.69) — korpus + tokenizer + Pa800 validator. Ova sesija ne dira engine/brain; samo plan + dokaz plana.*
