from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dna_midi_studio import (  # noqa: E402
    ALLOWED_EVENT_SOURCE,
    LEARNING_EVENT_TYPES,
    OVERRIDE_DIMENSIONS,
    PREFERENCE_DIMENSIONS,
    PROFILE_DELETION_SCHEMA,
    PROFILE_EXPORT_SCHEMA,
    PROFILE_OPERATION_VERSION,
    PROFILE_SCHEMA,
    PROFILE_VERSION,
    PROHIBITED_SIGNALS,
    RANKING_OVERLAY_SCHEMA,
    RANKING_OVERLAY_VERSION,
    build_cold_start_profile,
    build_personal_profile,
    build_preference_ranking_overlay,
    delete_personal_profile,
    edit_personal_profile,
    execute_personal_profile_api,
    execute_personal_profile_gui,
    export_personal_profile,
    validate_personal_profile_v2,
    validate_preference_ranking_overlay,
    validate_profile_deletion,
)
from dna_midi_studio.session29_fixture import REFERENCE_DATE, build_session29_chain  # noqa: E402


def _rehash(value, field):
    raw = json.dumps({key: item for key, item in value.items() if key != field},
                     sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    value[field] = sha256(raw).hexdigest()


class Session29PersonalProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = build_session29_chain(ROOT)
        cls.profile = cls.fixture["personalProfile"]
        cls.overlay = cls.fixture["rankingOverlay"]
        cls.documents = cls.fixture["documents"]

    def test_001_profile_constants(self):
        self.assertEqual((PROFILE_SCHEMA, PROFILE_VERSION),
                         ("dna-personal-producer-profile", "2.0"))

    def test_002_overlay_constants(self):
        self.assertEqual((RANKING_OVERLAY_SCHEMA, RANKING_OVERLAY_VERSION),
                         ("dna-personal-ranking-overlay", "1.0"))

    def test_003_operation_constants(self):
        self.assertEqual((PROFILE_EXPORT_SCHEMA, PROFILE_DELETION_SCHEMA, PROFILE_OPERATION_VERSION),
                         ("dna-personal-profile-export", "dna-personal-profile-deletion", "1.0"))

    def test_004_learning_types_are_explicit_only(self):
        self.assertEqual(LEARNING_EVENT_TYPES, ("ACCEPT_VARIANT", "LOCK_SELECTION"))

    def test_005_event_source_is_user_explicit(self):
        self.assertEqual(ALLOWED_EVENT_SOURCE, "USER_EXPLICIT")

    def test_006_prohibited_signals_cover_implicit_behavior(self):
        self.assertIn("PLAYBACK", PROHIBITED_SIGNALS)
        self.assertIn("CLOUD_TELEMETRY", PROHIBITED_SIGNALS)

    def test_007_preference_dimensions(self):
        self.assertEqual(PREFERENCE_DIMENSIONS, ("density", "transitions", "syncopation", "space"))

    def test_008_override_dimensions_are_bounded(self):
        self.assertEqual(set(OVERRIDE_DIMENSIONS),
                         {"pattern", "role", "density", "transitions", "syncopation", "space"})

    def test_009_profile_schema_is_strict(self):
        schema = json.loads((ROOT / "premium/schemas/v2/personal-producer-profile-v2.schema.json").read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["x-contract-version"], "2.0")

    def test_010_overlay_schema_is_strict(self):
        schema = json.loads((ROOT / "premium/schemas/v2/personal-ranking-overlay-v1.schema.json").read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["x-contract-version"], "1.0")

    def test_011_deletion_schema_forbids_retention(self):
        schema = json.loads((ROOT / "premium/schemas/v2/personal-profile-deletion-v1.schema.json").read_text())
        self.assertEqual(schema["properties"]["dataRetained"]["const"], False)
        self.assertEqual(schema["properties"]["profileUsable"]["const"], False)

    def test_012_generated_profile_validates(self):
        validate_personal_profile_v2(self.profile)

    def test_013_profile_hash_is_valid(self):
        self.assertRegex(self.profile["profileHash"], r"^[0-9a-f]{64}$")

    def test_014_profile_is_deterministic(self):
        second = build_personal_profile(
            self.fixture["workflow"], self.documents["producerBrief"],
            self.documents["arrangementGraph"], self.documents["candidateSet"],
            self.fixture["learningEvents"], self.fixture["profileIdentity"])
        self.assertEqual(second, self.profile)

    def test_015_profile_identity_is_local(self):
        self.assertTrue(self.profile["identity"]["localOnly"])
        self.assertEqual(self.profile["identity"]["locale"], "hr-HR")

    def test_016_profile_has_no_midi_project_or_audio(self):
        safety = self.profile["safety"]
        self.assertFalse(safety["containsMidi"] or safety["containsProject"] or safety["containsAudio"])

    def test_017_profile_has_no_cloud_or_telemetry(self):
        self.assertFalse(self.profile["safety"]["cloudSyncAllowed"])
        self.assertFalse(self.profile["safety"]["telemetryAllowed"])

    def test_018_profile_has_no_authoritative_power(self):
        safety = self.profile["safety"]
        self.assertFalse(any(safety[key] for key in
                             ("hardConstraintAuthority", "factoryDynamicsAuthority",
                              "soundBindingAuthority", "validatorAuthority", "bankProgramAuthority")))

    def test_019_profile_cannot_mutate_midi(self):
        self.assertFalse(self.profile["safety"]["midiMutationAllowed"])

    def test_020_profile_is_explainable_and_reversible(self):
        self.assertTrue(self.profile["audit"]["explainable"])
        self.assertTrue(self.profile["audit"]["reversible"])

    def test_021_exactly_two_explicit_events(self):
        self.assertEqual(self.profile["learning"]["eventCount"], 2)

    def test_022_event_type_counts(self):
        self.assertEqual(self.profile["learning"]["acceptedVariantCount"], 1)
        self.assertEqual(self.profile["learning"]["manualLockCount"], 1)

    def test_023_every_event_is_explicit_and_accepted(self):
        self.assertTrue(all(item["source"] == "USER_EXPLICIT" and item["accepted"]
                            for item in self.profile["learning"]["events"]))

    def test_024_every_event_is_hashed(self):
        self.assertTrue(all(len(item["eventHash"]) == 64
                            for item in self.profile["learning"]["events"]))

    def test_025_events_bind_workflow_and_candidate_set(self):
        self.assertEqual(self.profile["source"]["workflowHashes"],
                         [self.fixture["workflow"]["workflowHash"]])
        self.assertEqual(self.profile["source"]["candidateSetHashes"],
                         [self.documents["candidateSet"]["candidateSetHash"]])

    def test_026_variant_event_expands_selected_patterns(self):
        event = next(item for item in self.profile["learning"]["events"]
                     if item["eventType"] == "ACCEPT_VARIANT")
        self.assertGreaterEqual(len(event["selections"]), 50)

    def test_027_lock_event_has_one_exact_selection(self):
        event = next(item for item in self.profile["learning"]["events"]
                     if item["eventType"] == "LOCK_SELECTION")
        self.assertEqual(len(event["selections"]), 1)
        self.assertEqual(event["patternId"], event["selections"][0]["patternId"])

    def test_028_learning_context_comes_from_brief(self):
        intent = self.documents["producerBrief"]["intent"]
        self.assertTrue(all(item["context"]["genre"] == intent["genre"]
                            for item in self.profile["learning"]["events"]))

    def test_029_implicit_learning_count_is_zero(self):
        self.assertEqual(self.profile["audit"]["implicitLearningCount"], 0)

    def test_030_prohibited_signals_are_manifested(self):
        self.assertEqual(self.profile["learning"]["prohibitedSignals"], list(PROHIBITED_SIGNALS))

    def test_031_genre_preference_is_pop_folk(self):
        self.assertEqual(self.profile["preferences"]["genres"][0]["genre"], "pop-folk")

    def test_032_density_preference_is_balanced(self):
        self.assertEqual(self.profile["preferences"]["dimensions"]["density"][0]["value"], "balanced")

    def test_033_transition_preference_is_subtle(self):
        self.assertEqual(self.profile["preferences"]["dimensions"]["transitions"][0]["value"], "subtle")

    def test_034_profile_learns_all_active_roles(self):
        roles = {item["role"] for item in self.profile["preferences"]["roles"]}
        self.assertTrue({"drums", "bass", "guitar", "accompaniment"} <= roles)

    def test_035_profile_learns_all_ten_markers(self):
        self.assertEqual(len(self.profile["preferences"]["markers"]), 10)

    def test_036_pattern_preferences_use_stable_ids(self):
        self.assertTrue(all(__import__("re").fullmatch(r"[0-9]{3}(\.[0-9]{3}){2}", item["patternId"])
                            for item in self.profile["preferences"]["patterns"]))

    def test_037_pattern_preferences_are_evidence_backed(self):
        self.assertTrue(all(item["evidenceCount"] >= 1 and item["eventIds"]
                            for item in self.profile["preferences"]["patterns"]))

    def test_038_register_preferences_are_role_scoped(self):
        self.assertTrue(all(item["role"] and item["low"] <= item["center"] <= item["high"]
                            for item in self.profile["preferences"]["registerBands"]))

    def test_039_preference_weights_are_bounded(self):
        rows = self.profile["preferences"]["patterns"] + self.profile["preferences"]["roles"]
        self.assertTrue(all(0 < item["weight"] <= 1 for item in rows))

    def test_040_learning_does_not_mutate_source_documents(self):
        before = deepcopy(self.documents["candidateSet"])
        build_personal_profile(self.fixture["workflow"], self.documents["producerBrief"],
                               self.documents["arrangementGraph"], before,
                               self.fixture["learningEvents"], self.fixture["profileIdentity"])
        self.assertEqual(before, self.documents["candidateSet"])

    def test_041_playback_learning_is_rejected(self):
        event = deepcopy(self.fixture["learningEvents"][0]); event["eventType"] = "PLAYBACK"
        with self.assertRaisesRegex(ValueError, "Implicit or prohibited"):
            self._build([event])

    def test_042_rejected_decision_is_rejected(self):
        event = deepcopy(self.fixture["learningEvents"][0]); event["accepted"] = False
        with self.assertRaisesRegex(ValueError, "explicit accepted"):
            self._build([event])

    def test_043_cloud_source_is_rejected(self):
        event = deepcopy(self.fixture["learningEvents"][0]); event["source"] = "CLOUD_TELEMETRY"
        with self.assertRaisesRegex(ValueError, "explicit accepted"):
            self._build([event])

    def test_044_wrong_workflow_is_rejected(self):
        event = deepcopy(self.fixture["learningEvents"][0]); event["workflowHash"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "another workflow"):
            self._build([event])

    def test_045_unknown_variant_is_rejected(self):
        event = deepcopy(self.fixture["learningEvents"][0]); event["variantId"] = "D"
        with self.assertRaisesRegex(ValueError, "unknown variant"):
            self._build([event])

    def test_046_invalid_lock_is_rejected(self):
        event = deepcopy(self.fixture["learningEvents"][1]); event["patternId"] = "999.999.999"
        with self.assertRaisesRegex(ValueError, "not present"):
            self._build([event])

    def test_047_duplicate_event_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate learning"):
            self._build([self.fixture["learningEvents"][0]] * 2)

    def test_048_empty_learning_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "At least one"):
            self._build([])

    def test_049_future_event_date_is_rejected(self):
        event = deepcopy(self.fixture["learningEvents"][0]); event["eventDate"] = "2026-09-04"
        with self.assertRaisesRegex(ValueError, "future"):
            self._build([event])

    def test_050_unknown_event_field_is_rejected(self):
        event = deepcopy(self.fixture["learningEvents"][0]); event["midiHex"] = "4d546864"
        with self.assertRaisesRegex(ValueError, "Unknown learning event"):
            self._build([event])

    def test_051_overlay_validates(self):
        validate_preference_ranking_overlay(self.overlay)

    def test_052_overlay_is_deterministic(self):
        second = build_preference_ranking_overlay(self.documents["candidateSet"], self.profile, genre="pop-folk")
        self.assertEqual(second, self.overlay)

    def test_053_overlay_does_not_mutate_candidate_set(self):
        before = deepcopy(self.documents["candidateSet"])
        build_preference_ranking_overlay(before, self.profile, genre="pop-folk")
        self.assertEqual(before, self.documents["candidateSet"])

    def test_054_overlay_covers_all_requests(self):
        self.assertEqual(self.overlay["audit"]["requestCount"], 52)

    def test_055_overlay_covers_only_hard_passed_candidates(self):
        self.assertEqual(self.overlay["audit"]["eligibleCandidateCount"], 624)
        self.assertTrue(all(candidate["hardConstraintsPassed"] for row in self.overlay["requests"]
                            for candidate in row["candidates"]))

    def test_056_rejected_candidates_are_untouched(self):
        expected = sum(len(item["rejectedCandidates"]) for item in self.documents["candidateSet"]["requests"])
        self.assertEqual(self.overlay["audit"]["rejectedCandidateCountUntouched"], expected)
        self.assertFalse(self.overlay["safety"]["rejectedCandidatesPromoted"])

    def test_057_bonus_is_strictly_soft_and_bounded(self):
        self.assertLessEqual(self.overlay["audit"]["maximumAbsoluteBonus"], 0.08)
        self.assertGreater(self.overlay["audit"]["maximumAbsoluteBonus"], 0)

    def test_058_profile_changes_ranking_without_hard_recheck(self):
        self.assertGreater(self.overlay["audit"]["rerankedRequestCount"], 0)
        self.assertFalse(self.overlay["audit"]["hardConstraintsReevaluated"])

    def test_059_overlay_preserves_candidate_hashes(self):
        source = {(r["requestId"], c["patternId"]): c["candidateHash"]
                  for r in self.documents["candidateSet"]["requests"] for c in r["rankedCandidates"]}
        self.assertTrue(all(item["candidateHash"] == source[(row["requestId"], item["patternId"])]
                            for row in self.overlay["requests"] for item in row["candidates"]))

    def test_060_overlay_has_no_midi_or_validator_authority(self):
        self.assertFalse(self.overlay["safety"]["midiMutationAllowed"])
        self.assertFalse(self.overlay["safety"]["validatorAuthority"])

    def test_061_cold_start_is_neutral(self):
        cold = self.fixture["coldStartOverlay"]
        self.assertTrue(cold["source"]["neutral"])
        self.assertEqual(cold["audit"]["rerankedRequestCount"], 0)

    def test_062_disabled_profile_is_neutral(self):
        self.assertEqual(self.fixture["disabledOverlay"], self.fixture["coldStartOverlay"])

    def test_063_deleted_profile_returns_neutral_order(self):
        cold = [item["adjustedOrder"] for item in self.fixture["coldStartOverlay"]["requests"]]
        deleted = [item["adjustedOrder"] for item in self.fixture["deletedOverlay"]["requests"]]
        self.assertEqual(cold, deleted)

    def test_064_edit_is_explicit_and_hashed(self):
        edited = self.fixture["editedProfile"]
        self.assertEqual(edited["identity"]["displayName"], "Moj Premium producent")
        self.assertEqual(edited["overrides"][0]["source"], "USER_EXPLICIT")
        validate_personal_profile_v2(edited)

    def test_065_override_cannot_target_bank_program(self):
        with self.assertRaisesRegex(ValueError, "supported"):
            edit_personal_profile(self.profile, {"effectiveDate": REFERENCE_DATE,
                "overrides": [{"dimension": "bankProgram", "key": "0/0/1", "weight": 1,
                               "reason": "unsafe override", "source": "USER_EXPLICIT"}]})

    def test_066_profile_export_is_sanitized(self):
        exported = self.fixture["profileExport"]
        self.assertFalse(exported["containsMidi"] or exported["containsProject"] or exported["containsAudio"])
        self.assertTrue(exported["localOnly"])

    def test_067_profile_deletion_is_complete(self):
        deletion = self.fixture["profileDeletion"]
        validate_profile_deletion(deletion)
        self.assertFalse(deletion["dataRetained"] or deletion["profileUsable"])

    def test_068_deletion_tamper_is_rejected(self):
        deletion = deepcopy(self.fixture["profileDeletion"]); deletion["dataRetained"] = True
        _rehash(deletion, "deletionHash")
        with self.assertRaisesRegex(ValueError, "remove all"):
            validate_profile_deletion(deletion)

    def test_069_incremental_learning_preserves_profile_id(self):
        event = deepcopy(self.fixture["learningEvents"][0])
        event["eventId"] = "decision-accept-b-002"; event["variantId"] = "B"
        updated = build_personal_profile(self.fixture["workflow"], self.documents["producerBrief"],
                                         self.documents["arrangementGraph"], self.documents["candidateSet"],
                                         [event], existing_profile=self.profile)
        self.assertEqual(updated["identity"]["profileId"], self.profile["identity"]["profileId"])
        self.assertEqual(updated["learning"]["eventCount"], 3)

    def test_070_api_gui_cli_server_and_workspace(self):
        cold = execute_personal_profile_api({"action": "cold-start", "effectiveDate": REFERENCE_DATE})
        self.assertEqual(cold, execute_personal_profile_gui(
            {"action": "cold-start", "effectiveDate": REFERENCE_DATE}))
        self.assertEqual(execute_personal_profile_api({"action": "overlay",
            "candidateSet": self.documents["candidateSet"], "profile": self.profile,
            "genre": "pop-folk"}), self.overlay)
        run = subprocess.run([sys.executable, str(ROOT / "session29_personal_profile.py"), "--help"],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0)
        self.assertIn("Personal Producer Profile", run.stdout)
        self.assertIn("/api/personal-producer-profile", (ROOT / "server.py").read_text())
        gui = (ROOT / "web_gui.py").read_text()
        self.assertIn("PERSONAL PRODUCER PROFILE 2.0", gui)
        self.assertIn("dnaPersonalProducerProfileV2", gui)

    def _build(self, events):
        return build_personal_profile(self.fixture["workflow"], self.documents["producerBrief"],
                                      self.documents["arrangementGraph"], self.documents["candidateSet"],
                                      events, self.fixture["profileIdentity"])


