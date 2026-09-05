"""Independent-verifier tests (mido) for Mix Engineer 4.52 outputs.

mido is an independent, MIT-licensed MIDI implementation.  It is a DEV-ONLY
dependency: these tests skip when mido is not importable (stdlib test runs stay
green without it).  When present, it must agree with our parser on every byte
stream produced by the mix engine.

Run with mido available, e.g.:
    PYTHONPATH=/path/to/mido-install python3.11 -m unittest test_mido_independent_v452 -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

try:
    import mido  # noqa: F401
    HAVE_MIDO = True
except Exception:  # pragma: no cover
    HAVE_MIDO = False

from dna_midi_studio.midi import MidiFile  # noqa: E402


@unittest.skipUnless(HAVE_MIDO, "mido not importable (dev-only verifier)")
class MidoCrossCheckTests(unittest.TestCase):
    """Cross-implementation agreement: our parser vs mido on the same bytes."""

    def test_mido_agrees_on_source_files(self):
        from collections import Counter
        for path in (ROOT / "artifacts-max-4.51" / "arranged-4.51-fixture.mid",
                     ROOT / "baseline" / "reference-style.mid"):
            ours = Counter(n.channel for n in MidiFile.from_bytes(path.read_bytes()).notes())
            mm = mido.MidiFile(str(path))
            theirs = Counter(m.channel for t in mm.tracks for m in t
                             if m.type == "note_on" and m.velocity > 0)
            self.assertEqual(dict(ours), dict(theirs), str(path))

    def test_mido_agrees_on_mix_outputs_and_cc_values(self):
        from collections import Counter
        for path in (ROOT / "artifacts-max-4.52" / "mix-arranged-session35-arranged.mid",
                     ROOT / "artifacts-max-4.52" / "mix-arranged-reference-style.mid"):
            self.assertTrue(path.exists(), f"missing {path}")
            ours = Counter(n.channel for n in MidiFile.from_bytes(path.read_bytes()).notes())
            mm = mido.MidiFile(str(path))
            theirs = Counter(m.channel for t in mm.tracks for m in t
                             if m.type == "note_on" and m.velocity > 0)
            self.assertEqual(dict(ours), dict(theirs), str(path))
            # CC11 expression on ch10 must read 70 after the -45% cut (was 127)
            cc11 = [m.value for t in mm.tracks for m in t
                    if m.type == "control_change" and m.control == 11 and m.channel == 10]
            self.assertTrue(cc11, f"no CC11 on ch10 in {path}")
            self.assertEqual(set(cc11), {70})


if __name__ == "__main__":
    unittest.main()
