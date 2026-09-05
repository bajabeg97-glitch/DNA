"""Strict Pa800 Device Lab intake and DeviceProfile 2.0 certification.

The module validates evidence supplied by a human operator.  It never claims
that software observed a physical instrument, and the built-in reference
artifacts always remain ``WAITING_FOR_DEVICE``.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .device_certification import REQUIRED_CHECKS, sha256_file


CAPTURE_SCHEMA = "dna-pa800-device-capture"
CAPTURE_VERSION = "2.0"
PROFILE_SCHEMA = "dna-premium-device-profile"
PROFILE_VERSION = "2.0"
REPORT_SCHEMA = "dna-pa800-device-certification-report"
REPORT_VERSION = "2.0"

CORE_CHANNELS = tuple(range(9, 17))
MARKERS = ("i1cv1", "i2cv1", "v1cv1", "v2cv1", "v3cv1", "v4cv1",
           "f1cv1", "f2cv1", "e1cv1", "e2cv1")
CHANNEL_ROLES = {
    9: "bass", 10: "drums", 11: "percussion", 12: "acc1",
    13: "acc2", 14: "acc3", 15: "acc4", 16: "acc5",
}
ARTICULATION_ENGINES = ("GUITAR", "RX", "DNC")
MAP_STATUSES = ("CONFIRMED", "UNSUPPORTED", "UNKNOWN")
CAPTURE_AUTHORITIES = ("PENDING", "SOFTWARE_TEST_ONLY", "PHYSICAL_OPERATOR_CAPTURE")
STANDARD_TRIGGER_TYPES = ("KEY_SWITCH", "CC", "CHANNEL_PRESSURE")
EVIDENCE_KINDS = ("IMAGE", "AUDIO")


class DeviceCertificationError(ValueError):
    """Raised when a Device Lab document violates its strict contract."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _hash(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _strict(value: Mapping[str, Any], required: set[str], allowed: set[str], label: str) -> None:
    if not isinstance(value, Mapping):
        raise DeviceCertificationError(f"{label} must be an object")
    missing = required - set(value)
    unknown = set(value) - allowed
    if missing:
        raise DeviceCertificationError(f"{label} missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise DeviceCertificationError(f"{label} unknown fields: {', '.join(sorted(unknown))}")


def _safe_relative(base: Path, value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise DeviceCertificationError("Evidence path must be relative and traversal-free")
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as exc:
        raise DeviceCertificationError("Evidence path escapes the capture directory") from exc
    return candidate


def _media_magic(path: Path, kind: str) -> bool:
    raw = path.read_bytes()[:32]
    if kind == "IMAGE":
        return raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff")
    if kind == "AUDIO":
        return ((raw.startswith(b"RIFF") and raw[8:12] == b"WAVE")
                or raw.startswith(b"fLaC") or raw.startswith(b"ID3")
                or (len(raw) >= 12 and raw[4:8] == b"ftyp"))
    return False


def compute_capture_hash(capture: Mapping[str, Any]) -> str:
    value = deepcopy(dict(capture))
    value["captureHash"] = ""
    if isinstance(value.get("attestation"), dict):
        value["attestation"]["operatorApprovedHash"] = ""
    return _hash(value)


def compute_operator_approved_hash(capture: Mapping[str, Any]) -> str:
    operator = capture.get("operator") or {}
    device = capture.get("device") or {}
    attestation = capture.get("attestation") or {}
    return _hash({
        "captureHash": capture.get("captureHash", ""),
        "operatorName": operator.get("name", ""),
        "operatorSignature": attestation.get("signature", ""),
        "deviceSerialNumber": device.get("serialNumber", ""),
        "testDate": capture.get("testDate", ""),
    })


def seal_device_capture(capture: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(capture))
    value["captureHash"] = compute_capture_hash(value)
    value.setdefault("attestation", {})["operatorApprovedHash"] = compute_operator_approved_hash(value)
    return value


def _load_kit(root: Path) -> tuple[Path, dict[str, Any]]:
    path = root / "artifacts/session14-device-kit/kit-manifest.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "dna-pa800-device-test-kit" or value.get("version") != "1.0":
        raise DeviceCertificationError("Unsupported Session 14 kit manifest")
    return path, value


def build_device_capture_template(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    kit_path, kit = _load_kit(root)
    bindings = [{
        "channel": channel,
        "role": CHANNEL_ROLES[channel],
        "status": "UNKNOWN",
        "bankMsb": None,
        "bankLsb": None,
        "program": None,
        "soundName": "",
        "trackType": "",
        "ntt": "",
        "evidenceRefs": [],
    } for channel in CORE_CHANNELS]
    articulations = [{
        "engine": engine,
        "status": "UNKNOWN",
        "soundBinding": None,
        "triggerMap": [],
        "evidenceRefs": [],
    } for engine in ARTICULATION_ENGINES]
    voice_roles = [{
        "role": CHANNEL_ROLES[channel],
        "channel": channel,
        "oscillatorVoicesPerNote": None,
        "acceptableVoiceStealing": None,
        "evidenceRefs": [],
    } for channel in CORE_CHANNELS]
    value: dict[str, Any] = {
        "schema": CAPTURE_SCHEMA,
        "version": CAPTURE_VERSION,
        "captureAuthority": "PENDING",
        "kitManifestSha256": sha256_file(kit_path),
        "sourceArtifactSha256": {row["path"]: row["sha256"] for row in kit["artifacts"]},
        "operator": {"name": "", "organization": ""},
        "device": {"manufacturer": "Korg", "model": "Pa800", "serialNumber": "", "osVersion": ""},
        "testDate": "",
        "checks": {name: None for name in REQUIRED_CHECKS},
        "markerResults": {marker: None for marker in MARKERS},
        "styleBindings": bindings,
        "articulationCaptures": articulations,
        "voiceMeasurements": {
            "midiNoteCeiling": 54,
            "measuredSafeMidiPeak": None,
            "unacceptableVoiceStealingObserved": None,
            "roleVoiceCosts": voice_roles,
            "evidenceRefs": [],
        },
        "evidenceFiles": [],
        "attestation": {
            "physicalDeviceUsed": False,
            "resultTruthful": False,
            "independentReviewCompleted": False,
            "signature": "",
            "operatorApprovedHash": "",
        },
        "captureHash": "",
    }
    value["captureHash"] = compute_capture_hash(value)
    return value


def validate_device_capture(capture: Mapping[str, Any], *, require_seal: bool = True) -> None:
    root_fields = {
        "schema", "version", "captureAuthority", "kitManifestSha256", "sourceArtifactSha256",
        "operator", "device", "testDate", "checks", "markerResults", "styleBindings",
        "articulationCaptures", "voiceMeasurements", "evidenceFiles", "attestation", "captureHash",
    }
    _strict(capture, root_fields, root_fields, "device capture")
    if capture["schema"] != CAPTURE_SCHEMA or capture["version"] != CAPTURE_VERSION:
        raise DeviceCertificationError("Unsupported device capture schema/version")
    if capture["captureAuthority"] not in CAPTURE_AUTHORITIES:
        raise DeviceCertificationError("Unsupported capture authority")
    if not re.fullmatch(r"[0-9a-f]{64}", str(capture["kitManifestSha256"])):
        raise DeviceCertificationError("Invalid kit manifest SHA-256")
    if not isinstance(capture["sourceArtifactSha256"], Mapping):
        raise DeviceCertificationError("sourceArtifactSha256 must be an object")
    if any(not re.fullmatch(r"[0-9a-f]{64}", str(value))
           for value in capture["sourceArtifactSha256"].values()):
        raise DeviceCertificationError("Invalid source artifact SHA-256")

    _strict(capture["operator"], {"name", "organization"}, {"name", "organization"}, "operator")
    _strict(capture["device"], {"manufacturer", "model", "serialNumber", "osVersion"},
            {"manufacturer", "model", "serialNumber", "osVersion"}, "device")
    if capture["device"]["manufacturer"] != "Korg" or capture["device"]["model"] != "Pa800":
        raise DeviceCertificationError("Device identity must be Korg Pa800")

    if set(capture["checks"]) != set(REQUIRED_CHECKS):
        raise DeviceCertificationError("Device check set is incomplete or contains unknown checks")
    if any(value not in (True, False, None) for value in capture["checks"].values()):
        raise DeviceCertificationError("Device checks must be true, false or null")
    if tuple(capture["markerResults"]) != MARKERS:
        raise DeviceCertificationError("Marker result order/coverage mismatch")
    if any(value not in (True, False, None) for value in capture["markerResults"].values()):
        raise DeviceCertificationError("Marker results must be true, false or null")

    if not isinstance(capture["styleBindings"], list) or len(capture["styleBindings"]) != 8:
        raise DeviceCertificationError("Exactly eight Style bindings are required")
    seen_channels: set[int] = set()
    binding_fields = {"channel", "role", "status", "bankMsb", "bankLsb", "program",
                      "soundName", "trackType", "ntt", "evidenceRefs"}
    for binding in capture["styleBindings"]:
        _strict(binding, binding_fields, binding_fields, "Style binding")
        channel = binding["channel"]
        if channel not in CORE_CHANNELS or channel in seen_channels:
            raise DeviceCertificationError("Style binding channel coverage is invalid")
        seen_channels.add(channel)
        if binding["role"] != CHANNEL_ROLES[channel]:
            raise DeviceCertificationError("Style binding role/channel mismatch")
        if binding["status"] not in MAP_STATUSES:
            raise DeviceCertificationError("Invalid Style binding status")
        for key in ("bankMsb", "bankLsb", "program"):
            if binding[key] is not None and (not isinstance(binding[key], int) or not 0 <= binding[key] <= 127):
                raise DeviceCertificationError(f"Invalid {key}")
        if not isinstance(binding["evidenceRefs"], list) or len(set(binding["evidenceRefs"])) != len(binding["evidenceRefs"]):
            raise DeviceCertificationError("Style binding evidence references must be unique")

    if not isinstance(capture["articulationCaptures"], list) or len(capture["articulationCaptures"]) != 3:
        raise DeviceCertificationError("Exactly three articulation capture rows are required")
    if tuple(row.get("engine") for row in capture["articulationCaptures"]) != ARTICULATION_ENGINES:
        raise DeviceCertificationError("Articulation engine coverage/order mismatch")
    articulation_fields = {"engine", "status", "soundBinding", "triggerMap", "evidenceRefs"}
    trigger_fields = {"type", "number", "value", "noteOffTicks"}
    for row in capture["articulationCaptures"]:
        _strict(row, articulation_fields, articulation_fields, "articulation capture")
        if row["status"] not in MAP_STATUSES:
            raise DeviceCertificationError("Invalid articulation status")
        if not isinstance(row["triggerMap"], list) or not isinstance(row["evidenceRefs"], list):
            raise DeviceCertificationError("Articulation maps and evidenceRefs must be arrays")
        for trigger in row["triggerMap"]:
            _strict(trigger, trigger_fields, trigger_fields, "articulation trigger")
            if trigger["type"] not in STANDARD_TRIGGER_TYPES:
                raise DeviceCertificationError("Unsupported articulation trigger type")
            if not 0 <= trigger["number"] <= 127 or not 0 <= trigger["value"] <= 127:
                raise DeviceCertificationError("Articulation trigger number/value is invalid")
            if trigger["type"] == "KEY_SWITCH" and (not isinstance(trigger["noteOffTicks"], int)
                                                        or trigger["noteOffTicks"] <= 0):
                raise DeviceCertificationError("Key-switch requires positive note-off ticks")

    voice = capture["voiceMeasurements"]
    voice_fields = {"midiNoteCeiling", "measuredSafeMidiPeak", "unacceptableVoiceStealingObserved",
                    "roleVoiceCosts", "evidenceRefs"}
    _strict(voice, voice_fields, voice_fields, "voice measurements")
    if voice["midiNoteCeiling"] != 54:
        raise DeviceCertificationError("Software MIDI-note ceiling cannot be weakened")
    if voice["measuredSafeMidiPeak"] is not None and (
            not isinstance(voice["measuredSafeMidiPeak"], int)
            or not 1 <= voice["measuredSafeMidiPeak"] <= 54):
        raise DeviceCertificationError("Measured safe MIDI peak is invalid")
    costs = voice["roleVoiceCosts"]
    if not isinstance(costs, list) or len(costs) != 8:
        raise DeviceCertificationError("Eight role voice-cost rows are required")
    cost_fields = {"role", "channel", "oscillatorVoicesPerNote", "acceptableVoiceStealing", "evidenceRefs"}
    for position, cost in enumerate(costs):
        _strict(cost, cost_fields, cost_fields, "role voice cost")
        channel = CORE_CHANNELS[position]
        if cost["channel"] != channel or cost["role"] != CHANNEL_ROLES[channel]:
            raise DeviceCertificationError("Role voice-cost channel/order mismatch")
        value = cost["oscillatorVoicesPerNote"]
        if value is not None and (not isinstance(value, int) or not 1 <= value <= 16):
            raise DeviceCertificationError("Oscillator voice cost is invalid")
        if cost["acceptableVoiceStealing"] not in (True, False, None):
            raise DeviceCertificationError("Voice-stealing observation must be true, false or null")

    evidence_fields = {"id", "kind", "path", "sha256", "bytes", "capturedOnDevice", "description"}
    evidence_ids: set[str] = set()
    for evidence in capture["evidenceFiles"]:
        _strict(evidence, evidence_fields, evidence_fields, "evidence file")
        if evidence["id"] in evidence_ids or not re.fullmatch(r"evidence-[a-z0-9-]+", evidence["id"]):
            raise DeviceCertificationError("Evidence IDs must be unique stable identifiers")
        evidence_ids.add(evidence["id"])
        if evidence["kind"] not in EVIDENCE_KINDS:
            raise DeviceCertificationError("Unsupported evidence kind")
        if not re.fullmatch(r"[0-9a-f]{64}", str(evidence["sha256"])):
            raise DeviceCertificationError("Invalid evidence SHA-256")
        if not isinstance(evidence["bytes"], int) or evidence["bytes"] <= 0:
            raise DeviceCertificationError("Evidence byte count must be positive")

    refs: list[str] = []
    refs.extend(ref for row in capture["styleBindings"] for ref in row["evidenceRefs"])
    refs.extend(ref for row in capture["articulationCaptures"] for ref in row["evidenceRefs"])
    refs.extend(voice["evidenceRefs"])
    refs.extend(ref for row in costs for ref in row["evidenceRefs"])
    if any(ref not in evidence_ids for ref in refs):
        raise DeviceCertificationError("Evidence reference does not resolve")

    attestation_fields = {"physicalDeviceUsed", "resultTruthful", "independentReviewCompleted",
                          "signature", "operatorApprovedHash"}
    _strict(capture["attestation"], attestation_fields, attestation_fields, "attestation")
    approval = capture["attestation"]["operatorApprovedHash"]
    if approval and not re.fullmatch(r"[0-9a-f]{64}", str(approval)):
        raise DeviceCertificationError("Invalid operator-approved hash")
    if not re.fullmatch(r"[0-9a-f]{64}", str(capture["captureHash"])):
        raise DeviceCertificationError("Invalid capture hash")
    if require_seal:
        if capture["captureHash"] != compute_capture_hash(capture):
            raise DeviceCertificationError("Capture hash mismatch")
        if approval and approval != compute_operator_approved_hash(capture):
            raise DeviceCertificationError("Operator-approved hash mismatch")


def _physical_readiness_issues(capture: Mapping[str, Any], root: Path,
                               capture_path: Path | None) -> tuple[list[str], list[dict[str, Any]]]:
    issues: list[str] = []
    verified: list[dict[str, Any]] = []
    kit_path, kit = _load_kit(root)
    if capture["kitManifestSha256"] != sha256_file(kit_path):
        issues.append("KIT_MANIFEST_HASH_MISMATCH")
    expected_artifacts = {row["path"]: row["sha256"] for row in kit["artifacts"]}
    if capture["sourceArtifactSha256"] != expected_artifacts:
        issues.append("SOURCE_ARTIFACT_HASH_SET_MISMATCH")
    if capture["captureAuthority"] != "PHYSICAL_OPERATOR_CAPTURE":
        issues.append("PHYSICAL_OPERATOR_CAPTURE_REQUIRED")
    if not str(capture["operator"]["name"]).strip():
        issues.append("OPERATOR_NAME_REQUIRED")
    if not str(capture["device"]["serialNumber"]).strip():
        issues.append("DEVICE_SERIAL_REQUIRED")
    match = re.search(r"\d+(?:\.\d+)?", str(capture["device"]["osVersion"]))
    if not match or float(match.group()) < 2.0:
        issues.append("PA800_OS_2_OR_NEWER_REQUIRED")
    try:
        test_date = date.fromisoformat(str(capture["testDate"]))
        if test_date > date.today():
            issues.append("FUTURE_TEST_DATE")
    except ValueError:
        issues.append("VALID_TEST_DATE_REQUIRED")
    if any(value is not True for value in capture["checks"].values()):
        issues.append("ALL_DEVICE_CHECKS_MUST_PASS")
    if any(value is not True for value in capture["markerResults"].values()):
        issues.append("ALL_MARKERS_MUST_PASS")
    for row in capture["styleBindings"]:
        if (row["status"] != "CONFIRMED" or any(row[key] is None for key in ("bankMsb", "bankLsb", "program"))
                or not all(str(row[key]).strip() for key in ("soundName", "trackType", "ntt"))
                or not row["evidenceRefs"]):
            issues.append(f"STYLE_BINDING_{row['channel']}_NOT_CONFIRMED")
    for row in capture["articulationCaptures"]:
        if row["status"] == "CONFIRMED" and (not row["soundBinding"] or not row["triggerMap"]
                                                or not row["evidenceRefs"]):
            issues.append(f"{row['engine']}_CONFIRMED_MAP_INCOMPLETE")
    voice = capture["voiceMeasurements"]
    if voice["measuredSafeMidiPeak"] is None or voice["unacceptableVoiceStealingObserved"] is not False:
        issues.append("VOICE_STEALING_MEASUREMENT_REQUIRED")
    if not voice["evidenceRefs"]:
        issues.append("VOICE_MEASUREMENT_EVIDENCE_REQUIRED")
    for row in voice["roleVoiceCosts"]:
        if (row["oscillatorVoicesPerNote"] is None or row["acceptableVoiceStealing"] is not True
                or not row["evidenceRefs"]):
            issues.append(f"VOICE_COST_{row['channel']}_INCOMPLETE")

    if capture_path is None:
        if capture["evidenceFiles"]:
            issues.append("CAPTURE_FILE_PATH_REQUIRED_FOR_EVIDENCE")
    else:
        base = capture_path.resolve().parent
        kinds: set[str] = set()
        for row in capture["evidenceFiles"]:
            try:
                path = _safe_relative(base, row["path"])
            except DeviceCertificationError:
                issues.append(f"EVIDENCE_PATH_INVALID:{row['id']}")
                continue
            if not path.is_file():
                issues.append(f"EVIDENCE_MISSING:{row['id']}")
                continue
            if path.stat().st_size != row["bytes"] or sha256_file(path) != row["sha256"]:
                issues.append(f"EVIDENCE_HASH_OR_SIZE_MISMATCH:{row['id']}")
                continue
            if not _media_magic(path, row["kind"]):
                issues.append(f"EVIDENCE_MEDIA_MAGIC_INVALID:{row['id']}")
                continue
            if row["capturedOnDevice"] is not True:
                issues.append(f"EVIDENCE_NOT_DEVICE_CAPTURED:{row['id']}")
                continue
            kinds.add(row["kind"])
            verified.append({"id": row["id"], "kind": row["kind"], "path": row["path"],
                             "sha256": row["sha256"], "bytes": row["bytes"]})
        if set(EVIDENCE_KINDS) - kinds:
            issues.append("HASHED_IMAGE_AND_AUDIO_REQUIRED")
    attestation = capture["attestation"]
    if attestation["physicalDeviceUsed"] is not True:
        issues.append("PHYSICAL_DEVICE_ATTESTATION_REQUIRED")
    if attestation["resultTruthful"] is not True:
        issues.append("TRUTHFULNESS_ATTESTATION_REQUIRED")
    if attestation["independentReviewCompleted"] is not True:
        issues.append("INDEPENDENT_REVIEW_REQUIRED")
    if not str(attestation["signature"]).strip():
        issues.append("OPERATOR_SIGNATURE_REQUIRED")
    if not attestation["operatorApprovedHash"]:
        issues.append("OPERATOR_APPROVED_HASH_REQUIRED")
    return issues, verified


def evaluate_device_capture(capture: Mapping[str, Any], root: str | Path,
                            capture_path: str | Path | None = None) -> dict[str, Any]:
    root = Path(root)
    validate_device_capture(capture)
    path = Path(capture_path) if capture_path is not None else None
    issues, verified = _physical_readiness_issues(capture, root, path)
    explicit_failure = any(value is False for value in capture["checks"].values()) or any(
        value is False for value in capture["markerResults"].values()
    ) or capture["voiceMeasurements"]["unacceptableVoiceStealingObserved"] is True
    certified = not issues
    status = "PA800_DEVICE_CERTIFIED" if certified else (
        "DEVICE_TEST_FAILED" if explicit_failure else "WAITING_FOR_DEVICE"
    )
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "date": date.today().isoformat(),
        "status": status,
        "certified": certified,
        "issues": sorted(set(issues)),
        "captureHash": capture["captureHash"],
        "operatorApprovedHash": capture["attestation"]["operatorApprovedHash"],
        "verifiedEvidence": verified,
        "exactStyleBindingCount": sum(row["status"] == "CONFIRMED" for row in capture["styleBindings"]),
        "confirmedArticulationEngines": [row["engine"] for row in capture["articulationCaptures"]
                                                  if row["status"] == "CONFIRMED"],
        "unsupportedArticulationEngines": [row["engine"] for row in capture["articulationCaptures"]
                                                    if row["status"] == "UNSUPPORTED"],
        "unknownArticulationEngines": [row["engine"] for row in capture["articulationCaptures"]
                                                if row["status"] == "UNKNOWN"],
        "machineObservedPhysicalDevice": False,
        "humanOperatorAttested": certified,
        "finalCertifiedMidiExportAllowed": False,
    }
    report["reportHash"] = _hash(report)
    return report


def build_device_profile(capture: Mapping[str, Any], report: Mapping[str, Any]) -> dict[str, Any]:
    certified = report["status"] == "PA800_DEVICE_CERTIFIED"
    bindings = [{key: row[key] for key in ("channel", "role", "status", "bankMsb", "bankLsb",
                                            "program", "soundName", "trackType", "ntt")}
                for row in capture["styleBindings"]]
    articulations = [{"engine": row["engine"], "status": row["status"],
                      "soundBinding": deepcopy(row["soundBinding"]),
                      "triggerCount": len(row["triggerMap"])}
                     for row in capture["articulationCaptures"]]
    voice = capture["voiceMeasurements"]
    profile: dict[str, Any] = {
        "schema": PROFILE_SCHEMA,
        "version": PROFILE_VERSION,
        "manufacturer": "Korg",
        "model": "Pa800",
        "osVersion": capture["device"]["osVersion"],
        "serialNumberHash": sha256(str(capture["device"]["serialNumber"]).encode("utf-8")).hexdigest()
                            if capture["device"]["serialNumber"] else None,
        "status": report["status"],
        "styleChannels": list(CORE_CHANNELS),
        "markerContract": list(MARKERS),
        "soundBindings": bindings,
        "articulationMaps": articulations,
        "polyphony": {
            "midiNoteCeiling": 54,
            "voiceCostMeasured": certified,
            "measuredSafeMidiPeak": voice["measuredSafeMidiPeak"] if certified else None,
            "roleVoiceCosts": deepcopy(voice["roleVoiceCosts"]) if certified else [],
        },
        "evidence": {
            "captureHash": capture["captureHash"],
            "operatorApprovedHash": capture["attestation"]["operatorApprovedHash"],
            "reportHash": report["reportHash"],
            "verifiedEvidenceHashes": [row["sha256"] for row in report["verifiedEvidence"]],
        },
        "activation": {
            "deviceSpecificMapsAllowed": certified,
            "voiceCostModelAllowed": certified,
            "pa800CertifiedLabelAllowed": certified,
            "finalCertifiedMidiExportAllowed": False,
        },
    }
    profile["profileHash"] = _hash(profile)
    return profile


def validate_device_profile(profile: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "manufacturer", "model", "osVersion", "serialNumberHash",
              "status", "styleChannels", "markerContract", "soundBindings", "articulationMaps",
              "polyphony", "evidence", "activation", "profileHash"}
    _strict(profile, fields, fields, "device profile")
    if profile["schema"] != PROFILE_SCHEMA or profile["version"] != PROFILE_VERSION:
        raise DeviceCertificationError("Unsupported device profile schema/version")
    if profile["styleChannels"] != list(CORE_CHANNELS) or profile["markerContract"] != list(MARKERS):
        raise DeviceCertificationError("Device profile channel/marker contract mismatch")
    value = deepcopy(dict(profile))
    expected = value.pop("profileHash")
    if expected != _hash(value):
        raise DeviceCertificationError("Device profile hash mismatch")
    certified = profile["status"] == "PA800_DEVICE_CERTIFIED"
    if any(profile["activation"][key] != certified for key in (
        "deviceSpecificMapsAllowed", "voiceCostModelAllowed", "pa800CertifiedLabelAllowed"
    )):
        raise DeviceCertificationError("Device activation does not match certification status")
    if profile["activation"]["finalCertifiedMidiExportAllowed"] is not False:
        raise DeviceCertificationError("Device profile alone cannot unlock final MIDI export")


def build_reference_device_lab(root: str | Path) -> dict[str, Any]:
    template = build_device_capture_template(root)
    report = evaluate_device_capture(template, root)
    profile = build_device_profile(template, report)
    validate_device_profile(profile)
    return {"captureTemplate": template, "report": report, "profile": profile}


def verify_device_capture_file(capture_path: str | Path, root: str | Path) -> dict[str, Any]:
    path = Path(capture_path)
    capture = json.loads(path.read_text(encoding="utf-8"))
    report = evaluate_device_capture(capture, root, path)
    profile = build_device_profile(capture, report)
    validate_device_profile(profile)
    return {"capture": capture, "report": report, "profile": profile}


def execute_device_certification_api(payload: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    allowed = {"action", "capture"}
    if set(payload) - allowed:
        raise DeviceCertificationError("Unknown Device Lab API field")
    action = payload.get("action", "reference")
    if action == "reference":
        if "capture" in payload:
            raise DeviceCertificationError("Reference action does not accept capture")
        return build_reference_device_lab(root)
    if action == "inspect":
        capture = payload.get("capture")
        if not isinstance(capture, Mapping):
            raise DeviceCertificationError("Inspect action requires capture object")
        report = evaluate_device_capture(capture, root)
        profile = build_device_profile(capture, report)
        validate_device_profile(profile)
        return {"capture": deepcopy(dict(capture)), "report": report, "profile": profile}
    if action == "seal":
        capture = payload.get("capture")
        if not isinstance(capture, Mapping):
            raise DeviceCertificationError("Seal action requires capture object")
        validate_device_capture(capture, require_seal=False)
        return {"capture": seal_device_capture(capture), "certificationGranted": False}
    raise DeviceCertificationError("Unknown Device Lab action")


def execute_device_certification_gui(payload: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    return {
        "workspace": "PA800 DEVICE LAB",
        "result": execute_device_certification_api(payload, root),
        "machineObservedPhysicalDevice": False,
        "finalCertifiedMidiExportAllowed": False,
    }