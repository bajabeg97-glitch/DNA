# Istraživanje: bass pitch slide, finese, echo/terca optimizacija — 4.56 research

Datum: 2026-09-05. Sve tvrdnje su izvršene (GitHub API + web + repo probe);
dokazi: `reports-max-4.56/01-world-scan-evidence.json`,
`reports-max-4.56/02-repo-gap-scan.json`. Ovaj .md je vodič, ne dokaz.

## 1. Bass pitch slide i „finese" — šta se uopšte može riješiti u MIDI 1.0

Fizika sviranja: slide = kontinualni prelaz između dva tona; hammer-on/pull-off
= ponovni napad bez trzanja; palm-mute/ghost = prigušenje; slap/pop = napad.
MIDI 1.0 nema „artikulaciju po noti" — zato se finese rešavaju na 4 načina:

| finese | mehanizam u MIDI 1.0 | izvodljivo? |
|---|---|---|
| **slide (bass/gitara)** | pitch bend rampa (14-bit) između nota; portamento CC84 + CC65 uz overlap nota (legato patch); pivot pitch za duge glisande | DA, ali zvuči samo na patch-evima koji podržavaju legato/PB; GM set — ne |
| **hammer-on / pull-off** | legato mod sinteze (overlap nota) ili keyswitch artikulacija po tačnom zvuku | delom (keyswitch samo uz tačan sound profile) |
| **DNC slide (Korg Pa)** | DNC trigeri / guitar-mode kontroleri — nedostaje javna spec | NE bez device snimka (naš policy: `NO_UNVERIFIED_DNC_SLIDE_TRIGGER`) |
| **palm-mute / ghost** | gate + dinamika (kratko/tiho) | DA — već dokazano u 4.55 |
| **slap / pop** | kratki akcenti u registru | DA kao kandidati (4.55); trigeri samo uz device snimak |

Zaključak: ono što **može** da se riješi bez rizika je (a) evidencija
slide/legato kandidata na našim kanalima (proširenje 4.55: `slide-candidate`
postoji u rečniku basa, ali engine ga još ne mapira), (b) **writer** koji bi
pisao pitch-bend rampe isključivo između dokazanih parova nota, uz kapiju da
Pa800 stilovi uopšte poštuju PB (device test — nije još snimljeno; PB u style
trackovima Korg Pa800 nije potvrđen u našoj 4.53 dokumentaciji).

## 2. Echo track / terce — ko je napravio rešenje?

Svet (izvršeni GitHub search):
- `optimize_existing_echo_terca`, `special_track_engine`, `dnc-slide-trigger`:
  **0 javnih pogodaka** — niko.
- „third voice" generacija: 1 repo (`NachinBombin/Midi-Manipulator`, HTML, bez
  licence, 0 zvjezdica, živi performance router) — nije oslonac.
- Korg DNC: 0 repoa; „korg style arranger": 1 web tribute bez optimizacije.
- Web: „echo" u Korg kontekstu javno = hardware delay efekat; echo/terca kao
  **track uloge** nisu standardizovane u javnim izvorima — to je naš
  unutrašnji aranžerski koncept (uloge `terca`, `echo` u katalogu, gramatikama
  i interakcijama: terca prati solo, echo odgovara ispod sola i ostaje
  sporadičan).

## 3. Šta naš repo već ima (i gdje je puklo) — izvršeni nalazi

Postoji bogata starija osnova:
- `premium_expression.py` (Session 24): ORNAMENT_KINDS grace/trill/slide/
  turnaround; RELATIONSHIP_KINDS third/echo; budžeti (ornament 24 / third 16 /
  echo 8); read-only planer sa sourceNoteUid + evidence.
- `solo_enhancement.py` (Session 5): zaštićeni solo, ornaments, thirds, echo,
  CC11; koristi `allocate_delay_track` (echo traži slobodan fizički track —
  SMF0 blokiran).
- `ai_learning/melodic_relationship.py`: engine koji generiše third/echo
  varijante (FACTORY_ONLY velocity autoritet).
- `instrument_behavior.py`: detekcija legato/slide-kandidata i
  solo ornament ponašanja.
- Interakcije/maskiranje: `arrangement_interaction.py` — terca/echo prate solo
  i nikad ga ne maskiraju.

**Gapovi (dokazano, vidi 02-repo-gap-scan.json):**
1. `test_echo_terca_engine_v413.py` — puna spec echo/terca **optimizacije**
   (`optimize_existing_echo_terca`): echo se poravnava na main tajming, tiši je
   od main sloja, kraćeg gate-a; terca = dijatonska terca na main tajmingu.
   Modul `special_track_engine` **ne postoji nigde** (ni lib/, ni git
   istorija) → specifikacija bez implementacije. Glavni kandidat za 4.56.
2. Legacy testovi 12–37 ere: 6 import failova (ravni moduli iz starog
   layouta) + 1 apsolutna putanja `/home/user/data/...` → konsolidacioni dug.
3. `ai_learning` engine ima pokvaren data path (`data/factory-velocity-profiles.json`
   umesto `factory-velocity-profiles.json` u rootu) i nije povezan u 4.x flow.
4. `lib/dna_midi_studio/` (63 fajla) = ogledalo koje driftuje.
5. 4.55 ne klasifikuje `slide-candidate` (rečnik ga ima; `instrument_behavior`
   već detektuje legato/slide u starijem sloju).

## 4. Gdje još leži unapređenje (preporuke, redom)

1. **4.56 Special Track Engine** (`dna_midi_studio/special_track_engine.py`):
   detekcija echo/terca slojeva (role iz postojeće evidencije) + optimizacija
   postojećih: echo → poravnanje na main tajming, gate, dinamika tiša od main
   po factory krivoj kanala; terca → dijatonska terca uz main, bez maskiranja
   sola; kapije: main netaknut, note sloja menjane samo uz factory dokaz,
   trigeri 0. Spec već postoji u v413 testu.
2. **Slide/legato finese u 4.55**: dodati `slide-candidate` (susedne note,
   mali interval ≤3, legato gap) + `hammer/pull` kandidate; ostaje
   SEMANTIC_ONLY. Zaseban eksperiment: PB ramp writer sa kapijom
   „device PB support nije snimljen → samo software-test izveštaj".
3. **Test farm konsolidacija**: portovati ili ukloniti 7 import-broken legacy
   testova; popraviti data path ai_learning; sinhronizovati lib/ ogledalo.
4. Echo/terca optimizacija se oslanja na 4.52 maskiranje (echo vs solo
   overlap) — kad 4.56 bude radio, priključiti mix_engine izveštaj.

Nijedna od ovih stavki ne dira zaštićeni originalni materijal bez kapija;
velocity autoritet ostaje FACTORY_ONLY; DNC trigeri ostaju zaključani.
