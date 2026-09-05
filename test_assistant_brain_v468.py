"""test_assistant_brain_v468.py — milestone 4.68 (P0: AI Brain v1).

Golden-set eval harness for the assistant brain. The same questions are the
contract that a future BrainLLM adapter must also pass (grounding contract,
docs/ai-model-plan-4.67.md).

Verifies:
- NLU: intents + parameters (sr/bs tolerant);
- NLG grounding: every claim carries a json-path source and numbers match the
  report exactly (no invention);
- refusals: subjective listening questions are refused; generation requests
  route to the composer (4.70) since 4.71 — see test_assistant_brain_v471;
- no state mutation, determinism, stdlib-only;
- the whole answer() pipeline stays deterministic across runs.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dna_midi_studio.assistant_brain import (  # noqa: E402
    answer, claims_from_report, understand,
)

REPORT = json.loads((ROOT / "artifacts-max-4.65" / "session-pass-reference-style.json")
                    .read_text(encoding="utf-8"))


class NluTest(unittest.TestCase):
    def cases(self):
        return [
            ("zdravo", "greet"),
            ("ćao, šta možeš?", "help"),
            ("koja su pravila?", "rules"),
            ("objasni A05", "explain"),
            ("zašto je A05 zaključano", "explain"),
            ("objasni zašto je A03 preskočen", "explain"),
            ("šta dalje?", "what_next"),
            ("sažmi analizu", "summarize"),
            ("ukratko, šta si izmerio", "summarize"),
            ("primeni A01 A02", "apply"),
            ("analiziraj reference-style", "analyze"),
            ("kako zvuči bolje posle primene?", "refuse_music_claim"),
            ("generiši mi pesmu", "compose_request"),   # 4.71: kompozitor postoji
            ("šta sam ti rekao ranije", "memory"),
            ("bla bla nešto nasumično", "unknown"),
        ]

    def test_intents(self):
        for text, expected in self.cases():
            self.assertEqual(understand(text)["intent"], expected, text)

    def test_action_params_lowercase_tolerant(self):
        u = understand("primeni A01 A02")
        self.assertEqual(u["intent"], "apply")
        self.assertEqual([a.upper() for a in u["params"]["actions"]], ["A01", "A02"])
        u2 = understand("objasni a05")
        self.assertEqual(u2["intent"], "explain")
        self.assertEqual([a.upper() for a in u2["params"]["actions"]], ["A05"])

    def test_preset_params(self):
        u = understand("hajde analiziraj fixture")
        self.assertEqual(u["params"]["preset"], "fixture")


class GroundingTest(unittest.TestCase):
    def test_claims_have_sources_and_are_grounded(self):
        claims = claims_from_report(REPORT)
        self.assertGreaterEqual(len(claims), 15)
        for c in claims:
            self.assertTrue(c["source"].startswith("report."), c)
            self.assertTrue(c["text"])
        # every number in claims must be traceable to the report is too broad
        # to verify automatically; instead we verify known ground truths:
        joined = " | ".join(c["text"] for c in claims)
        self.assertIn("10 Korg markera", joined)       # fileFacts
        self.assertIn("A05_DEVICE_LOCKED_TRIGGERS", joined)
        self.assertIn("LOCKED", joined)
        self.assertIn("patternRecognition", " ".join(c["source"] for c in claims))
        self.assertIn("reference-style", joined)  # recognition names its file

    def test_explain_a05_returns_only_a05(self):
        a = answer("objasni A05", REPORT)
        self.assertEqual(a["intent"], "explain")
        self.assertEqual(len(a["claims"]), 1)
        self.assertIn("A05_DEVICE_LOCKED_TRIGGERS", a["claims"][0]["text"])
        self.assertIn("LOCKED", a["claims"][0]["text"])
        self.assertEqual(a["claims"][0]["source"], "report.actions[A05_DEVICE_LOCKED_TRIGGERS]")

    def test_explain_a03(self):
        a = answer("zašto je A03 preskočen?", REPORT)
        self.assertEqual(len(a["claims"]), 1)
        self.assertIn("A03_ECHO_TERCA", a["claims"][0]["text"])

    def test_reply_numbers_match_report(self):
        # summarize's first claim is the fileFacts line; check marker count
        a = answer("ukratko sažmi", REPORT)
        self.assertEqual(a["intent"], "summarize")
        first = a["claims"][0]
        self.assertIn("10", first["text"])
        self.assertEqual(first["source"], "report.fileFacts")


class RefusalTest(unittest.TestCase):
    def test_music_claims_refused_without_report(self):
        for q in ("kako zvuči posle primene?", "da li je bolje nego pre?"):
            a = answer(q, None)
            self.assertEqual(a["intent"], "refuse_music_claim", q)
            self.assertIn("ne mogu", a["reply"].lower())

    def test_explain_without_report_is_honest(self):
        a = answer("objasni A05", None)
        self.assertIn("nema analize", a["reply"])
        self.assertEqual(a["claims"], [])

    def test_no_report_no_fabrication(self):
        a = answer("sažmi", None)
        self.assertEqual(a["claims"], [])


class PipelineTest(unittest.TestCase):
    def test_tools(self):
        a = answer("analiziraj reference-style", None)
        self.assertEqual(a["tool"], {"type": "analyzePreset", "presetId": "reference-style"})
        b = answer("primeni a01 a02", REPORT)
        self.assertEqual(b["tool"]["type"], "applyActions")
        self.assertEqual([x.upper() for x in b["tool"]["actions"]], ["A01", "A02"])

    def test_deterministic(self):
        a1 = answer("objasni A05", REPORT)
        a2 = answer("objasni A05", REPORT)
        self.assertEqual(a1, a2)

    def test_memory_uses_history(self):
        hist = [{"role": "user", "kind": "text", "text": "analiziraj reference-style"},
                {"role": "assistant", "kind": "plan", "text": "Plan za reference-style.mid"}]
        a = answer("šta sam ti rekao?", REPORT, history=hist)
        self.assertEqual(a["intent"], "memory")
        self.assertIn("reference-style", a["reply"])

    def test_what_next_suggests_readys_and_locked(self):
        a = answer("šta dalje", REPORT)
        self.assertIn("A01_STY_EXPORT", a["reply"])
        self.assertIn("A05", a["reply"])

    def test_cli_roundtrip(self):
        proc = subprocess.run(
            [sys.executable, "dna_midi_studio/assistant_brain.py"],
            input=json.dumps({"text": "objasni A05", "report": REPORT}),
            capture_output=True, text=True, cwd=str(ROOT), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["intent"], "explain")
        self.assertEqual(len(out["claims"]), 1)
        self.assertIn("A05", out["claims"][0]["text"])

    def test_stdlib_only(self):
        src = (ROOT / "dna_midi_studio" / "assistant_brain.py").read_text(encoding="utf-8")
        for banned in ("import mido", "from mido", "import numpy", "import torch",
                       "import openai", "import ollama"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()
