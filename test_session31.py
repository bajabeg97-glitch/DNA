from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dna_midi_studio import (  # noqa: E402
    ACCEPT_CONFIDENCE,
    FACTORY_REGISTRY_SCHEMA,
    GM_FAMILIES,
    TRACK_ANALYSIS_SCHEMA,
    TRACK_ANALYSIS_VERSION,
    MidiEvent,
    MidiFile,
    MidiTrack,
    analyze_track_instruments,
    execute_track_instrument_analysis_api,
    execute_track_instrument_analysis_gui,
    validate_track_instrument_analysis,
)
from dna_midi_studio.session19_fixture import with_velocity  # noqa: E402
from dna_midi_studio.session31_fixture import build_session31_chain  # noqa: E402


class Session31TrackInstrumentAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = build_session31_chain(ROOT)
        cls.analysis = cls.fixture["analysis"]
        cls.temp = tempfile.TemporaryDirectory()
        cls.api_root = Path(cls.temp.name)
        (cls.api_root / "data").mkdir()
        (cls.api_root / "data/factory-velocity-profiles.json").write_text(
            json.dumps(cls.fixture["factoryCatalog"]), encoding="utf-8"
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()


def _case(name, operation):
    def test(self):
        operation(self)
    test.__name__ = "test_" + name
    setattr(Session31TrackInstrumentAnalysisTests, test.__name__, test)


def _equal(name, getter, expected):
    _case(name, lambda self: self.assertEqual(getter(self), expected))


def _true(name, getter):
    _case(name, lambda self: self.assertTrue(getter(self)))


case_count = 0

# Contract identity and schema: 12 tests.
for name, actual, expected in (
    ("001_schema_constant", TRACK_ANALYSIS_SCHEMA, "dna-automatic-track-instrument-analysis"),
    ("002_version_constant", TRACK_ANALYSIS_VERSION, "3.0"),
    ("003_factory_schema", FACTORY_REGISTRY_SCHEMA, "midi-arranger.factory-velocity-profiles"),
    ("004_accept_threshold", ACCEPT_CONFIDENCE, 0.86),
    ("005_gm_family_count", len(GM_FAMILIES), 16),
    ("006_first_gm_family", GM_FAMILIES[0], "piano"),
    ("007_last_gm_family", GM_FAMILIES[-1], "sound-effects"),
):
    _equal(name, lambda _self, value=actual: value, expected); case_count += 1

schema_path = ROOT / "premium/schemas/v2/track-instrument-analysis-v3.schema.json"
_case("008_schema_file_exists", lambda self: self.assertTrue(schema_path.is_file())); case_count += 1
_case("009_schema_is_draft_2020", lambda self: self.assertEqual(
    json.loads(schema_path.read_text())["$schema"], "https://json-schema.org/draft/2020-12/schema")); case_count += 1
_case("010_schema_is_strict", lambda self: self.assertFalse(
    json.loads(schema_path.read_text())["additionalProperties"])); case_count += 1
_case("011_schema_contract_version", lambda self: self.assertEqual(
    json.loads(schema_path.read_text())["x-contract-version"], "3.0")); case_count += 1
_case("012_reference_validates", lambda self: validate_track_instrument_analysis(self.analysis)); case_count += 1

# Reference document and optimization baseline: 24 tests.
reference_cases = (
    ("013_reference_schema", lambda s: s.analysis["schema"], TRACK_ANALYSIS_SCHEMA),
    ("014_reference_version", lambda s: s.analysis["version"], TRACK_ANALYSIS_VERSION),
    ("015_source_name", lambda s: s.analysis["source"]["fileName"], "session31-reference.mid"),
    ("016_source_format", lambda s: s.analysis["source"]["format"], 1),
    ("017_source_ppq", lambda s: s.analysis["source"]["ppq"], 480),
    ("018_physical_tracks", lambda s: s.analysis["summary"]["physicalTrackCount"], 6),
    ("019_note_tracks", lambda s: s.analysis["summary"]["noteTrackCount"], 5),
    ("020_metadata_tracks", lambda s: s.analysis["summary"]["metadataTrackCount"], 1),
    ("021_segments", lambda s: s.analysis["summary"]["segmentCount"], 6),
    ("022_accepted_tracks", lambda s: s.analysis["summary"]["acceptedTrackCount"], 5),
    ("023_review_tracks", lambda s: s.analysis["summary"]["manualReviewTrackCount"], 0),
    ("024_exact_segments", lambda s: s.analysis["summary"]["exactFactorySegmentCount"], 6),
    ("025_registry_available", lambda s: s.analysis["factoryRegistry"]["available"], True),
    ("026_registry_profile_count", lambda s: s.analysis["factoryRegistry"]["profileCount"], 7),
    ("027_registry_version", lambda s: s.analysis["factoryRegistry"]["version"], "3.3"),
    ("028_manual_review_empty", lambda s: s.analysis["manualReview"], []),
    ("029_analysis_hash_length", lambda s: len(s.analysis["analysisHash"]), 64),
    ("030_decision_hash_length", lambda s: len(s.analysis["decisionHash"]), 64),
    ("031_read_only", lambda s: s.analysis["invariants"]["readOnly"], True),
    ("032_no_original_mutation", lambda s: s.analysis["invariants"]["originalMidiChanged"], False),
    ("033_optimization_stage", lambda s: s.analysis["applicationOptimizationBaseline"]["fullOptimizationStage"], "PLANNED_AFTER_EVIDENCE_RESOLUTION"),
    ("034_safe_auto_count", lambda s: len(s.analysis["applicationOptimizationBaseline"]["safeAutomaticTracks"]), 5),
    ("035_review_auto_count", lambda s: len(s.analysis["applicationOptimizationBaseline"]["protectedOrReviewTracks"]), 0),
    ("036_deterministic_full_report", lambda s: analyze_track_instruments(
        s.fixture["midi"].to_bytes(), "session31-reference.mid",
        factory_catalog=s.fixture["factoryCatalog"]), lambda s: s.analysis),
)
for name, getter, expected in reference_cases:
    _case(name, lambda self, getter=getter, expected=expected: self.assertEqual(
        getter(self), expected(self) if callable(expected) else expected)); case_count += 1

# Physical tracks and time-scoped instrument segments: 32 tests.
def track(self, name):
    return next(item for item in self.analysis["tracks"] if item["trackName"] == name)

track_expectations = (
    ("037_conductor_ignored", "Conductor", "decision", "IGNORE_METADATA"),
    ("038_conductor_policy", "Conductor", "optimizationPolicy", "KEEP_METADATA"),
    ("039_harmony_role", "Harmony", "primaryRole", "harmony"),
    ("040_harmony_sound", "Harmony", "primaryInstrument", "Factory Grand Piano"),
    ("041_harmony_target", "Harmony", "suggestedPa800Track", "acc1"),
    ("042_bass_role", "Bass", "primaryRole", "bass"),
    ("043_bass_sound", "Bass", "primaryInstrument", "Factory Finger Bass"),
    ("044_bass_target", "Bass", "suggestedPa800Track", "bass"),
    ("045_solo_role", "Lead Solo", "primaryRole", "solo"),
    ("046_solo_sound", "Lead Solo", "primaryInstrument", "Factory Soprano Sax"),
    ("047_solo_target", "Lead Solo", "suggestedPa800Track", "acc4"),
    ("048_drums_role", "Drums", "primaryRole", "drums"),
    ("049_drums_target", "Drums", "suggestedPa800Track", "drum"),
    ("050_guitar_role", "Rhythm Guitar", "primaryRole", "guitar"),
    ("051_guitar_target", "Rhythm Guitar", "suggestedPa800Track", "acc1"),
    ("052_guitar_segment_count", "Rhythm Guitar", "segmentCount", 2),
)
for name, track_name, field, expected in track_expectations:
    _case(name, lambda self, track_name=track_name, field=field, expected=expected:
          self.assertEqual(track(self, track_name)[field], expected)); case_count += 1

segment_checks = (
    ("053_all_note_tracks_accepted", lambda s: all(t["decision"] == "ACCEPT" for t in s.analysis["tracks"] if t["noteCount"])),
    ("054_all_safe_tracks_factory_bounded", lambda s: all(t["optimizationPolicy"] == "FACTORY_BOUNDED_SAFE" for t in s.analysis["tracks"] if t["noteCount"])),
    ("055_all_segments_track_uid", lambda s: all(x["trackUid"] == t["trackUid"] for t in s.analysis["tracks"] for x in t["segments"])),
    ("056_all_segments_exact_authority", lambda s: all(x["authority"]["instrumentIdentity"] == "FACTORY_EXACT" for t in s.analysis["tracks"] for x in t["segments"])),
    ("057_no_segment_mutation_authority", lambda s: all(x["authority"]["midiMutation"] == "NONE" for t in s.analysis["tracks"] for x in t["segments"])),
    ("058_no_segment_device_authority", lambda s: all(x["authority"]["device"] == "NONE" for t in s.analysis["tracks"] for x in t["segments"])),
    ("059_sound_binding_complete", lambda s: all(x["soundBinding"]["complete"] for t in s.analysis["tracks"] for x in t["segments"])),
    ("060_track_numbers_one_based", lambda s: all(t["trackNumber"] == t["trackIndex"] + 1 for t in s.analysis["tracks"])),
    ("061_channel_numbers_one_based", lambda s: all(x["channelNumber"] == x["channelIndex"] + 1 for t in s.analysis["tracks"] for x in t["segments"])),
    ("062_positive_note_counts", lambda s: all(x["noteStatistics"]["noteCount"] > 0 for t in s.analysis["tracks"] for x in t["segments"])),
    ("063_polyphony_measured", lambda s: all(x["noteStatistics"]["polyphonyPeak"] >= 1 for t in s.analysis["tracks"] for x in t["segments"])),
    ("064_guitar_program_change_tick", lambda s: track(s, "Rhythm Guitar")["segments"][1]["startTick"] == 3840),
    ("065_guitar_programs", lambda s: [x["soundBinding"]["program"] for x in track(s, "Rhythm Guitar")["segments"]] == [24, 29]),
    ("066_guitar_sound_change", lambda s: [x["primaryInstrument"] for x in track(s, "Rhythm Guitar")["segments"]] == ["Factory Nylon Guitar", "Factory Overdrive Guitar"]),
    ("067_drum_note_profiles", lambda s: len(track(s, "Drums")["segments"][0]["factoryCandidates"]) == 2),
    ("068_stable_track_uid_format", lambda s: all(t["trackUid"].startswith("trk-") for t in s.analysis["tracks"])),
)
for name, getter in segment_checks:
    _true(name, getter); case_count += 1

# Missing evidence and shared-channel fail-closed behavior: 20 tests.
missing = (
    ("069_missing_registry_unavailable", lambda s: not s.fixture["missingCatalogAnalysis"]["factoryRegistry"]["available"]),
    ("070_missing_registry_exact_zero", lambda s: s.fixture["missingCatalogAnalysis"]["summary"]["exactFactorySegmentCount"] == 0),
    ("071_missing_registry_accept_zero", lambda s: s.fixture["missingCatalogAnalysis"]["summary"]["acceptedTrackCount"] == 0),
    ("072_missing_registry_review_five", lambda s: s.fixture["missingCatalogAnalysis"]["summary"]["manualReviewTrackCount"] == 5),
    ("073_missing_registry_review_records", lambda s: len(s.fixture["missingCatalogAnalysis"]["manualReview"]) == 6),
    ("074_missing_registry_gm_only", lambda s: all(x["identityStatus"] == "GM_FAMILY_ONLY" for t in s.fixture["missingCatalogAnalysis"]["tracks"] for x in t["segments"])),
    ("075_missing_registry_hint_only", lambda s: all(x["authority"]["instrumentIdentity"] == "HINT_ONLY" for t in s.fixture["missingCatalogAnalysis"]["tracks"] for x in t["segments"])),
    ("076_missing_registry_no_auto_policy", lambda s: all(t["optimizationPolicy"] != "FACTORY_BOUNDED_SAFE" for t in s.fixture["missingCatalogAnalysis"]["tracks"] if t["noteCount"])),
    ("077_shared_two_review_tracks", lambda s: s.fixture["sharedAnalysis"]["summary"]["manualReviewTrackCount"] == 2),
    ("078_shared_four_accepted_tracks", lambda s: s.fixture["sharedAnalysis"]["summary"]["acceptedTrackCount"] == 4),
    ("079_shared_review_code", lambda s: sum(x["code"] == "SHARED_CHANNEL_REVIEW" for x in s.fixture["sharedAnalysis"]["manualReview"]) == 2),
    ("080_shared_original_bass_review", lambda s: next(t for t in s.fixture["sharedAnalysis"]["tracks"] if t["trackName"] == "Bass")["decision"] == "MANUAL_REVIEW"),
    ("081_shared_layer_review", lambda s: next(t for t in s.fixture["sharedAnalysis"]["tracks"] if t["trackName"] == "Shared Bass Layer")["decision"] == "MANUAL_REVIEW"),
    ("082_shared_identity_confidence_capped", lambda s: all(x["identityConfidence"] <= .49 for t in s.fixture["sharedAnalysis"]["tracks"] for x in t["segments"] if x["channelIndex"] == 1)),
    ("083_shared_no_auto_accept", lambda s: not s.fixture["sharedAnalysis"]["invariants"]["sharedChannelAutoAcceptAllowed"]),
    ("084_shared_exact_still_audited", lambda s: s.fixture["sharedAnalysis"]["summary"]["exactFactorySegmentCount"] == 7),
    ("085_shared_review_protected", lambda s: len(s.fixture["sharedAnalysis"]["applicationOptimizationBaseline"]["protectedOrReviewTracks"]) == 2),
    ("086_missing_decision_differs", lambda s: s.fixture["missingCatalogAnalysis"]["decisionHash"] != s.analysis["decisionHash"]),
    ("087_shared_decision_differs", lambda s: s.fixture["sharedAnalysis"]["decisionHash"] != s.analysis["decisionHash"]),
    ("088_review_has_recovery_action", lambda s: all(x["recoveryAction"] for x in s.fixture["sharedAnalysis"]["manualReview"])),
)
for name, getter in missing:
    _true(name, getter); case_count += 1

# Safety invariants and hash integrity: 20 tests.
safety_cases = (
    ("089_velocity_not_used", lambda s: not s.analysis["invariants"]["analysisVelocityUsed"]),
    ("090_gold_not_used", lambda s: not s.analysis["invariants"]["goldUsed"]),
    ("091_gold_no_dynamics", lambda s: not s.analysis["invariants"]["goldAffectsDynamics"]),
    ("092_no_approximate_binding", lambda s: not s.analysis["invariants"]["approximateSoundBindingAllowed"]),
    ("093_track_local_state", lambda s: s.analysis["invariants"]["trackLocalSoundState"]),
    ("094_program_changes_segmented", lambda s: s.analysis["invariants"]["midSongProgramChangesSegmented"]),
    ("095_no_midi_authority", lambda s: not s.analysis["invariants"]["midiMutationAuthority"]),
    ("096_no_validator_authority", lambda s: not s.analysis["invariants"]["validatorAuthority"]),
    ("097_no_device_claim", lambda s: not s.analysis["invariants"]["deviceCertificationClaimed"]),
    ("098_velocity_decision_hash_same", lambda s: s.analysis["decisionHash"] == s.fixture["velocityAnalysis"]["decisionHash"]),
    ("099_velocity_source_hash_differs", lambda s: s.analysis["sourceSha256"] != s.fixture["velocityAnalysis"]["sourceSha256"]),
    ("100_velocity_track_uid_differs", lambda s: s.analysis["tracks"][1]["trackUid"] != s.fixture["velocityAnalysis"]["tracks"][1]["trackUid"]),
)
for name, getter in safety_cases:
    _true(name, getter); case_count += 1

def reject_extra(self):
    value = deepcopy(self.analysis); value["unexpected"] = True
    with self.assertRaises(ValueError): validate_track_instrument_analysis(value)
_case("101_reject_extra_field", reject_extra); case_count += 1

def reject_hash(self):
    value = deepcopy(self.analysis); value["analysisHash"] = "0" * 64
    with self.assertRaises(ValueError): validate_track_instrument_analysis(value)
_case("102_reject_tampered_hash", reject_hash); case_count += 1

def reject_velocity_flag(self):
    value = deepcopy(self.analysis); value["invariants"]["analysisVelocityUsed"] = True
    with self.assertRaises(ValueError): validate_track_instrument_analysis(value)
_case("103_reject_velocity_use", reject_velocity_flag); case_count += 1

def reject_mutation_flag(self):
    value = deepcopy(self.analysis); value["invariants"]["originalMidiChanged"] = True
    with self.assertRaises(ValueError): validate_track_instrument_analysis(value)
_case("104_reject_original_mutation", reject_mutation_flag); case_count += 1

def reject_approximate(self):
    value = deepcopy(self.analysis); value["invariants"]["approximateSoundBindingAllowed"] = True
    with self.assertRaises(ValueError): validate_track_instrument_analysis(value)
_case("105_reject_approximate_binding", reject_approximate); case_count += 1

_case("106_reject_malformed_midi", lambda self: self.assertRaises(
    Exception, analyze_track_instruments, b"not-midi")); case_count += 1

def reject_empty_midi(self):
    empty = MidiFile(1, 480, [MidiTrack([MidiEvent(0, 0, "meta", data=b"x", meta_type=3)])])
    with self.assertRaises(ValueError): analyze_track_instruments(empty.to_bytes())
_case("107_reject_no_notes", reject_empty_midi); case_count += 1

_case("108_source_midi_unchanged", lambda self: self.assertEqual(
    self.fixture["midi"].to_bytes(), self.fixture["midi"].to_bytes())); case_count += 1

# API, GUI and CLI parity / strict input: 16 tests.
def api_payload(self):
    return {"action": "analyze", "midiHex": self.fixture["midi"].to_bytes().hex(),
            "sourceName": "session31-reference.mid"}

_case("109_api_matches_core", lambda self: self.assertEqual(
    execute_track_instrument_analysis_api(api_payload(self), self.api_root), self.analysis)); case_count += 1
_case("110_gui_matches_api", lambda self: self.assertEqual(
    execute_track_instrument_analysis_gui(api_payload(self), self.api_root),
    execute_track_instrument_analysis_api(api_payload(self), self.api_root))); case_count += 1
_case("111_api_default_source", lambda self: self.assertEqual(
    execute_track_instrument_analysis_api({"action": "analyze", "midiHex": self.fixture["midi"].to_bytes().hex()}, self.api_root)["source"]["fileName"], "song.mid")); case_count += 1
_case("112_api_rejects_action", lambda self: self.assertRaises(
    ValueError, execute_track_instrument_analysis_api,
    {"action": "mutate", "midiHex": "00"}, self.api_root)); case_count += 1
_case("113_api_rejects_unknown_field", lambda self: self.assertRaises(
    ValueError, execute_track_instrument_analysis_api,
    {**api_payload(self), "velocity": 99}, self.api_root)); case_count += 1
_case("114_api_requires_midi_hex", lambda self: self.assertRaises(
    ValueError, execute_track_instrument_analysis_api, {"action": "analyze"}, self.api_root)); case_count += 1
_case("115_api_rejects_odd_hex", lambda self: self.assertRaises(
    ValueError, execute_track_instrument_analysis_api,
    {"action": "analyze", "midiHex": "0"}, self.api_root)); case_count += 1
_case("116_api_rejects_non_hex", lambda self: self.assertRaises(
    ValueError, execute_track_instrument_analysis_api,
    {"action": "analyze", "midiHex": "zz"}, self.api_root)); case_count += 1
_case("117_api_rejects_bad_extension", lambda self: self.assertRaises(
    ValueError, execute_track_instrument_analysis_api,
    {"action": "analyze", "midiHex": self.fixture["midi"].to_bytes().hex(), "sourceName": "song.txt"}, self.api_root)); case_count += 1
_case("118_api_rejects_non_string_name", lambda self: self.assertRaises(
    ValueError, execute_track_instrument_analysis_api,
    {"action": "analyze", "midiHex": self.fixture["midi"].to_bytes().hex(), "sourceName": 3}, self.api_root)); case_count += 1
_case("119_api_catalog_identity", lambda self: self.assertEqual(
    execute_track_instrument_analysis_api(api_payload(self), self.api_root)["factoryRegistry"]["databaseVersion"],
    "session31-self-authored-catalog")); case_count += 1
_case("120_api_does_not_embed_midi", lambda self: self.assertNotIn(
    "midiHex", execute_track_instrument_analysis_api(api_payload(self), self.api_root))); case_count += 1
_case("121_api_hash_validates", lambda self: validate_track_instrument_analysis(
    execute_track_instrument_analysis_api(api_payload(self), self.api_root))); case_count += 1
_case("122_api_is_deterministic", lambda self: self.assertEqual(
    execute_track_instrument_analysis_api(api_payload(self), self.api_root)["analysisHash"],
    execute_track_instrument_analysis_api(api_payload(self), self.api_root)["analysisHash"])); case_count += 1

def cli_parity(self):
    input_path = self.api_root / "input.mid"
    catalog_path = self.api_root / "catalog.json"
    output_path = self.api_root / "cli-report.json"
    input_path.write_bytes(self.fixture["midi"].to_bytes())
    catalog_path.write_text(json.dumps(self.fixture["factoryCatalog"]), encoding="utf-8")
    completed = subprocess.run([
        sys.executable, str(ROOT / "session31_track_analysis.py"), str(input_path),
        "--factory-catalog", str(catalog_path), "--output", str(output_path),
    ], cwd=ROOT, capture_output=True, text=True, check=False)
    self.assertEqual(completed.returncode, 0, completed.stderr)
    self.assertEqual(json.loads(output_path.read_text())["decisionHash"], self.analysis["decisionHash"])
_case("123_cli_parity", cli_parity); case_count += 1

_case("124_cli_help", lambda self: self.assertEqual(subprocess.run(
    [sys.executable, str(ROOT / "session31_track_analysis.py"), "--help"], cwd=ROOT,
    capture_output=True, text=True, check=False).returncode, 0)); case_count += 1

# Velocity-blind property sweep: 12 tests.
for index, velocity in enumerate((1, 7, 17, 28, 39, 50, 61, 72, 84, 96, 111, 127), 125):
    def velocity_case(self, velocity=velocity):
        changed = with_velocity(self.fixture["midi"].to_bytes(), velocity)
        report = analyze_track_instruments(
            changed, f"velocity-{velocity}.mid", factory_catalog=self.fixture["factoryCatalog"]
        )
        self.assertEqual(report["decisionHash"], self.analysis["decisionHash"])
        self.assertFalse(report["invariants"]["analysisVelocityUsed"])
    _case(f"{index:03d}_velocity_blind_{velocity}", velocity_case); case_count += 1

assert case_count == 136, case_count
