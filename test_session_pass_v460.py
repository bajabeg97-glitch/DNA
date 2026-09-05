"""DNA Session Pass 4.60 tests — Phase A consolidated single-pass report.

One call through all engines (mix 4.52, sty 4.53, groove 4.54, techniques
4.55, special track 4.57, role patterns 4.59). Numbers locked from executed
runs (2026-09-05) on artifacts-max-4.60/session-pass-*.json.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.session_pass import session_pass

ART = ROOT / "artifacts-max-4.60"


def _read(name):
    return json.loads((ART / name).read_text(encoding="utf-8"))


class SessionPassReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = _read("session-pass-reference-style.json")

    def test_identity_and_markers(self):
        self.assertEqual(self.r["schema"], "dna-session-pass")
        self.assertEqual(self.r["fileFacts"]["markerCount"], 10)
        self.assertEqual(self.r["fileFacts"]["markers"][0], "i1cv1")
        self.assertEqual(len(self.r["sourceSha256"]), 64)

    def test_role_map_used(self):
        self.assertEqual(self.r["roleMapUsed"]["8"], "bass")
        self.assertEqual(self.r["roleMapUsed"]["9"], "drums")
        self.assertEqual(self.r["roleMapUsed"]["10"], "percussion")
        self.assertEqual(self.r["roleMapUsed"]["15"], "accompaniment")

    def test_per_role_patterns(self):
        ch8 = self.r["perRolePatterns"]["8"]
        self.assertEqual(ch8["role"], "bass")
        self.assertEqual(ch8["noteCount"], 107)
        ch9 = self.r["perRolePatterns"]["9"]
        self.assertEqual(ch9["noteCount"], 263)
        self.assertEqual(self.r["perRolePatterns"]["10"]["noteCount"], 589)

    def test_groove_vs_human(self):
        by_ch = {g["channel"]: g for g in self.r["grooveVsHuman"]}
        self.assertAlmostEqual(by_ch[9]["stdMs"], 6.64, delta=0.1)
        self.assertGreater(by_ch[9]["exactOnGridShare"], 0.9)
        self.assertAlmostEqual(by_ch[10]["stdMs"], 22.24, delta=0.1)

    def test_technique_candidates(self):
        self.assertEqual(len(self.r["techniqueCandidates"]), 2)
        bass = self.r["techniqueCandidates"][0]
        self.assertEqual((bass["channel"], bass["role"]), (8, "bass"))
        self.assertEqual(bass["counts"]["total"], 107)

    def test_mix_plan_targets_percussion(self):
        self.assertEqual(self.r["mixPlan"]["targets"], {"10": 0.55})
        self.assertEqual(self.r["mixPlan"]["eventsPlanned"], 10)

    def test_action_statuses_and_applied_artifacts(self):
        statuses = [(a["id"], a["status"]) for a in self.r["actions"]]
        self.assertEqual(statuses, [
            ("A01_STY_EXPORT", "APPLIED"),
            ("A02_PERCUSSION_CC11_GAIN", "APPLIED"),
            ("A03_ECHO_TERCA", "SKIPPED"),
            ("A04_STUDIO_FILLS", "NEEDS_DECISION"),
            ("A05_DEVICE_LOCKED_TRIGGERS", "LOCKED"),
        ])
        a1 = self.r["actions"][0]
        self.assertTrue(a1["gates"]["smf0"])
        self.assertTrue(a1["gates"]["noteGeometryUnchanged"])
        self.assertEqual(a1["evidence"]["addedTotal"], 0)
        self.assertEqual(a1["evidence"]["cc11Added"], [])
        self.assertTrue(Path(a1["artifact"]).exists())

    def test_read_only_source_never_touched(self):
        # the applied artifacts must differ from input only by additions
        a1 = self.r["actions"][0]
        self.assertEqual(a1["evidence"]["addedTotal"], 0)  # conformance export
        a2 = self.r["actions"][1]
        self.assertTrue(a2["evidence"]["noteVelocitiesUntouched"])
        for g in a2["gates"].values():
            self.assertTrue(g)


class SessionPassOtherFilesTests(unittest.TestCase):
    def test_session35_readiness(self):
        r = _read("session-pass-session35-partial-preview.json")
        self.assertEqual(r["fileFacts"]["markerCount"], 10)
        st = {a["id"]: a["status"] for a in r["actions"]}
        self.assertEqual(st["A01_STY_EXPORT"], "READY")
        self.assertEqual(st["A02_PERCUSSION_CC11_GAIN"], "READY")
        self.assertEqual(st["A05_DEVICE_LOCKED_TRIGGERS"], "LOCKED")

    def test_song19_solo_scan(self):
        r = _read("session-pass-song19-01.json")
        self.assertEqual(r["roleMapUsed"]["2"], "solo")
        self.assertEqual(r["echoScan"]["soloChannel"], [2])
        self.assertEqual(r["echoScan"]["findings"], [])
        self.assertEqual(r["perRolePatterns"]["2"]["noteCount"], 16)
        st = {a["id"]: a["status"] for a in r["actions"]}
        self.assertEqual(st["A01_STY_EXPORT"], "SKIPPED")
        self.assertEqual(st["A02_PERCUSSION_CC11_GAIN"], "SKIPPED")

    def test_recompute_deterministic(self):
        raw = (ROOT / "baseline" / "reference-style.mid").read_bytes()
        res = session_pass(raw, source_name="reference-style.mid",
                           role_map={8: "bass", 9: "drums", 10: "percussion",
                                     11: "accompaniment", 12: "accompaniment",
                                     13: "accompaniment", 14: "accompaniment",
                                     15: "accompaniment"})
        self.assertEqual(len(res["actions"]), 5)
        self.assertEqual(res["perRolePatterns"]["8"]["noteCount"], 107)


if __name__ == "__main__":
    unittest.main()
