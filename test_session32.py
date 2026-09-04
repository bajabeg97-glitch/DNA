from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dna_midi_studio.track_plan import (  # noqa: E402
    DRY_RUN_SCHEMA,
    DRY_RUN_VERSION,
    OPERATION_KINDS,
    OPTIMIZER_OPERATION_SCHEMA,
    OPTIMIZER_OPERATION_VERSION,
    TRACK_PLAN_DECISIONS,
    TRACK_PLAN_SCHEMA,
    TRACK_PLAN_VERSION,
    build_track_plan,
    execute_track_plan_api,
    execute_track_plan_gui,
    validate_optimizer_operation,
    validate_track_plan_v3,
)
from dna_midi_studio.session32_fixture import build_session32_chain  # noqa: E402


class Session32TrackPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = build_session32_chain(ROOT)
        cls.plan = cls.fixture["trackPlan"]
        cls.fragments = cls.plan["fragments"]
        cls.sources = cls.plan["sourceTrackPlans"]
        cls.operations = [operation for item in cls.sources + cls.fragments
                          for operation in item["operations"]]
        cls.payload = {
            "midiBase64": base64.b64encode(cls.fixture["midi"].to_bytes()).decode("ascii"),
            "documents": cls.fixture["documents"],
            "evidenceLedger": cls.fixture["ledger"],
            "targetBindings": cls.fixture["targetBindings"],
            "controls": cls.fixture["controls"],
        }


def _case(name, operation):
    def test(self):
        operation(self)
    test.__name__ = "test_" + name
    setattr(Session32TrackPlanTests, test.__name__, test)


case_count = 0

# Contract constants and strict schema files: 20.
for name, actual, expected in (
    ("001_track_plan_schema", TRACK_PLAN_SCHEMA, "dna-track-plan"),
    ("002_track_plan_version", TRACK_PLAN_VERSION, "3.0"),
    ("003_operation_schema", OPTIMIZER_OPERATION_SCHEMA, "dna-optimizer-operation"),
    ("004_operation_version", OPTIMIZER_OPERATION_VERSION, "1.0"),
    ("005_dry_run_schema", DRY_RUN_SCHEMA, "dna-optimizer-dry-run"),
    ("006_dry_run_version", DRY_RUN_VERSION, "1.0"),
    ("007_decision_count", len(TRACK_PLAN_DECISIONS), 4),
    ("008_operation_kind_count", len(OPERATION_KINDS), 9),
    ("009_first_decision", TRACK_PLAN_DECISIONS[0], "KEEP"),
    ("010_last_decision", TRACK_PLAN_DECISIONS[-1], "MANUAL_REVIEW"),
):
    _case(name, lambda self, actual=actual, expected=expected: self.assertEqual(actual, expected)); case_count += 1

schema_paths = [
    ROOT / "premium/schemas/v2/track-plan-v3.schema.json",
    ROOT / "premium/schemas/v2/optimizer-operation-v1.schema.json",
    ROOT / "premium/schemas/v2/optimizer-dry-run-v1.schema.json",
]
for index, path in enumerate(schema_paths, 11):
    _case(f"{index:03d}_schema_exists", lambda self, path=path: self.assertTrue(path.is_file())); case_count += 1
for index, path in enumerate(schema_paths, 14):
    _case(f"{index:03d}_schema_draft", lambda self, path=path: self.assertEqual(
        json.loads(path.read_text())["$schema"], "https://json-schema.org/draft/2020-12/schema")); case_count += 1
for index, path in enumerate(schema_paths, 17):
    _case(f"{index:03d}_schema_strict", lambda self, path=path: self.assertFalse(
        json.loads(path.read_text())["additionalProperties"])); case_count += 1
_case("020_plan_validates", lambda self: validate_track_plan_v3(self.plan)); case_count += 1

