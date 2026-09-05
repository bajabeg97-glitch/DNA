@echo off
rem Regenerate pattern evidence artifacts:
rem   - 4.64: vendored corpus stats (artifacts-max-4.64\dmp-library-stats.json)
rem   - 4.65: user reference bank (artifacts-max-4.65\user-reference-*.json)
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  pause
  exit /b 1
)

python -m dna_midi_studio.pattern_library --json
if errorlevel 1 (
  echo [FAIL] pattern_library failed.
  pause
  exit /b 1
)

python -m dna_midi_studio.user_reference_bank --json
if errorlevel 1 (
  echo [FAIL] user_reference_bank failed.
  pause
  exit /b 1
)

echo Artifacts refreshed in artifacts-max-4.64\ and artifacts-max-4.65\
pause
