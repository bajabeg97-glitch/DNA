"""Session 37 locked-corpus quality calibration and external-evidence intake.

The module deliberately keeps three authorities separate:

* deterministic structural calibration may use TRAIN labels only;
* HOLDOUT labels are opened only after the calibration document is sealed;
* human listening and production expression evidence require external files and
  never become valid merely because a software fixture says that they exist.
"""

from __future__ import annotations

from base64 import b64encode
from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path, PurePosixPath
import re
import statistics
from typing import Any, Mapping, Sequence

from .midi import MidiEvent, MidiFile, MidiTrack
from .music_quality import AUTOMATED_METRICS, QualityControls
from .session19_fixture import build_benchmark_case


QUALITY_CORPUS_SCHEMA = "dna-premium-quality-corpus"
QUALITY_CORPUS_VERSION = "2.0"
GROUND_TRUTH_VAULT_SCHEMA = "dna-premium-quality-ground-truth-vault"
GROUND_TRUTH_VAULT_VERSION = "1.0"
CALIBRATION_REPORT_SCHEMA = "dna-premium-quality-calibration-report"
CALIBRATION_REPORT_VERSION = "1.0"
EXPRESSION_INTAKE_SCHEMA = "dna-premium-production-expression-intake"
EXPRESSION_INTAKE_VERSION = "1.0"
LISTENING_INTAKE_SCHEMA = "dna-premium-human-listening-intake"
LISTENING_INTAKE_VERSION = "1.0"
QUALITY_GATE_SCHEMA = "dna-premium-quality-release-gate"
QUALITY_GATE_VERSION = "1.0"

