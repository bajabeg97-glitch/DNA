"""test_composer_engine_v470.py — dokazi miljokaza 4.70 (S2 deterministički kompozitor).

Proverava:
- 10 stilskih templejta × seed daju validne pesme (composer_validator 100%),
  strukturu sa >= 3 sekcije i >= 3 uloge;
- reproduktibilnost: isti seed -> identičan item/digest i identičan .mid fajl;
- scorecard: struktura, akordi po taktu == broj taktova, izbori bubnjeva su
  stvarni naslovi iz fabričke evidencije (build_library), digest konzistentan;
- SMF izvoz: bez humanizacije byte-tačan roundtrip; sa humanizacijom (4.54,
  sigma 27.96 ms) kvantizacija ostaje ista, a mikropomeraji su u granicama
  (<= 0.35 koraka) i stvarno postoje;
- summary artefakt: 30/30 pesama validno.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dna_midi_studio import composer_engine as ce  # noqa: E402
from dna_midi_studio import composer_corpus as cc  # noqa: E402
from dna_midi_studio import composer_validator as pv  # noqa: E402
import dna_midi_studio.midi as m  # noqa: E402
import dna_midi_studio.pattern_library as pl  # noqa: E402

SONGS = ROOT / "artifacts-max-4.70" / "songs-4.70"


class ComposeTest(unittest.TestCase):
    def test_all_templates_valid_and_structured(self):
        for style in ce.STYLES:
            for seed in (1, 2, 3):
                res = ce.compose(style, seed)
                it = res["item"]
                v = pv.validate_item(it)
                self.assertTrue(v["ok"], (style, seed, v["errors"][:3]))
                secs = it["sections"]
                self.assertGreaterEqual(len(secs), 3, style)
                bars = sum(s["bars"] for s in secs)
                maxb = max(e["b"] for tr in it["tracks"] for e in tr["events"]) + 1
                self.assertEqual(bars, maxb, style)
                self.assertGreaterEqual(len(it["tracks"]), 3, style)
                roles = {tr["role"] for tr in it["tracks"]}
                self.assertIn("drums", roles)
                self.assertIn("bass", roles)
                self.assertIn("acc", roles)
                self.assertEqual(it["originKind"], "synthetic")

    def test_reproducible_same_seed(self):
        a = ce.compose("rock", 7)
        b = ce.compose("rock", 7)
        self.assertEqual(a["item"], b["item"])
        self.assertEqual(a["scorecard"]["digest"], b["scorecard"]["digest"])

    def test_different_seed_differs(self):
        a = ce.compose("rock", 1)
        b = ce.compose("rock", 2)
        self.assertNotEqual(a["item"], b["item"])

    def test_scorecard_grounded(self):
        res = ce.compose("funk", 1)
        card = res["scorecard"]
        it = res["item"]
        bars = sum(s["bars"] for s in it["sections"])
        self.assertEqual(len(card["chordsPerBar"]), bars)
        self.assertEqual(card["validation"]["ok"], True)
        titles = {p["title"] for p in pl.build_library()}
        for ch in card["drums"]["choices"]:
            self.assertIn(ch["title"], titles, ch)
            self.assertIn(ch["source"], ("Patterns_200", "Patterns_260"))
        self.assertIn("27.96", card["humanization"]["evidence"])
        # digest mora odgovarati itemu
        from dna_midi_studio.composer_tokens import item_digest
        self.assertEqual(card["digest"], item_digest(it))


class ExportTest(unittest.TestCase):
    def _tmp(self):
        return Path(tempfile.mkdtemp(prefix="dna-s2-"))

    def test_export_exact_without_humanize(self):
        res = ce.compose("waltz", 2)
        p = self._tmp() / "w.mid"
        cc.export_smf(res["item"], p, humanize=False)
        self.assertTrue(cc.smf_matches_item(p, res["item"]))

    def test_humanize_keeps_grid_and_bounds(self):
        res = ce.compose("rock", 1)
        it = res["item"]
        p = self._tmp() / "r.mid"
        cc.export_smf(it, p, humanize=True, seed=1)
        num, den = it["signature"]
        steps = pv.steps_per_bar([num, den])
        f = m.MidiFile.from_bytes(p.read_bytes())
        bar_ticks = f.ppq * 4.0 * num / den
        t_step = bar_ticks / steps
        bound = round(0.35 * t_step) + 2
        deltas = []
        for n in f.notes():
            b = int(n.start / bar_ticks)
            grid = b * bar_ticks
            st = (n.start - grid) / t_step
            self.assertGreaterEqual(st, -0.01)
            self.assertLess(st, steps + 0.01)
            st_i = int(round(st))
            self.assertGreaterEqual(st_i, 0)
            self.assertLess(st_i, steps)
            delta = (n.start - grid) - st_i * t_step
            deltas.append(abs(delta))
        self.assertTrue(deltas)
        self.assertLessEqual(max(deltas), bound)
        self.assertGreater(max(deltas), 0)  # humanizacija stvarno postoji

    def test_export_bytes_reproducible(self):
        res = ce.compose("dance90", 3)
        a = self._tmp() / "a.mid"
        b = self._tmp() / "b.mid"
        cc.export_smf(res["item"], a, humanize=True, seed=3)
        cc.export_smf(res["item"], b, humanize=True, seed=3)
        self.assertEqual(a.read_bytes(), b.read_bytes())


class ArtifactTest(unittest.TestCase):
    def test_summary_30_songs_valid(self):
        summary = json.loads((SONGS / "songs-4.70-summary.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(summary["songs"], 30)
        self.assertEqual(summary["valid"], 30)
        self.assertEqual(len(summary["styles"]), 10)
        self.assertEqual(summary["failures"], [])
        self.assertGreaterEqual(summary["totalBars"], 500)

    def test_scorecards_and_mids_exist(self):
        mids = list(SONGS.glob("*.mid"))
        cards = list(SONGS.glob("*.scorecard.json"))
        self.assertEqual(len(mids), 30)
        self.assertEqual(len(cards), 30)
        for card_path in cards[:5]:
            card = json.loads(card_path.read_text(encoding="utf-8"))
            self.assertTrue(card["validation"]["ok"])
            self.assertTrue((SONGS / card["files"]["mid"]).exists())

    def test_listenable_probes_selected(self):
        # pet reprezentativnih proba za ljudsko slušanje (D2a)
        for name in ("song-rock-s1.mid", "song-funk-s2.mid", "song-ballad-s3.mid",
                     "song-waltz-s1.mid", "song-latin12-s2.mid"):
            self.assertTrue((SONGS / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
