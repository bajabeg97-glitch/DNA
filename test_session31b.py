from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dna_midi_studio import (  # noqa: E402
    AUTHORITIES,
    AUTHORITY_DECISION_SCHEMA,
    AUTHORITY_DECISION_VERSION,
    COVERAGE_REPORT_SCHEMA,
    COVERAGE_REPORT_VERSION,
    DISPOSITIONS,
    EVIDENCE_LEDGER_SCHEMA,
    EVIDENCE_LEDGER_VERSION,
    FORBIDDEN_GOLD_SCOPES,
    build_evidence_ledger,
    clear_evidence_cache,
    evidence_cache_stats,
    execute_evidence_resolver_api,
    execute_evidence_resolver_gui,
    validate_authority_decision,
    validate_evidence_ledger,
)
from dna_midi_studio.session31b_fixture import build_session31b_chain  # noqa: E402


class Session31BEvidenceAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        clear_evidence_cache()
        cls.fixture = build_session31b_chain(ROOT)
        cls.ledger = cls.fixture["ledger"]
        cls.decisions = cls.ledger["decisions"]
        cls.by_type = {}
        for item in cls.decisions:
            cls.by_type.setdefault(item["subjectType"], []).append(item)


def _case(name, operation):
    def test(self):
        operation(self)
    test.__name__ = "test_" + name
    setattr(Session31BEvidenceAuthorityTests, test.__name__, test)


case_count = 0

# Contract constants and schemas: 20.
constant_cases = (
    ("001_ledger_schema", EVIDENCE_LEDGER_SCHEMA, "dna-evidence-ledger"),
    ("002_ledger_version", EVIDENCE_LEDGER_VERSION, "3.0"),
    ("003_decision_schema", AUTHORITY_DECISION_SCHEMA, "dna-authority-decision"),
    ("004_decision_version", AUTHORITY_DECISION_VERSION, "1.0"),
    ("005_coverage_schema", COVERAGE_REPORT_SCHEMA, "dna-evidence-coverage-report"),
    ("006_coverage_version", COVERAGE_REPORT_VERSION, "1.0"),
    ("007_disposition_count", len(DISPOSITIONS), 5),
    ("008_authority_count", len(AUTHORITIES), 13),
    ("009_gold_forbidden_count", len(FORBIDDEN_GOLD_SCOPES), 4),
)
for name, actual, expected in constant_cases:
    _case(name, lambda self, actual=actual, expected=expected: self.assertEqual(actual, expected)); case_count += 1

schema_paths = [
    ROOT / "premium/schemas/v2/evidence-ledger-v3.schema.json",
    ROOT / "premium/schemas/v2/authority-decision-v1.schema.json",
    ROOT / "premium/schemas/v2/evidence-coverage-report-v1.schema.json",
]
for index, path in enumerate(schema_paths, 10):
    _case(f"{index:03d}_schema_exists", lambda self, path=path: self.assertTrue(path.is_file())); case_count += 1
for index, path in enumerate(schema_paths, 13):
    _case(f"{index:03d}_schema_draft", lambda self, path=path: self.assertEqual(
        json.loads(path.read_text())["$schema"], "https://json-schema.org/draft/2020-12/schema")); case_count += 1
for index, path in enumerate(schema_paths, 16):
    _case(f"{index:03d}_schema_strict", lambda self, path=path: self.assertFalse(
        json.loads(path.read_text())["additionalProperties"])); case_count += 1
_case("019_ledger_validates", lambda self: validate_evidence_ledger(self.ledger)); case_count += 1
_case("020_first_decision_validates", lambda self: validate_authority_decision(self.decisions[0])); case_count += 1

