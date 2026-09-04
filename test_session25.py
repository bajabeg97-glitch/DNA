from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from dna_midi_studio import (
    ARTICULATION_CAPTURE_SCHEMA,
    ARTICULATION_CAPTURE_VERSION,
    ARTICULATION_CONTROL_VERSION,
    ARTICULATION_ENGINES,
    ARTICULATION_EVENT_TYPES,
    ARTICULATION_MAP_SCHEMA,
    ARTICULATION_MAP_VERSION,
    ARTICULATION_PLAN_SCHEMA,
    ARTICULATION_PLAN_VERSION,
    ARTICULATION_STATUSES,
    ArticulationControls,
    articulation_production_readiness,
    build_articulation_plan,
    build_reference_articulation_capture,
    execute_articulation_map_api,
    execute_articulation_map_gui,
    import_articulation_capture,
    validate_articulation_capture,
    validate_articulation_catalog,
    validate_articulation_plan_v2,
)
from dna_midi_studio.articulation_mapping import _hash_without
from dna_midi_studio.midi import MidiEvent, MidiFile, MidiTrack
from dna_midi_studio.session25_fixture import build_session25_chain


ROOT = Path(__file__).resolve().parents[1]


def rehash_capture(capture):
    for amap in capture["maps"]:
        for entry in amap["entries"]:
            entry["entryHash"] = _hash_without(entry, "entryHash")
        amap["mapHash"] = _hash_without(amap, "mapHash")
    capture["captureHash"] = _hash_without(capture, "captureHash")
    return capture


def trusted_capture(capture):
    value = deepcopy(capture)
    value["authority"] = "DEVICE_CAPTURED"
    value["device"]["hardwareVerified"] = True
    value["device"]["osVersion"] = "2.03"
    value["evidence"]["audioSha256"] = "a" * 64
    value["evidence"]["imageSha256"] = "b" * 64
    value["evidence"]["notes"] = "Physical operator capture with audio and image evidence."
    return rehash_capture(value)


