"""Korg STY Mapper 4.53 tests — markers, style map, export, gates.

Stdlib only. mido cross-check class skips when mido is absent (dev-only).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.midi import MidiFile
from dna_midi_studio.sty_mapper import (
    export_korg_style, marker_text, normalize_marker, parse_markers,
    role_map, run_export, section_map, structure_gates,
)

GOLD = ROOT / "baseline" / "reference-style.mid"
FIXTURE = ROOT / "artifacts-max-4.51" / "arranged-4.51-fixture.mid"

GOLD_MARKERS = ["i1cv1", "i2cv1", "v1cv1", "v2cv1", "v3cv1", "v4cv1",
                "f1cv1", "f2cv1", "e1cv1", "e2cv1"]


class MarkerTests(unittest.TestCase):
    def test_parse_gold_markers(self):
        marks = parse_markers(GOLD.read_bytes())
        self.assertEqual([m["text"] for m in marks], GOLD_MARKERS)
        self.assertEqual(marks[0]["tick"], 0)

    def test_normalize_forms(self):
        self.assertEqual(normalize_marker("i1cv1"), ("intro", 1, 1))
        self.assertEqual(normalize_marker("v4cv1"), ("variation", 4, 1))
        self.assertEqual(normalize_marker("F2CV1"), ("fill", 2, 1))
        self.assertEqual(normalize_marker("e2cv1"), ("ending", 2, 1))
        self.assertEqual(normalize_marker("Intro 1 CV1"), ("intro", 1, 1))
        self.assertEqual(normalize_marker("variation_2_cv_1"), ("variation", 2, 1))
        self.assertIsNone(normalize_marker("not-a-korg-label"))

    def test_marker_text_roundtrip(self):
        for text in GOLD_MARKERS:
            kind, num, cv = normalize_marker(text)
            self.assertEqual(marker_text(kind, num, cv), text)


class StyleMapTests(unittest.TestCase):
    def test_gold_section_map(self):
        sm = section_map(GOLD.read_bytes())
        self.assertEqual(sm["ppq"], 480)
        self.assertEqual(sm["ticksPerBar"], 1920)
        els = sm["elements"]
        self.assertEqual(len(els), 10)
        kinds = [(e["kind"], e["number"]) for e in els]
        self.assertEqual(kinds, [("intro", 1), ("intro", 2)] +
                         [("variation", i) for i in range(1, 5)] +
                         [("fill", 1), ("fill", 2), ("ending", 1), ("ending", 2)])
        self.assertEqual(els[0]["startTick"], 0)
        self.assertEqual(els[-1]["startTick"], 34560)
        # every gold element must have channel content mapped
        for e in els:
            self.assertTrue(e["channels"], e["text"])

    def test_fixture_section_map_bars(self):
        sm = section_map(FIXTURE.read_bytes())
        bars = [e["lengthBars"] for e in sm["elements"]]
        self.assertEqual(bars, [2, 2, 2, 2, 2, 2, 1, 1, 2, 2])
        self.assertEqual(sm["totalBars"], 18)

    def test_role_map_korg_channels(self):
        rm = role_map(FIXTURE.read_bytes())
        by = {r["channel"]: r for r in rm}
        self.assertEqual(by[8]["role"], "bass")
        self.assertEqual(by[9]["role"], "drums")
        self.assertEqual(by[10]["role"], "percussion")
        self.assertTrue(all(by[c]["role"] == "accompaniment" for c in (11, 12, 13, 14, 15)))
        self.assertEqual(by[15]["suggestedKorgChannel"], 15)


class ExportTests(unittest.TestCase):
    def test_gold_export_adds_nothing(self):
        raw = GOLD.read_bytes()
        out, report = export_korg_style(raw)
        self.assertEqual(report["addedTotal"], 0)  # gold is already conformant
        gates = structure_gates(raw, out)
        self.assertTrue(all(gates.values()), gates)

    def test_fixture_export_adds_cc11_for_ch15_only(self):
        raw = FIXTURE.read_bytes()
        out, report = export_korg_style(raw)
        self.assertEqual(len(report["cc11Added"]), 10)  # ch15 lacks CC11 in 10 elements
        self.assertTrue(all(a["channel"] == 15 for a in report["cc11Added"]))
        self.assertEqual(len(report["setupsAdded"]), 0)
        skipped = report["channelsWithoutSetup"]
        self.assertTrue(all(s["channel"] == 15 for s in skipped))
        gates = structure_gates(raw, out)
        self.assertTrue(all(gates.values()), gates)

    def test_layout_export_recreates_gold_markers(self):
        raw = GOLD.read_bytes()
        # strip all markers -> plain SMF arrangement
        midi = MidiFile.from_bytes(raw)
        midi.tracks[0].events = [e for e in midi.tracks[0].events
                                 if not (e.kind == "meta" and e.meta_type == 0x06)]
        plain = midi.to_bytes()
        self.assertEqual(parse_markers(plain), [])
        layout = [{"kind": "intro", "number": 1, "cv": 1, "bars": 2},
                  {"kind": "intro", "number": 2, "cv": 1, "bars": 4}]
        layout += [{"kind": "variation", "number": n, "cv": 1, "bars": 2}
                   for n in range(1, 5)]
        layout += [{"kind": "fill", "number": 1, "cv": 1, "bars": 1},
                   {"kind": "fill", "number": 2, "cv": 1, "bars": 1},
                   {"kind": "ending", "number": 1, "cv": 1, "bars": 2},
                   {"kind": "ending", "number": 2, "cv": 1, "bars": 2}]
        out, report = export_korg_style(plain, layout=layout)
        marks = parse_markers(out)
        self.assertEqual([m["text"] for m in marks], GOLD_MARKERS)
        self.assertTrue(report["markersAdded"])
        gates = structure_gates(plain, out, layout=layout)
        self.assertTrue(all(gates.values()), gates)

    def test_gold_run_artifact_status(self):
        p = ROOT / "artifacts-max-4.53" / "sty-run-reference-style.json"
        if not p.exists():
            self.skipTest("artifact not generated yet")
        import json
        res = json.loads(p.read_text())
        self.assertEqual(res["status"], "STY_EXPORT_OK")
        self.assertTrue(all(res["gates"].values()))
        self.assertEqual(len(res["styleMap"]["elements"]), 10)


try:
    import mido  # noqa: F401
    HAVE_MIDO = True
except Exception:
    HAVE_MIDO = False


@unittest.skipUnless(HAVE_MIDO, "mido not importable (dev-only verifier)")
class MidoStyTests(unittest.TestCase):
    def test_mido_sees_exported_markers_and_notes(self):
        for src, out_name in ((GOLD, "korg-style-reference-style.mid"),
                              (FIXTURE, "korg-style-arranged-4.51-fixture.mid")):
            out = ROOT / "artifacts-max-4.53" / out_name
            self.assertTrue(out.exists(), out_name)
            mm = mido.MidiFile(str(out))
            marks = [m.text for t in mm.tracks for m in t
                     if m.type == "marker"]
            self.assertGreaterEqual(len(marks), 10)
            from collections import Counter
            ours = Counter(n.channel for n in
                           MidiFile.from_bytes(src.read_bytes()).notes())
            theirs = Counter(m.channel for t in mm.tracks for m in t
                             if m.type == "note_on" and m.velocity > 0)
            self.assertEqual(dict(ours), dict(theirs), out_name)


if __name__ == "__main__":
    unittest.main()
