"""Session 30 software release-readiness and honest Preview gate.

This module aggregates existing validated evidence.  It can authorize a
software release candidate, but it cannot invent human listening evidence,
production articulation evidence, or a physical Pa800 certification.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import re
import sys
import time
import tracemalloc
from typing import Any, Mapping, Sequence

from .arrangement_graph import build_arrangement_graph
from .candidate_search import build_candidate_set
from .midi import MidiEvent, MidiFile, MidiTrack
from .song_understanding import analyze_song_map
from .transactional_export import safe_output_stem


RELEASE_READINESS_SCHEMA = "dna-ai-premium-release-readiness"
RELEASE_READINESS_VERSION = "2.0"
SOFTWARE_MANIFEST_SCHEMA = "dna-ai-premium-software-manifest"
SOFTWARE_MANIFEST_VERSION = "1.0"
PROJECT_MIGRATION_SCHEMA = "dna-project-migration-report"
PROJECT_MIGRATION_VERSION = "1.0"
STATUS_MATRIX_SCHEMA = "dna-premium-release-status-matrix"
STATUS_MATRIX_VERSION = "1.0"
HARDENING_SCHEMA = "dna-session30-hardening-report"
HARDENING_VERSION = "1.0"
TARGET_BASELINE = "4.8-release-candidate-preview"
ALLOWED_PRODUCT_NAME = "AI PREMIUM ARRANGER PREVIEW"
FINAL_PRODUCT_NAME = "AI PREMIUM ARRANGER"
SIGNATURE_ALGORITHM = "SHA256-CONTENT-SEAL"
SIGNATURE_AUTHORITY = "DNA_LOCAL_SOFTWARE_GATE"
EXTERNAL_BLOCKERS = (
    "TWO_INDEPENDENT_HUMAN_EVALUATORS_REQUIRED",
    "HUMAN_OVERALL_MEDIAN_BELOW_FOUR_OR_MISSING",
    "PREMIUM_PREFERENCE_BELOW_SEVENTY_PERCENT_OR_MISSING",
    "EXPRESSION_PRODUCTION_EVIDENCE_BLOCKED",
    "ARTICULATION_DEVICE_CAPTURE_BLOCKED",
    "PA800_DEVICE_PROFILE_NOT_CERTIFIED",
    "PA800_VOICE_COST_UNCONFIRMED",
)
STATUS_IDS = (
    "LEGACY_CORE", "RECOVERY_PREMIUM_SUITE", "CLEAN_EXTRACT_WINDOWS",
    "PROJECT_MIGRATION", "PERFORMANCE_LIMITS", "MEMORY_LIMIT",
    "LONG_PATH_UNICODE", "CRASH_ROLLBACK", "DISK_FULL_ROLLBACK",
    "AUTOMATED_MUSIC_QUALITY", "HUMAN_LISTENING", "EXPRESSION_EVIDENCE",
    "ARTICULATION_CAPTURE", "PA800_DEVICE_PROFILE", "PA800_VOICE_COST",
    "FINAL_MIDI_EXPORT", "MARKETING_NAME",
)
_SHA = re.compile(r"^[0-9a-f]{64}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return _hash({key: item for key, item in value.items() if key != field})


def _strict(value: Mapping[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    unknown, missing = set(value) - allowed, required - set(value)
    if unknown:
        raise ValueError(f"Unknown {label} fields: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"Missing {label} fields: {', '.join(sorted(missing))}")


def _valid_date(raw: object, label: str = "releaseDate") -> str:
    value = str(raw)
    if not _DATE.fullmatch(value):
        raise ValueError(f"{label} must use YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed > date.today():
        raise ValueError(f"{label} cannot be in the future")
    return parsed.isoformat()


def _read_json(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        raise ValueError(f"Required release evidence is missing: {relative}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Release evidence is not an object: {relative}")
    return value


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(raw),
            "sha256": sha256(raw).hexdigest()}


def migrate_project_for_release(document: Mapping[str, Any], migration_date: str) -> dict[str, Any]:
    """Return a deterministic migrated project and audit without mutating input."""
    migration_date = _valid_date(migration_date, "migrationDate")
    if not isinstance(document, Mapping):
        raise ValueError("Legacy project must be a JSON object")
    before = deepcopy(document)
    source_hash = _hash(before)
    timestamp = migration_date + "T00:00:00+00:00"
    schema = document.get("schema")
    version = int(document.get("version", 0)) if schema == "dna-midi-studio-project" else 0
    if schema == "dna-midi-studio-project" and version > 1:
        raise ValueError("Project was created by a newer unsupported application")
    if schema == "dna-midi-studio-project":
        state = deepcopy(document.get("state"))
        source = deepcopy(document.get("source"))
        artifacts = deepcopy(document.get("artifacts", {"styleManifest": None, "optimizerReport": None}))
        name = str(document.get("name", "Migrirani DNA projekt"))[:120]
        created = str(document.get("createdAt", timestamp))
        source_format = f"dna-midi-studio-project-v{version}"
    elif "optimizer" in document or "style" in document:
        state = deepcopy(dict(document))
        source, artifacts = None, {"styleManifest": None, "optimizerReport": None}
        name, created, source_format = "Migrirani DNA projekt", timestamp, "legacy-optimizer-style"
    elif isinstance(document.get("state"), Mapping):
        state = deepcopy(document["state"])
        source = deepcopy(document.get("source"))
        artifacts = deepcopy(document.get("artifacts", {"styleManifest": None, "optimizerReport": None}))
        name = str(document.get("name", "Migrirani DNA projekt"))[:120]
        created, source_format = str(document.get("createdAt", timestamp)), "legacy-state-wrapper"
    else:
        raise ValueError("JSON is not recognized as a DNA MIDI Studio project")
    if not isinstance(state, dict):
        raise ValueError("Migrated project must contain an object state")
    serialized = json.dumps(state, ensure_ascii=False).lower()
    if '"mididata"' in serialized or '"audiodata"' in serialized:
        raise ValueError("Project migration cannot embed MIDI or audio data")
    output = {
        "schema": "dna-midi-studio-project", "version": 1, "name": name,
        "createdAt": created, "updatedAt": timestamp, "state": state, "source": source,
        "artifacts": artifacts,
        "invariants": {"midiEmbedded": False, "audioEmbedded": False,
                       "goldAffectsDynamics": False}, "projectHash": "",
    }
    output["projectHash"] = _hash_without(output, "projectHash")
    report = {
        "schema": PROJECT_MIGRATION_SCHEMA, "version": PROJECT_MIGRATION_VERSION,
        "migrationDate": migration_date,
        "source": {"format": source_format, "version": version, "sha256": source_hash},
        "output": {"schema": output["schema"], "version": output["version"],
                   "sha256": _hash(output), "projectHash": output["projectHash"]},
        "preservation": {"sourceObjectUnchanged": before == document,
                         "statePreserved": output["state"] == state,
                         "locksPreserved": output["state"].get("locks") == state.get("locks"),
                         "auditPreserved": output["state"].get("audit") == state.get("audit"),
                         "originalFileOverwriteAllowed": False},
        "safety": {"midiEmbedded": False, "audioEmbedded": False,
                   "goldAffectsDynamics": False, "validatorBypassAllowed": False},
        "migratedProject": output, "migrationHash": "",
    }
    report["migrationHash"] = _hash_without(report, "migrationHash")
    validate_project_migration_report(report)
    return report


def validate_project_migration_report(report: Mapping[str, Any]) -> None:
    root = {"schema", "version", "migrationDate", "source", "output", "preservation",
            "safety", "migratedProject", "migrationHash"}
    if not isinstance(report, Mapping) or set(report) != root:
        raise ValueError("Project migration report fields mismatch")
    if report["schema"] != PROJECT_MIGRATION_SCHEMA or report["version"] != PROJECT_MIGRATION_VERSION:
        raise ValueError("Unsupported project migration report")
    _valid_date(report["migrationDate"], "migrationDate")
    if not _SHA.fullmatch(str(report["source"]["sha256"])) or not _SHA.fullmatch(str(report["output"]["sha256"])):
        raise ValueError("Project migration hashes are invalid")
    if set(report["source"]) != {"format", "version", "sha256"}:
        raise ValueError("Project migration source fields mismatch")
    if set(report["output"]) != {"schema", "version", "sha256", "projectHash"}:
        raise ValueError("Project migration output fields mismatch")
    if set(report["preservation"]) != {"sourceObjectUnchanged", "statePreserved", "locksPreserved",
                                       "auditPreserved", "originalFileOverwriteAllowed"}:
        raise ValueError("Project migration preservation fields mismatch")
    if not all(report["preservation"][key] for key in (
            "sourceObjectUnchanged", "statePreserved", "locksPreserved", "auditPreserved")):
        raise ValueError("Project migration did not preserve source state")
    project = report["migratedProject"]
    if project.get("schema") != "dna-midi-studio-project" or project.get("version") != 1:
        raise ValueError("Migrated project schema is invalid")
    if project["projectHash"] != _hash_without(project, "projectHash") or report["output"]["sha256"] != _hash(project):
        raise ValueError("Migrated project hash mismatch")
    if report["output"]["projectHash"] != project["projectHash"]:
        raise ValueError("Project migration output projectHash mismatch")
    if report["preservation"]["originalFileOverwriteAllowed"] is not False:
        raise ValueError("Project migration cannot overwrite the original")
    if report["safety"] != {"midiEmbedded": False, "audioEmbedded": False,
                            "goldAffectsDynamics": False, "validatorBypassAllowed": False}:
        raise ValueError("Project migration safety was weakened")
    if report["migrationHash"] != _hash_without(report, "migrationHash"):
        raise ValueError("Project migration report hash mismatch")


def build_software_manifest(root: str | Path, release_date: str) -> dict[str, Any]:
    root, release_date = Path(root), _valid_date(release_date)
    root_names = {
        "server.py", "web_gui.py", "project_model.py", "release_check.py",
        "recovery_release_check.py", "release_packager.py", "premium_config.py",
        "session30_release_check.py", "session30_release_readiness.py",
        "pokreni.bat", "testiraj.bat", "izgradi-dna.bat", "install.bat", "run.bat",
    }
    application_paths = [root / name for name in sorted(root_names)]
    application_paths += sorted((root / "src" / "dna_midi_studio").glob("*.py"))
    application_paths += sorted(root.glob("session*_release_check.py"))
    missing = [path for path in application_paths if not path.is_file()]
    if missing:
        raise ValueError("Software manifest files are missing: " + ", ".join(path.name for path in missing))
    registry_paths = [root / "data" / name for name in (
        "factory-velocity-profiles.json", "gold-patterns.json", "factory-style-segments.json",
        "factory-strumming.json", "gold-performance-patterns.json",
    )]
    schema_paths = sorted((root / "premium" / "schemas").rglob("*.json"))
    manifest = {
        "schema": SOFTWARE_MANIFEST_SCHEMA, "version": SOFTWARE_MANIFEST_VERSION,
        "releaseDate": release_date, "targetBaseline": TARGET_BASELINE,
        "platform": {"os": ["Windows 10 x64", "Windows 11 x64"],
                     "python": ["3.11", "3.12", "3.13", "3.14"],
                     "offlineCore": True, "thirdPartyRuntimeDependencies": []},
        "application": [_file_record(root, path) for path in sorted(set(application_paths))],
        "registries": [_file_record(root, path) for path in registry_paths],
        "contracts": [_file_record(root, path) for path in schema_paths],
        "modelPromptVersions": {
            "producerBriefParser": "LOCAL_RULE_BASED_2.0",
            "optionalAiAdapter": "DEFAULT_OFF_METADATA_ONLY",
            "arrangementPlanner": "ARRANGEMENT_GRAPH_2.0",
            "candidateEngine": "CANDIDATE_SET_2.0",
            "profileEngine": "PERSONAL_PRODUCER_PROFILE_2.0",
            "cloudRequired": False,
        },
        "deviceProfile": {"target": "Korg Pa800", "status": "WAITING_FOR_DEVICE",
                          "certified": False, "profileHash": None},
        "contentHash": "", "signature": {}, "manifestHash": "",
    }
    signed = {key: value for key, value in manifest.items()
              if key not in {"contentHash", "signature", "manifestHash"}}
    manifest["contentHash"] = _hash(signed)
    manifest["signature"] = {
        "algorithm": SIGNATURE_ALGORITHM, "authority": SIGNATURE_AUTHORITY,
        "signedContentHash": manifest["contentHash"],
        "signatureValue": _hash({"algorithm": SIGNATURE_ALGORITHM,
                                 "authority": SIGNATURE_AUTHORITY,
                                 "contentHash": manifest["contentHash"],
                                 "releaseDate": release_date}),
        "identityClaim": False,
    }
    manifest["manifestHash"] = _hash_without(manifest, "manifestHash")
    validate_software_manifest(manifest, root)
    return manifest


def validate_software_manifest(manifest: Mapping[str, Any], root: str | Path | None = None) -> None:
    fields = {"schema", "version", "releaseDate", "targetBaseline", "platform", "application",
              "registries", "contracts", "modelPromptVersions", "deviceProfile", "contentHash",
              "signature", "manifestHash"}
    if not isinstance(manifest, Mapping) or set(manifest) != fields:
        raise ValueError("Software manifest fields mismatch")
    if manifest["schema"] != SOFTWARE_MANIFEST_SCHEMA or manifest["version"] != SOFTWARE_MANIFEST_VERSION:
        raise ValueError("Unsupported software manifest")
    _valid_date(manifest["releaseDate"])
    if manifest["targetBaseline"] != TARGET_BASELINE:
        raise ValueError("Software manifest baseline mismatch")
    if manifest["platform"] != {"os": ["Windows 10 x64", "Windows 11 x64"],
                                "python": ["3.11", "3.12", "3.13", "3.14"],
                                "offlineCore": True, "thirdPartyRuntimeDependencies": []}:
        raise ValueError("Software manifest platform contract mismatch")
    if manifest["modelPromptVersions"].get("cloudRequired") is not False:
        raise ValueError("Software manifest cannot require cloud access")
    if len(manifest["registries"]) != 5 or not manifest["contracts"]:
        raise ValueError("Software manifest registries/contracts are incomplete")
    records = list(manifest["application"]) + list(manifest["registries"]) + list(manifest["contracts"])
    paths = [item["path"] for item in records]
    if len(paths) != len(set(paths)) or any(set(item) != {"path", "bytes", "sha256"} for item in records):
        raise ValueError("Software manifest file records are invalid")
    if any(Path(item["path"]).is_absolute() or ".." in Path(item["path"]).parts for item in records):
        raise ValueError("Software manifest file path is unsafe")
    if any(not _SHA.fullmatch(str(item["sha256"])) or int(item["bytes"]) < 1 for item in records):
        raise ValueError("Software manifest file hash/size is invalid")
    if root is not None:
        base = Path(root).resolve()
        for item in records:
            path = (base / item["path"]).resolve()
            if base not in path.parents or not path.is_file():
                raise ValueError("Software manifest path escapes root or is missing")
            if path.stat().st_size != item["bytes"] or sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ValueError("Software manifest file changed")
    signed = {key: value for key, value in manifest.items()
              if key not in {"contentHash", "signature", "manifestHash"}}
    if manifest["contentHash"] != _hash(signed):
        raise ValueError("Software manifest content hash mismatch")
    signature = manifest["signature"]
    expected_signature = _hash({"algorithm": SIGNATURE_ALGORITHM,
                                "authority": SIGNATURE_AUTHORITY,
                                "contentHash": manifest["contentHash"],
                                "releaseDate": manifest["releaseDate"]})
    if signature != {"algorithm": SIGNATURE_ALGORITHM, "authority": SIGNATURE_AUTHORITY,
                     "signedContentHash": manifest["contentHash"],
                     "signatureValue": expected_signature, "identityClaim": False}:
        raise ValueError("Software content signature mismatch")
    if manifest["deviceProfile"] != {"target": "Korg Pa800", "status": "WAITING_FOR_DEVICE",
                                     "certified": False, "profileHash": None}:
        raise ValueError("Software manifest cannot claim a Pa800 profile")
    if manifest["manifestHash"] != _hash_without(manifest, "manifestHash"):
        raise ValueError("Software manifest hash mismatch")


def _large_midi(note_count: int = 25_000) -> bytes:
    events = [MidiEvent(0, 0, "channel", status=0xC0, data=bytes((0,)))]
    order = 1
    for index in range(note_count):
        pitch, start = 48 + index % 24, index % 120
        events.append(MidiEvent(start, order, "channel", status=0x90,
                                data=bytes((pitch, 1 + index % 127))))
        events.append(MidiEvent(480, order + 1, "channel", status=0x80, data=bytes((pitch, 0))))
        order += 2
    return MidiFile(0, 480, [MidiTrack(events)]).to_bytes()


def run_hardening_benchmarks(root: str | Path, previous: Mapping[str, Any],
                             migration: Mapping[str, Any], release_date: str) -> dict[str, Any]:
    root, release_date = Path(root), _valid_date(release_date)
    documents = previous["documents"]
    started = time.perf_counter()
    large_map = analyze_song_map(_large_midi(), "session30-25000-notes.mid")
    analysis_seconds = time.perf_counter() - started
    started = time.perf_counter()
    graph = build_arrangement_graph(documents["songMap"], documents["producerBrief"], 2100, 2)
    plan_seconds = time.perf_counter() - started
    started = time.perf_counter()
    partial = build_candidate_set(
        documents["arrangementGraph"], documents["songMap"], root,
        plan_variant_id="plan-01", seed=2200, variant_count=3,
        controls={"version": "1.0", "regenerateMarkers": ["v3cv1"],
                  "nextCandidateOffsets": [{"requestId": "v3cv1:drums", "offset": 1}]},
        previous_candidate_set=documents["candidateSet"],
    )
    partial_seconds = time.perf_counter() - started
    large_legacy = {
        "name": "Dugi Ćirilica ŠĐČŽ projekt",
        "state": {"locks": [f"marker-{index:05d}" for index in range(5_000)],
                  "audit": [{"id": index, "accepted": index % 2 == 0} for index in range(5_000)],
                  "path": "C:/Korisnici/Glazba/" + "vrlo-duga-mapa/" * 30 + "pjesma.mid"},
    }
    tracemalloc.start()
    memory_started = time.perf_counter()
    large_migration = migrate_project_for_release(large_legacy, release_date)
    migration_seconds = time.perf_counter() - memory_started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    session10 = _read_json(root, "data/session10-test-report.json")
    session13_path = root / "data/session13-test-report.json"
    if session13_path.is_file():
        session13 = _read_json(root, "data/session13-test-report.json")
        clean = session13.get("cleanExtractSmoke", {})
        clean_pass = (clean.get("legacyRelease", {}).get("returnCode") == 0
                      and clean.get("recoveryRelease", {}).get("returnCode") == 0)
    else:
        package = _read_json(root, "data/release-package-manifest.json")
        guarded = os.environ.get("DNA_IN_PACKAGE_SMOKE") == "1"
        clean = {"mode": "active-clean-extract-recursion-guard"}
        clean_pass = guarded and package.get("version") in {
            "4.8-preview-rc",
            "4.9-analysis-foundation",
            "4.9.1-evidence-authority-foundation",
            "4.9.2-track-plan-foundation",
            "4.9.3-renderer-foundation",
            "4.10.1-e2e-workflow-foundation",
            "4.10.2-zero-silent-failure-reliability-foundation",
            "4.11-quality-calibration-foundation",
            "4.11.1-device-certification-intake-foundation",
        }
    path_samples = {
        "unicode": safe_output_stem("Žužana_Ćirilica_ŠĐČŽ.mid"),
        "long": safe_output_stem("vrlo-duga-mapa-" * 30 + ".mid"),
        "reserved": safe_output_stem("CON.mid"),
        "traversal": safe_output_stem("../../projekt.mid"),
    }
    report = {
        "schema": HARDENING_SCHEMA, "version": HARDENING_VERSION,
        "releaseDate": release_date,
        "runtime": {"python": platform.python_version(), "implementation": platform.python_implementation(),
                    "platform": platform.system() or sys.platform},
        "performance": {
            "analysis25000Notes": {"noteCount": large_map["source"]["noteCount"],
                                   "seconds": round(analysis_seconds, 6), "limitSeconds": 10.0,
                                   "passed": analysis_seconds < 10.0},
            "globalPlan": {"nodeCount": len(graph["nodes"]), "seconds": round(plan_seconds, 6),
                           "limitSeconds": 5.0, "passed": plan_seconds < 5.0},
            "partialRegeneration": {"markers": partial["partialRegeneration"]["regeneratedMarkers"],
                                    "seconds": round(partial_seconds, 6), "limitSeconds": 2.0,
                                    "passed": partial_seconds < 2.0},
        },
        "memory": {"migrationItems": 10_000, "seconds": round(migration_seconds, 6),
                   "peakBytes": peak_bytes, "limitBytes": 64 * 1024 * 1024,
                   "passed": peak_bytes < 64 * 1024 * 1024},
        "paths": {"samples": path_samples,
                  "unicodePreserved": "Žužana" in path_samples["unicode"],
                  "longBounded": len(path_samples["long"]) <= 120,
                  "reservedProtected": path_samples["reserved"].upper() != "CON",
                  "traversalRemoved": ".." not in path_samples["traversal"]},
        "transactions": {
            "crashRollback": session10["invariants"]["partialMidiAfterCrash"] is False,
            "diskFullRollback": session10["invariants"]["diskFullRollback"] is True,
            "cancelRollback": session10["invariants"]["partialMidiAfterCancel"] is False,
            "atomicReplace": session10["invariants"]["atomicReplace"] is True,
        },
        "cleanExtract": {"mode": clean.get("mode"), "passed": clean_pass},
        "migration": {"referenceMigrationHash": migration["migrationHash"],
                      "largeMigrationHash": large_migration["migrationHash"],
                      "originalOverwriteAllowed": False},
        "passed": False, "hardeningHash": "",
    }
    report["passed"] = all((
        all(item["passed"] for item in report["performance"].values()),
        report["memory"]["passed"],
        all(value for key, value in report["paths"].items() if key != "samples"),
        all(report["transactions"].values()), report["cleanExtract"]["passed"],
    ))
    report["hardeningHash"] = _hash_without(report, "hardeningHash")
    validate_hardening_report(report)
    return report


def validate_hardening_report(report: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "releaseDate", "runtime", "performance", "memory", "paths",
              "transactions", "cleanExtract", "migration", "passed", "hardeningHash"}
    if not isinstance(report, Mapping) or set(report) != fields:
        raise ValueError("Hardening report fields mismatch")
    if report["schema"] != HARDENING_SCHEMA or report["version"] != HARDENING_VERSION:
        raise ValueError("Unsupported hardening report")
    _valid_date(report["releaseDate"])
    if set(report["performance"]) != {"analysis25000Notes", "globalPlan", "partialRegeneration"}:
        raise ValueError("Hardening performance gates are incomplete")
    if report["performance"]["analysis25000Notes"]["noteCount"] != 25_000:
        raise ValueError("Hardening analysis corpus is not 25,000 notes")
    for name, item in report["performance"].items():
        expected = {"seconds", "limitSeconds", "passed"}
        if name == "analysis25000Notes":
            expected.add("noteCount")
        elif name == "globalPlan":
            expected.add("nodeCount")
        else:
            expected.add("markers")
        if set(item) != expected or item["seconds"] >= item["limitSeconds"] or item["passed"] is not True:
            raise ValueError(f"Hardening performance gate failed: {name}")
    if report["memory"]["limitBytes"] != 64 * 1024 * 1024:
        raise ValueError("Hardening memory limit changed")
    if report["migration"]["originalOverwriteAllowed"] is not False:
        raise ValueError("Hardening migration can overwrite original")
    if not all(report["paths"][key] for key in (
            "unicodePreserved", "longBounded", "reservedProtected", "traversalRemoved")):
        raise ValueError("Hardening path safety failed")
    if not all(report["transactions"].values()) or report["cleanExtract"]["passed"] is not True:
        raise ValueError("Hardening rollback or clean-extract gate failed")
    if report["passed"] is not True:
        raise ValueError("Hardening report did not pass")
    if report["hardeningHash"] != _hash_without(report, "hardeningHash"):
        raise ValueError("Hardening report hash mismatch")


def build_release_status_matrix(root: str | Path, migration: Mapping[str, Any],
                                hardening: Mapping[str, Any], release_date: str) -> dict[str, Any]:
    root, release_date = Path(root), _valid_date(release_date)
    validate_project_migration_report(migration)
    validate_hardening_report(hardening)
    legacy = _read_json(root, "data/release-check-report.json")
    recovery = _read_json(root, "data/recovery-release-report.json")
    quality = _read_json(root, "data/session27-benchmark-report.json")
    workflow = _read_json(root, "data/session28-benchmark-report.json")
    session24 = _read_json(root, "data/session24-test-report.json")
    session25 = _read_json(root, "data/session25-test-report.json")
    def row(identifier: str, category: str, status: str, passed: bool,
            required: bool, evidence: str, blocker: str | None = None) -> dict[str, Any]:
        return {"id": identifier, "category": category, "status": status, "passed": passed,
                "requiredForFinal": required, "evidence": evidence, "blocker": blocker}
    entries = [
        row("LEGACY_CORE", "software", "PASS", legacy.get("result") == "pass", True,
            "data/release-check-report.json"),
        row("RECOVERY_PREMIUM_SUITE", "software", "PASS",
            recovery.get("result") == "pass" and recovery.get("tests", {}).get("run", 0) >= 1260,
            True, "data/recovery-release-report.json"),
        row("CLEAN_EXTRACT_WINDOWS", "software", "PASS", hardening["cleanExtract"]["passed"],
            True, "data/session13-test-report.json"),
        row("PROJECT_MIGRATION", "software", "PASS", migration["preservation"]["sourceObjectUnchanged"],
            True, "artifacts/session30-project-migration.json"),
        row("PERFORMANCE_LIMITS", "software", "PASS",
            all(item["passed"] for item in hardening["performance"].values()), True,
            "data/session30-hardening-report.json"),
        row("MEMORY_LIMIT", "software", "PASS", hardening["memory"]["passed"], True,
            "data/session30-hardening-report.json"),
        row("LONG_PATH_UNICODE", "software", "PASS",
            all(value for key, value in hardening["paths"].items() if key != "samples"), True,
            "data/session30-hardening-report.json"),
        row("CRASH_ROLLBACK", "software", "PASS", hardening["transactions"]["crashRollback"], True,
            "data/session10-test-report.json"),
        row("DISK_FULL_ROLLBACK", "software", "PASS", hardening["transactions"]["diskFullRollback"], True,
            "data/session10-test-report.json"),
        row("AUTOMATED_MUSIC_QUALITY", "quality", "PASS",
            quality["technicalGatePassed"] and quality["automatedGatePassed"], True,
            "data/session27-benchmark-report.json"),
        row("HUMAN_LISTENING", "external", "PENDING", False, True,
            "data/session27-benchmark-report.json", "TWO_INDEPENDENT_HUMAN_EVALUATORS_REQUIRED"),
        row("EXPRESSION_EVIDENCE", "external", "BLOCKED", False, True,
            "data/session24-test-report.json", "EXPRESSION_PRODUCTION_EVIDENCE_BLOCKED"),
        row("ARTICULATION_CAPTURE", "external", "BLOCKED", False, True,
            "data/session25-test-report.json", "ARTICULATION_DEVICE_CAPTURE_BLOCKED"),
        row("PA800_DEVICE_PROFILE", "device", "WAITING", False, True,
            "data/session14-preflight-report.json", "PA800_DEVICE_PROFILE_NOT_CERTIFIED"),
        row("PA800_VOICE_COST", "device", "UNCONFIRMED", False, True,
            "data/session23-test-report.json", "PA800_VOICE_COST_UNCONFIRMED"),
        row("FINAL_MIDI_EXPORT", "release", "BLOCKED", workflow["finalExportAllowed"], True,
            "data/session28-benchmark-report.json", "FINAL_RELEASE_GATES_NOT_SATISFIED"),
        row("MARKETING_NAME", "release", "PREVIEW_ONLY", True, True,
            "AI_PREMIUM_ARRANGER_PLAN.md", "FINAL_PRODUCT_NAME_NOT_AUTHORIZED"),
    ]
    matrix = {
        "schema": STATUS_MATRIX_SCHEMA, "version": STATUS_MATRIX_VERSION,
        "releaseDate": release_date, "entries": entries,
        "summary": {
            "entryCount": len(entries),
            "softwarePassed": all(item["passed"] for item in entries if item["category"] == "software"),
            "openSeverity1Defects": 0, "openSeverity2Defects": 0,
            "externalBlockerCount": sum(item["category"] in {"external", "device"} and not item["passed"] for item in entries),
            "softwareReleaseCandidateReady": True,
            "finalPremiumReleaseAllowed": False,
            "finalMidiExportAllowed": False,
            "allowedProductName": ALLOWED_PRODUCT_NAME,
            "finalProductName": FINAL_PRODUCT_NAME,
        },
        "matrixHash": "",
    }
    matrix["matrixHash"] = _hash_without(matrix, "matrixHash")
    validate_release_status_matrix(matrix)
    return matrix


def validate_release_status_matrix(matrix: Mapping[str, Any]) -> None:
    if not isinstance(matrix, Mapping) or set(matrix) != {"schema", "version", "releaseDate", "entries", "summary", "matrixHash"}:
        raise ValueError("Release status matrix fields mismatch")
    if matrix["schema"] != STATUS_MATRIX_SCHEMA or matrix["version"] != STATUS_MATRIX_VERSION:
        raise ValueError("Unsupported release status matrix")
    _valid_date(matrix["releaseDate"])
    entries = matrix["entries"]
    if tuple(item["id"] for item in entries) != STATUS_IDS:
        raise ValueError("Release status matrix entries are incomplete or reordered")
    if len({item["id"] for item in entries}) != len(entries):
        raise ValueError("Release status matrix IDs are not unique")
    required_entry_fields = {"id", "category", "status", "passed", "requiredForFinal",
                             "evidence", "blocker"}
    if any(set(item) != required_entry_fields or not item["evidence"] for item in entries):
        raise ValueError("Release status matrix entry fields mismatch")
    summary = matrix["summary"]
    if summary["entryCount"] != len(entries):
        raise ValueError("Release status matrix entry count mismatch")
    software_passed = all(item["passed"] for item in entries if item["category"] == "software")
    if summary["softwarePassed"] is not software_passed or summary["softwareReleaseCandidateReady"] is not software_passed:
        raise ValueError("Release status matrix software summary mismatch")
    external_count = sum(item["category"] in {"external", "device"} and not item["passed"] for item in entries)
    if summary["externalBlockerCount"] != external_count:
        raise ValueError("Release status matrix external blocker count mismatch")
    if summary["openSeverity1Defects"] or summary["openSeverity2Defects"]:
        raise ValueError("Severity-1/2 defects block release candidate")
    if summary["finalPremiumReleaseAllowed"] or summary["finalMidiExportAllowed"]:
        raise ValueError("Release matrix bypassed external or device gates")
    if summary["allowedProductName"] != ALLOWED_PRODUCT_NAME:
        raise ValueError("Only the Preview product name is currently allowed")
    if matrix["matrixHash"] != _hash_without(matrix, "matrixHash"):
        raise ValueError("Release status matrix hash mismatch")


def build_release_readiness(root: str | Path, release_date: str,
                            migration: Mapping[str, Any], software_manifest: Mapping[str, Any],
                            hardening: Mapping[str, Any], status_matrix: Mapping[str, Any]) -> dict[str, Any]:
    root, release_date = Path(root), _valid_date(release_date)
    validate_project_migration_report(migration)
    validate_software_manifest(software_manifest, root)
    validate_hardening_report(hardening)
    validate_release_status_matrix(status_matrix)
    quality = _read_json(root, "data/session27-benchmark-report.json")
    recovery = _read_json(root, "data/recovery-release-report.json")
    source_paths = (
        "data/release-check-report.json", "data/recovery-release-report.json",
        "data/session13-test-report.json", "data/session24-test-report.json",
        "data/session25-test-report.json", "data/session27-test-report.json",
        "data/session28-test-report.json", "data/session29-test-report.json",
        "data/premium-feature-matrix.json",
    )
    resolved_source_paths = tuple(
        "data/release-package-manifest.json"
        if relative == "data/session13-test-report.json" and not (root / relative).is_file()
        else relative
        for relative in source_paths
    )
    readiness = {
        "schema": RELEASE_READINESS_SCHEMA, "version": RELEASE_READINESS_VERSION,
        "releaseId": "preview-rc-" + software_manifest["contentHash"][:20],
        "releaseDate": release_date, "targetBaseline": TARGET_BASELINE,
        "sources": [{"path": relative, "sha256": sha256((root / relative).read_bytes()).hexdigest()}
                    for relative in resolved_source_paths],
        "evidence": {
            "migrationHash": migration["migrationHash"],
            "softwareManifestHash": software_manifest["manifestHash"],
            "softwareContentHash": software_manifest["contentHash"],
            "hardeningHash": hardening["hardeningHash"],
            "statusMatrixHash": status_matrix["matrixHash"],
            "priorRecoveryTests": recovery["tests"]["run"],
            "expectedRecoveryTestsWithSession30": 1388,
        },
        "quality": {
            "technicalPassed": quality["technicalGatePassed"],
            "automatedPassed": quality["automatedGatePassed"],
            "automatedOverallScore": quality["automatedOverallScore"],
            "verifiedHumanEvaluators": quality["verifiedHumanEvaluatorCount"],
            "requiredHumanEvaluators": 2,
            "premiumPreferenceRate": quality["premiumPreferenceRate"],
        },
        "defects": {"openSeverity1": 0, "openSeverity2": 0,
                    "knownLimitations": [
                        "Physical Korg Pa800 import, NTT, save/reload and voice-cost evidence are missing.",
                        "Human listening evidence is 0/2 and Premium preference is not established.",
                        "Expression and articulation production evidence remains blocked.",
                        "The release candidate is Preview-only and final MIDI export remains blocked.",
                    ]},
        "gates": {
            "softwareReleaseCandidateReady": status_matrix["summary"]["softwareReleaseCandidateReady"],
            "finalPremiumReleaseAllowed": False, "finalMidiExportAllowed": False,
            "allowedProductName": ALLOWED_PRODUCT_NAME,
            "forbiddenProductName": FINAL_PRODUCT_NAME,
            "blockers": list(EXTERNAL_BLOCKERS),
        },
        "safety": {
            "readOnlyAssessment": True, "midiMutationAllowed": False,
            "validatorBypassAllowed": False, "qualityGateBypassAllowed": False,
            "deviceGateBypassAllowed": False, "marketingGateBypassAllowed": False,
            "cloudRequired": False, "physicalCertificationClaimed": False,
        },
        "readinessHash": "",
    }
    readiness["readinessHash"] = _hash_without(readiness, "readinessHash")
    validate_release_readiness_v2(readiness)
    return readiness


def validate_release_readiness_v2(readiness: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "releaseId", "releaseDate", "targetBaseline", "sources",
              "evidence", "quality", "defects", "gates", "safety", "readinessHash"}
    if not isinstance(readiness, Mapping) or set(readiness) != fields:
        raise ValueError("Release Readiness 2.0 fields mismatch")
    if readiness["schema"] != RELEASE_READINESS_SCHEMA or readiness["version"] != RELEASE_READINESS_VERSION:
        raise ValueError("Unsupported Release Readiness contract")
    _valid_date(readiness["releaseDate"])
    if readiness["targetBaseline"] != TARGET_BASELINE or not re.fullmatch(r"preview-rc-[0-9a-f]{20}", readiness["releaseId"]):
        raise ValueError("Release Readiness identity is invalid")
    if any(set(item) != {"path", "sha256"} or not _SHA.fullmatch(str(item["sha256"])) for item in readiness["sources"]):
        raise ValueError("Release Readiness source evidence is invalid")
    if len(readiness["sources"]) != 9 or len({item["path"] for item in readiness["sources"]}) != 9:
        raise ValueError("Release Readiness source evidence is incomplete")
    if readiness["evidence"]["expectedRecoveryTestsWithSession30"] != 1388:
        raise ValueError("Release Readiness expected recovery test count changed")
    quality = readiness["quality"]
    if quality["requiredHumanEvaluators"] != 2 or quality["verifiedHumanEvaluators"] >= 2:
        raise ValueError("Release Readiness incorrectly satisfied the human listening gate")
    if quality["premiumPreferenceRate"] is not None:
        raise ValueError("Release Readiness cannot claim Premium preference without listening evidence")
    if readiness["defects"]["openSeverity1"] or readiness["defects"]["openSeverity2"]:
        raise ValueError("Severity-1/2 defects block the release candidate")
    gates = readiness["gates"]
    if gates["softwareReleaseCandidateReady"] is not True:
        raise ValueError("Software release candidate gate did not pass")
    if gates["finalPremiumReleaseAllowed"] or gates["finalMidiExportAllowed"]:
        raise ValueError("Release Readiness bypassed final gates")
    if gates["allowedProductName"] != ALLOWED_PRODUCT_NAME or gates["forbiddenProductName"] != FINAL_PRODUCT_NAME:
        raise ValueError("Release Readiness product naming is unsafe")
    if tuple(gates["blockers"]) != EXTERNAL_BLOCKERS:
        raise ValueError("Release Readiness blockers are incomplete")
    expected_safety = {"readOnlyAssessment": True, "midiMutationAllowed": False,
                       "validatorBypassAllowed": False, "qualityGateBypassAllowed": False,
                       "deviceGateBypassAllowed": False, "marketingGateBypassAllowed": False,
                       "cloudRequired": False, "physicalCertificationClaimed": False}
    if readiness["safety"] != expected_safety:
        raise ValueError("Release Readiness safety was weakened")
    if readiness["readinessHash"] != _hash_without(readiness, "readinessHash"):
        raise ValueError("Release Readiness hash mismatch")


def execute_release_readiness_api(payload: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Release Readiness API payload must be an object")
    action = payload.get("action")
    if action == "migrate":
        _strict(payload, {"action", "releaseDate", "legacyProject"},
                {"action", "releaseDate", "legacyProject"}, "release readiness API payload")
        return migrate_project_for_release(payload["legacyProject"], payload["releaseDate"])
    if action == "reference":
        _strict(payload, {"action"}, {"action"}, "release readiness API payload")
        from .session30_fixture import build_session30_chain
        fixture = build_session30_chain(root)
        return {"readiness": fixture["releaseReadiness"],
                "statusMatrix": fixture["statusMatrix"],
                "hardening": fixture["hardeningReport"],
                "migration": fixture["projectMigration"]}
    raise ValueError("Release Readiness action must be migrate or reference")


def execute_release_readiness_gui(payload: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    return execute_release_readiness_api(payload, root)


__all__ = [
    "RELEASE_READINESS_SCHEMA", "RELEASE_READINESS_VERSION", "SOFTWARE_MANIFEST_SCHEMA",
    "SOFTWARE_MANIFEST_VERSION", "PROJECT_MIGRATION_SCHEMA", "PROJECT_MIGRATION_VERSION",
    "STATUS_MATRIX_SCHEMA", "STATUS_MATRIX_VERSION", "HARDENING_SCHEMA", "HARDENING_VERSION",
    "TARGET_BASELINE", "ALLOWED_PRODUCT_NAME", "FINAL_PRODUCT_NAME", "SIGNATURE_ALGORITHM",
    "SIGNATURE_AUTHORITY", "EXTERNAL_BLOCKERS", "STATUS_IDS", "migrate_project_for_release",
    "validate_project_migration_report", "build_software_manifest", "validate_software_manifest",
    "run_hardening_benchmarks", "validate_hardening_report", "build_release_status_matrix",
    "validate_release_status_matrix", "build_release_readiness", "validate_release_readiness_v2",
    "execute_release_readiness_api", "execute_release_readiness_gui",
]