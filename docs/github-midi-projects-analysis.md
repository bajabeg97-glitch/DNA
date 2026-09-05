# GitHub MIDI projekti — analiza pokrivenosti (generisanje i aranžeri) — 4.58

Datum: 2026-09-05. Metoda (izvršeno): `gh api` repo metapodaci (zvezdice,
licence, jezici, opisi) + README keyword-signal sken za 20 projekata.
Sirovi signali i pokrivenost: `reports-max-4.58/01-github-projects.json`.
Ovaj .md je vodič, ne dokaz.

## Šta najjači projekti pokrivaju

### 1. Generisanje — ML modeli (najjači po zvezdama)
| projekat | ★ | licenca | pokriva |
|---|---|---|---|
| magenta/magenta | 19.800 | Apache-2.0 | MusicVAE, Performance/Melody/Improv RNN, Groove — generisanje MIDI-ja modelima |
| microsoft/muzic | 4.951 | MIT | LLM simbolička muzika: razumevanje + generisanje (BERT/tokenizer istraživanja, akompanjman) |
| NotaGen | 1.224 | MIT | LLM generisanje simboličke muzike |
| MidiTok | 892 | MIT | MIDI → token vokabulari (REMI/TSD/CP) za ML |
| muspy | 525 | MIT | dataset alati + objektivne metrike evaluacije |
| midi-model / sym-diffusion / MIDI-GPT | 377/280/97 | Apache/MIT | transformer/difuzija/GPT-2 generisanje |

Zajedničko: teške ML zavisnosti (TF/PyTorch/C++), generišu „od nule", rade na
tokenima; nijedan ne optimizuje **postojeći** stilski fajl na osnovu dokaza.

### 2. Aranžeri / auto-akompanjman (rule-based)
| projekat | ★ | licenca | pokriva |
|---|---|---|---|
| JJazzLab | 586 | LGPL-2.1 (Java) | backing tracks iz chord sheet-a: ritam engine, delovi pesme, mute/fill — **najbliža aranžer ideja u OSS** |
| Cadenza | 5 | GPL-3.0 (C++/JUCE) | open arranger workstation |
| BandInMuseScore | 37 | GPL-3.0 (QML) | Band-in-a-Box stil akompanjman u MuseScore |
| Favorites-with-Style | 4 | GPL-3.0 (Java) | MIDI aranžer za auto-accompaniment pesme |
| autoaccompany | 2 | MIT (JS) | web akompanjman: akord → stilski pattern |
| fleet-midi-bass | 0 | MIT (Python) | ternarni bas generator („MIDI agent") |

### 3. Analiza/evaluacija/IO
music21 (BSD-3, 2.573★), pretty-midi (MIT, 1.036★), mido (MIT, 1.638★),
musicaiz (AGPL, 192★), mgeval, MusPy — nota/harmonija/metrike/IO.

## Šta NIKO ne pokriva (naša niša, dokazano 4.52–4.57)

- evidencijski vođena optimizacija postojećeg fajla (ništa se ne menja bez dokaza);
- Korg Pa style SMF uloge/markere/sections (4.53);
- groove evidencija protiv pravih bubnjara (4.54);
- slap/pop/ghost/palm-mute kandidati + warrant ledger bez trigera (4.55);
- echo/terca special-track optimizacija (4.57) — **0 javnih rešenja**;
- CC7/CC11 masking gain + factory-velocity autoritet (4.52).

## Reuse odluka

- **MIT/Apache/BSD** (mido, pretty-midi, music21, MidiTok, muspy, NotaGen,
  MIDI-GPT, autoaccompany, fleet-midi-bass, Muzic, magenta): sme se proučiti i
  ugraditi uz atribuciju. Već koristimo mido kao nezavisni verifikator (dev-only).
- **GPL/AGPL/LGPL** (Cadenza, BandInMuseScore, Favorites-with-Style, expremigen,
  musicaiz, JJazzLab): **studija samo** — ne ugrađujemo u MIT repo.
- Nijedan projekat ne donosi algoritam koji već nemamo: sve što nam treba za
  aranžersku optimizaciju je unutra, testirano i push-ovano.


## File-level nalazi (iz samih projekata) — 02-project-file-inventory.json

Dubinski sken stabala 20 repoa (`gh api git/trees`) — šta stvarno leži u
fajlovima, ne samo u README-u:

- **JJazzLab (LGPL)**: `Rhythms.zip` (1,85 MB ritam-pattern biblioteka),
  `YamahaDefaultFiles.zip` (stvarni Yamaha style fajlovi), spec PDF. Najbogatija
  stvarna pattern baza u OSS aranžeru — studija samo.
- **Cadenza (GPL)** i **Favorites-with-Style (GPL)**: kompletne aranžer
  arhitekture (`StyleLoader/StyleEngine/StyleGenerator`, `Casm.java` — Yamaha
  CASM parser). Dokaz da style-aranžeri u OSS postoje, ali pod GPL i bez
  Korg Pa + evidencijskog pristupa.
- **BandInMuseScore (GPL)**: `MMA-Styles.md` — imenik MMA akompanjman stilova.
- **music21 (BSD)**: `chord/` (196 KB), `scale/` (127 KB), `harmony.py`,
  `voiceLeading.py` — najdublja teorijska baza; kandidat za studiju uz
  atribuciju, ne za ugradnju (zavisnost).
- **musicaiz (AGPL)**: `keys.py`, `chords.py`, `harmonic_shift.py` — studija.
- **magenta / muzic / NotaGen / MIDI-GPT / midi-model**: modeli + tokenizeri
  (drums_rnn, melody_rnn, performance_rnn, music_vae, musecoco, telemelody...).
- **wobblemidi (MIT)**: jedini projekat iz ove liste koji smo STVARNO ugradili
  (4.54: `baseline/human-groove-rock-derived.json`, sha256 provenance).

Reuse odluka je ista kao u tabeli iznad: MIT/Apache/BSD uz atribuciju;
GPL/AGPL/LGPL studija samo. Nijedan fajl iz GPL/LGPL projekata nije ušao u
repo; ništa od MIT liste nije potrebno jer naši moduli (4.52–4.57) pokrivaju
isti posao bez zavisnosti.

## Zaključak

Ako bi se išlo na „generisanje od nule", studija ide ka magenta/muzic/NotaGen
konceptima ali van naših ograničenja (stdlib, dokazi, Pa800). Ako ostajemo na
„dokaži i optimizuj", naši moduli su ispred svih; sledeći korak po tvom
redosledu je punjenje **dokazima i patternima svih uloga (i sola)** na našem
korpusu — korpus za solo već postoji (`session19-benchmark/song19-*.mid`,
kanal „Lead Solo").
