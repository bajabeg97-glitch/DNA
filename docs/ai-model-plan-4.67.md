# AI Model — analiza projekta i plan (4.67)

Datum: 2026-09-05 · Analiza koda: `reports-max-4.67/01-code-analysis.json` · Matrica problema: `reports-max-4.67/02-remaining-problems-design.json` · Status: `dna-ai-model-analysis-status-4.67.json`

> Zatvaramo feature-razvoj. Ovaj dokument je **analiza + način rešavanja preostalih problema**, sa fokusom na AI model. Bez lažnih obećanja: sve što model „kaže” mora da bude izvodljivo dokazati iz izveštaja engine-a.

---

## 1. Stanje koda — mereno, ne osećaj

| Metrika | Vrednost |
|---|---|
| Python fajlova (sve, sa legacy testovima) | 291 |
| Engine modula u `dna_midi_studio/` (top-level) | 91 (~32.500 linija) |
| **Proizvodni lanac** (dostupno iz `session_pass`) | **12 modula** |
| Modula **van lanca** (legacy istraživanje 4.1x–4.5x) | **79** |
| Potpuno neadresiranih (nijedan test/root referenca) | 14 (`agent_runtime`, `dnc_engine`, `guitar_reconstruction`, `harmonic_reconstruction`, `independent_verifier`, `personal_profile`, `premium_workflow`, `production_adapter`, `regression_vault`, `release_readiness`, `rx_engine`, `session38_fixture`, `solo_enhancement`, `track_instrument_analysis`) |
| TODO/FIXME/HACK | 0 |
| Spoljne zavisnosti engine lanca | numpy (sve ostalo stdlib) |
| Legacy istraživački podsistemi | `ai_learning/` i sl. koriste torch — **nisu** u lancu |

**Zaključak:** projekat je *širok ali plitak po dubini proizvoda*: 91 modul, a korisnik vidi putanju od 12. AI model sloj treba da gradi **na 12-modulnom lancu kao jedinom izvoru istine**, a 79 legacy modula da tretira kao arhivu (osim ako ih neki problem eksplicitno ne zahteva).

## 2. Šta znači „radimo na AI modelu” ovde

Ne generišemo muziku (vizija 5.0: optimizator). AI model = **asistent koji razume, objašnjava, predlaže i pamti**, ali:

- svaka tvrdnja mora imati **izvor u izveštaju** (grounding, json-path);
- status akcija (READY/LOCKED/…) menja **samo engine + korisnik**, nikad model;
- model ne poznaje „možda” brojeve — ako podatka nema u izveštaju, kaže da ga nema;
- izlaz je na srpskom, kratak, sa opcijama.

## 3. Arhitektura (predlog)

```
Korisnik (UI chat)
   │  tekst
   ▼
┌────────────────────────────────────────────────┐
│ Assistant Core (novo, stdlib)                  │
│  • Session store (JSONL, per-session)          │
│  • NLU: intent + parametri iz teksta           │
│  • Router: tool pozivi (samo dozvoljeni)       │
│  • NLG: grounded objašnjenja iz izveštaja      │
│  • Brain interfejs: BrainV1 (deterministički)  │
│       └── BrainLLM (adapter: Ollama/OpenAI-    │
│           compatible) IZA ISTOG interfejsa     │
└───────────────┬────────────────────────────────┘
                │ tool pozivi (ograničeni)
                ▼
   Bridge REST (postojeći): analyze / apply / presets / session
                │
                ▼
   Session Pass (12-modulni deterministički engine, izvor istine)
                │
                ▼
   Izveštaj (JSON) ──► Grounding API: činjenice + json-path tagovi
```

Ključni princip: **model nikad ne vidi „sirovi svet”** — vidi samo (a) izveštaj engine-a, (b) dozvoljene tool pozive, (c) istoriju svoje sesije. Sve ostalo je van domašaja.

## 4. Grounding ugovor (najvažniji deo)

