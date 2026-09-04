"""Session 25 exact-sound articulation capture, catalog and preview planner.

The software can validate Guitar/RX/DNC articulation behavior without claiming
that a synthetic fixture is a Korg Pa800 device fact.  A map entry may be
structurally CONFIRMED while its authority remains SOFTWARE_TEST_ONLY.  Final
production eligibility additionally requires a DEVICE_CAPTURED document whose
hash appears in an external, operator-controlled approval set.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from .midi import MidiFile
from .track_identity import build_track_identities, channel_track_indices, sound_bindings


ARTICULATION_CAPTURE_SCHEMA = "dna-premium-articulation-capture"
ARTICULATION_CAPTURE_VERSION = "1.0"
ARTICULATION_MAP_SCHEMA = "dna-premium-articulation-map"
ARTICULATION_MAP_VERSION = "2.0"
ARTICULATION_PLAN_SCHEMA = "dna-premium-articulation-plan"
ARTICULATION_PLAN_VERSION = "2.0"
ARTICULATION_CONTROL_VERSION = "1.0"

ARTICULATION_ENGINES = ("GUITAR", "RX", "DNC")
ARTICULATION_STATUSES = ("CONFIRMED", "UNKNOWN", "BLOCKED")
ARTICULATION_EVENT_TYPES = ("KEYSWITCH", "CC", "CHANNEL_PRESSURE")
ARTICULATION_PLACEMENTS = ("BEFORE_ONSET", "AT_ONSET", "AFTER_RELEASE")
ARTICULATION_CONDITIONS = ("EVERY_NOTE", "LONG_NOTE", "LEAP", "PHRASE_START", "PHRASE_END")
CAPTURE_AUTHORITIES = ("SOFTWARE_TEST_ONLY", "DEVICE_CAPTURED")

_STABLE_ID = re.compile(r"^\d{3}\.\d{3}\.\d{3}$")
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TRACK_UID = re.compile(r"^trk-[0-9a-f]{20}(?:-[1-9][0-9]*)?$")
_PROTECTED_CC = {0, 32, 98, 99, 100, 101}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return sha256(_canonical({key: item for key, item in value.items() if key != field})).hexdigest()


def _require_keys(value: Mapping[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unknown:
        raise ValueError(f"Unknown {label} fields: " + ", ".join(unknown))
    if missing:
        raise ValueError(f"Missing {label} fields: " + ", ".join(missing))


def _integer(value: Any, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in range {low}..{high}")
    return value


def _stable_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ValueError(f"{label} must use stable ID format 000.000.000")
    return value


def _sha(value: Any, label: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _range(value: Any, label: str) -> tuple[int, int]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    _require_keys(value, {"min", "max"}, {"min", "max"}, label)
    low = _integer(value["min"], f"{label}.min", 0, 127)
    high = _integer(value["max"], f"{label}.max", 0, 127)
    if low > high:
        raise ValueError(f"{label}.min cannot exceed max")
    return low, high


def _entry_hash(entry: Mapping[str, Any]) -> str:
    return _hash_without(entry, "entryHash")


def _validate_entry(entry: Mapping[str, Any], playable: tuple[int, int], trigger: tuple[int, int]) -> None:
    if not isinstance(entry, Mapping):
        raise ValueError("Articulation entry must be an object")
    fields = {
        "articulationId", "name", "status", "eventType", "number", "value",
        "placement", "offsetTicks", "durationTicks", "condition", "requiredNoteOff",
        "sourceEvidenceId", "entryHash",
    }
    _require_keys(entry, fields, fields, "articulation entry")
    _stable_id(entry["articulationId"], "articulationId")
    _stable_id(entry["sourceEvidenceId"], "sourceEvidenceId")
    if not isinstance(entry["name"], str) or not _NAME.fullmatch(entry["name"]):
        raise ValueError("Articulation name must be a lowercase controlled identifier")
    status = entry["status"]
    if status not in ARTICULATION_STATUSES:
        raise ValueError("Articulation status must be CONFIRMED, UNKNOWN or BLOCKED")
    event_type = entry["eventType"]
    if event_type is not None and event_type not in ARTICULATION_EVENT_TYPES:
        raise ValueError("Unsupported or proprietary articulation event type")
    placement = entry["placement"]
    condition = entry["condition"]
    if placement is not None and placement not in ARTICULATION_PLACEMENTS:
        raise ValueError("Unsupported articulation placement")
    if condition is not None and condition not in ARTICULATION_CONDITIONS:
        raise ValueError("Unsupported articulation condition")
    offset = _integer(entry["offsetTicks"], "offsetTicks", 0, 3840)
    duration = _integer(entry["durationTicks"], "durationTicks", 0, 3840)
    if not isinstance(entry["requiredNoteOff"], bool):
        raise ValueError("requiredNoteOff must be boolean")
    if status != "CONFIRMED":
        if any(entry[key] is not None for key in ("eventType", "number", "value", "placement", "condition")):
            raise ValueError("UNKNOWN/BLOCKED articulation cannot contain executable trigger data")
        if offset or duration or entry["requiredNoteOff"]:
            raise ValueError("UNKNOWN/BLOCKED articulation cannot contain executable timing")
    elif event_type is None or placement is None or condition is None:
        raise ValueError("CONFIRMED articulation requires event type, placement and condition")
    elif event_type == "KEYSWITCH":
        number = _integer(entry["number"], "keyswitch number", 0, 127)
        if entry["value"] is not None:
            raise ValueError("Key-switch velocity cannot be articulation-map authority")
        if duration <= 0 or not entry["requiredNoteOff"]:
            raise ValueError("Key-switch requires positive duration and note-off")
        if not trigger[0] <= number <= trigger[1]:
            raise ValueError("Key-switch lies outside confirmed trigger range")
        if playable[0] <= number <= playable[1]:
            raise ValueError("Key-switch collides with playable range")
    elif event_type == "CC":
        number = _integer(entry["number"], "CC number", 0, 127)
        _integer(entry["value"], "CC value", 0, 127)
        if number in _PROTECTED_CC:
            raise ValueError("Articulation map cannot control Bank/RPN/NRPN CC")
        if duration or entry["requiredNoteOff"]:
            raise ValueError("CC articulation cannot have note duration")
    elif event_type == "CHANNEL_PRESSURE":
        if entry["number"] is not None:
            raise ValueError("Channel pressure cannot contain a controller number")
        _integer(entry["value"], "channel pressure value", 0, 127)
        if duration or entry["requiredNoteOff"]:
            raise ValueError("Channel pressure cannot have note duration")
    if entry["entryHash"] != _entry_hash(entry):
        raise ValueError("Articulation entry hash mismatch")


def _validate_map(item: Mapping[str, Any]) -> None:
    if not isinstance(item, Mapping):
        raise ValueError("Articulation map must be an object")
    fields = {
        "mapId", "engine", "status", "exactSound", "roles", "playableRange",
        "triggerRange", "entries", "sourceEvidenceIds", "mapHash",
    }
    _require_keys(item, fields, fields, "articulation map")
    _stable_id(item["mapId"], "mapId")
    if item["engine"] not in ARTICULATION_ENGINES:
        raise ValueError("Articulation engine must be GUITAR, RX or DNC")
    if item["status"] not in ARTICULATION_STATUSES:
        raise ValueError("Articulation map status is invalid")
    exact = item["exactSound"]
    if not isinstance(exact, Mapping):
        raise ValueError("exactSound must be an object")
    _require_keys(exact, {"bankMsb", "bankLsb", "program"},
                  {"bankMsb", "bankLsb", "program"}, "exactSound")
    for key in ("bankMsb", "bankLsb", "program"):
        _integer(exact[key], f"exactSound.{key}", 0, 127)
    roles = item["roles"]
    if not isinstance(roles, list) or not roles or any(not isinstance(role, str) or not _NAME.fullmatch(role) for role in roles):
        raise ValueError("Articulation map requires explicit controlled roles")
    if len(roles) != len(set(roles)):
        raise ValueError("Articulation roles must be unique")
    playable = _range(item["playableRange"], "playableRange")
    trigger = _range(item["triggerRange"], "triggerRange")
    entries = item["entries"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("Articulation map requires entries")
    for entry in entries:
        _validate_entry(entry, playable, trigger)
    names = [entry["name"] for entry in entries]
    ids = [entry["articulationId"] for entry in entries]
    if len(names) != len(set(names)) or len(ids) != len(set(ids)):
        raise ValueError("Articulation names and IDs must be unique inside a map")
    identities = [
        (entry["eventType"], entry["number"], entry["value"], entry["placement"], entry["condition"])
        for entry in entries if entry["status"] == "CONFIRMED"
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("Duplicate articulation trigger identity")
    evidence = item["sourceEvidenceIds"]
    if not isinstance(evidence, list) or not evidence or any(not _STABLE_ID.fullmatch(str(value)) for value in evidence):
        raise ValueError("Articulation map requires stable source evidence IDs")
    if not {entry["sourceEvidenceId"] for entry in entries} <= set(evidence):
        raise ValueError("Articulation entry refers to evidence outside its map")
    if item["mapHash"] != _hash_without(item, "mapHash"):
        raise ValueError("Articulation map hash mismatch")


def validate_articulation_capture(capture: Mapping[str, Any]) -> None:
    """Strictly validate capture structure, hashes and executable safety."""

    if not isinstance(capture, Mapping):
        raise ValueError("Articulation capture must be an object")
    fields = {
        "schema", "version", "captureId", "authority", "device", "capturedAt",
        "operatorId", "evidence", "maps", "captureHash",
    }
    _require_keys(capture, fields, fields, "articulation capture")
    if (capture["schema"], capture["version"]) != (ARTICULATION_CAPTURE_SCHEMA, ARTICULATION_CAPTURE_VERSION):
        raise ValueError("Unsupported articulation capture schema/version")
    _stable_id(capture["captureId"], "captureId")
    authority = capture["authority"]
    if authority not in CAPTURE_AUTHORITIES:
        raise ValueError("Unknown articulation capture authority")
    device = capture["device"]
    if not isinstance(device, Mapping):
        raise ValueError("device must be an object")
    device_fields = {"manufacturer", "model", "osVersion", "serialHash", "hardwareVerified"}
    _require_keys(device, device_fields, device_fields, "device")
    if device["manufacturer"] != "Korg" or device["model"] != "Pa800":
        raise ValueError("Session 25 capture is restricted to exact Korg Pa800 identity")
    if not isinstance(device["osVersion"], str) or not device["osVersion"]:
        raise ValueError("Pa800 OS version is required")
    _sha(device["serialHash"], "device.serialHash")
    if not isinstance(device["hardwareVerified"], bool):
        raise ValueError("device.hardwareVerified must be boolean")
    if authority == "SOFTWARE_TEST_ONLY" and device["hardwareVerified"]:
        raise ValueError("SOFTWARE_TEST_ONLY capture cannot claim verified hardware")
    if authority == "DEVICE_CAPTURED" and not device["hardwareVerified"]:
        raise ValueError("DEVICE_CAPTURED authority requires verified hardware")
    if not isinstance(capture["capturedAt"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", capture["capturedAt"]):
        raise ValueError("capturedAt must use YYYY-MM-DD")
    try:
        captured_date = date.fromisoformat(capture["capturedAt"])
    except ValueError as exc:
        raise ValueError("capturedAt must be a real calendar date") from exc
    if captured_date > date.today():
        raise ValueError("Articulation capture date cannot be in the future")
    if not isinstance(capture["operatorId"], str) or not _NAME.fullmatch(capture["operatorId"]):
        raise ValueError("operatorId must be a controlled local identifier")
    evidence = capture["evidence"]
    if not isinstance(evidence, Mapping):
        raise ValueError("evidence must be an object")
    evidence_fields = {"sourceMidiSha256", "audioSha256", "imageSha256", "notes"}
    _require_keys(evidence, evidence_fields, evidence_fields, "capture evidence")
    _sha(evidence["sourceMidiSha256"], "evidence.sourceMidiSha256")
    _sha(evidence["audioSha256"], "evidence.audioSha256", optional=True)
    _sha(evidence["imageSha256"], "evidence.imageSha256", optional=True)
    if not isinstance(evidence["notes"], str) or not evidence["notes"].strip():
        raise ValueError("Capture evidence notes are required")
    if authority == "DEVICE_CAPTURED" and (evidence["audioSha256"] is None or evidence["imageSha256"] is None):
        raise ValueError("DEVICE_CAPTURED authority requires audio and image evidence hashes")
    maps = capture["maps"]
    if not isinstance(maps, list) or not maps:
        raise ValueError("Articulation capture requires one or more maps")
    for item in maps:
        _validate_map(item)
    ids = [item["mapId"] for item in maps]
    keys = [(item["engine"], item["exactSound"]["bankMsb"], item["exactSound"]["bankLsb"], item["exactSound"]["program"]) for item in maps]
    if len(ids) != len(set(ids)) or len(keys) != len(set(keys)):
        raise ValueError("Capture contains duplicate map ID or exact engine/sound binding")
    if capture["captureHash"] != _hash_without(capture, "captureHash"):
        raise ValueError("Articulation capture hash mismatch")


def import_articulation_capture(
    capture: Mapping[str, Any], approved_capture_hashes: Iterable[str] = ()
) -> dict[str, Any]:
    """Build a normalized catalog without silently promoting test evidence."""

    validate_articulation_capture(capture)
    approved = set(approved_capture_hashes)
    for value in approved:
        _sha(value, "approved capture hash")
    device_captured = capture["authority"] == "DEVICE_CAPTURED"
    trusted = device_captured and capture["captureHash"] in approved
    maps = []
    for source in capture["maps"]:
        item = deepcopy(source)
        confirmed_entries = sum(entry["status"] == "CONFIRMED" for entry in item["entries"])
        item.update({
            "captureHash": capture["captureHash"],
            "authority": capture["authority"],
            "deviceVerified": bool(capture["device"]["hardwareVerified"]),
            "productionEligible": bool(trusted and item["status"] == "CONFIRMED" and confirmed_entries),
            "productionBlock": None if trusted else (
                "CAPTURE_HASH_NOT_OPERATOR_APPROVED" if device_captured
                else "SOFTWARE_TEST_FIXTURE_CANNOT_AUTHORIZE_PA800_PRODUCTION"
            ),
        })
        item["catalogEntryHash"] = _hash_without(item, "catalogEntryHash")
        maps.append(item)
    catalog = {
        "schema": ARTICULATION_MAP_SCHEMA,
        "version": ARTICULATION_MAP_VERSION,
        "capture": {
            "captureId": capture["captureId"], "captureHash": capture["captureHash"],
            "authority": capture["authority"], "capturedAt": capture["capturedAt"],
            "device": deepcopy(capture["device"]),
        },
        "maps": maps,
        "audit": {
            "mapCount": len(maps),
            "entryCount": sum(len(item["entries"]) for item in maps),
            "confirmedEntryCount": sum(entry["status"] == "CONFIRMED" for item in maps for entry in item["entries"]),
            "unknownEntryCount": sum(entry["status"] == "UNKNOWN" for item in maps for entry in item["entries"]),
            "blockedEntryCount": sum(entry["status"] == "BLOCKED" for item in maps for entry in item["entries"]),
            "productionEligibleMapCount": sum(item["productionEligible"] for item in maps),
        },
        "invariants": {
            "exactSoundBindingOnly": True, "approximateNameMatching": False,
            "nearestProgramFallback": False, "proprietarySysExAllowed": False,
            "keyswitchNoteOffRequired": True, "softwareFixtureCanAuthorizeProduction": False,
        },
        "productionStatus": "PRODUCTION_ELIGIBLE" if maps and all(item["productionEligible"] for item in maps)
                            else "DEVICE_BLOCKED",
        "catalogHash": "",
    }
    catalog["catalogHash"] = _hash_without(catalog, "catalogHash")
    validate_articulation_catalog(catalog)
    return catalog


def validate_articulation_catalog(catalog: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "capture", "maps", "audit", "invariants", "productionStatus", "catalogHash"}
    if not isinstance(catalog, Mapping):
        raise ValueError("Articulation catalog must be an object")
    _require_keys(catalog, fields, fields, "articulation catalog")
    if (catalog["schema"], catalog["version"]) != (ARTICULATION_MAP_SCHEMA, ARTICULATION_MAP_VERSION):
        raise ValueError("Unsupported ArticulationMap catalog schema/version")
    if not isinstance(catalog["maps"], list) or not catalog["maps"]:
        raise ValueError("Articulation catalog requires maps")
    for item in catalog["maps"]:
        base = {key: item[key] for key in (
            "mapId", "engine", "status", "exactSound", "roles", "playableRange",
            "triggerRange", "entries", "sourceEvidenceIds", "mapHash",
        )}
        _validate_map(base)
        extra = {"captureHash", "authority", "deviceVerified", "productionEligible", "productionBlock", "catalogEntryHash"}
        if set(item) != set(base) | extra:
            raise ValueError("Catalog map contains unknown normalized fields")
        if item["authority"] not in CAPTURE_AUTHORITIES or not isinstance(item["deviceVerified"], bool):
            raise ValueError("Catalog map has invalid capture authority")
        if not isinstance(item["productionEligible"], bool):
            raise ValueError("productionEligible must be boolean")
        if item["productionEligible"] and (item["authority"] != "DEVICE_CAPTURED" or not item["deviceVerified"]):
            raise ValueError("Only verified device capture can be production eligible")
        if item["catalogEntryHash"] != _hash_without(item, "catalogEntryHash"):
            raise ValueError("Catalog entry hash mismatch")
    if catalog["productionStatus"] not in {"PRODUCTION_ELIGIBLE", "DEVICE_BLOCKED"}:
        raise ValueError("Invalid articulation catalog production status")
    if catalog["catalogHash"] != _hash_without(catalog, "catalogHash"):
        raise ValueError("Articulation catalog hash mismatch")


def _entry(
    articulation_id: str, name: str, status: str, event_type: str | None,
    number: int | None, value: int | None, placement: str | None,
    offset: int, duration: int, condition: str | None, note_off: bool,
    evidence_id: str,
) -> dict[str, Any]:
    item = {
        "articulationId": articulation_id, "name": name, "status": status,
        "eventType": event_type, "number": number, "value": value,
        "placement": placement, "offsetTicks": offset, "durationTicks": duration,
        "condition": condition, "requiredNoteOff": note_off,
        "sourceEvidenceId": evidence_id, "entryHash": "",
    }
    item["entryHash"] = _entry_hash(item)
    return item


def _map(
    map_id: str, engine: str, sound: tuple[int, int, int], roles: Sequence[str],
    playable: tuple[int, int], trigger: tuple[int, int], entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    evidence = sorted({entry["sourceEvidenceId"] for entry in entries})
    item = {
        "mapId": map_id, "engine": engine, "status": "CONFIRMED",
        "exactSound": {"bankMsb": sound[0], "bankLsb": sound[1], "program": sound[2]},
        "roles": list(roles), "playableRange": {"min": playable[0], "max": playable[1]},
        "triggerRange": {"min": trigger[0], "max": trigger[1]},
        "entries": [deepcopy(entry) for entry in entries], "sourceEvidenceIds": evidence,
        "mapHash": "",
    }
    item["mapHash"] = _hash_without(item, "mapHash")
    return item


def build_reference_articulation_capture(source_midi_sha256: str) -> dict[str, Any]:
    """Create the immutable self-authored fixture used only by the software gate."""

    _sha(source_midi_sha256, "source_midi_sha256")
    guitar_entries = [
        _entry("250.100.001", "mute", "CONFIRMED", "KEYSWITCH", 2, None,
               "AT_ONSET", 0, 60, "PHRASE_END", True, "250.900.001"),
        _entry("250.100.002", "stop", "CONFIRMED", "KEYSWITCH", 3, None,
               "AFTER_RELEASE", 0, 60, "LONG_NOTE", True, "250.900.002"),
        _entry("250.100.003", "body_tap", "UNKNOWN", None, None, None,
               None, 0, 0, None, False, "250.900.003"),
    ]
    rx_entries = [
        _entry("250.200.001", "fret_noise", "CONFIRMED", "KEYSWITCH", 12, None,
               "AFTER_RELEASE", 8, 48, "LONG_NOTE", True, "250.900.004"),
        _entry("250.200.002", "release_noise", "CONFIRMED", "KEYSWITCH", 13, None,
               "AFTER_RELEASE", 0, 48, "PHRASE_END", True, "250.900.005"),
        _entry("250.200.003", "proprietary_noise", "BLOCKED", None, None, None,
               None, 0, 0, None, False, "250.900.006"),
    ]
    dnc_entries = [
        _entry("250.300.001", "legato_switch", "CONFIRMED", "KEYSWITCH", 20, None,
               "BEFORE_ONSET", 24, 48, "LEAP", True, "250.900.007"),
        _entry("250.300.002", "breath", "CONFIRMED", "CC", 1, 96,
               "AT_ONSET", 0, 0, "LONG_NOTE", False, "250.900.008"),
        _entry("250.300.003", "fall_pressure", "CONFIRMED", "CHANNEL_PRESSURE", None, 80,
               "AFTER_RELEASE", 0, 0, "PHRASE_END", False, "250.900.009"),
    ]
    capture = {
        "schema": ARTICULATION_CAPTURE_SCHEMA, "version": ARTICULATION_CAPTURE_VERSION,
        "captureId": "250.000.001", "authority": "SOFTWARE_TEST_ONLY",
        "device": {
            "manufacturer": "Korg", "model": "Pa800", "osVersion": "SIMULATED-2.0",
            "serialHash": sha256(b"session25-no-physical-device").hexdigest(),
            "hardwareVerified": False,
        },
        "capturedAt": "2026-09-03", "operatorId": "session25_fixture",
        "evidence": {
            "sourceMidiSha256": source_midi_sha256, "audioSha256": None,
            "imageSha256": None,
            "notes": "Self-authored deterministic software fixture; never Pa800 production evidence.",
        },
        "maps": [
            _map("250.010.001", "GUITAR", (121, 0, 24), ("guitar",), (40, 88), (0, 11), guitar_entries),
            _map("250.020.001", "RX", (121, 1, 40), ("guitar", "solo"), (40, 96), (12, 19), rx_entries),
            _map("250.030.001", "DNC", (121, 2, 80), ("solo",), (48, 96), (20, 35), dnc_entries),
        ],
        "captureHash": "",
    }
    capture["captureHash"] = _hash_without(capture, "captureHash")
    validate_articulation_capture(capture)
    return capture


@dataclass(frozen=True)
class ArticulationControls:
    version: str
    map_id: str
    track_uid: str
    track_number: int
    channel_number: int
    start_tick: int
    end_tick: int
    role: str
    requested_articulations: tuple[str, ...]
    seed: int
    allow_shared_channel: bool
    production_mode: bool
    max_generated_events: int

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ArticulationControls":
        if not isinstance(raw, Mapping):
            raise ValueError("Articulation controls must be an object")
        fields = {
            "version", "mapId", "trackUid", "trackNumber", "channelNumber", "startTick",
            "endTick", "role", "requestedArticulations", "seed", "allowSharedChannel",
            "productionMode", "maxGeneratedEvents",
        }
        required = fields - {"allowSharedChannel", "productionMode", "maxGeneratedEvents"}
        _require_keys(raw, fields, required, "articulation controls")
        if raw["version"] != ARTICULATION_CONTROL_VERSION:
            raise ValueError("Articulation controls require version 1.0")
        map_id = _stable_id(raw["mapId"], "mapId")
        uid = raw["trackUid"]
        if not isinstance(uid, str) or not _TRACK_UID.fullmatch(uid):
            raise ValueError("trackUid must be a stable physical-track identity")
        track_number = _integer(raw["trackNumber"], "trackNumber", 1, 16)
        channel_number = _integer(raw["channelNumber"], "channelNumber", 1, 16)
        start = _integer(raw["startTick"], "startTick", 0, 0x0FFFFFFF)
        end = _integer(raw["endTick"], "endTick", 1, 0x0FFFFFFF)
        if end <= start:
            raise ValueError("endTick must be greater than startTick")
        role = raw["role"]
        if not isinstance(role, str) or not _NAME.fullmatch(role):
            raise ValueError("role must be a controlled identifier")
        requested = raw["requestedArticulations"]
        if not isinstance(requested, list) or not requested or any(not isinstance(value, str) or not _NAME.fullmatch(value) for value in requested):
            raise ValueError("requestedArticulations must be a non-empty controlled list")
        if len(requested) != len(set(requested)):
            raise ValueError("requestedArticulations must be unique")
        seed = _integer(raw["seed"], "seed", 0, 2**31 - 1)
        shared = raw.get("allowSharedChannel", False)
        production = raw.get("productionMode", False)
        if not isinstance(shared, bool) or not isinstance(production, bool):
            raise ValueError("allowSharedChannel and productionMode must be boolean")
        maximum = _integer(raw.get("maxGeneratedEvents", 64), "maxGeneratedEvents", 0, 512)
        return cls(ARTICULATION_CONTROL_VERSION, map_id, uid, track_number, channel_number,
                   start, end, role, tuple(requested), seed, shared, production, maximum)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "version": self.version, "mapId": self.map_id, "trackUid": self.track_uid,
            "trackNumber": self.track_number, "channelNumber": self.channel_number,
            "startTick": self.start_tick, "endTick": self.end_tick, "role": self.role,
            "requestedArticulations": list(self.requested_articulations), "seed": self.seed,
            "allowSharedChannel": self.allow_shared_channel,
            "productionMode": self.production_mode,
            "maxGeneratedEvents": self.max_generated_events,
        }


def _source_note_uid(track_uid: str, channel: int, note: Any) -> str:
    payload = f"{track_uid}:{channel}:{note.start}:{note.end}:{note.pitch}:{note.velocity}"
    return "srcnote-" + sha256(payload.encode("ascii")).hexdigest()[:24]


def _condition_notes(notes: Sequence[Any], condition: str, ppq: int) -> list[Any]:
    if not notes:
        return []
    if condition == "EVERY_NOTE":
        return list(notes)
    if condition == "PHRASE_START":
        return [notes[0]]
    if condition == "PHRASE_END":
        return [notes[-1]]
    if condition == "LONG_NOTE":
        return [note for note in notes if note.end - note.start >= ppq]
    if condition == "LEAP":
        return [note for index, note in enumerate(notes) if index and abs(note.pitch - notes[index - 1].pitch) >= 5]
    return []


def _event_tick(entry: Mapping[str, Any], note: Any) -> int:
    placement = entry["placement"]
    offset = int(entry["offsetTicks"])
    if placement == "BEFORE_ONSET":
        return max(0, note.start - offset)
    if placement == "AFTER_RELEASE":
        return note.end + offset
    return note.start + offset


def _existing_event_identities(midi: MidiFile, track_index: int, channel: int) -> set[tuple[Any, ...]]:
    identities: set[tuple[Any, ...]] = set()
    for event in midi.tracks[track_index].events:
        if event.kind != "channel" or event.channel != channel:
            continue
        if event.command == 0xB0 and len(event.data) == 2:
            identities.add(("CC", event.tick, event.data[0], event.data[1]))
        elif event.command == 0xD0 and event.data:
            identities.add(("CHANNEL_PRESSURE", event.tick, None, event.data[0]))
        elif event.is_note_on and len(event.data) == 2:
            identities.add(("KEYSWITCH", event.tick, event.data[0], None))
    return identities


def build_articulation_plan(
    midi: MidiFile,
    catalog: Mapping[str, Any],
    groove_plan: Mapping[str, Any],
    expression_plan: Mapping[str, Any] | None,
    raw_controls: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a deterministic read-only trigger plan with exact SoundBinding."""

    validate_articulation_catalog(catalog)
    controls = ArticulationControls.from_mapping(raw_controls)
    matches = [item for item in catalog["maps"] if item["mapId"] == controls.map_id]
    if len(matches) != 1:
        raise ValueError("Requested ArticulationMap ID does not exist exactly once")
    amap = matches[0]
    if controls.role not in amap["roles"]:
        raise ValueError("Requested role is not confirmed by the ArticulationMap")
    track_index = controls.track_number - 1
    channel_index = controls.channel_number - 1
    if not 0 <= track_index < len(midi.tracks):
        raise ValueError("Requested physical track does not exist")
    identities = build_track_identities(midi)
    identity = identities[track_index]
    if identity.track_uid != controls.track_uid:
        raise ValueError("trackUid does not match the physical MIDI track")
    owners = channel_track_indices(midi, channel_index)
    if set(owners) - {track_index} and not controls.allow_shared_channel:
        raise ValueError("Target MIDI channel is shared by multiple physical tracks")
    bindings = sound_bindings(
        midi, track_index=track_index, channel=channel_index,
        start_tick=controls.start_tick, end_tick=controls.end_tick,
        track_uid=controls.track_uid,
    )
    if len(bindings) != 1 or not bindings[0].complete:
        raise ValueError("Articulation planning requires one complete time-scoped SoundBinding")
    exact = amap["exactSound"]
    expected_sound = (exact["bankMsb"], exact["bankLsb"], exact["program"])
    if bindings[0].sound != expected_sound:
        raise ValueError("Exact SoundBinding does not match the requested ArticulationMap")
    entries = {entry["name"]: entry for entry in amap["entries"]}
    unknown = sorted(set(controls.requested_articulations) - set(entries))
    if unknown:
        raise ValueError("Unknown requested articulation: " + ", ".join(unknown))
    all_channel_notes = [note for note in midi.notes() if note.track == track_index and note.channel == channel_index
                         and note.start < controls.end_tick and note.end > controls.start_tick]
    playable = (int(amap["playableRange"]["min"]), int(amap["playableRange"]["max"]))
    notes = [note for note in all_channel_notes if playable[0] <= note.pitch <= playable[1]]
    notes.sort(key=lambda note: (note.start, note.pitch, note.end, note.velocity))
    source_notes = [
        {"sourceNoteUid": _source_note_uid(identity.track_uid, channel_index, note),
         "onsetTick": note.start, "durationTick": note.end - note.start,
         "pitch": note.pitch, "velocity": note.velocity, "immutable": True}
        for note in notes
    ]
    source_by_key = {(item["onsetTick"], item["pitch"], item["durationTick"]): item["sourceNoteUid"] for item in source_notes}
    baseline_peak = int(groove_plan.get("audit", {}).get("maximumPeakAfterSimplification", 0))
    if expression_plan is not None:
        baseline_peak = max(baseline_peak, int(expression_plan.get("audit", {}).get("maximumEstimatedPeak", 0)))
    ceiling = 54
    existing = _existing_event_identities(midi, track_index, channel_index)
    planned: list[dict[str, Any]] = []
    skipped = Counter()
    active_key_intervals: list[tuple[int, int]] = []
    requested_entries = [entries[name] for name in controls.requested_articulations]
    for entry in requested_entries:
        if entry["status"] != "CONFIRMED":
            skipped[f"STATUS_{entry['status']}"] += 1
            continue
        for note in _condition_notes(notes, entry["condition"], midi.ppq):
            tick = _event_tick(entry, note)
            if tick < controls.start_tick or tick >= controls.end_tick:
                skipped["OUTSIDE_WINDOW"] += 1
                continue
            identity_key = (entry["eventType"], tick, entry["number"], entry["value"])
            if identity_key in existing or any(
                (event["eventType"], event["tick"], event["number"], event["value"]) == identity_key
                for event in planned
            ):
                skipped["DUPLICATE_EXISTING_EVENT"] += 1
                continue
            extra_peak = 0
            note_off_tick = None
            if entry["eventType"] == "KEYSWITCH":
                note_off_tick = tick + int(entry["durationTicks"])
                if any(
                    note.pitch == entry["number"] and note.start < note_off_tick and note.end > tick
                    for note in all_channel_notes
                ):
                    skipped["TRIGGER_NOTE_COLLISION"] += 1
                    continue
                simultaneous = sum(start <= tick < end for start, end in active_key_intervals)
                extra_peak = simultaneous + 1
                if baseline_peak + extra_peak > ceiling:
                    skipped["POLYPHONY_CEILING"] += 1
                    continue
                active_key_intervals.append((tick, note_off_tick))
            if len(planned) >= controls.max_generated_events:
                skipped["EVENT_BUDGET"] += 1
                continue
            source_uid = source_by_key[(note.start, note.pitch, note.end - note.start)]
            event = {
                "eventId": "art-" + sha256(
                    f"{amap['mapId']}:{entry['articulationId']}:{source_uid}:{tick}:{controls.seed}".encode("ascii")
                ).hexdigest()[:24],
                "mapId": amap["mapId"], "articulationId": entry["articulationId"],
                "articulation": entry["name"], "engine": amap["engine"],
                "status": entry["status"], "eventType": entry["eventType"],
                "trackUid": controls.track_uid, "trackNumber": controls.track_number,
                "channelNumber": controls.channel_number, "tick": tick,
                "noteOffTick": note_off_tick, "number": entry["number"], "value": entry["value"],
                "sourceNoteUid": source_uid, "sourceEvidenceId": entry["sourceEvidenceId"],
                "reasonCode": f"EXACT_{amap['engine']}_{entry['condition']}",
                "productionEligible": bool(amap["productionEligible"]),
                "eventHash": "",
            }
            event["eventHash"] = _hash_without(event, "eventHash")
            planned.append(event)
    generated_key_peak = 0
    boundaries = sorted({tick for interval in active_key_intervals for tick in interval})
    for tick in boundaries:
        generated_key_peak = max(generated_key_peak, sum(start <= tick < end for start, end in active_key_intervals))
    production_blocks = []
    if not amap["productionEligible"]:
        production_blocks.append({
            "code": amap["productionBlock"],
            "reason": "Articulation trigger map is not backed by operator-approved physical Pa800 capture.",
        })
    if controls.production_mode and production_blocks:
        production_blocks.append({
            "code": "PRODUCTION_MODE_DENIED",
            "reason": "Requested production mode cannot use software-only or unapproved capture evidence.",
        })
    if any(entries[name]["status"] != "CONFIRMED" for name in controls.requested_articulations):
        production_blocks.append({
            "code": "UNCONFIRMED_REQUESTED_ARTICULATION",
            "reason": "UNKNOWN/BLOCKED articulations remain KEEP/MANUAL_REVIEW and emit no trigger.",
        })
    plan = {
        "schema": ARTICULATION_PLAN_SCHEMA, "version": ARTICULATION_PLAN_VERSION,
        "source": {
            "inputMidiSha256": midi.digest(),
            "groovePlanHash": groove_plan.get("groovePlanHash"),
            "expressionPlanHash": expression_plan.get("expressionPlanHash") if expression_plan else None,
            "catalogHash": catalog["catalogHash"],
        },
        "controls": controls.to_manifest(),
        "map": {
            "mapId": amap["mapId"], "engine": amap["engine"], "status": amap["status"],
            "authority": amap["authority"], "captureHash": amap["captureHash"],
            "exactSound": deepcopy(amap["exactSound"]),
            "productionEligible": amap["productionEligible"],
        },
        "soundBinding": bindings[0].to_manifest(status="EXACT_MAP_MATCH"),
        "sourceNotes": source_notes,
        "events": planned,
        "audit": {
            "sourceNoteCount": len(source_notes), "requestedArticulationCount": len(requested_entries),
            "generatedEventCount": len(planned), "keyswitchEventCount": sum(event["eventType"] == "KEYSWITCH" for event in planned),
            "ccEventCount": sum(event["eventType"] == "CC" for event in planned),
            "channelPressureEventCount": sum(event["eventType"] == "CHANNEL_PRESSURE" for event in planned),
            "keyswitchNoteOffCount": sum(event["noteOffTick"] is not None for event in planned),
            "baselinePeak": baseline_peak, "articulationPeak": generated_key_peak,
            "estimatedPeak": baseline_peak + generated_key_peak,
            "softwareMidiNoteCeiling": ceiling,
            "withinPolyphonyCeiling": baseline_peak + generated_key_peak <= ceiling,
            "skipped": dict(sorted(skipped.items())),
            "duplicateEventCount": int(skipped["DUPLICATE_EXISTING_EVENT"]),
        },
        "productionBlocks": production_blocks,
        "readyForPreview": bool(planned),
        "readyForProductionRender": bool(planned and amap["productionEligible"] and not production_blocks),
        "finalMidiGenerated": False,
        "midiMutationAllowed": False,
        "articulationPlanHash": "",
    }
    plan["articulationPlanHash"] = _hash_without(plan, "articulationPlanHash")
    validate_articulation_plan_v2(plan)
    return plan


