from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import struct
import unittest

from dna_midi_studio.midi import MidiFile, MidiFormatError
from dna_midi_studio.reliability_gate import (
    REPORT_SCHEMA, REPORT_VERSION, VAULT_SCHEMA, VAULT_VERSION,
    execute_reliability_gate_api, safe_execute, sealed_midi_probe,
    serialize_reliability_chain, validate_regression_vault,
    validate_reliability_report,
)
from dna_midi_studio.session36_fixture import build_session36_chain


ROOT = Path(__file__).resolve().parents[1]


class Session36Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chain = build_session36_chain(ROOT)
        cls.report = cls.chain["reliabilityReport"]
        cls.vault = cls.chain["reliabilityVault"]


BASE = [
    lambda s: s.report["schema"] == REPORT_SCHEMA,
    lambda s: s.report["version"] == REPORT_VERSION,
    lambda s: s.report["result"] == "pass",
    lambda s: s.report["passed"],
    lambda s: len(s.report["reportHash"]) == 64,
    lambda s: s.vault["schema"] == VAULT_SCHEMA,
    lambda s: s.vault["version"] == VAULT_VERSION,
    lambda s: len(s.vault["vaultHash"]) == 64,
    lambda s: s.report["summary"]["malformedCases"] == 24,
    lambda s: s.report["summary"]["fuzzMutations"] == 32,
    lambda s: s.report["summary"]["atomicFaults"] == 7,
    lambda s: s.report["summary"]["workerCounts"] == [1, 2, 4],
    lambda s: s.report["summary"]["unhandledExceptions"] == 0,
    lambda s: s.report["summary"]["silentFallbacks"] == 0,
    lambda s: s.report["summary"]["partialOutputs"] == 0,
    lambda s: s.report["summary"]["openSeverity1"] == 0,
    lambda s: s.report["summary"]["openSeverity2"] == 0,
    lambda s: s.report["summary"]["byteIdenticalAcrossWorkers"],
    lambda s: s.report["atomicPublication"]["noPartialOutputs"],
    lambda s: s.report["atomicPublication"]["noTemporaryFiles"],
    lambda s: s.report["atomicPublication"]["resumeSafe"],
    lambda s: s.report["workerParity"]["sharedInputsUnchanged"],
    lambda s: s.report["batchIsolation"]["statuses"] == ["PASS", "BLOCKED", "PASS"],
    lambda s: s.report["batchIsolation"]["laterItemContinued"],
    lambda s: s.report["batchIsolation"]["blockedItemStructured"],
    lambda s: s.report["pathSafety"]["allBounded"],
    lambda s: s.report["pathSafety"]["noSeparators"],
    lambda s: all(row["withinBudget"] for row in s.report["stressProfiles"]),
    lambda s: s.vault["summary"]["entryCount"] == 64,
    lambda s: s.vault["summary"]["lockedRegressions"] == 64,
    lambda s: not s.report["release"]["finalCertifiedMidiExportAllowed"],
    lambda s: s.report["release"]["physicalPa800"] == "WAITING_FOR_DEVICE",
]

for index, callback in enumerate(BASE, 1):
    def test(self, callback=callback):
        self.assertTrue(callback(self))
    setattr(Session36Tests, f"test_{index:03d}_base", test)


counter = len(BASE)


def _structural_check(index: int, kind: int):
    def check(self):
        row = self.report["structuralCorpus"][index]
        if kind == 0:
            self.assertEqual(row["status"], "BLOCKED")
        elif kind == 1:
            self.assertTrue(row["failClosed"])
        else:
            self.assertFalse(row["fallbackUsed"])
    return check


for row_index in range(24):
    for kind_index in range(3):
        counter += 1
        setattr(Session36Tests, f"test_{counter:03d}_malformed_{row_index}_{kind_index}",
                _structural_check(row_index, kind_index))


