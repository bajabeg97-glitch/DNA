@echo off
rem DNA Optimizer - full test battery (curated list in reports-max-4.71).
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  pause
  exit /b 1
)
where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js not found in PATH.
  pause
  exit /b 1
)

echo === Python core suite (curated list) ===
python -m unittest test_arranger_pro_v449 test_studio_flow_v450 test_truth_engine_v451 test_mix_engine_v452 test_sty_mapper_v453 test_groove_engine_v454 test_instrument_techniques_v455 test_special_track_engine_v457 test_role_patterns_v459 test_session_pass_v460 test_session_pass_v462 test_mido_independent_v452 test_pattern_library_v464 test_user_reference_bank_v465 test_windows_bats_v466 test_analysis_v467 test_assistant_brain_v468 test_composer_corpus_v469 test_generation_plan_v469 test_composer_engine_v470 test_windows_bats_v471
if errorlevel 1 (
  echo [FAIL] Python core suite failed.
  pause
  exit /b 1
)

echo === Python legacy MAX registry ===
python -m unittest test_max_registry_v448
if errorlevel 1 (
  echo [FAIL] Python legacy suite failed.
  pause
  exit /b 1
)

echo === npm tests (bridge + GUI + sessions) ===
call npm test
if errorlevel 1 (
  echo [FAIL] npm tests failed.
  pause
  exit /b 1
)

echo.
echo ALL SUITES GREEN - Python core, Python legacy, npm.
pause
