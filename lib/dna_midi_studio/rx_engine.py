"""Session 6 evidence-gated RX noise engine.

RX triggers are never inferred from a note range or a program name.  A trigger
can only be emitted when a versioned map matches the exact Bank Select and
Program Change of the target track.  Synthetic maps exist solely for the
repeatable software gate and require an explicit opt-in.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .midi import MidiFile, MidiFormatError, Note


_STABLE_ID = re.compile(r"^\d{3}\.\d{3}\.\d{3}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_]*$")
_MAP_SOURCES = {"official-korg", "device-captured", "synthetic-test"}
_PLACEMENTS = {"before_onset", "after_release"}
_CONDITIONS = {"every_note", "long_note", "leap", "phrase_end"}


@dataclass(frozen=True)
class FactoryRxProfile:
    profile_id: str
    bank_msb: int
    bank_lsb: int
    program: int
    floor: int
    soft: int
    low_mid: int
    optimal: int
    high_mid: int
    strong: int
    ceiling: int

    def __post_init__(self) -> None:
        if not _STABLE_ID.fullmatch(self.profile_id):
            raise ValueError(f"Invalid Factory RX profile ID: {self.profile_id}")
        if any(not 0 <= value <= 127 for value in self.sound):
            raise ValueError("Factory RX sound must use exact 0..127 bank/program values")
        if self.points != tuple(sorted(self.points)):
            raise ValueError("Factory RX velocity curve must be monotonic")
        if any(not 1 <= value <= 127 for value in self.points):
            raise ValueError("Factory RX velocity points must be in range 1..127")

    @property
    def sound(self) -> tuple[int, int, int]:
        return self.bank_msb, self.bank_lsb, self.program

    @property
    def points(self) -> tuple[int, ...]:
        return (
            self.floor,
            self.soft,
            self.low_mid,
            self.optimal,
            self.high_mid,
            self.strong,
            self.ceiling,
        )

    def velocity(self, intensity: int) -> int:
        intensity = max(0, min(100, intensity))
        if intensity == 100:
            return self.ceiling
        position = intensity * 6 / 100
        lower = min(int(position), 5)
        fraction = position - lower
        value = self.points[lower] + fraction * (
            self.points[lower + 1] - self.points[lower]
        )
        return max(self.floor, min(self.ceiling, int(round(value))))


@dataclass(frozen=True)
class RxTriggerSpec:
    action: str
    event_type: str
    note: int
    placement: str
    offset_ticks: int
    duration_ticks: int
    condition: str
    intensity_offset: int
    min_source_duration: int = 0
    min_interval: int = 0
    min_gap_after: int = 0

    def __post_init__(self) -> None:
        if not _ACTION.fullmatch(self.action):
            raise ValueError(f"Invalid RX action: {self.action}")
        if self.event_type != "note":
            raise ValueError("Session 6 RX foundation supports confirmed note triggers only")
        if not 0 <= self.note <= 127:
            raise ValueError("Invalid RX trigger note")
        if self.placement not in _PLACEMENTS:
            raise ValueError(f"Unsupported RX placement: {self.placement}")
        if self.offset_ticks < 0 or self.duration_ticks <= 0:
            raise ValueError("RX trigger timing must be positive")
        if self.condition not in _CONDITIONS:
            raise ValueError(f"Unsupported RX condition: {self.condition}")
        if not -100 <= self.intensity_offset <= 100:
            raise ValueError("RX intensity offset must be in range -100..100")
        if self.min_source_duration < 0 or self.min_interval < 0 or self.min_gap_after < 0:
            raise ValueError("RX condition thresholds cannot be negative")


@dataclass(frozen=True)
class RxMap:
    map_id: str
    version: str
    source: str
    evidence: str
    confirmed: bool
    bank_msb: int
    bank_lsb: int
    program: int
    triggers: tuple[RxTriggerSpec, ...]
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _STABLE_ID.fullmatch(self.map_id):
            raise ValueError(f"Invalid RX map ID: {self.map_id}")
        if not self.version or self.source not in _MAP_SOURCES or not self.evidence:
            raise ValueError("RX map requires versioned source evidence")
        if any(not 0 <= value <= 127 for value in self.sound):
            raise ValueError("RX map requires exact bank/program values")
        if not self.triggers or not self.source_ids:
            raise ValueError("RX map requires triggers and source IDs")
        if any(not _STABLE_ID.fullmatch(source_id) for source_id in self.source_ids):
            raise ValueError("RX map source IDs must use the stable ID format")
        actions = [trigger.action for trigger in self.triggers]
        notes = [trigger.note for trigger in self.triggers]
        if len(actions) != len(set(actions)):
            raise ValueError("RX map actions must be unique")
        if len(notes) != len(set(notes)):
            raise ValueError("RX trigger notes must be unique")

    @property
    def sound(self) -> tuple[int, int, int]:
        return self.bank_msb, self.bank_lsb, self.program


@dataclass(frozen=True)
class RxConfig:
    track_index: int
    channel: int
    start_tick: int
    end_tick: int
    seed: int
    intensity: int
    map_id: str
    profile_id: str
    requested_actions: tuple[str, ...]
    allow_synthetic_map: bool = False
    max_generated_events: int = 32
    existing_tolerance_ticks: int = 8

    def __post_init__(self) -> None:
        if not 0 <= self.channel <= 15:
            raise ValueError("Invalid RX channel")
        if self.track_index < 0:
            raise ValueError("Invalid RX track")
        if self.start_tick < 0 or self.end_tick <= self.start_tick:
            raise ValueError("Invalid RX window")
        if not 0 <= self.intensity <= 100:
            raise ValueError("RX intensity must be in range 0..100")
        if not _STABLE_ID.fullmatch(self.map_id) or not _STABLE_ID.fullmatch(self.profile_id):
            raise ValueError("RX map and profile IDs must be stable IDs")
        actions = tuple(self.requested_actions)
        if not actions or any(not _ACTION.fullmatch(action) for action in actions):
            raise ValueError("RX actions must be non-empty stable action names")
        if len(actions) != len(set(actions)):
            raise ValueError("Requested RX actions must be unique")
        object.__setattr__(self, "requested_actions", actions)
        if self.max_generated_events < 0 or self.existing_tolerance_ticks < 0:
            raise ValueError("RX budgets cannot be negative")


@dataclass(frozen=True)
class RxNoteAudit:
    action: str
    source_note_index: int
    map_id: str
    source_id: str
    condition: str
    placement: str


@dataclass(frozen=True)
class RxPlan:
    decision: str
    reason: str
    input_hash: str
    structural_hash: str
    selection_hash: str
    config: RxConfig
    map_id: str | None
    profile_id: str | None
    map_source: str | None
    map_version: str | None
    map_evidence: str | None
    exact_sound: tuple[int, int, int] | None
    generated_notes: tuple[Note, ...]
    note_audit: tuple[RxNoteAudit, ...]
    original_event_fingerprint: tuple[tuple[Any, ...], ...]
    counts: Mapping[str, int]
    skipped: Mapping[str, int]
    source_ids: tuple[str, ...]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "schema": "dna-session6-rx-plan",
            "version": "1.0",
            "decision": self.decision,
            "reason": self.reason,
            "inputHash": self.input_hash,
            "structuralHash": self.structural_hash,
            "selectionHash": self.selection_hash,
            "seed": self.config.seed,
            "track": self.config.track_index,
            "channel": self.config.channel + 1,
            "window": [self.config.start_tick, self.config.end_tick],
            "rxMapId": self.map_id,
            "factoryProfileId": self.profile_id,
            "mapSource": self.map_source,
            "mapVersion": self.map_version,
            "mapEvidence": self.map_evidence,
            "exactSound": list(self.exact_sound) if self.exact_sound else None,
            "requestedActions": list(self.config.requested_actions),
            "generatedEvents": len(self.generated_notes),
            "counts": dict(self.counts),
            "skipped": dict(self.skipped),
            "sourceIds": list(self.source_ids),
            "rxTriggersGuessed": False,
            "analysisVelocityUsed": False,
            "goldAffectsVelocity": False,
            "syntheticMapOptIn": self.config.allow_synthetic_map,
            "notes": [
                {
                    "tick": note.start,
                    "duration": note.end - note.start,
                    "note": note.pitch,
                    "velocity": note.velocity,
                    "action": audit.action,
                    "condition": audit.condition,
                    "placement": audit.placement,
                    "sourceNoteIndex": audit.source_note_index,
                    "rxMapId": audit.map_id,
                    "sourceId": audit.source_id,
                    "factoryProfileId": note.factory_profile_id,
                }
                for note, audit in zip(self.generated_notes, self.note_audit)
            ],
        }


@dataclass(frozen=True)
class RxResult:
    midi: MidiFile
    manifest: Mapping[str, Any]


def _reject_trigger_velocity(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).replace("_", "").lower()
            if normalized in {"velocity", "velocities", "velocitycurve"}:
                raise ValueError(f"RX trigger dynamics are forbidden at {path}.{key}")
            _reject_trigger_velocity(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_trigger_velocity(child, f"{path}[{index}]")


def load_rx_registry(
    path: str | Path,
) -> tuple[dict[str, RxMap], dict[str, FactoryRxProfile]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    _reject_trigger_velocity(raw.get("rxMaps", []))
    maps: dict[str, RxMap] = {}
    for item in raw.get("rxMaps", []):
        rx_map = RxMap(
            map_id=item["id"],
            version=item["version"],
            source=item["source"],
            evidence=item["evidence"],
            confirmed=bool(item["confirmed"]),
            bank_msb=int(item["bankMsb"]),
            bank_lsb=int(item["bankLsb"]),
            program=int(item["program"]),
            triggers=tuple(
                RxTriggerSpec(
                    action=trigger["action"],
                    event_type=trigger["eventType"],
                    note=int(trigger["note"]),
                    placement=trigger["placement"],
                    offset_ticks=int(trigger["offsetTicks"]),
                    duration_ticks=int(trigger["durationTicks"]),
                    condition=trigger["condition"],
                    intensity_offset=int(trigger.get("intensityOffset", 0)),
                    min_source_duration=int(trigger.get("minSourceDuration", 0)),
                    min_interval=int(trigger.get("minInterval", 0)),
                    min_gap_after=int(trigger.get("minGapAfter", 0)),
                )
                for trigger in item["triggers"]
            ),
            source_ids=tuple(item["sourceIds"]),
        )
        if rx_map.map_id in maps:
            raise ValueError(f"Duplicate RX map ID: {rx_map.map_id}")
        maps[rx_map.map_id] = rx_map
    profiles: dict[str, FactoryRxProfile] = {}
    for item in raw.get("factoryProfiles", []):
        profile = FactoryRxProfile(
            profile_id=item["id"],
            bank_msb=int(item["bankMsb"]),
            bank_lsb=int(item["bankLsb"]),
            program=int(item["program"]),
            floor=int(item["floor"]),
            soft=int(item["soft"]),
            low_mid=int(item["lowMid"]),
            optimal=int(item["optimal"]),
            high_mid=int(item["highMid"]),
            strong=int(item["strong"]),
            ceiling=int(item["ceiling"]),
        )
        if profile.profile_id in profiles:
            raise ValueError(f"Duplicate Factory RX profile ID: {profile.profile_id}")
        profiles[profile.profile_id] = profile
    return maps, profiles


def _track_value_at(
    midi: MidiFile,
    track_index: int,
    channel: int,
    tick: int,
    command: int,
    controller: int | None = None,
) -> int | None:
    if not 0 <= track_index < len(midi.tracks):
        return None
    candidates: list[tuple[int, int, int]] = []
    for event in midi.tracks[track_index].events:
        if (
            event.kind != "channel"
            or event.channel != channel
            or event.command != command
            or event.tick > tick
            or not event.data
        ):
            continue
        if controller is not None:
            if len(event.data) != 2 or event.data[0] != controller:
                continue
            value = event.data[1]
        else:
            value = event.data[0]
        candidates.append((event.tick, event.order, value))
    return max(candidates)[2] if candidates else None


def _sound_at(midi: MidiFile, config: RxConfig) -> tuple[int, int, int] | None:
    bank_msb = _track_value_at(
        midi, config.track_index, config.channel, config.start_tick, 0xB0, 0
    )
    bank_lsb = _track_value_at(
        midi, config.track_index, config.channel, config.start_tick, 0xB0, 32
    )
    program = _track_value_at(
        midi, config.track_index, config.channel, config.start_tick, 0xC0
    )
    if bank_msb is None or bank_lsb is None or program is None:
        return None
    return bank_msb, bank_lsb, program


def _event_fingerprint(midi: MidiFile) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            track_index,
            event.tick,
            event.order,
            event.kind,
            event.status,
            event.data.hex(),
            event.meta_type,
        )
        for track_index, track in enumerate(midi.tracks)
        for event in track.events
    )


def _structural_hash(midi: MidiFile, config: RxConfig) -> str:
    payload = {
        "sound": _sound_at(midi, config),
        "notes": [
            [note.start, note.end, note.pitch]
            for note in midi.notes()
            if note.track == config.track_index
            and note.channel == config.channel
            and config.start_tick <= note.start < config.end_tick
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _eligible(
    trigger: RxTriggerSpec,
    source: Note,
    previous: Note | None,
    following: Note | None,
    end_tick: int,
) -> bool:
    if trigger.condition == "every_note":
        return True
    if trigger.condition == "long_note":
        return source.end - source.start >= trigger.min_source_duration
    if trigger.condition == "leap":
        return previous is not None and abs(source.pitch - previous.pitch) >= trigger.min_interval
    gap = (following.start if following is not None else end_tick) - source.end
    return gap >= trigger.min_gap_after


def _empty_plan(
    midi: MidiFile,
    config: RxConfig,
    decision: str,
    reason: str,
    *,
    structural_hash: str,
    rx_map: RxMap | None = None,
    profile: FactoryRxProfile | None = None,
    exact_sound: tuple[int, int, int] | None = None,
) -> RxPlan:
    payload = {
        "structuralHash": structural_hash,
        "seed": config.seed,
        "decision": decision,
        "reason": reason,
        "mapId": rx_map.map_id if rx_map else None,
        "profileId": profile.profile_id if profile else None,
    }
    return RxPlan(
        decision=decision,
        reason=reason,
        input_hash=midi.digest(),
        structural_hash=structural_hash,
        selection_hash=sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
        config=config,
        map_id=rx_map.map_id if rx_map else None,
        profile_id=profile.profile_id if profile else None,
        map_source=rx_map.source if rx_map else None,
        map_version=rx_map.version if rx_map else None,
        map_evidence=rx_map.evidence if rx_map else None,
        exact_sound=exact_sound,
        generated_notes=(),
        note_audit=(),
        original_event_fingerprint=_event_fingerprint(midi),
        counts={action: 0 for action in config.requested_actions},
        skipped={},
        source_ids=rx_map.source_ids if rx_map else (),
    )


def plan_rx_events(
    midi: MidiFile,
    rx_maps: Mapping[str, RxMap],
    profiles: Mapping[str, FactoryRxProfile],
    config: RxConfig,
) -> RxPlan:
    structural_hash = _structural_hash(midi, config)
    if not 0 <= config.track_index < len(midi.tracks):
        return _empty_plan(
            midi, config, "MANUAL_REVIEW", "Target RX track is missing", structural_hash=structural_hash
        )
    rx_map = rx_maps.get(config.map_id)
    if rx_map is None:
        return _empty_plan(
            midi, config, "MANUAL_REVIEW", "Confirmed RX map is missing", structural_hash=structural_hash
        )
    profile = profiles.get(config.profile_id)
    if profile is None:
        return _empty_plan(
            midi,
            config,
            "MANUAL_REVIEW",
            "Factory RX profile is missing",
            structural_hash=structural_hash,
            rx_map=rx_map,
        )
    exact_sound = _sound_at(midi, config)
    if not rx_map.confirmed:
        return _empty_plan(
            midi, config, "MANUAL_REVIEW", "RX map is not confirmed", structural_hash=structural_hash,
            rx_map=rx_map, profile=profile, exact_sound=exact_sound
        )
    if rx_map.source == "synthetic-test" and not config.allow_synthetic_map:
        return _empty_plan(
            midi, config, "MANUAL_REVIEW", "Synthetic RX map requires explicit test opt-in",
            structural_hash=structural_hash, rx_map=rx_map, profile=profile, exact_sound=exact_sound
        )
    if profile.sound != rx_map.sound:
        return _empty_plan(
            midi, config, "MANUAL_REVIEW", "Factory profile and RX map sound mismatch",
            structural_hash=structural_hash, rx_map=rx_map, profile=profile, exact_sound=exact_sound
        )
    if exact_sound is None:
        return _empty_plan(
            midi, config, "MANUAL_REVIEW", "Exact Bank Select and Program Change are required",
            structural_hash=structural_hash, rx_map=rx_map, profile=profile
        )
    if exact_sound != rx_map.sound:
        return _empty_plan(
            midi, config, "MANUAL_REVIEW", "RX map does not match target Bank/Program",
            structural_hash=structural_hash, rx_map=rx_map, profile=profile, exact_sound=exact_sound
        )
    by_action = {trigger.action: trigger for trigger in rx_map.triggers}
    missing = [action for action in config.requested_actions if action not in by_action]
    if missing:
        return _empty_plan(
            midi, config, "MANUAL_REVIEW", f"RX action is not confirmed: {missing[0]}",
            structural_hash=structural_hash, rx_map=rx_map, profile=profile, exact_sound=exact_sound
        )

    trigger_pitches = {trigger.note for trigger in rx_map.triggers}
    window_notes = [
        note
        for note in midi.notes()
        if note.track == config.track_index
        and note.channel == config.channel
        and config.start_tick <= note.start < config.end_tick
    ]
    sources = [note for note in window_notes if note.pitch not in trigger_pitches]
    existing_rx = [note for note in window_notes if note.pitch in trigger_pitches]
    if not sources:
        return _empty_plan(
            midi, config, "KEEP", "No eligible source notes in RX window", structural_hash=structural_hash,
            rx_map=rx_map, profile=profile, exact_sound=exact_sound
        )

    generated: list[Note] = []
    audit: list[RxNoteAudit] = []
    counts = {action: 0 for action in config.requested_actions}
    skipped = {"condition": 0, "boundary": 0, "existing": 0}
    primary_source_id = rx_map.source_ids[0]
    for source_index, source in enumerate(sources):
        previous = sources[source_index - 1] if source_index else None
        following = sources[source_index + 1] if source_index + 1 < len(sources) else None
        for action in config.requested_actions:
            trigger = by_action[action]
            if not _eligible(trigger, source, previous, following, config.end_tick):
                skipped["condition"] += 1
                continue
            tick = (
                source.start - trigger.offset_ticks
                if trigger.placement == "before_onset"
                else source.end + trigger.offset_ticks
            )
            end = tick + trigger.duration_ticks
            if tick < config.start_tick or end > config.end_tick:
                skipped["boundary"] += 1
                continue
            if any(
                note.pitch == trigger.note
                and abs(note.start - tick) <= config.existing_tolerance_ticks
                for note in existing_rx
            ):
                skipped["existing"] += 1
                continue
            generated.append(
                Note(
                    track=config.track_index,
                    channel=config.channel,
                    pitch=trigger.note,
                    start=tick,
                    end=end,
                    velocity=profile.velocity(config.intensity + trigger.intensity_offset),
                    factory_profile_id=profile.profile_id,
                    element=f"rx:{action}",
                )
            )
            audit.append(
                RxNoteAudit(
                    action=action,
                    source_note_index=source_index,
                    map_id=rx_map.map_id,
                    source_id=primary_source_id,
                    condition=trigger.condition,
                    placement=trigger.placement,
                )
            )
            counts[action] += 1

    if len(generated) > config.max_generated_events:
        return _empty_plan(
            midi, config, "MANUAL_REVIEW", "RX transformation budget exceeded",
            structural_hash=structural_hash, rx_map=rx_map, profile=profile, exact_sound=exact_sound
        )
    ordered = sorted(
        zip(generated, audit),
        key=lambda pair: (pair[0].start, pair[0].pitch, pair[1].action, pair[1].source_note_index),
    )
    generated = [pair[0] for pair in ordered]
    audit = [pair[1] for pair in ordered]
    if not generated:
        plan = _empty_plan(
            midi, config, "KEEP", "No RX events required", structural_hash=structural_hash,
            rx_map=rx_map, profile=profile, exact_sound=exact_sound
        )
        return RxPlan(**{**plan.__dict__, "counts": counts, "skipped": skipped})

    selection_payload = {
        "structuralHash": structural_hash,
        "seed": config.seed,
        "mapId": rx_map.map_id,
        "mapVersion": rx_map.version,
        "profileId": profile.profile_id,
        "actions": list(config.requested_actions),
        "notes": [
            [note.start, note.end, note.pitch, note.velocity, item.action, item.source_note_index]
            for note, item in zip(generated, audit)
        ],
    }
    return RxPlan(
        decision="AUGMENT",
        reason="Confirmed RX events are available",
        input_hash=midi.digest(),
        structural_hash=structural_hash,
        selection_hash=sha256(
            json.dumps(selection_payload, sort_keys=True).encode()
        ).hexdigest(),
        config=config,
        map_id=rx_map.map_id,
        profile_id=profile.profile_id,
        map_source=rx_map.source,
        map_version=rx_map.version,
        map_evidence=rx_map.evidence,
        exact_sound=exact_sound,
        generated_notes=tuple(generated),
        note_audit=tuple(audit),
        original_event_fingerprint=_event_fingerprint(midi),
        counts=counts,
        skipped=skipped,
        source_ids=rx_map.source_ids,
    )


def apply_rx_events(midi: MidiFile, plan: RxPlan) -> RxResult:
    if midi.digest() != plan.input_hash:
        raise MidiFormatError("RX plan input hash does not match MIDI")
    output = midi
    if plan.decision == "AUGMENT":
        output = midi.add_notes(
            track_index=plan.config.track_index, new_notes=plan.generated_notes
        )
    elif plan.decision not in {"KEEP", "MANUAL_REVIEW"}:
        raise MidiFormatError(f"Unsupported RX decision: {plan.decision}")

    original_counts = Counter(plan.original_event_fingerprint)
    output_counts = Counter(_event_fingerprint(output))
    original_preserved = all(output_counts[item] >= count for item, count in original_counts.items())
    if not original_preserved:
        raise MidiFormatError("RX apply mutated a protected original event")
    sound_preserved = (
        plan.exact_sound is None or _sound_at(output, plan.config) == plan.exact_sound
    )
    if not sound_preserved:
        raise MidiFormatError("RX apply changed Bank Select or Program Change")
    output.notes()
    manifest = {
        **plan.to_manifest(),
        "outputHash": output.digest(),
        "originalEventsPreserved": original_preserved,
        "targetSoundPreserved": sound_preserved,
        "notePairingValid": True,
    }
    return RxResult(output, manifest)