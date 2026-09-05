from __future__ import annotations

import json
import base64
from pathlib import Path
import subprocess
import tempfile
import unittest

from dna_midi_studio import PipelineConfig, execute_api_payload, execute_batch, execute_gui, execute_pipeline, execute_web
from dna_midi_studio.session2_fixture import build_demo_case
from dna_midi_studio.session3_fixture import build_session3_case
from dna_midi_studio.session4_fixture import build_session4_case
from dna_midi_studio.session5_fixture import build_session5_case
from dna_midi_studio.session6_fixture import build_session6_case
from dna_midi_studio.session7_fixture import build_session7_case


ROOT = Path(__file__).resolve().parents[1]


class Session9UnifiedPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.midi, *_ = build_session7_case(ROOT)
        self.raw = json.loads((ROOT / "data" / "session9-demo-config.json").read_text(encoding="utf-8"))

    def pipeline(self, raw=None):
        return execute_pipeline(self.midi.to_bytes(), self.raw if raw is None else raw, ROOT)

    def test_config_rejects_unknown_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown pipeline"):
            PipelineConfig.from_mapping({**self.raw, "transport": "cli"})

    def test_config_requires_version_and_stage(self) -> None:
        with self.assertRaisesRegex(ValueError, "version 1.0"):
            PipelineConfig.from_mapping({"version": "2.0", "stages": []})

    def test_config_rejects_unknown_engine(self) -> None:
        raw = json.loads(json.dumps(self.raw)); raw["stages"][0]["engine"] = "invented"
        with self.assertRaisesRegex(ValueError, "Unsupported engine"):
            PipelineConfig.from_mapping(raw)

    def test_registry_path_cannot_escape_workspace(self) -> None:
        raw = json.loads(json.dumps(self.raw)); raw["stages"][0]["registry"] = "../registry.json"
        with self.assertRaisesRegex(ValueError, "relative"):
            self.pipeline(raw)

    def test_preview_limit_is_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 0 and 25000"):
            PipelineConfig.from_mapping({**self.raw, "previewNoteLimit": 25001})

    def test_cli_web_gui_and_batch_have_identical_midi_hashes(self) -> None:
        direct = self.pipeline(); web = execute_web(self.midi.to_bytes(), self.raw, ROOT)
        gui = execute_gui(self.midi.to_bytes(), self.raw, ROOT)
        batch = execute_batch([(self.midi.to_bytes(), self.raw)], ROOT)[0]
        self.assertEqual({direct.manifest["outputHash"], web.manifest["outputHash"],
                          gui.manifest["outputHash"], batch.manifest["outputHash"]}, {direct.manifest["outputHash"]})
        self.assertEqual(direct.midi, web.midi)

    def test_transport_manifests_are_identical(self) -> None:
        self.assertEqual(self.pipeline().manifest, execute_gui(self.midi.to_bytes(), self.raw, ROOT).manifest)

    def test_http_api_payload_uses_the_same_pipeline(self) -> None:
        payload = {"midiBase64": base64.b64encode(self.midi.to_bytes()).decode("ascii"), "config": self.raw}
        response = execute_api_payload(payload, ROOT)
        direct = self.pipeline()
        self.assertEqual(base64.b64decode(response["midiBase64"]), direct.midi)
        self.assertEqual(response["manifest"], direct.manifest)

    def test_manifest_has_exactly_sixteen_track_rows(self) -> None:
        view = self.pipeline().manifest["trackView"]
        self.assertEqual(len(view), 16)
        self.assertEqual([row["track"] for row in view], list(range(1, 17)))

    def test_track_view_reports_present_and_empty_tracks(self) -> None:
        view = self.pipeline().manifest["trackView"]
        self.assertTrue(view[10]["present"])
        self.assertFalse(view[15]["present"])
        self.assertEqual(view[15]["noteCount"], 0)

    def test_preview_is_read_only_and_validation_neutral(self) -> None:
        preview = self.pipeline().manifest["preview"]
        self.assertTrue(preview["readOnly"])
        self.assertFalse(preview["affectsMidiValidation"])

    def test_preview_limit_truncates_without_changing_midi(self) -> None:
        limited = self.pipeline({**self.raw, "previewNoteLimit": 2})
        full = self.pipeline()
        self.assertEqual(limited.midi, full.midi)
        self.assertEqual(limited.manifest["preview"]["noteCount"], 2)
        self.assertTrue(limited.manifest["preview"]["truncated"])

    def test_preview_profile_changes_config_hash_not_midi(self) -> None:
        silent = self.pipeline({**self.raw, "previewProfile": "silent"})
        normal = self.pipeline()
        self.assertEqual(silent.midi, normal.midi)
        self.assertNotEqual(silent.manifest["configHash"], normal.manifest["configHash"])

    def test_original_input_object_is_immutable(self) -> None:
        before = self.midi.to_bytes(); self.pipeline()
        self.assertEqual(self.midi.to_bytes(), before)

    def test_same_request_is_byte_deterministic(self) -> None:
        first, second = self.pipeline(), self.pipeline()
        self.assertEqual((first.midi, first.manifest), (second.midi, second.manifest))

    def test_stage_manifest_records_real_dnc_decision(self) -> None:
        stage = self.pipeline().manifest["stages"][0]
        self.assertEqual((stage["engine"], stage["decision"]), ("dnc", "AUGMENT"))
        self.assertTrue(stage["manifest"]["notePairingValid"])

    def test_every_recovery_engine_uses_the_same_dispatcher(self) -> None:
        cases = [
            ("drum", lambda root: build_demo_case(root / "data/session2-demo-registry.json"), "data/session2-demo-registry.json"),
            ("harmonic", build_session3_case, "data/session3-demo-registry.json"),
            ("guitar", build_session4_case, "data/session4-demo-registry.json"),
            ("solo", build_session5_case, "data/session5-demo-registry.json"),
            ("rx", build_session6_case, "data/session6-demo-registry.json"),
            ("dnc", build_session7_case, "data/session7-demo-registry.json"),
        ]
        for engine, fixture, registry in cases:
            with self.subTest(engine=engine):
                midi, *parts = fixture(ROOT)
                config = parts[-1]
                stage = {"engine": engine, "registry": registry, "config": dict(config.__dict__)}
                if engine in {"harmonic", "guitar", "solo"}:
                    stage["context"] = {"chords": [dict(chord.__dict__) for chord in parts[-2]]}
                raw = {"version": "1.0", "stages": [stage]}
                result = execute_pipeline(midi.to_bytes(), raw, ROOT)
                self.assertEqual(result.manifest["stages"][0]["engine"], engine)

    def test_batch_preserves_input_order(self) -> None:
        second = {**self.raw, "previewProfile": "silent"}
        results = execute_batch([(self.midi.to_bytes(), self.raw), (self.midi.to_bytes(), second)], ROOT)
        self.assertEqual(results[0].manifest["preview"]["profile"], "pa800-gm")
        self.assertEqual(results[1].manifest["preview"]["profile"], "silent")

    def test_cli_entry_point_writes_same_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); input_path = base / "in.mid"; output = base / "out.mid"; manifest = base / "out.json"
            input_path.write_bytes(self.midi.to_bytes())
            completed = subprocess.run(["python", "session9_pipeline.py", "--input", str(input_path),
                                        "--config", "data/session9-demo-config.json", "--output", str(output),
                                        "--manifest", str(manifest)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(manifest.read_text())["outputHash"], self.pipeline().manifest["outputHash"])


if __name__ == "__main__":
    unittest.main()