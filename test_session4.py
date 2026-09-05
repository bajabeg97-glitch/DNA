from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from dna_midi_studio import (
    FactoryStrumStroke,
    GuitarConfig,
    MidiFile,
    apply_guitar_reconstruction,
    load_guitar_registry,
    plan_guitar_reconstruction,
)
from dna_midi_studio.session4_fixture import build_session4_case


ROOT = Path(__file__).resolve().parents[1]


class Session4GuitarTests(unittest.TestCase):
    def setUp(self) -> None:
        (
            self.midi,
            self.patterns,
            self.profiles,
            self.control_maps,
            self.chords,
            self.config,
        ) = build_session4_case(ROOT)

    def plan(self, **changes):
        config = GuitarConfig(**{**self.config.__dict__, **changes})
        return plan_guitar_reconstruction(
            self.midi,
            self.patterns,
            self.profiles,
            self.control_maps,
            self.chords,
            config,
        )

    def test_rhythm_guitar_registry_rejects_gold_patterns(self) -> None:
        registry = json.loads(
            (ROOT / "data" / "session4-demo-registry.json").read_text()
        )
        registry["goldPatterns"] = [{"id": "999.999.999"}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(registry))
            with self.assertRaisesRegex(ValueError, "cannot use GOLD"):
                load_guitar_registry(path)

    def test_factory_pattern_and_sources_are_selected(self) -> None:
        plan = self.plan()
        self.assertEqual(plan.decision, "REPLACE")
        self.assertEqual(plan.pattern_id, "130.100.001")
        self.assertEqual(plan.source_ids, ("131.001.001", "131.001.002"))
        self.assertFalse(plan.to_manifest()["goldControlsRhythmGuitar"])

    def test_downstroke_preserves_low_to_high_offsets(self) -> None:
        plan = self.plan()
        down = [
            audit
            for audit in plan.note_audit
            if audit.direction == "down" and 1920 <= audit.tick < 2400
        ]
        by_string = {audit.string: audit.tick - 1920 for audit in down}
        self.assertEqual(by_string, {1: 0, 2: 12, 3: 24, 4: 36})

    def test_upstroke_preserves_high_to_low_offsets(self) -> None:
        plan = self.plan()
        up = [
            audit
            for audit in plan.note_audit
            if audit.direction == "up" and 2400 <= audit.tick < 2880
        ]
        by_string = {audit.string: audit.tick - 2400 for audit in up}
        self.assertEqual(by_string, {1: 36, 2: 24, 3: 12, 4: 0})

    def test_block_chord_has_simultaneous_onsets(self) -> None:
        plan = self.plan()
        block = [
            audit
            for audit in plan.note_audit
            if audit.direction == "block" and 3120 <= audit.tick < 3360
        ]
        self.assertEqual({audit.tick for audit in block}, {3120})

    def test_c_major_and_a_minor_voicings_are_playable(self) -> None:
        plan = self.plan()
        c_major = sorted(
            note.pitch
            for note, audit in zip(plan.generated_notes, plan.note_audit)
            if audit.direction == "down" and 1920 <= note.start < 2400
        )
        a_minor = sorted(
            note.pitch
            for note, audit in zip(plan.generated_notes, plan.note_audit)
            if audit.direction == "down" and 3840 <= note.start < 4320
        )
        self.assertEqual(c_major, [48, 55, 60, 64])
        self.assertEqual(a_minor, [45, 52, 57, 60])
        self.assertLessEqual(plan.max_fret_span, 4)

    def test_every_pitched_note_has_valid_string_and_fret(self) -> None:
        profile = self.profiles[self.config.profile_id]
        plan = self.plan()
        for audit in plan.note_audit:
            if audit.control_action is not None:
                continue
            self.assertIsNotNone(audit.string)
            self.assertIsNotNone(audit.fret)
            self.assertGreaterEqual(audit.fret or 0, profile.fret_min)
            self.assertLessEqual(audit.fret or 0, profile.fret_max)
            self.assertEqual(audit.pitch, profile.tuning[audit.string or 0] + (audit.fret or 0))

    def test_velocity_is_factory_only(self) -> None:
        profile = self.profiles[self.config.profile_id]
        plan = self.plan()
        self.assertTrue(
            all(note.velocity == profile.optimal for note in plan.generated_notes)
        )
        self.assertTrue(
            all(note.factory_profile_id == profile.profile_id for note in plan.generated_notes)
        )
        self.assertFalse(plan.to_manifest()["goldAffectsVelocity"])

    def test_confirmed_control_map_emits_only_mapped_notes(self) -> None:
        plan = self.plan()
        controls = [
            (note.pitch, audit.control_action)
            for note, audit in zip(plan.generated_notes, plan.note_audit)
            if audit.control_action is not None
        ]
        self.assertEqual({pitch for pitch, _ in controls}, {24, 25})
        self.assertEqual({action for _, action in controls}, {"mute", "stop"})
        self.assertFalse(plan.to_manifest()["controlNotesGuessed"])

    def test_unconfirmed_control_map_requires_manual_review(self) -> None:
        maps = dict(self.control_maps)
        maps["130.900.001"] = replace(maps["130.900.001"], confirmed=False)
        plan = plan_guitar_reconstruction(
            self.midi, self.patterns, self.profiles, maps, self.chords, self.config
        )
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("unconfirmed", plan.reason)

    def test_missing_control_action_requires_manual_review(self) -> None:
        maps = dict(self.control_maps)
        control_map = maps["130.900.001"]
        maps["130.900.001"] = replace(
            control_map, actions={"mute": control_map.actions["mute"]}
        )
        plan = plan_guitar_reconstruction(
            self.midi, self.patterns, self.profiles, maps, self.chords, self.config
        )
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("lacks confirmed actions", plan.reason)

    def test_synthetic_control_map_is_blocked_by_default(self) -> None:
        plan = self.plan(allow_synthetic_control_map=False)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("forbidden outside explicit tests", plan.reason)

    def test_controls_can_be_explicitly_disabled_without_guessing(self) -> None:
        plan = self.plan(enable_controls=False, allow_synthetic_control_map=False)
        self.assertEqual(plan.decision, "REPLACE")
        self.assertTrue(all(audit.control_action is None for audit in plan.note_audit))

    def test_program_mismatch_requires_manual_review(self) -> None:
        profiles = dict(self.profiles)
        profiles[self.config.profile_id] = replace(
            profiles[self.config.profile_id], program=30
        )
        plan = plan_guitar_reconstruction(
            self.midi,
            self.patterns,
            profiles,
            self.control_maps,
            self.chords,
            self.config,
        )
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("Program Change", plan.reason)

    def test_impossible_voicing_requires_manual_review(self) -> None:
        profiles = dict(self.profiles)
        profiles[self.config.profile_id] = replace(
            profiles[self.config.profile_id], fret_max=1
        )
        plan = plan_guitar_reconstruction(
            self.midi,
            self.patterns,
            profiles,
            self.control_maps,
            self.chords,
            self.config,
        )
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("No playable Factory guitar voicing", plan.reason)

    def test_invalid_downstroke_direction_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Downstroke offsets"):
            FactoryStrumStroke(
                tick=0,
                direction="down",
                strings=(1, 2, 3),
                chord_tones=(0, 2, 0),
                offsets=(20, 10, 0),
                gate_ticks=120,
            )

    def test_invalid_string_range_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "String index"):
            FactoryStrumStroke(
                tick=0,
                direction="block",
                strings=(1, 6),
                chord_tones=(0, 2),
                offsets=(0, 0),
                gate_ticks=120,
            )

    def test_same_seed_is_byte_deterministic_and_round_trip_safe(self) -> None:
        first_plan = self.plan()
        second_plan = self.plan()
        first = apply_guitar_reconstruction(self.midi, first_plan)
        second = apply_guitar_reconstruction(self.midi, second_plan)
        self.assertEqual(first_plan.selection_hash, second_plan.selection_hash)
        self.assertEqual(first.midi.to_bytes(), second.midi.to_bytes())
        self.assertEqual(MidiFile.from_bytes(first.midi.to_bytes()).to_bytes(), first.midi.to_bytes())

    def test_apply_preserves_program_and_non_target_notes(self) -> None:
        before = self.midi.notes()
        result = apply_guitar_reconstruction(self.midi, self.plan())
        after = result.midi.notes()
        self.assertEqual(result.midi.program_at(11, 1920), 29)
        before_outside = {
            (note.track, note.channel, note.pitch, note.start, note.end)
            for note in before
            if note.track != 4 or note.start < 1920 or note.start >= 5760
        }
        after_outside = {
            (note.track, note.channel, note.pitch, note.start, note.end)
            for note in after
            if note.track != 4 or note.start < 1920 or note.start >= 5760
        }
        self.assertEqual(before_outside, after_outside)
        self.assertTrue(result.manifest["programChangePreserved"])
        self.assertTrue(result.manifest["notePairingValid"])


if __name__ == "__main__":
    unittest.main()