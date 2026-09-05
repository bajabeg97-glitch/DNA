"""test_generation_plan_v469.py — dokaz planske sesije 4.69 (DNA Composer plan).

Verifikuje da je plan po sesijama mašinski konzistentan (nema rupe u
numeraciji, svaka sesija ima prihvatanje, kapije referenciraju postojeće
sesije, broj sesija odgovara dogovoru sa korisnikom).
"""

import json
import re
import unittest
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PLAN = json.loads((ROOT / "reports-max-4.69" / "midi-generation-plan.json")
                  .read_text(encoding="utf-8"))
DOC = ROOT / "docs" / "midi-generation-plan-4.69.md"


def norm(s):
    """lowercase + bez dijakritika + samo alfanumerika (za robustan substring)."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", s.lower())


class PlanSchemaTest(unittest.TestCase):
    def test_meta(self):
        self.assertEqual(PLAN["schema"], "dna-midi-generation-plan")
        self.assertEqual(PLAN["version"], "4.69")
        self.assertEqual(PLAN["status"], "agreed-plan")
        self.assertEqual(PLAN["scope"]["generation"], "full-from-scratch")
        self.assertEqual(PLAN["scope"]["compute"], "local-cpu-no-keys")
        self.assertEqual(PLAN["scope"]["output"], "linear-song-smf-pa800-compatible")
        self.assertEqual(PLAN["scope"]["input"], ["midi-sketch", "text-description"])

    def test_session_count_agreed(self):
        # dogovor: 1 planska + 8 radnih sesija (miljokazi 4.69..4.76)
        sessions = PLAN["sessions"]
        self.assertEqual(len(sessions), PLAN["sessionCount"]["work"])
        self.assertEqual(len(sessions), 8)
        self.assertEqual(PLAN["sessionCount"]["planning"], 1)

    def test_session_ids_and_milestones_contiguous(self):
        sessions = PLAN["sessions"]
        ids = [s["id"] for s in sessions]
        self.assertEqual(ids, [f"S{i}" for i in range(1, len(sessions) + 1)])
        ms = [s["milestone"] for s in sessions]
        self.assertEqual(ms, [f"4.{69 + i}" for i in range(len(sessions))])

    def test_each_session_has_goal_acceptance_and_doc(self):
        for s in PLAN["sessions"]:
            self.assertTrue(s["goal"].strip())
            self.assertGreaterEqual(len(s["acceptance"]), 3)
        # doc pokriva svaku sesiju (naslov sesije, uz tolerantnost na dijakritike)
        raw = DOC.read_text(encoding="utf-8")
        text = norm(raw)
        for s in PLAN["sessions"]:
            self.assertIn(norm(s["title"]), text, s["id"])
        headings = re.findall(r"^### (S\d+) [—-]", raw, re.M)
        self.assertEqual(headings, [s["id"] for s in PLAN["sessions"]])

    def test_gates_reference_existing_sessions(self):
        ids = {s["id"] for s in PLAN["sessions"]}
        for g in PLAN["gates"]:
            self.assertIn(g["id"], {s["gate"] for s in PLAN["sessions"]})
        for s in PLAN["sessions"]:
            if s["gate"]:
                self.assertIn(s["gate"], [g["id"] for g in PLAN["gates"]])

    def test_standing_constraints_preserved(self):
        text = DOC.read_text(encoding="utf-8")
        for needle in ("bez API ključeva", "READY", "nikad ne menja original",
                       "permissive", "scorecard", "srpskom"):
            self.assertIn(needle.lower(), text.lower())


if __name__ == "__main__":
    unittest.main()
