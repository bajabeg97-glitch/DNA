from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import random
import struct
import tempfile
import unittest

import gold_schema
import pa800_validator
import server
from dna_midi_studio import (
    MidiEvent, MidiFile, MidiFormatError, MidiTrack, Note, RegressionVault,
    RoleDecisionCalibrator, RoleThreshold, load_dnc_registry, load_rx_registry,
    load_solo_registry,
)


ROOT = Path(__file__).resolve().parents[1]


def smf(payload: bytes, *, fmt: int = 0, tracks: int = 1, division: int = 480) -> bytes:
    return b"MThd" + struct.pack(">IHHH", 6, fmt, tracks, division) + b"MTrk" + struct.pack(">I", len(payload)) + payload


class Session12AdversarialTests(unittest.TestCase):
    def test_truncated_header_is_rejected(self) -> None:
        with self.assertRaises(MidiFormatError):
            MidiFile.from_bytes(b"MThd\x00")

    def test_missing_track_chunk_is_rejected(self) -> None:
        raw = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480)
        with self.assertRaisesRegex(MidiFormatError, "Missing MTrk"):
            MidiFile.from_bytes(raw)

    def test_truncated_declared_track_is_rejected(self) -> None:
        raw = b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + b"MTrk" + struct.pack(">I", 99) + b"x"
        with self.assertRaisesRegex(MidiFormatError, "exceeds file length"):
            MidiFile.from_bytes(raw)

    def test_truncated_vlq_is_rejected(self) -> None:
        with self.assertRaises(MidiFormatError):
            MidiFile.from_bytes(smf(b"\x81"))

    def test_vlq_longer_than_four_bytes_is_rejected(self) -> None:
        with self.assertRaisesRegex(MidiFormatError, "exceeds four bytes"):
            MidiFile.from_bytes(smf(b"\x81\x81\x81\x81\x00"))

    def test_smpte_division_is_rejected(self) -> None:
        with self.assertRaisesRegex(MidiFormatError, "SMPTE"):
            MidiFile.from_bytes(smf(b"\x00\xff\x2f\x00", division=0xE728))

    def test_valid_running_status_round_trip(self) -> None:
        raw = smf(b"\x00\x90\x3c\x40\x81\x70\x3c\x00\x00\xff\x2f\x00")
        parsed = MidiFile.from_bytes(raw)
        self.assertEqual(len(parsed.notes()), 1)
        self.assertEqual(MidiFile.from_bytes(parsed.to_bytes()).notes()[0].pitch, 60)

    def test_running_status_before_status_is_rejected(self) -> None:
        with self.assertRaisesRegex(MidiFormatError, "before a channel status"):
            MidiFile.from_bytes(smf(b"\x00\x3c\x40\x00\xff\x2f\x00"))

    def test_sysex_round_trip_is_preserved(self) -> None:
        parsed = MidiFile.from_bytes(smf(b"\x00\xf0\x03\x01\x02\x03\x00\xff\x2f\x00"))
        self.assertEqual(next(event for event in parsed.tracks[0].events if event.kind == "sysex").data, b"\x01\x02\x03")
        self.assertEqual(MidiFile.from_bytes(parsed.to_bytes()).to_bytes(), parsed.to_bytes())

    def test_rpn_and_nrpn_controllers_round_trip(self) -> None:
        payload = b"".join(bytes((0, 0xB0, cc, value)) for cc, value in ((101, 0), (100, 1), (99, 2), (98, 3))) + b"\x00\xff\x2f\x00"
        parsed = MidiFile.from_bytes(smf(payload))
        self.assertEqual([e.data[0] for e in parsed.tracks[0].events if e.command == 0xB0], [101, 100, 99, 98])

    def test_channel_pressure_round_trip(self) -> None:
        parsed = MidiFile.from_bytes(smf(b"\x00\xd0\x45\x00\xff\x2f\x00"))
        self.assertTrue(any(event.command == 0xD0 for event in parsed.tracks[0].events))

    def test_poly_aftertouch_round_trip(self) -> None:
        parsed = MidiFile.from_bytes(smf(b"\x00\xa0\x3c\x45\x00\xff\x2f\x00"))
        self.assertTrue(any(event.command == 0xA0 for event in parsed.tracks[0].events))

    def test_pitch_bend_round_trip(self) -> None:
        parsed = MidiFile.from_bytes(smf(b"\x00\xe0\x00\x40\x00\xff\x2f\x00"))
        self.assertTrue(any(event.command == 0xE0 and event.data == b"\x00\x40" for event in parsed.tracks[0].events))

    def test_zero_note_file_is_valid(self) -> None:
        midi = MidiFile.from_bytes(smf(b"\x00\xff\x2f\x00"))
        self.assertEqual(midi.notes(), [])

    def test_large_track_round_trip(self) -> None:
        notes = [Note(0, 0, 48 + index % 24, index * 20, index * 20 + 10, 64) for index in range(5000)]
        midi = MidiFile(0, 480, [MidiTrack([])]).add_notes(track_index=0, new_notes=notes)
        parsed = MidiFile.from_bytes(midi.to_bytes())
        self.assertEqual(len(parsed.notes()), 5000)

    def test_polyphony_limiter_counts_sustained_notes_not_only_onsets(self) -> None:
        pattern = {
            "role": "accompaniment", "lengthBars": 1,
            "notes": [
                [0, 4, 0], [0, 4, 4], [0, 4, 7], [0, 4, 12],
                [2, 4, 12], [2, 4, 16], [2, 4, 19], [2, 4, 24],
            ],
        }
        notes, report = server.prepared_notes(pattern, "acc1")
        self.assertEqual(report["peakPolyphonyBefore"], 8)
        self.assertEqual(report["peakPolyphonyAfter"], 4)
        self.assertEqual(report["polyphonyTailsTrimmed"], 4)
        self.assertEqual(report["polyphonyNotesRemoved"], 0)
        self.assertEqual([note[1] for note in notes[:4]], [240, 240, 240, 240])

    def test_pa800_validator_blocks_concurrent_note_overflow(self) -> None:
        channel = 11  # Acc1 / external CH12, limit 4
        events = [
            {"tick": 0, "priority": 0, "data": server.meta_text(6, "v1cv1")},
            {"tick": 0, "priority": 1, "data": server.meter_meta(4, 4)},
            {"tick": 0, "priority": 2, "data": [0xB0 | channel, 0, 0]},
            {"tick": 0, "priority": 2, "data": [0xB0 | channel, 32, 0]},
            {"tick": 0, "priority": 2, "data": [0xC0 | channel, 0]},
            {"tick": 0, "priority": 2, "data": [0xB0 | channel, 11, 127]},
        ]
        for pitch in range(60, 65):
            events.extend((
                {"tick": 0, "priority": 4, "data": [0x90 | channel, pitch, 90]},
                {"tick": 480, "priority": 3, "data": [0x80 | channel, pitch, 0]},
            ))
        events.append({"tick": 480, "priority": 9, "data": [0xFF, 0x2F, 0]})
        result = pa800_validator.validate_pa800_smf(
            server.smf0(events, 480), ["v1cv1"], [channel]
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["polyphonyPassed"])
        self.assertEqual(result["peakPolyphonyByChannel"]["12"], 5)
        self.assertTrue(any("Polifonija CH12" in issue for issue in result["issues"]))

    def test_illegal_channel_data_byte_is_rejected(self) -> None:
        with self.assertRaisesRegex(MidiFormatError, "exceeds 127"):
            MidiFile.from_bytes(smf(b"\x00\x90\x3c\x80\x00\xff\x2f\x00"))

    def test_missing_eot_is_rejected(self) -> None:
        with self.assertRaisesRegex(MidiFormatError, "missing End Of Track"):
            MidiFile.from_bytes(smf(b"\x00\xc0\x01"))

    def test_trailing_second_eot_is_rejected(self) -> None:
        with self.assertRaisesRegex(MidiFormatError, "End Of Track must be empty and final"):
            MidiFile.from_bytes(smf(b"\x00\xff\x2f\x00\x00\xff\x2f\x00"))

    def test_stuck_note_is_detected(self) -> None:
        midi = MidiFile.from_bytes(smf(b"\x00\x90\x3c\x40\x00\xff\x2f\x00"))
        with self.assertRaisesRegex(MidiFormatError, "dangling note-on"):
            midi.notes()

    def test_orphan_note_off_is_detected(self) -> None:
        midi = MidiFile.from_bytes(smf(b"\x00\x80\x3c\x00\x00\xff\x2f\x00"))
        with self.assertRaisesRegex(MidiFormatError, "Orphan note-off"):
            midi.notes()

    def test_zero_duration_note_is_detected(self) -> None:
        midi = MidiFile.from_bytes(smf(b"\x00\x90\x3c\x40\x00\x80\x3c\x00\x00\xff\x2f\x00"))
        with self.assertRaisesRegex(MidiFormatError, "Non-positive"):
            midi.notes()

    def test_deterministic_byte_fuzz_never_leaks_unexpected_exception(self) -> None:
        seed = bytearray(smf(b"\x00\x90\x3c\x40\x81\x70\x80\x3c\x00\x00\xff\x2f\x00"))
        rng = random.Random(12012)
        for _ in range(200):
            mutated = bytearray(seed)
            for _ in range(rng.randint(1, 4)):
                mutated[rng.randrange(len(mutated))] = rng.randrange(256)
            try:
                MidiFile.from_bytes(bytes(mutated))
            except MidiFormatError:
                pass
            except Exception as exc:  # pragma: no cover - assertion reports parser leak
                self.fail(f"Unexpected parser exception: {type(exc).__name__}: {exc}")

    def test_deeply_hidden_gold_velocity_is_rejected(self) -> None:
        result = gold_schema.validate_patterns([{"events": [{"meta": {"layers": [{"velocityCurve": [1]}]}}]}])
        self.assertFalse(result["passed"])
        self.assertIn("velocityCurve", result["forbiddenPaths"][0])

    def test_fake_rx_velocity_authority_is_rejected(self) -> None:
        raw = json.loads((ROOT / "data/session6-demo-registry.json").read_text())
        raw["rxMaps"][0]["triggers"][0]["nested"] = {"velocity": 99}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rx.json"; path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "dynamics are forbidden"):
                load_rx_registry(path)

    def test_fake_dnc_proprietary_event_is_rejected(self) -> None:
        raw = json.loads((ROOT / "data/session7-demo-registry.json").read_text())
        raw["dncMaps"][0]["triggers"][0]["eventType"] = "sysex"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dnc.json"; path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "proprietary DNC event type"):
                load_dnc_registry(path)

    def test_fake_solo_absolute_pitch_is_rejected(self) -> None:
        raw = json.loads((ROOT / "data/session5-demo-registry.json").read_text())
        raw["goldRelationships"][0]["hidden"] = {"absolutePitch": 72}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "solo.json"; path.write_text(json.dumps(raw))
            with self.assertRaisesRegex(ValueError, "Absolute pitch is forbidden"):
                load_solo_registry(path)

    def calibrator(self) -> RoleDecisionCalibrator:
        return RoleDecisionCalibrator({"drums": RoleThreshold(0.30, 0.65, 0.70),
                                       "solo": RoleThreshold(0.15, 0.45, 0.90),
                                       "bass": RoleThreshold(0.25, 0.60, 0.80)})

    def test_role_specific_thresholds_produce_different_decisions(self) -> None:
        calibrator = self.calibrator()
        self.assertEqual(calibrator.decide("drums", 0.5, 0.95), "REPAIR")
        self.assertEqual(calibrator.decide("solo", 0.5, 0.95), "KEEP")

    def test_unknown_role_never_uses_universal_threshold(self) -> None:
        self.assertEqual(self.calibrator().decide("unknown", 0.1, 1.0), "MANUAL_REVIEW")

    def test_low_role_evidence_requires_manual_review(self) -> None:
        self.assertEqual(self.calibrator().decide("solo", 0.1, 0.5), "MANUAL_REVIEW")

    def test_universal_single_role_calibrator_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not one universal"):
            RoleDecisionCalibrator({"all": RoleThreshold(0.2, 0.6, 0.8)})

    def test_regression_vault_is_deterministic_and_verifiable(self) -> None:
        paths = [f"data/session{number}-demo-registry.json" for number in range(2, 8)]
        first, second = RegressionVault.build(ROOT, paths), RegressionVault.build(ROOT, reversed(paths))
        self.assertEqual(first, second)
        self.assertTrue(first.verify(ROOT))

    def test_regression_vault_detects_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "item.json").write_text('{"id":"100.100.100"}')
            vault = RegressionVault.build(root, ["item.json"])
            (root / "item.json").write_text('{"id":"100.100.101"}')
            self.assertFalse(vault.verify(root))

    def test_regression_vault_rejects_cross_file_id_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "gold-patterns.json").write_text('{"id":"100.100.100"}')
            (root / "factory-strumming.json").write_text('{"id":"100.100.100"}')
            with self.assertRaisesRegex(ValueError, "collision"):
                RegressionVault.build(root, ["gold-patterns.json", "factory-strumming.json"])

    def test_regression_vault_blocks_path_traversal(self) -> None:
        with self.assertRaisesRegex(ValueError, "inside the workspace"):
            RegressionVault.build(ROOT, ["../outside.json"])


if __name__ == "__main__":
    unittest.main()