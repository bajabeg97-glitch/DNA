"""Studio Flow 4.50 tests — contract, planner, metrics, e2e (torch-free)."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dna_midi_studio.arranger_contract import (  # noqa: E402
    ACC_CHANNELS, chord_pc_set, factory_velocity, polyphony_limit,
)
from dna_midi_studio.arranger_planner import plan_regions  # noqa: E402
from dna_midi_studio.studio_flow import arrangement_metrics, run_studio  # noqa: E402
from dna_midi_studio.pa800_validator import PA800_CHANNEL_POLYPHONY_LIMITS  # noqa: E402

FIXTURE = ROOT / "session35-partial-preview.mid"


class ContractTests(unittest.TestCase):
    def test_channel_map_matches_pa800_limits(self):
        for ch in (8, 9, 10, *ACC_CHANNELS):
            self.assertIn(ch, PA800_CHANNEL_POLYPHONY_LIMITS)
            self.assertGreaterEqual(polyphony_limit(ch), 1)

    def test_chord_pc_set_aliases_and_symbol_fallback(self):
        # analyzer vocabulary aliases
        self.assertEqual(chord_pc_set({"root": 0, "quality": "major-seventh"}), {0, 4, 7, 11})
        self.assertEqual(chord_pc_set({"root": 2, "quality": "add-nine"}), {(2 + i) % 12 for i in (0, 4, 7, 14)})
        # symbol fallback (quality absent)
        self.assertEqual(chord_pc_set({"root": 5, "symbol": "Fadd9/A", "quality": ""}),
                         {(5 + i) % 12 for i in (0, 4, 7, 14)})
        self.assertEqual(chord_pc_set({"root": 8, "symbol": "G#m7b5", "quality": ""}),
                         {(8 + i) % 12 for i in (0, 3, 6, 10)})
        self.assertIsNone(chord_pc_set({"root": 0, "symbol": "?", "quality": "??"}))

    def test_factory_velocity_is_bounded_and_factory_only(self):
        from dna_midi_studio.midi import MidiFile
        midi = MidiFile.from_bytes(FIXTURE.read_bytes())
        v = factory_velocity(midi, 8, 0, 40, "bass")
        self.assertTrue(1 <= v["velocity"] <= 127)
        self.assertEqual(v["authority"], "FACTORY_ONLY")
        self.assertIn(v["curvePoint"], {"strong", "highMid", "optimal"})


class PlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(self):
        self.plan = plan_regions(FIXTURE.read_bytes())

    def test_planner_reports_all_style_channels(self):
        expected = {"8", "9", "10", "11", "12", "13", "14", "15"}
        self.assertEqual(set(self.plan["regions"]), expected)

    def test_empty_guitar_slot_is_fill_candidate(self):
        self.assertIn(15, self.plan["fillCandidates"])
        self.assertEqual(self.plan["regions"]["15"]["decision"], "FILL_EMPTY")
        self.assertEqual(self.plan["regions"]["15"]["budget"], PA800_CHANNEL_POLYPHONY_LIMITS[15])

    def test_populated_channels_have_fit_scores(self):
        for ch in ("8", "9", "11", "12", "13", "14"):
            fit = self.plan["regions"][ch]["fit"]
            self.assertIsNotNone(fit)
            for key in ("registerFit", "densityFit", "dynamicsFit", "gateFit"):
                self.assertTrue(0.0 <= fit[key] <= 1.0, f"ch{ch} {key}")

    def test_fill_priority_order(self):
        self.assertEqual(self.plan["fillPriority"], [15, 14, 13, 12, 11])
        self.assertEqual(self.plan["fillCandidates"], [15])  # only ch15 empty in fixture


class StudioFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(self):
        self.raw = FIXTURE.read_bytes()
        with tempfile.TemporaryDirectory() as td:
            self.res = run_studio(self.raw, out_dir=td)
            self.td = td

    def test_run_executes_and_gates_pass(self):
        self.assertTrue(self.res["execution"]["executed"])
        self.assertEqual(self.res["execution"]["channel"], 15)
        self.assertTrue(all(self.res["gates"].values()), self.res["gates"])
        self.assertEqual(self.res["status"], "STUDIO_RUN_COMPLETE")

    def test_metrics_full_coverage_and_register(self):
        m = self.res["metrics"]["perChannel"]["15"]
        self.assertEqual(m["addedNotes"], 410)
        self.assertEqual(m["cellsWithoutChordQuality"], 0)
        self.assertEqual(m["chordToneChecked"], 410)
        self.assertTrue(0.5 <= m["chordToneRatio"] <= 1.0)
        self.assertEqual(m["register"], [48, 72])
        self.assertEqual(m["outOfStrumRegister"], 0)
        self.assertTrue(all(self.res["metrics"]["headroomAfter"].values()))

    def test_metrics_are_raw_based(self):
        m = arrangement_metrics(self.raw, self.raw)
        self.assertEqual(m["addedNotesTotal"], 0)
        self.assertEqual(m["removedNotesTotal"], 0)

    def test_studio_refuses_busy_channel(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                run_studio(self.raw, target_channel=8, out_dir=td)


if __name__ == "__main__":
    unittest.main()
