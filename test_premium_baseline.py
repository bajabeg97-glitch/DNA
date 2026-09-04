from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest

import pa800_validator
from premium_config import (
    CONTRACT_SCHEMAS,
    CONTRACT_VERSION,
    PremiumConfig,
    build_read_only_plan,
    prepare_premium_baseline,
    sha256_file,
    validate_producer_brief,
    verify_baseline,
)


ROOT = Path(__file__).resolve().parents[1]


def sample_brief() -> dict:
    return {
        "schema": "dna-premium-producer-brief",
        "version": "1.0",
        "prompt": "Suzdrzana strofa, pun refren i povezani prijelazi.",
        "genre": "pop-folk",
        "energyCurve": [30, 48, 70, 92],
        "density": "balanced",
        "requiredRoles": ["drums", "bass", "guitar"],
        "forbiddenRoles": [],
        "lockedElements": ["v1cv1"],
        "transformationTolerance": 45,
        "notes": "Originalni solo ostaje potpuno zasticen.",
    }


def sample_config() -> dict:
    return {
        "schema": "dna-premium-config",
        "version": "1.0",
        "name": "Premium Baseline Test",
        "seed": 150015001,
        "targetDevice": "Korg Pa800",
        "producerBrief": sample_brief(),
        "variantCount": 3,
        "cloudEnabled": False,
        "outputMode": "plan-only",
    }


class PremiumBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.prepared = prepare_premium_baseline(ROOT)
        cls.baseline = cls.prepared["baseline"]
        cls.matrix = cls.prepared["featureMatrix"]
        cls.catalog = cls.prepared["schemaCatalog"]

    def test_all_nine_contract_schemas_are_cataloged(self) -> None:
        self.assertEqual(len(CONTRACT_SCHEMAS), 9)
        self.assertEqual({item["name"] for item in self.catalog["contracts"]}, set(CONTRACT_SCHEMAS))

    def test_schema_ids_are_unique(self) -> None:
        identifiers = [item["id"] for item in self.catalog["contracts"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(all(identifiers))

    def test_every_schema_is_strict_draft_2020_12(self) -> None:
        for name in CONTRACT_SCHEMAS:
            value = json.loads((ROOT / "premium" / "schemas" / "v1" / name).read_text())
            self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")
            self.assertEqual(value["x-contract-version"], CONTRACT_VERSION)
            self.assertEqual(value["type"], "object")
            self.assertIs(value["additionalProperties"], False)

    def test_schema_catalog_hashes_match_files(self) -> None:
        self.assertEqual(self.catalog["date"], "2026-09-02")
        for item in self.catalog["contracts"]:
            path = ROOT / "premium" / "schemas" / "v1" / item["name"]
            self.assertEqual(item["sha256"], sha256_file(path))

    def test_valid_producer_brief_is_accepted_without_mutation(self) -> None:
        source = sample_brief()
        accepted = validate_producer_brief(source)
        self.assertEqual(accepted, source)
        self.assertIsNot(accepted, source)

    def test_unknown_producer_brief_field_is_rejected(self) -> None:
        value = sample_brief()
        value["writeMidi"] = True
        with self.assertRaisesRegex(ValueError, "Unknown producer brief fields"):
            validate_producer_brief(value)

    def test_conflicting_required_and_forbidden_role_is_rejected(self) -> None:
        value = sample_brief()
        value["forbiddenRoles"] = ["bass"]
        with self.assertRaisesRegex(ValueError, "both required and forbidden"):
            validate_producer_brief(value)

    def test_invalid_energy_curve_is_rejected(self) -> None:
        value = sample_brief()
        value["energyCurve"] = [20, 101]
        with self.assertRaisesRegex(ValueError, "range 0..100"):
            validate_producer_brief(value)

    def test_premium_config_hash_is_deterministic(self) -> None:
        first = PremiumConfig.from_dict(sample_config())
        second = PremiumConfig.from_dict(json.loads(json.dumps(sample_config())))
        self.assertEqual(first.config_hash, second.config_hash)

    def test_non_pa800_target_is_rejected_in_session15(self) -> None:
        value = sample_config()
        value["targetDevice"] = "Unknown Arranger"
        with self.assertRaisesRegex(ValueError, "only the Korg Pa800"):
            PremiumConfig.from_dict(value)

    def test_variant_count_is_bounded_to_two_through_four(self) -> None:
        for count in (1, 5):
            value = sample_config()
            value["variantCount"] = count
            with self.assertRaisesRegex(ValueError, "2..4"):
                PremiumConfig.from_dict(value)

    def test_session15_rejects_midi_output_mode(self) -> None:
        value = sample_config()
        value["outputMode"] = "write-midi"
        with self.assertRaisesRegex(ValueError, "plan-only"):
            PremiumConfig.from_dict(value)

    def test_read_only_plan_forbids_every_sensitive_action(self) -> None:
        plan = build_read_only_plan(PremiumConfig.from_dict(sample_config()))
        self.assertEqual(plan["status"], "PLANNED")
        self.assertEqual(plan["variantIds"], ["variant-1", "variant-2", "variant-3"])
        self.assertTrue(all(value is False for key, value in plan["safety"].items()
                            if key != "goldAffectsDynamics"))
        self.assertFalse(plan["safety"]["goldAffectsDynamics"])

    def test_read_only_plan_hash_is_deterministic(self) -> None:
        config = PremiumConfig.from_dict(sample_config())
        first = build_read_only_plan(config)
        second = build_read_only_plan(config)
        self.assertEqual(first, second)
        self.assertRegex(first["planHash"], r"^[0-9a-f]{64}$")

    def test_feature_matrix_covers_sessions_15_through_30(self) -> None:
        self.assertEqual([item["session"] for item in self.matrix["features"]], list(range(15, 31)))
        self.assertEqual(len({item["id"] for item in self.matrix["features"]}), 16)

    def test_feature_matrix_does_not_claim_premium_is_complete(self) -> None:
        self.assertEqual(self.matrix["premiumProductStatus"], "PLANNED")
        self.assertEqual(self.matrix["features"][0]["status"], "SOFTWARE_VALIDATED")
        self.assertEqual(self.matrix["features"][1]["status"], "DEVICE_BLOCKED")
        self.assertEqual(self.matrix["invariants"]["physicalPa800"], "WAITING_FOR_DEVICE")

    def test_frozen_baseline_reverifies(self) -> None:
        self.assertEqual(self.baseline["date"], "2026-09-02")
        self.assertTrue(verify_baseline(ROOT, self.baseline))
        entries = self.baseline["frozenFiles"]
        expected = sha256(json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(self.baseline["contentHash"], expected)

    def test_frozen_baseline_has_production_corpus_counts(self) -> None:
        self.assertEqual(self.baseline["corpus"], {
            "factoryMidi": 3211,
            "goldMidi": 182,
            "factoryProfiles": 1964,
            "factoryStyleSegments": 26922,
            "factoryStrummingPatterns": 2919,
            "goldPerformancePatterns": 12918,
            "legacyGoldPatterns": 10637,
        })

    def test_frozen_baseline_contains_source_schemas_reports_and_reference_midi(self) -> None:
        paths = {item["path"] for item in self.baseline["frozenFiles"]}
        self.assertIn("prism-uploads/DNA.zip", paths)
        self.assertIn("premium/baseline/reference-style.mid", paths)
        self.assertIn("premium/baseline/reports/release-check-report.json", paths)
        self.assertTrue({f"premium/schemas/v1/{name}" for name in CONTRACT_SCHEMAS} <= paths)

    def test_reference_style_is_validated_and_matches_manifest_hash(self) -> None:
        midi = self.prepared["referenceMidi"].read_bytes()
        manifest = json.loads(self.prepared["referenceManifest"].read_text())
        markers = [item["marker"] for item in manifest["elements"]]
        channels = [channel - 1 for channel in manifest["midi"]["usedChannels"]]
        result = pa800_validator.validate_pa800_smf(midi, markers, channels)
        self.assertTrue(result["passed"], result["issues"])
        self.assertEqual(manifest["midi"]["sha256"], sha256(midi).hexdigest())


if __name__ == "__main__":
    unittest.main()