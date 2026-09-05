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

## 5.0 AI Session Musician — „zna kako svira i šta je gdje"
Integracija svega u jedan prolaz:
1. **Šta je gdje:** semantička mapa fajla (kanal → zvuk → uloga → registar → deo pesme).
2. **Šta instrument može:** katalog + factory dokazi (postoji u `complete-instrument-profiles-4.44.json`).
3. **Kako se svira:** 4.54 groove + 4.55 tehnike.
4. **Miks:** 4.52 slojevi i balans.
Izlaz: analiza + primenjen aranžman + izveštaj sa kapijama. Bez .md dokaza — samo
izvršeni bytes/metrike/testovi.
