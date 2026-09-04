"""Session 4 Factory-only Guitar Mode and strumming reconstruction."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .harmonic_reconstruction import ChordCell
from .midi import MidiFile, MidiFormatError, Note


_STABLE_ID = re.compile(r"^\d{3}\.\d{3}\.\d{3}$")
_DIRECTIONS = {"down", "up", "block", "mute", "stop"}
_CONTROL_DIRECTIONS = {"mute", "stop"}
_CHORD_INTERVALS = {"major": (0, 4, 7), "minor": (0, 3, 7)}
_CONTROL_SOURCES = {"official-korg", "device-captured", "synthetic-test"}


@dataclass(frozen=True)
class FactoryGuitarProfile:
    profile_id: str
    program: int
    tuning: tuple[int, ...]
    fret_min: int
    fret_max: int
    max_fret_span: int
    register_min: int
    register_max: int
    floor: int
    soft: int
    low_mid: int
    optimal: int
    high_mid: int
    strong: int
    ceiling: int
    control_map_id: str | None = None

    def __post_init__(self) -> None:
        if not _STABLE_ID.fullmatch(self.profile_id):
            raise ValueError(f"Invalid Factory guitar profile ID: {self.profile_id}")
        if not 0 <= self.program <= 127:
            raise ValueError("Guitar program must be in range 0..127")
        if len(self.tuning) != 6 or tuple(sorted(self.tuning)) != self.tuning:
            raise ValueError("Guitar tuning must contain six ascending open-string notes")
        if any(not 0 <= note <= 127 for note in self.tuning):
            raise ValueError("Invalid open-string note")
        if not 0 <= self.fret_min <= self.fret_max <= 24:
            raise ValueError("Invalid guitar fret range")
        if self.max_fret_span < 0:
            raise ValueError("Maximum fret span cannot be negative")
        if not 0 <= self.register_min <= self.register_max <= 127:
            raise ValueError("Invalid guitar register")
        if self.points != tuple(sorted(self.points)):
            raise ValueError("Factory guitar velocity curve must be monotonic")
        if any(not 1 <= value <= 127 for value in self.points):
            raise ValueError("Factory guitar velocity points must be in range 1..127")
        if self.control_map_id is not None and not _STABLE_ID.fullmatch(self.control_map_id):
            raise ValueError(f"Invalid Guitar Mode control-map ID: {self.control_map_id}")

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
class FactoryStrumStroke:
    tick: int
    direction: str
    strings: tuple[int, ...]
    chord_tones: tuple[int, ...]
    offsets: tuple[int, ...]
    gate_ticks: int
    control_action: str | None = None

    def __post_init__(self) -> None:
        if self.tick < 0 or self.gate_ticks <= 0:
            raise ValueError("Invalid Factory strum timing")
        if self.direction not in _DIRECTIONS:
            raise ValueError(f"Unsupported strum direction: {self.direction}")
        if self.direction in _CONTROL_DIRECTIONS:
            if self.strings or self.chord_tones or self.offsets:
                raise ValueError("Mute/stop strokes cannot contain pitched strings")
            if self.control_action != self.direction:
                raise ValueError("Mute/stop stroke requires the matching control action")
            return
        if self.control_action is not None:
            raise ValueError("Pitched strum cannot contain a control action")
        if not self.strings or not (
            len(self.strings) == len(self.chord_tones) == len(self.offsets)
        ):
            raise ValueError("Strum strings, chord tones and offsets must align")
        if len(set(self.strings)) != len(self.strings):
            raise ValueError("A strum cannot use the same string twice")
        if any(string not in range(6) for string in self.strings):
            raise ValueError("String index must be in range 0..5")
        if any(tone not in range(3) for tone in self.chord_tones):
            raise ValueError("Chord-tone index must be root, third or fifth")
        if any(offset < 0 for offset in self.offsets):
            raise ValueError("Inter-string offsets cannot be negative")
        pairs = sorted(zip(self.strings, self.offsets))
        ordered_offsets = [offset for _, offset in pairs]
        if self.direction == "down" and ordered_offsets != sorted(ordered_offsets):
            raise ValueError("Downstroke offsets must travel from low to high strings")
        if self.direction == "up" and ordered_offsets != sorted(ordered_offsets, reverse=True):
            raise ValueError("Upstroke offsets must travel from high to low strings")
        if self.direction == "block" and any(self.offsets):
            raise ValueError("Block chord offsets must all be zero")


@dataclass(frozen=True)
class FactoryStrumPattern:
    pattern_id: str
    profile_id: str
    section: str
    meter: str
    length_ticks: int
    strokes: tuple[FactoryStrumStroke, ...]
    evidence_quality: float
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _STABLE_ID.fullmatch(self.pattern_id):
            raise ValueError(f"Invalid Factory strum pattern ID: {self.pattern_id}")
        if not _STABLE_ID.fullmatch(self.profile_id):
            raise ValueError(f"Invalid Factory profile reference: {self.profile_id}")
        if self.length_ticks <= 0 or not self.strokes:
            raise ValueError("Factory strum pattern requires a length and strokes")
        if any(stroke.tick >= self.length_ticks for stroke in self.strokes):
            raise ValueError("Factory strum stroke lies outside its pattern")
        if not 0 <= self.evidence_quality <= 1:
            raise ValueError("Strum evidence quality must be in range 0..1")
        if not self.source_ids:
            raise ValueError("Factory strum pattern requires source evidence")


@dataclass(frozen=True)
class GuitarControlTrigger:
    note: int
    duration_ticks: int

    def __post_init__(self) -> None:
        if not 0 <= self.note <= 127 or self.duration_ticks <= 0:
            raise ValueError("Invalid Guitar Mode control trigger")


@dataclass(frozen=True)
class GuitarControlMap:
    map_id: str
    version: str
    source: str
    evidence: str
    confirmed: bool
    program: int
    actions: Mapping[str, GuitarControlTrigger]

    def __post_init__(self) -> None:
        if not _STABLE_ID.fullmatch(self.map_id):
            raise ValueError(f"Invalid Guitar Mode map ID: {self.map_id}")
        if not self.version or self.source not in _CONTROL_SOURCES or not self.evidence:
            raise ValueError("Guitar Mode map requires versioned source evidence")
        if not 0 <= self.program <= 127:
            raise ValueError("Guitar Mode map program is invalid")
        if any(action not in _CONTROL_DIRECTIONS for action in self.actions):
            raise ValueError("Unknown Guitar Mode control action")


@dataclass(frozen=True)
class GuitarConfig:
    track_index: int
    channel: int
    section: str
    start_tick: int
    end_tick: int
    seed: int
    intensity: int
    profile_id: str
    meter: str = "4/4"
    enable_controls: bool = True
    allow_synthetic_control_map: bool = False

    def __post_init__(self) -> None:
        if self.channel not in range(11, 16):
            raise ValueError("Factory Guitar Mode must use Pa800 ACC channel 12..16")
        if self.start_tick < 0 or self.end_tick <= self.start_tick:
            raise ValueError("Invalid guitar reconstruction window")
        if not 0 <= self.intensity <= 100:
            raise ValueError("Intensity must be in range 0..100")
        if not _STABLE_ID.fullmatch(self.profile_id):
            raise ValueError(f"Invalid Factory guitar profile ID: {self.profile_id}")


@dataclass(frozen=True)
class GuitarNoteAudit:
    tick: int
    pitch: int
    direction: str
    string: int | None
    fret: int | None
    offset: int
    control_action: str | None


@dataclass(frozen=True)
class GuitarPlan:
    decision: str
    reason: str
    input_hash: str
    selection_hash: str
    config: GuitarConfig
    pattern_id: str | None
    control_map_id: str | None
    candidate_ids: tuple[str, ...]
    generated_notes: tuple[Note, ...]
    note_audit: tuple[GuitarNoteAudit, ...]
    removed_notes: int
    stroke_counts: Mapping[str, int]
    max_fret_span: int
    source_ids: tuple[str, ...]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "schema": "dna-session4-guitar-plan",
            "version": "1.0",
            "decision": self.decision,
            "reason": self.reason,
            "inputHash": self.input_hash,
            "selectionHash": self.selection_hash,
            "seed": self.config.seed,
            "channel": self.config.channel + 1,
            "section": self.config.section,
            "window": [self.config.start_tick, self.config.end_tick],
            "factoryProfileId": self.config.profile_id,
            "factoryPatternId": self.pattern_id,
            "controlMapId": self.control_map_id,
            "candidateIds": list(self.candidate_ids),
            "sourceIds": list(self.source_ids),
            "removedNotes": self.removed_notes,
            "generatedNotes": len(self.generated_notes),
            "strokeCounts": dict(self.stroke_counts),
            "maxFretSpan": self.max_fret_span,
            "goldControlsRhythmGuitar": False,
            "controlNotesGuessed": False,
            "goldAffectsVelocity": False,
            "notes": [
                {
                    "tick": note.start,
                    "duration": note.end - note.start,
                    "note": note.pitch,
                    "velocity": note.velocity,
                    "direction": audit.direction,
                    "string": audit.string,
                    "fret": audit.fret,
                    "offset": audit.offset,
                    "controlAction": audit.control_action,
                    "factoryProfileId": note.factory_profile_id,
                    "factoryPatternId": note.gold_pattern_id,
                }
                for note, audit in zip(self.generated_notes, self.note_audit)
            ],
        }


@dataclass(frozen=True)
class GuitarResult:
    midi: MidiFile
    manifest: dict[str, Any]


def load_guitar_registry(
    path: str | Path,
) -> tuple[
    list[FactoryStrumPattern],
    dict[str, FactoryGuitarProfile],
    dict[str, GuitarControlMap],
]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("goldPatterns"):
        raise ValueError("Rhythm guitar cannot use GOLD patterns")
    profiles: dict[str, FactoryGuitarProfile] = {}
    for item in raw.get("factoryProfiles", []):
        profile = FactoryGuitarProfile(
            profile_id=item["id"],
            program=int(item["program"]),
            tuning=tuple(int(value) for value in item["tuning"]),
            fret_min=int(item["fretMin"]),
            fret_max=int(item["fretMax"]),
            max_fret_span=int(item["maxFretSpan"]),
            register_min=int(item["registerMin"]),
            register_max=int(item["registerMax"]),
            floor=int(item["floor"]),
            soft=int(item["soft"]),
            low_mid=int(item["lowMid"]),
            optimal=int(item["optimal"]),
            high_mid=int(item["highMid"]),
            strong=int(item["strong"]),
            ceiling=int(item["ceiling"]),
            control_map_id=item.get("controlMapId"),
        )
        if profile.profile_id in profiles:
            raise ValueError(f"Duplicate Factory guitar profile {profile.profile_id}")
        profiles[profile.profile_id] = profile
    patterns = [
        FactoryStrumPattern(
            pattern_id=item["id"],
            profile_id=item["profileId"],
            section=item["section"],
            meter=item.get("meter", "4/4"),
            length_ticks=int(item["lengthTicks"]),
            evidence_quality=float(item["evidenceQuality"]),
            source_ids=tuple(item["sourceIds"]),
            strokes=tuple(
                FactoryStrumStroke(
                    tick=int(stroke["tick"]),
                    direction=stroke["direction"],
                    strings=tuple(int(value) for value in stroke.get("strings", [])),
                    chord_tones=tuple(int(value) for value in stroke.get("chordTones", [])),
                    offsets=tuple(int(value) for value in stroke.get("offsets", [])),
                    gate_ticks=int(stroke["gateTicks"]),
                    control_action=stroke.get("controlAction"),
                )
                for stroke in item["strokes"]
            ),
        )
        for item in raw.get("factoryPatterns", [])
    ]
    control_maps: dict[str, GuitarControlMap] = {}
    for item in raw.get("controlMaps", []):
        control_map = GuitarControlMap(
            map_id=item["id"],
            version=item["version"],
            source=item["source"],
            evidence=item["evidence"],
            confirmed=bool(item["confirmed"]),
            program=int(item["program"]),
            actions={
                action: GuitarControlTrigger(
                    note=int(trigger["note"]),
                    duration_ticks=int(trigger["durationTicks"]),
                )
                for action, trigger in item.get("actions", {}).items()
            },
        )
        if control_map.map_id in control_maps:
            raise ValueError(f"Duplicate Guitar Mode map {control_map.map_id}")
        control_maps[control_map.map_id] = control_map
    return patterns, profiles, control_maps


def plan_guitar_reconstruction(
    midi: MidiFile,
    patterns: Iterable[FactoryStrumPattern],
    profiles: Mapping[str, FactoryGuitarProfile],
    control_maps: Mapping[str, GuitarControlMap],
    chords: Sequence[ChordCell],
    config: GuitarConfig,
) -> GuitarPlan:
    input_hash = midi.digest()
    profile = profiles.get(config.profile_id)
    if profile is None:
        return _empty_plan(input_hash, config, "MANUAL_REVIEW", "Factory guitar profile is missing")
    if midi.program_at(config.channel, config.start_tick, config.track_index) != profile.program:
        return _empty_plan(
            input_hash,
            config,
            "MANUAL_REVIEW",
            "Protected Program Change does not match Factory guitar profile",
        )
    chord_cells = sorted(chords, key=lambda item: (item.start_tick, item.end_tick))
    if not _chords_cover_window(chord_cells, config.start_tick, config.end_tick):
        return _empty_plan(
            input_hash,
            config,
            "MANUAL_REVIEW",
            "Chord timeline does not cover the complete guitar window",
        )
    candidates = [
        pattern
        for pattern in patterns
        if pattern.profile_id == profile.profile_id
        and pattern.meter == config.meter
        and pattern.section in {config.section, "generic"}
    ]
    if not candidates:
        return _empty_plan(
            input_hash,
            config,
            "KEEP",
            "No proven Factory ACC strumming pattern is compatible",
        )
    exact_section = [
        pattern for pattern in candidates if pattern.section == config.section
    ]
    if exact_section:
        candidates = exact_section
    candidates.sort(
        key=lambda pattern: (
            -(10 if pattern.section == config.section else 5),
            -pattern.evidence_quality,
            pattern.pattern_id,
        )
    )
    best_quality = candidates[0].evidence_quality
    best = [pattern for pattern in candidates if best_quality - pattern.evidence_quality <= 0.02]
    candidate_ids = tuple(pattern.pattern_id for pattern in best)
    chooser = sha256(
        f"{config.seed}|{input_hash}|{'|'.join(candidate_ids)}".encode("utf-8")
    ).digest()
    selected = best[int.from_bytes(chooser[:8], "big") % len(best)]

    control_map, control_error = _resolve_control_map(selected, profile, control_maps, config)
    if control_error:
        return _empty_plan(
            input_hash,
            config,
            "MANUAL_REVIEW",
            control_error,
            pattern_id=selected.pattern_id,
            candidate_ids=candidate_ids,
            source_ids=selected.source_ids,
        )
    try:
        notes, audits, counts, max_span = _expand_pattern(
            selected, profile, control_map, chord_cells, config
        )
    except ValueError as exc:
        return _empty_plan(
            input_hash,
            config,
            "MANUAL_REVIEW",
            f"No playable Factory guitar voicing: {exc}",
            pattern_id=selected.pattern_id,
            control_map_id=control_map.map_id if control_map else None,
            candidate_ids=candidate_ids,
            source_ids=selected.source_ids,
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
        "profile": profile.profile_id,
        "controlMap": control_map.map_id if control_map else None,
        "notes": [
            [note.start, note.end, note.pitch, note.velocity, note.element]
            for note in notes
        ],
    }
    selection_hash = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return GuitarPlan(
        decision="REPLACE",
        reason="Proven Factory ACC strumming mapped to playable chord voicings",
        input_hash=input_hash,
        selection_hash=selection_hash,
        config=config,
        pattern_id=selected.pattern_id,
        control_map_id=control_map.map_id if control_map else None,
        candidate_ids=candidate_ids,
        generated_notes=tuple(notes),
        note_audit=tuple(audits),
        removed_notes=len(target_notes),
        stroke_counts=counts,
        max_fret_span=max_span,
        source_ids=selected.source_ids,
    )


def apply_guitar_reconstruction(midi: MidiFile, plan: GuitarPlan) -> GuitarResult:
    if midi.digest() != plan.input_hash:
        raise ValueError("Input MIDI changed after the guitar plan was created")
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
        return GuitarResult(midi, manifest)
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
        raise MidiFormatError("Guitar reconstruction changed Program Change")
    manifest = plan.to_manifest()
    manifest.update(
        {
            "applied": True,
            "outputHash": output.digest(),
            "programChangePreserved": True,
            "notePairingValid": True,
        }
    )
    return GuitarResult(output, manifest)


def _resolve_control_map(
    pattern: FactoryStrumPattern,
    profile: FactoryGuitarProfile,
    control_maps: Mapping[str, GuitarControlMap],
    config: GuitarConfig,
) -> tuple[GuitarControlMap | None, str | None]:
    actions = {
        stroke.control_action
        for stroke in pattern.strokes
        if stroke.control_action is not None and config.enable_controls
    }
    if not actions:
        return None, None
    if profile.control_map_id is None:
        return None, "Factory profile has no confirmed Guitar Mode control map"
    control_map = control_maps.get(profile.control_map_id)
    if control_map is None or not control_map.confirmed:
        return None, "Guitar Mode control map is missing or unconfirmed"
    if control_map.program != profile.program:
        return None, "Guitar Mode control map targets a different program"
    if control_map.source == "synthetic-test" and not config.allow_synthetic_control_map:
        return None, "Synthetic Guitar Mode map is forbidden outside explicit tests"
    missing = sorted(action for action in actions if action not in control_map.actions)
    if missing:
        return None, f"Guitar Mode map lacks confirmed actions: {missing}"
    return control_map, None


def _expand_pattern(
    pattern: FactoryStrumPattern,
    profile: FactoryGuitarProfile,
    control_map: GuitarControlMap | None,
    chords: Sequence[ChordCell],
    config: GuitarConfig,
) -> tuple[list[Note], list[GuitarNoteAudit], dict[str, int], int]:
    pairs: list[tuple[Note, GuitarNoteAudit]] = []
    counts = {direction: 0 for direction in sorted(_DIRECTIONS)}
    max_span = 0
    cycle_start = config.start_tick
    while cycle_start < config.end_tick:
        for stroke in pattern.strokes:
            stroke_tick = cycle_start + stroke.tick
            if stroke_tick >= config.end_tick:
                continue
            counts[stroke.direction] += 1
            if stroke.direction in _CONTROL_DIRECTIONS:
                if not config.enable_controls:
                    continue
                if control_map is None or stroke.control_action is None:
                    raise ValueError("Control stroke has no confirmed map")
                trigger = control_map.actions[stroke.control_action]
                end = min(config.end_tick, stroke_tick + trigger.duration_ticks)
                note = Note(
                    track=config.track_index,
                    channel=config.channel,
                    pitch=trigger.note,
                    start=stroke_tick,
                    end=end,
                    velocity=profile.velocity(config.intensity),
                    factory_profile_id=profile.profile_id,
                    gold_pattern_id=pattern.pattern_id,
                    element=f"control:{stroke.control_action}",
                )
                pairs.append(
                    (
                        note,
                        GuitarNoteAudit(
                            tick=stroke_tick,
                            pitch=trigger.note,
                            direction=stroke.direction,
                            string=None,
                            fret=None,
                            offset=0,
                            control_action=stroke.control_action,
                        ),
                    )
                )
                continue
            chord = _chord_at(chords, stroke_tick)
            if chord is None:
                raise ValueError(f"No chord at tick {stroke_tick}")
            voicing = _map_voicing(stroke, chord, profile)
            frets = [fret for _, _, fret, _ in voicing]
            span = max(frets) - min(frets)
            max_span = max(max_span, span)
            if span > profile.max_fret_span:
                raise ValueError(
                    f"Fret span {span} exceeds Factory limit {profile.max_fret_span}"
                )
            previous_pitch = -1
            for string, pitch, fret, offset in sorted(voicing):
                if pitch <= previous_pitch:
                    raise ValueError("Voicing crosses or duplicates adjacent strings")
                previous_pitch = pitch
                start = stroke_tick + offset
                if start >= config.end_tick:
                    continue
                end = min(config.end_tick, start + stroke.gate_ticks)
                note = Note(
                    track=config.track_index,
                    channel=config.channel,
                    pitch=pitch,
                    start=start,
                    end=end,
                    velocity=profile.velocity(config.intensity),
                    factory_profile_id=profile.profile_id,
                    gold_pattern_id=pattern.pattern_id,
                    element=f"{stroke.direction}:string={string}:fret={fret}",
                )
                pairs.append(
                    (
                        note,
                        GuitarNoteAudit(
                            tick=start,
                            pitch=pitch,
                            direction=stroke.direction,
                            string=string,
                            fret=fret,
                            offset=offset,
                            control_action=None,
                        ),
                    )
                )
        cycle_start += pattern.length_ticks
    pairs = _dedupe_and_shorten(pairs)
    return [note for note, _ in pairs], [audit for _, audit in pairs], counts, max_span


def _map_voicing(
    stroke: FactoryStrumStroke,
    chord: ChordCell,
    profile: FactoryGuitarProfile,
) -> list[tuple[int, int, int, int]]:
    intervals = _CHORD_INTERVALS[chord.quality]
    voicing = []
    for string, tone, offset in zip(stroke.strings, stroke.chord_tones, stroke.offsets):
        pitch_class = (chord.root_pc + intervals[tone]) % 12
        open_pitch = profile.tuning[string]
        candidates = [
            pitch
            for pitch in range(open_pitch + profile.fret_min, open_pitch + profile.fret_max + 1)
            if pitch % 12 == pitch_class
            and profile.register_min <= pitch <= profile.register_max
        ]
        if not candidates:
            raise ValueError(
                f"String {string} cannot play chord tone {tone} in the confirmed fret range"
            )
        pitch = candidates[0]
        voicing.append((string, pitch, pitch - open_pitch, offset))
    return voicing


def _dedupe_and_shorten(
    pairs: Iterable[tuple[Note, GuitarNoteAudit]],
) -> list[tuple[Note, GuitarNoteAudit]]:
    unique: dict[tuple[int, int, int], tuple[Note, GuitarNoteAudit]] = {}
    for pair in sorted(pairs, key=lambda item: (item[0].start, item[0].pitch, item[0].end)):
        note = pair[0]
        unique.setdefault((note.track, note.start, note.pitch), pair)
    groups: dict[tuple[int, int, int], list[tuple[Note, GuitarNoteAudit]]] = {}
    for pair in unique.values():
        note = pair[0]
        groups.setdefault((note.track, note.channel, note.pitch), []).append(pair)
    output: list[tuple[Note, GuitarNoteAudit]] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda item: (item[0].start, item[0].end))
        for index, (note, audit) in enumerate(ordered):
            if index + 1 < len(ordered) and note.end > ordered[index + 1][0].start:
                note = replace(note, end=ordered[index + 1][0].start)
            if note.end > note.start:
                output.append((note, audit))
    return sorted(output, key=lambda item: (item[0].start, item[0].pitch, item[0].end))


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


def _empty_plan(
    input_hash: str,
    config: GuitarConfig,
    decision: str,
    reason: str,
    *,
    pattern_id: str | None = None,
    control_map_id: str | None = None,
    candidate_ids: tuple[str, ...] = (),
    source_ids: tuple[str, ...] = (),
) -> GuitarPlan:
    return GuitarPlan(
        decision=decision,
        reason=reason,
        input_hash=input_hash,
        selection_hash=sha256(
            f"{input_hash}|{config.seed}|{decision}|{reason}".encode("utf-8")
        ).hexdigest(),
        config=config,
        pattern_id=pattern_id,
        control_map_id=control_map_id,
        candidate_ids=candidate_ids,
        generated_notes=(),
        note_audit=(),
        removed_notes=0,
        stroke_counts={},
        max_fret_span=0,
        source_ids=source_ids,
    )