"""Atomic, resumable and path-safe publication of validated MIDI results."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import unicodedata
from typing import Any, Callable, Mapping, Sequence


_HASH = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_output_stem(name: str, max_length: int = 120) -> str:
    """Return a Unicode-preserving filename safe on Windows and POSIX."""
    value = unicodedata.normalize("NFC", Path(name).stem)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    value = re.sub(r"\s+", " ", value)
    if not value:
        value = "DNA_OUTPUT"
    if value.upper() in _WINDOWS_RESERVED:
        value = "_" + value
    return value[:max_length].rstrip(" .") or "DNA_OUTPUT"


def _inside(root: Path, path: Path) -> Path:
    root = root.resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("Output path escapes the authorized directory")
    return resolved


@dataclass(frozen=True)
class TransactionIdentity:
    source_hash: str
    config_hash: str
    database_hash: str

    def __post_init__(self) -> None:
        if any(not _HASH.fullmatch(value) for value in (self.source_hash, self.config_hash, self.database_hash)):
            raise ValueError("Transaction identity requires three SHA-256 hashes")

    @property
    def digest(self) -> str:
        return sha256(f"{self.source_hash}:{self.config_hash}:{self.database_hash}".encode()).hexdigest()


@dataclass
class CancelToken:
    cancelled: bool = False
    reason: str = "user-request"

    def cancel(self, reason: str = "user-request") -> None:
        self.cancelled, self.reason = True, reason


@dataclass(frozen=True)
class CommitResult:
    status: str
    output_path: Path | None
    output_hash: str | None
    resumed: bool
    validation: Mapping[str, Any]
    journal_path: Path


class AtomicMidiPublisher:
    def __init__(self, output_dir: Path, writer: Callable[[Path, bytes], None] | None = None):
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.writer = writer or self._write_file

    @staticmethod
    def _write_file(path: Path, content: bytes) -> None:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temp, path)

    def publish(self, content: bytes, source_name: str, identity: TransactionIdentity,
                verifier: Callable[[bytes], Mapping[str, Any]], cancel: CancelToken | None = None) -> CommitResult:
        if not isinstance(content, bytes) or not content:
            raise ValueError("Atomic publication requires non-empty bytes")
        cancel = cancel or CancelToken()
        stem = safe_output_stem(source_name)
        output = _inside(self.output_dir, self.output_dir / f"{stem}_OPT.mid")
        lock = _inside(self.output_dir, output.with_suffix(output.suffix + ".lock"))
        journal = _inside(self.output_dir, output.with_suffix(output.suffix + ".journal.json"))
        temp = _inside(self.output_dir, output.with_suffix(output.suffix + f".{identity.digest[:12]}.tmp"))
        output_hash = sha256(content).hexdigest()

        if journal.exists():
            state = json.loads(journal.read_text(encoding="utf-8"))
            if (state.get("status") == "COMMITTED" and state.get("identity") == identity.digest
                    and state.get("outputHash") == output_hash and output.exists()
                    and sha256(output.read_bytes()).hexdigest() == output_hash):
                return CommitResult("COMMITTED", output, output_hash, True, state.get("validation", {}), journal)

        try:
            lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError("Output is locked by another transaction") from exc
        os.close(lock_fd)
        validation: Mapping[str, Any] = {}
        try:
            if cancel.cancelled:
                self._write_json(journal, {"status": "CANCELLED", "identity": identity.digest,
                                           "reason": cancel.reason, "partialOutput": False})
                return CommitResult("CANCELLED", None, None, False, {}, journal)
            temp.unlink(missing_ok=True)
            self.writer(temp, content)
            if cancel.cancelled:
                temp.unlink(missing_ok=True)
                self._write_json(journal, {"status": "CANCELLED", "identity": identity.digest,
                                           "reason": cancel.reason, "partialOutput": False})
                return CommitResult("CANCELLED", None, None, False, {}, journal)
            validation = dict(verifier(temp.read_bytes()))
            if not validation.get("passed"):
                temp.unlink(missing_ok=True)
                self._write_json(journal, {"status": "BLOCKED", "identity": identity.digest,
                                           "validation": validation, "partialOutput": False})
                return CommitResult("BLOCKED", None, None, False, validation, journal)
            os.replace(temp, output)
            state = {"schema": "dna-atomic-export-journal", "version": "1.0", "status": "COMMITTED",
                     "identity": identity.digest, "sourceHash": identity.source_hash,
                     "configHash": identity.config_hash, "databaseHash": identity.database_hash,
                     "output": output.name, "outputHash": output_hash,
                     "validation": validation, "partialOutput": False}
            self._write_json(journal, state)
            return CommitResult("COMMITTED", output, output_hash, False, validation, journal)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        finally:
            lock.unlink(missing_ok=True)


@dataclass
class BatchProgress:
    total: int
    completed: int = 0
    committed: int = 0
    cancelled: int = 0
    blocked: int = 0
    failed: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)


def publish_batch(publisher: AtomicMidiPublisher,
                  jobs: Sequence[tuple[bytes, str, TransactionIdentity, Callable[[bytes], Mapping[str, Any]]]],
                  cancel: CancelToken | None = None,
                  on_progress: Callable[[BatchProgress], None] | None = None) -> BatchProgress:
    progress = BatchProgress(total=len(jobs))
    token = cancel or CancelToken()
    for content, name, identity, verifier in jobs:
        if token.cancelled:
            progress.cancelled += 1
            progress.completed += 1
            progress.items.append({"name": name, "status": "CANCELLED", "error": token.reason})
        else:
            try:
                result = publisher.publish(content, name, identity, verifier, token)
                key = result.status.lower()
                setattr(progress, key, getattr(progress, key) + 1)
                progress.completed += 1
                progress.items.append({"name": name, "status": result.status,
                                       "outputHash": result.output_hash, "resumed": result.resumed})
            except Exception as exc:
                progress.failed += 1
                progress.completed += 1
                progress.items.append({"name": name, "status": "FAILED", "error": str(exc)})
        if on_progress:
            on_progress(progress)
    return progress