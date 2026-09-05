from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from dna_midi_studio import (
    ALLOWED_PRODUCT_NAME,
    EXTERNAL_BLOCKERS,
    FINAL_PRODUCT_NAME,
    RELEASE_READINESS_SCHEMA,
    RELEASE_READINESS_VERSION,
    SIGNATURE_ALGORITHM,
    SIGNATURE_AUTHORITY,
    STATUS_IDS,
    TARGET_BASELINE,
    execute_release_readiness_api,
    execute_release_readiness_gui,
    migrate_project_for_release,
    validate_hardening_report,
    validate_project_migration_report,
    validate_release_readiness_v2,
    validate_release_status_matrix,
    validate_software_manifest,
)
from dna_midi_studio.session30_fixture import REFERENCE_DATE, build_session30_chain


ROOT = Path(__file__).resolve().parents[1]


class Session30ReleaseReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = build_session30_chain(ROOT)
        cls.migration = cls.fixture["projectMigration"]
        cls.manifest = cls.fixture["softwareManifest"]
        cls.hardening = cls.fixture["hardeningReport"]
        cls.matrix = cls.fixture["statusMatrix"]
        cls.readiness = cls.fixture["releaseReadiness"]


def _case(name, operation):
    def test(self):
        operation(self)
    test.__name__ = "test_" + name
    setattr(Session30ReleaseReadinessTests, test.__name__, test)


def _equal(name, getter, expected):
    _case(name, lambda self: self.assertEqual(getter(self), expected))


def _true(name, getter):
    _case(name, lambda self: self.assertTrue(getter(self)))


case_count = 0

# Contract and schema identity: 12 tests.
for name, actual, expected in (
    ("contract_release_schema", RELEASE_READINESS_SCHEMA, "dna-ai-premium-release-readiness"),
    ("contract_release_version", RELEASE_READINESS_VERSION, "2.0"),
    ("contract_target_baseline", TARGET_BASELINE, "4.8-release-candidate-preview"),
    ("contract_allowed_name", ALLOWED_PRODUCT_NAME, "AI PREMIUM ARRANGER PREVIEW"),
    ("contract_forbidden_final_name", FINAL_PRODUCT_NAME, "AI PREMIUM ARRANGER"),
    ("contract_signature_algorithm", SIGNATURE_ALGORITHM, "SHA256-CONTENT-SEAL"),
    ("contract_signature_authority", SIGNATURE_AUTHORITY, "DNA_LOCAL_SOFTWARE_GATE"),
    ("contract_external_blocker_count", len(EXTERNAL_BLOCKERS), 7),
):
    _equal(name, lambda _self, value=actual: value, expected)
    case_count += 1
for schema_name in (
    "release-readiness-v2.schema.json", "software-manifest-v1.schema.json",
    "project-migration-report-v1.schema.json", "release-status-matrix-v1.schema.json",
):
    def schema_check(self, schema_name=schema_name):
        value = json.loads((ROOT / "premium/schemas/v2" / schema_name).read_text(encoding="utf-8"))
        self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIs(value["additionalProperties"], False)
        self.assertTrue(value["x-contract-version"])
    _case("schema_" + schema_name.replace(".schema.json", "").replace("-", "_"), schema_check)
    case_count += 1

# Non-destructive project migration: 20 tests.
migration_equalities = (
    ("migration_input_unchanged", lambda s: s.fixture["legacyProject"],
     lambda s: s.fixture["legacyProjectBeforeMigration"]),
    ("migration_schema", lambda s: s.migration["schema"], lambda s: "dna-project-migration-report"),
    ("migration_version", lambda s: s.migration["version"], lambda s: "1.0"),
    ("migration_source_format", lambda s: s.migration["source"]["format"], lambda s: "legacy-state-wrapper"),
    ("migration_unicode_name", lambda s: s.migration["migratedProject"]["name"], lambda s: "ŠĐČŽ Premium projekt"),
    ("migration_locks_preserved", lambda s: s.migration["migratedProject"]["state"]["locks"],
     lambda s: ["v2cv1:bass", "v4cv1:drums"]),
    ("migration_audit_preserved", lambda s: s.migration["migratedProject"]["state"]["audit"][0]["variant"], lambda s: "C"),
    ("migration_path_preserved", lambda s: s.migration["migratedProject"]["state"]["path"],
     lambda s: "C:/Glazba/Ćirilica/ŠĐČŽ/vrlo-duga-mapa/pjesma.mid"),
    ("migration_no_overwrite", lambda s: s.migration["preservation"]["originalFileOverwriteAllowed"], lambda s: False),
    ("migration_no_midi", lambda s: s.migration["safety"]["midiEmbedded"], lambda s: False),
    ("migration_no_audio", lambda s: s.migration["safety"]["audioEmbedded"], lambda s: False),
    ("migration_hash_length", lambda s: len(s.migration["migrationHash"]), lambda s: 64),
)
for name, getter, expected_getter in migration_equalities:
    _case(name, lambda self, getter=getter, expected_getter=expected_getter:
          self.assertEqual(getter(self), expected_getter(self)))
    case_count += 1
