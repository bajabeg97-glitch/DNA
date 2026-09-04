from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from dna_midi_studio import (
    MidiEvent,
    MidiFile,
    MidiTrack,
    Note,
    SoloConfig,
    allocate_delay_track,
    build_track_identities,
    channel_track_indices,
    execute_pipeline,
    fingerprint_solo,
    plan_solo_enhancement,
    sound_bindings,
    verify_solo_fingerprint,
)
from dna_midi_studio.session5_fixture import build_session5_case


ROOT = Path(__file__).resolve().parents[1]


class Session18TrackIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        (
            self.midi,
            self.ornaments,
            self.relationships,
            self.profiles,
            self.chords,
            self.config,
        ) = build_session5_case(ROOT)

    def plan(self, midi=None, **changes):
        config = SoloConfig(**{**self.config.__dict__, **changes})
        return plan_solo_enhancement(
            self.midi if midi is None else midi,
            self.ornaments,
            self.relationships,
            self.profiles,
            self.chords,
            config,
        )

    def with_event(self, track_index: int, event: MidiEvent) -> MidiFile:
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        tracks[track_index].events.append(event)
        return MidiFile(self.midi.format_type, self.midi.ppq, tracks)

    def pipeline_config(self) -> dict:
        return {
            "version": "1.0",
            "stages": [{
                "engine": "solo",
                "registry": "data/session5-demo-registry.json",
                "config": dict(self.config.__dict__),
                "context": {"chords": [dict(chord.__dict__) for chord in self.chords]},
            }],
        }

    def test_three_session18_contracts_are_strict_json_schema(self) -> None:
        schema_dir = ROOT / "premium" / "schemas" / "v2"
        names = {
            "sound-binding-v2.schema.json",
            "track-identity-v1.schema.json",
            "solo-safety-report-v1.schema.json",
        }
        for name in names:
            value = json.loads((schema_dir / name).read_text(encoding="utf-8"))
            self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertEqual(value["type"], "object")
            self.assertIs(value["additionalProperties"], False)

    def test_track_uid_is_deterministic(self) -> None:
        first = build_track_identities(self.midi)
        second = build_track_identities(self.midi)
        self.assertEqual(first, second)
        self.assertTrue(all(item.track_uid.startswith("trk-") for item in first))

    def test_track_index_number_and_channel_number_are_separate(self) -> None:
        identity = build_track_identities(self.midi)[7].to_manifest()
        self.assertEqual((identity["trackIndex"], identity["trackNumber"]), (7, 8))
        self.assertEqual(identity["channelIndices"], [14])
        self.assertEqual(identity["channelNumbers"], [15])

    def test_duplicate_physical_tracks_receive_unique_uids(self) -> None:
        track = MidiTrack(list(self.midi.tracks[7].events))
        midi = MidiFile(1, self.midi.ppq, [track, MidiTrack(list(track.events))])
        identities = build_track_identities(midi)
        self.assertNotEqual(identities[0].track_uid, identities[1].track_uid)

    def test_track_uid_survives_midi_round_trip(self) -> None:
        before = [item.track_uid for item in build_track_identities(self.midi)]
        parsed = MidiFile.from_bytes(self.midi.to_bytes())
        after = [item.track_uid for item in build_track_identities(parsed)]
        self.assertEqual(before, after)

    def test_smf0_multi_channel_track_is_marked_merged(self) -> None:
        events = list(self.midi.tracks[7].events)
        events.append(MidiEvent(0, 999, "channel", status=0xC0, data=bytes((1,))))
        midi = MidiFile(0, self.midi.ppq, [MidiTrack(events)])
        self.assertTrue(build_track_identities(midi)[0].smf0_merged)

    def test_time_scoped_binding_matches_exact_factory_sound(self) -> None:
        bindings = sound_bindings(
            self.midi,
            track_index=7,
            channel=14,
            start_tick=1920,
            end_tick=5760,
        )
        self.assertEqual(len(bindings), 1)
        self.assertEqual(bindings[0].sound, self.profiles[self.config.profile_id].sound)

    def test_program_change_mid_window_splits_binding(self) -> None:
        midi = self.with_event(7, MidiEvent(3000, 999, "channel", status=0xCE, data=bytes((82,))))
        bindings = sound_bindings(midi, track_index=7, channel=14, start_tick=1920, end_tick=5760)
        self.assertEqual([(item.start_tick, item.end_tick, item.program) for item in bindings],
                         [(1920, 3000, 81), (3000, 5760, 82)])

    def test_bank_change_mid_window_splits_binding(self) -> None:
        midi = self.with_event(7, MidiEvent(3000, 999, "channel", status=0xBE, data=bytes((32, 5))))
        bindings = sound_bindings(midi, track_index=7, channel=14, start_tick=1920, end_tick=5760)
        self.assertEqual([item.bank_lsb for item in bindings], [0, 5])

    def test_change_away_and_back_remains_auditable(self) -> None:
        midi = self.with_event(7, MidiEvent(3000, 998, "channel", status=0xCE, data=bytes((82,))))
        tracks = [MidiTrack(list(track.events)) for track in midi.tracks]
        tracks[7].events.append(MidiEvent(4000, 999, "channel", status=0xCE, data=bytes((81,))))
        midi = MidiFile(1, midi.ppq, tracks)
        bindings = sound_bindings(midi, track_index=7, channel=14, start_tick=1920, end_tick=5760)
        self.assertEqual([item.program for item in bindings], [81, 82, 81])

    def test_sound_binding_is_physical_track_local(self) -> None:
        midi = self.with_event(0, MidiEvent(2500, 999, "channel", status=0xCE, data=bytes((99,))))
        bindings = sound_bindings(midi, track_index=7, channel=14, start_tick=1920, end_tick=5760)
        self.assertEqual([item.program for item in bindings], [81])

    def test_incomplete_binding_is_explicitly_unresolved(self) -> None:
        midi = MidiFile(1, 480, [MidiTrack([
            MidiEvent(0, 0, "channel", status=0xC0, data=bytes((1,)))
        ])])
        binding = sound_bindings(midi, track_index=0, channel=0, start_tick=0, end_tick=480)[0]
        self.assertFalse(binding.complete)
        self.assertEqual(binding.to_manifest()["status"], "UNRESOLVED")

    def test_shared_channel_owners_are_detected_by_physical_track(self) -> None:
        midi = self.with_event(0, MidiEvent(0, 999, "channel", status=0xCE, data=bytes((81,))))
        self.assertEqual(channel_track_indices(midi, 14), (0, 7))

    def test_delay_allocator_blocks_unapproved_existing_shared_channel(self) -> None:
        midi = self.with_event(0, MidiEvent(0, 999, "channel", status=0xCE, data=bytes((81,))))
        allocation = allocate_delay_track(midi, source_track_index=7, channel=14)
        self.assertFalse(allocation.allowed)
        self.assertEqual(allocation.authorization, "required")

    def test_delay_allocator_records_explicit_shared_channel_approval(self) -> None:
        midi = self.with_event(0, MidiEvent(0, 999, "channel", status=0xCE, data=bytes((81,))))
        allocation = allocate_delay_track(
            midi, source_track_index=7, channel=14, allow_existing_shared_channel=True
        )
        self.assertTrue(allocation.allowed)
        self.assertEqual(allocation.authorization, "explicit-delay-plan")

    def test_smf0_cannot_allocate_delay_track(self) -> None:
        midi = MidiFile(0, self.midi.ppq, [MidiTrack(list(self.midi.tracks[7].events))])
        allocation = allocate_delay_track(midi, source_track_index=0, channel=14)
        self.assertFalse(allocation.allowed)
        self.assertIn("SMF0", allocation.reason)

    def test_first_completely_empty_track_is_selected(self) -> None:
        midi = MidiFile(1, self.midi.ppq, self.midi.tracks + [MidiTrack([]), MidiTrack([])])
        allocation = allocate_delay_track(midi, source_track_index=7, channel=14)
        self.assertEqual((allocation.target_track_index, allocation.target_track_number), (8, 9))
        self.assertFalse(allocation.target_created)

    def test_next_track_is_allocated_when_under_sixteen(self) -> None:
        allocation = allocate_delay_track(self.midi, source_track_index=7, channel=14)
        self.assertEqual(allocation.target_track_index, 8)
        self.assertTrue(allocation.target_created)

    def test_seventeenth_track_is_never_allocated(self) -> None:
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        while len(tracks) < 16:
            tracks.append(MidiTrack([MidiEvent(0, 0, "meta", data=b"occupied", meta_type=3)]))
        allocation = allocate_delay_track(MidiFile(1, 480, tracks), source_track_index=7, channel=14)
        self.assertFalse(allocation.allowed)
        self.assertIsNone(allocation.target_track_number)

    def test_meta_occupied_track_is_not_considered_free(self) -> None:
        midi = MidiFile(1, 480, self.midi.tracks + [
            MidiTrack([MidiEvent(0, 0, "meta", data=b"name", meta_type=3)]),
            MidiTrack([]),
        ])
        allocation = allocate_delay_track(midi, source_track_index=7, channel=14)
        self.assertEqual(allocation.target_track_index, 9)

    def test_solo_fingerprint_is_deterministic(self) -> None:
        identity = build_track_identities(self.midi)[7]
        kwargs = dict(track_index=7, channel=14, start_tick=1920, end_tick=5760,
                      track_uid=identity.track_uid)
        self.assertEqual(fingerprint_solo(self.midi, **kwargs), fingerprint_solo(self.midi, **kwargs))

    def test_solo_fingerprint_detects_timing_mutation(self) -> None:
        fingerprint = fingerprint_solo(
            self.midi, track_index=7, channel=14, start_tick=1920, end_tick=5760
        )
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        changed = False
        replacement = []
        for event in tracks[7].events:
            if not changed and event.is_note_on and event.channel == 14 and event.tick >= 1920:
                replacement.append(replace(event, tick=event.tick + 1))
                changed = True
            else:
                replacement.append(event)
        tracks[7].events = replacement
        report = verify_solo_fingerprint(fingerprint, MidiFile(1, self.midi.ppq, tracks))
        self.assertFalse(report["passed"])
        self.assertTrue(report["missingNotes"])

    def test_solo_fingerprint_allows_audited_additions(self) -> None:
        fingerprint = fingerprint_solo(
            self.midi, track_index=7, channel=14, start_tick=1920, end_tick=5760
        )
        midi = self.midi.add_notes(track_index=7, new_notes=[Note(7, 14, 90, 2500, 2550, 60)])
        self.assertTrue(verify_solo_fingerprint(fingerprint, midi)["passed"])

    def test_wrong_track_uid_requires_manual_review(self) -> None:
        plan = self.plan(track_uid="trk-00000000000000000000")
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("trackUid", plan.reason)

    def test_mid_song_program_change_blocks_solo_enhancement(self) -> None:
        midi = self.with_event(7, MidiEvent(3000, 999, "channel", status=0xCE, data=bytes((82,))))
        plan = self.plan(midi=midi)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("Time-scoped", plan.reason)

    def test_solo_manifest_exposes_mapping_warning_and_bindings(self) -> None:
        manifest = self.plan().to_manifest()
        self.assertEqual(manifest["trackIdentity"]["trackNumber"], 8)
        self.assertEqual(manifest["soundBindings"][0]["channelNumber"], 15)
        self.assertEqual(manifest["mappingWarning"]["code"], "SOLO_TRACK_MAPPING")
        self.assertEqual(manifest["delayAllocation"]["targetTrackNumber"], 9)

    def test_pipeline_verifies_original_solo_after_stage(self) -> None:
        result = execute_pipeline(self.midi.to_bytes(), self.pipeline_config(), ROOT)
        safety = result.manifest["stages"][0]["soloSafety"]
        self.assertEqual(len(safety), 1)
        self.assertTrue(safety[0]["passed"])
        self.assertTrue(result.manifest["invariants"]["originalSoloVerifiedAfterEveryStage"])

    def test_pipeline_track_view_keeps_source_uid_after_augmentation(self) -> None:
        result = execute_pipeline(self.midi.to_bytes(), self.pipeline_config(), ROOT)
        stage_uid = result.manifest["stages"][0]["manifest"]["trackIdentity"]["trackUid"]
        self.assertEqual(result.manifest["trackView"][7]["trackUid"], stage_uid)


if __name__ == "__main__":
    unittest.main()