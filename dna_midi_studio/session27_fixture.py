"""Self-authored Session 27 metric, blind-listening and regression fixtures."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from .music_quality import (
    build_blind_listening_package,
    build_listening_response,
    build_quality_regression_vault,
    evaluate_music_quality,
    load_baseline_reference,
)
from .premium_preview import render_preview_wav
from .session26_fixture import build_session26_chain


def _ratings(value: int = 4):
    return [{"drum": value, "bass": value, "guitar": value,
             "accompaniment": value, "solo": value,
             "transition": value, "overall": value} for _ in range(2)]


def build_session27_chain(root: str | Path):
    root = Path(root)
    session26 = build_session26_chain()
    midi, song_map, preview_session = session26[0], session26[1], session26[7]
    wavs = {}
    manifests = {}
    for variant in ("A", "B", "C"):
        wavs[variant], manifests[variant] = render_preview_wav(preview_session, variant)
    baseline = load_baseline_reference(root)
    blind_package, private_key = build_blind_listening_package(
        preview_session, manifests, baseline, seed=2727
    )
    software_response = build_listening_response(
        blind_package, private_key, "session27-fixture", "SOFTWARE_TEST_ONLY",
        ["PREMIUM", "PREMIUM"], _ratings(4), independence_attested=False,
    )
    resolved_entry = {
        "caseId": "resolved-baseline-example",
        "baselineAudioSha256": manifests["A"]["wavSha256"],
        "premiumAudioSha256": manifests["C"]["wavSha256"],
        "outcome": "BASELINE_BETTER", "status": "RESOLVED",
        "evaluatorEvidenceHash": sha256(b"session27-regression-evaluator").hexdigest(),
        "resolutionEvidenceHash": sha256(b"session27-regression-resolution").hexdigest(),
        "reason": "Self-authored fixture proving a formerly worse result remains recorded after resolution.",
    }
    regression_vault = build_quality_regression_vault([resolved_entry])
    controls = {
        "version": "1.0", "premiumVariantId": "C", "minimumMetricScore": 2.5,
        "minimumAutomatedOverall": 3.5, "minimumHumanOverallMedian": 4.0,
        "minimumPremiumPreferenceRate": 0.70, "minimumHumanEvaluators": 2,
        "maximumHarmonyViolationRate": 0.45, "maximumRegisterCollisionRate": 0.12,
    }
    report = evaluate_music_quality(preview_session, song_map, baseline, blind_package,
                                    private_key, [software_response], regression_vault, controls)
    open_entry = dict(resolved_entry)
    open_entry.update({"caseId": "open-baseline-better-example", "status": "OPEN",
                       "resolutionEvidenceHash": None,
                       "reason": "Deliberate blocker fixture: baseline sounded better."})
    blocking_vault = build_quality_regression_vault([open_entry])
    return {
        "midi": midi, "songMap": song_map, "previewSession": preview_session,
        "wavs": wavs, "audioManifests": manifests, "baselineReference": baseline,
        "blindPackage": blind_package, "privateKey": private_key,
        "softwareResponse": software_response, "regressionVault": regression_vault,
        "blockingRegressionVault": blocking_vault, "controls": controls,
        "evaluationReport": report,
    }