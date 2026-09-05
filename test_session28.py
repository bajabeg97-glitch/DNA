from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dna_midi_studio import (  # noqa: E402
    COMMANDS,
    RECOVERY_SCHEMA,
    RECOVERY_VERSION,
    STAGE_IDS,
    WORKFLOW_CONTROLS_VERSION,
    WORKFLOW_SCHEMA,
    WORKFLOW_VERSION,
    build_premium_workflow,
    build_recovery_checkpoint,
    execute_premium_workflow_api,
    execute_premium_workflow_gui,
    resume_workflow,
    validate_premium_workflow_v2,
    validate_recovery_checkpoint,
)
from dna_midi_studio.session28_fixture import build_session28_chain  # noqa: E402


class Session28PremiumWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = build_session28_chain(ROOT)
        cls.workflow = cls.fixture["workflow"]

    def test_001_contract_constants(self):
        self.assertEqual((WORKFLOW_SCHEMA, WORKFLOW_VERSION),
                         ("dna-premium-producer-workflow", "2.0"))

    def test_002_recovery_constants(self):
        self.assertEqual((RECOVERY_SCHEMA, RECOVERY_VERSION),
                         ("dna-premium-workflow-recovery", "1.0"))

    def test_003_control_version(self):
        self.assertEqual(WORKFLOW_CONTROLS_VERSION, "1.0")

    def test_004_workflow_schema_is_strict(self):
        schema = json.loads((ROOT / "premium/schemas/v2/premium-workflow-v2.schema.json").read_text())
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])

    def test_005_recovery_schema_is_strict(self):
        schema = json.loads((ROOT / "premium/schemas/v2/workflow-recovery-v1.schema.json").read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["x-contract-version"], "1.0")

    def test_006_generated_workflow_validates(self):
        validate_premium_workflow_v2(self.workflow)

    def test_007_workflow_hash_is_valid(self):
        self.assertRegex(self.workflow["workflowHash"], r"^[0-9a-f]{64}$")

    def test_008_workflow_id_is_stable(self):
        self.assertRegex(self.workflow["workflowId"], r"^workflow-[0-9a-f]{20}$")

    def test_009_workflow_is_deterministic(self):
        second = build_premium_workflow(self.fixture["documents"], self.fixture["workflowControls"])
        self.assertEqual(second, self.workflow)

    def test_010_source_documents_are_not_mutated(self):
        documents = deepcopy(self.fixture["documents"])
        build_premium_workflow(documents, self.fixture["workflowControls"])
        self.assertEqual(documents, self.fixture["documents"])

    def test_011_source_hashes_are_complete(self):
        hashes = [value for key, value in self.workflow["source"].items()
                  if key.endswith("Hash") or key.endswith("Sha256")]
        self.assertTrue(hashes and all(len(value) == 64 for value in hashes))

    def test_012_chain_checks_are_explicit(self):
        self.assertEqual(len(self.workflow["source"]["chainChecks"]), 11)

    def test_013_source_midi_matches_fixture(self):
        self.assertEqual(self.workflow["source"]["midiSha256"], self.fixture["midi"].digest())

    def test_014_selected_variant_is_c(self):
        self.assertEqual(self.workflow["controls"]["selectedVariantId"], "C")

    def test_015_element_lock_is_preserved(self):
        self.assertEqual(self.workflow["locks"]["elements"], ["v2cv1"])

    def test_016_global_lock_is_off(self):
        self.assertFalse(self.workflow["locks"]["global"])

    def test_017_first_seven_stages_complete(self):
        self.assertTrue(all(item["status"] == "COMPLETE" for item in self.workflow["stages"][:7]))

    def test_018_export_stage_is_blocked(self):
        self.assertEqual(self.workflow["stages"][-1]["status"], "BLOCKED")

    def test_019_every_stage_can_open(self):
        self.assertTrue(all(item["canOpen"] for item in self.workflow["stages"]))

    def test_020_every_stage_has_job(self):
        self.assertEqual(len(self.workflow["jobs"]), len(STAGE_IDS))

    def test_021_job_stage_mapping_is_exact(self):
        self.assertEqual([item["stageId"] for item in self.workflow["jobs"]], list(STAGE_IDS))

    def test_022_completed_job_progress_is_full(self):
        self.assertTrue(all(item["progressPercent"] == 100 for item in self.workflow["jobs"][:7]))

    def test_023_timeline_has_all_pa800_elements(self):
        self.assertEqual(len(self.workflow["timeline"]), 10)

    def test_024_timeline_energy_is_bounded(self):
        self.assertTrue(all(0 <= item["targetEnergy"] <= 100 for item in self.workflow["timeline"]))

    def test_025_timeline_has_harmonic_context(self):
        self.assertTrue(all(item["harmonicContext"] for item in self.workflow["timeline"]))

    def test_026_timeline_has_transition_obligations(self):
        self.assertTrue(any(item["transition"] and item["transition"]["obligations"]
                            for item in self.workflow["timeline"]))

    def test_027_timeline_lock_count_matches(self):
        self.assertEqual(self.workflow["locks"]["lockedTimelineItems"], 1)

    def test_028_track_matrix_has_physical_tracks(self):
        self.assertEqual(len(self.workflow["trackMatrix"]), 4)

    def test_029_track_uids_are_unique(self):
        ids = [item["trackUid"] for item in self.workflow["trackMatrix"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_030_track_numbers_are_one_based(self):
        self.assertTrue(all(item["trackNumber"] == item["trackIndex"] + 1
                            for item in self.workflow["trackMatrix"]))

    def test_031_channel_numbers_are_one_based(self):
        self.assertTrue(all(item["channelNumber"] == item["channelIndex"] + 1
                            for item in self.workflow["trackMatrix"]))

    def test_032_track_registers_are_valid(self):
        self.assertTrue(all(0 <= item["register"]["low"] <= item["register"]["high"] <= 127
                            for item in self.workflow["trackMatrix"]))

    def test_033_track_peaks_are_full_duration(self):
        self.assertTrue(all(item["fullDurationPeak"] >= 1 for item in self.workflow["trackMatrix"]))

    def test_034_sound_binding_is_visible(self):
        self.assertTrue(all("status" in item["soundBinding"] for item in self.workflow["trackMatrix"]))

    def test_035_articulation_status_is_visible(self):
        self.assertTrue(all(item["articulationStatus"] == "DEVICE_CAPTURE_BLOCKED"
                            for item in self.workflow["trackMatrix"]))

    def test_036_explain_covers_selected_fragments(self):
        selected = next(item for item in self.fixture["documents"]["candidateSet"]["variants"]
                        if item["variantId"] == "variant-C")
        self.assertEqual(len(self.workflow["explain"]), len(selected["selections"]))

    def test_037_every_explanation_is_hashed(self):
        self.assertTrue(all(len(item["explainHash"]) == 64 for item in self.workflow["explain"]))

    def test_038_explanations_show_authority(self):
        self.assertTrue(all("AUTHORITY_COMPLIANT" in item["reasons"] for item in self.workflow["explain"]))

    def test_039_guitar_explanations_are_factory(self):
        guitar = [item for item in self.workflow["explain"] if item["role"] == "guitar"]
        self.assertTrue(guitar and all(item["sourceKind"] == "FACTORY_STRUMMING" for item in guitar))

    def test_040_ending_explanation_shows_export_blocker(self):
        ending = [item for item in self.workflow["explain"] if item["marker"].startswith("e")]
        self.assertTrue(ending and all(item["blockers"] for item in ending))

    def test_041_diff_is_hashed(self):
        self.assertRegex(self.workflow["diff"]["diffHash"], r"^[0-9a-f]{64}$")

    def test_042_diff_has_added_preview_notes(self):
        self.assertGreater(self.workflow["diff"]["notes"]["added"], 0)

    def test_043_diff_preserves_original_notes(self):
        self.assertEqual(self.workflow["diff"]["notes"]["originalNotesChanged"], 0)

    def test_044_diff_exposes_cc11(self):
        self.assertEqual(self.workflow["diff"]["controllers"]["cc11Added"], 16)

    def test_045_diff_preserves_sound_setup(self):
        self.assertEqual(self.workflow["diff"]["soundSetup"]["soundBindingChanges"], 0)

    def test_046_diff_has_all_manifest_hashes(self):
        self.assertEqual(len(self.workflow["diff"]["manifest"]["artifactHashes"]), 8)

    def test_047_independent_validator_passes(self):
        self.assertTrue(self.workflow["verification"]["independentValidatorPassed"])

    def test_048_technical_quality_passes(self):
        self.assertTrue(self.workflow["verification"]["technicalQualityPassed"])

    def test_049_human_quality_does_not_pass(self):
        self.assertFalse(self.workflow["verification"]["humanQualityPassed"])

    def test_050_device_is_not_certified(self):
        self.assertFalse(self.workflow["verification"]["deviceCertified"])

    def test_051_quality_blockers_reach_export(self):
        self.assertTrue(any(item.startswith("QUALITY_") for item in self.workflow["exportGate"]["blockers"]))

    def test_052_device_blocker_reaches_export(self):
        self.assertIn("PA800_DEVICE_PROFILE_NOT_CERTIFIED", self.workflow["exportGate"]["blockers"])

    def test_053_expression_blocker_reaches_export(self):
        self.assertIn("EXPRESSION_PRODUCTION_EVIDENCE_BLOCKED", self.workflow["exportGate"]["blockers"])

    def test_054_preview_and_project_download_remain_allowed(self):
        self.assertTrue(self.workflow["exportGate"]["previewDownloadAllowed"])
        self.assertTrue(self.workflow["exportGate"]["projectDownloadAllowed"])

    def test_055_export_requires_verifier_and_human(self):
        self.assertTrue(self.workflow["exportGate"]["requiresIndependentVerifier"])
        self.assertTrue(self.workflow["exportGate"]["requiresHumanApproval"])

    def test_056_command_palette_and_accessibility(self):
        export = next(item for item in self.workflow["commandPalette"] if item["commandId"] == "EXPORT")
        self.assertFalse(export["enabled"])
        self.assertTrue(self.workflow["accessibility"]["keyboardReachable"])
        self.assertFalse(self.workflow["accessibility"]["colorOnlyStatus"])

    def test_057_producer_task_and_safety(self):
        self.assertTrue(self.workflow["producerTask"]["guidedWithoutTerminal"])
        self.assertTrue(self.workflow["producerTask"]["referencePreviewTaskComplete"])
        self.assertFalse(self.workflow["safety"]["midiMutationAllowed"])
        self.assertFalse(self.workflow["safety"]["validatorBypassAllowed"])

    def test_058_cancel_creates_safe_checkpoint(self):
        checkpoint = build_recovery_checkpoint(self.workflow, "VERIFY")
        validate_recovery_checkpoint(checkpoint)
        self.assertFalse(checkpoint["midiEmbedded"])
        self.assertFalse(checkpoint["audioEmbedded"])

    def test_059_resume_verifies_checkpoint_identity(self):
        checkpoint = build_recovery_checkpoint(self.workflow, "VERIFY")
        self.assertEqual(resume_workflow(self.workflow, checkpoint)["status"], "READY_TO_RESUME")
        tampered = deepcopy(checkpoint)
        tampered["workflowHash"] = "0" * 64
        tampered["checkpointHash"] = __import__("hashlib").sha256(json.dumps(
            {key: value for key, value in tampered.items() if key != "checkpointHash"},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        with self.assertRaisesRegex(ValueError, "does not belong"):
            resume_workflow(self.workflow, tampered)

    def test_060_transports_cli_and_negative_guards(self):
        api = execute_premium_workflow_api({"action": "build", "documents": self.fixture["documents"],
                                            "controls": self.fixture["workflowControls"]}, ROOT)
        gui = execute_premium_workflow_gui({"action": "build", "documents": self.fixture["documents"],
                                            "controls": self.fixture["workflowControls"]}, ROOT)
        self.assertEqual(api, gui)
        with self.assertRaisesRegex(ValueError, "Unknown workflow controls"):
            build_premium_workflow(self.fixture["documents"], {"rogue": True})
        bad = deepcopy(self.fixture["documents"])
        bad["arrangementGraph"]["source"]["songMapHash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source chain mismatch"):
            build_premium_workflow(bad, self.fixture["workflowControls"])
        help_run = subprocess.run([sys.executable, str(ROOT / "session28_premium_workflow.py"), "--help"],
                                  capture_output=True, text=True)
        self.assertEqual(help_run.returncode, 0)
        self.assertIn("Premium Producer workflow", help_run.stdout)
        self.assertIn("/api/premium-producer-workflow", (ROOT / "server.py").read_text())
        gui_source = (ROOT / "web_gui.py").read_text()
        self.assertIn("PREMIUM PRODUCER WORKFLOW 2.0", gui_source)
        self.assertIn("TRACK MATRIX", gui_source)


def _add_dynamic_tests():
    for index, stage_id in enumerate(STAGE_IDS):
        def test(self, index=index, stage_id=stage_id):
            stage = self.workflow["stages"][index]
            self.assertEqual(stage["stageId"], stage_id)
            self.assertEqual(stage["index"], index)
        setattr(Session28PremiumWorkflowTests, f"test_{61 + index:03d}_stage_{stage_id.lower()}", test)

    offset = 69
    for index in range(10):
        def test(self, index=index):
            item = self.workflow["timeline"][index]
            self.assertEqual(item["index"], index)
            self.assertRegex(item["timelineItemHash"], r"^[0-9a-f]{64}$")
        setattr(Session28PremiumWorkflowTests, f"test_{offset + index:03d}_timeline_item_{index + 1}", test)

    offset = 79
    for index in range(4):
        def test(self, index=index):
            item = self.workflow["trackMatrix"][index]
            self.assertEqual(item["mappingStatus"], "EXACT_TRACK_UID")
            self.assertRegex(item["trackRowHash"], r"^[0-9a-f]{64}$")
        setattr(Session28PremiumWorkflowTests, f"test_{offset + index:03d}_track_matrix_row_{index + 1}", test)

    offset = 83
    for index, (command_id, _, shortcut) in enumerate(COMMANDS):
        def test(self, index=index, command_id=command_id, shortcut=shortcut):
            item = self.workflow["commandPalette"][index]
            self.assertEqual((item["commandId"], item["shortcut"]), (command_id, shortcut))
        setattr(Session28PremiumWorkflowTests, f"test_{offset + index:03d}_command_{command_id.lower()}", test)

    offset = 93
    for index in range(20):
        def test(self, index=index):
            item = self.workflow["explain"][index]
            self.assertTrue(item["patternId"])
            self.assertTrue(item["reasons"])
            self.assertGreaterEqual(item["score"], 0)
        setattr(Session28PremiumWorkflowTests, f"test_{offset + index:03d}_explain_row_{index + 1}", test)


_add_dynamic_tests()


if __name__ == "__main__":
    unittest.main()