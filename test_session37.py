from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from dna_midi_studio import (
    CALIBRATION_REPORT_SCHEMA,
    CALIBRATION_REPORT_VERSION,
    DEFAULT_METRIC_WEIGHTS,
    EXPRESSION_INTAKE_SCHEMA,
    EXPRESSION_INTAKE_VERSION,
    GROUND_TRUTH_VAULT_SCHEMA,
    GROUND_TRUTH_VAULT_VERSION,
    LISTENING_INTAKE_SCHEMA,
    LISTENING_INTAKE_VERSION,
    QUALITY_CORPUS_SCHEMA,
    QUALITY_CORPUS_VERSION,
    QUALITY_GATE_SCHEMA,
    QUALITY_GATE_VERSION,
    build_listening_intake,
    execute_quality_calibration_api,
    execute_quality_calibration_gui,
    import_evaluator_bundle,
    import_expression_evidence,
    validate_calibration_report,
    validate_expression_intake,
    validate_locked_quality_corpus,
    validate_quality_release_gate,
)
from dna_midi_studio.quality_calibration import _hash_without
from dna_midi_studio.session37_fixture import build_session37_chain


ROOT = Path(__file__).resolve().parents[1]


class Session37QualityCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chain = build_session37_chain(ROOT)
        for key, value in cls.chain.items():
            setattr(cls, key, value)
        cls.truth_by_id = {entry["caseId"]: entry for entry in cls.groundTruthVault["entries"]}
        cls.holdout_by_id = {entry["caseId"]: entry for entry in cls.holdout["results"]}

    def _evidence_files(self, root: Path):
        values = {}
        for name, payload in (("source.mid", b"MIDI"), ("capture.wav", b"WAVE"),
                              ("attestation.txt", b"SIGNED"), ("ratings.json", b"{}")):
            path = root / name
            path.write_bytes(payload)
            values[name] = {"path": name, "sha256": sha256(payload).hexdigest()}
        return values

    def _expression_submission(self, root: Path):
        files = self._evidence_files(root)
        return {
            "testOnly": True,
            "operatorApproved": False,
            "soundBinding": {"trackUid": "track-001", "bankMsb": 0, "bankLsb": 0,
                             "program": 24, "factoryProfileId": "120.111.231"},
            "sourceMidi": files["source.mid"],
            "audioEvidence": files["capture.wav"],
            "operatorAttestation": files["attestation.txt"],
            "relationships": [{"relationshipId": "rel-001", "kind": "approach",
                               "onsetDeltaTicks": -30, "pitchDeltaSemitones": -1,
                               "gateRatio": 0.8, "confidence": 0.9, "phraseRole": "pickup"}],
        }

    def _evaluator_submission(self, root: Path):
        files = self._evidence_files(root)
        return {
            "evaluatorId": "test-evaluator",
            "packageHash": self.blindPackage["packageHash"],
            "independenceAttested": True,
            "operatorApproved": False,
            "testOnly": True,
            "responseHash": sha256(b"response").hexdigest(),
            "evidenceFiles": [
                {"role": "SIGNED_ATTESTATION", **files["attestation.txt"]},
                {"role": "RATING_FORM", **files["ratings.json"]},
            ],
        }

    def test_001_contract_constants(self):
        self.assertEqual((QUALITY_CORPUS_SCHEMA, QUALITY_CORPUS_VERSION),
                         ("dna-premium-quality-corpus", "2.0"))

    def test_002_vault_constants(self):
        self.assertEqual((GROUND_TRUTH_VAULT_SCHEMA, GROUND_TRUTH_VAULT_VERSION),
                         ("dna-premium-quality-ground-truth-vault", "1.0"))

    def test_003_calibration_constants(self):
        self.assertEqual((CALIBRATION_REPORT_SCHEMA, CALIBRATION_REPORT_VERSION),
                         ("dna-premium-quality-calibration-report", "1.0"))

    def test_004_expression_constants(self):
        self.assertEqual((EXPRESSION_INTAKE_SCHEMA, EXPRESSION_INTAKE_VERSION),
                         ("dna-premium-production-expression-intake", "1.0"))

    def test_005_listening_constants(self):
        self.assertEqual((LISTENING_INTAKE_SCHEMA, LISTENING_INTAKE_VERSION),
                         ("dna-premium-human-listening-intake", "1.0"))

    def test_006_gate_constants(self):
        self.assertEqual((QUALITY_GATE_SCHEMA, QUALITY_GATE_VERSION),
                         ("dna-premium-quality-release-gate", "1.0"))

    def test_007_corpus_validates(self):
        self.assertIsNone(validate_locked_quality_corpus(self.corpus, self.groundTruthVault, ROOT))

    def test_008_case_count(self):
        self.assertEqual(self.corpus["caseCount"], 32)

    def test_009_split_count(self):
        self.assertEqual(self.corpus["splits"], {"train": 24, "holdout": 8})

    def test_010_corpus_locked(self):
        self.assertTrue(self.corpus["locked"])

    def test_011_legal_source(self):
        self.assertEqual(self.corpus["license"], "self-authored-test-fixtures")

    def test_012_source_counts(self):
        self.assertEqual([row["caseCount"] for row in self.corpus["sourceCorpora"]], [20, 12])

    def test_013_holdout_not_visible(self):
        self.assertFalse(self.corpus["holdoutPolicy"]["labelsVisibleToCalibration"])

    def test_014_threshold_changes_forbidden(self):
        self.assertFalse(self.corpus["holdoutPolicy"]["thresholdChangesAfterHoldout"])

    def test_015_hard_safety_weakening_forbidden(self):
        self.assertFalse(self.corpus["holdoutPolicy"]["hardSafetyThresholdWeakening"])

    def test_016_corpus_hash(self):
        self.assertEqual(self.corpus["corpusHash"], _hash_without(self.corpus, "corpusHash"))

    def test_017_vault_private(self):
        self.assertTrue(self.groundTruthVault["private"])

    def test_018_vault_hash(self):
        self.assertEqual(self.groundTruthVault["vaultHash"],
                         _hash_without(self.groundTruthVault, "vaultHash"))

    def test_019_calibration_validates(self):
        self.assertIsNone(validate_calibration_report(self.calibration, self.corpus))

    def test_020_calibration_mode(self):
        self.assertEqual(self.calibration["mode"], "SOFTWARE_STRUCTURAL_NORMALIZATION_ONLY")

    def test_021_training_count(self):
        self.assertEqual(self.calibration["trainingCaseCount"], 24)

    def test_022_holdout_count(self):
        self.assertEqual(self.calibration["holdoutCaseCount"], 8)

    def test_023_no_split_overlap(self):
        self.assertFalse(set(self.calibration["trainingCaseIds"]) &
                         set(self.calibration["holdoutCaseIdsSealed"]))

    def test_024_weights_unchanged(self):
        self.assertFalse(self.calibration["metricWeightsChanged"])

    def test_025_thresholds_unchanged(self):
        self.assertFalse(self.calibration["thresholdsChanged"])

    def test_026_no_human_claim(self):
        self.assertFalse(self.calibration["humanListeningClaim"])

    def test_027_no_expression_claim(self):
        self.assertFalse(self.calibration["productionExpressionClaim"])

    def test_028_sealed_before_holdout(self):
        self.assertTrue(self.calibration["sealedBeforeHoldoutEvaluation"])

    def test_029_holdout_structural_pass(self):
        self.assertTrue(self.holdout["structuralPass"])
        self.assertEqual(
            self.holdout["groundTruthKinds"],
            ["chord", "section", "trackRole", "transition"],
        )
        self.assertTrue(all(row["trackRoleExactRate"] >= 0.85 for row in self.holdout["results"]))
        self.assertTrue(all(row["transitionExactRate"] >= 0.80 for row in self.holdout["results"]))

    def test_030_holdout_eight(self):
        self.assertEqual(self.holdout["caseCount"], 8)

    def test_031_genre_coverage(self):
        self.assertGreaterEqual(len(self.holdout["coverage"]["genres"]), 7)

    def test_032_meter_coverage(self):
        self.assertEqual(set(self.holdout["coverage"]["meters"]), {"3/4", "4/4", "6/8"})

    def test_033_density_coverage(self):
        self.assertEqual(set(self.holdout["coverage"]["densityClasses"]), {"LOW", "MEDIUM", "HIGH"})

    def test_034_expression_template_validates(self):
        self.assertIsNone(validate_expression_intake(self.expressionIntake))

    def test_035_expression_awaiting(self):
        self.assertEqual(self.expressionIntake["status"], "AWAITING_OPERATOR_CAPTURE")

    def test_036_expression_not_eligible(self):
        self.assertFalse(self.expressionIntake["productionEligible"])

    def test_037_listening_zero_of_two(self):
        self.assertEqual((self.listeningIntake["verifiedIndependentEvaluators"],
                          self.listeningIntake["requiredIndependentEvaluators"]), (0, 2))

    def test_038_proxy_not_human(self):
        self.assertFalse(self.listeningIntake["proxyAudioCountsAsHuman"])

    def test_039_gate_validates(self):
        self.assertIsNone(validate_quality_release_gate(self.qualityGate))

    def test_040_release_still_blocked(self):
        self.assertFalse(self.qualityGate["qualityReleaseGatePassed"])

    def test_041_three_quality_blockers(self):
        self.assertEqual(len(self.qualityGate["blockers"]), 3)

    def test_042_allowed_name_preview(self):
        self.assertEqual(self.qualityGate["allowedProductName"], "AI PREMIUM ARRANGER PREVIEW")

    def test_043_physical_device_waiting(self):
        self.assertEqual(self.qualityGate["physicalPa800"], "WAITING_FOR_DEVICE")

    def test_044_certified_export_blocked(self):
        self.assertFalse(self.qualityGate["finalCertifiedMidiExportAllowed"])

    def test_045_build_api(self):
        result = execute_quality_calibration_api({"action": "build"}, ROOT)
        self.assertEqual(result["corpus"]["corpusHash"], self.corpus["corpusHash"])

    def test_046_gui_is_read_only(self):
        result = execute_quality_calibration_gui({"action": "build"}, ROOT)
        self.assertTrue(result["readOnlyCalibration"])

    def test_047_validate_api(self):
        result = execute_quality_calibration_api(
            {"action": "validate-corpus", "corpus": self.corpus, "vault": self.groundTruthVault}, ROOT)
        self.assertTrue(result["valid"])

    def test_048_test_expression_import(self):
        with tempfile.TemporaryDirectory() as folder:
            result = import_expression_evidence(self._expression_submission(Path(folder)), folder)
        self.assertEqual(result["status"], "SOFTWARE_TEST_ONLY")

    def test_049_test_expression_not_production(self):
        with tempfile.TemporaryDirectory() as folder:
            result = import_expression_evidence(self._expression_submission(Path(folder)), folder)
        self.assertFalse(result["productionEligible"])

    def test_050_test_evaluator_import(self):
        with tempfile.TemporaryDirectory() as folder:
            result = import_evaluator_bundle(self._evaluator_submission(Path(folder)), self.blindPackage, folder)
        self.assertTrue(result["testOnly"])

    def test_051_test_evaluator_not_production(self):
        with tempfile.TemporaryDirectory() as folder:
            result = import_evaluator_bundle(self._evaluator_submission(Path(folder)), self.blindPackage, folder)
        self.assertFalse(result["productionEligible"])

    def test_052_test_bundle_does_not_count(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = import_evaluator_bundle(self._evaluator_submission(Path(folder)), self.blindPackage, folder)
        intake = build_listening_intake(self.evaluationReport, [bundle])
        self.assertEqual(intake["verifiedIndependentEvaluators"], 0)

    def test_053_expression_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = self._expression_submission(Path(folder))
            payload["sourceMidi"]["path"] = "../source.mid"
            with self.assertRaises(ValueError):
                import_expression_evidence(payload, folder)

    def test_054_production_without_approval_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = self._expression_submission(Path(folder))
            payload["testOnly"] = False
            with self.assertRaises(ValueError):
                import_expression_evidence(payload, folder)

    def test_055_forbidden_expression_field_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = self._expression_submission(Path(folder))
            payload["relationships"][0]["velocity"] = 90
            with self.assertRaises(ValueError):
                import_expression_evidence(payload, folder)

    def test_056_evaluator_package_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = self._evaluator_submission(Path(folder))
            payload["packageHash"] = "0" * 64
            with self.assertRaises(ValueError):
                import_evaluator_bundle(payload, self.blindPackage, folder)

    def test_057_tampered_corpus_rejected(self):
        value = deepcopy(self.corpus)
        value["cases"][0]["genre"] = "tampered"
        with self.assertRaises(ValueError):
            validate_locked_quality_corpus(value, self.groundTruthVault)

    def test_058_tampered_weight_rejected(self):
        value = deepcopy(self.calibration)
        value["metricWeights"]["harmony"] = 0.19
        value["calibrationHash"] = _hash_without(value, "calibrationHash")
        with self.assertRaises(ValueError):
            validate_calibration_report(value, self.corpus)


def _case_file_test(case):
    def test(self):
        path = ROOT / case["midiPath"]
        self.assertTrue(path.is_file())
        self.assertEqual(sha256(path.read_bytes()).hexdigest(), case["midiSha256"])
    return test


def _case_vault_test(case):
    def test(self):
        entry = self.truth_by_id[case["caseId"]]
        self.assertEqual(entry["annotationHash"], case["annotationHash"])
    return test


def _train_access_test(case):
    def test(self):
        self.assertTrue(self.truth_by_id[case["caseId"]]["calibrationAccessible"])
    return test


def _holdout_access_test(case):
    def test(self):
        self.assertFalse(self.truth_by_id[case["caseId"]]["calibrationAccessible"])
    return test


def _holdout_chord_test(case):
    def test(self):
        self.assertGreaterEqual(self.holdout_by_id[case["caseId"]]["chordExactRate"], 0.85)
    return test


def _holdout_section_test(case):
    def test(self):
        self.assertGreaterEqual(self.holdout_by_id[case["caseId"]]["sectionBoundaryExactRate"], 0.80)
    return test


def _holdout_parsed_test(case):
    def test(self):
        self.assertTrue(self.holdout_by_id[case["caseId"]]["parsed"])
    return test


def _weight_exact_test(name, value):
    def test(self):
        self.assertEqual(self.calibration["metricWeights"][name], value)
    return test


def _weight_positive_test(name, value):
    def test(self):
        self.assertGreater(value, 0)
    return test


_preview_chain = build_session37_chain(ROOT)
for index, case in enumerate(_preview_chain["corpus"]["cases"], 1):
    setattr(Session37QualityCalibrationTests, f"test_case_{index:02d}_file_hash", _case_file_test(case))
    setattr(Session37QualityCalibrationTests, f"test_case_{index:02d}_vault_binding", _case_vault_test(case))
for index, case in enumerate((row for row in _preview_chain["corpus"]["cases"] if row["split"] == "TRAIN"), 1):
    setattr(Session37QualityCalibrationTests, f"test_train_{index:02d}_labels_accessible", _train_access_test(case))
for index, case in enumerate((row for row in _preview_chain["corpus"]["cases"] if row["split"] == "HOLDOUT"), 1):
    setattr(Session37QualityCalibrationTests, f"test_holdout_{index:02d}_sealed", _holdout_access_test(case))
    setattr(Session37QualityCalibrationTests, f"test_holdout_{index:02d}_chords", _holdout_chord_test(case))
    setattr(Session37QualityCalibrationTests, f"test_holdout_{index:02d}_sections", _holdout_section_test(case))
    setattr(Session37QualityCalibrationTests, f"test_holdout_{index:02d}_parsed", _holdout_parsed_test(case))
for index, (name, value) in enumerate(DEFAULT_METRIC_WEIGHTS.items(), 1):
    setattr(Session37QualityCalibrationTests, f"test_weight_{index:02d}_exact", _weight_exact_test(name, value))
    setattr(Session37QualityCalibrationTests, f"test_weight_{index:02d}_positive", _weight_positive_test(name, value))


if __name__ == "__main__":
    unittest.main()