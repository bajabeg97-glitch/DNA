"""Synthetic but byte-real MIDI fixture for the Session 2 release gate."""

from __future__ import annotations

from pathlib import Path

from .drum_reconstruction import ReconstructionConfig, load_registry
from .midi import MidiEvent, MidiFile, MidiTrack, channel_event, meta_event


def _paired_note(
    start: int,
    end: int,
    channel: int,
    pitch: int,
    velocity: int,
    order: int,
) -> list[MidiEvent]:
    return [
        channel_event(start, order, 0x90 | channel, pitch, velocity),
        channel_event(end, order + 1, 0x80 | channel, pitch, 0),
    ]


def build_demo_midi() -> MidiFile:
    conductor = MidiTrack(
        [
            meta_event(0, 0, 0x03, b"Conductor"),
            meta_event(0, 1, 0x51, bytes((0x07, 0xA1, 0x20))),
            meta_event(0, 2, 0x58, bytes((4, 2, 24, 8))),
        ]
    )
    drum_events = [
        meta_event(0, 0, 0x03, b"Weak Drums"),
        channel_event(0, 1, 0xC0 | 9, 0),
        channel_event(0, 2, 0xB0 | 9, 7, 100),
    ]
    order = 3
    for start, pitch in ((1440, 36), (1920, 36), (2880, 38), (3840, 36), (4800, 38)):
        drum_events.extend(_paired_note(start, start + 100, 9, pitch, 30, order))
        order += 2
    drum_events.extend(_paired_note(5900, 6000, 9, 49, 35, order))
    percussion_events = [
        meta_event(0, 0, 0x03, b"Percussion"),
        channel_event(0, 1, 0xC0 | 10, 0),
    ]
    percussion_events.extend(_paired_note(2160, 2230, 10, 54, 20, 2))
    return MidiFile(
        format_type=1,
        ppq=480,
        tracks=[
            conductor,
            MidiTrack(drum_events),
            MidiTrack(percussion_events),
        ],
    )


def build_demo_case(registry_path: str | Path):
    patterns, profiles = load_registry(registry_path)
    config = ReconstructionConfig(
        track_index=1,
        channel=9,
        role="drums",
        section="variation",
        start_tick=1920,
        end_tick=5760,
        seed=800,
        intensity=50,
        meter="4/4",
        desired_notes_per_quarter=3.25,
        expected_program=0,
    )
    return build_demo_midi(), patterns, profiles, config