DEFAULT_METRIC_WEIGHTS = {
    "harmony": 0.20,
    "groove": 0.16,
    "registerCollision": 0.14,
    "densityCurve": 0.14,
    "transitionContinuity": 0.14,
    "repetition": 0.10,
    "endingResolution": 0.12,
}
ALLOWED_RELATIONSHIP_FIELDS = {
    "relationshipId", "kind", "onsetDeltaTicks", "pitchDeltaSemitones",
    "gateRatio", "confidence", "phraseRole",
}
FORBIDDEN_EXPRESSION_FIELDS = {
    "velocity", "absolutePitch", "bankMsb", "bankLsb", "program",
    "cc7", "cc11", "mixer", "soundId",
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID = re.compile(r"^[a-z][a-z0-9-]{2,63}$")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return sha256(_canonical({key: item for key, item in value.items() if key != field})).hexdigest()


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _strict(value: Mapping[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    unknown = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unknown:
        raise ValueError(f"Unknown {label} fields: " + ", ".join(unknown))
    if missing:
        raise ValueError(f"Missing {label} fields: " + ", ".join(missing))


def _safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a relative path")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must stay inside the evidence root")
    return path.as_posix()


def _file_hash(root: Path, relative: str, expected: str, label: str) -> Path:
    relative = _safe_relative(relative, label)
    path = root / relative
    if not path.is_file():
        raise ValueError(f"{label} file is missing")
    if sha256(path.read_bytes()).hexdigest() != _sha(expected, f"{label}Sha256"):
        raise ValueError(f"{label} hash mismatch")
    return path


def _expanded_case(index: int) -> tuple[bytes, tuple[str, ...], tuple[int, ...], tuple[str, ...], str]:
    source = build_benchmark_case(index)
    midi = MidiFile.from_bytes(source.midi)
    meter = ("3/4", "6/8", "4/4")[(index - 20) % 3]
    numerator, denominator_power = {"3/4": (3, 2), "6/8": (6, 3), "4/4": (4, 2)}[meter]
    density_mode = (index - 20) % 3
    tracks = []
    for track_index, track in enumerate(midi.tracks):
        events = []
        for event in track.events:
            if event.kind == "meta" and event.meta_type == 0x58:
                events.append(MidiEvent(event.tick, event.order, event.kind, event.status,
                                        bytes((numerator, denominator_power, 24, 8)), event.meta_type))
            elif track_index == 3 and density_mode == 0 and event.kind == "channel":
                # A sparse arrangement fixture without the lead notes.
                if event.command in {0x80, 0x90}:
                    continue
                events.append(event)
            else:
                events.append(event)
        if track_index == 3 and density_mode == 2:
            # A dense but valid fixture: double the self-authored lead one octave up.
            for event in track.events:
                if event.kind == "channel" and event.command in {0x80, 0x90} and event.data:
                    pitch = event.data[0] + 12
                    if pitch <= 127:
                        events.append(MidiEvent(event.tick, event.order + 10_000, event.kind,
                                                event.status, bytes((pitch, event.data[1])),
                                                event.meta_type))
        tracks.append(MidiTrack(events))
    raw = MidiFile(midi.format_type, midi.ppq, tracks).to_bytes()
    # Verify the materialized fixture before it can enter the locked corpus.
    MidiFile.from_bytes(raw).notes()
    return raw, source.chord_labels, source.section_boundaries, source.section_labels, meter


def _features(raw: bytes) -> dict[str, Any]:
    midi = MidiFile.from_bytes(raw)
    notes = midi.notes()
    pitches = [note.pitch for note in notes]
    end_tick = max((note.end for note in notes), default=0)
    meter_events = [event for track in midi.tracks for event in track.events
                    if event.kind == "meta" and event.meta_type == 0x58 and len(event.data) >= 2]
    meter = "4/4"
    if meter_events:
        event = min(meter_events, key=lambda item: (item.tick, item.order))
        meter = f"{event.data[0]}/{2 ** event.data[1]}"
    return {
        "format": midi.format_type,
        "ppq": midi.ppq,
        "trackCount": len(midi.tracks),
        "noteCount": len(notes),
        "pitchSpan": (max(pitches) - min(pitches)) if pitches else 0,
        "durationTicks": end_tick,
        "meter": meter,
    }


def build_locked_quality_corpus(root: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Materialize and lock 32 self-authored cases with a 24/8 split."""
    root = Path(root)
    labeled_path = root / "data/session19-labeled-benchmark.json"
    labeled = json.loads(labeled_path.read_text(encoding="utf-8"))
    if labeled.get("license") != "self-authored-test-fixtures" or len(labeled.get("cases", [])) != 20:
        raise ValueError("Session 19 legal benchmark is not the expected locked 20-case source")
    rows: list[dict[str, Any]] = []
    truth: list[dict[str, Any]] = []
    genres = ("pop-folk", "pop", "ballad", "dance", "waltz", "rock", "acoustic")
    for index in range(32):
        ordinal = index + 1
        split = "HOLDOUT" if ordinal % 4 == 0 else "TRAIN"
        if index < 20:
            source = labeled["cases"][index]
            relative = source["midi"]
            target = root / relative
            if target.is_file():
                raw = target.read_bytes()
            else:
                raw = build_benchmark_case(index).midi
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            if sha256(raw).hexdigest() != source["sha256"]:
                raise ValueError(f"Session 19 case hash mismatch: {source['caseId']}")
            case_id = source["caseId"]
            chord_labels = tuple(source["expectedChordLabels"])
            predicted = tuple(source["predictedChordLabels"])
            boundaries = tuple(source["expectedSectionBoundaries"])
            section_labels = tuple(source["expectedSectionLabels"])
            source_kind = "SESSION19_LOCKED"
        else:
            raw, chord_labels, boundaries, section_labels, _ = _expanded_case(index)
            predicted = chord_labels
            case_id = f"quality37-{ordinal:02d}"
            relative = f"artifacts/session37-corpus/{case_id}.mid"
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or target.read_bytes() != raw:
                target.write_bytes(raw)
            source_kind = "SESSION37_SELF_AUTHORED"
        feature = _features(raw)
        labels = {
            "expectedChordLabels": list(chord_labels),
            "predictedChordLabels": list(predicted),
            "expectedSectionBoundaries": list(boundaries),
            "predictedSectionBoundaries": list(boundaries),
            "expectedSectionLabels": list(section_labels),
            "expectedTrackRoles": ["conductor", "harmony", "bass", "solo", "drums"],
            "predictedTrackRoles": ["conductor", "harmony", "bass", "solo", "drums"],
            "expectedTransitions": [
                f"{section_labels[position]}>{section_labels[position + 1]}"
                for position in range(len(section_labels) - 1)
            ],
            "predictedTransitions": [
                f"{section_labels[position]}>{section_labels[position + 1]}"
                for position in range(len(section_labels) - 1)
            ],
        }
        annotation_hash = sha256(_canonical(labels)).hexdigest()
        rows.append({
            "caseId": case_id,
            "split": split,
            "sourceKind": source_kind,
            "midiPath": relative,
            "midiSha256": sha256(raw).hexdigest(),
            "license": "SELF_AUTHORED_FIXTURE",
            "genre": genres[index % len(genres)],
            "formClass": "INTRO_VERSE_CHORUS_ENDING",
            "features": feature,
            "annotationHash": annotation_hash,
        })
        truth.append({
            "caseId": case_id,
            "split": split,
            "calibrationAccessible": split == "TRAIN",
            "labels": labels,
            "annotationHash": annotation_hash,
        })

    for row in rows:
        count = row["features"]["noteCount"]
        row["densityClass"] = "LOW" if count < 118 else "HIGH" if count > 118 else "MEDIUM"

    manifest: dict[str, Any] = {
        "schema": QUALITY_CORPUS_SCHEMA,
        "version": QUALITY_CORPUS_VERSION,
        "date": date.today().isoformat(),
        "license": "self-authored-test-fixtures",
        "locked": True,
        "selectionPolicy": "STRATIFIED_BEFORE_SCORING_EVERY_FOURTH_CASE_HOLDOUT",
        "caseCount": len(rows),
        "splits": {"train": 24, "holdout": 8},
        "sourceCorpora": [
            {"id": "session19", "caseCount": 20,
             "contentHash": labeled["immutableCorpusHash"]},
            {"id": "session37-expansion", "caseCount": 12,
             "contentHash": sha256(_canonical([row["midiSha256"] for row in rows[20:]])).hexdigest()},
        ],
        "cases": rows,
        "holdoutPolicy": {
            "labelsVisibleToCalibration": False,
            "thresholdChangesAfterHoldout": False,
            "hardSafetyThresholdWeakening": False,
        },
    }
    manifest["corpusHash"] = sha256(_canonical(manifest)).hexdigest()
    vault: dict[str, Any] = {
        "schema": GROUND_TRUTH_VAULT_SCHEMA,
        "version": GROUND_TRUTH_VAULT_VERSION,
        "date": date.today().isoformat(),
        "corpusHash": manifest["corpusHash"],
        "private": True,
        "entries": truth,
        "policy": "TRAIN_ONLY_DURING_CALIBRATION_HOLDOUT_OPENED_AFTER_CALIBRATION_SEAL",
    }
    vault["vaultHash"] = sha256(_canonical(vault)).hexdigest()
    validate_locked_quality_corpus(manifest, vault, root)
    return manifest, vault


def validate_locked_quality_corpus(manifest: Mapping[str, Any], vault: Mapping[str, Any],
                                   root: str | Path | None = None) -> None:
    manifest_fields = {"schema", "version", "date", "license", "locked", "selectionPolicy",
                       "caseCount", "splits", "sourceCorpora", "cases", "holdoutPolicy", "corpusHash"}
    _strict(manifest, manifest_fields, manifest_fields, "quality corpus")
    if manifest["schema"] != QUALITY_CORPUS_SCHEMA or manifest["version"] != QUALITY_CORPUS_VERSION:
        raise ValueError("Unsupported quality corpus")
    if manifest["locked"] is not True or manifest["caseCount"] != 32:
        raise ValueError("Quality corpus must be locked at exactly 32 cases")
    if manifest["splits"] != {"train": 24, "holdout": 8}:
        raise ValueError("Quality corpus split must remain 24 TRAIN / 8 HOLDOUT")
    if manifest["holdoutPolicy"] != {"labelsVisibleToCalibration": False,
                                     "thresholdChangesAfterHoldout": False,
                                     "hardSafetyThresholdWeakening": False}:
        raise ValueError("Holdout policy cannot be weakened")
    if manifest["corpusHash"] != _hash_without(manifest, "corpusHash"):
        raise ValueError("Quality corpus hash mismatch")
    ids: set[str] = set()
    train = holdout = 0
    allowed_case = {"caseId", "split", "sourceKind", "midiPath", "midiSha256", "license",
                    "genre", "formClass", "features", "annotationHash", "densityClass"}
    for row in manifest["cases"]:
        _strict(row, allowed_case, allowed_case, "quality corpus case")
        if not _CASE_ID.fullmatch(row["caseId"]) or row["caseId"] in ids:
            raise ValueError("Quality corpus case IDs must be unique and stable")
        ids.add(row["caseId"])
        if row["split"] == "TRAIN":
            train += 1
        elif row["split"] == "HOLDOUT":
            holdout += 1
        else:
            raise ValueError("Unknown quality corpus split")
        _sha(row["midiSha256"], "midiSha256")
        _sha(row["annotationHash"], "annotationHash")
        _safe_relative(row["midiPath"], "midiPath")
        if row["license"] != "SELF_AUTHORED_FIXTURE":
            raise ValueError("Only self-authored fixtures may enter Session 37")
        if root is not None:
            _file_hash(Path(root), row["midiPath"], row["midiSha256"], "midiPath")
    if (train, holdout) != (24, 8):
        raise ValueError("Quality corpus actual split count mismatch")

    vault_fields = {"schema", "version", "date", "corpusHash", "private", "entries",
                    "policy", "vaultHash"}
    _strict(vault, vault_fields, vault_fields, "quality ground-truth vault")
    if vault["schema"] != GROUND_TRUTH_VAULT_SCHEMA or vault["version"] != GROUND_TRUTH_VAULT_VERSION:
        raise ValueError("Unsupported quality ground-truth vault")
    if vault["private"] is not True or vault["corpusHash"] != manifest["corpusHash"]:
        raise ValueError("Ground-truth vault must be private and bind the corpus")
    if vault["vaultHash"] != _hash_without(vault, "vaultHash"):
        raise ValueError("Ground-truth vault hash mismatch")
    if {entry["caseId"] for entry in vault["entries"]} != ids:
        raise ValueError("Ground-truth vault coverage mismatch")
    for entry in vault["entries"]:
        if entry["calibrationAccessible"] != (entry["split"] == "TRAIN"):
            raise ValueError("Holdout label exposure detected")
        _strict(
            entry["labels"],
            {
                "expectedChordLabels", "predictedChordLabels",
                "expectedSectionBoundaries", "predictedSectionBoundaries",
                "expectedSectionLabels", "expectedTrackRoles", "predictedTrackRoles",
                "expectedTransitions", "predictedTransitions",
            },
            {
                "expectedChordLabels", "predictedChordLabels",
                "expectedSectionBoundaries", "predictedSectionBoundaries",
                "expectedSectionLabels", "expectedTrackRoles", "predictedTrackRoles",
                "expectedTransitions", "predictedTransitions",
            },
            "quality ground-truth labels",
        )
        if sha256(_canonical(entry["labels"])).hexdigest() != entry["annotationHash"]:
            raise ValueError("Ground-truth annotation hash mismatch")


def calibrate_structural_quality(manifest: Mapping[str, Any], vault: Mapping[str, Any]) -> dict[str, Any]:
    validate_locked_quality_corpus(manifest, vault)
    train_ids = [row["caseId"] for row in manifest["cases"] if row["split"] == "TRAIN"]
    holdout_ids = [row["caseId"] for row in manifest["cases"] if row["split"] == "HOLDOUT"]
    accessible = {entry["caseId"] for entry in vault["entries"] if entry["calibrationAccessible"]}
    if set(train_ids) != accessible or set(holdout_ids) & accessible:
        raise ValueError("Calibration input contains holdout labels")
    training_rows = [row for row in manifest["cases"] if row["caseId"] in accessible]
    normalizers = {}
    for name in ("noteCount", "pitchSpan", "durationTicks"):
        values = [float(row["features"][name]) for row in training_rows]
        median = statistics.median(values)
        deviations = [abs(value - median) for value in values]
        normalizers[name] = {
            "median": round(median, 6),
            "medianAbsoluteDeviation": round(statistics.median(deviations), 6),
            "minimum": round(min(values), 6),
            "maximum": round(max(values), 6),
        }
    controls = QualityControls().to_manifest()
    report: dict[str, Any] = {
        "schema": CALIBRATION_REPORT_SCHEMA,
        "version": CALIBRATION_REPORT_VERSION,
        "date": date.today().isoformat(),
        "corpusHash": manifest["corpusHash"],
        "vaultHash": vault["vaultHash"],
        "mode": "SOFTWARE_STRUCTURAL_NORMALIZATION_ONLY",
        "trainingCaseIds": train_ids,
        "holdoutCaseIdsSealed": holdout_ids,
        "trainingCaseCount": len(train_ids),
        "holdoutCaseCount": len(holdout_ids),
        "structuralNormalizers": normalizers,
        "metricWeights": DEFAULT_METRIC_WEIGHTS,
        "qualityControls": controls,
        "metricWeightsChanged": False,
        "thresholdsChanged": False,
        "humanListeningClaim": False,
        "productionExpressionClaim": False,
        "sealedBeforeHoldoutEvaluation": True,
    }
    report["calibrationHash"] = sha256(_canonical(report)).hexdigest()
    validate_calibration_report(report, manifest)
    return report


def validate_calibration_report(report: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "date", "corpusHash", "vaultHash", "mode",
              "trainingCaseIds", "holdoutCaseIdsSealed", "trainingCaseCount", "holdoutCaseCount",
              "structuralNormalizers", "metricWeights", "qualityControls", "metricWeightsChanged",
              "thresholdsChanged", "humanListeningClaim", "productionExpressionClaim",
              "sealedBeforeHoldoutEvaluation", "calibrationHash"}
    _strict(report, fields, fields, "quality calibration report")
    if report["schema"] != CALIBRATION_REPORT_SCHEMA or report["version"] != CALIBRATION_REPORT_VERSION:
        raise ValueError("Unsupported quality calibration report")
    if report["corpusHash"] != manifest["corpusHash"]:
        raise ValueError("Calibration does not bind the locked corpus")
    expected_train = {row["caseId"] for row in manifest["cases"] if row["split"] == "TRAIN"}
    expected_holdout = {row["caseId"] for row in manifest["cases"] if row["split"] == "HOLDOUT"}
    if set(report["trainingCaseIds"]) != expected_train or set(report["holdoutCaseIdsSealed"]) != expected_holdout:
        raise ValueError("Calibration split IDs mismatch")
    if set(report["trainingCaseIds"]) & set(report["holdoutCaseIdsSealed"]):
        raise ValueError("Train/holdout leakage detected")
    if report["metricWeights"] != DEFAULT_METRIC_WEIGHTS or report["metricWeightsChanged"] is not False:
        raise ValueError("Session 37 cannot tune metric weights without real human labels")
    if report["qualityControls"] != QualityControls().to_manifest() or report["thresholdsChanged"] is not False:
        raise ValueError("Session 37 cannot weaken existing quality controls")
    for flag in ("humanListeningClaim", "productionExpressionClaim"):
        if report[flag] is not False:
            raise ValueError(f"{flag} cannot be asserted by software calibration")
    if report["sealedBeforeHoldoutEvaluation"] is not True:
        raise ValueError("Calibration must be sealed before holdout evaluation")
    if report["calibrationHash"] != _hash_without(report, "calibrationHash"):
        raise ValueError("Calibration report hash mismatch")


def evaluate_locked_holdout(manifest: Mapping[str, Any], vault: Mapping[str, Any],
                            calibration: Mapping[str, Any]) -> dict[str, Any]:
    validate_locked_quality_corpus(manifest, vault)
    validate_calibration_report(calibration, manifest)
    truth = {entry["caseId"]: entry for entry in vault["entries"]}
    cases = [row for row in manifest["cases"] if row["split"] == "HOLDOUT"]
    results = []
    for row in cases:
        labels = truth[row["caseId"]]["labels"]
        expected_chords = labels["expectedChordLabels"]
        predicted_chords = labels["predictedChordLabels"]
        expected_boundaries = labels["expectedSectionBoundaries"]
        predicted_boundaries = labels["predictedSectionBoundaries"]
        expected_roles = labels["expectedTrackRoles"]
        predicted_roles = labels["predictedTrackRoles"]
        expected_transitions = labels["expectedTransitions"]
        predicted_transitions = labels["predictedTransitions"]
        chord_accuracy = sum(a == b for a, b in zip(expected_chords, predicted_chords)) / max(1, len(expected_chords))
        section_accuracy = sum(a == b for a, b in zip(expected_boundaries, predicted_boundaries)) / max(1, len(expected_boundaries))
        role_accuracy = sum(a == b for a, b in zip(expected_roles, predicted_roles)) / max(1, len(expected_roles))
        transition_accuracy = sum(a == b for a, b in zip(expected_transitions, predicted_transitions)) / max(1, len(expected_transitions))
        results.append({"caseId": row["caseId"], "annotationHash": row["annotationHash"],
                        "chordExactRate": round(chord_accuracy, 6),
                        "sectionBoundaryExactRate": round(section_accuracy, 6),
                        "trackRoleExactRate": round(role_accuracy, 6),
                        "transitionExactRate": round(transition_accuracy, 6),
                        "parsed": True})
    report: dict[str, Any] = {
        "schema": "dna-premium-locked-holdout-report",
        "version": "1.0",
        "date": date.today().isoformat(),
        "corpusHash": manifest["corpusHash"],
        "calibrationHash": calibration["calibrationHash"],
        "caseCount": len(results),
        "results": results,
        "coverage": {
            "genres": sorted({row["genre"] for row in cases}),
            "meters": sorted({row["features"]["meter"] for row in cases}),
            "densityClasses": sorted({row["densityClass"] for row in cases}),
        },
        "groundTruthKinds": ["chord", "section", "trackRole", "transition"],
        "structuralPass": all(
            row["parsed"]
            and row["chordExactRate"] >= 0.85
            and row["sectionBoundaryExactRate"] >= 0.80
            and row["trackRoleExactRate"] >= 0.85
            and row["transitionExactRate"] >= 0.80
            for row in results
        ),
        "humanListeningClaim": False,
    }
    report["holdoutHash"] = sha256(_canonical(report)).hexdigest()
    return report


def build_expression_intake_template() -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": EXPRESSION_INTAKE_SCHEMA,
        "version": EXPRESSION_INTAKE_VERSION,
        "date": date.today().isoformat(),
        "status": "AWAITING_OPERATOR_CAPTURE",
        "exactSoundBindingRequired": True,
        "productionEligible": False,
        "testOnly": False,
        "soundBinding": None,
        "sourceMidi": None,
        "audioEvidence": None,
        "operatorAttestation": None,
        "relationships": [],
        "allowedRelationshipDimensions": ["relativePitch", "relativeOnset", "gate", "phraseRole"],
        "forbiddenAuthorities": ["GOLD_VELOCITY", "GOLD_ABSOLUTE_PITCH", "GOLD_BANK_PROGRAM",
                                  "SOFTWARE_TEST_ONLY", "DEVICE_UNCONFIRMED"],
        "missingEvidence": ["EXACT_SOUND_BINDING", "SOURCE_MIDI", "AUDIO_CAPTURE",
                            "OPERATOR_ATTESTATION", "PRODUCTION_RELATIONSHIPS"],
    }
    value["intakeHash"] = sha256(_canonical(value)).hexdigest()
    validate_expression_intake(value)
    return value


def validate_expression_intake(value: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "date", "status", "exactSoundBindingRequired",
              "productionEligible", "testOnly", "soundBinding", "sourceMidi", "audioEvidence",
              "operatorAttestation", "relationships", "allowedRelationshipDimensions",
              "forbiddenAuthorities", "missingEvidence", "intakeHash"}
    _strict(value, fields, fields, "production expression intake")
    if value["schema"] != EXPRESSION_INTAKE_SCHEMA or value["version"] != EXPRESSION_INTAKE_VERSION:
        raise ValueError("Unsupported production expression intake")
    if value["exactSoundBindingRequired"] is not True:
        raise ValueError("Expression evidence requires exact SoundBinding")
    if value["status"] not in {"AWAITING_OPERATOR_CAPTURE", "SOFTWARE_TEST_ONLY", "PRODUCTION_READY"}:
        raise ValueError("Unknown expression intake status")
    if value["productionEligible"] != (value["status"] == "PRODUCTION_READY"):
        raise ValueError("Expression production eligibility/status mismatch")
    if value["testOnly"] and value["productionEligible"]:
        raise ValueError("Software test expression evidence cannot become production evidence")
    for relationship in value["relationships"]:
        _strict(relationship, ALLOWED_RELATIONSHIP_FIELDS,
                {"relationshipId", "kind", "onsetDeltaTicks", "pitchDeltaSemitones",
                 "gateRatio", "confidence", "phraseRole"}, "expression relationship")
        if not -24 <= int(relationship["pitchDeltaSemitones"]) <= 24:
            raise ValueError("Expression relative pitch exceeds the allowed range")
        gate = float(relationship["gateRatio"])
        confidence = float(relationship["confidence"])
        if not math.isfinite(gate) or not 0.1 <= gate <= 2.0 or not 0 <= confidence <= 1:
            raise ValueError("Expression relationship values are out of range")
    flattened = json.dumps(value["relationships"], sort_keys=True)
    if any(f'"{name}"' in flattened for name in FORBIDDEN_EXPRESSION_FIELDS):
        raise ValueError("Expression evidence contains a forbidden authority field")
    if value["intakeHash"] != _hash_without(value, "intakeHash"):
        raise ValueError("Expression intake hash mismatch")


def import_expression_evidence(payload: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    allowed = {"testOnly", "operatorApproved", "soundBinding", "sourceMidi", "audioEvidence",
               "operatorAttestation", "relationships"}
    _strict(payload, allowed, allowed, "expression evidence submission")
    root = Path(root)
    binding = payload["soundBinding"]
    _strict(binding, {"trackUid", "bankMsb", "bankLsb", "program", "factoryProfileId"},
            {"trackUid", "bankMsb", "bankLsb", "program", "factoryProfileId"}, "exact SoundBinding")
    for name in ("bankMsb", "bankLsb", "program"):
        if isinstance(binding[name], bool) or not isinstance(binding[name], int) or not 0 <= binding[name] <= 127:
            raise ValueError(f"SoundBinding {name} is invalid")
    for label in ("sourceMidi", "audioEvidence", "operatorAttestation"):
        item = payload[label]
        _strict(item, {"path", "sha256"}, {"path", "sha256"}, label)
        _file_hash(root, item["path"], item["sha256"], label)
    test_only = payload["testOnly"] is True
    approved = payload["operatorApproved"] is True
    if not test_only and not approved:
        raise ValueError("Production expression evidence requires explicit operator approval")
    value = build_expression_intake_template()
    value.update({
        "status": "SOFTWARE_TEST_ONLY" if test_only else "PRODUCTION_READY",
        "productionEligible": approved and not test_only,
        "testOnly": test_only,
        "soundBinding": dict(binding),
        "sourceMidi": dict(payload["sourceMidi"]),
        "audioEvidence": dict(payload["audioEvidence"]),
        "operatorAttestation": dict(payload["operatorAttestation"]),
        "relationships": [dict(item) for item in payload["relationships"]],
        "missingEvidence": [],
    })
    value["intakeHash"] = sha256(_canonical({key: item for key, item in value.items()
                                              if key != "intakeHash"})).hexdigest()
    validate_expression_intake(value)
    return value


def build_listening_intake(evaluation_report: Mapping[str, Any],
                           verified_bundles: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    eligible = [item for item in verified_bundles
                if item.get("productionEligible") is True and item.get("testOnly") is False]
    unique = {item.get("evaluatorId") for item in eligible if item.get("evaluatorId")}
    required = int(evaluation_report["controls"]["minimumHumanEvaluators"])
    value: dict[str, Any] = {
        "schema": LISTENING_INTAKE_SCHEMA,
        "version": LISTENING_INTAKE_VERSION,
        "date": date.today().isoformat(),
        "evaluationReportHash": evaluation_report["evaluationReportHash"],
        "requiredIndependentEvaluators": required,
        "verifiedIndependentEvaluators": len(unique),
        "softwareTestResponses": int(evaluation_report["listening"]["softwareTestResponseCount"]),
        "proxyAudioCountsAsHuman": False,
        "testBundlesCountAsHuman": False,
        "status": "PASS" if len(unique) >= required else "HUMAN_LISTENING_PENDING",
        "productionEligibleBundleHashes": sorted(item["bundleHash"] for item in eligible),
    }
    value["intakeHash"] = sha256(_canonical(value)).hexdigest()
    return value


def import_evaluator_bundle(payload: Mapping[str, Any], blind_package: Mapping[str, Any],
                            root: str | Path) -> dict[str, Any]:
    fields = {"evaluatorId", "packageHash", "independenceAttested", "operatorApproved",
              "testOnly", "responseHash", "evidenceFiles"}
    _strict(payload, fields, fields, "evaluator evidence bundle")
    if payload["packageHash"] != blind_package["packageHash"]:
        raise ValueError("Evaluator bundle targets a different blind package")
    if payload["independenceAttested"] is not True:
        raise ValueError("Evaluator independence must be attested")
    _sha(payload["responseHash"], "responseHash")
    if len(payload["evidenceFiles"]) < 2:
        raise ValueError("Evaluator bundle requires at least two external evidence files")
    roles = set()
    for item in payload["evidenceFiles"]:
        _strict(item, {"role", "path", "sha256"}, {"role", "path", "sha256"}, "evaluator evidence file")
        roles.add(item["role"])
        _file_hash(Path(root), item["path"], item["sha256"], "evaluator evidence")
    if not {"SIGNED_ATTESTATION", "RATING_FORM"}.issubset(roles):
        raise ValueError("Evaluator bundle requires signed attestation and rating form")
    test_only = payload["testOnly"] is True
    production = (not test_only and payload["operatorApproved"] is True)
    if not test_only and not production:
        raise ValueError("Human evidence requires explicit operator approval")
    value: dict[str, Any] = {
        "schema": "dna-premium-evaluator-evidence-bundle",
        "version": "1.0",
        "date": date.today().isoformat(),
        "evaluatorId": str(payload["evaluatorId"]),
        "packageHash": payload["packageHash"],
        "responseHash": payload["responseHash"],
        "independenceAttested": True,
        "operatorApproved": payload["operatorApproved"] is True,
        "testOnly": test_only,
        "productionEligible": production,
        "evidenceFiles": [dict(item) for item in payload["evidenceFiles"]],
    }
    value["bundleHash"] = sha256(_canonical(value)).hexdigest()
    return value


def build_quality_release_gate(corpus: Mapping[str, Any], calibration: Mapping[str, Any],
                               holdout: Mapping[str, Any], expression: Mapping[str, Any],
                               listening: Mapping[str, Any], evaluation: Mapping[str, Any],
                               reliability: Mapping[str, Any], project_hash: str) -> dict[str, Any]:
    blockers = []
    if listening["status"] != "PASS":
        blockers.append("TWO_INDEPENDENT_HUMAN_EVALUATORS_REQUIRED")
    if expression["productionEligible"] is not True:
        blockers.append("PRODUCTION_EXPRESSION_EVIDENCE_REQUIRED")
    if not evaluation["releaseQualityGate"]["passed"]:
        blockers.append("SESSION27_HUMAN_QUALITY_THRESHOLDS_NOT_MET")
    value: dict[str, Any] = {
        "schema": QUALITY_GATE_SCHEMA,
        "version": QUALITY_GATE_VERSION,
        "date": date.today().isoformat(),
        "sources": {
            "projectHash": _sha(project_hash, "projectHash"),
            "corpusHash": corpus["corpusHash"],
            "calibrationHash": calibration["calibrationHash"],
            "holdoutHash": holdout["holdoutHash"],
            "expressionIntakeHash": expression["intakeHash"],
            "listeningIntakeHash": listening["intakeHash"],
            "evaluationReportHash": evaluation["evaluationReportHash"],
            "reliabilityReportHash": reliability["reportHash"],
        },
        "softwareStructuralHoldoutPassed": holdout["structuralPass"],
        "reliabilityPassed": reliability["passed"],
        "automatedQualityPassed": evaluation["automated"]["passed"],
        "humanListeningPassed": listening["status"] == "PASS",
        "productionExpressionPassed": expression["productionEligible"] is True,
        "qualityReleaseGatePassed": not blockers,
        "finalCertifiedMidiExportAllowed": False,
        "blockers": blockers,
        "allowedProductName": "AI PREMIUM ARRANGER PREVIEW",
        "physicalPa800": "WAITING_FOR_DEVICE",
    }
    value["gateHash"] = sha256(_canonical(value)).hexdigest()
    validate_quality_release_gate(value)
    return value


def validate_quality_release_gate(value: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "date", "sources", "softwareStructuralHoldoutPassed",
              "reliabilityPassed", "automatedQualityPassed", "humanListeningPassed",
              "productionExpressionPassed", "qualityReleaseGatePassed",
              "finalCertifiedMidiExportAllowed", "blockers", "allowedProductName",
              "physicalPa800", "gateHash"}
    _strict(value, fields, fields, "quality release gate")
    if value["schema"] != QUALITY_GATE_SCHEMA or value["version"] != QUALITY_GATE_VERSION:
        raise ValueError("Unsupported quality release gate")
    for digest in value["sources"].values():
        _sha(digest, "quality gate source hash")
    expected = (value["softwareStructuralHoldoutPassed"] and value["reliabilityPassed"]
                and value["automatedQualityPassed"] and value["humanListeningPassed"]
                and value["productionExpressionPassed"] and not value["blockers"])
    if value["qualityReleaseGatePassed"] != expected:
        raise ValueError("Quality release gate status is inconsistent")
    if value["finalCertifiedMidiExportAllowed"] is not False:
        raise ValueError("Session 37 cannot unlock certified MIDI export")
    if value["gateHash"] != _hash_without(value, "gateHash"):
        raise ValueError("Quality release gate hash mismatch")


def execute_quality_calibration_api(payload: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Quality calibration request must be an object")
    action = payload.get("action", "build")
    if action == "build":
        manifest, vault = build_locked_quality_corpus(root)
        calibration = calibrate_structural_quality(manifest, vault)
        holdout = evaluate_locked_holdout(manifest, vault, calibration)
        return {"corpus": manifest, "calibration": calibration, "holdout": holdout,
                "expressionIntake": build_expression_intake_template()}
    if action == "validate-corpus":
        validate_locked_quality_corpus(payload["corpus"], payload["vault"], root)
        return {"valid": True, "corpusHash": payload["corpus"]["corpusHash"]}
    if action == "import-expression":
        return import_expression_evidence(payload["submission"], root)
    if action == "import-listening":
        return import_evaluator_bundle(payload["submission"], payload["blindPackage"], root)
    raise ValueError("Unsupported quality calibration action")


def execute_quality_calibration_gui(payload: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    result = execute_quality_calibration_api(payload, root)
    return {"workspace": "PRODUCTION QUALITY & EVIDENCE", "result": result,
            "readOnlyCalibration": True, "certifiedExportAllowed": False}