# Ledger structure, coverage and registry identity: 30.
structure_cases = (
    ("021_ledger_schema_value", lambda s: s.ledger["schema"], EVIDENCE_LEDGER_SCHEMA),
    ("022_ledger_version_value", lambda s: s.ledger["version"], EVIDENCE_LEDGER_VERSION),
    ("023_document_count", lambda s: s.ledger["source"]["documentCount"], 7),
    ("024_selected_variant", lambda s: s.ledger["source"]["selectedVariantId"], "C"),
    ("025_registry_count", lambda s: len(s.ledger["registries"]), 3),
    ("026_registry_verified", lambda s: sum(x["status"] == "VERIFIED" for x in s.ledger["registries"].values()), 3),
    ("027_document_record_count", lambda s: len(s.ledger["documents"]), 7),
    ("028_total_subjects", lambda s: s.ledger["coverage"]["totalSubjects"], 1657),
    ("029_explicit_subjects", lambda s: s.ledger["coverage"]["explicitAuthoritySubjects"], 1657),
    ("030_coverage_rate", lambda s: s.ledger["coverage"]["coverageRate"], 1.0),
    ("031_allow_count", lambda s: s.ledger["coverage"]["decisionCounts"]["ALLOW"], 1559),
    ("032_keep_count", lambda s: s.ledger["coverage"]["decisionCounts"]["KEEP"], 1),
    ("033_skip_count", lambda s: s.ledger["coverage"]["decisionCounts"]["SKIP"], 94),
    ("034_review_count", lambda s: s.ledger["coverage"]["decisionCounts"]["MANUAL_REVIEW"], 3),
    ("035_block_count", lambda s: s.ledger["coverage"]["decisionCounts"]["BLOCK"], 0),
    ("036_track_segment_count", lambda s: s.ledger["coverage"]["subjectCounts"]["TRACK_SEGMENT"], 4),
    ("037_candidate_count", lambda s: s.ledger["coverage"]["subjectCounts"]["PATTERN_SELECTION"], 52),
    ("038_groove_count", lambda s: s.ledger["coverage"]["subjectCounts"]["GROOVE_EVENT"], 1490),
    ("039_expression_count", lambda s: s.ledger["coverage"]["subjectCounts"]["EXPRESSION_EVENT"], 83),
    ("040_cc11_count", lambda s: s.ledger["coverage"]["subjectCounts"]["CC11_POINT"], 16),
    ("041_articulation_count", lambda s: s.ledger["coverage"]["subjectCounts"]["ARTICULATION_EVENT"], 11),
    ("042_evidence_count", lambda s: len(s.ledger["evidence"]), 111),
    ("043_ledger_hash_length", lambda s: len(s.ledger["ledgerHash"]), 64),
    ("044_coverage_hash_length", lambda s: len(s.ledger["coverage"]["coverageHash"]), 64),
    ("045_cache_key_length", lambda s: len(s.ledger["cache"]["contentKey"]), 64),
    ("046_cache_strategy", lambda s: s.ledger["cache"]["strategy"], "CONTENT_HASH"),
    ("047_cache_reuse_safe", lambda s: s.ledger["cache"]["decisionReuseSafe"], True),
    ("048_production_eligible", lambda s: s.ledger["coverage"]["productionEligibleCount"], 1559),
    ("049_manual_action_count", lambda s: s.ledger["coverage"]["manualActionCount"], 3),
    ("050_unused_evidence", lambda s: s.ledger["coverage"]["unusedEvidenceCount"], 0),
)
for name, getter, expected in structure_cases:
    _case(name, lambda self, getter=getter, expected=expected: self.assertEqual(getter(self), expected)); case_count += 1

