from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from dna_midi_studio import (
    ChordCell,
    HarmonicConfig,
    MidiFile,
    MidiTrack,
    apply_harmonic_reconstruction,
    load_harmonic_registry,
    plan_harmonic_reconstruction,
)
from dna_midi_studio.midi import channel_event
from dna_midi_studio.session3_fixture import build_session3_case


ROOT = Path(__file__).resolve().parents[1]


class Session3HarmonicTests(unittest.TestCase):
    def setUp(self) -> None:
        (
            self.midi,
            self.patterns,
            self.profiles,
            self.relationships,
            self.chords,
            self.config,
        ) = build_session3_case(ROOT)

    def plan(self, **changes):
        config = HarmonicConfig(**{**self.config.__dict__, **changes})
        return plan_harmonic_reconstruction(
            self.midi,
            self.patterns,
            self.profiles,
            self.relationships,
            self.chords,
            config,
        )

    def test_harmonic_gold_rejects_absolute_pitch(self) -> None:
        registry = json.loads(
            (ROOT / "data" / "session3-demo-registry.json").read_text()
        )
        registry["goldPatterns"][0]["events"][0]["note"] = 36
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(registry))
            with self.assertRaisesRegex(ValueError, "Absolute pitch is forbidden"):
                load_harmonic_registry(path)

    def test_confirmed_drum_bass_relationship_selects_pattern(self) -> None:
        plan = self.plan()
        self.assertEqual(plan.decision, "REPLACE")
        self.assertEqual(plan.pattern_id, "220.010.001")
        self.assertEqual(plan.relationship_id, "230.001.001")

    def test_chord_functions_follow_c_major_and_a_minor(self) -> None:
        plan = self.plan()
        c_roots = {
            note.pitch % 12
            for note in plan.generated_notes
            if note.start in {1920, 2880}
        }
        a_roots = {
            note.pitch % 12
            for note in plan.generated_notes
            if note.start in {3840, 4800}
        }
        self.assertEqual(c_roots, {0})
        self.assertEqual(a_roots, {9})
        approaches = {
            note.start: note.pitch % 12
            for note in plan.generated_notes
            if note.start in {3720, 5640}
        }
        self.assertEqual(approaches, {3720: 11, 5640: 8})

    def test_manual_bass_is_keep_and_byte_unchanged(self) -> None:
        plan = self.plan(manual_bass=True)
        self.assertEqual(plan.decision, "KEEP")
        result = apply_harmonic_reconstruction(self.midi, plan)
        self.assertFalse(result.manifest["applied"])
        self.assertEqual(result.midi.to_bytes(), self.midi.to_bytes())

    def test_missing_relationship_requires_manual_review(self) -> None:
        plan = self.plan(selected_drum_pattern_id="999.999.999")
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("drum-bass relationship", plan.reason)

    def test_program_mismatch_requires_manual_review(self) -> None:
        profiles = dict(self.profiles)
        profiles[self.config.profile_id] = replace(
            profiles[self.config.profile_id], program=33
        )
        plan = plan_harmonic_reconstruction(
            self.midi,
            self.patterns,
            profiles,
            self.relationships,
            self.chords,
            self.config,
        )
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("Program Change", plan.reason)

    def test_incomplete_chord_timeline_blocks_mapping(self) -> None:
        plan = plan_harmonic_reconstruction(
            self.midi,
            self.patterns,
            self.profiles,
            self.relationships,
            [ChordCell(1920, 3840, 0, "major")],
            self.config,
        )
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("does not cover", plan.reason)

    def test_factory_velocity_and_register_are_enforced(self) -> None:
        plan = self.plan()
        profile = self.profiles[self.config.profile_id]
        for note in plan.generated_notes:
            self.assertEqual(note.velocity, profile.optimal)
            self.assertEqual(note.factory_profile_id, profile.profile_id)
            self.assertGreaterEqual(note.pitch, profile.register_min)
            self.assertLessEqual(note.pitch, profile.register_max)
        manifest = plan.to_manifest()
        self.assertFalse(manifest["goldAffectsVelocity"])
        self.assertFalse(manifest["goldContainsAbsolutePitch"])

    def test_voice_leading_limits_large_register_jumps(self) -> None:
        plan = self.plan()
        self.assertLessEqual(plan.max_voice_leading_leap, 12)

    def test_impossible_factory_register_requires_manual_review(self) -> None:
        profiles = dict(self.profiles)
        profiles[self.config.profile_id] = replace(
            profiles[self.config.profile_id], register_min=30, register_max=32
        )
        plan = plan_harmonic_reconstruction(
            self.midi,
            self.patterns,
            profiles,
            self.relationships,
            self.chords,
            self.config,
        )
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("No safe Factory-register voicing", plan.reason)

    def test_collision_is_shifted_inside_factory_register(self) -> None:
        plan = self.plan()
        self.assertGreaterEqual(plan.collision_shifts, 1)
        first_root = next(note for note in plan.generated_notes if note.start == 1920)
        self.assertEqual(first_root.pitch, 48)

    def test_collision_budget_overflow_requires_manual_review(self) -> None:
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        order = max(event.order for event in tracks[6].events) + 1
        tracks[6].events.extend(
            [
                channel_event(1920, order, 0x90 | 12, 48, 70),
                channel_event(2280, order + 1, 0x80 | 12, 48, 0),
            ]
        )
        blocked_midi = MidiFile(self.midi.format_type, self.midi.ppq, tracks)
        config = HarmonicConfig(**{**self.config.__dict__, "collision_budget": 0})
        plan = plan_harmonic_reconstruction(
            blocked_midi,
            self.patterns,
            self.profiles,
            self.relationships,
            self.chords,
            config,
        )
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("Collision budget exceeded", plan.reason)

    def test_high_quality_power_riff_identity_is_protected(self) -> None:
        config = HarmonicConfig(
            track_index=4,
            channel=11,
            role="power-riff",
            section="variation",
            start_tick=1920,
            end_tick=5760,
            seed=804,
            intensity=50,
            profile_id="120.011.001",
            require_relationship=False,
            existing_quality=0.95,
        )
        plan = plan_harmonic_reconstruction(
            self.midi,
            self.patterns,
            self.profiles,
            self.relationships,
            self.chords,
            config,
        )
        self.assertEqual(plan.decision, "KEEP")

    def test_power_chord_inversion_is_relative_and_playable(self) -> None:
        config = HarmonicConfig(
            track_index=4,
            channel=11,
            role="power-riff",
            section="variation",
            start_tick=1920,
            end_tick=3840,
            seed=804,
            intensity=50,
            profile_id="120.011.001",
            require_relationship=False,
            existing_quality=0.2,
        )
        plan = plan_harmonic_reconstruction(
            self.midi,
            self.patterns,
            self.profiles,
            self.relationships,
            [self.chords[0]],
            config,
        )
        first = sorted(note.pitch for note in plan.generated_notes if note.start == 1920)
        inverted = sorted(note.pitch for note in plan.generated_notes if note.start == 2880)
        self.assertEqual([(pitch - first[0]) for pitch in first], [0, 7])
        self.assertEqual([(pitch - inverted[0]) for pitch in inverted], [0, 5])

    def test_same_seed_produces_same_plan_and_midi_bytes(self) -> None:
        first_plan = self.plan()
        second_plan = self.plan()
        self.assertEqual(first_plan.selection_hash, second_plan.selection_hash)
        first = apply_harmonic_reconstruction(self.midi, first_plan)
        second = apply_harmonic_reconstruction(self.midi, second_plan)
        self.assertEqual(first.midi.to_bytes(), second.midi.to_bytes())
        self.assertEqual(MidiFile.from_bytes(first.midi.to_bytes()).to_bytes(), first.midi.to_bytes())

    def test_apply_preserves_program_and_notes_outside_window(self) -> None:
        before = self.midi.notes()
        result = apply_harmonic_reconstruction(self.midi, self.plan())
        after = result.midi.notes()
        self.assertEqual(result.midi.program_at(8, 1920), 32)
        before_outside = {
            (note.track, note.channel, note.pitch, note.start, note.end)
            for note in before
            if note.track != 3 or note.start < 1920 or note.start >= 5760
        }
        after_outside = {
            (note.track, note.channel, note.pitch, note.start, note.end)
            for note in after
            if note.track != 3 or note.start < 1920 or note.start >= 5760
        }
        self.assertEqual(before_outside, after_outside)
        self.assertTrue(result.manifest["programChangePreserved"])
        self.assertTrue(result.manifest["notePairingValid"])


if __name__ == "__main__":
    unittest.main()