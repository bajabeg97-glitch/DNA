"""Deterministic corpus checksums and role-specific decision calibration."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


_STABLE_ID = re.compile(r"^\d{3}\.\d{3}\.\d{3}$")


def _collect_ids(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "id" and isinstance(child, str) and _STABLE_ID.fullmatch(child):
                found.append(child)
            found.extend(_collect_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_collect_ids(child))
    return found


@dataclass(frozen=True)
class VaultEntry:
    path: str
    namespace: str
    sha256: str
    size: int
    stable_ids: tuple[str, ...]


@dataclass(frozen=True)
class RegressionVault:
    entries: tuple[VaultEntry, ...]
    vault_hash: str

    @classmethod
    def build(cls, root: Path, relative_paths: Sequence[str]) -> "RegressionVault":
        root = root.resolve()
        entries = []
        all_ids: list[tuple[str, str]] = []
        for relative in sorted(set(relative_paths)):
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Regression vault paths must remain inside the workspace")
            resolved = (root / path).resolve()
            if root not in resolved.parents or not resolved.is_file():
                raise ValueError(f"Regression vault file is missing: {relative}")
            raw = resolved.read_bytes()
            ids: tuple[str, ...] = ()
            if resolved.suffix.lower() == ".json":
                # A registry may repeat the same ID as a reference.  The vault
                # inventories stable identities, while each builder owns the
                # stronger entity-level collision check for its schema.
                ids = tuple(sorted(set(_collect_ids(json.loads(raw.decode("utf-8"))))))
            name = path.name
            if name in {"gold-patterns.json", "factory-strumming.json", "gold-performance-patterns.json"}:
                namespace = "runtime-pattern"
            elif name == "factory-velocity-profiles.json":
                namespace = "factory-profile"
            elif name == "factory-style-segments.json":
                namespace = "factory-style-segment"
            else:
                namespace = f"file:{relative}"
            entries.append(VaultEntry(relative, namespace, sha256(raw).hexdigest(), len(raw), ids))
            all_ids.extend((namespace, stable_id) for stable_id in ids)
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Stable ID collision across regression vault files")
        payload = [{"path": item.path, "namespace": item.namespace, "sha256": item.sha256, "size": item.size,
                    "stableIds": list(item.stable_ids)} for item in entries]
        vault_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return cls(tuple(entries), vault_hash)

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "dna-regression-vault", "version": "1.0", "vaultHash": self.vault_hash,
                "entries": [{"path": item.path, "namespace": item.namespace,
                             "sha256": item.sha256, "size": item.size,
                             "stableIds": list(item.stable_ids)} for item in self.entries],
                "summary": {"files": len(self.entries),
                            "stableIds": sum(len(item.stable_ids) for item in self.entries)}}

    def verify(self, root: Path) -> bool:
        rebuilt = RegressionVault.build(root, [item.path for item in self.entries])
        return rebuilt == self


@dataclass(frozen=True)
class RoleThreshold:
    replace_below: float
    repair_below: float
    minimum_evidence: float

    def __post_init__(self) -> None:
        if not 0 <= self.replace_below < self.repair_below <= 1:
            raise ValueError("Role quality thresholds must be ordered in range 0..1")
        if not 0 <= self.minimum_evidence <= 1:
            raise ValueError("Role evidence threshold must be in range 0..1")


class RoleDecisionCalibrator:
    def __init__(self, thresholds: Mapping[str, RoleThreshold]):
        if len(thresholds) < 2:
            raise ValueError("Calibration requires multiple explicit roles, not one universal threshold")
        self.thresholds = dict(thresholds)

    def decide(self, role: str, quality: float, evidence: float) -> str:
        if not 0 <= quality <= 1 or not 0 <= evidence <= 1:
            raise ValueError("Quality and evidence must be in range 0..1")
        threshold = self.thresholds.get(role)
        if threshold is None or evidence < threshold.minimum_evidence:
            return "MANUAL_REVIEW"
        if quality < threshold.replace_below:
            return "REPLACE"
        if quality < threshold.repair_below:
            return "REPAIR"
        return "KEEP"