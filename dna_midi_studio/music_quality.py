"""Session 27 explainable music-quality evaluation and blind listening gates.

Automatic structural metrics are useful evidence, but they are not presented as
human listening results.  Only independently attested HUMAN_VERIFIED responses
count toward the Premium preference and median-rating release thresholds.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import statistics
from typing import Any, Mapping, Sequence

from .premium_preview import render_preview_wav, validate_preview_session_v2


EVALUATION_REPORT_SCHEMA = "dna-premium-evaluation-report"
EVALUATION_REPORT_VERSION = "2.0"
BLIND_LISTENING_PACKAGE_SCHEMA = "dna-premium-blind-listening-package"
BLIND_LISTENING_PACKAGE_VERSION = "1.0"
LISTENING_RESPONSE_SCHEMA = "dna-premium-listening-response"
LISTENING_RESPONSE_VERSION = "1.0"
QUALITY_REGRESSION_VAULT_SCHEMA = "dna-premium-quality-regression-vault"
QUALITY_REGRESSION_VAULT_VERSION = "1.0"
QUALITY_CONTROLS_VERSION = "1.0"

AUTOMATED_METRICS = (
    "harmony", "groove", "registerCollision", "densityCurve",
    "transitionContinuity", "repetition", "endingResolution",
)
RATING_CATEGORIES = (
    "drum", "bass", "guitar", "accompaniment", "solo", "transition", "overall",
)
LISTENING_AUTHORITIES = ("HUMAN_VERIFIED", "SOFTWARE_TEST_ONLY")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_CHORD_INTERVALS = {
    "major": {0, 4, 7}, "minor": {0, 3, 7}, "diminished": {0, 3, 6},
    "augmented": {0, 4, 8}, "power": {0, 7}, "suspended-second": {0, 2, 7},
    "suspended-fourth": {0, 5, 7}, "dominant-seventh": {0, 4, 7, 10},
    "major-seventh": {0, 4, 7, 11}, "minor-seventh": {0, 3, 7, 10},
    "sixth": {0, 4, 7, 9}, "minor-sixth": {0, 3, 7, 9},
    "add-nine": {0, 2, 4, 7}, "none": set(),
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return sha256(_canonical({key: item for key, item in value.items() if key != field})).hexdigest()


def _require_keys(value: Mapping[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unknown:
        raise ValueError(f"Unknown {label} fields: " + ", ".join(unknown))
    if missing:
        raise ValueError(f"Missing {label} fields: " + ", ".join(missing))


def _sha(value: Any, label: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _integer(value: Any, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in range {low}..{high}")
    return value


def _number(value: Any, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not low <= result <= high:
        raise ValueError(f"{label} must be in range {low}..{high}")
    return result


def _score(value: float) -> float:
    return round(max(0.0, min(5.0, value)), 3)


@dataclass(frozen=True)
class QualityControls:
    version: str = QUALITY_CONTROLS_VERSION
    premium_variant_id: str = "C"
    minimum_metric_score: float = 2.5
    minimum_automated_overall: float = 3.5
    minimum_human_overall_median: float = 4.0
    minimum_premium_preference_rate: float = 0.70
    minimum_human_evaluators: int = 2
    maximum_harmony_violation_rate: float = 0.45
    maximum_register_collision_rate: float = 0.12

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "QualityControls":
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("Quality controls must be an object")
        fields = {"version", "premiumVariantId", "minimumMetricScore",
                  "minimumAutomatedOverall", "minimumHumanOverallMedian",
                  "minimumPremiumPreferenceRate", "minimumHumanEvaluators",
                  "maximumHarmonyViolationRate", "maximumRegisterCollisionRate"}
        _require_keys(value, fields, {"version"}, "quality control")
        if value["version"] != QUALITY_CONTROLS_VERSION:
            raise ValueError("Unsupported quality controls version")
        variant = value.get("premiumVariantId", "C")
        if variant not in {"B", "C"}:
            raise ValueError("Premium quality variant must be B or C")
        return cls(
            version=value["version"], premium_variant_id=variant,
            minimum_metric_score=_number(value.get("minimumMetricScore", 2.5),
                                         "minimumMetricScore", 0, 5),
            minimum_automated_overall=_number(value.get("minimumAutomatedOverall", 3.5),
                                              "minimumAutomatedOverall", 0, 5),
            minimum_human_overall_median=_number(value.get("minimumHumanOverallMedian", 4.0),
                                                 "minimumHumanOverallMedian", 1, 5),
            minimum_premium_preference_rate=_number(value.get("minimumPremiumPreferenceRate", 0.70),
                                                    "minimumPremiumPreferenceRate", 0, 1),
            minimum_human_evaluators=_integer(value.get("minimumHumanEvaluators", 2),
                                              "minimumHumanEvaluators", 2, 20),
            maximum_harmony_violation_rate=_number(value.get("maximumHarmonyViolationRate", 0.45),
                                                   "maximumHarmonyViolationRate", 0, 1),
            maximum_register_collision_rate=_number(value.get("maximumRegisterCollisionRate", 0.12),
                                                    "maximumRegisterCollisionRate", 0, 1),
        )

    def to_manifest(self) -> dict[str, Any]:
        return {"version": self.version, "premiumVariantId": self.premium_variant_id,
                "minimumMetricScore": self.minimum_metric_score,
                "minimumAutomatedOverall": self.minimum_automated_overall,
                "minimumHumanOverallMedian": self.minimum_human_overall_median,
                "minimumPremiumPreferenceRate": self.minimum_premium_preference_rate,
                "minimumHumanEvaluators": self.minimum_human_evaluators,
                "maximumHarmonyViolationRate": self.maximum_harmony_violation_rate,
                "maximumRegisterCollisionRate": self.maximum_register_collision_rate}


def load_baseline_reference(root: str | Path) -> dict[str, Any]:
    root = Path(root)
    document_path = root / "data" / "premium-baseline.json"
    document = json.loads(document_path.read_text(encoding="utf-8"))
    if document.get("schema") != "dna-premium-baseline" or document.get("version") != "1.0":
        raise ValueError("Unsupported Premium baseline document")
    if document.get("baselineId") != "premium-3.17-98a6d52a09ccc789":
        raise ValueError("Session 27 requires the immutable 3.17 baseline ID")
    _sha(document.get("contentHash"), "baseline contentHash")
    target = next((item for item in document.get("frozenFiles", [])
                   if item.get("path") == "premium/baseline/reference-style.mid"), None)
    if target is None:
        raise ValueError("Premium baseline is missing its reference Style")
    artifact = root / target["path"]
    if not artifact.is_file() or sha256(artifact.read_bytes()).hexdigest() != target.get("sha256"):
        raise ValueError("Immutable 3.17 reference Style hash mismatch")
    reference = {"schema": "dna-premium-baseline-reference", "version": "1.0",
                 "baselineId": document["baselineId"], "contentHash": document["contentHash"],
                 "artifactPath": target["path"], "artifactSha256": target["sha256"],
                 "policy": "SAME_SOURCE_PAIRED_RENDER_REQUIRED_FOR_LISTENING_CLAIM"}
    reference["referenceHash"] = sha256(_canonical(reference)).hexdigest()
    return reference


def validate_baseline_reference(value: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "baselineId", "contentHash", "artifactPath",
              "artifactSha256", "policy", "referenceHash"}
    _require_keys(value, fields, fields, "baseline reference")
    if value["schema"] != "dna-premium-baseline-reference" or value["version"] != "1.0":
        raise ValueError("Unsupported baseline reference")
    if value["baselineId"] != "premium-3.17-98a6d52a09ccc789":
        raise ValueError("Wrong immutable baseline ID")
    for key in ("contentHash", "artifactSha256", "referenceHash"):
        _sha(value[key], key)
    if value["artifactPath"] != "premium/baseline/reference-style.mid":
        raise ValueError("Baseline artifact path is not the frozen reference Style")
    if value["policy"] != "SAME_SOURCE_PAIRED_RENDER_REQUIRED_FOR_LISTENING_CLAIM":
        raise ValueError("Baseline listening policy cannot be weakened")
    if value["referenceHash"] != _hash_without(value, "referenceHash"):
        raise ValueError("Baseline reference hash mismatch")


def _variant(session: Mapping[str, Any], variant_id: str) -> Mapping[str, Any]:
    matches = [item for item in session["variants"] if item["variantId"] == variant_id]
    if len(matches) != 1:
        raise ValueError(f"Preview is missing unique variant {variant_id}")
    return matches[0]


def _musical_notes(variant: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [note for note in variant["notes"]
            if note.get("role") not in {"drums", "percussion", "unknown"}]


def _cell_for(song_map: Mapping[str, Any], tick: int) -> Mapping[str, Any] | None:
    return next((cell for cell in song_map.get("chordCells", [])
                 if int(cell.get("startTick", -1)) <= tick < int(cell.get("endTick", -1))), None)


def _harmony_metric(variant: Mapping[str, Any], song_map: Mapping[str, Any]) -> dict[str, Any]:
    tested = violations = unresolved = 0
    examples = []
    for note in _musical_notes(variant):
        cell = _cell_for(song_map, int(note["startTick"]))
        if not cell or cell.get("root") is None or cell.get("quality") == "none":
            unresolved += 1
            continue
        intervals = _CHORD_INTERVALS.get(str(cell.get("quality")), set())
        tones = {(int(cell["root"]) + interval) % 12 for interval in intervals}
        if not tones:
            unresolved += 1
            continue
        tested += 1
        if int(note["pitch"]) % 12 not in tones:
            violations += 1
            if len(examples) < 8:
                examples.append({"noteUid": note["noteUid"], "pitch": note["pitch"],
                                 "chord": cell.get("symbol"), "tick": note["startTick"]})
    rate = violations / tested if tested else 0.0
    return {"score": _score(5.0 * (1.0 - min(1.0, rate * 1.25))),
            "facts": {"testedNoteCount": tested, "violationCount": violations,
                      "violationRate": round(rate, 6), "unresolvedCount": unresolved,
                      "examples": examples},
            "explanation": "Chord-tone agreement at each note onset; drums and unresolved cells are excluded."}


def _groove_metric(variant: Mapping[str, Any], ppq: int) -> dict[str, Any]:
    notes = variant["notes"]
    step = max(1, ppq // 4)
    deviations = [abs(int(note["startTick"]) - round(int(note["startTick"]) / step) * step)
                  for note in notes]
    normalized = statistics.mean(deviations) / max(1, step / 2) if deviations else 0.0
    offgrid_rate = sum(value > step * 0.35 for value in deviations) / max(1, len(deviations))
    return {"score": _score(5.0 * (1.0 - min(1.0, normalized * 0.62 + offgrid_rate * 0.38))),
            "facts": {"noteCount": len(notes), "gridTicks": step,
                      "meanAbsoluteDeviationTicks": round(statistics.mean(deviations), 3) if deviations else 0.0,
                      "strongOffGridRate": round(offgrid_rate, 6)},
            "explanation": "Onset stability relative to a 1/16 grid; expressive offsets remain measurable."}


def _register_metric(variant: Mapping[str, Any]) -> dict[str, Any]:
    notes = _musical_notes(variant)
    pairs = collisions = 0
    examples = []
    for index, first in enumerate(notes):
        for second in notes[index + 1:]:
            if second["startTick"] >= first["endTick"] or first["startTick"] >= second["endTick"]:
                continue
            if first["role"] == second["role"] or first["trackUid"] == second["trackUid"]:
                continue
            pairs += 1
            if abs(int(first["pitch"]) - int(second["pitch"])) <= 1:
                collisions += 1
                if len(examples) < 8:
                    examples.append({"firstNoteUid": first["noteUid"], "secondNoteUid": second["noteUid"],
                                     "pitches": [first["pitch"], second["pitch"]]})
    rate = collisions / pairs if pairs else 0.0
    return {"score": _score(5.0 * (1.0 - min(1.0, rate * 4.0))),
            "facts": {"overlappingCrossRolePairs": pairs, "collisionCount": collisions,
                      "collisionRate": round(rate, 6), "examples": examples},
            "explanation": "Simultaneous cross-role semitone/unison congestion; same-role voicing is not penalized."}


def _density_metric(variant: Mapping[str, Any], song_map: Mapping[str, Any]) -> dict[str, Any]:
    targets = {"intro": 0.55, "verse": 0.62, "chorus": 1.0, "bridge": 0.78,
               "ending": 0.58, "break": 0.40}
    rows = []
    raw = []
    for section in song_map.get("sections", []):
        start, end = int(section["startTick"]), int(section["endTick"])
        count = sum(start <= int(note["startTick"]) < end for note in variant["notes"])
        duration = max(1, end - start)
        density = count / duration
        raw.append(density)
        rows.append({"sectionId": section["id"], "label": section.get("label", "unknown"),
                     "noteCount": count, "durationTicks": duration, "rawDensity": density})
    maximum = max(raw, default=0.0)
    errors = []
    for row in rows:
        observed = row["rawDensity"] / maximum if maximum else 0.0
        target = targets.get(row["label"], 0.70)
        row.update({"normalizedDensity": round(observed, 6), "targetDensity": target})
        errors.append(abs(observed - target))
        del row["rawDensity"]
    mae = statistics.mean(errors) if errors else 1.0
    return {"score": _score(5.0 * (1.0 - min(1.0, mae))),
            "facts": {"sections": rows, "meanAbsoluteTargetError": round(mae, 6)},
            "explanation": "Section density shape normalized within the candidate and compared with role-neutral targets."}


def _transition_metric(variant: Mapping[str, Any], song_map: Mapping[str, Any]) -> dict[str, Any]:
    ppq = int(song_map.get("ppq", 480))
    boundaries = []
    sections = song_map.get("sections", [])
    for left, right in zip(sections, sections[1:]):
        tick = int(right["startTick"])
        before = {note["role"] for note in variant["notes"]
                  if tick - ppq <= int(note["startTick"]) < tick}
        after = {note["role"] for note in variant["notes"]
                 if tick <= int(note["startTick"]) < tick + ppq}
        continuity = len(before & after) / max(1, len(before | after))
        activity = min(1.0, (len(before) + len(after)) / 4.0)
        score = 0.58 * activity + 0.42 * continuity
        if right.get("label") == "ending" and before and not after:
            # A deliberate release into a silent ending cell is a valid cadence,
            # not automatically a broken transition.
            score = max(score, 0.85)
        boundaries.append({"tick": tick, "fromSection": left["id"], "toSection": right["id"],
                           "rolesBefore": sorted(before), "rolesAfter": sorted(after),
                           "continuity": round(continuity, 6), "activity": round(activity, 6),
                           "boundaryScore": round(score, 6)})
    mean = statistics.mean(item["boundaryScore"] for item in boundaries) if boundaries else 0.5
    return {"score": _score(5.0 * mean), "facts": {"boundaryCount": len(boundaries),
                                                    "boundaries": boundaries},
            "explanation": "Activity and role continuity in one-quarter-note windows around section boundaries."}


def _repetition_metric(variant: Mapping[str, Any], ppq: int) -> dict[str, Any]:
    bar = ppq * 4
    signatures: dict[int, list[tuple[str, int, int]]] = defaultdict(list)
    for note in variant["notes"]:
        index = int(note["startTick"]) // bar
        signatures[index].append((note["role"], int(note["startTick"]) % bar,
                                  int(note["pitch"]) % 12))
    normalized = [tuple(sorted(value)) for _, value in sorted(signatures.items())]
    duplicates = len(normalized) - len(set(normalized))
    duplicate_rate = duplicates / max(1, len(normalized) - 1)
    # Some repetition creates identity; both zero and extreme duplication are less desirable.
    desirability = 1.0 - min(1.0, abs(duplicate_rate - 0.30) / 0.70)
    return {"score": _score(2.5 + 2.5 * desirability),
            "facts": {"barSignatureCount": len(normalized), "duplicateCount": duplicates,
                      "duplicateRate": round(duplicate_rate, 6)},
            "explanation": "Exact role/onset/pitch-class bar repetition; moderate recurrence is treated as identity."}


def _ending_metric(variant: Mapping[str, Any], song_map: Mapping[str, Any]) -> dict[str, Any]:
    notes = _musical_notes(variant)
    ending = next((section for section in reversed(song_map.get("sections", []))
                   if section.get("label") == "ending"), None)
    if ending:
        candidates = [note for note in notes if int(note["startTick"]) < int(ending["endTick"])]
    else:
        candidates = notes
    if not candidates:
        return {"score": 0.0, "facts": {"resolved": False, "reason": "NO_MUSICAL_NOTES"},
                "explanation": "No pitched ending note is available."}
    last = max(candidates, key=lambda item: (item["endTick"], item["startTick"], item["pitch"]))
    cell = _cell_for(song_map, max(0, int(last["endTick"]) - 1)) or _cell_for(song_map, int(last["startTick"]))
    tonic = song_map.get("key", {}).get("root")
    pitch_class = int(last["pitch"]) % 12
    root = cell.get("root") if cell else None
    tones = ({(int(root) + interval) % 12 for interval in
              _CHORD_INTERVALS.get(str(cell.get("quality")), set())} if root is not None else set())
    if tonic is not None and pitch_class == int(tonic):
        value, reason = 5.0, "KEY_TONIC"
    elif root is not None and pitch_class == int(root):
        value, reason = 4.8, "ENDING_CHORD_ROOT"
    elif pitch_class in tones:
        value, reason = 4.0, "ENDING_CHORD_TONE"
    else:
        value, reason = 1.8, "UNRESOLVED_NON_CHORD_TONE"
    return {"score": _score(value),
            "facts": {"lastNoteUid": last["noteUid"], "pitch": last["pitch"],
                      "pitchClass": pitch_class, "keyRoot": tonic, "endingChord": cell.get("symbol") if cell else None,
                      "resolutionReason": reason},
            "explanation": "Final pitched note resolution against the ending chord and detected key tonic."}


def calculate_automated_metrics(preview_session: Mapping[str, Any], song_map: Mapping[str, Any],
                                variant_id: str = "C") -> dict[str, Any]:
    validate_preview_session_v2(preview_session)
    if song_map.get("schema") != "dna-premium-song-map" or song_map.get("version") != "2.0":
        raise ValueError("Quality evaluation requires SongMap 2.0")
    if song_map.get("sourceSha256") != preview_session["source"]["midiSha256"]:
        raise ValueError("SongMap and PreviewSession source hashes differ")
    variant = _variant(preview_session, variant_id)
    ppq = int(song_map.get("ppq", 480))
    metrics = {
        "harmony": _harmony_metric(variant, song_map),
        "groove": _groove_metric(variant, ppq),
        "registerCollision": _register_metric(variant),
        "densityCurve": _density_metric(variant, song_map),
        "transitionContinuity": _transition_metric(variant, song_map),
        "repetition": _repetition_metric(variant, ppq),
        "endingResolution": _ending_metric(variant, song_map),
    }
    weights = {"harmony": 0.20, "groove": 0.16, "registerCollision": 0.14,
               "densityCurve": 0.14, "transitionContinuity": 0.14,
               "repetition": 0.08, "endingResolution": 0.14}
    overall = sum(metrics[name]["score"] * weights[name] for name in AUTOMATED_METRICS)
    for name in AUTOMATED_METRICS:
        metrics[name]["weight"] = weights[name]
    category_scores = {
        "drum": _score((metrics["groove"]["score"] + metrics["densityCurve"]["score"]) / 2),
        "bass": _score((metrics["harmony"]["score"] + metrics["groove"]["score"]) / 2),
        "guitar": _score((metrics["harmony"]["score"] + metrics["registerCollision"]["score"]) / 2),
        "accompaniment": _score((metrics["densityCurve"]["score"] + metrics["registerCollision"]["score"]) / 2),
        "solo": _score((metrics["harmony"]["score"] + metrics["endingResolution"]["score"]) / 2),
        "transition": _score((metrics["transitionContinuity"]["score"] + metrics["endingResolution"]["score"]) / 2),
        "overall": _score(overall),
    }
    return {"variantId": variant_id, "metrics": metrics, "categoryScores": category_scores,
            "overallScore": _score(overall), "method": "DETERMINISTIC_MIDI_STRUCTURE_ONLY",
            "humanListeningClaim": False}


def build_blind_listening_package(preview_session: Mapping[str, Any],
                                  audio_manifests: Mapping[str, Mapping[str, Any]],
                                  baseline_reference: Mapping[str, Any], seed: int = 2700
                                  ) -> tuple[dict[str, Any], dict[str, Any]]:
    validate_preview_session_v2(preview_session)
    validate_baseline_reference(baseline_reference)
    seed = _integer(seed, "blind seed", 0, 2_147_483_647)
    required = {"A", "B", "C"}
    if set(audio_manifests) != required:
        raise ValueError("Blind package requires exact A, B and C audio manifests")
    for variant_id, manifest in audio_manifests.items():
        if manifest.get("previewSessionHash") != preview_session["previewSessionHash"] \
                or manifest.get("variantId") != variant_id:
            raise ValueError("Audio manifest does not belong to its preview variant")
        _sha(manifest.get("wavSha256"), f"{variant_id} wavSha256")
        if manifest.get("deviceAudio") is not False or manifest.get("affectsMidi") is not False:
            raise ValueError("Blind fixture audio must be validation-neutral proxy audio")
    benchmark_id = "blind-" + sha256(_canonical([baseline_reference["referenceHash"],
                                                   preview_session["previewSessionHash"], seed])).hexdigest()[:20]
    mappings = []
    trials = []
    for index, premium_id in enumerate(("B", "C"), start=1):
        trial_id = f"trial-{index:03d}"
        pair = [("A", audio_manifests["A"]), (premium_id, audio_manifests[premium_id])]
        reverse = int(sha256(_canonical([seed, trial_id])).hexdigest(), 16) % 2 == 1
        if reverse:
            pair.reverse()
        clips = []
        mapping = {"trialId": trial_id, "baselineVariantId": "A", "premiumVariantId": premium_id,
                   "clipRoles": {}}
        for position, (variant_id, manifest) in zip(("left", "right"), pair):
            clip_id = "clip-" + sha256(_canonical([benchmark_id, trial_id, position,
                                                    manifest["wavSha256"]])).hexdigest()[:20]
            clips.append({"position": position, "clipId": clip_id,
                          "audioSha256": manifest["wavSha256"],
                          "durationSeconds": manifest["durationSeconds"],
                          "loudnessMethod": "SESSION26_MATCHED_PROXY"})
            mapping["clipRoles"][clip_id] = "BASELINE" if variant_id == "A" else "PREMIUM"
        mappings.append(mapping)
        trials.append({"trialId": trial_id, "clips": clips,
                       "ratingCategories": list(RATING_CATEGORIES),
                       "prompt": "Choose the preferred clip or TIE, then rate the selected musical result."})
    private_key = {"schema": "dna-premium-blind-listening-key", "version": "1.0",
                   "benchmarkId": benchmark_id, "mappings": mappings}
    private_key["keyHash"] = sha256(_canonical(private_key)).hexdigest()
    package = {
        "schema": BLIND_LISTENING_PACKAGE_SCHEMA, "version": BLIND_LISTENING_PACKAGE_VERSION,
        "benchmarkId": benchmark_id, "baselineReference": baseline_reference,
        "sourceMidiSha256": preview_session["source"]["midiSha256"],
        "previewSessionHash": preview_session["previewSessionHash"], "seed": seed,
        "blind": True, "locked": True, "trialCount": len(trials), "trials": trials,
        "privateKeyHash": private_key["keyHash"],
        "authority": "PROTOCOL_READY_PROXY_AUDIO_NOT_HUMAN_RESULT",
    }
    package["packageHash"] = sha256(_canonical(package)).hexdigest()
    validate_blind_listening_package(package)
    validate_blind_listening_key(private_key, package)
    return package, private_key


def validate_blind_listening_package(package: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "benchmarkId", "baselineReference", "sourceMidiSha256",
              "previewSessionHash", "seed", "blind", "locked", "trialCount", "trials",
              "privateKeyHash", "authority", "packageHash"}
    _require_keys(package, fields, fields, "blind listening package")
    if package["schema"] != BLIND_LISTENING_PACKAGE_SCHEMA or package["version"] != BLIND_LISTENING_PACKAGE_VERSION:
        raise ValueError("Unsupported blind listening package")
    if package["blind"] is not True or package["locked"] is not True:
        raise ValueError("Listening benchmark must remain blind and locked")
    if package["authority"] != "PROTOCOL_READY_PROXY_AUDIO_NOT_HUMAN_RESULT":
        raise ValueError("Blind package cannot claim a listening result")
    validate_baseline_reference(package["baselineReference"])
    for key in ("sourceMidiSha256", "previewSessionHash", "privateKeyHash", "packageHash"):
        _sha(package[key], key)
    if package["trialCount"] != len(package["trials"]) or not package["trials"]:
        raise ValueError("Blind package trial count mismatch")
    serialized_trials = json.dumps(package["trials"], sort_keys=True).lower()
    if "variantid" in serialized_trials or "baselinevariant" in serialized_trials or "premiumvariant" in serialized_trials:
        raise ValueError("Public blind trials leak variant identity")
    trial_ids = []
    clip_ids = []
    for trial in package["trials"]:
        if set(trial) != {"trialId", "clips", "ratingCategories", "prompt"} or len(trial["clips"]) != 2:
            raise ValueError("Blind trial structure is invalid")
        trial_ids.append(trial["trialId"])
        if trial["ratingCategories"] != list(RATING_CATEGORIES):
            raise ValueError("Blind trial rating categories changed")
        for clip in trial["clips"]:
            if set(clip) != {"position", "clipId", "audioSha256", "durationSeconds", "loudnessMethod"}:
                raise ValueError("Blind clip structure is invalid")
            _sha(clip["audioSha256"], "clip audioSha256")
            clip_ids.append(clip["clipId"])
    if len(trial_ids) != len(set(trial_ids)) or len(clip_ids) != len(set(clip_ids)):
        raise ValueError("Blind trial and clip IDs must be unique")
    if package["packageHash"] != _hash_without(package, "packageHash"):
        raise ValueError("Blind package hash mismatch")


def validate_blind_listening_key(key: Mapping[str, Any], package: Mapping[str, Any]) -> None:
    if set(key) != {"schema", "version", "benchmarkId", "mappings", "keyHash"}:
        raise ValueError("Blind private key structure is invalid")
    if key["schema"] != "dna-premium-blind-listening-key" or key["version"] != "1.0":
        raise ValueError("Unsupported blind private key")
    if key["benchmarkId"] != package["benchmarkId"] or key["keyHash"] != package["privateKeyHash"]:
        raise ValueError("Blind key does not belong to package")
    if key["keyHash"] != _hash_without(key, "keyHash"):
        raise ValueError("Blind private key hash mismatch")
    trials = {item["trialId"]: {clip["clipId"] for clip in item["clips"]} for item in package["trials"]}
    if len(key["mappings"]) != len(trials):
        raise ValueError("Blind key mapping count mismatch")
    for mapping in key["mappings"]:
        if set(mapping) != {"trialId", "baselineVariantId", "premiumVariantId", "clipRoles"}:
            raise ValueError("Blind key mapping structure is invalid")
        if set(mapping["clipRoles"]) != trials.get(mapping["trialId"], set()) \
                or set(mapping["clipRoles"].values()) != {"BASELINE", "PREMIUM"}:
            raise ValueError("Blind key clip mapping is incomplete")


def validate_listening_response(response: Mapping[str, Any], package: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "responseId", "packageHash", "evaluator", "submittedAt",
              "trials", "responseHash"}
    _require_keys(response, fields, fields, "listening response")
    if response["schema"] != LISTENING_RESPONSE_SCHEMA or response["version"] != LISTENING_RESPONSE_VERSION:
        raise ValueError("Unsupported listening response")
    if response["packageHash"] != package["packageHash"]:
        raise ValueError("Listening response belongs to another blind package")
    evaluator = response["evaluator"]
    if set(evaluator) != {"evaluatorId", "authority", "independenceAttested", "evidenceHash"}:
        raise ValueError("Listening evaluator structure is invalid")
    if evaluator["authority"] not in LISTENING_AUTHORITIES:
        raise ValueError("Listening evaluator authority is invalid")
    if not isinstance(evaluator["evaluatorId"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", evaluator["evaluatorId"]):
        raise ValueError("evaluatorId must be a controlled identifier")
    if not isinstance(evaluator["independenceAttested"], bool):
        raise ValueError("independenceAttested must be boolean")
    if evaluator["authority"] == "HUMAN_VERIFIED":
        if not evaluator["independenceAttested"]:
            raise ValueError("Verified human evaluator must attest independence")
        _sha(evaluator["evidenceHash"], "evaluator evidenceHash")
    elif evaluator["evidenceHash"] is not None:
        _sha(evaluator["evidenceHash"], "software evidenceHash")
    try:
        submitted = date.fromisoformat(response["submittedAt"])
    except (TypeError, ValueError) as exc:
        raise ValueError("submittedAt must be an ISO date") from exc
    if submitted > date.today():
        raise ValueError("Listening response date cannot be in the future")
    expected = {item["trialId"]: {clip["clipId"] for clip in item["clips"]}
                for item in package["trials"]}
    if len(response["trials"]) != len(expected):
        raise ValueError("Listening response must answer every trial exactly once")
    seen = set()
    for trial in response["trials"]:
        if set(trial) != {"trialId", "preferredClipId", "ratings", "comment"}:
            raise ValueError("Listening trial response structure is invalid")
        trial_id = trial["trialId"]
        if trial_id in seen or trial_id not in expected:
            raise ValueError("Listening trial response ID is duplicate or unknown")
        seen.add(trial_id)
        if trial["preferredClipId"] != "TIE" and trial["preferredClipId"] not in expected[trial_id]:
            raise ValueError("Preferred clip is not part of the blind trial")
        if set(trial["ratings"]) != set(RATING_CATEGORIES):
            raise ValueError("Listening ratings must cover all categories")
        for category, rating in trial["ratings"].items():
            _integer(rating, f"rating.{category}", 1, 5)
        if not isinstance(trial["comment"], str) or len(trial["comment"]) > 2000:
            raise ValueError("Listening comment must be text up to 2000 characters")
    if response["responseHash"] != _hash_without(response, "responseHash"):
        raise ValueError("Listening response hash mismatch")


def summarize_listening(package: Mapping[str, Any], private_key: Mapping[str, Any],
                        responses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    validate_blind_listening_package(package)
    validate_blind_listening_key(private_key, package)
    response_ids = []
    evaluator_ids = []
    for response in responses:
        validate_listening_response(response, package)
        response_ids.append(response["responseId"])
        evaluator_ids.append(response["evaluator"]["evaluatorId"])
    if len(response_ids) != len(set(response_ids)) or len(evaluator_ids) != len(set(evaluator_ids)):
        raise ValueError("Listening responses require unique response and evaluator IDs")
    roles = {mapping["trialId"]: mapping["clipRoles"] for mapping in private_key["mappings"]}
    verified = [response for response in responses if response["evaluator"]["authority"] == "HUMAN_VERIFIED"]
    software = [response for response in responses if response["evaluator"]["authority"] == "SOFTWARE_TEST_ONLY"]
    premium = baseline = ties = 0
    ratings: dict[str, list[int]] = {category: [] for category in RATING_CATEGORIES}
    for response in verified:
        for trial in response["trials"]:
            chosen = trial["preferredClipId"]
            if chosen == "TIE":
                ties += 1
            elif roles[trial["trialId"]][chosen] == "PREMIUM":
                premium += 1
            else:
                baseline += 1
            for category in RATING_CATEGORIES:
                ratings[category].append(trial["ratings"][category])
    decided = premium + baseline
    return {
        "performed": bool(verified), "verifiedHumanEvaluatorCount": len(verified),
        "softwareTestResponseCount": len(software), "trialDecisionCount": premium + baseline + ties,
        "premiumPreferredCount": premium, "baselinePreferredCount": baseline, "tieCount": ties,
        "premiumPreferenceRate": round(premium / decided, 6) if decided else None,
        "categoryMedians": {category: (float(statistics.median(values)) if values else None)
                            for category, values in ratings.items()},
        "humanEvidenceOnly": True,
    }


def build_quality_regression_vault(entries: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    normalized = []
    for entry in entries or []:
        fields = {"caseId", "baselineAudioSha256", "premiumAudioSha256", "outcome", "status",
                  "evaluatorEvidenceHash", "resolutionEvidenceHash", "reason"}
        _require_keys(entry, fields, fields, "quality regression entry")
        if not isinstance(entry["caseId"], str) or not _CASE_ID.fullmatch(entry["caseId"]):
            raise ValueError("Regression caseId must be a controlled identifier")
        for key in ("baselineAudioSha256", "premiumAudioSha256", "evaluatorEvidenceHash"):
            _sha(entry[key], key)
        _sha(entry["resolutionEvidenceHash"], "resolutionEvidenceHash", optional=True)
        if entry["outcome"] not in {"BASELINE_BETTER", "PREMIUM_BETTER", "TIE"}:
            raise ValueError("Regression outcome is invalid")
        if entry["status"] not in {"OPEN", "RESOLVED"}:
            raise ValueError("Regression status is invalid")
        if entry["status"] == "RESOLVED" and entry["resolutionEvidenceHash"] is None:
            raise ValueError("Resolved regression requires resolution evidence")
        if entry["status"] == "OPEN" and entry["resolutionEvidenceHash"] is not None:
            raise ValueError("Open regression cannot contain resolution evidence")
        if not isinstance(entry["reason"], str) or not entry["reason"].strip():
            raise ValueError("Regression reason is required")
        item = dict(entry)
        item["entryHash"] = sha256(_canonical(item)).hexdigest()
        normalized.append(item)
    if len({item["caseId"] for item in normalized}) != len(normalized):
        raise ValueError("Regression case IDs must be unique")
    open_baseline = [item["caseId"] for item in normalized
                     if item["status"] == "OPEN" and item["outcome"] == "BASELINE_BETTER"]
    vault = {"schema": QUALITY_REGRESSION_VAULT_SCHEMA,
             "version": QUALITY_REGRESSION_VAULT_VERSION, "locked": True,
             "entries": normalized, "openBaselineBetterCases": open_baseline,
             "releaseBlocked": bool(open_baseline)}
    vault["vaultHash"] = sha256(_canonical(vault)).hexdigest()
    validate_quality_regression_vault(vault)
    return vault


def validate_quality_regression_vault(vault: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "locked", "entries", "openBaselineBetterCases",
              "releaseBlocked", "vaultHash"}
    _require_keys(vault, fields, fields, "quality regression vault")
    if vault["schema"] != QUALITY_REGRESSION_VAULT_SCHEMA or vault["version"] != QUALITY_REGRESSION_VAULT_VERSION:
        raise ValueError("Unsupported quality regression vault")
    if vault["locked"] is not True:
        raise ValueError("Quality regression vault must remain locked")
    for entry in vault["entries"]:
        if entry.get("entryHash") != _hash_without(entry, "entryHash"):
            raise ValueError("Quality regression entry hash mismatch")
    expected = [item["caseId"] for item in vault["entries"]
                if item["status"] == "OPEN" and item["outcome"] == "BASELINE_BETTER"]
    if vault["openBaselineBetterCases"] != expected or vault["releaseBlocked"] != bool(expected):
        raise ValueError("Quality regression blocking summary mismatch")
    if vault["vaultHash"] != _hash_without(vault, "vaultHash"):
        raise ValueError("Quality regression vault hash mismatch")


def evaluate_music_quality(preview_session: Mapping[str, Any], song_map: Mapping[str, Any],
                           baseline_reference: Mapping[str, Any], blind_package: Mapping[str, Any],
                           private_key: Mapping[str, Any],
                           listening_responses: Sequence[Mapping[str, Any]],
                           regression_vault: Mapping[str, Any],
                           controls: QualityControls | Mapping[str, Any] | None = None) -> dict[str, Any]:
    controls = controls if isinstance(controls, QualityControls) else QualityControls.from_dict(controls)
    validate_preview_session_v2(preview_session)
    validate_baseline_reference(baseline_reference)
    validate_blind_listening_package(blind_package)
    validate_blind_listening_key(private_key, blind_package)
    validate_quality_regression_vault(regression_vault)
    if blind_package["previewSessionHash"] != preview_session["previewSessionHash"] \
            or blind_package["baselineReference"]["referenceHash"] != baseline_reference["referenceHash"]:
        raise ValueError("Blind package does not belong to evaluation inputs")
    automated = calculate_automated_metrics(preview_session, song_map, controls.premium_variant_id)
    listening = summarize_listening(blind_package, private_key, listening_responses)
    hard_failures = []
    variant = _variant(preview_session, controls.premium_variant_id)
    if variant["polyphony"]["globalPeak"] > 54:
        hard_failures.append({"code": "MIDI_NOTE_CEILING_EXCEEDED", "value": variant["polyphony"]["globalPeak"],
                              "limit": 54})
    harmony_rate = automated["metrics"]["harmony"]["facts"]["violationRate"]
    if harmony_rate > controls.maximum_harmony_violation_rate:
        hard_failures.append({"code": "HARD_HARMONY_VIOLATION", "value": harmony_rate,
                              "limit": controls.maximum_harmony_violation_rate})
    collision_rate = automated["metrics"]["registerCollision"]["facts"]["collisionRate"]
    if collision_rate > controls.maximum_register_collision_rate:
        hard_failures.append({"code": "HARD_REGISTER_COLLISION", "value": collision_rate,
                              "limit": controls.maximum_register_collision_rate})
    technical_passed = not hard_failures
    low_metrics = [name for name, item in automated["metrics"].items()
                   if item["score"] < controls.minimum_metric_score]
    automated_passed = technical_passed and not low_metrics \
        and automated["overallScore"] >= controls.minimum_automated_overall
    human_count_ok = listening["verifiedHumanEvaluatorCount"] >= controls.minimum_human_evaluators
    overall_median = listening["categoryMedians"]["overall"]
    human_score_ok = overall_median is not None and overall_median >= controls.minimum_human_overall_median
    preference = listening["premiumPreferenceRate"]
    preference_ok = preference is not None and preference >= controls.minimum_premium_preference_rate
    regression_ok = not regression_vault["releaseBlocked"]
    blockers = []
    if not technical_passed:
        blockers.append("TECHNICAL_HARD_FAIL")
    if not automated_passed:
        blockers.append("AUTOMATED_METRIC_GATE_FAILED")
    if not human_count_ok:
        blockers.append("TWO_INDEPENDENT_HUMAN_EVALUATORS_REQUIRED")
    if not human_score_ok:
        blockers.append("HUMAN_OVERALL_MEDIAN_BELOW_FOUR_OR_MISSING")
    if not preference_ok:
        blockers.append("PREMIUM_PREFERENCE_BELOW_SEVENTY_PERCENT_OR_MISSING")
    if not regression_ok:
        blockers.append("OPEN_BASELINE_BETTER_REGRESSION")
    release_passed = not blockers
    report = {
        "schema": EVALUATION_REPORT_SCHEMA, "version": EVALUATION_REPORT_VERSION,
        "source": {"previewSessionHash": preview_session["previewSessionHash"],
                   "midiSha256": preview_session["source"]["midiSha256"],
                   "validatorIdentityHash": preview_session["validatorIdentity"]["identityHash"],
                   "songMapHash": song_map.get("mapHash"),
                   "baselineReferenceHash": baseline_reference["referenceHash"],
                   "blindPackageHash": blind_package["packageHash"],
                   "regressionVaultHash": regression_vault["vaultHash"]},
        "controls": controls.to_manifest(),
        "technical": {"passed": technical_passed, "hardFailures": hard_failures,
                      "validatorPassed": preview_session["validatorIdentity"]["passed"],
                      "midiNotePeak": variant["polyphony"]["globalPeak"], "midiNoteCeiling": 54},
        "automated": {**automated, "passed": automated_passed, "lowMetrics": low_metrics},
        "listening": listening,
        "regression": {"openBaselineBetterCases": regression_vault["openBaselineBetterCases"],
                       "passed": regression_ok},
        "releaseQualityGate": {"passed": release_passed, "blockers": blockers,
                               "requiresRealHumanEvidence": True,
                               "proxyAudioCanSatisfyHumanGate": False},
        "status": "QUALITY_GATE_PASS" if release_passed else "QUALITY_GATE_BLOCKED",
        "readOnly": True, "midiMutationAllowed": False, "finalMidiGenerated": False,
        "pa800DeviceCertified": False,
    }
    report["evaluationReportHash"] = sha256(_canonical(report)).hexdigest()
    validate_evaluation_report_v2(report)
    return report


def validate_evaluation_report_v2(report: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "source", "controls", "technical", "automated", "listening",
              "regression", "releaseQualityGate", "status", "readOnly", "midiMutationAllowed",
              "finalMidiGenerated", "pa800DeviceCertified", "evaluationReportHash"}
    _require_keys(report, fields, fields, "evaluation report")
    if report["schema"] != EVALUATION_REPORT_SCHEMA or report["version"] != EVALUATION_REPORT_VERSION:
        raise ValueError("Unsupported EvaluationReport contract")
    if report["readOnly"] is not True or report["midiMutationAllowed"] is not False \
            or report["finalMidiGenerated"] is not False or report["pa800DeviceCertified"] is not False:
        raise ValueError("Quality evaluator cannot mutate MIDI, render final output or certify Pa800")
    if set(report["automated"]["metrics"]) != set(AUTOMATED_METRICS):
        raise ValueError("Evaluation report metric set is incomplete")
    if set(report["automated"]["categoryScores"]) != set(RATING_CATEGORIES):
        raise ValueError("Evaluation category score set is incomplete")
    if bool(report["releaseQualityGate"]["blockers"]) == report["releaseQualityGate"]["passed"]:
        raise ValueError("Release quality gate blocker summary is inconsistent")
    if report["evaluationReportHash"] != _hash_without(report, "evaluationReportHash"):
        raise ValueError("Evaluation report hash mismatch")


def build_listening_response(package: Mapping[str, Any], private_key: Mapping[str, Any],
                             evaluator_id: str, authority: str, choices: Sequence[str],
                             ratings: Sequence[Mapping[str, int]], *,
                             evidence_hash: str | None = None,
                             independence_attested: bool = False,
                             submitted_at: str | None = None) -> dict[str, Any]:
    validate_blind_listening_package(package)
    validate_blind_listening_key(private_key, package)
    if len(choices) != len(package["trials"]) or len(ratings) != len(package["trials"]):
        raise ValueError("Response choices and ratings must cover every trial")
    trials = []
    for trial, choice, score in zip(package["trials"], choices, ratings):
        clip_ids = [clip["clipId"] for clip in trial["clips"]]
        if choice == "BASELINE" or choice == "PREMIUM":
            mapping = next(item for item in private_key["mappings"] if item["trialId"] == trial["trialId"])
            choice = next(clip for clip, role in mapping["clipRoles"].items() if role == choice)
        elif choice != "TIE" and choice not in clip_ids:
            raise ValueError("Unknown listening choice")
        trials.append({"trialId": trial["trialId"], "preferredClipId": choice,
                       "ratings": dict(score), "comment": ""})
    response = {"schema": LISTENING_RESPONSE_SCHEMA, "version": LISTENING_RESPONSE_VERSION,
                "responseId": "response-" + sha256(_canonical([package["packageHash"], evaluator_id,
                                                                  choices, ratings])).hexdigest()[:20],
                "packageHash": package["packageHash"],
                "evaluator": {"evaluatorId": evaluator_id, "authority": authority,
                              "independenceAttested": independence_attested,
                              "evidenceHash": evidence_hash},
                "submittedAt": submitted_at or date.today().isoformat(), "trials": trials}
    response["responseHash"] = sha256(_canonical(response)).hexdigest()
    validate_listening_response(response, package)
    return response


def execute_quality_evaluator_api(payload: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Quality evaluator API payload must be an object")
    action = payload.get("action", "evaluate-preview")
    if action == "evaluate-preview":
        allowed = {"action", "previewSession", "songMap", "controls"}
        _require_keys(payload, allowed, {"previewSession", "songMap"}, "quality API payload")
        session = payload["previewSession"]
        manifests = {variant: render_preview_wav(session, variant)[1] for variant in ("A", "B", "C")}
        baseline = load_baseline_reference(root)
        package, key = build_blind_listening_package(session, manifests, baseline)
        vault = build_quality_regression_vault()
        report = evaluate_music_quality(session, payload["songMap"], baseline, package, key, [], vault,
                                        payload.get("controls"))
        return {"report": report, "blindPackage": package,
                "privateKeyExportedToGui": False,
                "message": "Automated evidence complete; real blind human responses are still required."}
    if action == "evaluate":
        allowed = {"action", "previewSession", "songMap", "baselineReference", "blindPackage",
                   "privateKey", "listeningResponses", "regressionVault", "controls"}
        _require_keys(payload, allowed, allowed, "quality evaluation payload")
        return evaluate_music_quality(payload["previewSession"], payload["songMap"],
                                      payload["baselineReference"], payload["blindPackage"],
                                      payload["privateKey"], payload["listeningResponses"],
                                      payload["regressionVault"], payload["controls"])
    raise ValueError("Quality evaluator action must be evaluate-preview or evaluate")


def execute_quality_evaluator_gui(payload: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    return execute_quality_evaluator_api(payload, root)