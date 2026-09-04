from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from dna_midi_studio import (
    AuthorizedNoteAddition, MidiFile, MidiTrack, VerificationPolicy, execute_pipeline, verify_candidate,
)
from dna_midi_studio.midi import channel_event, meta_event
from dna_midi_studio.session7_fixture import build_session7_case


ROOT = Path(__file__).resolve().parents[1]


class Session11IndependentVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        midi, *_ = build_session7_case(ROOT)
        self.source = midi.to_bytes()
        self.config = json.loads((ROOT / "data/session9-demo-config.json").read_text(encoding="utf-8"))
        self.pipeline = execute_pipeline(self.source, self.config, ROOT)
        self.candidate = self.pipeline.midi
        self.manifest = json.loads(json.dumps(self.pipeline.manifest))
        self.policy = VerificationPolicy((AuthorizedNoteAddition(10, 12, 1800, 5760, 12, 35,
                                                                  "confirmed DNC key-switch map"),))

    def verify(self, candidate=None, manifest=None, policy=None, rerun=True, workers=True, journal=None):
        candidate = self.candidate if candidate is None else candidate
        return verify_candidate(
            self.source, candidate, self.manifest if manifest is None else manifest,
            self.policy if policy is None else policy,
            rerun=(lambda: candidate) if rerun is True else rerun,
            worker_runs={1: candidate, 2: candidate, 4: candidate} if workers is True else workers,
            journal=journal,
        )

    def test_valid_candidate_passes_independent_verification(self) -> None:
        report = self.verify()
        self.assertTrue(report.passed, report.issues)

    def test_report_declares_independent_parser_as_verdict_source(self) -> None:
        self.assertEqual(self.verify().to_dict()["verdictSource"], "independent-byte-parser")

    def test_manifest_input_hash_mismatch_is_blocked(self) -> None:
        manifest = json.loads(json.dumps(self.manifest)); manifest["inputHash"] = "0" * 64
        self.assertIn("input hash", " ".join(self.verify(manifest=manifest).issues))

    def test_manifest_output_hash_mismatch_is_blocked(self) -> None:
        manifest = json.loads(json.dumps(self.manifest)); manifest["outputHash"] = "0" * 64
        self.assertFalse(self.verify(manifest=manifest).passed)

    def test_final_stage_hash_mismatch_is_blocked(self) -> None:
        manifest = json.loads(json.dumps(self.manifest)); manifest["stages"][-1]["manifest"]["outputHash"] = "0" * 64
        self.assertFalse(self.verify(manifest=manifest).checks["finalStageHash"])

    def test_changed_original_note_pitch_is_blocked(self) -> None:
        midi = MidiFile.from_bytes(self.candidate)
        for track in midi.tracks:
            for index, event in enumerate(track.events):
                if event.is_note_on and event.data[0] == 60:
                    track.events[index] = replace(event, data=bytes((61, event.data[1])))
                    break
            else:
                continue
            break
        report = self.verify(candidate=midi.to_bytes(), rerun=lambda: midi.to_bytes(), workers={1: midi.to_bytes()})
        self.assertFalse(report.checks["originalNotesPreserved"])

    def test_changed_original_velocity_is_blocked(self) -> None:
        midi = MidiFile.from_bytes(self.candidate)
        event = next(e for e in midi.tracks[10].events if e.is_note_on and e.data[0] == 60)
        midi.tracks[10].events[midi.tracks[10].events.index(event)] = replace(event, data=bytes((60, event.data[1] - 1)))
        report = self.verify(candidate=midi.to_bytes(), rerun=lambda: midi.to_bytes(), workers={1: midi.to_bytes()})
        self.assertFalse(report.checks["originalNotesPreserved"])

    def test_unauthorized_added_note_is_blocked(self) -> None:
        report = self.verify(policy=VerificationPolicy())
        self.assertFalse(report.checks["noteAdditionsAuthorized"])

    def test_authorized_note_addition_requires_reason(self) -> None:
        with self.assertRaisesRegex(ValueError, "reason"):
            AuthorizedNoteAddition(10, 12, 0, 10, 20, 21, "")

    def test_out_of_range_added_note_is_blocked(self) -> None:
        narrow = VerificationPolicy((AuthorizedNoteAddition(10, 12, 1800, 5760, 20, 20, "one trigger"),))
        self.assertFalse(self.verify(policy=narrow).passed)

    def test_removed_program_change_is_blocked(self) -> None:
        midi = MidiFile.from_bytes(self.candidate)
        midi.tracks[10].events = [e for e in midi.tracks[10].events if e.command != 0xC0]
        data = midi.to_bytes()
        self.assertFalse(self.verify(candidate=data, rerun=lambda: data, workers={1: data}).checks["protectedEventsPreserved"])

    def test_removed_bank_select_is_blocked(self) -> None:
        midi = MidiFile.from_bytes(self.candidate)
        midi.tracks[10].events = [e for e in midi.tracks[10].events
                                  if not (e.command == 0xB0 and e.data[0] in {0, 32})]
        data = midi.to_bytes()
        self.assertFalse(self.verify(candidate=data, rerun=lambda: data, workers={1: data}).checks["protectedEventsPreserved"])

    def test_removed_meta_event_is_blocked(self) -> None:
        midi = MidiFile.from_bytes(self.candidate)
        midi.tracks[10].events = [e for e in midi.tracks[10].events if e.meta_type != 0x03]
        data = midi.to_bytes()
        self.assertFalse(self.verify(candidate=data, rerun=lambda: data, workers={1: data}).checks["protectedEventsPreserved"])

    def test_malformed_candidate_is_blocked_without_optimizer_verdict(self) -> None:
        report = self.verify(candidate=b"not-midi", rerun=lambda: b"not-midi", workers={1: b"not-midi"})
        self.assertFalse(report.passed)
        self.assertIn("MIDI parse failed", report.issues[0])

    def test_missing_idempotency_proof_is_blocked(self) -> None:
        self.assertFalse(self.verify(rerun=None).checks["idempotent"])

    def test_different_second_run_is_blocked(self) -> None:
        self.assertFalse(self.verify(rerun=lambda: self.source).checks["idempotent"])

    def test_worker_count_mismatch_is_blocked(self) -> None:
        self.assertFalse(self.verify(workers={1: self.candidate, 4: self.source}).checks["workerDeterminism"])

    def test_worker_counts_one_two_four_are_reproducible(self) -> None:
        self.assertTrue(self.verify().checks["workerDeterminism"])

    def test_uncommitted_journal_is_blocked(self) -> None:
        journal = {"status": "BLOCKED", "outputHash": self.manifest["outputHash"]}
        self.assertFalse(self.verify(journal=journal).checks["journalCommitted"])

    def test_journal_hash_mismatch_is_blocked(self) -> None:
        journal = {"status": "COMMITTED", "outputHash": "0" * 64}
        self.assertFalse(self.verify(journal=journal).checks["journalOutputHash"])

    def test_matching_atomic_journal_passes(self) -> None:
        journal = {"status": "COMMITTED", "outputHash": self.manifest["outputHash"]}
        self.assertTrue(self.verify(journal=journal).passed)

    def pa800_midi(self, marker=b"v1cv1") -> bytes:
        events = [meta_event(0, 0, 0x06, marker), meta_event(0, 1, 0x58, bytes((4, 2, 24, 8))),
                  channel_event(0, 2, 0xB8, 0, 0), channel_event(0, 3, 0xB8, 32, 0),
                  channel_event(0, 4, 0xC8, 32), channel_event(0, 5, 0xB8, 11, 100),
                  channel_event(10, 6, 0x98, 36, 80), channel_event(40, 7, 0x88, 36, 0)]
        return MidiFile(0, 480, [MidiTrack(events)]).to_bytes()

    def test_pa800_contract_blocks_format_one_song(self) -> None:
        policy = replace(self.policy, pa800_style_contract=True)
        self.assertFalse(self.verify(policy=policy).checks["pa800Contract"])

    def test_pa800_contract_blocks_uppercase_marker(self) -> None:
        source = self.pa800_midi(b"V1CV1"); manifest = {"inputHash": __import__("hashlib").sha256(source).hexdigest(),
                                                        "outputHash": __import__("hashlib").sha256(source).hexdigest()}
        report = verify_candidate(source, source, manifest, VerificationPolicy(pa800_style_contract=True, require_idempotency=False))
        self.assertFalse(report.checks["pa800Contract"])

    def test_valid_pa800_contract_passes(self) -> None:
        source = self.pa800_midi(); digest = __import__("hashlib").sha256(source).hexdigest()
        report = verify_candidate(source, source, {"inputHash": digest, "outputHash": digest},
                                  VerificationPolicy(pa800_style_contract=True, require_idempotency=False))
        self.assertTrue(report.passed, report.issues)


if __name__ == "__main__":
    unittest.main()