"""Session 2: deterministic, section-aware drum/percussion reconstruction."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .midi import MidiFile, MidiFormatError, Note


DRUM_CHANNEL = 9
PERCUSSION_CHANNEL = 10
_STABLE_ID = re.compile(r"^\d{3}\.\d{3}\.\d{3}$")
_FORBIDDEN_GOLD_KEYS = {
    "velocity",
    "velocities",
    "bank",
    "bankmsb",
    "banklsb",
    "program",
    "programchange",
    "instrumentkey",
    "factoryprofile",
    "factoryprofileid",
}
_ALLOWED_ELEMENTS = {
    "kick",
    "snare",
    "hat",
    "cymbal",
    "tom",
    "ghost",
    "fill",
    "percussion",
}
_DEFAULT_BUDGETS = {
    "kick": 8,
    "snare": 8,
    "hat": 16,
    "cymbal": 4,
    "tom": 8,
    "ghost": 8,
    "fill": 12,
    "percussion": 16,
}


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def assert_gold_has_no_dynamic_authority(value: Any, path: str = "gold") -> None:
    """Recursively reject all fields that could let GOLD control sound/dynamics."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalized_key(str(key))
            if normalized in _FORBIDDEN_GOLD_KEYS or "velocity" in normalized:
                raise ValueError(f"Forbidden GOLD field at {path}.{key}")
            assert_gold_has_no_dynamic_authority(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_gold_has_no_dynamic_authority(child, f"{path}[{index}]")


@dataclass(frozen=True)
class FactoryVelocityProfile:
    profile_id: str
    note: int
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
        if not 0 <= self.note <= 127:
            raise ValueError(f"Invalid Factory drum note: {self.note}")
        points = self.points
        if any(not 1 <= value <= 127 for value in points):
            raise ValueError("Factory velocity points must be in range 1..127")
        if points != tuple(sorted(points)):
            raise ValueError("Factory velocity curve must be monotonic")

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
        position = intensity * 6 / 100
        lower = min(int(position), 5)
        fraction = position - lower
        if intensity == 100:
            return self.ceiling
        value = self.points[lower] + fraction * (
            self.points[lower + 1] - self.points[lower]
        )
        return max(self.floor, min(self.ceiling, int(round(value))))


@dataclass(frozen=True)
class GoldPatternEvent:
    tick: int
    duration: int
    note: int
    element: str

    def __post_init__(self) -> None:
        if self.tick < 0 or self.duration <= 0:
            raise ValueError("GOLD event tick/duration is invalid")
        if not 0 <= self.note <= 127:
            raise ValueError(f"Invalid GOLD note: {self.note}")
        if self.element not in _ALLOWED_ELEMENTS:
            raise ValueError(f"Unsupported drum element: {self.element}")


@dataclass(frozen=True)
class GoldPattern:
    pattern_id: str
    role: str
    section: str
    meter: str
    length_ticks: int
    events: tuple[GoldPatternEvent, ...]
    evidence_quality: float = 1.0
    transition_group: str = "neutral"

    def __post_init__(self) -> None:
        if not _STABLE_ID.fullmatch(self.pattern_id):
            raise ValueError(f"Invalid GOLD pattern ID: {self.pattern_id}")
        if self.role not in {"drums", "percussion"}:
            raise ValueError(f"Unsupported GOLD role: {self.role}")
        if self.length_ticks <= 0:
            raise ValueError("GOLD pattern length must be positive")
        if not 0 <= self.evidence_quality <= 1:
            raise ValueError("Evidence quality must be in range 0..1")
        if not self.events:
            raise ValueError("GOLD pattern must contain at least one event")
        if any(event.tick >= self.length_ticks for event in self.events):
            raise ValueError("GOLD event lies outside its pattern")
        for event in self.events:
            _validate_element_note(self.role, event.element, event.note)

    @property
    def density(self) -> float:
        return len(self.events) / self.length_ticks


@dataclass(frozen=True)
class ReconstructionConfig:
    track_index: int
    channel: int
    role: str
    section: str
    start_tick: int
    end_tick: int
    seed: int
    intensity: int
    meter: str = "4/4"
    desired_notes_per_quarter: float = 4.0
    expected_program: int | None = None
    element_budgets: Mapping[str, int] = field(default_factory=lambda: dict(_DEFAULT_BUDGETS))

    def __post_init__(self) -> None:
        expected_channel = DRUM_CHANNEL if self.role == "drums" else PERCUSSION_CHANNEL
        if self.role not in {"drums", "percussion"}:
            raise ValueError(f"Unsupported reconstruction role: {self.role}")
        if self.channel != expected_channel:
            raise ValueError(
                f"Role {self.role} must use MIDI channel {expected_channel + 1}"
            )
        if self.start_tick < 0 or self.end_tick <= self.start_tick:
            raise ValueError("Invalid reconstruction window")
        if not 0 <= self.intensity <= 100:
            raise ValueError("Intensity must be in range 0..100")
        if self.desired_notes_per_quarter <= 0:
            raise ValueError("Desired density must be positive")
        if self.expected_program is not None and not 0 <= self.expected_program <= 127:
            raise ValueError("Expected program must be in range 0..127")
        unknown = set(self.element_budgets) - _ALLOWED_ELEMENTS
        if unknown or any(value < 0 for value in self.element_budgets.values()):
            raise ValueError(f"Invalid element budget: {sorted(unknown)}")


@dataclass(frozen=True)
class ReconstructionPlan:
    decision: str
    reason: str
    input_hash: str
    selection_hash: str
    config: ReconstructionConfig
    pattern_id: str | None
    candidate_ids: tuple[str, ...]
    generated_notes: tuple[Note, ...]
    removed_notes: int
    rejected_by_budget: int
    continuity_score: float
    factory_profile_ids: tuple[str, ...]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "schema": "dna-session2-reconstruction-plan",
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
            "patternId": self.pattern_id,
            "candidateIds": list(self.candidate_ids),
            "removedNotes": self.removed_notes,
            "generatedNotes": len(self.generated_notes),
            "rejectedByBudget": self.rejected_by_budget,
            "continuityScore": self.continuity_score,
            "factoryProfileIds": list(self.factory_profile_ids),
            "goldAffectsVelocity": False,
            "goldAffectsProgramChange": False,
            "kitProgramExpected": self.config.expected_program,
            "notes": [
                {
                    "tick": note.start,
                    "duration": note.end - note.start,
                    "note": note.pitch,
                    "velocity": note.velocity,
                    "element": note.element,
                    "goldPatternId": note.gold_pattern_id,
                    "factoryProfileId": note.factory_profile_id,
                }
                for note in self.generated_notes
            ],
        }


