"""Transport-neutral recovery pipeline shared by CLI, web, GUI and batch.

Transport adapters only decode input and encode output.  Every musical decision
is made here, so the same MIDI bytes and configuration produce the same result.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .dnc_engine import DncConfig, apply_dnc_events, load_dnc_registry, plan_dnc_events
from .drum_reconstruction import ReconstructionConfig, apply_reconstruction, load_registry, plan_reconstruction
from .guitar_reconstruction import GuitarConfig, apply_guitar_reconstruction, load_guitar_registry, plan_guitar_reconstruction
from .harmonic_reconstruction import ChordCell, HarmonicConfig, apply_harmonic_reconstruction, load_harmonic_registry, plan_harmonic_reconstruction
from .midi import MidiFile, MidiFormatError
from .production_adapter import ProductionAdapter
from .rx_engine import RxConfig, apply_rx_events, load_rx_registry, plan_rx_events
from .solo_enhancement import SoloConfig, apply_solo_enhancement, load_solo_registry, plan_solo_enhancement
from .track_identity import (
    build_track_identities,
    fingerprint_solo,
    verify_solo_fingerprint,
)


_ENGINES = {
    "drum": (ReconstructionConfig, load_registry, plan_reconstruction, apply_reconstruction),
    "harmonic": (HarmonicConfig, load_harmonic_registry, plan_harmonic_reconstruction, apply_harmonic_reconstruction),
    "guitar": (GuitarConfig, load_guitar_registry, plan_guitar_reconstruction, apply_guitar_reconstruction),
    "solo": (SoloConfig, load_solo_registry, plan_solo_enhancement, apply_solo_enhancement),
    "rx": (RxConfig, load_rx_registry, plan_rx_events, apply_rx_events),
    "dnc": (DncConfig, load_dnc_registry, plan_dnc_events, apply_dnc_events),
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _relative_file(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Pipeline registry path must be relative and remain inside the workspace")
    resolved = (root / path).resolve()
    if resolved != root.resolve() and root.resolve() not in resolved.parents:
        raise ValueError("Pipeline registry path escapes the workspace")
    if not resolved.is_file():
        raise ValueError(f"Pipeline registry does not exist: {value}")
    return resolved


@dataclass(frozen=True)
class PipelineConfig:
    version: str
    stages: tuple[Mapping[str, Any], ...]
    preview_profile: str = "pa800-gm"
    preview_note_limit: int = 25000

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "PipelineConfig":
        allowed = {"version", "stages", "previewProfile", "previewNoteLimit"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError("Unknown pipeline configuration fields: " + ", ".join(sorted(unknown)))
        config = cls(str(raw.get("version", "")), tuple(raw.get("stages", ())),
                     str(raw.get("previewProfile", "pa800-gm")), int(raw.get("previewNoteLimit", 25000)))
        if config.version != "1.0" or not config.stages:
            raise ValueError("Pipeline requires version 1.0 and at least one stage")
        if config.preview_profile not in {"gm", "pa800-gm", "silent"}:
            raise ValueError("Unsupported local preview profile")
        if not 0 <= config.preview_note_limit <= 25000:
            raise ValueError("Preview note limit must be between 0 and 25000")
        for stage in config.stages:
            keys = set(stage)
            if not {"engine", "config"} <= keys or keys - {
                "engine", "registry", "production", "config", "context"
            }:
                raise ValueError(
                    "Each stage requires engine/config and exactly one registry source; only context is optional"
                )
            if ("registry" in stage) == ("production" in stage):
                raise ValueError("Each stage requires exactly one of registry or production")
            if stage["engine"] not in _ENGINES or not isinstance(stage["config"], Mapping):
                raise ValueError("Unsupported engine or stage configuration")
            if "production" in stage and not isinstance(stage["production"], Mapping):
                raise ValueError("Production adapter options must be an object")
        return config

    def mapping(self) -> dict[str, Any]:
        return {"version": self.version, "stages": [dict(stage) for stage in self.stages],
                "previewProfile": self.preview_profile, "previewNoteLimit": self.preview_note_limit}


@dataclass(frozen=True)
class PipelineResult:
    midi: bytes
    manifest: Mapping[str, Any]


def _track_view(
    midi: MidiFile, track_uids: Mapping[int, str] | None = None
) -> list[dict[str, Any]]:
    notes = midi.notes()
    result = []
    identities = build_track_identities(midi)
    uid_map = dict(track_uids or {})
    for index in range(16):
        track_notes = [note for note in notes if note.track == index]
        events = midi.tracks[index].events if index < len(midi.tracks) else []
        channels = sorted({event.channel + 1 for event in events if event.channel is not None})
        programs = sorted({event.data[0] for event in events if event.command == 0xC0 and event.data})
        identity = identities[index] if index < len(identities) else None
        shared_channels = [
            channel
            for channel in channels
            if sum(
                any(event.kind == "channel" and event.channel == channel - 1 for event in track.events)
                for track in midi.tracks
            ) > 1
        ]
        result.append({"track": index + 1,
                       "trackUid": uid_map.get(index, identity.track_uid if identity else None),
                       "trackIndex": index, "trackNumber": index + 1,
                       "present": index < len(midi.tracks),
                       "eventCount": len(events), "noteCount": len(track_notes),
                       "channels": channels, "programs": programs,
                       "channelIndices": [channel - 1 for channel in channels],
                       "channelNumbers": channels,
                       "sharedChannelNumbers": shared_channels,
                       "smf0Merged": bool(identity and identity.smf0_merged),
                       "pitchRange": [min((n.pitch for n in track_notes), default=None),
                                      max((n.pitch for n in track_notes), default=None)]})
    return result


def _preview(midi: MidiFile, profile: str, limit: int) -> dict[str, Any]:
    notes = midi.notes()[:limit]
    return {"profile": profile, "readOnly": True, "affectsMidiValidation": False,
            "truncated": len(midi.notes()) > limit, "noteCount": len(notes),
            "notes": [{"track": n.track + 1, "channel": n.channel + 1, "pitch": n.pitch,
                       "start": n.start, "duration": n.end - n.start, "velocity": n.velocity}
                      for n in notes]}


def execute_pipeline(midi_bytes: bytes, raw_config: Mapping[str, Any], root: Path) -> PipelineResult:
    config = PipelineConfig.from_mapping(raw_config)
    midi = MidiFile.from_bytes(midi_bytes)
    input_hash = midi.digest()
    source_identities = build_track_identities(midi)
    track_uids = {item.track_index: item.track_uid for item in source_identities}
    solo_guards = []
    for stage_index, stage in enumerate(config.stages):
        if stage["engine"] != "solo":
            continue
        solo_config = SoloConfig.from_mapping(stage["config"])
        if solo_config.track_index >= len(midi.tracks):
            continue
        source_uid = track_uids[solo_config.track_index]
        solo_guards.append((
            stage_index,
            fingerprint_solo(
                midi,
                track_index=solo_config.track_index,
                channel=solo_config.channel,
                start_tick=solo_config.start_tick,
                end_tick=solo_config.end_tick,
                track_uid=source_uid,
            ),
        ))
    stage_reports = []
    production_adapter = None
    for index, raw_stage in enumerate(config.stages):
        engine = str(raw_stage["engine"])
        config_type, loader, planner, applier = _ENGINES[engine]
        stage_config = (SoloConfig.from_mapping(raw_stage["config"])
                        if engine == "solo" else config_type(**dict(raw_stage["config"])))
        before_hash = midi.digest()
        before_note_count = len(midi.notes())
        before_track_count = len(midi.tracks)
        adapter_manifest = None
        if "production" in raw_stage:
            if production_adapter is None:
                production_adapter = ProductionAdapter(root)
            bundle = production_adapter.adapt(
                engine, midi, stage_config, raw_stage["production"]
            )
            adapter_manifest = bundle.manifest
            if not bundle.allowed:
                plan = None
                applied_manifest = {
                    "schema": "dna-session17-blocked-stage",
                    "version": "1.0",
                    "decision": "MANUAL_REVIEW",
                    "reason": bundle.reason,
                    "applied": False,
                    "inputHash": before_hash,
                    "outputHash": before_hash,
                    "notePairingValid": True,
                }
                decision = "MANUAL_REVIEW"
            else:
                loaded = bundle.loaded
                if engine in {"harmonic", "guitar", "solo"}:
                    context = raw_stage.get("context", {})
                    chords = tuple(ChordCell(**item) for item in context.get("chords", ()))
                    plan = planner(midi, *loaded, chords, stage_config)
                else:
                    plan = planner(midi, *loaded, stage_config)
                applied = applier(midi, plan)
                midi = applied.midi
                applied_manifest = applied.manifest
                decision = plan.decision
        else:
            registry_path = _relative_file(root, str(raw_stage["registry"]))
            loaded = loader(registry_path)
            if not isinstance(loaded, tuple):
                loaded = (loaded,)
            if engine in {"harmonic", "guitar", "solo"}:
                context = raw_stage.get("context", {})
                chords = tuple(ChordCell(**item) for item in context.get("chords", ()))
                plan = planner(midi, *loaded, chords, stage_config)
            else:
                plan = planner(midi, *loaded, stage_config)
            applied = applier(midi, plan)
            midi = applied.midi
            applied_manifest = applied.manifest
            decision = plan.decision
        allocation = getattr(plan, "delay_allocation", None) if plan is not None else None
        if allocation is not None and allocation.allowed and allocation.target_track_index is not None:
            track_uids[allocation.target_track_index] = allocation.target_track_uid
        solo_safety = []
        for guard_stage, fingerprint in solo_guards:
            verification = verify_solo_fingerprint(fingerprint, midi)
            verification["guardStageIndex"] = guard_stage
            verification["verifiedAfterStageIndex"] = index
            verification["verifiedAfterEngine"] = engine
            solo_safety.append(verification)
            if not verification["passed"]:
                raise MidiFormatError(
                    f"Pipeline stage {index} modified protected solo {fingerprint.track_uid}"
                )
        after_hash = midi.digest()
        stage_report = {
            "index": index,
            "engine": engine,
            "decision": decision,
            "registry": str(raw_stage.get("registry", "production-registries")),
            "manifest": applied_manifest,
            "soloSafety": solo_safety,
            "stageDiff": {
                "beforeHash": before_hash,
                "afterHash": after_hash,
                "changed": before_hash != after_hash,
                "beforeNoteCount": before_note_count,
                "afterNoteCount": len(midi.notes()),
                "beforeTrackCount": before_track_count,
                "afterTrackCount": len(midi.tracks),
                "rollbackSafe": True,
            },
        }
        if adapter_manifest is not None:
            stage_report["productionAdapter"] = adapter_manifest
        stage_reports.append(stage_report)
    any_stage_changed = any(stage["stageDiff"]["changed"] for stage in stage_reports)
    output = midi.to_bytes() if any_stage_changed else midi_bytes
    manifest = {"schema": "dna-unified-pipeline-result", "version": "1.0",
                "configHash": sha256(_canonical(config.mapping())).hexdigest(),
                "inputHash": input_hash,
                "sourceBytesHash": sha256(midi_bytes).hexdigest(),
                "outputHash": sha256(output).hexdigest(),
                "midi": {"format": midi.format_type, "ppq": midi.ppq, "trackCount": len(midi.tracks)},
                "stages": stage_reports, "trackView": _track_view(midi, track_uids),
                "preview": _preview(midi, config.preview_profile, config.preview_note_limit),
                "invariants": {"singleEngineAcrossTransports": True, "originalInputImmutable": True,
                               "noOpPreservesSourceBytes": not any_stage_changed,
                               "previewAffectsMidiValidation": False,
                               "stageRollbackIsInMemory": True,
                               "originalSoloVerifiedAfterEveryStage": all(
                                   item["passed"]
                                   for stage in stage_reports
                                   for item in stage["soloSafety"]
                               ),
                               "soloGuardCount": len(solo_guards)}}
    return PipelineResult(output, manifest)


def execute_web(midi_bytes: bytes, config: Mapping[str, Any], root: Path) -> PipelineResult:
    return execute_pipeline(midi_bytes, config, root)


def execute_gui(midi_bytes: bytes, config: Mapping[str, Any], root: Path) -> PipelineResult:
    return execute_pipeline(midi_bytes, config, root)


def execute_batch(items: Sequence[tuple[bytes, Mapping[str, Any]]], root: Path) -> list[PipelineResult]:
    return [execute_pipeline(midi, config, root) for midi, config in items]


def execute_api_payload(payload: Mapping[str, Any], root: Path) -> dict[str, Any]:
    if set(payload) != {"midiBase64", "config"} or not isinstance(payload["config"], Mapping):
        raise ValueError("API pipeline payload requires exactly midiBase64 and config")
    try:
        midi_bytes = base64.b64decode(str(payload["midiBase64"]), validate=True)
    except Exception as exc:
        raise ValueError("API pipeline MIDI must be valid base64") from exc
    result = execute_web(midi_bytes, payload["config"], root)
    return {"midiBase64": base64.b64encode(result.midi).decode("ascii"),
            "manifest": result.manifest}