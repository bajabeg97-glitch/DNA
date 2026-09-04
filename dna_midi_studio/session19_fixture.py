"""Deterministic, legally self-authored fixtures for Session 19 benchmarks."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable

from .midi import MidiEvent, MidiFile, MidiTrack


PPQ = 480
QUALITY_INTERVALS = {
    "major": (0, 4, 7),
    "minor": (0, 3, 7),
    "diminished": (0, 3, 6),
    "suspended-2": (0, 2, 7),
    "suspended-4": (0, 5, 7),
    "dominant-seventh": (0, 4, 7, 10),
    "major-seventh": (0, 4, 7, 11),
    "minor-seventh": (0, 3, 7, 10),
}


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    midi: bytes
    chord_labels: tuple[str, ...]
    section_boundaries: tuple[int, ...]
    section_labels: tuple[str, ...]

    @property
    def sha256(self) -> str:
        return sha256(self.midi).hexdigest()


def _meta(tick: int, order: int, meta_type: int, data: bytes) -> MidiEvent:
    return MidiEvent(tick, order, "meta", data=data, meta_type=meta_type)


def _channel(tick: int, order: int, status: int, data: Iterable[int]) -> MidiEvent:
    return MidiEvent(tick, order, "channel", status=status, data=bytes(data))


def _add_note(events: list[MidiEvent], order: int, channel: int, pitch: int,
              start: int, end: int, velocity: int) -> int:
    events.append(_channel(start, order, 0x90 | channel, (pitch, velocity)))
    events.append(_channel(end, order + 1, 0x80 | channel, (pitch, 0)))
    return order + 2


def _label(root: int, quality: str, bass: int) -> str:
    return f"{root % 12}:{quality}:{bass % 12}"


def build_benchmark_case(index: int) -> BenchmarkCase:
    """Build an eight-bar song with 16 labeled half-bar chord cells."""
    roots = (0, 5, 7, 0, 9, 5, 7, 0)
    qualities = (
        "major", "major", "dominant-seventh", "major-seventh",
        "minor", "suspended-4", "dominant-seventh", "major",
    )
    transpose = (index * 5) % 12
    inversion = index % 4 == 3
    bpm = 84 + index * 3
    bar_ticks = PPQ * 4
    end_tick = bar_ticks * 8

    conductor = [
        _meta(0, 0, 0x03, b"Conductor"),
        _meta(0, 1, 0x51, int(round(60_000_000 / bpm)).to_bytes(3, "big")),
        _meta(0, 2, 0x58, bytes((4, 2, 24, 8))),
    ]
    section_spec = ((0, "intro"), (2, "verse"), (4, "chorus"), (6, "ending"))
    for order, (bar, label) in enumerate(section_spec, 3):
        conductor.append(_meta(bar * bar_ticks, order, 0x06, label.encode("ascii")))

    harmony = [_meta(0, 0, 0x03, b"Harmony"), _channel(0, 1, 0xC0, (0,))]
    bass = [_meta(0, 0, 0x03, b"Bass"), _channel(0, 1, 0xC1, (32,))]
    lead = [_meta(0, 0, 0x03, b"Lead Solo"), _channel(0, 1, 0xC2, (80,))]
    drums = [_meta(0, 0, 0x03, b"Drums"), _channel(0, 1, 0xC9, (0,))]
    orders = [2, 2, 2, 2]
    expected = []
    for cell in range(16):
        start, end = cell * bar_ticks // 2, (cell + 1) * bar_ticks // 2
        slot = cell % len(roots)
        root = (roots[slot] + transpose) % 12
        quality = qualities[slot]
        tones = QUALITY_INTERVALS[quality]
        bass_pc = (root + tones[1]) % 12 if inversion and cell in {3, 11} else root
        expected.append(_label(root, quality, bass_pc))
        for interval in tones:
            pitch = 60 + ((root + interval - 0) % 12)
            orders[0] = _add_note(harmony, orders[0], 0, pitch, start, end - 20, 38 + index % 70)
        orders[1] = _add_note(bass, orders[1], 1, 36 + bass_pc, start, end - 10, 100 - index % 30)
        melody_pitch = 72 + ((root + tones[-1]) % 12)
        orders[2] = _add_note(lead, orders[2], 2, melody_pitch,
                              start + PPQ // 2, min(end - 30, start + PPQ), 25 + index % 90)
        for offset, drum_pitch in ((0, 36), (PPQ, 38)):
            orders[3] = _add_note(drums, orders[3], 9, drum_pitch,
                                  start + offset, min(end - 1, start + offset + 60), 64)

    midi = MidiFile(1, PPQ, [MidiTrack(conductor), MidiTrack(harmony),
                              MidiTrack(bass), MidiTrack(lead), MidiTrack(drums)])
    return BenchmarkCase(
        case_id=f"song19-{index + 1:02d}", midi=midi.to_bytes(),
        chord_labels=tuple(expected),
        section_boundaries=tuple(bar * bar_ticks for bar, _ in section_spec),
        section_labels=tuple(label for _, label in section_spec),
    )


def build_labeled_benchmark(count: int = 20) -> tuple[BenchmarkCase, ...]:
    if count < 20:
        raise ValueError("Session 19 benchmark requires at least 20 songs")
    return tuple(build_benchmark_case(index) for index in range(count))


def build_variable_meter_case() -> bytes:
    """Build a read-only analysis fixture with tempo and meter changes."""
    conductor = [
        _meta(0, 0, 0x03, b"Variable conductor"),
        _meta(0, 1, 0x51, (500000).to_bytes(3, "big")),
        _meta(0, 2, 0x58, bytes((4, 2, 24, 8))),
        _meta(PPQ * 8, 3, 0x51, (400000).to_bytes(3, "big")),
        _meta(PPQ * 8, 4, 0x58, bytes((3, 2, 24, 8))),
        _meta(0, 5, 0x06, b"intro"),
        _meta(PPQ * 8, 6, 0x06, b"verse"),
    ]
    harmony = [_meta(0, 0, 0x03, b"Harmony"), _channel(0, 1, 0xC0, (0,))]
    order = 2
    for start, end, pitches in ((0, PPQ * 8, (60, 64, 67)),
                                (PPQ * 8, PPQ * 14, (65, 69, 72))):
        for pitch in pitches:
            order = _add_note(harmony, order, 0, pitch, start, end, 70)
    return MidiFile(1, PPQ, [MidiTrack(conductor), MidiTrack(harmony)]).to_bytes()


def with_velocity(midi_bytes: bytes, velocity: int) -> bytes:
    midi = MidiFile.from_bytes(midi_bytes)
    tracks = []
    for track in midi.tracks:
        events = []
        for event in track.events:
            if event.is_note_on:
                events.append(MidiEvent(event.tick, event.order, event.kind, event.status,
                                        bytes((event.data[0], velocity)), event.meta_type))
            else:
                events.append(event)
        tracks.append(MidiTrack(events))
    return MidiFile(midi.format_type, midi.ppq, tracks).to_bytes()