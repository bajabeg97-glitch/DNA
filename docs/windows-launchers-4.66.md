# 4.66 — Windows .bat launcheri

Datum: 2026-09-05 · Status: `dna-windows-launchers-status-4.66.json` · Dokazi: `reports-max-4.66/01-bats-manifest.json`, `reports-max-4.66/02-tests.json`, `test_windows_bats_v466.py`

## Šta je kreirano

Do 4.65 u repou nije postojao **nijedan** `.bat` fajl — sve se pokretalo bash/python komandama. Sada postoje Windows launcheri (svaki `@echo off`, `cd /d "%~dp0"`, CRLF završeci redova, čist ASCII — bez dijakritika da ne bude mojibake-a na bilo kom codepage-u):

| Fajl | Šta radi |
|---|---|
| `start-bridge.bat` | Pokreće `node dna_bridge.mjs` → AI Studio UI na `http://localhost:8123` |
| `session-pass.bat` | Session Pass CLI: `session-pass.bat --input fajl.mid --roles 8:bass,9:drums,… --apply-safe --apply-actions A01,A02`; bez argumenata ispisuje uputstvo |
| `run-tests.bat` | Puna test baterija: Python core lista (ista kao u miljokazima, sada + `test_windows_bats_v466`) + legacy MAX registry + `npm test`; pauzira i javlja `ALL SUITES GREEN` ili `[FAIL]` |
| `refresh-patterns.bat` | Regeneriše pattern evidenciju: `pattern_library --json` (4.64) + `user_reference_bank --json` (4.65) |
| `setup-windows.bat` | Jednokratna priprema: `pip install numpy` (jedina ne-stdlib zavisnost engine-a) + provera Node.js |
| `.gitattributes` | `*.bat text eol=crlf` — garantuje da svaki checkout na bilo kom OS-u dobije CRLF |

## Kako se koristi (Windows)

```bat
setup-windows.bat        :: prvi put: numpy + provere
run-tests.bat            :: cela test baterija
start-bridge.bat         :: UI na http://localhost:8123
session-pass.bat --input baseline\reference-style.mid --roles 8:bass,9:drums,10:percussion,11:accompaniment,12:accompaniment,13:accompaniment,14:accompaniment,15:accompaniment
refresh-patterns.bat     :: ponovo izgradi pattern artefakte
```

Napomene:
- Launcheri pozivaju `python` (ne `python3.11`) — na Windowsu je to standard; `setup-windows.bat` javlja ako python/node nisu u PATH-u.
- `run-tests.bat` lista modula mora ostati sinhronizovana sa izveštajima — to proverava test `test_run_tests_list_matches_status_report`.
- Poruke u .bat su namerno na engleskom/ASCII da rade na svakoj Windows instalaciji bez podešavanja codepage-a.

## Dokazi

- `test_windows_bats_v466.py` — 14 testova: prisustvo svih launcher-a, CRLF + ASCII + `@echo off`, `%~dp0`, reference na stvarne fajlove (session_pass.py, dna_bridge.mjs…), sinhronizacija liste sa izveštajem, manifest sha256.
- `reports-max-4.66/01-bats-manifest.json` — sha256 + svrha + broj CRLF po fajlu.
- Cela suita: Python 147 (133 + 14) OK, legacy 5 OK, npm 14/14 OK.
