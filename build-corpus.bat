@echo off
rem DNA Composer corpus (4.69) - rebuild the training corpus into workspace-4.71.
rem   Usage:
rem     build-corpus.bat                    -> defaults: seed 469, synthetic 300, real 24
rem     build-corpus.bat --seed 700 --synthetic 100 --real-limit 10
rem Output defaults to workspace-4.71\corpus-4.69 (committed S1 corpus untouched).
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH. Install Python 3.11+.
  pause
  exit /b 1
)

set ARGS=
if not "%~1"=="" set ARGS=%*
echo %ARGS% | findstr /C:"--out" >nul
if errorlevel 1 set ARGS=%ARGS% --out workspace-4.71

echo DNA corpus 4.69 - gradim korpus: %ARGS%
python -m dna_midi_studio.composer_corpus %ARGS%
if errorlevel 1 (
  echo [FAIL] Corpus build failed (validator must pass 100%%).
  pause
  exit /b 1
)

echo.
echo Done. Corpus is in workspace-4.71\corpus-4.69 with manifest + stats.
pause