# Reference plan structure and exact counts: 35.
structure = (
    ("021_plan_schema_value", lambda s: s.plan["schema"], TRACK_PLAN_SCHEMA),
    ("022_plan_version_value", lambda s: s.plan["version"], TRACK_PLAN_VERSION),
    ("023_source_plan_count", lambda s: len(s.sources), 5),
    ("024_fragment_count", lambda s: len(s.fragments), 52),
    ("025_binding_count", lambda s: len(s.plan["targetBindings"]), 52),
    ("026_unique_binding_requests", lambda s: len({x["requestId"] for x in s.plan["targetBindings"]}), 52),
    ("027_unique_target_uids", lambda s: len({x["targetTrackUid"] for x in s.plan["targetBindings"]}), 7),
    ("028_selected_variant", lambda s: s.plan["controls"]["selectedVariantId"], "C"),
    ("029_source_hash", lambda s: s.plan["source"]["sourceMidiSha256"], s_hash := None),
    ("030_chain_hash_length", lambda s: len(s.plan["source"]["chainHash"]), 64),
    ("031_plan_hash_length", lambda s: len(s.plan["trackPlanHash"]), 64),
    ("032_dry_hash_length", lambda s: len(s.plan["dryRun"]["dryRunHash"]), 64),
    ("033_source_keep", lambda s: s.plan["dryRun"]["sourceDecisionCounts"]["KEEP"], 1),
    ("034_source_repair", lambda s: s.plan["dryRun"]["sourceDecisionCounts"]["REPAIR"], 3),
    ("035_source_review", lambda s: s.plan["dryRun"]["sourceDecisionCounts"]["MANUAL_REVIEW"], 1),
    ("036_fragment_replace", lambda s: s.plan["dryRun"]["fragmentDecisionCounts"]["REPLACE"], 52),
    ("037_fragment_review", lambda s: s.plan["dryRun"]["fragmentDecisionCounts"].get("MANUAL_REVIEW", 0), 0),
    ("038_manual_total", lambda s: len(s.plan["manualReview"]), 1),
    ("039_operation_total", lambda s: len(s.operations), 249),
    ("040_replace_operations", lambda s: s.plan["dryRun"]["operationCounts"]["REPLACE_PATTERN"], 52),
    ("041_groove_operations", lambda s: s.plan["dryRun"]["operationCounts"]["APPLY_EVIDENCE_GROOVE"], 52),
    ("042_factory_operations", lambda s: s.plan["dryRun"]["operationCounts"]["APPLY_FACTORY_DYNAMICS_MIXER"], 54),
    ("043_register_operations", lambda s: s.plan["dryRun"]["operationCounts"]["REPAIR_REGISTER"], 38),
    ("044_polyphony_operations", lambda s: s.plan["dryRun"]["operationCounts"]["VERIFY_POLYPHONY"], 52),
    ("045_cc11_operations", lambda s: s.plan["dryRun"]["operationCounts"]["APPLY_FACTORY_CC11"], 1),
    ("046_role_count", lambda s: len({x["role"] for x in s.fragments}), 7),
    ("047_marker_count", lambda s: len({x["marker"] for x in s.fragments}), 10),
    ("048_drums_count", lambda s: sum(x["role"] == "drums" for x in s.fragments), 10),
    ("049_bass_count", lambda s: sum(x["role"] == "bass" for x in s.fragments), 10),
    ("050_guitar_count", lambda s: sum(x["role"] == "guitar" for x in s.fragments), 8),
    ("051_accompaniment_count", lambda s: sum(x["role"] == "accompaniment" for x in s.fragments), 8),
    ("052_pad_count", lambda s: sum(x["role"] == "pad" for x in s.fragments), 8),
    ("053_percussion_count", lambda s: sum(x["role"] == "percussion" for x in s.fragments), 4),
    ("054_riff_count", lambda s: sum(x["role"] == "riff" for x in s.fragments), 4),
    ("055_renderer_ready", lambda s: s.plan["readiness"]["readyForDeterministicRenderer"], True),
)
for name, getter, expected in structure:
    if name == "029_source_hash":
        _case(name, lambda self: self.assertEqual(
            self.plan["source"]["sourceMidiSha256"], self.fixture["midi"].digest())); case_count += 1
    else:
        _case(name, lambda self, getter=getter, expected=expected: self.assertEqual(getter(self), expected)); case_count += 1

