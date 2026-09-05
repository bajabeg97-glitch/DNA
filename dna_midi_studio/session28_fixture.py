"""Self-authored end-to-end Session 28 Premium Producer fixture."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from .music_quality import (
    build_blind_listening_package,
    build_quality_regression_vault,
    evaluate_music_quality,
    load_baseline_reference,
)
from .premium_expression import build_expression_plan
from .premium_preview import build_preview_session, render_preview_wav
from .premium_workflow import build_premium_workflow
from .session24_fixture import build_session24_chain


def build_session28_chain(root: str | Path):
    root = Path(root)
    midi, song_map, brief, graph, candidate, groove, expression_controls, evidence = \
        build_session24_chain(root)
    expression = build_expression_plan(midi, groove, song_map, root, expression_controls, evidence)
    verdict = {"passed": True, "finalMidiSha256": midi.digest(),
               "reportHash": sha256(b"session28-independent-validator").hexdigest(),
               "scope": "FINAL_MIDI"}
    preview_controls = {
        "version": "1.0", "profileId": "PA800_PROXY_V1", "variants": ["A", "B", "C"],
        "loopSectionId": "sec-001", "soloRoles": [], "mutedRoles": [],
        "targetRmsDbfs": -20.0, "sampleRate": 12000, "maxAudioSeconds": 4,
        "masterGainDb": 0.0,
        "externalAdapter": {"schema": "dna-premium-audio-render-adapter", "version": "1.0",
                            "mode": "DISABLED", "rendererId": "builtin-proxy",
                            "executableSha256": None, "soundfontSha256": None},
    }
    preview = build_preview_session(midi, song_map, groove, expression, [], verdict, preview_controls)
    manifests = {variant: render_preview_wav(preview, variant)[1] for variant in ("A", "B", "C")}
    baseline = load_baseline_reference(root)
    blind_package, private_key = build_blind_listening_package(preview, manifests, baseline, seed=2828)
    evaluation = evaluate_music_quality(preview, song_map, baseline, blind_package, private_key, [],
                                        build_quality_regression_vault())
    documents = {"songMap": song_map, "producerBrief": brief, "arrangementGraph": graph,
                 "candidateSet": candidate, "groovePlan": groove,
                 "expressionPlan": expression, "previewSession": preview,
                 "evaluationReport": evaluation}
    controls = {"version": "1.0", "selectedVariantId": "C", "seed": 2828,
                "globalLock": False, "elementLocks": ["v2cv1"],
                "palette": "HIGH_CONTRAST", "reducedMotion": False}
    workflow = build_premium_workflow(documents, controls)
    return {"midi": midi, "documents": documents, "workflowControls": controls,
            "workflow": workflow, "blindPackage": blind_package,
            "privateKey": private_key, "audioManifests": manifests,
            "baselineReference": baseline, "previewControls": preview_controls,
            "validatorVerdict": verdict}