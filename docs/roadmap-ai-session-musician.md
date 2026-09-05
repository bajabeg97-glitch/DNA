# Roadmap — „AI koji zna kako svira i šta je gdje" (dodaje se u postojeći projekat)

Plan je odgovor na zahtev: ne osmišljavamo novi projekat — **svaki korak se
ugrađuje u `dna_midi_studio/`**, redom, sa testovima i kapijama (dokument nije dokaz).

## 4.52 Mix Engineer — DONE
`mix_engine.py` — gain staging kroz CC7/CC11 na slojevima, detekcija
podloga/preklapanja (masking matrica), izveštaj o perkusionim akcentima na
bubanj kanalu. Dokazi u `artifacts-max-4.52/`.

## 4.53 Korg STY Mapper — DONE
`sty_mapper.py` — mapiranje/izvoz Pa800 style SMF0 (markeri, sekcije, uloge).
Dokazi u `artifacts-max-4.53/`.

## 4.54 Groove Engine — DONE
`groove_engine.py` — merenje tajminga (16-tinski grid, ms) i dinamike naših
bubanj/perc kanala + ljudska referenca (izvedena statistika iz Magenta Groove
MIDI korpusa preko wobblemidi profila; 110.313 uzoraka, std 27,96 ms).
Dokazi u `artifacts-max-4.54/`.

## 4.55 Instrumentalne tehnike — DONE (ovaj korak)
`instrument_techniques.py` — dokazni engine za slap/pop/ghost/palm-mute:
klasifikuje fabričke note/udare isključivo u postojeći rečnik profila
(`complete-instrument-profiles-4.44.json`), read-only, 0 trigera (warrant
ledger), kapije dokazuju da se ništa ne emituje. Dokazi u
`artifacts-max-4.55/`.

## 4.56 Special-track research — DONE
Bass-slide finese (šta je rešivo u MIDI 1.0: pitch bend / portamento CC84+CC65 /
overlap-legato / keyswitch), svetska potraga za echo/terca optimizacijom
(**0 javnih rešenja**) i repo gap analiza. Dokazi: `reports-max-4.56/`,
vodič `docs/finesse-special-tracks-research.md`.

## 4.57 Special Track Engine — DONE (ovaj korak)
`special_track_engine.py` — echo/terca optimizacija iz v413 spec (spec je
ranije postojao bez modula): echo REPAIR/REBUILD, terca = dijatonska terca na
main tajmingu uz harmony dokaz, inferencija neimenovanih aux treka; main se
nikada ne menja, 0 trigera. Dokazi: `artifacts-max-4.57/`, guide
`docs/studio-special-track-4.57.md`.

## 4.61 Session Bridge — DONE (Faza B)
`dna_bridge.mjs` — node:http server bez npm zavisnosti: UI na /, upload
`POST /api/analyze` -> Session Pass -> izveštaj + artefakti, demo preseti,
download. HTTP izvršeno na 4 uzorka (2 artefakta po stil fajlu). Dokazi:
`artifacts-max-4.61/bridge-run.json`, vodič `docs/session-bridge-4.61.md`.

## 4.60 Session Pass — DONE (Faza A)
`session_pass.py` — jedan CLI poziv kroz sve engine-e (4.52–4.59) → jedan
izveštaj: per-role patterni, groove vs ljudska referenca, tehnike, echo/terca
sken, miks plan i 5 akcija (A01 STY_EXPORT, A02 percussion CC11, A03
echo/terca, A04 fills, A05 LOCKED trigeri). `--apply-safe` piše nove artefakte,
izvor se nikad ne menja. Dokazi: `artifacts-max-4.60/`, vodič
`docs/session-pass-4.60.md`.

## 5.0 DNA Optimizer — „Suno iskustvo", samo optimizacija, na Korg Pa800 temelju
Proizvodni cilj (korisnik): korisnik donese fajl → sistem razume, predloži,
korisnik klikne, dobije Pa800 rezultat. Nema generisanja od nule — sve je
optimizacija postojećeg materijala, sve akcije sa dokazom i kapijom.
Vizija: `docs/dna-optimizer-vision.md`.

Integracija svega u jedan prolaz (faze):
1. **Šta je gdje:** semantička mapa fajla — kanal → zvuk → uloga → registar →
   deo pesme (4.51, 4.59 role patterni, GUI analiza).
2. **Šta instrument može:** katalog + factory dokazi
   (`complete-instrument-profiles-4.44.json`).
3. **Kako se svira:** 4.54 groove + 4.55 tehnike + 4.57 echo/terca.
4. **Miks:** 4.52 slojevi i balans.
5. **Pa800 izlaz:** 4.53 STY mapper (SMF0 + markeri + setup iz dokaza).
6. **Faze proizvoda:** A objedinjeni prolaz (CLI, 90 % postoji) → B Node
   bridge bez dependencija → C React tok (upload/plan/apply/izvoz) →
   D WebAudio A/B audicija u browseru.

Izlaz svakog koraka: analiza + primenjen aranžman + izveštaj sa kapijama.
Bez .md dokaza — samo izvršeni bytes/metrike/testovi (dokument nije dokaz).
