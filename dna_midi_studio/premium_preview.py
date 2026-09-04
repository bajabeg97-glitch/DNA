"""Session 26 validation-neutral Premium Preview 2.0.

The preview layer is deliberately downstream from MIDI generation and
verification.  It may filter, loop, synthesize and loudness-match a view of a
verified MIDI file, but it never returns MIDI bytes and cannot alter the
validator identity bound to that file.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
import math
import re
import struct
import wave
from typing import Any, Mapping, Sequence

from .midi import MidiFile
from .track_identity import build_track_identities


PREVIEW_SESSION_SCHEMA = "dna-premium-preview-session"
PREVIEW_SESSION_VERSION = "2.0"
PREVIEW_CONTROLS_VERSION = "1.0"
DEVICE_AUDIO_CAPTURE_SCHEMA = "dna-premium-device-audio-capture"
DEVICE_AUDIO_CAPTURE_VERSION = "1.0"
AUDIO_RENDER_ADAPTER_SCHEMA = "dna-premium-audio-render-adapter"
AUDIO_RENDER_ADAPTER_VERSION = "1.0"

PREVIEW_VARIANTS = ("A", "B", "C")
PREVIEW_PROFILES = ("GM_PROXY_V1", "PA800_PROXY_V1")
EXTERNAL_ADAPTER_MODES = ("DISABLED", "SOUNDFONT", "COMMAND")
_ROLES = {"drums", "percussion", "bass", "guitar", "accompaniment", "chords", "harmony",
          "riff", "pad", "solo", "melody", "unknown"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TRACK_UID = re.compile(r"^trk-[0-9a-f]{20}(?:-[1-9][0-9]*)?$")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


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


def _number(value: Any, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not low <= result <= high:
        raise ValueError(f"{label} must be in range {low}..{high}")
    return result


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _string_list(value: Any, label: str, allowed: set[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or item not in allowed for item in value):
        raise ValueError(f"{label} contains an unsupported value")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} must not contain duplicates")
    return tuple(value)


@dataclass(frozen=True)
class PreviewControls:
    version: str = PREVIEW_CONTROLS_VERSION
    profile_id: str = "GM_PROXY_V1"
    variants: tuple[str, ...] = PREVIEW_VARIANTS
    loop_section_id: str | None = None
    solo_roles: tuple[str, ...] = ()
    muted_roles: tuple[str, ...] = ()
    target_rms_dbfs: float = -20.0
    sample_rate: int = 12000
    max_audio_seconds: int = 12
    master_gain_db: float = 0.0
    external_adapter: Mapping[str, Any] | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "PreviewControls":
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("Preview controls must be an object")
        fields = {"version", "profileId", "variants", "loopSectionId", "soloRoles",
                  "mutedRoles", "targetRmsDbfs", "sampleRate", "maxAudioSeconds",
                  "masterGainDb", "externalAdapter"}
        _require_keys(value, fields, {"version"}, "preview control")
        if value["version"] != PREVIEW_CONTROLS_VERSION:
            raise ValueError("Unsupported preview controls version")
        profile = value.get("profileId", "GM_PROXY_V1")
        if profile not in PREVIEW_PROFILES:
            raise ValueError("Unsupported preview profile")
        variants = _string_list(value.get("variants", list(PREVIEW_VARIANTS)), "variants",
                                set(PREVIEW_VARIANTS))
        if not variants:
            raise ValueError("At least one preview variant is required")
        loop = value.get("loopSectionId")
        if loop is not None and (not isinstance(loop, str) or not re.fullmatch(r"sec-[0-9]{3}", loop)):
            raise ValueError("loopSectionId must be null or a section ID")
        solo = _string_list(value.get("soloRoles", []), "soloRoles", _ROLES)
        muted = _string_list(value.get("mutedRoles", []), "mutedRoles", _ROLES)
        if set(solo) & set(muted):
            raise ValueError("A role cannot be both soloed and muted")
        adapter = validate_audio_render_adapter(value.get("externalAdapter"))
        return cls(
            version=value["version"], profile_id=profile, variants=variants,
            loop_section_id=loop, solo_roles=solo, muted_roles=muted,
            target_rms_dbfs=_number(value.get("targetRmsDbfs", -20.0), "targetRmsDbfs", -30, -12),
            sample_rate=_integer(value.get("sampleRate", 12000), "sampleRate", 8000, 48000),
            max_audio_seconds=_integer(value.get("maxAudioSeconds", 12), "maxAudioSeconds", 1, 60),
            master_gain_db=_number(value.get("masterGainDb", 0.0), "masterGainDb", -24, 12),
            external_adapter=adapter,
        )

    def to_manifest(self) -> dict[str, Any]:
        return {
            "version": self.version, "profileId": self.profile_id,
            "variants": list(self.variants), "loopSectionId": self.loop_section_id,
            "soloRoles": list(self.solo_roles), "mutedRoles": list(self.muted_roles),
            "targetRmsDbfs": self.target_rms_dbfs, "sampleRate": self.sample_rate,
            "maxAudioSeconds": self.max_audio_seconds, "masterGainDb": self.master_gain_db,
            "externalAdapter": self.external_adapter,
        }


def validate_audio_render_adapter(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        value = {"schema": AUDIO_RENDER_ADAPTER_SCHEMA, "version": AUDIO_RENDER_ADAPTER_VERSION,
                 "mode": "DISABLED", "rendererId": "builtin-proxy",
                 "executableSha256": None, "soundfontSha256": None}
    if not isinstance(value, Mapping):
        raise ValueError("External audio adapter must be an object")
    fields = {"schema", "version", "mode", "rendererId", "executableSha256", "soundfontSha256"}
    _require_keys(value, fields, fields, "audio adapter")
    if value["schema"] != AUDIO_RENDER_ADAPTER_SCHEMA or value["version"] != AUDIO_RENDER_ADAPTER_VERSION:
        raise ValueError("Unsupported audio adapter contract")
    if value["mode"] not in EXTERNAL_ADAPTER_MODES:
        raise ValueError("Unsupported audio adapter mode")
    if not isinstance(value["rendererId"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", value["rendererId"]):
        raise ValueError("rendererId must be a controlled identifier")
    for key in ("executableSha256", "soundfontSha256"):
        if value[key] is not None:
            _sha(value[key], key)
    if value["mode"] == "COMMAND" and value["executableSha256"] is None:
        raise ValueError("COMMAND adapter requires executableSha256")
    if value["mode"] == "SOUNDFONT" and value["soundfontSha256"] is None:
        raise ValueError("SOUNDFONT adapter requires soundfontSha256")
    # Paths and command lines are intentionally absent from the contract.
    return dict(value)


def _tempo_segments(midi: MidiFile) -> list[tuple[int, int]]:
    changes = [(0, 500000)]
    for track in midi.tracks:
        for event in track.events:
            if event.kind == "meta" and event.meta_type == 0x51 and len(event.data) == 3:
                changes.append((event.tick, int.from_bytes(event.data, "big")))
    by_tick = {tick: tempo for tick, tempo in sorted(changes)}
    return sorted(by_tick.items())


def _tick_to_seconds(tick: int, ppq: int, tempos: Sequence[tuple[int, int]]) -> float:
    total = 0.0
    previous_tick, tempo = tempos[0]
    for change_tick, next_tempo in tempos[1:]:
        if tick <= change_tick:
            break
        total += (change_tick - previous_tick) * tempo / 1_000_000 / ppq
        previous_tick, tempo = change_tick, next_tempo
    total += max(0, tick - previous_tick) * tempo / 1_000_000 / ppq
    return round(total, 6)


def _role_for(song_map: Mapping[str, Any], track_uid: str, channel_number: int, tick: int) -> str:
    matches = [item for item in song_map.get("roleSegments", [])
               if item.get("trackUid") == track_uid and item.get("channelNumber") == channel_number
               and item.get("startTick", 0) <= tick < item.get("endTick", 0)
               and item.get("decision", "ACCEPT") == "ACCEPT"]
    if matches:
        role = str(matches[0].get("role", "unknown")).lower()
        return role if role in _ROLES else "unknown"
    if channel_number == 10:
        return "drums"
    if channel_number == 11:
        return "percussion"
    return "unknown"


def _sound_manifest(midi: MidiFile, track: int, channel: int, tick: int) -> dict[str, Any]:
    sound = midi.sound_at(channel, tick, track)
    return {"bankMsb": sound[0], "bankLsb": sound[1], "program": sound[2], "status": "EXACT"} \
        if sound is not None else {"bankMsb": None, "bankLsb": None, "program": None, "status": "UNRESOLVED"}


def _source_notes(midi: MidiFile, song_map: Mapping[str, Any]) -> list[dict[str, Any]]:
    identities = build_track_identities(midi)
    tempos = _tempo_segments(midi)
    result = []
    for index, note in enumerate(midi.notes()):
        identity = identities[note.track]
        role = _role_for(song_map, identity.track_uid, note.channel + 1, note.start)
        item = {
            "noteUid": "preview-src-" + sha256(_canonical([identity.track_uid, note.channel,
                                                              note.start, note.end, note.pitch,
                                                              note.velocity, index])).hexdigest()[:20],
            "source": "ORIGINAL_MIDI", "layer": "BASELINE", "trackUid": identity.track_uid,
            "trackIndex": note.track, "trackNumber": note.track + 1,
            "channelIndex": note.channel, "channelNumber": note.channel + 1,
            "role": role, "startTick": note.start, "endTick": note.end,
            "startSeconds": _tick_to_seconds(note.start, midi.ppq, tempos),
            "endSeconds": _tick_to_seconds(note.end, midi.ppq, tempos),
            "pitch": note.pitch, "velocity": note.velocity,
            "soundBinding": _sound_manifest(midi, note.track, note.channel, note.start),
            "productionEligible": True,
        }
        item["noteHash"] = sha256(_canonical(item)).hexdigest()
        result.append(item)
    return result


def _generated_expression_notes(midi: MidiFile, expression_plan: Mapping[str, Any] | None,
                                song_map: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not expression_plan:
        return []
    tempos = _tempo_segments(midi)
    identities = {identity.track_uid: identity for identity in build_track_identities(midi)}
    result = []
    for layer in expression_plan.get("layers", []):
        subtype = str(layer.get("subtype", "unknown"))
        for event in layer.get("events", []):
            uid = event.get("trackUid")
            identity = identities.get(uid)
            if identity is None:
                continue
            start = int(event.get("onsetTick", -1))
            duration = int(event.get("durationTick", 0))
            pitch = int(event.get("pitch", -1))
            velocity = int(event.get("velocity", 0))
            channel_number = int(event.get("channelNumber", 0))
            if start < 0 or duration <= 0 or not 0 <= pitch <= 127 or not 1 <= velocity <= 127 \
                    or not 1 <= channel_number <= 16:
                continue
            role = _role_for(song_map, uid, channel_number, start)
            item = {
                "noteUid": str(event.get("eventId", "preview-exp-" + sha256(_canonical(event)).hexdigest()[:20])),
                "source": "AI_EXPRESSION", "layer": subtype, "trackUid": uid,
                "trackIndex": identity.track_index, "trackNumber": identity.track_number,
                "channelIndex": channel_number - 1, "channelNumber": channel_number,
                "role": role, "startTick": start, "endTick": start + duration,
                "startSeconds": _tick_to_seconds(start, midi.ppq, tempos),
                "endSeconds": _tick_to_seconds(start + duration, midi.ppq, tempos),
                "pitch": pitch, "velocity": velocity,
                "soundBinding": _sound_manifest(midi, identity.track_index, channel_number - 1, start),
                "productionEligible": bool(event.get("productionEligible", False)),
                "evidenceId": event.get("evidenceId"), "reasonCode": event.get("reasonCode"),
            }
            item["noteHash"] = sha256(_canonical(item)).hexdigest()
            result.append(item)
    return sorted(result, key=lambda item: (item["startTick"], item["trackIndex"], item["pitch"]))


def _articulation_events(plans: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    result = []
    for plan in plans or []:
        for event in plan.get("events", []):
            result.append({
                "eventId": event.get("eventId"), "source": "AI_ARTICULATION",
                "engine": event.get("engine"), "articulation": event.get("articulation"),
                "eventType": event.get("eventType"), "tick": event.get("tick"),
                "noteOffTick": event.get("noteOffTick"), "trackUid": event.get("trackUid"),
                "trackNumber": event.get("trackNumber"), "channelNumber": event.get("channelNumber"),
                "sourceNoteUid": event.get("sourceNoteUid"),
                "sourceEvidenceId": event.get("sourceEvidenceId"),
                "productionEligible": bool(event.get("productionEligible", False)),
                "audibleProxy": False,
            })
    return sorted(result, key=lambda item: (item.get("tick", 0), str(item.get("eventId"))))


def _passes_mix(note: Mapping[str, Any], controls: PreviewControls) -> bool:
    role = note["role"]
    if controls.solo_roles and role not in controls.solo_roles:
        return False
    return role not in controls.muted_roles


def _polyphony(notes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    changes: list[tuple[int, int, Mapping[str, Any]]] = []
    for note in notes:
        changes.append((int(note["startTick"]), 1, note))
        changes.append((int(note["endTick"]), -1, note))
    # Note-off precedes note-on at the same tick.
    changes.sort(key=lambda item: (item[0], item[1]))
    total = 0
    by_role: Counter[str] = Counter()
    by_track: Counter[str] = Counter()
    by_channel: Counter[int] = Counter()
    peaks = {"global": 0, "roles": Counter(), "tracks": Counter(), "channels": Counter()}
    timeline = []
    for tick, delta, note in changes:
        total += delta
        by_role[note["role"]] += delta
        by_track[note["trackUid"]] += delta
        by_channel[note["channelNumber"]] += delta
        peaks["global"] = max(peaks["global"], total)
        peaks["roles"][note["role"]] = max(peaks["roles"][note["role"]], by_role[note["role"]])
        peaks["tracks"][note["trackUid"]] = max(peaks["tracks"][note["trackUid"]], by_track[note["trackUid"]])
        peaks["channels"][note["channelNumber"]] = max(peaks["channels"][note["channelNumber"]], by_channel[note["channelNumber"]])
        timeline.append({"tick": tick, "active": total})
    compact = []
    for item in timeline:
        if compact and compact[-1]["tick"] == item["tick"]:
            compact[-1] = item
        elif not compact or compact[-1]["active"] != item["active"]:
            compact.append(item)
    return {"globalPeak": peaks["global"], "byRole": dict(sorted(peaks["roles"].items())),
            "byTrackUid": dict(sorted(peaks["tracks"].items())),
            "byChannelNumber": {str(key): value for key, value in sorted(peaks["channels"].items())},
            "timeline": compact}


def _proxy_rms_dbfs(notes: Sequence[Mapping[str, Any]], duration: float) -> float:
    if not notes or duration <= 0:
        return -96.0
    energy = sum(((note["velocity"] / 127.0) ** 2) *
                 max(0.001, note["endSeconds"] - note["startSeconds"]) for note in notes)
    rms = math.sqrt(energy / max(duration, 0.001)) * 0.12
    return round(20 * math.log10(max(rms, 10 ** (-96 / 20))), 3)


def _loop_window(song_map: Mapping[str, Any], controls: PreviewControls, end_tick: int) -> dict[str, Any]:
    if controls.loop_section_id is None:
        return {"enabled": False, "sectionId": None, "startTick": 0, "endTick": end_tick}
    matches = [item for item in song_map.get("sections", []) if item.get("id") == controls.loop_section_id]
    if len(matches) != 1:
        raise ValueError("Requested loop section is not present in SongMap")
    item = matches[0]
    start, end = int(item["startTick"]), int(item["endTick"])
    if start < 0 or end <= start or end > end_tick:
        raise ValueError("Loop section has an invalid range")
    return {"enabled": True, "sectionId": controls.loop_section_id,
            "label": item.get("label", "section"), "startTick": start, "endTick": end}


def _validator_identity(midi: MidiFile, verdict: Mapping[str, Any] | None) -> dict[str, Any]:
    midi_hash = midi.digest()
    if verdict is None:
        verdict = {"passed": True, "finalMidiSha256": midi_hash,
                   "reportHash": sha256(_canonical({"scope": "STRUCTURAL_PREVIEW_ONLY",
                                                     "midi": midi_hash})).hexdigest(),
                   "scope": "STRUCTURAL_PREVIEW_ONLY"}
    if not isinstance(verdict, Mapping):
        raise ValueError("validatorVerdict must be an object")
    fields = {"passed", "finalMidiSha256", "reportHash", "scope"}
    _require_keys(verdict, fields, fields, "validator verdict")
    if verdict["passed"] is not True:
        raise ValueError("Preview requires a passing validator verdict")
    if _sha(verdict["finalMidiSha256"], "finalMidiSha256") != midi_hash:
        raise ValueError("Validator verdict does not belong to this MIDI")
    _sha(verdict["reportHash"], "reportHash")
    if verdict["scope"] not in {"FINAL_MIDI", "STRUCTURAL_PREVIEW_ONLY"}:
        raise ValueError("Unsupported validator verdict scope")
    identity = dict(verdict)
    identity["identityHash"] = sha256(_canonical(identity)).hexdigest()
    return identity


def build_preview_session(
    midi: MidiFile,
    song_map: Mapping[str, Any],
    groove_plan: Mapping[str, Any] | None = None,
    expression_plan: Mapping[str, Any] | None = None,
    articulation_plans: Sequence[Mapping[str, Any]] | None = None,
    validator_verdict: Mapping[str, Any] | None = None,
    controls: PreviewControls | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a synchronized, read-only A/B/C preview session."""

    controls = controls if isinstance(controls, PreviewControls) else PreviewControls.from_dict(controls)
    if song_map.get("schema") != "dna-premium-song-map" or song_map.get("version") != "2.0":
        raise ValueError("Preview requires SongMap 2.0")
    if song_map.get("sourceSha256") not in {None, midi.digest()}:
        raise ValueError("SongMap does not belong to this MIDI")
    verdict = _validator_identity(midi, validator_verdict)
    source_notes = _source_notes(midi, song_map)
    expression_notes = _generated_expression_notes(midi, expression_plan, song_map)
    articulation = _articulation_events(articulation_plans)
    end_tick = max([int(song_map.get("endTick", 0)), *(int(note["endTick"]) for note in source_notes)], default=0)
    if end_tick <= 0:
        raise ValueError("Preview MIDI contains no positive timeline")
    tempos = _tempo_segments(midi)
    loop = _loop_window(song_map, controls, end_tick)
    variants = []
    for variant_id in controls.variants:
        if variant_id == "A":
            added = []
        elif variant_id == "B":
            added = [note for note in expression_notes if note["layer"] != "echo"]
        else:
            added = list(expression_notes)
        all_notes = sorted([*source_notes, *added],
                           key=lambda item: (item["startTick"], item["trackIndex"], item["pitch"], item["source"]))
        audible = [note for note in all_notes if _passes_mix(note, controls)
                   and (not loop["enabled"] or
                        (note["endTick"] > loop["startTick"] and note["startTick"] < loop["endTick"]))]
        duration = (_tick_to_seconds(loop["endTick"], midi.ppq, tempos) -
                    _tick_to_seconds(loop["startTick"], midi.ppq, tempos)) \
            if loop["enabled"] else _tick_to_seconds(end_tick, midi.ppq, tempos)
        measured = _proxy_rms_dbfs(audible, duration)
        match_gain = max(-18.0, min(18.0, controls.target_rms_dbfs - measured)) if measured > -90 else 0.0
        variants.append({
            "variantId": variant_id,
            "label": {"A": "Verified baseline", "B": "Expression", "C": "Full preview"}[variant_id],
            "clockId": "preview-clock-" + verdict["identityHash"][:16],
            "notes": all_notes, "audibleNoteCount": len(audible),
            "filteredNoteCount": len(all_notes) - len(audible),
            "articulationEvents": articulation if variant_id == "C" else [],
            "polyphony": _polyphony(audible),
            "loudness": {"method": "MIDI_ENERGY_RMS_PROXY", "measuredRmsDbfs": measured,
                         "targetRmsDbfs": controls.target_rms_dbfs,
                         "matchGainDb": round(match_gain, 3),
                         "masterGainDb": controls.master_gain_db,
                         "affectsMidi": False},
        })
    session = {
        "schema": PREVIEW_SESSION_SCHEMA, "version": PREVIEW_SESSION_VERSION,
        "source": {"midiSha256": midi.digest(), "songMapHash": song_map.get("mapHash"),
                   "groovePlanHash": (groove_plan or {}).get("groovePlanHash"),
                   "expressionPlanHash": (expression_plan or {}).get("expressionPlanHash"),
                   "articulationPlanHashes": sorted(plan.get("articulationPlanHash") for plan in articulation_plans or []
                                                    if plan.get("articulationPlanHash"))},
        "validatorIdentity": verdict, "controls": controls.to_manifest(),
        "transport": {"synchronized": True, "variantIds": list(controls.variants),
                      "startSeconds": _tick_to_seconds(loop["startTick"], midi.ppq, tempos),
                      "endSeconds": _tick_to_seconds(loop["endTick"], midi.ppq, tempos),
                      "seekResolutionTicks": 1, "loop": loop},
        "variants": variants,
        "audioSeparation": {
            "midiStructuralPreview": "VERIFIED_SOURCE_VIEW",
            "builtinAudio": "GM_PA800_PROXY_NOT_DEVICE_AUDIO",
            "externalRenderer": "OPTIONAL_MANIFEST_ONLY_NOT_EXECUTED",
            "deviceCapture": "COMPARISON_ONLY_REQUIRES_SESSION16_FOR_CERTIFICATION",
        },
        "warnings": ["Preview audio is not a Korg Pa800 certification.",
                     "Articulation controls are annotations unless confirmed by physical-device capture."],
        "audit": {"sourceNoteCount": len(source_notes), "expressionNoteCount": len(expression_notes),
                  "articulationEventCount": len(articulation),
                  "variantCount": len(variants),
                  "maximumFullDurationPeak": max(item["polyphony"]["globalPeak"] for item in variants),
                  "allVariantsWithinMidiNoteCeiling": all(item["polyphony"]["globalPeak"] <= 54 for item in variants),
                  "exactTrackUidCoverage": all(_TRACK_UID.fullmatch(note["trackUid"]) for note in source_notes),
                  "soundBindingCoverage": all("soundBinding" in note for item in variants for note in item["notes"]),
                  "layerSourceCoverage": all(note.get("source") in {"ORIGINAL_MIDI", "AI_EXPRESSION"}
                                             for item in variants for note in item["notes"])},
        "readOnly": True, "midiMutationAllowed": False, "finalMidiGenerated": False,
        "validatorVerdictMutableByPreview": False, "pa800DeviceCertified": False,
    }
    session["previewSessionHash"] = sha256(_canonical(session)).hexdigest()
    validate_preview_session_v2(session)
    return session


