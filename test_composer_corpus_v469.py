"""test_composer_corpus_v469.py — dokazi miljokaza 4.69 (S1 korpus+tokens).

Proverava:
- score language + tokenizer v1: vocab <= 1024, determinističan, roundtrip
  tačan (događaji == ulaz) na sva tri izvora (vendor/synthetic/engine);
- composer_validator: ceo korpus prolazi 100%; namerno pokvareni itemi se
  odbijaju (GM nota van opsega na ch9, CC na kanalu bez treka, trajanje preko
  takta, bpm van opsega, program change u sintetičkom itemu, nepoznata uloga,
  više od 128 taktova);
- korpus: brojke iz stats-a (items/bars/arrangements/moments/tokens) i
  manifest konzistentan sa .jsonl fajlovima;
- reproduktibilnost: build sa istim seed-om daje iste id-eve/digeste.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dna_midi_studio import composer_corpus as cc  # noqa: E402
from dna_midi_studio.composer_tokens import (  # noqa: E402
    VOCAB_SIZE, encode, decode, roundtrip_events, canonical_item,
)
import dna_midi_studio.composer_validator as pv  # noqa: E402

ART = ROOT / "artifacts-max-4.69" / "corpus-4.69"


def load_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TokenizerTest(unittest.TestCase):
    def test_vocab_within_plan_cap(self):
        self.assertLessEqual(VOCAB_SIZE, 1024)
        self.assertEqual(VOCAB_SIZE, 380)

    def test_roundtrip_all_origins(self):
        for name in ("corpus-vendor.jsonl", "corpus-synthetic.jsonl",
                     "corpus-engine.jsonl"):
            items = load_lines(ART / name)
            self.assertTrue(items, name)
            for it in items[:3]:
                self.assertTrue(roundtrip_events(it), it["id"])

    def test_encode_deterministic(self):
        it = load_lines(ART / "corpus-synthetic.jsonl")[0]
        self.assertEqual(encode(it), encode(it))

    def test_decode_returns_events(self):
        it = canonical_item(load_lines(ART / "corpus-engine.jsonl")[0])
        toks = encode(it)
        self.assertEqual(toks[0], 1)   # SOS
        self.assertEqual(toks[-1], 2)  # EOS
        tracks, cc = decode(toks)
        self.assertTrue(tracks or cc)


class ValidatorTest(unittest.TestCase):
    def _base(self, **kw):
        item = {
            "id": "x", "originKind": "synthetic", "source": "test",
            "license": "CC0", "bpm": 100, "signature": [4, 4], "sections": [],
            "tracks": [{"role": "drums", "channel": 9,
                        "events": [{"b": 0, "st": 0, "d": 1, "v": 6, "n": 36}]}],
            "cc": [],
        }
        item.update(kw)
        return item

    def test_good_item_passes(self):
        self.assertTrue(pv.validate_item(self._base())["ok"])

    def test_bad_gm_note_on_drums_rejected(self):
        r = pv.validate_item(self._base(tracks=[{"role": "drums", "channel": 9,
                          "events": [{"b": 0, "st": 0, "d": 1, "v": 6, "n": 18}]}]))
        self.assertFalse(r["ok"])
        self.assertTrue(any("GM percussion" in e for e in r["errors"]))

    def test_duration_crossing_bar_rejected(self):
        r = pv.validate_item(self._base(tracks=[{"role": "drums", "channel": 9,
                          "events": [{"b": 0, "st": 14, "d": 4, "v": 6, "n": 36}]}]))
        self.assertFalse(r["ok"])

    def test_step_out_of_grid_rejected(self):
        r = pv.validate_item(self._base(tracks=[{"role": "drums", "channel": 9,
                          "events": [{"b": 0, "st": 16, "d": 1, "v": 6, "n": 36}]}]))
        self.assertFalse(r["ok"])

    def test_bpm_out_of_range_rejected(self):
        self.assertFalse(pv.validate_item(self._base(bpm=300))["ok"])

    def test_unknown_role_rejected(self):
        self.assertFalse(pv.validate_item(self._base(tracks=[
            {"role": "mellotron", "channel": 1, "events": []}]))["ok"])

    def test_cc_without_track_rejected(self):
        self.assertFalse(pv.validate_item(self._base(cc=[{"ch": 7, "b": 0, "st": 0, "v": 8}]))["ok"])

    def test_synthetic_with_programs_rejected(self):
        self.assertFalse(pv.validate_item(self._base(meta={"programs": {"9": [0]}}))["ok"])

    def test_section_sum_mismatch_rejected(self):
        r = pv.validate_item(self._base(sections=[{"name": "Intro", "bars": 2}]))
        self.assertFalse(r["ok"])

    def test_too_many_bars_rejected(self):
        ev = {"b": 128, "st": 0, "d": 1, "v": 6, "n": 36}
        r = pv.validate_item(self._base(tracks=[{"role": "drums", "channel": 9,
                                                 "events": [ev]}]))
        self.assertFalse(r["ok"])

    def test_engine_item_with_programs_allowed(self):
        it = self._base(originKind="engine", meta={"programs": {"2": [45]}})
        self.assertTrue(pv.validate_item(it)["ok"])


class CorpusTest(unittest.TestCase):
    def test_stats_meet_plan_targets(self):
        stats = json.loads((ART / "corpus-stats-4.69.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(stats["items"], 700)
        self.assertGreaterEqual(stats["arrangements"], 300)   # plan S1 cilj
        self.assertGreaterEqual(stats["moments"], 2000)       # plan S1 cilj
        self.assertGreaterEqual(stats["tokens"], 500_000)
        self.assertTrue(stats["validator"]["ok"])
        self.assertEqual(stats["validator"]["failed"], [])
        self.assertEqual(stats["vocab"]["size"], 380)

    def test_manifest_matches_jsonl(self):
        manifest = json.loads((ART / "corpus-manifest-4.69.json").read_text(encoding="utf-8"))
        stats = json.loads((ART / "corpus-stats-4.69.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["items"]), stats["items"])
        seen = set()
        for name in ("corpus-vendor.jsonl", "corpus-synthetic.jsonl",
                     "corpus-engine.jsonl"):
            for it in load_lines(ART / name):
                seen.add(it["id"])
        self.assertEqual(len(seen), stats["items"])
        for m in manifest["items"]:
            self.assertIn(m["id"], seen)
            self.assertIn(m["originKind"], ("vendor", "synthetic", "engine", "user"))
            self.assertTrue(m["source"])
            self.assertTrue(m["license"])
            self.assertEqual(len(m["digest"]), 16)

    def test_every_jsonl_item_passes_validator(self):
        for name in ("corpus-vendor.jsonl", "corpus-synthetic.jsonl",
                     "corpus-engine.jsonl"):
            items = load_lines(ART / name)
            res = pv.validate_many(items)
            self.assertTrue(res["ok"], (name, res["failed"][:1]))

    def test_build_reproducible_with_seed(self):
        a = cc.build(seed=469, synthetic_count=5, real_limit=2)
        b = cc.build(seed=469, synthetic_count=5, real_limit=2)
        self.assertEqual([it["id"] for it in a["items"]],
                         [it["id"] for it in b["items"]])
        self.assertEqual([it["source"] for it in a["items"]],
                         [it["source"] for it in b["items"]])

    def test_three_origins_present(self):
        origins = {m["originKind"] for m in
                   json.loads((ART / "corpus-manifest-4.69.json").read_text(encoding="utf-8"))["items"]}
        self.assertTrue({"vendor", "synthetic", "engine"} <= origins)


class ExportTest(unittest.TestCase):
    def _tmp(self):
        import tempfile
        return Path(tempfile.mkdtemp(prefix="dna-corp-"))

    def test_synthetic_export_roundtrip(self):
        items = load_lines(ART / "corpus-synthetic.jsonl")
        it = items[0]
        p = self._tmp() / "syn.mid"
        cc.export_smf(it, p)
        self.assertGreater(p.stat().st_size, 1000)
        self.assertTrue(cc.smf_matches_item(p, it))

    def test_vendor_export_roundtrip(self):
        it = load_lines(ART / "corpus-vendor.jsonl")[0]
        p = self._tmp() / "vendor.mid"
        cc.export_smf(it, p)
        self.assertTrue(cc.smf_matches_item(p, it))

    def test_engine_export_roundtrip(self):
        it = load_lines(ART / "corpus-engine.jsonl")[0]
        p = self._tmp() / "engine.mid"
        cc.export_smf(it, p)
        self.assertTrue(cc.smf_matches_item(p, it))

    def test_probes_exist(self):
        for name in ("probe-rock-seed469.mid", "probe-funk-seed469.mid",
                     "probe-ballad12-seed469.mid"):
            p = ROOT / "artifacts-max-4.69" / "probes-4.69" / name
            self.assertTrue(p.exists(), name)
            self.assertGreater(p.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
