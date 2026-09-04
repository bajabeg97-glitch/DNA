from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import unittest

from dna_midi_studio import (
    AUTOMATED_METRICS,
    BLIND_LISTENING_PACKAGE_SCHEMA,
    BLIND_LISTENING_PACKAGE_VERSION,
    EVALUATION_REPORT_SCHEMA,
    EVALUATION_REPORT_VERSION,
    LISTENING_AUTHORITIES,
    LISTENING_RESPONSE_SCHEMA,
    LISTENING_RESPONSE_VERSION,
    QUALITY_CONTROLS_VERSION,
    QUALITY_REGRESSION_VAULT_SCHEMA,
    QUALITY_REGRESSION_VAULT_VERSION,
    RATING_CATEGORIES,
    QualityControls,
    build_blind_listening_package,
    build_listening_response,
    build_quality_regression_vault,
    calculate_automated_metrics,
    evaluate_music_quality,
    execute_quality_evaluator_api,
    execute_quality_evaluator_gui,
    load_baseline_reference,
    render_preview_wav,
    summarize_listening,
    validate_baseline_reference,
    validate_blind_listening_key,
    validate_blind_listening_package,
    validate_evaluation_report_v2,
    validate_listening_response,
    validate_quality_regression_vault,
)
from dna_midi_studio.music_quality import _hash_without
from dna_midi_studio.session27_fixture import build_session27_chain


ROOT = Path(__file__).resolve().parents[1]


def ratings(value=4):
    return [{category: value for category in RATING_CATEGORIES} for _ in range(2)]


