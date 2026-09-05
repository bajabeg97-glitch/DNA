from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from dna_midi_studio.drum_reconstruction import (
    FactoryVelocityProfile,
    GoldPattern,
    GoldPatternEvent,
    ReconstructionConfig,
    apply_reconstruction,
    assert_gold_has_no_dynamic_authority,
    plan_reconstruction,
)
from dna_midi_studio.midi import MidiFile, MidiFormatError
from dna_midi_studio.session2_fixture import build_demo_case, build_demo_midi


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data" / "session2-demo-registry.json"


class Session2ReconstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.midi, self.patterns, self.profiles, self.config = build_demo_case(REGISTRY)

    def test_real_midi_round_trip_preserves_notes_and_program(self) -> None:
        raw = self.midi.to_bytes()
        parsed = MidiFile.from_bytes(raw)
        self.assertEqual(parsed.format_type, 1)
        self.assertEqual(parsed.ppq, 480)
        self.assertEqual(parsed.program_at(9, 1920), 0)
        self.assertEqual(len(parsed.notes()), len(self.midi.notes()))
        self.assertEqual(parsed.to_bytes(), raw)

    def test_missing_end_of_track_is_rejected(self) -> None:
        raw = self.midi.to_bytes()
        marker = raw.rfind(b"\x00\xff\x2f\x00")
        self.assertGreater(marker, 0)
        damaged = raw[:marker] + raw[marker + 4 :]
        track_header = damaged.rfind(b"MTrk", 0, marker)
        old_length = int.from_bytes(damaged[track_header + 4 : track_header + 8], "big")
        damaged = (
            damaged[: track_header + 4]
            + (old_length - 4).to_bytes(4, "big")
            + damaged[track_header + 8 :]
        )
        with self.assertRaisesRegex(MidiFormatError, "missing End Of Track"):
            MidiFile.from_bytes(damaged)

    def test_gold_velocity_is_rejected_recursively(self) -> None:
        with self.assertRaisesRegex(ValueError, "Forbidden GOLD field"):
            assert_gold_has_no_dynamic_authority(
                {"events": [{"tick": 0, "performance": {"velocity": 100}}]}
            )

    def test_factory_curve_anchors_intensity(self) -> None:
        profile = self.profiles[36]
        self.assertEqual(profile.velocity(0), profile.floor)
        self.assertEqual(profile.velocity(50), profile.optimal)
        self.assertEqual(profile.velocity(100), profile.ceiling)

    def test_plan_and_output_are_byte_deterministic(self) -> None:
        first_plan = plan_reconstruction(
            self.midi, self.patterns, self.profiles, self.config
        )
        second_plan = plan_reconstruction(
            self.midi, self.patterns, self.profiles, self.config
        )
        self.assertEqual(first_plan.selection_hash, second_plan.selection_hash)
        first = apply_reconstruction(self.midi, first_plan)
        second = apply_reconstruction(self.midi, second_plan)
        self.assertEqual(first.midi.to_bytes(), second.midi.to_bytes())

    def test_section_aware_pattern_replaces_weak_drums(self) -> None:
        plan = plan_reconstruction(self.midi, self.patterns, self.profiles, self.config)
        self.assertEqual(plan.decision, "REPLACE")
        self.assertEqual(plan.pattern_id, "210.010.001")
        self.assertEqual(plan.removed_notes, 4)
        self.assertGreater(len(plan.generated_notes), plan.removed_notes)
        self.assertEqual(
            {note.element for note in plan.generated_notes},
            {"kick", "snare", "hat", "cymbal"},
        )

    def test_every_generated_velocity_comes_from_factory_profile(self) -> None:
        plan = plan_reconstruction(self.midi, self.patterns, self.profiles, self.config)
        for note in plan.generated_notes:
            profile = self.profiles[note.pitch]
            self.assertEqual(note.velocity, profile.velocity(self.config.intensity))
            self.assertEqual(note.factory_profile_id, profile.profile_id)
        self.assertFalse(plan.to_manifest()["goldAffectsVelocity"])

    def test_missing_factory_profile_requires_manual_review(self) -> None:
        profiles = dict(self.profiles)
        del profiles[42]
        plan = plan_reconstruction(self.midi, self.patterns, profiles, self.config)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertEqual(plan.generated_notes, ())
        result = apply_reconstruction(self.midi, plan)
        self.assertFalse(result.manifest["applied"])
        self.assertEqual(result.midi.to_bytes(), self.midi.to_bytes())

    def test_kit_program_mismatch_blocks_mutation(self) -> None:
        config = ReconstructionConfig(
            **{**self.config.__dict__, "expected_program": 8}
        )
        plan = plan_reconstruction(self.midi, self.patterns, self.profiles, config)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("Kit program mismatch", plan.reason)

    def test_element_budget_prevents_percussion_overload(self) -> None:
        config = ReconstructionConfig(
            **{
                **self.config.__dict__,
                "element_budgets": {
                    "kick": 1,
                    "snare": 1,
                    "hat": 2,
                    "cymbal": 1,
                    "tom": 0,
                    "ghost": 0,
                    "fill": 0,
                    "percussion": 0,
                },
            }
        )
        plan = plan_reconstruction(self.midi, self.patterns, self.profiles, config)
        self.assertGreater(plan.rejected_by_budget, 0)
        per_measure: dict[tuple[int, str], int] = {}
        for note in plan.generated_notes:
            key = ((note.start - config.start_tick) // 1920, note.element or "")
            per_measure[key] = per_measure.get(key, 0) + 1
        self.assertTrue(all(count <= config.element_budgets[key[1]] for key, count in per_measure.items()))

    def test_apply_preserves_program_outside_notes_and_pairing(self) -> None:
        before_notes = self.midi.notes()
        plan = plan_reconstruction(self.midi, self.patterns, self.profiles, self.config)
        result = apply_reconstruction(self.midi, plan)
        after_notes = result.midi.notes()
        self.assertEqual(result.midi.program_at(9, 1920), 0)
        before_outside = {
            (note.track, note.channel, note.pitch, note.start, note.end)
            for note in before_notes
            if note.start < 1920 or note.start >= 5760
        }
        after_outside = {
            (note.track, note.channel, note.pitch, note.start, note.end)
            for note in after_notes
            if note.start < 1920 or note.start >= 5760
        }
        self.assertEqual(before_outside, after_outside)
        result.midi.to_bytes()
        self.assertTrue(result.manifest["notePairingValid"])

    def test_fill_and_percussion_roles_are_separate(self) -> None:
        fill_config = ReconstructionConfig(
            **{
                **self.config.__dict__,
                "section": "fill",
                "end_tick": 3840,
                "desired_notes_per_quarter": 1.25,
            }
        )
        fill_plan = plan_reconstruction(
            self.midi, self.patterns, self.profiles, fill_config
        )
        self.assertEqual(fill_plan.pattern_id, "210.020.001")
        self.assertIn("fill", {note.element for note in fill_plan.generated_notes})

        percussion_config = ReconstructionConfig(
            track_index=2,
            channel=10,
            role="percussion",
            section="variation",
            start_tick=1920,
            end_tick=3840,
            seed=801,
            intensity=50,
            desired_notes_per_quarter=1.0,
            expected_program=0,
        )
        percussion_plan = plan_reconstruction(
            self.midi, self.patterns, self.profiles, percussion_config
        )
        self.assertEqual(percussion_plan.pattern_id, "210.030.001")
        self.assertTrue(all(note.channel == 10 for note in percussion_plan.generated_notes))


if __name__ == "__main__":
    unittest.main()