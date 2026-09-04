"""Session 3 chord-aware Bass, Power-Chord and Riff reconstruction.

GOLD patterns contain rhythmic positions and relative harmonic functions only.
Absolute pitch, velocity, register and sound identity are resolved by the chord
timeline, Factory instrument profile and protected MIDI events.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .drum_reconstruction import assert_gold_has_no_dynamic_authority
from .midi import MidiFile, MidiFormatError, Note


_STABLE_ID = re.compile(r"^\d{3}\.\d{3}\.\d{3}$")
_ROLES = {"bass", "power-riff", "riff"}
_FUNCTIONS = {"root", "scale", "chord"}
_APPROACHES = {"none", "chromatic-below", "chromatic-above"}
_SCALES = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
}


@dataclass(frozen=True)
class ChordCell:
    start_tick: int
    end_tick: int
    root_pc: int
    quality: str
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if self.start_tick < 0 or self.end_tick <= self.start_tick:
            raise ValueError("Invalid chord-cell window")
        if not 0 <= self.root_pc <= 11:
            raise ValueError("Chord root must be a pitch class in range 0..11")
        if self.quality not in _SCALES:
            raise ValueError(f"Unsupported chord quality: {self.quality}")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Chord confidence must be in range 0..1")


@dataclass(frozen=True)
class FactoryInstrumentProfile:
    profile_id: str
    role: str
    program: int
    register_min: int
    register_max: int
    floor: int
    soft: int
    low_mid: int
    optimal: int
    high_mid: int
    strong: int
    ceiling: int

    def __post_init__(self) -> None:
        if not _STABLE_ID.fullmatch(self.profile_id):
            raise ValueError(f"Invalid Factory profile ID: {self.profile_id}")
        if self.role not in _ROLES:
            raise ValueError(f"Unsupported Factory role: {self.role}")
        if not 0 <= self.program <= 127:
            raise ValueError("Program must be in range 0..127")
        if not 0 <= self.register_min <= self.register_max <= 127:
            raise ValueError("Invalid Factory register")
        if self.points != tuple(sorted(self.points)):
            raise ValueError("Factory velocity curve must be monotonic")
        if any(not 1 <= value <= 127 for value in self.points):
            raise ValueError("Factory velocity points must be in range 1..127")

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
        if not 0 <= intensity <= 100:
            raise ValueError("Intensity must be in range 0..100")
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
class HarmonicPatternEvent:
    tick: int
    duration: int
    function: str
    degree: int = 1
    intervals: tuple[int, ...] = (0,)
    inversion: int = 0
    approach: str = "none"

    def __post_init__(self) -> None:
        if self.tick < 0 or self.duration <= 0:
            raise ValueError("Invalid GOLD harmonic event timing")
        if self.function not in _FUNCTIONS:
            raise ValueError(f"Unsupported harmonic function: {self.function}")
        if not 1 <= self.degree <= 7:
            raise ValueError("Scale/chord degree must be in range 1..7")
        if not self.intervals or any(interval < 0 or interval > 24 for interval in self.intervals):
            raise ValueError("Intervals must be relative values in range 0..24")
        if tuple(sorted(set(self.intervals))) != self.intervals:
            raise ValueError("Intervals must be sorted and unique")
        if not 0 <= self.inversion < len(self.intervals):
            raise ValueError("Inversion lies outside the interval set")
        if self.approach not in _APPROACHES:
            raise ValueError(f"Unsupported approach type: {self.approach}")


@dataclass(frozen=True)
class HarmonicPattern:
    pattern_id: str
    role: str
    section: str
    meter: str
    length_ticks: int
    events: tuple[HarmonicPatternEvent, ...]
    evidence_quality: float = 1.0

    def __post_init__(self) -> None:
        if not _STABLE_ID.fullmatch(self.pattern_id):
            raise ValueError(f"Invalid GOLD pattern ID: {self.pattern_id}")
        if self.role not in _ROLES:
            raise ValueError(f"Unsupported GOLD role: {self.role}")
        if self.length_ticks <= 0 or not self.events:
            raise ValueError("GOLD pattern must have a positive length and events")
        if any(event.tick >= self.length_ticks for event in self.events):
            raise ValueError("GOLD event lies outside its pattern")
        if not 0 <= self.evidence_quality <= 1:
            raise ValueError("Evidence quality must be in range 0..1")


@dataclass(frozen=True)
class DrumBassRelationship:
    relationship_id: str
    drum_pattern_id: str
    bass_pattern_id: str
    confidence: float

    def __post_init__(self) -> None:
        for value in (
            self.relationship_id,
            self.drum_pattern_id,
            self.bass_pattern_id,
        ):
            if not _STABLE_ID.fullmatch(value):
                raise ValueError(f"Invalid relationship ID: {value}")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Relationship confidence must be in range 0..1")


@dataclass(frozen=True)
class HarmonicConfig:
    track_index: int
    channel: int
    role: str
    section: str
    start_tick: int
    end_tick: int
    seed: int
    intensity: int
    profile_id: str
    meter: str = "4/4"
    desired_notes_per_quarter: float = 1.5
    selected_drum_pattern_id: str | None = None
    require_relationship: bool = True
    manual_bass: bool = False
    existing_quality: float = 0.0
    protect_existing_above: float = 0.8
    collision_channels: tuple[int, ...] = ()
    collision_distance: int = 0
    collision_budget: int = 2

    def __post_init__(self) -> None:
        if self.role not in _ROLES:
            raise ValueError(f"Unsupported role: {self.role}")
        if self.role == "bass" and self.channel != 8:
            raise ValueError("Pa800 Bass must use MIDI channel 9")
        if self.role != "bass" and self.channel not in range(11, 16):
            raise ValueError("Power/riff roles must use a Pa800 ACC channel 12..16")
        if self.start_tick < 0 or self.end_tick <= self.start_tick:
            raise ValueError("Invalid reconstruction window")
        if not 0 <= self.intensity <= 100:
            raise ValueError("Intensity must be in range 0..100")
        if not _STABLE_ID.fullmatch(self.profile_id):
            raise ValueError(f"Invalid profile ID: {self.profile_id}")
        if self.desired_notes_per_quarter <= 0:
            raise ValueError("Desired density must be positive")
        if not 0 <= self.existing_quality <= 1 or not 0 <= self.protect_existing_above <= 1:
            raise ValueError("Quality thresholds must be in range 0..1")
        if self.collision_distance < 0 or self.collision_budget < 0:
            raise ValueError("Collision limits cannot be negative")
        channels = tuple(int(channel) for channel in self.collision_channels)
        if any(channel not in range(16) for channel in channels):
            raise ValueError("Collision channel lies outside MIDI range")
        object.__setattr__(self, "collision_channels", channels)


@dataclass(frozen=True)
class HarmonicPlan:
    decision: str
    reason: str
    input_hash: str
    selection_hash: str
    config: HarmonicConfig
    pattern_id: str | None
    relationship_id: str | None
    candidate_ids: tuple[str, ...]
    generated_notes: tuple[Note, ...]
    removed_notes: int
    collision_shifts: int
    collision_drops: int
    max_voice_leading_leap: int
    chord_cells_used: tuple[tuple[int, int, int, str], ...]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "schema": "dna-session3-harmonic-plan",
            "version": "1.0",
            "decision": self.decision,
            "reason": self.reason,
            "inputHash": self.input_hash,
            "selectionHash": self.selection_hash,
            "seed": self.config.seed,
            "role": self.config.role,
            "channel": self.config.channel + 1,
            "section": self.config.section,
            "window": [self.config.start_tick, self.config.end_tick],
            "profileId": self.config.profile_id,
            "patternId": self.pattern_id,
            "relationshipId": self.relationship_id,
            "selectedDrumPatternId": self.config.selected_drum_pattern_id,
            "candidateIds": list(self.candidate_ids),
            "removedNotes": self.removed_notes,
            "generatedNotes": len(self.generated_notes),
            "collisionShifts": self.collision_shifts,
            "collisionDrops": self.collision_drops,
            "maxVoiceLeadingLeap": self.max_voice_leading_leap,
            "chordCellsUsed": [
                {"start": start, "end": end, "rootPc": root, "quality": quality}
                for start, end, root, quality in self.chord_cells_used
            ],
            "manualBassProtected": self.config.manual_bass,
            "goldContainsAbsolutePitch": False,
            "goldAffectsVelocity": False,
            "goldAffectsProgramChange": False,
            "notes": [
                {
                    "tick": note.start,
                    "duration": note.end - note.start,
                    "note": note.pitch,
                    "velocity": note.velocity,
                    "factoryProfileId": note.factory_profile_id,
                    "goldPatternId": note.gold_pattern_id,
                    "function": note.element,
                }
                for note in self.generated_notes
            ],
        }


@dataclass(frozen=True)
class HarmonicResult:
    midi: MidiFile
    manifest: dict[str, Any]


def load_harmonic_registry(
    path: str | Path,
) -> tuple[
    list[HarmonicPattern],
    dict[str, FactoryInstrumentProfile],
    list[DrumBassRelationship],
]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    gold_raw = raw.get("goldPatterns", [])
    assert_gold_has_no_dynamic_authority(gold_raw)
    _assert_no_absolute_gold_pitch(gold_raw)
    patterns = [
        HarmonicPattern(
            pattern_id=item["id"],
            role=item["role"],
            section=item["section"],
            meter=item.get("meter", "4/4"),
            length_ticks=int(item["lengthTicks"]),
            evidence_quality=float(item.get("evidenceQuality", 1.0)),
            events=tuple(
                HarmonicPatternEvent(
                    tick=int(event["tick"]),
                    duration=int(event["duration"]),
                    function=event["function"],
                    degree=int(event.get("degree", 1)),
                    intervals=tuple(int(value) for value in event.get("intervals", [0])),
                    inversion=int(event.get("inversion", 0)),
                    approach=event.get("approach", "none"),
                )
                for event in item["events"]
            ),
        )
        for item in gold_raw
    ]
    profiles: dict[str, FactoryInstrumentProfile] = {}
    for item in raw.get("factoryProfiles", []):
        profile = FactoryInstrumentProfile(
            profile_id=item["id"],
            role=item["role"],
            program=int(item["program"]),
            register_min=int(item["registerMin"]),
            register_max=int(item["registerMax"]),
            floor=int(item["floor"]),
            soft=int(item["soft"]),
            low_mid=int(item["lowMid"]),
            optimal=int(item["optimal"]),
            high_mid=int(item["highMid"]),
            strong=int(item["strong"]),
            ceiling=int(item["ceiling"]),
        )
        if profile.profile_id in profiles:
            raise ValueError(f"Duplicate Factory profile: {profile.profile_id}")
        profiles[profile.profile_id] = profile
    relationships = [
        DrumBassRelationship(
            relationship_id=item["id"],
            drum_pattern_id=item["drumPatternId"],
            bass_pattern_id=item["bassPatternId"],
            confidence=float(item["confidence"]),
        )
        for item in raw.get("drumBassRelationships", [])
    ]
    pattern_ids = {pattern.pattern_id for pattern in patterns}
    for relationship in relationships:
        if relationship.bass_pattern_id not in pattern_ids:
            raise ValueError(
                f"Relationship references missing bass pattern {relationship.bass_pattern_id}"
            )
    return patterns, profiles, relationships


def plan_harmonic_reconstruction(
    midi: MidiFile,
    patterns: Iterable[HarmonicPattern],
    profiles: Mapping[str, FactoryInstrumentProfile],
    relationships: Iterable[DrumBassRelationship],
    chords: Sequence[ChordCell],
    config: HarmonicConfig,
) -> HarmonicPlan:
    input_hash = midi.digest()
    profile = profiles.get(config.profile_id)
    if profile is None or profile.role != config.role:
        return _empty_plan(
            input_hash,
            config,
            "MANUAL_REVIEW",
            "Required Factory instrument profile is missing or has the wrong role",
        )
    if midi.program_at(config.channel, config.start_tick, config.track_index) != profile.program:
        return _empty_plan(
            input_hash,
            config,
            "MANUAL_REVIEW",
            "Protected Program Change does not match the Factory profile",
        )
    if config.role == "bass" and (config.manual_bass or _track_is_manual_bass(midi, config.track_index)):
        return _empty_plan(
            input_hash,
            config,
            "KEEP",
            "Manual bass is protected from automatic reconstruction",
        )
    if config.role in {"power-riff", "riff"} and config.existing_quality >= config.protect_existing_above:
        return _empty_plan(
            input_hash,
            config,
            "KEEP",
            "Existing power/riff identity meets the protection threshold",
        )

    chord_cells = sorted(chords, key=lambda item: (item.start_tick, item.end_tick))
    if not _chords_cover_window(chord_cells, config.start_tick, config.end_tick):
        return _empty_plan(
            input_hash,
            config,
            "MANUAL_REVIEW",
            "Chord timeline does not cover the complete reconstruction window",
        )

    candidates = [
        pattern
        for pattern in patterns
        if pattern.role == config.role
        and pattern.meter == config.meter
        and pattern.section in {config.section, "generic"}
    ]
    relationship_map = {
        relation.bass_pattern_id: relation
        for relation in relationships
        if relation.drum_pattern_id == config.selected_drum_pattern_id
    }
    if config.role == "bass" and config.require_relationship:
        candidates = [
            pattern for pattern in candidates if pattern.pattern_id in relationship_map
        ]
        if not candidates:
            return _empty_plan(
                input_hash,
                config,
                "MANUAL_REVIEW",
                "No confirmed drum-bass relationship for the selected drum pattern",
            )
    if not candidates:
        return _empty_plan(
            input_hash,
            config,
            "KEEP",
            "No compatible relative GOLD harmonic pattern",
        )

    scored = sorted(
        (
            _pattern_score(pattern, relationship_map.get(pattern.pattern_id), midi.ppq, config),
            pattern.pattern_id,
            pattern,
        )
        for pattern in candidates
    )
    best_score = scored[-1][0]
    best = [item[2] for item in scored if best_score - item[0] <= 0.25]
    best.sort(key=lambda pattern: pattern.pattern_id)
    candidate_ids = tuple(pattern.pattern_id for pattern in best)
    chooser = sha256(
        f"{config.seed}|{input_hash}|{'|'.join(candidate_ids)}".encode("utf-8")
    ).digest()
    selected = best[int.from_bytes(chooser[:8], "big") % len(best)]
    relationship = relationship_map.get(selected.pattern_id)

    try:
        generated, shifts, drops, leap, cells_used = _expand_harmonic_pattern(
            midi, selected, profile, chord_cells, config
        )
    except ValueError as exc:
        return _empty_plan(
            input_hash,
            config,
            "MANUAL_REVIEW",
            f"No safe Factory-register voicing: {exc}",
            pattern_id=selected.pattern_id,
            relationship_id=relationship.relationship_id if relationship else None,
            candidate_ids=candidate_ids,
        )
    if drops > config.collision_budget:
        return _empty_plan(
            input_hash,
            config,
            "MANUAL_REVIEW",
            f"Collision budget exceeded: {drops} > {config.collision_budget}",
            pattern_id=selected.pattern_id,
            relationship_id=relationship.relationship_id if relationship else None,
            candidate_ids=candidate_ids,
            collision_shifts=shifts,
            collision_drops=drops,
        )
    if not generated:
        return _empty_plan(
            input_hash,
            config,
            "MANUAL_REVIEW",
            "Harmonic mapping produced no safe notes",
            pattern_id=selected.pattern_id,
            relationship_id=relationship.relationship_id if relationship else None,
            candidate_ids=candidate_ids,
            collision_shifts=shifts,
            collision_drops=drops,
        )
    target_notes = [
        note
        for note in midi.notes()
        if note.track == config.track_index
        and note.channel == config.channel
        and config.start_tick <= note.start < config.end_tick
    ]
    payload = {
        "input": input_hash,
        "seed": config.seed,
        "pattern": selected.pattern_id,
        "relationship": relationship.relationship_id if relationship else None,
        "profile": profile.profile_id,
        "notes": [
            [note.start, note.end, note.pitch, note.velocity, note.element]
            for note in generated
        ],
    }
    selection_hash = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return HarmonicPlan(
        decision="REPLACE",
        reason="Relative GOLD functions mapped through chords and Factory constraints",
        input_hash=input_hash,
        selection_hash=selection_hash,
        config=config,
        pattern_id=selected.pattern_id,
        relationship_id=relationship.relationship_id if relationship else None,
        candidate_ids=candidate_ids,
        generated_notes=tuple(generated),
        removed_notes=len(target_notes),
        collision_shifts=shifts,
        collision_drops=drops,
        max_voice_leading_leap=leap,
        chord_cells_used=tuple(cells_used),
    )


def apply_harmonic_reconstruction(
    midi: MidiFile, plan: HarmonicPlan
) -> HarmonicResult:
    if midi.digest() != plan.input_hash:
        raise ValueError("Input MIDI changed after the harmonic plan was created")
    if plan.decision != "REPLACE":
        manifest = plan.to_manifest()
        manifest.update(
            {
                "applied": False,
                "outputHash": midi.digest(),
                "programChangePreserved": True,
                "notePairingValid": True,
            }
        )
        return HarmonicResult(midi, manifest)
    before_program = midi.program_at(plan.config.channel, plan.config.start_tick, plan.config.track_index)
    output = midi.replace_notes(
        track_index=plan.config.track_index,
        channel=plan.config.channel,
        start_tick=plan.config.start_tick,
        end_tick=plan.config.end_tick,
        new_notes=plan.generated_notes,
    )
    output.notes()
    if output.program_at(plan.config.channel, plan.config.start_tick, plan.config.track_index) != before_program:
        raise MidiFormatError("Harmonic reconstruction changed Program Change")
    manifest = plan.to_manifest()
    manifest.update(
        {
            "applied": True,
            "outputHash": output.digest(),
            "programChangePreserved": True,
            "notePairingValid": True,
        }
    )
    return HarmonicResult(output, manifest)


def _assert_no_absolute_gold_pitch(value: Any, path: str = "gold") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in {"note", "notes", "pitch", "midinote", "absolutepitch"}:
                raise ValueError(f"Absolute pitch is forbidden in harmonic GOLD at {path}.{key}")
            _assert_no_absolute_gold_pitch(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_absolute_gold_pitch(child, f"{path}[{index}]")


def _pattern_score(
    pattern: HarmonicPattern,
    relationship: DrumBassRelationship | None,
    ppq: int,
    config: HarmonicConfig,
) -> float:
    density = len(pattern.events) * ppq / pattern.length_ticks
    return (
        (10.0 if pattern.section == config.section else 5.0)
        + max(0.0, 4.0 - abs(density - config.desired_notes_per_quarter))
        + pattern.evidence_quality * 3.0
        + (relationship.confidence * 10.0 if relationship else 0.0)
    )


def _expand_harmonic_pattern(
    midi: MidiFile,
    pattern: HarmonicPattern,
    profile: FactoryInstrumentProfile,
    chords: Sequence[ChordCell],
    config: HarmonicConfig,
) -> tuple[list[Note], int, int, int, list[tuple[int, int, int, str]]]:
    obstacles = [
        note
        for note in midi.notes()
        if note.channel in config.collision_channels
        and note.track != config.track_index
        and note.end > config.start_tick
        and note.start < config.end_tick
    ]
    generated: list[Note] = []
    collision_shifts = 0
    collision_drops = 0
    previous_anchor: float | None = None
    max_leap = 0
    used: dict[tuple[int, int], tuple[int, int, int, str]] = {}
    cycle_start = config.start_tick
    while cycle_start < config.end_tick:
        for event in pattern.events:
            start = cycle_start + event.tick
            if start >= config.end_tick:
                continue
            chord = _chord_at(chords, start)
            if chord is None:
                continue
            used[(chord.start_tick, chord.end_tick)] = (
                chord.start_tick,
                chord.end_tick,
                chord.root_pc,
                chord.quality,
            )
            end = min(config.end_tick, start + event.duration)
            pitches = _map_event_pitches(event, chord, profile, previous_anchor)
            adjusted, shifted, dropped = _resolve_collisions(
                pitches,
                start,
                end,
                obstacles,
                profile,
                config.collision_distance,
            )
            collision_shifts += shifted
            collision_drops += dropped
            if not adjusted:
                continue
            anchor = sum(adjusted) / len(adjusted)
            if previous_anchor is not None:
                max_leap = max(max_leap, int(round(abs(anchor - previous_anchor))))
            previous_anchor = anchor
            for pitch in adjusted:
                generated.append(
                    Note(
                        track=config.track_index,
                        channel=config.channel,
                        pitch=pitch,
                        start=start,
                        end=end,
                        velocity=profile.velocity(config.intensity),
                        factory_profile_id=profile.profile_id,
                        gold_pattern_id=pattern.pattern_id,
                        element=f"{event.function}:{event.degree}:{event.approach}",
                    )
                )
        cycle_start += pattern.length_ticks
    return _dedupe_and_shorten(generated), collision_shifts, collision_drops, max_leap, list(used.values())


def _map_event_pitches(
    event: HarmonicPatternEvent,
    chord: ChordCell,
    profile: FactoryInstrumentProfile,
    previous_anchor: float | None,
) -> tuple[int, ...]:
    scale = _SCALES[chord.quality]
    offset = 0 if event.function == "root" else scale[event.degree - 1]
    if event.approach == "chromatic-below":
        offset -= 1
    elif event.approach == "chromatic-above":
        offset += 1
    pitch_class = (chord.root_pc + offset) % 12
    intervals = list(event.intervals)
    for _ in range(event.inversion):
        intervals.append(intervals.pop(0) + 12)
    candidates: list[tuple[float, tuple[int, ...]]] = []
    for base in range(pitch_class, 128, 12):
        pitches = tuple(base + interval for interval in intervals)
        if pitches[0] < profile.register_min or pitches[-1] > profile.register_max:
            continue
        anchor = sum(pitches) / len(pitches)
        target = previous_anchor if previous_anchor is not None else (
            profile.register_min + profile.register_max
        ) / 2
        candidates.append((abs(anchor - target), pitches))
    if not candidates:
        raise ValueError(
            f"No safe voicing for pitch class {pitch_class} in Factory register"
        )
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][1]


def _resolve_collisions(
    pitches: tuple[int, ...],
    start: int,
    end: int,
    obstacles: Sequence[Note],
    profile: FactoryInstrumentProfile,
    distance: int,
) -> tuple[tuple[int, ...], int, int]:
    def collisions(candidate: tuple[int, ...]) -> int:
        return sum(
            1
            for pitch in candidate
            if any(
                other.end > start
                and other.start < end
                and abs(other.pitch - pitch) <= distance
                for other in obstacles
            )
        )

    options = [pitches]
    for shift in (-12, 12):
        shifted = tuple(pitch + shift for pitch in pitches)
        if shifted[0] >= profile.register_min and shifted[-1] <= profile.register_max:
            options.append(shifted)
    options.sort(key=lambda candidate: (collisions(candidate), candidate != pitches, candidate))
    chosen = options[0]
    shifted_count = int(chosen != pitches)
    safe = tuple(
        pitch
        for pitch in chosen
        if not any(
            other.end > start
            and other.start < end
            and abs(other.pitch - pitch) <= distance
            for other in obstacles
        )
    )
    return safe, shifted_count, len(chosen) - len(safe)


def _dedupe_and_shorten(notes: Iterable[Note]) -> list[Note]:
    unique: dict[tuple[int, int, int], Note] = {}
    for note in sorted(notes, key=lambda item: (item.start, item.pitch, item.end)):
        unique.setdefault((note.track, note.start, note.pitch), note)
    groups: dict[tuple[int, int, int], list[Note]] = {}
    for note in unique.values():
        groups.setdefault((note.track, note.channel, note.pitch), []).append(note)
    output: list[Note] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda item: (item.start, item.end))
        for index, note in enumerate(ordered):
            if index + 1 < len(ordered) and note.end > ordered[index + 1].start:
                note = replace(note, end=ordered[index + 1].start)
            if note.end > note.start:
                output.append(note)
    return sorted(output, key=lambda item: (item.start, item.pitch, item.end))


def _chord_at(chords: Sequence[ChordCell], tick: int) -> ChordCell | None:
    return next((chord for chord in chords if chord.start_tick <= tick < chord.end_tick), None)


def _chords_cover_window(chords: Sequence[ChordCell], start: int, end: int) -> bool:
    cursor = start
    for chord in chords:
        if chord.end_tick <= cursor:
            continue
        if chord.start_tick > cursor:
            return False
        cursor = max(cursor, chord.end_tick)
        if cursor >= end:
            return True
    return False


def _track_is_manual_bass(midi: MidiFile, track_index: int) -> bool:
    if not 0 <= track_index < len(midi.tracks):
        return False
    for event in midi.tracks[track_index].events:
        if event.kind == "meta" and event.meta_type == 0x03:
            name = event.data.decode("latin-1", errors="ignore").lower()
            if "manual bass" in name or "manual-bass" in name:
                return True
    return False


def _empty_plan(
    input_hash: str,
    config: HarmonicConfig,
    decision: str,
    reason: str,
    *,
    pattern_id: str | None = None,
    relationship_id: str | None = None,
    candidate_ids: tuple[str, ...] = (),
    collision_shifts: int = 0,
    collision_drops: int = 0,
) -> HarmonicPlan:
    return HarmonicPlan(
        decision=decision,
        reason=reason,
        input_hash=input_hash,
        selection_hash=sha256(
            f"{input_hash}|{config.seed}|{decision}|{reason}".encode("utf-8")
        ).hexdigest(),
        config=config,
        pattern_id=pattern_id,
        relationship_id=relationship_id,
        candidate_ids=candidate_ids,
        generated_notes=(),
        removed_notes=0,
        collision_shifts=collision_shifts,
        collision_drops=collision_drops,
        max_voice_leading_leap=0,
        chord_cells_used=(),
    )