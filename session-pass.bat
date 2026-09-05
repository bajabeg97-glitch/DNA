@echo off
rem Session Pass CLI: one MIDI file through every engine -> plan report.
rem Usage:
rem   session-pass.bat --input <file.mid> [--roles r:c,r:c,...] [--apply-safe]
rem                     [--apply-actions A01,A02] [--out-dir <dir>]
cd /d "%~dp0"

if "%~1"=="" (
  echo Usage: session-pass.bat --input ^<file.mid^> [options]
  echo.
  echo Options:
  echo   --roles 8:bass,9:drums,10:percussion,11:accompaniment
  echo   --apply-safe            write new artifacts only (source never touched)
  echo   --apply-actions A01,A02 apply only the listed READY actions
  echo   --out-dir artifacts-max-4.66
  exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH. Install Python 3.11+.
  pause
  exit /b 1
)

python dna_midi_studio\session_pass.py %*
