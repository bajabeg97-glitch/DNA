from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from dna_midi_studio import (
    GROOVE_PLAN_SCHEMA,
    GROOVE_PLAN_VERSION,
    MIDI_NOTE_CEILING,
    ROLE_POLICIES,
    GrooveControls,
    analyze_full_duration_polyphony,
    build_arrangement_graph,
    build_candidate_set,
    build_groove_plan,
    build_producer_brief,
    execute_groove_plan_api,
    execute_groove_plan_gui,
    simplify_event_plan,
    validate_groove_plan_v2,
)
from dna_midi_studio.candidate_search import _hash_without as candidate_hash_without
from dna_midi_studio.groove_polyphony import _hash_without as groove_hash_without
from dna_midi_studio.session19_fixture import build_labeled_benchmark
from dna_midi_studio.song_understanding import analyze_song_map


ROOT = Path(__file__).resolve().parents[1]


def _event(index: int, role: str, tier: str, priority: int, *, marker: str = "v4cv1",
           request: str | None = None, start: int = 0, duration: int = 100,
           channel: int = 12, locked: bool = False) -> dict:
    return {
        "eventId": f"synthetic-{index:03d}", "requestId": request or f"{marker}:{role}",
        "marker": marker, "role": role, "channelNumber": channel,
        "onsetTick": start, "durationTick": duration, "enabled": True,
        "priorityTier": tier, "preservePriority": priority,
        "pitchToken": 40 + index, "locked": locked,
    }


def _device_profile(status: str = "PA800_DEVICE_CERTIFIED", ceiling: float = 200.0,
                    unit: float = 1.0) -> dict:
    profile = {
        "schema": "dna-premium-device-profile", "version": "2.0",
        "manufacturer": "Korg", "model": "Pa800", "status": status,
        "voiceCostModel": {"measured": True, "ceilingUnits": ceiling,
                           "roleUnits": {role: unit for role in ROLE_POLICIES}},
        "certificationEvidenceHash": "a" * 64, "profileHash": "",
    }
    profile["profileHash"] = groove_hash_without(profile, "profileHash")
    return profile


