"""Session 22 deterministic production candidate search.

The engine consumes an approved ArrangementGraph 2.0 and its matching
SongMap 2.0, retrieves real production Factory/GOLD patterns, applies hard
constraints before scoring, and produces two to four read-only arrangement
variants.  It selects pattern identities only: it cannot write MIDI, choose
Bank/Program values, alter Factory dynamics, or mutate an original solo.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .arrangement_graph import validate_arrangement_graph_v2
from .producer_brief import ELEMENTS, ROLES
from .production_adapter import ProductionAdapter
from .song_understanding import validate_song_map_v2


CANDIDATE_SET_SCHEMA = "dna-premium-candidate-set"
CANDIDATE_SET_VERSION = "2.0"
SOURCE_KINDS = ("GOLD_PERFORMANCE", "FACTORY_STRUMMING")
VARIANT_LABELS = ("A", "B", "C", "D")
CRITERIA = (
    "tempoMatch", "sectionMatch", "densityMatch", "energyMatch",
    "lengthMatch", "registerFit", "polyphonyHeadroom",
    "confidenceEvidence", "occurrenceEvidence", "transitionCompatibility",
    "transformationEconomy", "motifCompatibility",
    "relationshipCompatibility", "authorityCompliance",
)
_STABLE_ID = re.compile(r"^[0-9]{3}\.[0-9]{3}\.[0-9]{3}$")
_REQUEST_KEY = re.compile(r"^(i[12]cv1|v[1-4]cv1|f[12]cv1|e[12]cv1):[a-z-]+$")
_GOLD_FORBIDDEN_KEYS = {
    "velocity", "velocities", "bankmsb", "banklsb", "program",
    "programchange", "instrumentkey", "factoryprofileid", "factoryprofileids",
}
_SOURCE_ROLES = {
    "drums": ("drums",), "percussion": ("percussion",), "bass": ("bass",),
    "accompaniment": ("accompaniment",), "riff": ("riff", "power-riff"),
    "pad": ("accompaniment",),
}
_DENSITY_TARGET = {"sparse": 4.0, "balanced": 10.0, "full": 18.0}
_GOLD_AUTHORITY_CACHE: dict[int, bool] = {}
_PATTERN_METRIC_CACHE: dict[int, dict[str, Any]] = {}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return sha256(_canonical({key: item for key, item in value.items() if key != field})).hexdigest()


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _stable_hash(parts: Iterable[Any]) -> str:
    return sha256(":".join(str(item) for item in parts).encode("utf-8")).hexdigest()


def _forbidden_gold_paths(value: Any, prefix: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            if str(key).lower().replace("_", "") in _GOLD_FORBIDDEN_KEYS:
                found.append(path)
            found.extend(_forbidden_gold_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_forbidden_gold_paths(item, f"{prefix}[{index}]"))
    return found


def _value_at_tick(items: Sequence[Mapping[str, Any]], tick: int) -> Mapping[str, Any]:
    eligible = [item for item in items if int(item["tick"]) <= tick]
    return max(eligible, key=lambda item: int(item["tick"])) if eligible else items[0]


def _section_tick(song_map: Mapping[str, Any], section_id: str) -> int:
    section = next(item for item in song_map["sections"] if item["id"] == section_id)
    return int(section["startTick"])


def _meter_for_node(song_map: Mapping[str, Any], node: Mapping[str, Any]) -> str:
    meter = _value_at_tick(song_map["meterMap"], _section_tick(song_map, node["sourceSectionId"]))
    return f"{int(meter['numerator'])}/{int(meter['denominator'])}"


def _tempo_for_node(song_map: Mapping[str, Any], node: Mapping[str, Any]) -> float:
    tempo = _value_at_tick(song_map["tempoMap"], _section_tick(song_map, node["sourceSectionId"]))
    return float(tempo["bpm"])


def _source_section(node: Mapping[str, Any]) -> str:
    return {"intro": "intro", "variation": "body", "fill": "transition", "ending": "ending"}[
        str(node["elementType"])
    ]


def _section_score(expected: str, actual: str) -> float:
    if expected == actual:
        return 1.0
    compatibility = {
        ("intro", "body"): 0.60, ("ending", "body"): 0.55,
        ("transition", "body"): 0.68, ("body", "transition"): 0.48,
        ("intro", "transition"): 0.42, ("ending", "transition"): 0.50,
    }
    return compatibility.get((expected, actual), 0.25)


def _role_score(requested: str, actual: str, source_kind: str) -> float:
    if requested == "guitar":
        return 1.0 if source_kind == "FACTORY_STRUMMING" else 0.0
    if requested == actual:
        return 1.0
    if requested == "riff" and actual == "power-riff":
        return 0.90
    if requested == "pad" and actual == "accompaniment":
        return 0.82
    return 0.0


def _pattern_events(pattern: Mapping[str, Any]) -> list[Sequence[Any]]:
    events = pattern.get("events", pattern.get("notes", []))
    return [item for item in events if isinstance(item, (list, tuple)) and len(item) >= 3]


def _polyphony_peak(events: Sequence[Sequence[Any]]) -> int:
    sweep: list[tuple[float, int]] = []
    for event in events:
        start, duration = float(event[0]), max(0.000001, float(event[1]))
        sweep.extend(((start, 1), (start + duration, -1)))
    active = peak = 0
    for _, delta in sorted(sweep, key=lambda item: (item[0], item[1])):
        active += delta
        peak = max(peak, active)
    return peak


def _rhythm_signature(pattern: Mapping[str, Any]) -> str:
    events = _pattern_events(pattern)
    resolution = max(1, int(pattern.get("timingResolution", 96)))
    normalized = sorted({round(float(item[0]) / resolution, 4) for item in events})
    return sha256(_canonical(normalized)).hexdigest()[:16]


def _pattern_register(pattern: Mapping[str, Any]) -> tuple[int, int]:
    register = pattern.get("register", {})
    return int(register.get("low", 0)), int(register.get("high", 0))


def _gold_authority_violation(pattern: Mapping[str, Any]) -> bool:
    key = id(pattern)
    if key not in _GOLD_AUTHORITY_CACHE:
        _GOLD_AUTHORITY_CACHE[key] = bool(_forbidden_gold_paths(pattern))
    return _GOLD_AUTHORITY_CACHE[key]


def _pattern_metrics(pattern: Mapping[str, Any]) -> dict[str, Any]:
    key = id(pattern)
    cached = _PATTERN_METRIC_CACHE.get(key)
    if cached is not None:
        return cached
    events = _pattern_events(pattern)
    length = float(pattern.get("lengthBars", 0))
    proof = pattern.get("sourceProof", [])
    value = {
        "events": events,
        "length": length,
        "density": float(pattern.get("density", len(events) / max(1.0, length))),
        "peak": _polyphony_peak(events),
        "register": _pattern_register(pattern),
        "rhythmSignature": _rhythm_signature(pattern),
        "proof": proof,
        "sourceIds": sorted({str(item.get("sourceId")) for item in proof if item.get("sourceId")})[:8],
    }
    _PATTERN_METRIC_CACHE[key] = value
    return value


def _length_operations(length_bars: float, target_bars: int, requested_role: str) -> tuple[int, list[str]]:
    transformations: list[str] = []
    if length_bars <= 0:
        return 10**6, transformations
    if abs(length_bars - target_bars) < 1e-9:
        operations = 0
    elif target_bars % length_bars == 0:
        operations = 1
        transformations.append("repeat-to-target-bars")
    elif length_bars > target_bars:
        operations = 1
        transformations.append("crop-on-bar-boundary")
    else:
        operations = 2
        transformations.extend(("repeat-to-target-bars", "crop-on-bar-boundary"))
    if requested_role == "pad":
        operations += 1
        transformations.append("accompaniment-to-pad-role-projection")
    elif requested_role == "riff":
        transformations.append("relative-riff-role-projection")
    return operations, transformations


def _tempo_score(tempo: float, tempo_range: Sequence[Any]) -> float:
    if len(tempo_range) != 2:
        return 0.55
    low, high = float(tempo_range[0]), float(tempo_range[1])
    if low <= tempo <= high:
        return 1.0
    distance = min(abs(tempo - low), abs(tempo - high))
    return _clamp(1.0 - distance / 80.0)


def _density_score(target: str, density: float) -> float:
    desired = _DENSITY_TARGET[target]
    return _clamp(1.0 - abs(math.log1p(max(0.0, density)) - math.log1p(desired)) / 3.0)


def _energy_score(target: int, density: float, pattern: Mapping[str, Any]) -> float:
    syncopation = float(pattern.get("articulation", {}).get("syncopation", 0.0))
    estimated = min(100.0, 14.0 + density * 2.4 + syncopation * 18.0)
    return _clamp(1.0 - abs(estimated - target) / 100.0)


def _transition_score(node: Mapping[str, Any], pattern: Mapping[str, Any]) -> float:
    context = pattern.get("transitionContext", {})
    if node["elementType"] in {"fill", "ending"}:
        exit_notes = float(context.get("exitNotes", 0))
        space = 1.0 if context.get("endsWithSpace") else 0.55
        return _clamp(0.35 + min(0.4, exit_notes / 24.0) + 0.25 * space)
    return 0.85 if context else 0.70


def _motif_score(node: Mapping[str, Any], pattern: Mapping[str, Any]) -> float:
    events = _pattern_events(pattern)
    if not events:
        return 0.0
    onsets = len({float(item[0]) for item in events})
    repetition = len(events) / max(1, onsets)
    treatment = str(node["motifTreatment"])
    desired = 2.4 if treatment in {"peak", "intensify", "launch"} else 1.5
    return _clamp(1.0 - abs(repetition - desired) / 4.0)


@dataclass(frozen=True)
class CandidateSearchControls:
    version: str = "1.0"
    locked_selections: tuple[tuple[str, str, str], ...] = ()
    excluded_pattern_ids: tuple[str, ...] = ()
    next_candidate_offsets: tuple[tuple[str, int], ...] = ()
    regenerate_markers: tuple[str, ...] = ()
    max_candidates_per_request: int = 12

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "CandidateSearchControls":
        raw = {} if raw is None else raw
        if not isinstance(raw, Mapping):
            raise ValueError("Candidate search controls must be an object")
        allowed = {"version", "lockedSelections", "excludedPatternIds", "nextCandidateOffsets",
                   "regenerateMarkers", "maxCandidatesPerRequest"}
        if set(raw) - allowed:
            raise ValueError("Unknown Candidate Search controls: " + ", ".join(sorted(set(raw) - allowed)))
        version = raw.get("version", "1.0")
        if version != "1.0":
            raise ValueError("Candidate Search controls require version 1.0")
        maximum = raw.get("maxCandidatesPerRequest", 12)
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 4 <= maximum <= 32:
            raise ValueError("maxCandidatesPerRequest must be in range 4..32")
        locked: list[tuple[str, str, str]] = []
        for item in raw.get("lockedSelections", []):
            if not isinstance(item, Mapping) or set(item) != {"marker", "role", "patternId"}:
                raise ValueError("Each locked selection requires marker, role and patternId")
            marker, role, pattern_id = str(item["marker"]), str(item["role"]), str(item["patternId"])
            if marker not in ELEMENTS or role not in ROLES or not _STABLE_ID.fullmatch(pattern_id):
                raise ValueError("Invalid locked candidate selection")
            locked.append((marker, role, pattern_id))
        if len({(item[0], item[1]) for item in locked}) != len(locked):
            raise ValueError("A request can have only one locked selection")
        excluded = tuple(sorted(set(str(item) for item in raw.get("excludedPatternIds", []))))
        if any(not _STABLE_ID.fullmatch(item) for item in excluded):
            raise ValueError("Excluded pattern IDs must use stable numeric IDs")
        if set(excluded) & {item[2] for item in locked}:
            raise ValueError("A locked pattern cannot also be excluded")
        offsets: list[tuple[str, int]] = []
        for item in raw.get("nextCandidateOffsets", []):
            if not isinstance(item, Mapping) or set(item) != {"requestId", "offset"}:
                raise ValueError("Each next-candidate offset requires requestId and offset")
            request_id, offset = str(item["requestId"]), item["offset"]
            if not _REQUEST_KEY.fullmatch(request_id) or isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 31:
                raise ValueError("Invalid next-candidate offset")
            offsets.append((request_id, offset))
        if len({item[0] for item in offsets}) != len(offsets):
            raise ValueError("Duplicate next-candidate request")
        markers = tuple(sorted(set(str(item) for item in raw.get("regenerateMarkers", []))))
        if any(item not in ELEMENTS for item in markers):
            raise ValueError("Invalid partial-regeneration marker")
        return cls("1.0", tuple(sorted(locked)), excluded, tuple(sorted(offsets)), markers, maximum)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "lockedSelections": [
                {"marker": marker, "role": role, "patternId": pattern_id}
                for marker, role, pattern_id in self.locked_selections
            ],
            "excludedPatternIds": list(self.excluded_pattern_ids),
            "nextCandidateOffsets": [
                {"requestId": request_id, "offset": offset}
                for request_id, offset in self.next_candidate_offsets
            ],
            "regenerateMarkers": list(self.regenerate_markers),
            "maxCandidatesPerRequest": self.max_candidates_per_request,
        }


class CandidateSearchEngine:
    """Indexed, deterministic search over immutable production registries."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        adapter = ProductionAdapter(self.root)
        self.registry_manifest = {
            name: dict(value) for name, value in adapter.registry_manifest.items()
            if name in {"factoryStrumming", "goldPerformance"}
        }
        self.gold_patterns = list(adapter.documents["goldPerformance"].get("patterns", []))
        self.strum_patterns = list(adapter.documents["factoryStrumming"].get("patterns", []))
        self.gold_index: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        self.strum_index: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for pattern in self.gold_patterns:
            self.gold_index[(str(pattern.get("role")), str(pattern.get("meter")))].append(pattern)
        for pattern in self.strum_patterns:
            self.strum_index[str(pattern.get("meter"))].append(pattern)
        for values in (*self.gold_index.values(), *self.strum_index.values()):
            values.sort(key=lambda item: str(item.get("id")))
        self.relationship_by_pair: dict[tuple[str, str], str] = {}
        for item in adapter.documents["goldPerformance"].get("relationships", []):
            patterns = item.get("patterns", {})
            drum, bass = str(patterns.get("drums", "")), str(patterns.get("bass", ""))
            if _STABLE_ID.fullmatch(drum) and _STABLE_ID.fullmatch(bass):
                self.relationship_by_pair[(drum, bass)] = str(item["id"])

    def _fast_retrieve(
        self, node: Mapping[str, Any], target: Mapping[str, Any], role: str,
        meter: str, maximum: int,
    ) -> tuple[list[tuple[Mapping[str, Any], str]], dict[str, Any]]:
        expected_section = _source_section(node)
        if role == "guitar":
            source = [(item, "FACTORY_STRUMMING") for item in self.strum_index.get(meter, [])]
        else:
            source = []
            for source_role in _SOURCE_ROLES.get(role, ()):
                source.extend((item, "GOLD_PERFORMANCE") for item in self.gold_index.get((source_role, meter), []))
        density_target = _DENSITY_TARGET[str(target["targetDensity"])]
        ranked = sorted(source, key=lambda pair: (
            -_section_score(expected_section, str(pair[0].get("sourceSection", "body"))),
            -float(pair[0].get("confidence", pair[0].get("qualityScore", 0.0))),
            abs(float(pair[0].get("density", 0.0)) - density_target),
            str(pair[0].get("id")),
        ))
        limit = max(64, maximum * 8)
        pool = ranked[:limit]
        audit = {
            "sourcePoolCount": len(source), "fastRetrievedCount": len(pool),
            "fastOmittedCount": max(0, len(source) - len(pool)),
            "poolHash": sha256(_canonical([item[0].get("id") for item in pool])).hexdigest(),
            "meter": meter, "expectedSection": expected_section,
            "sourceRoles": ["factory-strum"] if role == "guitar" else list(_SOURCE_ROLES.get(role, ())),
        }
        return pool, audit

    def _evaluate(
        self, pattern: Mapping[str, Any], source_kind: str, node: Mapping[str, Any],
        target: Mapping[str, Any], role: str, meter: str, tempo: float,
        excluded: set[str], related_drum_ids: set[str],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        pattern_id = str(pattern.get("id", ""))
        reasons: list[str] = []
        actual_role = "factory-strum" if source_kind == "FACTORY_STRUMMING" else str(pattern.get("role", ""))
        if not _STABLE_ID.fullmatch(pattern_id):
            reasons.append("INVALID_STABLE_PATTERN_ID")
        if pattern_id in excluded:
            reasons.append("EXCLUDED_BY_USER")
        if str(pattern.get("meter")) != meter:
            reasons.append("METER_MISMATCH")
        role_fit = _role_score(role, actual_role, source_kind)
        if role_fit <= 0:
            reasons.append("ROLE_AUTHORITY_MISMATCH")
        pitch_mode = str(pattern.get("pitchMode", ""))
        allowed_pitch = {"guitar": {"factory-strum-relative-voicing"},
                         "drums": {"absolute-drum-note"},
                         "percussion": {"absolute-drum-note"}}
        if role in allowed_pitch:
            if pitch_mode not in allowed_pitch[role]:
                reasons.append("PITCH_MODE_MISMATCH")
        elif pitch_mode != "semitones-from-local-root":
            reasons.append("ABSOLUTE_HARMONIC_PITCH_FORBIDDEN")
        if source_kind == "GOLD_PERFORMANCE" and _gold_authority_violation(pattern):
            reasons.append("GOLD_AUTHORITY_VIOLATION")
        if role == "guitar" and source_kind != "FACTORY_STRUMMING":
            reasons.append("GUITAR_REQUIRES_FACTORY_STRUMMING")
        metrics = _pattern_metrics(pattern)
        events = metrics["events"]
        if not events:
            reasons.append("EMPTY_PATTERN")
        confidence = _clamp(float(pattern.get("confidence", pattern.get("qualityScore", 0.0))))
        if confidence < 0.50:
            reasons.append("INSUFFICIENT_EVIDENCE")
        section_fit = _section_score(_source_section(node), str(pattern.get("sourceSection", "body")))
        if section_fit < 0.40:
            reasons.append("SECTION_INCOMPATIBLE")
        low, high = metrics["register"]
        band = next(item for item in node["registerPlan"] if item["role"] == role)
        if high < low or high - low > int(band["high"]) - int(band["low"]):
            reasons.append("REGISTER_SPAN_OVERFLOW")
        peak = metrics["peak"]
        budget = int(node["polyphonyBudget"]["maximumConcurrentMidiNotes"])
        if peak > budget:
            reasons.append("POLYPHONY_OVERFLOW")
        length = metrics["length"]
        operations, transformations = _length_operations(length, int(node["bars"]), role)
        if operations > int(node["transformationBudget"]["maximumOperations"]):
            reasons.append("TRANSFORMATION_BUDGET_OVERFLOW")
        if reasons:
            return None, {"patternId": pattern_id, "sourceKind": source_kind,
                          "reasons": sorted(set(reasons))}

        density = metrics["density"]
        relation_fit = 0.55
        if role == "bass" and related_drum_ids:
            relation_fit = 1.0 if any((drum_id, pattern_id) in self.relationship_by_pair
                                      for drum_id in related_drum_ids) else 0.35
        criteria = {
            "tempoMatch": _tempo_score(tempo, pattern.get("tempoRange", [])),
            "sectionMatch": section_fit,
            "densityMatch": _density_score(str(target["targetDensity"]), density),
            "energyMatch": _energy_score(int(target["targetEnergy"]), density, pattern),
            "lengthMatch": 1.0 if operations == 0 else 0.86 if operations == 1 else 0.68,
            "registerFit": _clamp(1.0 - (high - low) / max(1, int(band["high"]) - int(band["low"])) * 0.35),
            "polyphonyHeadroom": _clamp(1.0 - peak / max(1, budget) * 0.55),
            "confidenceEvidence": confidence,
            "occurrenceEvidence": _clamp(math.log10(1 + float(pattern.get("occurrences", len(pattern.get("sourceProof", []))))) / 3.5),
            "transitionCompatibility": _transition_score(node, pattern),
            "transformationEconomy": _clamp(1.0 - operations / max(1, int(node["transformationBudget"]["maximumOperations"]))),
            "motifCompatibility": _motif_score(node, pattern),
            "relationshipCompatibility": relation_fit,
            "authorityCompliance": 1.0,
        }
        criteria = {key: round(_clamp(criteria[key]), 6) for key in CRITERIA}
        weights = {
            "tempoMatch": 0.08, "sectionMatch": 0.10, "densityMatch": 0.09,
            "energyMatch": 0.09, "lengthMatch": 0.07, "registerFit": 0.08,
            "polyphonyHeadroom": 0.08, "confidenceEvidence": 0.09,
            "occurrenceEvidence": 0.05, "transitionCompatibility": 0.08,
            "transformationEconomy": 0.07, "motifCompatibility": 0.05,
            "relationshipCompatibility": 0.05, "authorityCompliance": 0.02,
        }
        score = round(sum(criteria[key] * weights[key] for key in CRITERIA), 6)
        proof = metrics["proof"]
        source_ids = metrics["sourceIds"]
        candidate = {
            "candidateId": "cand-" + _stable_hash((node["marker"], role, source_kind, pattern_id))[:20],
            "patternId": pattern_id, "sourceKind": source_kind,
            "patternRole": actual_role, "score": score, "criteria": criteria,
            "hardConstraintsPassed": True, "rejectionReasons": [],
            "transformations": transformations, "transformationOperations": operations,
            "lengthBars": length, "density": round(density, 6),
            "polyphonyPeak": peak, "register": {"low": low, "high": high},
            "sourceSection": str(pattern.get("sourceSection", "body")),
            "rhythmSignature": metrics["rhythmSignature"],
            "provenance": {"sourceProofCount": len(proof), "sourceIds": source_ids},
            "candidateHash": "",
        }
        candidate["candidateHash"] = _hash_without(candidate, "candidateHash")
        return candidate, None

    def search_request(
        self, node: Mapping[str, Any], target: Mapping[str, Any], role: str,
        song_map: Mapping[str, Any], controls: CandidateSearchControls,
        related_drum_ids: set[str],
    ) -> dict[str, Any]:
        request_id = f"{node['marker']}:{role}"
        if node["locked"] or node["decision"] == "KEEP":
            request = {
                "requestId": request_id, "marker": node["marker"], "role": role,
                "status": "KEEP", "lockedByGraph": True,
                "retrieval": {"sourcePoolCount": 0, "fastRetrievedCount": 0,
                              "fastOmittedCount": 0, "poolHash": sha256(b"").hexdigest(),
                              "meter": _meter_for_node(song_map, node),
                              "expectedSection": _source_section(node), "sourceRoles": []},
                "rankedCandidates": [], "rejectedCandidates": [],
                "hardPassedCount": 0, "hardRejectedCount": 0,
                "rankedOmittedCount": 0, "requestHash": "",
            }
            request["requestHash"] = _hash_without(request, "requestHash")
            return request
        if role == "solo":
            request = {
                "requestId": request_id, "marker": node["marker"], "role": role,
                "status": "KEEP_ORIGINAL_SOLO", "lockedByGraph": False,
                "retrieval": {"sourcePoolCount": 0, "fastRetrievedCount": 0,
                              "fastOmittedCount": 0, "poolHash": sha256(b"").hexdigest(),
                              "meter": _meter_for_node(song_map, node),
                              "expectedSection": _source_section(node), "sourceRoles": []},
                "rankedCandidates": [], "rejectedCandidates": [],
                "hardPassedCount": 0, "hardRejectedCount": 0,
                "rankedOmittedCount": 0, "requestHash": "",
            }
            request["requestHash"] = _hash_without(request, "requestHash")
            return request
        meter, tempo = _meter_for_node(song_map, node), _tempo_for_node(song_map, node)
        pool, retrieval = self._fast_retrieve(node, target, role, meter, controls.max_candidates_per_request)
        accepted, rejected = [], []
        excluded = set(controls.excluded_pattern_ids)
        for pattern, source_kind in pool:
            candidate, rejection = self._evaluate(
                pattern, source_kind, node, target, role, meter, tempo, excluded, related_drum_ids
            )
            if candidate is not None:
                accepted.append(candidate)
            elif rejection is not None:
                rejected.append(rejection)
        accepted.sort(key=lambda item: (-float(item["score"]), item["patternId"]))
        returned = accepted[:controls.max_candidates_per_request]
        for rank, candidate in enumerate(returned, 1):
            candidate["rank"] = rank
            candidate["candidateHash"] = _hash_without(candidate, "candidateHash")
        status = "READY" if returned else "MANUAL_REVIEW"
        request = {
            "requestId": request_id, "marker": node["marker"], "role": role,
            "status": status, "lockedByGraph": False, "retrieval": retrieval,
            "rankedCandidates": returned, "rejectedCandidates": rejected,
            "hardPassedCount": len(accepted), "hardRejectedCount": len(rejected),
            "rankedOmittedCount": max(0, len(accepted) - len(returned)), "requestHash": "",
        }
        request["requestHash"] = _hash_without(request, "requestHash")
        return request


def _selection_fragment(selection: Mapping[str, Any]) -> str:
    return _hash_without(selection, "fragmentHash")


def _previous_selection(
    previous: Mapping[str, Any], variant_id: str, request_id: str,
) -> dict[str, Any] | None:
    variant = next((item for item in previous["variants"] if item["variantId"] == variant_id), None)
    if variant is None:
        return None
    selected = next((item for item in variant["selections"] if item["requestId"] == request_id), None)
    return deepcopy(selected) if selected is not None else None


def _select_variants(
    requests: Sequence[Mapping[str, Any]], relationship_by_pair: Mapping[tuple[str, str], str],
    seed: int, variant_count: int, controls: CandidateSearchControls,
    previous: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lock_map = {(marker, role): pattern_id for marker, role, pattern_id in controls.locked_selections}
    offsets = dict(controls.next_candidate_offsets)
    regenerate = set(controls.regenerate_markers)
    use_across_variants: Counter[tuple[str, str]] = Counter()
    variants: list[dict[str, Any]] = []
    preserved_hashes: list[str] = []
    marker_order = {marker: index for index, marker in enumerate(ELEMENTS)}
    ordered = sorted(requests, key=lambda item: (marker_order[item["marker"]], ROLES.index(item["role"])))
    for variant_index in range(variant_count):
        variant_id = f"variant-{VARIANT_LABELS[variant_index]}"
        selections: list[dict[str, Any]] = []
        selected_by_request: dict[str, dict[str, Any]] = {}
        use_within_variant: Counter[str] = Counter()
        last_by_role: dict[str, dict[str, Any]] = {}
        for request in ordered:
            request_id, marker, role = request["requestId"], request["marker"], request["role"]
            if previous is not None and regenerate and marker not in regenerate:
                preserved = _previous_selection(previous, variant_id, request_id)
                if preserved is None:
                    raise ValueError("Previous CandidateSet lacks a selection required for partial regeneration")
                selections.append(preserved)
                selected_by_request[request_id] = preserved
                preserved_hashes.append(preserved["fragmentHash"])
                if preserved["patternId"]:
                    use_within_variant[preserved["patternId"]] += 1
                    use_across_variants[(request_id, preserved["patternId"])] += 1
                    last_by_role[role] = preserved
                continue
            candidates = list(request["rankedCandidates"])
            if request["status"] in {"KEEP", "KEEP_ORIGINAL_SOLO"}:
                selection = {
                    "requestId": request_id, "marker": marker, "role": role,
                    "candidateId": None, "patternId": None, "sourceKind": None,
                    "score": 1.0, "baseScore": 1.0, "diversityPenalty": 0.0,
                    "relationshipId": None, "selectionMode": request["status"],
                    "locked": True, "fragmentHash": "",
                }
                selection["fragmentHash"] = _selection_fragment(selection)
            elif not candidates:
                selection = {
                    "requestId": request_id, "marker": marker, "role": role,
                    "candidateId": None, "patternId": None, "sourceKind": None,
                    "score": 0.0, "baseScore": 0.0, "diversityPenalty": 0.0,
                    "relationshipId": None, "selectionMode": "MANUAL_REVIEW",
                    "locked": False, "fragmentHash": "",
                }
                selection["fragmentHash"] = _selection_fragment(selection)
            else:
                locked_pattern = lock_map.get((marker, role))
                if locked_pattern is not None:
                    candidate = next((item for item in candidates if item["patternId"] == locked_pattern), None)
                    if candidate is None:
                        raise ValueError(f"Locked pattern {locked_pattern} is not eligible for {request_id}")
                    scored = [(float(candidate["score"]), 0.0, None, candidate)]
                    mode = "LOCKED_BY_USER"
                else:
                    scored = []
                    drum = selected_by_request.get(f"{marker}:drums")
                    for candidate in candidates:
                        penalty = 0.14 * use_within_variant[candidate["patternId"]]
                        penalty += 0.22 * use_across_variants[(request_id, candidate["patternId"])]
                        relation_id = None
                        relation_bonus = 0.0
                        if role == "bass" and drum and drum["patternId"]:
                            relation_id = relationship_by_pair.get((drum["patternId"], candidate["patternId"]))
                            relation_bonus = 0.25 if relation_id else -0.10
                        motif_bonus = 0.0
                        previous_role = last_by_role.get(role)
                        if previous_role and previous_role.get("rhythmSignature") == candidate["rhythmSignature"]:
                            motif_bonus = 0.04
                        jitter = int(_stable_hash((seed, variant_id, request_id, candidate["patternId"]))[:8], 16) / 0xFFFFFFFF
                        adjusted = float(candidate["score"]) - penalty + relation_bonus + motif_bonus + jitter * 0.012
                        scored.append((adjusted, penalty, relation_id, candidate))
                    scored.sort(key=lambda item: (-item[0], item[3]["patternId"]))
                    mode = "NEXT_CANDIDATE" if offsets.get(request_id, 0) else "DIVERSE_BEST"
                offset = offsets.get(request_id, 0)
                chosen = scored[min(offset, len(scored) - 1)]
                adjusted, penalty, relation_id, candidate = chosen
                selection = {
                    "requestId": request_id, "marker": marker, "role": role,
                    "candidateId": candidate["candidateId"], "patternId": candidate["patternId"],
                    "sourceKind": candidate["sourceKind"], "score": round(_clamp(adjusted), 6),
                    "baseScore": candidate["score"], "diversityPenalty": round(penalty, 6),
                    "relationshipId": relation_id, "selectionMode": mode,
                    "locked": locked_pattern is not None, "rhythmSignature": candidate["rhythmSignature"],
                    "fragmentHash": "",
                }
                selection["fragmentHash"] = _selection_fragment(selection)
                use_within_variant[candidate["patternId"]] += 1
                use_across_variants[(request_id, candidate["patternId"])] += 1
                last_by_role[role] = selection
            selections.append(selection)
            selected_by_request[request_id] = selection
        scored_selections = [item["score"] for item in selections if item["patternId"] is not None]
        variant = {
            "variantId": variant_id, "planVariantId": "",
            "selections": selections,
            "score": round(sum(scored_selections) / max(1, len(scored_selections)), 6),
            "diversityDistanceFromA": 0.0, "variantHash": "",
        }
        if variants:
            first = {item["requestId"]: item["patternId"] for item in variants[0]["selections"]}
            comparable = [item for item in selections if item["patternId"] is not None]
            changed = sum(first.get(item["requestId"]) != item["patternId"] for item in comparable)
            variant["diversityDistanceFromA"] = round(changed / max(1, len(comparable)), 6)
        variants.append(variant)
    return variants, {
        "enabled": previous is not None and bool(regenerate),
        "previousCandidateSetHash": previous["candidateSetHash"] if previous is not None else None,
        "regeneratedMarkers": sorted(regenerate),
        "preservedMarkers": [marker for marker in ELEMENTS if previous is not None and regenerate and marker not in regenerate],
        "preservedSelectionCount": len(preserved_hashes),
        "preservedFragmentHashes": sorted(preserved_hashes),
    }


def build_candidate_set(
    arrangement_graph: Mapping[str, Any], song_map: Mapping[str, Any], root: Path,
    plan_variant_id: str = "plan-01", seed: int = 0, variant_count: int = 3,
    controls: Mapping[str, Any] | None = None,
    previous_candidate_set: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validate_arrangement_graph_v2(arrangement_graph)
    validate_song_map_v2(song_map)
    if arrangement_graph["source"]["songMapHash"] != song_map["mapHash"]:
        raise ValueError("ArrangementGraph and SongMap hashes do not match")
    if arrangement_graph["readyForCandidateSearch"] is not True:
        raise ValueError("ArrangementGraph manual review must be resolved before Candidate Search")
    if plan_variant_id not in {item["variantId"] for item in arrangement_graph["planVariants"]}:
        raise ValueError("Unknown ArrangementGraph plan variant")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**31 - 1:
        raise ValueError("Candidate Search seed must be in range 0..2147483647")
    if isinstance(variant_count, bool) or not isinstance(variant_count, int) or not 2 <= variant_count <= 4:
        raise ValueError("Candidate Search variant count must be 2..4")
    normalized_controls = CandidateSearchControls.from_mapping(controls)
    if previous_candidate_set is not None:
        validate_candidate_set_v2(previous_candidate_set)
        if previous_candidate_set["source"]["graphHash"] != arrangement_graph["graphHash"]:
            raise ValueError("Previous CandidateSet belongs to another ArrangementGraph")
        if previous_candidate_set["source"]["planVariantId"] != plan_variant_id:
            raise ValueError("Previous CandidateSet uses another plan variant")
        if not normalized_controls.regenerate_markers:
            raise ValueError("Previous CandidateSet requires explicit regenerateMarkers")

    engine = CandidateSearchEngine(root)
    graph_variant = next(item for item in arrangement_graph["planVariants"]
                         if item["variantId"] == plan_variant_id)
    targets = {item["marker"]: item for item in graph_variant["elementTargets"]}
    requests: list[dict[str, Any]] = []
    drum_ids: dict[str, set[str]] = defaultdict(set)
    for node in arrangement_graph["nodes"]:
        for role in node["roles"]:
            request = engine.search_request(
                node, targets[node["marker"]], role, song_map, normalized_controls,
                drum_ids[node["marker"]],
            )
            requests.append(request)
            if role == "drums":
                drum_ids[node["marker"]] = {
                    item["patternId"] for item in request["rankedCandidates"]
                }
    variants, partial = _select_variants(
        requests, engine.relationship_by_pair, seed, variant_count,
        normalized_controls, previous_candidate_set,
    )
    for variant in variants:
        variant["planVariantId"] = plan_variant_id
        variant["variantHash"] = _hash_without(variant, "variantHash")
    reviews = [
        {"requestId": item["requestId"], "code": "NO_ELIGIBLE_PRODUCTION_PATTERN",
         "reason": "No production pattern passed every hard constraint."}
        for item in requests if item["status"] == "MANUAL_REVIEW"
    ]
    reason_counts = Counter(
        reason for request in requests for item in request["rejectedCandidates"] for reason in item["reasons"]
    )
    hard_rejected = sum(item["hardRejectedCount"] for item in requests)
    result = {
        "schema": CANDIDATE_SET_SCHEMA, "version": CANDIDATE_SET_VERSION,
        "source": {
            "graphHash": arrangement_graph["graphHash"], "songMapHash": song_map["mapHash"],
            "sourceMidiSha256": song_map["sourceSha256"], "planVariantId": plan_variant_id,
            "seed": seed, "variantCount": variant_count,
        },
        "registries": engine.registry_manifest,
        "controls": normalized_controls.to_manifest(),
        "requests": requests, "variants": variants,
        "partialRegeneration": partial,
        "audit": {
            "productionPatternCount": len(engine.gold_patterns) + len(engine.strum_patterns),
            "requestCount": len(requests),
            "fastRetrievedCount": sum(item["retrieval"]["fastRetrievedCount"] for item in requests),
            "hardPassedCount": sum(item["hardPassedCount"] for item in requests),
            "hardRejectedCount": hard_rejected,
            "rankedReturnedCount": sum(len(item["rankedCandidates"]) for item in requests),
            "rankedOmittedCount": sum(item["rankedOmittedCount"] for item in requests),
            "rejectionReasonCounts": dict(sorted(reason_counts.items())),
            "allDetailedRejectionsAudited": hard_rejected == sum(
                len(item["rejectedCandidates"]) for item in requests
            ),
        },
        "manualReview": reviews,
        "readyForVariantRendering": not reviews,
        "safety": {
            "readOnly": True, "midiMutationAllowed": False, "finalMidiGenerated": False,
            "factoryDynamicsAuthority": True, "goldDynamicsAuthority": False,
            "goldBankProgramAuthority": False, "goldGuitarAuthority": False,
            "originalSoloMutationAllowed": False, "hardConstraintsBeforeScoring": True,
            "deviceProfileConfirmed": False,
        },
        "candidateSetHash": "",
    }
    result["candidateSetHash"] = _hash_without(result, "candidateSetHash")
    validate_candidate_set_v2(result)
    return result


def validate_candidate_set_v2(value: Mapping[str, Any]) -> None:
    root = {"schema", "version", "source", "registries", "controls", "requests", "variants",
            "partialRegeneration", "audit", "manualReview", "readyForVariantRendering",
            "safety", "candidateSetHash"}
    if not isinstance(value, Mapping) or set(value) != root:
        raise ValueError("CandidateSet 2.0 root fields mismatch")
    if value["schema"] != CANDIDATE_SET_SCHEMA or value["version"] != CANDIDATE_SET_VERSION:
        raise ValueError("Unsupported CandidateSet schema/version")
    source = value["source"]
    if set(source) != {"graphHash", "songMapHash", "sourceMidiSha256", "planVariantId", "seed", "variantCount"}:
        raise ValueError("CandidateSet source fields mismatch")
    if any(not re.fullmatch(r"[0-9a-f]{64}", str(source[key]))
           for key in ("graphHash", "songMapHash", "sourceMidiSha256")):
        raise ValueError("CandidateSet source hash is invalid")
    if not re.fullmatch(r"plan-0[1-4]", str(source["planVariantId"])):
        raise ValueError("CandidateSet plan variant ID is invalid")
    if isinstance(source["seed"], bool) or not isinstance(source["seed"], int) or not 0 <= source["seed"] <= 2**31 - 1:
        raise ValueError("CandidateSet seed is invalid")
    if isinstance(source["variantCount"], bool) or not 2 <= source["variantCount"] <= 4:
        raise ValueError("CandidateSet variant count is invalid")
    if set(value["registries"]) != {"factoryStrumming", "goldPerformance"}:
        raise ValueError("CandidateSet registry provenance is incomplete")
    for item in value["registries"].values():
        if set(item) != {"path", "schema", "version", "databaseVersion", "sha256"} or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValueError("CandidateSet registry manifest is invalid")
    controls = CandidateSearchControls.from_mapping(value["controls"])
    request_keys = {"requestId", "marker", "role", "status", "lockedByGraph", "retrieval",
                    "rankedCandidates", "rejectedCandidates", "hardPassedCount",
                    "hardRejectedCount", "rankedOmittedCount", "requestHash"}
    candidate_keys = {"candidateId", "patternId", "sourceKind", "patternRole", "score", "criteria",
                      "hardConstraintsPassed", "rejectionReasons", "transformations",
                      "transformationOperations", "lengthBars", "density", "polyphonyPeak", "register",
                      "sourceSection", "rhythmSignature", "provenance", "candidateHash", "rank"}
    requests = value["requests"]
    if not isinstance(requests, list) or len({item["requestId"] for item in requests}) != len(requests):
        raise ValueError("CandidateSet request IDs are invalid")
    request_map = {}
    for request in requests:
        if set(request) != request_keys or request["requestId"] != f"{request['marker']}:{request['role']}":
            raise ValueError("CandidateSet request fields mismatch")
        if request["marker"] not in ELEMENTS or request["role"] not in ROLES:
            raise ValueError("CandidateSet request marker/role is invalid")
        if request["status"] not in {"READY", "KEEP", "KEEP_ORIGINAL_SOLO", "MANUAL_REVIEW"}:
            raise ValueError("CandidateSet request status is invalid")
        if request["hardRejectedCount"] != len(request["rejectedCandidates"]):
            raise ValueError("CandidateSet rejection audit count mismatch")
        if [item["rank"] for item in request["rankedCandidates"]] != list(range(1, len(request["rankedCandidates"]) + 1)):
            raise ValueError("CandidateSet ranks are invalid")
        for candidate in request["rankedCandidates"]:
            if set(candidate) != candidate_keys or candidate["sourceKind"] not in SOURCE_KINDS:
                raise ValueError("CandidateSet candidate fields mismatch")
            if not _STABLE_ID.fullmatch(candidate["patternId"]) or candidate["hardConstraintsPassed"] is not True or candidate["rejectionReasons"]:
                raise ValueError("CandidateSet contains an ineligible ranked candidate")
            if set(candidate["criteria"]) != set(CRITERIA) or any(not 0 <= score <= 1 for score in candidate["criteria"].values()):
                raise ValueError("CandidateSet scoring criteria are invalid")
            if candidate["candidateHash"] != _hash_without(candidate, "candidateHash"):
                raise ValueError("CandidateSet candidate hash mismatch")
            if request["role"] == "guitar" and candidate["sourceKind"] != "FACTORY_STRUMMING":
                raise ValueError("CandidateSet guitar selection is not Factory strumming")
            if request["role"] != "guitar" and candidate["sourceKind"] != "GOLD_PERFORMANCE":
                raise ValueError("CandidateSet non-guitar selection source is invalid")
        if request["requestHash"] != _hash_without(request, "requestHash"):
            raise ValueError("CandidateSet request hash mismatch")
        request_map[request["requestId"]] = request
    selection_keys_base = {"requestId", "marker", "role", "candidateId", "patternId", "sourceKind",
                           "score", "baseScore", "diversityPenalty", "relationshipId",
                           "selectionMode", "locked", "fragmentHash"}
    variants = value["variants"]
    if len(variants) != source["variantCount"]:
        raise ValueError("CandidateSet variant count mismatch")
    lock_map = {(marker, role): pattern_id for marker, role, pattern_id in controls.locked_selections}
    for index, variant in enumerate(variants):
        if set(variant) != {"variantId", "planVariantId", "selections", "score", "diversityDistanceFromA", "variantHash"}:
            raise ValueError("CandidateSet variant fields mismatch")
        if variant["variantId"] != f"variant-{VARIANT_LABELS[index]}" or variant["planVariantId"] != source["planVariantId"]:
            raise ValueError("CandidateSet variant identity is invalid")
        if {item["requestId"] for item in variant["selections"]} != set(request_map):
            raise ValueError("CandidateSet variant selections are incomplete")
        for selection in variant["selections"]:
            allowed_keys = selection_keys_base | ({"rhythmSignature"} if selection["patternId"] is not None else set())
            if set(selection) != allowed_keys or selection["fragmentHash"] != _selection_fragment(selection):
                raise ValueError("CandidateSet selection fields/hash mismatch")
            request = request_map[selection["requestId"]]
            if (selection["marker"], selection["role"]) != (request["marker"], request["role"]):
                raise ValueError("CandidateSet selection request mapping mismatch")
            if selection["patternId"] is not None and selection["patternId"] not in {
                    item["patternId"] for item in request["rankedCandidates"]}:
                raise ValueError("CandidateSet selection does not reference an eligible candidate")
            locked_pattern = lock_map.get((selection["marker"], selection["role"]))
            if locked_pattern is not None and (selection["patternId"] != locked_pattern or selection["locked"] is not True):
                raise ValueError("CandidateSet user lock was not preserved")
        if variant["variantHash"] != _hash_without(variant, "variantHash"):
            raise ValueError("CandidateSet variant hash mismatch")
    partial = value["partialRegeneration"]
    if set(partial) != {"enabled", "previousCandidateSetHash", "regeneratedMarkers", "preservedMarkers",
                       "preservedSelectionCount", "preservedFragmentHashes"}:
        raise ValueError("CandidateSet partial regeneration fields mismatch")
    audit = value["audit"]
    audit_keys = {"productionPatternCount", "requestCount", "fastRetrievedCount", "hardPassedCount",
                  "hardRejectedCount", "rankedReturnedCount", "rankedOmittedCount",
                  "rejectionReasonCounts", "allDetailedRejectionsAudited"}
    if set(audit) != audit_keys or audit["requestCount"] != len(requests):
        raise ValueError("CandidateSet audit fields mismatch")
    if audit["hardRejectedCount"] != sum(len(item["rejectedCandidates"]) for item in requests) or audit["allDetailedRejectionsAudited"] is not True:
        raise ValueError("CandidateSet rejection audit is incomplete")
    expected_safety = {
        "readOnly": True, "midiMutationAllowed": False, "finalMidiGenerated": False,
        "factoryDynamicsAuthority": True, "goldDynamicsAuthority": False,
        "goldBankProgramAuthority": False, "goldGuitarAuthority": False,
        "originalSoloMutationAllowed": False, "hardConstraintsBeforeScoring": True,
        "deviceProfileConfirmed": False,
    }
    if value["safety"] != expected_safety:
        raise ValueError("CandidateSet safety contract was weakened")
    if value["readyForVariantRendering"] != (not value["manualReview"]):
        raise ValueError("CandidateSet readiness disagrees with manual review")
    if value["candidateSetHash"] != _hash_without(value, "candidateSetHash"):
        raise ValueError("CandidateSet hash mismatch")


def execute_candidate_search_api(payload: Mapping[str, Any], root: Path) -> dict[str, Any]:
    allowed = {"arrangementGraph", "songMap", "planVariantId", "seed", "variantCount",
               "controls", "previousCandidateSet"}
    if not isinstance(payload, Mapping) or set(payload) - allowed:
        raise ValueError("Unknown Candidate Search API fields")
    if "arrangementGraph" not in payload or "songMap" not in payload:
        raise ValueError("Candidate Search API requires arrangementGraph and songMap")
    return build_candidate_set(
        payload["arrangementGraph"], payload["songMap"], root,
        str(payload.get("planVariantId", "plan-01")), payload.get("seed", 0),
        payload.get("variantCount", 3), payload.get("controls"), payload.get("previousCandidateSet"),
    )


def execute_candidate_search_gui(payload: Mapping[str, Any], root: Path) -> dict[str, Any]:
    return execute_candidate_search_api(payload, root)