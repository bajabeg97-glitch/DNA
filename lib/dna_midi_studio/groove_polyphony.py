"""Session 23 deterministic GroovePlan and polyphony safety engine.

The engine consumes a validated CandidateSet 2.0 and expands selected
production patterns into a read-only event timing plan.  GOLD contributes
only onset/gate relationships.  No velocity, Bank Select, Program Change,
SoundBinding, original-solo, or final MIDI authority is granted here.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from .arrangement_graph import MAX_CONCURRENT_MIDI_NOTES, validate_arrangement_graph_v2
from .candidate_search import validate_candidate_set_v2
from .producer_brief import ELEMENTS, ROLES
from .production_adapter import ProductionAdapter
from .song_understanding import validate_song_map_v2


GROOVE_PLAN_SCHEMA = "dna-premium-groove-plan"
GROOVE_PLAN_VERSION = "2.0"
GROOVE_CONTROL_VERSION = "1.0"
MIDI_NOTE_CEILING = MAX_CONCURRENT_MIDI_NOTES

ROLE_POLICIES: dict[str, dict[str, Any]] = {
    "bass": {"channelNumber": 9, "logicalTrack": "Bass", "microtimingLimitTicks": 8,
             "gateVariationLimitPercent": 4, "priorityTier": "CORE", "preservePriority": 95},
    "drums": {"channelNumber": 10, "logicalTrack": "Drum", "microtimingLimitTicks": 6,
              "gateVariationLimitPercent": 2, "priorityTier": "CORE", "preservePriority": 100},
    "percussion": {"channelNumber": 11, "logicalTrack": "Percussion", "microtimingLimitTicks": 10,
                   "gateVariationLimitPercent": 5, "priorityTier": "DECORATIVE", "preservePriority": 20},
    "guitar": {"channelNumber": 12, "logicalTrack": "Acc1", "microtimingLimitTicks": 12,
               "gateVariationLimitPercent": 8, "priorityTier": "SUPPORT", "preservePriority": 75},
    "accompaniment": {"channelNumber": 13, "logicalTrack": "Acc2", "microtimingLimitTicks": 8,
                      "gateVariationLimitPercent": 6, "priorityTier": "SUPPORT", "preservePriority": 70},
    "riff": {"channelNumber": 14, "logicalTrack": "Acc3", "microtimingLimitTicks": 7,
             "gateVariationLimitPercent": 5, "priorityTier": "DECORATIVE", "preservePriority": 35},
    "pad": {"channelNumber": 15, "logicalTrack": "Acc4", "microtimingLimitTicks": 3,
            "gateVariationLimitPercent": 2, "priorityTier": "DECORATIVE", "preservePriority": 30},
    "solo": {"channelNumber": 16, "logicalTrack": "Acc5", "microtimingLimitTicks": 0,
             "gateVariationLimitPercent": 0, "priorityTier": "PROTECTED", "preservePriority": 110},
}

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_OUTPUT_KEYS = {
    "velocity", "velocities", "bank", "bankmsb", "banklsb", "program",
    "programchange", "instrumentkey", "factoryprofileid", "factoryprofileids",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return sha256(_canonical({key: item for key, item in value.items() if key != field})).hexdigest()


def _stable_int(parts: Iterable[Any]) -> int:
    return int(sha256(":".join(str(item) for item in parts).encode("utf-8")).hexdigest()[:16], 16)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _forbidden_paths(value: Any, prefix: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("_", "")
            path = f"{prefix}.{key}"
            if normalized in _FORBIDDEN_OUTPUT_KEYS:
                found.append(path)
            found.extend(_forbidden_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_forbidden_paths(item, f"{prefix}[{index}]"))
    return found


@dataclass(frozen=True)
class GrooveControls:
    version: str = GROOVE_CONTROL_VERSION
    strength: int = 65
    gate_strength: int = 55
    simplification_policy: str = "AUTO_SAFE"
    software_midi_note_ceiling: int = MIDI_NOTE_CEILING

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "GrooveControls":
        raw = {} if raw is None else raw
        if not isinstance(raw, Mapping):
            raise ValueError("Groove controls must be an object")
        allowed = {"version", "strength", "gateStrength", "simplificationPolicy",
                   "softwareMidiNoteCeiling"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError("Unknown Groove controls: " + ", ".join(sorted(unknown)))
        if raw.get("version", GROOVE_CONTROL_VERSION) != GROOVE_CONTROL_VERSION:
            raise ValueError("Groove controls require version 1.0")
        strength = raw.get("strength", 65)
        gate_strength = raw.get("gateStrength", 55)
        for name, value in (("strength", strength), ("gateStrength", gate_strength)):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                raise ValueError(f"{name} must be an integer in range 0..100")
        policy = raw.get("simplificationPolicy", "AUTO_SAFE")
        if policy not in {"AUTO_SAFE", "MANUAL_REVIEW"}:
            raise ValueError("simplificationPolicy must be AUTO_SAFE or MANUAL_REVIEW")
        ceiling = raw.get("softwareMidiNoteCeiling", MIDI_NOTE_CEILING)
        if ceiling != MIDI_NOTE_CEILING:
            raise ValueError(f"softwareMidiNoteCeiling is fixed at {MIDI_NOTE_CEILING}")
        return cls(GROOVE_CONTROL_VERSION, strength, gate_strength, policy, ceiling)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "strength": self.strength,
            "gateStrength": self.gate_strength,
            "simplificationPolicy": self.simplification_policy,
            "softwareMidiNoteCeiling": self.software_midi_note_ceiling,
        }


def _meter_at_tick(song_map: Mapping[str, Any], tick: int) -> Mapping[str, Any]:
    eligible = [item for item in song_map["meterMap"] if int(item["tick"]) <= tick]
    return max(eligible, key=lambda item: int(item["tick"])) if eligible else song_map["meterMap"][0]


def _node_meter(song_map: Mapping[str, Any], node: Mapping[str, Any]) -> tuple[int, int]:
    section = next(item for item in song_map["sections"] if item["id"] == node["sourceSectionId"])
    meter = _meter_at_tick(song_map, int(section["startTick"]))
    return int(meter["numerator"]), int(meter["denominator"])


def _bar_ticks(ppq: int, numerator: int, denominator: int) -> int:
    return max(1, round(ppq * 4 * numerator / denominator))


def _pattern_events(pattern: Mapping[str, Any]) -> list[Sequence[Any]]:
    events = pattern.get("events", pattern.get("notes", []))
    return [item for item in events if isinstance(item, (list, tuple)) and len(item) >= 3]


def _source_timing_hash(pattern: Mapping[str, Any]) -> str:
    timing = [[int(item[0]), int(item[1]), int(item[2])] for item in _pattern_events(pattern)]
    return sha256(_canonical(timing)).hexdigest()


def _signed_jitter(parts: Iterable[Any], maximum: int) -> int:
    if maximum <= 0:
        return 0
    return (_stable_int(parts) % (maximum * 2 + 1)) - maximum


def _intentional_feel(offsets: Sequence[int]) -> str:
    nonzero = [item for item in offsets if item]
    if not nonzero:
        return "STRAIGHT"
    center = float(median(nonzero))
    if center <= -1:
        return "AHEAD"
    if center >= 1:
        return "LAID_BACK"
    return "MIXED"


def _build_template(pattern: Mapping[str, Any], pattern_id: str, source_kind: str,
                    role: str, ppq: int, controls: GrooveControls) -> dict[str, Any]:
    resolution = max(1, int(pattern.get("timingResolution", 96)))
    grid = max(1, round(resolution / 4))
    policy = ROLE_POLICIES[role]
    limit = int(policy["microtimingLimitTicks"])
    by_step: dict[int, list[int]] = defaultdict(list)
    source_offsets = []
    for event in _pattern_events(pattern):
        onset = int(event[0])
        nearest = round(onset / grid) * grid
        source_offset = round((onset - nearest) * ppq / resolution)
        bounded = _clamp(source_offset, -limit, limit)
        step = int(round(onset / grid)) % max(1, round(16 * float(pattern.get("lengthBars", 1))))
        by_step[step].append(bounded)
        source_offsets.append(bounded)
    steps = []
    for step, values in sorted(by_step.items()):
        source_offset = int(round(median(values)))
        applied = int(round(source_offset * controls.strength / 100))
        steps.append({
            "stepIndex": step, "sampleCount": len(values),
            "medianSourceOffsetTicks": source_offset,
            "appliedOffsetTicks": _clamp(applied, -limit, limit),
        })
    template = {
        "templateId": "groove-" + sha256(
            f"{pattern_id}:{source_kind}:{role}:{ppq}:{controls.strength}".encode("ascii")
        ).hexdigest()[:20],
        "patternId": pattern_id, "sourceKind": source_kind, "role": role,
        "sourceTimingResolution": resolution, "targetPpq": ppq,
        "gridDivision": "1/16", "microtimingLimitTicks": limit,
        "gateVariationLimitPercent": int(policy["gateVariationLimitPercent"]),
        "intentionalFeel": _intentional_feel(source_offsets),
        "timingAuthority": "TIMING_AND_GATE_ONLY" if source_kind == "GOLD_PERFORMANCE"
                           else "FACTORY_PERFORMANCE_TIMING",
        "steps": steps, "templateHash": "",
    }
    template["templateHash"] = _hash_without(template, "templateHash")
    return template


def _template_offset(template: Mapping[str, Any], source_onset: int) -> int:
    resolution = int(template["sourceTimingResolution"])
    grid = max(1, round(resolution / 4))
    step_count = max((int(item["stepIndex"]) for item in template["steps"]), default=0) + 1
    step = int(round(source_onset / grid)) % max(1, step_count)
    exact = next((item for item in template["steps"] if item["stepIndex"] == step), None)
    if exact is not None:
        return int(exact["appliedOffsetTicks"])
    nearest = min(template["steps"], key=lambda item: abs(int(item["stepIndex"]) - step), default=None)
    return int(nearest["appliedOffsetTicks"]) if nearest else 0


def _expand_fragment(pattern: Mapping[str, Any], selection: Mapping[str, Any],
                     node: Mapping[str, Any], song_map: Mapping[str, Any], seed: int,
                     variant_id: str, controls: GrooveControls,
                     template: Mapping[str, Any]) -> dict[str, Any]:
    role = str(selection["role"])
    policy = ROLE_POLICIES[role]
    ppq = int(song_map["ppq"])
    numerator, denominator = _node_meter(song_map, node)
    marker_ticks = _bar_ticks(ppq, numerator, denominator) * int(node["bars"])
    source_resolution = max(1, int(pattern.get("timingResolution", 96)))
    source_bar_ticks = _bar_ticks(source_resolution, numerator, denominator)
    source_length = max(1, round(float(pattern.get("lengthBars", 1)) * source_bar_ticks))
    scale = ppq / source_resolution
    locked = bool(selection["locked"] or node["locked"])
    events = []
    cycle = 0
    cycle_start = 0
    source_events = _pattern_events(pattern)
    while cycle_start < marker_ticks:
        for source_index, item in enumerate(source_events):
            source_onset = int(item[0])
            source_duration = max(1, int(item[1]))
            original_onset = cycle_start + round(source_onset * scale)
            if original_onset >= marker_ticks:
                continue
            original_duration = max(1, round(source_duration * scale))
            original_duration = min(original_duration, marker_ticks - original_onset)
            timing_offset = 0
            gate_delta = 0
            if not locked:
                base_offset = _template_offset(template, source_onset)
                jitter_limit = max(0, round(int(policy["microtimingLimitTicks"])
                                            * controls.strength / 400))
                jitter = _signed_jitter((seed, variant_id, selection["requestId"], cycle,
                                         source_index, "timing"), jitter_limit)
                if base_offset > 0:
                    jitter = max(0, jitter)
                elif base_offset < 0:
                    jitter = min(0, jitter)
                timing_offset = _clamp(base_offset + jitter,
                                       -int(policy["microtimingLimitTicks"]),
                                       int(policy["microtimingLimitTicks"]))
                max_gate_percent = int(policy["gateVariationLimitPercent"])
                gate_percent = _signed_jitter((seed, variant_id, selection["requestId"], cycle,
                                                source_index, "gate"), max_gate_percent)
                gate_percent = round(gate_percent * controls.gate_strength / 100)
                gate_delta = round(original_duration * gate_percent / 100)
            onset = _clamp(original_onset + timing_offset, cycle_start,
                           max(cycle_start, marker_ticks - original_duration))
            duration = _clamp(original_duration + gate_delta, 1, marker_ticks - onset)
            event = {
                "eventId": "evt-" + sha256(
                    f"{variant_id}:{selection['requestId']}:{cycle}:{source_index}".encode("utf-8")
                ).hexdigest()[:20],
                "sourceEventIndex": source_index, "cycleIndex": cycle,
                "originalOnsetTick": original_onset, "originalDurationTick": original_duration,
                "onsetTick": onset, "durationTick": duration,
                "timingOffsetTick": onset - original_onset,
                "gateDeltaTick": duration - original_duration,
                "pitchToken": int(item[2]), "enabled": True,
                "priorityTier": str(policy["priorityTier"]),
                "preservePriority": int(policy["preservePriority"]), "eventHash": "",
            }
            event["eventHash"] = _hash_without(event, "eventHash")
            events.append(event)
        cycle += 1
        cycle_start = round(cycle * source_length * scale)
    fragment = {
        "requestId": selection["requestId"], "marker": selection["marker"], "role": role,
        "logicalTrack": policy["logicalTrack"], "channelNumber": policy["channelNumber"],
        "sourceKind": selection["sourceKind"], "patternId": selection["patternId"],
        "pitchMode": str(pattern.get("pitchMode", "unknown")),
        "sourceSelectionFragmentHash": selection["fragmentHash"],
        "sourceTimingHash": _source_timing_hash(pattern),
        "templateId": template["templateId"], "locked": locked,
        "humanizationApplied": not locked and (controls.strength > 0 or controls.gate_strength > 0),
        "status": "PRESERVED_LOCKED" if locked else "GROOVE_PLANNED",
        "markerLengthTicks": marker_ticks, "events": events, "fragmentHash": "",
    }
    fragment["fragmentHash"] = _hash_without(fragment, "fragmentHash")
    return fragment


def _keep_fragment(selection: Mapping[str, Any], node: Mapping[str, Any]) -> dict[str, Any]:
    policy = ROLE_POLICIES[selection["role"]]
    fragment = {
        "requestId": selection["requestId"], "marker": selection["marker"],
        "role": selection["role"], "logicalTrack": policy["logicalTrack"],
        "channelNumber": policy["channelNumber"], "sourceKind": None, "patternId": None,
        "pitchMode": None, "sourceSelectionFragmentHash": selection["fragmentHash"],
        "sourceTimingHash": None, "templateId": None, "locked": True,
        "humanizationApplied": False, "status": "KEEP_ORIGINAL",
        "markerLengthTicks": 0, "events": [], "fragmentHash": "",
    }
    fragment["fragmentHash"] = _hash_without(fragment, "fragmentHash")
    return fragment


def _event_window(event: Mapping[str, Any], sustain_windows: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    start = int(event["onsetTick"])
    end = start + int(event["durationTick"])
    for window in sustain_windows:
        if (str(window["marker"]) == str(event.get("marker"))
                and int(window["channelNumber"]) == int(event.get("channelNumber", 0))
                and start < int(window["endTick"])
                and int(window["startTick"]) <= end < int(window["endTick"])):
            end = int(window["endTick"])
    return start, end


def analyze_full_duration_polyphony(events: Sequence[Mapping[str, Any]],
                                    sustain_windows: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Measure active note intervals; note-off sorts before note-on at a shared tick."""
    active_events = [item for item in events if item.get("enabled", True)]
    sweep: list[tuple[int, int]] = []
    longest = 0
    for event in active_events:
        start, end = _event_window(event, sustain_windows)
        if end <= start:
            continue
        sweep.extend(((start, 1), (end, -1)))
        longest = max(longest, end - start)
    active = peak = 0
    peak_tick = 0
    for tick, delta in sorted(sweep, key=lambda item: (item[0], item[1])):
        active += delta
        if active > peak:
            peak, peak_tick = active, tick
    result = {
        "noteCount": len(active_events), "peak": peak, "peakTick": peak_tick,
        "longestDurationTick": longest, "sustainWindowCount": len(sustain_windows),
        "method": "full-note-duration-sweep-sustain-aware", "analysisHash": "",
    }
    result["analysisHash"] = _hash_without(result, "analysisHash")
    return result