class Session27MusicQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chain = build_session27_chain(ROOT)
        for key, value in cls.chain.items():
            setattr(cls, key, value)

    def human(self, evaluator, choices=("PREMIUM", "PREMIUM"), value=4):
        return build_listening_response(
            self.blindPackage, self.privateKey, evaluator, "HUMAN_VERIFIED",
            list(choices), ratings(value), evidence_hash=sha256(evaluator.encode()).hexdigest(),
            independence_attested=True,
        )

    def evaluate(self, responses=None, vault=None, controls=None, session=None, song_map=None,
                 package=None, key=None):
        return evaluate_music_quality(
            session or self.previewSession, song_map or self.songMap, self.baselineReference,
            package or self.blindPackage, key or self.privateKey,
            responses if responses is not None else [self.softwareResponse],
            vault or self.regressionVault, controls or self.controls,
        )

    def test_001_evaluation_constants(self):
        self.assertEqual((EVALUATION_REPORT_SCHEMA, EVALUATION_REPORT_VERSION),
                         ("dna-premium-evaluation-report", "2.0"))

    def test_002_blind_package_constants(self):
        self.assertEqual((BLIND_LISTENING_PACKAGE_SCHEMA, BLIND_LISTENING_PACKAGE_VERSION),
                         ("dna-premium-blind-listening-package", "1.0"))

    def test_003_response_constants(self):
        self.assertEqual((LISTENING_RESPONSE_SCHEMA, LISTENING_RESPONSE_VERSION),
                         ("dna-premium-listening-response", "1.0"))

    def test_004_regression_constants(self):
        self.assertEqual((QUALITY_REGRESSION_VAULT_SCHEMA, QUALITY_REGRESSION_VAULT_VERSION),
                         ("dna-premium-quality-regression-vault", "1.0"))

    def test_005_control_version(self):
        self.assertEqual(QUALITY_CONTROLS_VERSION, "1.0")

    def test_006_all_automated_metrics_named(self):
        self.assertEqual(set(AUTOMATED_METRICS), {"harmony", "groove", "registerCollision",
                         "densityCurve", "transitionContinuity", "repetition", "endingResolution"})

    def test_007_all_rating_categories_named(self):
        self.assertEqual(set(RATING_CATEGORIES), {"drum", "bass", "guitar", "accompaniment",
                                                 "solo", "transition", "overall"})

    def test_008_listening_authorities(self):
        self.assertEqual(set(LISTENING_AUTHORITIES), {"HUMAN_VERIFIED", "SOFTWARE_TEST_ONLY"})

    def test_009_evaluation_schema_strict(self):
        schema = json.loads((ROOT / "premium/schemas/v2/evaluation-report-v2.schema.json").read_text())
        self.assertIs(schema["additionalProperties"], False)

    def test_010_evaluation_schema_requires_humans(self):
        schema = json.loads((ROOT / "premium/schemas/v2/evaluation-report-v2.schema.json").read_text())
        self.assertIs(schema["x-human-listening-required"], True)

    def test_011_blind_schema_is_locked(self):
        schema = json.loads((ROOT / "premium/schemas/v2/blind-listening-package-v1.schema.json").read_text())
        self.assertEqual(schema["properties"]["locked"]["const"], True)

    def test_012_response_schema_software_not_human(self):
        schema = json.loads((ROOT / "premium/schemas/v2/listening-response-v1.schema.json").read_text())
        self.assertIs(schema["x-software-responses-count-as-human"], False)

    def test_013_regression_schema_blocks_baseline_better(self):
        schema = json.loads((ROOT / "premium/schemas/v2/quality-regression-vault-v1.schema.json").read_text())
        self.assertIs(schema["x-open-baseline-better-blocks-release"], True)

    def test_014_baseline_reference_validates(self):
        self.assertIsNone(validate_baseline_reference(self.baselineReference))

    def test_015_baseline_is_exact_317(self):
        self.assertEqual(self.baselineReference["baselineId"], "premium-3.17-98a6d52a09ccc789")

    def test_016_baseline_reference_hash(self):
        self.assertEqual(self.baselineReference["referenceHash"],
                         _hash_without(self.baselineReference, "referenceHash"))

    def test_017_baseline_artifact_hash_is_real(self):
        path = ROOT / self.baselineReference["artifactPath"]
        self.assertEqual(sha256(path.read_bytes()).hexdigest(), self.baselineReference["artifactSha256"])

    def test_018_baseline_requires_same_source_pairing(self):
        self.assertEqual(self.baselineReference["policy"],
                         "SAME_SOURCE_PAIRED_RENDER_REQUIRED_FOR_LISTENING_CLAIM")

    def test_019_baseline_reload_is_deterministic(self):
        self.assertEqual(load_baseline_reference(ROOT), self.baselineReference)

    def test_020_automatic_metric_set_complete(self):
        self.assertEqual(set(self.evaluationReport["automated"]["metrics"]), set(AUTOMATED_METRICS))

    def test_021_automated_method_is_structure_only(self):
        self.assertEqual(self.evaluationReport["automated"]["method"],
                         "DETERMINISTIC_MIDI_STRUCTURE_ONLY")

    def test_022_automated_has_no_human_claim(self):
        self.assertFalse(self.evaluationReport["automated"]["humanListeningClaim"])

    def test_023_harmony_score_bounded(self):
        self.assertGreaterEqual(self.evaluationReport["automated"]["metrics"]["harmony"]["score"], 0)

    def test_024_harmony_has_violation_audit(self):
        self.assertIn("violationRate", self.evaluationReport["automated"]["metrics"]["harmony"]["facts"])

    def test_025_groove_has_grid_audit(self):
        self.assertEqual(self.evaluationReport["automated"]["metrics"]["groove"]["facts"]["gridTicks"], 120)

    def test_026_register_has_collision_audit(self):
        self.assertIn("collisionRate", self.evaluationReport["automated"]["metrics"]["registerCollision"]["facts"])

    def test_027_density_has_section_rows(self):
        self.assertEqual(len(self.evaluationReport["automated"]["metrics"]["densityCurve"]["facts"]["sections"]), 2)

    def test_028_transition_has_boundary_rows(self):
        self.assertEqual(self.evaluationReport["automated"]["metrics"]["transitionContinuity"]["facts"]["boundaryCount"], 1)

    def test_029_repetition_has_bar_signatures(self):
        self.assertGreaterEqual(self.evaluationReport["automated"]["metrics"]["repetition"]["facts"]["barSignatureCount"], 1)

    def test_030_ending_has_resolution_reason(self):
        self.assertIn("resolutionReason", self.evaluationReport["automated"]["metrics"]["endingResolution"]["facts"])

    def test_031_automated_overall_is_weighted(self):
        self.assertGreaterEqual(self.evaluationReport["automated"]["overallScore"], 3.5)

    def test_032_automated_reference_passes(self):
        self.assertTrue(self.evaluationReport["automated"]["passed"])

    def test_033_category_scores_complete(self):
        self.assertEqual(set(self.evaluationReport["automated"]["categoryScores"]), set(RATING_CATEGORIES))

    def test_034_metric_calculation_deterministic(self):
        self.assertEqual(calculate_automated_metrics(self.previewSession, self.songMap),
                         calculate_automated_metrics(self.previewSession, self.songMap))

    def test_035_variant_b_can_be_evaluated(self):
        self.assertEqual(calculate_automated_metrics(self.previewSession, self.songMap, "B")["variantId"], "B")

    def test_036_blind_package_validates(self):
        self.assertIsNone(validate_blind_listening_package(self.blindPackage))

    def test_037_blind_key_validates(self):
        self.assertIsNone(validate_blind_listening_key(self.privateKey, self.blindPackage))

    def test_038_blind_package_hash(self):
        self.assertEqual(self.blindPackage["packageHash"], _hash_without(self.blindPackage, "packageHash"))

    def test_039_blind_key_hash(self):
        self.assertEqual(self.privateKey["keyHash"], _hash_without(self.privateKey, "keyHash"))

    def test_040_blind_package_is_locked(self):
        self.assertTrue(self.blindPackage["locked"])

    def test_041_blind_package_is_blind(self):
        self.assertTrue(self.blindPackage["blind"])

    def test_042_public_trials_hide_variant_ids(self):
        text = json.dumps(self.blindPackage["trials"]).lower()
        self.assertNotIn("variantid", text)

    def test_043_public_trials_hide_baseline_role(self):
        text = json.dumps(self.blindPackage["trials"]).lower()
        self.assertNotIn("baselinevariant", text)

    def test_044_private_key_has_two_mappings(self):
        self.assertEqual(len(self.privateKey["mappings"]), 2)

    def test_045_every_trial_has_two_clips(self):
        self.assertTrue(all(len(item["clips"]) == 2 for item in self.blindPackage["trials"]))

    def test_046_clip_ids_unique(self):
        ids = [clip["clipId"] for trial in self.blindPackage["trials"] for clip in trial["clips"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_047_blind_package_binds_317(self):
        self.assertEqual(self.blindPackage["baselineReference"]["baselineId"], self.baselineReference["baselineId"])

    def test_048_blind_package_binds_preview(self):
        self.assertEqual(self.blindPackage["previewSessionHash"], self.previewSession["previewSessionHash"])

    def test_049_proxy_audio_is_not_human_result(self):
        self.assertEqual(self.blindPackage["authority"], "PROTOCOL_READY_PROXY_AUDIO_NOT_HUMAN_RESULT")

    def test_050_blind_randomization_deterministic(self):
        package, key = build_blind_listening_package(self.previewSession, self.audioManifests,
                                                     self.baselineReference, 2727)
        self.assertEqual((package, key), (self.blindPackage, self.privateKey))

    def test_051_different_seed_changes_blind_layout(self):
        package, _ = build_blind_listening_package(self.previewSession, self.audioManifests,
                                                   self.baselineReference, 2728)
        self.assertNotEqual(package["packageHash"], self.blindPackage["packageHash"])

    def test_052_software_response_validates(self):
        self.assertIsNone(validate_listening_response(self.softwareResponse, self.blindPackage))

    def test_053_software_response_not_counted_as_human(self):
        self.assertEqual(self.evaluationReport["listening"]["verifiedHumanEvaluatorCount"], 0)

    def test_054_software_response_counted_separately(self):
        self.assertEqual(self.evaluationReport["listening"]["softwareTestResponseCount"], 1)

    def test_055_software_response_does_not_create_preference_rate(self):
        self.assertIsNone(self.evaluationReport["listening"]["premiumPreferenceRate"])

    def test_056_human_response_requires_evidence(self):
        with self.assertRaisesRegex(ValueError, "evidenceHash"):
            build_listening_response(self.blindPackage, self.privateKey, "human-x", "HUMAN_VERIFIED",
                                     ["PREMIUM", "PREMIUM"], ratings(), independence_attested=True)

    def test_057_human_response_requires_independence(self):
        with self.assertRaisesRegex(ValueError, "attest independence"):
            build_listening_response(self.blindPackage, self.privateKey, "human-x", "HUMAN_VERIFIED",
                                     ["PREMIUM", "PREMIUM"], ratings(),
                                     evidence_hash="a" * 64, independence_attested=False)

    def test_058_two_verified_humans_are_counted(self):
        summary = summarize_listening(self.blindPackage, self.privateKey,
                                      [self.human("human-a"), self.human("human-b")])
        self.assertEqual(summary["verifiedHumanEvaluatorCount"], 2)

    def test_059_premium_preference_rate_is_computed(self):
        summary = summarize_listening(self.blindPackage, self.privateKey,
                                      [self.human("human-a"), self.human("human-b")])
        self.assertEqual(summary["premiumPreferenceRate"], 1.0)

    def test_060_category_median_is_computed(self):
        summary = summarize_listening(self.blindPackage, self.privateKey,
                                      [self.human("human-a", value=4), self.human("human-b", value=5)])
        self.assertEqual(summary["categoryMedians"]["overall"], 4.5)

    def test_061_tie_is_separate_from_preference_denominator(self):
        summary = summarize_listening(self.blindPackage, self.privateKey,
                                      [self.human("human-a", ("TIE", "PREMIUM"))])
        self.assertEqual((summary["tieCount"], summary["premiumPreferenceRate"]), (1, 1.0))

    def test_062_baseline_choice_is_unblinded_by_private_key(self):
        summary = summarize_listening(self.blindPackage, self.privateKey,
                                      [self.human("human-a", ("BASELINE", "BASELINE"))])
        self.assertEqual(summary["baselinePreferredCount"], 2)

    def test_063_duplicate_evaluator_is_rejected(self):
        first = self.human("human-a")
        second = deepcopy(first); second["responseId"] = "response-" + "b" * 20
        second["responseHash"] = _hash_without(second, "responseHash")
        with self.assertRaisesRegex(ValueError, "unique response and evaluator"):
            summarize_listening(self.blindPackage, self.privateKey, [first, second])

    def test_064_response_requires_all_categories(self):
        response = deepcopy(self.softwareResponse)
        del response["trials"][0]["ratings"]["guitar"]
        response["responseHash"] = _hash_without(response, "responseHash")
        with self.assertRaisesRegex(ValueError, "all categories"):
            validate_listening_response(response, self.blindPackage)

    def test_065_response_rating_bounded(self):
        response = deepcopy(self.softwareResponse); response["trials"][0]["ratings"]["overall"] = 6
        response["responseHash"] = _hash_without(response, "responseHash")
        with self.assertRaisesRegex(ValueError, "rating.overall"):
            validate_listening_response(response, self.blindPackage)

    def test_066_response_future_date_rejected(self):
        response = deepcopy(self.softwareResponse)
        response["submittedAt"] = (date.today() + timedelta(days=1)).isoformat()
        response["responseHash"] = _hash_without(response, "responseHash")
        with self.assertRaisesRegex(ValueError, "future"):
            validate_listening_response(response, self.blindPackage)

    def test_067_response_other_package_rejected(self):
        response = deepcopy(self.softwareResponse); response["packageHash"] = "0" * 64
        response["responseHash"] = _hash_without(response, "responseHash")
        with self.assertRaisesRegex(ValueError, "another blind package"):
            validate_listening_response(response, self.blindPackage)

    def test_068_regression_vault_validates(self):
        self.assertIsNone(validate_quality_regression_vault(self.regressionVault))

    def test_069_resolved_regression_does_not_block(self):
        self.assertFalse(self.regressionVault["releaseBlocked"])

    def test_070_open_baseline_better_blocks(self):
        self.assertTrue(self.blockingRegressionVault["releaseBlocked"])

    def test_071_open_case_is_named(self):
        self.assertEqual(self.blockingRegressionVault["openBaselineBetterCases"],
                         ["open-baseline-better-example"])

    def test_072_regression_vault_hash(self):
        self.assertEqual(self.regressionVault["vaultHash"], _hash_without(self.regressionVault, "vaultHash"))

    def test_073_regression_entry_hash(self):
        entry = self.regressionVault["entries"][0]
        self.assertEqual(entry["entryHash"], _hash_without(entry, "entryHash"))

    def test_074_open_regression_cannot_have_resolution(self):
        entry = deepcopy(self.blockingRegressionVault["entries"][0]); entry.pop("entryHash")
        entry["resolutionEvidenceHash"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "Open regression"):
            build_quality_regression_vault([entry])

    def test_075_resolved_regression_requires_resolution(self):
        entry = deepcopy(self.regressionVault["entries"][0]); entry.pop("entryHash")
        entry["resolutionEvidenceHash"] = None
        with self.assertRaisesRegex(ValueError, "Resolved regression"):
            build_quality_regression_vault([entry])

    def test_076_duplicate_regression_case_rejected(self):
        entry = deepcopy(self.regressionVault["entries"][0]); entry.pop("entryHash")
        with self.assertRaisesRegex(ValueError, "case IDs"):
            build_quality_regression_vault([entry, entry])

    def test_077_evaluation_report_validates(self):
        self.assertIsNone(validate_evaluation_report_v2(self.evaluationReport))

    def test_078_evaluation_report_hash(self):
        self.assertEqual(self.evaluationReport["evaluationReportHash"],
                         _hash_without(self.evaluationReport, "evaluationReportHash"))

    def test_079_technical_and_subjective_are_separate(self):
        self.assertTrue(self.evaluationReport["technical"]["passed"])
        self.assertFalse(self.evaluationReport["listening"]["performed"])

    def test_080_reference_has_no_hard_fail(self):
        self.assertEqual(self.evaluationReport["technical"]["hardFailures"], [])

    def test_081_reference_quality_gate_is_blocked(self):
        self.assertFalse(self.evaluationReport["releaseQualityGate"]["passed"])

    def test_082_reference_blocked_only_by_humans(self):
        self.assertEqual(set(self.evaluationReport["releaseQualityGate"]["blockers"]),
                         {"TWO_INDEPENDENT_HUMAN_EVALUATORS_REQUIRED",
                          "HUMAN_OVERALL_MEDIAN_BELOW_FOUR_OR_MISSING",
                          "PREMIUM_PREFERENCE_BELOW_SEVENTY_PERCENT_OR_MISSING"})

    def test_083_two_good_humans_pass_quality_gate(self):
        report = self.evaluate([self.human("human-a"), self.human("human-b")])
        self.assertTrue(report["releaseQualityGate"]["passed"])

    def test_084_two_good_humans_set_pass_status(self):
        report = self.evaluate([self.human("human-a"), self.human("human-b")])
        self.assertEqual(report["status"], "QUALITY_GATE_PASS")

    def test_085_low_human_median_blocks(self):
        report = self.evaluate([self.human("human-a", value=3), self.human("human-b", value=3)])
        self.assertIn("HUMAN_OVERALL_MEDIAN_BELOW_FOUR_OR_MISSING", report["releaseQualityGate"]["blockers"])

    def test_086_low_preference_blocks(self):
        report = self.evaluate([self.human("human-a", ("BASELINE", "BASELINE")),
                                self.human("human-b", ("PREMIUM", "PREMIUM"))])
        self.assertIn("PREMIUM_PREFERENCE_BELOW_SEVENTY_PERCENT_OR_MISSING",
                      report["releaseQualityGate"]["blockers"])

    def test_087_open_regression_blocks_good_humans(self):
        report = self.evaluate([self.human("human-a"), self.human("human-b")],
                               self.blockingRegressionVault)
        self.assertIn("OPEN_BASELINE_BETTER_REGRESSION", report["releaseQualityGate"]["blockers"])

    def test_088_quality_report_is_read_only(self):
        self.assertTrue(self.evaluationReport["readOnly"])

    def test_089_quality_report_cannot_mutate_midi(self):
        self.assertFalse(self.evaluationReport["midiMutationAllowed"])

    def test_090_quality_report_cannot_render_final_midi(self):
        self.assertFalse(self.evaluationReport["finalMidiGenerated"])

    def test_091_quality_report_cannot_certify_pa800(self):
        self.assertFalse(self.evaluationReport["pa800DeviceCertified"])

    def test_092_proxy_cannot_satisfy_human_gate(self):
        self.assertFalse(self.evaluationReport["releaseQualityGate"]["proxyAudioCanSatisfyHumanGate"])

    def test_093_reject_unknown_control_field(self):
        controls = deepcopy(self.controls); controls["fakeHuman"] = True
        with self.assertRaisesRegex(ValueError, "Unknown quality control"):
            QualityControls.from_dict(controls)

    def test_094_reject_one_human_minimum(self):
        controls = deepcopy(self.controls); controls["minimumHumanEvaluators"] = 1
        with self.assertRaisesRegex(ValueError, "minimumHumanEvaluators"):
            QualityControls.from_dict(controls)

    def test_095_reject_variant_a_as_premium(self):
        controls = deepcopy(self.controls); controls["premiumVariantId"] = "A"
        with self.assertRaisesRegex(ValueError, "B or C"):
            QualityControls.from_dict(controls)

    def test_096_reject_wrong_songmap_source(self):
        song_map = deepcopy(self.songMap); song_map["sourceSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source hashes differ"):
            calculate_automated_metrics(self.previewSession, song_map)

    def test_097_reject_tampered_blind_package(self):
        package = deepcopy(self.blindPackage); package["trials"][0]["prompt"] += " tamper"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            validate_blind_listening_package(package)

    def test_098_reject_wrong_private_key(self):
        key = deepcopy(self.privateKey); key["mappings"][0]["clipRoles"] = {}
        key["keyHash"] = _hash_without(key, "keyHash")
        with self.assertRaisesRegex(ValueError, "does not belong"):
            validate_blind_listening_key(key, self.blindPackage)

    def test_099_api_evaluate_preview_blocks_human_gate(self):
        result = execute_quality_evaluator_api(
            {"action": "evaluate-preview", "previewSession": self.previewSession,
             "songMap": self.songMap, "controls": self.controls}, ROOT)
        self.assertFalse(result["report"]["releaseQualityGate"]["passed"])

    def test_100_api_does_not_export_private_key_to_gui(self):
        result = execute_quality_evaluator_api(
            {"action": "evaluate-preview", "previewSession": self.previewSession,
             "songMap": self.songMap}, ROOT)
        self.assertFalse(result["privateKeyExportedToGui"])

    def test_101_gui_api_parity(self):
        payload = {"action": "evaluate-preview", "previewSession": self.previewSession,
                   "songMap": self.songMap, "controls": self.controls}
        self.assertEqual(execute_quality_evaluator_gui(payload, ROOT),
                         execute_quality_evaluator_api(payload, ROOT))

    def test_102_cli_help(self):
        completed = subprocess.run(["python", "session27_quality_evaluator.py", "--help"],
                                   cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_103_server_and_gui_expose_quality_workflow(self):
        self.assertIn("/api/premium-quality-evaluator", (ROOT / "server.py").read_text())
        self.assertIn("MUSIC QUALITY EVALUATOR 2.0", (ROOT / "web_gui.py").read_text())

    def test_104_evaluation_is_deterministic(self):
        self.assertEqual(self.evaluationReport, self.evaluate())


if __name__ == "__main__":
    unittest.main()