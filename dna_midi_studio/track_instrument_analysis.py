"""Evidence-first automatic track and instrument analysis.

The detector is deliberately read-only.  Exact track-local Bank Select and
Program Change state may identify a Factory sound; General MIDI families,
register, density and track names are only explainable hints.  No result from
this module has MIDI mutation, dynamics, mixer, validator or device authority.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import statistics
from typing import Any, Iterable, Mapping, Sequence

from .midi import MidiFile, MidiFormatError, Note
from .instrument_behavior import behavior_profile
from .instrument_profile_matrix import profile_document
from .role_disambiguation import classify as disambiguate_role, echo_relationship
from .track_identity import (
    build_track_identities,
    channel_track_indices,
    sound_bindings,
)


TRACK_ANALYSIS_SCHEMA = "dna-automatic-track-instrument-analysis"
TRACK_ANALYSIS_VERSION = "3.0"
FACTORY_REGISTRY_SCHEMA = "midi-arranger.factory-velocity-profiles"
ACCEPT_CONFIDENCE = 0.86

GM_FAMILIES = (
    "piano", "chromatic-percussion", "organ", "guitar", "bass", "strings",
    "ensemble", "brass", "reed", "pipe", "synth-lead", "synth-pad",
    "synth-effects", "ethnic", "percussive", "sound-effects",
)

_NAME_HINTS = {
    "drums": ("drum", "bubanj", "kit"),
    "percussion": ("perc", "perkus", "udaral", "conga", "shaker", "tamb"),
    "bass": ("bass", "bas"),
    "guitar": ("guitar", "gitara", "gtr"),
    "solo": ("solo", "lead", "melody", "melodija", "vocal"),
    "strings": ("strings", "string", "violin", "viola", "cello", "guda"),
    "brass": ("brass", "trumpet", "trombone", "horn", "truba"),
    "sax": ("sax", "saxophone", "saksofon"),
    "woodwind": ("flute", "clarinet", "oboe", "fagot", "frula", "klarinet"),
    "accordion": ("accordion", "harmonika", "accord"),
    "piano": ("piano", "klavir"),
    "organ": ("organ", "orgulje"),
    "choir": ("choir", "vocal pad", "ahh", "ooh"),
    "pad": ("pad", "podloga"),
    "riff": ("riff", "phrase", "fraza"),
}

_PA800_TRACK = {
    "drums": "drum", "percussion": "perc", "bass": "bass",
    "guitar": "acc1", "rhythm-guitar": "acc1", "power-riff": "acc3", "harmony": "acc1", "accompaniment": "acc2",
    "riff": "acc3", "melody": "acc3", "brass": "acc3", "woodwind": "acc3", "sax": "acc3",
    "solo": "acc4", "echo": None, "terca": "acc5", "accordion": "acc4", "strings": "acc5", "pad": "acc5", "choir": "acc5",
    "piano": "acc2", "organ": "acc2", "unknown": None,
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _hash(value: Mapping[str, Any], field: str) -> str:
    return sha256(_canonical({key: item for key, item in value.items() if key != field})).hexdigest()


def _track_name(midi: MidiFile, track_index: int) -> str:
    for event in midi.tracks[track_index].events:
        if event.kind == "meta" and event.meta_type == 0x03:
            return event.data.decode("utf-8", errors="replace").strip()
    return ""


def _peak_polyphony(notes: Sequence[Note]) -> int:
    events: list[tuple[int, int]] = []
    for note in notes:
        events.extend(((note.start, 1), (note.end, -1)))
    active = peak = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        active += delta
        peak = max(peak, active)
    return peak


def _note_stats(notes: Sequence[Note], ppq: int) -> dict[str, Any]:
    pitches = [note.pitch for note in notes]
    durations = [note.end - note.start for note in notes]
    onsets = Counter(note.start for note in notes)
    if not notes:
        return {
            "noteCount": 0, "pitchLow": None, "pitchHigh": None,
            "medianPitch": None, "medianDurationTicks": None,
            "polyphonyPeak": 0, "polyphonicOnsetRate": 0.0,
            "notesPerQuarter": 0.0,
        }
    span = max(ppq, max(note.end for note in notes) - min(note.start for note in notes))
    return {
        "noteCount": len(notes), "pitchLow": min(pitches), "pitchHigh": max(pitches),
        "medianPitch": round(float(statistics.median(pitches)), 3),
        "medianDurationTicks": round(float(statistics.median(durations)), 3),
        "polyphonyPeak": _peak_polyphony(notes),
        "polyphonicOnsetRate": round(sum(count > 1 for count in onsets.values()) / max(1, len(onsets)), 4),
        "notesPerQuarter": round(len(notes) * ppq / span, 4),
    }


def _gm_family(program: int | None, channel: int) -> str:
    if channel == 9:
        return "drum-kit"
    if program is None or not 0 <= program <= 127:
        return "unknown"
    return GM_FAMILIES[program // 8]


def _normalize_factory_role(value: object) -> str | None:
    role = str(value or "").lower().replace("_", "-")
    aliases = {
        "drum": "drums", "drums": "drums", "percussion": "percussion",
        "bass": "bass", "guitar": "guitar", "chords": "harmony",
        "chord": "harmony", "harmony": "harmony", "accompaniment": "accompaniment",
        "melody": "solo", "solo": "solo", "lead": "solo", "riff": "riff",
        "power-riff": "riff", "pad": "pad", "strings": "strings",
        "brass": "brass", "woodwind": "woodwind", "sax": "sax",
        "accordion": "accordion", "piano": "piano", "organ": "organ", "choir": "choir",
    }
    return aliases.get(role)


def _name_roles(name: str) -> list[str]:
    lowered = name.lower()
    return [role for role, tokens in _NAME_HINTS.items() if any(token in lowered for token in tokens)]


def _infer_role(
    *, channel: int, family: str, stats: Mapping[str, Any], track_name: str,
    factory_roles: Sequence[str],
) -> tuple[str, float, list[str]]:
    reasons: list[str] = []
    if channel == 9:
        return "drums", 1.0, ["MIDI_CHANNEL_10"]
    exact_roles = [role for role in factory_roles if role]
    if exact_roles:
        role = Counter(exact_roles).most_common(1)[0][0]
        reasons.append("EXACT_FACTORY_ROLE")
        return role, 0.97, reasons
    hints = _name_roles(track_name)
    family_role = {
        "piano": "piano", "organ": "organ", "guitar": "guitar", "bass": "bass",
        "synth-pad": "pad", "ensemble": "choir", "strings": "strings",
        "percussive": "percussion", "chromatic-percussion": "percussion",
        "synth-lead": "solo", "reed": "sax", "pipe": "woodwind", "brass": "brass",
        "ethnic": "solo",
    }.get(family)
    if family_role:
        reasons.append("GM_FAMILY_HINT")
    if hints:
        reasons.append("TRACK_NAME_HINT")
    if hints and family_role and hints[0] == family_role:
        return hints[0], 0.82, reasons
    if family_role:
        return family_role, 0.72, reasons
    if hints:
        return hints[0], 0.68, reasons
    peak = int(stats["polyphonyPeak"])
    median = stats["medianPitch"]
    duration = stats["medianDurationTicks"] or 0
    if median is not None and median < 45 and peak <= 2:
        return "bass", 0.58, ["LOW_REGISTER_HINT"]
    if peak >= 3 and duration >= 1:
        return "harmony", 0.56, ["POLYPHONIC_TEXTURE_HINT"]
    if peak <= 1:
        return "melody", 0.52, ["MONOPHONIC_CONTOUR_HINT"]
    return "unknown", 0.25, ["INSUFFICIENT_EVIDENCE"]


def _catalog(catalog: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    if catalog is None:
        profiles: list[Mapping[str, Any]] = []
        schema = None
        version = None
        database_version = None
    elif isinstance(catalog, Mapping):
        profiles = list(catalog.get("profiles", []))
        schema = catalog.get("schema")
        version = catalog.get("version")
        database_version = catalog.get("databaseVersion")
    else:
        profiles = list(catalog)
        schema = "embedded-profile-list"
        version = "test"
        database_version = None
    melodic: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    drums: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for profile in profiles:
        key = profile.get("instrumentKey")
        if not isinstance(key, str):
            continue
        (drums if profile.get("kind") == "drum" else melodic)[key].append(profile)
    return {
        "available": bool(profiles), "schema": schema, "version": version,
        "databaseVersion": database_version, "profileCount": len(profiles),
        "melodic": melodic, "drums": drums,
    }


def load_factory_catalog(root: str | Path) -> dict[str, Any] | None:
    path = Path(root) / "data" / "factory-velocity-profiles.json"
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != FACTORY_REGISTRY_SCHEMA or not isinstance(document.get("profiles"), list):
        raise ValueError("Factory registry has an unsupported schema")
    return document


def _profile_candidate(profile: Mapping[str, Any], authority: str) -> dict[str, Any]:
    return {
        "instrument": profile.get("instrument") or profile.get("instrument_name") or "Factory sound",
        "instrumentKey": profile.get("instrumentKey"), "profileId": profile.get("id"),
        "role": _normalize_factory_role(profile.get("role")),
        "register": profile.get("register") or {
            "low": profile.get("register_low"), "high": profile.get("register_high")
        },
        "sampleCount": int(profile.get("samples", profile.get("sample_count", 0)) or 0),
        "confidence": round(float(profile.get("confidence", 0.0) or 0.0), 4),
        "authority": authority,
    }


def _segment_profiles(
    profiles: Mapping[str, Any], binding: Mapping[str, Any], notes: Sequence[Note], channel: int
) -> list[dict[str, Any]]:
    if not binding["complete"]:
        return []
    msb, lsb, program = binding["bankMsb"], binding["bankLsb"], binding["program"]
    if channel != 9:
        key = f"melodic:{msb}:{lsb}:{program}"
        return [_profile_candidate(item, "EXACT_FACTORY_SOUND") for item in profiles["melodic"].get(key, [])]
    found: dict[str, dict[str, Any]] = {}
    for pitch in sorted({note.pitch for note in notes}):
        key = f"drum:{msb}:{lsb}:{program}:{pitch}"
        for item in profiles["drums"].get(key, []):
            candidate = _profile_candidate(item, "EXACT_FACTORY_DRUM_NOTE")
            candidate["drumNote"] = pitch
            found[str(candidate.get("profileId") or key)] = candidate
    return list(found.values())


def _decision_hash_payload(tracks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def binding(segment: Mapping[str, Any]) -> dict[str, Any]:
        value = segment["soundBinding"]
        return {key: value[key] for key in (
            "channelIndex", "startTick", "endTick", "bankMsb", "bankLsb",
            "program", "complete", "status",
        )}

    return {
        "tracks": [{
            "trackIndex": track["trackIndex"], "trackName": track["trackName"],
            "decision": track["decision"], "primaryRole": track["primaryRole"],
            "primaryInstrumentFamily": track["primaryInstrumentFamily"],
            "segments": [{
                key: segment[key] for key in (
                    "channelIndex", "startTick", "endTick",
                    "instrumentFamily", "detectedRole", "identityStatus",
                    "decision", "suggestedPa800Track", "noteStatistics",
                )
                } | {"soundBinding": binding(segment)
            } for segment in track["segments"]],
        } for track in tracks],
        "rules": {"analysisVelocityUsed": False, "approximateSoundBindingAllowed": False},
    }


def analyze_track_instruments(
    data: bytes,
    source_name: str = "song.mid",
    *,
    factory_catalog: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, velocity-blind track/instrument evidence report."""

    midi = MidiFile.from_bytes(data)
    notes = midi.notes()
    if not notes:
        raise ValueError("MIDI has no notes")
    identities = build_track_identities(midi)
    catalog = _catalog(factory_catalog)
    end_tick = max(max(note.end for note in notes), max(
        (event.tick for track in midi.tracks for event in track.events), default=1
    ))
    notes_by_track_channel: dict[tuple[int, int], list[Note]] = defaultdict(list)
    for note in notes:
        notes_by_track_channel[(note.track, note.channel)].append(note)
    tracks: list[dict[str, Any]] = []
    manual_review: list[dict[str, Any]] = []
    segment_note_cache: dict[str, list[Note]] = {}
    for identity in identities:
        track_name = _track_name(midi, identity.track_index)
        segments: list[dict[str, Any]] = []
        channels = sorted(channel for track, channel in notes_by_track_channel if track == identity.track_index)
        for channel in channels:
            owners = channel_track_indices(midi, channel)
            bindings = sound_bindings(
                midi, track_index=identity.track_index, channel=channel,
                start_tick=0, end_tick=end_tick + 1, track_uid=identity.track_uid,
            )
            for binding in bindings:
                local = [note for note in notes_by_track_channel[(identity.track_index, channel)]
                         if binding.start_tick <= note.start < binding.end_tick]
                if not local:
                    continue
                manifest = binding.to_manifest()
                candidates = _segment_profiles(catalog, manifest, local, channel)
                stats = _note_stats(local, midi.ppq)
                family = _gm_family(binding.program, channel)
                factory_roles = [candidate["role"] for candidate in candidates if candidate.get("role")]
                role, role_confidence, reasons = _infer_role(
                    channel=channel, family=family, stats=stats, track_name=track_name,
                    factory_roles=factory_roles,
                )
                base_inferred_role = role
                disambiguation = disambiguate_role(
                    local, midi.ppq, family=family, base_role=role, track_name=track_name
                )
                # Generic timbre/family labels never outrank musical behaviour.  This is
                # especially important for guitar sounds used as Solo or PowerChord.
                if role in {"guitar", "harmony", "accompaniment", "melody", "solo", "riff", "unknown"}:
                    if disambiguation.role in {"rhythm-guitar", "power-riff", "solo"} and disambiguation.confidence >= .68:
                        role = disambiguation.role
                        role_confidence = (max(role_confidence, disambiguation.confidence)
                                           if base_inferred_role == disambiguation.role
                                           else disambiguation.confidence)
                        reasons.extend(disambiguation.reasons)
                exact = bool(candidates)
                if channel == 9:
                    exact = exact and {item.get("drumNote") for item in candidates} == {
                        note.pitch for note in local
                    }
                shared = len(owners) > 1
                if shared:
                    identity_status = "SHARED_CHANNEL_REVIEW"
                    decision = "MANUAL_REVIEW"
                    reasons.append("SHARED_CHANNEL_CONFLICT")
                elif exact:
                    identity_status = "EXACT_FACTORY_DRUM_NOTES" if channel == 9 else "EXACT_FACTORY_SOUND"
                    decision = "ACCEPT" if role_confidence >= ACCEPT_CONFIDENCE else "MANUAL_REVIEW"
                elif binding.complete:
                    identity_status = (
                        "PARTIAL_FACTORY_DRUM_MAP" if channel == 9 and candidates
                        else "GM_FAMILY_ONLY"
                    )
                    decision = "MANUAL_REVIEW"
                    reasons.append("NO_EXACT_FACTORY_PROFILE")
                else:
                    identity_status = "UNRESOLVED_SOUND_BINDING"
                    decision = "MANUAL_REVIEW"
                    reasons.append("INCOMPLETE_BANK_PROGRAM")
                identity_confidence = 0.99 if exact else (0.68 if binding.complete else 0.25)
                if shared:
                    identity_confidence = min(identity_confidence, 0.49)
                segment_id = f"{identity.track_uid}-ch{channel + 1}-{binding.start_tick}"
                segment_note_cache[segment_id] = list(local)
                segment = {
                    "segmentId": segment_id,
                    "trackUid": identity.track_uid, "trackIndex": identity.track_index,
                    "trackNumber": identity.track_number, "channelIndex": channel,
                    "channelNumber": channel + 1, "startTick": binding.start_tick,
                    "endTick": binding.end_tick, "soundBinding": manifest,
                    "instrumentFamily": family,
                    "primaryInstrument": candidates[0]["instrument"] if candidates else family,
                    "factoryCandidates": sorted(candidates, key=lambda item: (
                        -item["sampleCount"], str(item.get("profileId"))
                    ))[:8],
                    "detectedRole": role, "identityConfidence": round(identity_confidence, 4),
                    "instrumentBehavior": json.loads(json.dumps(asdict(behavior_profile(role)))),
                    "completeInstrumentProfile": profile_document(role),
                    "roleConfidence": round(role_confidence, 4),
                    "identityStatus": identity_status, "decision": decision,
                    "suggestedPa800Track": _PA800_TRACK.get(role),
                    "noteStatistics": stats, "evidenceReasons": sorted(set(reasons)),
                    "roleDisambiguation": disambiguation.to_dict(),
                    "echoDetectionPolicy": {
                        "relationshipRequired": True,
                        "strummingVeto": True,
                        "standaloneRepetitionIsNotEchoEvidence": True,
                    },
                    "authority": {
                        "instrumentIdentity": "FACTORY_EXACT" if exact else "HINT_ONLY",
                        "role": "FACTORY_EXACT" if factory_roles else "HEURISTIC_HINT",
                        "midiMutation": "NONE", "dynamics": "NONE", "device": "NONE",
                    },
                }
                segments.append(segment)
                if decision != "ACCEPT":
                    manual_review.append({
                        "code": identity_status, "trackUid": identity.track_uid,
                        "trackNumber": identity.track_number, "channelNumber": channel + 1,
                        "startTick": binding.start_tick, "endTick": binding.end_tick,
                        "reason": ",".join(sorted(set(reasons))),
                        "recoveryAction": "Confirm exact SoundBinding or assign the track role manually.",
                    })
        note_count = sum(segment["noteStatistics"]["noteCount"] for segment in segments)
        primary = max(segments, key=lambda item: item["noteStatistics"]["noteCount"], default=None)
        track_decision = (
            "ACCEPT" if segments and all(item["decision"] == "ACCEPT" for item in segments)
            else ("IGNORE_METADATA" if not segments else "MANUAL_REVIEW")
        )
        if len(channels) > 1:
            track_decision = "MANUAL_REVIEW"
            manual_review.append({
                "code": "MULTI_CHANNEL_PHYSICAL_TRACK", "trackUid": identity.track_uid,
                "trackNumber": identity.track_number, "channelNumbers": [value + 1 for value in channels],
                "reason": "One physical track contains multiple MIDI channels.",
                "recoveryAction": "Review each channel segment before track-level optimization.",
            })
        tracks.append({
            **identity.to_manifest(), "trackName": track_name, "noteCount": note_count,
            "channelIndices": channels, "channelNumbers": [value + 1 for value in channels],
            "segmentCount": len(segments), "segments": segments,
            "primaryInstrument": primary["primaryInstrument"] if primary else None,
            "primaryInstrumentFamily": primary["instrumentFamily"] if primary else "unknown",
            "primaryRole": primary["detectedRole"] if primary else "unknown",
            "suggestedPa800Track": primary["suggestedPa800Track"] if primary else None,
            "decision": track_decision,
            "optimizationPolicy": (
                "FACTORY_BOUNDED_SAFE" if track_decision == "ACCEPT"
                else ("KEEP_METADATA" if track_decision == "IGNORE_METADATA"
                      else "ANALYSIS_ONLY_MANUAL_REVIEW")
            ),
        })
    # Second pass: Echo is a relationship role, never a texture/program role.
    solo_segments = [seg for tr in tracks for seg in tr["segments"] if seg.get("detectedRole") == "solo"]
    for tr in tracks:
        for seg in tr["segments"]:
            target_notes = segment_note_cache.get(seg["segmentId"], [])
            checks = []
            if seg.get("detectedRole") in {"rhythm-guitar", "power-riff"} or seg.get("roleDisambiguation", {}).get("signature", {}).get("strum_like"):
                seg["echoRelationship"] = {"isEcho": False, "confidence": 0.0, "reason": "STRUMMING_OR_POWER_VETO"}
                continue
            for src in solo_segments:
                if src["segmentId"] == seg["segmentId"]:
                    continue
                source_notes = segment_note_cache.get(src["segmentId"], [])
                relation = echo_relationship(source_notes, target_notes, midi.ppq)
                relation["sourceSegmentId"] = src["segmentId"]
                checks.append(relation)
            best = max(checks, key=lambda x: float(x.get("confidence", 0.0)), default={"isEcho": False, "confidence": 0.0, "reason": "NO_CONFIRMED_SOLO_SOURCE"})
            seg["echoRelationship"] = best
            if best.get("isEcho") and float(best.get("confidence", 0.0)) >= .82 and seg.get("detectedRole") in {"unknown", "accompaniment", "melody", "solo"}:
                seg["detectedRole"] = "echo"
                seg["roleConfidence"] = round(float(best["confidence"]), 4)
                seg["evidenceReasons"] = sorted(set(seg.get("evidenceReasons", []) + ["CONFIRMED_SOLO_ECHO_RELATIONSHIP"]))
                seg["suggestedPa800Track"] = None
                seg["instrumentBehavior"] = json.loads(json.dumps(asdict(behavior_profile("echo"))))
                seg["completeInstrumentProfile"] = profile_document("echo")
    # Recompute track-level primary role after relationship-only Echo resolution.
    for tr in tracks:
        primary = max(tr["segments"], key=lambda item: item["noteStatistics"]["noteCount"], default=None)
        if primary is not None:
            tr["primaryRole"] = primary["detectedRole"]
            tr["suggestedPa800Track"] = primary["suggestedPa800Track"]

    accepted = sum(track["decision"] == "ACCEPT" for track in tracks)
    review_count = sum(track["decision"] == "MANUAL_REVIEW" for track in tracks)
    report: dict[str, Any] = {
        "schema": TRACK_ANALYSIS_SCHEMA, "version": TRACK_ANALYSIS_VERSION,
        "source": {"fileName": source_name, "bytes": len(data), "format": midi.format_type,
                   "ppq": midi.ppq, "trackCount": len(midi.tracks), "noteCount": len(notes)},
        "sourceSha256": sha256(data).hexdigest(), "endTick": end_tick,
        "factoryRegistry": {
            key: catalog[key] for key in ("available", "schema", "version", "databaseVersion", "profileCount")
        },
        "tracks": tracks, "manualReview": manual_review,
        "summary": {
            "physicalTrackCount": len(tracks), "noteTrackCount": sum(bool(track["noteCount"]) for track in tracks),
            "segmentCount": sum(track["segmentCount"] for track in tracks),
            "acceptedTrackCount": accepted, "manualReviewTrackCount": review_count,
            "metadataTrackCount": sum(track["decision"] == "IGNORE_METADATA" for track in tracks),
            "exactFactorySegmentCount": sum(
                segment["authority"]["instrumentIdentity"] == "FACTORY_EXACT"
                for track in tracks for segment in track["segments"]
            ),
        },
        "applicationOptimizationBaseline": {
            "safeAutomaticTracks": [track["trackUid"] for track in tracks if track["decision"] == "ACCEPT"],
            "protectedOrReviewTracks": [track["trackUid"] for track in tracks if track["decision"] == "MANUAL_REVIEW"],
            "downstreamPolicy": "Only ACCEPT tracks may enter automatic TrackPlan; all others stay KEEP/MANUAL_REVIEW.",
            "fullOptimizationStage": "PLANNED_AFTER_EVIDENCE_RESOLUTION",
        },
        "invariants": {
            "readOnly": True, "analysisVelocityUsed": False, "goldUsed": False,
            "goldAffectsDynamics": False, "approximateSoundBindingAllowed": False,
            "trackLocalSoundState": True, "midSongProgramChangesSegmented": True,
            "sharedChannelAutoAcceptAllowed": False, "originalMidiChanged": False,
            "midiMutationAuthority": False, "validatorAuthority": False,
            "deviceCertificationClaimed": False,
        },
    }
    report["decisionHash"] = sha256(_canonical(_decision_hash_payload(tracks))).hexdigest()
    report["analysisHash"] = _hash(report, "analysisHash")
    validate_track_instrument_analysis(report)
    return report


