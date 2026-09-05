"""Session 21 deterministic ArrangementGraph 2.0 planner.

The planner joins a validated SongMap 2.0 and ProducerBrief 2.0 into a
read-only global plan for all Pa800 Style elements.  It cannot select runtime
patterns or mutate MIDI.  Harmony, register, transition, lock and full-note
polyphony constraints are explicit so Session 22 can search candidates only
after this graph passes validation.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

from .producer_brief import ELEMENTS, ROLES, validate_producer_brief_v2
from .song_understanding import validate_song_map_v2


GRAPH_SCHEMA = "dna-premium-arrangement-graph"
GRAPH_VERSION = "2.0"
MAX_CONCURRENT_MIDI_NOTES = 54
ELEMENT_TYPES = ("intro", "variation", "fill", "ending")
DENSITIES = ("sparse", "balanced", "full")
DECISIONS = ("PLAN", "KEEP", "MANUAL_REVIEW")
TRANSITION_TYPES = ("direct", "fill", "ending")
OBLIGATIONS = (
    "motif-continuity", "pickup", "crash-accent", "bass-approach",
    "harmonic-anticipation", "ending-cadence",
)
STRATEGIES = ("coherent-rise", "spacious-rise", "impact-rise", "motif-forward")

_MARKER_SPECS = (
    ("i1cv1", "intro", "intro", "introduce"),
    ("i2cv1", "intro", "intro", "foreshadow"),
    ("v1cv1", "variation", "verse", "establish"),
    ("v2cv1", "variation", "bridge", "develop"),
    ("v3cv1", "variation", "chorus", "intensify"),
    ("v4cv1", "variation", "chorus", "peak"),
    ("f1cv1", "fill", "verse", "bridge"),
    ("f2cv1", "fill", "chorus", "launch"),
    ("e1cv1", "ending", "ending", "resolve"),
    ("e2cv1", "ending", "ending", "final-resolve"),
)
_EDGE_SPECS = (
    ("i1cv1", "v1cv1", "direct"),
    ("i2cv1", "v1cv1", "direct"),
    ("v1cv1", "f1cv1", "fill"),
    ("f1cv1", "v2cv1", "direct"),
    ("v2cv1", "v3cv1", "direct"),
    ("v3cv1", "f2cv1", "fill"),
    ("f2cv1", "v4cv1", "direct"),
    ("v4cv1", "e1cv1", "ending"),
    ("v4cv1", "e2cv1", "ending"),
)
_ROLE_ORDER = {role: index for index, role in enumerate(ROLES)}
_ROLE_BANDS = {
    "drums": (35, 81), "percussion": (35, 81), "bass": (28, 60),
    "guitar": (40, 84), "accompaniment": (48, 84), "riff": (48, 88),
    "solo": (55, 96), "pad": (48, 88),
}
_ROLE_POLYPHONY = {
    "drums": 12, "percussion": 8, "bass": 1, "guitar": 4,
    "accompaniment": 4, "riff": 2, "solo": 1, "pad": 4,
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return sha256(_canonical({key: item for key, item in value.items() if key != field})).hexdigest()


def _clamp(value: int, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(value)))


def _density_shift(value: str, delta: int) -> str:
    return DENSITIES[_clamp(DENSITIES.index(value) + delta, 0, len(DENSITIES) - 1)]


def _energy_by_section(brief: Mapping[str, Any]) -> dict[str, int]:
    return {item["section"]: int(item["value"]) for item in brief["intent"]["energyCurve"]}


def _variation_energy(curve: Mapping[str, int]) -> list[int]:
    raw = [curve["verse"] - 8, round((curve["verse"] + curve["bridge"]) / 2),
           max(curve["chorus"] - 5, curve["bridge"] + 4), curve["chorus"] + 8]
    result = [_clamp(raw[0], 0, 85)]
    for value in raw[1:]:
        result.append(_clamp(max(value, result[-1] + 4)))
    return result


def _target_energies(brief: Mapping[str, Any]) -> dict[str, int]:
    curve = _energy_by_section(brief)
    variations = _variation_energy(curve)
    return {
        "i1cv1": _clamp(curve["intro"] - 8),
        "i2cv1": _clamp(curve["intro"] + 8),
        "v1cv1": variations[0], "v2cv1": variations[1],
        "v3cv1": variations[2], "v4cv1": variations[3],
        "f1cv1": _clamp(variations[1] + 8),
        "f2cv1": _clamp(variations[3] + 6),
        "e1cv1": _clamp(curve["ending"] + 8),
        "e2cv1": _clamp(curve["ending"] - 5),
    }


def _phrase_density(song_map: Mapping[str, Any], section_id: str) -> float:
    values = [float(item["density"]) for item in song_map["phrases"]
              if item.get("sectionId") == section_id]
    return sum(values) / len(values) if values else 0.0


def _section_for_label(song_map: Mapping[str, Any], label: str) -> tuple[Mapping[str, Any], bool, str]:
    sections = list(song_map["sections"])
    exact = [item for item in sections if item.get("label") == label]
    if exact:
        selected = max(exact, key=lambda item: (float(item.get("confidence", 0)), -int(item["startTick"])))
        return selected, True, "exact-section-label"
    if label == "intro":
        return sections[0], False, "first-section-fallback"
    if label == "ending":
        return sections[-1], False, "last-section-fallback"
    if label == "chorus":
        selected = max(sections, key=lambda item: (_phrase_density(song_map, str(item["id"])),
                                                   float(item.get("confidence", 0))))
        return selected, False, "highest-density-section-fallback"
    non_edges = [item for item in sections if item.get("label") not in {"intro", "ending"}]
    return (non_edges[0] if non_edges else sections[0]), False, "nearest-musical-section-fallback"


def _section_bars(song_map: Mapping[str, Any], section: Mapping[str, Any]) -> int:
    count = sum(int(bar["startTick"]) < int(section["endTick"])
                and int(bar["endTick"]) > int(section["startTick"])
                for bar in song_map["bars"])
    return max(1, min(32, count))


def _harmonic_context(song_map: Mapping[str, Any], section: Mapping[str, Any]) -> list[str]:
    symbols = []
    for cell in song_map["chordCells"]:
        if int(cell["startTick"]) < int(section["endTick"]) and int(cell["endTick"]) > int(section["startTick"]):
            symbol = str(cell["symbol"])
            if symbol not in symbols:
                symbols.append(symbol)
    return symbols[:12] or ["N.C."]


def _node_density(base: str, energy: int, element_type: str) -> str:
    shift = -1 if energy < 35 else 1 if energy >= 78 else 0
    if element_type == "fill" and energy >= 55:
        shift += 1
    return _density_shift(base, shift)


def _roles_for_node(brief: Mapping[str, Any], density: str, element_type: str,
                    energy: int) -> list[str]:
    base = {
        "sparse": ["drums", "bass", "accompaniment"],
        "balanced": ["drums", "bass", "guitar", "accompaniment", "pad"],
        "full": ["drums", "percussion", "bass", "guitar", "accompaniment", "riff", "pad"],
    }[density]
    if element_type == "fill":
        base = [role for role in base if role in {"drums", "percussion", "bass", "riff"}]
    if element_type == "intro" and energy < 30:
        base = [role for role in base if role != "drums"]
    required = list(brief["intent"]["requiredRoles"])
    forbidden = set(brief["intent"]["forbiddenRoles"])
    roles = (set(base) | set(required)) - forbidden
    if brief["intent"]["soloTreatment"] == "no-new-layers" and "solo" not in required:
        roles.discard("solo")
    return sorted(roles, key=lambda role: _ROLE_ORDER[role])


def _polyphony_budget(roles: Sequence[str], energy: int) -> dict[str, Any]:
    raw = sum(_ROLE_POLYPHONY[role] for role in roles)
    peak = max(1, min(MAX_CONCURRENT_MIDI_NOTES, round(raw * (0.62 + energy / 250))))
    return {
        "maximumConcurrentMidiNotes": peak,
        "estimatedVoiceCostUnits": peak * 2,
        "voiceCostStatus": "SOFTWARE_ESTIMATE_ONLY",
        "deviceConfirmed": False,
    }


def _transformation_budget(roles: Sequence[str], bars: int, tolerance: int,
                           locked: bool) -> dict[str, Any]:
    if locked:
        return {"maximumOperations": 0, "maximumAddedNotes": 0,
                "tolerance": tolerance, "locked": True}
    scale = tolerance / 100
    return {
        "maximumOperations": round((4 + len(roles) * bars) * scale),
        "maximumAddedNotes": round(len(roles) * bars * 4 * scale),
        "tolerance": tolerance, "locked": False,
    }


def _manual_review(song_map: Mapping[str, Any]) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    for index, item in enumerate(song_map["manualReview"][:100], 1):
        reasons = item.get("reasons", [])
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
        reviews.append({
            "id": f"source-review-{index:03d}", "code": "SONG_MAP_MANUAL_REVIEW",
            "reason": "SongMap contains unresolved musical evidence.",
            "source": "song-map", "marker": None,
            "details": {"sourceId": str(item.get("id", index)),
                        "reasons": [str(reason) for reason in reasons]},
        })
    if float(song_map["confidence"]) < 0.75:
        reviews.append({
            "id": "song-map-confidence", "code": "LOW_SONG_MAP_CONFIDENCE",
            "reason": "SongMap confidence is below the global planning threshold.",
            "source": "song-map", "marker": None,
            "details": {"sourceId": "song-map", "reasons": [str(song_map["confidence"])]},
        })
    if int(song_map["polyphony"]["globalPeak"]) > MAX_CONCURRENT_MIDI_NOTES:
        reviews.append({
            "id": "source-polyphony-overflow", "code": "SOURCE_POLYPHONY_OVERFLOW",
            "reason": "Source full-duration polyphony exceeds the software planning ceiling.",
            "source": "polyphony", "marker": None,
            "details": {"sourceId": "globalPeak",
                        "reasons": [str(song_map["polyphony"]["globalPeak"])]},
        })
    labels = {str(item.get("label")) for item in song_map["sections"]}
    for label in ("verse", "chorus"):
        if label not in labels:
            reviews.append({
                "id": f"missing-{label}", "code": "MISSING_CORE_SECTION",
                "reason": f"No explicit {label} section exists in SongMap.",
                "source": "song-map", "marker": None,
                "details": {"sourceId": label, "reasons": ["section-label-not-found"]},
            })
    return reviews


def _transition_obligations(transition_style: str, edge_type: str) -> list[str]:
    by_style = {
        "subtle": ["motif-continuity", "bass-approach"],
        "balanced": ["motif-continuity", "bass-approach", "harmonic-anticipation", "crash-accent"],
        "dramatic": ["motif-continuity", "pickup", "crash-accent", "bass-approach",
                     "harmonic-anticipation"],
    }
    obligations = list(by_style[transition_style])
    if edge_type == "ending":
        obligations.append("ending-cadence")
    return [item for item in OBLIGATIONS if item in obligations]


def _edge_harmony(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    from_chord = left["harmonicContext"][-1]
    to_chord = right["harmonicContext"][0]
    return {
        "fromChord": from_chord, "toChord": to_chord,
        "requiresAdaptation": from_chord != to_chord,
        "confidence": round(min(float(left["confidence"]), float(right["confidence"])), 4),
    }


def _variant_targets(nodes: Sequence[Mapping[str, Any]], strategy: str,
                     energy_delta: int, density_delta: int) -> list[dict[str, Any]]:
    targets = []
    for node in nodes:
        locked = bool(node["locked"])
        targets.append({
            "marker": node["marker"],
            "targetEnergy": int(node["targetEnergy"]) if locked else _clamp(int(node["targetEnergy"]) + energy_delta),
            "targetDensity": node["targetDensity"] if locked else _density_shift(str(node["targetDensity"]), density_delta),
            "transitionEmphasis": "motif" if strategy == "motif-forward" else
                                  "impact" if strategy == "impact-rise" else
                                  "space" if strategy == "spacious-rise" else "coherence",
            "locked": locked,
        })
    by_marker = {item["marker"]: item for item in targets}
    variation = [by_marker[f"v{index}cv1"] for index in range(1, 5)]
    for index in range(1, len(variation)):
        if variation[index]["targetEnergy"] < variation[index - 1]["targetEnergy"]:
            if not variation[index]["locked"]:
                variation[index]["targetEnergy"] = variation[index - 1]["targetEnergy"]
            elif not variation[index - 1]["locked"]:
                variation[index - 1]["targetEnergy"] = variation[index]["targetEnergy"]
    return targets


def _plan_variants(nodes: Sequence[Mapping[str, Any]], seed: int, count: int) -> list[dict[str, Any]]:
    templates = [
        ("coherent-rise", 0, 0),
        ("spacious-rise", -6, -1),
        ("impact-rise", 6, 1),
        ("motif-forward", 2, 0),
    ]
    alternatives = sorted(templates[1:], key=lambda item: sha256(
        f"{seed}:{item[0]}".encode("ascii")).hexdigest())
    selected = [templates[0], *alternatives[:count - 1]]
    output = []
    for index, (strategy, delta, density_delta) in enumerate(selected, 1):
        variant = {
            "variantId": f"plan-{index:02d}", "strategy": strategy,
            "seedOffset": int(sha256(f"{seed}:{strategy}".encode("ascii")).hexdigest()[:8], 16),
            "energyDelta": delta, "densityDelta": density_delta,
            "elementTargets": _variant_targets(nodes, strategy, delta, density_delta),
            "readOnly": True,
        }
        variant["variantHash"] = _hash_without(variant, "variantHash")
        output.append(variant)
    return output


def build_arrangement_graph(song_map: Mapping[str, Any], producer_brief: Mapping[str, Any],
                            seed: int = 0, variant_count: int = 2) -> dict[str, Any]:
    validate_song_map_v2(song_map)
    validate_producer_brief_v2(producer_brief)
    if producer_brief["readyForPlanning"] is not True:
        raise ValueError("ProducerBrief must be approved before ArrangementGraph planning")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**31 - 1:
        raise ValueError("ArrangementGraph seed must be in range 0..2147483647")
    if isinstance(variant_count, bool) or not isinstance(variant_count, int) or not 2 <= variant_count <= 4:
        raise ValueError("ArrangementGraph variant count must be 2..4")
    required = set(producer_brief["intent"]["requiredRoles"])
    forbidden = set(producer_brief["intent"]["forbiddenRoles"])
    if required & forbidden:
        raise ValueError("ArrangementGraph role constraints conflict")

    energies = _target_energies(producer_brief)
    tolerance = int(producer_brief["intent"]["transformationTolerance"])
    locked_markers = set(producer_brief["intent"]["lockedElements"])
    motif_id = "motif-" + sha256(
        f"{song_map['mapHash']}:{producer_brief['briefHash']}".encode("ascii")).hexdigest()[:16]
    reviews = _manual_review(song_map)
    nodes = []
    for marker, element_type, requested_label, motif_treatment in _MARKER_SPECS:
        section, exact, section_reason = _section_for_label(song_map, requested_label)
        bars = 1 if element_type == "fill" else min(8, _section_bars(song_map, section))
        energy = energies[marker]
        density = _node_density(str(producer_brief["intent"]["density"]), energy, element_type)
        roles = _roles_for_node(producer_brief, density, element_type, energy)
        locked = marker in locked_markers
        confidence = round(min(float(song_map["confidence"]), float(section.get("confidence", 0)))
                           * (1.0 if exact else 0.82), 4)
        decision = "KEEP" if locked or tolerance == 0 else "PLAN"
        if confidence < 0.68 or section.get("decision") == "MANUAL_REVIEW":
            decision = "MANUAL_REVIEW"
            reviews.append({
                "id": f"node-{marker}", "code": "LOW_NODE_CONFIDENCE",
                "reason": f"{marker} source section requires manual confirmation.",
                "source": "arrangement-node", "marker": marker,
                "details": {"sourceId": str(section["id"]),
                            "reasons": [section_reason, str(confidence)]},
            })
        transition_target = "v2cv1" if marker == "f1cv1" else "v4cv1" if marker == "f2cv1" else None
        nodes.append({
            "marker": marker, "elementType": element_type,
            "sourceSectionId": str(section["id"]),
            "sourceSectionLabel": str(section["label"]), "bars": bars,
            "targetEnergy": energy, "targetDensity": density, "roles": roles,
            "forbiddenRoles": sorted(forbidden, key=lambda role: _ROLE_ORDER[role]),
            "registerPlan": [{"role": role, "low": _ROLE_BANDS[role][0],
                              "high": _ROLE_BANDS[role][1], "source": "software-safe-band"}
                             for role in roles],
            "polyphonyBudget": _polyphony_budget(roles, energy),
            "transformationBudget": _transformation_budget(roles, bars, tolerance, locked),
            "motifFamilyId": motif_id, "motifTreatment": motif_treatment,
            "harmonicContext": _harmonic_context(song_map, section),
            "transitionTarget": transition_target, "locked": locked,
            "decision": decision, "confidence": confidence,
            "evidence": {"songMapHash": song_map["mapHash"],
                         "producerBriefHash": producer_brief["briefHash"],
                         "sectionMapping": section_reason,
                         "sourceSectionConfidence": float(section.get("confidence", 0))},
        })

    by_marker = {node["marker"]: node for node in nodes}
    edges = []
    transition_style = str(producer_brief["intent"]["transitions"])
    for index, (left_marker, right_marker, edge_type) in enumerate(_EDGE_SPECS, 1):
        left, right = by_marker[left_marker], by_marker[right_marker]
        decision = "MANUAL_REVIEW" if "MANUAL_REVIEW" in {left["decision"], right["decision"]} else "PLAN"
        edges.append({
            "id": f"edge-{index:02d}", "from": left_marker, "to": right_marker,
            "transitionType": edge_type,
            "obligations": _transition_obligations(transition_style, edge_type),
            "targetMarker": right_marker,
            "harmonicContinuity": _edge_harmony(left, right),
            "maximumRegisterJumpSemitones": 12 if transition_style == "subtle" else 9,
            "maximumAddedNotes": 0 if left["locked"] or right["locked"] else
                                 round((3 if edge_type == "direct" else 8) * tolerance / 100),
            "decision": decision,
        })

    maximum_transformations = sum(node["transformationBudget"]["maximumOperations"] for node in nodes)
    maximum_added = sum(node["transformationBudget"]["maximumAddedNotes"] for node in nodes)
    planned_peak = max(node["polyphonyBudget"]["maximumConcurrentMidiNotes"] for node in nodes)
    graph = {
        "schema": GRAPH_SCHEMA, "version": GRAPH_VERSION,
        "source": {"songMapHash": song_map["mapHash"],
                   "producerBriefHash": producer_brief["briefHash"],
                   "sourceMidiSha256": song_map["sourceSha256"],
                   "seed": seed, "variantCount": variant_count},
        "nodes": nodes, "edges": edges,
        "globalBudgets": {
            "maximumAddedNotes": maximum_added,
            "maximumTransformations": maximum_transformations,
            "maximumConcurrentMidiNotes": MAX_CONCURRENT_MIDI_NOTES,
            "sourceConcurrentMidiNotes": int(song_map["polyphony"]["globalPeak"]),
            "plannedConcurrentMidiNotes": planned_peak,
            "fullDurationPolyphonyRequired": True,
            "transformationTolerance": tolerance,
            "roleRegisterBands": [{"role": role, "low": low, "high": high}
                                  for role, (low, high) in _ROLE_BANDS.items()],
            "deviceVoiceCost": {"status": "UNCONFIRMED", "ceiling": None,
                                "estimateOnly": True},
        },
        "hardConstraints": {
            "harmonyCollisionAllowed": False, "registerCollisionAllowed": False,
            "polyphonyOverflowAllowed": False, "lockedElementMutationAllowed": False,
            "lowConfidenceAction": "MANUAL_REVIEW", "transitionTargetRequired": True,
            "originalSoloMutationAllowed": False, "factoryDynamicsOnly": True,
            "goldDynamicsAllowed": False,
        },
        "planVariants": [], "manualReview": reviews,
        "readyForCandidateSearch": not reviews,
        "safety": {
            "readOnly": True, "midiMutationAllowed": False,
            "candidatePatternSelectionAllowed": False,
            "factoryDynamicsAuthority": True, "goldDynamicsAuthority": False,
            "originalSoloMutationAllowed": False, "deviceProfileConfirmed": False,
            "lowConfidenceRequiresManualReview": True,
        },
    }
    graph["planVariants"] = _plan_variants(nodes, seed, variant_count)
    graph["graphHash"] = _hash_without(graph, "graphHash")
    validate_arrangement_graph_v2(graph)
    return graph


def validate_arrangement_graph_v2(value: Mapping[str, Any]) -> None:
    root = {"schema", "version", "source", "nodes", "edges", "globalBudgets",
            "hardConstraints", "planVariants", "manualReview",
            "readyForCandidateSearch", "safety", "graphHash"}
    if not isinstance(value, Mapping) or set(value) != root:
        raise ValueError("ArrangementGraph 2.0 root fields mismatch")
    if value["schema"] != GRAPH_SCHEMA or value["version"] != GRAPH_VERSION:
        raise ValueError("Unsupported ArrangementGraph schema/version")
    source = value["source"]
    if set(source) != {"songMapHash", "producerBriefHash", "sourceMidiSha256", "seed", "variantCount"}:
        raise ValueError("ArrangementGraph source fields mismatch")
    if any(not re.fullmatch(r"[0-9a-f]{64}", str(source[field]))
           for field in ("songMapHash", "producerBriefHash", "sourceMidiSha256")):
        raise ValueError("ArrangementGraph source hash is invalid")
    if isinstance(source["seed"], bool) or not isinstance(source["seed"], int) or not 0 <= source["seed"] <= 2**31 - 1:
        raise ValueError("ArrangementGraph source seed is invalid")
    if isinstance(source["variantCount"], bool) or not 2 <= source["variantCount"] <= 4:
        raise ValueError("ArrangementGraph variant count is invalid")
    nodes = value["nodes"]
    node_keys = {"marker", "elementType", "sourceSectionId", "sourceSectionLabel", "bars",
                 "targetEnergy", "targetDensity", "roles", "forbiddenRoles", "registerPlan",
                 "polyphonyBudget", "transformationBudget", "motifFamilyId", "motifTreatment",
                 "harmonicContext", "transitionTarget", "locked", "decision", "confidence", "evidence"}
    if not isinstance(nodes, list) or len(nodes) != len(ELEMENTS) or set(item["marker"] for item in nodes) != set(ELEMENTS):
        raise ValueError("ArrangementGraph must contain every Pa800 marker exactly once")
    for node in nodes:
        if set(node) != node_keys:
            raise ValueError("ArrangementGraph node fields mismatch")
        if node["elementType"] not in ELEMENT_TYPES or node["targetDensity"] not in DENSITIES:
            raise ValueError("ArrangementGraph node type or density is invalid")
        if not 1 <= node["bars"] <= 32 or not 0 <= node["targetEnergy"] <= 100:
            raise ValueError("ArrangementGraph node bars or energy is invalid")
        if node["decision"] not in DECISIONS or not 0 <= node["confidence"] <= 1:
            raise ValueError("ArrangementGraph node decision or confidence is invalid")
        if len(node["roles"]) != len(set(node["roles"])) or not set(node["roles"]) <= set(ROLES):
            raise ValueError("ArrangementGraph node roles are invalid")
        if set(node["roles"]) & set(node["forbiddenRoles"]):
            raise ValueError("ArrangementGraph node contains a forbidden role")
        if {item["role"] for item in node["registerPlan"]} != set(node["roles"]):
            raise ValueError("ArrangementGraph register plan does not match roles")
        if any(set(item) != {"role", "low", "high", "source"} or not 0 <= item["low"] <= item["high"] <= 127
               for item in node["registerPlan"]):
            raise ValueError("ArrangementGraph register plan is invalid")
        poly = node["polyphonyBudget"]
        if set(poly) != {"maximumConcurrentMidiNotes", "estimatedVoiceCostUnits", "voiceCostStatus", "deviceConfirmed"}:
            raise ValueError("ArrangementGraph polyphony budget fields mismatch")
        if not 1 <= poly["maximumConcurrentMidiNotes"] <= MAX_CONCURRENT_MIDI_NOTES or poly["deviceConfirmed"] is not False:
            raise ValueError("ArrangementGraph node polyphony budget is invalid")
        transform = node["transformationBudget"]
        if set(transform) != {"maximumOperations", "maximumAddedNotes", "tolerance", "locked"}:
            raise ValueError("ArrangementGraph transformation budget fields mismatch")
        if node["locked"] != transform["locked"] or node["locked"] and (transform["maximumOperations"] or transform["maximumAddedNotes"]):
            raise ValueError("ArrangementGraph locked node budget is invalid")
        if node["transitionTarget"] is not None and node["transitionTarget"] not in ELEMENTS:
            raise ValueError("ArrangementGraph transition target is invalid")
        if not node["harmonicContext"] or not all(isinstance(item, str) for item in node["harmonicContext"]):
            raise ValueError("ArrangementGraph harmonic context is invalid")
    by_marker = {node["marker"]: node for node in nodes}
    variation_energy = [by_marker[f"v{index}cv1"]["targetEnergy"] for index in range(1, 5)]
    if variation_energy != sorted(variation_energy):
        raise ValueError("ArrangementGraph variation energy must be nondecreasing")
    edges = value["edges"]
    edge_keys = {"id", "from", "to", "transitionType", "obligations", "targetMarker",
                 "harmonicContinuity", "maximumRegisterJumpSemitones", "maximumAddedNotes", "decision"}
    if not isinstance(edges, list) or len({edge["id"] for edge in edges}) != len(edges):
        raise ValueError("ArrangementGraph edge IDs are invalid")
    for edge in edges:
        if set(edge) != edge_keys or edge["from"] not in by_marker or edge["to"] not in by_marker:
            raise ValueError("ArrangementGraph edge fields or references are invalid")
        if edge["transitionType"] not in TRANSITION_TYPES or edge["targetMarker"] != edge["to"]:
            raise ValueError("ArrangementGraph edge transition is invalid")
        if not set(edge["obligations"]) <= set(OBLIGATIONS) or edge["decision"] not in {"PLAN", "MANUAL_REVIEW"}:
            raise ValueError("ArrangementGraph edge obligations or decision are invalid")
        harmony = edge["harmonicContinuity"]
        if set(harmony) != {"fromChord", "toChord", "requiresAdaptation", "confidence"}:
            raise ValueError("ArrangementGraph edge harmony fields mismatch")
    for fill, target in (("f1cv1", "v2cv1"), ("f2cv1", "v4cv1")):
        if by_marker[fill]["transitionTarget"] != target or not any(
                edge["from"] == fill and edge["to"] == target for edge in edges):
            raise ValueError("ArrangementGraph fill transition target is missing")
    for edge in edges:
        if edge["transitionType"] == "ending" and "ending-cadence" not in edge["obligations"]:
            raise ValueError("ArrangementGraph ending edge lacks cadence obligation")
    budgets = value["globalBudgets"]
    budget_keys = {"maximumAddedNotes", "maximumTransformations", "maximumConcurrentMidiNotes",
                   "sourceConcurrentMidiNotes", "plannedConcurrentMidiNotes",
                   "fullDurationPolyphonyRequired", "transformationTolerance",
                   "roleRegisterBands", "deviceVoiceCost"}
    if set(budgets) != budget_keys or budgets["maximumConcurrentMidiNotes"] != MAX_CONCURRENT_MIDI_NOTES:
        raise ValueError("ArrangementGraph global budget fields mismatch")
    if budgets["plannedConcurrentMidiNotes"] > budgets["maximumConcurrentMidiNotes"] or budgets["fullDurationPolyphonyRequired"] is not True:
        raise ValueError("ArrangementGraph global polyphony budget is invalid")
    voice = budgets["deviceVoiceCost"]
    if voice != {"status": "UNCONFIRMED", "ceiling": None, "estimateOnly": True}:
        raise ValueError("ArrangementGraph device voice cost must remain unconfirmed")
    constraints = value["hardConstraints"]
    expected_constraints = {
        "harmonyCollisionAllowed": False, "registerCollisionAllowed": False,
        "polyphonyOverflowAllowed": False, "lockedElementMutationAllowed": False,
        "lowConfidenceAction": "MANUAL_REVIEW", "transitionTargetRequired": True,
        "originalSoloMutationAllowed": False, "factoryDynamicsOnly": True,
        "goldDynamicsAllowed": False,
    }
    if constraints != expected_constraints:
        raise ValueError("ArrangementGraph hard constraints were weakened")
    reviews = value["manualReview"]
    review_keys = {"id", "code", "reason", "source", "marker", "details"}
    if any(set(item) != review_keys for item in reviews) or len({item["id"] for item in reviews}) != len(reviews):
        raise ValueError("ArrangementGraph manual review entries are invalid")
    if value["readyForCandidateSearch"] != (not reviews):
        raise ValueError("ArrangementGraph readiness disagrees with manual review")
    variants = value["planVariants"]
    if not isinstance(variants, list) or len(variants) != source["variantCount"]:
        raise ValueError("ArrangementGraph plan variant count mismatch")
    locked = {node["marker"]: node for node in nodes if node["locked"]}
    for index, variant in enumerate(variants, 1):
        variant_keys = {"variantId", "strategy", "seedOffset", "energyDelta", "densityDelta",
                        "elementTargets", "readOnly", "variantHash"}
        if set(variant) != variant_keys or variant["variantId"] != f"plan-{index:02d}":
            raise ValueError("ArrangementGraph plan variant fields mismatch")
        if variant["strategy"] not in STRATEGIES or variant["readOnly"] is not True:
            raise ValueError("ArrangementGraph plan variant strategy is invalid")
        targets = variant["elementTargets"]
        if set(item["marker"] for item in targets) != set(ELEMENTS):
            raise ValueError("ArrangementGraph variant targets are incomplete")
        for target in targets:
            if set(target) != {"marker", "targetEnergy", "targetDensity", "transitionEmphasis", "locked"}:
                raise ValueError("ArrangementGraph variant target fields mismatch")
            if not 0 <= target["targetEnergy"] <= 100 or target["targetDensity"] not in DENSITIES:
                raise ValueError("ArrangementGraph variant target is invalid")
            if target["marker"] in locked and (target["targetEnergy"] != locked[target["marker"]]["targetEnergy"]
                                                or target["targetDensity"] != locked[target["marker"]]["targetDensity"]):
                raise ValueError("ArrangementGraph variant changed a locked element")
        v_energy = [next(item["targetEnergy"] for item in targets if item["marker"] == f"v{i}cv1")
                    for i in range(1, 5)]
        if v_energy != sorted(v_energy):
            raise ValueError("ArrangementGraph variant energy must be nondecreasing")
        if variant["variantHash"] != _hash_without(variant, "variantHash"):
            raise ValueError("ArrangementGraph variant hash mismatch")
    primary = {item["marker"]: item for item in variants[0]["elementTargets"]}
    if any(primary[node["marker"]]["targetEnergy"] != node["targetEnergy"]
           or primary[node["marker"]]["targetDensity"] != node["targetDensity"] for node in nodes):
        raise ValueError("ArrangementGraph primary variant must match graph nodes")
    expected_safety = {
        "readOnly": True, "midiMutationAllowed": False,
        "candidatePatternSelectionAllowed": False,
        "factoryDynamicsAuthority": True, "goldDynamicsAuthority": False,
        "originalSoloMutationAllowed": False, "deviceProfileConfirmed": False,
        "lowConfidenceRequiresManualReview": True,
    }
    if value["safety"] != expected_safety:
        raise ValueError("ArrangementGraph safety contract was weakened")
    if value["graphHash"] != _hash_without(value, "graphHash"):
        raise ValueError("ArrangementGraph graphHash mismatch")


def execute_arrangement_graph_api(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"songMap", "producerBrief", "seed", "variantCount"}
    if not isinstance(payload, Mapping) or set(payload) - allowed or not {"songMap", "producerBrief"} <= set(payload):
        raise ValueError("ArrangementGraph API requires only songMap, producerBrief, seed and variantCount")
    return build_arrangement_graph(payload["songMap"], payload["producerBrief"],
                                   payload.get("seed", 0), payload.get("variantCount", 2))


def execute_arrangement_graph_gui(payload: Mapping[str, Any]) -> dict[str, Any]:
    return execute_arrangement_graph_api(payload)