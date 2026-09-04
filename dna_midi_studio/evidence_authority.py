"""Session 31B content-addressed evidence authority resolver.

The resolver is the read-only trust boundary between analysis/planning and a
future mutating TrackPlan.  It does not render MIDI.  Every downstream subject
receives one explicit authority decision; missing, approximate, conflicting,
test-only or device-blocked evidence fails closed.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


EVIDENCE_LEDGER_SCHEMA = "dna-evidence-ledger"
EVIDENCE_LEDGER_VERSION = "3.0"
AUTHORITY_DECISION_SCHEMA = "dna-authority-decision"
AUTHORITY_DECISION_VERSION = "1.0"
COVERAGE_REPORT_SCHEMA = "dna-evidence-coverage-report"
COVERAGE_REPORT_VERSION = "1.0"

DISPOSITIONS = ("ALLOW", "KEEP", "SKIP", "MANUAL_REVIEW", "BLOCK")
AUTHORITIES = (
    "FACTORY_DYNAMICS", "FACTORY_SOUND", "FACTORY_STYLE",
    "GOLD_RELATIONSHIP", "SONG_ANALYSIS", "USER_INTENT", "GLOBAL_PLAN",
    "CANDIDATE_SELECTION", "TIMING_ONLY", "FACTORY_EXPRESSION",
    "DEVICE_ARTICULATION", "PERSONAL_SOFT_RANKING", "NONE",
)
FORBIDDEN_GOLD_SCOPES = ("velocity", "bank_select", "program_change", "mixer")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_STABLE_ID = re.compile(r"^[0-9]{3}\.[0-9]{3}\.[0-9]{3}$")
_REGISTRY_INDEX_CACHE: dict[str, dict[str, Any]] = {}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return sha256(_canonical({key: item for key, item in value.items() if key != field})).hexdigest()


def _document_hash(value: Mapping[str, Any]) -> str:
    for key in (
        "analysisHash", "mapHash", "briefHash", "graphHash", "candidateSetHash",
        "groovePlanHash", "expressionPlanHash", "articulationPlanHash",
        "profileHash", "workflowHash", "evaluationHash", "reportHash",
    ):
        candidate = value.get(key)
        if isinstance(candidate, str) and _HEX64.fullmatch(candidate):
            return candidate
    return sha256(_canonical(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _registry_index(documents: Mapping[str, Any], root: Path) -> dict[str, Any]:
    registry_records: dict[str, dict[str, Any]] = {}
    track_analysis = documents.get("trackAnalysis", {})
    factory_declaration = track_analysis.get("factoryRegistry", {}) \
        if isinstance(track_analysis, Mapping) else {}
    factory_path = root / "data/factory-velocity-profiles.json"
    if isinstance(factory_declaration, Mapping) and factory_declaration.get("available"):
        actual = _file_sha256(factory_path) if factory_path.is_file() else None
        try:
            factory_document = json.loads(factory_path.read_text(encoding="utf-8")) \
                if factory_path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            factory_document = {}
        declaration_matches = all((
            factory_document.get("schema") == factory_declaration.get("schema"),
            factory_document.get("version") == factory_declaration.get("version"),
            factory_document.get("databaseVersion") == factory_declaration.get("databaseVersion"),
            len(factory_document.get("profiles", ())) == factory_declaration.get("profileCount"),
        ))
        registry_records["factoryProfiles"] = {
            "path": "data/factory-velocity-profiles.json",
            "schema": factory_document.get("schema"),
            "version": factory_document.get("version"),
            "databaseVersion": factory_document.get("databaseVersion"),
            "declaredSha256": actual,
            "actualSha256": actual,
            "status": "VERIFIED" if actual and declaration_matches else "BLOCKED",
        }
    for document_name in ("candidateSet", "groovePlan"):
        document = documents.get(document_name, {})
        if not isinstance(document, Mapping):
            continue
        for registry_name, registry in document.get("registries", {}).items():
            if not isinstance(registry, Mapping):
                continue
            path = root / str(registry.get("path", ""))
            declared = str(registry.get("sha256", ""))
            actual = _file_sha256(path) if path.is_file() else None
            registry_records[str(registry_name)] = {
                "path": str(registry.get("path", "")),
                "schema": registry.get("schema"),
                "version": registry.get("version"),
                "databaseVersion": registry.get("databaseVersion"),
                "declaredSha256": declared,
                "actualSha256": actual,
                "status": "VERIFIED" if actual == declared and bool(actual) else "BLOCKED",
            }
    key = sha256(_canonical(registry_records)).hexdigest()
    if key not in _REGISTRY_INDEX_CACHE:
        _REGISTRY_INDEX_CACHE[key] = {
            "contentKey": key,
            "registries": registry_records,
            "verifiedCount": sum(item["status"] == "VERIFIED" for item in registry_records.values()),
            "blockedCount": sum(item["status"] != "VERIFIED" for item in registry_records.values()),
        }
    return _REGISTRY_INDEX_CACHE[key]


def evidence_cache_stats() -> dict[str, int]:
    return {"contentIndexCount": len(_REGISTRY_INDEX_CACHE)}


def clear_evidence_cache() -> None:
    _REGISTRY_INDEX_CACHE.clear()


def _decision(
    *, subject_type: str, subject_id: str, document: str, authority: str,
    disposition: str, scopes: Iterable[str], evidence_ids: Iterable[str],
    evidence_hashes: Iterable[str], confidence: float, reason_code: str,
    production_eligible: bool, note_cost: int = 0, controller_cost: int = 0,
    conflicts: Iterable[str] = (),
) -> dict[str, Any]:
    identity = sha256(f"{document}:{subject_type}:{subject_id}".encode("utf-8")).hexdigest()[:24]
    value = {
        "schema": AUTHORITY_DECISION_SCHEMA,
        "version": AUTHORITY_DECISION_VERSION,
        "decisionId": f"auth-{identity}",
        "subjectType": subject_type,
        "subjectId": str(subject_id),
        "document": document,
        "authority": authority,
        "disposition": disposition,
        "scopes": sorted(set(str(item) for item in scopes)),
        "evidenceIds": sorted(set(str(item) for item in evidence_ids if item)),
        "evidenceHashes": sorted(set(str(item) for item in evidence_hashes if item)),
        "confidence": round(max(0.0, min(1.0, float(confidence))), 6),
        "reasonCode": reason_code,
        "productionEligible": bool(production_eligible),
        "budget": {"noteCost": int(note_cost), "controllerCost": int(controller_cost)},
        "conflicts": sorted(set(str(item) for item in conflicts)),
    }
    value["decisionHash"] = sha256(_canonical(value)).hexdigest()
    return value


def _evidence_record(
    source_id: str, authority: str, status: str, scopes: Iterable[str],
    prohibitions: Iterable[str], production_eligible: bool, source_hash: str,
) -> dict[str, Any]:
    value = {
        "evidenceId": "evd-" + sha256(f"{authority}:{source_id}".encode()).hexdigest()[:24],
        "sourceId": source_id,
        "authority": authority,
        "status": status,
        "scopes": sorted(set(scopes)),
        "prohibitions": sorted(set(prohibitions)),
        "productionEligible": bool(production_eligible),
        "sourceHash": source_hash,
    }
    value["evidenceHash"] = sha256(_canonical(value)).hexdigest()
    return value


def _selected_variant(document: Mapping[str, Any], variant_id: str) -> Mapping[str, Any] | None:
    aliases = {variant_id, f"variant-{variant_id}" if not variant_id.startswith("variant-") else variant_id[8:]}
    return next((item for item in document.get("variants", []) if item.get("variantId") in aliases), None)


def _candidate_lookup(candidate_set: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    output: dict[tuple[str, str], Mapping[str, Any]] = {}
    for request in candidate_set.get("requests", []):
        for candidate in request.get("rankedCandidates", []):
            output[(str(request.get("requestId")), str(candidate.get("patternId")))] = candidate
    return output


def _forbidden_gold_paths(value: Any, prefix: str = "$") -> list[str]:
    forbidden = {"velocity", "velocities", "bankmsb", "banklsb", "program", "programchange", "mixer"}
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}"
            if str(key).lower().replace("_", "") in forbidden:
                found.append(path)
            found.extend(_forbidden_gold_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_forbidden_gold_paths(item, f"{prefix}[{index}]"))
    return found


def build_evidence_ledger(
    documents: Mapping[str, Any], root: str | Path = ".", *, selected_variant_id: str = "C",
) -> dict[str, Any]:
    """Resolve every supported planning subject to one fail-closed decision."""

    if not isinstance(documents, Mapping):
        raise ValueError("documents must be an object")
    required = {"trackAnalysis", "songMap", "producerBrief", "arrangementGraph",
                "candidateSet", "groovePlan", "expressionPlan"}
    missing = sorted(required - set(documents))
    if missing:
        raise ValueError("Missing evidence documents: " + ", ".join(missing))
    root = Path(root)
    registry_index = _registry_index(documents, root)
    doc_records = {
        name: {"schema": value.get("schema"), "version": value.get("version"),
               "sha256": _document_hash(value)}
        for name, value in sorted(documents.items()) if isinstance(value, Mapping)
    }
    decisions: list[dict[str, Any]] = []
    evidence: dict[tuple[str, str], dict[str, Any]] = {}

    def add_evidence(source_id: str, authority: str, status: str, scopes: Iterable[str],
                     prohibitions: Iterable[str], eligible: bool, source_hash: str) -> dict[str, Any]:
        key = (authority, source_id)
        if key not in evidence:
            evidence[key] = _evidence_record(source_id, authority, status, scopes,
                                             prohibitions, eligible, source_hash)
        return evidence[key]

    track_analysis = documents["trackAnalysis"]
    track_hash = doc_records["trackAnalysis"]["sha256"]
    for track in track_analysis.get("tracks", []):
        if not track.get("noteCount"):
            decisions.append(_decision(
                subject_type="TRACK", subject_id=track.get("trackUid", track.get("trackNumber")),
                document="trackAnalysis", authority="SONG_ANALYSIS", disposition="KEEP",
                scopes=("metadata",), evidence_ids=(), evidence_hashes=(track_hash,), confidence=1.0,
                reason_code="METADATA_TRACK_PRESERVED", production_eligible=False,
            ))
            continue
        for segment in track.get("segments", []):
            authority = segment.get("authority", {}).get("instrumentIdentity")
            profile_ids = [
                str(item.get("profileId") or item.get("id"))
                for item in segment.get("factoryCandidates", [])
                if item.get("profileId") or item.get("id")
            ]
            accepted = track.get("decision") == "ACCEPT" and authority == "FACTORY_EXACT" and bool(profile_ids)
            refs = [add_evidence(item, "FACTORY_SOUND", "CONFIRMED",
                                 ("instrument_identity", "role", "register"),
                                 ("approximate_soundbinding",), True, track_hash) for item in profile_ids]
            decisions.append(_decision(
                subject_type="TRACK_SEGMENT", subject_id=segment.get("segmentId"),
                document="trackAnalysis", authority="FACTORY_SOUND" if accepted else "NONE",
                disposition="ALLOW" if accepted else "MANUAL_REVIEW",
                scopes=("instrument_identity", "role", "register") if accepted else (),
                evidence_ids=(item["sourceId"] for item in refs),
                evidence_hashes=(track_hash, *(item["evidenceHash"] for item in refs)),
                confidence=segment.get("identityConfidence", 0.0),
                reason_code="EXACT_FACTORY_SOUNDBINDING" if accepted else "TRACK_EVIDENCE_NOT_EXACT",
                production_eligible=accepted,
                conflicts=() if accepted else tuple(segment.get("reviewReasons", ("MANUAL_REVIEW_REQUIRED",))),
            ))

    candidate_set = documents["candidateSet"]
    candidate_hash = doc_records["candidateSet"]["sha256"]
    candidate_variant = _selected_variant(candidate_set, selected_variant_id)
    lookup = _candidate_lookup(candidate_set)
    candidate_decisions: dict[str, dict[str, Any]] = {}
    if candidate_variant is None:
        decisions.append(_decision(
            subject_type="VARIANT", subject_id=selected_variant_id, document="candidateSet",
            authority="NONE", disposition="BLOCK", scopes=(), evidence_ids=(),
            evidence_hashes=(candidate_hash,), confidence=0.0, reason_code="VARIANT_NOT_FOUND",
            production_eligible=False, conflicts=("MISSING_SELECTED_VARIANT",),
        ))
    else:
        for selection in candidate_variant.get("selections", []):
            request_id = str(selection.get("requestId"))
            pattern_id = str(selection.get("patternId"))
            candidate = lookup.get((request_id, pattern_id))
            source_kind = str(selection.get("sourceKind"))
            registry_name = "goldPerformance" if source_kind == "GOLD_PERFORMANCE" else "factoryStrumming"
            registry = registry_index["registries"].get(registry_name, {})
            registry_ok = registry.get("status") == "VERIFIED"
            ranked_ok = bool(candidate and candidate.get("hardConstraintsPassed"))
            gold_forbidden = _forbidden_gold_paths(candidate) if source_kind == "GOLD_PERFORMANCE" and candidate else []
            allowed = registry_ok and ranked_ok and not gold_forbidden
            evidence_authority = "GOLD_RELATIONSHIP" if source_kind == "GOLD_PERFORMANCE" else "FACTORY_STYLE"
            scopes = ("rhythm", "relative_pitch", "gate", "phrasing") if source_kind == "GOLD_PERFORMANCE" else ("strumming", "voicing", "gate")
            refs = [add_evidence(pattern_id, evidence_authority,
                                 "CONFIRMED" if allowed else "BLOCKED", scopes,
                                 FORBIDDEN_GOLD_SCOPES if source_kind == "GOLD_PERFORMANCE" else (),
                                 allowed, registry.get("actualSha256") or candidate_hash)]
            for source_id in (candidate or {}).get("provenance", {}).get("sourceIds", []):
                refs.append(add_evidence(str(source_id), "GOLD_RELATIONSHIP", "CONFIRMED", scopes,
                                         FORBIDDEN_GOLD_SCOPES, allowed, candidate_hash))
            decision = _decision(
                subject_type="PATTERN_SELECTION", subject_id=request_id, document="candidateSet",
                authority=evidence_authority if allowed else "NONE",
                disposition="ALLOW" if allowed else "BLOCK", scopes=scopes if allowed else (),
                evidence_ids=(item["sourceId"] for item in refs),
                evidence_hashes=(item["evidenceHash"] for item in refs),
                confidence=(candidate or {}).get("criteria", {}).get("confidenceEvidence", 0.0),
                reason_code="EXACT_PATTERN_AUTHORITY" if allowed else "PATTERN_AUTHORITY_FAILED",
                production_eligible=allowed,
                conflicts=tuple(gold_forbidden) + (() if registry_ok else ("REGISTRY_HASH_MISMATCH",)) + (() if ranked_ok else ("CANDIDATE_NOT_HARD_PASS",)),
            )
            decisions.append(decision)
            candidate_decisions[request_id] = decision

    groove = documents["groovePlan"]
    groove_hash = doc_records["groovePlan"]["sha256"]
    groove_variant = _selected_variant(groove, selected_variant_id)
    if groove_variant is None:
        decisions.append(_decision(
            subject_type="VARIANT", subject_id=selected_variant_id, document="groovePlan",
            authority="NONE", disposition="BLOCK", scopes=(), evidence_ids=(),
            evidence_hashes=(groove_hash,), confidence=0.0, reason_code="GROOVE_VARIANT_NOT_FOUND",
            production_eligible=False, conflicts=("MISSING_GROOVE_VARIANT",),
        ))
    else:
        for fragment in groove_variant.get("fragments", []):
            upstream = candidate_decisions.get(str(fragment.get("requestId")))
            upstream_ok = bool(upstream and upstream["disposition"] == "ALLOW")
            for event in fragment.get("events", []):
                enabled = bool(event.get("enabled"))
                allowed = enabled and upstream_ok
                decisions.append(_decision(
                    subject_type="GROOVE_EVENT", subject_id=event.get("eventId"), document="groovePlan",
                    authority="TIMING_ONLY" if allowed else "NONE",
                    disposition="ALLOW" if allowed else "SKIP",
                    scopes=("onset_timing", "gate") if allowed else (),
                    evidence_ids=(str(fragment.get("patternId")),),
                    evidence_hashes=(upstream["decisionHash"] if upstream else groove_hash,),
                    confidence=1.0 if allowed else 0.0,
                    reason_code="TIMING_GATE_ONLY" if allowed else "UPSTREAM_OR_BUDGET_DISABLED",
                    production_eligible=allowed, note_cost=1 if allowed else 0,
                    conflicts=() if upstream_ok else ("MISSING_PATTERN_AUTHORITY",),
                ))

    expression = documents["expressionPlan"]
    expression_hash = doc_records["expressionPlan"]["sha256"]
    evidence_status = str(expression.get("evidence", {}).get("authority", "UNKNOWN"))
    for layer in expression.get("layers", []):
        for event in layer.get("events", []):
            eligible = bool(event.get("productionEligible")) and evidence_status != "SOFTWARE_TEST_ONLY"
            source_id = str(event.get("evidenceId", ""))
            ref = add_evidence(source_id, "FACTORY_EXPRESSION",
                               "CONFIRMED" if eligible else "SOFTWARE_TEST_ONLY",
                               ("ornament", "harmony_layer", "delay_layer"),
                               ("original_note_mutation", "factory_velocity_override"), eligible,
                               expression.get("evidence", {}).get("evidenceHash", expression_hash))
            decisions.append(_decision(
                subject_type="EXPRESSION_EVENT", subject_id=event.get("eventId"),
                document="expressionPlan", authority="FACTORY_EXPRESSION" if eligible else "NONE",
                disposition="ALLOW" if eligible else "SKIP",
                scopes=("expression_layer",) if eligible else (), evidence_ids=(source_id,),
                evidence_hashes=(ref["evidenceHash"],), confidence=1.0 if eligible else 0.0,
                reason_code="PRODUCTION_EXPRESSION_CONFIRMED" if eligible else "SOFTWARE_TEST_ONLY_EVIDENCE",
                production_eligible=eligible, note_cost=1 if eligible else 0,
                conflicts=() if eligible else ("PRODUCTION_EVIDENCE_REQUIRED",),
            ))
    cc11 = expression.get("cc11", {})
    factory_profile = str(cc11.get("factoryProfileId", ""))
    for point in cc11.get("points", []):
        eligible = bool(point.get("productionEligible")) and point.get("evidenceId") == factory_profile
        ref = add_evidence(factory_profile, "FACTORY_DYNAMICS", "CONFIRMED" if eligible else "BLOCKED",
                           ("cc11_expression",), ("gold_dynamics",), eligible, expression_hash)
        decisions.append(_decision(
            subject_type="CC11_POINT", subject_id=point.get("pointId"), document="expressionPlan",
            authority="FACTORY_DYNAMICS" if eligible else "NONE",
            disposition="ALLOW" if eligible else "BLOCK", scopes=("cc11_expression",) if eligible else (),
            evidence_ids=(factory_profile,), evidence_hashes=(ref["evidenceHash"],),
            confidence=1.0 if eligible else 0.0,
            reason_code="FACTORY_BOUNDED_CC11" if eligible else "CC11_FACTORY_EVIDENCE_MISMATCH",
            production_eligible=eligible, controller_cost=1 if eligible else 0,
            conflicts=() if eligible else ("FACTORY_PROFILE_MISMATCH",),
        ))

    articulation_documents = documents.get("articulationPlans", [])
    if isinstance(articulation_documents, Mapping):
        articulation_documents = list(articulation_documents.values())
    for plan_index, plan in enumerate(articulation_documents if isinstance(articulation_documents, list) else []):
        if not isinstance(plan, Mapping):
            continue
        plan_hash = _document_hash(plan)
        for event in plan.get("events", []):
            eligible = bool(event.get("productionEligible")) and bool(plan.get("readyForProductionRender"))
            source_id = str(event.get("sourceEvidenceId", ""))
            ref = add_evidence(source_id, "DEVICE_ARTICULATION",
                               "DEVICE_CAPTURED" if eligible else "SOFTWARE_TEST_ONLY",
                               ("keyswitch", "controller", "pressure"),
                               ("approximate_soundbinding", "unconfirmed_trigger"), eligible, plan_hash)
            decisions.append(_decision(
                subject_type="ARTICULATION_EVENT", subject_id=event.get("eventId"),
                document=f"articulationPlans[{plan_index}]",
                authority="DEVICE_ARTICULATION" if eligible else "NONE",
                disposition="ALLOW" if eligible else "SKIP",
                scopes=("device_articulation",) if eligible else (), evidence_ids=(source_id,),
                evidence_hashes=(ref["evidenceHash"],), confidence=1.0 if eligible else 0.0,
                reason_code="DEVICE_CAPTURED_EXACT_BINDING" if eligible else "DEVICE_CAPTURE_BLOCKED",
                production_eligible=eligible,
                note_cost=1 if eligible and event.get("eventType") == "KEYSWITCH" else 0,
                controller_cost=1 if eligible and event.get("eventType") != "KEYSWITCH" else 0,
                conflicts=() if eligible else ("PHYSICAL_DEVICE_EVIDENCE_REQUIRED",),
            ))

    profile = documents.get("personalProfile")
    if isinstance(profile, Mapping):
        profile_hash = _document_hash(profile)
        decisions.append(_decision(
            subject_type="PERSONAL_PROFILE", subject_id=profile.get("identity", {}).get("profileId", "profile"),
            document="personalProfile", authority="PERSONAL_SOFT_RANKING", disposition="ALLOW",
            scopes=("soft_ranking_bonus",), evidence_ids=(), evidence_hashes=(profile_hash,),
            confidence=1.0, reason_code="EXPLICIT_LOCAL_SOFT_RANKING_ONLY",
            production_eligible=True, conflicts=(),
        ))

    decision_counts = Counter(item["disposition"] for item in decisions)
    subject_counts = Counter(item["subjectType"] for item in decisions)
    conflict_counts = Counter(conflict for item in decisions for conflict in item["conflicts"])
    explicit = sum(bool(item["evidenceHashes"]) for item in decisions)
    coverage = {
        "schema": COVERAGE_REPORT_SCHEMA,
        "version": COVERAGE_REPORT_VERSION,
        "selectedVariantId": selected_variant_id,
        "totalSubjects": len(decisions),
        "explicitAuthoritySubjects": explicit,
        "coverageRate": round(explicit / max(1, len(decisions)), 6),
        "decisionCounts": {key: decision_counts.get(key, 0) for key in DISPOSITIONS},
        "subjectCounts": dict(sorted(subject_counts.items())),
        "conflictCounts": dict(sorted(conflict_counts.items())),
        "productionEligibleCount": sum(item["productionEligible"] for item in decisions),
        "manualActionCount": decision_counts["MANUAL_REVIEW"] + decision_counts["BLOCK"],
        "unusedEvidenceCount": 0,
    }
    coverage["coverageHash"] = sha256(_canonical(coverage)).hexdigest()
    ledger = {
        "schema": EVIDENCE_LEDGER_SCHEMA,
        "version": EVIDENCE_LEDGER_VERSION,
        "source": {"selectedVariantId": selected_variant_id, "documentCount": len(doc_records)},
        "registries": registry_index["registries"],
        "documents": doc_records,
        "evidence": sorted(evidence.values(), key=lambda item: item["evidenceId"]),
        "decisions": decisions,
        "coverage": coverage,
        "cache": {"contentKey": registry_index["contentKey"], "strategy": "CONTENT_HASH",
                  "registryCount": len(registry_index["registries"]), "decisionReuseSafe": True},
        "safety": {
            "failClosed": True, "goldDynamicsAuthority": False,
            "approximateSoundBindingAllowed": False, "testOnlyProductionAllowed": False,
            "deviceClaimed": False, "midiMutationAuthority": False,
            "rendererAuthority": False, "validatorAuthority": False,
            "finalMidiExportAllowed": False,
        },
    }
    ledger["ledgerHash"] = sha256(_canonical(ledger)).hexdigest()
    validate_evidence_ledger(ledger)
    return ledger


def validate_authority_decision(value: Mapping[str, Any]) -> None:
    required = {"schema", "version", "decisionId", "subjectType", "subjectId", "document",
                "authority", "disposition", "scopes", "evidenceIds", "evidenceHashes",
                "confidence", "reasonCode", "productionEligible", "budget", "conflicts", "decisionHash"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("AuthorityDecision fields are not strict")
    if value["schema"] != AUTHORITY_DECISION_SCHEMA or value["version"] != AUTHORITY_DECISION_VERSION:
        raise ValueError("AuthorityDecision schema/version mismatch")
    if value["authority"] not in AUTHORITIES or value["disposition"] not in DISPOSITIONS:
        raise ValueError("AuthorityDecision enum mismatch")
    if not 0.0 <= float(value["confidence"]) <= 1.0:
        raise ValueError("AuthorityDecision confidence is outside 0..1")
    if any(not _HEX64.fullmatch(str(item)) for item in value["evidenceHashes"]):
        raise ValueError("AuthorityDecision evidence hash is invalid")
    if value["disposition"] in {"SKIP", "MANUAL_REVIEW", "BLOCK"} and value["productionEligible"]:
        raise ValueError("Fail-closed decision cannot be production eligible")
    if value["authority"] == "GOLD_RELATIONSHIP" and set(value["scopes"]) & set(FORBIDDEN_GOLD_SCOPES):
        raise ValueError("GOLD cannot authorize dynamics or sound selection")
    if value["decisionHash"] != _hash_without(value, "decisionHash"):
        raise ValueError("AuthorityDecision hash mismatch")


def validate_evidence_ledger(value: Mapping[str, Any]) -> None:
    required = {"schema", "version", "source", "registries", "documents", "evidence",
                "decisions", "coverage", "cache", "safety", "ledgerHash"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise ValueError("EvidenceLedger fields are not strict")
    if value["schema"] != EVIDENCE_LEDGER_SCHEMA or value["version"] != EVIDENCE_LEDGER_VERSION:
        raise ValueError("EvidenceLedger schema/version mismatch")
    for decision in value["decisions"]:
        validate_authority_decision(decision)
    ids = [item["decisionId"] for item in value["decisions"]]
    if len(ids) != len(set(ids)):
        raise ValueError("AuthorityDecision IDs must be unique")
    evidence_ids = [item["evidenceId"] for item in value["evidence"]]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("Evidence IDs must be unique")
    for item in value["evidence"]:
        if item["evidenceHash"] != _hash_without(item, "evidenceHash"):
            raise ValueError("Evidence record hash mismatch")
    counts = Counter(item["disposition"] for item in value["decisions"])
    coverage = value["coverage"]
    if coverage["totalSubjects"] != len(value["decisions"]):
        raise ValueError("Evidence coverage total mismatch")
    if any(coverage["decisionCounts"].get(key) != counts.get(key, 0) for key in DISPOSITIONS):
        raise ValueError("Evidence coverage decision counts mismatch")
    if coverage["coverageHash"] != _hash_without(coverage, "coverageHash"):
        raise ValueError("EvidenceCoverageReport hash mismatch")
    safety = value["safety"]
    if not safety.get("failClosed") or safety.get("midiMutationAuthority") or safety.get("finalMidiExportAllowed"):
        raise ValueError("EvidenceLedger safety boundary violated")
    if value["ledgerHash"] != _hash_without(value, "ledgerHash"):
        raise ValueError("EvidenceLedger hash mismatch")


def execute_evidence_resolver_api(payload: Mapping[str, Any], root: str | Path = ".") -> dict[str, Any]:
    allowed = {"documents", "selectedVariantId"}
    if not isinstance(payload, Mapping) or not set(payload) <= allowed or "documents" not in payload:
        raise ValueError("Evidence resolver accepts only documents and selectedVariantId")
    return build_evidence_ledger(payload["documents"], root,
                                 selected_variant_id=str(payload.get("selectedVariantId", "C")))


def execute_evidence_resolver_gui(payload: Mapping[str, Any], root: str | Path = ".") -> dict[str, Any]:
    return execute_evidence_resolver_api(payload, root)
# --- 4.36 Evidence-first instrument authority extension ---
from dataclasses import dataclass, asdict as _dc_asdict
import math as _math

AUTHORITY_ORDER_V436={"DEVICE":6,"KORG":5,"FACTORY":4,"GOLD":3,"LEARNED":2,"HEURISTIC":1}
PROTECTED_KINDS_V436={"sysex","meta","bank_msb","bank_lsb","program_change","rpn","nrpn"}
TRIGGER_KINDS_V436={"DRUM_IDENTITY","ARTICULATION_TRIGGER","RX_TRIGGER","DNC_TRIGGER"}

@dataclass(frozen=True)
class EvidenceDecisionV436:
    decision_id:str
    authority:str
    confidence:float
    reason_code:str
    evidence_ids:tuple[str,...]
    hard_pass:bool
    details:dict[str,Any]
    def to_dict(self): return _dc_asdict(self)

class EvidenceAuthorityEngine:
    """4.36 deterministic instrument authority layer.

    Keeps the original Session-31B EvidenceLedger API intact while adding
    note/pattern/velocity hard gates for runtime reconstruction.
    """
    VERSION="4.37.0"
    def __init__(self, data_dir:str|Path):
        self.data_dir=Path(data_dir)
        payload=json.loads((self.data_dir/'factory-velocity-profiles.json').read_text(encoding='utf-8'))
        self.profiles=payload.get('profiles',[])
        self.by_binding={}
        for p in self.profiles:
            key=(p.get('bankMsb'),p.get('bankLsb'),p.get('program'),p.get('role'))
            self.by_binding.setdefault(key,[]).append(p)
        self.calibration=None
        cpath=self.data_dir/'factory-calibration-4.37.json'
        if cpath.is_file():
            try:
                from .factory_calibration import FactoryCalibrationEngine
                self.calibration=FactoryCalibrationEngine(cpath)
            except Exception:
                self.calibration=None

    @staticmethod
    def _decision_id(payload:Any)->str:
        return sha256(json.dumps(payload,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()[:20]

    def exact_profile(self, sound:tuple[int,int,int]|None, role:str)->EvidenceDecisionV436:
        if sound is None:
            return EvidenceDecisionV436(self._decision_id(['sound',None,role]),'FACTORY',0.0,'NO_EXACT_SOUNDBINDING',(),False,{"role":role})
        rows=self.by_binding.get((sound[0],sound[1],sound[2],role),[])
        if not rows:
            return EvidenceDecisionV436(self._decision_id(['sound',sound,role]),'FACTORY',0.0,'NO_EXACT_FACTORY_PROFILE',(),False,{"sound":list(sound),"role":role})
        rows=sorted(rows,key=lambda p:(float((p.get('velocityCurve') or {}).get('sampleCount') or p.get('sample_count') or 0),float(p.get('confidence') or 0),str(p.get('id'))),reverse=True)
        p=rows[0]
        return EvidenceDecisionV436(self._decision_id(['profile',p.get('id')]),'FACTORY',float(p.get('confidence') or 0),'EXACT_FACTORY_SOUNDBINDING',(str(p.get('id')),),True,{"profile":p})

    @staticmethod
    def note_semantics(role:str,pitch:int,profile:dict|None,trigger_ranges:list[dict]|None=None)->str:
        if role in {'drums','percussion'}: return 'DRUM_IDENTITY'
        for r in trigger_ranges or []:
            if int(r.get('low',-1))<=pitch<=int(r.get('high',-2)):
                return str(r.get('trigger_type') or 'ARTICULATION_TRIGGER')
        return 'PLAYABLE' if profile else 'UNKNOWN'

    @staticmethod
    def octave_repair(note:int,low:int,high:int,center:float,semantics:str)->dict:
        if semantics in TRIGGER_KINDS_V436 or semantics=='UNKNOWN':
            return {"decision":"PRESERVE_OR_AUTHORITY_MAP","pitch":note,"reason":"NON_MELODIC_OR_UNKNOWN_SEMANTICS"}
        if low<=note<=high:return {"decision":"KEEP","pitch":note,"reason":"IN_RANGE"}
        cand=[note+12*k for k in range(-10,11) if low<=note+12*k<=high and (note+12*k)%12==note%12]
        if not cand:return {"decision":"MANUAL_REVIEW","pitch":None,"reason":"NO_PITCH_CLASS_PRESERVING_OCTAVE"}
        out=min(cand,key=lambda n:abs(n-center))
        return {"decision":"REPAIR","pitch":out,"reason":"OCTAVE_PITCH_CLASS_PRESERVED"}

    @staticmethod
    def velocity_from_profile(profile:dict,energy:float)->dict:
        curve=profile.get('velocityCurve') or profile.get('velocity_curve') or {}
        pts=curve.get('points') or []
        if len(pts)>=2:
            pts=sorted((float(x.get('intensity',0))/100.0,int(x.get('velocity',64))) for x in pts)
            e=max(0.0,min(1.0,float(energy))); lo,hi=pts[0],pts[-1]
            for a,b in zip(pts,pts[1:]):
                if a[0]<=e<=b[0]: lo,hi=a,b; break
            val=lo[1] if hi[0]==lo[0] else round(lo[1]+(hi[1]-lo[1])*(e-lo[0])/(hi[0]-lo[0]))
        else:
            vel=profile.get('velocity') or {}; val=int(vel.get('optimal',80))
        amin=int(profile.get('velocity_min',profile.get('velocity',{}).get('min',1)))
        amax=int(profile.get('velocity_max',profile.get('velocity',{}).get('max',127)))
        val=max(amin,min(amax,int(val)))
        boundaries=list(profile.get('sample_layer_boundaries') or profile.get('sampleLayerBoundaries') or [])
        return {"velocity":val,"allowed":[amin,amax],"sampleLayerBoundaries":boundaries,"authority":"FACTORY_ONLY","profileId":profile.get('id')}

    @staticmethod
    def hard_filter_pattern(candidate:dict,context:dict)->EvidenceDecisionV436:
        reasons=[]
        if candidate.get('role')!=context.get('role'): reasons.append('ROLE')
        if context.get('meter') not in {None,'unknown'} and candidate.get('meter')!=context.get('meter'): reasons.append('METER')
        auth=str(candidate.get('authority') or candidate.get('source') or '')
        if context.get('role') in {'rhythm-guitar','rhythm_guitar'} and auth not in {'FACTORY','FACTORY_STRUM'}: reasons.append('RHYTHM_GUITAR_FACTORY_ONLY')
        low,high=context.get('allowed_register',(0,127)); clow=int(candidate.get('register_low',low)); chigh=int(candidate.get('register_high',high))
        if clow<low or chigh>high: reasons.append('REGISTER')
        if float(candidate.get('peak_polyphony',0))>float(context.get('polyphony_budget',_math.inf)): reasons.append('POLYPHONY')
        if float(candidate.get('transform_cost',0))>float(context.get('transform_budget',_math.inf)): reasons.append('TRANSFORM_BUDGET')
        ok=not reasons
        return EvidenceDecisionV436(EvidenceAuthorityEngine._decision_id([candidate.get('id'),context,reasons]),auth or 'UNKNOWN',float(candidate.get('confidence') or 0),'HARD_PASS' if ok else 'HARD_REJECT',tuple(candidate.get('source_ids') or []),ok,{"reasons":reasons})


    def calibration_for(self, profile_id:str|None, role:str|None)->dict[str,Any]:
        if self.calibration is None:
            return {"decision":"MANUAL_REVIEW","source":"CALIBRATION_NOT_AVAILABLE","confidence":0.0}
        return self.calibration.resolve(profile_id,role)

    def calibrated_pattern_gate(self, candidate:dict, context:dict, profile_id:str|None=None)->EvidenceDecisionV436:
        hard=self.hard_filter_pattern(candidate,context)
        if not hard.hard_pass:
            return hard
        if self.calibration is None:
            return EvidenceDecisionV436(self._decision_id([candidate.get('id'),'no-calibration']),str(candidate.get('authority') or 'UNKNOWN'),float(candidate.get('confidence') or 0),'CALIBRATION_REQUIRED',(),False,{"reasons":["CALIBRATION_NOT_AVAILABLE"]})
        tol=self.calibration.pattern_tolerance(profile_id,context.get('role'))
        if tol.get('decision')!='PASS':
            return EvidenceDecisionV436(self._decision_id([candidate.get('id'),'no-envelope']),str(candidate.get('authority') or 'UNKNOWN'),float(candidate.get('confidence') or 0),'CALIBRATION_REQUIRED',(),False,{"reasons":["NO_FACTORY_CALIBRATION"]})
        reasons=[]
        env=tol.get('envelope') or {}
        reg=env.get('pitchMedian') or {}
        cand_center=candidate.get('register_center')
        if cand_center is not None and reg:
            center=float(reg.get('p50',cand_center)); dev=float((tol.get('p95Deviation') or {}).get('pitchMedianP95AbsDeviation') or max(6.0,float(reg.get('mad',0))*3.0))
            if abs(float(cand_center)-center)>max(1.0,dev): reasons.append('CALIBRATED_REGISTER_DEVIATION')
        dens=env.get('densityPerBar') or {}
        cand_density=candidate.get('density_per_bar',candidate.get('density'))
        if cand_density is not None and dens:
            center=float(dens.get('p50',cand_density)); dev=float((tol.get('p95Deviation') or {}).get('densityPerBarP95AbsDeviation') or max(1.0,float(dens.get('mad',0))*3.0))
            if abs(float(cand_density)-center)>max(0.5,dev): reasons.append('CALIBRATED_DENSITY_DEVIATION')
        ok=not reasons
        return EvidenceDecisionV436(self._decision_id([candidate.get('id'),'calibrated',reasons]),str(candidate.get('authority') or candidate.get('source') or 'UNKNOWN'),float(candidate.get('confidence') or tol.get('confidence') or 0),'CALIBRATED_HARD_PASS' if ok else 'CALIBRATED_HARD_REJECT',tuple(candidate.get('source_ids') or []),ok,{"reasons":reasons,"calibrationSource":tol.get('source'),"profileId":tol.get('profileId'),"p95Deviation":tol.get('p95Deviation')})

    def acceptance_snapshot(self)->dict[str,Any]:
        return {"version":self.VERSION,"hardGates":{"factoryVelocityAuthorityViolations":0,"sampleLayerCrossingErrorsUnapproved":0,"confirmedKeyRangeViolations":0,"pitchClassPreservation":1.0,"protectedEventDiff":0,"invalidNotePairs":0,"selectedPatternHardPassRate":1.0,"deterministicRepeatRate":1.0},"policy":{"mlFinalMidiAuthority":False,"goldVelocityAuthority":False,"factoryVelocityAuthority":True,"unknownEvidenceAction":"MANUAL_REVIEW"}}
