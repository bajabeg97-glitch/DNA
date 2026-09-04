from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from dna_midi_studio import (
    MidiFile,
    MidiTrack,
    Note,
    RxConfig,
    apply_rx_events,
    load_rx_registry,
    plan_rx_events,
)
from dna_midi_studio.session6_fixture import build_session6_case


ROOT = Path(__file__).resolve().parents[1]


class Session6RxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.midi, self.maps, self.profiles, self.config = build_session6_case(ROOT)

    def plan(self, midi=None, maps=None, profiles=None, **changes):
        config = RxConfig(**{**self.config.__dict__, **changes})
        return plan_rx_events(
            self.midi if midi is None else midi,
            self.maps if maps is None else maps,
            self.profiles if profiles is None else profiles,
            config,
        )

    def registry(self):
        return json.loads(
            (ROOT / "data" / "session6-demo-registry.json").read_text(
                encoding="utf-8"
            )
        )

    def load_bad_registry(self, raw):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            return load_rx_registry(path)

    def test_registry_rejects_trigger_velocity(self) -> None:
        raw = self.registry()
        raw["rxMaps"][0]["triggers"][0]["velocity"] = 90
        with self.assertRaisesRegex(ValueError, "dynamics are forbidden"):
            self.load_bad_registry(raw)

    def test_registry_rejects_unverified_event_type(self) -> None:
        raw = self.registry()
        raw["rxMaps"][0]["triggers"][0]["eventType"] = "cc"
        with self.assertRaisesRegex(ValueError, "confirmed note triggers only"):
            self.load_bad_registry(raw)

    def test_registry_rejects_non_exact_sound(self) -> None:
        raw = self.registry()
        raw["rxMaps"][0]["bankMsb"] = -1
        with self.assertRaisesRegex(ValueError, "exact bank/program"):
            self.load_bad_registry(raw)

    def test_synthetic_map_requires_explicit_test_opt_in(self) -> None:
        plan = self.plan(allow_synthetic_map=False)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("explicit test opt-in", plan.reason)

    def test_official_confirmed_map_does_not_need_synthetic_opt_in(self) -> None:
        maps = dict(self.maps)
        maps[self.config.map_id] = replace(
            maps[self.config.map_id], source="official-korg"
        )
        plan = self.plan(maps=maps, allow_synthetic_map=False)
        self.assertEqual(plan.decision, "AUGMENT")

    def test_unconfirmed_map_requires_manual_review(self) -> None:
        maps = dict(self.maps)
        maps[self.config.map_id] = replace(maps[self.config.map_id], confirmed=False)
        plan = self.plan(maps=maps)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("not confirmed", plan.reason)

    def test_missing_bank_select_requires_manual_review(self) -> None:
        target = self.config.track_index
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        tracks[target].events = [
            event
            for event in tracks[target].events
            if not (
                event.kind == "channel"
                and event.channel == 13
                and event.command == 0xB0
                and event.data[0] == 32
            )
        ]
        plan = self.plan(midi=MidiFile(self.midi.format_type, self.midi.ppq, tracks))
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("Exact Bank Select", plan.reason)

    def test_bank_mismatch_requires_manual_review(self) -> None:
        target = self.config.track_index
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        tracks[target].events = [
            replace(event, data=bytes((0, 1)))
            if event.kind == "channel"
            and event.channel == 13
            and event.command == 0xB0
            and event.data[0] == 0
            else event
            for event in tracks[target].events
        ]
        plan = self.plan(midi=MidiFile(self.midi.format_type, self.midi.ppq, tracks))
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("Bank/Program", plan.reason)

    def test_program_mismatch_requires_manual_review(self) -> None:
        target = self.config.track_index
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        tracks[target].events = [
            replace(event, data=bytes((25,)))
            if event.kind == "channel"
            and event.channel == 13
            and event.command == 0xC0
            else event
            for event in tracks[target].events
        ]
        plan = self.plan(midi=MidiFile(self.midi.format_type, self.midi.ppq, tracks))
        self.assertEqual(plan.decision, "MANUAL_REVIEW")

    def test_profile_and_map_sound_mismatch_requires_manual_review(self) -> None:
        profiles = dict(self.profiles)
        profiles[self.config.profile_id] = replace(
            profiles[self.config.profile_id], program=25
        )
        plan = self.plan(profiles=profiles)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("profile and RX map", plan.reason)

    def test_missing_map_requires_manual_review(self) -> None:
        plan = self.plan(maps={})
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("map is missing", plan.reason)
        self.assertEqual(apply_rx_events(self.midi, plan).midi.to_bytes(), self.midi.to_bytes())

    def test_missing_factory_profile_requires_manual_review(self) -> None:
        plan = self.plan(profiles={})
        self.assertEqual(plan.decision, "MANUAL_REVIEW")

    def test_unknown_action_is_never_guessed(self) -> None:
        plan = self.plan(requested_actions=("pick_noise", "unknown_noise"))
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("not confirmed", plan.reason)

    def test_all_confirmed_conditions_generate_expected_counts(self) -> None:
        plan = self.plan()
        self.assertEqual(plan.decision, "AUGMENT")
        self.assertEqual(
            plan.counts,
            {
                "pick_noise": 6,
                "release_noise": 2,
                "position_noise": 1,
                "phrase_release": 4,
            },
        )
        self.assertEqual(len(plan.generated_notes), 13)

    def test_trigger_placement_follows_confirmed_map(self) -> None:
        plan = self.plan()
        action_notes = {}
        for note, audit in zip(plan.generated_notes, plan.note_audit):
            action_notes.setdefault(audit.action, []).append(note)
        self.assertIn(1896, [note.start for note in action_notes["pick_noise"]])
        self.assertIn(3480, [note.start for note in action_notes["release_noise"]])
        self.assertIn(4260, [note.start for note in action_notes["position_noise"]])
        self.assertIn(2172, [note.start for note in action_notes["phrase_release"]])

    def test_generated_velocity_comes_only_from_factory_profile(self) -> None:
        profile = self.profiles[self.config.profile_id]
        trigger_offsets = {
            trigger.action: trigger.intensity_offset
            for trigger in self.maps[self.config.map_id].triggers
        }
        plan = self.plan()
        for note, audit in zip(plan.generated_notes, plan.note_audit):
            self.assertEqual(
                note.velocity,
                profile.velocity(self.config.intensity + trigger_offsets[audit.action]),
            )
            self.assertEqual(note.factory_profile_id, profile.profile_id)
        self.assertFalse(plan.to_manifest()["goldAffectsVelocity"])

    def test_apply_preserves_every_original_event(self) -> None:
        plan = self.plan()
        result = apply_rx_events(self.midi, plan)
        self.assertTrue(result.manifest["originalEventsPreserved"])
        for index in range(len(self.midi.tracks)):
            if index != self.config.track_index:
                self.assertEqual(result.midi.tracks[index].events, self.midi.tracks[index].events)

    def test_apply_preserves_exact_target_sound(self) -> None:
        result = apply_rx_events(self.midi, self.plan())
        self.assertTrue(result.manifest["targetSoundPreserved"])
        self.assertEqual(result.midi.program_at(13, 1800), 24)

    def test_same_seed_is_byte_deterministic(self) -> None:
        first = self.plan()
        second = self.plan()
        self.assertEqual(first.selection_hash, second.selection_hash)
        self.assertEqual(
            apply_rx_events(self.midi, first).midi.to_bytes(),
            apply_rx_events(self.midi, second).midi.to_bytes(),
        )

    def test_output_round_trip_and_note_pairing(self) -> None:
        result = apply_rx_events(self.midi, self.plan())
        parsed = MidiFile.from_bytes(result.midi.to_bytes())
        self.assertEqual(parsed.to_bytes(), result.midi.to_bytes())
        self.assertTrue(result.manifest["notePairingValid"])

    def test_existing_rx_trigger_is_not_duplicated(self) -> None:
        midi = self.midi.add_notes(
            track_index=self.config.track_index,
            new_notes=[Note(self.config.track_index, 13, 28, 1896, 1926, 31)],
        )
        plan = self.plan(midi=midi)
        self.assertEqual(plan.counts["pick_noise"], 5)
        self.assertEqual(plan.skipped["existing"], 1)

    def test_replanning_applied_output_does_not_recurse(self) -> None:
        first = self.plan()
        output = apply_rx_events(self.midi, first).midi
        second = self.plan(midi=output)
        self.assertEqual(second.decision, "KEEP")
        self.assertEqual(second.generated_notes, ())
        self.assertGreater(second.skipped["existing"], 0)

    def test_transformation_budget_blocks_output(self) -> None:
        plan = self.plan(max_generated_events=12)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("budget exceeded", plan.reason)
        self.assertEqual(apply_rx_events(self.midi, plan).midi.to_bytes(), self.midi.to_bytes())

    def test_velocity_changes_do_not_change_rx_plan(self) -> None:
        target = self.config.track_index
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        tracks[target].events = [
            replace(event, data=bytes((event.data[0], 5)))
            if event.is_note_on and event.channel == 13
            else event
            for event in tracks[target].events
        ]
        quiet = MidiFile(self.midi.format_type, self.midi.ppq, tracks)
        normal_plan = self.plan()
        quiet_plan = self.plan(midi=quiet)
        self.assertNotEqual(normal_plan.input_hash, quiet_plan.input_hash)
        self.assertEqual(normal_plan.structural_hash, quiet_plan.structural_hash)
        self.assertEqual(normal_plan.selection_hash, quiet_plan.selection_hash)
        self.assertEqual(normal_plan.generated_notes, quiet_plan.generated_notes)
        self.assertFalse(normal_plan.to_manifest()["analysisVelocityUsed"])

    def test_manifest_proves_source_and_no_guessing(self) -> None:
        manifest = self.plan().to_manifest()
        self.assertEqual(manifest["mapSource"], "synthetic-test")
        self.assertEqual(manifest["mapVersion"], "synthetic-test-1")
        self.assertEqual(manifest["sourceIds"], ["160.900.001"])
        self.assertFalse(manifest["rxTriggersGuessed"])

    def test_no_source_notes_returns_keep(self) -> None:
        plan = self.plan(start_tick=0, end_tick=1000)
        self.assertEqual(plan.decision, "KEEP")


if __name__ == "__main__":
    unittest.main()