def _active_at(events: Sequence[Mapping[str, Any]], tick: int) -> list[Mapping[str, Any]]:
    return [item for item in events if item.get("enabled", True)
            and int(item["onsetTick"]) <= tick < int(item["onsetTick"]) + int(item["durationTick"])]


def simplify_event_plan(events: Sequence[Mapping[str, Any]], ceiling: int = MIDI_NOTE_CEILING,
                        policy: str = "AUTO_SAFE") -> dict[str, Any]:
    """Deterministically remove decorative layers, then thin support voices."""
    if isinstance(ceiling, bool) or not isinstance(ceiling, int) or not 1 <= ceiling <= MIDI_NOTE_CEILING:
        raise ValueError(f"Polyphony ceiling must be in range 1..{MIDI_NOTE_CEILING}")
    if policy not in {"AUTO_SAFE", "MANUAL_REVIEW"}:
        raise ValueError("Unknown simplification policy")
    working = deepcopy(list(events))
    before = analyze_full_duration_polyphony(working)
    operations: list[dict[str, Any]] = []
    if before["peak"] <= ceiling or policy == "MANUAL_REVIEW":
        return {"events": working, "operations": operations, "before": before,
                "after": before, "blocked": before["peak"] > ceiling}

    decorative = sorted({(str(item.get("marker")), str(item.get("requestId")),
                           int(item.get("preservePriority", 999)))
                          for item in working if item.get("enabled", True)
                          and item.get("priorityTier") == "DECORATIVE"
                          and not item.get("locked", False)}, key=lambda item: (item[2], item[0], item[1]))
    for marker, request_id, _ in decorative:
        if analyze_full_duration_polyphony(working)["peak"] <= ceiling:
            break
        affected = [item for item in working if item.get("enabled", True)
                    and item.get("marker") == marker and item.get("requestId") == request_id]
        if not affected:
            continue
        for item in affected:
            item["enabled"] = False
            item["eventHash"] = _hash_without(item, "eventHash")
        operations.append({"operation": "DROP_DECORATIVE_LAYER", "marker": marker,
                           "requestId": request_id, "eventCount": len(affected),
                           "reason": "MIDI_NOTE_CEILING"})

    guard = 0
    while analyze_full_duration_polyphony(working)["peak"] > ceiling and guard < len(working):
        guard += 1
        peak_tick = analyze_full_duration_polyphony(working)["peakTick"]
        removable = [item for item in _active_at(working, peak_tick)
                     if item.get("priorityTier") == "SUPPORT" and not item.get("locked", False)]
        if not removable:
            break
        victim = sorted(removable, key=lambda item: (
            int(item.get("preservePriority", 999)), -int(item.get("pitchToken", 0)),
            str(item.get("requestId")), str(item.get("eventId"))))[0]
        victim["enabled"] = False
        victim["eventHash"] = _hash_without(victim, "eventHash")
        operations.append({"operation": "THIN_SUPPORT_VOICE", "marker": str(victim.get("marker")),
                           "requestId": str(victim.get("requestId")), "eventCount": 1,
                           "reason": "MIDI_NOTE_CEILING"})
    after = analyze_full_duration_polyphony(working)
    return {"events": working, "operations": operations, "before": before,
            "after": after, "blocked": after["peak"] > ceiling}


