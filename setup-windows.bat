@echo off
rem One-time Windows setup for DNA Optimizer.
rem Installs numpy (the only non-stdlib engine dependency) and checks node.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.11+ and enable 'Add to PATH'.
  pause
  exit /b 1
)

echo Installing numpy ...
python -m pip install numpy
if errorlevel 1 (
  echo [WARN] numpy install failed - engine suites will not run.
)

where node >nul 2>nul
if errorlevel 1 (
  echo [WARN] Node.js not found - bridge/UI (start-bridge.bat) will not run.
)

echo.
echo Done. Next steps:
echo   run-tests.bat        - full test battery
echo   start-bridge.bat     - AI Studio UI on http://localhost:8123
echo   session-pass.bat     - analyze/apply one MIDI file from the console
echo   refresh-patterns.bat - rebuild pattern evidence artifacts
pause
