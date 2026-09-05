"""Instrument Technique Evidence Engine 4.55 tests.

The engine is read-only: it classifies factory notes/strokes against the
role technique vocabulary of complete-instrument-profiles-4.44.json and never
emits triggers. Numbers below were produced by executed runs (2026-09-05).
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.instrument_techniques import (
    role_forbidden_triggers, role_technique_vocab,
    run_technique_evidence,
)

FIXTURE = ROOT / "artifacts-max-4.51" / "arranged-4.51-fixture.mid"
BASELINE = ROOT / "baseline" / "reference-style.mid"


def _artifact(role, ch, stem):
    p = ROOT / "artifacts-max-4.55" / f"techniques-{role}-ch{ch}-{stem}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


class VocabularyTests(unittest.TestCase):
    def test_bass_vocabulary_has_slap_family(self):
        v = role_technique_vocab("bass")
        for name in ("ghost-candidate", "slap-candidate", "pop-candidate",
                     "slide-candidate"):
            self.assertIn(name, v)
        self.assertNotIn("palm-mute-candidate", v)  # not a bass technique

    def test_rhythm_guitar_vocabulary_has_mute_family(self):
        v = role_technique_vocab("rhythm-guitar")
        for name in ("palm-mute-candidate", "mute-candidate",
                     "open-strum", "single-string-candidate"):
            self.assertIn(name, v)

    def test_forbidden_triggers_policy(self):
        fb = role_forbidden_triggers("bass")
        self.assertIn("slap-trigger", fb)
        self.assertIn("pop-trigger", fb)
        self.assertIn("rx-noise-trigger", fb)


class FixtureBassTests(unittest.TestCase):
    def test_counts(self):
        a = _artifact("bass", 8, "arranged-4.51-fixture")
        self.assertIsNotNone(a)
        c = a["counts"]["perLabel"]
        self.assertEqual(a["counts"]["total"], 122)
        self.assertEqual(c["slap-candidate"], 3)
        self.assertEqual(c["pop-candidate"], 25)
        self.assertEqual(c["ghost-candidate"], 24)

    def test_gates_no_emission(self):
        a = _artifact("bass", 8, "arranged-4.51-fixture")
        g = a["gates"]
        self.assertTrue(g["readOnlyNoInputBytesWritten"])
        for k in ("emittedTriggers", "emittedBankProgramChanges",
                  "emittedControllerEvents", "emittedKeyswitches",
                  "emittedNoteOnOff"):
            self.assertEqual(g[k], 0)

    def test_warrant_slap_pop_semantic_only(self):
        a = _artifact("bass", 8, "arranged-4.51-fixture")
        for w in a["warrantLedger"]:
            self.assertEqual(w["emissionState"], "SEMANTIC_ONLY")
            if w["technique"] == "slap-trigger":
                self.assertTrue(w["requiresDeviceEvidenceBeforeTrigger"])
        self.assertTrue(a["gates"]["roleKnown"])


class FixtureChordalTests(unittest.TestCase):
    def test_counts(self):
        a = _artifact("rhythm-guitar", 12, "arranged-4.51-fixture")
        self.assertIsNotNone(a)
        c = a["counts"]["perLabel"]
        self.assertEqual(a["counts"]["total"], 228)
        self.assertEqual(c["open-strum"], 24)
        self.assertEqual(c["palm-mute-candidate"], 7)
        self.assertEqual(c["single-string-candidate"], 53)

    def test_sound_evidence_recorded(self):
        a = _artifact("rhythm-guitar", 12, "arranged-4.51-fixture")
        self.assertTrue(a["channelSoundEvidence"])  # (121,3,0) observed


class ReferenceBassTests(unittest.TestCase):
    def test_counts(self):
        a = _artifact("bass", 8, "reference-style")
        self.assertIsNotNone(a)
        c = a["counts"]["perLabel"]
        self.assertEqual(a["counts"]["total"], 107)
        self.assertEqual(c["sustain"], 49)
        self.assertEqual(c["slap-candidate"], 5)
        self.assertEqual(c["pop-candidate"], 2)
        self.assertEqual(c["ghost-candidate"], 16)

    def test_no_notes_touched(self):
        a = _artifact("bass", 8, "reference-style")
        self.assertTrue(a["gates"]["readOnlyNoInputBytesWritten"])


class ReferenceChordalTests(unittest.TestCase):
    def test_counts(self):
        a = _artifact("rhythm-guitar", 12, "reference-style")
        self.assertIsNotNone(a)
        c = a["counts"]["perLabel"]
        self.assertEqual(a["counts"]["total"], 222)
        self.assertEqual(c["single-string-candidate"], 100)
        self.assertEqual(c["open-strum"], 25)
        self.assertEqual(c["palm-mute-candidate"], 9)

    def test_byte_identity_roundtrip(self):
        # read-only engine: raw input never written back anywhere
        a = _artifact("rhythm-guitar", 12, "reference-style")
        self.assertEqual(len(a["sourceSha256"]), 64)


if __name__ == "__main__":
    unittest.main()