1. Model sme tvrditi **samo** ono što postoji u izveštaju (broj, status, gate, match).
2. Svaka tvrdnja može nositi `source: "report.grooveVsHuman[0].stdMs"` — UI može da je prikaže kao izvor.
3. Ako korisnik pita nešto van izveštaja („kako zvuči?”, „koji je ovo tonalitet?” ako ga nema) → odgovor: *„To ne mogu da tvrdim — nemam taj podatak u analizi. Mogu da…”* + ponuđene opcije.
4. Model ne može: da menja status akcije, da označi LOCKED kao primenljivo, da obeća da će nešto „zvučati bolje”.
5. Sve deterministički proverljivo: **eval harness** sa ~20 golden Q&A primera gde je očekivani odgovor unapred poznat iz fiksnih izveštaja (metrike: factual accuracy, tačnost odbijanja, format).

## 5. BrainV1 (prvi korak — stdlib, radi odmah, bez ključeva)

- **NLU v1 intents**: `analiziraj(fajl|preset)`, `primeni(akcije|sve)`, `objasni(akcija|gate|broj)`, `uporedi(fajl1,fajl2)`, `staDalje`, `pomoc`, `pravila` — parser: kanonski sinonimi (sr/bs), izvlačenje parametara (A0x ID-jeva, imena preseta/fajlova).
- **NLG v1**: predlošci nad sekcijama izveštaja (šta je izmereno → rečenica sa brojevima povučenim iz JSON-a; zašto je akcija LOCKED → rečenica iz gates/reason polja; šta dalje → pravila odluke iz statusa).
- **Predlozi (P08)**: na osnovu istorije sesija (P02) asistent predlaže šta da probaš (npr. A03 zahteva fajl sa potvrđenim echom — „ako mi daš takav fajl, mogu da ga primenim”).
- Sve u `dna_midi_studio/assistant/` (novi podpaket), testirano golden setom.

## 6. BrainLLM (kasnije, isti interfejs)

- Adapter za Ollama (lokalno, Windows: `ollama run`) ili OpenAI-compatible API — **preko korisničkog ključa, nikad u repou**.
- Isti grounding ugovor; sistem prompt kaže modelu da sme da koristi samo dozvoljene tool-ove i da citira izveštaj; izlaz se **validira** pre prikaza (brojevi se unakrsno proveravaju sa izveštajem — ako LLM „kaže” broj koji ne postoji, UI ga odbija ili obeležava).
- Eval: isti golden set kao BrainV1 — poredimo deterministički vs LLM kvalitet pre nego što LLM uopšte dobije pravo glasa.
- U Arena sandbox-u nema ključeva — BrainV1 je podrazumevani; LLM je opcioni.

## 7. Redosled (matrica: `reports-max-4.67/02-remaining-problems-design.json`)

| Prioritet | Šta | Zašto |
|---|---|---|
| **P0** | P01 AI Brain v1 (NLU+grounded NLG) + P02 session store + P08 predlozi | „Radimo na AI modelu” = ovo; sve ostalo je podrška |
| **P1** | P04 nearest-row + banka song19-02..20, P06 CI, P09 poruke grešaka | Jeftino, povećava poverenje |
| **P2** | P05 WebAudio A/B | Najveća rupa u iskustvu posle AI-ja |
| **P3** | P03 device capture + warrant ledger | Zahteva tvoj hardver; otključava A05/DNC i pravi Pa800 test |
| Odloženo | P07 legacy arhiva (XL), P10 dataset-i | Ne blokira AI model |

## 8. Poštenje i rizici

- **Ne overpromise**: BrainV1 nije „AGI” — jeste stvarno razumevanje ograničenog domena sa garancijom tačnosti. BrainLLM može da halucinira — zato validacija izlaza + eval pre upotrebe.
- Sandbox nema LLM ključeve; sve što gradimo mora da radi i bez njih.
- Srpski jezik (ekavica/ijekavica mešano) — sinonimi u NLU; NLG na neutralnoj osnovici.
- 79 legacy modula ostaje arhiva dok ih problem ne pozove (npr. `harmonic_reconstruction` ako zatreba harmonska analiza u A03/A04).

---

*Sledeći korak je tvoj izbor prioriteta (P0 odmah ili nešto drugo). Analiza i matrica su u JSON-ovima; ovaj dokument je čitljivi sažetak.*
