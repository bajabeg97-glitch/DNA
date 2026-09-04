"""Session 29 local Personal Producer Profile and soft-ranking overlay.

The profile learns only from explicit accepted variant decisions and explicit
selection locks.  It never stores MIDI/project content and can influence only
the ordering of candidates that already passed every hard constraint.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

from .candidate_search import validate_candidate_set_v2
from .premium_workflow import validate_premium_workflow_v2
from .producer_brief import ELEMENTS, ROLES, validate_producer_brief_v2
from .arrangement_graph import validate_arrangement_graph_v2


PROFILE_SCHEMA = "dna-personal-producer-profile"
PROFILE_VERSION = "2.0"
RANKING_OVERLAY_SCHEMA = "dna-personal-ranking-overlay"
RANKING_OVERLAY_VERSION = "1.0"
PROFILE_EXPORT_SCHEMA = "dna-personal-profile-export"
PROFILE_DELETION_SCHEMA = "dna-personal-profile-deletion"
PROFILE_OPERATION_VERSION = "1.0"
LEARNING_EVENT_TYPES = ("ACCEPT_VARIANT", "LOCK_SELECTION")
ALLOWED_EVENT_SOURCE = "USER_EXPLICIT"
PROHIBITED_SIGNALS = (
    "PLAYBACK", "PREVIEW_POSITION", "HOVER", "REJECT_VARIANT",
    "CLOUD_TELEMETRY", "IMPLICIT_BEHAVIOR", "MIDI_CONTENT",
)
PREFERENCE_DIMENSIONS = ("density", "transitions", "syncopation", "space")
OVERRIDE_DIMENSIONS = ("pattern", "role", *PREFERENCE_DIMENSIONS)
_SHA = re.compile(r"^[0-9a-f]{64}$")
_STABLE_ID = re.compile(r"^[0-9]{3}\.[0-9]{3}\.[0-9]{3}$")
_EVENT_ID = re.compile(r"^decision-[a-z0-9][a-z0-9-]{2,63}$")
_PROFILE_ID = re.compile(r"^profile-[a-z0-9][a-z0-9-]{2,63}$")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return _hash({key: item for key, item in value.items() if key != field})


def _strict(value: Mapping[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    unknown, missing = set(value) - allowed, required - set(value)
    if unknown:
        raise ValueError(f"Unknown {label} fields: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"Missing {label} fields: {', '.join(sorted(missing))}")


def _valid_date(raw: object, label: str) -> str:
    try:
        value = date.fromisoformat(str(raw))
    except ValueError as error:
        raise ValueError(f"{label} must use YYYY-MM-DD") from error
    if value > date.today():
        raise ValueError(f"{label} cannot be in the future")
    return value.isoformat()


def _identity(raw: Mapping[str, Any] | None, effective_date: str) -> dict[str, Any]:
    raw = {} if raw is None else raw
    _strict(raw, {"profileId", "displayName", "locale", "createdDate", "enabled"}, set(),
            "profile identity")
    profile_id = str(raw.get("profileId", "profile-local-producer"))
    display_name = str(raw.get("displayName", "Local Producer"))
    locale = str(raw.get("locale", "hr-HR"))
    created = _valid_date(raw.get("createdDate", effective_date), "createdDate")
    if not _PROFILE_ID.fullmatch(profile_id):
        raise ValueError("Invalid personal profile ID")
    if not 1 <= len(display_name) <= 80 or not re.fullmatch(r"[A-Za-z0-9 ._\-À-ž]+", display_name):
        raise ValueError("Invalid personal profile display name")
    if not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", locale):
        raise ValueError("Invalid personal profile locale")
    return {"profileId": profile_id, "displayName": display_name, "locale": locale,
            "enabled": bool(raw.get("enabled", True)), "localOnly": True,
            "createdDate": created, "updatedDate": effective_date}


def _validate_source_chain(workflow: Mapping[str, Any], brief: Mapping[str, Any],
                           graph: Mapping[str, Any], candidate_set: Mapping[str, Any]) -> None:
    validate_premium_workflow_v2(workflow)
    validate_producer_brief_v2(brief)
    validate_arrangement_graph_v2(graph)
    validate_candidate_set_v2(candidate_set)
    expected = (
        (workflow["source"]["producerBriefHash"], brief["briefHash"]),
        (workflow["source"]["arrangementGraphHash"], graph["graphHash"]),
        (workflow["source"]["candidateSetHash"], candidate_set["candidateSetHash"]),
        (graph["source"]["producerBriefHash"], brief["briefHash"]),
        (candidate_set["source"]["graphHash"], graph["graphHash"]),
    )
    if any(left != right for left, right in expected):
        raise ValueError("Personal profile source chain mismatch")


def _candidate_lookup(candidate_set: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    result = {}
    for request in candidate_set["requests"]:
        for candidate in request["rankedCandidates"]:
            result[(request["requestId"], candidate["patternId"])] = candidate
    return result


def _normalize_event(raw: Mapping[str, Any], workflow: Mapping[str, Any],
                     brief: Mapping[str, Any], graph: Mapping[str, Any],
                     candidate_set: Mapping[str, Any]) -> dict[str, Any]:
    fields = {"eventId", "eventType", "source", "accepted", "eventDate", "workflowHash",
              "variantId", "marker", "role", "patternId"}
    _strict(raw, fields, fields, "learning event")
    event_id, event_type = str(raw["eventId"]), str(raw["eventType"])
    if not _EVENT_ID.fullmatch(event_id):
        raise ValueError("Invalid learning event ID")
    if event_type not in LEARNING_EVENT_TYPES:
        if event_type in PROHIBITED_SIGNALS:
            raise ValueError("Implicit or prohibited profile learning signal")
        raise ValueError("Unsupported profile learning event")
    if raw["source"] != ALLOWED_EVENT_SOURCE or raw["accepted"] is not True:
        raise ValueError("Profile learning requires an explicit accepted user action")
    if raw["workflowHash"] != workflow["workflowHash"]:
        raise ValueError("Learning event belongs to another workflow")
    event_date = _valid_date(raw["eventDate"], "eventDate")
    variant_id = str(raw["variantId"])
    variant = next((item for item in candidate_set["variants"]
                    if item["variantId"] == f"variant-{variant_id}"), None)
    if variant is None:
        raise ValueError("Learning event references an unknown variant")
    lookup = _candidate_lookup(candidate_set)
    selected = [item for item in variant["selections"] if item["patternId"] is not None]
    marker = raw["marker"]
    role = raw["role"]
    pattern_id = raw["patternId"]
    if event_type == "ACCEPT_VARIANT":
        if any(item is not None for item in (marker, role, pattern_id)):
            raise ValueError("Variant acceptance cannot contain a selection target")
    else:
        marker, role, pattern_id = str(marker), str(role), str(pattern_id)
        if marker not in ELEMENTS or role not in ROLES or not _STABLE_ID.fullmatch(pattern_id):
            raise ValueError("Invalid explicit selection lock")
        selected = [item for item in selected if (item["marker"], item["role"], item["patternId"])
                    == (marker, role, pattern_id)]
        if len(selected) != 1:
            raise ValueError("Locked selection is not present in the accepted variant")
    context = {key: brief["intent"][key] for key in
               ("genre", "density", "transitions", "syncopation", "space")}
    selection_rows = []
    for item in selected:
        candidate = lookup[(item["requestId"], item["patternId"])]
        selection_rows.append({
            "requestId": item["requestId"], "marker": item["marker"], "role": item["role"],
            "patternId": item["patternId"], "sourceKind": item["sourceKind"],
            "density": candidate["density"], "register": candidate["register"],
            "candidateHash": candidate["candidateHash"],
        })
    event = {
        "eventId": event_id, "eventType": event_type, "source": ALLOWED_EVENT_SOURCE,
        "accepted": True, "eventDate": event_date, "workflowHash": workflow["workflowHash"],
        "candidateSetHash": candidate_set["candidateSetHash"], "variantId": variant_id,
        "marker": marker, "role": role, "patternId": pattern_id,
        "context": context, "selections": selection_rows, "eventHash": "",
    }
    event["eventHash"] = _hash_without(event, "eventHash")
    return event


def _preference_rows(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    genre_counts: Counter[str] = Counter()
    dimension_counts = {name: Counter() for name in PREFERENCE_DIMENSIONS}
    role_counts: Counter[str] = Counter()
    marker_counts: Counter[str] = Counter()
    pattern_counts: Counter[tuple[str, str, str]] = Counter()
    pattern_events: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    register_values: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for event in events:
        event_id = event["eventId"]
        genre_counts[str(event["context"]["genre"])] += 1
        for dimension in PREFERENCE_DIMENSIONS:
            dimension_counts[dimension][str(event["context"][dimension])] += 1
        for item in event["selections"]:
            role, marker, pattern_id = item["role"], item["marker"], item["patternId"]
            role_counts[role] += 1
            marker_counts[marker] += 1
            key = (pattern_id, role, marker)
            pattern_counts[key] += 1
            pattern_events[key].add(event_id)
            register_values[role].append((int(item["register"]["low"]),
                                          int(item["register"]["high"]), event_id))

    def weighted_rows(counts: Counter[str], key_name: str) -> list[dict[str, Any]]:
        maximum = max(counts.values(), default=1)
        return [{key_name: key, "weight": round(value / maximum, 6), "evidenceCount": value}
                for key, value in sorted(counts.items())]

    patterns = []
    maximum_pattern = max(pattern_counts.values(), default=1)
    for (pattern_id, role, marker), count in sorted(pattern_counts.items()):
        patterns.append({"patternId": pattern_id, "role": role, "marker": marker,
                         "weight": round(count / maximum_pattern, 6),
                         "evidenceCount": count, "eventIds": sorted(pattern_events[(pattern_id, role, marker)])})
    registers = []
    for role, values in sorted(register_values.items()):
        low = round(sum(item[0] for item in values) / len(values))
        high = round(sum(item[1] for item in values) / len(values))
        registers.append({"role": role, "low": low, "high": high,
                          "center": round((low + high) / 2, 3), "evidenceCount": len(values),
                          "eventIds": sorted({item[2] for item in values})})
    return {
        "genres": weighted_rows(genre_counts, "genre"),
        "dimensions": {name: weighted_rows(dimension_counts[name], "value")
                       for name in PREFERENCE_DIMENSIONS},
        "roles": weighted_rows(role_counts, "role"),
        "markers": weighted_rows(marker_counts, "marker"),
        "patterns": patterns, "registerBands": registers,
    }


def build_cold_start_profile(effective_date: str = "2026-09-03",
                             identity: Mapping[str, Any] | None = None) -> dict[str, Any]:
    effective_date = _valid_date(effective_date, "effectiveDate")
    profile = {
        "schema": PROFILE_SCHEMA, "version": PROFILE_VERSION,
        "identity": _identity(identity, effective_date),
        "source": {"workflowHashes": [], "candidateSetHashes": [], "learningEventHashes": []},
        "learning": {"eventCount": 0, "acceptedVariantCount": 0, "manualLockCount": 0,
                     "events": [], "explicitUserActionsOnly": True,
                     "prohibitedSignals": list(PROHIBITED_SIGNALS)},
        "preferences": _preference_rows([]), "overrides": [],
        "audit": {"coldStart": True, "explainable": True, "reversible": True,
                  "profileAffectsSoftRankingOnly": True, "implicitLearningCount": 0},
        "safety": {"localOnly": True, "cloudSyncAllowed": False, "containsMidi": False,
                   "containsProject": False, "containsAudio": False, "telemetryAllowed": False,
                   "hardConstraintAuthority": False, "factoryDynamicsAuthority": False,
                   "soundBindingAuthority": False, "validatorAuthority": False,
                   "bankProgramAuthority": False, "midiMutationAllowed": False},
        "profileHash": "",
    }
    profile["profileHash"] = _hash_without(profile, "profileHash")
    validate_personal_profile_v2(profile)
    return profile


def build_personal_profile(workflow: Mapping[str, Any], producer_brief: Mapping[str, Any],
                           arrangement_graph: Mapping[str, Any], candidate_set: Mapping[str, Any],
                           learning_events: Sequence[Mapping[str, Any]],
                           identity: Mapping[str, Any] | None = None,
                           existing_profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
    _validate_source_chain(workflow, producer_brief, arrangement_graph, candidate_set)
    if not isinstance(learning_events, Sequence) or isinstance(learning_events, (str, bytes)):
        raise ValueError("learningEvents must be an array")
    normalized = [_normalize_event(item, workflow, producer_brief, arrangement_graph, candidate_set)
                  for item in learning_events]
    if not normalized:
        raise ValueError("At least one explicit learning event is required")
    if len({item["eventId"] for item in normalized}) != len(normalized):
        raise ValueError("Duplicate learning event ID")
    if existing_profile is not None:
        validate_personal_profile_v2(existing_profile)
        existing_events = deepcopy(existing_profile["learning"]["events"])
        duplicates = {item["eventId"] for item in existing_events} & {item["eventId"] for item in normalized}
        if duplicates:
            raise ValueError("Learning event already exists in profile")
        events = existing_events + normalized
        base_identity = deepcopy(existing_profile["identity"])
        base_identity["updatedDate"] = max(item["eventDate"] for item in normalized)
        if identity:
            requested = _identity(identity, base_identity["updatedDate"])
            if requested["profileId"] != base_identity["profileId"]:
                raise ValueError("Incremental learning cannot change profileId")
            base_identity.update({key: requested[key] for key in ("displayName", "locale", "enabled")})
        overrides = deepcopy(existing_profile["overrides"])
    else:
        events = normalized
        effective_date = max(item["eventDate"] for item in events)
        base_identity = _identity(identity, effective_date)
        overrides = []
    profile = {
        "schema": PROFILE_SCHEMA, "version": PROFILE_VERSION, "identity": base_identity,
        "source": {
            "workflowHashes": sorted({item["workflowHash"] for item in events}),
            "candidateSetHashes": sorted({item["candidateSetHash"] for item in events}),
            "learningEventHashes": sorted(item["eventHash"] for item in events),
        },
        "learning": {
            "eventCount": len(events),
            "acceptedVariantCount": sum(item["eventType"] == "ACCEPT_VARIANT" for item in events),
            "manualLockCount": sum(item["eventType"] == "LOCK_SELECTION" for item in events),
            "events": sorted(events, key=lambda item: (item["eventDate"], item["eventId"])),
            "explicitUserActionsOnly": True, "prohibitedSignals": list(PROHIBITED_SIGNALS),
        },
        "preferences": _preference_rows(events), "overrides": overrides,
        "audit": {"coldStart": False, "explainable": True, "reversible": True,
                  "profileAffectsSoftRankingOnly": True, "implicitLearningCount": 0},
        "safety": {"localOnly": True, "cloudSyncAllowed": False, "containsMidi": False,
                   "containsProject": False, "containsAudio": False, "telemetryAllowed": False,
                   "hardConstraintAuthority": False, "factoryDynamicsAuthority": False,
                   "soundBindingAuthority": False, "validatorAuthority": False,
                   "bankProgramAuthority": False, "midiMutationAllowed": False},
        "profileHash": "",
    }
    profile["profileHash"] = _hash_without(profile, "profileHash")
    validate_personal_profile_v2(profile)
    return profile


def validate_personal_profile_v2(profile: Mapping[str, Any]) -> None:
    root = {"schema", "version", "identity", "source", "learning", "preferences", "overrides",
            "audit", "safety", "profileHash"}
    if not isinstance(profile, Mapping) or set(profile) != root:
        raise ValueError("PersonalProfile 2.0 root fields mismatch")
    if profile["schema"] != PROFILE_SCHEMA or profile["version"] != PROFILE_VERSION:
        raise ValueError("Unsupported PersonalProfile schema/version")
    identity = profile["identity"]
    if set(identity) != {"profileId", "displayName", "locale", "enabled", "localOnly",
                        "createdDate", "updatedDate"} or identity["localOnly"] is not True:
        raise ValueError("PersonalProfile identity is invalid")
    if not _PROFILE_ID.fullmatch(str(identity["profileId"])):
        raise ValueError("PersonalProfile profileId is invalid")
    _valid_date(identity["createdDate"], "createdDate")
    _valid_date(identity["updatedDate"], "updatedDate")
    source = profile["source"]
    if set(source) != {"workflowHashes", "candidateSetHashes", "learningEventHashes"}:
        raise ValueError("PersonalProfile source fields mismatch")
    if any(not _SHA.fullmatch(str(item)) for values in source.values() for item in values):
        raise ValueError("PersonalProfile source hash is invalid")
    learning = profile["learning"]
    if set(learning) != {"eventCount", "acceptedVariantCount", "manualLockCount", "events",
                        "explicitUserActionsOnly", "prohibitedSignals"}:
        raise ValueError("PersonalProfile learning fields mismatch")
    if learning["explicitUserActionsOnly"] is not True or learning["prohibitedSignals"] != list(PROHIBITED_SIGNALS):
        raise ValueError("PersonalProfile implicit-learning protection was weakened")
    if learning["eventCount"] != len(learning["events"]):
        raise ValueError("PersonalProfile learning count mismatch")
    if len({item["eventId"] for item in learning["events"]}) != len(learning["events"]):
        raise ValueError("PersonalProfile event IDs are not unique")
    for event in learning["events"]:
        if event["source"] != ALLOWED_EVENT_SOURCE or event["accepted"] is not True:
            raise ValueError("PersonalProfile contains non-explicit learning")
        if event["eventHash"] != _hash_without(event, "eventHash"):
            raise ValueError("PersonalProfile event hash mismatch")
    preferences = profile["preferences"]
    if set(preferences) != {"genres", "dimensions", "roles", "markers", "patterns", "registerBands"}:
        raise ValueError("PersonalProfile preference fields mismatch")
    if set(preferences["dimensions"]) != set(PREFERENCE_DIMENSIONS):
        raise ValueError("PersonalProfile preference dimensions mismatch")
    for item in preferences["patterns"]:
        if not _STABLE_ID.fullmatch(str(item["patternId"])) or item["role"] not in ROLES or item["marker"] not in ELEMENTS:
            raise ValueError("PersonalProfile pattern preference is invalid")
    for override in profile["overrides"]:
        if set(override) != {"dimension", "key", "weight", "reason", "source", "overrideHash"}:
            raise ValueError("PersonalProfile override fields mismatch")
        if override["dimension"] not in OVERRIDE_DIMENSIONS or override["source"] != ALLOWED_EVENT_SOURCE:
            raise ValueError("PersonalProfile override is not explicit or supported")
        if not -1.0 <= float(override["weight"]) <= 1.0 or override["overrideHash"] != _hash_without(override, "overrideHash"):
            raise ValueError("PersonalProfile override weight/hash is invalid")
    expected_safety = {"localOnly": True, "cloudSyncAllowed": False, "containsMidi": False,
                       "containsProject": False, "containsAudio": False, "telemetryAllowed": False,
                       "hardConstraintAuthority": False, "factoryDynamicsAuthority": False,
                       "soundBindingAuthority": False, "validatorAuthority": False,
                       "bankProgramAuthority": False, "midiMutationAllowed": False}
    if profile["safety"] != expected_safety:
        raise ValueError("PersonalProfile safety contract was weakened")
    if profile["profileHash"] != _hash_without(profile, "profileHash"):
        raise ValueError("PersonalProfile hash mismatch")


def edit_personal_profile(profile: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    validate_personal_profile_v2(profile)
    allowed = {"displayName", "enabled", "overrides", "clearOverrides", "effectiveDate"}
    _strict(patch, allowed, {"effectiveDate"}, "profile edit")
    result = deepcopy(profile)
    effective_date = _valid_date(patch["effectiveDate"], "effectiveDate")
    if "displayName" in patch:
        name = str(patch["displayName"])
        if not 1 <= len(name) <= 80 or not re.fullmatch(r"[A-Za-z0-9 ._\-À-ž]+", name):
            raise ValueError("Invalid personal profile display name")
        result["identity"]["displayName"] = name
    if "enabled" in patch:
        if not isinstance(patch["enabled"], bool):
            raise ValueError("Profile enabled must be boolean")
        result["identity"]["enabled"] = patch["enabled"]
    if patch.get("clearOverrides"):
        result["overrides"] = []
    for raw in patch.get("overrides", []):
        _strict(raw, {"dimension", "key", "weight", "reason", "source"},
                {"dimension", "key", "weight", "reason", "source"}, "profile override")
        dimension, key = str(raw["dimension"]), str(raw["key"])
        weight, reason = float(raw["weight"]), str(raw["reason"])
        if dimension not in OVERRIDE_DIMENSIONS or raw["source"] != ALLOWED_EVENT_SOURCE:
            raise ValueError("Only explicit supported profile overrides are allowed")
        if dimension == "pattern" and not _STABLE_ID.fullmatch(key):
            raise ValueError("Pattern override requires a stable pattern ID")
        if dimension == "role" and key not in ROLES:
            raise ValueError("Role override is invalid")
        if not -1.0 <= weight <= 1.0 or not 3 <= len(reason) <= 160:
            raise ValueError("Profile override weight/reason is invalid")
        override = {"dimension": dimension, "key": key, "weight": round(weight, 6),
                    "reason": reason, "source": ALLOWED_EVENT_SOURCE, "overrideHash": ""}
        override["overrideHash"] = _hash_without(override, "overrideHash")
        result["overrides"] = [item for item in result["overrides"]
                               if (item["dimension"], item["key"]) != (dimension, key)] + [override]
    result["overrides"].sort(key=lambda item: (item["dimension"], item["key"]))
    result["identity"]["updatedDate"] = effective_date
    result["profileHash"] = _hash_without(result, "profileHash")
    validate_personal_profile_v2(result)
    return result


def _preference_map(profile: Mapping[str, Any], category: str, key: str) -> dict[str, float]:
    rows = profile["preferences"][category]
    return {str(item[key]): float(item["weight"]) for item in rows}


def _density_bucket(value: float) -> str:
    return "sparse" if value < 7 else "balanced" if value < 14 else "full"


def build_preference_ranking_overlay(candidate_set: Mapping[str, Any],
                                     profile: Mapping[str, Any] | None = None,
                                     deletion: Mapping[str, Any] | None = None,
                                     genre: str = "unknown") -> dict[str, Any]:
    validate_candidate_set_v2(candidate_set)
    if profile is not None and deletion is not None:
        raise ValueError("Ranking overlay accepts profile or deletion, not both")
    deleted = deletion is not None
    if deletion is not None:
        validate_profile_deletion(deletion)
    if profile is not None:
        validate_personal_profile_v2(profile)
    active = (profile is not None and profile["identity"]["enabled"]
              and not profile["audit"]["coldStart"] and not deleted)
    pattern_weights: dict[tuple[str, str, str], float] = {}
    role_weights: dict[str, float] = {}
    density_weights: dict[str, float] = {}
    register_centers: dict[str, float] = {}
    override_weights: dict[tuple[str, str], float] = {}
    profile_hash = None
    if active:
        profile_hash = profile["profileHash"]
        pattern_weights = {(item["patternId"], item["role"], item["marker"]): float(item["weight"])
                           for item in profile["preferences"]["patterns"]}
        role_weights = _preference_map(profile, "roles", "role")
        density_weights = {str(item["value"]): float(item["weight"])
                           for item in profile["preferences"]["dimensions"]["density"]}
        register_centers = {item["role"]: float(item["center"])
                            for item in profile["preferences"]["registerBands"]}
        override_weights = {(item["dimension"], item["key"]): float(item["weight"])
                            for item in profile["overrides"]}
    rows = []
    reranked = 0
    for request in candidate_set["requests"]:
        candidates = []
        for candidate in request["rankedCandidates"]:
            reasons = []
            bonus = 0.0
            if active:
                pattern_weight = pattern_weights.get((candidate["patternId"], request["role"], request["marker"]), 0.0)
                if pattern_weight:
                    bonus += 0.05 * pattern_weight
                    reasons.append("EXPLICIT_PATTERN_ACCEPTANCE")
                role_weight = role_weights.get(request["role"], 0.0)
                if role_weight:
                    bonus += 0.006 * role_weight
                    reasons.append("ROLE_AFFINITY")
                bucket = _density_bucket(float(candidate["density"]))
                density_weight = density_weights.get(bucket, 0.0)
                if density_weight:
                    bonus += 0.009 * density_weight
                    reasons.append("DENSITY_AFFINITY")
                center = (int(candidate["register"]["low"]) + int(candidate["register"]["high"])) / 2
                if request["role"] in register_centers:
                    distance = abs(center - register_centers[request["role"]])
                    bonus += 0.008 * max(0.0, 1.0 - distance / 36.0)
                    reasons.append("REGISTER_AFFINITY")
                manual = override_weights.get(("pattern", candidate["patternId"]), 0.0)
                manual += override_weights.get(("role", request["role"]), 0.0)
                manual += override_weights.get(("density", bucket), 0.0)
                if manual:
                    bonus += 0.007 * manual
                    reasons.append("EXPLICIT_MANUAL_OVERRIDE")
            bonus = round(max(-0.08, min(0.08, bonus)), 6)
            adjusted = round(max(0.0, min(1.0, float(candidate["score"]) + bonus)), 6)
            candidates.append({"patternId": candidate["patternId"], "candidateHash": candidate["candidateHash"],
                               "originalRank": candidate["rank"], "baseScore": candidate["score"],
                               "preferenceBonus": bonus, "adjustedScore": adjusted, "adjustedRank": 0,
                               "reasonCodes": reasons or ["NEUTRAL_PROFILE"],
                               "hardConstraintsPassed": True})
        candidates.sort(key=lambda item: (-item["adjustedScore"], item["patternId"]))
        for index, item in enumerate(candidates, start=1):
            item["adjustedRank"] = index
        changed = any(item["adjustedRank"] != item["originalRank"] for item in candidates)
        reranked += changed
        row = {"requestId": request["requestId"], "marker": request["marker"],
               "role": request["role"], "profileApplied": active,
               "originalOrder": [item["patternId"] for item in request["rankedCandidates"]],
               "adjustedOrder": [item["patternId"] for item in candidates],
               "rankingChanged": changed, "candidates": candidates, "requestOverlayHash": ""}
        row["requestOverlayHash"] = _hash_without(row, "requestOverlayHash")
        rows.append(row)
    overlay = {
        "schema": RANKING_OVERLAY_SCHEMA, "version": RANKING_OVERLAY_VERSION,
        "source": {"candidateSetHash": candidate_set["candidateSetHash"],
                   "profileHash": profile_hash, "deletionHash": deletion["deletionHash"] if deleted else None,
                   "genre": str(genre), "neutral": not active},
        "requests": rows,
        "audit": {"requestCount": len(rows),
                  "eligibleCandidateCount": sum(len(item["rankedCandidates"]) for item in candidate_set["requests"]),
                  "rerankedRequestCount": reranked,
                  "rejectedCandidateCountUntouched": sum(len(item["rejectedCandidates"]) for item in candidate_set["requests"]),
                  "maximumAbsoluteBonus": max((abs(candidate["preferenceBonus"])
                                                for row in rows for candidate in row["candidates"]), default=0.0),
                  "hardConstraintsReevaluated": False, "profileApplied": active},
        "safety": {"readOnly": True, "candidateSetMutated": False,
                   "rejectedCandidatesPromoted": False, "hardConstraintAuthority": False,
                   "factoryDynamicsAuthority": False, "soundBindingAuthority": False,
                   "validatorAuthority": False, "midiMutationAllowed": False},
        "overlayHash": "",
    }
    overlay["overlayHash"] = _hash_without(overlay, "overlayHash")
    validate_preference_ranking_overlay(overlay)
    return overlay


def validate_preference_ranking_overlay(overlay: Mapping[str, Any]) -> None:
    if not isinstance(overlay, Mapping) or set(overlay) != {"schema", "version", "source", "requests",
                                                                  "audit", "safety", "overlayHash"}:
        raise ValueError("RankingOverlay root fields mismatch")
    if overlay["schema"] != RANKING_OVERLAY_SCHEMA or overlay["version"] != RANKING_OVERLAY_VERSION:
        raise ValueError("Unsupported RankingOverlay schema/version")
    if not _SHA.fullmatch(str(overlay["source"]["candidateSetHash"])):
        raise ValueError("RankingOverlay CandidateSet hash is invalid")
    for row in overlay["requests"]:
        if row["requestOverlayHash"] != _hash_without(row, "requestOverlayHash"):
            raise ValueError("RankingOverlay request hash mismatch")
        if any(not -0.08 <= item["preferenceBonus"] <= 0.08 or item["hardConstraintsPassed"] is not True
               for item in row["candidates"]):
            raise ValueError("RankingOverlay contains an unsafe candidate adjustment")
    expected_safety = {"readOnly": True, "candidateSetMutated": False,
                       "rejectedCandidatesPromoted": False, "hardConstraintAuthority": False,
                       "factoryDynamicsAuthority": False, "soundBindingAuthority": False,
                       "validatorAuthority": False, "midiMutationAllowed": False}
    if overlay["safety"] != expected_safety:
        raise ValueError("RankingOverlay safety contract was weakened")
    if overlay["overlayHash"] != _hash_without(overlay, "overlayHash"):
        raise ValueError("RankingOverlay hash mismatch")


def export_personal_profile(profile: Mapping[str, Any], export_date: str) -> dict[str, Any]:
    validate_personal_profile_v2(profile)
    document = {"schema": PROFILE_EXPORT_SCHEMA, "version": PROFILE_OPERATION_VERSION,
                "exportDate": _valid_date(export_date, "exportDate"), "profile": deepcopy(profile),
                "containsMidi": False, "containsProject": False, "containsAudio": False,
                "localOnly": True, "exportHash": ""}
    document["exportHash"] = _hash_without(document, "exportHash")
    return document


def delete_personal_profile(profile: Mapping[str, Any], deletion_date: str) -> dict[str, Any]:
    validate_personal_profile_v2(profile)
    deletion = {"schema": PROFILE_DELETION_SCHEMA, "version": PROFILE_OPERATION_VERSION,
                "profileId": profile["identity"]["profileId"],
                "deletedProfileHash": profile["profileHash"],
                "deletionDate": _valid_date(deletion_date, "deletionDate"),
                "dataRetained": False, "profileUsable": False, "localOnly": True,
                "deletionHash": ""}
    deletion["deletionHash"] = _hash_without(deletion, "deletionHash")
    validate_profile_deletion(deletion)
    return deletion


def validate_profile_deletion(deletion: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "profileId", "deletedProfileHash", "deletionDate",
              "dataRetained", "profileUsable", "localOnly", "deletionHash"}
    if not isinstance(deletion, Mapping) or set(deletion) != fields:
        raise ValueError("Profile deletion fields mismatch")
    if deletion["schema"] != PROFILE_DELETION_SCHEMA or deletion["version"] != PROFILE_OPERATION_VERSION:
        raise ValueError("Unsupported profile deletion contract")
    if deletion["dataRetained"] or deletion["profileUsable"] or deletion["localOnly"] is not True:
        raise ValueError("Profile deletion must remove all usable local data")
    if not _SHA.fullmatch(str(deletion["deletedProfileHash"])):
        raise ValueError("Deleted profile hash is invalid")
    _valid_date(deletion["deletionDate"], "deletionDate")
    if deletion["deletionHash"] != _hash_without(deletion, "deletionHash"):
        raise ValueError("Profile deletion hash mismatch")


def execute_personal_profile_api(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Personal Profile API payload must be an object")
    action = payload.get("action")
    if action == "cold-start":
        _strict(payload, {"action", "effectiveDate", "identity"}, {"action", "effectiveDate"},
                "personal profile API payload")
        return build_cold_start_profile(payload["effectiveDate"], payload.get("identity"))
    if action == "learn":
        allowed = {"action", "workflow", "producerBrief", "arrangementGraph", "candidateSet",
                   "learningEvents", "identity", "existingProfile"}
        _strict(payload, allowed, {"action", "workflow", "producerBrief", "arrangementGraph",
                                   "candidateSet", "learningEvents"}, "personal profile API payload")
        return build_personal_profile(payload["workflow"], payload["producerBrief"],
                                      payload["arrangementGraph"], payload["candidateSet"],
                                      payload["learningEvents"], payload.get("identity"),
                                      payload.get("existingProfile"))
    if action == "overlay":
        allowed = {"action", "candidateSet", "profile", "deletion", "genre"}
        _strict(payload, allowed, {"action", "candidateSet"}, "personal profile API payload")
        return build_preference_ranking_overlay(payload["candidateSet"], payload.get("profile"),
                                                payload.get("deletion"), str(payload.get("genre", "unknown")))
    if action == "edit":
        _strict(payload, {"action", "profile", "patch"}, {"action", "profile", "patch"},
                "personal profile API payload")
        return edit_personal_profile(payload["profile"], payload["patch"])
    if action == "export":
        _strict(payload, {"action", "profile", "exportDate"}, {"action", "profile", "exportDate"},
                "personal profile API payload")
        return export_personal_profile(payload["profile"], payload["exportDate"])
    if action == "delete":
        _strict(payload, {"action", "profile", "deletionDate"},
                {"action", "profile", "deletionDate"}, "personal profile API payload")
        return delete_personal_profile(payload["profile"], payload["deletionDate"])
    raise ValueError("Personal Profile action must be cold-start, learn, overlay, edit, export or delete")


def execute_personal_profile_gui(payload: Mapping[str, Any]) -> dict[str, Any]:
    return execute_personal_profile_api(payload)


__all__ = [
    "PROFILE_SCHEMA", "PROFILE_VERSION", "RANKING_OVERLAY_SCHEMA", "RANKING_OVERLAY_VERSION",
    "PROFILE_EXPORT_SCHEMA", "PROFILE_DELETION_SCHEMA", "PROFILE_OPERATION_VERSION",
    "LEARNING_EVENT_TYPES", "ALLOWED_EVENT_SOURCE", "PROHIBITED_SIGNALS",
    "PREFERENCE_DIMENSIONS", "OVERRIDE_DIMENSIONS", "build_cold_start_profile",
    "build_personal_profile", "validate_personal_profile_v2", "edit_personal_profile",
    "build_preference_ranking_overlay", "validate_preference_ranking_overlay",
    "export_personal_profile", "delete_personal_profile", "validate_profile_deletion",
    "execute_personal_profile_api", "execute_personal_profile_gui",
]