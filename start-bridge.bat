@echo off
rem DNA Optimizer - start bridge (AI Studio UI) on http://localhost:8123
rem Keep this window open while you use the UI.
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js not found in PATH. Install from https://nodejs.org
  pause
  exit /b 1
)

echo Starting DNA bridge on http://localhost:8123 ...
echo Open that URL in your browser (or use the Arena live preview).
node dna_bridge.mjs
pause