@dataclass(frozen=True)
class ReconstructionResult:
    midi: MidiFile
    manifest: dict[str, Any]


def load_registry(
    path: str | Path,
) -> tuple[list[GoldPattern], dict[int, FactoryVelocityProfile]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    gold_raw = raw.get("goldPatterns", [])
    assert_gold_has_no_dynamic_authority(gold_raw)
    patterns = []
    for pattern in gold_raw:
        patterns.append(
            GoldPattern(
                pattern_id=pattern["id"],
                role=pattern["role"],
                section=pattern["section"],
                meter=pattern.get("meter", "4/4"),
                length_ticks=int(pattern["lengthTicks"]),
                evidence_quality=float(pattern.get("evidenceQuality", 1.0)),
                transition_group=pattern.get("transitionGroup", "neutral"),
                events=tuple(
                    GoldPatternEvent(
                        tick=int(event["tick"]),
                        duration=int(event["duration"]),
                        note=int(event["note"]),
                        element=event["element"],
                    )
                    for event in pattern["events"]
                ),
            )
        )
    profiles: dict[int, FactoryVelocityProfile] = {}
    for profile in raw.get("factoryProfiles", []):
        item = FactoryVelocityProfile(
            profile_id=profile["id"],
            note=int(profile["note"]),
            floor=int(profile["floor"]),
            soft=int(profile["soft"]),
            low_mid=int(profile["lowMid"]),
            optimal=int(profile["optimal"]),
            high_mid=int(profile["highMid"]),
            strong=int(profile["strong"]),
            ceiling=int(profile["ceiling"]),
        )
        if item.note in profiles:
            raise ValueError(f"Duplicate Factory profile for note {item.note}")
        profiles[item.note] = item
    return patterns, profiles


def plan_reconstruction(
    midi: MidiFile,
    patterns: Iterable[GoldPattern],
    profiles: Mapping[int, FactoryVelocityProfile],
    config: ReconstructionConfig,
) -> ReconstructionPlan:
    input_hash = midi.digest()
    current_program = midi.program_at(config.channel, config.start_tick, config.track_index)
    if config.expected_program is not None and current_program != config.expected_program:
        return _empty_plan(
            config,
            input_hash,
            "MANUAL_REVIEW",
            f"Kit program mismatch: expected {config.expected_program}, found {current_program}",
        )

    all_notes = midi.notes()
    target_notes = [
        note
        for note in all_notes
        if note.track == config.track_index
        and note.channel == config.channel
        and config.start_tick <= note.start < config.end_tick
    ]
    candidates = [
        pattern
        for pattern in patterns
        if pattern.role == config.role
        and pattern.meter == config.meter
        and (pattern.section == config.section or pattern.section == "generic")
    ]
    if not candidates:
        return _empty_plan(
            config,
            input_hash,
            "KEEP",
            "No compatible GOLD pattern; original notes are preserved",
            removed_notes=len(target_notes),
        )

    scored = [
        (
            _pattern_score(midi, pattern, config),
            pattern.pattern_id,
            pattern,
        )
        for pattern in candidates
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    best_score = scored[0][0]
    best = [item[2] for item in scored if best_score - item[0] <= 0.25]
    candidate_ids = tuple(pattern.pattern_id for pattern in best)
    chooser = sha256(
        f"{config.seed}|{input_hash}|{'|'.join(candidate_ids)}".encode("utf-8")
    ).digest()
    selected = best[int.from_bytes(chooser[:8], "big") % len(best)]

    missing_profiles = sorted({event.note for event in selected.events} - set(profiles))
    if missing_profiles:
        return _empty_plan(
            config,
            input_hash,
            "MANUAL_REVIEW",
            f"Missing Factory velocity profiles for notes: {missing_profiles}",
            pattern_id=selected.pattern_id,
            candidate_ids=candidate_ids,
            removed_notes=len(target_notes),
        )

    generated, rejected = _expand_pattern(selected, profiles, midi.ppq, config)
    if not generated:
        return _empty_plan(
            config,
            input_hash,
            "KEEP",
            "Element budgets rejected every candidate note",
            pattern_id=selected.pattern_id,
            candidate_ids=candidate_ids,
            removed_notes=len(target_notes),
            rejected_by_budget=rejected,
        )
    continuity = _continuity_score(midi, selected, config)
    factory_ids = tuple(sorted({note.factory_profile_id or "" for note in generated}))
    selection_payload = {
        "input": input_hash,
        "seed": config.seed,
        "pattern": selected.pattern_id,
        "profiles": factory_ids,
        "window": [config.start_tick, config.end_tick],
        "notes": [
            [note.start, note.end, note.pitch, note.velocity, note.element]
            for note in generated
        ],
    }
    selection_hash = sha256(
        json.dumps(selection_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ReconstructionPlan(
        decision="REPLACE",
        reason="Compatible section-aware GOLD pattern with complete Factory dynamics",
        input_hash=input_hash,
        selection_hash=selection_hash,
        config=config,
        pattern_id=selected.pattern_id,
        candidate_ids=candidate_ids,
        generated_notes=tuple(generated),
        removed_notes=len(target_notes),
        rejected_by_budget=rejected,
        continuity_score=continuity,
        factory_profile_ids=factory_ids,
    )


def apply_reconstruction(midi: MidiFile, plan: ReconstructionPlan) -> ReconstructionResult:
    if midi.digest() != plan.input_hash:
        raise ValueError("Input MIDI changed after the reconstruction plan was created")
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
        return ReconstructionResult(midi=midi, manifest=manifest)

    before_program = midi.program_at(plan.config.channel, plan.config.start_tick, plan.config.track_index)
    output = midi.replace_notes(
        track_index=plan.config.track_index,
        channel=plan.config.channel,
        start_tick=plan.config.start_tick,
        end_tick=plan.config.end_tick,
        new_notes=plan.generated_notes,
    )
    output.notes()
    after_program = output.program_at(plan.config.channel, plan.config.start_tick, plan.config.track_index)
    if before_program != after_program:
        raise MidiFormatError("Reconstruction changed the kit Program Change")
    manifest = plan.to_manifest()
    manifest.update(
        {
            "applied": True,
            "outputHash": output.digest(),
            "programChangePreserved": True,
            "notePairingValid": True,
        }
    )
    return ReconstructionResult(midi=output, manifest=manifest)


def _empty_plan(
    config: ReconstructionConfig,
    input_hash: str,
    decision: str,
    reason: str,
    *,
    pattern_id: str | None = None,
    candidate_ids: tuple[str, ...] = (),
    removed_notes: int = 0,
    rejected_by_budget: int = 0,
) -> ReconstructionPlan:
    return ReconstructionPlan(
        decision=decision,
        reason=reason,
        input_hash=input_hash,
        selection_hash=sha256(
            f"{input_hash}|{config.seed}|{decision}|{reason}".encode("utf-8")
        ).hexdigest(),
        config=config,
        pattern_id=pattern_id,
        candidate_ids=candidate_ids,
        generated_notes=(),
        removed_notes=removed_notes,
        rejected_by_budget=rejected_by_budget,
        continuity_score=0.0,
        factory_profile_ids=(),
    )


def _pattern_score(
    midi: MidiFile, pattern: GoldPattern, config: ReconstructionConfig
) -> float:
    exact_section = 10.0 if pattern.section == config.section else 5.0
    density_per_quarter = len(pattern.events) * midi.ppq / pattern.length_ticks
    density_score = max(
        0.0, 5.0 - abs(density_per_quarter - config.desired_notes_per_quarter)
    )
    return (
        exact_section
        + density_score
        + pattern.evidence_quality * 3.0
        + _continuity_score(midi, pattern, config)
    )


def _continuity_score(
    midi: MidiFile, pattern: GoldPattern, config: ReconstructionConfig
) -> float:
    lookback = max(0, config.start_tick - midi.ppq)
    preceding = {
        classify_drum_note(note.pitch)
        for note in midi.notes()
        if note.track == config.track_index
        and note.channel == config.channel
        and lookback <= note.start < config.start_tick
    }
    opening = {
        event.element
        for event in pattern.events
        if event.tick < min(pattern.length_ticks, midi.ppq)
    }
    if not preceding or not opening:
        return 0.0
    comparable_opening = {
        classify_drum_note(event.note)
        for event in pattern.events
        if event.tick < min(pattern.length_ticks, midi.ppq)
    }
    return len(preceding & comparable_opening) / len(preceding | comparable_opening)


def _expand_pattern(
    pattern: GoldPattern,
    profiles: Mapping[int, FactoryVelocityProfile],
    ppq: int,
    config: ReconstructionConfig,
) -> tuple[list[Note], int]:
    numerator, denominator = _parse_meter(config.meter)
    measure_ticks = int(ppq * 4 * numerator / denominator)
    budget_counts: dict[tuple[int, str], int] = {}
    generated: list[Note] = []
    rejected = 0
    cycle_start = config.start_tick
    while cycle_start < config.end_tick:
        for event in pattern.events:
            start = cycle_start + event.tick
            if start >= config.end_tick:
                continue
            measure = (start - config.start_tick) // measure_ticks
            key = (measure, event.element)
            limit = config.element_budgets.get(event.element, 0)
            if budget_counts.get(key, 0) >= limit:
                rejected += 1
                continue
            end = min(config.end_tick, start + event.duration)
            if end <= start:
                rejected += 1
                continue
            profile = profiles[event.note]
            generated.append(
                Note(
                    track=config.track_index,
                    channel=config.channel,
                    pitch=event.note,
                    start=start,
                    end=end,
                    velocity=profile.velocity(config.intensity),
                    factory_profile_id=profile.profile_id,
                    gold_pattern_id=pattern.pattern_id,
                    element=event.element,
                )
            )
            budget_counts[key] = budget_counts.get(key, 0) + 1
        cycle_start += pattern.length_ticks
    return _remove_duplicate_and_overlapping_notes(generated), rejected


def _remove_duplicate_and_overlapping_notes(notes: Iterable[Note]) -> list[Note]:
    unique: dict[tuple[int, int, int], Note] = {}
    for note in sorted(notes, key=lambda item: (item.start, item.pitch, item.end)):
        unique.setdefault((note.track, note.start, note.pitch), note)
    by_pitch: dict[tuple[int, int, int], list[Note]] = {}
    for note in unique.values():
        by_pitch.setdefault((note.track, note.channel, note.pitch), []).append(note)
    output: list[Note] = []
    for group in by_pitch.values():
        ordered = sorted(group, key=lambda item: (item.start, item.end))
        for index, note in enumerate(ordered):
            if index + 1 < len(ordered) and note.end > ordered[index + 1].start:
                note = replace(note, end=ordered[index + 1].start)
            if note.end > note.start:
                output.append(note)
    return sorted(output, key=lambda item: (item.start, item.pitch, item.end))


def _parse_meter(meter: str) -> tuple[int, int]:
    try:
        numerator_text, denominator_text = meter.split("/", 1)
        numerator = int(numerator_text)
        denominator = int(denominator_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid meter: {meter}") from exc
    if numerator <= 0 or denominator <= 0:
        raise ValueError(f"Invalid meter: {meter}")
    return numerator, denominator


def classify_drum_note(note: int) -> str:
    if note in {35, 36}:
        return "kick"
    if note in {37, 38, 39, 40}:
        return "snare"
    if note in {42, 44, 46}:
        return "hat"
    if note in {41, 43, 45, 47, 48, 50}:
        return "tom"
    if note in {49, 51, 52, 53, 55, 57, 59}:
        return "cymbal"
    return "percussion"


def _validate_element_note(role: str, element: str, note: int) -> None:
    classified = classify_drum_note(note)
    if role == "percussion":
        if element not in {"percussion", "ghost", "cymbal"}:
            raise ValueError(f"Percussion pattern cannot declare {element}")
        return
    if element == "ghost" and classified == "snare":
        return
    if element == "fill" and classified in {"kick", "snare", "tom", "cymbal"}:
        return
    if element != classified:
        raise ValueError(
            f"Element/note mismatch: note {note} is {classified}, declared {element}"
        )