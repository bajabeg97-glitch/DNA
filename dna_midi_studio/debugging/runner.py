from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import os
import subprocess
import time
from typing import Mapping, Sequence, Any

@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["command"] = list(self.command)
        data["passed"] = self.passed
        return data


def run_command(command: Sequence[str], cwd: str | Path, *, timeout: float = 180.0,
                env: Mapping[str, str] | None = None) -> CommandResult:
    if not command:
        raise ValueError("command must not be empty")
    started = time.monotonic()
    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})
    try:
        completed = subprocess.run(
            [str(x) for x in command], cwd=str(cwd), env=merged_env,
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        return CommandResult(tuple(map(str, command)), str(cwd), completed.returncode,
                             completed.stdout, completed.stderr,
                             time.monotonic() - started, False)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return CommandResult(tuple(map(str, command)), str(cwd), 124, stdout, stderr,
                             time.monotonic() - started, True)
