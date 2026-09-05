"""Prepare and evaluate the human-operated Korg Pa800 device test.

The module can prove that the test kit is internally valid and that supplied
evidence files match their hashes.  It cannot observe a physical instrument;
device certification therefore always requires explicit human attestation.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any
import zipfile


KIT_SCHEMA = "dna-pa800-device-test-kit"
RESULT_SCHEMA = "dna-pa800-device-test-result"
REPORT_SCHEMA = "dna-pa800-device-certification-report"
VERSION = "1.0"

REQUIRED_CHECKS = (
    "usbMediaReadable",
    "shiftExecuteImportCompleted",
    "allMarkersImported",
    "channelsNineThroughSixteenCorrect",
    "trackTypesConfirmed",
    "nttConfirmed",
    "allStyleTransitionsAudible",
    "noStuckOrMissingNotes",
    "normalStyleNoUnacceptableVoiceStealing",
    "polyphonyStressNoUnacceptableVoiceStealing",
    "soloOriginalPreserved",
    "delayRoutingCorrect",
    "rxDncOnlyOnConfirmedSounds",
    "noDigitalClipping",
    "headroomAcceptable",
    "savedToUserOrFavorite",
    "reloadMatchesSavedStyle",
)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".m4a"}
KIT_ZIP_NAME = "DNA-PA800-Session14-Device-Test-Kit.zip"
KIT_ZIP_FILES = (
    "DNA_PA800_FUNCTIONAL_TEST.mid",
    "DNA_PA800_FUNCTIONAL_TEST.manifest.json",
    "DNA_PA800_POLYPHONY_STRESS.mid",
    "DNA_PA800_POLYPHONY_STRESS.manifest.json",
    "README.md",
    "kit-manifest.json",
    "device-result-template.json",
)
ZIP_TIME = (2026, 9, 2, 0, 0, 0)


def sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _stress_pitches(track_name: str) -> list[int]:
    return {
        "bass": [36],
        "drum": list(range(35, 59)),
        "perc": list(range(60, 76)),
        "acc1": [48, 52, 55, 60],
        "acc2": [60, 64, 67, 72],
        "acc3": [72],
        "acc4": [84],
        "acc5": [48, 55, 60],
    }[track_name]


def _build_polyphony_stress(server: Any, validator: Any) -> tuple[bytes, dict[str, Any]]:
    events = [
        {"tick": 0, "priority": 0, "data": server.meta_text(3, "DNA PA800 POLYPHONY")},
        {"tick": 0, "priority": 0, "data": server.meta_text(6, "v1cv1")},
        {"tick": 0, "priority": 1, "data": server.tempo_meta(120)},
        {"tick": 0, "priority": 1, "data": server.meter_meta(4, 4)},
    ]
    tracks: dict[str, Any] = {}
    for track_name, track_info in server.TRACKS.items():
        choice = server.OPTIONS["tracks"][track_name][0]
        channel = track_info["channel"]
        pitches = _stress_pitches(track_name)
        if len(pitches) != server.POLYPHONY_LIMITS[track_name]:
            raise RuntimeError(f"Stress pitch count does not match {track_name} limit")
        events.extend((
            {"tick": 0, "priority": 2, "data": [0xB0 | channel, 0, choice["bankMsb"]]},
            {"tick": 0, "priority": 2, "data": [0xB0 | channel, 32, choice["bankLsb"]]},
            {"tick": 0, "priority": 2, "data": [0xC0 | channel, choice["program"]]},
            {"tick": 0, "priority": 2, "data": [0xB0 | channel, 11, 127]},
        ))
        for start in (0, 480, 960, 1440):
            for pitch in pitches:
                profile = server.drum_profile(choice, pitch) if track_name in ("drum", "perc") else choice
                velocity = server.velocity_at(profile, 80)
                events.extend((
                    {"tick": start, "priority": 4, "data": [0x90 | channel, pitch, velocity]},
                    {"tick": start + 240, "priority": 3, "data": [0x80 | channel, pitch, 0]},
                ))
        tracks[track_name] = {
            "channel": channel + 1,
            "polyphonyLimit": server.POLYPHONY_LIMITS[track_name],
            "pitches": pitches,
            "bankMsb": choice["bankMsb"],
            "bankLsb": choice["bankLsb"],
            "program": choice["program"],
            "profileId": choice["id"],
        }
    events.append({"tick": 1920, "priority": 9, "data": [0xFF, 0x2F, 0]})
    midi = server.smf0(events, 480)
    validation = validator.validate_pa800_smf(midi, ["v1cv1"], list(range(8, 16)))
    if not validation["passed"]:
        raise RuntimeError("Generated device stress MIDI failed validation: " + "; ".join(validation["issues"]))
    expected_peak = sum(server.POLYPHONY_LIMITS.values())
    if validation["globalPeakConcurrentNotes"] != expected_peak:
        raise RuntimeError("Generated device stress MIDI has an unexpected global peak")
    return midi, {
        "schema": "dna-pa800-polyphony-stress-manifest",
        "version": VERSION,
        "target": "Korg Pa800 OS 2.0+",
        "referenceKey": "C",
        "referenceChord": "Major",
        "marker": "v1cv1",
        "tempo": 120,
        "ppq": 480,
        "tracks": tracks,
        "expectedMidiNotePeak": expected_peak,
        "physicalSoundVoiceCostVerified": False,
        "validation": validation,
        "midiSha256": sha256(midi).hexdigest(),
    }


def _kit_readme() -> str:
    checks = "\n".join(f"- [ ] {name}" for name in REQUIRED_CHECKS)
    return f"""# Session 14 - Korg Pa800 physical device test

