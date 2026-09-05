from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from dna_midi_studio import (
    MidiEvent,
    MidiFile,
    MidiTrack,
    Note,
    DncConfig,
    apply_dnc_events,
    load_dnc_registry,
    plan_dnc_events,
)
from dna_midi_studio.session7_fixture import build_session7_case


ROOT = Path(__file__).resolve().parents[1]


class Session7DncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.midi, self.maps, self.profiles, self.config = build_session7_case(ROOT)

    def plan(self, midi=None, maps=None, profiles=None, **changes):
        config = DncConfig(**{**self.config.__dict__, **changes})
        return plan_dnc_events(
            self.midi if midi is None else midi,
            self.maps if maps is None else maps,
            self.profiles if profiles is None else profiles,
            config,
        )

    def registry(self):
        return json.loads(
            (ROOT / "data" / "session7-demo-registry.json").read_text(encoding="utf-8")
        )

    def load_bad(self, raw):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            return load_dnc_registry(path)

    def test_registry_rejects_trigger_velocity(self) -> None:
        raw = self.registry()
        raw["dncMaps"][0]["triggers"][0]["velocity"] = 100
        with self.assertRaisesRegex(ValueError, "dynamics are forbidden"):
            self.load_bad(raw)

    def test_registry_rejects_proprietary_sysex(self) -> None:
        raw = self.registry()
        raw["dncMaps"][0]["triggers"][0]["eventType"] = "sysex"
        with self.assertRaisesRegex(ValueError, "proprietary DNC event type"):
            self.load_bad(raw)

    def test_registry_rejects_protected_controller(self) -> None:
        raw = self.registry()
        raw["dncMaps"][0]["triggers"][1]["number"] = 32
        with self.assertRaisesRegex(ValueError, "protected Bank/RPN/NRPN"):
            self.load_bad(raw)

    def test_registry_rejects_keyswitch_playable_collision(self) -> None:
        raw = self.registry()
        raw["dncMaps"][0]["triggerMax"] = 80
        raw["dncMaps"][0]["triggers"][0]["number"] = 60
        with self.assertRaisesRegex(ValueError, "collides with playable"):
            self.load_bad(raw)

    def test_synthetic_map_requires_explicit_opt_in(self) -> None:
        plan = self.plan(allow_synthetic_map=False)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("explicit test opt-in", plan.reason)

    def test_official_confirmed_map_does_not_need_test_opt_in(self) -> None:
        maps = dict(self.maps)
        maps[self.config.map_id] = replace(maps[self.config.map_id], source="official-korg")
        self.assertEqual(self.plan(maps=maps, allow_synthetic_map=False).decision, "AUGMENT")

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
            event for event in tracks[target].events
            if not (event.command == 0xB0 and event.channel == 12 and event.data[0] == 32)
        ]
        plan = self.plan(midi=MidiFile(self.midi.format_type, self.midi.ppq, tracks))
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("Exact Bank Select", plan.reason)

    def test_bank_mismatch_requires_manual_review(self) -> None:
        target = self.config.track_index
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        tracks[target].events = [
            replace(event, data=bytes((0, 1)))
            if event.command == 0xB0 and event.channel == 12 and event.data[0] == 0
            else event for event in tracks[target].events
        ]
        plan = self.plan(midi=MidiFile(self.midi.format_type, self.midi.ppq, tracks))
        self.assertEqual(plan.decision, "MANUAL_REVIEW")

    def test_program_mismatch_requires_manual_review(self) -> None:
        target = self.config.track_index
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        tracks[target].events = [
            replace(event, data=bytes((49,)))
            if event.command == 0xC0 and event.channel == 12
            else event for event in tracks[target].events
        ]
        plan = self.plan(midi=MidiFile(self.midi.format_type, self.midi.ppq, tracks))
        self.assertEqual(plan.decision, "MANUAL_REVIEW")

    def test_role_mismatch_requires_manual_review(self) -> None:
        plan = self.plan(role="brass")
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("target role", plan.reason)

    def test_profile_map_sound_mismatch_requires_manual_review(self) -> None:
        profiles = dict(self.profiles)
        profiles[self.config.profile_id] = replace(profiles[self.config.profile_id], program=49)
        plan = self.plan(profiles=profiles)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")

    def test_missing_map_requires_manual_review_and_no_mutation(self) -> None:
        plan = self.plan(maps={})
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertEqual(apply_dnc_events(self.midi, plan).midi.to_bytes(), self.midi.to_bytes())

    def test_missing_profile_requires_manual_review(self) -> None:
        self.assertEqual(self.plan(profiles={}).decision, "MANUAL_REVIEW")

    def test_unknown_articulation_is_never_guessed(self) -> None:
        plan = self.plan(requested_articulations=("legato_switch", "invented_trigger"))
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("not confirmed", plan.reason)

    def test_confirmed_articulations_have_expected_counts(self) -> None:
        plan = self.plan()
        self.assertEqual(plan.decision, "AUGMENT")
        self.assertEqual(plan.counts, {
            "legato_switch": 4,
            "accent_cc": 1,
            "release_pressure": 3,
            "phrase_switch": 2,
        })
        self.assertEqual(len(plan.generated_notes), 6)
        self.assertEqual(len(plan.generated_events), 4)

    def test_event_types_are_exactly_map_driven(self) -> None:
        plan = self.plan()
        self.assertEqual({note.pitch for note in plan.generated_notes}, {20, 21})
        self.assertIn((0xB0 | 12, bytes((80, 100))), {(e.status, e.data) for e in plan.generated_events})
        self.assertIn((0xD0 | 12, bytes((45,))), {(e.status, e.data) for e in plan.generated_events})

    def test_keyswitch_timing_and_duration_follow_map(self) -> None:
        plan = self.plan()
        self.assertTrue(any(note.pitch == 20 and note.start == 2550 and note.end == 2586 for note in plan.generated_notes))
        self.assertTrue(any(note.pitch == 21 and note.start == 1872 and note.end == 1920 for note in plan.generated_notes))

    def test_keyswitch_velocity_is_factory_only(self) -> None:
        profile = self.profiles[self.config.profile_id]
        expected = {20: profile.velocity(35), 21: profile.velocity(30)}
        for note in self.plan().generated_notes:
            self.assertEqual(note.velocity, expected[note.pitch])
            self.assertEqual(note.factory_profile_id, profile.profile_id)

    def test_apply_preserves_original_events(self) -> None:
        result = apply_dnc_events(self.midi, self.plan())
        self.assertTrue(result.manifest["originalEventsPreserved"])

    def test_non_target_tracks_and_sound_are_preserved(self) -> None:
        result = apply_dnc_events(self.midi, self.plan())
        for index in range(len(self.midi.tracks)):
            if index != self.config.track_index:
                self.assertEqual(result.midi.tracks[index].events, self.midi.tracks[index].events)
        self.assertTrue(result.manifest["targetSoundPreserved"])

    def test_round_trip_has_valid_note_pairing(self) -> None:
        result = apply_dnc_events(self.midi, self.plan())
        parsed = MidiFile.from_bytes(result.midi.to_bytes())
        self.assertEqual(parsed.to_bytes(), result.midi.to_bytes())
        self.assertTrue(result.manifest["notePairingValid"])

    def test_same_seed_is_byte_deterministic(self) -> None:
        first = apply_dnc_events(self.midi, self.plan())
        second = apply_dnc_events(self.midi, self.plan())
        self.assertEqual(first.midi.to_bytes(), second.midi.to_bytes())

    def test_velocity_changes_do_not_change_plan(self) -> None:
        target = self.config.track_index
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        tracks[target].events = [
            replace(event, data=bytes((event.data[0], 5))) if event.is_note_on else event
            for event in tracks[target].events
        ]
        quiet = MidiFile(self.midi.format_type, self.midi.ppq, tracks)
        normal = self.plan()
        changed = self.plan(midi=quiet)
        self.assertNotEqual(normal.input_hash, changed.input_hash)
        self.assertEqual(normal.structural_hash, changed.structural_hash)
        self.assertEqual(normal.selection_hash, changed.selection_hash)

    def test_existing_keyswitch_is_not_duplicated(self) -> None:
        midi = self.midi.add_notes(
            track_index=self.config.track_index,
            new_notes=[Note(self.config.track_index, 12, 20, 2550, 2586, 40)],
        )
        plan = self.plan(midi=midi)
        self.assertEqual(plan.counts["legato_switch"], 3)
        self.assertEqual(plan.skipped["existing"], 1)

    def test_existing_cc_is_not_duplicated(self) -> None:
        midi = self.midi.add_events(
            track_index=self.config.track_index,
            new_events=[MidiEvent(4500, -1, "channel", status=0xB0 | 12, data=bytes((80, 100)))],
        )
        plan = self.plan(midi=midi)
        self.assertEqual(plan.counts["accent_cc"], 0)
        self.assertEqual(plan.skipped["existing"], 1)

    def test_replanning_output_does_not_recurse(self) -> None:
        output = apply_dnc_events(self.midi, self.plan()).midi
        second = self.plan(midi=output)
        self.assertEqual(second.decision, "KEEP")
        self.assertGreater(second.skipped["existing"], 0)

    def test_transformation_budget_blocks_output(self) -> None:
        plan = self.plan(max_generated_events=9)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertEqual(apply_dnc_events(self.midi, plan).midi.to_bytes(), self.midi.to_bytes())

    def test_manifest_proves_source_and_no_invention(self) -> None:
        manifest = self.plan().to_manifest()
        self.assertEqual(manifest["sourceIds"], ["170.900.001"])
        self.assertFalse(manifest["dncTriggersGuessed"])
        self.assertFalse(manifest["proprietaryEventsGenerated"])
        self.assertFalse(manifest["analysisVelocityUsed"])

    def test_no_playable_source_notes_returns_keep(self) -> None:
        self.assertEqual(self.plan(start_tick=0, end_tick=1000).decision, "KEEP")


if __name__ == "__main__":
    unittest.main()