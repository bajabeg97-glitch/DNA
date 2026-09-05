"""test_pattern_library_v464.py — milestone 4.64.

Verifies:
- the vendored dmp_midi corpus (MIT) loads, validates and normalizes fully;
- GM drum mapping covers every part name; stats are deterministic;
- exact row lookup round-trips (corpus finds itself; probes on our own files
  return only real exact matches);
- vendor integrity: LICENSE is MIT allow-listed, NOTICE + manifest exist,
  every vendored file matches its recorded sha256;
- the module stays stdlib-only (no mido import).
"""

import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(ROOT))

from dna_midi_studio.pattern_library import (  # noqa: E402
    GM_NOTE, ROLE_OF_NOTE, VENDOR_DIR, aggregate_stats, build_library,
    iter_patterns, normalize_pattern, row_lookup, match_real_bar,
)
from dna_midi_studio.midi import MidiFile  # noqa: E402


class VendorIntegrityTest(unittest.TestCase):
    def test_license_is_mit_allowlisted(self):
        text = (VENDOR_DIR / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", text)
        self.assertIn("Permission is hereby granted", text)
        manifest = json.loads((VENDOR_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["license"], "MIT")
        self.assertIn("MIT", manifest["allowlist"])
        self.assertTrue(manifest["copiedVerbatim"])

    def test_notice_and_manifest_present(self):
        notice = (VENDOR_DIR / "NOTICE.md").read_text(encoding="utf-8")
        self.assertIn("gvellut/dmp_midi", notice)
        self.assertIn("81f926a4c7045a37dbadc21b9c332d799b1be9bf", notice)
        self.assertIn("200 Drum Machine Patterns", notice)
        manifest = json.loads((VENDOR_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("LICENSE", manifest["files"])

    def test_every_vendored_file_matches_manifest_sha256(self):
        manifest = json.loads((VENDOR_DIR / "manifest.json").read_text(encoding="utf-8"))
        for rel, expected in manifest["files"].items():
            data = (VENDOR_DIR / rel).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), expected, rel)

    def test_no_gpl_or_nc_material_vendored(self):
        # policy check: vendor tree may only hold allow-listed licenses
        manifest = json.loads((VENDOR_DIR / "manifest.json").read_text(encoding="utf-8"))
        allowed = {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "CC0-1.0", "Unlicense"}
        self.assertTrue(all(lic in allowed for lic in manifest["allowlist"]),
                        "disallowed license in vendor allowlist")


class CorpusSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patterns = build_library()

    def test_corpus_counts(self):
        self.assertEqual(len(self.patterns), 468)
        by_source = {}
        for src, _, _ in iter_patterns():
            by_source[src] = by_source.get(src, 0) + 1
        self.assertEqual(by_source["input/Patterns_200.json"], 200)
        self.assertEqual(by_source["input/Patterns_260.json"], 268)

    def test_schema_all_patterns(self):
        for p in self.patterns:
            self.assertIn(p["signature"], {"4/4", "12/8", "3/4"}, p["title"])
            self.assertIn(p["length"], (12, 16), p["title"])
            self.assertEqual(p["densityPerStep"], round(p["hitsTotal"] / p["length"], 4))
            for name, row in p["tracks"].items():
                self.assertIn(name, GM_NOTE, p["title"])
                self.assertEqual(len(row["steps"]), p["length"], p["title"])
                self.assertRegex(row["steps"], r"^[01]+$")
                self.assertEqual(row["steps"].count("1"), row["hits"])
                self.assertIn(row["gm"], ROLE_OF_NOTE)

    def test_length_signature_distribution(self):
        # observed corpus invariant (upstream data is loose: 4/4 may be 12 or
        # 16 steps; 3/4 may be 12 or 16) — pin the exact distribution instead
        from collections import Counter
        dist = Counter((p["signature"], p["length"]) for p in self.patterns)
        self.assertEqual(dist, {
            ("4/4", 16): 387, ("4/4", 12): 42, ("12/8", 12): 30,
            ("3/4", 12): 6, ("3/4", 16): 3,
        })

    def test_accent_rows_valid(self):
        for p in self.patterns:
            self.assertEqual(len(p["accentSteps"]), p["length"], p["title"])
            self.assertRegex(p["accentSteps"], r"^[01]*$")
            self.assertEqual(p["accentSteps"].count("1"), p["accentHits"])

    def test_normalized_dump_matches_build(self):
        dump = json.loads((ROOT / "artifacts-max-4.64" / "dmp-patterns-normalized.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(len(dump), 468)
        self.assertEqual([p["title"] for p in dump], [p["title"] for p in self.patterns])


class StatsAndLookupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patterns = build_library()
        cls.stats = aggregate_stats(cls.patterns)

    def test_stats_counts(self):
        self.assertEqual(self.stats["patternsTotal"], 468)
        self.assertEqual(self.stats["signatures"]["4/4"], 429)
        self.assertEqual(self.stats["signatures"]["12/8"], 30)
        self.assertEqual(self.stats["signatures"]["3/4"], 9)
        self.assertEqual(self.stats["lengths"], {"16 steps": 390, "12 steps": 78})

    def test_stats_deterministic(self):
        again = aggregate_stats(build_library())
        self.assertEqual(self.stats["digest"], again["digest"])
        self.assertEqual(len(self.stats["digest"]), 64)

    def test_row_lookup_roundtrip(self):
        # a pattern's own row must find its own title for each part
        for p in self.patterns[:60]:
            for name, row in p["tracks"].items():
                if row["hits"] == 0:
                    continue
                hits = row_lookup(self.patterns, row["gm"], row["steps"])
                self.assertIn(f'{p["source"]}:{p["title"]}', hits, name)

    def test_known_pattern_probe_reference_style(self):
        # reference-style kick row was measured to match Rock1MeasureB/Rock7
        midi = MidiFile.read(str(ROOT / "baseline" / "reference-style.mid"))
        res = match_real_bar(midi.notes(), midi.ppq, set(GM_NOTE.values()),
                             self.patterns)
        kick = res["rows"].get("36", "")
        self.assertTrue(kick)
        hits = row_lookup(self.patterns, 36, kick)
        self.assertTrue(any("Rock1MeasureB" in h for h in hits), hits)

    def test_stats_artifact_written(self):
        artifact = json.loads((ROOT / "artifacts-max-4.64" / "dmp-library-stats.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(artifact["patternsTotal"], 468)
        self.assertEqual(artifact["digest"], self.stats["digest"])


class EngineHygieneTest(unittest.TestCase):
    def test_module_is_stdlib_only(self):
        src = (ROOT / "dna_midi_studio" / "pattern_library.py").read_text(encoding="utf-8")
        for banned in ("import mido", "from mido", "import numpy", "import scipy",
                       "import torch", "import pretty_midi"):
            self.assertNotIn(banned, src)

    def test_matches_demo_artifact_valid(self):
        art = json.loads((ROOT / "artifacts-max-4.64" / "dmp-pattern-matches-demo.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(len(art["probes"]), 4)
        for probe in art["probes"]:
            self.assertTrue(probe["file"])
            for gm, m in probe["matches"].items():
                self.assertEqual(len(m["row"]), 16)
                for title in m["titles"]:
                    self.assertRegex(title, r"^Patterns_(200|260):.+$")


if __name__ == "__main__":
    unittest.main()
