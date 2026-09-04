from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from dna_midi_studio import (
    GRAPH_SCHEMA,
    GRAPH_VERSION,
    MAX_CONCURRENT_MIDI_NOTES,
    MidiEvent,
    MidiFile,
    MidiTrack,
    build_arrangement_graph,
    build_producer_brief,
    execute_arrangement_graph_api,
    execute_arrangement_graph_gui,
    validate_arrangement_graph_v2,
)
from dna_midi_studio.arrangement_graph import OBLIGATIONS, STRATEGIES
from dna_midi_studio.producer_brief import ELEMENTS, ROLES
from dna_midi_studio.session19_fixture import PPQ, build_benchmark_case
from dna_midi_studio.song_understanding import _map_hash, analyze_song_map


ROOT = Path(__file__).resolve().parents[1]


def _fallback_song_map() -> dict:
    midi = MidiFile(0, PPQ, [MidiTrack([
        MidiEvent(0, 0, "channel", status=0x90, data=bytes((60, 64))),
        MidiEvent(PPQ, 1, "channel", status=0x80, data=bytes((60, 0))),
    ])]).to_bytes()
    return analyze_song_map(midi, "uncertain.mid")


def _rehash_song_map(value: dict) -> dict:
    value["mapHash"] = _map_hash(value)
    return value


