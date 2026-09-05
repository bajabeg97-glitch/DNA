"""Session 5 protected solo, ornaments, thirds, echo and CC11 expression."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .drum_reconstruction import assert_gold_has_no_dynamic_authority
from .harmonic_reconstruction import ChordCell
from .midi import MidiEvent, MidiFile, MidiFormatError, MidiTrack, Note
from .track_identity import (
    DelayAllocation,
    SoloFingerprint,
    SoundBinding,
    TrackIdentity,
    allocate_delay_track,
    channel_track_indices,
    fingerprint_solo,
    identity_for_track,
    mapping_warning,
    sound_bindings,
    verify_solo_fingerprint,
)


_STABLE_ID = re.compile(r"^\d{3}\.\d{3}\.\d{3}$")
_ORNAMENTS = {"trill", "grace", "slide"}
_RELATIONSHIPS = {"third", "echo"}
_SCALES = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
}


@dataclass(frozen=True)
class FactorySoloProfile:
    profile_id: str
    bank_msb: int
    bank_lsb: int
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
    expression_min: int
    expression_max: int
    expression_max_step: int
    echo_intensity_drop: int
    third_intensity_drop: int

    def __post_init__(self) -> None:
        if not _STABLE_ID.fullmatch(self.profile_id):
            raise ValueError(f"Invalid Factory solo profile ID: {self.profile_id}")
        if any(not 0 <= value <= 127 for value in self.sound):
            raise ValueError("Solo sound requires exact Bank MSB/LSB/Program")
        if not 0 <= self.register_min <= self.register_max <= 127:
            raise ValueError("Invalid solo register")
        if self.points != tuple(sorted(self.points)):
            raise ValueError("Factory solo velocity curve must be monotonic")
        if any(not 1 <= value <= 127 for value in self.points):
            raise ValueError("Factory velocity points must be in range 1..127")
        if not 0 <= self.expression_min <= self.expression_max <= 127:
            raise ValueError("Invalid Factory CC11 range")
        if self.expression_max_step <= 0:
            raise ValueError("CC11 smoothing step must be positive")
        if self.echo_intensity_drop < 0 or self.third_intensity_drop < 0:
            raise ValueError("Factory layer intensity drops cannot be negative")

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
class GoldOrnamentEvidence:
    evidence_id: str
    kind: str
    intervals: tuple[int, ...]
    allowed_qualities: tuple[str, ...]
    min_gap_ticks: int
    note_duration_ticks: int
    confidence: float

    def __post_init__(self) -> None:
        if not _STABLE_ID.fullmatch(self.evidence_id):
            raise ValueError(f"Invalid GOLD ornament ID: {self.evidence_id}")
        if self.kind not in _ORNAMENTS:
            raise ValueError(f"Unsupported ornament kind: {self.kind}")
        if not self.intervals or any(interval < -12 or interval > 12 for interval in self.intervals):
            raise ValueError("Ornament intervals must be relative semitones")
        if any(quality not in _SCALES for quality in self.allowed_qualities):
            raise ValueError("Unsupported ornament chord quality")
        if self.min_gap_ticks <= 0 or self.note_duration_ticks <= 0:
            raise ValueError("Ornament timing must be positive")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Ornament confidence must be in range 0..1")


@dataclass(frozen=True)
class GoldSoloRelationship:
    relationship_id: str
    kind: str
    allowed_qualities: tuple[str, ...]
    accepted_intervals: tuple[int, ...] = ()
    delay_ticks: int = 0
    duration_ratio: float = 1.0
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not _STABLE_ID.fullmatch(self.relationship_id):
            raise ValueError(f"Invalid GOLD relationship ID: {self.relationship_id}")
        if self.kind not in _RELATIONSHIPS:
            raise ValueError(f"Unsupported solo relationship: {self.kind}")
        if any(quality not in _SCALES for quality in self.allowed_qualities):
            raise ValueError("Unsupported relationship chord quality")
        if self.kind == "third" and not self.accepted_intervals:
            raise ValueError("Third relationship requires accepted intervals")
        if self.kind == "echo" and (self.delay_ticks <= 0 or not 0 < self.duration_ratio <= 1):
            raise ValueError("Echo relationship requires a positive delay and duration ratio")
        if not 0 <= self.confidence <= 1:
            raise ValueError("Relationship confidence must be in range 0..1")


@dataclass(frozen=True)
class SoloConfig:
    track_index: int
    channel: int
    start_tick: int
    end_tick: int
    seed: int
    intensity: int
    profile_id: str
    ornaments: tuple[str, ...] = ("trill", "grace", "slide")
    enable_third: bool = True
    enable_echo: bool = True
    enable_expression: bool = True
    max_generated_notes: int = 64
    min_chord_confidence: float = 0.8
    track_uid: str | None = None
    allow_shared_channel: bool = False

    def __post_init__(self) -> None:
        if self.track_index < 0:
            raise ValueError("Invalid solo track index")
        if not 0 <= self.channel <= 15:
            raise ValueError("Invalid solo channel")
        if self.start_tick < 0 or self.end_tick <= self.start_tick:
            raise ValueError("Invalid solo window")
        if not 0 <= self.intensity <= 100:
            raise ValueError("Intensity must be in range 0..100")
        if not _STABLE_ID.fullmatch(self.profile_id):
            raise ValueError(f"Invalid Factory solo profile ID: {self.profile_id}")
        ornament_tuple = tuple(self.ornaments)
        if any(kind not in _ORNAMENTS for kind in ornament_tuple):
            raise ValueError("Unsupported requested ornament")
        object.__setattr__(self, "ornaments", ornament_tuple)
        if self.max_generated_notes < 0:
            raise ValueError("Transformation budget cannot be negative")
        if not 0 <= self.min_chord_confidence <= 1:
            raise ValueError("Chord-confidence threshold must be in range 0..1")
        if self.track_uid is not None and not self.track_uid.startswith("trk-"):
            raise ValueError("trackUid must be a stable track identity")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SoloConfig":
        """Accept explicit external 1-based numbers or internal 0-based fields."""
        values = dict(raw)
        if "trackUid" in values:
            if "track_uid" in values:
                raise ValueError("Use either trackUid or track_uid, not both")
            values["track_uid"] = str(values.pop("trackUid"))
        if "allowSharedChannel" in values:
            if "allow_shared_channel" in values:
                raise ValueError(
                    "Use either allowSharedChannel or allow_shared_channel, not both"
                )
            values["allow_shared_channel"] = bool(values.pop("allowSharedChannel"))
        if "trackNumber" in values:
            if "track_index" in values:
                raise ValueError("Use either trackNumber or track_index, not both")
            number = int(values.pop("trackNumber"))
            if not 1 <= number <= 16:
                raise ValueError("trackNumber must be in range 1..16")
            values["track_index"] = number - 1
        if "channelNumber" in values:
            if "channel" in values:
                raise ValueError("Use either channelNumber or channel, not both")
            number = int(values.pop("channelNumber"))
            if not 1 <= number <= 16:
                raise ValueError("channelNumber must be in range 1..16")
            values["channel"] = number - 1
        return cls(**values)


@dataclass(frozen=True)
class SoloNoteAudit:
    kind: str
    evidence_id: str
    source_note_index: int
    interval: int


@dataclass(frozen=True)
class ExpressionPoint:
    tick: int
    value: int
    reason: str


@dataclass(frozen=True)
class SoloPlan:
    decision: str
    reason: str
    input_hash: str
    structural_hash: str
    selection_hash: str
    config: SoloConfig
    generated_notes: tuple[Note, ...]
    note_audit: tuple[SoloNoteAudit, ...]
    expression_events: tuple[MidiEvent, ...]
    expression_points: tuple[ExpressionPoint, ...]
    original_fingerprint: tuple[tuple[int, int, int, int], ...]
    counts: Mapping[str, int]
    skipped: Mapping[str, int]
    evidence_ids: tuple[str, ...]
    manual_expression_preserved: bool
    echo_track_index: int | None
    source_identity: TrackIdentity | None = None
    sound_binding_segments: tuple[SoundBinding, ...] = ()
    protected_fingerprint: SoloFingerprint | None = None
    delay_allocation: DelayAllocation | None = None

    def to_manifest(self) -> dict[str, Any]:
        manifest = {
            "schema": "dna-session5-solo-plan",
            "version": "1.0",
            "decision": self.decision,
            "reason": self.reason,
            "inputHash": self.input_hash,
            "structuralHash": self.structural_hash,
            "selectionHash": self.selection_hash,
            "seed": self.config.seed,
            "sourceTrackIndex": self.config.track_index,
            "sourceTrackNumber": self.config.track_index + 1,
            "channelIndex": self.config.channel,
            "channelNumber": self.config.channel + 1,
            "channel": self.config.channel + 1,
            "window": [self.config.start_tick, self.config.end_tick],
            "factoryProfileId": self.config.profile_id,
            "generatedNotes": len(self.generated_notes),
            "counts": dict(self.counts),
            "skipped": dict(self.skipped),
            "evidenceIds": list(self.evidence_ids),
            "manualExpressionPreserved": self.manual_expression_preserved,
            "delayTrackIndex": self.echo_track_index,
            "delayTrackNumber": self.echo_track_index + 1
            if self.echo_track_index is not None
            else None,
            "delayTrackUid": self.delay_allocation.target_track_uid
            if self.delay_allocation is not None
            else None,
            "delayOnSourceTrack": False,
            "originalSoloTimingMutable": False,
            "analysisVelocityUsed": False,
            "goldAffectsVelocity": False,
            "echoRecursive": False,
            "notes": [
                {
                    "tick": note.start,
                    "duration": note.end - note.start,
                    "note": note.pitch,
                    "velocity": note.velocity,
                    "kind": audit.kind,
                    "interval": audit.interval,
                    "sourceNoteIndex": audit.source_note_index,
                    "evidenceId": audit.evidence_id,
                    "factoryProfileId": note.factory_profile_id,
                    "trackIndex": note.track,
                    "trackNumber": note.track + 1,
                }
                for note, audit in zip(self.generated_notes, self.note_audit)
            ],
            "expression": [
                {"tick": point.tick, "cc11": point.value, "reason": point.reason}
                for point in self.expression_points
            ],
        }
        if self.source_identity is not None:
            manifest["trackIdentity"] = self.source_identity.to_manifest()
            manifest["soundBindings"] = [
                binding.to_manifest() for binding in self.sound_binding_segments
            ]
            manifest["originalSoloFingerprint"] = (
                self.protected_fingerprint.to_manifest()
                if self.protected_fingerprint is not None
                else None
            )
            manifest["delayAllocation"] = (
                self.delay_allocation.to_manifest()
                if self.delay_allocation is not None
                else None
            )
            manifest["mappingWarning"] = mapping_warning(
                source=self.source_identity,
                channel=self.config.channel,
                bindings=self.sound_binding_segments,
                allocation=self.delay_allocation,
            )
            manifest["sharedChannelApproval"] = self.config.allow_shared_channel
        return manifest


@dataclass(frozen=True)
class SoloResult:
    midi: MidiFile
    manifest: dict[str, Any]


def load_solo_registry(
    path: str | Path,
) -> tuple[
    list[GoldOrnamentEvidence],
    list[GoldSoloRelationship],
    dict[str, FactorySoloProfile],
]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    gold_raw = {
        "ornaments": raw.get("goldOrnaments", []),
        "relationships": raw.get("goldRelationships", []),
    }
    assert_gold_has_no_dynamic_authority(gold_raw)
    _assert_no_absolute_gold_pitch(gold_raw)
    ornaments = [
        GoldOrnamentEvidence(
            evidence_id=item["id"],
            kind=item["kind"],
            intervals=tuple(int(value) for value in item["intervals"]),
            allowed_qualities=tuple(item["allowedQualities"]),
            min_gap_ticks=int(item["minGapTicks"]),
            note_duration_ticks=int(item["noteDurationTicks"]),
            confidence=float(item["confidence"]),
        )
        for item in raw.get("goldOrnaments", [])
    ]
    relationships = [
        GoldSoloRelationship(
            relationship_id=item["id"],
            kind=item["kind"],
            allowed_qualities=tuple(item["allowedQualities"]),
            accepted_intervals=tuple(int(value) for value in item.get("acceptedIntervals", [])),
            delay_ticks=int(item.get("delayTicks", 0)),
            duration_ratio=float(item.get("durationRatio", 1.0)),
            confidence=float(item["confidence"]),
        )
        for item in raw.get("goldRelationships", [])
    ]
    profiles: dict[str, FactorySoloProfile] = {}
    for item in raw.get("factoryProfiles", []):
        profile = FactorySoloProfile(
            profile_id=item["id"],
            bank_msb=int(item["bankMsb"]),
            bank_lsb=int(item["bankLsb"]),
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
            expression_min=int(item["expressionMin"]),
            expression_max=int(item["expressionMax"]),
            expression_max_step=int(item["expressionMaxStep"]),
            echo_intensity_drop=int(item["echoIntensityDrop"]),
            third_intensity_drop=int(item["thirdIntensityDrop"]),
        )
        if profile.profile_id in profiles:
            raise ValueError(f"Duplicate Factory solo profile {profile.profile_id}")
        profiles[profile.profile_id] = profile
    return ornaments, relationships, profiles


def plan_solo_enhancement(
    midi: MidiFile,
    ornaments: Iterable[GoldOrnamentEvidence],
    relationships: Iterable[GoldSoloRelationship],
    profiles: Mapping[str, FactorySoloProfile],
    chords: Sequence[ChordCell],
    config: SoloConfig,
) -> SoloPlan:
    input_hash = midi.digest()
    if config.track_index >= len(midi.tracks):
        return _empty_plan(
            input_hash, sha256(b"invalid-track").hexdigest(), (), config,
            "MANUAL_REVIEW", "Selected solo track does not exist",
        )
    source_identity = identity_for_track(midi, config.track_index)
    if config.track_uid is not None and config.track_uid != source_identity.track_uid:
        return _empty_plan(
            input_hash,
            sha256(b"track-uid-mismatch").hexdigest(),
            (),
            config,
            "MANUAL_REVIEW",
            "Selected trackUid does not match the physical solo track",
            source_identity=source_identity,
        )
    protected_fingerprint = fingerprint_solo(
        midi,
        track_index=config.track_index,
        channel=config.channel,
        start_tick=config.start_tick,
        end_tick=config.end_tick,
        track_uid=source_identity.track_uid,
    )
    originals = [
        note
        for note in midi.notes()
        if note.track == config.track_index
        and note.channel == config.channel
        and config.start_tick <= note.start < config.end_tick
    ]
    originals.sort(key=lambda note: (note.start, note.pitch, note.end))
    fingerprint = protected_fingerprint.notes
    structural_hash = sha256(
        json.dumps(
            [(note.start, note.end, note.pitch) for note in originals],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    profile = profiles.get(config.profile_id)
    if profile is None:
        return _empty_plan(
            input_hash, structural_hash, fingerprint, config, "MANUAL_REVIEW",
            "Factory solo profile is missing",
            source_identity=source_identity,
            protected_fingerprint=protected_fingerprint,
        )
    binding_segments = sound_bindings(
        midi,
        track_index=config.track_index,
        channel=config.channel,
        start_tick=config.start_tick,
        end_tick=config.end_tick,
        track_uid=source_identity.track_uid,
    )
    if any(binding.sound != profile.sound for binding in binding_segments):
        return _empty_plan(
            input_hash,
            structural_hash,
            fingerprint,
            config,
            "MANUAL_REVIEW",
            "Time-scoped Bank Select and Program Change on the selected solo track do not match Factory profile",
            source_identity=source_identity,
            sound_binding_segments=binding_segments,
            protected_fingerprint=protected_fingerprint,
        )
    channel_tracks = set(channel_track_indices(midi, config.channel))
    if channel_tracks - {config.track_index} and not config.allow_shared_channel:
        return _empty_plan(
            input_hash, structural_hash, fingerprint, config, "MANUAL_REVIEW",
            "Solo channel is shared by multiple source tracks; mapping is ambiguous",
            source_identity=source_identity,
            sound_binding_segments=binding_segments,
            protected_fingerprint=protected_fingerprint,
        )
    if not originals:
        return _empty_plan(
            input_hash, structural_hash, fingerprint, config, "KEEP",
            "No solo notes in the requested window",
            source_identity=source_identity,
            sound_binding_segments=binding_segments,
            protected_fingerprint=protected_fingerprint,
        )

    chord_cells = sorted(chords, key=lambda item: (item.start_tick, item.end_tick))
    ornament_list = sorted(
        [item for item in ornaments if item.kind in config.ornaments],
        key=lambda item: (item.kind, item.evidence_id),
    )
    relationship_map = {item.kind: item for item in relationships}
    generated_pairs: list[tuple[Note, SoloNoteAudit]] = []
    counts = {"trill": 0, "grace": 0, "slide": 0, "third": 0, "echo": 0, "cc11": 0}
    skipped = {"insufficientGap": 0, "unconfirmedHarmony": 0, "unconfirmedEvidence": 0,
               "unconfirmedRelationship": 0, "register": 0, "overlap": 0}

    for index, (current, following) in enumerate(zip(originals, originals[1:])):
        gap = following.start - current.end
        chord = _chord_at(chord_cells, following.start)
        eligible = [
            evidence
            for evidence in ornament_list
            if chord is not None
            and chord.confidence >= config.min_chord_confidence
            and evidence.confidence >= config.min_chord_confidence
            and chord.quality in evidence.allowed_qualities
            and gap >= evidence.min_gap_ticks
            and len(evidence.intervals) * evidence.note_duration_ticks <= gap
        ]
        if not eligible:
            if chord is None or chord.confidence < config.min_chord_confidence:
                skipped["unconfirmedHarmony"] += 1
            else:
                skipped["insufficientGap"] += 1
            continue
        evidence = eligible[index % len(eligible)]
        ornament_pairs = _ornament_notes(
            evidence, current, following, index, profile, config
        )
        generated_pairs.extend(ornament_pairs)
        counts[evidence.kind] += len(ornament_pairs)

    third = relationship_map.get("third")
    if config.enable_third and third is not None and third.confidence >= config.min_chord_confidence:
        for index, note in enumerate(originals):
            chord = _chord_at(chord_cells, note.start)
            if chord is None or chord.confidence < config.min_chord_confidence or chord.quality not in third.allowed_qualities:
                skipped["unconfirmedHarmony"] += 1
                continue
            interval = _diatonic_third_interval(note.pitch, chord)
            target = note.pitch + interval if interval is not None else None
            if interval not in third.accepted_intervals or target is None:
                skipped["unconfirmedHarmony"] += 1
                continue
            if not profile.register_min <= target <= profile.register_max:
                skipped["register"] += 1
                continue
            generated_pairs.append(
                (
                    Note(
                        track=config.track_index,
                        channel=config.channel,
                        pitch=target,
                        start=note.start,
                        end=note.end,
                        velocity=profile.velocity(config.intensity - profile.third_intensity_drop),
                        factory_profile_id=profile.profile_id,
                        gold_pattern_id=third.relationship_id,
                        element="third",
                    ),
                    SoloNoteAudit("third", third.relationship_id, index, interval),
                )
            )
            counts["third"] += 1
    elif config.enable_third and third is not None:
        skipped["unconfirmedRelationship"] += len(originals)

    echo = relationship_map.get("echo")
    echo_track_index = None
    delay_allocation = None
    if config.enable_echo and echo is not None and echo.confidence >= config.min_chord_confidence:
        delay_allocation = allocate_delay_track(
            midi,
            source_track_index=config.track_index,
            channel=config.channel,
            allow_existing_shared_channel=config.allow_shared_channel,
        )
        echo_track_index = delay_allocation.target_track_index
        if not delay_allocation.allowed or echo_track_index is None:
            return _empty_plan(
                input_hash,
                structural_hash,
                fingerprint,
                config,
                "MANUAL_REVIEW",
                delay_allocation.reason,
                source_identity=source_identity,
                sound_binding_segments=binding_segments,
                protected_fingerprint=protected_fingerprint,
                delay_allocation=delay_allocation,
            )
        for index, note in enumerate(originals):
            chord = _chord_at(chord_cells, note.start)
            if chord is None or chord.confidence < config.min_chord_confidence or chord.quality not in echo.allowed_qualities:
                skipped["unconfirmedHarmony"] += 1
                continue
            start = note.start + echo.delay_ticks
            if not profile.register_min <= note.pitch <= profile.register_max:
                skipped["register"] += 1
                continue
            if start < note.end or start >= config.end_tick:
                skipped["overlap"] += 1
                continue
            duration = max(1, int(round((note.end - note.start) * echo.duration_ratio)))
            end = min(config.end_tick, start + duration)
            next_start = originals[index + 1].start if index + 1 < len(originals) else config.end_tick
            end = min(end, next_start)
            if end <= start:
                skipped["overlap"] += 1
                continue
            generated_pairs.append(
                (
                    Note(
                        track=echo_track_index,
                        channel=config.channel,
                        pitch=note.pitch,
                        start=start,
                        end=end,
                        velocity=profile.velocity(config.intensity - profile.echo_intensity_drop),
                        factory_profile_id=profile.profile_id,
                        gold_pattern_id=echo.relationship_id,
                        element="echo",
                    ),
                    SoloNoteAudit("echo", echo.relationship_id, index, 0),
                )
            )
            counts["echo"] += 1
    elif config.enable_echo and echo is not None:
        skipped["unconfirmedRelationship"] += len(originals)

    generated_pairs = _remove_unsafe_overlaps(generated_pairs, originals, skipped)
    for kind in ("trill", "grace", "slide", "third", "echo"):
        counts[kind] = sum(1 for _, audit in generated_pairs if audit.kind == kind)
    if len(generated_pairs) > config.max_generated_notes:
        return _empty_plan(
            input_hash,
            structural_hash,
            fingerprint,
            config,
            "MANUAL_REVIEW",
            f"Solo transformation budget exceeded: {len(generated_pairs)} > {config.max_generated_notes}",
            source_identity=source_identity,
            sound_binding_segments=binding_segments,
            protected_fingerprint=protected_fingerprint,
            delay_allocation=delay_allocation,
        )

    expression_events, expression_points, manual_expression = _expression_plan(
        midi, originals, chord_cells, profile, config
    )
    counts["cc11"] = len(expression_events)
    generated_notes = tuple(pair[0] for pair in generated_pairs)
    note_audit = tuple(pair[1] for pair in generated_pairs)
    evidence_ids = tuple(
        sorted({audit.evidence_id for audit in note_audit})
    )
    selection_payload = {
        "structure": structural_hash,
        "seed": config.seed,
        "profile": profile.profile_id,
        "notes": [
            [note.track, note.start, note.end, note.pitch, note.velocity, audit.kind, audit.evidence_id]
            for note, audit in generated_pairs
        ],
        "cc11": [[point.tick, point.value] for point in expression_points],
    }
    selection_hash = sha256(
        json.dumps(selection_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    decision = "AUGMENT" if generated_notes or expression_events else "KEEP"
    return SoloPlan(
        decision=decision,
        reason="Evidence-gated solo layers preserve every original note"
        if decision == "AUGMENT"
        else "No safe evidence-backed solo enhancement",
        input_hash=input_hash,
        structural_hash=structural_hash,
        selection_hash=selection_hash,
        config=config,
        generated_notes=generated_notes,
        note_audit=note_audit,
        expression_events=tuple(expression_events),
        expression_points=tuple(expression_points),
        original_fingerprint=fingerprint,
        counts=counts,
        skipped=skipped,
        evidence_ids=evidence_ids,
        manual_expression_preserved=manual_expression,
        echo_track_index=echo_track_index,
        source_identity=source_identity,
        sound_binding_segments=binding_segments,
        protected_fingerprint=protected_fingerprint,
        delay_allocation=delay_allocation,
    )


def apply_solo_enhancement(midi: MidiFile, plan: SoloPlan) -> SoloResult:
    if midi.digest() != plan.input_hash:
        raise ValueError("Input MIDI changed after the solo plan was created")
    if plan.decision != "AUGMENT":
        manifest = plan.to_manifest()
        fingerprint_report = (
            verify_solo_fingerprint(plan.protected_fingerprint, midi)
            if plan.protected_fingerprint is not None
            else {"passed": True}
        )
        manifest.update(
            {
                "applied": False,
                "outputHash": midi.digest(),
                "originalSoloPreserved": fingerprint_report["passed"],
                "soloFingerprintVerification": fingerprint_report,
                "programChangePreserved": True,
                "notePairingValid": True,
            }
        )
        return SoloResult(midi, manifest)
    before_bindings = plan.sound_binding_segments
    source_notes = [
        note for note in plan.generated_notes if note.track == plan.config.track_index
    ]
    echo_notes = [
        note for note in plan.generated_notes if note.element == "echo"
    ]
    output = midi
    if source_notes:
        output = output.add_notes(
            track_index=plan.config.track_index, new_notes=source_notes
        )
    output = output.add_events(
        track_index=plan.config.track_index, new_events=plan.expression_events
    )
    echo_track_created = False
    echo_setup_copied = False
    if echo_notes:
        if plan.echo_track_index is None or plan.echo_track_index == plan.config.track_index:
            raise MidiFormatError("Delay/Echo did not receive a separate free track")
        if plan.echo_track_index > len(output.tracks):
            raise MidiFormatError("Delay/Echo track allocation is not contiguous")
        if plan.echo_track_index == len(output.tracks):
            output = MidiFile(
                output.format_type, output.ppq, output.tracks + [MidiTrack([])]
            )
            echo_track_created = True
        setup = _copy_sound_setup(midi, plan)
        output = output.add_events(
            track_index=plan.echo_track_index, new_events=setup
        )
        output = output.add_notes(
            track_index=plan.echo_track_index, new_notes=echo_notes
        )
        echo_setup_copied = True
    fingerprint_report = (
        verify_solo_fingerprint(plan.protected_fingerprint, output)
        if plan.protected_fingerprint is not None
        else {"passed": not (Counter(plan.original_fingerprint) - Counter(
            (note.start, note.end, note.pitch, note.velocity)
            for note in output.notes()
            if note.track == plan.config.track_index and note.channel == plan.config.channel
        ))}
    )
    if not fingerprint_report["passed"]:
        raise MidiFormatError("Solo enhancement modified an original melody note")
    after_bindings = sound_bindings(
        output,
        track_index=plan.config.track_index,
        channel=plan.config.channel,
        start_tick=plan.config.start_tick,
        end_tick=plan.config.end_tick,
        track_uid=plan.source_identity.track_uid if plan.source_identity else None,
    )
    if before_bindings and after_bindings != before_bindings:
        raise MidiFormatError("Solo enhancement changed Bank Select or Program Change")
    manifest = plan.to_manifest()
    manifest.update(
        {
            "applied": True,
            "outputHash": output.digest(),
            "originalSoloPreserved": True,
            "soloFingerprintVerification": fingerprint_report,
            "programChangePreserved": True,
            "notePairingValid": True,
            "delayTrackIndex": plan.echo_track_index,
            "delayTrackCreated": echo_track_created,
            "delaySoundSetupCopied": echo_setup_copied,
            "delayOnSourceTrack": False,
        }
    )
    return SoloResult(output, manifest)


def _ornament_notes(
    evidence: GoldOrnamentEvidence,
    current: Note,
    following: Note,
    source_index: int,
    profile: FactorySoloProfile,
    config: SoloConfig,
) -> list[tuple[Note, SoloNoteAudit]]:
    count = len(evidence.intervals)
    total = count * evidence.note_duration_ticks
    if evidence.kind == "trill":
        first_tick = current.end
        anchor_pitch = current.pitch
    else:
        first_tick = following.start - total
        anchor_pitch = following.pitch
    output = []
    for index, interval in enumerate(evidence.intervals):
        pitch = anchor_pitch + interval
        if not profile.register_min <= pitch <= profile.register_max:
            continue
        start = first_tick + index * evidence.note_duration_ticks
        end = start + evidence.note_duration_ticks
        output.append(
            (
                Note(
                    track=config.track_index,
                    channel=config.channel,
                    pitch=pitch,
                    start=start,
                    end=end,
                    velocity=profile.velocity(config.intensity),
                    factory_profile_id=profile.profile_id,
                    gold_pattern_id=evidence.evidence_id,
                    element=evidence.kind,
                ),
                SoloNoteAudit(evidence.kind, evidence.evidence_id, source_index, interval),
            )
        )
    return output


def _diatonic_third_interval(pitch: int, chord: ChordCell) -> int | None:
    scale = _SCALES[chord.quality]
    relative = (pitch - chord.root_pc) % 12
    if relative not in scale:
        return None
    degree = scale.index(relative)
    target = scale[(degree + 2) % 7]
    if degree + 2 >= 7:
        target += 12
    interval = target - relative
    return interval if interval in {3, 4} else None


def _expression_plan(
    midi: MidiFile,
    originals: Sequence[Note],
    chords: Sequence[ChordCell],
    profile: FactorySoloProfile,
    config: SoloConfig,
) -> tuple[list[MidiEvent], list[ExpressionPoint], bool]:
    if not config.enable_expression:
        return [], [], False
    existing = [
        event
        for event in midi.tracks[config.track_index].events
        if event.kind == "channel"
        and event.command == 0xB0
        and event.channel == config.channel
        and len(event.data) == 2
        and event.data[0] == 11
        and config.start_tick <= event.tick < config.end_tick
    ]
    if existing:
        return [], [], True
    base = int(
        round(
            profile.expression_min
            + (profile.expression_max - profile.expression_min) * config.intensity / 100
        )
    )
    previous = max(profile.expression_min, min(profile.expression_max, base))
    points: list[ExpressionPoint] = []
    events: list[MidiEvent] = []
    previous_chord: tuple[int, str] | None = None
    for index, note in enumerate(originals):
        leap = abs(note.pitch - originals[index - 1].pitch) if index else 0
        chord = _chord_at(chords, note.start)
        chord_key = (chord.root_pc, chord.quality) if chord else None
        chord_change = 6 if index and chord_key != previous_chord else 0
        target = min(profile.expression_max, base + min(12, leap) + chord_change)
        delta = max(-profile.expression_max_step, min(profile.expression_max_step, target - previous))
        value = max(profile.expression_min, min(profile.expression_max, previous + delta))
        reason = "phrase-start" if index == 0 else ("chord-change" if chord_change else "melodic-tension")
        points.append(ExpressionPoint(note.start, value, reason))
        events.append(
            MidiEvent(
                tick=note.start,
                order=-1,
                kind="channel",
                status=0xB0 | config.channel,
                data=bytes((11, value)),
            )
        )
        previous = value
        previous_chord = chord_key
    return events, points, False


def _remove_unsafe_overlaps(
    pairs: Iterable[tuple[Note, SoloNoteAudit]],
    originals: Sequence[Note],
    skipped: dict[str, int],
) -> list[tuple[Note, SoloNoteAudit]]:
    output: list[tuple[Note, SoloNoteAudit]] = []
    seen: set[tuple[int, int, int, int]] = set()
    last_end_by_pitch: dict[tuple[int, int], int] = {}
    for note, audit in sorted(pairs, key=lambda pair: (pair[0].start, pair[0].pitch, pair[0].end, pair[1].kind)):
        key = (note.track, note.start, note.pitch, note.end)
        if key in seen:
            skipped["overlap"] += 1
            continue
        if note.track == originals[0].track and any(
            original.pitch == note.pitch
            and original.end > note.start
            and original.start < note.end
            for original in originals
        ):
            skipped["overlap"] += 1
            continue
        track_pitch = (note.track, note.pitch)
        if note.start < last_end_by_pitch.get(track_pitch, -1):
            skipped["overlap"] += 1
            continue
        seen.add(key)
        output.append((note, audit))
        last_end_by_pitch[track_pitch] = note.end
    return output


def _first_free_track(midi: MidiFile, source_track_index: int) -> int | None:
    """Return the first truly empty track, or the next SMF1 track slot."""

    for index, track in enumerate(midi.tracks):
        if index == source_track_index:
            continue
        meaningful = [
            event
            for event in track.events
            if not (event.kind == "meta" and event.meta_type == 0x2F)
        ]
        if not meaningful:
            return index
    return len(midi.tracks) if midi.format_type == 1 and len(midi.tracks) < 16 else None


def _copy_sound_setup(midi: MidiFile, plan: SoloPlan) -> list[MidiEvent]:
    """Copy the protected solo sound setup to the separate delay track."""

    source_events = midi.tracks[plan.config.track_index].events
    selected: dict[tuple[int, int | None], MidiEvent] = {}
    for event in source_events:
        if (
            event.kind != "channel"
            or event.channel != plan.config.channel
            or event.tick > plan.config.start_tick
        ):
            continue
        if event.command == 0xC0:
            key = (0xC0, None)
        elif event.command == 0xB0 and len(event.data) == 2 and event.data[0] in {0, 7, 11, 32}:
            key = (0xB0, event.data[0])
        else:
            continue
        previous = selected.get(key)
        if previous is None or (event.tick, event.order) > (previous.tick, previous.order):
            selected[key] = event
    copied = [MidiEvent(0, -1, "meta", data=b"Solo Delay", meta_type=0x03)]
    for event in sorted(selected.values(), key=lambda item: (item.tick, item.order)):
        copied.append(
            MidiEvent(
                tick=event.tick,
                order=-1,
                kind="channel",
                status=event.status,
                data=event.data,
            )
        )
    if not any(event.command == 0xC0 for event in copied):
        raise MidiFormatError("Delay track cannot copy the protected Program Change")
    return copied


def _assert_no_absolute_gold_pitch(value: Any, path: str = "gold") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in {"note", "notes", "pitch", "midinote", "absolutepitch"}:
                raise ValueError(f"Absolute pitch is forbidden in solo GOLD at {path}.{key}")
            _assert_no_absolute_gold_pitch(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_absolute_gold_pitch(child, f"{path}[{index}]")


def _chord_at(chords: Sequence[ChordCell], tick: int) -> ChordCell | None:
    return next((chord for chord in chords if chord.start_tick <= tick < chord.end_tick), None)


def _empty_plan(
    input_hash: str,
    structural_hash: str,
    fingerprint: tuple[tuple[int, int, int, int], ...],
    config: SoloConfig,
    decision: str,
    reason: str,
    *,
    source_identity: TrackIdentity | None = None,
    sound_binding_segments: tuple[SoundBinding, ...] = (),
    protected_fingerprint: SoloFingerprint | None = None,
    delay_allocation: DelayAllocation | None = None,
) -> SoloPlan:
    return SoloPlan(
        decision=decision,
        reason=reason,
        input_hash=input_hash,
        structural_hash=structural_hash,
        selection_hash=sha256(
            f"{structural_hash}|{config.seed}|{decision}|{reason}".encode("utf-8")
        ).hexdigest(),
        config=config,
        generated_notes=(),
        note_audit=(),
        expression_events=(),
        expression_points=(),
        original_fingerprint=fingerprint,
        counts={},
        skipped={},
        evidence_ids=(),
        manual_expression_preserved=False,
        echo_track_index=None,
        source_identity=source_identity,
        sound_binding_segments=sound_binding_segments,
        protected_fingerprint=protected_fingerprint,
        delay_allocation=delay_allocation,
    )