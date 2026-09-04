"""Session 35 Song-to-Style end-to-end project coordinator."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

from .arrangement_renderer import validate_render_manifest_v2
from .global_coherence import validate_global_coherence_plan, verify_global_coherence
from .midi import MidiEvent, MidiFile, MidiTrack


PROJECT_SCHEMA = "dna-song-to-style-project"
PROJECT_VERSION = "1.0"
CHECKPOINT_SCHEMA = "dna-arranger-project-checkpoint"
CHECKPOINT_VERSION = "1.0"
STAGES = ("IMPORT", "ANALYZE", "BRIEF", "PLAN", "SELECT", "RENDER", "COHERE",
          "VERIFY", "PREVIEW", "PUBLISH")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _without(value: Mapping[str, Any], field: str) -> str:
    return _hash({key: item for key, item in value.items() if key != field})


@dataclass(frozen=True)
class ArrangerWorkflowError(ValueError):
    code: str
    message: str
    recovery_action: str
    stage: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message,
                "recoveryAction": self.recovery_action, "stage": self.stage,
                "failClosed": True}


ERROR_CATALOG = {
    "E2E_SOURCE_HASH": ("Source MIDI does not match the evidence chain", "REIMPORT_SOURCE", "IMPORT"),
    "E2E_RENDER_HASH": ("Rendered MIDI or manifest hash is invalid", "RERUN_RENDER", "RENDER"),
    "E2E_COHERENCE_HASH": ("Coherence plan or MIDI variants are invalid", "RERUN_COHERENCE", "COHERE"),
    "E2E_UNKNOWN_STAGE": ("Requested workflow stage does not exist", "OPEN_WORKFLOW_OVERVIEW", "VERIFY"),
    "E2E_LOCKED_FRAGMENT": ("Partial regeneration targets a locked marker", "UNLOCK_OR_KEEP", "SELECT"),
    "E2E_FRAGMENT_SCOPE": ("Partial regeneration has no authorized differing fragment", "CHOOSE_OTHER_VARIANT", "COHERE"),
    "E2E_CHECKPOINT_HASH": ("Checkpoint does not belong to this project", "LOAD_MATCHING_PROJECT", "VERIFY"),
    "E2E_ACTION": ("Project action is invalid", "REVIEW_ACTION", "PREVIEW"),
}


def _error(code: str) -> ArrangerWorkflowError:
    message, recovery, stage = ERROR_CATALOG[code]
    return ArrangerWorkflowError(code, message, recovery, stage)


def _document_hash(value: Mapping[str, Any]) -> str:
    for key in ("analysisHash", "mapHash", "briefHash", "graphHash", "candidateSetHash",
                "groovePlanHash", "ledgerHash", "trackPlanHash", "renderManifestHash",
                "coherencePlanHash"):
        if key in value:
            return str(value[key])
    return _hash(value)


def serialize_end_to_end_chain(chain: Mapping[str, Any]) -> dict[str, Any]:
    return {"trackPlan": chain["trackPlan"], "documents": chain["documents"],
            "renderManifest": chain["renderManifest"],
            "renderedMidiBase64": base64.b64encode(chain["renderedMidi"]).decode(),
            "coherencePlan": chain["coherencePlan"],
            "coherentVariantsBase64": {key: base64.b64encode(raw).decode()
                                       for key, raw in chain["coherentVariants"].items()},
            "sourceName": chain.get("sourceName", "song.mid")}


def _normalize_chain(chain: Mapping[str, Any]) -> dict[str, Any]:
    if "renderedMidi" in chain and "coherentVariants" in chain:
        return dict(chain)
    required = {"trackPlan", "documents", "renderManifest", "renderedMidiBase64",
                "coherencePlan", "coherentVariantsBase64", "sourceName"}
    if set(chain) != required:
        raise _error("E2E_ACTION")
    try:
        rendered = base64.b64decode(chain["renderedMidiBase64"], validate=True)
        variants = {key: base64.b64decode(raw, validate=True)
                    for key, raw in chain["coherentVariantsBase64"].items()}
    except Exception as exc:
        raise _error("E2E_ACTION") from exc
    return {"trackPlan": chain["trackPlan"], "documents": chain["documents"],
            "renderManifest": chain["renderManifest"], "renderedMidi": rendered,
            "coherencePlan": chain["coherencePlan"], "coherentVariants": variants,
            "sourceName": chain["sourceName"]}


def _stage(stage_id: str, artifact_type: str, artifact_hash: str, index: int) -> dict[str, Any]:
    row = {"stageId": stage_id, "index": index, "status": "COMPLETE",
           "progressPercent": 100, "artifactType": artifact_type,
           "artifactHash": artifact_hash, "invalidatedBy": None,
           "error": None, "resumable": True, "cancellable": stage_id != "PUBLISH"}
    row["stageHash"] = _hash(row)
    return row


def build_song_to_style_project(source_midi: bytes, chain: Mapping[str, Any],
                                controls: Mapping[str, Any] | None = None) -> dict[str, Any]:
    chain = _normalize_chain(chain)
    controls = dict(controls or {})
    allowed = {"selectedVariantId", "lockedMarkers", "projectSeed", "previewTier"}
    if set(controls) - allowed:
        raise _error("E2E_ACTION")
    selected = str(controls.get("selectedVariantId", "C"))
    if selected not in {"A", "B", "C"}:
        raise _error("E2E_ACTION")
    locks = sorted(set(str(item) for item in controls.get("lockedMarkers", [])))
    seed = int(controls.get("projectSeed", 3535))
    source_hash = sha256(source_midi).hexdigest()
    track_plan = chain["trackPlan"]
    if source_hash != track_plan["source"]["sourceMidiSha256"]:
        raise _error("E2E_SOURCE_HASH")
    rendered = chain["renderedMidi"]; manifest = chain["renderManifest"]
    try:
        validate_render_manifest_v2(manifest, rendered)
    except Exception as exc:
        raise _error("E2E_RENDER_HASH") from exc
    variants = chain["coherentVariants"]; coherence = chain["coherencePlan"]
    try:
        validate_global_coherence_plan(coherence, variants)
    except Exception as exc:
        raise _error("E2E_COHERENCE_HASH") from exc
    verification = verify_global_coherence(variants, coherence, rendered)
    if not verification["passed"]:
        raise _error("E2E_COHERENCE_HASH")
    documents = chain["documents"]
    artifacts = (
        ("sourceMidiSha256", source_hash),
        ("trackAnalysisHash", _document_hash(documents["trackAnalysis"])),
        ("producerBriefHash", _document_hash(documents["producerBrief"])),
        ("arrangementGraphHash", _document_hash(documents["arrangementGraph"])),
        ("candidateSetHash", _document_hash(documents["candidateSet"])),
        ("renderManifestHash", manifest["renderManifestHash"]),
        ("coherencePlanHash", coherence["coherencePlanHash"]),
        ("verificationHash", _hash(verification)),
        ("previewMidiSha256", sha256(variants[selected]).hexdigest()),
        ("previewPublicationHash", _hash([sha256(variants[selected]).hexdigest(), "PREVIEW_ONLY"])),
    )
    stages = [_stage(stage_id, kind, digest, index)
              for index, (stage_id, (kind, digest)) in enumerate(zip(STAGES, artifacts))]
    normalized_controls = {"selectedVariantId": selected, "lockedMarkers": locks,
                           "projectSeed": seed, "previewTier": "PREVIEW_ONLY"}
    project = {
        "schema": PROJECT_SCHEMA, "version": PROJECT_VERSION,
        "projectId": "project-" + _hash([source_hash, normalized_controls])[:20],
        "source": {"midiSha256": source_hash, "fileName": chain.get("sourceName", "song.mid"),
                   "originalOverwritten": False},
        "controls": normalized_controls, "stages": stages,
        "hashChain": {kind: digest for kind, digest in artifacts},
        "workflow": {"currentStage": "PUBLISH", "completedStages": list(STAGES),
                     "progressPercent": 100, "standardTaskWithoutTerminal": True,
                     "downstreamOnlyInvalidation": True, "batchIsolation": True},
        "history": {"entries": [], "cursor": 0, "undoAvailable": False,
                    "redoAvailable": False},
        "recovery": {"lastConfirmedStage": "PUBLISH",
                     "lastConfirmedStageHash": stages[-1]["stageHash"],
                     "checkpointAvailable": True},
        "publication": {"selectedMidiSha256": sha256(variants[selected]).hexdigest(),
                        "previewDownloadAllowed": True, "finalCertifiedExportAllowed": False,
                        "allowedProductName": "AI PREMIUM ARRANGER PREVIEW",
                        "humanListening": "PENDING_0_OF_2", "physicalPa800": "WAITING_FOR_DEVICE"},
        "errors": [{"code": code, "message": value[0], "recoveryAction": value[1],
                    "stage": value[2], "failClosed": True} for code, value in ERROR_CATALOG.items()],
        "safety": {"sourceMidiMutated": False, "sourceMidiOverwritten": False,
                   "goldAffectsDynamics": False, "approximateSoundBinding": False,
                   "validatorBypassAllowed": False, "deviceGateBypassAllowed": False,
                   "finalCertifiedMidiExportAllowed": False},
        "projectHash": "",
    }
    project["hashChain"]["chainHash"] = _hash(list(project["hashChain"].items()))
    project["projectHash"] = _without(project, "projectHash")
    validate_song_to_style_project(project)
    return project


def validate_song_to_style_project(project: Mapping[str, Any]) -> None:
    if project.get("schema") != PROJECT_SCHEMA or project.get("version") != PROJECT_VERSION:
        raise ValueError("Unsupported Song-to-Style project")
    if [row["stageId"] for row in project["stages"]] != list(STAGES):
        raise ValueError("Song-to-Style stage order mismatch")
    if any(row["stageHash"] != _hash({key: value for key, value in row.items() if key != "stageHash"})
           for row in project["stages"]):
        raise ValueError("Song-to-Style stage hash mismatch")
    if any(value is not None and key != "chainHash" and not _SHA.fullmatch(str(value))
           for key, value in project["hashChain"].items()):
        raise ValueError("Song-to-Style artifact hash is invalid")
    if project["hashChain"]["chainHash"] != _hash([(key, value) for key, value in project["hashChain"].items()
                                                     if key != "chainHash"]):
        raise ValueError("Song-to-Style hash chain mismatch")
    safety = project["safety"]
    if any(safety[key] for key in ("sourceMidiMutated", "sourceMidiOverwritten", "goldAffectsDynamics",
                                   "approximateSoundBinding", "validatorBypassAllowed",
                                   "deviceGateBypassAllowed", "finalCertifiedMidiExportAllowed")):
        raise ValueError("Song-to-Style safety boundary violated")
    if project["projectHash"] != _without(project, "projectHash"):
        raise ValueError("Song-to-Style project hash mismatch")


def _rehash_project(project: dict[str, Any]) -> dict[str, Any]:
    project["hashChain"]["chainHash"] = _hash([(key, value) for key, value in project["hashChain"].items()
                                                 if key != "chainHash"])
    project["projectHash"] = ""; project["projectHash"] = _without(project, "projectHash")
    validate_song_to_style_project(project)
    return project


def invalidate_downstream(project: Mapping[str, Any], changed_stage: str,
                          replacement_hash: str) -> dict[str, Any]:
    validate_song_to_style_project(project)
    if changed_stage not in STAGES or not _SHA.fullmatch(replacement_hash):
        raise _error("E2E_UNKNOWN_STAGE")
    result = deepcopy(project); index = STAGES.index(changed_stage)
    changed = result["stages"][index]
    changed["artifactHash"] = replacement_hash; changed["status"] = "COMPLETE"
    changed["invalidatedBy"] = None; changed["stageHash"] = _hash({k: v for k, v in changed.items() if k != "stageHash"})
    result["hashChain"][changed["artifactType"]] = replacement_hash
    invalidated = []
    for row in result["stages"][index + 1:]:
        invalidated.append(row["stageId"]); row["status"] = "INVALIDATED"
        row["progressPercent"] = 0; row["artifactHash"] = None; row["invalidatedBy"] = changed_stage
        row["stageHash"] = _hash({k: v for k, v in row.items() if k != "stageHash"})
        result["hashChain"][row["artifactType"]] = None
    result["workflow"].update({"currentStage": invalidated[0] if invalidated else changed_stage,
                               "completedStages": list(STAGES[:index + 1]),
                               "progressPercent": round(100 * (index + 1) / len(STAGES))})
    result["recovery"] = {"lastConfirmedStage": changed_stage,
                         "lastConfirmedStageHash": changed["stageHash"], "checkpointAvailable": True}
    result["lastInvalidation"] = {"changedStage": changed_stage, "invalidatedStages": invalidated,
                                  "upstreamStagesPreserved": list(STAGES[:index]),
                                  "invalidationHash": _hash([project["projectHash"], changed_stage,
                                                             replacement_hash, invalidated])}
    # Optional transient field is intentionally outside the persisted contract.
    result.pop("lastInvalidation")
    return _rehash_project(result)


def apply_project_action(project: Mapping[str, Any], action: Mapping[str, Any]) -> dict[str, Any]:
    validate_song_to_style_project(project)
    if set(action) != {"type", "value"} or action["type"] not in {"SELECT_VARIANT", "LOCK_MARKER", "UNLOCK_MARKER"}:
        raise _error("E2E_ACTION")
    result = deepcopy(project); before = deepcopy(result["controls"])
    if action["type"] == "SELECT_VARIANT":
        if action["value"] not in {"A", "B", "C"}: raise _error("E2E_ACTION")
        result["controls"]["selectedVariantId"] = action["value"]
    elif action["type"] == "LOCK_MARKER":
        result["controls"]["lockedMarkers"] = sorted(set(result["controls"]["lockedMarkers"] + [action["value"]]))
    else:
        result["controls"]["lockedMarkers"] = [x for x in result["controls"]["lockedMarkers"] if x != action["value"]]
    history = result["history"]; history["entries"] = history["entries"][:history["cursor"]]
    entry = {"action": dict(action), "before": before, "after": deepcopy(result["controls"])}
    entry["actionHash"] = _hash(entry); history["entries"].append(entry); history["cursor"] += 1
    history["undoAvailable"] = True; history["redoAvailable"] = False
    return _rehash_project(result)


def undo_project_action(project: Mapping[str, Any]) -> dict[str, Any]:
    validate_song_to_style_project(project); result = deepcopy(project); history = result["history"]
    if history["cursor"] <= 0: raise _error("E2E_ACTION")
    history["cursor"] -= 1; result["controls"] = deepcopy(history["entries"][history["cursor"]]["before"])
    history["undoAvailable"] = history["cursor"] > 0; history["redoAvailable"] = True
    return _rehash_project(result)


def redo_project_action(project: Mapping[str, Any]) -> dict[str, Any]:
    validate_song_to_style_project(project); result = deepcopy(project); history = result["history"]
    if history["cursor"] >= len(history["entries"]): raise _error("E2E_ACTION")
    result["controls"] = deepcopy(history["entries"][history["cursor"]]["after"]); history["cursor"] += 1
    history["undoAvailable"] = True; history["redoAvailable"] = history["cursor"] < len(history["entries"])
    return _rehash_project(result)


def build_project_checkpoint(project: Mapping[str, Any], resume_stage: str,
                             reason: str = "CRASH_RECOVERY") -> dict[str, Any]:
    validate_song_to_style_project(project)
    if resume_stage not in STAGES: raise _error("E2E_UNKNOWN_STAGE")
    index = STAGES.index(resume_stage); confirmed = project["stages"][max(0, index - 1)]
    checkpoint = {"schema": CHECKPOINT_SCHEMA, "version": CHECKPOINT_VERSION,
                  "checkpointId": "checkpoint-" + _hash([project["projectHash"], resume_stage, reason])[:20],
                  "projectHash": project["projectHash"], "sourceMidiSha256": project["source"]["midiSha256"],
                  "resumeStage": resume_stage, "reason": reason,
                  "completedStages": list(STAGES[:index]), "lastConfirmedStageHash": confirmed["stageHash"],
                  "controls": deepcopy(project["controls"]), "history": deepcopy(project["history"]),
                  "midiEmbedded": False, "checkpointHash": ""}
    checkpoint["checkpointHash"] = _without(checkpoint, "checkpointHash")
    return checkpoint


def resume_project(project: Mapping[str, Any], checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    validate_song_to_style_project(project)
    if checkpoint.get("checkpointHash") != _without(checkpoint, "checkpointHash") \
            or checkpoint.get("projectHash") != project["projectHash"] \
            or checkpoint.get("sourceMidiSha256") != project["source"]["midiSha256"]:
        raise _error("E2E_CHECKPOINT_HASH")
    return {"schema": "dna-arranger-project-resume", "version": "1.0", "status": "READY_TO_RESUME",
            "projectHash": project["projectHash"], "checkpointHash": checkpoint["checkpointHash"],
            "resumeStage": checkpoint["resumeStage"], "restoredControls": checkpoint["controls"],
            "lastConfirmedStageHash": checkpoint["lastConfirmedStageHash"],
            "resumeHash": _hash([project["projectHash"], checkpoint["checkpointHash"]])}


def partial_regenerate_fragment(project: Mapping[str, Any], variants: Mapping[str, bytes],
                                coherence_plan: Mapping[str, Any], render_manifest: Mapping[str, Any],
                                marker: str, role: str, source_variant: str = "B") -> dict[str, Any]:
    validate_song_to_style_project(project); validate_global_coherence_plan(coherence_plan, variants)
    if marker in project["controls"]["lockedMarkers"]: raise _error("E2E_LOCKED_FRAGMENT")
    if source_variant not in variants: raise _error("E2E_ACTION")
    selected = project["controls"]["selectedVariantId"]
    base, donor = MidiFile.from_bytes(variants[selected]), MidiFile.from_bytes(variants[source_variant])
    by_marker = {row["marker"]: int(row["tick"]) for row in render_manifest["markerSetup"]}
    setup = [{"marker": marker_name, "tick": tick}
             for marker_name, tick in sorted(by_marker.items(), key=lambda item: item[1])]
    position = next((i for i, row in enumerate(setup) if row["marker"] == marker), None)
    binding = next((row for row in render_manifest["channelBindings"] if row["role"] == role), None)
    if position is None or binding is None: raise _error("E2E_FRAGMENT_SCOPE")
    start = setup[position]["tick"]; end = setup[position + 1]["tick"] if position + 1 < len(setup) else render_manifest["midi"]["lengthTicks"]
    channel = binding["channelNumber"] - 1; donor_events = {(e.tick, e.order): e for e in donor.tracks[0].events}
    changed = 0; events = []
    for event in base.tracks[0].events:
        replacement = donor_events.get((event.tick, event.order))
        in_scope = start <= event.tick < end and event.channel == channel and (event.is_note_on or event.is_note_off)
        if in_scope and replacement and replacement.data != event.data:
            events.append(MidiEvent(event.tick, event.order, event.kind, event.status, replacement.data, event.meta_type)); changed += 1
        else: events.append(event)
    if not changed: raise _error("E2E_FRAGMENT_SCOPE")
    patched = MidiFile(0, base.ppq, [MidiTrack(events)]).to_bytes()
    outside_before = [(e.tick, e.order, e.status, e.data) for e in base.tracks[0].events
                      if not (start <= e.tick < end and e.channel == channel and (e.is_note_on or e.is_note_off))]
    outside_after = [(e.tick, e.order, e.status, e.data) for e in MidiFile.from_bytes(patched).tracks[0].events
                     if not (start <= e.tick < end and e.channel == channel and (e.is_note_on or e.is_note_off))]
    report = {"schema": "dna-partial-fragment-regeneration", "version": "1.0",
              "projectHash": project["projectHash"], "marker": marker, "role": role,
              "selectedVariant": selected, "donorVariant": source_variant,
              "changedMidiEvents": changed, "outsideFragmentUnchanged": outside_before == outside_after,
              "lockedFragmentsChanged": 0, "inputMidiSha256": sha256(variants[selected]).hexdigest(),
              "outputMidiSha256": sha256(patched).hexdigest(), "previewOnly": True,
              "finalCertifiedExportAllowed": False, "reportHash": ""}
    report["reportHash"] = _without(report, "reportHash")
    return {"midiBytes": patched, "report": report}


def execute_end_to_end_api(payload: Mapping[str, Any]) -> dict[str, Any]:
    action = payload.get("action", "build") if isinstance(payload, Mapping) else None
    try:
        if action == "build":
            if set(payload) != {"action", "sourceMidiBase64", "chain", "controls"}: raise _error("E2E_ACTION")
            source = base64.b64decode(payload["sourceMidiBase64"], validate=True)
            return build_song_to_style_project(source, payload["chain"], payload["controls"])
        if action == "invalidate":
            return invalidate_downstream(payload["project"], payload["stageId"], payload["replacementHash"])
        if action == "checkpoint":
            return build_project_checkpoint(payload["project"], payload["resumeStage"], payload.get("reason", "USER_CANCELLED"))
        if action == "resume": return resume_project(payload["project"], payload["checkpoint"])
        if action == "apply": return apply_project_action(payload["project"], payload["projectAction"])
        if action == "undo": return undo_project_action(payload["project"])
        if action == "redo": return redo_project_action(payload["project"])
        raise _error("E2E_ACTION")
    except ArrangerWorkflowError:
        raise
    except Exception as exc:
        raise _error("E2E_ACTION") from exc


def execute_end_to_end_gui(payload: Mapping[str, Any]) -> dict[str, Any]:
    return execute_end_to_end_api(payload)


def execute_end_to_end_batch(payloads: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for index, payload in enumerate(payloads):
        try: output.append({"index": index, "status": "PASS", "result": execute_end_to_end_api(payload)})
        except ArrangerWorkflowError as exc:
            output.append({"index": index, "status": "BLOCKED", "error": exc.payload()})
        except Exception:
            output.append({"index": index, "status": "BLOCKED", "error": _error("E2E_ACTION").payload()})
    return output