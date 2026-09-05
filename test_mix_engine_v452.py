"""Mix Engineer 4.52 tests — gain staging via CC11, gates, facts.

Runs against real repo files (fixture + reference style), stdlib only.
Evidence lives in artifacts-max-4.52/*.json; this file proves behaviour.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.midi import MidiFile
from dna_midi_studio.mix_engine import (
    CC11_EXPRESSION, CC7_VOLUME,
    apply_gain_plan, channel_facts, masking_windows,
    percussion_accents_on_drums, plan_cc_gain, run_mix_gain,
)

FIXTURE = ROOT / "artifacts-max-4.51" / "arranged-4.51-fixture.mid"
BASELINE = ROOT / "baseline" / "reference-style.mid"


class GainPlanTests(unittest.TestCase):
    def setUp(self):
        self.raw_fix = FIXTURE.read_bytes()
        self.raw_base = BASELINE.read_bytes()

    def test_fixture_plan_40_events_on_pads_and_perc(self):
        plan, summary = plan_cc_gain(self.raw_fix, {11: 0.55, 13: 0.55, 14: 0.55, 10: 0.55})
        self.assertEqual(summary["eventsPlanned"], 40)  # 4 channels x 10 bar events
        self.assertEqual(summary["channelsMissingCC"], [])
        channels = {p["channel"] for p in plan}
        self.assertEqual(channels, {10, 11, 13, 14})
        self.assertTrue(all(p["oldValue"] == 127 and p["newValue"] == 70
                            for p in plan))
        self.assertTrue(all(p["controller"] == CC11_EXPRESSION for p in plan))

    def test_baseline_plan_30_events(self):
        plan, summary = plan_cc_gain(self.raw_base, {13: 0.55, 14: 0.55, 10: 0.55})
        self.assertEqual(summary["eventsPlanned"], 30)
        self.assertEqual({p["channel"] for p in plan}, {10, 13, 14})

    def test_no_targets_means_no_plan(self):
        plan, summary = plan_cc_gain(self.raw_fix, {})
        self.assertEqual(plan, [])
        res = run_mix_gain(self.raw_fix, targets={})
        self.assertEqual(res["status"], "MIX_NO_CHANGE_NEEDED")

    def test_non_target_channels_untouched_and_cc7_untouched(self):
        raw = self.raw_fix
        plan, _ = plan_cc_gain(raw, {11: 0.55, 10: 0.55})
        after = apply_gain_plan(raw, plan)
        mb, ma = MidiFile.from_bytes(raw), MidiFile.from_bytes(after)

        def cc(m, controller):
            out = []
            for t in m.tracks:
                for e in t.events:
                    if e.kind == "channel" and e.status is not None and \
                            (e.status >> 4) == 0xB and len(e.data) == 2 and \
                            e.data[0] == controller:
                        out.append((e.tick, e.status & 0x0F, e.data[1]))
            return sorted(out)

        before11, after11 = cc(mb, CC11_EXPRESSION), cc(ma, CC11_EXPRESSION)
        for (tick, ch, val) in before11:
            if ch in (10, 11):
                self.assertEqual(val, 127)
        self.assertEqual(cc(mb, CC7_VOLUME), cc(ma, CC7_VOLUME))  # CC7 identical
        changed = [(x, y) for x, y in zip(before11, after11) if x != y]
        self.assertTrue(changed)
        self.assertTrue(all(x[1] in (10, 11) and y[2] == 70 for x, y in changed))


class GatesTests(unittest.TestCase):
    def setUp(self):
        self.raw_fix = FIXTURE.read_bytes()
        self.raw_base = BASELINE.read_bytes()

    def test_fixture_gates_all_true(self):
        res = run_mix_gain(self.raw_fix, targets={11: 0.55, 13: 0.55, 14: 0.55, 10: 0.55})
        self.assertEqual(res["status"], "MIX_APPLIED")
        self.assertTrue(all(res["gates"].values()), res["gates"])
        self.assertEqual(len(res["plan"]), 40)

    def test_baseline_gates_all_true(self):
        res = run_mix_gain(self.raw_base, targets={13: 0.55, 14: 0.55, 10: 0.55})
        self.assertEqual(res["status"], "MIX_APPLIED")
        self.assertTrue(all(res["gates"].values()), res["gates"])
        self.assertEqual(len(res["plan"]), 30)

    def test_velocity_change_would_break_velocity_gate(self):
        # simulate tampering: one note velocity changed in "after"
        import dataclasses
        raw = self.raw_fix
        midi = MidiFile.from_bytes(raw)
        note = midi.notes()[0]
        # find and lower velocity event on that note
        for t in midi.tracks:
            new_events = []
            for e in t.events:
                if e.tick == note.start and e.status == 0x90 | note.channel and \
                        len(e.data) == 2 and e.data[0] == note.pitch:
                    e = dataclasses.replace(e, data=bytes((e.data[0], 60)))
                new_events.append(e)
            t.events = new_events
        tampered = midi.to_bytes()
        plan, _ = plan_cc_gain(raw, {10: 0.55})
        from dna_midi_studio.mix_engine import mix_gates
        gates = mix_gates(raw, apply_gain_plan(tampered, plan), plan, {10: 0.55})
        self.assertFalse(gates["velocitiesUntouched"])


class FactsTests(unittest.TestCase):
    def test_channel_facts_measured(self):
        facts = channel_facts(FIXTURE.read_bytes())
        self.assertEqual(facts[8]["role"], "bass")
        self.assertEqual(facts[15]["noteCount"], 372)
        self.assertEqual(facts[15]["register"], [48, 72])
        self.assertEqual(facts[9]["role"], "drums")
        self.assertEqual(facts[10]["role"], "percussion")
        # measured sounds (fixture): ch13 strings pad, ch14 second pad voice
        self.assertEqual(facts[13]["sound"], (120, 0, 64))
        self.assertEqual(facts[14]["sound"], (120, 0, 89))

    def test_masking_windows_fixture(self):
        m = masking_windows(FIXTURE.read_bytes())
        self.assertEqual(m["busiestPair"], "11-15")
        self.assertGreater(m["pairs"]["11-15"], 800)
        self.assertGreater(m["totalCollidingChannelNotes"], 6000)

    def test_masking_windows_baseline(self):
        m = masking_windows(BASELINE.read_bytes())
        self.assertEqual(m["busiestPair"], "11-12")
        self.assertGreater(m["pairs"]["9-10"], 400)  # drum <-> percussion interleave

    def test_percussion_accent_report(self):
        base = percussion_accents_on_drums(BASELINE.read_bytes())
        self.assertGreater(base["accentNoteCount"], 0)
        self.assertFalse(base["applied"])  # report-only policy
        fix = percussion_accents_on_drums(FIXTURE.read_bytes())
        self.assertGreater(fix["accentNoteCount"], 0)

    def test_fixture_run_json_artifact(self):
        p = ROOT / "artifacts-max-4.52" / "mix-run-session35-arranged.json"
        if not p.exists():
            self.skipTest("artifact not generated yet")
        res = json.loads(p.read_text())
        self.assertEqual(res["status"], "MIX_APPLIED")
        self.assertTrue(all(res["gates"].values()))


if __name__ == "__main__":
    unittest.main()
