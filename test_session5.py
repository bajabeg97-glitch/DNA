from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from dna_midi_studio import (
    ChordCell,
    MidiEvent,
    MidiFile,
    MidiTrack,
    SoloConfig,
    apply_solo_enhancement,
    load_solo_registry,
    plan_solo_enhancement,
)
from dna_midi_studio.session5_fixture import build_session5_case


ROOT = Path(__file__).resolve().parents[1]


class Session5SoloTests(unittest.TestCase):
    def setUp(self) -> None:
        (
            self.midi,
            self.ornaments,
            self.relationships,
            self.profiles,
            self.chords,
            self.config,
        ) = build_session5_case(ROOT)

    def plan(self, midi=None, chords=None, profiles=None, ornaments=None, relationships=None, **changes):
        config = SoloConfig(**{**self.config.__dict__, **changes})
        return plan_solo_enhancement(
            self.midi if midi is None else midi,
            self.ornaments if ornaments is None else ornaments,
            self.relationships if relationships is None else relationships,
            self.profiles if profiles is None else profiles,
            self.chords if chords is None else chords,
            config,
        )

    def test_gold_velocity_is_rejected(self) -> None:
        registry = json.loads(
            (ROOT / "data" / "session5-demo-registry.json").read_text()
        )
        registry["goldOrnaments"][0]["velocity"] = 100
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(registry))
            with self.assertRaisesRegex(ValueError, "Forbidden GOLD field"):
                load_solo_registry(path)

    def test_absolute_gold_pitch_is_rejected(self) -> None:
        registry = json.loads(
            (ROOT / "data" / "session5-demo-registry.json").read_text()
        )
        registry["goldOrnaments"][0]["note"] = 72
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(registry))
            with self.assertRaisesRegex(ValueError, "Absolute pitch is forbidden"):
                load_solo_registry(path)

    def test_original_solo_fingerprint_is_never_mutated(self) -> None:
        plan = self.plan()
        result = apply_solo_enhancement(self.midi, plan)
        after = {
            (note.start, note.end, note.pitch, note.velocity)
            for note in result.midi.notes()
            if note.track == 7 and note.channel == 14
        }
        self.assertTrue(set(plan.original_fingerprint).issubset(after))
        self.assertFalse(plan.to_manifest()["originalSoloTimingMutable"])

    def test_all_evidence_backed_ornament_types_are_generated(self) -> None:
        plan = self.plan()
        self.assertGreater(plan.counts["trill"], 0)
        self.assertGreater(plan.counts["grace"], 0)
        self.assertGreater(plan.counts["slide"], 0)
        ornament_ids = {
            audit.evidence_id
            for audit in plan.note_audit
            if audit.kind in {"trill", "grace", "slide"}
        }
        self.assertEqual(
            ornament_ids, {"150.100.001", "150.100.002", "150.100.003"}
        )

    def test_insufficient_gap_skips_ornaments(self) -> None:
        ornaments = [replace(item, min_gap_ticks=2000) for item in self.ornaments]
        plan = self.plan(ornaments=ornaments)
        self.assertEqual(plan.counts["trill"], 0)
        self.assertEqual(plan.counts["grace"], 0)
        self.assertEqual(plan.counts["slide"], 0)

    def test_low_confidence_gold_ornament_is_not_used(self) -> None:
        ornaments = [replace(item, confidence=0.1) for item in self.ornaments]
        plan = self.plan(ornaments=ornaments)
        self.assertEqual(sum(plan.counts[kind] for kind in ("trill", "grace", "slide")), 0)

    def test_low_confidence_third_and_echo_relationships_are_not_used(self) -> None:
        relationships = [replace(item, confidence=0.1) for item in self.relationships]
        plan = self.plan(relationships=relationships)
        self.assertEqual(plan.counts["third"], 0)
        self.assertEqual(plan.counts["echo"], 0)
        self.assertGreater(plan.skipped["unconfirmedRelationship"], 0)

    def test_low_confidence_harmony_blocks_note_layers(self) -> None:
        chords = [replace(chord, confidence=0.2) for chord in self.chords]
        plan = self.plan(chords=chords)
        self.assertEqual(
            sum(plan.counts[kind] for kind in ("trill", "grace", "slide", "third", "echo")),
            0,
        )
        self.assertGreater(plan.counts["cc11"], 0)

    def test_generated_note_velocity_is_factory_only(self) -> None:
        profile = self.profiles[self.config.profile_id]
        plan = self.plan()
        allowed = {
            profile.velocity(self.config.intensity),
            profile.velocity(self.config.intensity - profile.third_intensity_drop),
            profile.velocity(self.config.intensity - profile.echo_intensity_drop),
        }
        self.assertTrue(all(note.velocity in allowed for note in plan.generated_notes))
        self.assertTrue(
            all(note.factory_profile_id == profile.profile_id for note in plan.generated_notes)
        )
        self.assertFalse(plan.to_manifest()["goldAffectsVelocity"])

    def test_diatonic_third_positive_relationship(self) -> None:
        plan = self.plan()
        thirds = [
            (note.pitch, audit.source_note_index, audit.interval)
            for note, audit in zip(plan.generated_notes, plan.note_audit)
            if audit.kind == "third"
        ]
        self.assertIn((64, 0, 4), thirds)
        self.assertIn((72, 4, 3), thirds)

    def test_non_scale_note_does_not_receive_third(self) -> None:
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        changed = []
        for event in tracks[7].events:
            if event.kind == "channel" and event.channel == 14 and event.note_number == 60:
                changed.append(replace(event, data=bytes((61, event.data[1]))))
            else:
                changed.append(event)
        tracks[7].events = changed
        midi = MidiFile(self.midi.format_type, self.midi.ppq, tracks)
        plan = self.plan(midi=midi)
        self.assertFalse(
            any(audit.kind == "third" and audit.source_note_index == 0 for audit in plan.note_audit)
        )

    def test_echo_is_delayed_shorter_and_factory_quieter(self) -> None:
        profile = self.profiles[self.config.profile_id]
        originals = [
            note for note in self.midi.notes() if note.track == 7 and note.channel == 14
        ]
        plan = self.plan()
        echo_pairs = [
            (note, audit)
            for note, audit in zip(plan.generated_notes, plan.note_audit)
            if audit.kind == "echo"
        ]
        self.assertTrue(echo_pairs)
        self.assertEqual(plan.echo_track_index, len(self.midi.tracks))
        for note, audit in echo_pairs:
            source = originals[audit.source_note_index]
            self.assertEqual(note.track, plan.echo_track_index)
            self.assertNotEqual(note.track, self.config.track_index)
            self.assertEqual(note.start - source.start, 240)
            self.assertLessEqual(note.end - note.start, source.end - source.start)
            self.assertLess(note.velocity, profile.velocity(self.config.intensity))

    def test_echo_is_not_recursive(self) -> None:
        plan = self.plan()
        echo_audits = [audit for audit in plan.note_audit if audit.kind == "echo"]
        self.assertLessEqual(len(echo_audits), len(plan.original_fingerprint))
        self.assertEqual(
            len({audit.source_note_index for audit in echo_audits}), len(echo_audits)
        )
        self.assertFalse(plan.to_manifest()["echoRecursive"])

    def test_echo_routes_to_first_free_track(self) -> None:
        plan = self.plan()
        self.assertEqual(plan.echo_track_index, 8)
        self.assertTrue(
            all(
                note.track == 8
                for note, audit in zip(plan.generated_notes, plan.note_audit)
                if audit.kind == "echo"
            )
        )
        self.assertTrue(
            all(
                note.track == 7
                for note, audit in zip(plan.generated_notes, plan.note_audit)
                if audit.kind != "echo"
            )
        )
        self.assertFalse(plan.to_manifest()["delayOnSourceTrack"])

    def test_existing_empty_track_is_selected_before_append(self) -> None:
        midi = MidiFile(
            self.midi.format_type,
            self.midi.ppq,
            self.midi.tracks + [MidiTrack([]), MidiTrack([])],
        )
        plan = self.plan(midi=midi)
        self.assertEqual(plan.echo_track_index, 8)
        result = apply_solo_enhancement(midi, plan)
        self.assertEqual(len(result.midi.tracks), 10)
        self.assertFalse(result.manifest["delayTrackCreated"])

    def test_echo_track_copies_sound_setup(self) -> None:
        result = apply_solo_enhancement(self.midi, self.plan())
        self.assertEqual(len(result.midi.tracks), 9)
        track = result.midi.tracks[8]
        self.assertTrue(
            any(event.kind == "meta" and event.meta_type == 0x03 and event.data == b"Solo Delay" for event in track.events)
        )
        self.assertTrue(any(event.command == 0xC0 and event.data == bytes((81,)) for event in track.events))
        controllers = {
            event.data[0]
            for event in track.events
            if event.command == 0xB0 and len(event.data) == 2
        }
        self.assertTrue({0, 7, 32}.issubset(controllers))
        self.assertTrue(result.manifest["delaySoundSetupCopied"])
        self.assertTrue(result.manifest["delayTrackCreated"])

    def test_disabling_echo_does_not_allocate_track(self) -> None:
        plan = self.plan(enable_echo=False)
        result = apply_solo_enhancement(self.midi, plan)
        self.assertIsNone(plan.echo_track_index)
        self.assertEqual(len(result.midi.tracks), len(self.midi.tracks))
        self.assertFalse(any(audit.kind == "echo" for audit in plan.note_audit))

    def test_smf0_without_free_track_blocks_echo(self) -> None:
        midi = MidiFile(0, self.midi.ppq, [MidiTrack(list(self.midi.tracks[7].events))])
        plan = self.plan(midi=midi, track_index=0)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("separate free track", plan.reason)

    def test_cc11_is_factory_bounded_and_smoothed(self) -> None:
        profile = self.profiles[self.config.profile_id]
        plan = self.plan()
        values = [point.value for point in plan.expression_points]
        self.assertTrue(values)
        self.assertTrue(
            all(profile.expression_min <= value <= profile.expression_max for value in values)
        )
        self.assertTrue(
            all(
                abs(right - left) <= profile.expression_max_step
                for left, right in zip(values, values[1:])
            )
        )
        self.assertTrue(all(event.data[0] == 11 for event in plan.expression_events))

    def test_existing_manual_cc11_is_preserved_without_new_curve(self) -> None:
        midi = self.midi.add_events(
            track_index=7,
            new_events=[
                MidiEvent(1920, -1, "channel", status=0xB0 | 14, data=bytes((11, 70)))
            ],
        )
        plan = self.plan(midi=midi)
        self.assertTrue(plan.manual_expression_preserved)
        self.assertEqual(plan.expression_events, ())

    def test_transformation_budget_blocks_excess(self) -> None:
        plan = self.plan(max_generated_notes=1)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("budget exceeded", plan.reason)

    def test_program_mismatch_requires_manual_review(self) -> None:
        profiles = dict(self.profiles)
        profiles[self.config.profile_id] = replace(
            profiles[self.config.profile_id], program=82
        )
        plan = self.plan(profiles=profiles)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("Program Change", plan.reason)

    def test_exact_bank_mismatch_requires_manual_review(self) -> None:
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        tracks[7].events = [
            replace(event, data=bytes((0, 5)))
            if event.command == 0xB0 and event.channel == 14 and event.data[0] == 0
            else event for event in tracks[7].events
        ]
        plan = self.plan(midi=MidiFile(self.midi.format_type, self.midi.ppq, tracks))
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("Bank Select", plan.reason)

    def test_program_lookup_is_scoped_to_selected_solo_track(self) -> None:
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        tracks[0].events.append(MidiEvent(0, 999, "channel", status=0xC0 | 14, data=bytes((99,))))
        midi = MidiFile(self.midi.format_type, self.midi.ppq, tracks)
        self.assertEqual(midi.program_at(14, 1920, 7), 81)
        plan = self.plan(midi=midi)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("shared by multiple source tracks", plan.reason)

    def test_external_one_based_track_and_channel_mapping(self) -> None:
        raw = dict(self.config.__dict__)
        raw.pop("track_index"); raw.pop("channel")
        raw["trackNumber"], raw["channelNumber"] = 8, 15
        mapped = SoloConfig.from_mapping(raw)
        self.assertEqual((mapped.track_index, mapped.channel), (7, 14))

    def test_ambiguous_zero_and_one_based_mapping_is_rejected(self) -> None:
        raw = dict(self.config.__dict__); raw["trackNumber"] = 8
        with self.assertRaisesRegex(ValueError, "either trackNumber"):
            SoloConfig.from_mapping(raw)

    def test_nonexistent_solo_track_requires_manual_review(self) -> None:
        plan = self.plan(track_index=15)
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("does not exist", plan.reason)

    def test_echo_does_not_allocate_seventeenth_track(self) -> None:
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        while len(tracks) < 16:
            tracks.append(MidiTrack([MidiEvent(0, 0, "meta", data=b"Occupied", meta_type=0x03)]))
        plan = self.plan(midi=MidiFile(1, self.midi.ppq, tracks))
        self.assertEqual(plan.decision, "MANUAL_REVIEW")
        self.assertIn("separate free track", plan.reason)

    def test_manifest_exposes_unambiguous_track_numbers(self) -> None:
        manifest = self.plan().to_manifest()
        self.assertEqual((manifest["sourceTrackIndex"], manifest["sourceTrackNumber"]), (7, 8))
        self.assertEqual((manifest["channelIndex"], manifest["channelNumber"]), (14, 15))
        self.assertTrue(all(item["trackNumber"] == item["trackIndex"] + 1 for item in manifest["notes"]))

    def test_missing_factory_profile_requires_manual_review(self) -> None:
        plan = self.plan(profiles={})
        self.assertEqual(plan.decision, "MANUAL_REVIEW")

    def test_no_solo_notes_returns_keep(self) -> None:
        plan = self.plan(start_tick=0, end_tick=1000)
        self.assertEqual(plan.decision, "KEEP")

    def test_velocity_changes_do_not_change_musical_plan(self) -> None:
        tracks = [MidiTrack(list(track.events)) for track in self.midi.tracks]
        changed = []
        for event in tracks[7].events:
            if event.is_note_on:
                changed.append(replace(event, data=bytes((event.data[0], 5))))
            else:
                changed.append(event)
        tracks[7].events = changed
        quiet = MidiFile(self.midi.format_type, self.midi.ppq, tracks)
        normal_plan = self.plan()
        quiet_plan = self.plan(midi=quiet)
        self.assertNotEqual(normal_plan.input_hash, quiet_plan.input_hash)
        self.assertEqual(normal_plan.structural_hash, quiet_plan.structural_hash)
        self.assertEqual(normal_plan.selection_hash, quiet_plan.selection_hash)
        self.assertEqual(normal_plan.generated_notes, quiet_plan.generated_notes)
        self.assertFalse(normal_plan.to_manifest()["analysisVelocityUsed"])

    def test_same_seed_is_deterministic(self) -> None:
        first = self.plan()
        second = self.plan()
        self.assertEqual(first.selection_hash, second.selection_hash)
        first_result = apply_solo_enhancement(self.midi, first)
        second_result = apply_solo_enhancement(self.midi, second)
        self.assertEqual(first_result.midi.to_bytes(), second_result.midi.to_bytes())

    def test_output_round_trip_has_valid_note_pairing(self) -> None:
        result = apply_solo_enhancement(self.midi, self.plan())
        parsed = MidiFile.from_bytes(result.midi.to_bytes())
        self.assertEqual(parsed.to_bytes(), result.midi.to_bytes())
        self.assertTrue(result.manifest["notePairingValid"])

    def test_program_and_original_velocity_are_preserved_after_apply(self) -> None:
        plan = self.plan()
        result = apply_solo_enhancement(self.midi, plan)
        self.assertEqual(result.midi.program_at(14, 1920), 81)
        after = {
            (note.start, note.end, note.pitch, note.velocity)
            for note in result.midi.notes()
            if note.track == 7 and note.channel == 14
        }
        self.assertTrue(set(plan.original_fingerprint).issubset(after))
        self.assertTrue(result.manifest["originalSoloPreserved"])
        self.assertTrue(result.manifest["programChangePreserved"])

    def test_generated_same_pitch_notes_do_not_overlap(self) -> None:
        plan = self.plan()
        by_pitch = {}
        for note in plan.generated_notes:
            by_pitch.setdefault((note.track, note.pitch), []).append(note)
        for notes in by_pitch.values():
            notes.sort(key=lambda note: note.start)
            self.assertTrue(
                all(left.end <= right.start for left, right in zip(notes, notes[1:]))
            )


if __name__ == "__main__":
    unittest.main()