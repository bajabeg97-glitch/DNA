from __future__ import annotations

import base64
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import dna_builder

from dna_midi_studio import (
    ChordCell,
    DncConfig,
    GuitarConfig,
    HarmonicConfig,
    MidiEvent,
    MidiFile,
    MidiTrack,
    PipelineConfig,
    ProductionAdapter,
    ProductionOptions,
    ReconstructionConfig,
    RxConfig,
    SoloConfig,
    apply_guitar_reconstruction,
    apply_harmonic_reconstruction,
    apply_reconstruction,
    apply_solo_enhancement,
    build_track_identities,
    execute_api_payload,
    execute_batch,
    execute_gui,
    execute_pipeline,
    execute_web,
    fingerprint_solo,
    plan_guitar_reconstruction,
    plan_harmonic_reconstruction,
    plan_reconstruction,
    plan_solo_enhancement,
    verify_solo_fingerprint,
)


ROOT = Path(__file__).resolve().parents[1]


def _midi(
    sound: tuple[int, int, int],
    channel: int,
    *,
    pitches: tuple[int, ...] = (60, 64),
    ppq: int = 480,
    format_type: int = 1,
) -> MidiFile:
    events = [
        MidiEvent(0, 0, "channel", 0xB0 | channel, bytes((0, sound[0]))),
        MidiEvent(0, 1, "channel", 0xB0 | channel, bytes((32, sound[1]))),
        MidiEvent(0, 2, "channel", 0xC0 | channel, bytes((sound[2],))),
    ]
    order = 3
    for index, pitch in enumerate(pitches):
        start = 120 + index * 360
        events.append(MidiEvent(start, order, "channel", 0x90 | channel, bytes((pitch, 72 + index))))
        order += 1
        events.append(MidiEvent(start + 180, order, "channel", 0x80 | channel, bytes((pitch, 0))))
        order += 1
    return MidiFile(format_type, ppq, [MidiTrack(events)])


