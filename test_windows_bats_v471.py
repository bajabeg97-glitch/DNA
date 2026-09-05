"""test_windows_bats_v471.py — milestone 4.71 (DNA Studio GUI + launcheri).

Verifies the full Windows .bat set (5 legacy 4.66 + 4 new 4.71 flows):
- every launcher exists with Windows-safe content (CRLF, pure ASCII, no tabs),
  cd's to the repo root via %~dp0, and references scripts that exist;
- the 4.71 launchers wrap the real composer/corpus/model/validation CLIs;
- run-tests.bat's curated list covers the 4.67..4.71 suites as well;
- the GUI (dna_studio_ui.html) is served next to the bridge and exposes the
  model-learning options; the bridge routes for those flows exist in code;
- workspace-4.71/ is gitignored (GUI outputs never dirty the tree);
- reports-max-4.71/01-bats-manifest.json stays in sync (sha256) with every .bat.
"""

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

EXPECTED = [
    "start-bridge.bat",
    "session-pass.bat",
    "run-tests.bat",
    "refresh-patterns.bat",
    "setup-windows.bat",
    "compose-songs.bat",
    "build-corpus.bat",
    "validate-songs.bat",
    "model-train.bat",
]

# python core modules added in 4.67..4.71 (must stay in run-tests.bat)
NEW_CORE_MODULES = [
    "test_analysis_v467", "test_assistant_brain_v468", "test_composer_corpus_v469",
    "test_generation_plan_v469", "test_composer_engine_v470", "test_windows_bats_v471",
]

GUI_MARKERS = [
    "v-compose", "v-model", "v-corpus", "v-files", "/api/compose",
    "/api/model/status", "/api/model/train", "/api/corpus/build",
    "/api/ws/list", "/api/ws/file", "Model trening (S3)",
]

BRIDGE_MARKERS = [
    "BRIDGE_VERSION = '4.71'", "dna_studio_ui.html", "/api/model/train",
    "/api/compose/info", "model_train.py", "workspace-4.71",
]


class BatPresenceTest(unittest.TestCase):
    def test_all_launchers_exist(self):
        for name in EXPECTED:
            self.assertTrue((ROOT / name).is_file(), name)

    def test_gitattributes_pins_crlf(self):
        ga = (ROOT / ".gitattributes").read_text(encoding="ascii")
        self.assertIn("*.bat text eol=crlf", ga)

    def test_workspace_gitignored(self):
        gi = (ROOT / ".gitignore").read_text(encoding="ascii")
        self.assertIn("workspace-4.71/", gi)


class BatFormatTest(unittest.TestCase):
    def _read(self, name: str) -> bytes:
        return (ROOT / name).read_bytes()

    def test_crlf_and_ascii_only(self):
        for name in EXPECTED:
            data = self._read(name)
            self.assertTrue(data.isascii(), f"{name}: non-ascii content")
            self.assertTrue(data.startswith(b"@echo off\r\n"), name)
            self.assertEqual(data.count(b"\n"), data.count(b"\r\n"),
                             f"{name}: mixed/odd line endings")
            self.assertGreaterEqual(data.count(b"\r\n"), 10, name)

    def test_cds_to_repo_root(self):
        for name in EXPECTED:
            text = self._read(name).decode("ascii")
            self.assertIn('cd /d "%~dp0"', text, name)

    def test_no_tab_characters(self):
        for name in EXPECTED:
            self.assertNotIn(b"\t", self._read(name), name)


class BatContentTest(unittest.TestCase):
    def test_compose_songs(self):
        text = (ROOT / "compose-songs.bat").read_text(encoding="ascii")
        self.assertIn("dna_midi_studio.composer_engine", text)
        self.assertIn("--out workspace-4.71", text)
        self.assertIn("workspace-4.71\\songs-4.70", text)
        self.assertIn("findstr /C:\"--out\"", text)

    def test_build_corpus(self):
        text = (ROOT / "build-corpus.bat").read_text(encoding="ascii")
        self.assertIn("dna_midi_studio.composer_corpus", text)
        self.assertIn("workspace-4.71", text)
        self.assertTrue((ROOT / "dna_midi_studio" / "composer_corpus.py").is_file())

    def test_validate_songs(self):
        text = (ROOT / "validate-songs.bat").read_text(encoding="ascii")
        self.assertIn("test_composer_corpus_v469", text)
        self.assertIn("test_composer_engine_v470", text)

    def test_model_train(self):
        text = (ROOT / "model-train.bat").read_text(encoding="ascii")
        self.assertIn("model_train.py", text)
        self.assertIn("python -m dna_midi_studio.model_train", text)
        # honest pre-flight: module arrives with the S3 layer
        self.assertIn("not exist", text)

    def test_start_bridge_opens_browser(self):
        text = (ROOT / "start-bridge.bat").read_text(encoding="ascii")
        self.assertIn("node dna_bridge.mjs", text)
        self.assertIn("localhost:8123", text)
        self.assertIn('start "" http://localhost:8123', text)

    def test_setup_windows_lists_new_launchers(self):
        text = (ROOT / "setup-windows.bat").read_text(encoding="ascii")
        for name in ("compose-songs.bat", "build-corpus.bat",
                     "validate-songs.bat", "model-train.bat"):
            self.assertIn(name, text, name)

    def test_run_tests_covers_471_suites(self):
        text = (ROOT / "run-tests.bat").read_text(encoding="ascii")
        for module in NEW_CORE_MODULES:
            self.assertIn(module, text, module)
        self.assertIn("ALL SUITES GREEN", text)


class GuiGlueTest(unittest.TestCase):
    def test_ui_file_exists_with_model_panel(self):
        ui = ROOT / "dna_studio_ui.html"
        self.assertTrue(ui.is_file())
        text = ui.read_text(encoding="utf-8")
        for marker in GUI_MARKERS:
            self.assertIn(marker, text, marker)

    def test_bridge_code_has_studio_routes(self):
        bridge = (ROOT / "dna_bridge.mjs").read_text(encoding="utf-8")
        for marker in BRIDGE_MARKERS:
            self.assertIn(marker, bridge, marker)
        self.assertNotIn("const INDEX_HTML = `", bridge,
                         "old inline template must be gone (UI now separate file)")

    def test_manifest_lists_every_bat_with_sha256(self):
        manifest = json.loads((ROOT / "reports-max-4.71" / "01-bats-manifest.json")
                              .read_text(encoding="utf-8"))
        for name in EXPECTED:
            self.assertIn(name, manifest["files"], name)
            entry = manifest["files"][name]
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
            data = (ROOT / name).read_bytes()
            self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest(), name)
            self.assertEqual(entry["bytes"], len(data), name)
            self.assertEqual(entry["crlf"], data.count(b"\r\n"), name)

    def test_ui_marker_in_manifestless_reports_dir(self):
        # reports dir for the milestone exists with tests report
        tests_report = ROOT / "reports-max-4.71" / "02-tests.json"
        self.assertTrue(tests_report.is_file())
        report = json.loads(tests_report.read_text(encoding="utf-8"))
        listed = report["suites"][0]["command"]
        for module in NEW_CORE_MODULES:
            self.assertIn(module, listed, module)


if __name__ == "__main__":
    unittest.main()
