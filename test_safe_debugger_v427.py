from __future__ import annotations
from pathlib import Path
import sys

from dna_midi_studio.debugging import (
    PatchProposal, analyze_failure, error_signature, run_command, validate_patch_in_sandbox,
)


def test_import_error_diagnosis_is_specific_and_stable():
    a = '''Traceback (most recent call last):\n  File "C:\\Users\\Baja\\x\\server.py", line 27, in <module>\n    import pa800_validator\nModuleNotFoundError: No module named 'dna_midi_studio'\n'''
    b = a.replace("C:\\Users\\Baja\\x", "D:\\other")
    da = analyze_failure(a)
    db = analyze_failure(b)
    assert da.category == "IMPORT_PATH_OR_DEPENDENCY"
    assert da.missing_module == "dna_midi_studio"
    assert da.signature == db.signature


def test_command_runner_captures_exit_status(tmp_path: Path):
    r = run_command([sys.executable, "-c", "import sys; print('x'); print('e', file=sys.stderr); sys.exit(3)"], tmp_path)
    assert r.exit_code == 3
    assert "x" in r.stdout and "e" in r.stderr
    assert not r.passed


def test_patch_path_traversal_rejected(tmp_path: Path):
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    proposal = PatchProposal({"../escape.py": "x=2\n"})
    try:
        validate_patch_in_sandbox(tmp_path, proposal, [[sys.executable, "-c", "pass"]])
    except ValueError as exc:
        assert "unsafe patch path" in str(exc)
    else:
        raise AssertionError("unsafe path accepted")


def test_failed_patch_validation_does_not_touch_original(tmp_path: Path):
    original = tmp_path / "mod.py"
    original.write_text("VALUE = 1\n", encoding="utf-8")
    proposal = PatchProposal({"mod.py": "VALUE = 2\n"})
    result = validate_patch_in_sandbox(
        tmp_path, proposal,
        [[sys.executable, "-c", "import sys; sys.exit(9)"]],
    )
    assert not result.accepted
    assert original.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_passing_patch_is_only_approved_not_committed(tmp_path: Path):
    original = tmp_path / "mod.py"
    original.write_text("VALUE = 1\n", encoding="utf-8")
    proposal = PatchProposal({"mod.py": "VALUE = 2\n"})
    result = validate_patch_in_sandbox(
        tmp_path, proposal,
        [[sys.executable, "-c", "from mod import VALUE; assert VALUE == 2"]],
    )
    assert result.accepted
    assert original.read_text(encoding="utf-8") == "VALUE = 1\n"
