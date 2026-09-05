@echo off
rem DNA model learning (S3, 4.71) - trainer launcher for the GUI model flow.
rem   Usage: model-train.bat [options]
rem The module dna_midi_studio\model_train.py arrives with the S3 layer;
rem the GUI Model tab (start-bridge.bat) already exposes all training options
rem and validates them on the server.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH. Install Python 3.11+.
  pause
  exit /b 1
)

if not exist "dna_midi_studio\model_train.py" (
  echo [INFO] model_train.py (statistical layer, S3/4.71) ships with the next
  echo        update of this same chain. The GUI Model tab is ready now and
  echo        shows all options (n-gram order, epochs, seed, threads, corpus).
  echo        After the update this launcher runs:
  echo          python -m dna_midi_studio.model_train --layer stat --seed 471
  pause
  exit /b 1
)

python -m dna_midi_studio.model_train %*
if errorlevel 1 (
  echo [FAIL] Training failed. See output above.
  pause
  exit /b 1
)

echo.
echo Done. Checkpoint written - load it from the GUI Model tab.
pause