# Authority behavior and fail-closed semantics: 34.
behavior = (
    ("051_all_decisions_unique", lambda s: len({x["decisionId"] for x in s.decisions}) == len(s.decisions)),
    ("052_all_decision_hashes", lambda s: all(len(x["decisionHash"]) == 64 for x in s.decisions)),
    ("053_all_decision_schemas", lambda s: all(x["schema"] == AUTHORITY_DECISION_SCHEMA for x in s.decisions)),
    ("054_all_confidence_bounded", lambda s: all(0 <= x["confidence"] <= 1 for x in s.decisions)),
    ("055_fail_closed_true", lambda s: s.ledger["safety"]["failClosed"]),
    ("056_no_gold_dynamics", lambda s: not s.ledger["safety"]["goldDynamicsAuthority"]),
    ("057_no_approximate_binding", lambda s: not s.ledger["safety"]["approximateSoundBindingAllowed"]),
    ("058_no_test_only_production", lambda s: not s.ledger["safety"]["testOnlyProductionAllowed"]),
    ("059_no_device_claim", lambda s: not s.ledger["safety"]["deviceClaimed"]),
    ("060_no_midi_mutation_authority", lambda s: not s.ledger["safety"]["midiMutationAuthority"]),
    ("061_no_renderer_authority", lambda s: not s.ledger["safety"]["rendererAuthority"]),
    ("062_no_validator_authority", lambda s: not s.ledger["safety"]["validatorAuthority"]),
    ("063_final_export_blocked", lambda s: not s.ledger["safety"]["finalMidiExportAllowed"]),
    ("064_candidate_all_allowed", lambda s: all(x["disposition"] == "ALLOW" for x in s.by_type["PATTERN_SELECTION"])),
    ("065_candidate_evidence_present", lambda s: all(x["evidenceIds"] for x in s.by_type["PATTERN_SELECTION"])),
    ("066_gold_scopes_safe", lambda s: all(not set(x["scopes"]) & set(FORBIDDEN_GOLD_SCOPES) for x in s.decisions if x["authority"] == "GOLD_RELATIONSHIP")),
    ("067_groove_timing_only", lambda s: all(x["authority"] == "TIMING_ONLY" for x in s.by_type["GROOVE_EVENT"])),
    ("068_groove_scopes_limited", lambda s: all(set(x["scopes"]) <= {"onset_timing", "gate"} for x in s.by_type["GROOVE_EVENT"])),
    ("069_groove_note_cost", lambda s: all(x["budget"]["noteCost"] == 1 for x in s.by_type["GROOVE_EVENT"])),
    ("070_expression_test_only_skipped", lambda s: all(x["disposition"] == "SKIP" for x in s.by_type["EXPRESSION_EVENT"])),
    ("071_expression_not_eligible", lambda s: all(not x["productionEligible"] for x in s.by_type["EXPRESSION_EVENT"])),
    ("072_expression_reason", lambda s: all(x["reasonCode"] == "SOFTWARE_TEST_ONLY_EVIDENCE" for x in s.by_type["EXPRESSION_EVENT"])),
    ("073_cc11_factory_allowed", lambda s: all(x["authority"] == "FACTORY_DYNAMICS" for x in s.by_type["CC11_POINT"])),
    ("074_cc11_controller_cost", lambda s: all(x["budget"]["controllerCost"] == 1 for x in s.by_type["CC11_POINT"])),
    ("075_articulation_skipped", lambda s: all(x["disposition"] == "SKIP" for x in s.by_type["ARTICULATION_EVENT"])),
    ("076_articulation_device_blocked", lambda s: all("PHYSICAL_DEVICE_EVIDENCE_REQUIRED" in x["conflicts"] for x in s.by_type["ARTICULATION_EVENT"])),
    ("077_track_reviews_fail_closed", lambda s: sum(x["disposition"] == "MANUAL_REVIEW" for x in s.by_type["TRACK_SEGMENT"]) == 3 and sum(x["disposition"] == "ALLOW" for x in s.by_type["TRACK_SEGMENT"]) == 1),
    ("078_metadata_kept", lambda s: s.by_type["TRACK"][0]["disposition"] == "KEEP"),
    ("079_evidence_unique", lambda s: len({x["evidenceId"] for x in s.ledger["evidence"]}) == len(s.ledger["evidence"])),
    ("080_evidence_hashes", lambda s: all(len(x["evidenceHash"]) == 64 for x in s.ledger["evidence"])),
    ("081_gold_prohibitions", lambda s: all(set(FORBIDDEN_GOLD_SCOPES) <= set(x["prohibitions"]) for x in s.ledger["evidence"] if x["authority"] == "GOLD_RELATIONSHIP")),
    ("082_articulation_nonproduction", lambda s: all(not x["productionEligible"] for x in s.ledger["evidence"] if x["authority"] == "DEVICE_ARTICULATION")),
    ("083_conflict_expression_count", lambda s: s.ledger["coverage"]["conflictCounts"]["PRODUCTION_EVIDENCE_REQUIRED"] == 83),
    ("084_conflict_device_count", lambda s: s.ledger["coverage"]["conflictCounts"]["PHYSICAL_DEVICE_EVIDENCE_REQUIRED"] == 11),
)
for name, getter in behavior:
    _case(name, lambda self, getter=getter: self.assertTrue(getter(self))); case_count += 1

# Thirty independently hashed event decisions across all major authorities: 30.
for index in range(30):
    _case(f"{85 + index:03d}_sample_decision_valid", lambda self, index=index:
          validate_authority_decision(self.decisions[(index * 53) % len(self.decisions)])); case_count += 1

# Negative validation, API/GUI parity, cache and conflict behavior: 30.
_case("115_api_parity", lambda self: self.assertEqual(
    execute_evidence_resolver_api({"documents": self.fixture["documents"], "selectedVariantId": "C"}, ROOT),
    self.ledger)); case_count += 1
_case("116_gui_parity", lambda self: self.assertEqual(
    execute_evidence_resolver_gui({"documents": self.fixture["documents"], "selectedVariantId": "C"}, ROOT),
    self.ledger)); case_count += 1
_case("117_cache_populated", lambda self: self.assertGreaterEqual(evidence_cache_stats()["contentIndexCount"], 1)); case_count += 1
_case("118_repeat_deterministic", lambda self: self.assertEqual(
    build_evidence_ledger(self.fixture["documents"], ROOT), self.ledger)); case_count += 1
_case("119_variant_alias", lambda self: self.assertEqual(
    build_evidence_ledger(self.fixture["documents"], ROOT, selected_variant_id="variant-C")["coverage"]["totalSubjects"], 1657)); case_count += 1

