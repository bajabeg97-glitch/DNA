@echo off
rem One-time Windows setup for DNA Optimizer (4.71).
rem Installs numpy (the only non-stdlib engine dependency) and checks node.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.11+ and enable Add to PATH.
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
echo   run-tests.bat        - full test battery (python + node)
echo   start-bridge.bat     - DNA Studio GUI on http://localhost:8123
echo   compose-songs.bat    - generate songs (10 styles, seeds) into workspace-4.71
echo   build-corpus.bat     - rebuild training corpus into workspace-4.71
echo   validate-songs.bat   - song/corpus validator suites
echo   model-train.bat      - S3 model training (arrives with the S3 layer)
echo   session-pass.bat     - analyze/apply one MIDI file from the console
echo   refresh-patterns.bat - rebuild pattern evidence artifacts
pause
