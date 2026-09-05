"""test_user_reference_bank_v465.py — milestone 4.65.

Verifies:
- the user reference bank is built from the user's own files, in the same
  canonical 16-step representation as the vendored dmp corpus;
- per-file classification numbers are pinned/deterministic (bars, markers,
  motifs, corpus matches);
- genuinely shared rows across the user's files are detected;
- Session Pass reports now carry a deterministic patternRecognition section
  and action statuses are unchanged by the addition;
- artifacts exist and digests match a fresh build;
- module stays stdlib-only.
"""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dna_midi_studio.user_reference_bank import (  # noqa: E402
    DRUM_CHANNELS, USER_REFERENCE_FILES, bank_digest, bars_of,
    build_bank, classification_report, pattern_recognition,
)
from dna_midi_studio.pattern_library import build_library  # noqa: E402
from dna_midi_studio.session_pass import session_pass  # noqa: E402

STEPS_RE = re.compile(r"^[01]{16}$")


class BankBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bank = build_bank(build_library())

    def test_bank_covers_user_files(self):
        self.assertEqual(len(self.bank), len(USER_REFERENCE_FILES))
        self.assertEqual([Path(e["file"]).name for e in self.bank],
                         ["reference-style.mid", "session35-partial-preview.mid",
                          "song19-01.mid"])

    def test_bar_rows_are_16step(self):
        for e in self.bank:
            self.assertTrue(e["bars"], e["file"])
            for b in e["bars"]:
                self.assertEqual(b["endTick"] - b["startTick"],
                                 4 * 480)  # ppq 480, 4 beats
                for row in b["rows"].values():
                    self.assertRegex(row, STEPS_RE)

    def test_per_file_numbers_pinned(self):
        by_name = {Path(e["file"]).name: e for e in self.bank}
        self.assertEqual(len(by_name["reference-style.mid"]["bars"]), 22)
        self.assertEqual(len(by_name["reference-style.mid"]["markers"]), 10)
        self.assertEqual(len(by_name["reference-style.mid"]["motifCounts"]), 51)
        self.assertEqual(len(by_name["reference-style.mid"]["corpusMatches"]), 15)
        self.assertEqual(len(by_name["session35-partial-preview.mid"]["bars"]), 18)
        self.assertEqual(len(by_name["session35-partial-preview.mid"]["markers"]), 10)
        self.assertEqual(len(by_name["session35-partial-preview.mid"]["corpusMatches"]), 8)
        self.assertEqual(len(by_name["song19-01.mid"]["bars"]), 8)
        self.assertEqual(len(by_name["song19-01.mid"]["corpusMatches"]), 2)

    def test_pattern_entry_mirrors_corpus_schema(self):
        for e in self.bank:
            pe = e["patternEntry"]
            self.assertEqual(pe["signature"], "4/4")
            self.assertTrue(pe["signatureAssumed"])
            self.assertEqual(pe["length"], 16)
            self.assertTrue(pe["tracks"])
            for t in pe["tracks"].values():
                self.assertIsInstance(t["gm"], int)
                self.assertGreaterEqual(t["distinctRows"], 1)

    def test_corpus_match_reference_style_kick(self):
        by_name = {Path(e["file"]).name: e for e in self.bank}
        cm = by_name["reference-style.mid"]["corpusMatches"]
        self.assertIn("36:1000001010100010", cm)
        self.assertIn("Patterns_200:Rock1MeasureB", cm["36:1000001010100010"]["titles"])

    def test_shared_rows_across_files_honest(self):
        rep = classification_report(self.bank)
        self.assertEqual(rep["rowsSharedAcrossFiles"], 6)
        shared = rep["sharedRowsAcrossFiles"]
        self.assertEqual(shared["38:0000100000001000"],
                         ["reference-style.mid", "song19-01.mid"])
        self.assertEqual(shared["36:1000100010001000"],
                         ["reference-style.mid", "session35-partial-preview.mid"])

    def test_bank_deterministic(self):
        again = build_bank(build_library())
        self.assertEqual(bank_digest(self.bank), bank_digest(again))


class RecognitionAndSessionPassTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (ROOT / "baseline" / "reference-style.mid").read_bytes()

    def test_recognition_section_deterministic(self):
        a = pattern_recognition(self.raw, "reference-style.mid")
        b = pattern_recognition(self.raw, "reference-style.mid")
        self.assertEqual(a, b)
        self.assertEqual(a["schema"], "dna-pattern-recognition")
        self.assertEqual(a["barsAnalyzed"], 22)
        self.assertIn("36:1000001010100010", a["corpusMatches"])
        self.assertTrue(re.fullmatch(r"[0-9a-f]{16}", a["digest"]))

    def test_self_reference_match_is_visible(self):
        # honesty: when the input IS a bank file, its own rows must show up
        a = pattern_recognition(self.raw, "reference-style.mid")
        self.assertGreaterEqual(len(a["userReferenceMatches"]), 51)

    def test_session_pass_report_contains_recognition(self):
        r = session_pass(self.raw, source_name="reference-style.mid",
                         role_map={8: "bass", 9: "drums", 10: "percussion",
                                   11: "accompaniment", 12: "accompaniment",
                                   13: "accompaniment", 14: "accompaniment",
                                   15: "accompaniment"})
        pr = r["patternRecognition"]
        self.assertEqual(pr["schema"], "dna-pattern-recognition")
        self.assertEqual(pr["version"], "4.65")
        self.assertEqual(pr["barsAnalyzed"], 22)
        # action semantics unchanged by the addition
        statuses = [(a["id"], a["status"]) for a in r["actions"]]
        self.assertEqual(statuses, [
            ("A01_STY_EXPORT", "READY"),
            ("A02_PERCUSSION_CC11_GAIN", "READY"),
            ("A03_ECHO_TERCA", "SKIPPED"),
            ("A04_STUDIO_FILLS", "NEEDS_DECISION"),
            ("A05_DEVICE_LOCKED_TRIGGERS", "LOCKED"),
        ])
        self.assertIn("4.65 pattern bank", r["note"])


class ArtifactsTest(unittest.TestCase):
    def test_bank_artifacts_valid(self):
        art_dir = ROOT / "artifacts-max-4.65"
        bank = json.loads((art_dir / "user-reference-bank.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(len(bank), 3)
        cl = json.loads((art_dir / "user-reference-classification.json")
                        .read_text(encoding="utf-8"))
        self.assertEqual(cl["totalBars"], 48)
        self.assertEqual(cl["motifsSeen"], 51 + 76 + 2)
        # digest in artifact matches a fresh build
        fresh = classification_report(build_bank(build_library()))
        self.assertEqual(cl["digest"], fresh["digest"])

    def test_every_reference_file_has_sha256(self):
        bank = json.loads((ROOT / "artifacts-max-4.65" / "user-reference-bank.json")
                          .read_text(encoding="utf-8"))
        for e in bank:
            self.assertRegex(e["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue((ROOT / e["file"]).exists())

    def test_module_is_stdlib_only(self):
        src = (ROOT / "dna_midi_studio" / "user_reference_bank.py").read_text(encoding="utf-8")
        for banned in ("import mido", "from mido", "import numpy", "import scipy",
                       "import torch"):
            self.assertNotIn(banned, src)


class HonestReportTest(unittest.TestCase):
    def test_honest_report_json_valid_and_structured(self):
        rep = json.loads((ROOT / "dna-honest-report-4.65.json")
                         .read_text(encoding="utf-8"))
        vocab = set(rep["statusVocabulary"])
        ids = set()
        for item in rep["matrix"]:
            self.assertIn(item["status"], vocab, item["id"])
            ids.add(item["id"])
        self.assertEqual(len(ids), len(rep["matrix"]), "unique ids")
        # no overclaiming: the not-built items are explicit
        statuses = {i["id"]: i["status"] for i in rep["matrix"]}
        self.assertEqual(statuses["3.1"], "not-built")   # WebAudio A/B
        self.assertEqual(statuses["3.3"], "needs-hardware")  # device capture
        self.assertEqual(statuses["2.1"], "works-limited")  # A01 shape-only

    def test_honest_report_md_present(self):
        md = (ROOT / "docs" / "honest-report-4.65.md").read_text(encoding="utf-8")
        self.assertIn("Šta RADI", md)
        self.assertIn("Šta NE radi", md)
        self.assertIn("WebAudio", md)


if __name__ == "__main__":
    unittest.main()
