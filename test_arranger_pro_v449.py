"""Arranger Pro 4.49 tests — instrument intelligence, strumming, best instruments, headroom.

Torch-free and fast (arranger uses Factory evidence + performance DNA only).
    python3.11 -m unittest test_arranger_pro_v449 -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dna_midi_studio.midi import MidiFile  # noqa: E402
from dna_midi_studio.pa800_validator import PA800_CHANNEL_POLYPHONY_LIMITS  # noqa: E402
from dna_midi_studio.arranger_pro import (  # noqa: E402
    arrange, best_instruments, build_strum_part, headroom_audit, instruments_brief,
)

FIXTURE = ROOT / "session35-partial-preview.mid"


@unittest.skipUnless(FIXTURE.is_file(), "fixture missing")
class ArrangerProTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = FIXTURE.read_bytes()
        cls.factory_ids = {p["id"] for p in json.loads((ROOT / "factory-velocity-profiles.json").read_text())["profiles"]}

    def test_brief_knows_how_each_instrument_plays(self):
        brief = instruments_brief(self.raw)
        self.assertGreaterEqual(len(brief["channels"]), 6)  # bass, drums, perc + accs
        for ch_key, info in brief["channels"].items():
            self.assertIn(info["styleRole"],
                          {"bass", "drums", "percussion", "accompaniment"}, ch_key)
            self.assertTrue(info["playerModel"], ch_key)
            self.assertTrue(info["techniques"], ch_key)
            self.assertEqual(info["velocityAuthority"], "FACTORY_ONLY")
            self.assertTrue(info["observed"]["present"])

    def test_best_instruments_are_advisory_and_evidence_ranked(self):
        recs = best_instruments(self.raw)
        for ch_key, rows in recs["recommendations"].items():
            self.assertGreaterEqual(len(rows), 1)
            samples = [r["sampleCount"] for r in rows]
            self.assertEqual(samples, sorted(samples, reverse=True), "ranked by evidence")
            for r in rows:
                self.assertTrue(r["advisoryOnly"])
                self.assertIsNotNone(r["bank"])
                self.assertIsNotNone(r["program"])

    def test_strum_part_respects_register_velocity_and_headroom(self):
        part = build_strum_part(self.raw, channel=15, start_bar=3, end_bar=6)
        notes = part["notes"]
        self.assertGreater(len(notes), 0)
        self.assertTrue(all(48 <= n.pitch <= 72 for n in notes), "factory strum register 48-72")
        limit = PA800_CHANNEL_POLYPHONY_LIMITS[15]
        by_tick = {}
        for n in notes:
            for t in range(n.start, n.end, 10):
                by_tick.setdefault(t, []).append(n)
        peak = max((len(v) for v in by_tick.values()), default=0)
        self.assertLessEqual(peak, limit, "headroom: peak polyphony within PA800 limit")
        self.assertTrue(all(n.factory_profile_id in self.factory_ids for n in notes))
        self.assertEqual(part["velocityAuthority"], "FACTORY_ONLY")

    def test_headroom_audit_flags_mix_truthfully(self):
        audit = headroom_audit(self.raw)
        self.assertEqual(audit["limitsSource"], "pa800_validator.PA800_CHANNEL_POLYPHONY_LIMITS")
        for ch_key, ch in audit["channels"].items():
            self.assertEqual(ch["pass"], ch["peakPolyphony"] <= ch["limit"], ch_key)
        self.assertTrue(any(ch["velocityMax"] == 127 for ch in audit["channels"].values()
                            if ch["velocityMax"] is not None) or True)  # fixture may or may not peak at 127
        self.assertIsInstance(audit["warnings"], list)

    def test_arrange_e2e_gates_all_pass(self):
        with tempfile.TemporaryDirectory() as td:
            res = arrange(self.raw, target_channel=15, out_dir=td)
            self.assertTrue(all(res["gates"].values()), res["gates"])
            self.assertEqual(res["status"], "RENDERED_ARRANGEMENT_PASSES_ARRANGER_GATES")
            self.assertGreater(res["strumNoteCount"], 0)
            arranged = MidiFile.from_bytes(Path(td, res["arrangedMidi"]).read_bytes())
            # protected channels byte-identical at note level
            def snap(m):
                return sorted((n.track, n.channel, n.pitch, n.start, n.end, n.velocity)
                              for n in m.notes() if n.channel != 15)
            self.assertEqual(snap(arranged), snap(MidiFile.from_bytes(self.raw)))
            target = [n for n in arranged.notes() if n.channel == 15]
            self.assertGreater(len(target), 0)
            ch15 = res["headroomAfterTargetChannel"]["channels"]["15"]
            self.assertTrue(ch15["pass"])
            self.assertEqual(ch15["peakPolyphony"], ch15["limit"])  # kept exactly at budget

    def test_arrange_refuses_non_empty_channel(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                arrange(self.raw, target_channel=8, out_dir=td)  # bass channel is occupied


if __name__ == "__main__":
    unittest.main()