class Session25ArticulationMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (cls.midi, cls.capture, cls.catalog, cls.groove,
         cls.expression, cls.controls) = build_session25_chain()
        cls.plans = {
            engine: build_articulation_plan(cls.midi, cls.catalog, cls.groove,
                                            cls.expression, controls)
            for engine, controls in cls.controls.items()
        }

    def plan(self, engine="DNC", *, midi=None, catalog=None, groove=None,
             expression="default", controls=None):
        expression_value = self.expression if expression == "default" else expression
        return build_articulation_plan(
            midi or self.midi, catalog or self.catalog, groove or self.groove,
            expression_value, controls or self.controls[engine],
        )

    def test_01_capture_constants(self):
        self.assertEqual((ARTICULATION_CAPTURE_SCHEMA, ARTICULATION_CAPTURE_VERSION),
                         ("dna-premium-articulation-capture", "1.0"))

    def test_02_map_constants(self):
        self.assertEqual((ARTICULATION_MAP_SCHEMA, ARTICULATION_MAP_VERSION),
                         ("dna-premium-articulation-map", "2.0"))

    def test_03_plan_constants(self):
        self.assertEqual((ARTICULATION_PLAN_SCHEMA, ARTICULATION_PLAN_VERSION,
                          ARTICULATION_CONTROL_VERSION),
                         ("dna-premium-articulation-plan", "2.0", "1.0"))

    def test_04_supported_engines(self):
        self.assertEqual(set(ARTICULATION_ENGINES), {"GUITAR", "RX", "DNC"})

    def test_05_supported_statuses(self):
        self.assertEqual(set(ARTICULATION_STATUSES), {"CONFIRMED", "UNKNOWN", "BLOCKED"})

    def test_06_supported_standard_events(self):
        self.assertEqual(set(ARTICULATION_EVENT_TYPES), {"KEYSWITCH", "CC", "CHANNEL_PRESSURE"})

    def test_07_capture_schema_is_strict(self):
        schema = json.loads((ROOT / "premium/schemas/v2/articulation-capture-v1.schema.json").read_text())
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertIs(schema["additionalProperties"], False)

    def test_08_map_schema_is_strict(self):
        schema = json.loads((ROOT / "premium/schemas/v2/articulation-map-v2.schema.json").read_text())
        self.assertEqual(schema["x-contract-version"], "2.0")
        self.assertIs(schema["additionalProperties"], False)

    def test_09_plan_schema_is_read_only(self):
        schema = json.loads((ROOT / "premium/schemas/v2/articulation-plan-v2.schema.json").read_text())
        self.assertEqual(schema["properties"]["finalMidiGenerated"]["const"], False)
        self.assertEqual(schema["properties"]["midiMutationAllowed"]["const"], False)

    def test_10_reference_capture_validates(self):
        self.assertIsNone(validate_articulation_capture(self.capture))

    def test_11_reference_capture_hash(self):
        self.assertEqual(self.capture["captureHash"], _hash_without(self.capture, "captureHash"))

    def test_12_reference_is_software_only(self):
        self.assertEqual(self.capture["authority"], "SOFTWARE_TEST_ONLY")
        self.assertFalse(self.capture["device"]["hardwareVerified"])

    def test_13_reference_exact_device_identity(self):
        self.assertEqual((self.capture["device"]["manufacturer"], self.capture["device"]["model"]),
                         ("Korg", "Pa800"))

    def test_14_reference_contains_all_engines(self):
        self.assertEqual({item["engine"] for item in self.capture["maps"]}, set(ARTICULATION_ENGINES))

    def test_15_reference_contains_all_statuses(self):
        self.assertEqual({entry["status"] for item in self.capture["maps"] for entry in item["entries"]},
                         set(ARTICULATION_STATUSES))

    def test_16_catalog_validates(self):
        self.assertIsNone(validate_articulation_catalog(self.catalog))

    def test_17_catalog_hash(self):
        self.assertEqual(self.catalog["catalogHash"], _hash_without(self.catalog, "catalogHash"))

    def test_18_software_catalog_is_device_blocked(self):
        self.assertEqual(self.catalog["productionStatus"], "DEVICE_BLOCKED")
        self.assertEqual(self.catalog["audit"]["productionEligibleMapCount"], 0)

    def test_19_software_maps_cannot_render_production(self):
        self.assertFalse(any(item["productionEligible"] for item in self.catalog["maps"]))

    def test_20_catalog_forbids_approximate_fallback(self):
        self.assertFalse(self.catalog["invariants"]["approximateNameMatching"])
        self.assertFalse(self.catalog["invariants"]["nearestProgramFallback"])

    def test_21_unapproved_device_capture_stays_blocked(self):
        capture = trusted_capture(self.capture)
        catalog = import_articulation_capture(capture)
        self.assertEqual(catalog["productionStatus"], "DEVICE_BLOCKED")

    def test_22_operator_approved_device_capture_is_eligible(self):
        capture = trusted_capture(self.capture)
        catalog = import_articulation_capture(capture, [capture["captureHash"]])
        self.assertEqual(catalog["productionStatus"], "PRODUCTION_ELIGIBLE")
        self.assertTrue(all(item["productionEligible"] for item in catalog["maps"]))

    def test_23_software_hash_approval_cannot_promote(self):
        catalog = import_articulation_capture(self.capture, [self.capture["captureHash"]])
        self.assertFalse(any(item["productionEligible"] for item in catalog["maps"]))

    def test_24_device_capture_requires_audio(self):
        capture = trusted_capture(self.capture)
        capture["evidence"]["audioSha256"] = None
        rehash_capture(capture)
        with self.assertRaisesRegex(ValueError, "audio and image"):
            validate_articulation_capture(capture)

    def test_25_device_capture_requires_hardware_flag(self):
        capture = trusted_capture(self.capture)
        capture["device"]["hardwareVerified"] = False
        rehash_capture(capture)
        with self.assertRaisesRegex(ValueError, "verified hardware"):
            validate_articulation_capture(capture)

    def test_26_software_capture_cannot_claim_hardware(self):
        capture = deepcopy(self.capture)
        capture["device"]["hardwareVerified"] = True
        rehash_capture(capture)
        with self.assertRaisesRegex(ValueError, "cannot claim"):
            validate_articulation_capture(capture)

    def test_27_capture_hash_tampering_rejected(self):
        capture = deepcopy(self.capture)
        capture["evidence"]["notes"] += " tamper"
        with self.assertRaisesRegex(ValueError, "capture hash mismatch"):
            validate_articulation_capture(capture)

    def test_28_entry_hash_tampering_rejected(self):
        capture = deepcopy(self.capture)
        capture["maps"][0]["entries"][0]["name"] = "changed"
        capture["maps"][0]["mapHash"] = _hash_without(capture["maps"][0], "mapHash")
        capture["captureHash"] = _hash_without(capture, "captureHash")
        with self.assertRaisesRegex(ValueError, "entry hash mismatch"):
            validate_articulation_capture(capture)

    def test_29_map_hash_tampering_rejected(self):
        capture = deepcopy(self.capture)
        capture["maps"][0]["roles"].append("solo")
        capture["captureHash"] = _hash_without(capture, "captureHash")
        with self.assertRaisesRegex(ValueError, "map hash mismatch"):
            validate_articulation_capture(capture)

    def test_30_sysex_is_rejected(self):
        capture = deepcopy(self.capture)
        capture["maps"][2]["entries"][0]["eventType"] = "SYSEX"
        rehash_capture(capture)
        with self.assertRaisesRegex(ValueError, "proprietary"):
            validate_articulation_capture(capture)

    def test_31_protected_bank_cc_is_rejected(self):
        capture = deepcopy(self.capture)
        entry = capture["maps"][2]["entries"][1]
        entry["number"] = 0
        rehash_capture(capture)
        with self.assertRaisesRegex(ValueError, "Bank/RPN/NRPN"):
            validate_articulation_capture(capture)

    def test_32_keyswitch_playable_collision_rejected(self):
        capture = deepcopy(self.capture)
        capture["maps"][0]["playableRange"]["min"] = 2
        rehash_capture(capture)
        with self.assertRaisesRegex(ValueError, "collides"):
            validate_articulation_capture(capture)

    def test_33_keyswitch_outside_trigger_range_rejected(self):
        capture = deepcopy(self.capture)
        capture["maps"][0]["entries"][0]["number"] = 12
        rehash_capture(capture)
        with self.assertRaisesRegex(ValueError, "outside"):
            validate_articulation_capture(capture)

    def test_34_keyswitch_noteoff_required(self):
        capture = deepcopy(self.capture)
        capture["maps"][0]["entries"][0]["requiredNoteOff"] = False
        rehash_capture(capture)
        with self.assertRaisesRegex(ValueError, "note-off"):
            validate_articulation_capture(capture)

    def test_35_unknown_trigger_cannot_be_executable(self):
        capture = deepcopy(self.capture)
        entry = capture["maps"][0]["entries"][2]
        entry.update({"eventType": "CC", "number": 1, "value": 2,
                      "placement": "AT_ONSET", "condition": "EVERY_NOTE"})
        rehash_capture(capture)
        with self.assertRaisesRegex(ValueError, "cannot contain executable"):
            validate_articulation_capture(capture)

    def test_36_duplicate_trigger_identity_rejected(self):
        capture = deepcopy(self.capture)
        first, second = capture["maps"][0]["entries"][:2]
        for key in ("eventType", "number", "value", "placement", "condition"):
            second[key] = first[key]
        rehash_capture(capture)
        with self.assertRaisesRegex(ValueError, "Duplicate articulation trigger"):
            validate_articulation_capture(capture)

    def test_37_guitar_plan_validates(self):
        self.assertIsNone(validate_articulation_plan_v2(self.plans["GUITAR"]))

    def test_38_rx_plan_validates(self):
        self.assertIsNone(validate_articulation_plan_v2(self.plans["RX"]))

    def test_39_dnc_plan_validates(self):
        self.assertIsNone(validate_articulation_plan_v2(self.plans["DNC"]))

    def test_40_plans_are_deterministic(self):
        self.assertEqual(self.plans["DNC"], self.plan("DNC"))

    def test_41_plan_does_not_mutate_inputs(self):
        original = (self.midi.digest(), deepcopy(self.catalog), deepcopy(self.groove), deepcopy(self.expression))
        self.plan("DNC")
        self.assertEqual((self.midi.digest(), self.catalog, self.groove, self.expression), original)

    def test_42_plan_hash_is_valid(self):
        plan = self.plans["DNC"]
        self.assertEqual(plan["articulationPlanHash"], _hash_without(plan, "articulationPlanHash"))

    def test_43_exact_sound_binding_is_recorded(self):
        self.assertEqual(self.plans["DNC"]["soundBinding"]["status"], "EXACT_MAP_MATCH")

    def test_44_track_and_channel_are_one_based(self):
        plan = self.plans["DNC"]
        self.assertEqual((plan["controls"]["trackNumber"], plan["controls"]["channelNumber"]), (4, 4))

    def test_45_source_notes_are_immutable(self):
        self.assertTrue(all(note["immutable"] for note in self.plans["DNC"]["sourceNotes"]))

    def test_46_source_note_uids_are_unique(self):
        uids = [note["sourceNoteUid"] for note in self.plans["DNC"]["sourceNotes"]]
        self.assertEqual(len(uids), len(set(uids)))

    def test_47_every_event_has_source_and_evidence(self):
        self.assertTrue(all(event["sourceNoteUid"] and event["sourceEvidenceId"]
                            for event in self.plans["DNC"]["events"]))

    def test_48_every_event_has_reason_code(self):
        self.assertTrue(all(event["reasonCode"].startswith("EXACT_DNC_")
                            for event in self.plans["DNC"]["events"]))

    def test_49_every_event_hash_is_valid(self):
        self.assertTrue(all(event["eventHash"] == _hash_without(event, "eventHash")
                            for plan in self.plans.values() for event in plan["events"]))

    def test_50_keyswitches_have_positive_noteoff(self):
        switches = [event for plan in self.plans.values() for event in plan["events"]
                    if event["eventType"] == "KEYSWITCH"]
        self.assertTrue(switches)
        self.assertTrue(all(event["noteOffTick"] > event["tick"] for event in switches))

    def test_51_unknown_and_blocked_emit_nothing(self):
        for engine, name in (("GUITAR", "body_tap"), ("RX", "proprietary_noise")):
            self.assertFalse(any(event["articulation"] == name for event in self.plans[engine]["events"]))

    def test_52_unknown_and_blocked_are_audited(self):
        self.assertEqual(self.plans["GUITAR"]["audit"]["skipped"]["STATUS_UNKNOWN"], 1)
        self.assertEqual(self.plans["RX"]["audit"]["skipped"]["STATUS_BLOCKED"], 1)

    def test_53_dnc_supports_all_standard_event_types(self):
        self.assertEqual({event["eventType"] for event in self.plans["DNC"]["events"]},
                         set(ARTICULATION_EVENT_TYPES))

    def test_54_polyphony_uses_expression_peak(self):
        self.assertEqual(self.plans["DNC"]["audit"]["baselinePeak"], 18)

    def test_55_polyphony_stays_under_54(self):
        self.assertTrue(all(plan["audit"]["withinPolyphonyCeiling"] for plan in self.plans.values()))

    def test_56_software_plan_is_preview_only(self):
        self.assertTrue(self.plans["DNC"]["readyForPreview"])
        self.assertFalse(self.plans["DNC"]["readyForProductionRender"])

    def test_57_plan_never_generates_final_midi(self):
        self.assertTrue(all(not plan["finalMidiGenerated"] and not plan["midiMutationAllowed"]
                            for plan in self.plans.values()))

    def test_58_production_mode_is_explicitly_denied(self):
        controls = deepcopy(self.controls["DNC"])
        controls["productionMode"] = True
        plan = self.plan("DNC", controls=controls)
        self.assertIn("PRODUCTION_MODE_DENIED", {item["code"] for item in plan["productionBlocks"]})

    def test_59_trusted_capture_allows_production_plan(self):
        capture = trusted_capture(self.capture)
        catalog = import_articulation_capture(capture, [capture["captureHash"]])
        controls = deepcopy(self.controls["DNC"])
        controls["productionMode"] = True
        plan = self.plan("DNC", catalog=catalog, controls=controls)
        self.assertTrue(plan["readyForProductionRender"])
        self.assertFalse(plan["productionBlocks"])

    def test_60_wrong_sound_is_rejected_without_nearest_fallback(self):
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        changed = []
        for event in tracks[3].events:
            if event.command == 0xC0 and event.channel == 3:
                changed.append(MidiEvent(event.tick, event.order, event.kind, status=event.status,
                                         data=bytes((81,)), meta_type=event.meta_type))
            else:
                changed.append(event)
        tracks[3] = MidiTrack(changed)
        midi = MidiFile(self.midi.format_type, self.midi.ppq, tracks)
        controls = deepcopy(self.controls["DNC"])
        from dna_midi_studio.track_identity import identity_for_track
        controls["trackUid"] = identity_for_track(midi, 3).track_uid
        with self.assertRaisesRegex(ValueError, "Exact SoundBinding"):
            self.plan("DNC", midi=midi, controls=controls)

    def test_61_wrong_track_uid_is_rejected(self):
        controls = deepcopy(self.controls["DNC"])
        controls["trackUid"] = self.controls["RX"]["trackUid"]
        with self.assertRaisesRegex(ValueError, "trackUid"):
            self.plan("DNC", controls=controls)

    def test_62_wrong_role_is_rejected(self):
        controls = deepcopy(self.controls["DNC"])
        controls["role"] = "drums"
        with self.assertRaisesRegex(ValueError, "role"):
            self.plan("DNC", controls=controls)

    def test_63_missing_map_is_rejected(self):
        controls = deepcopy(self.controls["DNC"])
        controls["mapId"] = "999.999.999"
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.plan("DNC", controls=controls)

    def test_64_unknown_articulation_name_is_rejected(self):
        controls = deepcopy(self.controls["DNC"])
        controls["requestedArticulations"] = ["nearest_sound_guess"]
        with self.assertRaisesRegex(ValueError, "Unknown requested"):
            self.plan("DNC", controls=controls)

    def test_65_zero_event_budget_is_fail_safe(self):
        controls = deepcopy(self.controls["GUITAR"])
        controls["maxGeneratedEvents"] = 0
        plan = self.plan("GUITAR", controls=controls)
        self.assertFalse(plan["events"])
        self.assertFalse(plan["readyForPreview"])

    def test_66_polyphony_overflow_suppresses_keyswitch(self):
        groove = deepcopy(self.groove)
        groove["audit"]["maximumPeakAfterSimplification"] = 54
        expression = deepcopy(self.expression)
        expression["audit"]["maximumEstimatedPeak"] = 54
        plan = self.plan("GUITAR", groove=groove, expression=expression)
        self.assertFalse(plan["events"])
        self.assertGreater(plan["audit"]["skipped"]["POLYPHONY_CEILING"], 0)

    def test_67_no_expression_plan_uses_groove_peak(self):
        plan = self.plan("DNC", expression=None)
        self.assertEqual(plan["audit"]["baselinePeak"], 14)

    def test_68_readiness_blocks_software_map(self):
        result = articulation_production_readiness(self.catalog, "DNC", (121, 2, 80))
        self.assertFalse(result["allowed"])
        self.assertTrue(result["status"].startswith("DEVICE_BLOCKED"))

    def test_69_readiness_refuses_nearest_sound(self):
        result = articulation_production_readiness(self.catalog, "DNC", (121, 2, 81))
        self.assertEqual(result["status"], "MANUAL_REVIEW")
        self.assertIn("nearest-sound", result["reason"])

    def test_70_readiness_allows_trusted_exact_map(self):
        capture = trusted_capture(self.capture)
        catalog = import_articulation_capture(capture, [capture["captureHash"]])
        self.assertTrue(articulation_production_readiness(catalog, "RX", (121, 1, 40))["allowed"])

    def test_71_api_reference_matches_catalog(self):
        result = execute_articulation_map_api({"action": "reference",
                                               "sourceMidiSha256": self.midi.digest()})
        self.assertEqual(result, self.catalog)

    def test_72_api_import_matches_catalog(self):
        result = execute_articulation_map_api({"action": "import", "capture": self.capture})
        self.assertEqual(result, self.catalog)

    def test_73_api_plan_matches_direct_plan(self):
        result = execute_articulation_map_api({
            "action": "plan", "midiHex": self.midi.to_bytes().hex(), "catalog": self.catalog,
            "groovePlan": self.groove, "expressionPlan": self.expression,
            "controls": self.controls["DNC"],
        })
        self.assertEqual(result, self.plans["DNC"])

    def test_74_gui_and_api_parity(self):
        payload = {"action": "reference", "sourceMidiSha256": self.midi.digest()}
        self.assertEqual(execute_articulation_map_gui(payload), execute_articulation_map_api(payload))

    def test_75_api_rejects_unknown_action(self):
        with self.assertRaisesRegex(ValueError, "reference, import or plan"):
            execute_articulation_map_api({"action": "guess"})

    def test_76_controls_reject_unknown_fields(self):
        controls = deepcopy(self.controls["DNC"])
        controls["nearestSound"] = True
        with self.assertRaisesRegex(ValueError, "Unknown articulation controls"):
            ArticulationControls.from_mapping(controls)

    def test_77_controls_require_unique_requests(self):
        controls = deepcopy(self.controls["DNC"])
        controls["requestedArticulations"] = ["breath", "breath"]
        with self.assertRaisesRegex(ValueError, "unique"):
            ArticulationControls.from_mapping(controls)

    def test_78_cli_reference_and_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            midi_path = root / "input.mid"
            capture_path = root / "capture.json"
            catalog_path = root / "catalog.json"
            groove_path = root / "groove.json"
            expression_path = root / "expression.json"
            controls_path = root / "controls.json"
            plan_path = root / "plan.json"
            midi_path.write_bytes(self.midi.to_bytes())
            for path, value in ((groove_path, self.groove), (expression_path, self.expression),
                                (controls_path, self.controls["DNC"])):
                path.write_text(json.dumps(value), encoding="utf-8")
            first = subprocess.run([
                "python", str(ROOT / "session25_articulation_map.py"), "reference",
                str(midi_path), str(capture_path), str(catalog_path),
            ], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = subprocess.run([
                "python", str(ROOT / "session25_articulation_map.py"), "plan",
                str(midi_path), str(catalog_path), str(groove_path), str(controls_path),
                str(plan_path), "--expression-plan", str(expression_path),
            ], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(plan_path.read_text()), self.plans["DNC"])

    def test_79_future_capture_date_is_rejected(self):
        capture = deepcopy(self.capture)
        capture["capturedAt"] = "2099-01-01"
        rehash_capture(capture)
        with self.assertRaisesRegex(ValueError, "future"):
            validate_articulation_capture(capture)

    def test_80_http_import_cannot_self_approve_capture(self):
        capture = trusted_capture(self.capture)
        with self.assertRaisesRegex(ValueError, "Unknown import action"):
            execute_articulation_map_api({
                "action": "import", "capture": capture,
                "approvedCaptureHashes": [capture["captureHash"]],
            })

    def test_81_runtime_keyswitch_collision_is_skipped(self):
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        events = list(tracks[3].events)
        events.extend([
            MidiEvent(680, 800, "channel", status=0x93, data=bytes((20, 70))),
            MidiEvent(760, 801, "channel", status=0x83, data=bytes((20, 0))),
        ])
        tracks[3] = MidiTrack(events)
        midi = MidiFile(self.midi.format_type, self.midi.ppq, tracks)
        controls = deepcopy(self.controls["DNC"])
        from dna_midi_studio.track_identity import identity_for_track
        controls["trackUid"] = identity_for_track(midi, 3).track_uid
        plan = self.plan("DNC", midi=midi, controls=controls)
        self.assertGreater(plan["audit"]["skipped"]["TRIGGER_NOTE_COLLISION"], 0)


def _make_dynamic_test(engine: str, key: str, expected: Any):
    def test(self):
        amap = next(item for item in self.catalog["maps"] if item["engine"] == engine)
        if key == "map_id":
            actual = amap["mapId"]
        elif key == "sound":
            actual = tuple(amap["exactSound"].values())
        elif key == "entry_count":
            actual = len(amap["entries"])
        elif key == "production":
            actual = amap["productionEligible"]
        else:
            actual = amap["status"]
        self.assertEqual(actual, expected)
    return test


_DYNAMIC = [
    ("GUITAR", "map_id", "250.010.001"), ("RX", "map_id", "250.020.001"),
    ("DNC", "map_id", "250.030.001"), ("GUITAR", "sound", (121, 0, 24)),
    ("RX", "sound", (121, 1, 40)), ("DNC", "sound", (121, 2, 80)),
    ("GUITAR", "entry_count", 3),
]
for number, spec in enumerate(_DYNAMIC, 82):
    setattr(Session25ArticulationMapTests, f"test_{number:02d}_dynamic_{spec[0].lower()}_{spec[1]}",
            _make_dynamic_test(*spec))


if __name__ == "__main__":
    unittest.main()