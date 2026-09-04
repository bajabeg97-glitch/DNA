"""Byte-real Session 7 DNC fixture built on the Session 2–6 chain."""

from __future__ import annotations

from pathlib import Path

from .dnc_engine import DncConfig, load_dnc_registry
from .midi import MidiEvent, MidiFile, MidiTrack, channel_event, meta_event
from .rx_engine import apply_rx_events, plan_rx_events
from .session6_fixture import build_session6_case


def _paired(start: int, end: int, channel: int, pitch: int, velocity: int, order: int) -> list[MidiEvent]:
    return [
        channel_event(start, order, 0x90 | channel, pitch, velocity),
        channel_event(end, order + 1, 0x80 | channel, pitch, 0),
    ]


def build_session7_midi(root: str | Path) -> MidiFile:
    root = Path(root)
    midi, rx_maps, profiles, config = build_session6_case(root)
    midi = apply_rx_events(midi, plan_rx_events(midi, rx_maps, profiles, config)).midi
    channel = 12
    events = [
        meta_event(0, 0, 0x03, b"DNC Strings"),
        channel_event(0, 1, 0xB0 | channel, 0, 0),
        channel_event(0, 2, 0xB0 | channel, 32, 0),
        channel_event(0, 3, 0xC0 | channel, 48),
        channel_event(0, 4, 0xB0 | channel, 7, 98),
    ]
    phrase = [
        (1920, 2520, 60, 35),
        (2580, 2820, 62, 96),
        (2880, 3120, 64, 44),
        (3840, 4440, 67, 105),
        (4500, 4740, 74, 39),
        (4800, 5520, 72, 87),
    ]
    order = 5
    for start, end, pitch, velocity in phrase:
        events.extend(_paired(start, end, channel, pitch, velocity, order))
        order += 2
    return MidiFile(midi.format_type, midi.ppq, midi.tracks + [MidiTrack(events)])


def build_session7_case(root: str | Path):
    root = Path(root)
    midi = build_session7_midi(root)
    maps, profiles = load_dnc_registry(root / "data" / "session7-demo-registry.json")
    config = DncConfig(
        track_index=10,
        channel=12,
        start_tick=1800,
        end_tick=5760,
        seed=807,
        intensity=50,
        role="strings",
        map_id="170.100.001",
        profile_id="170.012.001",
        requested_articulations=("legato_switch", "accent_cc", "release_pressure", "phrase_switch"),
        allow_synthetic_map=True,
        max_generated_events=16,
        existing_tolerance_ticks=8,
    )
    return midi, maps, profiles, config