"""MAX 4.48 registry/layout tests (registry-engineer + test-engineer).

Runnable in the flattened repository WITHOUT torch:
    python3.11 -m unittest test_max_registry_v448 -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dna_midi_studio.ai_learning.max_orchestrator import (  # noqa: E402
    MaxCandidateOrchestrator, MaxModelRegistry, build_max_status,
)


class RegistryLayoutTests(unittest.TestCase):
    def test_flat_repo_registry_finds_all_ten_models(self):
        s = build_max_status(ROOT)
        reg = s["registry"]
        self.assertEqual(reg["present"], 10)
        self.assertEqual(reg["missing"], 0)
        for m in reg["models"]:
            self.assertTrue(m["present"], m["key"])
            self.assertTrue((ROOT / m["path"]).is_file(), m["path"])

    def test_classic_layout_is_also_resolved(self):
        # Simulate the original classic workspace (data/, models/) with symlinks.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "data").mkdir()
            (tmp / "models").mkdir()
            for name in ("ai_event_decoder", "ai_autoregressive_event", "ai_multibar_event",
                         "ai_phrase_context", "ai_section_context", "ai_song_context",
                         "ai_transition_fill", "relationship-transformer-v1",
                         "relationship-sequence-v2", "dna-reconstructor-v2",
                         "gold-performance-patterns.json", "factory-strumming.json",
                         "factory-velocity-profiles.json", "learning_dataset_v1.npz"):
                target = ROOT / name
                if target.exists():
                    (tmp / "data" / name).symlink_to(target)
            reg = MaxModelRegistry(tmp).scan()
            self.assertEqual(reg["present"], 10, "classic layout must resolve all 10 models")
            for m in reg["models"]:
                self.assertTrue((tmp / m["path"]).exists(), f"{m['key']} -> {m['path']}")

    def test_hard_invalid_never_ranks(self):
        o = MaxCandidateOrchestrator()
        rows = o.rank_bar_candidates([
            {"hard_valid": False, "evidenceSource": "GOLD", "retrievalScore": 100,
             "score": 100, "retrievalRank": 0},
            {"hard_valid": True, "evidenceSource": "FULL_SONG_EVENT_DECODER_V1",
             "retrievalScore": 0, "score": 0, "contextScore": .9, "phraseScore": .8,
             "retrievalRank": 1},
        ], "bass")
        self.assertTrue(rows[0]["hard_valid"])
        self.assertLess(rows[-1]["maxScore"], 0)

    def test_application_contract_gates_are_present(self):
        s = build_max_status(ROOT)
        self.assertIn("FINAL_PRODUCTION_VALIDATOR", s["application"]["requiredGates"])
        self.assertEqual(s["scoring"]["authority"], "RANKING_ONLY_NOT_VALIDATION")

    def test_registry_authority_velocity_factory_only(self):
        s = build_max_status(ROOT)
        a = s["registry"]["authority"]
        self.assertEqual(a["velocity"], "FACTORY_ONLY")
        self.assertFalse(a["goldVelocity"])
        self.assertFalse(a["neuralVelocity"])


if __name__ == "__main__":
    unittest.main()
