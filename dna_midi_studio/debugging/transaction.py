from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import shutil
import tempfile
from typing import Mapping, Sequence

from .runner import CommandResult, run_command

@dataclass(frozen=True)
class PatchProposal:
    files: Mapping[str, str]
    rationale: str = ""
    provider: str = "manual-or-ai-advisor"

@dataclass(frozen=True)
class PatchValidationResult:
    accepted: bool
    reason: str
    changed_files: tuple[str, ...]
    command_results: tuple[CommandResult, ...]

_PROTECTED_PREFIXES = ("data/", "models/", "regression_vault/")


def _safe_relative(path: str) -> Path:
    p = Path(path)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"unsafe patch path: {path}")
    return p


def _tree_hash(root: Path, relative: Path) -> str | None:
    path = root / relative
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_patch_in_sandbox(project_root: str | Path, proposal: PatchProposal,
                              validation_commands: Sequence[Sequence[str]], *,
                              timeout_per_command: float = 180.0,
                              allow_protected: bool = False) -> PatchValidationResult:
    root = Path(project_root).resolve()
    rels = tuple(_safe_relative(name) for name in proposal.files)
    if not allow_protected:
        for rel in rels:
            text = rel.as_posix()
            if text.startswith(_PROTECTED_PREFIXES):
                return PatchValidationResult(False, f"protected path rejected: {text}", (), ())

    with tempfile.TemporaryDirectory(prefix="dna-debug-sandbox-") as tmp:
        sandbox = Path(tmp) / "project"
        shutil.copytree(root, sandbox, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        before = {rel.as_posix(): _tree_hash(sandbox, rel) for rel in rels}
        changed: list[str] = []
        for rel, content in zip(rels, proposal.files.values()):
            dst = sandbox / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(content, encoding="utf-8")
            if _tree_hash(sandbox, rel) != before[rel.as_posix()]:
                changed.append(rel.as_posix())

        if not changed:
            return PatchValidationResult(False, "proposal changes no bytes", (), ())

        results: list[CommandResult] = []
        env = {"PYTHONPATH": str(sandbox / "src")}
        for command in validation_commands:
            result = run_command(command, sandbox, timeout=timeout_per_command, env=env)
            results.append(result)
            if not result.passed:
                return PatchValidationResult(False, "validation command failed; original remains untouched",
                                             tuple(changed), tuple(results))
        return PatchValidationResult(True, "all sandbox validation commands passed",
                                     tuple(changed), tuple(results))
