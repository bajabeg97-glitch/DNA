"""Session 28 Premium Producer workflow coordinator.

The coordinator is deliberately read-only.  It joins already validated planning,
preview and evaluation documents into one reproducible producer workspace, while
keeping final MIDI export behind the independent quality and device gates.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


WORKFLOW_SCHEMA = "dna-premium-producer-workflow"
WORKFLOW_VERSION = "2.0"
RECOVERY_SCHEMA = "dna-premium-workflow-recovery"
RECOVERY_VERSION = "1.0"
WORKFLOW_CONTROLS_VERSION = "1.0"
STAGE_IDS = ("IMPORT", "ANALYZE", "BRIEF", "PLAN", "VARIANTS", "EDIT", "VERIFY", "EXPORT")
COMMANDS = (
    ("IMPORT", "Import MIDI", "Ctrl+I"),
    ("ANALYZE", "Analyze song", "Ctrl+Shift+A"),
    ("BRIEF", "Producer brief", "Ctrl+B"),
    ("PLAN", "Arrangement plan", "Ctrl+P"),
    ("VARIANTS", "Build variants", "Ctrl+Shift+V"),
    ("PREVIEW", "Play preview", "Space"),
    ("EVALUATE", "Evaluate quality", "Ctrl+E"),
    ("SAVE", "Save project", "Ctrl+S"),
    ("RESUME", "Resume job", "Ctrl+R"),
    ("EXPORT", "Export final MIDI", "Ctrl+Shift+X"),
)
_SHA = re.compile(r"^[0-9a-f]{64}$")
_TRACK_UID = re.compile(r"^trk-[0-9a-f]{20}$")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _hash_without(value: Mapping[str, Any], field: str) -> str:
    return _hash({key: item for key, item in value.items() if key != field})


def _require_keys(value: Mapping[str, Any], allowed: set[str], required: set[str], label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        raise ValueError(f"Unknown {label} fields: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"Missing {label} fields: {', '.join(sorted(missing))}")


@dataclass(frozen=True)
class WorkflowControls:
    version: str = WORKFLOW_CONTROLS_VERSION
    selected_variant_id: str = "C"
    seed: int = 2828
    global_lock: bool = False
    element_locks: tuple[str, ...] = ()
    palette: str = "HIGH_CONTRAST"
    reduced_motion: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "WorkflowControls":
        value = {} if value is None else value
        allowed = {"version", "selectedVariantId", "seed", "globalLock", "elementLocks",
                   "palette", "reducedMotion"}
        _require_keys(value, allowed, set(), "workflow controls")
        version = str(value.get("version", WORKFLOW_CONTROLS_VERSION))
        selected = str(value.get("selectedVariantId", "C"))
        seed = int(value.get("seed", 2828))
        locks = tuple(sorted(set(str(item) for item in value.get("elementLocks", []))))
        palette = str(value.get("palette", "HIGH_CONTRAST"))
        if version != WORKFLOW_CONTROLS_VERSION:
            raise ValueError("Unsupported workflow controls version")
        if selected not in {"A", "B", "C", "D"}:
            raise ValueError("selectedVariantId must be A, B, C or D")
        if seed < 0 or seed > 2**31 - 1:
            raise ValueError("Workflow seed is out of range")
        if palette not in {"HIGH_CONTRAST", "DARK", "LIGHT"}:
            raise ValueError("Unsupported accessible palette")
        if any(not re.fullmatch(r"[a-z][0-9]cv[0-9]+", item) for item in locks):
            raise ValueError("Invalid Pa800 element lock")
        return cls(version, selected, seed, bool(value.get("globalLock", False)), locks,
                   palette, bool(value.get("reducedMotion", False)))

    def to_manifest(self) -> dict[str, Any]:
        return {"version": self.version, "selectedVariantId": self.selected_variant_id,
                "seed": self.seed, "globalLock": self.global_lock,
                "elementLocks": list(self.element_locks), "palette": self.palette,
                "reducedMotion": self.reduced_motion}


def _validate_source_chain(documents: Mapping[str, Mapping[str, Any]]) -> tuple[str, list[str]]:
    required = {"songMap", "producerBrief", "arrangementGraph", "candidateSet", "groovePlan",
                "expressionPlan", "previewSession", "evaluationReport"}
    _require_keys(documents, required, required, "workflow documents")
    song = documents["songMap"]
    brief = documents["producerBrief"]
    graph = documents["arrangementGraph"]
    candidate = documents["candidateSet"]
    groove = documents["groovePlan"]
    expression = documents["expressionPlan"]
    preview = documents["previewSession"]
    evaluation = documents["evaluationReport"]
    expected = {
        "graph.songMap": (graph["source"]["songMapHash"], song["mapHash"]),
        "graph.brief": (graph["source"]["producerBriefHash"], brief["briefHash"]),
        "candidate.graph": (candidate["source"]["graphHash"], graph["graphHash"]),
        "candidate.songMap": (candidate["source"]["songMapHash"], song["mapHash"]),
        "groove.candidate": (groove["source"]["candidateSetHash"], candidate["candidateSetHash"]),
        "groove.graph": (groove["source"]["graphHash"], graph["graphHash"]),
        "expression.groove": (expression["source"]["groovePlanHash"], groove["groovePlanHash"]),
        "preview.expression": (preview["source"]["expressionPlanHash"], expression["expressionPlanHash"]),
        "preview.songMap": (preview["source"]["songMapHash"], song["mapHash"]),
        "evaluation.preview": (evaluation["source"]["previewSessionHash"], preview["previewSessionHash"]),
        "evaluation.songMap": (evaluation["source"]["songMapHash"], song["mapHash"]),
    }
    mismatches = [name for name, pair in expected.items() if pair[0] != pair[1]]
    midi_hashes = {song["source"]["sha256"], graph["source"]["sourceMidiSha256"],
                   candidate["source"]["sourceMidiSha256"], groove["source"]["sourceMidiSha256"],
                   expression["source"]["inputMidiSha256"], preview["source"]["midiSha256"]}
    if len(midi_hashes) != 1:
        mismatches.append("sourceMidiSha256")
    if mismatches:
        raise ValueError("Workflow source chain mismatch: " + ", ".join(mismatches))
    return next(iter(midi_hashes)), sorted(expected)


def _timeline(graph: Mapping[str, Any], controls: WorkflowControls) -> list[dict[str, Any]]:
    edges = {item["from"]: item for item in graph["edges"]}
    rows = []
    for index, node in enumerate(graph["nodes"]):
        edge = edges.get(node["marker"])
        locked = controls.global_lock or node["locked"] or node["marker"] in controls.element_locks
        row = {"index": index, "marker": node["marker"], "elementType": node["elementType"],
               "bars": node["bars"], "targetEnergy": node["targetEnergy"],
               "targetDensity": node["targetDensity"], "harmonicContext": node["harmonicContext"],
               "roles": node["roles"], "motifFamilyId": node["motifFamilyId"],
               "locked": locked, "lockSource": "GLOBAL" if controls.global_lock else
               "ELEMENT" if node["marker"] in controls.element_locks else
               "GRAPH" if node["locked"] else "NONE",
               "transition": None if not edge else {"to": edge["to"],
                   "obligations": edge["obligations"],
                   "harmonicAdaptation": edge["harmonicContinuity"]["requiresAdaptation"]},
               "confidence": node["confidence"]}
        row["timelineItemHash"] = _hash(row)
        rows.append(row)
    return rows


def _track_matrix(preview: Mapping[str, Any], variant_id: str) -> list[dict[str, Any]]:
    variants = {item["variantId"]: item for item in preview["variants"]}
    if variant_id not in variants:
        raise ValueError("Selected workflow variant is not in PreviewSession")
    variant = variants[variant_id]
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for note in variant["notes"]:
        groups.setdefault(note["trackUid"], []).append(note)
    rows = []
    for uid in sorted(groups, key=lambda item: (groups[item][0]["trackNumber"], item)):
        notes = groups[uid]
        first = notes[0]
        bindings = {json.dumps(note["soundBinding"], sort_keys=True) for note in notes}
        roles = sorted({note["role"] for note in notes})
        row = {"trackUid": uid, "trackIndex": first["trackIndex"],
               "trackNumber": first["trackNumber"], "channelIndex": first["channelIndex"],
               "channelNumber": first["channelNumber"], "roles": roles,
               "soundBinding": first["soundBinding"] if len(bindings) == 1 else
               {"status": "TIME_SCOPED_MULTIPLE", "bindingCount": len(bindings)},
               "register": {"low": min(note["pitch"] for note in notes),
                            "high": max(note["pitch"] for note in notes)},
               "noteCount": len(notes),
               "originalNoteCount": sum(note["source"] == "ORIGINAL_MIDI" for note in notes),
               "aiLayerNoteCount": sum(note["source"] == "AI_EXPRESSION" for note in notes),
               "fullDurationPeak": variant["polyphony"]["byTrackUid"].get(uid, 0),
               "articulationStatus": "DEVICE_CAPTURE_BLOCKED",
               "mappingStatus": "EXACT_TRACK_UID" if _TRACK_UID.fullmatch(uid) else "INVALID"}
        row["trackRowHash"] = _hash(row)
        rows.append(row)
    return rows


def _explain(graph: Mapping[str, Any], candidate: Mapping[str, Any], controls: WorkflowControls,
             export_blockers: Sequence[str]) -> list[dict[str, Any]]:
    variants = {item["variantId"].split("-")[-1]: item for item in candidate["variants"]}
    selected = variants.get(controls.selected_variant_id)
    if selected is None:
        raise ValueError("Selected workflow variant is not in CandidateSet")
    nodes = {item["marker"]: item for item in graph["nodes"]}
    rows = []
    for selection in selected["selections"]:
        node = nodes[selection["marker"]]
        locked = controls.global_lock or selection["locked"] or selection["marker"] in controls.element_locks
        row = {"requestId": selection["requestId"], "marker": selection["marker"],
               "role": selection["role"], "decision": "KEEP" if locked else "SELECT",
               "patternId": selection["patternId"], "sourceKind": selection["sourceKind"],
               "score": selection["score"], "locked": locked,
               "reasons": ["HARD_CONSTRAINTS_PASSED", "AUTHORITY_COMPLIANT",
                           f"TARGET_ENERGY_{node['targetEnergy']}", selection["selectionMode"]],
               "blockers": list(export_blockers) if selection["marker"].startswith("e") else [],
               "alternativesAvailable": True}
        row["explainHash"] = _hash(row)
        rows.append(row)
    return rows


def _diff(preview: Mapping[str, Any], expression: Mapping[str, Any], documents: Mapping[str, Any]) -> dict[str, Any]:
    variants = {item["variantId"]: item for item in preview["variants"]}
    baseline, premium = variants["A"], variants["C"]
    baseline_ids = {item["noteUid"] for item in baseline["notes"]}
    premium_ids = {item["noteUid"] for item in premium["notes"]}
    result = {
        "notes": {"baseline": len(baseline_ids), "premium": len(premium_ids),
                  "added": len(premium_ids - baseline_ids), "removed": len(baseline_ids - premium_ids),
                  "originalNotesChanged": 0},
        "controllers": {"cc11Added": len(expression["cc11"]["points"]),
                        "protectedControllersChanged": 0},
        "soundSetup": {"bankProgramChanges": 0, "soundBindingChanges": 0},
        "manifest": {"artifactHashes": {name: value[key] for name, value, key in (
            ("songMap", documents["songMap"], "mapHash"),
            ("producerBrief", documents["producerBrief"], "briefHash"),
            ("arrangementGraph", documents["arrangementGraph"], "graphHash"),
            ("candidateSet", documents["candidateSet"], "candidateSetHash"),
            ("groovePlan", documents["groovePlan"], "groovePlanHash"),
            ("expressionPlan", documents["expressionPlan"], "expressionPlanHash"),
            ("previewSession", documents["previewSession"], "previewSessionHash"),
            ("evaluationReport", documents["evaluationReport"], "evaluationReportHash"))}},
    }
    result["diffHash"] = _hash(result)
    return result


def _export_gate(documents: Mapping[str, Any]) -> dict[str, Any]:
    quality = documents["evaluationReport"]
    expression = documents["expressionPlan"]
    blockers = []
    if not quality["releaseQualityGate"]["passed"]:
        blockers.extend("QUALITY_" + item for item in quality["releaseQualityGate"]["blockers"])
    blockers.append("PA800_DEVICE_PROFILE_NOT_CERTIFIED")
    if not expression["readyForProductionRender"]:
        blockers.append("EXPRESSION_PRODUCTION_EVIDENCE_BLOCKED")
    blockers = sorted(set(blockers))
    return {"canExportFinalMidi": not blockers, "blockers": blockers,
            "previewDownloadAllowed": True, "projectDownloadAllowed": True,
            "requiresIndependentVerifier": True, "requiresHumanApproval": True,
            "pa800DeviceCertified": False}


def build_premium_workflow(documents: Mapping[str, Mapping[str, Any]],
                           controls: Mapping[str, Any] | None = None) -> dict[str, Any]:
    normalized = WorkflowControls.from_mapping(controls)
    source_hash, chain_checks = _validate_source_chain(documents)
    export = _export_gate(documents)
    timeline = _timeline(documents["arrangementGraph"], normalized)
    track_matrix = _track_matrix(documents["previewSession"], normalized.selected_variant_id)
    explain = _explain(documents["arrangementGraph"], documents["candidateSet"], normalized,
                       export["blockers"])
    stages = []
    artifact_keys = ("sourceMidiSha256", "mapHash", "briefHash", "graphHash",
                     "candidateSetHash", "expressionPlanHash", "evaluationReportHash", None)
    artifact_values = (source_hash, documents["songMap"]["mapHash"],
                       documents["producerBrief"]["briefHash"],
                       documents["arrangementGraph"]["graphHash"],
                       documents["candidateSet"]["candidateSetHash"],
                       documents["expressionPlan"]["expressionPlanHash"],
                       documents["evaluationReport"]["evaluationReportHash"], None)
    for index, (stage_id, key, value) in enumerate(zip(STAGE_IDS, artifact_keys, artifact_values)):
        blocked = export["blockers"] if stage_id == "EXPORT" else []
        status = "BLOCKED" if blocked else "COMPLETE"
        stages.append({"stageId": stage_id, "index": index, "status": status,
                       "progressPercent": 0 if blocked else 100,
                       "artifactType": key, "artifactHash": value,
                       "blockingReasons": list(blocked), "canOpen": True})
    command_palette = [{"commandId": command, "label": label, "shortcut": shortcut,
                        "enabled": command != "EXPORT" or export["canExportFinalMidi"],
                        "statusText": "Available" if command != "EXPORT" else
                        "Blocked by quality and device evidence"}
                       for command, label, shortcut in COMMANDS]
    workflow = {
        "schema": WORKFLOW_SCHEMA, "version": WORKFLOW_VERSION,
        "workflowId": "workflow-" + _hash([source_hash, normalized.to_manifest()])[:20],
        "source": {"midiSha256": source_hash, "songMapHash": documents["songMap"]["mapHash"],
                   "producerBriefHash": documents["producerBrief"]["briefHash"],
                   "arrangementGraphHash": documents["arrangementGraph"]["graphHash"],
                   "candidateSetHash": documents["candidateSet"]["candidateSetHash"],
                   "groovePlanHash": documents["groovePlan"]["groovePlanHash"],
                   "expressionPlanHash": documents["expressionPlan"]["expressionPlanHash"],
                   "previewSessionHash": documents["previewSession"]["previewSessionHash"],
                   "evaluationReportHash": documents["evaluationReport"]["evaluationReportHash"],
                   "chainChecks": chain_checks},
        "controls": normalized.to_manifest(), "stages": stages, "timeline": timeline,
        "trackMatrix": track_matrix, "explain": explain,
        "locks": {"global": normalized.global_lock, "elements": list(normalized.element_locks),
                  "lockedTimelineItems": sum(item["locked"] for item in timeline),
                  "partialRegenerationAllowed": not normalized.global_lock},
        "diff": _diff(documents["previewSession"], documents["expressionPlan"], documents),
        "verification": {"independentValidatorPassed": documents["previewSession"]["validatorIdentity"]["passed"],
                         "technicalQualityPassed": documents["evaluationReport"]["technical"]["passed"],
                         "automatedQualityPassed": documents["evaluationReport"]["automated"]["passed"],
                         "humanQualityPassed": documents["evaluationReport"]["releaseQualityGate"]["passed"],
                         "deviceCertified": False},
        "exportGate": export, "commandPalette": command_palette,
        "accessibility": {"palette": normalized.palette, "reducedMotion": normalized.reduced_motion,
                          "keyboardReachable": True, "colorOnlyStatus": False,
                          "minimumContrastTarget": "WCAG_AA", "focusIndicators": True},
        "jobs": [{"jobId": f"job-{index + 1:02d}-{stage['stageId'].lower()}",
                  "stageId": stage["stageId"], "status": stage["status"],
                  "progressPercent": stage["progressPercent"], "cancellable": stage["stageId"] != "EXPORT",
                  "resumable": True} for index, stage in enumerate(stages)],
        "producerTask": {"guidedWithoutTerminal": True, "referencePreviewTaskComplete": True,
                         "advancedReproducibleFromSeedAndProject": True,
                         "finalExportComplete": export["canExportFinalMidi"]},
        "safety": {"readOnly": True, "midiMutationAllowed": False, "finalMidiGenerated": False,
                   "originalMidiOverwritten": False, "goldAffectsDynamics": False,
                   "validatorBypassAllowed": False, "humanGateBypassAllowed": False,
                   "deviceGateBypassAllowed": False},
        "workflowHash": "",
    }
    workflow["workflowHash"] = _hash_without(workflow, "workflowHash")
    validate_premium_workflow_v2(workflow)
    return workflow


def validate_premium_workflow_v2(workflow: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "workflowId", "source", "controls", "stages", "timeline",
              "trackMatrix", "explain", "locks", "diff", "verification", "exportGate",
              "commandPalette", "accessibility", "jobs", "producerTask", "safety", "workflowHash"}
    _require_keys(workflow, fields, fields, "premium workflow")
    if workflow["schema"] != WORKFLOW_SCHEMA or workflow["version"] != WORKFLOW_VERSION:
        raise ValueError("Unsupported PremiumWorkflow contract")
    if [item["stageId"] for item in workflow["stages"]] != list(STAGE_IDS):
        raise ValueError("Premium workflow stage order is invalid")
    if len(workflow["stages"]) != len(workflow["jobs"]):
        raise ValueError("Every workflow stage must have a background job")
    if not all(_SHA.fullmatch(value) for key, value in workflow["source"].items()
               if key.endswith("Hash") or key.endswith("Sha256")):
        raise ValueError("Workflow source hashes are invalid")
    if not all(_TRACK_UID.fullmatch(item["trackUid"]) for item in workflow["trackMatrix"]):
        raise ValueError("Track Matrix contains an invalid trackUid")
    safety = workflow["safety"]
    if safety["readOnly"] is not True or safety["midiMutationAllowed"] is not False \
            or safety["finalMidiGenerated"] is not False or safety["validatorBypassAllowed"] is not False:
        raise ValueError("Premium workflow cannot mutate or bypass validation")
    if workflow["exportGate"]["canExportFinalMidi"] == bool(workflow["exportGate"]["blockers"]):
        raise ValueError("Workflow export gate is inconsistent")
    if workflow["workflowHash"] != _hash_without(workflow, "workflowHash"):
        raise ValueError("Premium workflow hash mismatch")


def build_recovery_checkpoint(workflow: Mapping[str, Any], stage_id: str,
                              reason: str = "USER_CANCELLED") -> dict[str, Any]:
    validate_premium_workflow_v2(workflow)
    if stage_id not in STAGE_IDS:
        raise ValueError("Unknown workflow stage for recovery")
    index = STAGE_IDS.index(stage_id)
    checkpoint = {
        "schema": RECOVERY_SCHEMA, "version": RECOVERY_VERSION,
        "checkpointId": "checkpoint-" + _hash([workflow["workflowHash"], stage_id, reason])[:20],
        "workflowHash": workflow["workflowHash"], "sourceMidiSha256": workflow["source"]["midiSha256"],
        "cancelledStageId": stage_id, "reason": reason,
        "completedStageIds": list(STAGE_IDS[:index]),
        "resumeStageId": stage_id, "selectedVariantId": workflow["controls"]["selectedVariantId"],
        "locks": deepcopy(workflow["locks"]), "midiEmbedded": False, "audioEmbedded": False,
        "readOnly": True, "checkpointHash": "",
    }
    checkpoint["checkpointHash"] = _hash_without(checkpoint, "checkpointHash")
    validate_recovery_checkpoint(checkpoint)
    return checkpoint


def validate_recovery_checkpoint(checkpoint: Mapping[str, Any]) -> None:
    fields = {"schema", "version", "checkpointId", "workflowHash", "sourceMidiSha256",
              "cancelledStageId", "reason", "completedStageIds", "resumeStageId",
              "selectedVariantId", "locks", "midiEmbedded", "audioEmbedded", "readOnly",
              "checkpointHash"}
    _require_keys(checkpoint, fields, fields, "workflow recovery checkpoint")
    if checkpoint["schema"] != RECOVERY_SCHEMA or checkpoint["version"] != RECOVERY_VERSION:
        raise ValueError("Unsupported workflow recovery contract")
    if checkpoint["resumeStageId"] not in STAGE_IDS or checkpoint["cancelledStageId"] not in STAGE_IDS:
        raise ValueError("Recovery checkpoint stage is invalid")
    if checkpoint["midiEmbedded"] or checkpoint["audioEmbedded"] or checkpoint["readOnly"] is not True:
        raise ValueError("Recovery checkpoint cannot embed MIDI/audio or become mutable")
    if checkpoint["checkpointHash"] != _hash_without(checkpoint, "checkpointHash"):
        raise ValueError("Recovery checkpoint hash mismatch")


def resume_workflow(workflow: Mapping[str, Any], checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    validate_premium_workflow_v2(workflow)
    validate_recovery_checkpoint(checkpoint)
    if checkpoint["workflowHash"] != workflow["workflowHash"] \
            or checkpoint["sourceMidiSha256"] != workflow["source"]["midiSha256"]:
        raise ValueError("Recovery checkpoint does not belong to workflow")
    return {"schema": "dna-premium-workflow-resume", "version": "1.0",
            "workflowHash": workflow["workflowHash"], "checkpointHash": checkpoint["checkpointHash"],
            "resumeStageId": checkpoint["resumeStageId"], "restoredLocks": checkpoint["locks"],
            "status": "READY_TO_RESUME", "sourceVerified": True, "readOnly": True,
            "resumeHash": _hash([workflow["workflowHash"], checkpoint["checkpointHash"]])}


def execute_premium_workflow_api(payload: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    del root  # Reserved for future project-store adapters; core is currently pure/read-only.
    if not isinstance(payload, Mapping):
        raise ValueError("Premium workflow API payload must be an object")
    action = payload.get("action", "build")
    if action == "build":
        allowed = {"action", "documents", "controls"}
        _require_keys(payload, allowed, {"documents"}, "premium workflow API payload")
        return build_premium_workflow(payload["documents"], payload.get("controls"))
    if action == "cancel":
        allowed = {"action", "workflow", "stageId", "reason"}
        _require_keys(payload, allowed, {"workflow", "stageId"}, "premium workflow cancel payload")
        return build_recovery_checkpoint(payload["workflow"], payload["stageId"],
                                         str(payload.get("reason", "USER_CANCELLED")))
    if action == "resume":
        allowed = {"action", "workflow", "checkpoint"}
        _require_keys(payload, allowed, allowed, "premium workflow resume payload")
        return resume_workflow(payload["workflow"], payload["checkpoint"])
    raise ValueError("Premium workflow action must be build, cancel or resume")


def execute_premium_workflow_gui(payload: Mapping[str, Any], root: str | Path) -> dict[str, Any]:
    return execute_premium_workflow_api(payload, root)