def validate_track_instrument_analysis(report: Mapping[str, Any]) -> None:
    required = {
        "schema", "version", "source", "sourceSha256", "endTick", "factoryRegistry",
        "tracks", "manualReview", "summary", "applicationOptimizationBaseline",
        "invariants", "decisionHash", "analysisHash",
    }
    if set(report) != required:
        raise ValueError("Track analysis has unknown or missing top-level fields")
    if report["schema"] != TRACK_ANALYSIS_SCHEMA or report["version"] != TRACK_ANALYSIS_VERSION:
        raise ValueError("Unsupported track analysis contract")
    if not isinstance(report["tracks"], list) or not report["tracks"]:
        raise ValueError("Track analysis must contain physical tracks")
    for field in ("sourceSha256", "decisionHash", "analysisHash"):
        value = report[field]
        if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError(f"Invalid {field}")
    invariants = report["invariants"]
    if invariants.get("analysisVelocityUsed") is not False or invariants.get("originalMidiChanged") is not False:
        raise ValueError("Track analysis violated read-only/velocity-blind invariants")
    if invariants.get("approximateSoundBindingAllowed") is not False:
        raise ValueError("Approximate SoundBinding is forbidden")
    if report["analysisHash"] != _hash(report, "analysisHash"):
        raise ValueError("Track analysis hash mismatch")


def execute_track_instrument_analysis_api(payload: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    if set(payload) - {"action", "midiHex", "sourceName"}:
        raise ValueError("Track analysis request contains unsupported fields")
    if payload.get("action") != "analyze":
        raise ValueError("Track analysis supports only action=analyze")
    midi_hex = payload.get("midiHex")
    if not isinstance(midi_hex, str) or not midi_hex:
        raise ValueError("midiHex is required")
    if len(midi_hex) > 64_000_000 or len(midi_hex) % 2:
        raise ValueError("midiHex is invalid or too large")
    try:
        raw = bytes.fromhex(midi_hex)
    except ValueError as error:
        raise ValueError("midiHex is not valid hexadecimal") from error
    source_name = payload.get("sourceName", "song.mid")
    if not isinstance(source_name, str) or not source_name.lower().endswith((".mid", ".midi")):
        raise ValueError("sourceName must be a MIDI filename")
    return analyze_track_instruments(raw, source_name, factory_catalog=load_factory_catalog(root))


def execute_track_instrument_analysis_gui(payload: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    return execute_track_instrument_analysis_api(payload, root)