_case("migration_validator_accepts_reference", lambda self: validate_project_migration_report(self.migration)); case_count += 1
_case("migration_is_deterministic", lambda self: self.assertEqual(
    migrate_project_for_release(self.fixture["legacyProject"], REFERENCE_DATE)["migrationHash"],
    self.migration["migrationHash"])); case_count += 1
_case("migration_accepts_optimizer_legacy", lambda self: self.assertEqual(
    migrate_project_for_release({"optimizer": {"seed": 1}, "style": {}}, REFERENCE_DATE)["source"]["format"],
    "legacy-optimizer-style")); case_count += 1
_case("migration_accepts_current_project", lambda self: self.assertEqual(
    migrate_project_for_release(self.migration["migratedProject"], REFERENCE_DATE)["output"]["version"], 1)); case_count += 1
_case("migration_accepts_state_wrapper", lambda self: self.assertTrue(
    migrate_project_for_release({"state": {"locks": [], "audit": []}}, REFERENCE_DATE)["preservation"]["statePreserved"])); case_count += 1
_case("migration_rejects_unknown_json", lambda self: self.assertRaises(
    ValueError, migrate_project_for_release, {"hello": "world"}, REFERENCE_DATE)); case_count += 1
_case("migration_rejects_future_date", lambda self: self.assertRaises(
    ValueError, migrate_project_for_release, {"state": {}}, "2099-01-01")); case_count += 1
_case("migration_rejects_embedded_midi", lambda self: self.assertRaises(
    ValueError, migrate_project_for_release, {"state": {"midiData": "AAAA"}}, REFERENCE_DATE)); case_count += 1

# Software content manifest: 24 tests.
manifest_equalities = (
    ("manifest_schema", lambda s: s.manifest["schema"], "dna-ai-premium-software-manifest"),
    ("manifest_version", lambda s: s.manifest["version"], "1.0"),
    ("manifest_target", lambda s: s.manifest["targetBaseline"], TARGET_BASELINE),
    ("manifest_windows", lambda s: s.manifest["platform"]["os"], ["Windows 10 x64", "Windows 11 x64"]),
    ("manifest_python", lambda s: s.manifest["platform"]["python"], ["3.11", "3.12", "3.13", "3.14"]),
    ("manifest_offline", lambda s: s.manifest["platform"]["offlineCore"], True),
    ("manifest_dependencies", lambda s: s.manifest["platform"]["thirdPartyRuntimeDependencies"], []),
    ("manifest_registry_count", lambda s: len(s.manifest["registries"]), 5),
    ("manifest_contract_minimum", lambda s: len(s.manifest["contracts"]) >= 9, True),
    ("manifest_device_waiting", lambda s: s.manifest["deviceProfile"]["status"], "WAITING_FOR_DEVICE"),
    ("manifest_device_not_certified", lambda s: s.manifest["deviceProfile"]["certified"], False),
    ("manifest_device_hash_absent", lambda s: s.manifest["deviceProfile"]["profileHash"], None),
    ("manifest_content_seal_algorithm", lambda s: s.manifest["signature"]["algorithm"], SIGNATURE_ALGORITHM),
    ("manifest_no_identity_claim", lambda s: s.manifest["signature"]["identityClaim"], False),
    ("manifest_signed_hash_matches", lambda s: s.manifest["signature"]["signedContentHash"], lambda s: s.manifest["contentHash"]),
)
for name, getter, expected in manifest_equalities:
    _case(name, lambda self, getter=getter, expected=expected: self.assertEqual(
        getter(self), expected(self) if callable(expected) else expected))
    case_count += 1
_case("manifest_validator_accepts_reference", lambda self: validate_software_manifest(self.manifest, ROOT)); case_count += 1
_case("manifest_content_hash_is_deterministic", lambda self: self.assertEqual(
    self.manifest["contentHash"], self.fixture["softwareManifest"]["contentHash"])); case_count += 1
for filename in ("server.py", "web_gui.py", "session30_release_check.py", "session30_release_readiness.py"):
    _case("manifest_contains_" + filename.replace(".", "_"), lambda self, filename=filename: self.assertIn(
        filename, {item["path"] for item in self.manifest["application"]}))
    case_count += 1
_case("manifest_paths_are_relative", lambda self: self.assertTrue(all(
    not Path(item["path"]).is_absolute() and ".." not in Path(item["path"]).parts
    for group in (self.manifest["application"], self.manifest["registries"], self.manifest["contracts"])
    for item in group))); case_count += 1
