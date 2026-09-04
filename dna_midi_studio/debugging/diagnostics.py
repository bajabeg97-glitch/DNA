from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re
from typing import Any

_TRACE_FILE_RE = re.compile(r'File "([^"]+)", line (\d+)(?:, in ([^\n]+))?')
_EXCEPTION_RE = re.compile(r"(?m)^([A-Za-z_][\w.]*(?:Error|Exception|Interrupt)):\s*(.*)$")
_MODULE_RE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")

@dataclass(frozen=True)
class Diagnostic:
    signature: str
    category: str
    exception_type: str | None
    message: str
    missing_module: str | None
    files: tuple[str, ...]
    likely_causes: tuple[str, ...]
    recommended_checks: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["files"] = list(self.files)
        data["likely_causes"] = list(self.likely_causes)
        data["recommended_checks"] = list(self.recommended_checks)
        return data


def _normalize_output(text: str) -> str:
    text = text.replace("\r\n", "\n")
    # Remove volatile absolute path prefixes while preserving basename/line info.
    text = re.sub(r'File "[^"\n]*[\\/]([^"\\/]+)"', r'File "\1"', text)
    text = re.sub(r"\b\d+\.\d{3,}\s*s\b", "<duration>", text)
    return text.strip()


def error_signature(text: str) -> str:
    normalized = _normalize_output(text)
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()


def analyze_failure(text: str) -> Diagnostic:
    normalized = _normalize_output(text)
    exc_matches = list(_EXCEPTION_RE.finditer(normalized))
    exc_type = exc_matches[-1].group(1) if exc_matches else None
    message = exc_matches[-1].group(2).strip() if exc_matches else normalized[-400:].strip()
    module_match = _MODULE_RE.search(normalized)
    missing_module = module_match.group(1) if module_match else None
    files = tuple(dict.fromkeys(m.group(1) for m in _TRACE_FILE_RE.finditer(normalized)))

    causes: list[str] = []
    checks: list[str] = []
    category = "UNKNOWN_FAILURE"

    if exc_type == "ModuleNotFoundError" or missing_module:
        category = "IMPORT_PATH_OR_DEPENDENCY"
        causes.extend((
            "Python package is not installed or not visible on sys.path.",
            "A launcher may omit the repository src directory from PYTHONPATH.",
            "A root compatibility wrapper may import a package before bootstrap initialization.",
        ))
        checks.extend((
            "Run package_selfcheck.py from the same launcher/runtime that failed.",
            "Verify that <project>/src is present before importing dna_midi_studio.",
            "Prefer package imports over relying on the current working directory.",
        ))
    elif exc_type in {"AssertionError"} or "FAILED" in normalized or "FAILURES" in normalized:
        category = "TEST_OR_INVARIANT_FAILURE"
        causes.append("A behavioral assertion or release invariant failed.")
        checks.extend((
            "Re-run only the failing test first.",
            "Inspect generated MIDI/report artifacts instead of trusting file existence.",
            "Do not accept a patch unless protected musical invariants still pass.",
        ))
    elif exc_type in {"SyntaxError", "IndentationError", "TabError"}:
        category = "PYTHON_SYNTAX"
        causes.append("Python source cannot be parsed.")
        checks.append("Run py_compile on the exact failing file before broader tests.")
    elif exc_type in {"MemoryError"} or "out of memory" in normalized.lower():
        category = "RESOURCE_EXHAUSTION"
        causes.append("Training/test workload exceeded available memory.")
        checks.extend(("Reduce batch/model size without changing dataset split or musical authority rules.",
                       "Record the reduced configuration in the training manifest."))
    elif "timeout" in normalized.lower() or "timed out" in normalized.lower():
        category = "TIMEOUT"
        causes.append("The command did not complete within the configured execution window.")
        checks.append("Treat timeout as UNVERIFIED, never PASS; isolate the slow stage and rerun it separately.")
    else:
        checks.extend((
            "Capture the exact command, exit code, stdout and stderr.",
            "Minimize to the smallest reproducible test before proposing code changes.",
        ))

    return Diagnostic(
        signature=error_signature(normalized),
        category=category,
        exception_type=exc_type,
        message=message,
        missing_module=missing_module,
        files=files,
        likely_causes=tuple(causes),
        recommended_checks=tuple(checks),
    )
