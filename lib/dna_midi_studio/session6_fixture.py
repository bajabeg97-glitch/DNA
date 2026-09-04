"""Byte-real Session 6 RX fixture built on the Session 2–5 chain."""

from __future__ import annotations

from pathlib import Path

from .midi import MidiEvent, MidiFile, MidiTrack, channel_event, meta_event
from .rx_engine import RxConfig, load_rx_registry
from .session5_fixture import build_session5_case
from .solo_enhancement import apply_solo_enhancement, plan_solo_enhancement


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


def build_session6_midi(root: str | Path) -> MidiFile:
    root = Path(root)
    midi, ornaments, relationships, profiles, chords, config = build_session5_case(root)
    midi = apply_solo_enhancement(
        midi,
        plan_solo_enhancement(
            midi, ornaments, relationships, profiles, chords, config
        ),
    ).midi
    channel = 13
    events = [
        meta_event(0, 0, 0x03, b"RX Acoustic Guitar"),
        channel_event(0, 1, 0xB0 | channel, 0, 0),
        channel_event(0, 2, 0xB0 | channel, 32, 0),
        channel_event(0, 3, 0xC0 | channel, 24),
        channel_event(0, 4, 0xB0 | channel, 7, 96),
    ]
    phrase = [
        (1920, 2160, 60, 32),
        (2400, 2700, 64, 91),
        (2880, 3480, 67, 48),
        (3840, 4140, 69, 103),
        (4320, 4560, 76, 37),
        (4800, 5520, 72, 84),
    ]
    order = 5
    for start, end, pitch, velocity in phrase:
        events.extend(_paired_note(start, end, channel, pitch, velocity, order))
        order += 2
    return MidiFile(midi.format_type, midi.ppq, midi.tracks + [MidiTrack(events)])


def build_session6_case(root: str | Path):
    root = Path(root)
    midi = build_session6_midi(root)
    rx_maps, profiles = load_rx_registry(root / "data" / "session6-demo-registry.json")
    config = RxConfig(
        track_index=9,
        channel=13,
        start_tick=1800,
        end_tick=5760,
        seed=806,
        intensity=50,
        map_id="160.100.001",
        profile_id="160.014.001",
        requested_actions=(
            "pick_noise",
            "release_noise",
            "position_noise",
            "phrase_release",
        ),
        allow_synthetic_map=True,
        max_generated_events=16,
        existing_tolerance_ticks=8,
    )
    return midi, rx_maps, profiles, config