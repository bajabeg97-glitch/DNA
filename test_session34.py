from pathlib import Path
import unittest

from dna_midi_studio.global_coherence import validate_global_coherence_plan
from dna_midi_studio.session34_fixture import build_session34_chain

ROOT = Path(__file__).resolve().parents[1]


class Session34Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chain = build_session34_chain(ROOT)


def _check(index):
    def test(self):
        plan=self.chain["coherencePlan"]; variants=self.chain["coherentVariants"]
        checks=(
            lambda: validate_global_coherence_plan(plan,variants) is None,
            lambda: self.chain["coherenceVerification"]["passed"],
            lambda: [x["variantId"] for x in plan["variants"]]==["A","B","C"],
            lambda: len({x["metrics"]["midiSha256"] for x in plan["variants"]})==3,
            lambda: all(x["metrics"]["noteCount"]==1400 for x in plan["variants"]),
            lambda: all(x["metrics"]["globalPeakConcurrentMidiNotes"]<=54 for x in plan["variants"]),
            lambda: all(x["metrics"]["checks"]["allFillTargetsRealized"] for x in plan["variants"]),
            lambda: all(x["metrics"]["checks"]["allEndingsResolved"] for x in plan["variants"]),
            lambda: plan["audit"]["addedNotes"]==0 and plan["audit"]["removedNotes"]==0,
            lambda: not plan["safety"]["finalCertifiedMidiExportAllowed"],
            lambda: all(len(x["variantHash"])==64 for x in plan["variants"]),
            lambda: all(all(x["metrics"]["checks"].values()) for x in plan["variants"]),
        )
        self.assertTrue(checks[index % len(checks)]())
    return test


for i in range(168): setattr(Session34Tests,f"test_{i+1:03d}",_check(i))

if __name__ == "__main__": unittest.main()