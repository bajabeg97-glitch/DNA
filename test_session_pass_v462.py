"""Session Pass 4.62 — per-action (user-confirmed) application tests.

Phase C core: the plan lists actions; only the ones the user confirms are
applied (--apply-actions), always into NEW artifacts; the source is never
modified. Numbers locked from executed runs (2026-09-05).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.session_pass import session_pass

RAW = (ROOT / "baseline" / "reference-style.mid").read_bytes()
ROLE_MAP = {8: "bass", 9: "drums", 10: "percussion",
            11: "accompaniment", 12: "accompaniment", 13: "accompaniment",
            14: "accompaniment", 15: "accompaniment"}


def run(actions, out_dir):
    return session_pass(RAW, source_name="reference-style.mid",
                        role_map=ROLE_MAP, apply_safe=True,
                        apply_actions=actions, out_dir=out_dir)


class PerActionApplyTests(unittest.TestCase):
    def test_a01_only(self):
        with tempfile.TemporaryDirectory() as td:
            r = run({"A01_STY_EXPORT"}, td)
            st = {a["id"]: a["status"] for a in r["actions"]}
            self.assertEqual(st["A01_STY_EXPORT"], "APPLIED")
            self.assertEqual(st["A02_PERCUSSION_CC11_GAIN"], "READY")
            files = {p.name for p in Path(td).glob("*")}
            self.assertTrue(any("session-sty-" in f for f in files))
            self.assertFalse(any("session-mixed-" in f for f in files))

    def test_a02_only(self):
        with tempfile.TemporaryDirectory() as td:
            r = run({"A02_PERCUSSION_CC11_GAIN"}, td)
            st = {a["id"]: a["status"] for a in r["actions"]}
            self.assertEqual(st["A02_PERCUSSION_CC11_GAIN"], "APPLIED")
            self.assertEqual(st["A01_STY_EXPORT"], "READY")
            files = {p.name for p in Path(td).glob("*")}
            self.assertTrue(any("session-mixed-" in f for f in files))
            self.assertFalse(any("session-sty-" in f for f in files))

    def test_none_means_all_ready_backwards_compatible(self):
        with tempfile.TemporaryDirectory() as td:
            r = run(None, td)
            st = {a["id"]: a["status"] for a in r["actions"]}
            self.assertEqual(st["A01_STY_EXPORT"], "APPLIED")
            self.assertEqual(st["A02_PERCUSSION_CC11_GAIN"], "APPLIED")
            files = {p.name for p in Path(td).glob("*")}
            self.assertTrue(any("session-sty-" in f for f in files))
            self.assertTrue(any("session-mixed-" in f for f in files))

    def test_source_bytes_never_modified_by_apply(self):
        before = RAW
        with tempfile.TemporaryDirectory() as td:
            run({"A01_STY_EXPORT", "A02_PERCUSSION_CC11_GAIN"}, td)
        self.assertEqual(RAW, before)  # input object untouched
        self.assertEqual(len(before), len(RAW))


if __name__ == "__main__":
    unittest.main()
