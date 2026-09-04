"""Session 17 adapters from production DNA registries to recovery engines.

The production databases deliberately keep richer provenance than the small
Session 2--7 demo registries.  This module is the only translation boundary:
it verifies one physical track and its time-scoped SoundBinding first, then
creates the already-tested engine dataclasses.  It never grants GOLD velocity,
Bank Select, Program Change, absolute harmonic pitch or guitar authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .drum_reconstruction import (
    FactoryVelocityProfile,
    GoldPattern,
    GoldPatternEvent,
    ReconstructionConfig,
    assert_gold_has_no_dynamic_authority,
    classify_drum_note,
)
from .guitar_reconstruction import (
    FactoryGuitarProfile,
    FactoryStrumPattern,
    FactoryStrumStroke,
    GuitarConfig,
)
from .harmonic_reconstruction import (
    DrumBassRelationship,
    FactoryInstrumentProfile,
    HarmonicConfig,
    HarmonicPattern,
    HarmonicPatternEvent,
)
from .midi import MidiFile, MidiFormatError
from .solo_enhancement import FactorySoloProfile, SoloConfig
from .track_identity import (
    build_track_identities,
    channel_track_indices,
    sound_bindings,
)


_REGISTRIES = {
    "factoryProfiles": "data/factory-velocity-profiles.json",
    "factorySegments": "data/factory-style-segments.json",
    "factoryStrumming": "data/factory-strumming.json",
    "goldLegacy": "data/gold-patterns.json",
    "goldPerformance": "data/gold-performance-patterns.json",
}
_SCHEMAS = {
    "factoryProfiles": ("midi-arranger.factory-velocity-profiles", "3.3"),
    "factorySegments": ("dna.factory-style-segments", "1.1"),
    "factoryStrumming": ("dna.factory-strumming", "1.1"),
    "goldPerformance": ("dna.gold-performance-patterns", "1.1"),
}
_SECTION_MAP = {
    "intro": "intro",
    "body": "body",
    "verse": "body",
    "chorus": "body",
    "variation": "body",
    "generic": "body",
    "fill": "transition",
    "break": "transition",
    "transition": "transition",
    "ending": "ending",
}
_DOCUMENT_CACHE: dict[tuple[Any, ...], tuple[dict[str, Any], dict[str, Any]]] = {}


def _curve(item: Mapping[str, Any]) -> tuple[int, int, int, int, int, int, int]:
    values = item.get("velocityCurve", {}).get("values", {})
    labels = ("floor", "soft", "lowMid", "optimal", "highMid", "strong", "ceiling")
    try:
        return tuple(int(values[label]) for label in labels)  # type: ignore[return-value]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Factory profile {item.get('id')} has no complete velocity curve") from exc


def _meter_quarters(meter: str) -> float:
    try:
        numerator, denominator = (int(value) for value in meter.split("/", 1))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Invalid meter: {meter}") from exc
    if numerator <= 0 or denominator <= 0:
        raise ValueError(f"Invalid meter: {meter}")
    return numerator * 4 / denominator


def _scale_tick(value: int | float, source_ppq: int, target_ppq: int) -> int:
    return int(round(float(value) * target_ppq / source_ppq))


def _sound(item: Mapping[str, Any]) -> tuple[int, int, int]:
    return int(item["bankMsb"]), int(item["bankLsb"]), int(item["program"])


def _profile_sound(item: Mapping[str, Any]) -> tuple[int, int, int]:
    return int(item["bankMsb"]), int(item["bankLsb"]), int(item["program"])


def _confidence(item: Mapping[str, Any]) -> float:
    return max(0.0, min(1.0, float(item.get("confidence", item.get("qualityScore", 1.0)))))


@dataclass(frozen=True)
class ProductionOptions:
    version: str = "1.0"
    max_patterns: int = 8
    allow_shared_channel: bool = False
    expected_track_uid: str | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ProductionOptions":
        if not isinstance(raw, Mapping):
            raise ValueError("Production adapter options must be an object")
        allowed = {"version", "maxPatterns", "allowSharedChannel", "expectedTrackUid"}
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError("Unknown production adapter fields: " + ", ".join(unknown))
        version = str(raw.get("version", ""))
        maximum = raw.get("maxPatterns", 8)
        shared = raw.get("allowSharedChannel", False)
        uid = raw.get("expectedTrackUid")
        if version != "1.0":
            raise ValueError("Production adapter requires version 1.0")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 32:
            raise ValueError("maxPatterns must be an integer in range 1..32")
        if not isinstance(shared, bool):
            raise ValueError("allowSharedChannel must be boolean")
        if uid is not None and (not isinstance(uid, str) or not uid.startswith("trk-")):
            raise ValueError("expectedTrackUid must be a stable track identity")
        return cls(version, maximum, shared, uid)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "maxPatterns": self.max_patterns,
            "allowSharedChannel": self.allow_shared_channel,
            "expectedTrackUid": self.expected_track_uid,
        }


@dataclass(frozen=True)
class AdapterBundle:
    allowed: bool
    status: str
    reason: str
    loaded: tuple[Any, ...]
    manifest: Mapping[str, Any]


class ProductionAdapter:
    """Read and adapt the five immutable production DNA registries."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.paths = {name: self.root / relative for name, relative in _REGISTRIES.items()}
        missing = [relative for name, relative in _REGISTRIES.items() if not self.paths[name].is_file()]
        if missing:
            raise FileNotFoundError("Missing production registries: " + ", ".join(missing))
        signature = tuple(
            (name, path.stat().st_size, path.stat().st_mtime_ns)
            for name, path in sorted(self.paths.items())
        )
        cached = _DOCUMENT_CACHE.get(signature)
        if cached is not None:
            self.documents, self.registry_manifest = cached
            self.factory_profiles = {
                item["id"]: item for item in self.documents["factoryProfiles"].get("profiles", [])
            }
            return
        self.documents = {
            name: json.loads(path.read_text(encoding="utf-8"))
            for name, path in self.paths.items()
        }
        for name, (schema, version) in _SCHEMAS.items():
            document = self.documents[name]
            if document.get("schema") != schema or str(document.get("version")) != version:
                raise ValueError(f"Unsupported production registry schema: {name}")
        validation = self.documents["goldPerformance"].get("schemaValidation", {})
        if validation.get("passed") is not True:
            raise ValueError("Production GOLD performance registry failed its schema validation")
        assert_gold_has_no_dynamic_authority(self.documents["goldPerformance"].get("patterns", []))
        assert_gold_has_no_dynamic_authority(self.documents["goldPerformance"].get("relationships", []))
        self.factory_profiles = {
            item["id"]: item for item in self.documents["factoryProfiles"].get("profiles", [])
        }
        self.registry_manifest = {
            name: {
                "path": _REGISTRIES[name],
                "schema": document.get("schema"),
                "version": str(document.get("version")),
                "databaseVersion": document.get("databaseVersion"),
                "sha256": sha256(self.paths[name].read_bytes()).hexdigest(),
            }
            for name, document in self.documents.items()
        }
        _DOCUMENT_CACHE.clear()
        _DOCUMENT_CACHE[signature] = (self.documents, self.registry_manifest)

    def catalog(self) -> dict[str, Any]:
        return {
            "schema": "dna-session17-production-registry-catalog",
            "version": "1.0",
            "registries": self.registry_manifest,
            "counts": {
                "factoryProfiles": len(self.factory_profiles),
                "factorySegments": len(self.documents["factorySegments"].get("segments", [])),
                "factoryStrumming": len(self.documents["factoryStrumming"].get("patterns", [])),
                "goldLegacy": len(self.documents["goldLegacy"].get("patterns", [])),
                "goldPerformance": len(self.documents["goldPerformance"].get("patterns", [])),
                "drumBassRelationships": len(self.documents["goldPerformance"].get("relationships", [])),
            },
            "invariants": {
                "goldAffectsVelocity": False,
                "goldAffectsBankSelect": False,
                "goldAffectsProgramChange": False,
                "goldControlsRhythmGuitar": False,
            },
        }

    def adapt(
        self,
        engine: str,
        midi: MidiFile,
        config: Any,
        raw_options: Mapping[str, Any],
    ) -> AdapterBundle:
        options = ProductionOptions.from_mapping(raw_options)
        preflight = self._preflight(midi, config, options)
        if not preflight["allowed"]:
            return self._bundle(False, "MANUAL_REVIEW", preflight["reason"], (), options, preflight)
        if engine == "drum":
            return self._adapt_drum(midi, config, options, preflight)
        if engine == "harmonic":
            return self._adapt_harmonic(midi, config, options, preflight)
        if engine == "guitar":
            return self._adapt_guitar(midi, config, options, preflight)
        if engine == "solo":
            return self._adapt_solo(config, options, preflight)
        if engine == "rx":
            return self._bundle(
                False,
                "DEVICE_BLOCKED_MISSING_CONFIRMED_RX_MAP",
                "Production registry has no confirmed Pa800 RX articulation map",
                (), options, preflight,
            )
        if engine == "dnc":
            return self._bundle(
                False,
                "DEVICE_BLOCKED_MISSING_CONFIRMED_DNC_MAP",
                "Production registry has no confirmed Pa800 DNC articulation map",
                (), options, preflight,
            )
        raise ValueError(f"Unsupported production engine: {engine}")

    def _bundle(
        self,
        allowed: bool,
        status: str,
        reason: str,
        loaded: tuple[Any, ...],
        options: ProductionOptions,
        preflight: Mapping[str, Any],
        **details: Any,
    ) -> AdapterBundle:
        manifest = {
            "schema": "dna-session17-production-adapter",
            "version": "1.0",
            "allowed": allowed,
            "status": status,
            "reason": reason,
            "options": options.to_manifest(),
            "preflight": dict(preflight),
            "registries": self.registry_manifest,
            "authority": {
                "factoryVelocityOnly": True,
                "goldRelativePerformanceOnly": True,
                "goldBankProgramAuthority": False,
                "goldGuitarAuthority": False,
                "unconfirmedArticulationTriggers": False,
            },
            **details,
        }
        return AdapterBundle(allowed, status, reason, loaded, manifest)

    def _preflight(
        self, midi: MidiFile, config: Any, options: ProductionOptions
    ) -> dict[str, Any]:
        track_index = int(config.track_index)
        channel = int(config.channel)
        if not 0 <= track_index < len(midi.tracks):
            return {"allowed": False, "reason": "Selected production target track does not exist"}
        identities = build_track_identities(midi)
        identity = identities[track_index]
        expected_uid = options.expected_track_uid or getattr(config, "track_uid", None)
        if expected_uid is not None and expected_uid != identity.track_uid:
            return {
                "allowed": False,
                "reason": "trackUid/expectedTrackUid does not match the physical production target track",
                "trackIdentity": identity.to_manifest(),
            }
        try:
            bindings = sound_bindings(
                midi,
                track_index=track_index,
                channel=channel,
                start_tick=int(config.start_tick),
                end_tick=int(config.end_tick),
                track_uid=identity.track_uid,
            )
        except (MidiFormatError, ValueError) as exc:
            return {"allowed": False, "reason": str(exc), "trackIdentity": identity.to_manifest()}
        if any(not binding.complete for binding in bindings):
            return {
                "allowed": False,
                "reason": "Production target has an unresolved time-scoped Bank/Program binding",
                "trackIdentity": identity.to_manifest(),
                "soundBindings": [item.to_manifest() for item in bindings],
            }
        sounds = {binding.sound for binding in bindings}
        if len(bindings) != 1 or len(sounds) != 1:
            return {
                "allowed": False,
                "reason": "Production target changes Bank Select or Program inside the requested window",
                "trackIdentity": identity.to_manifest(),
                "soundBindings": [item.to_manifest() for item in bindings],
            }
        owners = channel_track_indices(midi, channel)
        shared_allowed = options.allow_shared_channel or bool(
            getattr(config, "allow_shared_channel", False)
        )
        if set(owners) - {track_index} and not shared_allowed:
            return {
                "allowed": False,
                "reason": "Production target channel is shared by multiple physical tracks",
                "trackIdentity": identity.to_manifest(),
                "soundBindings": [item.to_manifest() for item in bindings],
                "channelOwnerTrackIndices": list(owners),
                "channelOwnerTrackNumbers": [index + 1 for index in owners],
            }
        return {
            "allowed": True,
            "reason": "Exact physical-track and time-scoped SoundBinding verified",
            "trackIdentity": identity.to_manifest(),
            "soundBindings": [item.to_manifest() for item in bindings],
            "exactSound": list(next(iter(sounds))),
            "channelOwnerTrackIndices": list(owners),
            "channelOwnerTrackNumbers": [index + 1 for index in owners],
            "sharedChannelApproved": bool(set(owners) - {track_index}) and shared_allowed,
            "smf0Merged": identity.smf0_merged,
        }

    def _adapt_drum(
        self,
        midi: MidiFile,
        config: ReconstructionConfig,
        options: ProductionOptions,
        preflight: Mapping[str, Any],
    ) -> AdapterBundle:
        exact_sound = tuple(preflight["exactSound"])
        raw_profiles = [
            item for item in self.factory_profiles.values()
            if item.get("kind") == "drum" and _profile_sound(item) == exact_sound
        ]
        profiles: dict[int, FactoryVelocityProfile] = {}
        for item in raw_profiles:
            points = _curve(item)
            note = int(item["drumNote"])
            profiles[note] = FactoryVelocityProfile(str(item["id"]), note, *points)
        section = _SECTION_MAP.get(config.section, config.section)
        candidates = []
        for item in self.documents["goldPerformance"].get("patterns", []):
            if item.get("role") != config.role or item.get("meter") != config.meter:
                continue
            if item.get("sourceSection") != section:
                continue
            notes = {int(event[2]) for event in item.get("events", [])}
            if notes and notes <= set(profiles):
                candidates.append(item)
        candidates.sort(key=lambda item: (-_confidence(item), -float(item.get("qualityScore", 0)), item["id"]))
        patterns = []
        for item in candidates[: options.max_patterns]:
            source_ppq = int(item.get("timingResolution", 96))
            length = max(1, int(round(float(item["lengthBars"]) * _meter_quarters(config.meter) * midi.ppq)))
            events = []
            for tick, duration, note in item.get("events", []):
                note = int(note)
                element = "percussion" if config.role == "percussion" else classify_drum_note(note)
                events.append(GoldPatternEvent(
                    max(0, _scale_tick(tick, source_ppq, midi.ppq)),
                    max(1, _scale_tick(duration, source_ppq, midi.ppq)),
                    note,
                    element,
                ))
            patterns.append(GoldPattern(
                str(item["id"]), config.role, config.section, config.meter,
                length, tuple(events), _confidence(item), str(item.get("sourceSection", "neutral")),
            ))
        if not profiles:
            return self._bundle(False, "MANUAL_REVIEW", "No exact Factory drum-note profiles for target kit", (), options, preflight)
        if not patterns:
            return self._bundle(False, "MANUAL_REVIEW", "No production GOLD drum pattern is fully covered by the exact Factory kit", (), options, preflight)
        return self._bundle(
            True, "PRODUCTION_ADAPTER_VALIDATED",
            "Production drum/percussion patterns adapted with exact Factory note dynamics",
            (patterns, profiles), options, preflight,
            engine="drum", patternIds=[item.pattern_id for item in patterns],
            factoryProfileIds=sorted(item.profile_id for item in profiles.values()),
            transformations=["timing-resolution-to-midi-ppq", "factory-note-velocity-binding"],
        )

    def _adapt_harmonic(
        self,
        midi: MidiFile,
        config: HarmonicConfig,
        options: ProductionOptions,
        preflight: Mapping[str, Any],
    ) -> AdapterBundle:
        raw_profile = self.factory_profiles.get(config.profile_id)
        exact_sound = tuple(preflight["exactSound"])
        allowed_roles = {"bass"} if config.role == "bass" else {"chords", "melody"}
        if raw_profile is None or raw_profile.get("role") not in allowed_roles or _profile_sound(raw_profile) != exact_sound:
            return self._bundle(False, "MANUAL_REVIEW", "Exact Factory harmonic profile/SoundBinding match is missing", (), options, preflight)
        points = _curve(raw_profile)
        register = raw_profile.get("register", {})
        profile = FactoryInstrumentProfile(
            str(raw_profile["id"]), config.role, int(raw_profile["program"]),
            int(register.get("low", 0)), int(register.get("high", 127)), *points,
        )
        raw_relationships = self.documents["goldPerformance"].get("relationships", [])
        selected_ids: set[str] | None = None
        if config.role == "bass" and config.require_relationship and config.selected_drum_pattern_id:
            selected_ids = {
                str(item.get("patterns", {}).get("bass"))
                for item in raw_relationships
                if item.get("patterns", {}).get("drums") == config.selected_drum_pattern_id
            }
        section = _SECTION_MAP.get(config.section, config.section)
        candidates = [
            item for item in self.documents["goldPerformance"].get("patterns", [])
            if item.get("role") == config.role
            and item.get("meter") == config.meter
            and item.get("sourceSection") == section
            and (selected_ids is None or item.get("id") in selected_ids)
        ]
        candidates.sort(key=lambda item: (-_confidence(item), -float(item.get("qualityScore", 0)), item["id"]))
        patterns = []
        for item in candidates[: options.max_patterns]:
            source_ppq = int(item.get("timingResolution", 96))
            grouped: dict[tuple[int, int], list[int]] = {}
            for tick, duration, relative in item.get("events", []):
                grouped.setdefault((int(tick), int(duration)), []).append(int(relative))
            converted = []
            for (tick, duration), relative_values in sorted(grouped.items()):
                intervals = self._relative_intervals(relative_values)
                if not intervals:
                    continue
                converted.append(HarmonicPatternEvent(
                    max(0, _scale_tick(tick, source_ppq, midi.ppq)),
                    max(1, _scale_tick(duration, source_ppq, midi.ppq)),
                    "root", 1, intervals, 0, "none",
                ))
            if not converted:
                continue
            length = max(1, int(round(float(item["lengthBars"]) * _meter_quarters(config.meter) * midi.ppq)))
            patterns.append(HarmonicPattern(
                str(item["id"]), config.role, config.section, config.meter,
                length, tuple(converted), _confidence(item),
            ))
        pattern_ids = {item.pattern_id for item in patterns}
        relationships = []
        for item in raw_relationships:
            ids = item.get("patterns", {})
            if ids.get("bass") not in pattern_ids:
                continue
            relationships.append(DrumBassRelationship(
                str(item["id"]), str(ids["drums"]), str(ids["bass"]), 1.0,
            ))
        if not patterns:
            return self._bundle(False, "MANUAL_REVIEW", "No compatible relative production GOLD harmonic pattern", (), options, preflight)
        return self._bundle(
            True, "PRODUCTION_ADAPTER_VALIDATED",
            "Relative GOLD harmony adapted through exact Factory sound/register/dynamics",
            (patterns, {profile.profile_id: profile}, relationships), options, preflight,
            engine="harmonic", patternIds=[item.pattern_id for item in patterns],
            relationshipIds=[item.relationship_id for item in relationships],
            factoryProfileIds=[profile.profile_id],
            transformations=["relative-semitone-normalization", "timing-resolution-to-midi-ppq"],
            goldContainsAbsolutePitch=False,
        )

    @staticmethod
    def _relative_intervals(values: list[int]) -> tuple[int, ...]:
        for octave_shift in sorted(range(-4, 5), key=lambda value: (abs(value), value)):
            shifted = sorted({value + 12 * octave_shift for value in values})
            if shifted and shifted[0] >= 0 and shifted[-1] <= 24:
                return tuple(shifted)
        return ()

    def _adapt_guitar(
        self,
        midi: MidiFile,
        config: GuitarConfig,
        options: ProductionOptions,
        preflight: Mapping[str, Any],
    ) -> AdapterBundle:
        if config.enable_controls:
            return self._bundle(
                False, "DEVICE_BLOCKED_MISSING_CONFIRMED_GUITAR_CONTROL_MAP",
                "Production Factory strumming is available, but Guitar Mode controls require a confirmed device map",
                (), options, preflight,
            )
        raw_profile = self.factory_profiles.get(config.profile_id)
        exact_sound = tuple(preflight["exactSound"])
        if raw_profile is None or raw_profile.get("role") not in {"chords", "melody"} or _profile_sound(raw_profile) != exact_sound:
            return self._bundle(False, "MANUAL_REVIEW", "Exact Factory guitar profile/SoundBinding match is missing", (), options, preflight)
        points = _curve(raw_profile)
        register = raw_profile.get("register", {})
        profile = FactoryGuitarProfile(
            str(raw_profile["id"]), int(raw_profile["program"]),
            (40, 45, 50, 55, 59, 64), 0, 24, 12,
            max(40, int(register.get("low", 40))), min(88, int(register.get("high", 88))),
            *points, None,
        )
        section = _SECTION_MAP.get(config.section, config.section)
        candidates = []
        for item in self.documents["factoryStrumming"].get("patterns", []):
            if config.profile_id not in item.get("factoryProfileIds", []):
                continue
            if item.get("meter") != config.meter or item.get("sourceSection") != section:
                continue
            evidence = item.get("soundEvidence", {})
            if _sound(evidence) != exact_sound:
                continue
            candidates.append(item)
        candidates.sort(key=lambda item: (-_confidence(item), -float(item.get("qualityScore", 0)), item["id"]))
        patterns = []
        dropped_mixed = 0
        for item in candidates:
            source_ppq = int(item.get("timingResolution", 96))
            strokes = []
            for stroke in item.get("strokes", []):
                direction = str(stroke.get("direction"))
                if direction not in {"down", "up", "block"}:
                    dropped_mixed += 1
                    continue
                count = min(6, max(1, int(stroke.get("chordSize", 1))))
                raw_offsets = [int(value) for value in stroke.get("interStringOffsets", [])]
                if len(raw_offsets) < count:
                    raw_offsets += [raw_offsets[-1] if raw_offsets else 0] * (count - len(raw_offsets))
                scaled = [_scale_tick(value, source_ppq, midi.ppq) for value in raw_offsets[:count]]
                if direction == "down":
                    offsets = tuple(sorted(scaled))
                elif direction == "up":
                    offsets = tuple(sorted(scaled, reverse=True))
                else:
                    offsets = (0,) * count
                strokes.append(FactoryStrumStroke(
                    max(0, _scale_tick(stroke["onset"], source_ppq, midi.ppq)),
                    direction,
                    tuple(range(count)),
                    tuple(index % 3 for index in range(count)),
                    offsets,
                    max(1, _scale_tick(stroke.get("medianGate", 1), source_ppq, midi.ppq)),
                ))
            if not strokes:
                continue
            source_ids = tuple(sorted({
                str(proof.get("sourceId"))
                for proof in item.get("sourceProof", [])
                if proof.get("sourceId")
            }))
            if not source_ids:
                continue
            length = max(1, int(round(float(item["lengthBars"]) * _meter_quarters(config.meter) * midi.ppq)))
            patterns.append(FactoryStrumPattern(
                str(item["id"]), profile.profile_id, config.section, config.meter,
                length, tuple(strokes), _confidence(item), source_ids,
            ))
            if len(patterns) >= options.max_patterns:
                break
        if not patterns:
            return self._bundle(False, "MANUAL_REVIEW", "No exact-sound Factory strumming pattern can be safely adapted", (), options, preflight)
        return self._bundle(
            True, "PRODUCTION_ADAPTER_VALIDATED",
            "Factory-only strumming adapted without GOLD or unconfirmed control triggers",
            (patterns, {profile.profile_id: profile}, {}), options, preflight,
            engine="guitar", patternIds=[item.pattern_id for item in patterns],
            factoryProfileIds=[profile.profile_id], droppedMixedStrokes=dropped_mixed,
            transformations=["six-string-cap", "root-third-fifth-string-projection", "timing-resolution-to-midi-ppq"],
            goldControlsRhythmGuitar=False, controlNotesGuessed=False,
        )

    def _adapt_solo(
        self,
        config: SoloConfig,
        options: ProductionOptions,
        preflight: Mapping[str, Any],
    ) -> AdapterBundle:
        raw_profile = self.factory_profiles.get(config.profile_id)
        exact_sound = tuple(preflight["exactSound"])
        if raw_profile is None or raw_profile.get("role") != "melody" or _profile_sound(raw_profile) != exact_sound:
            return self._bundle(False, "MANUAL_REVIEW", "Exact Factory solo profile/SoundBinding match is missing", (), options, preflight)
        expression = raw_profile.get("mixerProfile", {}).get("expression")
        if not isinstance(expression, Mapping):
            return self._bundle(False, "MANUAL_REVIEW", "Factory solo profile has no CC11 evidence", (), options, preflight)
        allowed_range = expression.get("allowedRange", [0, 127])
        expression_min, expression_max = (int(allowed_range[0]), int(allowed_range[1]))
        points = _curve(raw_profile)
        register = raw_profile.get("register", {})
        profile = FactorySoloProfile(
            str(raw_profile["id"]), *exact_sound,
            int(register.get("low", 0)), int(register.get("high", 127)),
            *points,
            expression_min, expression_max,
            max(1, min(24, math.ceil(max(1, expression_max - expression_min) / 6))),
            12, 8,
        )
        return self._bundle(
            True, "PRODUCTION_PARTIAL_FACTORY_EXPRESSION_ONLY",
            "Exact Factory solo expression is available; production GOLD ornaments/relationships are not yet authoritative",
            ([], [], {profile.profile_id: profile}), options, preflight,
            engine="solo", factoryProfileIds=[profile.profile_id],
            ornamentEvidenceIds=[], relationshipIds=[],
            transformations=["factory-cc11-range-and-smoothing"],
            originalSoloMutationAllowed=False,
        )