# Safety, scope, budgets and evidence behavior: 45.
checks = (
    lambda s: s.plan["safety"]["readOnly"] is True,
    lambda s: s.plan["safety"]["midiMutationAllowed"] is False,
    lambda s: s.plan["safety"]["midiBytesWritten"] == 0,
    lambda s: s.plan["safety"]["originalMidiOverwritten"] is False,
    lambda s: s.plan["safety"]["originalSoloFingerprintProtected"] is True,
    lambda s: s.plan["safety"]["lockedFragmentsProtected"] is True,
    lambda s: s.plan["safety"]["exactSoundBindingRequired"] is True,
    lambda s: s.plan["safety"]["goldAffectsDynamics"] is False,
    lambda s: s.plan["safety"]["goldAffectsMixer"] is False,
    lambda s: s.plan["safety"]["approximateSoundBindingAllowed"] is False,
    lambda s: s.plan["safety"]["implicitFallbackAllowed"] is False,
    lambda s: s.plan["safety"]["rendererAuthority"] is False,
    lambda s: s.plan["safety"]["validatorAuthority"] is False,
    lambda s: s.plan["safety"]["finalMidiExportAllowed"] is False,
    lambda s: s.plan["dryRun"]["candidateMidiGenerated"] is False,
    lambda s: s.plan["dryRun"]["midiBytesWritten"] == 0,
    lambda s: s.plan["dryRun"]["protectedOriginalNoteChanges"] == 0,
    lambda s: s.plan["dryRun"]["soundBindingChanges"] == 0,
    lambda s: s.plan["dryRun"]["goldVelocityChanges"] == 0,
    lambda s: s.plan["dryRun"]["goldBankProgramChanges"] == 0,
    lambda s: all(x["budget"]["withinBudget"] for x in s.fragments),
    lambda s: all(x["budget"]["withinBudget"] for x in s.sources),
    lambda s: all(len(x["operationHash"]) == 64 for x in s.operations),
    lambda s: all(x["evidenceHashes"] and all(len(h) == 64 for h in x["evidenceHashes"]) for x in s.operations),
    lambda s: all(re.fullmatch(r"trk-[0-9a-f]{20}", x["targetTrackUid"]) for x in s.plan["targetBindings"]),
    lambda s: all(x["channelNumber"] == x["channelIndex"] + 1 for x in s.plan["targetBindings"]),
    lambda s: all(x["targetTrackNumber"] == x["targetTrackIndex"] + 1 for x in s.plan["targetBindings"]),
    lambda s: all(x["confirmation"] == "EXACT_FACTORY_SOFTWARE" for x in s.plan["targetBindings"]),
    lambda s: all(x["deviceConfirmed"] is False for x in s.plan["targetBindings"]),
    lambda s: all(all(re.fullmatch(r"[0-9]{3}\.[0-9]{3}\.[0-9]{3}", p) for p in x["factoryProfileIds"]) for x in s.plan["targetBindings"]),
    lambda s: all(x["operations"] for x in s.fragments),
    lambda s: all(any(o["kind"] == "REPLACE_PATTERN" for o in x["operations"]) for x in s.fragments),
    lambda s: all(any(o["kind"] == "VERIFY_POLYPHONY" for o in x["operations"]) for x in s.fragments),
    lambda s: all(any(o["kind"] == "APPLY_FACTORY_DYNAMICS_MIXER" for o in x["operations"]) for x in s.fragments),
    lambda s: all(any(o["kind"] == "APPLY_EVIDENCE_GROOVE" for o in x["operations"]) for x in s.fragments),
    lambda s: all(any(o["kind"] == "REPAIR_REGISTER" for o in x["operations"]) for x in s.fragments if x["role"] not in {"drums", "percussion"}),
    lambda s: all(not any(o["kind"] == "REPAIR_REGISTER" for o in x["operations"]) for x in s.fragments if x["role"] in {"drums", "percussion"}),
    lambda s: all(next(o for o in x["operations"] if o["kind"] == "REPLACE_PATTERN")["limits"]["basePatternNotesExcludedFromTransformationBudget"] for x in s.fragments),
    lambda s: all(next(o for o in x["operations"] if o["kind"] == "APPLY_EVIDENCE_GROOVE")["limits"]["velocityChanges"] == 0 for x in s.fragments),
    lambda s: all(next(o for o in x["operations"] if o["kind"] == "APPLY_FACTORY_DYNAMICS_MIXER")["limits"]["goldAuthority"] is False for x in s.fragments),
    lambda s: all(next(o for o in x["operations"] if o["kind"] == "VERIFY_POLYPHONY")["limits"]["maximumConcurrentMidiNotes"] <= 54 for x in s.fragments),
    lambda s: sum(x["protectedSolo"] for x in s.sources) == 1,
    lambda s: any(o["kind"] == "APPLY_FACTORY_CC11" for x in s.sources if x["protectedSolo"] for o in x["operations"]),
    lambda s: all(o["kind"] != "APPLY_FACTORY_DYNAMICS_MIXER" for x in s.sources if x["protectedSolo"] for o in x["operations"]),
    lambda s: not s.sources[0]["operations"],
)
for index, check in enumerate(checks, 56):
    _case(f"{index:03d}_behavior", lambda self, check=check: self.assertTrue(check(self))); case_count += 1

