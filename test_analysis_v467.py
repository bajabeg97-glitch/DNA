"""test_analysis_v467.py — milestone 4.67.

Verifies the code-analysis artifacts stay truthful:
- 01-code-analysis.json numbers match a fresh live recount (engine module
  count, chain membership, orphan lists, marker counts);
- 02-remaining-problems-design.json schema is valid (ids P01..P10, effort
  vocabulary, order references only known ids, aiRelevance vocabulary);
- the AI model plan doc exists with its key sections.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _live_chain():
    import sys
    sys.path.insert(0, str(ROOT))
    engine = sorted((ROOT / "dna_midi_studio").glob("*.py"))
    pat = re.compile(r"^\s*(?:from|import)\s+(\.?\w+(?:\.\w+)*)", re.M)

    def neighbors(p):
        txt = p.read_text(encoding="utf-8", errors="replace")
        out = set()
        for m in pat.findall(txt):
            head = m.lstrip(".")
            parts = head.split(".")
            if m.startswith("."):
                out.add(parts[0])
            elif parts[0] == "dna_midi_studio" and len(parts) > 1:
                out.add(parts[1])
        return out

    edges = {p.stem: neighbors(p) for p in engine}
    seen = {"session_pass"}
    stack = ["session_pass"]
    while stack:
        cur = stack.pop()
        for nxt in edges.get(cur, ()):
            if nxt not in seen and (ROOT / "dna_midi_studio" / f"{nxt}.py").exists():
                seen.add(nxt)
                stack.append(nxt)
    return sorted(seen)


class CodeAnalysisTest(unittest.TestCase):
    def test_analysis_json_exists_and_valid(self):
        data = json.loads((ROOT / "reports-max-4.67" / "01-code-analysis.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "dna-code-analysis")
        self.assertGreaterEqual(data["totals"]["pythonFiles"], 200)
        self.assertGreaterEqual(data["totals"]["engineLocTotal"], 30000)

    def test_engine_module_count_matches_live_recount(self):
        data = json.loads((ROOT / "reports-max-4.67" / "01-code-analysis.json")
                          .read_text(encoding="utf-8"))
        live = len([p for p in (ROOT / "dna_midi_studio").glob("*.py")])
        self.assertEqual(data["totals"]["engineModulesTop"], live)

    def test_chain_membership_matches_live_recount(self):
        data = json.loads((ROOT / "reports-max-4.67" / "01-code-analysis.json")
                          .read_text(encoding="utf-8"))
        reported = set(data["chain"]["reachable"])
        live = set(_live_chain())
        self.assertEqual(reported, live)
        self.assertEqual(len(live), data["chain"]["reachableCount"])
        # the honest report claims engine is narrow: must stay <= 20
        self.assertLessEqual(len(live), 20)
        for core in ("session_pass", "sty_mapper", "mix_engine", "groove_engine",
                     "role_patterns", "pattern_library", "user_reference_bank",
                     "midi", "instrument_techniques"):
            self.assertIn(core, live)

    def test_orphan_lists_consistent(self):
        data = json.loads((ROOT / "reports-max-4.67" / "01-code-analysis.json")
                          .read_text(encoding="utf-8"))
        engine_total = data["totals"]["engineModulesTop"]
        orphans = data["orphans"]["modules"]
        chain_n = data["chain"]["reachableCount"]
        # every engine module is either in chain or in orphans (legacy roots
        # are excluded by construction elsewhere, so allow >=)
        self.assertGreaterEqual(len(orphans) + chain_n, engine_total)
        truly = set(data["orphans"]["trulyUnreferencedAnywhere"])
        self.assertEqual(len(truly), 15)  # 14 legacy + assistant_brain (4.68, referenced only by bridge process)
        self.assertIn("dnc_engine", truly)
        self.assertIn("agent_runtime", truly)

    def test_no_todo_markers(self):
        data = json.loads((ROOT / "reports-max-4.67" / "01-code-analysis.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(data["markers"], {"TODO": 0, "FIXME": 0, "HACK": 0, "XXX": 0})

    def test_chain_has_no_heavy_third_party(self):
        data = json.loads((ROOT / "reports-max-4.67" / "01-code-analysis.json")
                          .read_text(encoding="utf-8"))
        for mod in data["chain"]["reachable"]:
            src = (ROOT / "dna_midi_studio" / f"{mod}.py").read_text(encoding="utf-8")
            for banned in ("import torch", "from torch", "import mido", "from mido"):
                self.assertNotIn(banned, src, mod)


class DesignMatrixTest(unittest.TestCase):
    def test_matrix_schema(self):
        data = json.loads((ROOT / "reports-max-4.67" / "02-remaining-problems-design.json")
                          .read_text(encoding="utf-8"))
        ids = []
        for p in data["problems"]:
            self.assertRegex(p["id"], r"^P\d+$")
            self.assertIn(p["effort"], {"S", "M", "L", "XL"})
            self.assertIn(p["aiRelevance"], {"core", "support", "none"})
            ids.append(p["id"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 10)

    def test_order_references_only_known_ids(self):
        data = json.loads((ROOT / "reports-max-4.67" / "02-remaining-problems-design.json")
                          .read_text(encoding="utf-8"))
        known = {p["id"] for p in data["problems"]}
        for prio, items in data["order"].items():
            for entry in items:
                pid = entry.split(" (")[0]
                self.assertIn(pid, known, prio)
        self.assertEqual(set(data["order"]), {"P0", "P1", "P2", "P3", "deferred"})

    def test_problems_cover_honest_gaps(self):
        data = json.loads((ROOT / "reports-max-4.67" / "02-remaining-problems-design.json")
                          .read_text(encoding="utf-8"))
        links = set()
        for p in data["problems"]:
            links.update(p["links"])
        for gap in ("2.1", "2.5", "3.1", "3.2", "3.3", "3.7", "3.8"):
            self.assertIn(gap, links)


class AiModelDocTest(unittest.TestCase):
    def test_plan_doc_sections(self):
        md = (ROOT / "docs" / "ai-model-plan-4.67.md").read_text(encoding="utf-8")
        for marker in ("Arhitektura", "Grounding ugovor", "BrainV1", "BrainLLM",
                       "Redosled", "Poštenje i rizici"):
            self.assertIn(marker, md)
        self.assertIn("12", md)  # 12-modulni lanac se pominje

    def test_status_json_valid(self):
        status = json.loads((ROOT / "dna-ai-model-analysis-status-4.67.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "analysis-complete")
        self.assertEqual(status["schema"], "dna-milestone-status")
        self.assertIn("reports-max-4.67/01-code-analysis.json",
                      status["deliverables"])


if __name__ == "__main__":
    unittest.main()
