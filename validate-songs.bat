@echo off
rem DNA Composer validation - run the song/corpus validator suites (4.69+4.70).
rem Verifies every generated song: structure, Pa800-safe SMF, scorecard evidence,
rem humanization grid bounds, deterministic seed reproduction.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH. Install Python 3.11+.
  pause
  exit /b 1
)

python -m unittest test_composer_corpus_v469 test_composer_engine_v470 -v
if errorlevel 1 (
  echo [FAIL] Validation failed.
  pause
  exit /b 1
)

echo.
echo ALL VALID - corpus and song engine suites are green.
pause
