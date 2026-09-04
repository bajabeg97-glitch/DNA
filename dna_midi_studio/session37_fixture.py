"""Session 37 production-quality calibration fixture chain."""

from __future__ import annotations

import json
from pathlib import Path

from .quality_calibration import (
    build_expression_intake_template,
    build_listening_intake,
    build_locked_quality_corpus,
    build_quality_release_gate,
    calibrate_structural_quality,
    evaluate_locked_holdout,
)


def build_session37_chain(root: str | Path) -> dict:
    root = Path(root)
    corpus, truth = build_locked_quality_corpus(root)
    calibration = calibrate_structural_quality(corpus, truth)
    holdout = evaluate_locked_holdout(corpus, truth, calibration)
    expression = build_expression_intake_template()
    evaluation = json.loads((root / "artifacts/session27-evaluation-report.json").read_text(encoding="utf-8"))
    blind_package = json.loads((root / "artifacts/session27-blind-listening-package.json").read_text(encoding="utf-8"))
    reliability = json.loads((root / "artifacts/session36-reliability-report.json").read_text(encoding="utf-8"))
    project = json.loads((root / "artifacts/session35-song-to-style-project.json").read_text(encoding="utf-8"))
    listening = build_listening_intake(evaluation)
    gate = build_quality_release_gate(
        corpus, calibration, holdout, expression, listening, evaluation,
        reliability, project["projectHash"],
    )
    return {
        "corpus": corpus,
        "groundTruthVault": truth,
        "calibration": calibration,
        "holdout": holdout,
        "expressionIntake": expression,
        "listeningIntake": listening,
        "qualityGate": gate,
        "evaluationReport": evaluation,
        "blindPackage": blind_package,
        "reliabilityReport": reliability,
        "project": project,
    }