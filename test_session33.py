from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
import tempfile
from pathlib import Path
import unittest

from dna_midi_studio.arrangement_renderer import (
    execute_arrangement_renderer_api,
    execute_arrangement_renderer_batch,
    execute_arrangement_renderer_gui,
    publish_rendered_arrangement,
    render_arrangement,
    validate_render_manifest_v2,
    verify_rendered_arrangement,
)
from dna_midi_studio.session33_fixture import build_session33_chain
from dna_midi_studio.transactional_export import CancelToken


ROOT = Path(__file__).resolve().parents[1]


def _raises(callback) -> bool:
    try:
        callback()
    except Exception:
        return True
    return False


class Session33RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chain = build_session33_chain(ROOT)
        cls.source = cls.chain["sourceBytes"]
        cls.midi = cls.chain["renderedMidi"]
        cls.manifest = cls.chain["renderManifest"]
        cls.verification = cls.chain["verification"]
        cls.fragments = cls.manifest["fragments"]


BASE_CASES = [
    ("schema", lambda s: s.manifest["schema"] == "dna-arrangement-render-manifest"),
    ("version", lambda s: s.manifest["version"] == "2.0"),
    ("render_id", lambda s: s.manifest["renderId"].startswith("render-")),
    ("manifest_hash", lambda s: len(s.manifest["renderManifestHash"]) == 64),
    ("source_hash", lambda s: s.manifest["source"]["sourceMidiSha256"] == sha256(s.source).hexdigest()),
    ("track_plan_hash", lambda s: s.manifest["source"]["trackPlanHash"] == s.chain["trackPlan"]["trackPlanHash"]),
    ("ledger_hash", lambda s: s.manifest["source"]["evidenceLedgerHash"] == s.chain["ledger"]["ledgerHash"]),
    ("format_zero", lambda s: s.manifest["midi"]["format"] == 0),
    ("ppq_480", lambda s: s.manifest["midi"]["ppq"] == 480),
    ("one_track", lambda s: s.manifest["midi"]["trackCount"] == 1),
    ("ten_markers", lambda s: s.manifest["midi"]["markerCount"] == 10),
    ("seven_channels", lambda s: s.manifest["midi"]["usedChannels"] == [9, 10, 11, 12, 13, 14, 15]),
    ("fifty_two_fragments", lambda s: len(s.fragments) == 52),
    ("note_count", lambda s: s.manifest["midi"]["noteCount"] == 1400),
    ("midi_hash", lambda s: s.manifest["midi"]["outputSha256"] == sha256(s.midi).hexdigest()),
    ("peak", lambda s: s.manifest["midi"]["globalPeakConcurrentMidiNotes"] == 14),
    ("ceiling", lambda s: s.manifest["midi"]["softwareMidiNoteCeiling"] == 54),
    ("gold_timing_only", lambda s: s.manifest["audit"]["goldTimingRelativePitchOnly"] is True),
    ("factory_velocity", lambda s: s.manifest["audit"]["factoryVelocityOnly"] is True),
    ("factory_cc7", lambda s: s.manifest["audit"]["factoryCc7Only"] is True),
    ("cc11_contract", lambda s: s.manifest["audit"]["pa800Cc11Initialization"] == 127),
    ("expression_excluded", lambda s: s.manifest["audit"]["deviceUnconfirmedLayersExcluded"]["EXPRESSION_EVENT"] == 83),
    ("articulation_excluded", lambda s: s.manifest["audit"]["deviceUnconfirmedLayersExcluded"]["ARTICULATION_EVENT"] == 11),
    ("verification_pass", lambda s: s.verification["passed"] is True),
    ("software_preview", lambda s: s.manifest["safety"]["softwarePreviewMidiAllowed"] is True),
    ("certified_export_blocked", lambda s: s.manifest["safety"]["finalCertifiedMidiExportAllowed"] is False),
]


