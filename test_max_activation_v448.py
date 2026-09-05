"""MAX 4.48 end-to-end activation tests (replacement-engineer + test-engineer).

Requires torch + numpy (neural stack) and the repo fixture
session35-partial-preview.mid.  Skips cleanly when torch is unavailable.

    python3.11 -m unittest test_max_activation_v448 -v
"""
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    import torch  # noqa: F401
    TORCH_OK = True
except Exception:  # pragma: no cover
    TORCH_OK = False

from dna_midi_studio.midi import MidiFile  # noqa: E402
from dna_midi_studio.max_layout import engine_dirs  # noqa: E402
from dna_midi_studio.ai_learning.track_replacement import ReplacementRequest, TrackReplacementEngine  # noqa: E402

FIXTURE = ROOT / "session35-partial-preview.mid"
REQUEST = dict(track_index=0, channel=8, start_bar=6, end_bar=9, role="bass")


@unittest.skipUnless(TORCH_OK and FIXTURE.is_file(), "torch or fixture unavailable")
class MaxActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dirs = engine_dirs(ROOT)
        cls.dirs = dirs
        cls.raw = FIXTURE.read_bytes()
        cls.engine = TrackReplacementEngine(dirs["model_dir"], dirs["learning_data_dir"], dirs["data_dir"])
        cls.req = ReplacementRequest(**REQUEST)
        cls.report = cls.engine.replace(cls.raw, cls.req, n=8)

    def test_report_schema_and_authority(self):
        r = self.report
        self.assertEqual(r["schema"], "dna-neural-track-replacement")
        self.assertEqual(r["authority"]["velocity"], "FACTORY_ONLY")
        self.assertFalse(r["authority"]["neuralVelocityOutput"])
        self.assertTrue(r["maxOrchestration"]["rankingOnly"])
        self.assertTrue(r["maxOrchestration"]["finalValidatorRequired"])
        self.assertEqual(r["status"], "RENDERED_REPLACE_A_B_C_REQUIRES_FINAL_PRODUCTION_VALIDATOR")

    def test_variants_abc_exist_and_differ(self):
        self.assertEqual(set(self.report["variants"]), {"A", "B", "C"})
        shas = {v["sha256"] for v in self.report["variants"].values()}
        self.assertEqual(len(shas), 3, "A/B/C must be distinct renders")
        for label, v in self.report["variants"].items():
            self.assertGreater(v["noteCount"], 0, label)
            self.assertTrue(v["protectedEventsPreserved"], label)
            self.assertFalse(v["goldVelocityUsed"], label)
            self.assertFalse(v["neuralVelocityUsed"], label)
            self.assertTrue(v["factoryVelocityProfileIds"], label)
            self.assertGreater(len(v["candidatePath"]), 0, label)
            for step in v["candidatePath"]:
                self.assertIn("scoreBreakdown", step)
                self.assertIn("maxScore", step)

    def test_every_variant_reparses_and_roundtrips(self):
        for label, v in self.report["variants"].items():
            reparsed = MidiFile.from_bytes(v["midiBytes"])
            self.assertGreater(len(reparsed.notes()), 0, label)
            self.assertEqual(hashlib.sha256(v["midiBytes"]).hexdigest(), v["sha256"], label)

    def test_factory_ids_come_from_factory_profiles(self):
        payload = json.loads((self.dirs["data_dir"] / "factory-velocity-profiles.json").read_text())
        known = {p.get("id") for p in payload["profiles"]}
        for label, v in self.report["variants"].items():
            self.assertTrue(set(v["factoryVelocityProfileIds"]) <= known, label)

    def test_decision_warrant_record(self):
        # The activation executor requires a REPLACE/AUGMENT warrant or --force.
        from dna_midi_studio.max_activation import execute_max_replace
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            result = execute_max_replace(
                FIXTURE, **REQUEST, out_dir=td, project_root=ROOT, force=True,
            )
            self.assertEqual(result["request"]["role"], "bass")
            self.assertEqual(set(result["outputs"]), {"A", "B", "C"})
            self.assertEqual(result["engineStatus"], "RENDERED_REPLACE_A_B_C_REQUIRES_FINAL_PRODUCTION_VALIDATOR")
            self.assertEqual(result["registry"]["present"], 10)
            for label, out in result["outputs"].items():
                self.assertTrue((Path(td) / out["file"]).is_file(), out["file"])


if __name__ == "__main__":
    unittest.main()
