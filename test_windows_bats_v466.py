"""test_windows_bats_v466.py — milestone 4.66.

Verifies the Windows .bat launchers:
- every expected launcher exists with Windows-safe content (CRLF endings,
  pure ASCII, no mojibake risk on default cmd codepages);
- each launcher cd's to the repo root via %~dp0;
- each launcher references scripts/commands that actually exist in the repo;
- the curated test list in run-tests.bat matches the milestone report list;
- .gitattributes pins *.bat to CRLF for future checkouts.
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

EXPECTED = [
    "start-bridge.bat",
    "session-pass.bat",
    "run-tests.bat",
    "refresh-patterns.bat",
    "setup-windows.bat",
]

# curated python core list (must stay in sync with run-tests.bat + reports)
CORE_MODULES = [
    "test_arranger_pro_v449", "test_studio_flow_v450", "test_truth_engine_v451",
    "test_mix_engine_v452", "test_sty_mapper_v453", "test_groove_engine_v454",
    "test_instrument_techniques_v455", "test_special_track_engine_v457",
    "test_role_patterns_v459", "test_session_pass_v460", "test_session_pass_v462",
    "test_mido_independent_v452", "test_pattern_library_v464",
    "test_user_reference_bank_v465", "test_windows_bats_v466",
]


class BatPresenceTest(unittest.TestCase):
    def test_all_launchers_exist(self):
        for name in EXPECTED:
            self.assertTrue((ROOT / name).is_file(), name)

    def test_gitattributes_pins_crlf(self):
        ga = (ROOT / ".gitattributes").read_text(encoding="ascii")
        self.assertIn("*.bat text eol=crlf", ga)


class BatFormatTest(unittest.TestCase):
    def _read(self, name: str) -> bytes:
        return (ROOT / name).read_bytes()

    def test_crlf_and_ascii_only(self):
        for name in EXPECTED:
            data = self._read(name)
            self.assertTrue(data.isascii(), f"{name}: non-ascii content")
            self.assertTrue(data.startswith(b"@echo off\r\n"), name)
            lf = data.count(b"\n")
            crlf = data.count(b"\r\n")
            self.assertEqual(lf, crlf, f"{name}: mixed/odd line endings")
            self.assertGreaterEqual(crlf, 10, name)

    def test_cds_to_repo_root(self):
        for name in EXPECTED:
            text = self._read(name).decode("ascii")
            self.assertIn('cd /d "%~dp0"', text, name)

    def test_no_tab_characters(self):
        for name in EXPECTED:
            self.assertNotIn(b"\t", self._read(name), name)


class BatContentTest(unittest.TestCase):
    def test_start_bridge(self):
        text = (ROOT / "start-bridge.bat").read_text(encoding="ascii")
        self.assertIn("node dna_bridge.mjs", text)
        self.assertTrue((ROOT / "dna_bridge.mjs").is_file())
        self.assertIn("localhost:8123", text)

    def test_session_pass(self):
        text = (ROOT / "session-pass.bat").read_text(encoding="ascii")
        self.assertIn("python dna_midi_studio\\session_pass.py %*", text)
        self.assertTrue((ROOT / "dna_midi_studio" / "session_pass.py").is_file())
        self.assertIn("--apply-actions", text)
        self.assertIn("--roles", text)

    def test_run_tests_curated_list(self):
        text = (ROOT / "run-tests.bat").read_text(encoding="ascii")
        for module in CORE_MODULES:
            self.assertIn(module, text, module)
        self.assertIn("test_max_registry_v448", text)
        self.assertIn("call npm test", text)
        self.assertIn("ALL SUITES GREEN", text)

    def test_run_tests_list_matches_status_report(self):
        report = json.loads((ROOT / "reports-max-4.66" / "02-tests.json")
                            .read_text(encoding="utf-8"))
        listed = report["suites"][0]["command"]
        for module in CORE_MODULES:
            self.assertIn(module, listed, module)

    def test_refresh_patterns(self):
        text = (ROOT / "refresh-patterns.bat").read_text(encoding="ascii")
        self.assertIn("dna_midi_studio.pattern_library", text)
        self.assertIn("dna_midi_studio.user_reference_bank", text)
        for mod in ("dna_midi_studio/pattern_library.py",
                    "dna_midi_studio/user_reference_bank.py"):
            self.assertTrue((ROOT / mod).is_file())

    def test_setup_windows(self):
        text = (ROOT / "setup-windows.bat").read_text(encoding="ascii")
        self.assertIn("python -m pip install numpy", text)
        self.assertIn("run-tests.bat", text)
        self.assertIn("start-bridge.bat", text)

    def test_manifest_lists_every_bat(self):
        manifest = json.loads((ROOT / "reports-max-4.66" / "01-bats-manifest.json")
                              .read_text(encoding="utf-8"))
        for name in EXPECTED:
            self.assertIn(name, manifest["files"], name)
            self.assertRegex(manifest["files"][name]["sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
