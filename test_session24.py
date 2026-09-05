from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from dna_midi_studio import (
    EXPRESSION_CONTROLS_VERSION,
    EXPRESSION_EVIDENCE_SCHEMA,
    EXPRESSION_EVIDENCE_VERSION,
    EXPRESSION_PLAN_SCHEMA,
    EXPRESSION_PLAN_VERSION,
    ORNAMENT_KINDS,
    RELATIONSHIP_KINDS,
    ExpressionControls,
    build_expression_evidence,
    build_expression_plan,
    execute_expression_plan_api,
    execute_expression_plan_gui,
    remove_ai_expression_layer,
    validate_expression_evidence,
    validate_expression_plan_v2,
    verify_solo_fingerprint,
)
from dna_midi_studio.midi import MidiEvent
from dna_midi_studio.premium_expression import (
    _cc11_plan,
    _hash_without,
    _note_records,
    _source_notes,
    _variant_budgets,
)
from dna_midi_studio.session24_fixture import build_session24_chain
from dna_midi_studio.track_identity import fingerprint_solo


ROOT = Path(__file__).resolve().parents[1]


class Session24PremiumExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        (cls.midi, cls.song_map, cls.brief, cls.graph, cls.candidate,
         cls.groove, cls.controls, cls.evidence) = build_session24_chain(ROOT)
        cls.plan = build_expression_plan(cls.midi, cls.groove, cls.song_map, ROOT,
                                         cls.controls, cls.evidence)
        cls.repeat = build_expression_plan(cls.midi, cls.groove, cls.song_map, ROOT,
                                           cls.controls, cls.evidence)

    def build(self, *, controls=None, evidence=None):
        return build_expression_plan(self.midi, self.groove, self.song_map, ROOT,
                                     controls or self.controls,
                                     self.evidence if evidence is None else evidence)

    def events(self, plan=None):
        return [event for layer in (plan or self.plan)["layers"] for event in layer["events"]]

    def layer(self, subtype, plan=None):
        return next(item for item in (plan or self.plan)["layers"] if item["subtype"] == subtype)

    def test_01_schema_constants_are_explicit(self):
        self.assertEqual((EXPRESSION_PLAN_SCHEMA, EXPRESSION_PLAN_VERSION),
                         ("dna-premium-expression-plan", "2.0"))

    def test_02_evidence_constants_are_explicit(self):
        self.assertEqual((EXPRESSION_EVIDENCE_SCHEMA, EXPRESSION_EVIDENCE_VERSION,
                          EXPRESSION_CONTROLS_VERSION),
                         ("dna-premium-expression-evidence", "1.0", "1.0"))

    def test_03_supported_layer_kinds_are_complete(self):
        self.assertEqual(set(ORNAMENT_KINDS), {"grace", "trill", "slide", "turnaround"})
        self.assertEqual(set(RELATIONSHIP_KINDS), {"third", "echo"})

    def test_04_json_schema_is_strict_draft_2020_12(self):
        schema = json.loads((ROOT / "premium/schemas/v2/expression-plan-v2.schema.json").read_text())
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["x-contract-version"], "2.0")

    def test_05_schema_requires_every_root_field(self):
        schema = json.loads((ROOT / "premium/schemas/v2/expression-plan-v2.schema.json").read_text())
        self.assertEqual(set(schema["required"]), set(self.plan))

    def test_06_generated_plan_validates(self):
        self.assertIsNone(validate_expression_plan_v2(self.plan))

    def test_07_same_input_and_seed_are_deterministic(self):
        self.assertEqual(self.plan, self.repeat)

    def test_08_source_documents_are_not_mutated(self):
        midi_hash = self.midi.digest()
        groove, song, controls, evidence = map(deepcopy,
                                               (self.groove, self.song_map, self.controls, self.evidence))
        self.build()
        self.assertEqual(self.midi.digest(), midi_hash)
        self.assertEqual((self.groove, self.song_map, self.controls, self.evidence),
                         (groove, song, controls, evidence))

    def test_09_plan_hash_is_valid(self):
        self.assertEqual(self.plan["expressionPlanHash"], _hash_without(self.plan, "expressionPlanHash"))

    def test_10_source_hashes_are_bound(self):
        source = self.plan["source"]
        self.assertEqual(source["inputMidiSha256"], self.midi.digest())
        self.assertEqual(source["songMapHash"], self.song_map["mapHash"])
        self.assertEqual(source["groovePlanHash"], self.groove["groovePlanHash"])

    def test_11_track_and_channel_numbers_are_one_based(self):
        self.assertEqual((self.plan["source"]["trackNumber"], self.plan["source"]["channelNumber"]), (4, 3))

    def test_12_original_fingerprint_is_bound_to_track_uid(self):
        fingerprint = self.plan["source"]["originalSoloFingerprint"]
        self.assertEqual(fingerprint["trackUid"], self.controls["trackUid"])
        self.assertEqual(fingerprint["trackNumber"], self.controls["trackNumber"])

    def test_13_original_note_count_matches_fingerprint(self):
        self.assertEqual(len(self.plan["originalNotes"]),
                         self.plan["source"]["originalSoloFingerprint"]["noteCount"])

    def test_14_source_note_uids_are_unique(self):
        uids = [item["sourceNoteUid"] for item in self.plan["originalNotes"]]
        self.assertEqual(len(uids), len(set(uids)))

    def test_15_original_notes_are_immutable(self):
        self.assertTrue(all(item["immutable"] for item in self.plan["originalNotes"]))

    def test_16_original_note_values_equal_source_midi(self):
        planned = [(x["onsetTick"], x["onsetTick"] + x["durationTick"], x["pitch"], x["velocity"])
                   for x in self.plan["originalNotes"]]
        actual = [(x.start, x.end, x.pitch, x.velocity) for x in self.midi.notes()
                  if x.track == 3 and x.channel == 2]
        self.assertEqual(planned, actual)

    def test_17_phrase_references_cover_original_notes(self):
        covered = {uid for phrase in self.plan["phrases"] for uid in phrase["sourceNoteUids"]}
        self.assertEqual(covered, {item["sourceNoteUid"] for item in self.plan["originalNotes"]})

    def test_18_phrase_references_come_from_songmap(self):
        expected = {item["id"] for item in self.song_map["phrases"]}
        self.assertTrue({item["phraseId"] for item in self.plan["phrases"]} <= expected)

    def test_19_factory_profile_is_exact_and_authoritative(self):
        profile = self.plan["factoryProfile"]
        self.assertEqual(profile["profileId"], self.controls["profileId"])
        self.assertEqual(profile["authority"], "FACTORY_ONLY")
        self.assertEqual(profile["soundBinding"], {"bankMsb": 120, "bankLsb": 0, "program": 64})

    def test_20_factory_profile_hash_is_valid(self):
        profile = self.plan["factoryProfile"]
        self.assertEqual(profile["profileHash"], _hash_without(profile, "profileHash"))

    def test_21_factory_curves_have_seven_points(self):
        profile = self.plan["factoryProfile"]
        self.assertEqual((len(profile["velocityCurve"]), len(profile["cc11Curve"])), (7, 7))

    def test_22_reference_evidence_validates(self):
        self.assertIsNone(validate_expression_evidence(self.evidence))

    def test_23_reference_evidence_hash_is_valid(self):
        self.assertEqual(self.evidence["evidenceHash"], _hash_without(self.evidence, "evidenceHash"))

    def test_24_builtin_evidence_cannot_claim_production(self):
        with self.assertRaisesRegex(ValueError, "SOFTWARE_TEST_ONLY"):
            build_expression_evidence(authority="PRODUCTION_VERIFIED")

    def test_25_test_evidence_blocks_production_render(self):
        self.assertEqual(self.plan["evidence"]["authority"], "SOFTWARE_TEST_ONLY")
        self.assertFalse(self.plan["evidence"]["productionEligible"])
        self.assertFalse(self.plan["readyForProductionRender"])

    def test_26_evidence_velocity_authority_is_rejected(self):
        evidence = deepcopy(self.evidence)
        evidence["ornaments"][0]["velocity"] = 90
        evidence["evidenceHash"] = _hash_without(evidence, "evidenceHash")
        with self.assertRaises(ValueError):
            validate_expression_evidence(evidence)

    def test_27_evidence_bank_authority_is_rejected(self):
        evidence = deepcopy(self.evidence)
        evidence["relationships"][0]["bankMsb"] = 120
        evidence["evidenceHash"] = _hash_without(evidence, "evidenceHash")
        with self.assertRaises(ValueError):
            validate_expression_evidence(evidence)

    def test_28_evidence_hash_tampering_is_rejected(self):
        evidence = deepcopy(self.evidence); evidence["notice"] += " tampered"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            validate_expression_evidence(evidence)

    def test_29_every_generated_event_has_source_evidence_and_reason(self):
        self.assertTrue(self.events())
        self.assertTrue(all(item["sourceNoteUid"] and item["evidenceId"] and item["reasonCode"]
                            for item in self.events()))

    def test_30_every_generated_event_hash_is_valid(self):
        self.assertTrue(all(item["eventHash"] == _hash_without(item, "eventHash")
                            for item in self.events()))

    def test_31_every_layer_hash_is_valid(self):
        self.assertTrue(all(item["layerHash"] == _hash_without(item, "layerHash")
                            for item in self.plan["layers"]))

    def test_32_generated_event_ids_are_unique(self):
        ids = [item["eventId"] for item in self.events()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_33_generated_velocities_are_factory_bounded(self):
        curve = self.plan["factoryProfile"]["velocityCurve"]
        self.assertTrue(all(min(curve) <= item["velocity"] <= max(curve) for item in self.events()))

    def test_34_ornaments_are_phrase_aware(self):
        phrase_ids = {item["phraseId"] for item in self.plan["phrases"]}
        ornaments = [item for item in self.events() if item["kind"] in ORNAMENT_KINDS]
        self.assertTrue(ornaments and all(item["phraseId"] in phrase_ids for item in ornaments))

    def test_35_grace_is_evidence_gated(self):
        events = self.layer("grace")["events"]
        self.assertTrue(events and all(item["evidenceId"] == "240.100.001" for item in events))

    def test_36_trill_is_evidence_gated(self):
        events = self.layer("trill")["events"]
        self.assertTrue(events and all(item["evidenceId"] == "240.100.002" for item in events))

    def test_37_turnaround_is_evidence_gated(self):
        events = self.layer("turnaround")["events"]
        self.assertTrue(events and all(item["evidenceId"] == "240.100.004" for item in events))

    def test_38_slide_path_works_when_selected_alone(self):
        controls = deepcopy(self.controls); controls["enabledLayers"] = ["slide"]
        plan = self.build(controls=controls)
        self.assertTrue(self.layer("slide", plan)["events"])

    def test_39_low_evidence_confidence_returns_keep(self):
        controls = deepcopy(self.controls); controls["minEvidenceConfidence"] = 0.99
        plan = self.build(controls=controls)
        self.assertTrue(all(layer["decision"] == "KEEP" for layer in plan["layers"]))

    def test_40_disabled_note_layers_return_keep(self):
        controls = deepcopy(self.controls); controls["enabledLayers"] = ["cc11"]
        plan = self.build(controls=controls)
        self.assertFalse(self.events(plan))
        self.assertTrue(all(layer["decision"] == "KEEP" for layer in plan["layers"]))

    def test_41_generated_notes_stay_in_register(self):
        register = self.plan["factoryProfile"]["register"]
        self.assertTrue(all(register["min"] <= item["pitch"] <= register["max"]
                            for item in self.events()))

    def test_42_generated_note_durations_are_positive_and_in_window(self):
        start, end = self.controls["startTick"], self.controls["endTick"]
        self.assertTrue(all(start <= item["onsetTick"]
                            and item["onsetTick"] + item["durationTick"] <= end
                            and item["durationTick"] > 0 for item in self.events()))

    def test_43_thirds_are_diatonic_minor_or_major_thirds(self):
        sources = {item["sourceNoteUid"]: item for item in self.plan["originalNotes"]}
        thirds = self.layer("third")["events"]
        self.assertTrue(thirds)
        self.assertTrue(all(item["pitch"] - sources[item["sourceNoteUid"]]["pitch"] in {3, 4}
                            for item in thirds))

    def test_44_thirds_use_separate_visual_layer(self):
        self.assertTrue(all(item["routing"] == "AI_HARMONY_PREVIEW_LAYER"
                            for item in self.layer("third")["events"]))

    def test_45_echo_is_nonrecursive_and_uses_original_sources(self):
        source_uids = {item["sourceNoteUid"] for item in self.plan["originalNotes"]}
        echo = self.layer("echo")["events"]
        self.assertTrue(echo and all(item["sourceNoteUid"] in source_uids for item in echo))
        self.assertTrue(self.plan["safety"]["nonRecursiveEcho"])

    def test_46_echo_uses_separate_delay_track(self):
        allocation = self.plan["delayAllocation"]
        self.assertTrue(all(item["routing"] == "SEPARATE_DELAY_PREVIEW_TRACK"
                            and item["trackUid"] == allocation["targetTrackUid"]
                            for item in self.layer("echo")["events"]))

    def test_47_echo_never_allocates_track_seventeen(self):
        self.assertLessEqual(self.plan["delayAllocation"]["targetTrackNumber"], 16)

    def test_48_echo_is_delayed_after_source_note_end(self):
        sources = {item["sourceNoteUid"]: item for item in self.plan["originalNotes"]}
        self.assertTrue(all(item["onsetTick"] >= sources[item["sourceNoteUid"]]["onsetTick"]
                            + sources[item["sourceNoteUid"]]["durationTick"]
                            for item in self.layer("echo")["events"]))

    def test_49_cc11_curve_is_factory_bounded(self):
        low, high = self.plan["cc11"]["bounds"]
        self.assertTrue(self.plan["cc11"]["points"])
        self.assertTrue(all(low <= item["value"] <= high for item in self.plan["cc11"]["points"]))

    def test_50_cc11_curve_is_smoothed(self):
        values = [item["value"] for item in self.plan["cc11"]["points"]]
        self.assertTrue(all(abs(right - left) <= self.plan["cc11"]["maxStep"]
                            for left, right in zip(values, values[1:])))

    def test_51_cc11_points_have_source_evidence_and_reason(self):
        self.assertTrue(all(item["sourceNoteUid"] and item["evidenceId"]
                            == self.controls["profileId"] and item["reasonCode"]
                            for item in self.plan["cc11"]["points"]))

    def test_52_existing_manual_cc11_is_preserved(self):
        midi = self.midi.add_events(track_index=3, new_events=[
            MidiEvent(240, -1, "channel", status=0xB0 | 2, data=bytes((11, 77)))])
        controls = ExpressionControls.from_mapping(self.controls)
        notes = _source_notes(midi, controls)
        records = _note_records(notes, controls)
        result = _cc11_plan(midi, records, self.song_map, self.plan["factoryProfile"], controls)
        self.assertEqual(result["decision"], "PRESERVE_MANUAL")
        self.assertTrue(result["manualExisting"])
        self.assertFalse(result["points"])

    def test_53_cc11_can_be_disabled(self):
        controls = deepcopy(self.controls); controls["enabledLayers"] = ["third"]
        plan = self.build(controls=controls)
        self.assertEqual(plan["cc11"]["decision"], "KEEP")
        self.assertFalse(plan["cc11"]["points"])

    def test_54_every_groove_variant_has_expression_budget(self):
        self.assertEqual([item["variantId"] for item in self.plan["variantBudgets"]],
                         [item["variantId"] for item in self.groove["variants"]])

    def test_55_budget_binds_source_variant_hash(self):
        by_id = {item["variantId"]: item for item in self.groove["variants"]}
        self.assertTrue(all(item["sourceVariantHash"] == by_id[item["variantId"]]["variantHash"]
                            for item in self.plan["variantBudgets"]))

    def test_56_groove_peak_is_measured_before_each_addition(self):
        by_id = {item["variantId"]: item for item in self.groove["variants"]}
        self.assertTrue(all(item["baselinePeak"] == by_id[item["variantId"]]["polyphonyAfter"]["globalPeak"]
                            for item in self.plan["variantBudgets"]))

    def test_57_all_expression_variants_stay_within_54_notes(self):
        self.assertTrue(all(item["withinMidiNoteCeiling"] and item["estimatedPeak"] <= 54
                            for item in self.plan["variantBudgets"]))

    def test_58_full_baseline_suppresses_note_layers(self):
        groove = {"variants": [{"variantId": "stress", "variantHash": "a" * 64,
                                "polyphonyAfter": {"globalPeak": 54}}]}
        budgets = _variant_budgets(groove, self.plan["layers"])
        self.assertEqual(budgets[0]["decision"], "KEEP")
        self.assertFalse(budgets[0]["enabledEventIds"])
        self.assertTrue(budgets[0]["suppressedEventIds"])

    def test_59_overflow_simplification_starts_with_echo(self):
        groove = {"variants": [{"variantId": "stress", "variantHash": "a" * 64,
                                "polyphonyAfter": {"globalPeak": 54}}]}
        budget = _variant_budgets(groove, self.plan["layers"])[0]
        self.assertEqual(budget["operations"][0]["operation"], "DROP_ECHO_LAYER")

    def test_60_budget_hashes_are_valid(self):
        self.assertTrue(all(item["budgetHash"] == _hash_without(item, "budgetHash")
                            for item in self.plan["variantBudgets"]))

    def test_61_a_b_previews_are_explicit(self):
        self.assertEqual([(item["previewId"], item["label"]) for item in self.plan["previews"]],
                         [("A", "ORIGINAL_SOLO"), ("B", "AI_EXPRESSION_LAYER")])

    def test_62_a_preview_contains_no_ai_events(self):
        preview = self.plan["previews"][0]
        self.assertFalse(preview["eventIds"] or preview["cc11PointIds"])

    def test_63_b_preview_contains_ai_expression(self):
        preview = self.plan["previews"][1]
        self.assertTrue(preview["eventIds"] and preview["cc11PointIds"])

    def test_64_both_previews_bind_same_original_fingerprint(self):
        expected = self.plan["source"]["originalSoloFingerprint"]["sha256"]
        self.assertTrue(all(item["originalFingerprintHash"] == expected for item in self.plan["previews"]))

    def test_65_preview_hashes_are_valid(self):
        self.assertTrue(all(item["previewHash"] == _hash_without(item, "previewHash")
                            for item in self.plan["previews"]))

    def test_66_removal_operation_targets_only_generated_events(self):
        self.assertEqual(set(self.plan["removalOperation"]["eventIds"]),
                         {item["eventId"] for item in self.events()})
        self.assertTrue(self.plan["removalOperation"]["removesOnlyAiLayers"])

    def test_67_one_click_removal_preserves_original(self):
        result = remove_ai_expression_layer(self.plan)
        self.assertTrue(result["originalNotesPreserved"] and result["soundBindingPreserved"])
        self.assertEqual(result["resultPreviewId"], "A")
        self.assertEqual(result["originalSoloFingerprint"],
                         self.plan["source"]["originalSoloFingerprint"])

    def test_68_original_fingerprint_verifies_against_unchanged_midi(self):
        controls = ExpressionControls.from_mapping(self.controls)
        fingerprint = fingerprint_solo(self.midi, track_index=controls.track_index,
                                       channel=controls.channel_index,
                                       start_tick=controls.start_tick, end_tick=controls.end_tick,
                                       track_uid=controls.track_uid)
        self.assertTrue(verify_solo_fingerprint(fingerprint, self.midi)["passed"])

    def test_69_api_and_gui_match_core(self):
        payload = {"midiHex": self.midi.to_bytes().hex(), "groovePlan": self.groove,
                   "songMap": self.song_map, "controls": self.controls,
                   "evidence": self.evidence}
        self.assertEqual(execute_expression_plan_api(payload, ROOT), self.plan)
        self.assertEqual(execute_expression_plan_gui(payload, ROOT), self.plan)
        self.assertIn('/api/premium-expression-plan', (ROOT / "server.py").read_text())
        self.assertIn('PREMIUM SOLO &amp; EXPRESSION 2.0', (ROOT / "web_gui.py").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            inputs = {
                "midi": folder / "source.mid", "groove": folder / "groove.json",
                "song": folder / "song.json", "controls": folder / "controls.json",
                "evidence": folder / "evidence.json", "output": folder / "plan.json",
            }
            inputs["midi"].write_bytes(self.midi.to_bytes())
            for name, value in (("groove", self.groove), ("song", self.song_map),
                                ("controls", self.controls), ("evidence", self.evidence)):
                inputs[name].write_text(json.dumps(value), encoding="utf-8")
            completed = subprocess.run([
                "python", str(ROOT / "session24_expression_plan.py"),
                str(inputs["midi"]), str(inputs["groove"]), str(inputs["song"]),
                str(inputs["controls"]), str(inputs["output"]),
                "--evidence", str(inputs["evidence"]),
            ], cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(inputs["output"].read_text()), self.plan)

    def test_70_api_rejects_unknown_fields(self):
        with self.assertRaisesRegex(ValueError, "Unknown"):
            execute_expression_plan_api({"unexpected": True}, ROOT)

    def test_71_api_rejects_invalid_midi_hex(self):
        payload = {"midiHex": "not-hex", "groovePlan": self.groove,
                   "songMap": self.song_map, "controls": self.controls}
        with self.assertRaisesRegex(ValueError, "hexadecimal"):
            execute_expression_plan_api(payload, ROOT)

    def test_72_controls_reject_unknown_fields(self):
        controls = deepcopy(self.controls); controls["bypassValidator"] = True
        with self.assertRaisesRegex(ValueError, "Unknown"):
            ExpressionControls.from_mapping(controls)

    def test_73_track_uid_mismatch_returns_manual_review(self):
        controls = deepcopy(self.controls); controls["trackUid"] = "trk-00000000000000000000"
        plan = self.build(controls=controls)
        self.assertFalse(plan["readyForPreview"])
        self.assertTrue(plan["manualReview"])

    def test_74_missing_factory_profile_returns_manual_review(self):
        controls = deepcopy(self.controls); controls["profileId"] = "999.999.999"
        plan = self.build(controls=controls)
        self.assertFalse(plan["readyForPreview"])
        self.assertIn("Factory", plan["manualReview"][0]["reason"])

    def test_75_tampered_event_hash_is_rejected(self):
        value = deepcopy(self.plan)
        layer = next(layer for layer in value["layers"] if layer["events"])
        layer["events"][0]["pitch"] += 1
        layer["layerHash"] = _hash_without(layer, "layerHash")
        value["expressionPlanHash"] = _hash_without(value, "expressionPlanHash")
        with self.assertRaisesRegex(ValueError, "event hash"):
            validate_expression_plan_v2(value)

    def test_76_tampered_safety_contract_is_rejected(self):
        value = deepcopy(self.plan); value["safety"]["midiMutationAllowed"] = True
        value["expressionPlanHash"] = _hash_without(value, "expressionPlanHash")
        with self.assertRaisesRegex(ValueError, "safety contract"):
            validate_expression_plan_v2(value)

    def test_77_tampered_preview_fingerprint_is_rejected(self):
        value = deepcopy(self.plan); value["previews"][1]["originalFingerprintHash"] = "0" * 64
        value["previews"][1]["previewHash"] = _hash_without(value["previews"][1], "previewHash")
        value["expressionPlanHash"] = _hash_without(value, "expressionPlanHash")
        with self.assertRaisesRegex(ValueError, "original fingerprint"):
            validate_expression_plan_v2(value)

    def test_78_tampered_removal_scope_is_rejected(self):
        value = deepcopy(self.plan); value["removalOperation"]["eventIds"].pop()
        value["removalOperation"]["operationHash"] = _hash_without(value["removalOperation"], "operationHash")
        value["expressionPlanHash"] = _hash_without(value, "expressionPlanHash")
        with self.assertRaisesRegex(ValueError, "removal contract"):
            validate_expression_plan_v2(value)

    def test_79_read_only_safety_is_explicit(self):
        safety = self.plan["safety"]
        self.assertTrue(safety["readOnly"] and safety["originalSoloUnchanged"]
                        and safety["soundBindingUnchanged"] and safety["aiLayersRemovable"])
        self.assertFalse(safety["midiMutationAllowed"] or safety["finalMidiGenerated"]
                         or safety["goldDynamicsAuthority"])

    def test_80_reference_plan_is_preview_ready_but_production_blocked(self):
        self.assertTrue(self.plan["readyForPreview"])
        self.assertFalse(self.plan["readyForProductionRender"])
        self.assertEqual(self.plan["productionBlocks"], ["PRODUCTION_ORNAMENT_EVIDENCE_REQUIRED"])


if __name__ == "__main__":
    unittest.main()