def _fragment_core(index):
    def check(self):
        item = self.fragments[index]
        self.assertEqual(item["status"], "RENDERED")
        self.assertIn(item["decision"], {"KEEP", "REPLACE"})
        self.assertEqual(len(item["fragmentRenderHash"]), 64)
        self.assertEqual(len(item["outputEventHash"]), 64)
    return check


def _fragment_bounds(index):
    def check(self):
        item = self.fragments[index]
        self.assertGreater(item["noteCount"], 0)
        self.assertGreater(item["endTick"], item["startTick"])
        self.assertLessEqual(item["factoryAllowedVelocityRange"][0], item["velocityRange"][0])
        self.assertGreaterEqual(item["factoryAllowedVelocityRange"][1], item["velocityRange"][1])
        self.assertLessEqual(item["authorizedRegister"][0], item["pitchRange"][0])
        self.assertGreaterEqual(item["authorizedRegister"][1], item["pitchRange"][1])
    return check


for index, (name, callback) in enumerate(BASE_CASES, 1):
    def test(self, callback=callback):
        self.assertTrue(callback(self))
    setattr(Session33RendererTests, f"test_{index:03d}_{name}", test)

offset = len(BASE_CASES)
for fragment_index in range(52):
    setattr(Session33RendererTests, f"test_{offset + fragment_index * 2 + 1:03d}_fragment_{fragment_index:02d}_core",
            _fragment_core(fragment_index))
    setattr(Session33RendererTests, f"test_{offset + fragment_index * 2 + 2:03d}_fragment_{fragment_index:02d}_bounds",
            _fragment_bounds(fragment_index))


def _mutated_manifest(self, path, value):
    manifest = deepcopy(self.manifest)
    target = manifest
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return manifest


def _bad_validate(path, value):
    def check(self):
        manifest = _mutated_manifest(self, path, value)
        self.assertTrue(_raises(lambda: validate_render_manifest_v2(manifest, self.midi)))
    return check


NEGATIVE_TESTS = [
    ("manifest_hash", _bad_validate(("renderManifestHash",), "0" * 64)),
    ("source_chain", _bad_validate(("source", "sourceHashChain"), "0" * 64)),
    ("source_hash", _bad_validate(("source", "sourceMidiSha256"), "bad")),
    ("format", _bad_validate(("midi", "format"), 1)),
    ("ppq", _bad_validate(("midi", "ppq"), 960)),
    ("track_count", _bad_validate(("midi", "trackCount"), 2)),
    ("ceiling", _bad_validate(("midi", "softwareMidiNoteCeiling"), 55)),
    ("peak", _bad_validate(("midi", "globalPeakConcurrentMidiNotes"), 55)),
    ("marker", _bad_validate(("midi", "markers"), ["INVALID"])),
    ("verification", _bad_validate(("verification", "status"), "PENDING")),
    ("source_mutated", _bad_validate(("safety", "sourceMidiMutated"), True)),
    ("gold_dynamics", _bad_validate(("safety", "goldAffectsDynamics"), True)),
    ("gold_mixer", _bad_validate(("safety", "goldAffectsMixer"), True)),
    ("gold_program", _bad_validate(("safety", "goldAffectsBankProgram"), True)),
    ("approximate_binding", _bad_validate(("safety", "approximateSoundBindingAllowed"), True)),
    ("implicit_fallback", _bad_validate(("safety", "implicitFallbackAllowed"), True)),
    ("certified_export", _bad_validate(("safety", "finalCertifiedMidiExportAllowed"), True)),
]


def _corrupt_midi(self):
    damaged = self.midi[:-1]
    self.assertFalse(verify_rendered_arrangement(damaged, self.manifest, self.source)["passed"])


def _wrong_source(self):
    self.assertFalse(verify_rendered_arrangement(self.midi, self.manifest, self.source + b"x")["passed"])


def _render_wrong_source(self):
    self.assertTrue(_raises(lambda: render_arrangement(
        self.source + b"x", self.chain["trackPlan"], self.chain["documents"], self.chain["ledger"], ROOT)))