def validate_articulation_plan_v2(plan: Mapping[str, Any]) -> None:
    fields = {
        "schema", "version", "source", "controls", "map", "soundBinding", "sourceNotes",
        "events", "audit", "productionBlocks", "readyForPreview", "readyForProductionRender",
        "finalMidiGenerated", "midiMutationAllowed", "articulationPlanHash",
    }
    if not isinstance(plan, Mapping):
        raise ValueError("ArticulationPlan must be an object")
    _require_keys(plan, fields, fields, "ArticulationPlan")
    if (plan["schema"], plan["version"]) != (ARTICULATION_PLAN_SCHEMA, ARTICULATION_PLAN_VERSION):
        raise ValueError("Unsupported ArticulationPlan schema/version")
    if plan["finalMidiGenerated"] or plan["midiMutationAllowed"]:
        raise ValueError("Session 25 plan is read-only and cannot generate final MIDI")
    if plan["readyForProductionRender"] and plan["productionBlocks"]:
        raise ValueError("Production-ready plan cannot contain production blocks")
    source_uids = {item["sourceNoteUid"] for item in plan["sourceNotes"]}
    event_ids = []
    for event in plan["events"]:
        if event["sourceNoteUid"] not in source_uids:
            raise ValueError("Articulation event has unknown sourceNoteUid")
        if event["eventHash"] != _hash_without(event, "eventHash"):
            raise ValueError("Articulation event hash mismatch")
        if event["eventType"] == "KEYSWITCH" and (event["noteOffTick"] is None or event["noteOffTick"] <= event["tick"]):
            raise ValueError("Key-switch event requires paired positive note-off")
        event_ids.append(event["eventId"])
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("Articulation event IDs must be unique")
    if int(plan["audit"]["estimatedPeak"]) > int(plan["audit"]["softwareMidiNoteCeiling"]):
        raise ValueError("Articulation plan exceeds MIDI-note ceiling")
    if plan["articulationPlanHash"] != _hash_without(plan, "articulationPlanHash"):
        raise ValueError("ArticulationPlan hash mismatch")