def validate_preview_session_v2(session: Mapping[str, Any]) -> None:
    if not isinstance(session, Mapping):
        raise ValueError("Preview session must be an object")
    fields = {"schema", "version", "source", "validatorIdentity", "controls", "transport",
              "variants", "audioSeparation", "warnings", "audit", "readOnly",
              "midiMutationAllowed", "finalMidiGenerated", "validatorVerdictMutableByPreview",
              "pa800DeviceCertified", "previewSessionHash"}
    _require_keys(session, fields, fields, "preview session")
    if session["schema"] != PREVIEW_SESSION_SCHEMA or session["version"] != PREVIEW_SESSION_VERSION:
        raise ValueError("Unsupported preview session contract")
    if session["readOnly"] is not True or session["midiMutationAllowed"] is not False \
            or session["finalMidiGenerated"] is not False:
        raise ValueError("Preview session cannot mutate or generate final MIDI")
    if session["validatorVerdictMutableByPreview"] is not False or session["pa800DeviceCertified"] is not False:
        raise ValueError("Preview cannot change validator verdict or certify Pa800")
    _sha(session["source"]["midiSha256"], "source.midiSha256")
    if session["validatorIdentity"]["finalMidiSha256"] != session["source"]["midiSha256"]:
        raise ValueError("Preview source and validator identity mismatch")
    ids = [item.get("variantId") for item in session["variants"]]
    if ids != session["controls"]["variants"] or len(ids) != len(set(ids)):
        raise ValueError("Preview variants do not match controls")
    clocks = {item.get("clockId") for item in session["variants"]}
    if len(clocks) != 1 or session["transport"]["synchronized"] is not True:
        raise ValueError("Preview variants must share one synchronized clock")
    if session["previewSessionHash"] != _hash_without(session, "previewSessionHash"):
        raise ValueError("Preview session hash mismatch")


