@echo off
rem DNA Optimizer - DNA Studio GUI (4.71) on http://localhost:8123
rem Opens the browser and keeps the bridge running in this window.
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js not found in PATH. Install from https://nodejs.org
  pause
  exit /b 1
)

echo Starting DNA Studio 4.71 bridge on http://localhost:8123 ...
start "" http://localhost:8123
node dna_bridge.mjs
pause
