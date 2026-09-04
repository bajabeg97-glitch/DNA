"""Byte-real downstream fixture for Session 3 harmonic reconstruction."""

from __future__ import annotations

from pathlib import Path

from .drum_reconstruction import apply_reconstruction, plan_reconstruction
from .harmonic_reconstruction import (
    ChordCell,
    HarmonicConfig,
    load_harmonic_registry,
)
from .midi import MidiEvent, MidiFile, MidiTrack, channel_event, meta_event
from .session2_fixture import build_demo_case


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


def build_session3_midi(session2_registry: str | Path) -> MidiFile:
    midi, patterns, profiles, config = build_demo_case(session2_registry)
    session2_output = apply_reconstruction(
        midi, plan_reconstruction(midi, patterns, profiles, config)
    ).midi

    bass_events = [
        meta_event(0, 0, 0x03, b"Electric Bass"),
        channel_event(0, 1, 0xC0 | 8, 32),
        channel_event(0, 2, 0xB0 | 8, 7, 100),
    ]
    order = 3
    for start, pitch in ((1440, 36), (1920, 36), (3840, 45), (5900, 43)):
        bass_events.extend(_paired_note(start, start + 180, 8, pitch, 32, order))
        order += 2

    power_events = [
        meta_event(0, 0, 0x03, b"Power Riff"),
        channel_event(0, 1, 0xC0 | 11, 29),
    ]
    power_events.extend(_paired_note(1920, 2280, 11, 48, 40, 2))
    power_events.extend(_paired_note(1920, 2280, 11, 55, 40, 4))

    riff_events = [
        meta_event(0, 0, 0x03, b"Riff"),
        channel_event(0, 1, 0xC0 | 13, 81),
    ]
    riff_events.extend(_paired_note(1920, 2100, 13, 60, 38, 2))

    collision_events = [
        meta_event(0, 0, 0x03, b"Chord Pad"),
        channel_event(0, 1, 0xC0 | 12, 48),
    ]
    collision_events.extend(_paired_note(1920, 2280, 12, 36, 70, 2))

    return MidiFile(
        format_type=1,
        ppq=session2_output.ppq,
        tracks=session2_output.tracks
        + [
            MidiTrack(bass_events),
            MidiTrack(power_events),
            MidiTrack(riff_events),
            MidiTrack(collision_events),
        ],
    )


def build_session3_case(root: str | Path):
    root = Path(root)
    midi = build_session3_midi(root / "data" / "session2-demo-registry.json")
    patterns, profiles, relationships = load_harmonic_registry(
        root / "data" / "session3-demo-registry.json"
    )
    chords = [
        ChordCell(1920, 3840, 0, "major", 0.96),
        ChordCell(3840, 5760, 9, "minor", 0.94),
    ]
    config = HarmonicConfig(
        track_index=3,
        channel=8,
        role="bass",
        section="variation",
        start_tick=1920,
        end_tick=5760,
        seed=803,
        intensity=50,
        profile_id="120.008.001",
        desired_notes_per_quarter=1.5,
        selected_drum_pattern_id="210.010.001",
        require_relationship=True,
        existing_quality=0.2,
        collision_channels=(12,),
        collision_budget=2,
    )
    return midi, patterns, profiles, relationships, chords, config