def _render_bad_plan_hash(self):
    plan = deepcopy(self.chain["trackPlan"])
    plan["trackPlanHash"] = "0" * 64
    self.assertTrue(_raises(lambda: render_arrangement(
        self.source, plan, self.chain["documents"], self.chain["ledger"], ROOT)))


def _render_bad_ledger(self):
    ledger = deepcopy(self.chain["ledger"])
    ledger["ledgerHash"] = "0" * 64
    self.assertTrue(_raises(lambda: render_arrangement(
        self.source, self.chain["trackPlan"], self.chain["documents"], ledger, ROOT)))


def _api_strict(self):
    self.assertTrue(_raises(lambda: execute_arrangement_renderer_api({}, ROOT)))


def _api_base64(self):
    payload = {"midiBase64": "!!!", "documents": self.chain["documents"],
               "evidenceLedger": self.chain["ledger"], "trackPlan": self.chain["trackPlan"]}
    self.assertTrue(_raises(lambda: execute_arrangement_renderer_api(payload, ROOT)))


def _api_gui_parity(self):
    payload = {"midiBase64": base64.b64encode(self.source).decode(), "documents": self.chain["documents"],
               "evidenceLedger": self.chain["ledger"], "trackPlan": self.chain["trackPlan"]}
    self.assertEqual(execute_arrangement_renderer_api(payload, ROOT),
                     execute_arrangement_renderer_gui(payload, ROOT))


def _batch_isolation(self):
    good = {"midiBase64": base64.b64encode(self.source).decode(), "documents": self.chain["documents"],
            "evidenceLedger": self.chain["ledger"], "trackPlan": self.chain["trackPlan"]}
    result = execute_arrangement_renderer_batch([good, {}], ROOT)
    self.assertEqual([item["status"] for item in result], ["PASS", "BLOCKED"])


def _atomic_commit(self):
    with tempfile.TemporaryDirectory() as directory:
        result = publish_rendered_arrangement(self.source, self.midi, self.manifest, directory, "song.mid")
        self.assertEqual(result["status"], "COMMITTED")
        self.assertTrue(Path(result["output_path"]).is_file())
        self.assertTrue(Path(result["manifest_path"]).is_file())
        self.assertFalse(list(Path(directory).glob("*.tmp")))
        self.assertFalse(list(Path(directory).glob("*.lock")))


def _atomic_resume(self):
    with tempfile.TemporaryDirectory() as directory:
        first = publish_rendered_arrangement(self.source, self.midi, self.manifest, directory, "song.mid")
        second = publish_rendered_arrangement(self.source, self.midi, self.manifest, directory, "song.mid")
        self.assertEqual(first["output_hash"], second["output_hash"])
        self.assertTrue(second["resumed"])


def _atomic_cancel(self):
    with tempfile.TemporaryDirectory() as directory:
        token = CancelToken()
        token.cancel("test")
        result = publish_rendered_arrangement(self.source, self.midi, self.manifest,
                                              directory, "song.mid", token)
        self.assertEqual(result["status"], "CANCELLED")
        self.assertFalse(list(Path(directory).glob("*.mid")))


NEGATIVE_TESTS.extend([
    ("corrupt_midi", _corrupt_midi),
    ("wrong_source", _wrong_source),
    ("render_wrong_source", _render_wrong_source),
    ("render_bad_plan_hash", _render_bad_plan_hash),
    ("render_bad_ledger", _render_bad_ledger),
    ("api_strict", _api_strict),
    ("api_base64", _api_base64),
    ("api_gui_parity", _api_gui_parity),
    ("batch_isolation", _batch_isolation),
    ("atomic_commit", _atomic_commit),
    ("atomic_resume", _atomic_resume),
    ("atomic_cancel", _atomic_cancel),
    ("midi_hash_tamper", _bad_validate(("midi", "outputSha256"), "0" * 64)),
])

assert len(BASE_CASES) + 104 == 130
assert len(NEGATIVE_TESTS) == 30
for index, (name, callback) in enumerate(NEGATIVE_TESTS, 131):
    setattr(Session33RendererTests, f"test_{index:03d}_{name}", callback)


if __name__ == "__main__":
    unittest.main()