def _atomic_check(index: int, kind: int):
    def check(self):
        row = self.report["atomicPublication"]["faults"][index]
        if kind == 0:
            self.assertIn(row["status"], {"BLOCKED", "CANCELLED"})
        elif kind == 1:
            self.assertTrue(row["failClosed"])
        elif kind == 2:
            self.assertFalse(row["partialOutput"])
        else:
            self.assertEqual(row["temporaryFiles"], [])
    return check


for row_index in range(7):
    for kind_index in range(4):
        counter += 1
        setattr(Session36Tests, f"test_{counter:03d}_atomic_{row_index}_{kind_index}",
                _atomic_check(row_index, kind_index))


def _fuzz_check(index: int):
    def check(self):
        row = self.report["sealedMutationFuzz"][index]
        self.assertEqual((row["status"], row["errorCode"], row["failClosed"]),
                         ("BLOCKED", "REL_HASH_MISMATCH", True))
    return check


for row_index in range(32):
    counter += 1
    setattr(Session36Tests, f"test_{counter:03d}_sealed_mutation_{row_index}", _fuzz_check(row_index))


def _worker_check(index: int, kind: int):
    def check(self):
        row = self.report["workerParity"]["runs"][index]
        if kind == 0:
            self.assertEqual(row["workers"], (1, 2, 4)[index])
        elif kind == 1:
            self.assertEqual(row["jobs"], 4)
        elif kind == 2:
            self.assertTrue(row["singleHash"])
        else:
            self.assertEqual(len(set(row["outputHashes"])), 1)
    return check


for row_index in range(3):
    for kind_index in range(4):
        counter += 1
        setattr(Session36Tests, f"test_{counter:03d}_worker_{row_index}_{kind_index}",
                _worker_check(row_index, kind_index))


def _validate_report(self):
    self.assertIsNone(validate_reliability_report(self.report))


def _tampered_report(self):
    value = deepcopy(self.report); value["summary"]["silentFallbacks"] = 1
    with self.assertRaises(ValueError):
        validate_reliability_report(value)


def _validate_vault(self):
    self.assertIsNone(validate_regression_vault(self.vault))


def _tampered_vault(self):
    value = deepcopy(self.vault); value["entries"][0]["status"] = "OPEN"
    with self.assertRaises(ValueError):
        validate_regression_vault(value)


def _unexpected_is_contained(self):
    outcome = safe_execute("boom", lambda: 1 / 0)
    self.assertEqual(outcome["status"], "BLOCKED")
    self.assertEqual(outcome["error"]["code"], "REL_STAGE_EXCEPTION")
    self.assertTrue(outcome["error"]["failClosed"])


def _valid_seal_passes(self):
    raw = self.chain["coherentVariants"]["C"]
    markers = []
    for row in self.chain["renderManifest"]["markerSetup"]:
        if row["marker"] not in markers: markers.append(row["marker"])
    channels = sorted({row["channelNumber"] - 1 for row in self.chain["renderManifest"]["channelBindings"]})
    self.assertEqual(sealed_midi_probe(raw, sha256(raw).hexdigest(), markers, channels)["status"], "PASS")


def _zero_track_regression(self):
    raw = b"MThd" + struct.pack(">IHHH", 6, 1, 0, 480)
    with self.assertRaises(MidiFormatError):
        MidiFile.from_bytes(raw)


def _api_gate(self):
    payload = {"chain": serialize_reliability_chain(self.chain),
               "stressNoteCounts": [20, 50], "fuzzCount": 8}
    result = execute_reliability_gate_api(payload, ROOT)
    self.assertEqual(result["result"], "pass")
    self.assertEqual(result["summary"]["fuzzMutations"], 8)


for name, callback in (
    ("validate_report", _validate_report), ("tampered_report", _tampered_report),
    ("validate_vault", _validate_vault), ("tampered_vault", _tampered_vault),
    ("unexpected_contained", _unexpected_is_contained), ("valid_seal", _valid_seal_passes),
    ("zero_track", _zero_track_regression), ("api_gate", _api_gate),
):
    counter += 1
    setattr(Session36Tests, f"test_{counter:03d}_{name}", callback)


assert counter == 184


if __name__ == "__main__":
    unittest.main()