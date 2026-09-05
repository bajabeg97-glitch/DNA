"""Byte-real Session 5 solo fixture built on the Session 2–4 chain."""

from __future__ import annotations

from pathlib import Path

from .guitar_reconstruction import apply_guitar_reconstruction, plan_guitar_reconstruction
from .midi import MidiEvent, MidiFile, MidiTrack, channel_event, meta_event
from .session4_fixture import build_session4_case
from .solo_enhancement import SoloConfig, load_solo_registry


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


def build_session5_midi(root: str | Path) -> tuple[MidiFile, list]:
    root = Path(root)
    midi, patterns, profiles, control_maps, chords, config = build_session4_case(root)
    midi = apply_guitar_reconstruction(
        midi,
        plan_guitar_reconstruction(
            midi, patterns, profiles, control_maps, chords, config
        ),
    ).midi
    solo_events = [
        meta_event(0, 0, 0x03, b"Solo Lead"),
        channel_event(0, 1, 0xB0 | 14, 0, 0),
        channel_event(0, 2, 0xB0 | 14, 32, 0),
        channel_event(0, 3, 0xC0 | 14, 81),
        channel_event(0, 4, 0xB0 | 14, 7, 100),
    ]
    melody = [
        (1920, 2070, 60, 34),
        (2390, 2540, 64, 52),
        (2890, 3040, 67, 88),
        (3350, 3500, 64, 43),
        (3840, 3990, 69, 95),
        (4330, 4480, 72, 47),
        (4810, 4960, 76, 83),
        (5290, 5440, 72, 59)
    ]
    order = 5
    for start, end, pitch, velocity in melody:
        solo_events.extend(_paired_note(start, end, 14, pitch, velocity, order))
        order += 2
    return MidiFile(midi.format_type, midi.ppq, midi.tracks + [MidiTrack(solo_events)]), chords


def build_session5_case(root: str | Path):
    root = Path(root)
    midi, chords = build_session5_midi(root)
    ornaments, relationships, profiles = load_solo_registry(
        root / "data" / "session5-demo-registry.json"
    )
    config = SoloConfig(
        track_index=7,
        channel=14,
        start_tick=1920,
        end_tick=5760,
        seed=805,
        intensity=50,
        profile_id="140.014.001",
        ornaments=("trill", "grace", "slide"),
        enable_third=True,
        enable_echo=True,
        enable_expression=True,
        max_generated_notes=64,
        min_chord_confidence=0.8,
    )
    return midi, ornaments, relationships, profiles, chords, config