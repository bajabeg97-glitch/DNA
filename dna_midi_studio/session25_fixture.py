"""Self-authored Session 25 MIDI and articulation capture fixtures."""

from __future__ import annotations

from hashlib import sha256
import json

from .articulation_mapping import (
    build_reference_articulation_capture,
    import_articulation_capture,
)
from .midi import MidiEvent, MidiFile, MidiTrack
from .track_identity import identity_for_track


def _track(channel: int, sound: tuple[int, int, int], pitches: tuple[int, ...]) -> MidiTrack:
    events = [
        MidiEvent(0, -3, "channel", status=0xB0 | channel, data=bytes((0, sound[0]))),
        MidiEvent(0, -2, "channel", status=0xB0 | channel, data=bytes((32, sound[1]))),
        MidiEvent(0, -1, "channel", status=0xC0 | channel, data=bytes((sound[2],))),
    ]
    tick = 240
    for index, pitch in enumerate(pitches):
        duration = 720 if index % 2 == 0 else 360
        events.extend([
            MidiEvent(tick, index * 2, "channel", status=0x90 | channel,
                      data=bytes((pitch, 72 + index))),
            MidiEvent(tick + duration, index * 2 + 1, "channel", status=0x80 | channel,
                      data=bytes((pitch, 0))),
        ])
        tick += 480
    events.append(MidiEvent(2640, 999, "meta", data=b"", meta_type=0x2F))
    return MidiTrack(events)


def build_session25_midi() -> MidiFile:
    conductor = MidiTrack([
        MidiEvent(0, 0, "meta", data=bytes((0x07, 0xA1, 0x20)), meta_type=0x51),
        MidiEvent(2640, 1, "meta", data=b"", meta_type=0x2F),
    ])
    return MidiFile(1, 480, [
        conductor,
        _track(11, (121, 0, 24), (52, 55, 60, 57)),
        _track(2, (121, 1, 40), (60, 67, 64, 72)),
        _track(3, (121, 2, 80), (64, 71, 69, 76)),
    ])


def _document_hash(label: str) -> str:
    return sha256(json.dumps({"session": 25, "document": label}, sort_keys=True).encode()).hexdigest()


def build_session25_chain():
    midi = build_session25_midi()
    capture = build_reference_articulation_capture(midi.digest())
    catalog = import_articulation_capture(capture)
    groove = {
        "schema": "dna-premium-groove-plan", "version": "2.0",
        "groovePlanHash": _document_hash("groove"),
        "audit": {"maximumPeakAfterSimplification": 14},
    }
    expression = {
        "schema": "dna-premium-expression-plan", "version": "2.0",
        "expressionPlanHash": _document_hash("expression"),
        "audit": {"maximumEstimatedPeak": 18},
        "layers": [],
    }
    specs = {
        "GUITAR": (1, 12, "250.010.001", "guitar", ["mute", "stop", "body_tap"]),
        "RX": (2, 3, "250.020.001", "solo", ["fret_noise", "release_noise", "proprietary_noise"]),
        "DNC": (3, 4, "250.030.001", "solo", ["legato_switch", "breath", "fall_pressure"]),
    }
    controls = {}
    for engine, (track_index, channel_number, map_id, role, requested) in specs.items():
        controls[engine] = {
            "version": "1.0", "mapId": map_id,
            "trackUid": identity_for_track(midi, track_index).track_uid,
            "trackNumber": track_index + 1, "channelNumber": channel_number,
            "startTick": 0, "endTick": 2640, "role": role,
            "requestedArticulations": requested, "seed": 2525,
            "allowSharedChannel": False, "productionMode": False,
            "maxGeneratedEvents": 64,
        }
    return midi, capture, catalog, groove, expression, controls
