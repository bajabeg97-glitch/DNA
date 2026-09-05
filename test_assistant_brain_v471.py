"""test_assistant_brain_v471.py — milestone 4.71 (NLU v2 + compose tool).

Since 4.70 the deterministic composer exists, so generation requests are no
longer refused: the brain recognizes composition intents, extracts style +
seed, and returns a {type:"composeSong"} tool the bridge executes (writer of
the .mid + scorecard into workspace-4.71). Subjective listening questions
stay refused (no listening model). This suite is the 4.71 golden extension
of the 4.68 eval harness (test_assistant_brain_v468.py).

Verifies:
- NLU: compose_request with/without style; seed extraction (seed/seme/#);
  analyze/apply are NOT hijacked by compose words; thanks intent; unknown
  fallback text now teaches concrete examples;
- refusal scope narrowed to subjective listening claims;
- answer(): guide reply mentions the Kompozitor panel token [tab:compose];
  compose_request with style returns the composeSong tool payload.
"""

import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dna_midi_studio.assistant_brain import answer, understand  # noqa: E402


class NluV471Test(unittest.TestCase):
    def cases(self):
        return [
            # (text, intent, style or None, seed or None)
            ("napravi rock pesmu", "compose_request", "rock", None),
            ("generiši mi pesmu", "compose_request", None, None),
            ("komponuj ballad sa seed 7", "compose_request", "ballad", 7),
            ("generiši latin12 sa seed 42", "compose_request", "latin12", 42),
            ("skladaj funk sa semenom 3", "compose_request", "funk", 3),
            ("hoću da napišeš jednu numu", "compose_request", None, None),
            ("pravi mi pop pesmicu", "compose_request", "pop", None),
            ("daj mi dance90 pesmu #9", "compose_request", "dance90", 9),
            ("kako zvuči bolje posle primene?", "refuse_music_claim", None, None),
            ("da li je lepše sada?", "refuse_music_claim", None, None),
            ("hvala puno", "thanks", None, None),
            ("odlično, bravo", "thanks", None, None),
            ("napravi analizu reference-style", "analyze", None, None),
            ("primeni A01 A02", "apply", None, None),
            ("šta znači LOCKED", "explain", None, None),
        ]

    def test_compose_intents_and_params(self):
        for text, expected, style, seed in self.cases():
            u = understand(text)
            self.assertEqual(u["intent"], expected, text)
            if style is not None:
                self.assertEqual(u["params"]["style"], style, text)
            if seed is not None:
                self.assertEqual(u["params"]["seed"], seed, text)

    def test_unknown_still_unknown_but_reply_teaches(self):
        u = understand("bla bla nešto sasvim nasumično")
        self.assertEqual(u["intent"], "unknown")
        out = answer("bla bla nešto sasvim nasumično", None, [])
        self.assertEqual(out["intent"], "unknown")
        self.assertIn("napravi rock pesmu", out["reply"])
        self.assertIn("[tab:compose]", out["reply"])
        self.assertNotIn("Mogu: „analiziraj X”, „objasni A05”", out["reply"])

    def test_compose_tool_payload(self):
        out = answer("napravi rock pesmu sa seed 7", None, [])
        self.assertEqual(out["intent"], "compose_request")
        self.assertEqual(out["tool"], {"type": "composeSong", "style": "rock", "seed": 7})
        self.assertEqual(out["claims"], [])

    def test_compose_guide_without_style(self):
        out = answer("generiši mi pesmu", None, [])
        self.assertEqual(out["intent"], "compose_request")
        self.assertIsNone(out["tool"])
        self.assertIn("rock", out["reply"])
        self.assertIn("latin12", out["reply"])

    def test_help_mentions_compose(self):
        out = answer("pomoć", None, [])
        self.assertEqual(out["intent"], "help")
        self.assertIn("napravi rock pesmu", out["reply"])

    def test_refuse_still_honest(self):
        out = answer("kako zvuči bolje posle primene?", None, [])
        self.assertEqual(out["intent"], "refuse_music_claim")
        self.assertIn("ne mogu", out["reply"])

    def test_thanks_reply(self):
        out = answer("hvala puno", None, [])
        self.assertEqual(out["intent"], "thanks")
        self.assertIn("Nema na čemu", out["reply"])


if __name__ == "__main__":
    unittest.main()
