"""Session 32 evidence-driven TrackPlan 3.0 and Full Optimizer dry-run.

The module converts the read-only analysis/authority chain into a strict
mutation contract.  It never emits or changes MIDI bytes.  Every proposed
operation is scoped, budgeted, hash-addressed and fail-closed so Session 33 can
later render only explicitly authorized fragments.
"""

from __future__ import annotations

import base64
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .evidence_authority import validate_evidence_ledger
from .midi import MidiFile


TRACK_PLAN_SCHEMA = "dna-track-plan"
TRACK_PLAN_VERSION = "3.0"
OPTIMIZER_OPERATION_SCHEMA = "dna-optimizer-operation"
OPTIMIZER_OPERATION_VERSION = "1.0"
DRY_RUN_SCHEMA = "dna-optimizer-dry-run"
DRY_RUN_VERSION = "1.0"

TRACK_PLAN_DECISIONS = ("KEEP", "REPAIR", "REPLACE", "MANUAL_REVIEW")
OPERATION_KINDS = (
    "CLEANUP_NOTE_PAIRS", "RESOLVE_OVERLAPS", "QUANTIZE_ONSET_GATE",
    "APPLY_FACTORY_DYNAMICS_MIXER", "REPAIR_REGISTER", "APPLY_FACTORY_CC11",
    "REPLACE_PATTERN", "APPLY_EVIDENCE_GROOVE", "VERIFY_POLYPHONY",
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TRACK_UID = re.compile(r"^trk-[0-9a-f]{20}(?:-[1-9][0-9]*)?$")
_STABLE_ID = re.compile(r"^[0-9]{3}\.[0-9]{3}\.[0-9]{3}$")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _hash(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return _hash({key: item for key, item in value.items() if key != field})


def _document_hash(value: Mapping[str, Any]) -> str:
    for key in ("analysisHash", "mapHash", "briefHash", "graphHash",
                "candidateSetHash", "groovePlanHash", "expressionPlanHash"):
        candidate = value.get(key)
        if isinstance(candidate, str) and _HEX64.fullmatch(candidate):
            return candidate
    return _hash(value)


def _strict_mapping(value: Mapping[str, Any] | None, allowed: set[str], label: str) -> dict[str, Any]:
    result = dict(value or {})
    unknown = set(result) - allowed
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {', '.join(sorted(unknown))}")
    return result


def _controls(value: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _strict_mapping(value, {
        "selectedVariantId", "cleanupNotes", "removeRedundantControllers",
        "quantizeDivision", "quantizeStrength", "factoryDynamics", "velocityStrength",
        "factoryMixer", "mixerStrength", "repairRegister", "fxAuto",
        "phaseOptimization", "sourceMaximumOperations", "lockedFragmentIds",
        "lockedTrackUids", "softwareMidiNoteCeiling",
    }, "TrackPlan controls")
    division = int(raw.get("quantizeDivision", 16))
    if division not in (0, 8, 16, 32):
        raise ValueError("quantizeDivision must be 0, 8, 16 or 32")
    result = {
        "selectedVariantId": str(raw.get("selectedVariantId", "C")),
        "cleanupNotes": bool(raw.get("cleanupNotes", True)),
        "removeRedundantControllers": bool(raw.get("removeRedundantControllers", True)),
        "quantizeDivision": division,
        "quantizeStrength": max(0, min(100, int(raw.get("quantizeStrength", 65)))),
        "factoryDynamics": bool(raw.get("factoryDynamics", True)),
        "velocityStrength": max(0, min(100, int(raw.get("velocityStrength", 65)))),
        "factoryMixer": bool(raw.get("factoryMixer", True)),
        "mixerStrength": max(0, min(100, int(raw.get("mixerStrength", 60)))),
        "repairRegister": bool(raw.get("repairRegister", True)),
        "fxAuto": bool(raw.get("fxAuto", False)),
        "phaseOptimization": bool(raw.get("phaseOptimization", False)),
        "sourceMaximumOperations": max(1, min(12, int(raw.get("sourceMaximumOperations", 6)))),
        "lockedFragmentIds": sorted(set(map(str, raw.get("lockedFragmentIds", ())))),
        "lockedTrackUids": sorted(set(map(str, raw.get("lockedTrackUids", ())))),
        "softwareMidiNoteCeiling": int(raw.get("softwareMidiNoteCeiling", 54)),
    }
    if result["softwareMidiNoteCeiling"] != 54:
        raise ValueError("Session 32 software MIDI-note ceiling is fixed at 54")
    return result


def _load_registry(path: Path, expected_hash: str | None = None) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    raw = path.read_bytes()
    actual = sha256(raw).hexdigest()
    if expected_hash and actual != expected_hash:
        raise ValueError(f"Registry hash mismatch: {path}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"Registry must be an object: {path}")
    return value


def _operation(*, kind: str, scope: str, authority: str,
               decision_ids: Iterable[str], evidence_hashes: Iterable[str],
               limits: Mapping[str, Any], predicted: Mapping[str, int],
               status: str = "PLANNED") -> dict[str, Any]:
    if kind not in OPERATION_KINDS:
        raise ValueError(f"Unknown optimizer operation: {kind}")
    normalized_decision_ids = sorted(set(str(item) for item in decision_ids if item))
    normalized_evidence_hashes = sorted(set(str(item) for item in evidence_hashes if item))
    identity = _hash([kind, scope, normalized_decision_ids, normalized_evidence_hashes, limits])[:24]
    result = {
        "schema": OPTIMIZER_OPERATION_SCHEMA,
        "version": OPTIMIZER_OPERATION_VERSION,
        "operationId": "op-" + identity,
        "kind": kind,
        "scope": scope,
        "status": status,
        "authority": authority,
        "decisionIds": normalized_decision_ids,
        "evidenceHashes": normalized_evidence_hashes,
        "limits": dict(limits),
        "predicted": {str(key): int(item) for key, item in predicted.items()},
        "operationHash": "",
    }
    result["operationHash"] = _hash_without(result, "operationHash")
    return result


def _track_audit(midi: MidiFile, track_index: int, grid_ticks: int | None) -> dict[str, int]:
    notes = [note for note in midi.notes() if note.track == track_index]
    groups: dict[tuple[int, int], list[Any]] = defaultdict(list)
    for note in notes:
        groups[(note.channel, note.pitch)].append(note)
    duplicates = overlaps = 0
    for values in groups.values():
        values.sort(key=lambda item: (item.start, item.end, item.velocity))
        previous = None
        for note in values:
            if previous and note.start == previous.start:
                duplicates += 1
            elif previous and note.start < previous.end:
                overlaps += 1
            if previous is None or note.end > previous.end:
                previous = note
    redundant = 0
    states: dict[tuple[int, int, int], tuple[int, ...]] = {}
    for event in sorted(midi.tracks[track_index].events, key=lambda item: (item.tick, item.order)):
        if event.kind != "channel" or event.command not in (0xB0, 0xC0):
            continue
        key = (event.channel or 0, event.command or 0, event.data[0] if event.command == 0xB0 else -1)
        current = tuple(event.data)
        if states.get(key) == current:
            redundant += 1
        states[key] = current
    return {
        "noteCount": len(notes),
        "duplicateNotes": duplicates,
        "overlappingNotes": overlaps,
        "offGridNotes": sum(bool(grid_ticks and note.start % grid_ticks) for note in notes),
        "redundantControllers": redundant,
    }


def _selected_variant(document: Mapping[str, Any], variant_id: str) -> Mapping[str, Any] | None:
    aliases = {variant_id, variant_id.removeprefix("variant-"),
               variant_id if variant_id.startswith("variant-") else "variant-" + variant_id}
    return next((item for item in document.get("variants", ())
                 if str(item.get("variantId")) in aliases), None)


def _validate_chain(midi_bytes: bytes, documents: Mapping[str, Any], ledger: Mapping[str, Any]) -> dict[str, str]:
    required = {"trackAnalysis", "songMap", "producerBrief", "arrangementGraph",
                "candidateSet", "groovePlan", "expressionPlan"}
    missing = required - set(documents)
    if missing:
        raise ValueError("TrackPlan is missing documents: " + ", ".join(sorted(missing)))
    source_hash = sha256(midi_bytes).hexdigest()
    if documents["trackAnalysis"].get("sourceSha256") != source_hash:
        raise ValueError("Track analysis does not belong to source MIDI")
    for name in required:
        expected = ledger.get("documents", {}).get(name, {}).get("sha256")
        actual = _document_hash(documents[name])
        if expected != actual:
            raise ValueError(f"EvidenceLedger document hash mismatch: {name}")
    midi_claims = {
        documents["songMap"].get("sourceSha256"),
        documents["candidateSet"].get("source", {}).get("sourceMidiSha256"),
        documents["groovePlan"].get("source", {}).get("sourceMidiSha256"),
        documents["expressionPlan"].get("source", {}).get("inputMidiSha256"),
    }
    if midi_claims != {source_hash}:
        raise ValueError("TrackPlan source MIDI hash chain mismatch")
    hashes = {name: _document_hash(documents[name]) for name in sorted(required)}
    hashes["sourceMidiSha256"] = source_hash
    hashes["ledgerHash"] = str(ledger["ledgerHash"])
    return hashes


def _binding_index(bindings: Sequence[Mapping[str, Any]], profiles: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    required = {"requestId", "role", "logicalTrack", "targetTrackUid", "targetTrackIndex",
                "targetTrackNumber", "channelIndex", "channelNumber", "bankMsb", "bankLsb",
                "program", "factoryProfileIds", "confirmation", "deviceConfirmed"}
    for raw in bindings:
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise ValueError("Target binding fields are strict")
        request_id = str(raw["requestId"])
        if request_id in output:
            raise ValueError("Target binding requestId must be unique")
        if not _TRACK_UID.fullmatch(str(raw["targetTrackUid"])):
            raise ValueError("Target binding trackUid is invalid")
        if raw["channelNumber"] != raw["channelIndex"] + 1:
            raise ValueError("Target binding channel numbering mismatch")
        if raw["targetTrackNumber"] != raw["targetTrackIndex"] + 1:
            raise ValueError("Target binding track numbering mismatch")
        if raw["confirmation"] != "EXACT_FACTORY_SOFTWARE" or raw["deviceConfirmed"] is not False:
            raise ValueError("Session 32 accepts exact software Factory bindings only")
        profile_ids = [str(item) for item in raw["factoryProfileIds"]]
        if not profile_ids or any(item not in profiles or not _STABLE_ID.fullmatch(item) for item in profile_ids):
            raise ValueError("Target binding references an unknown Factory profile")
        for profile_id in profile_ids:
            profile = profiles[profile_id]
            if (profile.get("bankMsb"), profile.get("bankLsb"), profile.get("program")) != \
                    (raw["bankMsb"], raw["bankLsb"], raw["program"]):
                raise ValueError("Target binding and Factory profile sound do not match exactly")
        output[request_id] = dict(raw)
    return output


def build_track_plan(midi_bytes: bytes, documents: Mapping[str, Any],
                     evidence_ledger: Mapping[str, Any], target_bindings: Sequence[Mapping[str, Any]],
                     controls: Mapping[str, Any] | None = None, root: str | Path = ".") -> dict[str, Any]:
    """Build a deterministic, read-only optimizer/render contract."""

    validate_evidence_ledger(evidence_ledger)
    normalized = _controls(controls)
    if evidence_ledger["source"]["selectedVariantId"] != normalized["selectedVariantId"]:
        raise ValueError("TrackPlan selected variant differs from EvidenceLedger")
    chain = _validate_chain(midi_bytes, documents, evidence_ledger)
    midi = MidiFile.from_bytes(midi_bytes)
    if midi.ppq <= 0:
        raise ValueError("TrackPlan requires PPQ MIDI")
    root = Path(root)

    factory_registry = evidence_ledger.get("registries", {}).get("factoryProfiles", {})
    if factory_registry.get("status") != "VERIFIED":
        raise ValueError("Factory velocity registry is not verified by EvidenceLedger")
    factory_doc = _load_registry(root / factory_registry["path"], factory_registry["actualSha256"])
    profiles = {str(item["id"]): item for item in factory_doc.get("profiles", ())}
    binding_by_request = _binding_index(target_bindings, profiles)

    decisions = evidence_ledger["decisions"]
    decision_index = {(item["subjectType"], str(item["subjectId"])): item for item in decisions}
    cc11_decisions = [item for item in decisions
                      if item["subjectType"] == "CC11_POINT" and item["disposition"] == "ALLOW"]
    groove_by_request: dict[str, list[dict[str, Any]]] = defaultdict(list)
    groove_variant = _selected_variant(documents["groovePlan"], normalized["selectedVariantId"])
    if groove_variant is None:
        raise ValueError("Selected GroovePlan variant is missing")
    event_request: dict[str, str] = {}
    for fragment in groove_variant.get("fragments", ()):
        for event in fragment.get("events", ()):
            event_request[str(event["eventId"])] = str(fragment["requestId"])
    for item in decisions:
        if item["subjectType"] == "GROOVE_EVENT":
            groove_by_request[event_request.get(str(item["subjectId"]), "")].append(item)

    candidate_variant = _selected_variant(documents["candidateSet"], normalized["selectedVariantId"])
    if candidate_variant is None:
        raise ValueError("Selected CandidateSet variant is missing")
    candidate_requests = {str(item["requestId"]): item for item in documents["candidateSet"]["requests"]}
    candidate_lookup = {
        (str(request["requestId"]), str(candidate["patternId"])): candidate
        for request in documents["candidateSet"]["requests"]
        for candidate in request["rankedCandidates"]
    }
    graph_nodes = {str(item["marker"]): item for item in documents["arrangementGraph"]["nodes"]}
    role_policies = {str(item["role"]): item for item in documents["groovePlan"]["rolePolicies"]}

    registry_documents: dict[str, dict[str, Any]] = {}
    for name in ("goldPerformance", "factoryStrumming"):
        record = evidence_ledger["registries"][name]
        registry_documents[name] = _load_registry(root / record["path"], record["actualSha256"])
    pattern_indexes = {
        name: {str(item["id"]): item for item in document.get("patterns", ())}
        for name, document in registry_documents.items()
    }

    source_plans: list[dict[str, Any]] = []
    grid_ticks = round(midi.ppq * 4 / normalized["quantizeDivision"]) \
        if normalized["quantizeDivision"] else None
    protected = documents["expressionPlan"].get("source", {}).get("originalSoloFingerprint", {})
    protected_uid = protected.get("trackUid")
    track_by_uid = {str(item["trackUid"]): item for item in documents["trackAnalysis"]["tracks"]}
    for track_uid, track in sorted(track_by_uid.items(), key=lambda item: item[1]["trackIndex"]):
        audit = _track_audit(midi, int(track["trackIndex"]), grid_ticks)
        locked = track_uid in normalized["lockedTrackUids"]
        operations: list[dict[str, Any]] = []
        segment_decisions = [decision_index.get(("TRACK_SEGMENT", str(segment["segmentId"])))
                             for segment in track.get("segments", ())]
        segment_decisions = [item for item in segment_decisions if item]
        authorized = bool(segment_decisions) and all(item["disposition"] == "ALLOW" for item in segment_decisions)
        is_solo = track_uid == protected_uid
        if track["decision"] == "IGNORE_METADATA" or locked:
            decision = "KEEP"
            reason = "METADATA_OR_USER_LOCK"
        elif not authorized:
            decision = "MANUAL_REVIEW"
            reason = "TRACK_EVIDENCE_NOT_AUTHORIZED"
        else:
            ids = [item["decisionId"] for item in segment_decisions]
            hashes = [item["decisionHash"] for item in segment_decisions]
            if normalized["cleanupNotes"] and (audit["duplicateNotes"] or audit["overlappingNotes"]):
                operations.append(_operation(
                    kind="CLEANUP_NOTE_PAIRS", scope=track_uid, authority="SONG_ANALYSIS",
                    decision_ids=ids, evidence_hashes=hashes,
                    limits={"maximumRemovedNotes": audit["duplicateNotes"], "protectedSolo": is_solo},
                    predicted={"removedNotes": audit["duplicateNotes"], "shortenedNotes": audit["overlappingNotes"]}))
            if grid_ticks and audit["offGridNotes"] and not is_solo:
                operations.append(_operation(
                    kind="QUANTIZE_ONSET_GATE", scope=track_uid, authority="SONG_ANALYSIS",
                    decision_ids=ids, evidence_hashes=hashes,
                    limits={"gridTicks": grid_ticks, "strength": normalized["quantizeStrength"]},
                    predicted={"notesAffectedMaximum": audit["offGridNotes"]}))
            if normalized["factoryDynamics"] and not is_solo:
                operations.append(_operation(
                    kind="APPLY_FACTORY_DYNAMICS_MIXER", scope=track_uid,
                    authority="FACTORY_DYNAMICS", decision_ids=ids, evidence_hashes=hashes,
                    limits={"velocityStrength": normalized["velocityStrength"],
                            "mixerStrength": normalized["mixerStrength"] if normalized["factoryMixer"] else 0,
                            "goldAuthority": False},
                    predicted={"velocityNotesMaximum": audit["noteCount"],
                               "controllerChangesMaximum": 2 if normalized["factoryMixer"] else 0}))
            if is_solo and cc11_decisions:
                operations.append(_operation(
                    kind="APPLY_FACTORY_CC11", scope=track_uid, authority="FACTORY_DYNAMICS",
                    decision_ids=(item["decisionId"] for item in cc11_decisions),
                    evidence_hashes=(item["decisionHash"] for item in cc11_decisions),
                    limits={"originalNoteChanges": 0, "factoryProfileId":
                            documents["expressionPlan"]["cc11"]["factoryProfileId"]},
                    predicted={"controllerChangesMaximum": len(cc11_decisions)}))
            if len(operations) > normalized["sourceMaximumOperations"]:
                operations = []
                decision = "MANUAL_REVIEW"
                reason = "SOURCE_OPERATION_BUDGET_EXCEEDED"
            else:
                decision = "REPAIR" if operations else "KEEP"
                reason = "AUTHORIZED_DRY_RUN" if operations else "NO_SAFE_CHANGE_REQUIRED"
        source = {
            "trackUid": track_uid, "trackIndex": track["trackIndex"],
            "trackNumber": track["trackNumber"], "role": track.get("primaryRole", "unknown"),
            "decision": decision, "reasonCode": reason, "locked": locked,
            "protectedSolo": is_solo, "audit": audit, "operations": operations,
            "budget": {"maximumOperations": normalized["sourceMaximumOperations"],
                       "plannedOperations": len(operations), "withinBudget": bool(operations) is False or
                       len(operations) <= normalized["sourceMaximumOperations"]},
            "originalSoloFingerprint": protected if is_solo else None,
            "sourcePlanHash": "",
        }
        source["sourcePlanHash"] = _hash_without(source, "sourcePlanHash")
        source_plans.append(source)

    fragments: list[dict[str, Any]] = []
    for selection in candidate_variant["selections"]:
        request_id = str(selection["requestId"])
        marker, role = str(selection["marker"]), str(selection["role"])
        graph = graph_nodes[marker]
        binding = binding_by_request.get(request_id)
        candidate_decision = decision_index.get(("PATTERN_SELECTION", request_id))
        groove_decisions = groove_by_request.get(request_id, [])
        allowed_groove = [item for item in groove_decisions if item["disposition"] == "ALLOW"]
        locked = bool(selection.get("locked") or graph.get("locked") or
                      request_id in normalized["lockedFragmentIds"])
        candidate = candidate_lookup.get((request_id, str(selection["patternId"])))
        registry_name = "goldPerformance" if selection["sourceKind"] == "GOLD_PERFORMANCE" else "factoryStrumming"
        pattern = pattern_indexes[registry_name].get(str(selection["patternId"]))
        operations: list[dict[str, Any]] = []
        conflicts: list[str] = []
        binding_valid = binding is not None and binding["role"] == role
        if binding_valid and role in {"drums", "percussion"}:
            required_pitches = {int(note[2]) for note in (pattern or {}).get("notes", ()) if len(note) >= 3}
            covered = {int(profiles[item]["drumNote"]) for item in binding["factoryProfileIds"]
                       if profiles[item].get("kind") == "drum" and profiles[item].get("drumNote") is not None}
            if not required_pitches <= covered:
                binding_valid = False
                conflicts.append("DRUM_FACTORY_PROFILE_COVERAGE_INCOMPLETE")
        if locked:
            fragment_decision = "KEEP"
            reason = "USER_OR_GRAPH_LOCK"
        elif not candidate_decision or candidate_decision["disposition"] != "ALLOW":
            fragment_decision = "MANUAL_REVIEW"
            reason = "PATTERN_AUTHORITY_NOT_ALLOWED"
            conflicts.append("CANDIDATE_AUTHORITY_REQUIRED")
        elif not binding_valid:
            fragment_decision = "MANUAL_REVIEW"
            reason = "EXACT_TARGET_SOUNDBINDING_REQUIRED"
            conflicts.append("TARGET_SOUNDBINDING_REQUIRED")
        elif not candidate or not pattern:
            fragment_decision = "MANUAL_REVIEW"
            reason = "PATTERN_REGISTRY_LOOKUP_FAILED"
            conflicts.append("PATTERN_NOT_FOUND")
        else:
            profile_evidence_hash = _hash({
                "registrySha256": factory_registry["actualSha256"],
                "profiles": [profiles[item] for item in binding["factoryProfileIds"]],
                "binding": binding,
            })
            operations.append(_operation(
                kind="REPLACE_PATTERN", scope=request_id, authority="CANDIDATE_SELECTION",
                decision_ids=(candidate_decision["decisionId"],),
                evidence_hashes=(candidate_decision["decisionHash"],),
                limits={"patternId": selection["patternId"], "sourceKind": selection["sourceKind"],
                        "basePatternNotesExcludedFromTransformationBudget": True},
                predicted={"patternEventsMaximum": len(pattern.get("events", pattern.get("notes", ())))}))
            if allowed_groove:
                policy = role_policies[role]
                operations.append(_operation(
                    kind="APPLY_EVIDENCE_GROOVE", scope=request_id, authority="TIMING_ONLY",
                    decision_ids=(item["decisionId"] for item in allowed_groove),
                    evidence_hashes=(item["decisionHash"] for item in allowed_groove),
                    limits={"microtimingLimitTicks": policy["microtimingLimitTicks"],
                            "gateVariationLimitPercent": policy["gateVariationLimitPercent"],
                            "velocityChanges": 0},
                    predicted={"timingEventsMaximum": len(allowed_groove),
                               "gateEventsMaximum": len(allowed_groove)}))
            operations.append(_operation(
                kind="APPLY_FACTORY_DYNAMICS_MIXER", scope=request_id,
                authority="FACTORY_DYNAMICS", decision_ids=(candidate_decision["decisionId"],),
                evidence_hashes=(factory_registry["actualSha256"], profile_evidence_hash),
                limits={"factoryProfileIds": binding["factoryProfileIds"],
                        "velocityStrength": normalized["velocityStrength"],
                        "mixerStrength": normalized["mixerStrength"], "goldAuthority": False},
                predicted={"velocityNotesMaximum": len(pattern.get("notes", ())),
                           "controllerChangesMaximum": 2}))
            if role not in {"drums", "percussion"}:
                register = next(item for item in graph["registerPlan"] if item["role"] == role)
                operations.append(_operation(
                    kind="REPAIR_REGISTER", scope=request_id, authority="GLOBAL_PLAN",
                    decision_ids=(candidate_decision["decisionId"],),
                    evidence_hashes=(chain["arrangementGraph"], profile_evidence_hash),
                    limits={"low": register["low"], "high": register["high"],
                            "octaveFoldMaximum": 2}, predicted={"registerChecks": 1}))
            operations.append(_operation(
                kind="VERIFY_POLYPHONY", scope=request_id, authority="GLOBAL_PLAN",
                decision_ids=(candidate_decision["decisionId"],),
                evidence_hashes=(chain["arrangementGraph"], chain["groovePlan"]),
                limits={"maximumConcurrentMidiNotes": min(
                    normalized["softwareMidiNoteCeiling"], graph["polyphonyBudget"]["maximumConcurrentMidiNotes"]),
                    "deviceVoiceCostConfirmed": False}, predicted={"polyphonyChecks": 1}))
            maximum_operations = int(graph["transformationBudget"]["maximumOperations"])
            counted = sum(item["kind"] != "VERIFY_POLYPHONY" for item in operations)
            if counted > maximum_operations:
                operations = []
                fragment_decision = "MANUAL_REVIEW"
                reason = "FRAGMENT_OPERATION_BUDGET_EXCEEDED"
                conflicts.append("TRANSFORMATION_BUDGET_EXCEEDED")
            else:
                fragment_decision = "REPLACE"
                reason = "EVIDENCE_AUTHORIZED_REPLACEMENT"
        budget = {
            "maximumOperations": int(graph["transformationBudget"]["maximumOperations"]),
            "plannedOperations": sum(item["kind"] != "VERIFY_POLYPHONY" for item in operations),
            "maximumAddedNotes": int(graph["transformationBudget"]["maximumAddedNotes"]),
            "plannedAddedNotes": 0,
            "softwareMidiNoteCeiling": normalized["softwareMidiNoteCeiling"],
            "withinBudget": bool(operations) is False or
                            sum(item["kind"] != "VERIFY_POLYPHONY" for item in operations) <=
                            int(graph["transformationBudget"]["maximumOperations"]),
        }
        fragment = {
            "fragmentId": "fragment-" + _hash(request_id)[:20],
            "requestId": request_id, "marker": marker, "elementType": graph["elementType"],
            "role": role, "targetBinding": binding, "patternId": selection["patternId"],
            "sourceKind": selection["sourceKind"], "decision": fragment_decision,
            "reasonCode": reason, "locked": locked, "operations": operations,
            "budget": budget, "conflicts": sorted(set(conflicts)), "fragmentHash": "",
        }
        fragment["fragmentHash"] = _hash_without(fragment, "fragmentHash")
        fragments.append(fragment)

    missing_bindings = sorted(set(binding_by_request) - {item["requestId"] for item in fragments})
    if missing_bindings:
        raise ValueError("Target bindings do not belong to selected candidate variant")

    all_operations = [operation for item in source_plans for operation in item["operations"]] + \
                     [operation for item in fragments for operation in item["operations"]]
    operation_counts = dict(sorted(Counter(item["kind"] for item in all_operations).items()))
    source_decisions = dict(sorted(Counter(item["decision"] for item in source_plans).items()))
    fragment_decisions = dict(sorted(Counter(item["decision"] for item in fragments).items()))
    predicted = Counter()
    for operation in all_operations:
        predicted.update(operation["predicted"])
    dry_run = {
        "schema": DRY_RUN_SCHEMA, "version": DRY_RUN_VERSION,
        "sourceMidiSha256": chain["sourceMidiSha256"], "candidateMidiGenerated": False,
        "midiBytesWritten": 0, "operationCounts": operation_counts,
        "sourceDecisionCounts": source_decisions, "fragmentDecisionCounts": fragment_decisions,
        "predictedMaximumChanges": dict(sorted(predicted.items())),
        "protectedOriginalNoteChanges": 0, "soundBindingChanges": 0,
        "goldVelocityChanges": 0, "goldBankProgramChanges": 0,
        "blockedOperationCount": sum(item["decision"] == "MANUAL_REVIEW" for item in source_plans + fragments),
        "dryRunHash": "",
    }
    dry_run["dryRunHash"] = _hash_without(dry_run, "dryRunHash")
    manual_review = [
        {"scope": item.get("requestId", item.get("trackUid")), "reasonCode": item["reasonCode"]}
        for item in [*source_plans, *fragments] if item["decision"] == "MANUAL_REVIEW"
    ]
    arranger_ready = all(item["decision"] in {"KEEP", "REPLACE"} for item in fragments)
    plan = {
        "schema": TRACK_PLAN_SCHEMA, "version": TRACK_PLAN_VERSION,
        "planId": "track-plan-" + _hash([chain, normalized, target_bindings])[:20],
        "source": {**chain, "chainHash": _hash(chain)},
        "controls": normalized,
        "targetBindings": [binding_by_request[key] for key in sorted(binding_by_request)],
        "sourceTrackPlans": source_plans, "fragments": fragments,
        "dryRun": dry_run,
        "partialInvalidation": {
            "strategy": "CONTENT_HASH_DOWNSTREAM_ONLY",
            "nodes": ["source", "analysis", "evidence-ledger", "track-plan", "renderer"],
            "selectedVariantChangeInvalidates": ["track-plan.fragments", "renderer"],
            "trackLockChangeInvalidates": ["track-plan.sourceTrackPlans", "renderer"],
            "unchangedFragmentHashesReusable": True,
        },
        "readiness": {
            "arrangerFragmentsReady": arranger_ready,
            "sourceOptimizationManualReviewCount": sum(item["decision"] == "MANUAL_REVIEW" for item in source_plans),
            "readyForDeterministicRenderer": arranger_ready,
            "finalMidiExportAllowed": False,
        },
        "manualReview": manual_review,
        "safety": {
            "readOnly": True, "midiMutationAllowed": False, "midiBytesWritten": 0,
            "originalMidiOverwritten": False, "originalSoloFingerprintProtected": True,
            "lockedFragmentsProtected": True, "exactSoundBindingRequired": True,
            "goldAffectsDynamics": False, "goldAffectsMixer": False,
            "approximateSoundBindingAllowed": False, "implicitFallbackAllowed": False,
            "rendererAuthority": False, "validatorAuthority": False,
            "finalMidiExportAllowed": False,
        },
        "trackPlanHash": "",
    }
    plan["trackPlanHash"] = _hash_without(plan, "trackPlanHash")
    validate_track_plan_v3(plan)
    return plan


def validate_optimizer_operation(value: Mapping[str, Any]) -> None:
    required = {"schema", "version", "operationId", "kind", "scope", "status", "authority",
                "decisionIds", "evidenceHashes", "limits", "predicted", "operationHash"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("OptimizerOperation fields are strict")
    if value["schema"] != OPTIMIZER_OPERATION_SCHEMA or value["version"] != OPTIMIZER_OPERATION_VERSION:
        raise ValueError("OptimizerOperation schema/version mismatch")
    if value["kind"] not in OPERATION_KINDS or value["status"] != "PLANNED":
        raise ValueError("OptimizerOperation enum mismatch")
    if not value["evidenceHashes"] or any(not _HEX64.fullmatch(str(item)) for item in value["evidenceHashes"]):
        raise ValueError("OptimizerOperation requires valid evidence hashes")
    if value["operationHash"] != _hash_without(value, "operationHash"):
        raise ValueError("OptimizerOperation hash mismatch")


def validate_track_plan_v3(value: Mapping[str, Any]) -> None:
    required = {"schema", "version", "planId", "source", "controls", "targetBindings",
                "sourceTrackPlans", "fragments", "dryRun", "partialInvalidation", "readiness",
                "manualReview", "safety", "trackPlanHash"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("TrackPlan fields are strict")
    if value["schema"] != TRACK_PLAN_SCHEMA or value["version"] != TRACK_PLAN_VERSION:
        raise ValueError("TrackPlan schema/version mismatch")
    if not _HEX64.fullmatch(str(value["source"].get("chainHash", ""))):
        raise ValueError("TrackPlan chain hash is invalid")
    for collection in (value["sourceTrackPlans"], value["fragments"]):
        for item in collection:
            if item["decision"] not in TRACK_PLAN_DECISIONS:
                raise ValueError("TrackPlan decision is invalid")
            for operation in item["operations"]:
                validate_optimizer_operation(operation)
            if not item["budget"]["withinBudget"]:
                raise ValueError("TrackPlan contains an over-budget operation set")
    dry_run = value["dryRun"]
    if dry_run["schema"] != DRY_RUN_SCHEMA or dry_run["version"] != DRY_RUN_VERSION \
            or dry_run["candidateMidiGenerated"] or dry_run["midiBytesWritten"] != 0:
        raise ValueError("TrackPlan dry-run boundary violated")
    if dry_run["dryRunHash"] != _hash_without(dry_run, "dryRunHash"):
        raise ValueError("TrackPlan dry-run hash mismatch")
    safety = value["safety"]
    forbidden_true = ("midiMutationAllowed", "goldAffectsDynamics", "goldAffectsMixer",
                      "approximateSoundBindingAllowed", "implicitFallbackAllowed",
                      "rendererAuthority", "validatorAuthority", "finalMidiExportAllowed")
    if safety["readOnly"] is not True or safety["midiBytesWritten"] != 0 \
            or any(safety[key] for key in forbidden_true):
        raise ValueError("TrackPlan safety boundary violated")
    if value["trackPlanHash"] != _hash_without(value, "trackPlanHash"):
        raise ValueError("TrackPlan hash mismatch")


def execute_track_plan_api(payload: Mapping[str, Any], root: str | Path = ".") -> dict[str, Any]:
    allowed = {"midiBase64", "documents", "evidenceLedger", "targetBindings", "controls"}
    if not isinstance(payload, Mapping) or set(payload) - allowed or not {
        "midiBase64", "documents", "evidenceLedger", "targetBindings"
    } <= set(payload):
        raise ValueError("TrackPlan API payload fields are strict")
    try:
        midi_bytes = base64.b64decode(str(payload["midiBase64"]), validate=True)
    except Exception as exc:
        raise ValueError("TrackPlan MIDI must be valid base64") from exc
    return build_track_plan(midi_bytes, payload["documents"], payload["evidenceLedger"],
                            payload["targetBindings"], payload.get("controls"), root)


def execute_track_plan_gui(payload: Mapping[str, Any], root: str | Path = ".") -> dict[str, Any]:
    return execute_track_plan_api(payload, root)