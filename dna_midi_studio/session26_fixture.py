"""Self-authored Session 26 preview, audio and device-comparison fixtures."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json

from .articulation_mapping import build_articulation_plan
from .premium_preview import build_preview_session, render_preview_wav
from .session25_fixture import build_session25_chain
from .song_understanding import analyze_song_map
from .track_identity import identity_for_track


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _expression_plan(midi, song_map):
    identity = identity_for_track(midi, 2)
    notes = [note for note in midi.notes() if note.track == 2]
    layers = []
    for subtype, shift, limit in (("grace", -1, 2), ("third", 4, 2), ("echo", 7, 1)):
        events = []
        for index, note in enumerate(notes[:limit]):
            onset = max(0, note.start - 36) if subtype == "grace" else note.start + (120 if subtype == "echo" else 0)
            duration = 36 if subtype == "grace" else max(48, min(240, note.end - note.start))
            event = {
                "eventId": f"fixture-{subtype}-{index + 1}", "kind": subtype,
                "sourceNoteUid": f"source-{index + 1}", "evidenceId": "260.100.001",
                "reasonCode": f"SESSION26_{subtype.upper()}_PREVIEW", "phraseId": "phrase-001",
                "chordCellIndex": index, "trackUid": identity.track_uid,
                "trackNumber": identity.track_number, "channelNumber": note.channel + 1,
                "routing": "SESSION26_PREVIEW_LAYER", "onsetTick": onset,
                "durationTick": duration, "pitch": max(0, min(127, note.pitch + shift)),
                "velocity": max(1, note.velocity - 8), "productionEligible": False,
            }
            event["eventHash"] = sha256(_canonical(event)).hexdigest()
            events.append(event)
        layer = {"layerId": f"fixture-layer-{subtype}", "layerType": "PREVIEW",
                 "subtype": subtype, "priorityTier": "DECORATIVE", "removable": True,
                 "decision": "ADD", "reason": "SELF_AUTHORED_SESSION26_FIXTURE",
                 "budget": limit, "events": events, "skipped": {}}
        layer["layerHash"] = sha256(_canonical(layer)).hexdigest()
        layers.append(layer)
    plan = {"schema": "dna-premium-expression-plan", "version": "2.0", "layers": layers,
            "audit": {"maximumEstimatedPeak": 8}, "readyForPreview": True,
            "readyForProductionRender": False}
    plan["expressionPlanHash"] = sha256(_canonical(plan)).hexdigest()
    return plan


def build_session26_chain():
    midi, capture, catalog, groove, _, articulation_controls = build_session25_chain()
    song_map = analyze_song_map(midi.to_bytes(), "session26-reference.mid")
    expression = _expression_plan(midi, song_map)
    articulations = [
        build_articulation_plan(midi, catalog, groove, {"schema": "dna-premium-expression-plan",
                                                        "version": "2.0", "layers": [],
                                                        "audit": {"maximumEstimatedPeak": 8},
                                                        "expressionPlanHash": sha256(b"session26-expression-base").hexdigest()},
                                articulation_controls[engine])
        for engine in ("GUITAR", "RX", "DNC")
    ]
    verdict = {"passed": True, "finalMidiSha256": midi.digest(),
               "reportHash": sha256(b"session26-independent-validator").hexdigest(),
               "scope": "FINAL_MIDI"}
    controls = {"version": "1.0", "profileId": "PA800_PROXY_V1",
                "variants": ["A", "B", "C"], "loopSectionId": "sec-001",
                "soloRoles": [], "mutedRoles": [], "targetRmsDbfs": -20.0,
                "sampleRate": 12000, "maxAudioSeconds": 4, "masterGainDb": 0.0,
                "externalAdapter": {"schema": "dna-premium-audio-render-adapter",
                                    "version": "1.0", "mode": "DISABLED",
                                    "rendererId": "builtin-proxy", "executableSha256": None,
                                    "soundfontSha256": None}}
    session = build_preview_session(midi, song_map, groove, expression, articulations,
                                    verdict, controls)
    wav_bytes, audio_manifest = render_preview_wav(session, "C")
    capture_metadata = {"version": "1.0", "manufacturer": "Korg", "model": "Pa800",
                        "osVersion": "2.03-unverified", "capturedAt": date.today().isoformat(),
                        "operatorId": "session26-fixture", "sourceMidiSha256": midi.digest(),
                        "session16EvidenceHash": None,
                        "notes": "Self-authored comparison fixture; not physical certification evidence."}
    return (midi, song_map, groove, expression, articulations, verdict, controls,
            session, wav_bytes, audio_manifest, capture_metadata)