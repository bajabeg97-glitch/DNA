"""Self-authored Session 31 automatic track/instrument analysis fixtures."""

from __future__ import annotations

from pathlib import Path

from .midi import MidiEvent, MidiFile, MidiTrack
from .session19_fixture import PPQ, build_benchmark_case, with_velocity
from .track_instrument_analysis import analyze_track_instruments


REFERENCE_DATE = "2026-09-03"


def _channel(tick: int, order: int, status: int, *data: int) -> MidiEvent:
    return MidiEvent(tick, order, "channel", status=status, data=bytes(data))


def _banked(events: list[MidiEvent], channel: int, program: int) -> list[MidiEvent]:
    output = [
        _channel(0, -3, 0xB0 | channel, 0, 0),
        _channel(0, -2, 0xB0 | channel, 32, 0),
    ]
    for event in events:
        if event.command == 0xC0 and event.channel == channel and event.tick == 0:
            output.append(MidiEvent(event.tick, event.order, event.kind, event.status,
                                    bytes((program,)), event.meta_type))
        else:
            output.append(event)
    return output


def build_session31_midi() -> MidiFile:
    base = MidiFile.from_bytes(build_benchmark_case(0).midi)
    tracks = [MidiTrack(list(track.events)) for track in base.tracks]
    tracks[1] = MidiTrack(_banked(tracks[1].events, 0, 0))
    tracks[2] = MidiTrack(_banked(tracks[2].events, 1, 32))
    tracks[3] = MidiTrack(_banked(tracks[3].events, 2, 64))
    tracks[4] = MidiTrack(_banked(tracks[4].events, 9, 0))

    guitar = [
        MidiEvent(0, 0, "meta", data=b"Rhythm Guitar", meta_type=0x03),
        _channel(0, 1, 0xB3, 0, 0), _channel(0, 2, 0xB3, 32, 0),
        _channel(0, 3, 0xC3, 24),
    ]
    order = 10
    for start in range(0, PPQ * 16, PPQ):
        if start == PPQ * 8:
            guitar.append(_channel(start, order, 0xC3, 29)); order += 1
        for pitch in (52, 55, 60):
            guitar.append(_channel(start, order, 0x93, pitch, 70)); order += 1
            guitar.append(_channel(start + PPQ // 2, order, 0x83, pitch, 0)); order += 1
    tracks.append(MidiTrack(guitar))
    return MidiFile(1, PPQ, tracks)


def build_session31_factory_catalog() -> dict:
    def melodic(identifier: str, key: str, name: str, role: str, low: int, high: int):
        _, msb, lsb, program = key.split(":")
        return {
            "id": identifier, "instrumentKey": key, "instrument": name,
            "kind": "melodic", "role": role, "bankMsb": int(msb),
            "bankLsb": int(lsb), "program": int(program),
            "register": {"low": low, "high": high}, "samples": 120,
            "confidence": 0.94,
        }

    def drum(identifier: str, pitch: int, name: str):
        return {
            "id": identifier, "instrumentKey": f"drum:0:0:0:{pitch}",
            "instrument": name, "kind": "drum", "role": "drums",
            "bankMsb": 0, "bankLsb": 0, "program": 0, "drumNote": pitch,
            "register": {"low": pitch, "high": pitch}, "samples": 240,
            "confidence": 0.98,
        }

    profiles = [
        melodic("131.000.001", "melodic:0:0:0", "Factory Grand Piano", "chords", 36, 96),
        melodic("131.032.001", "melodic:0:0:32", "Factory Finger Bass", "bass", 28, 58),
        melodic("131.064.001", "melodic:0:0:64", "Factory Soprano Sax", "solo", 55, 94),
        melodic("131.024.001", "melodic:0:0:24", "Factory Nylon Guitar", "guitar", 40, 84),
        melodic("131.029.001", "melodic:0:0:29", "Factory Overdrive Guitar", "guitar", 40, 88),
        drum("131.036.001", 36, "Factory Kick"),
        drum("131.038.001", 38, "Factory Snare"),
    ]
    return {
        "schema": "midi-arranger.factory-velocity-profiles", "version": "3.3",
        "databaseVersion": "session31-self-authored-catalog", "profiles": profiles,
    }


def with_shared_bass_channel(midi: MidiFile) -> MidiFile:
    tracks = [MidiTrack(list(track.events)) for track in midi.tracks]
    events = [
        MidiEvent(0, 0, "meta", data=b"Shared Bass Layer", meta_type=0x03),
        _channel(0, 1, 0xB1, 0, 0), _channel(0, 2, 0xB1, 32, 0),
        _channel(0, 3, 0xC1, 32), _channel(0, 4, 0x91, 40, 70),
        _channel(PPQ, 5, 0x81, 40, 0),
    ]
    tracks.append(MidiTrack(events))
    return MidiFile(1, midi.ppq, tracks)


def build_session31_chain(root: str | Path):
    _ = Path(root)
    midi = build_session31_midi()
    catalog = build_session31_factory_catalog()
    analysis = analyze_track_instruments(
        midi.to_bytes(), "session31-reference.mid", factory_catalog=catalog
    )
    velocity_analysis = analyze_track_instruments(
        with_velocity(midi.to_bytes(), 17), "session31-velocity-variant.mid",
        factory_catalog=catalog,
    )
    missing_catalog = analyze_track_instruments(
        midi.to_bytes(), "session31-no-registry.mid", factory_catalog=None
    )
    shared_midi = with_shared_bass_channel(midi)
    shared_analysis = analyze_track_instruments(
        shared_midi.to_bytes(), "session31-shared-channel.mid", factory_catalog=catalog
    )
    return {
        "midi": midi, "factoryCatalog": catalog, "analysis": analysis,
        "velocityAnalysis": velocity_analysis, "missingCatalogAnalysis": missing_catalog,
        "sharedMidi": shared_midi, "sharedAnalysis": shared_analysis,
    }