def _add_dynamic_tests():
    for index in range(10):
        def test(self, index=index):
            item = self.profile["preferences"]["patterns"][index]
            self.assertRegex(item["patternId"], r"^[0-9]{3}(\.[0-9]{3}){2}$")
            self.assertGreater(item["evidenceCount"], 0)
        setattr(Session29PersonalProfileTests, f"test_{71 + index:03d}_pattern_preference_{index + 1}", test)

    for index in range(10):
        def test(self, index=index):
            row = self.overlay["requests"][index]
            self.assertRegex(row["requestOverlayHash"], r"^[0-9a-f]{64}$")
            self.assertEqual(len(row["candidates"]), 12)
        setattr(Session29PersonalProfileTests, f"test_{81 + index:03d}_overlay_request_{index + 1}", test)

    first = 0
    for index in range(10):
        def test(self, index=index):
            item = self.overlay["requests"][first]["candidates"][index]
            self.assertEqual(item["adjustedRank"], index + 1)
            self.assertTrue(item["reasonCodes"])
        setattr(Session29PersonalProfileTests, f"test_{91 + index:03d}_adjusted_candidate_{index + 1}", test)

    safety_keys = [
        "localOnly", "cloudSyncAllowed", "containsMidi", "containsProject", "containsAudio",
        "telemetryAllowed", "hardConstraintAuthority", "factoryDynamicsAuthority",
        "soundBindingAuthority", "validatorAuthority",
    ]
    for index, key in enumerate(safety_keys):
        def test(self, key=key):
            expected = key == "localOnly"
            self.assertEqual(self.profile["safety"][key], expected)
        setattr(Session29PersonalProfileTests, f"test_{101 + index:03d}_safety_{key.lower()}", test)

    neutral_rows = list(range(10))
    for index in neutral_rows:
        def test(self, index=index):
            cold = self.fixture["coldStartOverlay"]["requests"][index]
            deleted = self.fixture["deletedOverlay"]["requests"][index]
            self.assertEqual(cold["adjustedOrder"], deleted["adjustedOrder"])
            self.assertTrue(all(item["preferenceBonus"] == 0 for item in cold["candidates"]))
        setattr(Session29PersonalProfileTests, f"test_{111 + index:03d}_neutral_after_delete_{index + 1}", test)


_add_dynamic_tests()


if __name__ == "__main__":
    unittest.main()