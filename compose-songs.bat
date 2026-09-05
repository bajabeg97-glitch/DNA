@echo off
rem DNA Composer (4.70) - generate songs as .mid + scorecard per song.
rem   Usage:
rem     compose-songs.bat                  -> all 10 styles x seeds 1,2,3 (30 songs)
rem     compose-songs.bat --styles rock,ballad --seeds 1,2,3
rem     compose-songs.bat --all --seeds 7 --no-humanize
rem Output defaults to workspace-4.71\songs-4.70 (never touches committed S2 set).
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH. Install Python 3.11+.
  pause
  exit /b 1
)

set ARGS=
if "%~1"=="" (
  set ARGS=--all
) else (
  set ARGS=%*
)

echo %ARGS% | findstr /C:"--out" >nul
if errorlevel 1 set ARGS=%ARGS% --out workspace-4.71

echo DNA Composer 4.70 - komponujem: %ARGS%
python -m dna_midi_studio.composer_engine %ARGS%
if errorlevel 1 (
  echo [FAIL] Composer failed. See output above.
  pause
  exit /b 1
)

echo.
echo Done. Songs + scorecards are in workspace-4.71\songs-4.70.
echo Open the GUI (start-bridge.bat) and use the Files tab to listen/download.
pause
