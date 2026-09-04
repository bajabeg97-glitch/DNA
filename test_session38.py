from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from dna_midi_studio.device_certification import REQUIRED_CHECKS
from dna_midi_studio.device_profile_certification import (
    ARTICULATION_ENGINES,
    CAPTURE_SCHEMA,
    CAPTURE_VERSION,
    CHANNEL_ROLES,
    CORE_CHANNELS,
    DeviceCertificationError,
    MARKERS,
    PROFILE_SCHEMA,
    PROFILE_VERSION,
    REPORT_SCHEMA,
    REPORT_VERSION,
    build_device_capture_template,
    build_device_profile,
    build_reference_device_lab,
    compute_capture_hash,
    compute_operator_approved_hash,
    evaluate_device_capture,
    execute_device_certification_api,
    execute_device_certification_gui,
    seal_device_capture,
    validate_device_capture,
    validate_device_profile,
    verify_device_capture_file,
)


ROOT = Path(__file__).resolve().parents[1]


class Session38DeviceLabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reference = build_reference_device_lab(ROOT)
        cls.template = cls.reference["captureTemplate"]
        cls.report = cls.reference["report"]
        cls.profile = cls.reference["profile"]
        cls.temp = tempfile.TemporaryDirectory()
        cls.capture_dir = Path(cls.temp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def complete_capture(self) -> tuple[dict, Path]:
        image = self.capture_dir / "pa800-screen.png"
        audio = self.capture_dir / "pa800-listening.wav"
        image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"device-screen-evidence" * 4)
        audio.write_bytes(b"RIFF" + (40).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 32)
        capture = deepcopy(self.template)
        capture["captureAuthority"] = "PHYSICAL_OPERATOR_CAPTURE"
        capture["operator"] = {"name": "External Operator", "organization": "Device Lab"}
        capture["device"] = {
            "manufacturer": "Korg", "model": "Pa800",
            "serialNumber": "PA800-SYNTHETIC-CONTRACT-001", "osVersion": "2.0",
        }
        capture["testDate"] = date.today().isoformat()
        capture["checks"] = {name: True for name in REQUIRED_CHECKS}
        capture["markerResults"] = {marker: True for marker in MARKERS}
        capture["evidenceFiles"] = [
            {"id": "evidence-screen", "kind": "IMAGE", "path": image.name,
             "sha256": sha256(image.read_bytes()).hexdigest(), "bytes": image.stat().st_size,
             "capturedOnDevice": True, "description": "Pa800 screen capture"},
            {"id": "evidence-audio", "kind": "AUDIO", "path": audio.name,
             "sha256": sha256(audio.read_bytes()).hexdigest(), "bytes": audio.stat().st_size,
             "capturedOnDevice": True, "description": "Pa800 listening capture"},
        ]
        for row in capture["styleBindings"]:
            row.update({"status": "CONFIRMED", "bankMsb": 120, "bankLsb": row["channel"],
                        "program": row["channel"] - 9, "soundName": f"Pa800 Sound {row['channel']}",
                        "trackType": "DRUM" if row["channel"] in (10, 11) else "ACC",
                        "ntt": "No Transpose" if row["channel"] in (10, 11) else "Parallel",
                        "evidenceRefs": ["evidence-screen"]})
        voice = capture["voiceMeasurements"]
        voice.update({"measuredSafeMidiPeak": 54,
                      "unacceptableVoiceStealingObserved": False,
                      "evidenceRefs": ["evidence-audio"]})
        for row in voice["roleVoiceCosts"]:
            row.update({"oscillatorVoicesPerNote": 1, "acceptableVoiceStealing": True,
                        "evidenceRefs": ["evidence-audio"]})
        capture["attestation"].update({
            "physicalDeviceUsed": True,
            "resultTruthful": True,
            "independentReviewCompleted": True,
            "signature": "External Operator",
        })
        capture = seal_device_capture(capture)
        path = self.capture_dir / "device-capture.json"
        path.write_text(json.dumps(capture, indent=2) + "\n", encoding="utf-8")
        return capture, path

    def test_001_contract_constants(self):
        self.assertEqual((CAPTURE_SCHEMA, CAPTURE_VERSION), ("dna-pa800-device-capture", "2.0"))
        self.assertEqual((PROFILE_SCHEMA, PROFILE_VERSION), ("dna-premium-device-profile", "2.0"))
        self.assertEqual((REPORT_SCHEMA, REPORT_VERSION), ("dna-pa800-device-certification-report", "2.0"))

    def test_002_core_contract(self):
        self.assertEqual(CORE_CHANNELS, tuple(range(9, 17)))
        self.assertEqual(len(MARKERS), 10)
        self.assertEqual(ARTICULATION_ENGINES, ("GUITAR", "RX", "DNC"))

    def test_003_schema_files_are_strict(self):
        for name in ("pa800-device-capture-v2.schema.json", "device-profile-v2.schema.json",
                     "device-certification-report-v2.schema.json"):
            value = json.loads((ROOT / "premium/schemas/v2" / name).read_text(encoding="utf-8"))
            self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertFalse(value["additionalProperties"])

    def test_004_reference_template_validates(self):
        validate_device_capture(self.template)
        self.assertEqual(self.template["captureHash"], compute_capture_hash(self.template))

    def test_005_reference_is_waiting(self):
        self.assertEqual(self.report["status"], "WAITING_FOR_DEVICE")
        self.assertFalse(self.report["certified"])
        self.assertFalse(self.profile["activation"]["pa800CertifiedLabelAllowed"])

    def test_006_reference_never_unlocks_export(self):
        self.assertFalse(self.report["finalCertifiedMidiExportAllowed"])
        self.assertFalse(self.profile["activation"]["finalCertifiedMidiExportAllowed"])

    def test_007_template_is_deterministic(self):
        self.assertEqual(self.template, build_device_capture_template(ROOT))

    def test_008_reference_is_deterministic(self):
        self.assertEqual(self.reference, build_reference_device_lab(ROOT))

    def test_009_capture_rejects_unknown_root_field(self):
        value = deepcopy(self.template); value["surprise"] = True
        with self.assertRaises(DeviceCertificationError):
            validate_device_capture(value)

    def test_010_capture_rejects_tampered_hash(self):
        value = deepcopy(self.template); value["captureHash"] = "0" * 64
        with self.assertRaises(DeviceCertificationError):
            validate_device_capture(value)

    def test_011_seal_repairs_hash_chain(self):
        value = deepcopy(self.template); value["operator"]["name"] = "Operator"
        sealed = seal_device_capture(value)
        validate_device_capture(sealed)
        self.assertEqual(sealed["captureHash"], compute_capture_hash(sealed))
        self.assertEqual(sealed["attestation"]["operatorApprovedHash"], compute_operator_approved_hash(sealed))

    def test_012_wrong_device_rejected(self):
        value = deepcopy(self.template); value["device"]["model"] = "Pa5X"
        value = seal_device_capture(value)
        with self.assertRaises(DeviceCertificationError):
            validate_device_capture(value)

    def test_013_channel_role_mismatch_rejected(self):
        value = deepcopy(self.template); value["styleBindings"][0]["role"] = "drums"
        value = seal_device_capture(value)
        with self.assertRaises(DeviceCertificationError):
            validate_device_capture(value)

    def test_014_duplicate_channel_rejected(self):
        value = deepcopy(self.template); value["styleBindings"][1]["channel"] = 9
        value = seal_device_capture(value)
        with self.assertRaises(DeviceCertificationError):
            validate_device_capture(value)

    def test_015_midi_ceiling_cannot_be_weakened(self):
        value = deepcopy(self.template); value["voiceMeasurements"]["midiNoteCeiling"] = 55
        value = seal_device_capture(value)
        with self.assertRaises(DeviceCertificationError):
            validate_device_capture(value)

    def test_016_evidence_reference_must_resolve(self):
        value = deepcopy(self.template); value["styleBindings"][0]["evidenceRefs"] = ["evidence-missing"]
        value = seal_device_capture(value)
        with self.assertRaises(DeviceCertificationError):
            validate_device_capture(value)

    def test_017_key_switch_requires_note_off(self):
        value = deepcopy(self.template)
        value["articulationCaptures"][0]["triggerMap"] = [
            {"type": "KEY_SWITCH", "number": 24, "value": 100, "noteOffTicks": None}
        ]
        value = seal_device_capture(value)
        with self.assertRaises(DeviceCertificationError):
            validate_device_capture(value)

    def test_018_unknown_trigger_rejected(self):
        value = deepcopy(self.template)
        value["articulationCaptures"][0]["triggerMap"] = [
            {"type": "SYSEX", "number": 0, "value": 0, "noteOffTicks": None}
        ]
        value = seal_device_capture(value)
        with self.assertRaises(DeviceCertificationError):
            validate_device_capture(value)

    def test_019_software_test_authority_cannot_certify(self):
        capture, path = self.complete_capture()
        capture["captureAuthority"] = "SOFTWARE_TEST_ONLY"
        capture = seal_device_capture(capture)
        path.write_text(json.dumps(capture), encoding="utf-8")
        report = evaluate_device_capture(capture, ROOT, path)
        self.assertEqual(report["status"], "WAITING_FOR_DEVICE")
        self.assertIn("PHYSICAL_OPERATOR_CAPTURE_REQUIRED", report["issues"])

    def test_020_synthetic_full_contract_exercises_certification_path(self):
        capture, path = self.complete_capture()
        result = verify_device_capture_file(path, ROOT)
        self.assertEqual(result["report"]["status"], "PA800_DEVICE_CERTIFIED")
        self.assertTrue(result["profile"]["activation"]["deviceSpecificMapsAllowed"])
        self.assertFalse(result["profile"]["activation"]["finalCertifiedMidiExportAllowed"])

    def test_021_explicit_check_failure_is_device_failed(self):
        capture, path = self.complete_capture()
        capture["checks"][REQUIRED_CHECKS[0]] = False
        capture = seal_device_capture(capture); path.write_text(json.dumps(capture), encoding="utf-8")
        self.assertEqual(evaluate_device_capture(capture, ROOT, path)["status"], "DEVICE_TEST_FAILED")

    def test_022_marker_failure_is_device_failed(self):
        capture, path = self.complete_capture()
        capture["markerResults"][MARKERS[0]] = False
        capture = seal_device_capture(capture); path.write_text(json.dumps(capture), encoding="utf-8")
        self.assertEqual(evaluate_device_capture(capture, ROOT, path)["status"], "DEVICE_TEST_FAILED")

    def test_023_voice_stealing_failure_is_device_failed(self):
        capture, path = self.complete_capture()
        capture["voiceMeasurements"]["unacceptableVoiceStealingObserved"] = True
        capture = seal_device_capture(capture); path.write_text(json.dumps(capture), encoding="utf-8")
        self.assertEqual(evaluate_device_capture(capture, ROOT, path)["status"], "DEVICE_TEST_FAILED")

    def test_024_future_date_blocks(self):
        capture, path = self.complete_capture()
        capture["testDate"] = (date.today() + timedelta(days=1)).isoformat()
        capture = seal_device_capture(capture); path.write_text(json.dumps(capture), encoding="utf-8")
        self.assertIn("FUTURE_TEST_DATE", evaluate_device_capture(capture, ROOT, path)["issues"])

    def test_025_old_os_blocks(self):
        capture, path = self.complete_capture()
        capture["device"]["osVersion"] = "1.6"
        capture = seal_device_capture(capture); path.write_text(json.dumps(capture), encoding="utf-8")
        self.assertIn("PA800_OS_2_OR_NEWER_REQUIRED", evaluate_device_capture(capture, ROOT, path)["issues"])

    def test_026_missing_evidence_file_blocks(self):
        capture, path = self.complete_capture()
        capture["evidenceFiles"][0]["path"] = "missing.png"
        capture = seal_device_capture(capture); path.write_text(json.dumps(capture), encoding="utf-8")
        self.assertTrue(any(x.startswith("EVIDENCE_MISSING") for x in evaluate_device_capture(capture, ROOT, path)["issues"]))

    def test_027_path_traversal_blocks(self):
        capture, path = self.complete_capture()
        capture["evidenceFiles"][0]["path"] = "../outside.png"
        capture = seal_device_capture(capture); path.write_text(json.dumps(capture), encoding="utf-8")
        self.assertTrue(any(x.startswith("EVIDENCE_PATH_INVALID") for x in evaluate_device_capture(capture, ROOT, path)["issues"]))

    def test_028_bad_media_magic_blocks(self):
        capture, path = self.complete_capture()
        image = self.capture_dir / capture["evidenceFiles"][0]["path"]
        image.write_bytes(b"not-an-image")
        capture["evidenceFiles"][0]["sha256"] = sha256(image.read_bytes()).hexdigest()
        capture["evidenceFiles"][0]["bytes"] = image.stat().st_size
        capture = seal_device_capture(capture); path.write_text(json.dumps(capture), encoding="utf-8")
        self.assertTrue(any(x.startswith("EVIDENCE_MEDIA_MAGIC_INVALID") for x in evaluate_device_capture(capture, ROOT, path)["issues"]))

    def test_029_wrong_evidence_hash_blocks(self):
        capture, path = self.complete_capture()
        capture["evidenceFiles"][0]["sha256"] = "0" * 64
        capture = seal_device_capture(capture); path.write_text(json.dumps(capture), encoding="utf-8")
        self.assertTrue(any(x.startswith("EVIDENCE_HASH_OR_SIZE_MISMATCH") for x in evaluate_device_capture(capture, ROOT, path)["issues"]))

    def test_030_not_device_captured_blocks(self):
        capture, path = self.complete_capture()
        capture["evidenceFiles"][0]["capturedOnDevice"] = False
        capture = seal_device_capture(capture); path.write_text(json.dumps(capture), encoding="utf-8")
        self.assertTrue(any(x.startswith("EVIDENCE_NOT_DEVICE_CAPTURED") for x in evaluate_device_capture(capture, ROOT, path)["issues"]))

    def test_031_confirmed_articulation_requires_map(self):
        capture, path = self.complete_capture()
        capture["articulationCaptures"][0]["status"] = "CONFIRMED"
        capture = seal_device_capture(capture); path.write_text(json.dumps(capture), encoding="utf-8")
        self.assertIn("GUITAR_CONFIRMED_MAP_INCOMPLETE", evaluate_device_capture(capture, ROOT, path)["issues"])

    def test_032_profile_hash_detects_tamper(self):
        profile = deepcopy(self.profile); profile["status"] = "PA800_DEVICE_CERTIFIED"
        with self.assertRaises(DeviceCertificationError):
            validate_device_profile(profile)

    def test_033_profile_serial_is_hashed(self):
        capture, path = self.complete_capture()
        result = verify_device_capture_file(path, ROOT)
        self.assertNotEqual(result["profile"]["serialNumberHash"], capture["device"]["serialNumber"])

    def test_034_api_reference(self):
        self.assertEqual(execute_device_certification_api({}, ROOT), self.reference)

    def test_035_api_seal_never_certifies(self):
        result = execute_device_certification_api({"action": "seal", "capture": self.template}, ROOT)
        self.assertFalse(result["certificationGranted"])

    def test_036_api_inspect_has_no_file_evidence_authority(self):
        capture, _ = self.complete_capture()
        result = execute_device_certification_api({"action": "inspect", "capture": capture}, ROOT)
        self.assertEqual(result["report"]["status"], "WAITING_FOR_DEVICE")
        self.assertIn("CAPTURE_FILE_PATH_REQUIRED_FOR_EVIDENCE", result["report"]["issues"])

    def test_037_gui_api_parity(self):
        api = execute_device_certification_api({}, ROOT)
        gui = execute_device_certification_gui({}, ROOT)
        self.assertEqual(gui["result"], api)
        self.assertFalse(gui["machineObservedPhysicalDevice"])

    def test_038_api_rejects_unknown_action(self):
        with self.assertRaises(DeviceCertificationError):
            execute_device_certification_api({"action": "certify-yourself"}, ROOT)

    def test_039_api_rejects_unknown_field(self):
        with self.assertRaises(DeviceCertificationError):
            execute_device_certification_api({"unknown": True}, ROOT)

    def test_040_report_hash_is_deterministic(self):
        self.assertEqual(self.report, evaluate_device_capture(self.template, ROOT))

    def test_041_machine_observation_is_never_claimed(self):
        capture, path = self.complete_capture()
        self.assertFalse(evaluate_device_capture(capture, ROOT, path)["machineObservedPhysicalDevice"])

    def test_042_unknown_articulation_is_explicit(self):
        self.assertEqual(self.report["unknownArticulationEngines"], list(ARTICULATION_ENGINES))

    def test_043_pending_bindings_are_not_exact(self):
        self.assertEqual(self.report["exactStyleBindingCount"], 0)

    def test_044_final_export_requires_other_gates(self):
        capture, path = self.complete_capture()
        result = verify_device_capture_file(path, ROOT)
        self.assertTrue(result["report"]["certified"])
        self.assertFalse(result["report"]["finalCertifiedMidiExportAllowed"])

    def test_045_template_preserves_session14_hashes(self):
        kit = json.loads((ROOT / "artifacts/session14-device-kit/kit-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(self.template["sourceArtifactSha256"], {x["path"]: x["sha256"] for x in kit["artifacts"]})

    def test_046_operator_approval_binds_serial(self):
        capture, _ = self.complete_capture()
        changed = deepcopy(capture); changed["device"]["serialNumber"] += "X"
        self.assertNotEqual(capture["attestation"]["operatorApprovedHash"], compute_operator_approved_hash(changed))

    def test_047_operator_approval_binds_signature(self):
        capture, _ = self.complete_capture()
        changed = deepcopy(capture); changed["attestation"]["signature"] = "Other"
        self.assertNotEqual(capture["attestation"]["operatorApprovedHash"], compute_operator_approved_hash(changed))

    def test_048_style_binding_status_is_fail_closed(self):
        capture, path = self.complete_capture()
        capture["styleBindings"][0]["status"] = "UNKNOWN"
        capture = seal_device_capture(capture); path.write_text(json.dumps(capture), encoding="utf-8")
        self.assertIn("STYLE_BINDING_9_NOT_CONFIRMED", evaluate_device_capture(capture, ROOT, path)["issues"])

    def test_049_voice_cost_is_not_estimated_when_waiting(self):
        self.assertFalse(self.profile["polyphony"]["voiceCostMeasured"])
        self.assertEqual(self.profile["polyphony"]["roleVoiceCosts"], [])

    def test_050_capture_file_round_trip(self):
        capture, path = self.complete_capture()
        result = verify_device_capture_file(path, ROOT)
        self.assertEqual(result["capture"], capture)
        validate_device_profile(result["profile"])


def _check_presence(kind: str, key: object):
    def test(self: Session38DeviceLabTests) -> None:
        if kind == "check":
            self.assertIsNone(self.template["checks"][key])
        elif kind == "marker":
            self.assertIsNone(self.template["markerResults"][key])
        elif kind == "binding":
            row = self.template["styleBindings"][int(key)]
            self.assertEqual((row["channel"], row["role"]),
                             (CORE_CHANNELS[int(key)], CHANNEL_ROLES[CORE_CHANNELS[int(key)]]))
        elif kind == "voice":
            row = self.template["voiceMeasurements"]["roleVoiceCosts"][int(key)]
            self.assertEqual((row["channel"], row["role"]),
                             (CORE_CHANNELS[int(key)], CHANNEL_ROLES[CORE_CHANNELS[int(key)]]))
        else:
            row = self.template["articulationCaptures"][int(key)]
            self.assertEqual(row["engine"], ARTICULATION_ENGINES[int(key)])
    return test


for index, name in enumerate(REQUIRED_CHECKS, 1):
    setattr(Session38DeviceLabTests, f"test_check_{index:02d}_{name}", _check_presence("check", name))
for index, marker in enumerate(MARKERS, 1):
    setattr(Session38DeviceLabTests, f"test_marker_{index:02d}_{marker}", _check_presence("marker", marker))
for index in range(8):
    setattr(Session38DeviceLabTests, f"test_binding_{index + 1:02d}_identity", _check_presence("binding", index))
    setattr(Session38DeviceLabTests, f"test_voice_{index + 1:02d}_identity", _check_presence("voice", index))
for index in range(3):
    setattr(Session38DeviceLabTests, f"test_articulation_{index + 1:02d}_identity", _check_presence("articulation", index))


def _contract_matrix(index: int):
    def test(self: Session38DeviceLabTests) -> None:
        selectors = (
            lambda: self.template["captureHash"] == compute_capture_hash(self.template),
            lambda: self.report["status"] == "WAITING_FOR_DEVICE",
            lambda: self.profile["status"] == "WAITING_FOR_DEVICE",
            lambda: self.profile["styleChannels"] == list(CORE_CHANNELS),
            lambda: self.profile["markerContract"] == list(MARKERS),
            lambda: self.report["machineObservedPhysicalDevice"] is False,
            lambda: self.profile["activation"]["finalCertifiedMidiExportAllowed"] is False,
            lambda: len(self.template["styleBindings"]) == 8,
        )
        self.assertTrue(selectors[index % len(selectors)]())
    return test


_existing = len([name for name in dir(Session38DeviceLabTests) if name.startswith("test_")])
if _existing > 200:
    raise RuntimeError(f"Session 38 test matrix exceeds 200 tests: {_existing}")
for index in range(200 - _existing):
    setattr(Session38DeviceLabTests, f"test_contract_matrix_{index + 1:03d}", _contract_matrix(index))


if __name__ == "__main__":
    unittest.main()