class Session21ArrangementGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        case = build_benchmark_case(3)
        cls.song_map = analyze_song_map(case.midi, case.case_id + ".mid")
        cls.brief = build_producer_brief(
            "Napravi življi pop-folk Style sa gitarom i podlogom, "
            "sa suzdržanom strofom i punim refrenom."
        )
        cls.graph = build_arrangement_graph(cls.song_map, cls.brief, seed=2100, variant_count=2)

    def test_01_schema_constants_are_explicit(self) -> None:
        self.assertEqual((GRAPH_SCHEMA, GRAPH_VERSION),
                         ("dna-premium-arrangement-graph", "2.0"))

    def test_02_json_schema_is_strict_draft_2020_12(self) -> None:
        value = json.loads((ROOT / "premium/schemas/v2/arrangement-graph-v2.schema.json").read_text())
        self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIs(value["additionalProperties"], False)
        self.assertEqual(value["x-contract-version"], "2.0")

    def test_03_json_schema_requires_all_root_fields(self) -> None:
        value = json.loads((ROOT / "premium/schemas/v2/arrangement-graph-v2.schema.json").read_text())
        self.assertEqual(set(value["required"]), set(self.graph))

    def test_04_generated_graph_validates(self) -> None:
        self.assertIsNone(validate_arrangement_graph_v2(self.graph))

    def test_05_same_input_and_seed_are_deterministic(self) -> None:
        self.assertEqual(self.graph, build_arrangement_graph(self.song_map, self.brief, 2100, 2))

    def test_06_source_documents_are_not_mutated(self) -> None:
        song_map, brief = deepcopy(self.song_map), deepcopy(self.brief)
        build_arrangement_graph(self.song_map, self.brief, 3, 2)
        self.assertEqual((self.song_map, self.brief), (song_map, brief))

    def test_07_source_hashes_are_bound_into_graph(self) -> None:
        self.assertEqual(self.graph["source"]["songMapHash"], self.song_map["mapHash"])
        self.assertEqual(self.graph["source"]["producerBriefHash"], self.brief["briefHash"])
        self.assertEqual(self.graph["source"]["sourceMidiSha256"], self.song_map["sourceSha256"])

    def test_08_seed_is_auditable(self) -> None:
        self.assertEqual(self.graph["source"]["seed"], 2100)

    def test_09_all_ten_pa800_markers_are_present(self) -> None:
        self.assertEqual({item["marker"] for item in self.graph["nodes"]}, set(ELEMENTS))

    def test_10_element_type_counts_are_complete(self) -> None:
        counts = {kind: sum(item["elementType"] == kind for item in self.graph["nodes"])
                  for kind in ("intro", "variation", "fill", "ending")}
        self.assertEqual(counts, {"intro": 2, "variation": 4, "fill": 2, "ending": 2})

    def test_11_node_bars_are_bounded(self) -> None:
        self.assertTrue(all(1 <= item["bars"] <= 32 for item in self.graph["nodes"]))
        self.assertTrue(all(item["bars"] == 1 for item in self.graph["nodes"]
                            if item["elementType"] == "fill"))

    def test_12_variation_energy_is_a_controlled_rise(self) -> None:
        values = [next(item["targetEnergy"] for item in self.graph["nodes"]
                       if item["marker"] == f"v{i}cv1") for i in range(1, 5)]
        self.assertEqual(values, sorted(values))
        self.assertGreater(values[-1], values[0])

    def test_13_density_is_controlled_vocabulary(self) -> None:
        self.assertTrue({item["targetDensity"] for item in self.graph["nodes"]}
                        <= {"sparse", "balanced", "full"})

    def test_14_node_roles_are_controlled(self) -> None:
        self.assertTrue(all(set(item["roles"]) <= set(ROLES) for item in self.graph["nodes"]))

    def test_15_required_roles_are_global_obligations(self) -> None:
        non_fills = [item for item in self.graph["nodes"] if item["elementType"] != "fill"]
        self.assertTrue(all({"guitar", "pad"} <= set(item["roles"]) for item in non_fills))

    def test_16_forbidden_roles_are_removed(self) -> None:
        brief = build_producer_brief("Make a full pop style without percussion.")
        graph = build_arrangement_graph(self.song_map, brief)
        self.assertTrue(all("percussion" not in item["roles"] for item in graph["nodes"]))

    def test_17_locked_element_is_keep(self) -> None:
        graph = build_arrangement_graph(self.song_map, build_producer_brief("Make pop and lock v2cv1."))
        node = next(item for item in graph["nodes"] if item["marker"] == "v2cv1")
        self.assertTrue(node["locked"])
        self.assertEqual(node["decision"], "KEEP")

    def test_18_locked_element_has_zero_transformation_budget(self) -> None:
        graph = build_arrangement_graph(self.song_map, build_producer_brief("Make pop and lock f1cv1."))
        node = next(item for item in graph["nodes"] if item["marker"] == "f1cv1")
        self.assertEqual((node["transformationBudget"]["maximumOperations"],
                          node["transformationBudget"]["maximumAddedNotes"]), (0, 0))

    def test_19_zero_tolerance_creates_keep_only_plan(self) -> None:
        graph = build_arrangement_graph(self.song_map, build_producer_brief("Make pop, do not change."))
        self.assertTrue(all(item["decision"] == "KEEP" for item in graph["nodes"]))
        self.assertEqual(graph["globalBudgets"]["maximumTransformations"], 0)

    def test_20_one_motif_family_connects_every_element(self) -> None:
        self.assertEqual(len({item["motifFamilyId"] for item in self.graph["nodes"]}), 1)

    def test_21_each_element_has_a_motif_treatment(self) -> None:
        self.assertEqual(len({item["motifTreatment"] for item in self.graph["nodes"]}), 10)

    def test_22_every_node_has_harmonic_context(self) -> None:
        self.assertTrue(all(item["harmonicContext"] for item in self.graph["nodes"]))

    def test_23_source_sections_are_traceable(self) -> None:
        source_ids = {item["id"] for item in self.song_map["sections"]}
        self.assertTrue(all(item["sourceSectionId"] in source_ids for item in self.graph["nodes"]))

    def test_24_source_polyphony_is_full_duration_measurement(self) -> None:
        self.assertEqual(self.song_map["polyphony"]["method"], "full-note-duration-sweep")
        self.assertEqual(self.graph["globalBudgets"]["sourceConcurrentMidiNotes"],
                         self.song_map["polyphony"]["globalPeak"])

    def test_25_node_polyphony_never_exceeds_ceiling(self) -> None:
        self.assertTrue(all(item["polyphonyBudget"]["maximumConcurrentMidiNotes"]
                            <= MAX_CONCURRENT_MIDI_NOTES for item in self.graph["nodes"]))

    def test_26_global_planned_peak_never_exceeds_ceiling(self) -> None:
        budget = self.graph["globalBudgets"]
        self.assertLessEqual(budget["plannedConcurrentMidiNotes"], budget["maximumConcurrentMidiNotes"])

    def test_27_device_voice_cost_is_not_claimed_as_confirmed(self) -> None:
        self.assertEqual(self.graph["globalBudgets"]["deviceVoiceCost"],
                         {"status": "UNCONFIRMED", "ceiling": None, "estimateOnly": True})

    def test_28_all_role_register_bands_are_declared(self) -> None:
        self.assertEqual({item["role"] for item in self.graph["globalBudgets"]["roleRegisterBands"]},
                         set(ROLES))

    def test_29_node_register_plan_matches_active_roles(self) -> None:
        self.assertTrue(all({item["role"] for item in node["registerPlan"]} == set(node["roles"])
                            for node in self.graph["nodes"]))

    def test_30_edges_reference_existing_nodes(self) -> None:
        markers = {item["marker"] for item in self.graph["nodes"]}
        self.assertTrue(all({edge["from"], edge["to"]} <= markers for edge in self.graph["edges"]))

    def test_31_fill_nodes_declare_transition_targets(self) -> None:
        targets = {item["marker"]: item["transitionTarget"] for item in self.graph["nodes"]
                   if item["elementType"] == "fill"}
        self.assertEqual(targets, {"f1cv1": "v2cv1", "f2cv1": "v4cv1"})

    def test_32_each_fill_has_an_outgoing_target_edge(self) -> None:
        self.assertTrue(any(edge["from"] == "f1cv1" and edge["to"] == "v2cv1"
                            for edge in self.graph["edges"]))
        self.assertTrue(any(edge["from"] == "f2cv1" and edge["to"] == "v4cv1"
                            for edge in self.graph["edges"]))

    def test_33_ending_edges_require_cadence(self) -> None:
        endings = [edge for edge in self.graph["edges"] if edge["transitionType"] == "ending"]
        self.assertTrue(endings)
        self.assertTrue(all("ending-cadence" in edge["obligations"] for edge in endings))

    def test_34_balanced_transitions_have_four_obligations(self) -> None:
        edge = self.graph["edges"][0]
        self.assertEqual(set(edge["obligations"]),
                         {"motif-continuity", "bass-approach", "harmonic-anticipation", "crash-accent"})

    def test_35_subtle_transitions_are_restrained(self) -> None:
        graph = build_arrangement_graph(self.song_map,
                                        build_producer_brief("Make pop with subtle transitions."))
        direct = next(edge for edge in graph["edges"] if edge["transitionType"] == "direct")
        self.assertEqual(set(direct["obligations"]), {"motif-continuity", "bass-approach"})

    def test_36_dramatic_transitions_include_pickup(self) -> None:
        graph = build_arrangement_graph(self.song_map,
                                        build_producer_brief("Make pop with dramatic transitions."))
        self.assertTrue(all("pickup" in edge["obligations"] for edge in graph["edges"]
                            if edge["transitionType"] != "ending"))

    def test_37_edge_harmony_is_explainable(self) -> None:
        self.assertTrue(all(set(edge["harmonicContinuity"]) ==
                            {"fromChord", "toChord", "requiresAdaptation", "confidence"}
                            for edge in self.graph["edges"]))

    def test_38_register_jump_is_bounded(self) -> None:
        self.assertTrue(all(0 <= edge["maximumRegisterJumpSemitones"] <= 12
                            for edge in self.graph["edges"]))

    def test_39_default_has_two_global_plan_variants(self) -> None:
        self.assertEqual(len(self.graph["planVariants"]), 2)

    def test_40_variant_count_can_be_four(self) -> None:
        graph = build_arrangement_graph(self.song_map, self.brief, 1, 4)
        self.assertEqual(len(graph["planVariants"]), 4)
        self.assertEqual({item["strategy"] for item in graph["planVariants"]}, set(STRATEGIES))

    def test_41_primary_variant_matches_graph_nodes(self) -> None:
        primary = {item["marker"]: item for item in self.graph["planVariants"][0]["elementTargets"]}
        self.assertTrue(all(primary[node["marker"]]["targetEnergy"] == node["targetEnergy"]
                            for node in self.graph["nodes"]))

    def test_42_every_variant_has_a_deterministic_hash(self) -> None:
        repeated = build_arrangement_graph(self.song_map, self.brief, 2100, 2)
        self.assertEqual([item["variantHash"] for item in self.graph["planVariants"]],
                         [item["variantHash"] for item in repeated["planVariants"]])

    def test_43_variants_never_change_locked_targets(self) -> None:
        graph = build_arrangement_graph(self.song_map,
                                        build_producer_brief("Make pop and lock v2cv1."), 7, 4)
        node = next(item for item in graph["nodes"] if item["marker"] == "v2cv1")
        for variant in graph["planVariants"]:
            target = next(item for item in variant["elementTargets"] if item["marker"] == "v2cv1")
            self.assertEqual((target["targetEnergy"], target["targetDensity"]),
                             (node["targetEnergy"], node["targetDensity"]))

    def test_44_different_seed_changes_alternative_order_or_hash(self) -> None:
        other = build_arrangement_graph(self.song_map, self.brief, 2101, 2)
        self.assertNotEqual(self.graph["graphHash"], other["graphHash"])

    def test_45_every_variant_preserves_variation_rise(self) -> None:
        for variant in build_arrangement_graph(self.song_map, self.brief, 5, 4)["planVariants"]:
            targets = {item["marker"]: item for item in variant["elementTargets"]}
            energies = [targets[f"v{i}cv1"]["targetEnergy"] for i in range(1, 5)]
            self.assertEqual(energies, sorted(energies))

    def test_46_unapproved_brief_is_blocked(self) -> None:
        with self.assertRaisesRegex(ValueError, "approved"):
            build_arrangement_graph(self.song_map,
                                    build_producer_brief("Use drums but make it without drums."))

    def test_47_invalid_seed_is_rejected(self) -> None:
        for seed in (-1, 2**31, True):
            with self.assertRaisesRegex(ValueError, "seed"):
                build_arrangement_graph(self.song_map, self.brief, seed)

    def test_48_invalid_variant_count_is_rejected(self) -> None:
        for count in (1, 5, True):
            with self.assertRaisesRegex(ValueError, "variant count"):
                build_arrangement_graph(self.song_map, self.brief, variant_count=count)

    def test_49_low_song_map_confidence_blocks_candidate_search(self) -> None:
        changed = deepcopy(self.song_map)
        changed["confidence"] = 0.5
        graph = build_arrangement_graph(_rehash_song_map(changed), self.brief)
        self.assertFalse(graph["readyForCandidateSearch"])
        self.assertTrue(any(item["code"] == "LOW_SONG_MAP_CONFIDENCE"
                            for item in graph["manualReview"]))

    def test_50_source_manual_review_is_propagated(self) -> None:
        graph = build_arrangement_graph(_fallback_song_map(), self.brief)
        self.assertTrue(any(item["code"] == "SONG_MAP_MANUAL_REVIEW"
                            for item in graph["manualReview"]))

    def test_51_missing_chorus_requires_manual_review(self) -> None:
        changed = deepcopy(self.song_map)
        for section in changed["sections"]:
            if section["label"] == "chorus":
                section["label"] = "bridge"
        graph = build_arrangement_graph(_rehash_song_map(changed), self.brief)
        self.assertTrue(any(item["id"] == "missing-chorus" for item in graph["manualReview"]))

    def test_52_source_polyphony_overflow_requires_manual_review(self) -> None:
        changed = deepcopy(self.song_map)
        changed["polyphony"]["globalPeak"] = 55
        graph = build_arrangement_graph(_rehash_song_map(changed), self.brief)
        self.assertTrue(any(item["code"] == "SOURCE_POLYPHONY_OVERFLOW"
                            for item in graph["manualReview"]))

    def test_53_validator_rejects_unknown_root_field(self) -> None:
        changed = deepcopy(self.graph)
        changed["unknown"] = True
        with self.assertRaisesRegex(ValueError, "root fields mismatch"):
            validate_arrangement_graph_v2(changed)

    def test_54_validator_rejects_tampered_graph_hash(self) -> None:
        changed = deepcopy(self.graph)
        changed["graphHash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "graphHash mismatch"):
            validate_arrangement_graph_v2(changed)

    def test_55_validator_rejects_weakened_safety(self) -> None:
        changed = deepcopy(self.graph)
        changed["safety"]["midiMutationAllowed"] = True
        with self.assertRaisesRegex(ValueError, "safety contract"):
            validate_arrangement_graph_v2(changed)

    def test_56_api_and_gui_transports_are_identical(self) -> None:
        payload = {"songMap": self.song_map, "producerBrief": self.brief,
                   "seed": 44, "variantCount": 3}
        self.assertEqual(execute_arrangement_graph_api(payload),
                         execute_arrangement_graph_gui(payload))

    def test_57_api_rejects_unknown_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires only"):
            execute_arrangement_graph_api({"songMap": self.song_map,
                                           "producerBrief": self.brief, "writeMidi": True})

    def test_58_cli_writes_same_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            song_path, brief_path, output = root / "song.json", root / "brief.json", root / "graph.json"
            song_path.write_text(json.dumps(self.song_map), encoding="utf-8")
            brief_path.write_text(json.dumps(self.brief), encoding="utf-8")
            completed = subprocess.run([
                "python", str(ROOT / "session21_arrangement_graph.py"),
                "--song-map", str(song_path), "--producer-brief", str(brief_path),
                "--seed", "2100", "--variants", "2", "--output", str(output),
            ], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(output.read_text()), self.graph)

    def test_59_server_exposes_song_map_and_graph_endpoints(self) -> None:
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn('"/api/premium-song-map"', source)
        self.assertIn('"/api/premium-arrangement-graph"', source)

    def test_60_gui_exposes_arrangement_graph_plan(self) -> None:
        source = (ROOT / "web_gui.py").read_text(encoding="utf-8")
        self.assertIn("ARRANGEMENT GRAPH 2.0", source)
        self.assertIn("/api/premium-arrangement-graph", source)

    def test_61_v1_contract_remains_unchanged_and_available(self) -> None:
        value = json.loads((ROOT / "premium/schemas/v1/arrangement-graph-v1.schema.json").read_text())
        self.assertEqual(value["x-contract-version"], "1.0")

    def test_62_planner_has_no_midi_or_candidate_authority(self) -> None:
        self.assertEqual(self.graph["safety"]["midiMutationAllowed"], False)
        self.assertEqual(self.graph["safety"]["candidatePatternSelectionAllowed"], False)
        self.assertFalse(self.graph["hardConstraints"]["originalSoloMutationAllowed"])
        self.assertFalse(self.graph["hardConstraints"]["goldDynamicsAllowed"])


if __name__ == "__main__":
    unittest.main()