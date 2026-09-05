# DNA Optimizer — proizvodna vizija: „Suno iskustvo", ali SAMO optimizacija, na Korg Pa800 temelju

Datum: 2026-09-05. Cilj (korisnik): da radi „kao Suno" — korisnik nešto donese,
sistem razume, ponudi, korisnik klikne, dobije rezultat — **ali nema generisanja
pesme od nule**: sve je optimizacija postojećeg materijala, a izlaz je vezan za
Korg Pa800 (stilski fajl + SMF + izveštaj).

## Šta Suno jeste, a šta mi radimo umesto toga

| Suno | DNA Optimizer (naš) |
|---|---|
| prompt: „upbeat funk, 90 bpm" | upload: `.mid` / `.sty` / SMF sa Pa800 markerima (ili ceo folder) |
| model generiše pesmu od nule | **nema generisanja** — analiza meri šta fabrika već svira |
| stil model = naučen iz korpusa | stil znanje = role/pattern evidencija + Pa800 layout (markeri, CV, sekcije) |
| „Make a Song" → 2 pesme | „Optimizuj" → plan akcija, svaka sa dokazom i kapijom |
| iterate / regenerate | korisnik bira akcije (toggle): popuni prazne slotove, uravnoteži slojeve, echo/terca, izvezi STY |
| izlaz: audio 2 min | izlaz: **Pa800-importabilan `.sty`** (SMF0 + markeri) + optimizovan SMF + izveštaj sa kapijama |

## Šta sistem „zna" (sve izvršeno, u repou, miljokazi)

1. **Šta je gdje** — role mapiranje kanala + aranžerske uloge + semantička mapa:
   4.51 studio flow, 4.59 role patterni (bass/drums/perc/acc/**solo** sa melodijskim
   profilom), GUI analiza (`musicAnalysis.js`).
2. **Šta instrument može** — `complete-instrument-profiles-4.44.json`, factory
   velocity autoritet, rečnici tehnika.
3. **Kako se svira** — 4.54 groove (grid vs ljudska referenca), 4.55 tehnike
   (slap/pop/ghost/palm-mute kandidati, warrant ledger), 4.57 echo/terca.
4. **Miks** — 4.52 CC7/CC11 slojevi i masking.
5. **Izlaz za Pa800** — 4.53 STY mapper: markeri, sekcije, uloge, SMF0 izvoz,
   setup samo iz dokaza.

## Korisnički tok (proizvod)

1. **Upload** (drag&drop; `.mid`, `.sty`; markeri se prepoznaju).
2. **Analiza** (GUI + engine-i) → „šta je gdje": kanali, role, ton, markeri,
   pokrivenost CV slotova, groove/tajming činjenice, tehnike, maskiranje.
3. **Plan optimizacije** — lista akcija sa statusom:
   - `DOKAZANO — primeni` (npr. CC11 balans slojeva; popuna praznih slotova
     akordnim/komp glasovima; echo/terca REPAIR; STY setup iz postojećeg zvuka);
   - `ZAHTEVA ODLUKU` (npr. uredi echo REBUILD, terca bez harmony dokaza → PRESERVE);
   - `ZAKLJUČANO` (DNC/slap/pop trigeri bez device snimka; promena zvuka bez
     dokaza; velocity izvan factory krive).
4. **Pregled/audicija**: original vs optimizovano (numerički dif + kasnije
   zvuk u browseru preko WebAudio GM sintetizatora — TODO faza).
5. **Izvoz**: Pa800 `.sty` (SMF0 + `i1cv1...e2cv1` markeri) ili SMF; original
   se nikad ne prepisuje; izveštaj (gates + sha256 + šta je menjano i zašto).

## Arhitektura (faze)

- **Faza A — konsolidacija (sada)**: svi engine-i u `dna_midi_studio/`,
  stdlib-only; jedan ulaz → jedan objedinjeni izveštaj (CLI). Već postoji 90 %.
- **Faza B — bridge**: mali Node server (node:http + child_process, bez npm
  dependencija) koji servira statiku + poziva Python engine-e (JSON u/iz).
  Provereno: `registry.npmjs.org` je dostupan (200), Python 3.11 u sandboxu.
- **Faza C — frontend tok**: postojeći React/Vite UI (dark studio) dobija
  korake 1–5; akcije se primenjuju samo one koje korisnik potvrdi.
- **Faza D — audicija**: WebAudio GM sinteza za A/B preslušavanje SMF-a u
  browseru (bez vanjskih dependencija — sopstveni mali synth/player).

## Nepromenjivi uslovi (engines to enforce, ne kršimo nikad)

- originalni fajl se nikada ne menja (izlaz u `artifacts-*`);
- velocity autoritet: FACTORY_ONLY; bank/program bez dokaza se ne emituje;
- DNC/slap/pop trigeri zaključani do device snimka (warrant);
- svaka akcija u izveštaju ima reason + gate; report se računa kao dokaz;
- sve radi bez mreže i bez dodatnih instalacija (stdlib + ugrađeni JS).

## Šta nedostaje do „Suno osećaja" (iskreno)

- audicija zvuka u browseru (Faza D) — bez nje korisnik vidi brojeve, ne čuje;
- perzistencija projekata/istorija uploada (localStorage u Fazi C);
- „klikni-primeni-sve-dokazano" jedno dugme sa A/B izveštajem (Faza C);
- desktop verzija nije cilj — web je dovoljan (Suno je web).