class Session23GroovePolyphonyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        case = build_labeled_benchmark()[0]
        cls.song_map = analyze_song_map(case.midi, "session23-reference.mid")
        cls.brief = build_producer_brief(
            "Napravi zivlji pop-folk Style sa gitarom i podlogom, "
            "suptilnim prijelazima i punim refrenom."
        )
        cls.graph = build_arrangement_graph(cls.song_map, cls.brief, 2122, 2)
        cls.candidate = build_candidate_set(cls.graph, cls.song_map, ROOT, seed=2222, variant_count=3)
        cls.plan = build_groove_plan(cls.candidate, cls.graph, cls.song_map, ROOT, seed=2300)
        cls.repeat = build_groove_plan(cls.candidate, cls.graph, cls.song_map, ROOT, seed=2300)
        ready = next(item for item in cls.candidate["requests"] if item["rankedCandidates"])
        cls.lock_marker, cls.lock_role = ready["marker"], ready["role"]
        cls.lock_pattern = ready["rankedCandidates"][0]["patternId"]
        cls.locked_candidate = build_candidate_set(
            cls.graph, cls.song_map, ROOT, seed=2222, variant_count=2,
            controls={"version": "1.0", "lockedSelections": [{
                "marker": cls.lock_marker, "role": cls.lock_role,
                "patternId": cls.lock_pattern,
            }]},
        )
        cls.locked_plan = build_groove_plan(
            cls.locked_candidate, cls.graph, cls.song_map, ROOT, seed=2300
        )
        cls.keep_graph = build_arrangement_graph(
            cls.song_map, build_producer_brief("Make balanced pop with guitar. Lock v2cv1."), 23, 2
        )
        cls.keep_candidate = build_candidate_set(
            cls.keep_graph, cls.song_map, ROOT, seed=23, variant_count=2
        )
        cls.keep_plan = build_groove_plan(
            cls.keep_candidate, cls.keep_graph, cls.song_map, ROOT, seed=23
        )
        cls.certified_plan = build_groove_plan(
            cls.candidate, cls.graph, cls.song_map, ROOT, seed=2300,
            device_profile=_device_profile(),
        )

    def test_01_schema_constants_are_explicit(self) -> None:
        self.assertEqual((GROOVE_PLAN_SCHEMA, GROOVE_PLAN_VERSION),
                         ("dna-premium-groove-plan", "2.0"))

    def test_02_json_schema_is_strict_draft_2020_12(self) -> None:
        schema = json.loads((ROOT / "premium/schemas/v2/groove-plan-v2.schema.json").read_text())
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["x-contract-version"], "2.0")

    def test_03_schema_requires_every_root_field(self) -> None:
        schema = json.loads((ROOT / "premium/schemas/v2/groove-plan-v2.schema.json").read_text())
        self.assertEqual(set(schema["required"]), set(self.plan))

    def test_04_generated_plan_validates(self) -> None:
        self.assertIsNone(validate_groove_plan_v2(self.plan))

    def test_05_same_input_and_seed_are_deterministic(self) -> None:
        self.assertEqual(self.plan, self.repeat)

    def test_06_source_documents_are_not_mutated(self) -> None:
        candidate, graph, song = deepcopy(self.candidate), deepcopy(self.graph), deepcopy(self.song_map)
        build_groove_plan(self.candidate, self.graph, self.song_map, ROOT, seed=99)
        self.assertEqual((self.candidate, self.graph, self.song_map), (candidate, graph, song))

    def test_07_source_hashes_are_bound(self) -> None:
        source = self.plan["source"]
        self.assertEqual(source["candidateSetHash"], self.candidate["candidateSetHash"])
        self.assertEqual(source["graphHash"], self.graph["graphHash"])
        self.assertEqual(source["songMapHash"], self.song_map["mapHash"])

    def test_08_registry_provenance_is_preserved(self) -> None:
        self.assertEqual(self.plan["registries"], self.candidate["registries"])

    def test_09_all_candidate_variants_are_planned(self) -> None:
        self.assertEqual([item["variantId"] for item in self.plan["variants"]],
                         [item["variantId"] for item in self.candidate["variants"]])

    def test_10_every_selection_has_one_fragment(self) -> None:
        for source, planned in zip(self.candidate["variants"], self.plan["variants"]):
            self.assertEqual({item["requestId"] for item in source["selections"]},
                             {item["requestId"] for item in planned["fragments"]})

    def test_11_pa800_channels_are_exactly_nine_through_sixteen(self) -> None:
        self.assertEqual({item["channelNumber"] for item in self.plan["channelAssignments"]},
                         set(range(9, 17)))

    def test_12_every_role_has_one_channel_assignment(self) -> None:
        self.assertEqual({item["role"] for item in self.plan["channelAssignments"]},
                         set(ROLE_POLICIES))

    def test_13_role_policies_are_fixed_and_complete(self) -> None:
        actual = {item["role"]: {k: v for k, v in item.items() if k != "role"}
                  for item in self.plan["rolePolicies"]}
        self.assertEqual(actual, ROLE_POLICIES)

    def test_14_solo_policy_forbids_microtiming_and_gate_changes(self) -> None:
        self.assertEqual((ROLE_POLICIES["solo"]["microtimingLimitTicks"],
                          ROLE_POLICIES["solo"]["gateVariationLimitPercent"]), (0, 0))

    def test_15_every_template_hash_is_valid(self) -> None:
        self.assertTrue(all(item["templateHash"] == groove_hash_without(item, "templateHash")
                            for item in self.plan["grooveTemplates"]))

    def test_16_templates_reference_only_production_source_kinds(self) -> None:
        self.assertTrue(all(item["sourceKind"] in {"GOLD_PERFORMANCE", "FACTORY_STRUMMING"}
                            for item in self.plan["grooveTemplates"]))

    def test_17_gold_templates_have_timing_only_authority(self) -> None:
        gold = [item for item in self.plan["grooveTemplates"]
                if item["sourceKind"] == "GOLD_PERFORMANCE"]
        self.assertTrue(gold)
        self.assertTrue(all(item["timingAuthority"] == "TIMING_AND_GATE_ONLY" for item in gold))

    def test_18_factory_strumming_templates_remain_factory_timing(self) -> None:
        factory = [item for item in self.plan["grooveTemplates"]
                   if item["sourceKind"] == "FACTORY_STRUMMING"]
        self.assertTrue(factory)
        self.assertTrue(all(item["timingAuthority"] == "FACTORY_PERFORMANCE_TIMING"
                            for item in factory))

    def test_19_intentional_feel_is_explicit(self) -> None:
        self.assertTrue(all(item["intentionalFeel"] in {"STRAIGHT", "AHEAD", "LAID_BACK", "MIXED"}
                            for item in self.plan["grooveTemplates"]))

    def test_20_template_application_preserves_offset_direction(self) -> None:
        for template in self.plan["grooveTemplates"]:
            for step in template["steps"]:
                if step["medianSourceOffsetTicks"] > 0:
                    self.assertGreaterEqual(step["appliedOffsetTicks"], 0)
                if step["medianSourceOffsetTicks"] < 0:
                    self.assertLessEqual(step["appliedOffsetTicks"], 0)

    def test_21_every_event_hash_is_valid(self) -> None:
        self.assertTrue(all(event["eventHash"] == groove_hash_without(event, "eventHash")
                            for variant in self.plan["variants"] for fragment in variant["fragments"]
                            for event in fragment["events"]))

    def test_22_every_fragment_hash_is_valid(self) -> None:
        self.assertTrue(all(fragment["fragmentHash"] == groove_hash_without(fragment, "fragmentHash")
                            for variant in self.plan["variants"] for fragment in variant["fragments"]))

    def test_23_every_variant_hash_is_valid(self) -> None:
        self.assertTrue(all(item["variantHash"] == groove_hash_without(item, "variantHash")
                            for item in self.plan["variants"]))

    def test_24_plan_hash_is_valid(self) -> None:
        self.assertEqual(self.plan["groovePlanHash"], groove_hash_without(self.plan, "groovePlanHash"))

    def test_25_event_ids_are_unique_per_plan(self) -> None:
        ids = [event["eventId"] for variant in self.plan["variants"]
               for fragment in variant["fragments"] for event in fragment["events"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_26_all_event_durations_are_positive(self) -> None:
        self.assertTrue(all(event["durationTick"] >= 1 for variant in self.plan["variants"]
                            for fragment in variant["fragments"] for event in fragment["events"]))

    def test_27_all_events_remain_inside_their_marker(self) -> None:
        self.assertTrue(all(event["onsetTick"] + event["durationTick"] <= fragment["markerLengthTicks"]
                            for variant in self.plan["variants"] for fragment in variant["fragments"]
                            for event in fragment["events"]))

    def test_28_microtiming_never_exceeds_role_limit(self) -> None:
        self.assertTrue(all(abs(event["timingOffsetTick"])
                            <= ROLE_POLICIES[fragment["role"]]["microtimingLimitTicks"]
                            for variant in self.plan["variants"] for fragment in variant["fragments"]
                            for event in fragment["events"]))

    def test_29_gate_variation_never_exceeds_role_limit(self) -> None:
        for variant in self.plan["variants"]:
            for fragment in variant["fragments"]:
                limit = ROLE_POLICIES[fragment["role"]]["gateVariationLimitPercent"]
                for event in fragment["events"]:
                    allowed = round(event["originalDurationTick"] * limit / 100) + 1
                    self.assertLessEqual(abs(event["gateDeltaTick"]), allowed)

    def test_30_zero_strength_removes_all_microtiming_offsets(self) -> None:
        plan = build_groove_plan(self.candidate, self.graph, self.song_map, ROOT, seed=2,
                                 controls={"version": "1.0", "strength": 0})
        self.assertTrue(all(event["timingOffsetTick"] == 0 for variant in plan["variants"]
                            for fragment in variant["fragments"] for event in fragment["events"]))

    def test_31_zero_gate_strength_preserves_all_source_gate_lengths(self) -> None:
        plan = build_groove_plan(self.candidate, self.graph, self.song_map, ROOT, seed=2,
                                 controls={"version": "1.0", "gateStrength": 0})
        self.assertTrue(all(event["gateDeltaTick"] == 0 for variant in plan["variants"]
                            for fragment in variant["fragments"] for event in fragment["events"]))

    def test_32_locked_selection_is_preserved_in_every_variant(self) -> None:
        fragments = [fragment for variant in self.locked_plan["variants"]
                     for fragment in variant["fragments"]
                     if fragment["requestId"] == f"{self.lock_marker}:{self.lock_role}"]
        self.assertTrue(fragments)
        self.assertTrue(all(item["patternId"] == self.lock_pattern and item["locked"]
                            for item in fragments))

    def test_33_locked_fragment_has_no_microtiming_change(self) -> None:
        fragments = [fragment for variant in self.locked_plan["variants"]
                     for fragment in variant["fragments"] if fragment["locked"] and fragment["events"]]
        self.assertTrue(all(event["timingOffsetTick"] == 0 for fragment in fragments
                            for event in fragment["events"]))

    def test_34_locked_fragment_has_no_gate_change(self) -> None:
        fragments = [fragment for variant in self.locked_plan["variants"]
                     for fragment in variant["fragments"] if fragment["locked"] and fragment["events"]]
        self.assertTrue(all(event["gateDeltaTick"] == 0 for fragment in fragments
                            for event in fragment["events"]))

    def test_35_graph_keep_marker_stays_event_free(self) -> None:
        fragments = [fragment for variant in self.keep_plan["variants"]
                     for fragment in variant["fragments"] if fragment["marker"] == "v2cv1"]
        self.assertTrue(fragments)
        self.assertTrue(all(item["status"] == "KEEP_ORIGINAL" and not item["events"] for item in fragments))

    def test_36_locked_fragment_audit_is_true(self) -> None:
        self.assertIs(self.locked_plan["audit"]["lockedFragmentsPreserved"], True)

    def test_37_output_contains_no_dynamic_or_sound_authority_keys(self) -> None:
        forbidden = {"velocity", "velocities", "bank", "bankmsb", "banklsb", "program",
                     "programchange", "instrumentkey", "factoryprofileid"}
        def keys(value):
            if isinstance(value, dict):
                yield from (str(key).lower().replace("_", "") for key in value)
                for item in value.values():
                    yield from keys(item)
            elif isinstance(value, list):
                for item in value:
                    yield from keys(item)
        self.assertFalse(set(keys(self.plan)) & forbidden)

    def test_38_factory_dynamics_remain_unchanged(self) -> None:
        self.assertIs(self.plan["safety"]["factoryDynamicsUnchanged"], True)

    def test_39_gold_has_no_dynamics_or_bank_program_authority(self) -> None:
        self.assertEqual((self.plan["safety"]["goldDynamicsAuthority"],
                          self.plan["safety"]["goldBankProgramAuthority"]), (False, False))

    def test_40_sound_binding_and_original_solo_are_unchanged(self) -> None:
        self.assertEqual((self.plan["safety"]["soundBindingUnchanged"],
                          self.plan["safety"]["originalSoloUnchanged"]), (True, True))

    def test_41_full_duration_sweep_counts_sustained_overlap(self) -> None:
        events = [_event(1, "pad", "DECORATIVE", 30, start=0, duration=100),
                  _event(2, "pad", "DECORATIVE", 30, start=50, duration=100)]
        self.assertEqual(analyze_full_duration_polyphony(events)["peak"], 2)

    def test_42_note_off_precedes_note_on_at_same_tick(self) -> None:
        events = [_event(1, "bass", "CORE", 95, start=0, duration=10),
                  _event(2, "bass", "CORE", 95, start=10, duration=10)]
        self.assertEqual(analyze_full_duration_polyphony(events)["peak"], 1)

    def test_43_sustain_window_extends_note_until_release(self) -> None:
        events = [_event(1, "pad", "DECORATIVE", 30, start=0, duration=50),
                  _event(2, "pad", "DECORATIVE", 30, start=75, duration=10)]
        windows = [{"marker": "v4cv1", "channelNumber": 12, "startTick": 0, "endTick": 100}]
        self.assertEqual(analyze_full_duration_polyphony(events, windows)["peak"], 2)

    def test_44_long_sustain_tails_are_measured(self) -> None:
        events = [_event(i, "pad", "DECORATIVE", 30, start=i * 10, duration=200)
                  for i in range(12)]
        self.assertEqual(analyze_full_duration_polyphony(events)["peak"], 12)

    def test_45_every_variant_has_ten_section_peaks(self) -> None:
        self.assertTrue(all(len(item["polyphonyAfter"]["sections"]) == 10
                            for item in self.plan["variants"]))

    def test_46_every_variant_has_eight_track_peaks(self) -> None:
        self.assertTrue(all(len(item["polyphonyAfter"]["tracks"]) == 8
                            for item in self.plan["variants"]))

    def test_47_every_variant_has_eight_channel_peaks(self) -> None:
        self.assertTrue(all(len(item["polyphonyAfter"]["channels"]) == 8
                            for item in self.plan["variants"]))

    def test_48_software_ceiling_is_exactly_fifty_four(self) -> None:
        self.assertEqual((MIDI_NOTE_CEILING, self.plan["safety"]["softwareMidiNoteCeiling"]), (54, 54))

    def test_49_production_variants_are_inside_the_software_ceiling(self) -> None:
        self.assertTrue(all(item["polyphonyAfter"]["globalPeak"] <= 54
                            for item in self.plan["variants"]))

    def test_50_decorative_layer_is_removed_before_core(self) -> None:
        core = [_event(i, "drums", "CORE", 100, request="v4cv1:drums", channel=10)
                for i in range(40)]
        decorative = [_event(100 + i, "percussion", "DECORATIVE", 20,
                             request="v4cv1:percussion", channel=11) for i in range(20)]
        value = simplify_event_plan(core + decorative)
        self.assertEqual(value["operations"][0]["operation"], "DROP_DECORATIVE_LAYER")
        self.assertTrue(all(item["enabled"] for item in value["events"] if item["role"] == "drums"))

    def test_51_safe_simplification_reaches_the_ceiling(self) -> None:
        events = [_event(i, "drums", "CORE", 100, channel=10) for i in range(40)] + [
            _event(100 + i, "percussion", "DECORATIVE", 20, channel=11) for i in range(20)]
        value = simplify_event_plan(events)
        self.assertFalse(value["blocked"])
        self.assertLessEqual(value["after"]["peak"], 54)

    def test_52_support_voice_is_thinned_after_decorative_options(self) -> None:
        events = [_event(i, "drums", "CORE", 100, channel=10) for i in range(50)] + [
            _event(100 + i, "guitar", "SUPPORT", 75, channel=12) for i in range(10)]
        value = simplify_event_plan(events)
        self.assertTrue(value["operations"])
        self.assertTrue(all(item["operation"] == "THIN_SUPPORT_VOICE" for item in value["operations"]))
        self.assertEqual(value["after"]["peak"], 54)

    def test_53_core_overflow_is_never_silently_removed(self) -> None:
        events = [_event(i, "drums", "CORE", 100, channel=10) for i in range(60)]
        value = simplify_event_plan(events)
        self.assertTrue(value["blocked"])
        self.assertTrue(all(item["enabled"] for item in value["events"]))

    def test_54_locked_decorative_layer_is_never_removed(self) -> None:
        events = [_event(i, "drums", "CORE", 100, channel=10) for i in range(50)] + [
            _event(100 + i, "pad", "DECORATIVE", 30, channel=15, locked=True) for i in range(10)]
        value = simplify_event_plan(events)
        self.assertTrue(value["blocked"])
        self.assertTrue(all(item["enabled"] for item in value["events"] if item["role"] == "pad"))

    def test_55_manual_review_policy_never_auto_simplifies(self) -> None:
        events = [_event(i, "pad", "DECORATIVE", 30) for i in range(60)]
        value = simplify_event_plan(events, policy="MANUAL_REVIEW")
        self.assertTrue(value["blocked"])
        self.assertFalse(value["operations"])

    def test_56_unknown_control_field_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GrooveControls.from_mapping({"version": "1.0", "unknown": True})

    def test_57_invalid_strength_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GrooveControls.from_mapping({"version": "1.0", "strength": 101})

    def test_58_software_ceiling_cannot_be_weakened(self) -> None:
        with self.assertRaises(ValueError):
            GrooveControls.from_mapping({"version": "1.0", "softwareMidiNoteCeiling": 55})

    def test_59_mismatched_graph_is_rejected(self) -> None:
        other = build_arrangement_graph(self.song_map, self.brief, 2123, 2)
        with self.assertRaises(ValueError):
            build_groove_plan(self.candidate, other, self.song_map, ROOT)

    def test_60_api_and_gui_are_identical(self) -> None:
        payload = {"candidateSet": self.candidate, "arrangementGraph": self.graph,
                   "songMap": self.song_map, "seed": 2300}
        self.assertEqual(execute_groove_plan_api(payload, ROOT),
                         execute_groove_plan_gui(payload, ROOT))

    def test_61_api_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValueError):
            execute_groove_plan_api({"candidateSet": self.candidate,
                                     "arrangementGraph": self.graph, "songMap": self.song_map,
                                     "bypass": True}, ROOT)

    def test_62_cli_writes_the_same_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            paths = [directory / name for name in ("candidate.json", "graph.json", "song.json")]
            for path, value in zip(paths, (self.candidate, self.graph, self.song_map)):
                path.write_text(json.dumps(value), encoding="utf-8")
            output = directory / "groove.json"
            completed = subprocess.run(
                ["python", "session23_groove_plan.py", *map(str, paths), str(output), "--seed", "2300"],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(output.read_text()), self.plan)

    def test_63_device_cost_stays_unconfirmed_without_physical_profile(self) -> None:
        self.assertEqual(self.plan["deviceVoiceCost"]["status"], "UNCONFIRMED")
        self.assertTrue(self.plan["deviceVoiceCost"]["physicalPa800Required"])

    def test_64_confirmed_profile_activates_measured_cost_model(self) -> None:
        self.assertEqual(self.certified_plan["deviceVoiceCost"]["status"], "CONFIRMED_MODEL")
        self.assertTrue(self.certified_plan["safety"]["deviceVoiceCostConfirmed"])

    def test_65_uncertified_profile_cannot_activate_cost_estimation(self) -> None:
        plan = build_groove_plan(self.candidate, self.graph, self.song_map, ROOT,
                                 device_profile=_device_profile("WAITING_FOR_DEVICE"))
        self.assertEqual(plan["deviceVoiceCost"]["status"], "UNCONFIRMED")

    def test_66_confirmed_cost_overflow_requires_manual_review(self) -> None:
        plan = build_groove_plan(self.candidate, self.graph, self.song_map, ROOT,
                                 device_profile=_device_profile(ceiling=1.0, unit=10.0))
        self.assertFalse(plan["readyForRenderPlanning"])
        self.assertTrue(any(item["code"] == "DEVICE_VOICE_COST_OVERFLOW"
                            for item in plan["manualReview"]))

    def test_67_invalid_device_profile_hash_is_rejected(self) -> None:
        profile = _device_profile()
        profile["profileHash"] = "0" * 64
        with self.assertRaises(ValueError):
            build_groove_plan(self.candidate, self.graph, self.song_map, ROOT,
                              device_profile=profile)

    def test_68_plan_is_read_only_and_generates_no_final_midi(self) -> None:
        self.assertEqual((self.plan["safety"]["readOnly"],
                          self.plan["safety"]["midiMutationAllowed"],
                          self.plan["safety"]["finalMidiGenerated"]), (True, False, False))

    def test_69_different_seed_changes_the_groove_plan(self) -> None:
        other = build_groove_plan(self.candidate, self.graph, self.song_map, ROOT, seed=2301)
        self.assertNotEqual(self.plan["groovePlanHash"], other["groovePlanHash"])

    def test_70_server_and_gui_expose_the_groove_workflow(self) -> None:
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        gui = (ROOT / "web_gui.py").read_text(encoding="utf-8")
        self.assertIn("/api/premium-groove-plan", server)
        self.assertIn("GROOVE &amp; POLYPHONY 2.0", gui)


if __name__ == "__main__":
    unittest.main()