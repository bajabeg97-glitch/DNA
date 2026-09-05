# Roadmap — „AI koji zna kako svira i šta je gdje" (dodaje se u postojeći projekat)

Plan je odgovor na zahtev: ne osmišljavamo novi projekat — **svaki korak se
ugrađuje u `dna_midi_studio/`**, redom, sa testovima i kapijama (dokument nije dokaz).

## 4.52 Mix Engineer — DONE (ovaj korak)
`mix_engine.py` — gain staging kroz CC11 na slojevima, detekcija podloga/preklapanja,
izveštaj o perkusionim akcentima na bubanj kanalu. Dokazi u `artifacts-max-4.52/`.

## 4.53 Groove Engine — logika današnjih inženjera
Ono što pravi svirač/groove ima, a mi merimo:
- **Mikrotajming:** swing %, push/pull, humanizacija unutar tolerance (bez lomljenja
  grid-a stila); izvor istine = **Groove MIDI Dataset** (pravi bubnjari, CC-BY) —
  preuzimanje + statistika, ne TF modeli.
- **Hijerarhija akcenata:** downbeat > backbeat > offbeat; ghost notes; kick/snare pocket.
- **Dužine nota i artikulacija** po ulozi (staccato komp, legato pad).
- Ducking: CC11 dip na podlogama posle kick/snare udara (midi sidechain).

## 4.54 Instrumentalne tehnike — „slapp i takve fore"
Na postojeću bazu (player modeli: `palm-mute-candidate`, `ghost`, `swell-candidate`,
`kick-pocket`...) dodaju se izvodljive generativne gramatike:
- **Slap/pop bas:** thumb = kratak akcenat na toniku/kvinti (niži registar),
  pop = akcenat na visokom tonu (dur ~24–30), ghost = tiho, mutirano;
  obrasci: 16-ine sa thumb/ghost/pop grupama; isključivo za bas zvukove
  (slap familija) uz factory velocity dokaze.
- **Palm-mute gitara / ritam:** kratki gate, down-stroke bias.
- **Terce/echo/solo:** već postoji u katalogu — povezivanje sa groove engine-om.

## 4.55/5.0 AI Session Musician — „zna kako svira i šta je gdje"
Integracija svega u jedan prolaz:
1. **Šta je gdje:** semantička mapa fajla (kanal → zvuk → uloga → registar → deo pesme).
2. **Šta instrument može:** katalog + factory dokazi (postoji u `complete-instrument-profiles-4.44.json`).
3. **Kako se svira:** 4.53 groove + 4.54 tehnike.
4. **Miks:** 4.52 slojevi i balans.
Izlaz: analiza + primenjen aranžman + izveštaj sa kapijama. Bez .md dokaza — samo
izvršeni bytes/metrike/testovi.
