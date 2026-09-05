"""Groove Engine 4.54 tests — measured factory groove + human reference.

Stdlib only. All numbers below were produced by executed runs on the real
repo files (2026-09-05); the tests lock the measurement to the facts.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.groove_engine import (
    compare_factory_to_human, drum_groove_stats, load_human_summary,
    run_groove_evidence,
)

FIXTURE = ROOT / "artifacts-max-4.51" / "arranged-4.51-fixture.mid"
BASELINE = ROOT / "baseline" / "reference-style.mid"
HUMAN = ROOT / "baseline" / "human-groove-rock-derived.json"


class HumanReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = json.loads(HUMAN.read_text(encoding="utf-8"))

    def test_provenance_and_scale(self):
        self.assertEqual(self.h["schema"], "dna-human-groove-derived-summary-v2")
        self.assertIn("sourceSha256", self.h)
        self.assertEqual(len(self.h["sourceSha256"]), 64)

    def test_overall_human_timing_spread(self):
        t = self.h["overallTimingMs"]
        self.assertEqual(t["n"], 110313)
        self.assertAlmostEqual(t["stdMs"], 27.96, delta=0.1)
        self.assertGreater(t["p95Ms"], 40)   # humans deviate up to ~+-47 ms
        self.assertLess(t["p5Ms"], -40)

    def test_instruments_present(self):
        inst = self.h["perInstrumentGlobal"]
        for name in ("kick", "snare", "hihat_closed", "ride", "crash", "tom_low"):
            self.assertIn(name, inst)
            self.assertGreater(inst[name]["n"], 1000)

    def test_position_means(self):
        kick = self.h["kickMeanOffsetMsBy16thPosition"]
        snare = self.h["snareMeanOffsetMsBy16thPosition"]
        self.assertEqual(len(kick), 16)
        self.assertEqual(len(snare), 16)


class MeasuredGrooveTests(unittest.TestCase):
    def test_fixture_drums_measured(self):
        s = drum_groove_stats(FIXTURE.read_bytes(), 9)
        self.assertEqual(s["noteCount"], 308)
        self.assertEqual(s["gridSixteenthTicks"], 120)
        self.assertEqual(s["bpm"], 120.0)
        self.assertAlmostEqual(s["densityNotesPerBar"], 17.1, delta=1.0)
        t = s["timingMs"]
        self.assertEqual(t["p50Ms"], 0.0)
        self.assertLess(t["stdMs"], 10.0)
        self.assertGreater(t["exactOnGridShare"], 0.1)
        pp = s["perPitch"]
        self.assertEqual(pp["36"]["count"], 72)
        self.assertEqual(pp["38"]["count"], 88)
        self.assertAlmostEqual(pp["38"]["velMean"], 118.3, delta=0.5)

    def test_reference_drums_mostly_quantized(self):
        s = drum_groove_stats(BASELINE.read_bytes(), 9)
        self.assertEqual(s["noteCount"], 263)
        self.assertGreater(s["timingMs"]["exactOnGridShare"], 0.9)  # factory quantized

    def test_reference_percussion_has_human_scale_spread(self):
        s = drum_groove_stats(BASELINE.read_bytes(), 10)
        self.assertEqual(s["noteCount"], 589)
        self.assertGreater(s["timingMs"]["stdMs"], 15.0)

    def test_comparison_table(self):
        h = load_human_summary()
        factory = drum_groove_stats(BASELINE.read_bytes(), 9)
        c = compare_factory_to_human(factory, h)
        self.assertLess(c["factoryTimingStdMs"], c["humanReferenceTimingStdMs"])
        self.assertEqual(c["humanSamples"], 110313)

    def test_evidence_artifact(self):
        p = ROOT / "artifacts-max-4.54" / "groove-ch9-arranged-4.51-fixture.json"
        if not p.exists():
            self.skipTest("artifact not generated yet")
        res = json.loads(p.read_text())
        self.assertEqual(res["factoryMeasured"]["noteCount"], 308)
        self.assertIn("comparison", res)


if __name__ == "__main__":
    unittest.main()
