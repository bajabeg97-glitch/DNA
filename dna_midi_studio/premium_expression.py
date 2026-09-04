"""Session 24 read-only Premium Solo and Expression Director.

The director binds one physical solo track to SongMap phrases, an exact
time-scoped SoundBinding and a GroovePlan polyphony budget.  It never edits
the source MIDI.  Original notes are represented by immutable sourceNoteUid
records; every proposed note/controller event cites a source note, evidence
identifier and reason code.

Factory data is the only authority for generated velocity and CC11 bounds.
External ornament/relationship evidence may influence relative intervals and
timing only.  Software-test evidence can exercise the complete safety path,
but cannot make a plan production-renderable.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .groove_polyphony import (
    MIDI_NOTE_CEILING,
    analyze_full_duration_polyphony,
    validate_groove_plan_v2,
)
from .midi import MidiFile, Note
from .song_understanding import validate_song_map_v2
from .track_identity import (
    allocate_delay_track,
    channel_track_indices,
    fingerprint_solo,
    identity_for_track,
    sound_bindings,
)


EXPRESSION_PLAN_SCHEMA = "dna-premium-expression-plan"
EXPRESSION_PLAN_VERSION = "2.0"
EXPRESSION_EVIDENCE_SCHEMA = "dna-premium-expression-evidence"
EXPRESSION_EVIDENCE_VERSION = "1.0"
EXPRESSION_CONTROLS_VERSION = "1.0"

ORNAMENT_KINDS = ("grace", "trill", "slide", "turnaround")
RELATIONSHIP_KINDS = ("third", "echo")
NOTE_LAYER_ORDER = ("ECHO", "ORNAMENT", "THIRD")
_STABLE_ID = re.compile(r"^[0-9]{3}\.[0-9]{3}\.[0-9]{3}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TRACK_UID = re.compile(r"^trk-[0-9a-f]{20}(?:-[1-9][0-9]*)?$")
_FORBIDDEN_EVIDENCE_KEYS = {
    "velocity", "velocities", "bank", "bankmsb", "banklsb", "program",
    "programchange", "instrument", "sound", "cc11", "expression",
}
_QUALITY_FAMILIES = {
    "major": "major", "major-seventh": "major", "dominant-seventh": "major",
    "sixth": "major", "add-nine": "major", "suspended-2": "major",
    "suspended-4": "major", "power": "major", "augmented": "major",
    "minor": "minor", "minor-seventh": "minor", "minor-sixth": "minor",
    "diminished": "minor", "half-diminished": "minor",
    "diminished-seventh": "minor",
}
_SCALES = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return sha256(_canonical({key: item for key, item in value.items() if key != field})).hexdigest()


def _forbidden_paths(value: Any, prefix: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            path = f"{prefix}.{key}"
            if normalized in _FORBIDDEN_EVIDENCE_KEYS:
                found.append(path)
            found.extend(_forbidden_paths(item, path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(_forbidden_paths(item, f"{prefix}[{index}]"))
    return found


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


@dataclass(frozen=True)
class ExpressionControls:
    track_uid: str
    track_number: int
    channel_number: int
    start_tick: int
    end_tick: int
    seed: int = 2400
    intensity: int = 50
    profile_id: str | None = None
    enabled_layers: tuple[str, ...] = ("grace", "trill", "slide", "turnaround", "third", "echo", "cc11")
    min_evidence_confidence: float = 0.9
    ornament_budget: int = 24
    third_budget: int = 16
    echo_budget: int = 8
    allow_shared_channel: bool = False
    version: str = EXPRESSION_CONTROLS_VERSION

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ExpressionControls":
        if not isinstance(raw, Mapping):
            raise ValueError("Expression controls must be an object")
        allowed = {
            "version", "trackUid", "trackNumber", "channelNumber", "startTick",
            "endTick", "seed", "intensity", "profileId", "enabledLayers",
            "minEvidenceConfidence", "layerBudgets", "allowSharedChannel",
        }
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError("Unknown Expression controls: " + ", ".join(sorted(unknown)))
        if raw.get("version", EXPRESSION_CONTROLS_VERSION) != EXPRESSION_CONTROLS_VERSION:
            raise ValueError("Expression controls require version 1.0")
        track_uid = raw.get("trackUid")
        if not isinstance(track_uid, str) or not _TRACK_UID.fullmatch(track_uid):
            raise ValueError("trackUid must be a stable physical track identity")
        track_number = raw.get("trackNumber")
        channel_number = raw.get("channelNumber")
        if isinstance(track_number, bool) or not isinstance(track_number, int) or not 1 <= track_number <= 16:
            raise ValueError("trackNumber must be a 1-based integer in range 1..16")
        if isinstance(channel_number, bool) or not isinstance(channel_number, int) or not 1 <= channel_number <= 16:
            raise ValueError("channelNumber must be a 1-based integer in range 1..16")
        start, end = raw.get("startTick"), raw.get("endTick")
        if any(isinstance(item, bool) or not isinstance(item, int) for item in (start, end)):
            raise ValueError("Expression window ticks must be integers")
        if start < 0 or end <= start:
            raise ValueError("Expression window is invalid")
        seed = raw.get("seed", 2400)
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**31 - 1:
            raise ValueError("Expression seed must be in range 0..2147483647")
        intensity = raw.get("intensity", 50)
        if isinstance(intensity, bool) or not isinstance(intensity, int) or not 0 <= intensity <= 100:
            raise ValueError("Expression intensity must be in range 0..100")
        profile_id = raw.get("profileId")
        if profile_id is not None and (not isinstance(profile_id, str) or not _STABLE_ID.fullmatch(profile_id)):
            raise ValueError("profileId must be a stable Factory ID")
        layers = tuple(raw.get("enabledLayers", cls.enabled_layers))
        allowed_layers = set(ORNAMENT_KINDS) | set(RELATIONSHIP_KINDS) | {"cc11"}
        if len(layers) != len(set(layers)) or not set(layers) <= allowed_layers:
            raise ValueError("enabledLayers contains duplicates or unsupported layers")
        confidence = raw.get("minEvidenceConfidence", 0.9)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("minEvidenceConfidence must be in range 0..1")
        budgets = raw.get("layerBudgets", {})
        if not isinstance(budgets, Mapping) or set(budgets) - {"ornament", "third", "echo"}:
            raise ValueError("layerBudgets fields mismatch")
        values = [budgets.get("ornament", 24), budgets.get("third", 16), budgets.get("echo", 8)]
        if any(isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 128 for item in values):
            raise ValueError("Expression layer budgets must be integers in range 0..128")
        shared = raw.get("allowSharedChannel", False)
        if not isinstance(shared, bool):
            raise ValueError("allowSharedChannel must be boolean")
        return cls(track_uid, track_number, channel_number, start, end, seed, intensity,
                   profile_id, layers, float(confidence), *values, shared)

    @property
    def track_index(self) -> int:
        return self.track_number - 1

    @property
    def channel_index(self) -> int:
        return self.channel_number - 1

    def to_manifest(self) -> dict[str, Any]:
        return {
            "version": self.version, "trackUid": self.track_uid,
            "trackNumber": self.track_number, "channelNumber": self.channel_number,
            "startTick": self.start_tick, "endTick": self.end_tick,
            "seed": self.seed, "intensity": self.intensity,
            "profileId": self.profile_id, "enabledLayers": list(self.enabled_layers),
            "minEvidenceConfidence": self.min_evidence_confidence,
            "layerBudgets": {"ornament": self.ornament_budget,
                             "third": self.third_budget, "echo": self.echo_budget},
            "allowSharedChannel": self.allow_shared_channel,
        }


def build_expression_evidence(*, authority: str = "SOFTWARE_TEST_ONLY") -> dict[str, Any]:
    """Return a self-authored reference corpus used only by the Session 24 gate."""
    if authority != "SOFTWARE_TEST_ONLY":
        raise ValueError("Built-in reference evidence can only be SOFTWARE_TEST_ONLY")
    qualities = sorted(_QUALITY_FAMILIES)
    evidence = {
        "schema": EXPRESSION_EVIDENCE_SCHEMA, "version": EXPRESSION_EVIDENCE_VERSION,
        "authority": authority,
        "notice": "Self-authored relative-interval evidence for software validation only.",
        "ornaments": [
            {"evidenceId": "240.100.001", "kind": "grace", "intervals": [-1],
             "allowedQualities": qualities, "minGapTicks": 100,
             "noteDurationTicks": 36, "confidence": 0.96, "status": "CONFIRMED"},
            {"evidenceId": "240.100.002", "kind": "trill", "intervals": [2, 0, 2],
             "allowedQualities": qualities, "minGapTicks": 180,
             "noteDurationTicks": 40, "confidence": 0.95, "status": "CONFIRMED"},
            {"evidenceId": "240.100.003", "kind": "slide", "intervals": [-2, -1],
             "allowedQualities": qualities, "minGapTicks": 150,
             "noteDurationTicks": 36, "confidence": 0.94, "status": "CONFIRMED"},
            {"evidenceId": "240.100.004", "kind": "turnaround", "intervals": [-2, 0, 2],
             "allowedQualities": qualities, "minGapTicks": 220,
             "noteDurationTicks": 36, "confidence": 0.93, "status": "CONFIRMED"},
        ],
        "relationships": [
            {"evidenceId": "240.200.001", "kind": "third",
             "allowedQualities": qualities, "acceptedIntervals": [3, 4],
             "confidence": 0.97, "status": "CONFIRMED"},
            {"evidenceId": "240.200.002", "kind": "echo",
             "allowedQualities": qualities, "delayTicks": 360,
             "durationRatio": 0.5, "confidence": 0.96, "status": "CONFIRMED"},
        ],
        "provenanceHash": "0" * 64,
        "evidenceHash": "",
    }
    evidence["evidenceHash"] = _hash_without(evidence, "evidenceHash")
    return evidence


def validate_expression_evidence(value: Mapping[str, Any]) -> None:
    required = {"schema", "version", "authority", "notice", "ornaments",
                "relationships", "provenanceHash", "evidenceHash"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("Expression evidence root fields mismatch")
    if value["schema"] != EXPRESSION_EVIDENCE_SCHEMA or value["version"] != EXPRESSION_EVIDENCE_VERSION:
        raise ValueError("Unsupported expression evidence schema/version")
    if value["authority"] not in {"SOFTWARE_TEST_ONLY", "PRODUCTION_VERIFIED"}:
        raise ValueError("Expression evidence authority is invalid")
    if not _HEX64.fullmatch(str(value["provenanceHash"])):
        raise ValueError("Expression evidence provenance hash is invalid")
    seen: set[str] = set()
    for item in value["ornaments"]:
        keys = {"evidenceId", "kind", "intervals", "allowedQualities", "minGapTicks",
                "noteDurationTicks", "confidence", "status"}
        if set(item) != keys or item["kind"] not in ORNAMENT_KINDS:
            raise ValueError("Expression ornament evidence fields/kind mismatch")
        if (not _STABLE_ID.fullmatch(str(item["evidenceId"])) or item["evidenceId"] in seen
                or item["status"] != "CONFIRMED"):
            raise ValueError("Expression ornament evidence ID/status is invalid")
        seen.add(item["evidenceId"])
        if (not item["intervals"] or any(isinstance(x, bool) or not isinstance(x, int) or not -12 <= x <= 12
                                         for x in item["intervals"])):
            raise ValueError("Expression ornament intervals must be relative semitones")
        if not set(item["allowedQualities"]) <= set(_QUALITY_FAMILIES):
            raise ValueError("Expression ornament chord qualities are invalid")
        if (item["minGapTicks"] <= 0 or item["noteDurationTicks"] <= 0
                or not 0 <= item["confidence"] <= 1):
            raise ValueError("Expression ornament timing/confidence is invalid")
    for item in value["relationships"]:
        common = {"evidenceId", "kind", "allowedQualities", "confidence", "status"}
        extras = {"acceptedIntervals"} if item.get("kind") == "third" else {"delayTicks", "durationRatio"}
        if set(item) != common | extras or item.get("kind") not in RELATIONSHIP_KINDS:
            raise ValueError("Expression relationship evidence fields/kind mismatch")
        if (not _STABLE_ID.fullmatch(str(item["evidenceId"])) or item["evidenceId"] in seen
                or item["status"] != "CONFIRMED"):
            raise ValueError("Expression relationship evidence ID/status is invalid")
        seen.add(item["evidenceId"])
        if not set(item["allowedQualities"]) <= set(_QUALITY_FAMILIES) or not 0 <= item["confidence"] <= 1:
            raise ValueError("Expression relationship qualities/confidence are invalid")
        if item["kind"] == "third" and (not item["acceptedIntervals"]
                or any(x not in {3, 4} for x in item["acceptedIntervals"])):
            raise ValueError("Third evidence requires diatonic 3/4-semitone intervals")
        if item["kind"] == "echo" and (item["delayTicks"] <= 0
                or not 0 < item["durationRatio"] <= 1):
            raise ValueError("Echo evidence timing is invalid")
    if _forbidden_paths({"ornaments": value["ornaments"], "relationships": value["relationships"]}):
        raise ValueError("Expression evidence contains forbidden dynamics or sound authority")
    if value["evidenceHash"] != _hash_without(value, "evidenceHash"):
        raise ValueError("Expression evidence hash mismatch")


def _production_evidence_is_trusted(root: Path, evidence: Mapping[str, Any]) -> bool:
    """Require a separately maintained local allow-list for production authority.

    A caller cannot promote a JSON payload merely by changing its authority
    string.  Session 24 intentionally ships without this allow-list.
    """
    if evidence["authority"] != "PRODUCTION_VERIFIED":
        return False
    path = root / "data" / "approved-expression-evidence.json"
    if not path.is_file():
        return False
    trust = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(trust, Mapping)
            or set(trust) != {"schema", "version", "approvedEvidenceHashes", "trustStoreHash"}
            or trust["schema"] != "dna-approved-expression-evidence"
            or trust["version"] != "1.0"
            or trust["trustStoreHash"] != _hash_without(trust, "trustStoreHash")
            or any(not _HEX64.fullmatch(str(item)) for item in trust["approvedEvidenceHashes"])):
        raise ValueError("Approved expression evidence trust store is invalid")
    return evidence["evidenceHash"] in set(trust["approvedEvidenceHashes"])


def _load_factory_profiles(root: Path) -> tuple[str, dict[str, Mapping[str, Any]]]:
    path = root / "data" / "factory-velocity-profiles.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    profiles = {str(item["id"]): item for item in raw["profiles"] if item.get("kind") == "melodic"}
    return str(raw["databaseVersion"]), profiles


def _curve_values(profile: Mapping[str, Any], kind: str) -> tuple[int, ...]:
    if kind == "velocity":
        raw = profile["velocityCurve"]["values"]
    else:
        raw = profile["mixerProfile"]["expression"]["values"]
    return tuple(int(raw[key]) for key in ("floor", "soft", "lowMid", "optimal", "highMid", "strong", "ceiling"))


def _curve_value(values: Sequence[int], intensity: int) -> int:
    intensity = _clamp(intensity, 0, 100)
    if intensity == 100:
        return int(values[-1])
    position = intensity * 6 / 100
    lower = min(int(position), 5)
    fraction = position - lower
    return int(round(values[lower] + fraction * (values[lower + 1] - values[lower])))


def _normalize_profile(database_version: str, profile: Mapping[str, Any]) -> dict[str, Any]:
    velocity = _curve_values(profile, "velocity")
    expression = _curve_values(profile, "expression")
    normalized = {
        "profileId": str(profile["id"]), "databaseVersion": database_version,
        "sourceProfileHash": sha256(_canonical(profile)).hexdigest(),
        "soundBinding": {"bankMsb": int(profile["bankMsb"]),
                         "bankLsb": int(profile["bankLsb"]),
                         "program": int(profile["program"])},
        "register": {"min": int(profile["register"]["low"]),
                     "max": int(profile["register"]["high"])},
        "velocityCurve": list(velocity), "cc11Curve": list(expression),
        "cc11Bounds": [min(expression), max(expression)],
        "smoothingMaxStep": 7, "authority": "FACTORY_ONLY",
        "profileHash": "",
    }
    normalized["profileHash"] = _hash_without(normalized, "profileHash")
    return normalized


def _find_profile(root: Path, controls: ExpressionControls, sound: tuple[int, int, int] | None) -> dict[str, Any] | None:
    database_version, profiles = _load_factory_profiles(root)
    if controls.profile_id is not None:
        raw = profiles.get(controls.profile_id)
        return _normalize_profile(database_version, raw) if raw is not None else None
    matches = [item for item in profiles.values()
               if sound is not None and (int(item["bankMsb"]), int(item["bankLsb"]), int(item["program"])) == sound]
    if len(matches) != 1:
        return None
    return _normalize_profile(database_version, matches[0])


def _source_notes(midi: MidiFile, controls: ExpressionControls) -> list[Note]:
    notes = [note for note in midi.notes()
             if note.track == controls.track_index and note.channel == controls.channel_index
             and controls.start_tick <= note.start < controls.end_tick]
    return sorted(notes, key=lambda item: (item.start, item.pitch, item.end, item.velocity))


def _note_records(notes: Sequence[Note], controls: ExpressionControls) -> list[dict[str, Any]]:
    occurrences: Counter[tuple[int, int, int, int]] = Counter()
    output = []
    for note in notes:
        signature = (note.start, note.end, note.pitch, note.velocity)
        occurrences[signature] += 1
        uid = "src-" + sha256(_canonical([
            controls.track_uid, controls.channel_number, *signature, occurrences[signature]
        ])).hexdigest()[:20]
        output.append({
            "sourceNoteUid": uid, "onsetTick": note.start,
            "durationTick": note.end - note.start, "pitch": note.pitch,
            "velocity": note.velocity, "occurrence": occurrences[signature],
            "immutable": True,
        })
    return output


def _phrase_at(song_map: Mapping[str, Any], tick: int) -> Mapping[str, Any] | None:
    return next((item for item in song_map["phrases"]
                 if int(item["startTick"]) <= tick < int(item["endTick"])), None)


def _chord_at(song_map: Mapping[str, Any], tick: int) -> Mapping[str, Any] | None:
    return next((item for item in song_map["chordCells"]
                 if int(item["startTick"]) <= tick < int(item["endTick"])), None)


def _phrase_refs(song_map: Mapping[str, Any], controls: ExpressionControls,
                 note_records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    refs = []
    for phrase in song_map["phrases"]:
        start = max(controls.start_tick, int(phrase["startTick"]))
        end = min(controls.end_tick, int(phrase["endTick"]))
        if end <= start:
            continue
        uids = [item["sourceNoteUid"] for item in note_records
                if start <= int(item["onsetTick"]) < end]
        if uids:
            refs.append({"phraseId": phrase["id"], "sectionId": phrase["sectionId"],
                         "startTick": start, "endTick": end,
                         "confidence": phrase["confidence"], "sourceNoteUids": uids})
    return refs


def _reason(kind: str) -> str:
    return {
        "grace": "PHRASE_ENTRY_GRACE_CONFIRMED",
        "trill": "PHRASE_GAP_TRILL_CONFIRMED",
        "slide": "PHRASE_ENTRY_SLIDE_CONFIRMED",
        "turnaround": "PHRASE_END_TURNAROUND_CONFIRMED",
        "third": "DIATONIC_THIRD_HARMONY_CONFIRMED",
        "echo": "NONRECURSIVE_DELAY_SPACE_CONFIRMED",
        "cc11": "FACTORY_CC11_TENSION_RELEASE",
    }[kind]


def _event(*, kind: str, source: Mapping[str, Any], evidence_id: str,
           onset: int, duration: int, pitch: int, velocity: int,
           phrase_id: str, chord_index: int, track_uid: str,
           track_number: int, channel_number: int, routing: str,
           production_eligible: bool) -> dict[str, Any]:
    payload = {
        "eventId": "exp-" + sha256(_canonical([
            kind, source["sourceNoteUid"], evidence_id, onset, duration, pitch, routing
        ])).hexdigest()[:20],
        "kind": kind, "sourceNoteUid": source["sourceNoteUid"],
        "evidenceId": evidence_id, "reasonCode": _reason(kind),
        "phraseId": phrase_id, "chordCellIndex": chord_index,
        "trackUid": track_uid, "trackNumber": track_number,
        "channelNumber": channel_number, "routing": routing,
        "onsetTick": onset, "durationTick": duration,
        "pitch": pitch, "velocity": velocity,
        "productionEligible": production_eligible, "eventHash": "",
    }
    payload["eventHash"] = _hash_without(payload, "eventHash")
    return payload


def _overlaps(start: int, end: int, events: Iterable[Mapping[str, Any]], pitch: int | None = None) -> bool:
    return any((pitch is None or int(item["pitch"]) == pitch)
               and int(item["onsetTick"]) < end
               and int(item["onsetTick"]) + int(item["durationTick"]) > start
               for item in events)


def _ornament_layers(note_records: Sequence[Mapping[str, Any]], song_map: Mapping[str, Any],
                     evidence: Mapping[str, Any], profile: Mapping[str, Any],
                     controls: ExpressionControls, production: bool) -> list[dict[str, Any]]:
    layers = []
    register = profile["register"]
    velocity = _curve_value(profile["velocityCurve"], controls.intensity - 5)
    existing: list[dict[str, Any]] = []
    for spec in evidence["ornaments"]:
        kind = str(spec["kind"])
        events: list[dict[str, Any]] = []
        skipped = Counter()
        if kind not in controls.enabled_layers or spec["confidence"] < controls.min_evidence_confidence:
            decision, reason = "KEEP", "DISABLED_OR_EVIDENCE_BELOW_THRESHOLD"
        else:
            for index in range(max(0, len(note_records) - 1)):
                current, following = note_records[index], note_records[index + 1]
                current_end = int(current["onsetTick"]) + int(current["durationTick"])
                next_start = int(following["onsetTick"])
                gap = next_start - current_end
                phrase = _phrase_at(song_map, next_start if kind != "trill" else int(current["onsetTick"]))
                chord = _chord_at(song_map, next_start if kind != "trill" else int(current["onsetTick"]))
                if phrase is None or float(phrase["confidence"]) < 0.8:
                    skipped["PHRASE_UNCERTAIN"] += 1; continue
                if chord is None or chord["decision"] != "ACCEPT" or float(chord["confidence"]) < controls.min_evidence_confidence:
                    skipped["HARMONY_UNCERTAIN"] += 1; continue
                if chord["quality"] not in spec["allowedQualities"]:
                    skipped["QUALITY_UNSUPPORTED"] += 1; continue
                total = len(spec["intervals"]) * int(spec["noteDurationTicks"])
                if gap < max(int(spec["minGapTicks"]), total):
                    skipped["NO_FREE_TIMING_SPACE"] += 1; continue
                anchor = current if kind in {"trill", "turnaround"} else following
                first = current_end if kind == "trill" else next_start - total
                if kind == "turnaround":
                    first = max(current_end, next_start - total)
                proposal = []
                for offset_index, interval in enumerate(spec["intervals"]):
                    onset = first + offset_index * int(spec["noteDurationTicks"])
                    duration = int(spec["noteDurationTicks"])
                    pitch = int(anchor["pitch"]) + int(interval)
                    if not int(register["min"]) <= pitch <= int(register["max"]):
                        skipped["REGISTER"] += 1; proposal = []; break
                    if onset < controls.start_tick or onset + duration > controls.end_tick:
                        skipped["WINDOW"] += 1; proposal = []; break
                    proposal.append(_event(
                        kind=kind, source=anchor, evidence_id=spec["evidenceId"],
                        onset=onset, duration=duration, pitch=pitch, velocity=velocity,
                        phrase_id=str(phrase["id"]), chord_index=int(chord["cellIndex"]),
                        track_uid=controls.track_uid, track_number=controls.track_number,
                        channel_number=controls.channel_number, routing="SOURCE_SOLO_PREVIEW_LAYER",
                        production_eligible=production,
                    ))
                if proposal and not any(_overlaps(int(item["onsetTick"]), int(item["onsetTick"]) + int(item["durationTick"]), existing, int(item["pitch"])) for item in proposal):
                    events.extend(proposal); existing.extend(proposal)
                elif proposal:
                    skipped["GENERATED_COLLISION"] += 1
                if len(events) >= controls.ornament_budget:
                    events = events[:controls.ornament_budget]; break
            decision = "ADD" if events else "KEEP"
            reason = "EVIDENCE_AND_SPACE_CONFIRMED" if events else "NO_SAFE_ORNAMENT_SPACE"
        layer = {"layerId": f"layer-{kind}", "layerType": "ORNAMENT", "subtype": kind,
                 "priorityTier": "DECORATIVE", "removable": True,
                 "decision": decision, "reason": reason, "budget": controls.ornament_budget,
                 "events": events, "skipped": dict(sorted(skipped.items())), "layerHash": ""}
        layer["layerHash"] = _hash_without(layer, "layerHash")
        layers.append(layer)
    return layers


def _diatonic_third(pitch: int, key: Mapping[str, Any]) -> int | None:
    mode = str(key["mode"])
    scale = _SCALES.get(mode)
    if scale is None:
        return None
    relative = (pitch - int(key["root"])) % 12
    if relative not in scale:
        return None
    degree = scale.index(relative)
    target = scale[(degree + 2) % 7] + (12 if degree + 2 >= 7 else 0)
    interval = target - relative
    return interval if interval in {3, 4} else None


def _third_layer(note_records: Sequence[Mapping[str, Any]], song_map: Mapping[str, Any],
                 evidence: Mapping[str, Any], profile: Mapping[str, Any],
                 controls: ExpressionControls, production: bool) -> dict[str, Any]:
    spec = next((item for item in evidence["relationships"] if item["kind"] == "third"), None)
    events: list[dict[str, Any]] = []
    skipped = Counter()
    if spec is not None and "third" in controls.enabled_layers and spec["confidence"] >= controls.min_evidence_confidence:
        for source in note_records:
            chord = _chord_at(song_map, int(source["onsetTick"]))
            phrase = _phrase_at(song_map, int(source["onsetTick"]))
            if chord is None or phrase is None or chord["decision"] != "ACCEPT" or float(chord["confidence"]) < controls.min_evidence_confidence:
                skipped["HARMONY_UNCERTAIN"] += 1; continue
            interval = _diatonic_third(int(source["pitch"]), song_map["key"])
            pitch = int(source["pitch"]) + interval if interval is not None else None
            if interval not in spec["acceptedIntervals"] or pitch is None:
                skipped["NOT_DIATONIC"] += 1; continue
            if not int(profile["register"]["min"]) <= pitch <= int(profile["register"]["max"]):
                skipped["REGISTER"] += 1; continue
            onset, duration = int(source["onsetTick"]), int(source["durationTick"])
            if _overlaps(onset, onset + duration, events, pitch):
                skipped["GENERATED_COLLISION"] += 1; continue
            events.append(_event(
                kind="third", source=source, evidence_id=spec["evidenceId"],
                onset=onset, duration=duration, pitch=pitch,
                velocity=_curve_value(profile["velocityCurve"], controls.intensity - 12),
                phrase_id=str(phrase["id"]), chord_index=int(chord["cellIndex"]),
                track_uid="virtual-third-" + controls.track_uid[4:], track_number=controls.track_number,
                channel_number=controls.channel_number, routing="AI_HARMONY_PREVIEW_LAYER",
                production_eligible=production,
            ))
            if len(events) >= controls.third_budget:
                break
    decision = "ADD" if events else "KEEP"
    layer = {"layerId": "layer-third", "layerType": "THIRD", "subtype": "third",
             "priorityTier": "SUPPORT", "removable": True, "decision": decision,
             "reason": "DIATONIC_HARMONY_CONFIRMED" if events else "NO_SAFE_THIRD",
             "budget": controls.third_budget, "events": events,
             "skipped": dict(sorted(skipped.items())), "layerHash": ""}
    layer["layerHash"] = _hash_without(layer, "layerHash")
    return layer


def _echo_layer(midi: MidiFile, note_records: Sequence[Mapping[str, Any]], song_map: Mapping[str, Any],
                evidence: Mapping[str, Any], profile: Mapping[str, Any], controls: ExpressionControls,
                production: bool) -> tuple[dict[str, Any], dict[str, Any] | None]:
    spec = next((item for item in evidence["relationships"] if item["kind"] == "echo"), None)
    allocation = allocate_delay_track(midi, source_track_index=controls.track_index,
                                      channel=controls.channel_index,
                                      allow_existing_shared_channel=controls.allow_shared_channel)
    allocation_manifest = allocation.to_manifest()
    events: list[dict[str, Any]] = []
    skipped = Counter()
    if (spec is not None and "echo" in controls.enabled_layers
            and spec["confidence"] >= controls.min_evidence_confidence
            and allocation.allowed and allocation.target_track_uid is not None
            and allocation.target_track_number is not None):
        for index, source in enumerate(note_records):
            phrase = _phrase_at(song_map, int(source["onsetTick"]))
            chord = _chord_at(song_map, int(source["onsetTick"]))
            if phrase is None or chord is None or chord["decision"] != "ACCEPT" or float(chord["confidence"]) < controls.min_evidence_confidence:
                skipped["HARMONY_UNCERTAIN"] += 1; continue
            start = int(source["onsetTick"]) + int(spec["delayTicks"])
            source_end = int(source["onsetTick"]) + int(source["durationTick"])
            next_start = (int(note_records[index + 1]["onsetTick"])
                          if index + 1 < len(note_records) else controls.end_tick)
            duration = max(1, round(int(source["durationTick"]) * float(spec["durationRatio"])))
            end = min(start + duration, next_start, controls.end_tick)
            if start < source_end or end <= start:
                skipped["NO_FREE_TIMING_SPACE"] += 1; continue
            events.append(_event(
                kind="echo", source=source, evidence_id=spec["evidenceId"],
                onset=start, duration=end - start, pitch=int(source["pitch"]),
                velocity=_curve_value(profile["velocityCurve"], controls.intensity - 30),
                phrase_id=str(phrase["id"]), chord_index=int(chord["cellIndex"]),
                track_uid=str(allocation.target_track_uid),
                track_number=int(allocation.target_track_number),
                channel_number=controls.channel_number, routing="SEPARATE_DELAY_PREVIEW_TRACK",
                production_eligible=production,
            ))
            if len(events) >= controls.echo_budget:
                break
    decision = "ADD" if events else ("MANUAL_REVIEW" if spec is not None and not allocation.allowed else "KEEP")
    layer = {"layerId": "layer-echo", "layerType": "ECHO", "subtype": "echo",
             "priorityTier": "DECORATIVE", "removable": True, "decision": decision,
             "reason": ("NONRECURSIVE_SEPARATE_ROUTING" if events else allocation.reason),
             "budget": controls.echo_budget, "events": events,
             "skipped": dict(sorted(skipped.items())), "layerHash": ""}
    layer["layerHash"] = _hash_without(layer, "layerHash")
    return layer, allocation_manifest


def _cc11_plan(midi: MidiFile, note_records: Sequence[Mapping[str, Any]], song_map: Mapping[str, Any],
               profile: Mapping[str, Any], controls: ExpressionControls) -> dict[str, Any]:
    existing = [event for event in midi.tracks[controls.track_index].events
                if event.kind == "channel" and event.command == 0xB0
                and event.channel == controls.channel_index and len(event.data) == 2
                and event.data[0] == 11 and controls.start_tick <= event.tick < controls.end_tick]
    points: list[dict[str, Any]] = []
    if "cc11" in controls.enabled_layers and not existing:
        previous = _curve_value(profile["cc11Curve"], controls.intensity)
        previous_chord = None
        for index, source in enumerate(note_records):
            phrase = _phrase_at(song_map, int(source["onsetTick"]))
            chord = _chord_at(song_map, int(source["onsetTick"]))
            if phrase is None or chord is None:
                continue
            phrase_start = source["sourceNoteUid"] == next((item["sourceNoteUid"] for item in note_records
                if int(phrase["startTick"]) <= int(item["onsetTick"]) < int(phrase["endTick"])), None)
            chord_key = (chord["root"], chord["quality"])
            leap = abs(int(source["pitch"]) - int(note_records[index - 1]["pitch"])) if index else 0
            target_intensity = controls.intensity + min(12, leap) + (6 if previous_chord not in {None, chord_key} else 0)
            if phrase_start:
                target_intensity -= 6
            if "ending-phrase" in next((p.get("cues", []) for p in song_map["phrases"] if p["id"] == phrase["id"]), []):
                target_intensity -= 10
            target = _curve_value(profile["cc11Curve"], target_intensity)
            value = previous + _clamp(target - previous, -int(profile["smoothingMaxStep"]), int(profile["smoothingMaxStep"]))
            value = _clamp(value, int(profile["cc11Bounds"][0]), int(profile["cc11Bounds"][1]))
            point = {"pointId": "cc11-" + sha256(_canonical([source["sourceNoteUid"], source["onsetTick"], value])).hexdigest()[:20],
                     "sourceNoteUid": source["sourceNoteUid"], "evidenceId": profile["profileId"],
                     "reasonCode": _reason("cc11"), "phraseId": phrase["id"],
                     "tick": source["onsetTick"], "controller": 11, "value": value,
                     "productionEligible": True, "pointHash": ""}
            point["pointHash"] = _hash_without(point, "pointHash")
            points.append(point)
            previous, previous_chord = value, chord_key
    plan = {"decision": ("PRESERVE_MANUAL" if existing else "ADD" if points else "KEEP"),
            "manualExisting": bool(existing), "factoryProfileId": profile["profileId"],
            "bounds": list(profile["cc11Bounds"]), "maxStep": profile["smoothingMaxStep"],
            "points": points, "planHash": ""}
    plan["planHash"] = _hash_without(plan, "planHash")
    return plan


def _expression_events(layers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [deepcopy(event) for layer in layers for event in layer["events"]]


def _variant_budgets(groove_plan: Mapping[str, Any], layers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    all_events = _expression_events(layers)
    output = []
    for variant in groove_plan["variants"]:
        baseline = int(variant["polyphonyAfter"]["globalPeak"])
        enabled = list(all_events)
        suppressed: list[str] = []
        operations = []
        def measured() -> Mapping[str, Any]:
            return analyze_full_duration_polyphony(enabled)
        for layer_type in NOTE_LAYER_ORDER:
            if baseline + int(measured()["peak"]) <= MIDI_NOTE_CEILING:
                break
            victims = [item for item in enabled if next(layer["layerType"] for layer in layers
                       if item in layer["events"]) == layer_type]
            if victims:
                ids = {item["eventId"] for item in victims}
                enabled = [item for item in enabled if item["eventId"] not in ids]
                suppressed.extend(sorted(ids))
                operations.append({"operation": f"DROP_{layer_type}_LAYER", "eventCount": len(ids),
                                   "reason": "GROOVEPLAN_54_NOTE_BUDGET"})
        additional = int(measured()["peak"])
        blocked = baseline + additional > MIDI_NOTE_CEILING
        item = {"variantId": variant["variantId"], "sourceVariantHash": variant["variantHash"],
                "baselinePeak": baseline, "availableHeadroom": max(0, MIDI_NOTE_CEILING - baseline),
                "additionalPeak": additional, "estimatedPeak": baseline + additional,
                "enabledEventIds": sorted(event["eventId"] for event in enabled),
                "suppressedEventIds": suppressed, "operations": operations,
                "decision": "MANUAL_REVIEW" if blocked else "ADD" if enabled else "KEEP",
                "withinMidiNoteCeiling": not blocked, "budgetHash": ""}
        item["budgetHash"] = _hash_without(item, "budgetHash")
        output.append(item)
    return output


def _blocked_plan(midi: MidiFile, groove_plan: Mapping[str, Any], song_map: Mapping[str, Any],
                  controls: ExpressionControls, reason: str,
                  profile: Mapping[str, Any] | None = None,
                  evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    identity = identity_for_track(midi, controls.track_index) if controls.track_index < len(midi.tracks) else None
    notes = _source_notes(midi, controls) if identity is not None else []
    records = _note_records(notes, controls)
    fingerprint = (fingerprint_solo(midi, track_index=controls.track_index,
                                    channel=controls.channel_index,
                                    start_tick=controls.start_tick, end_tick=controls.end_tick,
                                    track_uid=controls.track_uid).to_manifest()
                   if identity is not None else None)
    result = {
        "schema": EXPRESSION_PLAN_SCHEMA, "version": EXPRESSION_PLAN_VERSION,
        "source": {"inputMidiSha256": midi.digest(), "songMapHash": song_map["mapHash"],
                   "groovePlanHash": groove_plan["groovePlanHash"], "trackUid": controls.track_uid,
                   "trackNumber": controls.track_number, "channelNumber": controls.channel_number,
                   "originalSoloFingerprint": fingerprint},
        "controls": controls.to_manifest(), "factoryProfile": deepcopy(profile),
        "evidence": {"authority": evidence["authority"] if evidence else "NONE",
                     "evidenceHash": evidence["evidenceHash"] if evidence else None,
                     "productionEligible": bool(evidence and evidence["authority"] == "PRODUCTION_VERIFIED")},
        "phrases": [], "originalNotes": records, "layers": [],
        "cc11": {"decision": "KEEP", "manualExisting": False,
                 "factoryProfileId": profile["profileId"] if profile else None,
                 "bounds": profile["cc11Bounds"] if profile else None,
                 "maxStep": profile["smoothingMaxStep"] if profile else None,
                 "points": [], "planHash": sha256(reason.encode()).hexdigest()},
        "delayAllocation": None, "variantBudgets": [], "previews": [],
        "removalOperation": {"operation": "REMOVE_AI_EXPRESSION_LAYER", "removesOnlyAiLayers": True,
                             "preservesOriginalFingerprint": True, "eventIds": [], "operationHash": ""},
        "audit": {"originalNoteCount": len(records), "generatedNoteCount": 0,
                  "cc11PointCount": 0, "sourceNoteUidCoverage": True,
                  "evidenceCoverage": True, "reasonCodeCoverage": True,
                  "maximumEstimatedPeak": 0, "allVariantsWithinMidiNoteCeiling": False},
        "manualReview": [{"code": "SOLO_EXPRESSION_BLOCKED", "reason": reason}],
        "productionBlocks": ["SOLO_EXPRESSION_BLOCKED"],
        "readyForPreview": False, "readyForProductionRender": False,
        "safety": {"readOnly": True, "midiMutationAllowed": False, "finalMidiGenerated": False,
                   "originalSoloUnchanged": True, "soundBindingUnchanged": True,
                   "factoryDynamicsOnly": True, "goldDynamicsAuthority": False,
                   "nonRecursiveEcho": True, "aiLayersRemovable": True,
                   "softwareMidiNoteCeiling": MIDI_NOTE_CEILING},
        "expressionPlanHash": "",
    }
    result["removalOperation"]["operationHash"] = _hash_without(result["removalOperation"], "operationHash")
    result["expressionPlanHash"] = _hash_without(result, "expressionPlanHash")
    validate_expression_plan_v2(result)
    return result


def build_expression_plan(midi: MidiFile, groove_plan: Mapping[str, Any],
                          song_map: Mapping[str, Any], root: Path,
                          controls: Mapping[str, Any],
                          evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    validate_groove_plan_v2(groove_plan)
    validate_song_map_v2(song_map)
    normalized = ExpressionControls.from_mapping(controls)
    if groove_plan["source"]["songMapHash"] != song_map["mapHash"]:
        raise ValueError("GroovePlan and SongMap hashes do not match")
    if song_map["sourceSha256"] != midi.digest() or groove_plan["source"]["sourceMidiSha256"] != midi.digest():
        raise ValueError("Expression source MIDI hash does not match SongMap/GroovePlan")
    if not groove_plan["readyForRenderPlanning"]:
        raise ValueError("GroovePlan manual review must be resolved before expression planning")
    if normalized.track_index >= len(midi.tracks):
        raise ValueError("Selected solo track does not exist")
    identity = identity_for_track(midi, normalized.track_index)
    if identity.track_uid != normalized.track_uid or identity.track_number != normalized.track_number:
        return _blocked_plan(midi, groove_plan, song_map, normalized,
                             "trackUid/trackNumber does not identify the selected physical solo track")
    owners = channel_track_indices(midi, normalized.channel_index)
    if set(owners) - {normalized.track_index} and not normalized.allow_shared_channel:
        return _blocked_plan(midi, groove_plan, song_map, normalized,
                             "Solo channel is shared by multiple physical tracks")
    bindings = sound_bindings(midi, track_index=normalized.track_index,
                              channel=normalized.channel_index,
                              start_tick=normalized.start_tick, end_tick=normalized.end_tick,
                              track_uid=normalized.track_uid)
    exact_sounds = {item.sound for item in bindings}
    exact_sound = next(iter(exact_sounds)) if len(exact_sounds) == 1 else None
    profile = _find_profile(Path(root), normalized, exact_sound)
    if profile is None:
        return _blocked_plan(midi, groove_plan, song_map, normalized,
                             "Exact Factory expression profile is missing or ambiguous")
    profile_sound = profile["soundBinding"]
    expected = (profile_sound["bankMsb"], profile_sound["bankLsb"], profile_sound["program"])
    if any(item.sound != expected for item in bindings):
        return _blocked_plan(midi, groove_plan, song_map, normalized,
                             "Time-scoped SoundBinding does not match the Factory profile",
                             profile=profile)
    if evidence is None:
        evidence = {"schema": EXPRESSION_EVIDENCE_SCHEMA, "version": EXPRESSION_EVIDENCE_VERSION,
                    "authority": "SOFTWARE_TEST_ONLY", "notice": "No ornament evidence supplied.",
                    "ornaments": [], "relationships": [], "provenanceHash": "0" * 64,
                    "evidenceHash": ""}
        evidence["evidenceHash"] = _hash_without(evidence, "evidenceHash")
    validate_expression_evidence(evidence)
    production = _production_evidence_is_trusted(Path(root), evidence)
    notes = _source_notes(midi, normalized)
    records = _note_records(notes, normalized)
    fingerprint = fingerprint_solo(midi, track_index=normalized.track_index,
                                   channel=normalized.channel_index,
                                   start_tick=normalized.start_tick, end_tick=normalized.end_tick,
                                   track_uid=normalized.track_uid)
    if not records:
        return _blocked_plan(midi, groove_plan, song_map, normalized,
                             "No solo notes exist in the selected window",
                             profile=profile, evidence=evidence)
    phrases = _phrase_refs(song_map, normalized, records)
    layers = _ornament_layers(records, song_map, evidence, profile, normalized, production)
    layers.append(_third_layer(records, song_map, evidence, profile, normalized, production))
    echo, allocation = _echo_layer(midi, records, song_map, evidence, profile, normalized, production)
    layers.append(echo)
    cc11 = _cc11_plan(midi, records, song_map, profile, normalized)
    budgets = _variant_budgets(groove_plan, layers)
    enabled = budgets[0]["enabledEventIds"] if budgets else []
    cc11_ids = [item["pointId"] for item in cc11["points"]]
    previews = [
        {"previewId": "A", "label": "ORIGINAL_SOLO", "sourceVariantId": None,
         "eventIds": [], "cc11PointIds": [], "originalFingerprintHash": fingerprint.sha256,
         "previewHash": ""},
        {"previewId": "B", "label": "AI_EXPRESSION_LAYER", "sourceVariantId": budgets[0]["variantId"],
         "eventIds": list(enabled), "cc11PointIds": cc11_ids,
         "originalFingerprintHash": fingerprint.sha256, "previewHash": ""},
    ]
    for item in previews:
        item["previewHash"] = _hash_without(item, "previewHash")
    all_events = _expression_events(layers)
    removal = {"operation": "REMOVE_AI_EXPRESSION_LAYER", "removesOnlyAiLayers": True,
               "preservesOriginalFingerprint": True,
               "eventIds": sorted(item["eventId"] for item in all_events), "operationHash": ""}
    removal["operationHash"] = _hash_without(removal, "operationHash")
    manual_review = []
    if any(item["decision"] == "MANUAL_REVIEW" for item in layers):
        manual_review.append({"code": "LAYER_ROUTING_REVIEW", "reason": "One or more AI layers could not be routed safely"})
    if any(not item["withinMidiNoteCeiling"] for item in budgets):
        manual_review.append({"code": "POLYPHONY_REVIEW", "reason": "Expression layers exceed the GroovePlan 54-note budget"})
    production_blocks = [] if production else ["PRODUCTION_ORNAMENT_EVIDENCE_REQUIRED"]
    generated_count = len(all_events)
    audit = {"originalNoteCount": len(records), "generatedNoteCount": generated_count,
             "cc11PointCount": len(cc11["points"]),
             "sourceNoteUidCoverage": all(item["sourceNoteUid"] for item in all_events + cc11["points"]),
             "evidenceCoverage": all(item["evidenceId"] for item in all_events + cc11["points"]),
             "reasonCodeCoverage": all(item["reasonCode"] for item in all_events + cc11["points"]),
             "maximumEstimatedPeak": max((item["estimatedPeak"] for item in budgets), default=0),
             "allVariantsWithinMidiNoteCeiling": all(item["withinMidiNoteCeiling"] for item in budgets)}
    result = {
        "schema": EXPRESSION_PLAN_SCHEMA, "version": EXPRESSION_PLAN_VERSION,
        "source": {"inputMidiSha256": midi.digest(), "songMapHash": song_map["mapHash"],
                   "groovePlanHash": groove_plan["groovePlanHash"], "trackUid": normalized.track_uid,
                   "trackNumber": normalized.track_number, "channelNumber": normalized.channel_number,
                   "originalSoloFingerprint": fingerprint.to_manifest()},
        "controls": normalized.to_manifest(), "factoryProfile": profile,
        "evidence": {"authority": evidence["authority"], "evidenceHash": evidence["evidenceHash"],
                     "productionEligible": production},
        "phrases": phrases, "originalNotes": records, "layers": layers,
        "cc11": cc11, "delayAllocation": allocation, "variantBudgets": budgets,
        "previews": previews, "removalOperation": removal, "audit": audit,
        "manualReview": manual_review, "productionBlocks": production_blocks,
        "readyForPreview": not manual_review,
        "readyForProductionRender": not manual_review and not production_blocks,
        "safety": {"readOnly": True, "midiMutationAllowed": False, "finalMidiGenerated": False,
                   "originalSoloUnchanged": True, "soundBindingUnchanged": True,
                   "factoryDynamicsOnly": True, "goldDynamicsAuthority": False,
                   "nonRecursiveEcho": True, "aiLayersRemovable": True,
                   "softwareMidiNoteCeiling": MIDI_NOTE_CEILING},
        "expressionPlanHash": "",
    }
    result["expressionPlanHash"] = _hash_without(result, "expressionPlanHash")
    validate_expression_plan_v2(result)
    return result


def remove_ai_expression_layer(plan: Mapping[str, Any]) -> dict[str, Any]:
    validate_expression_plan_v2(plan)
    result = {"operation": "REMOVE_AI_EXPRESSION_LAYER",
              "sourceExpressionPlanHash": plan["expressionPlanHash"],
              "removedEventIds": list(plan["removalOperation"]["eventIds"]),
              "removedCc11PointIds": [item["pointId"] for item in plan["cc11"]["points"]],
              "originalSoloFingerprint": deepcopy(plan["source"]["originalSoloFingerprint"]),
              "originalNotesPreserved": True, "soundBindingPreserved": True,
              "resultPreviewId": "A", "removalHash": ""}
    result["removalHash"] = _hash_without(result, "removalHash")
    return result


def validate_expression_plan_v2(value: Mapping[str, Any]) -> None:
    root = {"schema", "version", "source", "controls", "factoryProfile", "evidence",
            "phrases", "originalNotes", "layers", "cc11", "delayAllocation",
            "variantBudgets", "previews", "removalOperation", "audit", "manualReview",
            "productionBlocks", "readyForPreview", "readyForProductionRender", "safety",
            "expressionPlanHash"}
    if not isinstance(value, Mapping) or set(value) != root:
        raise ValueError("ExpressionPlan 2.0 root fields mismatch")
    if value["schema"] != EXPRESSION_PLAN_SCHEMA or value["version"] != EXPRESSION_PLAN_VERSION:
        raise ValueError("Unsupported ExpressionPlan schema/version")
    source = value["source"]
    if set(source) != {"inputMidiSha256", "songMapHash", "groovePlanHash", "trackUid",
                      "trackNumber", "channelNumber", "originalSoloFingerprint"}:
        raise ValueError("ExpressionPlan source fields mismatch")
    if any(not _HEX64.fullmatch(str(source[key])) for key in ("inputMidiSha256", "songMapHash", "groovePlanHash")):
        raise ValueError("ExpressionPlan source hash is invalid")
    ExpressionControls.from_mapping(value["controls"])
    if value["factoryProfile"] is not None:
        profile = value["factoryProfile"]
        if profile["profileHash"] != _hash_without(profile, "profileHash") or profile["authority"] != "FACTORY_ONLY":
            raise ValueError("ExpressionPlan Factory profile hash/authority is invalid")
    note_uids = {item["sourceNoteUid"] for item in value["originalNotes"]}
    if len(note_uids) != len(value["originalNotes"]) or any(item["immutable"] is not True for item in value["originalNotes"]):
        raise ValueError("ExpressionPlan original note identities are invalid")
    event_ids = set()
    for layer in value["layers"]:
        if layer["layerHash"] != _hash_without(layer, "layerHash") or layer["removable"] is not True:
            raise ValueError("Expression layer hash/removal contract is invalid")
        for event in layer["events"]:
            if event["eventHash"] != _hash_without(event, "eventHash"):
                raise ValueError("Expression layer event hash mismatch")
            if event["sourceNoteUid"] not in note_uids or not _STABLE_ID.fullmatch(str(event["evidenceId"])):
                raise ValueError("Expression layer event source/evidence is invalid")
            if not event["reasonCode"] or event["durationTick"] < 1 or not 0 <= event["pitch"] <= 127:
                raise ValueError("Expression layer event content is invalid")
            if event["eventId"] in event_ids:
                raise ValueError("Expression layer event IDs are not unique")
            event_ids.add(event["eventId"])
    cc11 = value["cc11"]
    if cc11["planHash"] != _hash_without(cc11, "planHash"):
        if not (not value["readyForPreview"] and not cc11["points"]):
            raise ValueError("Expression CC11 plan hash mismatch")
    for point in cc11["points"]:
        if (point["pointHash"] != _hash_without(point, "pointHash")
                or point["sourceNoteUid"] not in note_uids or point["controller"] != 11
                or not _STABLE_ID.fullmatch(str(point["evidenceId"]))):
            raise ValueError("Expression CC11 point is invalid")
        if cc11["bounds"] is not None and not cc11["bounds"][0] <= point["value"] <= cc11["bounds"][1]:
            raise ValueError("Expression CC11 point exceeds Factory bounds")
    for budget in value["variantBudgets"]:
        if budget["budgetHash"] != _hash_without(budget, "budgetHash"):
            raise ValueError("Expression variant budget hash mismatch")
        if budget["withinMidiNoteCeiling"] != (budget["estimatedPeak"] <= MIDI_NOTE_CEILING):
            raise ValueError("Expression variant budget decision is inconsistent")
    for preview in value["previews"]:
        if preview["previewHash"] != _hash_without(preview, "previewHash"):
            raise ValueError("Expression preview hash mismatch")
        if preview["originalFingerprintHash"] != source["originalSoloFingerprint"]["sha256"]:
            raise ValueError("Expression preview changed the original fingerprint")
    removal = value["removalOperation"]
    if (removal["operationHash"] != _hash_without(removal, "operationHash")
            or removal["removesOnlyAiLayers"] is not True
            or removal["preservesOriginalFingerprint"] is not True
            or set(removal["eventIds"]) != event_ids):
        raise ValueError("Expression AI-layer removal contract is invalid")
    safety = value["safety"]
    expected = {"readOnly": True, "midiMutationAllowed": False, "finalMidiGenerated": False,
                "originalSoloUnchanged": True, "soundBindingUnchanged": True,
                "factoryDynamicsOnly": True, "goldDynamicsAuthority": False,
                "nonRecursiveEcho": True, "aiLayersRemovable": True,
                "softwareMidiNoteCeiling": MIDI_NOTE_CEILING}
    if safety != expected:
        raise ValueError("ExpressionPlan safety contract was weakened")
    if value["readyForPreview"] != (not value["manualReview"]):
        raise ValueError("ExpressionPlan preview readiness is inconsistent")
    if value["readyForProductionRender"] != (value["readyForPreview"] and not value["productionBlocks"]):
        raise ValueError("ExpressionPlan production readiness is inconsistent")
    if value["audit"]["sourceNoteUidCoverage"] is not True or value["audit"]["evidenceCoverage"] is not True or value["audit"]["reasonCodeCoverage"] is not True:
        raise ValueError("ExpressionPlan event audit coverage is incomplete")
    if value["audit"]["maximumEstimatedPeak"] > MIDI_NOTE_CEILING and value["readyForPreview"]:
        raise ValueError("ExpressionPlan exceeds the software MIDI-note ceiling")
    if value["expressionPlanHash"] != _hash_without(value, "expressionPlanHash"):
        raise ValueError("ExpressionPlan hash mismatch")


def execute_expression_plan_api(payload: Mapping[str, Any], root: Path) -> dict[str, Any]:
    allowed = {"midiHex", "groovePlan", "songMap", "controls", "evidence"}
    if not isinstance(payload, Mapping) or set(payload) - allowed:
        raise ValueError("Unknown ExpressionPlan API fields")
    if not {"midiHex", "groovePlan", "songMap", "controls"} <= set(payload):
        raise ValueError("ExpressionPlan API requires midiHex, groovePlan, songMap and controls")
    try:
        midi = MidiFile.from_bytes(bytes.fromhex(str(payload["midiHex"])))
    except ValueError as error:
        raise ValueError("midiHex is not valid hexadecimal MIDI data") from error
    return build_expression_plan(midi, payload["groovePlan"], payload["songMap"], root,
                                 payload["controls"], payload.get("evidence"))


def execute_expression_plan_gui(payload: Mapping[str, Any], root: Path) -> dict[str, Any]:
    return execute_expression_plan_api(payload, root)