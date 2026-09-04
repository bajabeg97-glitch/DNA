from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from dna_midi_studio.device_certification import (
    REQUIRED_CHECKS,
    evaluate_device_result,
    prepare_device_kit,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]


class Session14DevicePreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.kit_dir = Path(cls.temp.name) / "kit"
        cls.prepared = prepare_device_kit(ROOT, cls.kit_dir)
        cls.manifest_path = cls.prepared["manifestPath"]
        cls.template_path = cls.prepared["resultTemplatePath"]
        cls.manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))
        cls.template = json.loads(cls.template_path.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def write_result(self, value: dict, name: str = "device-result.json") -> Path:
        path = self.kit_dir / name
        path.write_text(json.dumps(value, indent=2), encoding="utf-8")
        return path

    def complete_result(self) -> dict:
        result = deepcopy(self.template)
        image = self.kit_dir / "import-screen.png"
        audio = self.kit_dir / "transition-test.wav"
        image.write_bytes(b"session14-image-evidence")
        audio.write_bytes(b"session14-audio-evidence")
        result.update({"operator": "Device Test Operator", "testDate": date.today().isoformat()})
        result["device"] = {"model": "Korg Pa800", "serialNumber": "PA800-TEST-001", "osVersion": "2.0"}
        result["checks"] = {name: True for name in REQUIRED_CHECKS}
        result["evidenceFiles"] = [
            {"path": image.name, "sha256": sha256_file(image)},
            {"path": audio.name, "sha256": sha256_file(audio)},
        ]
        result["attestation"] = {
            "physicalDeviceUsed": True,
            "resultTruthful": True,
            "signature": "Device Test Operator",
        }
        return result

    def test_kit_contains_functional_and_polyphony_midi(self) -> None:
        self.assertTrue(self.prepared["functionalMidi"].is_file())
        self.assertTrue(self.prepared["polyphonyMidi"].is_file())
        self.assertTrue(self.prepared["deviceKitZip"].is_file())
        first_hash = sha256_file(self.prepared["deviceKitZip"])
        second = prepare_device_kit(ROOT, self.kit_dir)
        self.assertEqual(first_hash, sha256_file(second["deviceKitZip"]))
        with zipfile.ZipFile(second["deviceKitZip"]) as archive:
            self.assertEqual(set(archive.namelist()), {
                "DNA_PA800_FUNCTIONAL_TEST.mid",
                "DNA_PA800_FUNCTIONAL_TEST.manifest.json",
                "DNA_PA800_POLYPHONY_STRESS.mid",
                "DNA_PA800_POLYPHONY_STRESS.manifest.json",
                "README.md", "kit-manifest.json", "device-result-template.json",
            })
        self.assertIn('"/api/device-test-kit"', (ROOT / "server.py").read_text(encoding="utf-8"))
        self.assertIn("Preuzmi Pa800 test-paket", (ROOT / "web_gui.py").read_text(encoding="utf-8"))

    def test_every_manifest_artifact_hash_matches(self) -> None:
        for item in self.manifest["artifacts"]:
            self.assertEqual(item["sha256"], sha256_file(self.kit_dir / item["path"]))

    def test_functional_style_has_all_required_markers(self) -> None:
        functional = json.loads((self.kit_dir / "DNA_PA800_FUNCTIONAL_TEST.manifest.json").read_text())
        self.assertEqual(len(functional["elements"]), 10)
        self.assertTrue(functional["compliance"]["passed"])
        self.assertTrue(functional["quality"]["polyphonyPassed"])

    def test_polyphony_stress_reaches_project_peak_without_overflow(self) -> None:
        stress = json.loads((self.kit_dir / "DNA_PA800_POLYPHONY_STRESS.manifest.json").read_text())
        self.assertEqual(stress["expectedMidiNotePeak"], 54)
        self.assertEqual(stress["validation"]["globalPeakConcurrentNotes"], 54)
        self.assertTrue(stress["validation"]["polyphonyPassed"])

    def test_preflight_never_claims_device_certification(self) -> None:
        self.assertEqual(self.manifest["status"], "PREPARED_WAITING_FOR_DEVICE")
        self.assertEqual(self.manifest["certification"]["physicalPa800"], "WAITING_FOR_DEVICE")

    def test_template_contains_every_required_check(self) -> None:
        self.assertEqual(tuple(self.template["checks"]), REQUIRED_CHECKS)
        self.assertTrue(all(value is None for value in self.template["checks"].values()))

    def test_incomplete_template_is_not_certified(self) -> None:
        report = evaluate_device_result(self.template_path, self.manifest_path)
        self.assertEqual(report["status"], "WAITING_FOR_DEVICE")
        self.assertFalse(report["humanAttested"])

    def test_future_test_date_is_rejected(self) -> None:
        result = self.complete_result()
        result["testDate"] = (date.today() + timedelta(days=1)).isoformat()
        report = evaluate_device_result(self.write_result(result), self.manifest_path)
        self.assertIn("Device test date cannot be in the future", report["issues"])

    def test_wrong_artifact_hash_is_rejected(self) -> None:
        result = self.complete_result()
        first = next(iter(result["artifactSha256"]))
        result["artifactSha256"][first] = "0" * 64
        report = evaluate_device_result(self.write_result(result), self.manifest_path)
        self.assertIn("Test artifact SHA-256 set does not match the kit", report["issues"])
        artifact = self.kit_dir / first
        original = artifact.read_bytes()
        try:
            artifact.write_bytes(original + b"tampered")
            report = evaluate_device_result(
                self.write_result(self.complete_result(), "tampered-kit-result.json"), self.manifest_path
            )
            self.assertTrue(any("Kit artifact SHA-256 mismatch" in issue for issue in report["issues"]))
        finally:
            artifact.write_bytes(original)

    def test_missing_audio_evidence_is_rejected(self) -> None:
        result = self.complete_result()
        result["evidenceFiles"] = result["evidenceFiles"][:1]
        report = evaluate_device_result(self.write_result(result), self.manifest_path)
        self.assertIn("At least one hashed audio evidence file is required", report["issues"])

    def test_evidence_path_traversal_is_rejected(self) -> None:
        result = self.complete_result()
        result["evidenceFiles"][0]["path"] = "../outside.png"
        report = evaluate_device_result(self.write_result(result), self.manifest_path)
        self.assertIn("Evidence path must stay beside the result file", report["issues"])

    def test_complete_human_attestation_can_certify(self) -> None:
        result_path = self.write_result(self.complete_result())
        report = evaluate_device_result(result_path, self.manifest_path)
        self.assertEqual(report["status"], "PA800_DEVICE_CERTIFIED", report["issues"])
        self.assertTrue(report["humanAttested"])
        self.assertFalse(report["machineObservedPhysicalDevice"])
        self.assertEqual(report["resultSha256"], sha256(result_path.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()