def articulation_production_readiness(
    catalog: Mapping[str, Any], engine: str, exact_sound: Sequence[int]
) -> dict[str, Any]:
    """Return a fail-safe adapter decision for Session 17/25 integration."""

    validate_articulation_catalog(catalog)
    if engine not in ARTICULATION_ENGINES:
        raise ValueError("Unknown articulation engine")
    if len(exact_sound) != 3 or any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 127 for value in exact_sound):
        raise ValueError("exact_sound must contain Bank MSB, Bank LSB and Program")
    matches = [item for item in catalog["maps"] if item["engine"] == engine and
               [item["exactSound"]["bankMsb"], item["exactSound"]["bankLsb"], item["exactSound"]["program"]] == list(exact_sound)]
    if not matches:
        return {"allowed": False, "status": "MANUAL_REVIEW", "reason": "No exact articulation map; nearest-sound fallback is forbidden"}
    item = matches[0]
    return {
        "allowed": bool(item["productionEligible"]),
        "status": "PRODUCTION_ARTICULATION_MAP_CONFIRMED" if item["productionEligible"]
                  else "DEVICE_BLOCKED_UNTRUSTED_ARTICULATION_CAPTURE",
        "reason": "Exact operator-approved device capture" if item["productionEligible"]
                  else "Exact map exists only as software or unapproved device evidence",
        "mapId": item["mapId"], "captureHash": item["captureHash"],
    }


