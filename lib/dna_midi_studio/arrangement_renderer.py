"""Session 33 deterministic, evidence-driven Pa800 Arrangement Renderer.

The renderer is the first Premium component allowed to create MIDI bytes.  It
accepts only a validated TrackPlan 3.0 whose complete source/evidence hash
chain still matches.  GOLD and Factory-strumming registries contribute pattern
timing and relative pitch; note velocity and CC7 are derived only from exact
Factory profiles.  Device-unconfirmed expression/articulation events are not
rendered.
"""

from __future__ import annotations

import base64
from collections import Counter, defaultdict
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from .evidence_authority import validate_evidence_ledger
from .groove_polyphony import ROLE_POLICIES, validate_groove_plan_v2
from .midi import MidiEvent, MidiFile, MidiTrack
from .track_plan import validate_track_plan_v3
from .transactional_export import AtomicMidiPublisher, CancelToken, TransactionIdentity


RENDER_MANIFEST_SCHEMA = "dna-arrangement-render-manifest"
RENDER_MANIFEST_VERSION = "2.0"
RENDER_FRAGMENT_SCHEMA = "dna-rendered-fragment"
RENDER_FRAGMENT_VERSION = "1.0"
MIDI_PPQ = 480
MIDI_NOTE_CEILING = 54
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MARKER = re.compile(r"^(?:i[12]|v[1-4]|f[12]|e[12])cv1$")
_NOTE_CHANNEL_LIMITS = {8: 1, 9: 24, 10: 16, 11: 4, 12: 4, 13: 1, 14: 1, 15: 3}
_ROOTS = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3,
    "E": 4, "F": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8,
    "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11,
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _hash(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return _hash({key: item for key, item in value.items() if key != field})


def _document_hash(value: Mapping[str, Any]) -> str:
    for key in ("analysisHash", "mapHash", "briefHash", "graphHash",
                "candidateSetHash", "groovePlanHash", "expressionPlanHash"):
        candidate = value.get(key)
        if isinstance(candidate, str) and _HEX64.fullmatch(candidate):
            return candidate
    return _hash(value)


def _selected_variant(document: Mapping[str, Any], variant_id: str) -> Mapping[str, Any]:
    aliases = {variant_id, variant_id.removeprefix("variant-"),
               "variant-" + variant_id.removeprefix("variant-")}
    found = next((item for item in document.get("variants", ())
                  if str(item.get("variantId")) in aliases), None)
    if found is None:
        raise ValueError(f"Selected variant is missing: {variant_id}")
    return found


def _load_verified_registry(root: Path, entry: Mapping[str, Any]) -> dict[str, Any]:
    if entry.get("status") != "VERIFIED" or not _HEX64.fullmatch(str(entry.get("actualSha256", ""))):
        raise ValueError("Renderer registry is not verified by EvidenceLedger")
    path = root / str(entry.get("path", ""))
    if not path.is_file():
        raise FileNotFoundError(path)
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != entry["actualSha256"]:
        raise ValueError(f"Renderer registry hash mismatch: {path}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"Renderer registry must be an object: {path}")
    return value


def _curve_at(profile: Mapping[str, Any] | None, intensity: float,
              value_key: str) -> int | None:
    if not profile:
        return None
    points = sorted(
        ((float(item["intensity"]), int(item[value_key]))
         for item in profile.get("points", ()) if value_key in item),
        key=lambda item: item[0],
    )
    if not points:
        return None
    amount = max(0.0, min(100.0, float(intensity)))
    if amount <= points[0][0]:
        return points[0][1]
    if amount >= points[-1][0]:
        return points[-1][1]
    for left, right in zip(points, points[1:]):
        if left[0] <= amount <= right[0]:
            ratio = (amount - left[0]) / max(1e-9, right[0] - left[0])
            return round(left[1] + (right[1] - left[1]) * ratio)
    return points[-1][1]


def _velocity_at(profile: Mapping[str, Any], intensity: float) -> int:
    value = _curve_at(profile.get("velocityCurve") or profile.get("velocity_curve"),
                      intensity, "velocity")
    if value is None:
        velocity = profile.get("velocity", {})
        value = int(velocity.get("optimal", velocity.get("max", 96)))
    allowed = (profile.get("velocityCurve") or {}).get("allowedRange") \
        or profile.get("velocityRange") or [1, 127]
    return max(int(allowed[0]), min(int(allowed[1]), max(1, min(127, int(value)))))


def _controller_at(profile: Mapping[str, Any], name: str, intensity: float) -> int | None:
    return _curve_at(profile.get("mixerProfile", {}).get(name), intensity, "value")


def _meter_for_node(song_map: Mapping[str, Any], node: Mapping[str, Any]) -> tuple[int, int]:
    section = next((item for item in song_map["sections"]
                    if item["id"] == node["sourceSectionId"]), None)
    if section is None:
        raise ValueError(f"Arrangement node has unknown source section: {node['marker']}")
    start = int(section["startTick"])
    candidates = [item for item in song_map["meterMap"] if int(item["tick"]) <= start]
    meter = max(candidates, key=lambda item: int(item["tick"])) if candidates else song_map["meterMap"][0]
    return int(meter["numerator"]), int(meter["denominator"])


def _time_signature_data(numerator: int, denominator: int) -> bytes:
    if numerator < 1 or denominator < 1 or denominator & (denominator - 1):
        raise ValueError("Pa800 renderer requires a power-of-two time-signature denominator")
    power = denominator.bit_length() - 1
    return bytes((numerator, power, 24, 8))


def _chord_root(symbol: str) -> int:
    match = re.match(r"^([A-Ga-g])([#bB]?)", symbol.strip())
    if not match:
        raise ValueError(f"Unsupported harmonic context: {symbol}")
    return _ROOTS[match.group(1).upper() + match.group(2).upper()]


def _relative_pitch(token: int, chord: str, low: int, high: int, role: str) -> int:
    root = _chord_root(chord)
    preferred = low + (4 if role == "bass" else max(8, (high - low) // 3))
    anchors = [pitch for pitch in range(low - 24, high + 25) if pitch % 12 == root]
    if not anchors:
        raise ValueError("No chord-root anchor exists near the authorized register")
    anchor = min(anchors, key=lambda pitch: (abs(pitch - preferred), pitch))
    pitch = anchor + int(token)
    moves = 0
    while pitch < low and moves < 2:
        pitch += 12
        moves += 1
    while pitch > high and moves < 2:
        pitch -= 12
        moves += 1
    if not low <= pitch <= high:
        raise ValueError(f"Relative pitch cannot fit authorized register {low}..{high}")
    return pitch


def _profile_for_pitch(binding: Mapping[str, Any], profiles: Mapping[str, Mapping[str, Any]],
                       pitch: int, role: str) -> Mapping[str, Any]:
    choices = [profiles[str(item)] for item in binding["factoryProfileIds"]]
    if role in {"drums", "percussion"}:
        match = next((item for item in choices if item.get("kind") == "drum"
                      and int(item.get("drumNote", -1)) == pitch), None)
        if match is None:
            raise ValueError(f"Exact Factory drum profile is missing for pitch {pitch}")
        return match
    if len(choices) != 1 or choices[0].get("kind") != "melodic":
        raise ValueError("Melodic renderer binding must contain one exact Factory profile")
    return choices[0]


def _binding_fingerprint(binding: Mapping[str, Any]) -> tuple[Any, ...]:
    return (binding["role"], binding["logicalTrack"], binding["channelIndex"],
            binding["targetTrackUid"])


def _normalize_notes(notes: Sequence[dict[str, Any]], channel_limit: int,
                     preferred_pitch: int = 60) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Remove exact duplicates/overlaps without inventing notes or velocities."""
    by_pitch: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for note in notes:
        by_pitch[int(note["pitch"])].append(dict(note))
    cleaned: list[dict[str, Any]] = []
    duplicate_count = overlap_count = 0
    for pitch in sorted(by_pitch):
        group = sorted(by_pitch[pitch], key=lambda item: (item["start"], item["end"], item["eventId"]))
        for note in group:
            previous = next((item for item in reversed(cleaned) if item["pitch"] == pitch), None)
            if previous and note["start"] == previous["start"]:
                previous["end"] = max(previous["end"], note["end"])
                previous["sourceEventIds"].append(note["eventId"])
                duplicate_count += 1
                continue
            if previous and note["start"] < previous["end"]:
                previous["end"] = note["start"]
                overlap_count += 1
                if previous["end"] <= previous["start"]:
                    cleaned.remove(previous)
            note["sourceEventIds"] = [note.pop("eventId")]
            cleaned.append(note)
    by_start: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for note in cleaned:
        by_start[int(note["start"])].append(note)
    limited: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    polyphony_removed = tails_trimmed = peak = 0
    for tick in sorted(by_start):
        active = [item for item in active if item["end"] > tick]
        incoming = sorted(by_start[tick],
                          key=lambda item: (abs(item["pitch"] - preferred_pitch), item["pitch"], item["end"]))
        if len(active) >= channel_limit and incoming:
            for item in active:
                if item["end"] > tick:
                    item["end"] = tick
                    tails_trimmed += 1
            active = []
        available = max(0, channel_limit - len(active))
        accepted, rejected = incoming[:available], incoming[available:]
        polyphony_removed += len(rejected)
        limited.extend(accepted)
        active.extend(accepted)
        peak = max(peak, len(active))
    cleaned = [item for item in limited if item["end"] > item["start"]]
    return sorted(cleaned, key=lambda item: (item["start"], item["pitch"], item["end"])), {
        "duplicatesCollapsed": duplicate_count, "overlapsTrimmed": overlap_count,
        "polyphonyNotesRemoved": polyphony_removed, "polyphonyTailsTrimmed": tails_trimmed,
        "peak": peak,
    }


def _validate_chain(source_bytes: bytes, track_plan: Mapping[str, Any],
                    documents: Mapping[str, Any], ledger: Mapping[str, Any]) -> None:
    validate_track_plan_v3(track_plan)
    validate_evidence_ledger(ledger)
    validate_groove_plan_v2(documents["groovePlan"])
    source_hash = sha256(source_bytes).hexdigest()
    if source_hash != track_plan["source"]["sourceMidiSha256"]:
        raise ValueError("Renderer source MIDI hash does not match TrackPlan")
    if ledger["ledgerHash"] != track_plan["source"]["ledgerHash"]:
        raise ValueError("Renderer EvidenceLedger hash does not match TrackPlan")
    names = {"trackAnalysis", "songMap", "producerBrief", "arrangementGraph",
             "candidateSet", "groovePlan", "expressionPlan"}
    if not names <= set(documents):
        raise ValueError("Renderer document chain is incomplete")
    for name in names:
        actual = _document_hash(documents[name])
        if actual != track_plan["source"][name]:
            raise ValueError(f"Renderer document hash does not match TrackPlan: {name}")
        if ledger["documents"][name]["sha256"] != actual:
            raise ValueError(f"Renderer document hash does not match EvidenceLedger: {name}")
    if track_plan["source"]["chainHash"] != _hash({
            key: value for key, value in track_plan["source"].items() if key != "chainHash"}):
        raise ValueError("Renderer TrackPlan source chain hash is invalid")
    if not track_plan["readiness"]["readyForDeterministicRenderer"]:
        raise ValueError("TrackPlan is not ready for deterministic rendering")


def _render_core(source_bytes: bytes, track_plan: Mapping[str, Any],
                 documents: Mapping[str, Any], ledger: Mapping[str, Any],
                 root: Path) -> tuple[bytes, dict[str, Any]]:
    _validate_chain(source_bytes, track_plan, documents, ledger)
    factory_entry = ledger["registries"]["factoryProfiles"]
    factory = _load_verified_registry(root, factory_entry)
    profiles = {str(item["id"]): item for item in factory.get("profiles", ())}
    variant_id = str(track_plan["controls"]["selectedVariantId"])
    groove_variant = _selected_variant(documents["groovePlan"], variant_id)
    candidate_variant = _selected_variant(documents["candidateSet"], variant_id)
    if not groove_variant.get("renderable"):
        raise ValueError("Selected GroovePlan variant is not renderable")
    groove_by_request = {str(item["requestId"]): item for item in groove_variant["fragments"]}
    candidate_by_request = {str(item["requestId"]): item for item in candidate_variant["selections"]}
    decisions = {(item["subjectType"], str(item["subjectId"])): item
                 for item in ledger["decisions"]}
    graph_nodes = list(documents["arrangementGraph"]["nodes"])
    graph_by_marker = {str(item["marker"]): item for item in graph_nodes}
    song_map = documents["songMap"]
    source_ppq = int(song_map["ppq"])
    if source_ppq <= 0:
        raise ValueError("Renderer requires positive source PPQ")
    scale = MIDI_PPQ / source_ppq

    fragments = list(track_plan["fragments"])
    plan_by_request = {str(item["requestId"]): item for item in fragments}
    if len(plan_by_request) != len(fragments):
        raise ValueError("TrackPlan contains duplicate fragment request IDs")
    for item in fragments:
        if item["marker"] not in graph_by_marker:
            raise ValueError(f"TrackPlan fragment references unknown marker: {item['marker']}")
        if item["decision"] not in {"KEEP", "REPLACE"}:
            raise ValueError(f"Renderer refuses unresolved TrackPlan fragment: {item['requestId']}")

    bindings_by_channel: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for binding in track_plan["targetBindings"]:
        if str(binding["requestId"]) not in plan_by_request:
            raise ValueError("TrackPlan target binding has no fragment")
        if binding["confirmation"] != "EXACT_FACTORY_SOFTWARE":
            raise ValueError("Renderer requires exact Factory target bindings")
        if binding["channelNumber"] != binding["channelIndex"] + 1 \
                or not 8 <= int(binding["channelIndex"]) <= 15:
            raise ValueError("Renderer target channel is outside Pa800 channels 9..16")
        if any(str(profile_id) not in profiles for profile_id in binding["factoryProfileIds"]):
            raise ValueError("Renderer target binding references an unknown Factory profile")
        for profile_id in binding["factoryProfileIds"]:
            profile = profiles[str(profile_id)]
            if (profile.get("bankMsb"), profile.get("bankLsb"), profile.get("program")) != (
                    binding["bankMsb"], binding["bankLsb"], binding["program"]):
                raise ValueError("Renderer binding and Factory profile sound do not match")
        bindings_by_channel[int(binding["channelIndex"])].append(binding)
    for channel, bindings in bindings_by_channel.items():
        fingerprints = {_binding_fingerprint(item) for item in bindings}
        if len(fingerprints) != 1:
            raise ValueError(f"Renderer found conflicting exact bindings on channel {channel + 1}")
    used_channels = sorted(bindings_by_channel)

    marker_starts: dict[str, int] = {}
    marker_ends: dict[str, int] = {}
    cursor = 0
    for node in graph_nodes:
        marker = str(node["marker"])
        if not _MARKER.fullmatch(marker):
            raise ValueError(f"Renderer refuses invalid Pa800 marker: {marker}")
        marker_fragments = [groove_by_request[item["requestId"]]
                            for item in fragments if item["marker"] == marker]
        lengths = {int(item["markerLengthTicks"]) for item in marker_fragments
                   if int(item["markerLengthTicks"]) > 0}
        if len(lengths) != 1:
            raise ValueError(f"Renderer marker length evidence is incomplete: {marker}")
        marker_length = max(1, round(next(iter(lengths)) * scale))
        marker_starts[marker], marker_ends[marker] = cursor, cursor + marker_length
        cursor += marker_length

    events: list[MidiEvent] = [MidiEvent(0, -1000, "meta", data=b"DNA AI Premium Arranger Preview",
                                         meta_type=0x03)]
    order = 0
    marker_setup: list[dict[str, Any]] = []
    rendered_fragments: list[dict[str, Any]] = []
    global_note_specs: list[dict[str, Any]] = []
    totals = Counter()

    for node in graph_nodes:
        marker = str(node["marker"])
        marker_start, marker_end = marker_starts[marker], marker_ends[marker]
        numerator, denominator = _meter_for_node(song_map, node)
        events.append(MidiEvent(marker_start, order, "meta", data=marker.encode("ascii"), meta_type=0x06))
        order += 1
        events.append(MidiEvent(marker_start, order, "meta",
                                data=_time_signature_data(numerator, denominator), meta_type=0x58))
        order += 1
        marker_bindings = {int(item["targetBinding"]["channelIndex"]): item["targetBinding"]
                           for item in fragments if item["marker"] == marker and item["targetBinding"]}
        for channel in used_channels:
            binding = marker_bindings.get(channel, bindings_by_channel[channel][0])
            selected_profiles = [profiles[str(item)] for item in binding["factoryProfileIds"]]
            intensity = 50 + (float(node["targetEnergy"]) - 50) * \
                int(track_plan["controls"]["mixerStrength"]) / 100
            volume_values = [_controller_at(profile, "volume", intensity)
                             for profile in selected_profiles]
            if any(value is None for value in volume_values):
                raise ValueError(f"Factory CC7 evidence is missing for {marker} CH{channel + 1}")
            cc7 = round(median(int(value) for value in volume_values if value is not None))
            # CC11=127 is the immutable Pa800 Style initialization contract.
            # Factory expression curves remain recorded in the setup audit and
            # GOLD has no controller authority.
            factory_expression = [_controller_at(profile, "expression", intensity)
                                  for profile in selected_profiles]
            events.extend((
                MidiEvent(marker_start, order, "channel", status=0xB0 | channel,
                          data=bytes((0, int(binding["bankMsb"])))),
                MidiEvent(marker_start, order + 1, "channel", status=0xB0 | channel,
                          data=bytes((32, int(binding["bankLsb"])))),
                MidiEvent(marker_start, order + 2, "channel", status=0xC0 | channel,
                          data=bytes((int(binding["program"]),))),
                MidiEvent(marker_start, order + 3, "channel", status=0xB0 | channel,
                          data=bytes((7, cc7))),
                MidiEvent(marker_start, order + 4, "channel", status=0xB0 | channel,
                          data=bytes((11, 127))),
            ))
            order += 5
            marker_setup.append({
                "marker": marker, "tick": marker_start, "channelNumber": channel + 1,
                "bankMsb": int(binding["bankMsb"]), "bankLsb": int(binding["bankLsb"]),
                "program": int(binding["program"]), "cc7": cc7, "cc11": 127,
                "cc7Authority": "FACTORY_MIXER_CURVE",
                "cc11Authority": "PA800_STYLE_INITIALIZATION_CONTRACT",
                "factoryExpressionReference": [value for value in factory_expression if value is not None],
                "factoryProfileIds": list(binding["factoryProfileIds"]),
                "setupHash": "",
            })
            marker_setup[-1]["setupHash"] = _hash_without(marker_setup[-1], "setupHash")

        for plan_fragment in sorted((item for item in fragments if item["marker"] == marker),
                                    key=lambda item: (item["targetBinding"]["channelNumber"], item["role"])):
            request_id = str(plan_fragment["requestId"])
            groove = groove_by_request.get(request_id)
            selection = candidate_by_request.get(request_id)
            binding = plan_fragment["targetBinding"]
            if groove is None or selection is None or binding is None:
                raise ValueError(f"Renderer fragment chain is incomplete: {request_id}")
            if (groove["patternId"], groove["sourceKind"], selection["patternId"],
                    selection["sourceKind"]) != (plan_fragment["patternId"], plan_fragment["sourceKind"],
                                                  plan_fragment["patternId"], plan_fragment["sourceKind"]):
                raise ValueError(f"Renderer pattern chain mismatch: {request_id}")
            candidate_decision = decisions.get(("PATTERN_SELECTION", request_id))
            if candidate_decision is None or candidate_decision["disposition"] != "ALLOW":
                raise ValueError(f"Renderer pattern authority is not ALLOW: {request_id}")
            groove_operation = next((item for item in plan_fragment["operations"]
                                     if item["kind"] == "APPLY_EVIDENCE_GROOVE"), None)
            allowed_decision_ids = set(groove_operation["decisionIds"]) if groove_operation else set()
            if plan_fragment["decision"] == "REPLACE" and groove_operation is None:
                raise ValueError(f"Renderer timing operation is missing: {request_id}")
            register = next((item for item in node["registerPlan"] if item["role"] == plan_fragment["role"]), None)
            if plan_fragment["role"] not in {"drums", "percussion"} and register is None:
                raise ValueError(f"Renderer register plan is missing: {request_id}")
            local_notes: list[dict[str, Any]] = []
            disabled = 0
            marker_source_length = int(groove["markerLengthTicks"])
            for event in groove["events"]:
                decision = decisions.get(("GROOVE_EVENT", str(event["eventId"])))
                if not event["enabled"]:
                    disabled += 1
                    if decision is None or decision["disposition"] not in {"SKIP", "ALLOW"}:
                        raise ValueError(f"Disabled Groove event lacks an explicit decision: {event['eventId']}")
                    continue
                if decision is None or decision["disposition"] != "ALLOW":
                    raise ValueError(f"Renderer Groove event authority is not ALLOW: {event['eventId']}")
                if plan_fragment["decision"] == "REPLACE" and decision["decisionId"] not in allowed_decision_ids:
                    raise ValueError(f"TrackPlan does not authorize Groove event: {event['eventId']}")
                local_start = max(0, round(int(event["onsetTick"]) * scale))
                duration = max(1, round(int(event["durationTick"]) * scale))
                local_end = min(marker_end - marker_start, local_start + duration)
                if local_end <= local_start:
                    raise ValueError(f"Renderer produced a non-positive note duration: {event['eventId']}")
                token = int(event["pitchToken"])
                if groove["pitchMode"] == "absolute-drum-note":
                    pitch = token
                elif groove["pitchMode"] in {"semitones-from-local-root", "factory-strum-relative-voicing"}:
                    harmonic = list(node["harmonicContext"])
                    chord_index = min(len(harmonic) - 1,
                                      int(event["onsetTick"]) * len(harmonic) // max(1, marker_source_length))
                    pitch = _relative_pitch(token, harmonic[chord_index], int(register["low"]),
                                            int(register["high"]), str(plan_fragment["role"]))
                else:
                    raise ValueError(f"Renderer refuses unknown pitch authority: {groove['pitchMode']}")
                if not 0 <= pitch <= 127:
                    raise ValueError(f"Renderer pitch is outside MIDI range: {pitch}")
                profile = _profile_for_pitch(binding, profiles, pitch, str(plan_fragment["role"]))
                effective_energy = 50 + (float(node["targetEnergy"]) - 50) * \
                    int(track_plan["controls"]["velocityStrength"]) / 100
                velocity = _velocity_at(profile, effective_energy)
                local_notes.append({
                    "eventId": str(event["eventId"]), "start": local_start,
                    "end": local_end, "pitch": pitch, "velocity": velocity,
                    "profileId": str(profile["id"]),
                })
            channel = int(binding["channelIndex"])
            verify_operation = next((item for item in plan_fragment["operations"]
                                     if item["kind"] == "VERIFY_POLYPHONY"), None)
            if plan_fragment["decision"] == "REPLACE" and verify_operation is None:
                raise ValueError(f"Renderer polyphony verification operation is missing: {request_id}")
            preferred_pitch = 60 if register is None else round((int(register["low"]) + int(register["high"])) / 2)
            try:
                local_notes, cleanup = _normalize_notes(
                    local_notes, _NOTE_CHANNEL_LIMITS[channel], preferred_pitch
                )
            except ValueError as exc:
                raise ValueError(f"{request_id}: {exc}") from exc
            for note in local_notes:
                absolute_start = marker_start + int(note["start"])
                absolute_end = marker_start + int(note["end"])
                events.append(MidiEvent(absolute_start, order, "channel", status=0x90 | channel,
                                        data=bytes((int(note["pitch"]), int(note["velocity"])))))
                order += 1
                events.append(MidiEvent(absolute_end, order, "channel", status=0x80 | channel,
                                        data=bytes((int(note["pitch"]), 0))))
                order += 1
                global_note_specs.append({"marker": marker, "channel": channel, **note})
            pitch_values = [int(item["pitch"]) for item in local_notes]
            velocity_values = [int(item["velocity"]) for item in local_notes]
            allowed_ranges = [
                (profiles[str(item)].get("velocityCurve") or {}).get("allowedRange")
                or profiles[str(item)].get("velocityRange") or [1, 127]
                for item in binding["factoryProfileIds"]
            ]
            fragment_output = {
                "schema": RENDER_FRAGMENT_SCHEMA, "version": RENDER_FRAGMENT_VERSION,
                "fragmentId": plan_fragment["fragmentId"], "requestId": request_id,
                "marker": marker, "role": plan_fragment["role"],
                "logicalTrack": binding["logicalTrack"], "channelNumber": binding["channelNumber"],
                "patternId": plan_fragment["patternId"], "sourceKind": plan_fragment["sourceKind"],
                "decision": plan_fragment["decision"], "status": "RENDERED",
                "startTick": marker_start, "endTick": marker_end,
                "input": {
                    "trackPlanFragmentHash": plan_fragment["fragmentHash"],
                    "grooveFragmentHash": groove["fragmentHash"],
                    "candidateSelectionHash": selection["fragmentHash"],
                    "bindingHash": _hash(binding),
                },
                "factoryProfileIds": list(binding["factoryProfileIds"]),
                "operationIds": [item["operationId"] for item in plan_fragment["operations"]],
                "noteCount": len(local_notes), "disabledEventCount": disabled,
                "pitchRange": [min(pitch_values), max(pitch_values)] if pitch_values else None,
                "authorizedRegister": ([int(register["low"]), int(register["high"])]
                                       if register is not None else [0, 127]),
                "velocityRange": [min(velocity_values), max(velocity_values)] if velocity_values else None,
                "factoryAllowedVelocityRange": [min(int(item[0]) for item in allowed_ranges),
                                                max(int(item[1]) for item in allowed_ranges)],
                "cleanup": cleanup,
                "outputEventHash": _hash([[item["start"], item["end"] - item["start"],
                                            item["pitch"], item["velocity"]]
                                           for item in local_notes]),
                "fragmentRenderHash": "",
            }
            fragment_output["fragmentRenderHash"] = _hash_without(fragment_output, "fragmentRenderHash")
            rendered_fragments.append(fragment_output)
            totals["notes"] += len(local_notes)
            totals["disabled"] += disabled
            totals["duplicates"] += cleanup["duplicatesCollapsed"]
            totals["overlaps"] += cleanup["overlapsTrimmed"]
            totals["polyphonyRemoved"] += cleanup["polyphonyNotesRemoved"]
            totals["polyphonyTails"] += cleanup["polyphonyTailsTrimmed"]

    midi = MidiFile(format_type=0, ppq=MIDI_PPQ, tracks=[MidiTrack(events)])
    midi_bytes = midi.to_bytes()
    parsed = MidiFile.from_bytes(midi_bytes)
    parsed_notes = parsed.notes()
    global_points = []
    for note in parsed_notes:
        global_points.extend(((note.start, 1), (note.end, -1)))
    active = global_peak = 0
    for _tick, delta in sorted(global_points, key=lambda item: (item[0], item[1])):
        active += delta
        global_peak = max(global_peak, active)
    if global_peak > MIDI_NOTE_CEILING:
        raise ValueError(f"Rendered global polyphony exceeds software ceiling: {global_peak} > 54")

    source = {
        "sourceMidiSha256": sha256(source_bytes).hexdigest(),
        "trackPlanHash": track_plan["trackPlanHash"],
        "evidenceLedgerHash": ledger["ledgerHash"],
        "trackPlanChainHash": track_plan["source"]["chainHash"],
        "trackAnalysisHash": track_plan["source"]["trackAnalysis"],
        "songMapHash": track_plan["source"]["songMap"],
        "producerBriefHash": track_plan["source"]["producerBrief"],
        "arrangementGraphHash": track_plan["source"]["arrangementGraph"],
        "candidateSetHash": track_plan["source"]["candidateSet"],
        "groovePlanHash": track_plan["source"]["groovePlan"],
        "expressionPlanHash": track_plan["source"]["expressionPlan"],
        "factoryRegistrySha256": factory_entry["actualSha256"],
        "selectedVariantId": variant_id,
        "sourceHashChain": "",
    }
    source["sourceHashChain"] = _hash_without(source, "sourceHashChain")
    excluded = Counter()
    for decision in ledger["decisions"]:
        subject = str(decision["subjectType"])
        if "EXPRESSION" in subject or "ARTICULATION" in subject:
            excluded[subject] += 1
    midi_summary = {
        "format": 0, "ppq": MIDI_PPQ, "trackCount": 1,
        "markerCount": len(graph_nodes), "markers": [item["marker"] for item in graph_nodes],
        "usedChannels": [item + 1 for item in used_channels],
        "logicalTrackCount": len(used_channels), "noteCount": len(parsed_notes),
        "eventCount": len(parsed.tracks[0].events), "lengthTicks": cursor,
        "globalPeakConcurrentMidiNotes": global_peak,
        "softwareMidiNoteCeiling": MIDI_NOTE_CEILING,
        "outputSha256": sha256(midi_bytes).hexdigest(),
    }
    manifest = {
        "schema": RENDER_MANIFEST_SCHEMA, "version": RENDER_MANIFEST_VERSION,
        "renderId": "render-" + _hash([source, [item["fragmentRenderHash"]
                                                  for item in rendered_fragments]])[:20],
        "target": "Korg Pa800 OS 2.0+ Style Import SMF0",
        "source": source, "midi": midi_summary,
        "channelBindings": [
            {"channelNumber": channel + 1, "role": bindings_by_channel[channel][0]["role"],
             "logicalTrack": bindings_by_channel[channel][0]["logicalTrack"],
             "targetTrackUid": bindings_by_channel[channel][0]["targetTrackUid"],
             "scope": "MARKER_SCOPED_EXACT_SOUNDBINDING",
             "exactSoundStates": sorted({
                 (int(item["bankMsb"]), int(item["bankLsb"]), int(item["program"]))
                 for item in bindings_by_channel[channel]
             }),
             "exactSoundStateCount": len({
                 (int(item["bankMsb"]), int(item["bankLsb"]), int(item["program"]))
                 for item in bindings_by_channel[channel]
             })}
            for channel in used_channels
        ],
        "markerSetup": marker_setup,
        "fragments": rendered_fragments,
        "audit": {
            "renderedFragments": len(rendered_fragments),
            "renderedNotes": totals["notes"], "disabledGrooveEvents": totals["disabled"],
            "duplicatesCollapsed": totals["duplicates"], "overlapsTrimmed": totals["overlaps"],
            "channelPolyphonyNotesRemoved": totals["polyphonyRemoved"],
            "channelPolyphonyTailsTrimmed": totals["polyphonyTails"],
            "fragmentHashChain": _hash([item["fragmentRenderHash"] for item in rendered_fragments]),
            "goldTimingRelativePitchOnly": True, "factoryVelocityOnly": True,
            "factoryCc7Only": True, "pa800Cc11Initialization": 127,
            "deviceUnconfirmedLayersExcluded": dict(sorted(excluded.items())),
        },
        "verification": {},
        "safety": {
            "midiBytesGenerated": True, "sourceMidiMutated": False,
            "sourceMidiOverwritten": False, "originalSoloFingerprintProtected": True,
            "lockedFragmentsProtected": True, "exactSoundBindingRequired": True,
            "goldAffectsDynamics": False, "goldAffectsMixer": False,
            "goldAffectsBankProgram": False, "approximateSoundBindingAllowed": False,
            "implicitFallbackAllowed": False, "deviceUnconfirmedExpressionRendered": False,
            "deviceUnconfirmedArticulationRendered": False,
            "independentVerifierRequired": True, "atomicPublicationRequired": True,
            "softwarePreviewMidiAllowed": True, "finalCertifiedMidiExportAllowed": False,
        },
        "certification": {
            "software": "SOFTWARE_VALIDATED_ARRANGER_PREVIEW",
            "humanListening": "PENDING_0_OF_2", "physicalPa800": "WAITING_FOR_DEVICE",
            "allowedProductName": "AI PREMIUM ARRANGER PREVIEW",
        },
        "renderManifestHash": "",
    }
    core_verification = _verify_midi_core(midi_bytes, manifest)
    if not core_verification["passed"]:
        raise ValueError("Independent renderer verification failed: " + "; ".join(core_verification["issues"][:5]))
    manifest["verification"] = {
        "status": "PASS", "verifier": "independent-pa800-byte-parser",
        "reportHash": _hash(core_verification),
        "checks": core_verification["checks"],
    }
    manifest["renderManifestHash"] = _hash_without(manifest, "renderManifestHash")
    validate_render_manifest_v2(manifest, midi_bytes)
    final_verification = verify_rendered_arrangement(midi_bytes, manifest, source_bytes)
    if not final_verification["passed"]:
        raise ValueError("Final renderer verification failed: " + "; ".join(final_verification["issues"][:5]))
    return midi_bytes, manifest


def _verify_midi_core(midi_bytes: bytes, manifest: Mapping[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    checks: dict[str, bool] = {}
    try:
        from . import pa800_validator

        pa800 = pa800_validator.validate_pa800_smf(
            midi_bytes, list(manifest["midi"]["markers"]),
            [int(item) - 1 for item in manifest["midi"]["usedChannels"]],
        )
        checks["pa800HardValidator"] = bool(pa800.get("passed"))
        issues.extend(pa800.get("issues", ()))
        checks["globalPolyphony"] = int(pa800.get("globalPeakConcurrentNotes", 999)) <= MIDI_NOTE_CEILING
        if not checks["globalPolyphony"]:
            issues.append("Rendered MIDI exceeds the 54-note software ceiling")
    except Exception as exc:
        checks["pa800HardValidator"] = checks["globalPolyphony"] = False
        issues.append(f"Pa800 byte validator failed: {exc}")
        pa800 = {}
    try:
        parsed = MidiFile.from_bytes(midi_bytes)
        notes = parsed.notes()
        checks["independentParse"] = True
        checks["byteRoundTrip"] = parsed.to_bytes() == midi_bytes
        checks["manifestOutputHash"] = sha256(midi_bytes).hexdigest() == manifest["midi"]["outputSha256"]
        checks["manifestNoteCount"] = len(notes) == int(manifest["midi"]["noteCount"])
        allowed_cc = {0, 7, 11, 32}
        checks["controllerWhitelist"] = all(
            event.command != 0xB0 or (len(event.data) == 2 and event.data[0] in allowed_cc)
            for event in parsed.tracks[0].events
        )
        fragment_hashes = True
        register_bounds = True
        velocity_bounds = True
        for fragment in manifest["fragments"]:
            channel = int(fragment["channelNumber"]) - 1
            start, end = int(fragment["startTick"]), int(fragment["endTick"])
            fragment_notes = [note for note in notes
                              if note.channel == channel and start <= note.start < end]
            encoded = [[note.start - start, note.end - note.start, note.pitch, note.velocity]
                       for note in fragment_notes]
            fragment_hashes = fragment_hashes and _hash(encoded) == fragment["outputEventHash"]
            low, high = map(int, fragment["authorizedRegister"])
            register_bounds = register_bounds and all(low <= note.pitch <= high for note in fragment_notes)
            vlow, vhigh = map(int, fragment["factoryAllowedVelocityRange"])
            velocity_bounds = velocity_bounds and all(vlow <= note.velocity <= vhigh
                                                       for note in fragment_notes)
        checks["fragmentEventHashes"] = fragment_hashes
        checks["registerBounds"] = register_bounds
        checks["factoryVelocityBounds"] = velocity_bounds
        marker_setup_valid = True
        all_events = parsed.tracks[0].events
        for setup in manifest["markerSetup"]:
            tick, channel = int(setup["tick"]), int(setup["channelNumber"]) - 1
            same = [event for event in all_events if event.tick == tick and event.channel == channel]
            controls = {(event.data[0], event.data[1]) for event in same
                        if event.command == 0xB0 and len(event.data) == 2}
            programs = [event.data[0] for event in same if event.command == 0xC0 and event.data]
            marker_setup_valid = marker_setup_valid and all((cc, int(setup[key])) in controls
                for cc, key in ((0, "bankMsb"), (32, "bankLsb"), (7, "cc7"), (11, "cc11")))
            marker_setup_valid = marker_setup_valid and int(setup["program"]) in programs
        checks["markerScopedSoundBinding"] = marker_setup_valid
        for name in ("byteRoundTrip", "manifestOutputHash", "manifestNoteCount", "controllerWhitelist",
                     "fragmentEventHashes", "registerBounds", "factoryVelocityBounds",
                     "markerScopedSoundBinding"):
            if not checks[name]:
                issues.append(f"Renderer check failed: {name}")
    except Exception as exc:
        checks["independentParse"] = False
        issues.append(f"Independent MIDI parse failed: {exc}")
    return {
        "schema": "dna-renderer-independent-verification", "version": "1.0",
        "passed": not issues and all(checks.values()), "issues": issues,
        "checks": checks, "midiSha256": sha256(midi_bytes).hexdigest(),
        "pa800": pa800,
    }


def validate_render_manifest_v2(value: Mapping[str, Any], midi_bytes: bytes | None = None) -> None:
    required = {"schema", "version", "renderId", "target", "source", "midi",
                "channelBindings", "markerSetup", "fragments", "audit", "verification",
                "safety", "certification", "renderManifestHash"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("RenderManifest 2.0 root fields mismatch")
    if value["schema"] != RENDER_MANIFEST_SCHEMA or value["version"] != RENDER_MANIFEST_VERSION:
        raise ValueError("Unsupported RenderManifest schema/version")
    if not str(value["renderId"]).startswith("render-"):
        raise ValueError("RenderManifest render ID is invalid")
    hashes = [value["source"][key] for key in value["source"] if key.endswith("Hash") or key.endswith("Sha256")]
    if any(not _HEX64.fullmatch(str(item)) for item in hashes):
        raise ValueError("RenderManifest contains an invalid source hash")
    if value["source"]["sourceHashChain"] != _hash_without(value["source"], "sourceHashChain"):
        raise ValueError("RenderManifest source hash chain mismatch")
    if value["midi"]["format"] != 0 or value["midi"]["ppq"] != MIDI_PPQ \
            or value["midi"]["trackCount"] != 1:
        raise ValueError("RenderManifest MIDI contract mismatch")
    if value["midi"]["softwareMidiNoteCeiling"] != MIDI_NOTE_CEILING \
            or value["midi"]["globalPeakConcurrentMidiNotes"] > MIDI_NOTE_CEILING:
        raise ValueError("RenderManifest polyphony contract mismatch")
    if value["midi"]["markerCount"] != len(value["midi"]["markers"]):
        raise ValueError("RenderManifest marker count mismatch")
    if any(not _MARKER.fullmatch(str(item)) for item in value["midi"]["markers"]):
        raise ValueError("RenderManifest contains an invalid marker")
    if len({item["requestId"] for item in value["fragments"]}) != len(value["fragments"]):
        raise ValueError("RenderManifest fragment request IDs are not unique")
    for fragment in value["fragments"]:
        if fragment["schema"] != RENDER_FRAGMENT_SCHEMA or fragment["version"] != RENDER_FRAGMENT_VERSION:
            raise ValueError("Rendered fragment schema/version mismatch")
        if fragment["fragmentRenderHash"] != _hash_without(fragment, "fragmentRenderHash"):
            raise ValueError("Rendered fragment hash mismatch")
        if fragment["decision"] not in {"KEEP", "REPLACE"} or fragment["status"] != "RENDERED":
            raise ValueError("Rendered fragment decision/status mismatch")
    for setup in value["markerSetup"]:
        if setup["setupHash"] != _hash_without(setup, "setupHash") or setup["cc11"] != 127:
            raise ValueError("RenderManifest marker setup hash/CC11 mismatch")
    if value["audit"]["fragmentHashChain"] != _hash(
            [item["fragmentRenderHash"] for item in value["fragments"]]):
        raise ValueError("RenderManifest fragment hash chain mismatch")
    safety = value["safety"]
    if not safety["midiBytesGenerated"] or safety["sourceMidiMutated"] \
            or safety["goldAffectsDynamics"] or safety["goldAffectsMixer"] \
            or safety["goldAffectsBankProgram"] or safety["approximateSoundBindingAllowed"] \
            or safety["implicitFallbackAllowed"] or safety["finalCertifiedMidiExportAllowed"]:
        raise ValueError("RenderManifest safety boundary violated")
    if value["verification"].get("status") != "PASS":
        raise ValueError("RenderManifest does not contain a PASS verification summary")
    if value["renderManifestHash"] != _hash_without(value, "renderManifestHash"):
        raise ValueError("RenderManifest hash mismatch")
    if midi_bytes is not None and sha256(midi_bytes).hexdigest() != value["midi"]["outputSha256"]:
        raise ValueError("RenderManifest output hash does not match MIDI bytes")


def verify_rendered_arrangement(midi_bytes: bytes, manifest: Mapping[str, Any],
                                source_bytes: bytes | None = None) -> dict[str, Any]:
    issues: list[str] = []
    checks: dict[str, bool] = {}
    try:
        validate_render_manifest_v2(manifest, midi_bytes)
        checks["manifestValid"] = True
    except Exception as exc:
        checks["manifestValid"] = False
        issues.append(str(exc))
    core = _verify_midi_core(midi_bytes, manifest)
    checks.update(core["checks"])
    issues.extend(core["issues"])
    if source_bytes is not None:
        checks["sourceUnchanged"] = sha256(source_bytes).hexdigest() == manifest["source"]["sourceMidiSha256"]
        if not checks["sourceUnchanged"]:
            issues.append("Source MIDI hash changed during rendering")
    else:
        checks["sourceUnchanged"] = True
    report_hash_matches = manifest.get("verification", {}).get("reportHash") == _hash(core)
    checks["embeddedVerificationHash"] = report_hash_matches
    if not report_hash_matches:
        issues.append("Embedded independent verification hash mismatch")
    return {
        "schema": "dna-renderer-final-verification", "version": "1.0",
        "passed": not issues and all(checks.values()), "issues": issues,
        "checks": checks, "midiSha256": sha256(midi_bytes).hexdigest(),
        "renderManifestHash": manifest.get("renderManifestHash"),
        "verdictSource": "independent-byte-parser-plus-pa800-hard-validator",
    }


def render_arrangement(source_bytes: bytes, track_plan: Mapping[str, Any],
                       documents: Mapping[str, Any], evidence_ledger: Mapping[str, Any],
                       root: str | Path = ".") -> tuple[bytes, dict[str, Any]]:
    if not isinstance(source_bytes, bytes) or not source_bytes:
        raise ValueError("Arrangement renderer requires non-empty source MIDI bytes")
    return _render_core(source_bytes, track_plan, documents, evidence_ledger,
                        Path(root).resolve())


def publish_rendered_arrangement(source_bytes: bytes, midi_bytes: bytes,
                                 manifest: Mapping[str, Any], output_dir: str | Path,
                                 source_name: str, cancel: CancelToken | None = None) -> dict[str, Any]:
    validate_render_manifest_v2(manifest, midi_bytes)
    identity = TransactionIdentity(
        source_hash=manifest["source"]["sourceMidiSha256"],
        config_hash=manifest["source"]["trackPlanHash"],
        database_hash=manifest["source"]["evidenceLedgerHash"],
    )
    publisher = AtomicMidiPublisher(Path(output_dir))
    result = publisher.publish(
        midi_bytes, source_name, identity,
        lambda content: verify_rendered_arrangement(content, manifest, source_bytes), cancel,
    )
    payload = asdict(result)
    payload["output_path"] = str(result.output_path) if result.output_path else None
    payload["journal_path"] = str(result.journal_path)
    if result.status == "COMMITTED" and result.output_path is not None:
        manifest_path = result.output_path.with_suffix(".render-manifest.json")
        temp = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        try:
            temp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            with temp.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temp, manifest_path)
        except Exception:
            temp.unlink(missing_ok=True)
            result.output_path.unlink(missing_ok=True)
            result.journal_path.unlink(missing_ok=True)
            raise
        payload["manifest_path"] = str(manifest_path)
    else:
        payload["manifest_path"] = None
    return payload


def execute_arrangement_renderer_api(payload: Mapping[str, Any], root: str | Path = ".") -> dict[str, Any]:
    allowed = {"midiBase64", "documents", "evidenceLedger", "trackPlan"}
    if not isinstance(payload, Mapping) or set(payload) != allowed:
        raise ValueError("Arrangement Renderer API payload fields are strict")
    try:
        source = base64.b64decode(str(payload["midiBase64"]), validate=True)
    except Exception as exc:
        raise ValueError("Arrangement Renderer MIDI must be valid base64") from exc
    midi, manifest = render_arrangement(source, payload["trackPlan"], payload["documents"],
                                        payload["evidenceLedger"], root)
    return {
        "schema": "dna-arrangement-render-api-result", "version": "1.0",
        "midiBase64": base64.b64encode(midi).decode("ascii"),
        "renderManifest": manifest,
        "verification": verify_rendered_arrangement(midi, manifest, source),
    }


def execute_arrangement_renderer_gui(payload: Mapping[str, Any], root: str | Path = ".") -> dict[str, Any]:
    return execute_arrangement_renderer_api(payload, root)


def execute_arrangement_renderer_batch(payloads: Sequence[Mapping[str, Any]],
                                       root: str | Path = ".") -> list[dict[str, Any]]:
    results = []
    for index, payload in enumerate(payloads):
        try:
            results.append({"index": index, "status": "PASS",
                            "result": execute_arrangement_renderer_api(payload, root)})
        except Exception as exc:
            results.append({"index": index, "status": "BLOCKED", "error": str(exc)})
    return results