def _annotated_events(fragments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for fragment in fragments:
        for event in fragment["events"]:
            item = deepcopy(event)
            item.update({"marker": fragment["marker"], "role": fragment["role"],
                         "requestId": fragment["requestId"],
                         "logicalTrack": fragment["logicalTrack"],
                         "channelNumber": fragment["channelNumber"],
                         "locked": fragment["locked"]})
            output.append(item)
    return output


def _polyphony_report(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sections = []
    for marker in ELEMENTS:
        section_events = [item for item in events if item["marker"] == marker]
        measured = analyze_full_duration_polyphony(section_events)
        sections.append({"marker": marker, "peak": measured["peak"],
                         "peakTick": measured["peakTick"], "noteCount": measured["noteCount"]})
    tracks = []
    for role in ROLES:
        role_events = [item for item in events if item["role"] == role]
        peaks = [(section["marker"], analyze_full_duration_polyphony(
            [item for item in role_events if item["marker"] == section["marker"]]
        )) for section in sections]
        marker, measured = max(peaks, key=lambda item: (item[1]["peak"], -ELEMENTS.index(item[0])))
        policy = ROLE_POLICIES[role]
        tracks.append({"logicalTrack": policy["logicalTrack"], "role": role,
                       "channelNumber": policy["channelNumber"], "peak": measured["peak"],
                       "peakMarker": marker, "noteCount": sum(item.get("enabled", True) for item in role_events)})
    channels = [{"channelNumber": item["channelNumber"], "logicalTrack": item["logicalTrack"],
                 "role": item["role"], "peak": item["peak"], "peakMarker": item["peakMarker"]}
                for item in tracks]
    peak_section = max(sections, key=lambda item: (item["peak"], -ELEMENTS.index(item["marker"])))
    report = {
        "softwareMidiNoteCeiling": MIDI_NOTE_CEILING,
        "globalPeak": peak_section["peak"], "globalPeakMarker": peak_section["marker"],
        "withinSoftwareCeiling": peak_section["peak"] <= MIDI_NOTE_CEILING,
        "method": "full-note-duration-per-style-section",
        "tracks": tracks, "channels": channels, "sections": sections,
        "reportHash": "",
    }
    report["reportHash"] = _hash_without(report, "reportHash")
    return report


def _device_voice_cost(polyphony: Mapping[str, Any], device_profile: Mapping[str, Any] | None) -> dict[str, Any]:
    if device_profile is None:
        return {"status": "UNCONFIRMED", "profileHash": None, "estimatedPeakUnits": None,
                "ceilingUnits": None, "overflow": None,
                "decision": "PHYSICAL_DEVICE_PROFILE_REQUIRED"}
    if not isinstance(device_profile, Mapping):
        raise ValueError("DeviceProfile must be an object")
    required = {"schema", "version", "manufacturer", "model", "status",
                "voiceCostModel", "certificationEvidenceHash", "profileHash"}
    if set(device_profile) != required:
        raise ValueError("DeviceProfile voice-cost fields mismatch")
    if (device_profile["schema"] != "dna-premium-device-profile"
            or device_profile["version"] != "2.0" or device_profile["manufacturer"] != "Korg"
            or device_profile["model"] != "Pa800"):
        raise ValueError("Unsupported DeviceProfile for voice-cost analysis")
    if device_profile["profileHash"] != _hash_without(device_profile, "profileHash"):
        raise ValueError("DeviceProfile hash mismatch")
    if device_profile["status"] != "PA800_DEVICE_CERTIFIED":
        return {"status": "UNCONFIRMED", "profileHash": device_profile["profileHash"],
                "estimatedPeakUnits": None, "ceilingUnits": None, "overflow": None,
                "decision": "PHYSICAL_DEVICE_PROFILE_REQUIRED"}
    model = device_profile["voiceCostModel"]
    if set(model) != {"measured", "ceilingUnits", "roleUnits"} or model["measured"] is not True:
        raise ValueError("Certified DeviceProfile requires a measured voice-cost model")
    if set(model["roleUnits"]) != set(ROLES):
        raise ValueError("DeviceProfile voice-cost role map is incomplete")
    if (not _HEX64.fullmatch(str(device_profile["certificationEvidenceHash"]))
            or not isinstance(model["ceilingUnits"], (int, float))
            or isinstance(model["ceilingUnits"], bool) or model["ceilingUnits"] <= 0
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
                   for value in model["roleUnits"].values())):
        raise ValueError("DeviceProfile voice-cost evidence/model is invalid")
    track_by_role = {item["role"]: item["peak"] for item in polyphony["tracks"]}
    estimated = round(sum(track_by_role[role] * float(model["roleUnits"][role]) for role in ROLES), 4)
    ceiling = float(model["ceilingUnits"])
    overflow = estimated > ceiling
    return {"status": "CONFIRMED_MODEL", "profileHash": device_profile["profileHash"],
            "estimatedPeakUnits": estimated, "ceilingUnits": ceiling, "overflow": overflow,
            "decision": "SIMPLIFICATION_REQUIRED" if overflow else "WITHIN_CONFIRMED_DEVICE_BUDGET"}


def _apply_simplification(fragments: list[dict[str, Any]], controls: GrooveControls) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any], bool]:
    annotated = _annotated_events(fragments)
    before = _polyphony_report(annotated)
    operations: list[dict[str, Any]] = []
    blocked = False
    enabled_by_id = {item["eventId"]: item["enabled"] for item in annotated}
    for marker in ELEMENTS:
        marker_events = [item for item in annotated if item["marker"] == marker]
        simplified = simplify_event_plan(marker_events, controls.software_midi_note_ceiling,
                                         controls.simplification_policy)
        enabled_by_id.update({item["eventId"]: item["enabled"] for item in simplified["events"]})
        operations.extend(simplified["operations"])
        blocked = blocked or bool(simplified["blocked"])
    for fragment in fragments:
        changed = False
        for event in fragment["events"]:
            enabled = enabled_by_id[event["eventId"]]
            if event["enabled"] != enabled:
                event["enabled"] = enabled
                event["eventHash"] = _hash_without(event, "eventHash")
                changed = True
        if changed:
            fragment["status"] = "SIMPLIFIED_FOR_POLYPHONY"
        fragment["fragmentHash"] = _hash_without(fragment, "fragmentHash")
    after = _polyphony_report(_annotated_events(fragments))
    return fragments, operations, before, after, blocked