def _waveform(profile: str, role: str, phase: float) -> float:
    if role in {"drums", "percussion"}:
        return math.sin(phase) * math.exp(-((phase % (2 * math.pi)) / math.pi))
    if profile == "PA800_PROXY_V1":
        return 0.72 * math.sin(phase) + 0.18 * math.sin(2 * phase) + 0.10 * math.sin(3 * phase)
    return math.sin(phase)


def render_preview_wav(session: Mapping[str, Any], variant_id: str) -> tuple[bytes, dict[str, Any]]:
    """Render a small deterministic PCM proxy; no external command is executed."""

    validate_preview_session_v2(session)
    variants = {item["variantId"]: item for item in session["variants"]}
    if variant_id not in variants:
        raise ValueError("Requested variant is not present in preview session")
    controls = PreviewControls.from_dict(session["controls"])
    variant = variants[variant_id]
    start_seconds = float(session["transport"]["startSeconds"])
    end_seconds = min(float(session["transport"]["endSeconds"]),
                      start_seconds + controls.max_audio_seconds)
    duration = max(0.001, end_seconds - start_seconds)
    frame_count = max(1, int(duration * controls.sample_rate))
    samples = [0.0] * frame_count
    active_notes = [note for note in variant["notes"] if _passes_mix(note, controls)
                    and note["endSeconds"] > start_seconds and note["startSeconds"] < end_seconds]
    gain_db = variant["loudness"]["matchGainDb"] + controls.master_gain_db
    gain = 10 ** (gain_db / 20.0)
    for note in active_notes[:512]:
        begin = max(0, int((note["startSeconds"] - start_seconds) * controls.sample_rate))
        finish = min(frame_count, int(math.ceil((note["endSeconds"] - start_seconds) * controls.sample_rate)))
        frequency = 440.0 * (2.0 ** ((note["pitch"] - 69) / 12.0))
        amplitude = (note["velocity"] / 127.0) * 0.075 * gain
        length = max(1, finish - begin)
        for frame in range(begin, finish):
            local = frame - begin
            envelope = min(1.0, local / max(1, int(0.006 * controls.sample_rate)))
            envelope *= min(1.0, (finish - frame) / max(1, int(0.015 * controls.sample_rate)))
            phase = 2 * math.pi * frequency * local / controls.sample_rate
            samples[frame] += amplitude * envelope * _waveform(controls.profile_id, note["role"], phase)
    peak = max((abs(value) for value in samples), default=0.0)
    limiter = 0.96 / peak if peak > 0.96 else 1.0
    pcm = bytearray()
    square_sum = 0.0
    measured_peak = 0.0
    for value in samples:
        value = max(-1.0, min(1.0, value * limiter))
        square_sum += value * value
        measured_peak = max(measured_peak, abs(value))
        pcm.extend(struct.pack("<h", int(round(value * 32767))))
    stream = BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(controls.sample_rate)
        output.writeframes(bytes(pcm))
    raw = stream.getvalue()
    rms = math.sqrt(square_sum / frame_count)
    manifest = {
        "schema": "dna-premium-preview-audio", "version": "1.0",
        "previewSessionHash": session["previewSessionHash"], "variantId": variant_id,
        "profileId": controls.profile_id, "kind": "BUILTIN_MIDI_PROXY",
        "deviceAudio": False, "sampleRate": controls.sample_rate, "channels": 1,
        "sampleWidthBits": 16, "durationSeconds": round(duration, 6),
        "renderedNoteCount": len(active_notes[:512]), "truncatedNoteCount": max(0, len(active_notes) - 512),
        "measuredRmsDbfs": round(20 * math.log10(max(rms, 10 ** (-96 / 20))), 3),
        "measuredPeakDbfs": round(20 * math.log10(max(measured_peak, 10 ** (-96 / 20))), 3),
        "wavSha256": sha256(raw).hexdigest(), "sourceMidiSha256": session["source"]["midiSha256"],
        "validatorIdentityHash": session["validatorIdentity"]["identityHash"],
        "affectsMidi": False, "affectsValidatorVerdict": False, "pa800DeviceCertified": False,
    }
    manifest["audioManifestHash"] = sha256(_canonical(manifest)).hexdigest()
    return raw, manifest


