from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

import release_packager


ROOT = Path(__file__).resolve().parents[1]


class Session13WindowsPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.dist = Path(cls.temp.name) / "dist"
        cls.zip_path, cls.checksum_path, cls.manifest = release_packager.build_release(ROOT, cls.dist)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_required_batch_entry_points_exist(self) -> None:
        for name in ("pokreni.bat", "izgradi-dna.bat", "testiraj.bat"):
            self.assertTrue((ROOT / name).is_file())

    def test_batch_files_are_ascii_and_packaged_as_crlf(self) -> None:
        with zipfile.ZipFile(self.zip_path) as archive:
            for name in ("pokreni.bat", "izgradi-dna.bat", "testiraj.bat", "install.bat", "run.bat"):
                raw = archive.read(name)
                raw.decode("ascii")
                self.assertIn(b"\r\n", raw)
                self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))

    def test_testiraj_runs_legacy_and_recovery_gates(self) -> None:
        text = (ROOT / "testiraj.bat").read_text(encoding="ascii")
        self.assertIn("release_check.py", text)
        self.assertIn("recovery_release_check.py", text)

    def test_rebuild_script_uses_authoritative_archive(self) -> None:
        text = (ROOT / "izgradi-dna.bat").read_text(encoding="ascii")
        self.assertIn('prism-uploads\\DNA.zip', text)
        self.assertIn("dna_builder.py", text)

    def test_dependency_lock_has_no_third_party_packages(self) -> None:
        lines = [line for line in (ROOT / "requirements-lock.txt").read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(lines, [])

    def test_manifest_declares_supported_windows_and_python(self) -> None:
        self.assertEqual(self.manifest["version"], "4.11.1-device-certification-intake-foundation")
        self.assertEqual(self.manifest["python"], ["3.11", "3.12", "3.13", "3.14"])
        self.assertEqual(self.manifest["platform"], ["Windows 10 x64", "Windows 11 x64"])

    def test_package_contains_all_five_production_registries(self) -> None:
        names = {item["path"] for item in self.manifest["files"]}
        expected = {f"data/{name}" for name in release_packager.FULL_REGISTRIES}
        self.assertTrue(expected <= names)

    def test_package_contains_only_authoritative_and_device_kit_zips(self) -> None:
        names = [item["path"] for item in self.manifest["files"] if item["path"].lower().endswith(".zip")]
        self.assertEqual(names, ["artifacts/session14-device-kit/DNA-PA800-Session14-Device-Test-Kit.zip",
                                 "prism-uploads/DNA.zip"])

    def test_package_has_no_cache_lock_or_temp_files(self) -> None:
        names = [item["path"] for item in self.manifest["files"]]
        self.assertFalse(any("__pycache__" in name or name.endswith((".lock", ".tmp")) for name in names))

    def test_every_package_path_is_relative_and_traversal_free(self) -> None:
        for item in self.manifest["files"]:
            path = Path(item["path"])
            self.assertFalse(path.is_absolute())
            self.assertNotIn("..", path.parts)

    def test_zip_file_hashes_match_manifest(self) -> None:
        by_path = {item["path"]: item for item in self.manifest["files"]}
        with zipfile.ZipFile(self.zip_path) as archive:
            for name, item in by_path.items():
                raw = archive.read(name)
                self.assertEqual((len(raw), sha256(raw).hexdigest()), (item["size"], item["sha256"]))

    def test_external_checksum_matches_zip(self) -> None:
        expected = self.checksum_path.read_text(encoding="ascii").split()[0]
        self.assertEqual(expected, sha256(self.zip_path.read_bytes()).hexdigest())

    def test_build_is_byte_reproducible(self) -> None:
        second_dir = Path(self.temp.name) / "second"
        second, _, _ = release_packager.build_release(ROOT, second_dir)
        self.assertEqual(sha256(self.zip_path.read_bytes()).hexdigest(), sha256(second.read_bytes()).hexdigest())

    def test_release_directory_has_one_user_zip(self) -> None:
        self.assertEqual(len(list(self.dist.glob("*.zip"))), 1)

    def test_clean_extract_import_smoke(self) -> None:
        extract = Path(self.temp.name) / "extract"
        with zipfile.ZipFile(self.zip_path) as archive:
            archive.extractall(extract)
        completed = subprocess.run(["python", "-c", "import server; server.load_data(); print(server.FACTORY['summary']['profileCount'])"],
                                   cwd=extract, capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("1964", completed.stdout)

    def test_user_guide_and_pdf_are_in_package(self) -> None:
        names = {item["path"] for item in self.manifest["files"]}
        self.assertTrue({"README.md", "WINDOWS_RELEASE.md", "main.pdf",
                         "AI_PREMIUM_ARRANGER_PLAN.md",
                         "premium_config.py", "session15_release_check.py",
                         "session17_release_check.py",
                         "session18_release_check.py",
                         "session19_release_check.py",
                         "session20_release_check.py", "session20_producer_brief.py",
                         "data/premium-baseline.json", "data/premium-feature-matrix.json",
                         "data/session15-test-report.json", "premium/schemas/catalog.json",
                         "data/session17-test-report.json",
                         "data/session17-production-registry-catalog.json",
                         "data/session17-real-corpus-manifest.json",
                         "artifacts/session17-production-adapter-manifest.json",
                         "data/session18-test-report.json", "data/session18-schema-catalog.json",
                         "premium/schemas/v2/sound-binding-v2.schema.json",
                         "premium/schemas/v2/track-identity-v1.schema.json",
                         "premium/schemas/v2/solo-safety-report-v1.schema.json",
                         "premium/schemas/v2/song-map-v2.schema.json",
                         "premium/schemas/v2/song-map-corrections-v1.schema.json",
                         "data/session19-test-report.json",
                         "data/session19-labeled-benchmark.json",
                         "data/session19-benchmark-report.json",
                         "data/session19-schema-catalog.json",
                         "artifacts/session19-song-map.json",
                         "artifacts/session19-variable-meter-song-map.json",
                         "premium/schemas/v2/producer-brief-v2.schema.json",
                         "data/session20-test-report.json",
                         "data/session20-intent-corpus.json",
                         "data/session20-benchmark-report.json",
                         "data/session20-schema-catalog.json",
                         "artifacts/session20-producer-brief.json",
                         "artifacts/session20-approved-conflict-brief.json",
                         "artifacts/session20-offline-fallback-brief.json",
                         "session21_release_check.py", "session21_arrangement_graph.py",
                         "premium/schemas/v2/arrangement-graph-v2.schema.json",
                         "data/session21-test-report.json",
                         "data/session21-benchmark-report.json",
                         "data/session21-schema-catalog.json",
                         "artifacts/session21-arrangement-graph.json",
                         "artifacts/session21-locked-four-plan-graph.json",
                         "session22_release_check.py", "session22_candidate_search.py",
                         "premium/schemas/v2/candidate-set-v2.schema.json",
                         "data/session22-test-report.json",
                         "data/session22-benchmark-report.json",
                         "data/session22-schema-catalog.json",
                         "artifacts/session22-candidate-set.json",
                         "artifacts/session22-partial-regeneration.json",
                         "session23_release_check.py", "session23_groove_plan.py",
                         "premium/schemas/v2/groove-plan-v2.schema.json",
                         "data/session23-test-report.json",
                         "data/session23-benchmark-report.json",
                         "data/session23-schema-catalog.json",
                         "artifacts/session23-song-map.json",
                         "artifacts/session23-arrangement-graph.json",
                         "artifacts/session23-candidate-set.json",
                         "artifacts/session23-groove-plan.json",
                         "artifacts/session23-polyphony-stress.json",
                         "session24_release_check.py", "session24_expression_plan.py",
                         "premium/schemas/v2/expression-plan-v2.schema.json",
                         "data/session24-test-report.json",
                         "data/session24-benchmark-report.json",
                         "data/session24-schema-catalog.json",
                         "artifacts/session24-reference.mid",
                         "artifacts/session24-song-map.json",
                         "artifacts/session24-groove-plan.json",
                         "artifacts/session24-expression-controls.json",
                         "artifacts/session24-reference-evidence.json",
                         "artifacts/session24-expression-plan.json",
                         "artifacts/session24-remove-ai-layer.json",
                         "session32_release_check.py", "session32_track_plan.py",
                         "premium/schemas/v2/track-plan-v3.schema.json",
                         "premium/schemas/v2/optimizer-operation-v1.schema.json",
                         "premium/schemas/v2/optimizer-dry-run-v1.schema.json",
                         "data/session32-test-report.json",
                         "data/session32-benchmark-report.json",
                         "data/session32-schema-catalog.json",
                         "artifacts/session32-track-plan.json",
                         "artifacts/session32-optimizer-dry-run.json",
                         "session33_release_check.py", "session33_arrangement_renderer.py",
                         "premium/schemas/v2/rendered-fragment-v1.schema.json",
                         "premium/schemas/v2/render-manifest-v2.schema.json",
                         "premium/schemas/v2/renderer-verification-v1.schema.json",
                         "data/session33-test-report.json",
                         "data/session33-benchmark-report.json",
                         "data/session33-schema-catalog.json",
                         "artifacts/session33-render-manifest.json",
                         "artifacts/session33-render-verification.json",
                         "artifacts/session33-publish/session33-arranger-preview_OPT.mid",
                         "session34_release_check.py",
                         "data/session34-test-report.json",
                         "artifacts/session34-coherence-plan.json",
                         "session35_release_check.py", "session35_end_to_end_arranger.py",
                         "data/session35-test-report.json",
                         "artifacts/session35-song-to-style-project.json",
                         "session36_release_check.py", "session36_reliability_gate.py",
                         "premium/schemas/v2/reliability-report-v1.schema.json",
                         "premium/schemas/v2/reliability-regression-vault-v1.schema.json",
                         "data/session36-test-report.json",
                         "data/session36-benchmark-report.json",
                         "data/session36-schema-catalog.json",
                         "artifacts/session36-reliability-report.json",
                         "artifacts/session36-regression-vault.json",
                         "session37_release_check.py", "session37_quality_calibration.py",
                         "premium/schemas/v2/quality-corpus-v2.schema.json",
                         "premium/schemas/v2/quality-calibration-report-v1.schema.json",
                         "premium/schemas/v2/production-expression-intake-v1.schema.json",
                         "premium/schemas/v2/human-listening-intake-v1.schema.json",
                         "premium/schemas/v2/quality-release-gate-v1.schema.json",
                         "data/session37-test-report.json",
                         "data/session37-benchmark-report.json",
                         "data/session37-schema-catalog.json",
                         "artifacts/session37-quality-corpus.json",
                         "artifacts/session37-calibration-report.json",
                         "artifacts/session37-holdout-report.json",
                         "artifacts/session37-expression-intake.json",
                         "artifacts/session37-listening-intake.json",
                         "artifacts/session37-quality-release-gate.json",
                         "session38_release_check.py", "session38_device_lab.py",
                         "premium/schemas/v2/pa800-device-capture-v2.schema.json",
                         "premium/schemas/v2/device-profile-v2.schema.json",
                         "premium/schemas/v2/device-certification-report-v2.schema.json",
                         "data/session38-test-report.json",
                         "data/session38-benchmark-report.json",
                         "data/session38-schema-catalog.json",
                         "artifacts/session38-device-capture-template.json",
                         "artifacts/session38-device-certification-report.json",
                         "artifacts/session38-device-profile.json",
                         "session25_release_check.py", "session25_articulation_map.py",
                         "premium/schemas/v2/articulation-capture-v1.schema.json",
                         "premium/schemas/v2/articulation-map-v2.schema.json",
                         "premium/schemas/v2/articulation-plan-v2.schema.json",
                         "data/session25-test-report.json",
                         "data/session25-benchmark-report.json",
                         "data/session25-schema-catalog.json",
                         "artifacts/session25-reference.mid",
                         "artifacts/session25-reference-capture.json",
                         "artifacts/session25-articulation-map.json",
                         "artifacts/session25-guitar-articulation-plan.json",
                         "artifacts/session25-rx-articulation-plan.json",
                         "artifacts/session25-dnc-articulation-plan.json",
                         "session26_release_check.py", "session26_premium_preview.py",
                         "premium/schemas/v2/preview-session-v2.schema.json",
                         "premium/schemas/v2/audio-render-adapter-v1.schema.json",
                         "premium/schemas/v2/device-audio-capture-v1.schema.json",
                         "data/session26-test-report.json",
                         "data/session26-benchmark-report.json",
                         "data/session26-schema-catalog.json",
                         "artifacts/session26-reference.mid",
                         "artifacts/session26-preview-session.json",
                         "artifacts/session26-variant-c-proxy.wav",
                         "artifacts/session26-audio-manifest.json",
                         "artifacts/session26-device-audio-capture.json",
                         "artifacts/session26-audio-comparison.json",
                         "session27_release_check.py", "session27_quality_evaluator.py",
                         "premium/schemas/v2/evaluation-report-v2.schema.json",
                         "premium/schemas/v2/blind-listening-package-v1.schema.json",
                         "premium/schemas/v2/listening-response-v1.schema.json",
                         "premium/schemas/v2/quality-regression-vault-v1.schema.json",
                         "data/session27-test-report.json",
                         "data/session27-benchmark-report.json",
                         "data/session27-schema-catalog.json",
                         "artifacts/session27-reference.mid",
                         "artifacts/session27-preview-session.json",
                         "artifacts/session27-blind-listening-package.json",
                         "artifacts/session27-private-listening-key.json",
                         "artifacts/session27-quality-regression-vault.json",
                         "artifacts/session27-evaluation-report.json",
                         "artifacts/session27-variant-a-proxy.wav",
                         "artifacts/session27-variant-b-proxy.wav",
                         "artifacts/session27-variant-c-proxy.wav",
                         "session28_release_check.py", "session28_premium_workflow.py",
                         "premium/schemas/v2/premium-workflow-v2.schema.json",
                         "premium/schemas/v2/workflow-recovery-v1.schema.json",
                         "data/session28-test-report.json",
                         "data/session28-benchmark-report.json",
                         "data/session28-schema-catalog.json",
                         "artifacts/session28-reference.mid",
                         "artifacts/session28-song-map.json",
                         "artifacts/session28-producer-brief.json",
                         "artifacts/session28-arrangement-graph.json",
                         "artifacts/session28-candidate-set.json",
                         "artifacts/session28-groove-plan.json",
                         "artifacts/session28-expression-plan.json",
                         "artifacts/session28-preview-session.json",
                         "artifacts/session28-evaluation-report.json",
                         "artifacts/session28-workflow-controls.json",
                         "artifacts/session28-premium-workflow.json",
                         "artifacts/session28-workflow-diff.json",
                         "artifacts/session28-recovery-checkpoint.json",
                         "artifacts/session28-resume-report.json",
                         "session29_release_check.py", "session29_personal_profile.py",
                         "premium/schemas/v2/personal-producer-profile-v2.schema.json",
                         "premium/schemas/v2/personal-ranking-overlay-v1.schema.json",
                         "premium/schemas/v2/personal-profile-deletion-v1.schema.json",
                         "data/session29-test-report.json",
                         "data/session29-benchmark-report.json",
                         "data/session29-schema-catalog.json",
                         "artifacts/session29-reference.mid",
                         "artifacts/session29-learning-events.json",
                         "artifacts/session29-source-manifest.json",
                         "artifacts/session29-personal-profile.json",
                         "artifacts/session29-ranking-overlay.json",
                         "artifacts/session29-cold-start-profile.json",
                         "artifacts/session29-cold-start-overlay.json",
                         "artifacts/session29-edited-profile.json",
                         "artifacts/session29-edited-overlay.json",
                         "artifacts/session29-disabled-profile.json",
                         "artifacts/session29-disabled-overlay.json",
                         "artifacts/session29-profile-export.json",
                         "artifacts/session29-profile-deletion.json",
                         "artifacts/session29-deleted-overlay.json",
                         "session30_release_check.py", "session30_release_readiness.py",
                         "premium/schemas/v2/release-readiness-v2.schema.json",
                         "premium/schemas/v2/software-manifest-v1.schema.json",
                         "premium/schemas/v2/project-migration-report-v1.schema.json",
                         "premium/schemas/v2/release-status-matrix-v1.schema.json",
                         "data/session30-test-report.json",
                         "data/session30-benchmark-report.json",
                         "data/session30-hardening-report.json",
                         "data/session30-schema-catalog.json",
                         "artifacts/session30-reference.mid",
                         "artifacts/session30-legacy-project.json",
                         "artifacts/session30-project-migration.json",
                         "artifacts/session30-migrated-project.dnaproject.json",
                         "artifacts/session30-software-manifest.json",
                         "artifacts/session30-release-status-matrix.json",
                         "artifacts/session30-release-readiness.json",
                         "premium/baseline/reference-style.mid",
                         "SESSION14_DEVICE_CHECKLIST.md", "session14_device_check.py",
                         "session14_release_check.py",
                         "artifacts/session14-device-kit/kit-manifest.json",
                         "artifacts/session14-device-kit/DNA_PA800_FUNCTIONAL_TEST.mid",
                         "artifacts/session14-device-kit/DNA_PA800_POLYPHONY_STRESS.mid",
                         "artifacts/session14-device-kit/DNA-PA800-Session14-Device-Test-Kit.zip"} <= names)


if __name__ == "__main__":
    unittest.main()