# Thirty independently hashed operations across the full contract: 30.
for index in range(30):
    _case(f"{101 + index:03d}_sample_operation_valid", lambda self, index=index:
          validate_optimizer_operation(self.operations[(index * 37) % len(self.operations)])); case_count += 1

# API/GUI parity, determinism and adversarial fail-closed cases: 22.
_case("131_api_parity", lambda self: self.assertEqual(execute_track_plan_api(self.payload, ROOT), self.plan)); case_count += 1
_case("132_gui_parity", lambda self: self.assertEqual(execute_track_plan_gui(self.payload, ROOT), self.plan)); case_count += 1
_case("133_repeat_deterministic", lambda self: self.assertEqual(build_track_plan(
    self.fixture["midi"].to_bytes(), self.fixture["documents"], self.fixture["ledger"],
    self.fixture["targetBindings"], self.fixture["controls"], ROOT), self.plan)); case_count += 1

def raises_api(self, payload):
    with self.assertRaises(ValueError): execute_track_plan_api(payload, ROOT)

_case("134_invalid_base64", lambda self: raises_api(self, {**self.payload, "midiBase64": "***"})); case_count += 1
_case("135_extra_api_field", lambda self: raises_api(self, {**self.payload, "extra": True})); case_count += 1

def missing_document(self):
    docs = dict(self.fixture["documents"]); docs.pop("songMap")
    with self.assertRaises(ValueError): build_track_plan(self.fixture["midi"].to_bytes(), docs,
        self.fixture["ledger"], self.fixture["targetBindings"], self.fixture["controls"], ROOT)
_case("136_missing_document", missing_document); case_count += 1

def source_mismatch(self):
    raw = bytearray(self.fixture["midi"].to_bytes()); raw[-1] ^= 1
    with self.assertRaises(ValueError): build_track_plan(bytes(raw), self.fixture["documents"],
        self.fixture["ledger"], self.fixture["targetBindings"], self.fixture["controls"], ROOT)
_case("137_source_hash_mismatch", source_mismatch); case_count += 1

def invalid_ledger(self):
    ledger = deepcopy(self.fixture["ledger"]); ledger["ledgerHash"] = "0" * 64
    with self.assertRaises(ValueError): build_track_plan(self.fixture["midi"].to_bytes(),
        self.fixture["documents"], ledger, self.fixture["targetBindings"], self.fixture["controls"], ROOT)
_case("138_invalid_ledger", invalid_ledger); case_count += 1

_case("139_variant_mismatch", lambda self: self.assertRaises(ValueError, build_track_plan,
    self.fixture["midi"].to_bytes(), self.fixture["documents"], self.fixture["ledger"],
    self.fixture["targetBindings"], {**self.fixture["controls"], "selectedVariantId": "A"}, ROOT)); case_count += 1

def bad_binding(self, field, value):
    bindings = deepcopy(self.fixture["targetBindings"]); bindings[0][field] = value
    with self.assertRaises(ValueError): build_track_plan(self.fixture["midi"].to_bytes(),
        self.fixture["documents"], self.fixture["ledger"], bindings, self.fixture["controls"], ROOT)