def build_groove_plan(candidate_set: Mapping[str, Any], arrangement_graph: Mapping[str, Any],
                      song_map: Mapping[str, Any], root: Path, seed: int = 2300,
                      controls: Mapping[str, Any] | None = None,
                      device_profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    validate_candidate_set_v2(candidate_set)
    validate_arrangement_graph_v2(arrangement_graph)
    validate_song_map_v2(song_map)
    if candidate_set["source"]["graphHash"] != arrangement_graph["graphHash"]:
        raise ValueError("CandidateSet and ArrangementGraph hashes do not match")
    if candidate_set["source"]["songMapHash"] != song_map["mapHash"]:
        raise ValueError("CandidateSet and SongMap hashes do not match")
    if candidate_set["readyForVariantRendering"] is not True:
        raise ValueError("CandidateSet manual review must be resolved before GroovePlan")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**31 - 1:
        raise ValueError("GroovePlan seed must be in range 0..2147483647")
    normalized = GrooveControls.from_mapping(controls)
    adapter = ProductionAdapter(Path(root).resolve())
    pattern_maps = {
        "GOLD_PERFORMANCE": {str(item["id"]): item for item in adapter.documents["goldPerformance"]["patterns"]},
        "FACTORY_STRUMMING": {str(item["id"]): item for item in adapter.documents["factoryStrumming"]["patterns"]},
    }
    nodes = {item["marker"]: item for item in arrangement_graph["nodes"]}
    templates: dict[str, dict[str, Any]] = {}
    variants = []
    manual_review = []
    maximum_before = maximum_after = disabled_events = 0
    total_events = 0
    for variant in candidate_set["variants"]:
        fragments: list[dict[str, Any]] = []
        for selection in variant["selections"]:
            node = nodes[selection["marker"]]
            if selection["patternId"] is None:
                fragments.append(_keep_fragment(selection, node))
                continue
            source_kind = str(selection["sourceKind"])
            pattern = pattern_maps.get(source_kind, {}).get(str(selection["patternId"]))
            if pattern is None:
                raise ValueError(f"Selected production pattern is missing: {selection['patternId']}")
            template_key = f"{source_kind}:{selection['patternId']}:{selection['role']}"
            if template_key not in templates:
                templates[template_key] = _build_template(
                    pattern, str(selection["patternId"]), source_kind, str(selection["role"]),
                    int(song_map["ppq"]), normalized,
                )
            fragments.append(_expand_fragment(
                pattern, selection, node, song_map, seed, str(variant["variantId"]), normalized,
                templates[template_key],
            ))
        fragments, operations, before, after, blocked = _apply_simplification(fragments, normalized)
        if blocked:
            manual_review.append({
                "variantId": variant["variantId"], "code": "MIDI_NOTE_POLYPHONY_OVERFLOW",
                "reason": "Safe simplification cannot reach the 54-note software ceiling without core or locked changes.",
                "peak": after["globalPeak"], "ceiling": MIDI_NOTE_CEILING,
            })
        voice = _device_voice_cost(after, device_profile)
        if voice["status"] == "CONFIRMED_MODEL" and voice["overflow"]:
            manual_review.append({
                "variantId": variant["variantId"], "code": "DEVICE_VOICE_COST_OVERFLOW",
                "reason": "Confirmed Pa800 voice-cost model requires an additional approved simplification plan.",
                "peak": voice["estimatedPeakUnits"], "ceiling": voice["ceilingUnits"],
            })
        total_events += sum(len(item["events"]) for item in fragments)
        disabled_events += sum(not event["enabled"] for item in fragments for event in item["events"])
        maximum_before = max(maximum_before, int(before["globalPeak"]))
        maximum_after = max(maximum_after, int(after["globalPeak"]))
        output_variant = {
            "variantId": variant["variantId"], "sourceVariantHash": variant["variantHash"],
            "fragments": fragments, "simplificationOperations": operations,
            "polyphonyBefore": before, "polyphonyAfter": after,
            "deviceVoiceCost": voice, "renderable": not blocked and not bool(voice.get("overflow")),
            "variantHash": "",
        }
        output_variant["variantHash"] = _hash_without(output_variant, "variantHash")
        variants.append(output_variant)
    device_statuses = {item["deviceVoiceCost"]["status"] for item in variants}
    device_summary = {
        "status": next(iter(device_statuses)) if len(device_statuses) == 1 else "MIXED",
        "profileHash": variants[0]["deviceVoiceCost"]["profileHash"] if variants else None,
        "physicalPa800Required": "CONFIRMED_MODEL" not in device_statuses,
    }
    role_policies = [{"role": role, **policy} for role, policy in ROLE_POLICIES.items()]
    channel_assignments = [{"role": role, "logicalTrack": policy["logicalTrack"],
                            "channelNumber": policy["channelNumber"]}
                           for role, policy in ROLE_POLICIES.items()]
    result = {
        "schema": GROOVE_PLAN_SCHEMA, "version": GROOVE_PLAN_VERSION,
        "source": {
            "candidateSetHash": candidate_set["candidateSetHash"],
            "graphHash": arrangement_graph["graphHash"], "songMapHash": song_map["mapHash"],
            "sourceMidiSha256": song_map["sourceSha256"], "seed": seed,
            "variantCount": len(candidate_set["variants"]),
        },
        "registries": deepcopy(candidate_set["registries"]),
        "controls": normalized.to_manifest(), "channelAssignments": channel_assignments,
        "rolePolicies": role_policies,
        "grooveTemplates": sorted(templates.values(), key=lambda item: item["templateId"]),
        "variants": variants,
        "audit": {
            "templateCount": len(templates), "variantCount": len(variants),
            "fragmentCount": sum(len(item["fragments"]) for item in variants),
            "eventCount": total_events, "disabledEventCount": disabled_events,
            "maximumPeakBeforeSimplification": maximum_before,
            "maximumPeakAfterSimplification": maximum_after,
            "allVariantsWithinMidiNoteCeiling": all(
                item["polyphonyAfter"]["withinSoftwareCeiling"] for item in variants),
            "goldTimingOnly": True, "lockedFragmentsPreserved": all(
                not fragment["humanizationApplied"]
                for item in variants for fragment in item["fragments"] if fragment["locked"]),
        },
        "deviceVoiceCost": device_summary,
        "manualReview": manual_review,
        "readyForRenderPlanning": not manual_review and all(item["renderable"] for item in variants),
        "safety": {
            "readOnly": True, "midiMutationAllowed": False, "finalMidiGenerated": False,
            "factoryDynamicsUnchanged": True, "goldDynamicsAuthority": False,
            "goldBankProgramAuthority": False, "soundBindingUnchanged": True,
            "originalSoloUnchanged": True, "lockedFragmentsUnchanged": True,
            "fullDurationPolyphonyMeasured": True,
            "softwareMidiNoteCeiling": MIDI_NOTE_CEILING,
            "deviceVoiceCostConfirmed": "CONFIRMED_MODEL" in device_statuses,
        },
        "groovePlanHash": "",
    }
    if _forbidden_paths(result):
        raise ValueError("GroovePlan contains forbidden dynamics or sound-authority fields")
    result["groovePlanHash"] = _hash_without(result, "groovePlanHash")
    validate_groove_plan_v2(result)
    return result


def validate_groove_plan_v2(value: Mapping[str, Any]) -> None:
    root = {"schema", "version", "source", "registries", "controls", "channelAssignments",
            "rolePolicies", "grooveTemplates", "variants", "audit", "deviceVoiceCost",
            "manualReview", "readyForRenderPlanning", "safety", "groovePlanHash"}
    if not isinstance(value, Mapping) or set(value) != root:
        raise ValueError("GroovePlan 2.0 root fields mismatch")
    if value["schema"] != GROOVE_PLAN_SCHEMA or value["version"] != GROOVE_PLAN_VERSION:
        raise ValueError("Unsupported GroovePlan schema/version")
    source = value["source"]
    if set(source) != {"candidateSetHash", "graphHash", "songMapHash", "sourceMidiSha256",
                       "seed", "variantCount"}:
        raise ValueError("GroovePlan source fields mismatch")
    if any(not _HEX64.fullmatch(str(source[key])) for key in
           ("candidateSetHash", "graphHash", "songMapHash", "sourceMidiSha256")):
        raise ValueError("GroovePlan source hash is invalid")
    if isinstance(source["seed"], bool) or not isinstance(source["seed"], int) or not 0 <= source["seed"] <= 2**31 - 1:
        raise ValueError("GroovePlan source seed is invalid")
    GrooveControls.from_mapping(value["controls"])
    if {item["role"] for item in value["channelAssignments"]} != set(ROLES):
        raise ValueError("GroovePlan channel assignments are incomplete")
    if {item["channelNumber"] for item in value["channelAssignments"]} != set(range(9, 17)):
        raise ValueError("GroovePlan must use Pa800 channels 9..16 exactly once")
    if {item["role"] for item in value["rolePolicies"]} != set(ROLES):
        raise ValueError("GroovePlan role policies are incomplete")
    for item in value["rolePolicies"]:
        expected = {"role": item["role"], **ROLE_POLICIES[item["role"]]}
        if item != expected:
            raise ValueError("GroovePlan role policy differs from the fixed safety policy")
    template_ids = set()
    for template in value["grooveTemplates"]:
        keys = {"templateId", "patternId", "sourceKind", "role", "sourceTimingResolution",
                "targetPpq", "gridDivision", "microtimingLimitTicks",
                "gateVariationLimitPercent", "intentionalFeel", "timingAuthority", "steps",
                "templateHash"}
        if set(template) != keys or template["templateHash"] != _hash_without(template, "templateHash"):
            raise ValueError("Groove template fields/hash mismatch")
        if template["role"] not in ROLES or template["intentionalFeel"] not in {
                "STRAIGHT", "AHEAD", "LAID_BACK", "MIXED"}:
            raise ValueError("Groove template role/feel is invalid")
        if template["templateId"] in template_ids:
            raise ValueError("GroovePlan template IDs are not unique")
        template_ids.add(template["templateId"])
    if source["variantCount"] != len(value["variants"]):
        raise ValueError("GroovePlan variant count mismatch")
    fragment_ids = set()
    for variant in value["variants"]:
        if set(variant) != {"variantId", "sourceVariantHash", "fragments",
                           "simplificationOperations", "polyphonyBefore", "polyphonyAfter",
                           "deviceVoiceCost", "renderable", "variantHash"}:
            raise ValueError("GroovePlan variant fields mismatch")
        if variant["variantHash"] != _hash_without(variant, "variantHash"):
            raise ValueError("GroovePlan variant hash mismatch")
        for report_name in ("polyphonyBefore", "polyphonyAfter"):
            report = variant[report_name]
            if report["reportHash"] != _hash_without(report, "reportHash"):
                raise ValueError("GroovePlan polyphony report hash mismatch")
        for fragment in variant["fragments"]:
            keys = {"requestId", "marker", "role", "logicalTrack", "channelNumber",
                    "sourceKind", "patternId", "pitchMode", "sourceSelectionFragmentHash",
                    "sourceTimingHash", "templateId", "locked", "humanizationApplied", "status",
                    "markerLengthTicks", "events", "fragmentHash"}
            if set(fragment) != keys or fragment["fragmentHash"] != _hash_without(fragment, "fragmentHash"):
                raise ValueError("GroovePlan fragment fields/hash mismatch")
            if fragment["marker"] not in ELEMENTS or fragment["role"] not in ROLES:
                raise ValueError("GroovePlan fragment marker/role is invalid")
            policy = ROLE_POLICIES[fragment["role"]]
            if (fragment["channelNumber"], fragment["logicalTrack"]) != (
                    policy["channelNumber"], policy["logicalTrack"]):
                raise ValueError("GroovePlan fragment channel assignment is invalid")
            if fragment["templateId"] is not None and fragment["templateId"] not in template_ids:
                raise ValueError("GroovePlan fragment references an unknown template")
            key = (variant["variantId"], fragment["requestId"])
            if key in fragment_ids:
                raise ValueError("GroovePlan contains duplicate variant/request fragments")
            fragment_ids.add(key)
            if fragment["locked"] and fragment["humanizationApplied"]:
                raise ValueError("Locked GroovePlan fragment was humanized")
            for event in fragment["events"]:
                event_keys = {"eventId", "sourceEventIndex", "cycleIndex", "originalOnsetTick",
                              "originalDurationTick", "onsetTick", "durationTick",
                              "timingOffsetTick", "gateDeltaTick", "pitchToken", "enabled",
                              "priorityTier", "preservePriority", "eventHash"}
                if set(event) != event_keys or event["eventHash"] != _hash_without(event, "eventHash"):
                    raise ValueError("GroovePlan event fields/hash mismatch")
                if event["durationTick"] < 1 or event["onsetTick"] < 0:
                    raise ValueError("GroovePlan event timing is invalid")
                if (event["timingOffsetTick"] != event["onsetTick"] - event["originalOnsetTick"]
                        or event["gateDeltaTick"] != event["durationTick"] - event["originalDurationTick"]):
                    raise ValueError("GroovePlan event timing audit is inconsistent")
                limit = int(ROLE_POLICIES[fragment["role"]]["microtimingLimitTicks"])
                if abs(int(event["timingOffsetTick"])) > limit:
                    raise ValueError("GroovePlan event exceeds its role microtiming limit")
                if fragment["locked"] and (event["timingOffsetTick"] or event["gateDeltaTick"]):
                    raise ValueError("Locked GroovePlan event timing was changed")
    audit_keys = {"templateCount", "variantCount", "fragmentCount", "eventCount",
                  "disabledEventCount", "maximumPeakBeforeSimplification",
                  "maximumPeakAfterSimplification", "allVariantsWithinMidiNoteCeiling",
                  "goldTimingOnly", "lockedFragmentsPreserved"}
    if set(value["audit"]) != audit_keys:
        raise ValueError("GroovePlan audit fields mismatch")
    if value["audit"]["templateCount"] != len(value["grooveTemplates"]):
        raise ValueError("GroovePlan template audit count mismatch")
    if value["audit"]["variantCount"] != len(value["variants"]):
        raise ValueError("GroovePlan variant audit count mismatch")
    safety = value["safety"]
    expected = {
        "readOnly": True, "midiMutationAllowed": False, "finalMidiGenerated": False,
        "factoryDynamicsUnchanged": True, "goldDynamicsAuthority": False,
        "goldBankProgramAuthority": False, "soundBindingUnchanged": True,
        "originalSoloUnchanged": True, "lockedFragmentsUnchanged": True,
        "fullDurationPolyphonyMeasured": True, "softwareMidiNoteCeiling": MIDI_NOTE_CEILING,
        "deviceVoiceCostConfirmed": safety["deviceVoiceCostConfirmed"],
    }
    if safety != expected:
        raise ValueError("GroovePlan safety contract was weakened")
    if safety["deviceVoiceCostConfirmed"] != (value["deviceVoiceCost"]["status"] == "CONFIRMED_MODEL"):
        raise ValueError("GroovePlan device voice-cost status is inconsistent")
    if _forbidden_paths(value):
        raise ValueError("GroovePlan contains forbidden dynamics/sound keys")
    if value["audit"]["maximumPeakAfterSimplification"] > MIDI_NOTE_CEILING and value["readyForRenderPlanning"]:
        raise ValueError("GroovePlan cannot render above the software MIDI-note ceiling")
    if value["readyForRenderPlanning"] != (not value["manualReview"]
                                             and all(item["renderable"] for item in value["variants"])):
        raise ValueError("GroovePlan readiness disagrees with variant/manual-review state")
    if value["groovePlanHash"] != _hash_without(value, "groovePlanHash"):
        raise ValueError("GroovePlan hash mismatch")


def execute_groove_plan_api(payload: Mapping[str, Any], root: Path) -> dict[str, Any]:
    allowed = {"candidateSet", "arrangementGraph", "songMap", "seed", "controls", "deviceProfile"}
    if not isinstance(payload, Mapping) or set(payload) - allowed:
        raise ValueError("Unknown GroovePlan API fields")
    if not {"candidateSet", "arrangementGraph", "songMap"} <= set(payload):
        raise ValueError("GroovePlan API requires candidateSet, arrangementGraph and songMap")
    return build_groove_plan(
        payload["candidateSet"], payload["arrangementGraph"], payload["songMap"], root,
        payload.get("seed", 2300), payload.get("controls"), payload.get("deviceProfile"),
    )


def execute_groove_plan_gui(payload: Mapping[str, Any], root: Path) -> dict[str, Any]:
    return execute_groove_plan_api(payload, root)