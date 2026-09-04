from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import time
import unittest

from dna_midi_studio import (
    CORRECTION_SCHEMA,
    CORRECTION_VERSION,
    MidiEvent,
    MidiFile,
    MidiTrack,
    SONG_MAP_SCHEMA,
    SONG_MAP_VERSION,
    analyze_song_map,
    apply_song_map_corrections,
    boundary_f1,
    validate_song_map_v2,
    weighted_f1,
)
from dna_midi_studio.session19_fixture import (
    PPQ,
    build_benchmark_case,
    build_labeled_benchmark,
    build_variable_meter_case,
    with_velocity,
)


ROOT = Path(__file__).resolve().parents[1]


def _semantic(song_map: dict) -> dict:
    value = {key: deepcopy(item) for key, item in song_map.items()
             if key not in {"source", "sourceSha256", "mapHash"}}
    for role in value["roleSegments"]:
        role.pop("trackUid", None)
    return value


def _empty_note_case() -> bytes:
    return MidiFile(0, PPQ, [MidiTrack([
        MidiEvent(0, 0, "meta", data=b"", meta_type=0x2F)
    ])]).to_bytes()


def _fallback_meta_case() -> bytes:
    return MidiFile(0, PPQ, [MidiTrack([
        MidiEvent(0, 0, "channel", status=0x90, data=bytes((60, 64))),
        MidiEvent(PPQ, 1, "channel", status=0x80, data=bytes((60, 0))),
    ])]).to_bytes()


def _large_case(note_count: int = 25_000) -> bytes:
    events = [MidiEvent(0, 0, "channel", status=0xC0, data=bytes((0,)))]
    order = 1
    for index in range(note_count):
        pitch = 48 + index % 24
        start = index % 120
        events.append(MidiEvent(start, order, "channel", status=0x90,
                                data=bytes((pitch, 1 + index % 127))))
        events.append(MidiEvent(PPQ, order + 1, "channel", status=0x80,
                                data=bytes((pitch, 0))))
        order += 2
    return MidiFile(0, PPQ, [MidiTrack(events)]).to_bytes()