class Session17ProductionAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.adapter = ProductionAdapter(ROOT)
        factory, gold = dna_builder.read_nested_archive(ROOT / "prism-uploads" / "DNA.zip")
        cls.factory_files = dict(factory)
        cls.gold_files = dict(gold)
        cls.acoustic = MidiFile.from_bytes(
            cls.factory_files["Workspace_Styles/Acoustic Bld/Acoustic Bld_3_4_Var1.mid"]
        )
        cls.analog = MidiFile.from_bytes(
            cls.factory_files["Workspace_Styles/Analog Beat 2/Analog Beat 2_Intro2.mid"]
        )

    def test_01_catalog_has_all_five_production_registries(self) -> None:
        catalog = self.adapter.catalog()
        self.assertEqual(len(catalog["registries"]), 5)
        self.assertEqual(catalog["counts"]["factoryProfiles"], 1964)
        self.assertEqual(catalog["counts"]["goldPerformance"], 12918)

    def test_02_catalog_preserves_known_production_counts(self) -> None:
        counts = self.adapter.catalog()["counts"]
        self.assertEqual(counts["factorySegments"], 26922)
        self.assertEqual(counts["factoryStrumming"], 2919)
        self.assertEqual(counts["goldLegacy"], 10637)
        self.assertEqual(counts["drumBassRelationships"], 4373)

    def test_03_catalog_hashes_match_registry_bytes(self) -> None:
        for item in self.adapter.catalog()["registries"].values():
            self.assertEqual(item["sha256"], sha256((ROOT / item["path"]).read_bytes()).hexdigest())

    def test_04_gold_authority_flags_are_all_false(self) -> None:
        invariants = self.adapter.catalog()["invariants"]
        self.assertFalse(invariants["goldAffectsVelocity"])
        self.assertFalse(invariants["goldAffectsBankSelect"])
        self.assertFalse(invariants["goldAffectsProgramChange"])
        self.assertFalse(invariants["goldControlsRhythmGuitar"])

    def test_05_production_options_reject_unknown_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown production"):
            ProductionOptions.from_mapping({"version": "1.0", "writeMidi": True})

    def test_06_production_options_require_version_1(self) -> None:
        with self.assertRaisesRegex(ValueError, "version 1.0"):
            ProductionOptions.from_mapping({"version": "2.0"})

    def test_07_production_options_bound_pattern_count(self) -> None:
        for value in (0, 33):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "1..32"):
                ProductionOptions.from_mapping({"version": "1.0", "maxPatterns": value})

    def test_08_pipeline_requires_exactly_one_registry_source(self) -> None:
        base = {"engine": "drum", "config": {}}
        for stage in (base, {**base, "registry": "x", "production": {"version": "1.0"}}):
            with self.subTest(stage=stage), self.assertRaisesRegex(ValueError, "exactly one"):
                PipelineConfig.from_mapping({"version": "1.0", "stages": [stage]})

    def test_09_wrong_track_uid_blocks_before_registry_mapping(self) -> None:
        config = ReconstructionConfig(0, 9, "drums", "body", 0, 960, 1, 50, expected_program=34)
        midi = _midi((120, 0, 34), 9)
        bundle = self.adapter.adapt(
            "drum", midi, config,
            {"version": "1.0", "expectedTrackUid": "trk-00000000000000000000"},
        )
        self.assertFalse(bundle.allowed)
        self.assertIn("trackUid", bundle.reason)

    def test_10_incomplete_sound_binding_blocks(self) -> None:
        midi = MidiFile(1, 480, [MidiTrack([
            MidiEvent(0, 0, "channel", 0xC9, bytes((34,)))
        ])])
        config = ReconstructionConfig(0, 9, "drums", "body", 0, 960, 1, 50, expected_program=34)
        bundle = self.adapter.adapt("drum", midi, config, {"version": "1.0"})
        self.assertFalse(bundle.allowed)
        self.assertIn("unresolved", bundle.reason)

    def test_11_mid_window_program_change_blocks(self) -> None:
        midi = _midi((120, 0, 34), 9)
        midi.tracks[0].events.append(MidiEvent(600, 99, "channel", 0xC9, bytes((35,))))
        config = ReconstructionConfig(0, 9, "drums", "body", 0, 960, 1, 50, expected_program=34)
        bundle = self.adapter.adapt("drum", midi, config, {"version": "1.0"})
        self.assertFalse(bundle.allowed)
        self.assertIn("inside", bundle.reason)

    def test_12_shared_channel_is_blocked_without_approval(self) -> None:
        midi = _midi((120, 0, 34), 9)
        midi.tracks.append(MidiTrack([MidiEvent(0, 0, "channel", 0xC9, bytes((34,)))]))
        config = ReconstructionConfig(0, 9, "drums", "body", 0, 960, 1, 50, expected_program=34)
        bundle = self.adapter.adapt("drum", midi, config, {"version": "1.0"})
        self.assertFalse(bundle.allowed)
        self.assertIn("shared", bundle.reason)

    def test_13_shared_channel_approval_is_audited(self) -> None:
        midi = _midi((120, 0, 34), 9)
        midi.tracks.append(MidiTrack([MidiEvent(0, 0, "channel", 0xC9, bytes((34,)))]))
        config = ReconstructionConfig(0, 9, "drums", "body", 0, 960, 1, 50, expected_program=34)
        bundle = self.adapter.adapt(
            "drum", midi, config, {"version": "1.0", "allowSharedChannel": True}
        )
        self.assertTrue(bundle.allowed)
        self.assertTrue(bundle.manifest["preflight"]["sharedChannelApproved"])

    def test_14_smf0_merge_is_reported(self) -> None:
        midi = _midi((120, 0, 34), 9, format_type=0)
        midi.tracks[0].events.append(MidiEvent(0, 90, "channel", 0xC0, bytes((1,))))
        config = ReconstructionConfig(0, 9, "drums", "body", 0, 960, 1, 50, expected_program=34)
        bundle = self.adapter.adapt("drum", midi, config, {"version": "1.0"})
        self.assertTrue(bundle.allowed)
        self.assertTrue(bundle.manifest["preflight"]["smf0Merged"])

    def test_15_real_factory_drum_track_gets_production_patterns(self) -> None:
        config = ReconstructionConfig(1, 9, "drums", "body", 0, 1440, 17, 50,
                                      expected_program=48)
        bundle = self.adapter.adapt(
            "drum", self.acoustic, config,
            {"version": "1.0", "maxPatterns": 4, "allowSharedChannel": True},
        )
        self.assertTrue(bundle.allowed)
        self.assertEqual(len(bundle.loaded[0]), 4)
        self.assertTrue(bundle.manifest["factoryProfileIds"])

    def test_16_drum_render_uses_only_factory_velocity_profiles(self) -> None:
        config = ReconstructionConfig(1, 9, "drums", "body", 0, 1440, 17, 50,
                                      expected_program=48)
        bundle = self.adapter.adapt(
            "drum", self.acoustic, config,
            {"version": "1.0", "maxPatterns": 4, "allowSharedChannel": True},
        )
        plan = plan_reconstruction(self.acoustic, *bundle.loaded, config)
        profiles = bundle.loaded[1]
        self.assertEqual(plan.decision, "REPLACE")
        for note in plan.generated_notes:
            self.assertEqual(note.velocity, profiles[note.pitch].velocity(config.intensity))

    def test_17_percussion_adapter_accepts_only_fully_profiled_patterns(self) -> None:
        midi = _midi((120, 0, 34), 10)
        config = ReconstructionConfig(0, 10, "percussion", "body", 0, 1920, 2, 40,
                                      expected_program=34)
        bundle = self.adapter.adapt("drum", midi, config, {"version": "1.0", "maxPatterns": 3})
        self.assertTrue(bundle.allowed)
        patterns, profiles = bundle.loaded
        self.assertTrue(all({event.note for event in pattern.events} <= set(profiles) for pattern in patterns))
        self.assertTrue(all(event.element == "percussion" for pattern in patterns for event in pattern.events))

    def test_18_real_factory_bass_track_renders_relative_gold(self) -> None:
        config = HarmonicConfig(3, 8, "bass", "intro", 0, 2976, 3, 55,
                                "417.343.366", require_relationship=False)
        bundle = self.adapter.adapt(
            "harmonic", self.analog, config,
            {"version": "1.0", "maxPatterns": 8, "allowSharedChannel": True},
        )
        plan = plan_harmonic_reconstruction(
            self.analog, *bundle.loaded, [ChordCell(0, 2976, 0, "major")], config
        )
        self.assertEqual(plan.decision, "REPLACE")
        self.assertTrue(plan.generated_notes)

    def test_19_harmonic_patterns_contain_relative_intervals_only(self) -> None:
        config = HarmonicConfig(3, 8, "bass", "intro", 0, 2976, 3, 55,
                                "417.343.366", require_relationship=False)
        bundle = self.adapter.adapt(
            "harmonic", self.analog, config,
            {"version": "1.0", "maxPatterns": 8, "allowSharedChannel": True},
        )
        self.assertTrue(all(
            0 <= interval <= 24
            for pattern in bundle.loaded[0]
            for event in pattern.events
            for interval in event.intervals
        ))
        self.assertFalse(bundle.manifest["goldContainsAbsolutePitch"])

    def test_20_drum_bass_relationship_has_no_dynamics_authority(self) -> None:
        relationships = self.adapter.documents["goldPerformance"]["relationships"]
        relation = relationships[0]
        bass_id = relation["patterns"]["bass"]
        bass_pattern = next(
            item for item in self.adapter.documents["goldPerformance"]["patterns"]
            if item["id"] == bass_id
        )
        raw_profile = next(item for item in self.adapter.factory_profiles.values() if item["role"] == "bass")
        midi = _midi((raw_profile["bankMsb"], raw_profile["bankLsb"], raw_profile["program"]), 8)
        config = HarmonicConfig(
            0, 8, "bass", bass_pattern["sourceSection"], 0, 1920, 4, 50,
            raw_profile["id"], meter=bass_pattern["meter"],
            selected_drum_pattern_id=relation["patterns"]["drums"], require_relationship=True,
        )
        bundle = self.adapter.adapt("harmonic", midi, config, {"version": "1.0", "maxPatterns": 32})
        self.assertTrue(bundle.allowed)
        self.assertIn(bass_id, {item.pattern_id for item in bundle.loaded[0]})
        self.assertTrue(bundle.loaded[2])
        self.assertEqual(relation["dynamicsEvidence"], "none")

    def _guitar_case(self) -> tuple[MidiFile, GuitarConfig, list[ChordCell]]:
        midi = _midi((121, 13, 28), 11, ppq=192)
        config = GuitarConfig(0, 11, "body", 0, 1536, 5, 50,
                              "388.910.492", enable_controls=False)
        return midi, config, [ChordCell(0, 1536, 0, "major")]

    def test_21_guitar_adapter_uses_factory_strumming_only(self) -> None:
        midi, config, _ = self._guitar_case()
        bundle = self.adapter.adapt("guitar", midi, config, {"version": "1.0", "maxPatterns": 4})
        self.assertTrue(bundle.allowed)
        self.assertFalse(bundle.manifest["goldControlsRhythmGuitar"])
        self.assertTrue(all(item.source_ids for item in bundle.loaded[0]))

    def test_22_guitar_render_is_playable_and_factory_bounded(self) -> None:
        midi, config, chords = self._guitar_case()
        bundle = self.adapter.adapt("guitar", midi, config, {"version": "1.0", "maxPatterns": 4})
        plan = plan_guitar_reconstruction(midi, *bundle.loaded, chords, config)
        applied = apply_guitar_reconstruction(midi, plan)
        self.assertEqual(plan.decision, "REPLACE")
        self.assertTrue(applied.manifest["notePairingValid"])
        self.assertLessEqual(plan.max_fret_span, bundle.loaded[1][config.profile_id].max_fret_span)

    def test_23_unconfirmed_guitar_controls_are_device_blocked(self) -> None:
        midi, config, _ = self._guitar_case()
        config = GuitarConfig(**{**config.__dict__, "enable_controls": True})
        bundle = self.adapter.adapt("guitar", midi, config, {"version": "1.0"})
        self.assertFalse(bundle.allowed)
        self.assertEqual(bundle.status, "DEVICE_BLOCKED_MISSING_CONFIRMED_GUITAR_CONTROL_MAP")

    def _solo_case(self) -> tuple[MidiFile, SoloConfig, list[ChordCell]]:
        midi = _midi((120, 0, 64), 14)
        config = SoloConfig(0, 14, 0, 960, 6, 50, "698.771.685",
                            enable_third=False, enable_echo=False)
        return midi, config, [ChordCell(0, 960, 0, "major")]

    def test_24_solo_adapter_is_explicitly_expression_only(self) -> None:
        midi, config, _ = self._solo_case()
        bundle = self.adapter.adapt("solo", midi, config, {"version": "1.0"})
        self.assertTrue(bundle.allowed)
        self.assertEqual(bundle.status, "PRODUCTION_PARTIAL_FACTORY_EXPRESSION_ONLY")
        self.assertEqual((bundle.loaded[0], bundle.loaded[1]), ([], []))

    def test_25_solo_render_preserves_original_note_fingerprint(self) -> None:
        midi, config, chords = self._solo_case()
        before = fingerprint_solo(midi, track_index=0, channel=14, start_tick=0, end_tick=960)
        bundle = self.adapter.adapt("solo", midi, config, {"version": "1.0"})
        plan = plan_solo_enhancement(midi, *bundle.loaded, chords, config)
        applied = apply_solo_enhancement(midi, plan)
        self.assertEqual(plan.counts["cc11"], 2)
        self.assertTrue(verify_solo_fingerprint(before, applied.midi)["passed"])

    def test_26_rx_production_stage_is_device_blocked(self) -> None:
        midi, _, _ = self._solo_case()
        config = RxConfig(0, 14, 0, 960, 1, 50, "111.111.111", "698.771.685", ("release",))
        bundle = self.adapter.adapt("rx", midi, config, {"version": "1.0"})
        self.assertFalse(bundle.allowed)
        self.assertEqual(bundle.status, "DEVICE_BLOCKED_MISSING_CONFIRMED_RX_MAP")

    def test_27_dnc_production_stage_is_device_blocked(self) -> None:
        midi, _, _ = self._solo_case()
        config = DncConfig(0, 14, 0, 960, 1, 50, "solo", "111.111.111",
                           "698.771.685", ("release",))
        bundle = self.adapter.adapt("dnc", midi, config, {"version": "1.0"})
        self.assertFalse(bundle.allowed)
        self.assertEqual(bundle.status, "DEVICE_BLOCKED_MISSING_CONFIRMED_DNC_MAP")

    def test_28_pipeline_records_production_stage_diff(self) -> None:
        midi, config, chords = self._guitar_case()
        raw = {"version": "1.0", "stages": [{
            "engine": "guitar", "production": {"version": "1.0", "maxPatterns": 4},
            "config": dict(config.__dict__),
            "context": {"chords": [dict(item.__dict__) for item in chords]},
        }]}
        result = execute_pipeline(midi.to_bytes(), raw, ROOT)
        stage = result.manifest["stages"][0]
        self.assertTrue(stage["stageDiff"]["changed"])
        self.assertTrue(stage["stageDiff"]["rollbackSafe"])
        self.assertEqual(stage["productionAdapter"]["status"], "PRODUCTION_ADAPTER_VALIDATED")

    def test_29_blocked_stage_is_byte_exact_rollback(self) -> None:
        midi, _, _ = self._solo_case()
        config = RxConfig(0, 14, 0, 960, 1, 50, "111.111.111", "698.771.685", ("release",))
        raw = {"version": "1.0", "stages": [{
            "engine": "rx", "production": {"version": "1.0"}, "config": dict(config.__dict__)
        }]}
        result = execute_pipeline(midi.to_bytes(), raw, ROOT)
        stage = result.manifest["stages"][0]
        self.assertEqual(result.midi, midi.to_bytes())
        self.assertFalse(stage["stageDiff"]["changed"])
        self.assertEqual(stage["decision"], "MANUAL_REVIEW")

    def test_30_production_transport_parity_is_byte_identical(self) -> None:
        midi, config, chords = self._guitar_case()
        raw = {"version": "1.0", "stages": [{
            "engine": "guitar", "production": {"version": "1.0", "maxPatterns": 4},
            "config": dict(config.__dict__),
            "context": {"chords": [dict(item.__dict__) for item in chords]},
        }]}
        direct = execute_pipeline(midi.to_bytes(), raw, ROOT)
        web = execute_web(midi.to_bytes(), raw, ROOT)
        gui = execute_gui(midi.to_bytes(), raw, ROOT)
        batch = execute_batch([(midi.to_bytes(), raw)], ROOT)[0]
        api = execute_api_payload({
            "midiBase64": base64.b64encode(midi.to_bytes()).decode("ascii"), "config": raw
        }, ROOT)
        self.assertEqual(
            {direct.manifest["outputHash"], web.manifest["outputHash"],
             gui.manifest["outputHash"], batch.manifest["outputHash"],
             api["manifest"]["outputHash"]},
            {direct.manifest["outputHash"]},
        )

    def _assert_real_gold_song_fails_safe(self, name: str, channel: int, profile_id: str) -> None:
        raw_midi = self.gold_files[name]
        midi = MidiFile.from_bytes(raw_midi)
        config = SoloConfig(0, channel, 100, 1000, 9, 50, profile_id,
                            enable_third=False, enable_echo=False)
        raw = {"version": "1.0", "stages": [{
            "engine": "solo", "production": {"version": "1.0"},
            "config": dict(config.__dict__), "context": {"chords": []},
        }]}
        result = execute_pipeline(raw_midi, raw, ROOT)
        self.assertEqual(result.midi, raw_midi)
        self.assertEqual(result.manifest["stages"][0]["decision"], "MANUAL_REVIEW")
        self.assertTrue(result.manifest["stages"][0]["productionAdapter"]["preflight"]["smf0Merged"])

    def test_31_real_gold_song_one_fails_safe_without_role_guessing(self) -> None:
        self._assert_real_gold_song_fails_safe(
            "Gold DNA/EVO DZEPA-KNINDZA UZIVO.MID", 0, "097.023.601"
        )

    def test_32_real_gold_song_two_fails_safe_without_role_guessing(self) -> None:
        self._assert_real_gold_song_fails_safe(
            "Gold DNA/GORA SE DRMALA RODIO SE MIS-KALESISKI DIJ UZIVO.MID", 3, "952.330.518"
        )

    def test_33_real_gold_song_three_fails_safe_without_role_guessing(self) -> None:
        self._assert_real_gold_song_fails_safe(
            "Gold DNA/GRMEC I UNA-GR,DRVAR UZIVO.MID", 3, "840.775.369"
        )

    def test_34_existing_engine_apply_contracts_remain_valid(self) -> None:
        drum_config = ReconstructionConfig(1, 9, "drums", "body", 0, 1440, 17, 50,
                                            expected_program=48)
        drum_bundle = self.adapter.adapt(
            "drum", self.acoustic, drum_config,
            {"version": "1.0", "maxPatterns": 4, "allowSharedChannel": True},
        )
        drum_plan = plan_reconstruction(self.acoustic, *drum_bundle.loaded, drum_config)
        self.assertTrue(apply_reconstruction(self.acoustic, drum_plan).manifest["programChangePreserved"])

        harmonic_config = HarmonicConfig(3, 8, "bass", "intro", 0, 2976, 3, 55,
                                         "417.343.366", require_relationship=False)
        harmonic_bundle = self.adapter.adapt(
            "harmonic", self.analog, harmonic_config,
            {"version": "1.0", "maxPatterns": 8, "allowSharedChannel": True},
        )
        harmonic_plan = plan_harmonic_reconstruction(
            self.analog, *harmonic_bundle.loaded,
            [ChordCell(0, 2976, 0, "major")], harmonic_config,
        )
        self.assertTrue(apply_harmonic_reconstruction(
            self.analog, harmonic_plan
        ).manifest["programChangePreserved"])

    def test_35_cli_uses_the_same_production_dispatcher(self) -> None:
        midi, config, chords = self._guitar_case()
        raw = {"version": "1.0", "stages": [{
            "engine": "guitar", "production": {"version": "1.0", "maxPatterns": 4},
            "config": dict(config.__dict__),
            "context": {"chords": [dict(item.__dict__) for item in chords]},
        }]}
        direct = execute_pipeline(midi.to_bytes(), raw, ROOT)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            input_path = base / "in.mid"
            config_path = base / "config.json"
            output_path = base / "out.mid"
            manifest_path = base / "out.json"
            input_path.write_bytes(midi.to_bytes())
            config_path.write_text(json.dumps(raw), encoding="utf-8")
            completed = subprocess.run(
                ["python", "session9_pipeline.py", "--input", str(input_path),
                 "--config", str(config_path), "--output", str(output_path),
                 "--manifest", str(manifest_path)],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(output_path.read_bytes(), direct.midi)
            self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), direct.manifest)


if __name__ == "__main__":
    unittest.main()