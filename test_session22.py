from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from dna_midi_studio import (
    CANDIDATE_SET_SCHEMA,
    CANDIDATE_SET_VERSION,
    CRITERIA,
    CandidateSearchControls,
    build_arrangement_graph,
    build_candidate_set,
    build_producer_brief,
    execute_candidate_search_api,
    execute_candidate_search_gui,
    validate_candidate_set_v2,
)
from dna_midi_studio.arrangement_graph import _hash_without as graph_hash_without
from dna_midi_studio.candidate_search import _hash_without as candidate_hash_without
from dna_midi_studio.session19_fixture import build_benchmark_case
from dna_midi_studio.song_understanding import analyze_song_map


ROOT = Path(__file__).resolve().parents[1]


class Session22CandidateSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        case = build_benchmark_case(4)
        cls.song_map = analyze_song_map(case.midi, "session22-reference.mid")
        cls.brief = build_producer_brief(
            "Napravi življi pop-folk Style sa gitarom i podlogom, "
            "sa suzdržanom strofom i punim refrenom."
        )
        cls.graph = build_arrangement_graph(cls.song_map, cls.brief, 2100, 2)
        cls.result = build_candidate_set(cls.graph, cls.song_map, ROOT, seed=2200, variant_count=3)
        cls.repeat = build_candidate_set(cls.graph, cls.song_map, ROOT, seed=2200, variant_count=3)
        cls.ready_request = next(
            item for item in cls.result["requests"] if len(item["rankedCandidates"]) >= 3
        )
        cls.request_id = cls.ready_request["requestId"]
        cls.marker = cls.ready_request["marker"]
        cls.role = cls.ready_request["role"]
        cls.top_pattern = cls.ready_request["rankedCandidates"][0]["patternId"]
        cls.second_pattern = cls.ready_request["rankedCandidates"][1]["patternId"]
        cls.locked = build_candidate_set(
            cls.graph, cls.song_map, ROOT, seed=2200, variant_count=3,
            controls={"version": "1.0", "lockedSelections": [{
                "marker": cls.marker, "role": cls.role, "patternId": cls.second_pattern,
            }]},
        )
        cls.excluded = build_candidate_set(
            cls.graph, cls.song_map, ROOT, seed=2200, variant_count=3,
            controls={"version": "1.0", "excludedPatternIds": [cls.top_pattern]},
        )
        cls.nexted = build_candidate_set(
            cls.graph, cls.song_map, ROOT, seed=2200, variant_count=3,
            controls={"version": "1.0", "nextCandidateOffsets": [{
                "requestId": cls.request_id, "offset": 1,
            }]},
        )
        cls.partial = build_candidate_set(
            cls.graph, cls.song_map, ROOT, seed=2200, variant_count=3,
            controls={"version": "1.0", "regenerateMarkers": ["v3cv1"],
                      "nextCandidateOffsets": [{"requestId": "v3cv1:drums", "offset": 1}]},
            previous_candidate_set=cls.result,
        )

    def test_01_schema_constants_are_explicit(self) -> None:
        self.assertEqual((CANDIDATE_SET_SCHEMA, CANDIDATE_SET_VERSION),
                         ("dna-premium-candidate-set", "2.0"))

    def test_02_json_schema_is_strict_draft_2020_12(self) -> None:
        schema = json.loads((ROOT / "premium/schemas/v2/candidate-set-v2.schema.json").read_text())
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["x-contract-version"], "2.0")

    def test_03_schema_requires_every_root_field(self) -> None:
        schema = json.loads((ROOT / "premium/schemas/v2/candidate-set-v2.schema.json").read_text())
        self.assertEqual(set(schema["required"]), set(self.result))

    def test_04_generated_candidate_set_validates(self) -> None:
        self.assertIsNone(validate_candidate_set_v2(self.result))

    def test_05_same_input_seed_and_variant_are_deterministic(self) -> None:
        self.assertEqual(self.result, self.repeat)

    def test_06_source_documents_are_not_mutated(self) -> None:
        graph, song = deepcopy(self.graph), deepcopy(self.song_map)
        build_candidate_set(self.graph, self.song_map, ROOT, seed=9, variant_count=2)
        self.assertEqual((self.graph, self.song_map), (graph, song))

    def test_07_source_hashes_are_bound(self) -> None:
        source = self.result["source"]
        self.assertEqual(source["graphHash"], self.graph["graphHash"])
        self.assertEqual(source["songMapHash"], self.song_map["mapHash"])
        self.assertEqual(source["sourceMidiSha256"], self.song_map["sourceSha256"])

    def test_08_registry_hashes_match_production_files(self) -> None:
        for item in self.result["registries"].values():
            self.assertEqual(item["sha256"], sha256((ROOT / item["path"]).read_bytes()).hexdigest())

    def test_09_production_pattern_count_is_real(self) -> None:
        self.assertEqual(self.result["audit"]["productionPatternCount"], 12918 + 2919)

    def test_10_only_authoritative_pattern_registries_are_bound(self) -> None:
        self.assertEqual(set(self.result["registries"]), {"goldPerformance", "factoryStrumming"})

    def test_11_request_ids_are_unique(self) -> None:
        ids = [item["requestId"] for item in self.result["requests"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_12_every_graph_node_role_has_a_request(self) -> None:
        expected = {f"{node['marker']}:{role}" for node in self.graph["nodes"] for role in node["roles"]}
        self.assertEqual({item["requestId"] for item in self.result["requests"]}, expected)

    def test_13_request_mapping_is_unambiguous(self) -> None:
        self.assertTrue(all(item["requestId"] == f"{item['marker']}:{item['role']}"
                            for item in self.result["requests"]))

    def test_14_two_stage_retrieval_is_audited(self) -> None:
        audit = self.result["audit"]
        self.assertGreater(audit["fastRetrievedCount"], audit["rankedReturnedCount"])
        self.assertGreater(audit["hardPassedCount"], 0)

    def test_15_hard_constraints_run_before_scoring(self) -> None:
        self.assertIs(self.result["safety"]["hardConstraintsBeforeScoring"], True)

    def test_16_every_ranked_candidate_has_fourteen_criteria(self) -> None:
        for request in self.result["requests"]:
            for candidate in request["rankedCandidates"]:
                self.assertEqual(set(candidate["criteria"]), set(CRITERIA))

    def test_17_scores_are_bounded_and_ranked(self) -> None:
        for request in self.result["requests"]:
            scores = [item["score"] for item in request["rankedCandidates"]]
            self.assertTrue(all(0 <= score <= 1 for score in scores))
            self.assertEqual(scores, sorted(scores, reverse=True))

    def test_18_candidate_hashes_are_verified(self) -> None:
        self.assertTrue(all(
            candidate["candidateHash"] == candidate_hash_without(candidate, "candidateHash")
            for request in self.result["requests"] for candidate in request["rankedCandidates"]
        ))

    def test_19_request_hashes_are_verified(self) -> None:
        self.assertTrue(all(item["requestHash"] == candidate_hash_without(item, "requestHash")
                            for item in self.result["requests"]))

    def test_20_every_pattern_uses_stable_numeric_id(self) -> None:
        self.assertTrue(all(
            len(candidate["patternId"].split(".")) == 3
            for request in self.result["requests"] for candidate in request["rankedCandidates"]
        ))

    def test_21_non_guitar_candidates_are_gold_performance(self) -> None:
        for request in self.result["requests"]:
            if request["role"] != "guitar":
                self.assertTrue(all(item["sourceKind"] == "GOLD_PERFORMANCE"
                                    for item in request["rankedCandidates"]))

    def test_22_guitar_candidates_are_factory_strumming_only(self) -> None:
        guitar = [item for item in self.result["requests"] if item["role"] == "guitar"]
        self.assertTrue(guitar)
        self.assertTrue(all(candidate["sourceKind"] == "FACTORY_STRUMMING"
                            for request in guitar for candidate in request["rankedCandidates"]))

    def test_23_gold_has_no_dynamic_bank_program_or_guitar_authority(self) -> None:
        safety = self.result["safety"]
        self.assertEqual((safety["goldDynamicsAuthority"], safety["goldBankProgramAuthority"],
                          safety["goldGuitarAuthority"]), (False, False, False))

    def test_24_candidate_output_contains_no_velocity_or_program_fields(self) -> None:
        text = json.dumps(self.result["requests"], sort_keys=True).lower()
        self.assertNotIn('"velocity"', text)
        self.assertNotIn('"program"', text)
        self.assertNotIn('"bankmsb"', text)

    def test_25_only_hard_pass_candidates_are_ranked(self) -> None:
        self.assertTrue(all(candidate["hardConstraintsPassed"] and not candidate["rejectionReasons"]
                            for request in self.result["requests"]
                            for candidate in request["rankedCandidates"]))

    def test_26_all_detailed_rejections_are_audited(self) -> None:
        self.assertIs(self.result["audit"]["allDetailedRejectionsAudited"], True)
        self.assertEqual(self.result["audit"]["hardRejectedCount"], sum(
            len(item["rejectedCandidates"]) for item in self.result["requests"]
        ))

    def test_27_rejection_reason_counts_match_entries(self) -> None:
        total = sum(self.result["audit"]["rejectionReasonCounts"].values())
        actual = sum(len(item["reasons"]) for request in self.result["requests"]
                     for item in request["rejectedCandidates"])
        self.assertEqual(total, actual)

    def test_28_excluded_pattern_is_never_selected(self) -> None:
        self.assertFalse(any(selection["patternId"] == self.top_pattern
                             for variant in self.excluded["variants"]
                             for selection in variant["selections"]))

    def test_29_excluded_pattern_has_explicit_rejection_reason(self) -> None:
        request = next(item for item in self.excluded["requests"] if item["requestId"] == self.request_id)
        rejection = next(item for item in request["rejectedCandidates"] if item["patternId"] == self.top_pattern)
        self.assertIn("EXCLUDED_BY_USER", rejection["reasons"])

    def test_30_user_lock_is_preserved_in_every_variant(self) -> None:
        selected = [selection for variant in self.locked["variants"] for selection in variant["selections"]
                    if selection["requestId"] == self.request_id]
        self.assertTrue(all(item["patternId"] == self.second_pattern and item["locked"] for item in selected))

    def test_31_lock_and_exclude_conflict_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CandidateSearchControls.from_mapping({
                "version": "1.0",
                "lockedSelections": [{"marker": self.marker, "role": self.role,
                                      "patternId": self.second_pattern}],
                "excludedPatternIds": [self.second_pattern],
            })

    def test_32_next_candidate_offset_changes_the_selection(self) -> None:
        base = next(item for item in self.result["variants"][0]["selections"]
                    if item["requestId"] == self.request_id)
        changed = next(item for item in self.nexted["variants"][0]["selections"]
                       if item["requestId"] == self.request_id)
        self.assertNotEqual(base["patternId"], changed["patternId"])

    def test_33_next_candidate_mode_is_audited(self) -> None:
        changed = next(item for item in self.nexted["variants"][0]["selections"]
                       if item["requestId"] == self.request_id)
        self.assertEqual(changed["selectionMode"], "NEXT_CANDIDATE")

    def test_34_default_output_has_stable_abc_variants(self) -> None:
        self.assertEqual([item["variantId"] for item in self.result["variants"]],
                         ["variant-A", "variant-B", "variant-C"])

    def test_35_two_variants_are_supported(self) -> None:
        value = build_candidate_set(self.graph, self.song_map, ROOT, seed=2, variant_count=2)
        self.assertEqual(len(value["variants"]), 2)

    def test_36_four_variants_are_supported(self) -> None:
        value = build_candidate_set(self.graph, self.song_map, ROOT, seed=4, variant_count=4)
        self.assertEqual(len(value["variants"]), 4)

    def test_37_variant_hashes_are_verified(self) -> None:
        self.assertTrue(all(item["variantHash"] == candidate_hash_without(item, "variantHash")
                            for item in self.result["variants"]))

    def test_38_b_and_c_are_materially_diverse_from_a(self) -> None:
        self.assertTrue(all(item["diversityDistanceFromA"] >= 0.5
                            for item in self.result["variants"][1:]))

    def test_39_repeated_seed_preserves_every_variant_hash(self) -> None:
        self.assertEqual([item["variantHash"] for item in self.result["variants"]],
                         [item["variantHash"] for item in self.repeat["variants"]])

    def test_40_different_seed_changes_candidate_set(self) -> None:
        other = build_candidate_set(self.graph, self.song_map, ROOT, seed=2201, variant_count=3)
        self.assertNotEqual(self.result["candidateSetHash"], other["candidateSetHash"])

    def test_41_v1_through_v4_apply_role_diversity_penalty(self) -> None:
        selections = self.result["variants"][0]["selections"]
        for role in ("drums", "bass", "guitar", "accompaniment"):
            ids = [item["patternId"] for item in selections
                   if item["marker"] in {"v1cv1", "v2cv1", "v3cv1", "v4cv1"}
                   and item["role"] == role]
            self.assertEqual(len(ids), len(set(ids)))

    def test_42_drum_bass_relationship_is_used_when_available(self) -> None:
        relationships = [item for variant in self.result["variants"] for item in variant["selections"]
                         if item["role"] == "bass" and item["relationshipId"]]
        self.assertTrue(relationships)

    def test_43_relationship_ids_exist_in_production_registry(self) -> None:
        document = json.loads((ROOT / "data/gold-performance-patterns.json").read_text())
        known = {item["id"] for item in document["relationships"]}
        self.assertTrue(all(item["relationshipId"] in known
                            for variant in self.result["variants"] for item in variant["selections"]
                            if item["relationshipId"] is not None))

    def test_44_graph_locked_node_requests_keep(self) -> None:
        graph = build_arrangement_graph(
            self.song_map, build_producer_brief("Make balanced pop with guitar. Lock v2cv1."), 3, 2
        )
        value = build_candidate_set(graph, self.song_map, ROOT, seed=3, variant_count=2)
        self.assertTrue(all(item["status"] == "KEEP" for item in value["requests"]
                            if item["marker"] == "v2cv1"))

    def test_45_graph_locked_node_has_no_selected_pattern(self) -> None:
        graph = build_arrangement_graph(
            self.song_map, build_producer_brief("Make balanced pop with guitar. Lock v2cv1."), 3, 2
        )
        value = build_candidate_set(graph, self.song_map, ROOT, seed=3, variant_count=2)
        self.assertTrue(all(item["patternId"] is None and item["locked"]
                            for variant in value["variants"] for item in variant["selections"]
                            if item["marker"] == "v2cv1"))

    def test_46_solo_request_keeps_the_original(self) -> None:
        graph = build_arrangement_graph(
            self.song_map, build_producer_brief("Make balanced pop with solo."), 5, 2
        )
        value = build_candidate_set(graph, self.song_map, ROOT, seed=5, variant_count=2)
        self.assertTrue(all(item["status"] == "KEEP_ORIGINAL_SOLO"
                            for item in value["requests"] if item["role"] == "solo"))

    def test_47_solo_has_no_candidate_pattern(self) -> None:
        graph = build_arrangement_graph(
            self.song_map, build_producer_brief("Make balanced pop with solo."), 5, 2
        )
        value = build_candidate_set(graph, self.song_map, ROOT, seed=5, variant_count=2)
        self.assertTrue(all(item["patternId"] is None
                            for variant in value["variants"] for item in variant["selections"]
                            if item["role"] == "solo"))

    def test_48_unresolved_arrangement_graph_is_blocked(self) -> None:
        graph = deepcopy(self.graph)
        graph["manualReview"].append({"id": "manual-x", "code": "TEST",
                                      "reason": "test", "source": "test", "marker": None,
                                      "details": {"sourceId": "x", "reasons": ["test"]}})
        graph["readyForCandidateSearch"] = False
        graph["graphHash"] = graph_hash_without(graph, "graphHash")
        with self.assertRaises(ValueError):
            build_candidate_set(graph, self.song_map, ROOT)

    def test_49_graph_song_map_hash_mismatch_is_blocked(self) -> None:
        song = deepcopy(self.song_map)
        song["mapHash"] = "0" * 64
        with self.assertRaises(ValueError):
            build_candidate_set(self.graph, song, ROOT)

    def test_50_unknown_plan_variant_is_blocked(self) -> None:
        with self.assertRaises(ValueError):
            build_candidate_set(self.graph, self.song_map, ROOT, plan_variant_id="plan-04")

    def test_51_invalid_seed_is_blocked(self) -> None:
        with self.assertRaises(ValueError):
            build_candidate_set(self.graph, self.song_map, ROOT, seed=-1)

    def test_52_invalid_variant_count_is_blocked(self) -> None:
        with self.assertRaises(ValueError):
            build_candidate_set(self.graph, self.song_map, ROOT, variant_count=5)

    def test_53_unknown_control_field_is_blocked(self) -> None:
        with self.assertRaises(ValueError):
            build_candidate_set(self.graph, self.song_map, ROOT, controls={"version": "1.0", "midi": "x"})

    def test_54_invalid_excluded_id_is_blocked(self) -> None:
        with self.assertRaises(ValueError):
            CandidateSearchControls.from_mapping({"version": "1.0", "excludedPatternIds": ["bad"]})

    def test_55_duplicate_request_lock_is_blocked(self) -> None:
        item = {"marker": self.marker, "role": self.role, "patternId": self.second_pattern}
        with self.assertRaises(ValueError):
            CandidateSearchControls.from_mapping({"version": "1.0", "lockedSelections": [item, item]})

    def test_56_previous_set_requires_explicit_regeneration_scope(self) -> None:
        with self.assertRaises(ValueError):
            build_candidate_set(self.graph, self.song_map, ROOT,
                                previous_candidate_set=self.result)

    def test_57_partial_regeneration_is_explicit(self) -> None:
        partial = self.partial["partialRegeneration"]
        self.assertTrue(partial["enabled"])
        self.assertEqual(partial["regeneratedMarkers"], ["v3cv1"])

    def test_58_partial_regeneration_preserves_every_other_fragment(self) -> None:
        for before, after in zip(self.result["variants"], self.partial["variants"]):
            old = {item["requestId"]: item["fragmentHash"] for item in before["selections"]}
            new = {item["requestId"]: item["fragmentHash"] for item in after["selections"]}
            self.assertTrue(all(old[key] == new[key] for key in old if not key.startswith("v3cv1:")))

    def test_59_partial_regeneration_changes_requested_candidate(self) -> None:
        before = next(item for item in self.result["variants"][0]["selections"]
                      if item["requestId"] == "v3cv1:drums")
        after = next(item for item in self.partial["variants"][0]["selections"]
                     if item["requestId"] == "v3cv1:drums")
        self.assertNotEqual(before["fragmentHash"], after["fragmentHash"])

    def test_60_partial_regeneration_records_previous_hash(self) -> None:
        self.assertEqual(self.partial["partialRegeneration"]["previousCandidateSetHash"],
                         self.result["candidateSetHash"])

    def test_61_api_and_gui_transports_are_identical(self) -> None:
        payload = {"arrangementGraph": self.graph, "songMap": self.song_map,
                   "planVariantId": "plan-01", "seed": 33, "variantCount": 2}
        self.assertEqual(execute_candidate_search_api(payload, ROOT),
                         execute_candidate_search_gui(payload, ROOT))

    def test_62_api_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValueError):
            execute_candidate_search_api({"arrangementGraph": self.graph,
                                          "songMap": self.song_map, "writeMidi": True}, ROOT)

    def test_63_cli_writes_the_same_candidate_set(self) -> None:
        expected = build_candidate_set(self.graph, self.song_map, ROOT, seed=44, variant_count=2)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            graph_path, song_path, output = base / "graph.json", base / "song.json", base / "out.json"
            graph_path.write_text(json.dumps(self.graph), encoding="utf-8")
            song_path.write_text(json.dumps(self.song_map), encoding="utf-8")
            completed = subprocess.run([
                "python", str(ROOT / "session22_candidate_search.py"),
                "--graph", str(graph_path), "--song-map", str(song_path),
                "--seed", "44", "--variants", "2", "--output", str(output),
            ], cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(output.read_text())["candidateSetHash"],
                             expected["candidateSetHash"])

    def test_64_candidate_search_never_generates_final_midi(self) -> None:
        safety = self.result["safety"]
        self.assertEqual((safety["readOnly"], safety["midiMutationAllowed"],
                          safety["finalMidiGenerated"]), (True, False, False))

    def test_65_server_exposes_candidate_search_endpoint(self) -> None:
        source = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn('/api/premium-candidate-search', source)
        self.assertIn('execute_candidate_search_api', source)

    def test_66_gui_exposes_abc_candidate_workflow(self) -> None:
        source = (ROOT / "web_gui.py").read_text(encoding="utf-8")
        self.assertIn('PREMIUM CANDIDATE SEARCH 2.0', source)
        self.assertIn('Izradi A/B/C varijante', source)
        self.assertIn('finalni MIDI nije generiran', source)

    def test_67_candidate_set_v1_contract_remains_available(self) -> None:
        schema = json.loads((ROOT / "premium/schemas/v1/candidate-set-v1.schema.json").read_text())
        self.assertEqual(schema["x-contract-version"], "1.0")
        self.assertEqual(schema["properties"]["version"]["const"], "1.0")

    def test_68_max_candidates_per_request_is_enforced(self) -> None:
        value = build_candidate_set(
            self.graph, self.song_map, ROOT, seed=68, variant_count=2,
            controls={"version": "1.0", "maxCandidatesPerRequest": 4},
        )
        self.assertTrue(all(len(item["rankedCandidates"]) <= 4 for item in value["requests"]))


if __name__ == "__main__":
    unittest.main()