This directory is a prepared test kit. Its presence does not certify a device.

## Import procedure

1. Copy both `.mid` files to USB media readable by the Pa800.
2. Open Style Record and create a new Style.
3. Choose Import SMF, keep SHIFT pressed and choose Execute.
4. Use Initialize for a new Style.
5. Confirm original Key/Chord C Major, Track Type and NTT.
6. Test the functional Style first, then the polyphony stress Style.
7. Save to USER/FAVORITE, reload it and repeat the transition test.
8. Add at least one image and one audio evidence file beside the result JSON.
9. Fill `device-result-template.json`, save it under a new name and run verification.

The polyphony file reaches the project's simultaneous MIDI-note limits. It is
not a claim about oscillator consumption: a Pa800 Sound may consume more than
one hardware voice per MIDI note, which is why listening on the device is mandatory.

## Required observations

{checks}
"""


def build_device_kit_zip(output_dir: Path) -> Path:
    """Build a deterministic ZIP without collecting user evidence/results."""
    output_dir = output_dir.resolve()
    paths = [output_dir / name for name in KIT_ZIP_FILES]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing device kit files: " + ", ".join(missing))
    zip_path = output_dir / KIT_ZIP_NAME
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in paths:
            info = zipfile.ZipInfo(path.name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return zip_path


def prepare_device_kit(root: Path, output_dir: Path) -> dict[str, Any]:
    from . import pa800_validator
    import server

    root = root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if server.FACTORY is None:
        server.load_data()

    functional_config = {
        "name": "DNA PA800 DEVICE TEST",
        "tempo": 120,
        "meter": "4/4",
        "seed": 140014001,
        "elements": server.DEFAULT_ELEMENTS,
        "tracks": {name: {"enabled": True} for name in server.TRACKS},
    }
    functional_midi, functional_manifest = server.build_pa800_style(functional_config)
    stress_midi, stress_manifest = _build_polyphony_stress(server, pa800_validator)

    files = {
        "functionalMidi": output_dir / "DNA_PA800_FUNCTIONAL_TEST.mid",
        "functionalManifest": output_dir / "DNA_PA800_FUNCTIONAL_TEST.manifest.json",
        "polyphonyMidi": output_dir / "DNA_PA800_POLYPHONY_STRESS.mid",
        "polyphonyManifest": output_dir / "DNA_PA800_POLYPHONY_STRESS.manifest.json",
        "instructions": output_dir / "README.md",
    }
    files["functionalMidi"].write_bytes(functional_midi)
    _write_json(files["functionalManifest"], functional_manifest)
    files["polyphonyMidi"].write_bytes(stress_midi)
    _write_json(files["polyphonyManifest"], stress_manifest)
    files["instructions"].write_text(_kit_readme(), encoding="utf-8")

    roles = {
        "functionalMidi": "normal-style-import-and-transition-test",
        "functionalManifest": "functional-style-machine-evidence",
        "polyphonyMidi": "project-limit-polyphony-listening-test",
        "polyphonyManifest": "polyphony-stress-machine-evidence",
        "instructions": "human-device-procedure",
    }
    artifacts = [
        {"key": key, "role": roles[key], "path": path.name,
         "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for key, path in files.items()
    ]
    kit_manifest = {
        "schema": KIT_SCHEMA,
        "version": VERSION,
        "date": date.today().isoformat(),
        "target": "Korg Pa800 OS 2.0+",
        "status": "PREPARED_WAITING_FOR_DEVICE",
        "officialImportContract": {
            "format": 0,
            "channels": list(range(9, 17)),
            "markerImport": "Style Record > Import SMF > hold SHIFT > Execute",
            "newStyleInitialize": True,
            "requiredCvStartEvents": ["Time Signature", "CC00", "CC32", "Program Change", "CC11"],
        },
        "requiredChecks": list(REQUIRED_CHECKS),
        "requiredEvidence": ["at least one image", "at least one audio recording"],
        "artifacts": artifacts,
        "certification": {"softwarePreflight": "PASS", "physicalPa800": "WAITING_FOR_DEVICE"},
    }
    manifest_path = output_dir / "kit-manifest.json"
    _write_json(manifest_path, kit_manifest)

    result_template = {
        "schema": RESULT_SCHEMA,
        "version": VERSION,
        "kitManifestSha256": sha256_file(manifest_path),
        "artifactSha256": {item["path"]: item["sha256"] for item in artifacts},
        "operator": "",
        "testDate": "",
        "device": {"model": "Korg Pa800", "serialNumber": "", "osVersion": ""},
        "checks": {name: None for name in REQUIRED_CHECKS},
        "observations": {"normalStyle": "", "polyphonyStress": "", "rxDncSolo": ""},
        "evidenceFiles": [],
        "attestation": {"physicalDeviceUsed": False, "resultTruthful": False, "signature": ""},
    }
    template_path = output_dir / "device-result-template.json"
    _write_json(template_path, result_template)
    zip_path = build_device_kit_zip(output_dir)
    return {
        "manifest": kit_manifest,
        "manifestPath": manifest_path,
        "manifestSha256": sha256_file(manifest_path),
        "resultTemplatePath": template_path,
        "functionalMidi": files["functionalMidi"],
        "polyphonyMidi": files["polyphonyMidi"],
        "deviceKitZip": zip_path,
        "deviceKitZipSha256": sha256_file(zip_path),
    }


def _safe_evidence_path(base: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Evidence path must stay beside the result file")
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError("Evidence path must stay beside the result file") from exc
    return candidate


def evaluate_device_result(result_path: Path, kit_manifest_path: Path) -> dict[str, Any]:
    result_path = result_path.resolve()
    kit_manifest_path = kit_manifest_path.resolve()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    kit = json.loads(kit_manifest_path.read_text(encoding="utf-8"))
    issues: list[str] = []

    if result.get("schema") != RESULT_SCHEMA or result.get("version") != VERSION:
        issues.append("Unsupported device result schema/version")
    if kit.get("schema") != KIT_SCHEMA or kit.get("version") != VERSION:
        issues.append("Unsupported device kit schema/version")
    if result.get("kitManifestSha256") != sha256_file(kit_manifest_path):
        issues.append("Kit manifest SHA-256 does not match")

    expected_hashes = {item["path"]: item["sha256"] for item in kit.get("artifacts", [])}
    if result.get("artifactSha256") != expected_hashes:
        issues.append("Test artifact SHA-256 set does not match the kit")
    for item in kit.get("artifacts", []):
        try:
            artifact_path = _safe_evidence_path(kit_manifest_path.parent, str(item.get("path", "")))
        except ValueError:
            issues.append("Kit artifact path escapes the kit directory")
            continue
        if not artifact_path.is_file():
            issues.append(f"Kit artifact is missing: {item.get('path', '')}")
        elif sha256_file(artifact_path) != item.get("sha256"):
            issues.append(f"Kit artifact SHA-256 mismatch: {item.get('path', '')}")

    if not str(result.get("operator", "")).strip():
        issues.append("Operator name is required")
    try:
        test_date = date.fromisoformat(result.get("testDate", ""))
        if test_date > date.today():
            issues.append("Device test date cannot be in the future")
    except (TypeError, ValueError):
        issues.append("A valid device test date is required")

    device = result.get("device") or {}
    if device.get("model") != "Korg Pa800":
        issues.append("Device model must be Korg Pa800")
    if not str(device.get("serialNumber", "")).strip():
        issues.append("Device serial number is required")
    version_match = re.search(r"\d+(?:\.\d+)?", str(device.get("osVersion", "")))
    if not version_match or float(version_match.group()) < 2.0:
        issues.append("Pa800 OS version 2.0 or newer is required")

    checks = result.get("checks") or {}
    missing_checks = [name for name in REQUIRED_CHECKS if checks.get(name) is not True]
    if missing_checks:
        issues.append("Required device checks are not all PASS: " + ", ".join(missing_checks))

    attestation = result.get("attestation") or {}
    if attestation.get("physicalDeviceUsed") is not True:
        issues.append("Physical-device attestation is required")
    if attestation.get("resultTruthful") is not True:
        issues.append("Truthfulness attestation is required")
    if not str(attestation.get("signature", "")).strip():
        issues.append("Operator signature is required")

    evidence = result.get("evidenceFiles") or []
    image_found = audio_found = False
    verified_evidence = []
    for item in evidence:
        try:
            path = _safe_evidence_path(result_path.parent, str(item.get("path", "")))
        except ValueError as error:
            issues.append(str(error))
            continue
        if not path.is_file():
            issues.append(f"Evidence file is missing: {item.get('path', '')}")
            continue
        actual_hash = sha256_file(path)
        if item.get("sha256") != actual_hash:
            issues.append(f"Evidence SHA-256 mismatch: {item.get('path', '')}")
            continue
        suffix = path.suffix.lower()
        image_found = image_found or suffix in IMAGE_SUFFIXES
        audio_found = audio_found or suffix in AUDIO_SUFFIXES
        verified_evidence.append({"path": item["path"], "sha256": actual_hash})
    if not image_found:
        issues.append("At least one hashed image evidence file is required")
    if not audio_found:
        issues.append("At least one hashed audio evidence file is required")

    explicit_failure = any(value is False for value in checks.values())
    passed = not issues
    status = "PA800_DEVICE_CERTIFIED" if passed else (
        "DEVICE_TEST_FAILED" if explicit_failure else "WAITING_FOR_DEVICE"
    )
    return {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "date": date.today().isoformat(),
        "result": "pass" if passed else "fail",
        "status": status,
        "issues": issues,
        "operator": result.get("operator", ""),
        "device": deepcopy(device),
        "testDate": result.get("testDate", ""),
        "resultSha256": sha256_file(result_path),
        "kitManifestSha256": sha256_file(kit_manifest_path),
        "checks": {name: checks.get(name) is True for name in REQUIRED_CHECKS},
        "verifiedEvidence": verified_evidence,
        "humanAttested": passed,
        "machineObservedPhysicalDevice": False,
    }