def execute_articulation_map_api(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Articulation API payload must be an object")
    action = payload.get("action", "reference")
    if action == "reference":
        unknown = set(payload) - {"action", "sourceMidiSha256"}
        if unknown:
            raise ValueError("Unknown reference action fields: " + ", ".join(sorted(unknown)))
        source_hash = payload.get("sourceMidiSha256", sha256(b"session25-api-reference").hexdigest())
        capture = build_reference_articulation_capture(source_hash)
        return import_articulation_capture(capture)
    if action == "import":
        unknown = set(payload) - {"action", "capture"}
        if unknown:
            raise ValueError("Unknown import action fields: " + ", ".join(sorted(unknown)))
        # HTTP clients may validate/import capture structure, but may not
        # self-authorize production. Approval is deliberately a separate local
        # operator-controlled input used by the CLI or trusted host workflow.
        return import_articulation_capture(payload["capture"])
    if action == "plan":
        fields = {"action", "midiHex", "catalog", "groovePlan", "expressionPlan", "controls"}
        unknown = set(payload) - fields
        if unknown:
            raise ValueError("Unknown plan action fields: " + ", ".join(sorted(unknown)))
        raw = payload.get("midiHex")
        if not isinstance(raw, str) or len(raw) % 2:
            raise ValueError("midiHex must contain complete hexadecimal bytes")
        try:
            midi = MidiFile.from_bytes(bytes.fromhex(raw))
        except ValueError as exc:
            raise ValueError("midiHex is not valid hexadecimal MIDI") from exc
        return build_articulation_plan(midi, payload["catalog"], payload["groovePlan"],
                                       payload.get("expressionPlan"), payload["controls"])
    raise ValueError("Articulation API action must be reference, import or plan")


def execute_articulation_map_gui(payload: Mapping[str, Any]) -> dict[str, Any]:
    return execute_articulation_map_api(payload)