def missing_document(self):
    value = dict(self.fixture["documents"]); value.pop("trackAnalysis")
    with self.assertRaises(ValueError): build_evidence_ledger(value, ROOT)
_case("120_missing_document_rejected", missing_document); case_count += 1

def api_extra(self):
    with self.assertRaises(ValueError): execute_evidence_resolver_api({"documents": {}, "extra": True}, ROOT)
_case("121_api_extra_rejected", api_extra); case_count += 1

def bad_variant(self):
    value = build_evidence_ledger(self.fixture["documents"], ROOT, selected_variant_id="Z")
    self.assertEqual(value["coverage"]["decisionCounts"]["BLOCK"], 2)
_case("122_missing_variant_blocks", bad_variant); case_count += 1

def registry_mismatch(self):
    docs = deepcopy(self.fixture["documents"])
    docs["candidateSet"]["registries"]["goldPerformance"]["sha256"] = "0" * 64
    docs["groovePlan"]["registries"]["goldPerformance"]["sha256"] = "0" * 64
    value = build_evidence_ledger(docs, ROOT)
    self.assertGreater(value["coverage"]["decisionCounts"]["BLOCK"], 0)
_case("123_registry_hash_fail_closed", registry_mismatch); case_count += 1

def mutate_and_reject(self, path, replacement):
    value = deepcopy(self.ledger)
    target = value
    for key in path[:-1]: target = target[key]
    target[path[-1]] = replacement
    with self.assertRaises(ValueError): validate_evidence_ledger(value)

negative_cases = (
    ("124_bad_ledger_hash", ["ledgerHash"], "0" * 64),
    ("125_bad_schema", ["schema"], "bad"),
    ("126_bad_version", ["version"], "9"),
    ("127_bad_coverage_total", ["coverage", "totalSubjects"], 1),
    ("128_bad_coverage_hash", ["coverage", "coverageHash"], "0" * 64),
    ("129_midi_authority_rejected", ["safety", "midiMutationAuthority"], True),
    ("130_export_authority_rejected", ["safety", "finalMidiExportAllowed"], True),
    ("131_fail_open_rejected", ["safety", "failClosed"], False),
    ("132_duplicate_decision_id", ["decisions", 1, "decisionId"], None),
    ("133_bad_decision_hash", ["decisions", 0, "decisionHash"], "0" * 64),
    ("134_bad_disposition", ["decisions", 0, "disposition"], "MAYBE"),
    ("135_bad_authority", ["decisions", 0, "authority"], "GUESS"),
    ("136_bad_confidence", ["decisions", 0, "confidence"], 2.0),
    ("137_bad_evidence_hash", ["evidence", 0, "evidenceHash"], "0" * 64),
    ("138_production_skip_rejected", ["decisions", -1, "productionEligible"], True),
)
for name, path, replacement in negative_cases:
    if name == "132_duplicate_decision_id":
        _case(name, lambda self: mutate_and_reject(self, ["decisions", 1, "decisionId"], self.ledger["decisions"][0]["decisionId"])); case_count += 1
    else:
        _case(name, lambda self, path=path, replacement=replacement: mutate_and_reject(self, path, replacement)); case_count += 1

def decision_extra(self):
    value = deepcopy(self.decisions[0]); value["extra"] = True
    with self.assertRaises(ValueError): validate_authority_decision(value)
_case("139_decision_extra_rejected", decision_extra); case_count += 1

def gold_dynamic_scope(self):
    value = deepcopy(next(x for x in self.decisions if x["authority"] == "GOLD_RELATIONSHIP"))
    value["scopes"].append("velocity")
    value["decisionHash"] = __import__("hashlib").sha256(json.dumps(
        {k: v for k, v in value.items() if k != "decisionHash"}, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    with self.assertRaises(ValueError): validate_authority_decision(value)
_case("140_gold_velocity_rejected", gold_dynamic_scope); case_count += 1

def bad_evidence_reference(self):
    value = deepcopy(self.decisions[0]); value["evidenceHashes"] = ["bad"]
    with self.assertRaises(ValueError): validate_authority_decision(value)
_case("141_bad_evidence_reference", bad_evidence_reference); case_count += 1

def invalid_documents_type(self):
    with self.assertRaises(ValueError): build_evidence_ledger([], ROOT)
_case("142_invalid_documents_type", invalid_documents_type); case_count += 1

_case("143_source_midi_unchanged", lambda self: self.assertEqual(
    self.fixture["midi"].digest(), self.fixture["trackAnalysis"]["sourceSha256"])); case_count += 1
_case("144_no_output_bytes", lambda self: self.assertNotIn("midiBytes", self.ledger)); case_count += 1

assert case_count == 144, case_count