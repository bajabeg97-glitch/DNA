# Session Bridge 4.61 — Faza B: HTTP sloj bez npm zavisnosti (vodič, ne dokaz)

Dokazi: `artifacts-max-4.61/bridge-run.json` (4 HTTP prolaza), testovi
`tests/bridge-session.test.mjs` (5), `npm test` 12/12, Python suita 98 + 5 OK.

## Šta je bridge

`dna_bridge.mjs` — jedan `node:http` server (stdlib; nema `npm install`):
spaja korisnički sloj sa Python Session Pass-om (4.60). To je „tanko staklo"
Faze B iz vizije: UI → HTTP → `session_pass.py` → izveštaj + artefakti.

## Endpoint-i (izvršeno)

| endpoint | značenje |
|---|---|
| `GET /` | minimalni dark UI (Suno-like tok: izaberi fajl → Analiziraj/Optimizuj; demo dugmad za korpus) |
| `GET /api/health` | provera: version, python, engine-i |
| `GET /api/presets` | demo uzorci (reference-style, fixture, session35, song19-01) |
| `GET /api/sample-analyze?p=<preset>&apply=1` | pokreće Session Pass na uzorku preko HTTP |
| `POST /api/analyze` | upload `.mid/.midi/.kar` (headers: `x-filename`, `x-roles`, `x-melody`, `x-apply`) → izveštaj |
| `GET /api/artifacts/<name>` | download generisanih `.mid`/`.json` |

## Izmereno (2026-09-05, izvršeno kroz HTTP)

`artifacts-max-4.61/bridge-run.json` — sva 4 uzorka:
- reference-style / fixture / session35: `A01 STY_EXPORT APPLIED` +
  `A02 PERCUSSION_CC11 APPLIED` → download artefakti
  (`session-sty-*.mid`, `session-mixed-*.mid`);
- song19-01: solo korpus — A01/A02 SKIPPED (format/role), echo sken OK;
- svaki poziv je prošao ceo engine set (trajanje ~0,7–1 s po fajlu).

## Poštenje i invarijante

- upload fajl se kopira u `artifacts-max-4.61/uploads/` — nikad se ne menja;
- primena (apply) ide samo kroz `--apply-safe` Session Pass-a (nove datoteke);
- 50 MB limit, samo MIDI ekstenzije, path traversal zaštićen;
- UI je minimalan da dokaže tok — Faza C (React) ga zamenjuje, endpoint-i
  ostaju isti.

## Reprodukcija

```bash
node dna_bridge.mjs            # http://0.0.0.0:8123
curl -s http://127.0.0.1:8123/api/health
curl -s "http://127.0.0.1:8123/api/sample-analyze?p=reference-style&apply=1"
node --test tests/bridge-session.test.mjs
```
