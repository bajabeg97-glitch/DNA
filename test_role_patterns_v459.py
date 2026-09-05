"""GUI chain + Role Pattern Evidence 4.59 tests.

Proves the evidence chain from the GUI analysis (musicAnalysis.js executed
via gui_chain_4.59.mjs) onward to the per-role pattern engine. Numbers below
were produced by executed runs (2026-09-05).
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.role_patterns import role_pattern_evidence

ART = ROOT / "artifacts-max-4.59"


def _read(rel):
    return json.loads((ART / rel).read_text(encoding="utf-8"))


class GuiChainTests(unittest.TestCase):
    def test_gui_run_covered_five_real_files(self):
        run = _read("gui-run.json")
        self.assertEqual(run["schema"], "dna-gui-chain-run")
        self.assertEqual(len(run["analyses"]), 5)
        for a in run["analyses"]:
            self.assertTrue(a["guiOk"])
        by_name = {a["file"].split("/")[-1]: a for a in run["analyses"]}
        self.assertEqual(by_name["reference-style.mid"]["notes"], 2081)
        self.assertEqual(by_name["reference-style.mid"]["styleMarkers"], 10)
        self.assertEqual(by_name["song19-01.mid"]["channels"], 4)
        self.assertIn("detectedKey", by_name["arranged-4.51-fixture.mid"])

    def test_gui_analysis_artifacts_written(self):
        for stem in ("reference-style", "arranged-4.51-fixture",
                     "song19-01", "song19-02", "session35-partial-preview"):
            a = _read(f"gui-analysis-{stem}.json")
            self.assertGreater(a["notes"], 0)
            self.assertGreaterEqual(a["score"], 60)


class RolePatternTests(unittest.TestCase):
    def test_reference_style_roles_measured(self):
        p = _read("role-patterns-reference-style.json")
        self.assertEqual(p["channels"]["8"]["role"], "bass")
        self.assertEqual(p["channels"]["8"]["noteCount"], 107)
        self.assertEqual(p["channels"]["9"]["role"], "drums")
        self.assertEqual(p["channels"]["9"]["noteCount"], 263)
        self.assertEqual(p["channels"]["10"]["noteCount"], 589)
        self.assertGreater(p["channels"]["10"]["densityNotesPerBar"], 20)

    def test_fixture_roles_measured(self):
        p = _read("role-patterns-arranged-4.51-fixture.json")
        self.assertEqual(p["channels"]["9"]["noteCount"], 308)
        self.assertEqual(p["channels"]["8"]["noteCount"], 122)

    def test_solo_pattern_measured_per_song(self):
        p = _read("role-patterns-song19-01.json")
        solo = p["channels"]["2"]
        self.assertEqual(solo["role"], "solo")
        self.assertEqual(solo["noteCount"], 16)
        mp = solo["melodyPattern"]
        for k in ("upShare", "downShare", "sameShare", "meanAbsSemis",
                  "phraseCount", "phraseLenNotes"):
            self.assertIn(k, mp)

    def test_corpus_aggregate_has_all_roles(self):
        c = _read("role-patterns-corpus.json")
        s19 = c["song19BenchmarkSoloCorpus"]
        self.assertEqual(s19["solo"]["files"], 20)
        self.assertEqual(s19["solo"]["totalNotes"], 320)
        self.assertEqual(s19["bass"]["totalNotes"], 320)
        self.assertEqual(s19["drums"]["totalNotes"], 640)
        mel = s19["solo"]["melody"]
        self.assertGreater(mel["files"], 0)
        self.assertGreater(mel["upShareMedian"], mel["downShareMedian"])

    def test_recompute_matches_artifact(self):
        # the engine is deterministic: re-running on one song yields same counts
        raw = (ROOT / "session19-benchmark" / "song19-01.mid").read_bytes()
        res = role_pattern_evidence(raw, source_name="song19-01.mid",
                                    role_map={0: "accompaniment", 1: "bass",
                                              2: "solo", 9: "drums"},
                                    melody_channels=(2,))
        self.assertEqual(res["channels"]["2"]["noteCount"], 16)


if __name__ == "__main__":
    unittest.main()
