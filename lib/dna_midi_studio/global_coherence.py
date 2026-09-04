"""Evidence-preserving Session 34 global coherence renderer."""

from __future__ import annotations

import base64
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .arrangement_renderer import MIDI_NOTE_CEILING, validate_render_manifest_v2
from .midi import MidiEvent, MidiFile, MidiTrack, Note


COHERENCE_SCHEMA = "dna-global-coherence-plan"
COHERENCE_VERSION = "2.0"
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ROOTS = {"C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4,
          "F": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8,
          "A": 9, "A#": 10, "BB": 10, "B": 11}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _without(value: Mapping[str, Any], field: str) -> str:
    return _hash({key: item for key, item in value.items() if key != field})


def _root(chord: str) -> int:
    match = re.match(r"^([A-Ga-g])([#bB]?)", chord)
    if not match:
        raise ValueError("Unsupported coherence chord")
    return _ROOTS[match.group(1).upper() + match.group(2).upper()]


def _spans(manifest: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
    rows = sorted(manifest["markerSetup"], key=lambda item: int(item["tick"]))
    length = int(manifest["midi"]["lengthTicks"])
    return {row["marker"]: (int(row["tick"]), int(rows[index + 1]["tick"])
            if index + 1 < len(rows) else length) for index, row in enumerate(rows)}


def _channels(manifest: Mapping[str, Any]) -> dict[str, int]:
    return {row["role"]: int(row["channelNumber"]) - 1 for row in manifest["channelBindings"]}


def _notes(notes: Sequence[Note], marker: str, role: str, spans, channels) -> list[Note]:
    if role not in channels:
        return []
    start, end = spans[marker]
    return sorted((note for note in notes if note.channel == channels[role]
                   and start <= note.start < end), key=lambda note: (note.start, note.pitch, note.end))


def _bounds(manifest: Mapping[str, Any], marker: str, role: str) -> tuple[int, int]:
    row = next(item for item in manifest["fragments"]
               if item["marker"] == marker and item["role"] == role)
    return tuple(map(int, row["authorizedRegister"]))


def _nearest(pc: int, low: int, high: int, reference: int) -> int:
    candidates = [pitch for pitch in range(low, high + 1) if pitch % 12 == pc]
    if not candidates:
        raise ValueError("Pitch class is outside authorized register")
    return min(candidates, key=lambda pitch: (abs(pitch - reference), pitch))


def _safe(all_notes: Sequence[Note], target: Note, pitch: int, operations) -> bool:
    changed = {(item["onOrder"], item["offOrder"]): item["afterPitch"] for item in operations}
    for note in all_notes:
        if note.channel != target.channel or (note.on_order, note.off_order) == (target.on_order, target.off_order):
            continue
        actual = changed.get((note.on_order, note.off_order), note.pitch)
        if actual == pitch and note.start < target.end and target.start < note.end:
            return False
    return True


def _add(operations, variant, kind, marker, role, note, pitch, reason, evidence, all_notes):
    if pitch == note.pitch or not _safe(all_notes, note, pitch, operations):
        return
    if any(item["onOrder"] == note.on_order for item in operations):
        return
    row = {"variantId": variant, "kind": kind, "marker": marker, "role": role,
           "channelNumber": note.channel + 1, "startTick": note.start, "endTick": note.end,
           "velocity": note.velocity, "beforePitch": note.pitch, "afterPitch": pitch,
           "onOrder": note.on_order, "offOrder": note.off_order, "reason": reason,
           "evidenceHashes": list(evidence)}
    row["operationId"] = "coh-" + _hash(row)[:24]
    operations.append(row)


def _operations(variant: str, notes: Sequence[Note], manifest, graph) -> list[dict[str, Any]]:
    spans, channels = _spans(manifest), _channels(manifest)
    nodes = {row["marker"]: row for row in graph["nodes"]}
    evidence = [graph["graphHash"], manifest["renderManifestHash"]]
    output = []
    for fill in ("f1cv1", "f2cv1"):
        edge = next(row for row in graph["edges"] if row["from"] == fill)
        note = _notes(notes, fill, "bass", spans, channels)[-1]
        low, high = _bounds(manifest, fill, "bass")
        root = _root(edge["harmonicContinuity"]["toChord"])
        pitch = _nearest((root - 1) % 12, low, high, note.pitch)
        if pitch == note.pitch:
            pitch = _nearest((root + 1) % 12, low, high, note.pitch)
        _add(output, variant, "FILL_BASS_APPROACH", fill, "bass", note, pitch,
             "Existing bass note approaches target harmony", evidence, notes)
    if variant in {"B", "C"}:
        for ending in ("e1cv1", "e2cv1"):
            root = _root(nodes[ending]["harmonicContext"][-1])
            for role in ("guitar", "riff", "pad"):
                selected = _notes(notes, ending, role, spans, channels)
                if not selected:
                    continue
                note = selected[-1]; low, high = _bounds(manifest, ending, role)
                choices = [_nearest(pc, low, high, note.pitch) for pc in (root, (root + 4) % 12, (root + 7) % 12)]
                _add(output, variant, "ENDING_RESOLUTION", ending, role, note,
                     min(choices, key=lambda pitch: (abs(pitch - note.pitch), pitch)),
                     "Existing ending voice resolves inside final chord", evidence, notes)
        seen = set()
        for edge in graph["edges"]:
            for role in ("bass", "guitar", "riff", "pad"):
                target = (edge["to"], role)
                if target in seen:
                    continue
                left = _notes(notes, edge["from"], role, spans, channels)
                right = _notes(notes, edge["to"], role, spans, channels)
                if left and right:
                    note = right[0]; low, high = _bounds(manifest, edge["to"], role)
                    pitch = _nearest(note.pitch % 12, low, high, left[-1].pitch)
                    if abs(pitch - left[-1].pitch) < abs(note.pitch - left[-1].pitch):
                        _add(output, variant, "VOICE_LEADING", edge["to"], role, note, pitch,
                             "Reduce marker-boundary leap while preserving pitch class",
                             [edge["id"], *evidence], notes)
                seen.add(target)
    if variant == "C":
        role = next((name for name in ("riff", "guitar", "pad")
                     if len(_notes(notes, "i1cv1", name, spans, channels)) >= 2), None)
        if role:
            intro = _notes(notes, "i1cv1", role, spans, channels)
            interval = max(-7, min(7, intro[1].pitch - intro[0].pitch))
            for marker in ("v1cv1", "v2cv1", "v3cv1", "v4cv1", "f1cv1", "f2cv1", "e1cv1", "e2cv1"):
                selected = _notes(notes, marker, role, spans, channels)
                if len(selected) < 2:
                    continue
                note = selected[1]; low, high = _bounds(manifest, marker, role)
                root = _root(nodes[marker]["harmonicContext"][0]); desired = selected[0].pitch + interval
                choices = [_nearest(pc, low, high, desired) for pc in (root, (root + 4) % 12, (root + 7) % 12)]
                _add(output, variant, "MOTIF_CONTOUR", marker, role, note,
                     min(choices, key=lambda pitch: (abs(pitch - desired), pitch)),
                     "Reuse Intro contour inside target harmony", evidence, notes)
    return sorted(output, key=lambda item: (item["startTick"], item["channelNumber"], item["onOrder"]))


def _apply(raw: bytes, operations) -> bytes:
    midi = MidiFile.from_bytes(raw); replacement = {}
    for row in operations:
        replacement[row["onOrder"]] = row["afterPitch"]
        replacement[row["offOrder"]] = row["afterPitch"]
    events = []
    for event in midi.tracks[0].events:
        if event.order in replacement and (event.is_note_on or event.is_note_off):
            events.append(MidiEvent(event.tick, event.order, event.kind, event.status,
                                    bytes((replacement[event.order], event.data[1])), event.meta_type))
        else:
            events.append(event)
    return MidiFile(0, midi.ppq, [MidiTrack(events)]).to_bytes()


def _peak(notes: Sequence[Note]) -> int:
    active = peak = 0; timeline = []
    for note in notes:
        timeline.extend(((note.start, 1), (note.end, -1)))
    for _, delta in sorted(timeline, key=lambda item: (item[0], item[1])):
        active += delta; peak = max(peak, active)
    return peak


def _metrics(raw: bytes, baseline: bytes, operations, manifest, graph) -> dict[str, Any]:
    from . import pa800_validator
    midi, original = MidiFile.from_bytes(raw), MidiFile.from_bytes(baseline)
    notes, baseline_notes = midi.notes(), original.notes(); spans, channels = _spans(manifest), _channels(manifest)
    nodes = {row["marker"]: row for row in graph["nodes"]}
    fills = []
    for marker in ("f1cv1", "f2cv1"):
        edge = next(row for row in graph["edges"] if row["from"] == marker)
        note = _notes(notes, marker, "bass", spans, channels)[-1]; root = _root(edge["harmonicContinuity"]["toChord"])
        fills.append({"marker": marker, "targetMarker": edge["to"], "lastBassPitch": note.pitch,
                      "bassApproachRealized": (note.pitch - root) % 12 in {1, 11}, "addedNotes": 0})
    endings = []
    for marker in ("e1cv1", "e2cv1"):
        bass = _notes(notes, marker, "bass", spans, channels); root = _root(nodes[marker]["harmonicContext"][-1])
        endings.append({"marker": marker, "lastBassPitch": bass[-1].pitch,
                        "rootResolutionConfirmed": bass[-1].pitch % 12 == root})
    curve = []
    for marker in ("v1cv1", "v2cv1", "v3cv1", "v4cv1"):
        start, end = spans[marker]; selected = [note for note in notes if start <= note.start < end]
        curve.append({"marker": marker, "targetEnergy": nodes[marker]["targetEnergy"],
                      "noteCount": len(selected), "densityPerQuarter": round(len(selected) / ((end-start)/480), 4),
                      "meanRegister": round(sum(note.pitch for note in selected) / len(selected), 4)})
    non_notes = lambda mf: [(e.tick, e.kind, e.status, e.data, e.meta_type) for e in mf.tracks[0].events
                            if not (e.is_note_on or e.is_note_off)]
    shape = lambda ns: Counter((n.channel, n.start, n.end, n.velocity) for n in ns)
    pa = pa800_validator.validate_pa800_smf(raw, list(manifest["midi"]["markers"]),
                                           [int(x)-1 for x in manifest["midi"]["usedChannels"]])
    checks = {"pa800HardValidator": bool(pa["passed"]), "nonNoteEventsUnchanged": non_notes(midi) == non_notes(original),
              "timingDurationVelocityUnchanged": shape(notes) == shape(baseline_notes),
              "noAddedOrRemovedNotes": len(notes) == len(baseline_notes),
              "allFillTargetsRealized": all(row["bassApproachRealized"] for row in fills),
              "allEndingsResolved": all(row["rootResolutionConfirmed"] for row in endings),
              "softwarePolyphonyCeiling": _peak(notes) <= MIDI_NOTE_CEILING}
    return {"midiSha256": sha256(raw).hexdigest(), "noteCount": len(notes),
            "operationCount": len(operations), "globalPeakConcurrentMidiNotes": _peak(notes),
            "variationCurve": curve, "fillTargets": fills, "endingResolutions": endings,
            "checks": checks}


def build_global_coherence_variants(rendered_midi: bytes, render_manifest: Mapping[str, Any],
                                    arrangement_graph: Mapping[str, Any]):
    validate_render_manifest_v2(render_manifest, rendered_midi)
    if arrangement_graph.get("graphHash") != render_manifest["source"]["arrangementGraphHash"]:
        raise ValueError("Coherence graph hash mismatch")
    notes = MidiFile.from_bytes(rendered_midi).notes(); variants = {}; rows = []
    for variant in ("A", "B", "C"):
        operations = _operations(variant, notes, render_manifest, arrangement_graph)
        raw = _apply(rendered_midi, operations); metrics = _metrics(raw, rendered_midi, operations, render_manifest, arrangement_graph)
        if not all(metrics["checks"].values()):
            raise ValueError("Coherence hard gate failed")
        variants[variant] = raw
        row = {"variantId": variant, "operations": operations, "metrics": metrics, "variantHash": ""}
        row["variantHash"] = _without(row, "variantHash"); rows.append(row)
    if len({sha256(raw).hexdigest() for raw in variants.values()}) != 3:
        raise ValueError("A/B/C variants are not distinct")
    plan = {"schema": COHERENCE_SCHEMA, "version": COHERENCE_VERSION,
            "coherenceId": "coherence-" + _hash([render_manifest["renderManifestHash"], arrangement_graph["graphHash"]])[:20],
            "source": {"renderManifestHash": render_manifest["renderManifestHash"],
                       "baselineMidiSha256": sha256(rendered_midi).hexdigest(),
                       "arrangementGraphHash": arrangement_graph["graphHash"]},
            "variants": rows, "audit": {"variantCount": 3, "baselineNoteCount": len(notes),
                "addedNotes": 0, "removedNotes": 0, "timingDurationVelocityMutations": 0,
                "controllerProgramMarkerMutations": 0, "goldDynamicsAuthority": False,
                "approximateSoundBinding": False},
            "safety": {"softwarePreviewOnly": True, "finalCertifiedMidiExportAllowed": False,
                       "humanListening": "PENDING_0_OF_2", "physicalPa800": "WAITING_FOR_DEVICE"},
            "coherencePlanHash": ""}
    plan["coherencePlanHash"] = _without(plan, "coherencePlanHash")
    validate_global_coherence_plan(plan, variants)
    return variants, plan


def validate_global_coherence_plan(plan: Mapping[str, Any], variants: Mapping[str, bytes] | None = None) -> None:
    if plan.get("schema") != COHERENCE_SCHEMA or plan.get("version") != COHERENCE_VERSION:
        raise ValueError("Unsupported CoherencePlan")
    if any(not _SHA.fullmatch(str(value)) for value in plan["source"].values()):
        raise ValueError("Invalid coherence source hash")
    if [row["variantId"] for row in plan["variants"]] != ["A", "B", "C"]:
        raise ValueError("Coherence variants must be A/B/C")
    if any(row["variantHash"] != _without(row, "variantHash") or not all(row["metrics"]["checks"].values())
           for row in plan["variants"]):
        raise ValueError("Invalid coherence variant")
    audit = plan["audit"]
    if any(audit[key] for key in ("addedNotes", "removedNotes", "timingDurationVelocityMutations",
                                  "controllerProgramMarkerMutations", "goldDynamicsAuthority", "approximateSoundBinding")):
        raise ValueError("Coherence authority expanded")
    if not plan["safety"]["softwarePreviewOnly"] or plan["safety"]["finalCertifiedMidiExportAllowed"]:
        raise ValueError("Coherence certification boundary violated")
    if plan["coherencePlanHash"] != _without(plan, "coherencePlanHash"):
        raise ValueError("Coherence plan hash mismatch")
    if variants is not None:
        if set(variants) != {"A", "B", "C"}:
            raise ValueError("Coherence MIDI set mismatch")
        for row in plan["variants"]:
            if sha256(variants[row["variantId"]]).hexdigest() != row["metrics"]["midiSha256"]:
                raise ValueError("Coherence MIDI hash mismatch")


def verify_global_coherence(variants, plan, baseline):
    issues = []
    try:
        validate_global_coherence_plan(plan, variants); valid = True
    except Exception as exc:
        valid = False; issues.append(str(exc))
    checks = {"planValid": valid,
              "baselineHash": sha256(baseline).hexdigest() == plan.get("source", {}).get("baselineMidiSha256"),
              "threeDistinctVariants": len({sha256(raw).hexdigest() for raw in variants.values()}) == 3,
              "allVariantGates": all(all(row["metrics"]["checks"].values()) for row in plan.get("variants", ())),
              "noEventAuthorityExpansion": plan.get("audit", {}).get("addedNotes") == 0}
    return {"schema": "dna-global-coherence-verification", "version": "1.0",
            "passed": not issues and all(checks.values()), "issues": issues, "checks": checks,
            "coherencePlanHash": plan.get("coherencePlanHash"),
            "variantMidiSha256": {key: sha256(raw).hexdigest() for key, raw in variants.items()}}


def execute_global_coherence_api(payload: Mapping[str, Any]):
    if set(payload) != {"renderedMidiBase64", "renderManifest", "arrangementGraph"}:
        raise ValueError("Global Coherence API fields are strict")
    try:
        baseline = base64.b64decode(payload["renderedMidiBase64"], validate=True)
    except Exception as exc:
        raise ValueError("Invalid base64 MIDI") from exc
    variants, plan = build_global_coherence_variants(baseline, payload["renderManifest"], payload["arrangementGraph"])
    return {"schema": "dna-global-coherence-api-result", "version": "1.0",
            "midiVariantsBase64": {key: base64.b64encode(raw).decode() for key, raw in variants.items()},
            "coherencePlan": plan, "verification": verify_global_coherence(variants, plan, baseline)}


def execute_global_coherence_gui(payload):
    return execute_global_coherence_api(payload)


def execute_global_coherence_batch(payloads):
    output = []
    for index, payload in enumerate(payloads):
        try:
            output.append({"index": index, "status": "PASS", "result": execute_global_coherence_api(payload)})
        except Exception as exc:
            output.append({"index": index, "status": "BLOCKED", "error": str(exc)})
    return output