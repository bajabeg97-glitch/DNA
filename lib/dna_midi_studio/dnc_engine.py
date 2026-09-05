"""Session 7 data-driven DNC articulation engine.

Only events explicitly present in a versioned exact-sound map can be emitted.
The foundation supports key-switch notes, CC and channel pressure; proprietary
messages remain blocked until an official or device-captured map proves them.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .midi import MidiEvent, MidiFile, MidiFormatError, Note


_STABLE_ID = re.compile(r"^\d{3}\.\d{3}\.\d{3}$")
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_SOURCES = {"official-korg", "device-captured", "synthetic-test"}
_EVENT_TYPES = {"keyswitch", "cc", "channel_pressure"}
_PLACEMENTS = {"before_onset", "at_onset", "after_release"}
_CONDITIONS = {"every_note", "long_note", "leap", "close_entry", "phrase_start"}
_PROTECTED_CONTROLLERS = {0, 32, 98, 99, 100, 101}


@dataclass(frozen=True)
class FactoryDncProfile:
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
            raise ValueError(f"Invalid Factory DNC profile ID: {self.profile_id}")
        if any(not 0 <= value <= 127 for value in self.sound):
            raise ValueError("Factory DNC sound requires exact bank/program values")
        if self.points != tuple(sorted(self.points)):
            raise ValueError("Factory DNC velocity curve must be monotonic")
        if any(not 1 <= value <= 127 for value in self.points):
            raise ValueError("Factory DNC velocity points must be in range 1..127")

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
class DncTriggerSpec:
    articulation: str
    event_type: str
    number: int | None
    value: int | None
    placement: str
    offset_ticks: int
    duration_ticks: int
    condition: str
    intensity_offset: int = 0
    min_source_duration: int = 0
    min_interval: int = 0
    max_gap_before: int = 0
    min_gap_before: int = 0

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.articulation):
            raise ValueError(f"Invalid DNC articulation: {self.articulation}")
        if self.event_type not in _EVENT_TYPES:
            raise ValueError("Unsupported or proprietary DNC event type")
        if self.placement not in _PLACEMENTS or self.condition not in _CONDITIONS:
            raise ValueError("Unsupported DNC placement or condition")
        if self.offset_ticks < 0 or self.duration_ticks < 0:
            raise ValueError("DNC timing cannot be negative")
        if not -100 <= self.intensity_offset <= 100:
            raise ValueError("DNC intensity offset must be in range -100..100")
        if any(
            value < 0
            for value in (
                self.min_source_duration,
                self.min_interval,
                self.max_gap_before,
                self.min_gap_before,
            )
        ):
            raise ValueError("DNC thresholds cannot be negative")
        if self.event_type == "keyswitch":
            if self.number is None or not 0 <= self.number <= 127 or self.duration_ticks <= 0:
                raise ValueError("DNC key-switch requires note and duration")
            if self.value is not None:
                raise ValueError("DNC key-switch velocity must come from Factory profile")
        elif self.event_type == "cc":
            if self.number is None or not 0 <= self.number <= 127:
                raise ValueError("DNC CC requires a controller number")
            if self.number in _PROTECTED_CONTROLLERS:
                raise ValueError("DNC map cannot control protected Bank/RPN/NRPN CC")
            if self.value is None or not 0 <= self.value <= 127 or self.duration_ticks:
                raise ValueError("DNC CC requires one exact value and no duration")
        else:
            if self.number is not None or self.value is None or not 0 <= self.value <= 127:
                raise ValueError("DNC channel pressure requires one exact value")
            if self.duration_ticks:
                raise ValueError("DNC channel pressure cannot have note duration")


@dataclass(frozen=True)
class DncMap:
    map_id: str
    version: str
    source: str
    evidence: str
    confirmed: bool
    bank_msb: int
    bank_lsb: int
    program: int
    roles: tuple[str, ...]
    playable_min: int
    playable_max: int
    trigger_min: int
    trigger_max: int
    triggers: tuple[DncTriggerSpec, ...]
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _STABLE_ID.fullmatch(self.map_id):
            raise ValueError(f"Invalid DNC map ID: {self.map_id}")
        if not self.version or self.source not in _SOURCES or not self.evidence:
            raise ValueError("DNC map requires versioned source evidence")
        if any(not 0 <= value <= 127 for value in self.sound):
            raise ValueError("DNC map requires exact bank/program values")
        if not self.roles or any(not _NAME.fullmatch(role) for role in self.roles):
            raise ValueError("DNC map requires explicit roles")
        if not 0 <= self.playable_min <= self.playable_max <= 127:
            raise ValueError("Invalid DNC playable range")
        if not 0 <= self.trigger_min <= self.trigger_max <= 127:
            raise ValueError("Invalid DNC trigger range")
        if not self.triggers or not self.source_ids:
            raise ValueError("DNC map requires triggers and source evidence")
        if any(not _STABLE_ID.fullmatch(source_id) for source_id in self.source_ids):
            raise ValueError("DNC source IDs must use stable ID format")
        articulations = [trigger.articulation for trigger in self.triggers]
        if len(articulations) != len(set(articulations)):
            raise ValueError("DNC articulations must be unique")
        key_notes = [
            trigger.number
            for trigger in self.triggers
            if trigger.event_type == "keyswitch"
        ]
        if len(key_notes) != len(set(key_notes)):
            raise ValueError("DNC key-switch notes must be unique")
        for note in key_notes:
            if note is None or not self.trigger_min <= note <= self.trigger_max:
                raise ValueError("DNC key-switch lies outside confirmed trigger range")
            if self.playable_min <= note <= self.playable_max:
                raise ValueError("DNC key-switch collides with playable note range")

    @property
    def sound(self) -> tuple[int, int, int]:
        return self.bank_msb, self.bank_lsb, self.program


@dataclass(frozen=True)
class DncConfig:
    track_index: int
    channel: int
    start_tick: int
    end_tick: int
    seed: int
    intensity: int
    role: str
    map_id: str
    profile_id: str
    requested_articulations: tuple[str, ...]
    allow_synthetic_map: bool = False
    max_generated_events: int = 32
    existing_tolerance_ticks: int = 8

    def __post_init__(self) -> None:
        if self.track_index < 0 or not 0 <= self.channel <= 15:
            raise ValueError("Invalid DNC track/channel")
        if self.start_tick < 0 or self.end_tick <= self.start_tick:
            raise ValueError("Invalid DNC window")
        if not 0 <= self.intensity <= 100 or not _NAME.fullmatch(self.role):
            raise ValueError("Invalid DNC intensity or role")
        if not _STABLE_ID.fullmatch(self.map_id) or not _STABLE_ID.fullmatch(self.profile_id):
            raise ValueError("DNC map/profile IDs must be stable IDs")
        requested = tuple(self.requested_articulations)
        if not requested or any(not _NAME.fullmatch(item) for item in requested):
            raise ValueError("DNC articulations must be explicit")
        if len(requested) != len(set(requested)):
            raise ValueError("Requested DNC articulations must be unique")
        object.__setattr__(self, "requested_articulations", requested)
        if self.max_generated_events < 0 or self.existing_tolerance_ticks < 0:
            raise ValueError("DNC budgets cannot be negative")


@dataclass(frozen=True)
class DncAudit:
    articulation: str
    event_type: str
    source_note_index: int
    source_id: str
    condition: str
    tick: int


@dataclass(frozen=True)
class DncPlan:
    decision: str
    reason: str
    input_hash: str
    structural_hash: str
    selection_hash: str
    config: DncConfig
    map_id: str | None
    profile_id: str | None
    map_source: str | None
    map_version: str | None
    map_evidence: str | None
    exact_sound: tuple[int, int, int] | None
    generated_notes: tuple[Note, ...]
    generated_events: tuple[MidiEvent, ...]
    audit: tuple[DncAudit, ...]
    original_event_fingerprint: tuple[tuple[Any, ...], ...]
    counts: Mapping[str, int]
    skipped: Mapping[str, int]
    source_ids: tuple[str, ...]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "schema": "dna-session7-dnc-plan",
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
            "role": self.config.role,
            "dncMapId": self.map_id,
            "factoryProfileId": self.profile_id,
            "mapSource": self.map_source,
            "mapVersion": self.map_version,
            "mapEvidence": self.map_evidence,
            "exactSound": list(self.exact_sound) if self.exact_sound else None,
            "requestedArticulations": list(self.config.requested_articulations),
            "generatedNotes": len(self.generated_notes),
            "generatedEvents": len(self.generated_events),
            "counts": dict(self.counts),
            "skipped": dict(self.skipped),
            "sourceIds": list(self.source_ids),
            "dncTriggersGuessed": False,
            "proprietaryEventsGenerated": False,
            "analysisVelocityUsed": False,
            "goldAffectsVelocity": False,
            "events": [
                {
                    "articulation": item.articulation,
                    "eventType": item.event_type,
                    "sourceNoteIndex": item.source_note_index,
                    "sourceId": item.source_id,
                    "condition": item.condition,
                    "tick": item.tick,
                }
                for item in self.audit
            ],
        }


@dataclass(frozen=True)
class DncResult:
    midi: MidiFile
    manifest: Mapping[str, Any]


def _reject_dynamic_fields(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).replace("_", "").lower()
            if normalized in {"velocity", "velocities", "velocitycurve"}:
                raise ValueError(f"DNC trigger dynamics are forbidden at {path}.{key}")
            _reject_dynamic_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_dynamic_fields(child, f"{path}[{index}]")


def load_dnc_registry(
    path: str | Path,
) -> tuple[dict[str, DncMap], dict[str, FactoryDncProfile]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    _reject_dynamic_fields(raw.get("dncMaps", []))
    maps: dict[str, DncMap] = {}
    for item in raw.get("dncMaps", []):
        dnc_map = DncMap(
            map_id=item["id"],
            version=item["version"],
            source=item["source"],
            evidence=item["evidence"],
            confirmed=bool(item["confirmed"]),
            bank_msb=int(item["bankMsb"]),
            bank_lsb=int(item["bankLsb"]),
            program=int(item["program"]),
            roles=tuple(item["roles"]),
            playable_min=int(item["playableMin"]),
            playable_max=int(item["playableMax"]),
            trigger_min=int(item["triggerMin"]),
            trigger_max=int(item["triggerMax"]),
            triggers=tuple(
                DncTriggerSpec(
                    articulation=trigger["articulation"],
                    event_type=trigger["eventType"],
                    number=int(trigger["number"]) if trigger.get("number") is not None else None,
                    value=int(trigger["value"]) if trigger.get("value") is not None else None,
                    placement=trigger["placement"],
                    offset_ticks=int(trigger.get("offsetTicks", 0)),
                    duration_ticks=int(trigger.get("durationTicks", 0)),
                    condition=trigger["condition"],
                    intensity_offset=int(trigger.get("intensityOffset", 0)),
                    min_source_duration=int(trigger.get("minSourceDuration", 0)),
                    min_interval=int(trigger.get("minInterval", 0)),
                    max_gap_before=int(trigger.get("maxGapBefore", 0)),
                    min_gap_before=int(trigger.get("minGapBefore", 0)),
                )
                for trigger in item["triggers"]
            ),
            source_ids=tuple(item["sourceIds"]),
        )
        if dnc_map.map_id in maps:
            raise ValueError(f"Duplicate DNC map ID: {dnc_map.map_id}")
        maps[dnc_map.map_id] = dnc_map
    profiles: dict[str, FactoryDncProfile] = {}
    for item in raw.get("factoryProfiles", []):
        profile = FactoryDncProfile(
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
            raise ValueError(f"Duplicate Factory DNC profile ID: {profile.profile_id}")
        profiles[profile.profile_id] = profile
    return maps, profiles


def _track_value(
    midi: MidiFile,
    track_index: int,
    channel: int,
    tick: int,
    command: int,
    controller: int | None = None,
) -> int | None:
    if not 0 <= track_index < len(midi.tracks):
        return None
    found: list[tuple[int, int, int]] = []
    for event in midi.tracks[track_index].events:
        if event.kind != "channel" or event.channel != channel or event.command != command or event.tick > tick:
            continue
        if controller is None and event.data:
            found.append((event.tick, event.order, event.data[0]))
        elif controller is not None and len(event.data) == 2 and event.data[0] == controller:
            found.append((event.tick, event.order, event.data[1]))
    return max(found)[2] if found else None


def _sound_at(midi: MidiFile, config: DncConfig) -> tuple[int, int, int] | None:
    values = (
        _track_value(midi, config.track_index, config.channel, config.start_tick, 0xB0, 0),
        _track_value(midi, config.track_index, config.channel, config.start_tick, 0xB0, 32),
        _track_value(midi, config.track_index, config.channel, config.start_tick, 0xC0),
    )
    return values if all(value is not None for value in values) else None  # type: ignore[return-value]


def _fingerprint(midi: MidiFile) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (i, event.tick, event.order, event.kind, event.status, event.data.hex(), event.meta_type)
        for i, track in enumerate(midi.tracks)
        for event in track.events
    )


def _structure(midi: MidiFile, config: DncConfig, trigger_notes: set[int]) -> str:
    payload = [
        [note.start, note.end, note.pitch]
        for note in midi.notes()
        if note.track == config.track_index
        and note.channel == config.channel
        and config.start_tick <= note.start < config.end_tick
        and note.pitch not in trigger_notes
    ]
    return sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def _condition(
    trigger: DncTriggerSpec,
    note: Note,
    previous: Note | None,
) -> bool:
    if trigger.condition == "every_note":
        return True
    if trigger.condition == "long_note":
        return note.end - note.start >= trigger.min_source_duration
    if trigger.condition == "leap":
        return previous is not None and abs(note.pitch - previous.pitch) >= trigger.min_interval
    gap = note.start - previous.end if previous is not None else note.start
    if trigger.condition == "close_entry":
        return previous is not None and gap <= trigger.max_gap_before
    return previous is None or gap >= trigger.min_gap_before


def _tick(trigger: DncTriggerSpec, note: Note) -> int:
    if trigger.placement == "before_onset":
        return note.start - trigger.offset_ticks
    if trigger.placement == "after_release":
        return note.end + trigger.offset_ticks
    return note.start + trigger.offset_ticks


def _empty(
    midi: MidiFile,
    config: DncConfig,
    decision: str,
    reason: str,
    structural_hash: str,
    dnc_map: DncMap | None = None,
    profile: FactoryDncProfile | None = None,
    sound: tuple[int, int, int] | None = None,
) -> DncPlan:
    payload = [structural_hash, config.seed, decision, reason, config.map_id, config.profile_id]
    return DncPlan(
        decision, reason, midi.digest(), structural_hash,
        sha256(json.dumps(payload).encode()).hexdigest(), config,
        dnc_map.map_id if dnc_map else None,
        profile.profile_id if profile else None,
        dnc_map.source if dnc_map else None,
        dnc_map.version if dnc_map else None,
        dnc_map.evidence if dnc_map else None,
        sound, (), (), (), _fingerprint(midi),
        {item: 0 for item in config.requested_articulations}, {},
        dnc_map.source_ids if dnc_map else (),
    )


def plan_dnc_events(
    midi: MidiFile,
    dnc_maps: Mapping[str, DncMap],
    profiles: Mapping[str, FactoryDncProfile],
    config: DncConfig,
) -> DncPlan:
    dnc_map = dnc_maps.get(config.map_id)
    trigger_notes = {
        trigger.number
        for trigger in dnc_map.triggers
        if dnc_map and trigger.event_type == "keyswitch" and trigger.number is not None
    } if dnc_map else set()
    structural_hash = _structure(midi, config, trigger_notes)
    if not 0 <= config.track_index < len(midi.tracks):
        return _empty(midi, config, "MANUAL_REVIEW", "Target DNC track is missing", structural_hash)
    if dnc_map is None:
        return _empty(midi, config, "MANUAL_REVIEW", "Confirmed DNC map is missing", structural_hash)
    profile = profiles.get(config.profile_id)
    if profile is None:
        return _empty(midi, config, "MANUAL_REVIEW", "Factory DNC profile is missing", structural_hash, dnc_map)
    sound = _sound_at(midi, config)
    if not dnc_map.confirmed:
        return _empty(midi, config, "MANUAL_REVIEW", "DNC map is not confirmed", structural_hash, dnc_map, profile, sound)
    if dnc_map.source == "synthetic-test" and not config.allow_synthetic_map:
        return _empty(midi, config, "MANUAL_REVIEW", "Synthetic DNC map requires explicit test opt-in", structural_hash, dnc_map, profile, sound)
    if config.role not in dnc_map.roles:
        return _empty(midi, config, "MANUAL_REVIEW", "DNC map does not confirm the target role", structural_hash, dnc_map, profile, sound)
    if profile.sound != dnc_map.sound:
        return _empty(midi, config, "MANUAL_REVIEW", "Factory profile and DNC map sound mismatch", structural_hash, dnc_map, profile, sound)
    if sound is None:
        return _empty(midi, config, "MANUAL_REVIEW", "Exact Bank Select and Program Change are required", structural_hash, dnc_map, profile)
    if sound != dnc_map.sound:
        return _empty(midi, config, "MANUAL_REVIEW", "DNC map does not match target Bank/Program", structural_hash, dnc_map, profile, sound)
    by_name = {trigger.articulation: trigger for trigger in dnc_map.triggers}
    missing = [item for item in config.requested_articulations if item not in by_name]
    if missing:
        return _empty(midi, config, "MANUAL_REVIEW", f"DNC articulation is not confirmed: {missing[0]}", structural_hash, dnc_map, profile, sound)

    all_notes = [
        note for note in midi.notes()
        if note.track == config.track_index and note.channel == config.channel
        and config.start_tick <= note.start < config.end_tick
    ]
    sources = [
        note for note in all_notes
        if note.pitch not in trigger_notes and dnc_map.playable_min <= note.pitch <= dnc_map.playable_max
    ]
    if not sources:
        return _empty(midi, config, "KEEP", "No playable source notes in DNC window", structural_hash, dnc_map, profile, sound)
    existing_notes = [note for note in all_notes if note.pitch in trigger_notes]
    existing_events = midi.tracks[config.track_index].events
    notes: list[Note] = []
    events: list[MidiEvent] = []
    audits: list[DncAudit] = []
    counts = {item: 0 for item in config.requested_articulations}
    skipped = {"condition": 0, "boundary": 0, "existing": 0, "collision": 0}
    source_id = dnc_map.source_ids[0]
    occupied: dict[int, int] = {}
    for index, source in enumerate(sources):
        previous = sources[index - 1] if index else None
        for name in config.requested_articulations:
            trigger = by_name[name]
            if not _condition(trigger, source, previous):
                skipped["condition"] += 1
                continue
            tick = _tick(trigger, source)
            if tick < config.start_tick or tick >= config.end_tick:
                skipped["boundary"] += 1
                continue
            if trigger.event_type == "keyswitch":
                assert trigger.number is not None
                end = tick + trigger.duration_ticks
                if end > config.end_tick:
                    skipped["boundary"] += 1
                    continue
                if any(note.pitch == trigger.number and abs(note.start - tick) <= config.existing_tolerance_ticks for note in existing_notes):
                    skipped["existing"] += 1
                    continue
                if tick < occupied.get(trigger.number, -1):
                    skipped["collision"] += 1
                    continue
                notes.append(Note(
                    config.track_index, config.channel, trigger.number, tick, end,
                    profile.velocity(config.intensity + trigger.intensity_offset),
                    factory_profile_id=profile.profile_id, element=f"dnc:{name}",
                ))
                occupied[trigger.number] = end
            else:
                if trigger.event_type == "cc":
                    assert trigger.number is not None and trigger.value is not None
                    status = 0xB0 | config.channel
                    data = bytes((trigger.number, trigger.value))
                else:
                    assert trigger.value is not None
                    status = 0xD0 | config.channel
                    data = bytes((trigger.value,))
                if any(event.kind == "channel" and event.status == status and event.data == data and abs(event.tick - tick) <= config.existing_tolerance_ticks for event in existing_events):
                    skipped["existing"] += 1
                    continue
                events.append(MidiEvent(tick, -1, "channel", status=status, data=data))
            audits.append(DncAudit(name, trigger.event_type, index, source_id, trigger.condition, tick))
            counts[name] += 1

    if len(notes) + len(events) > config.max_generated_events:
        return _empty(midi, config, "MANUAL_REVIEW", "DNC transformation budget exceeded", structural_hash, dnc_map, profile, sound)
    if not notes and not events:
        plan = _empty(midi, config, "KEEP", "No DNC events required", structural_hash, dnc_map, profile, sound)
        return DncPlan(**{**plan.__dict__, "counts": counts, "skipped": skipped})
    notes.sort(key=lambda note: (note.start, note.pitch, note.end))
    events.sort(key=lambda event: (event.tick, event.status or 0, event.data))
    audits.sort(key=lambda item: (item.tick, item.articulation, item.source_note_index))
    selection = {
        "structure": structural_hash,
        "seed": config.seed,
        "map": dnc_map.map_id,
        "version": dnc_map.version,
        "profile": profile.profile_id,
        "notes": [[n.start, n.end, n.pitch, n.velocity] for n in notes],
        "events": [[e.tick, e.status, e.data.hex()] for e in events],
    }
    return DncPlan(
        "AUGMENT", "Confirmed DNC articulation events are available", midi.digest(), structural_hash,
        sha256(json.dumps(selection, sort_keys=True).encode()).hexdigest(), config,
        dnc_map.map_id, profile.profile_id, dnc_map.source, dnc_map.version,
        dnc_map.evidence, sound, tuple(notes), tuple(events), tuple(audits),
        _fingerprint(midi), counts, skipped, dnc_map.source_ids,
    )


def apply_dnc_events(midi: MidiFile, plan: DncPlan) -> DncResult:
    if midi.digest() != plan.input_hash:
        raise MidiFormatError("DNC plan input hash does not match MIDI")
    output = midi
    if plan.decision == "AUGMENT":
        if plan.generated_notes:
            output = output.add_notes(track_index=plan.config.track_index, new_notes=plan.generated_notes)
        if plan.generated_events:
            output = output.add_events(track_index=plan.config.track_index, new_events=plan.generated_events)
    elif plan.decision not in {"KEEP", "MANUAL_REVIEW"}:
        raise MidiFormatError(f"Unsupported DNC decision: {plan.decision}")
    before = Counter(plan.original_event_fingerprint)
    after = Counter(_fingerprint(output))
    preserved = all(after[item] >= count for item, count in before.items())
    if not preserved:
        raise MidiFormatError("DNC apply mutated a protected original event")
    sound_preserved = plan.exact_sound is None or _sound_at(output, plan.config) == plan.exact_sound
    if not sound_preserved:
        raise MidiFormatError("DNC apply changed Bank Select or Program Change")
    output.notes()
    manifest = {
        **plan.to_manifest(),
        "outputHash": output.digest(),
        "originalEventsPreserved": preserved,
        "targetSoundPreserved": sound_preserved,
        "notePairingValid": True,
    }
    return DncResult(output, manifest)