def _wav_metrics(raw: bytes) -> dict[str, Any]:
    try:
        with wave.open(BytesIO(raw), "rb") as source:
            channels = source.getnchannels()
            width = source.getsampwidth()
            rate = source.getframerate()
            frames = source.getnframes()
            payload = source.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise ValueError("Device capture must be a valid PCM WAV") from exc
    if channels not in {1, 2} or width != 2 or not 8000 <= rate <= 192000 or frames <= 0:
        raise ValueError("Device capture requires non-empty mono/stereo 16-bit PCM WAV")
    values = struct.unpack("<" + "h" * (len(payload) // 2), payload)
    normalized = [value / 32768.0 for value in values]
    rms = math.sqrt(sum(value * value for value in normalized) / max(1, len(normalized)))
    peak = max((abs(value) for value in normalized), default=0.0)
    return {"channels": channels, "sampleRate": rate, "sampleWidthBits": 16,
            "frameCount": frames, "durationSeconds": round(frames / rate, 6),
            "measuredRmsDbfs": round(20 * math.log10(max(rms, 10 ** (-96 / 20))), 3),
            "measuredPeakDbfs": round(20 * math.log10(max(peak, 10 ** (-96 / 20))), 3)}


def import_device_audio_capture(raw: bytes, metadata: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > 100_000_000:
        raise ValueError("Device audio capture must be bytes smaller than 100 MB")
    if not isinstance(metadata, Mapping):
        raise ValueError("Device capture metadata must be an object")
    fields = {"version", "manufacturer", "model", "osVersion", "capturedAt", "operatorId",
              "sourceMidiSha256", "session16EvidenceHash", "notes"}
    _require_keys(metadata, fields, fields, "device capture metadata")
    if metadata["version"] != DEVICE_AUDIO_CAPTURE_VERSION:
        raise ValueError("Unsupported device audio capture version")
    if metadata["manufacturer"] != "Korg" or metadata["model"] != "Pa800":
        raise ValueError("Device capture identity must be exact Korg Pa800")
    try:
        captured = date.fromisoformat(metadata["capturedAt"])
    except (TypeError, ValueError) as exc:
        raise ValueError("capturedAt must be an ISO date") from exc
    if captured > date.today():
        raise ValueError("Device capture date cannot be in the future")
    if not isinstance(metadata["operatorId"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", metadata["operatorId"]):
        raise ValueError("operatorId must be a controlled identifier")
    _sha(metadata["sourceMidiSha256"], "sourceMidiSha256")
    if metadata["session16EvidenceHash"] is not None:
        _sha(metadata["session16EvidenceHash"], "session16EvidenceHash")
    if not isinstance(metadata["osVersion"], str) or not metadata["osVersion"].strip():
        raise ValueError("osVersion is required")
    if not isinstance(metadata["notes"], str) or len(metadata["notes"]) > 2000:
        raise ValueError("notes must be text up to 2000 characters")
    capture = {
        "schema": DEVICE_AUDIO_CAPTURE_SCHEMA, "version": DEVICE_AUDIO_CAPTURE_VERSION,
        "authority": "DEVICE_AUDIO_COMPARISON_ONLY", "device": {
            "manufacturer": "Korg", "model": "Pa800", "osVersion": metadata["osVersion"]},
        "capturedAt": metadata["capturedAt"], "operatorId": metadata["operatorId"],
        "sourceMidiSha256": metadata["sourceMidiSha256"],
        "session16EvidenceHash": metadata["session16EvidenceHash"],
        "notes": metadata["notes"], "audio": {**_wav_metrics(bytes(raw)),
                                                  "wavSha256": sha256(raw).hexdigest()},
        "comparisonAllowed": True, "certificationAllowed": False,
        "pa800DeviceCertified": False,
    }
    capture["captureHash"] = sha256(_canonical(capture)).hexdigest()
    validate_device_audio_capture(capture)
    return capture


def validate_device_audio_capture(capture: Mapping[str, Any]) -> None:
    if capture.get("schema") != DEVICE_AUDIO_CAPTURE_SCHEMA or capture.get("version") != DEVICE_AUDIO_CAPTURE_VERSION:
        raise ValueError("Unsupported device audio capture contract")
    if capture.get("authority") != "DEVICE_AUDIO_COMPARISON_ONLY" \
            or capture.get("certificationAllowed") is not False \
            or capture.get("pa800DeviceCertified") is not False:
        raise ValueError("Audio comparison cannot certify a Pa800")
    _sha(capture.get("sourceMidiSha256"), "sourceMidiSha256")
    _sha(capture.get("audio", {}).get("wavSha256"), "audio.wavSha256")
    if capture.get("captureHash") != _hash_without(capture, "captureHash"):
        raise ValueError("Device audio capture hash mismatch")


def compare_device_audio_capture(session: Mapping[str, Any], variant_id: str,
                                 capture: Mapping[str, Any],
                                 proxy_manifest: Mapping[str, Any]) -> dict[str, Any]:
    validate_preview_session_v2(session)
    validate_device_audio_capture(capture)
    if capture["sourceMidiSha256"] != session["source"]["midiSha256"]:
        raise ValueError("Device capture belongs to another MIDI")
    if proxy_manifest.get("previewSessionHash") != session["previewSessionHash"] \
            or proxy_manifest.get("variantId") != variant_id:
        raise ValueError("Proxy manifest does not belong to requested preview variant")
    result = {
        "schema": "dna-premium-audio-comparison", "version": "1.0",
        "previewSessionHash": session["previewSessionHash"], "variantId": variant_id,
        "proxyAudioSha256": proxy_manifest["wavSha256"],
        "deviceCaptureHash": capture["captureHash"],
        "durationDeltaSeconds": round(capture["audio"]["durationSeconds"] -
                                      proxy_manifest["durationSeconds"], 6),
        "rmsDeltaDb": round(capture["audio"]["measuredRmsDbfs"] -
                            proxy_manifest["measuredRmsDbfs"], 3),
        "sameSourceMidi": True, "comparisonOnly": True,
        "session16EvidencePresent": capture["session16EvidenceHash"] is not None,
        "certificationAllowed": False, "pa800DeviceCertified": False,
        "warning": "Audio comparison is not a Pa800 certification; Session 16 evidence gate remains authoritative.",
    }
    result["comparisonHash"] = sha256(_canonical(result)).hexdigest()
    return result


def execute_preview_session_api(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Preview API payload must be an object")
    action = payload.get("action", "plan")
    if action == "plan":
        allowed = {"action", "midiHex", "songMap", "groovePlan", "expressionPlan",
                   "articulationPlans", "controls"}
        _require_keys(payload, allowed, {"midiHex", "songMap", "controls"}, "preview API payload")
        try:
            raw = bytes.fromhex(payload["midiHex"])
        except (TypeError, ValueError) as exc:
            raise ValueError("midiHex must contain hexadecimal MIDI bytes") from exc
        if len(raw) > 64_000_000:
            raise ValueError("Preview MIDI is larger than 64 MB")
        return build_preview_session(MidiFile.from_bytes(raw), payload["songMap"],
                                     payload.get("groovePlan"), payload.get("expressionPlan"),
                                     payload.get("articulationPlans"), None,
                                     payload["controls"])
    if action == "capture":
        _require_keys(payload, {"action", "wavHex", "metadata"}, {"action", "wavHex", "metadata"},
                      "preview capture payload")
        try:
            raw = bytes.fromhex(payload["wavHex"])
        except (TypeError, ValueError) as exc:
            raise ValueError("wavHex must contain hexadecimal WAV bytes") from exc
        return import_device_audio_capture(raw, payload["metadata"])
    raise ValueError("Preview API action must be plan or capture")


def execute_preview_session_gui(payload: Mapping[str, Any]) -> dict[str, Any]:
    return execute_preview_session_api(payload)