_case("140_approximate_binding", lambda self: bad_binding(self, "confirmation", "APPROXIMATE")); case_count += 1
_case("141_device_claim_rejected", lambda self: bad_binding(self, "deviceConfirmed", True)); case_count += 1
_case("142_unknown_profile", lambda self: bad_binding(self, "factoryProfileIds", ["999.999.999"])); case_count += 1
_case("143_sound_mismatch", lambda self: bad_binding(self, "program", 127)); case_count += 1

def duplicate_binding(self):
    bindings = deepcopy(self.fixture["targetBindings"]); bindings.append(deepcopy(bindings[0]))
    with self.assertRaises(ValueError): build_track_plan(self.fixture["midi"].to_bytes(),
        self.fixture["documents"], self.fixture["ledger"], bindings, self.fixture["controls"], ROOT)
_case("144_duplicate_binding", duplicate_binding); case_count += 1

def extra_binding_field(self):
    bindings = deepcopy(self.fixture["targetBindings"]); bindings[0]["guess"] = True
    with self.assertRaises(ValueError): build_track_plan(self.fixture["midi"].to_bytes(),
        self.fixture["documents"], self.fixture["ledger"], bindings, self.fixture["controls"], ROOT)
_case("145_extra_binding_field", extra_binding_field); case_count += 1

def missing_binding(self):
    plan = build_track_plan(self.fixture["midi"].to_bytes(), self.fixture["documents"],
        self.fixture["ledger"], self.fixture["targetBindings"][1:], self.fixture["controls"], ROOT)
    self.assertEqual(plan["fragments"][0]["decision"], "MANUAL_REVIEW")
    self.assertFalse(plan["readiness"]["readyForDeterministicRenderer"])
_case("146_missing_binding_fails_closed", missing_binding); case_count += 1

def foreign_binding(self):
    bindings = deepcopy(self.fixture["targetBindings"]); item = deepcopy(bindings[0]); item["requestId"] = "foreign:role"; bindings.append(item)
    with self.assertRaises(ValueError): build_track_plan(self.fixture["midi"].to_bytes(),
        self.fixture["documents"], self.fixture["ledger"], bindings, self.fixture["controls"], ROOT)
_case("147_foreign_binding_rejected", foreign_binding); case_count += 1

def lock_fragment(self):
    request_id = self.fragments[0]["requestId"]
    plan = build_track_plan(self.fixture["midi"].to_bytes(), self.fixture["documents"],
        self.fixture["ledger"], self.fixture["targetBindings"],
        {**self.fixture["controls"], "lockedFragmentIds": [request_id]}, ROOT)
    self.assertEqual(plan["fragments"][0]["decision"], "KEEP")
    self.assertFalse(plan["fragments"][0]["operations"])
    self.assertEqual(plan["fragments"][1]["fragmentHash"], self.fragments[1]["fragmentHash"])
_case("148_fragment_lock", lock_fragment); case_count += 1

def lock_track(self):
    target = self.sources[1]["trackUid"]
    plan = build_track_plan(self.fixture["midi"].to_bytes(), self.fixture["documents"],
        self.fixture["ledger"], self.fixture["targetBindings"],
        {**self.fixture["controls"], "lockedTrackUids": [target]}, ROOT)
    self.assertEqual(plan["sourceTrackPlans"][1]["decision"], "KEEP")
    self.assertFalse(plan["sourceTrackPlans"][1]["operations"])
_case("149_track_lock", lock_track); case_count += 1

_case("150_bad_quantize_division", lambda self: self.assertRaises(ValueError, build_track_plan,
    self.fixture["midi"].to_bytes(), self.fixture["documents"], self.fixture["ledger"],
    self.fixture["targetBindings"], {**self.fixture["controls"], "quantizeDivision": 12}, ROOT)); case_count += 1
_case("151_bad_polyphony_ceiling", lambda self: self.assertRaises(ValueError, build_track_plan,
    self.fixture["midi"].to_bytes(), self.fixture["documents"], self.fixture["ledger"],
    self.fixture["targetBindings"], {**self.fixture["controls"], "softwareMidiNoteCeiling": 55}, ROOT)); case_count += 1
_case("152_source_bytes_unchanged", lambda self: self.assertEqual(
    base64.b64decode(self.payload["midiBase64"]), self.fixture["midi"].to_bytes())); case_count += 1

assert case_count == 152, case_count