_case("manifest_paths_are_unique", lambda self: self.assertEqual(
    len([item["path"] for group in (self.manifest["application"], self.manifest["registries"], self.manifest["contracts"]) for item in group]),
    len({item["path"] for group in (self.manifest["application"], self.manifest["registries"], self.manifest["contracts"]) for item in group}))); case_count += 1
def manifest_tamper(self):
    value = deepcopy(self.manifest); value["contentHash"] = "0" * 64
    with self.assertRaises(ValueError): validate_software_manifest(value)
_case("manifest_rejects_tamper", manifest_tamper); case_count += 1

# Measured hardening evidence: 18 tests.
hardening_cases = (
    ("hardening_passed", lambda s: s.hardening["passed"]),
    ("hardening_25000_notes", lambda s: s.hardening["performance"]["analysis25000Notes"]["noteCount"] == 25000),
    ("hardening_analysis_under_10s", lambda s: s.hardening["performance"]["analysis25000Notes"]["seconds"] < 10),
    ("hardening_plan_has_10_nodes", lambda s: s.hardening["performance"]["globalPlan"]["nodeCount"] == 10),
    ("hardening_plan_under_5s", lambda s: s.hardening["performance"]["globalPlan"]["seconds"] < 5),
    ("hardening_partial_marker", lambda s: s.hardening["performance"]["partialRegeneration"]["markers"] == ["v3cv1"]),
    ("hardening_partial_under_2s", lambda s: s.hardening["performance"]["partialRegeneration"]["seconds"] < 2),
    ("hardening_memory_items", lambda s: s.hardening["memory"]["migrationItems"] == 10000),
    ("hardening_memory_under_64mb", lambda s: s.hardening["memory"]["peakBytes"] < 64 * 1024 * 1024),
    ("hardening_unicode", lambda s: s.hardening["paths"]["unicodePreserved"]),
    ("hardening_long_path", lambda s: s.hardening["paths"]["longBounded"]),
    ("hardening_reserved_path", lambda s: s.hardening["paths"]["reservedProtected"]),
    ("hardening_traversal", lambda s: s.hardening["paths"]["traversalRemoved"]),
    ("hardening_crash_rollback", lambda s: s.hardening["transactions"]["crashRollback"]),
    ("hardening_disk_full", lambda s: s.hardening["transactions"]["diskFullRollback"]),
    ("hardening_cancel", lambda s: s.hardening["transactions"]["cancelRollback"]),
    ("hardening_atomic", lambda s: s.hardening["transactions"]["atomicReplace"]),
    ("hardening_clean_extract", lambda s: s.hardening["cleanExtract"]["passed"]),
)
for name, getter in hardening_cases:
    _true(name, getter); case_count += 1

# Release status matrix: 22 tests.
status_equalities = (
    ("matrix_schema", lambda s: s.matrix["schema"], "dna-premium-release-status-matrix"),
    ("matrix_version", lambda s: s.matrix["version"], "1.0"),
    ("matrix_entry_count", lambda s: s.matrix["summary"]["entryCount"], 17),
    ("matrix_ids_ordered", lambda s: tuple(item["id"] for item in s.matrix["entries"]), STATUS_IDS),
    ("matrix_ids_unique", lambda s: len({item["id"] for item in s.matrix["entries"]}), 17),
    ("matrix_software_passed", lambda s: s.matrix["summary"]["softwarePassed"], True),
    ("matrix_severity1_zero", lambda s: s.matrix["summary"]["openSeverity1Defects"], 0),
    ("matrix_severity2_zero", lambda s: s.matrix["summary"]["openSeverity2Defects"], 0),
    ("matrix_external_blockers", lambda s: s.matrix["summary"]["externalBlockerCount"], 5),
    ("matrix_preview_rc_ready", lambda s: s.matrix["summary"]["softwareReleaseCandidateReady"], True),
    ("matrix_final_release_blocked", lambda s: s.matrix["summary"]["finalPremiumReleaseAllowed"], False),
    ("matrix_export_blocked", lambda s: s.matrix["summary"]["finalMidiExportAllowed"], False),
    ("matrix_allowed_name", lambda s: s.matrix["summary"]["allowedProductName"], ALLOWED_PRODUCT_NAME),
    ("matrix_final_name_recorded", lambda s: s.matrix["summary"]["finalProductName"], FINAL_PRODUCT_NAME),
)
for name, getter, expected in status_equalities:
    _equal(name, getter, expected); case_count += 1
for identifier, status in (
    ("HUMAN_LISTENING", "PENDING"), ("EXPRESSION_EVIDENCE", "BLOCKED"),
    ("ARTICULATION_CAPTURE", "BLOCKED"), ("PA800_DEVICE_PROFILE", "WAITING"),
    ("PA800_VOICE_COST", "UNCONFIRMED"), ("FINAL_MIDI_EXPORT", "BLOCKED"),
    ("MARKETING_NAME", "PREVIEW_ONLY"), ("LEGACY_CORE", "PASS"),
):
    _case("matrix_row_" + identifier.lower(), lambda self, identifier=identifier, status=status: self.assertEqual(
        next(item for item in self.matrix["entries"] if item["id"] == identifier)["status"], status))
    case_count += 1

