from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import wave
from io import BytesIO

from dna_midi_studio import (
    AUDIO_RENDER_ADAPTER_SCHEMA,
    AUDIO_RENDER_ADAPTER_VERSION,
    DEVICE_AUDIO_CAPTURE_SCHEMA,
    DEVICE_AUDIO_CAPTURE_VERSION,
    EXTERNAL_ADAPTER_MODES,
    PREVIEW_CONTROLS_VERSION,
    PREVIEW_PROFILES,
    PREVIEW_SESSION_SCHEMA,
    PREVIEW_SESSION_VERSION,
    PREVIEW_VARIANTS,
    PreviewControls,
    build_preview_session,
    compare_device_audio_capture,
    execute_preview_session_api,
    execute_preview_session_gui,
    import_device_audio_capture,
    render_preview_wav,
    validate_audio_render_adapter,
    validate_device_audio_capture,
    validate_preview_session_v2,
)
from dna_midi_studio.premium_preview import _hash_without
from dna_midi_studio.session26_fixture import build_session26_chain


ROOT = Path(__file__).resolve().parents[1]


class Session26PremiumPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (cls.midi, cls.song_map, cls.groove, cls.expression, cls.articulations,
         cls.verdict, cls.controls, cls.session, cls.wav, cls.audio_manifest,
         cls.capture_metadata) = build_session26_chain()
        cls.capture = import_device_audio_capture(cls.wav, cls.capture_metadata)

    def rebuild(self, controls=None, verdict="default", expression="default", articulations="default"):
        return build_preview_session(
            self.midi, self.song_map, self.groove,
            self.expression if expression == "default" else expression,
            self.articulations if articulations == "default" else articulations,
            self.verdict if verdict == "default" else verdict,
            self.controls if controls is None else controls,
        )

    def test_01_contract_constants(self):
        self.assertEqual((PREVIEW_SESSION_SCHEMA, PREVIEW_SESSION_VERSION),
                         ("dna-premium-preview-session", "2.0"))

    def test_02_capture_constants(self):
        self.assertEqual((DEVICE_AUDIO_CAPTURE_SCHEMA, DEVICE_AUDIO_CAPTURE_VERSION),
                         ("dna-premium-device-audio-capture", "1.0"))

    def test_03_adapter_constants(self):
        self.assertEqual((AUDIO_RENDER_ADAPTER_SCHEMA, AUDIO_RENDER_ADAPTER_VERSION),
                         ("dna-premium-audio-render-adapter", "1.0"))

    def test_04_control_version(self):
        self.assertEqual(PREVIEW_CONTROLS_VERSION, "1.0")

    def test_05_variants(self):
        self.assertEqual(PREVIEW_VARIANTS, ("A", "B", "C"))

    def test_06_profiles(self):
        self.assertEqual(set(PREVIEW_PROFILES), {"GM_PROXY_V1", "PA800_PROXY_V1"})

    def test_07_adapter_modes(self):
        self.assertEqual(set(EXTERNAL_ADAPTER_MODES), {"DISABLED", "SOUNDFONT", "COMMAND"})

    def test_08_preview_schema_strict(self):
        schema = json.loads((ROOT / "premium/schemas/v2/preview-session-v2.schema.json").read_text())
        self.assertIs(schema["additionalProperties"], False)

    def test_09_preview_schema_read_only(self):
        schema = json.loads((ROOT / "premium/schemas/v2/preview-session-v2.schema.json").read_text())
        self.assertEqual(schema["properties"]["midiMutationAllowed"]["const"], False)

    def test_10_capture_schema_cannot_certify(self):
        schema = json.loads((ROOT / "premium/schemas/v2/device-audio-capture-v1.schema.json").read_text())
        self.assertEqual(schema["properties"]["certificationAllowed"]["const"], False)

    def test_11_adapter_schema_has_no_path(self):
        schema = json.loads((ROOT / "premium/schemas/v2/audio-render-adapter-v1.schema.json").read_text())
        self.assertNotIn("path", schema["properties"])

    def test_12_session_validates(self):
        self.assertIsNone(validate_preview_session_v2(self.session))

    def test_13_session_hash(self):
        self.assertEqual(self.session["previewSessionHash"],
                         _hash_without(self.session, "previewSessionHash"))

    def test_14_session_read_only(self):
        self.assertTrue(self.session["readOnly"])

    def test_15_session_never_generates_final_midi(self):
        self.assertFalse(self.session["finalMidiGenerated"])

    def test_16_session_never_mutates_midi(self):
        self.assertFalse(self.session["midiMutationAllowed"])

    def test_17_preview_cannot_mutate_verdict(self):
        self.assertFalse(self.session["validatorVerdictMutableByPreview"])

    def test_18_preview_cannot_certify_device(self):
        self.assertFalse(self.session["pa800DeviceCertified"])

    def test_19_source_hash_is_exact(self):
        self.assertEqual(self.session["source"]["midiSha256"], self.midi.digest())

    def test_20_validator_hash_is_exact(self):
        self.assertEqual(self.session["validatorIdentity"]["finalMidiSha256"], self.midi.digest())

    def test_21_validator_scope_preserved(self):
        self.assertEqual(self.session["validatorIdentity"]["scope"], "FINAL_MIDI")

    def test_22_three_variants(self):
        self.assertEqual([item["variantId"] for item in self.session["variants"]], ["A", "B", "C"])

    def test_23_synchronized_clock(self):
        self.assertEqual(len({item["clockId"] for item in self.session["variants"]}), 1)

    def test_24_transport_synchronized(self):
        self.assertTrue(self.session["transport"]["synchronized"])

    def test_25_section_loop_enabled(self):
        self.assertTrue(self.session["transport"]["loop"]["enabled"])

    def test_26_section_loop_exact(self):
        self.assertEqual(self.session["transport"]["loop"]["sectionId"], "sec-001")

    def test_27_baseline_contains_only_source_notes(self):
        self.assertEqual({n["source"] for n in self.session["variants"][0]["notes"]}, {"ORIGINAL_MIDI"})

    def test_28_expression_variant_adds_notes(self):
        self.assertGreater(len(self.session["variants"][1]["notes"]),
                           len(self.session["variants"][0]["notes"]))

    def test_29_full_variant_contains_echo(self):
        self.assertIn("echo", {n["layer"] for n in self.session["variants"][2]["notes"]})

    def test_30_expression_variant_excludes_echo(self):
        self.assertNotIn("echo", {n["layer"] for n in self.session["variants"][1]["notes"]})

    def test_31_articulations_only_full_variant(self):
        self.assertEqual([len(v["articulationEvents"]) for v in self.session["variants"]][:2], [0, 0])

    def test_32_articulation_annotations_are_not_proxy_audio(self):
        self.assertTrue(all(not e["audibleProxy"] for e in self.session["variants"][2]["articulationEvents"]))

    def test_33_all_notes_have_track_uid(self):
        self.assertTrue(self.session["audit"]["exactTrackUidCoverage"])

    def test_34_all_notes_have_sound_binding(self):
        self.assertTrue(self.session["audit"]["soundBindingCoverage"])

    def test_35_all_notes_have_layer_source(self):
        self.assertTrue(self.session["audit"]["layerSourceCoverage"])

    def test_36_full_duration_peak_computed(self):
        self.assertGreater(self.session["audit"]["maximumFullDurationPeak"], 0)

    def test_37_all_variants_under_ceiling(self):
        self.assertTrue(self.session["audit"]["allVariantsWithinMidiNoteCeiling"])

    def test_38_polyphony_per_role(self):
        self.assertTrue(any(v["polyphony"]["byRole"] for v in self.session["variants"]))

    def test_39_polyphony_per_track(self):
        self.assertTrue(all(v["polyphony"]["byTrackUid"] for v in self.session["variants"]))

    def test_40_polyphony_per_channel(self):
        self.assertTrue(all(v["polyphony"]["byChannelNumber"] for v in self.session["variants"]))

    def test_41_loudness_is_proxy(self):
        self.assertTrue(all(v["loudness"]["method"] == "MIDI_ENERGY_RMS_PROXY" for v in self.session["variants"]))

    def test_42_loudness_never_affects_midi(self):
        self.assertTrue(all(not v["loudness"]["affectsMidi"] for v in self.session["variants"]))

    def test_43_audio_types_are_separated(self):
        self.assertEqual(set(self.session["audioSeparation"]),
                         {"midiStructuralPreview", "builtinAudio", "externalRenderer", "deviceCapture"})

    def test_44_warning_disclaims_certification(self):
        self.assertTrue(any("not a Korg Pa800 certification" in item for item in self.session["warnings"]))
        self.assertIn("/api/premium-preview-session", (ROOT / "server.py").read_text())
        self.assertIn("PREMIUM PREVIEW 2.0", (ROOT / "web_gui.py").read_text())

    def test_45_deterministic_session(self):
        self.assertEqual(self.session, self.rebuild())

    def test_46_profile_change_preserves_midi_hash(self):
        controls = deepcopy(self.controls); controls["profileId"] = "GM_PROXY_V1"
        self.assertEqual(self.rebuild(controls)["source"]["midiSha256"], self.midi.digest())

    def test_47_profile_change_preserves_validator_identity(self):
        controls = deepcopy(self.controls); controls["profileId"] = "GM_PROXY_V1"
        self.assertEqual(self.rebuild(controls)["validatorIdentity"], self.session["validatorIdentity"])

    def test_48_volume_change_preserves_validator_identity(self):
        controls = deepcopy(self.controls); controls["masterGainDb"] = -9.0
        self.assertEqual(self.rebuild(controls)["validatorIdentity"], self.session["validatorIdentity"])

    def test_49_loudness_target_preserves_validator_identity(self):
        controls = deepcopy(self.controls); controls["targetRmsDbfs"] = -16.0
        self.assertEqual(self.rebuild(controls)["validatorIdentity"], self.session["validatorIdentity"])

    def test_50_loop_change_preserves_validator_identity(self):
        controls = deepcopy(self.controls); controls["loopSectionId"] = "sec-002"
        self.assertEqual(self.rebuild(controls)["validatorIdentity"], self.session["validatorIdentity"])

    def test_51_mute_filters_audio_not_note_audit(self):
        controls = deepcopy(self.controls); controls["mutedRoles"] = ["melody"]
        changed = self.rebuild(controls)
        self.assertEqual(len(changed["variants"][0]["notes"]), len(self.session["variants"][0]["notes"]))

    def test_52_solo_filter_reduces_audible_count(self):
        controls = deepcopy(self.controls); controls["soloRoles"] = ["melody"]
        changed = self.rebuild(controls)
        self.assertLess(changed["variants"][0]["audibleNoteCount"], len(changed["variants"][0]["notes"]))

    def test_53_one_click_baseline_is_variant_a(self):
        self.assertEqual(self.session["variants"][0]["label"], "Verified baseline")

    def test_54_render_wav_is_deterministic(self):
        self.assertEqual(self.wav, render_preview_wav(self.session, "C")[0])

    def test_55_render_manifest_is_deterministic(self):
        self.assertEqual(self.audio_manifest, render_preview_wav(self.session, "C")[1])

    def test_56_render_is_valid_wav(self):
        with wave.open(BytesIO(self.wav), "rb") as source:
            self.assertEqual((source.getnchannels(), source.getsampwidth()), (1, 2))

    def test_57_render_sha_matches(self):
        self.assertEqual(self.audio_manifest["wavSha256"], sha256(self.wav).hexdigest())

    def test_58_render_is_not_device_audio(self):
        self.assertFalse(self.audio_manifest["deviceAudio"])

    def test_59_render_cannot_certify(self):
        self.assertFalse(self.audio_manifest["pa800DeviceCertified"])

    def test_60_render_preserves_validator_hash(self):
        self.assertEqual(self.audio_manifest["validatorIdentityHash"],
                         self.session["validatorIdentity"]["identityHash"])

    def test_61_capture_validates(self):
        self.assertIsNone(validate_device_audio_capture(self.capture))

    def test_62_capture_hash(self):
        self.assertEqual(self.capture["captureHash"], _hash_without(self.capture, "captureHash"))

    def test_63_capture_exact_device(self):
        self.assertEqual(self.capture["device"]["model"], "Pa800")

    def test_64_capture_is_comparison_only(self):
        self.assertEqual(self.capture["authority"], "DEVICE_AUDIO_COMPARISON_ONLY")

    def test_65_capture_cannot_certify(self):
        self.assertFalse(self.capture["certificationAllowed"])

    def test_66_capture_has_wav_metrics(self):
        self.assertGreater(self.capture["audio"]["durationSeconds"], 0)

    def test_67_comparison_matches_source(self):
        result = compare_device_audio_capture(self.session, "C", self.capture, self.audio_manifest)
        self.assertTrue(result["sameSourceMidi"])

    def test_68_comparison_cannot_certify(self):
        result = compare_device_audio_capture(self.session, "C", self.capture, self.audio_manifest)
        self.assertFalse(result["certificationAllowed"])

    def test_69_comparison_is_deterministic(self):
        first = compare_device_audio_capture(self.session, "C", self.capture, self.audio_manifest)
        second = compare_device_audio_capture(self.session, "C", self.capture, self.audio_manifest)
        self.assertEqual(first, second)

    def test_70_api_plan_parity(self):
        payload = {"action": "plan", "midiHex": self.midi.to_bytes().hex(), "songMap": self.song_map,
                   "groovePlan": self.groove, "expressionPlan": self.expression,
                   "articulationPlans": self.articulations,
                   "controls": self.controls}
        api = execute_preview_session_api(payload)
        self.assertEqual(api["validatorIdentity"]["scope"], "STRUCTURAL_PREVIEW_ONLY")
        self.assertEqual(execute_preview_session_gui(payload), api)

    def test_71_api_rejects_client_validator_authority(self):
        payload = {"action": "plan", "midiHex": self.midi.to_bytes().hex(), "songMap": self.song_map,
                   "groovePlan": self.groove, "expressionPlan": self.expression,
                   "articulationPlans": self.articulations,
                   "controls": self.controls, "validatorVerdict": self.verdict}
        with self.assertRaisesRegex(ValueError, "Unknown preview API"):
            execute_preview_session_api(payload)

    def test_72_api_capture_parity(self):
        payload = {"action": "capture", "wavHex": self.wav.hex(), "metadata": self.capture_metadata}
        self.assertEqual(execute_preview_session_api(payload), self.capture)

    def test_73_cli_help(self):
        completed = subprocess.run(["python", "session26_premium_preview.py", "--help"], cwd=ROOT,
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_74_reject_unknown_control_field(self):
        controls = deepcopy(self.controls); controls["midiWriter"] = True
        with self.assertRaisesRegex(ValueError, "Unknown preview control"):
            self.rebuild(controls)

    def test_75_reject_unknown_profile(self):
        controls = deepcopy(self.controls); controls["profileId"] = "REAL_PA800"
        with self.assertRaisesRegex(ValueError, "Unsupported preview profile"):
            self.rebuild(controls)

    def test_76_reject_duplicate_variant(self):
        controls = deepcopy(self.controls); controls["variants"] = ["A", "A"]
        with self.assertRaisesRegex(ValueError, "duplicates"):
            self.rebuild(controls)

    def test_77_reject_invalid_loop(self):
        controls = deepcopy(self.controls); controls["loopSectionId"] = "missing"
        with self.assertRaisesRegex(ValueError, "section ID"):
            self.rebuild(controls)

    def test_78_reject_missing_loop(self):
        controls = deepcopy(self.controls); controls["loopSectionId"] = "sec-999"
        with self.assertRaisesRegex(ValueError, "not present"):
            self.rebuild(controls)

    def test_79_reject_solo_mute_conflict(self):
        controls = deepcopy(self.controls); controls["soloRoles"] = ["solo"]; controls["mutedRoles"] = ["solo"]
        with self.assertRaisesRegex(ValueError, "both soloed and muted"):
            self.rebuild(controls)

    def test_80_reject_excess_gain(self):
        controls = deepcopy(self.controls); controls["masterGainDb"] = 24
        with self.assertRaisesRegex(ValueError, "masterGainDb"):
            self.rebuild(controls)

    def test_81_reject_invalid_sample_rate(self):
        controls = deepcopy(self.controls); controls["sampleRate"] = 7999
        with self.assertRaisesRegex(ValueError, "sampleRate"):
            self.rebuild(controls)

    def test_82_reject_failed_verdict(self):
        verdict = deepcopy(self.verdict); verdict["passed"] = False
        with self.assertRaisesRegex(ValueError, "passing"):
            self.rebuild(verdict=verdict)

    def test_83_reject_wrong_midi_verdict(self):
        verdict = deepcopy(self.verdict); verdict["finalMidiSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.rebuild(verdict=verdict)

    def test_84_reject_tampered_session(self):
        value = deepcopy(self.session); value["warnings"].append("tamper")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            validate_preview_session_v2(value)

    def test_85_reject_command_without_hash(self):
        adapter = deepcopy(self.controls["externalAdapter"]); adapter["mode"] = "COMMAND"
        with self.assertRaisesRegex(ValueError, "executableSha256"):
            validate_audio_render_adapter(adapter)

    def test_86_reject_soundfont_without_hash(self):
        adapter = deepcopy(self.controls["externalAdapter"]); adapter["mode"] = "SOUNDFONT"
        with self.assertRaisesRegex(ValueError, "soundfontSha256"):
            validate_audio_render_adapter(adapter)

    def test_87_accept_hashed_soundfont_manifest(self):
        adapter = deepcopy(self.controls["externalAdapter"]); adapter["mode"] = "SOUNDFONT"; adapter["soundfontSha256"] = "a" * 64
        self.assertEqual(validate_audio_render_adapter(adapter)["mode"], "SOUNDFONT")

    def test_88_reject_invalid_wav(self):
        with self.assertRaisesRegex(ValueError, "valid PCM WAV"):
            import_device_audio_capture(b"not-wave", self.capture_metadata)

    def test_89_reject_wrong_device(self):
        metadata = deepcopy(self.capture_metadata); metadata["model"] = "Pa5X"
        with self.assertRaisesRegex(ValueError, "exact Korg Pa800"):
            import_device_audio_capture(self.wav, metadata)

    def test_90_reject_future_capture(self):
        metadata = deepcopy(self.capture_metadata); metadata["capturedAt"] = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "future"):
            import_device_audio_capture(self.wav, metadata)

    def test_91_reject_capture_tamper(self):
        capture = deepcopy(self.capture); capture["notes"] += " tamper"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            validate_device_audio_capture(capture)

    def test_92_reject_capture_certification_claim(self):
        capture = deepcopy(self.capture); capture["certificationAllowed"] = True
        capture["captureHash"] = _hash_without(capture, "captureHash")
        with self.assertRaisesRegex(ValueError, "cannot certify"):
            validate_device_audio_capture(capture)

    def test_93_reject_comparison_other_midi(self):
        capture = deepcopy(self.capture); capture["sourceMidiSha256"] = "1" * 64
        capture["captureHash"] = _hash_without(capture, "captureHash")
        with self.assertRaisesRegex(ValueError, "another MIDI"):
            compare_device_audio_capture(self.session, "C", capture, self.audio_manifest)

    def test_94_reject_wrong_proxy_variant(self):
        manifest = deepcopy(self.audio_manifest); manifest["variantId"] = "A"
        with self.assertRaisesRegex(ValueError, "requested preview variant"):
            compare_device_audio_capture(self.session, "C", self.capture, manifest)

    def test_95_no_expression_still_previews(self):
        session = self.rebuild(expression=None)
        self.assertEqual(session["audit"]["expressionNoteCount"], 0)

    def test_96_no_articulation_still_previews(self):
        session = self.rebuild(articulations=[])
        self.assertEqual(session["audit"]["articulationEventCount"], 0)


if __name__ == "__main__":
    unittest.main()