class Session19SongUnderstandingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.case = build_benchmark_case(3)
        cls.song_map = analyze_song_map(cls.case.midi, cls.case.case_id + ".mid")

    def test_01_schema_and_version_are_explicit(self) -> None:
        self.assertEqual((self.song_map["schema"], self.song_map["version"]),
                         (SONG_MAP_SCHEMA, SONG_MAP_VERSION))

    def test_02_contract_schemas_are_strict_root_objects(self) -> None:
        for name, version in (("song-map-v2.schema.json", "2.0"),
                              ("song-map-corrections-v1.schema.json", "1.0")):
            value = json.loads((ROOT / "premium" / "schemas" / "v2" / name).read_text())
            self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertIs(value["additionalProperties"], False)
            self.assertEqual(value["x-contract-version"], version)

    def test_03_validator_accepts_generated_map(self) -> None:
        self.assertIsNone(validate_song_map_v2(self.song_map))

    def test_04_validator_rejects_unknown_root_field(self) -> None:
        changed = deepcopy(self.song_map)
        changed["unknown"] = True
        with self.assertRaisesRegex(ValueError, "root fields mismatch"):
            validate_song_map_v2(changed)

    def test_05_validator_rejects_tampered_hash(self) -> None:
        changed = deepcopy(self.song_map)
        changed["confidence"] = 0.0
        with self.assertRaisesRegex(ValueError, "mapHash mismatch"):
            validate_song_map_v2(changed)

    def test_06_same_input_is_byte_level_deterministic(self) -> None:
        self.assertEqual(self.song_map, analyze_song_map(self.case.midi, self.case.case_id + ".mid"))

    def test_07_analysis_does_not_mutate_source_bytes(self) -> None:
        before = sha256(self.case.midi).hexdigest()
        analyze_song_map(self.case.midi)
        self.assertEqual(before, sha256(self.case.midi).hexdigest())

    def test_08_velocity_is_not_used_for_music_analysis(self) -> None:
        quiet = analyze_song_map(with_velocity(self.case.midi, 8), "quiet.mid")
        loud = analyze_song_map(with_velocity(self.case.midi, 120), "loud.mid")
        self.assertEqual(_semantic(quiet), _semantic(loud))
        self.assertFalse(quiet["analysisVelocityUsed"])

    def test_09_source_metadata_is_auditable(self) -> None:
        self.assertEqual(self.song_map["sourceSha256"], self.case.sha256)
        self.assertEqual(self.song_map["source"]["trackCount"], 5)
        self.assertEqual(self.song_map["source"]["format"], 1)

    def test_10_tempo_map_reads_real_meta_events(self) -> None:
        variable = analyze_song_map(build_variable_meter_case())
        self.assertEqual([item["bpm"] for item in variable["tempoMap"]], [120.0, 150.0])

    def test_11_meter_map_reads_real_meta_events(self) -> None:
        variable = analyze_song_map(build_variable_meter_case())
        self.assertEqual([(item["numerator"], item["denominator"]) for item in variable["meterMap"]],
                         [(4, 4), (3, 4)])

    def test_12_missing_tempo_and_meter_receive_safe_defaults(self) -> None:
        result = analyze_song_map(_fallback_meta_case())
        self.assertEqual(result["tempoMap"][0]["bpm"], 120.0)
        self.assertEqual((result["meterMap"][0]["numerator"], result["meterMap"][0]["denominator"]),
                         (4, 4))

    def test_13_beat_grid_marks_downbeats(self) -> None:
        downbeats = [item for item in self.song_map["beatGrid"] if item["downbeat"]]
        self.assertEqual(len(downbeats), len(self.song_map["bars"]))
        self.assertTrue(all(item["beat"] == 1 for item in downbeats))

    def test_14_chords_are_analyzed_at_half_bar_resolution(self) -> None:
        self.assertEqual(len(self.song_map["chordCells"]), 16)
        self.assertEqual({item["part"] for item in self.song_map["chordCells"]}, {1, 2})

    def test_15_extended_chord_qualities_are_recognized(self) -> None:
        qualities = {item["quality"] for item in self.song_map["chordCells"]}
        self.assertTrue({"major", "minor", "suspended-4", "dominant-seventh",
                         "major-seventh"} <= qualities)

    def test_16_slash_chord_bass_is_preserved(self) -> None:
        slash = [item for item in self.song_map["chordCells"] if "/" in item["symbol"]]
        self.assertTrue(slash)
        self.assertTrue(all(item["bass"] != item["root"] for item in slash))

    def test_17_chord_uncertainty_is_explainable(self) -> None:
        for cell in self.song_map["chordCells"]:
            self.assertIn("alternatives", cell)
            self.assertIn("nonChordToneRatio", cell)
            self.assertIn("modalBorrowing", cell)

    def test_18_marker_sections_are_exact(self) -> None:
        self.assertEqual([item["label"] for item in self.song_map["sections"]],
                         list(self.case.section_labels))
        self.assertTrue(all(item["evidence"]["kind"] == "midi-marker"
                            for item in self.song_map["sections"]))

    def test_19_section_boundaries_reach_perfect_fixture_f1(self) -> None:
        predicted = [item["startTick"] for item in self.song_map["sections"]]
        self.assertEqual(boundary_f1(self.case.section_boundaries, predicted), 1.0)

    def test_20_phrase_map_contains_ending_cue(self) -> None:
        self.assertTrue(any("ending-phrase" in item["cues"] for item in self.song_map["phrases"]))

    def test_21_cadence_map_is_section_scoped(self) -> None:
        self.assertEqual({item["sectionId"] for item in self.song_map["cadences"]},
                         {item["id"] for item in self.song_map["sections"]})

    def test_22_roles_are_time_scoped_by_section(self) -> None:
        harmony = [item for item in self.song_map["roleSegments"] if item["trackNumber"] == 2]
        self.assertEqual(len(harmony), len(self.song_map["sections"]))
        self.assertEqual({item["sectionId"] for item in harmony},
                         {item["id"] for item in self.song_map["sections"]})

    def test_23_role_mapping_uses_unambiguous_numbering(self) -> None:
        self.assertTrue(all(item["trackNumber"] == item["trackIndex"] + 1
                            and item["channelNumber"] == item["channelIndex"] + 1
                            for item in self.song_map["roleSegments"]))

    def test_24_role_mapping_carries_stable_track_uid(self) -> None:
        self.assertTrue(all(item["trackUid"].startswith("trk-")
                            for item in self.song_map["roleSegments"]))

    def test_25_polyphony_uses_full_note_duration(self) -> None:
        self.assertGreaterEqual(self.song_map["polyphony"]["globalPeak"], 4)
        self.assertEqual(self.song_map["polyphony"]["method"], "full-note-duration-sweep")

    def test_26_low_confidence_routes_to_manual_review(self) -> None:
        single = analyze_song_map(_fallback_meta_case())
        self.assertTrue(single["manualReview"])
        self.assertTrue(all(item["reasons"] for item in single["manualReview"]))

    def test_27_empty_song_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one paired MIDI note"):
            analyze_song_map(_empty_note_case())

    def test_28_chord_correction_is_a_pure_overlay(self) -> None:
        before = deepcopy(self.song_map)
        corrected = apply_song_map_corrections(self.song_map, {
            "schema": CORRECTION_SCHEMA, "version": CORRECTION_VERSION,
            "sourceSha256": self.song_map["sourceSha256"],
            "chordOverrides": [{"cellIndex": 0, "symbol": "C#m7/G#"}],
        })
        self.assertEqual(self.song_map, before)
        self.assertEqual(corrected["chordCells"][0]["symbol"], "C#m7/G#")
        self.assertEqual(corrected["chordCells"][0]["decision"], "USER_CONFIRMED")

    def test_29_section_correction_is_a_pure_overlay(self) -> None:
        corrected = apply_song_map_corrections(self.song_map, {
            "schema": CORRECTION_SCHEMA, "version": CORRECTION_VERSION,
            "sourceSha256": self.song_map["sourceSha256"],
            "sectionOverrides": [{"id": "sec-002", "label": "bridge"}],
        })
        self.assertEqual(corrected["sections"][1]["label"], "bridge")
        self.assertEqual(corrected["correctionAudit"][-1]["source"], "explicit-user-correction")

    def test_30_correction_source_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "source hash mismatch"):
            apply_song_map_corrections(self.song_map, {
                "schema": CORRECTION_SCHEMA, "version": CORRECTION_VERSION,
                "sourceSha256": "0" * 64,
            })

    def test_31_unknown_correction_field_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown SongMap correction"):
            apply_song_map_corrections(self.song_map, {
                "schema": CORRECTION_SCHEMA, "version": CORRECTION_VERSION,
                "sourceSha256": self.song_map["sourceSha256"], "writeMidi": True,
            })

    def test_32_overlapping_section_correction_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "overlap"):
            apply_song_map_corrections(self.song_map, {
                "schema": CORRECTION_SCHEMA, "version": CORRECTION_VERSION,
                "sourceSha256": self.song_map["sourceSha256"],
                "sectionOverrides": [{"id": "sec-002", "startTick": 0}],
            })

    def test_33_weighted_f1_handles_errors_and_perfect_match(self) -> None:
        self.assertEqual(weighted_f1(["C", "F"], ["C", "F"]), 1.0)
        self.assertLess(weighted_f1(["C", "F"], ["C", "C"]), 1.0)

    def test_34_locked_benchmark_has_twenty_unique_songs(self) -> None:
        corpus = build_labeled_benchmark()
        self.assertEqual(len(corpus), 20)
        self.assertEqual(len({item.sha256 for item in corpus}), 20)

    def test_35_locked_benchmark_exceeds_quality_gates(self) -> None:
        chord_scores, boundary_scores = [], []
        for case in build_labeled_benchmark():
            result = analyze_song_map(case.midi, case.case_id + ".mid")
            predicted = [f"{item['root']}:{item['quality']}:{item['bass']}"
                         for item in result["chordCells"]]
            chord_scores.append(weighted_f1(case.chord_labels, predicted))
            boundary_scores.append(boundary_f1(
                case.section_boundaries, [item["startTick"] for item in result["sections"]]
            ))
        self.assertGreaterEqual(sum(chord_scores) / len(chord_scores), 0.85)
        self.assertGreaterEqual(sum(boundary_scores) / len(boundary_scores), 0.80)

    def test_36_twenty_five_thousand_note_analysis_meets_ten_second_gate(self) -> None:
        started = time.perf_counter()
        result = analyze_song_map(_large_case())
        elapsed = time.perf_counter() - started
        self.assertEqual(result["source"]["noteCount"], 25_000)
        self.assertLess(elapsed, 10.0)


if __name__ == "__main__":
    unittest.main()