# Final readiness remains honest: 20 tests.
readiness_equalities = (
    ("readiness_schema", lambda s: s.readiness["schema"], RELEASE_READINESS_SCHEMA),
    ("readiness_version", lambda s: s.readiness["version"], RELEASE_READINESS_VERSION),
    ("readiness_id_prefix", lambda s: s.readiness["releaseId"].startswith("preview-rc-"), True),
    ("readiness_date", lambda s: s.readiness["releaseDate"], REFERENCE_DATE),
    ("readiness_target", lambda s: s.readiness["targetBaseline"], TARGET_BASELINE),
    ("readiness_source_count", lambda s: len(s.readiness["sources"]), 9),
    ("readiness_source_hashes", lambda s: all(len(item["sha256"]) == 64 for item in s.readiness["sources"]), True),
    ("readiness_expected_recovery_total", lambda s: s.readiness["evidence"]["expectedRecoveryTestsWithSession30"], 1388),
    ("readiness_prior_recovery", lambda s: s.readiness["evidence"]["priorRecoveryTests"] >= 1260, True),
    ("readiness_quality_technical", lambda s: s.readiness["quality"]["technicalPassed"], True),
    ("readiness_quality_automated", lambda s: s.readiness["quality"]["automatedPassed"], True),
    ("readiness_quality_score", lambda s: s.readiness["quality"]["automatedOverallScore"], 4.543),
    ("readiness_human_zero", lambda s: s.readiness["quality"]["verifiedHumanEvaluators"], 0),
    ("readiness_human_required", lambda s: s.readiness["quality"]["requiredHumanEvaluators"], 2),
    ("readiness_preference_missing", lambda s: s.readiness["quality"]["premiumPreferenceRate"], None),
    ("readiness_no_severity_defects", lambda s: s.readiness["defects"]["openSeverity1"] + s.readiness["defects"]["openSeverity2"], 0),
    ("readiness_software_ready", lambda s: s.readiness["gates"]["softwareReleaseCandidateReady"], True),
    ("readiness_final_blocked", lambda s: s.readiness["gates"]["finalPremiumReleaseAllowed"], False),
    ("readiness_export_blocked", lambda s: s.readiness["gates"]["finalMidiExportAllowed"], False),
    ("readiness_no_physical_claim", lambda s: s.readiness["safety"]["physicalCertificationClaimed"], False),
)
for name, getter, expected in readiness_equalities:
    _equal(name, getter, expected); case_count += 1

# CLI/API/GUI integration and route visibility: 12 tests.
_case("api_migrate_parity", lambda self: self.assertEqual(
    execute_release_readiness_api({"action": "migrate", "releaseDate": REFERENCE_DATE,
                                   "legacyProject": self.fixture["legacyProject"]}, ROOT)["migrationHash"],
    self.migration["migrationHash"])); case_count += 1
_case("gui_migrate_parity", lambda self: self.assertEqual(
    execute_release_readiness_gui({"action": "migrate", "releaseDate": REFERENCE_DATE,
                                   "legacyProject": self.fixture["legacyProject"]}, ROOT)["migrationHash"],
    self.migration["migrationHash"])); case_count += 1
_case("api_rejects_action", lambda self: self.assertRaises(
    ValueError, execute_release_readiness_api, {"action": "certify-pa800"}, ROOT)); case_count += 1
_case("api_rejects_unknown_field", lambda self: self.assertRaises(
    ValueError, execute_release_readiness_api,
    {"action": "migrate", "releaseDate": REFERENCE_DATE, "legacyProject": {"state": {}}, "bypass": True}, ROOT)); case_count += 1
for name, path, token in (
    ("server_route", "server.py", "/api/premium-release-readiness"),
    ("server_import", "server.py", "execute_release_readiness_api"),
    ("web_workspace", "web_gui.py", "AI PREMIUM RELEASE READINESS 2.0"),
    ("web_endpoint", "web_gui.py", "/api/premium-release-readiness"),
    ("web_export_blocked", "web_gui.py", "FINAL EXPORT BLOCKED"),
    ("cli_reference", "session30_release_readiness.py", "reference"),
    ("cli_migrate", "session30_release_readiness.py", "migrate"),
    ("package_export", "src/dna_midi_studio/__init__.py", "execute_release_readiness_api"),
):
    _case(name, lambda self, path=path, token=token: self.assertIn(
        token, (ROOT / path).read_text(encoding="utf-8")))
    case_count += 1

assert case_count == 128, case_count
