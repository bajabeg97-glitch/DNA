# Session Flow 4.62 — Faza C jezgro: plan → korisnik potvrdi → primeni (vodič, ne dokaz)

Dokazi: `artifacts-max-4.62/bridge-run.json` (5 HTTP poziva sa izborom akcija),
testovi `test_session_pass_v462.py` (4), bridge testovi (2 nova), `npm test`
14/14, Python 102 + legacy 5 OK.

## Šta je dodato na Fazu A/B

Suno-tok ima korak „korisnik bira": Session Pass (4.60) i bridge (4.61)
sada podržavaju **primenu samo potvrđenih akcija**:

- `session_pass.py`: novi parametar `--apply-actions A01,A02,...` — sa
  `--apply-safe`, primenjuju se SAMO navedene akcije; ostale ostaju `READY`
  (analiza postoji, primena ne);
- `dna_bridge.mjs`: HTTP `x-actions` (upload) i `?actions=` (sample);
- UI: dvokoračni tok — korak 1 „Analiziraj (plan)" prikazuje sve akcije sa
  checkbox-ovima pored `READY`; korak 2 „Primeni izabrane (n)" šalje samo
  označene → rezultat sa download linkovima.

## Izmereno (2026-09-05, izvršeno kroz HTTP)

`artifacts-max-4.62/bridge-run.json`:
- `actions=A01_STY_EXPORT` → tačno 1 artefakt (`session-sty-reference-style.mid`);
- `actions=A02_PERCUSSION_CC11_GAIN` → tačno 1 artefakt (`session-mixed-reference-style.mid`);
- `actions=A01,A02` na fixture → 2 artefakta;
- `session35` bez izbora (sve READY) → 2 artefakta;
- `song19-01` → 0 (nema primenljivih akcija — korektno);
- UI ima „Primeni izabrane (N)" i naslov „Session Flow".

## Invarijante (nepromenjene)

- izvor se nikad ne menja; primena uvek u nove artefakte;
- akcije koje nisu `READY` ne mogu se označiti (LOCKED/NEEDS_DECISION ostaju
  zaključane u UI-ju);
- `--apply-actions` prazan = stare ponašanje (primeni sve READY).

## Reprodukcija

```bash
python3.11 dna_midi_studio/session_pass.py --input baseline/reference-style.mid \
  --roles 8:bass,9:drums,10:percussion,11:accompaniment,... \
  --apply-safe --apply-actions A01_STY_EXPORT --out-dir artifacts-max-4.62
node dna_bridge.mjs   # UI: Analiziraj → označi → Primeni izabrane
```
