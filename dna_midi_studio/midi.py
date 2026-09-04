"""Small, dependency-free Standard MIDI File reader/writer.

The codec deliberately keeps non-note events as opaque MIDI events while exposing
paired notes for deterministic editing.  It supports the event types required by
the Session 2 reconstruction gate, including running status, meta events, SysEx,
controllers and Program Change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import struct
from typing import Iterable


class MidiFormatError(ValueError):
    """Raised when an SMF is malformed or unsupported."""


def _read_vlq(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if offset >= len(data):
            raise MidiFormatError("Truncated variable-length quantity")
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            return value, offset
    raise MidiFormatError("Variable-length quantity exceeds four bytes")


def _write_vlq(value: int) -> bytes:
    if value < 0 or value > 0x0FFFFFFF:
        raise MidiFormatError(f"Invalid variable-length quantity: {value}")
    output = [value & 0x7F]
    value >>= 7
    while value:
        output.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(output))


@dataclass(frozen=True)
class MidiEvent:
    tick: int
    order: int
    kind: str
    status: int | None = None
    data: bytes = b""
    meta_type: int | None = None

    @property
    def channel(self) -> int | None:
        if self.kind == "channel" and self.status is not None:
            return self.status & 0x0F
        return None

    @property
    def command(self) -> int | None:
        if self.kind == "channel" and self.status is not None:
            return self.status & 0xF0
        return None

    @property
    def is_note_on(self) -> bool:
        return self.command == 0x90 and len(self.data) == 2 and self.data[1] > 0

    @property
    def is_note_off(self) -> bool:
        return (
            self.command == 0x80
            or (self.command == 0x90 and len(self.data) == 2 and self.data[1] == 0)
        )

    @property
    def note_number(self) -> int | None:
        if (self.is_note_on or self.is_note_off) and self.data:
            return self.data[0]
        return None


@dataclass(frozen=True)
class Note:
    track: int
    channel: int
    pitch: int
    start: int
    end: int
    velocity: int
    on_order: int = -1
    off_order: int = -1
    factory_profile_id: str | None = None
    gold_pattern_id: str | None = None
    element: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.channel <= 15:
            raise MidiFormatError(f"Invalid MIDI channel: {self.channel}")
        if not 0 <= self.pitch <= 127:
            raise MidiFormatError(f"Invalid note number: {self.pitch}")
        if not 1 <= self.velocity <= 127:
            raise MidiFormatError(f"Invalid note velocity: {self.velocity}")
        if self.start < 0 or self.end <= self.start:
            raise MidiFormatError(
                f"Invalid note duration: start={self.start}, end={self.end}"
            )


@dataclass
class MidiTrack:
    events: list[MidiEvent] = field(default_factory=list)


@dataclass
class MidiFile:
    format_type: int
    ppq: int
    tracks: list[MidiTrack]

    @classmethod
    def from_bytes(cls, raw: bytes) -> "MidiFile":
        if len(raw) < 14 or raw[:4] != b"MThd":
            raise MidiFormatError("Missing MThd header")
        header_length = struct.unpack(">I", raw[4:8])[0]
        if header_length < 6 or len(raw) < 8 + header_length:
            raise MidiFormatError("Invalid MThd length")
        format_type, track_count, division = struct.unpack(">HHH", raw[8:14])
        if format_type not in (0, 1):
            raise MidiFormatError(f"Unsupported MIDI format: {format_type}")
        if track_count == 0:
            raise MidiFormatError("MIDI file must contain at least one track")
        if format_type == 0 and track_count != 1:
            raise MidiFormatError("SMF0 must contain exactly one track")
        if division & 0x8000:
            raise MidiFormatError("SMPTE time division is not supported")
        if division == 0:
            raise MidiFormatError("PPQ must be greater than zero")

        offset = 8 + header_length
        tracks: list[MidiTrack] = []
        for _ in range(track_count):
            if offset + 8 > len(raw) or raw[offset : offset + 4] != b"MTrk":
                raise MidiFormatError("Missing MTrk chunk")
            length = struct.unpack(">I", raw[offset + 4 : offset + 8])[0]
            start = offset + 8
            end = start + length
            if end > len(raw):
                raise MidiFormatError("MTrk chunk exceeds file length")
            tracks.append(MidiTrack(_parse_track(raw[start:end])))
            offset = end
        if offset != len(raw):
            raise MidiFormatError("Trailing bytes after declared MIDI tracks")
        return cls(format_type=format_type, ppq=division, tracks=tracks)

    @classmethod
    def read(cls, path: str | Path) -> "MidiFile":
        return cls.from_bytes(Path(path).read_bytes())

    def to_bytes(self) -> bytes:
        if self.format_type == 0 and len(self.tracks) != 1:
            raise MidiFormatError("SMF0 must contain exactly one track")
        if not self.tracks:
            raise MidiFormatError("A MIDI file must contain at least one track")
        header = b"MThd" + struct.pack(">IHHH", 6, self.format_type, len(self.tracks), self.ppq)
        chunks = []
        for track in self.tracks:
            payload = _write_track(track.events)
            chunks.append(b"MTrk" + struct.pack(">I", len(payload)) + payload)
        return header + b"".join(chunks)

    def write(self, path: str | Path) -> None:
        Path(path).write_bytes(self.to_bytes())

    def digest(self) -> str:
        return sha256(self.to_bytes()).hexdigest()

    def notes(self) -> list[Note]:
        notes: list[Note] = []
        for track_index, track in enumerate(self.tracks):
            active: dict[tuple[int, int], list[MidiEvent]] = {}
            for event in sorted(track.events, key=lambda item: (item.tick, item.order)):
                if event.is_note_on:
                    key = (event.channel or 0, event.note_number or 0)
                    active.setdefault(key, []).append(event)
                elif event.is_note_off:
                    key = (event.channel or 0, event.note_number or 0)
                    queue = active.get(key)
                    if not queue:
                        raise MidiFormatError(
                            f"Orphan note-off at track {track_index}, tick {event.tick}"
                        )
                    onset = queue.pop(0)
                    if event.tick <= onset.tick:
                        raise MidiFormatError(
                            f"Non-positive note duration at track {track_index}, tick {event.tick}"
                        )
                    notes.append(
                        Note(
                            track=track_index,
                            channel=key[0],
                            pitch=key[1],
                            start=onset.tick,
                            end=event.tick,
                            velocity=onset.data[1],
                            on_order=onset.order,
                            off_order=event.order,
                        )
                    )
            dangling = sum(len(queue) for queue in active.values())
            if dangling:
                raise MidiFormatError(
                    f"Track {track_index} contains {dangling} dangling note-on event(s)"
                )
        return sorted(notes, key=lambda item: (item.track, item.start, item.channel, item.pitch))

    def program_at(self, channel: int, tick: int, track_index: int | None = None) -> int | None:
        candidates: list[tuple[int, int, int]] = []
        if track_index is not None and not 0 <= track_index < len(self.tracks):
            raise MidiFormatError(f"Invalid target track: {track_index}")
        tracks = [self.tracks[track_index]] if track_index is not None else self.tracks
        for track in tracks:
            for event in track.events:
                if (
                    event.kind == "channel"
                    and event.command == 0xC0
                    and event.channel == channel
                    and event.tick <= tick
                    and event.data
                ):
                    candidates.append((event.tick, event.order, event.data[0]))
        return max(candidates)[2] if candidates else None

    def sound_at(self, channel: int, tick: int, track_index: int | None = None) -> tuple[int, int, int] | None:
        """Return exact Bank MSB/LSB/Program state, optionally scoped to one track."""
        if track_index is not None and not 0 <= track_index < len(self.tracks):
            raise MidiFormatError(f"Invalid target track: {track_index}")
        tracks = [self.tracks[track_index]] if track_index is not None else self.tracks
        events = sorted((event for track in tracks for event in track.events),
                        key=lambda item: (item.tick, item.order))
        bank_msb = bank_lsb = program = None
        for event in events:
            if event.tick > tick or event.kind != "channel" or event.channel != channel:
                continue
            if event.command == 0xB0 and len(event.data) == 2:
                if event.data[0] == 0:
                    bank_msb = event.data[1]
                elif event.data[0] == 32:
                    bank_lsb = event.data[1]
            elif event.command == 0xC0 and event.data:
                program = event.data[0]
        if bank_msb is None or bank_lsb is None or program is None:
            return None
        return bank_msb, bank_lsb, program

    def replace_notes(
        self,
        *,
        track_index: int,
        channel: int,
        start_tick: int,
        end_tick: int,
        new_notes: Iterable[Note],
    ) -> "MidiFile":
        if not 0 <= track_index < len(self.tracks):
            raise MidiFormatError(f"Invalid target track: {track_index}")
        old_notes = self.notes()
        remove_orders: set[int] = set()
        for note in old_notes:
            if (
                note.track == track_index
                and note.channel == channel
                and note.start >= start_tick
                and note.start < end_tick
            ):
                remove_orders.add(note.on_order)
                remove_orders.add(note.off_order)

        tracks = [MidiTrack(list(track.events)) for track in self.tracks]
        target = tracks[track_index]
        target.events = [
            event
            for event in target.events
            if event.order not in remove_orders or event.kind != "channel"
        ]
        next_order = max((event.order for event in target.events), default=-1) + 1
        for note in new_notes:
            if note.track != track_index or note.channel != channel:
                raise MidiFormatError("Generated note targets the wrong track or channel")
            target.events.append(
                MidiEvent(
                    tick=note.start,
                    order=next_order,
                    kind="channel",
                    status=0x90 | channel,
                    data=bytes((note.pitch, note.velocity)),
                )
            )
            next_order += 1
            target.events.append(
                MidiEvent(
                    tick=note.end,
                    order=next_order,
                    kind="channel",
                    status=0x80 | channel,
                    data=bytes((note.pitch, 0)),
                )
            )
            next_order += 1
        return MidiFile(self.format_type, self.ppq, tracks)

    def add_notes(self, *, track_index: int, new_notes: Iterable[Note]) -> "MidiFile":
        """Return a copy with paired notes appended to one existing track."""

        if not 0 <= track_index < len(self.tracks):
            raise MidiFormatError(f"Invalid target track: {track_index}")
        tracks = [MidiTrack(list(track.events)) for track in self.tracks]
        target = tracks[track_index]
        next_order = max((event.order for event in target.events), default=-1) + 1
        for note in new_notes:
            if note.track != track_index:
                raise MidiFormatError("Generated note targets the wrong track")
            target.events.append(
                MidiEvent(
                    tick=note.start,
                    order=next_order,
                    kind="channel",
                    status=0x90 | note.channel,
                    data=bytes((note.pitch, note.velocity)),
                )
            )
            next_order += 1
            target.events.append(
                MidiEvent(
                    tick=note.end,
                    order=next_order,
                    kind="channel",
                    status=0x80 | note.channel,
                    data=bytes((note.pitch, 0)),
                )
            )
            next_order += 1
        return MidiFile(self.format_type, self.ppq, tracks)

    def add_events(
        self, *, track_index: int, new_events: Iterable[MidiEvent]
    ) -> "MidiFile":
        """Return a copy with non-note events appended using fresh event ordering."""

        if not 0 <= track_index < len(self.tracks):
            raise MidiFormatError(f"Invalid target track: {track_index}")
        tracks = [MidiTrack(list(track.events)) for track in self.tracks]
        target = tracks[track_index]
        next_order = max((event.order for event in target.events), default=-1) + 1
        for event in new_events:
            if event.is_note_on or event.is_note_off:
                raise MidiFormatError("Use add_notes for paired note events")
            target.events.append(
                MidiEvent(
                    tick=event.tick,
                    order=next_order,
                    kind=event.kind,
                    status=event.status,
                    data=event.data,
                    meta_type=event.meta_type,
                )
            )
            next_order += 1
        return MidiFile(self.format_type, self.ppq, tracks)


def _parse_track(data: bytes) -> list[MidiEvent]:
    events: list[MidiEvent] = []
    offset = 0
    tick = 0
    running_status: int | None = None
    order = 0
    saw_eot = False
    while offset < len(data):
        delta, offset = _read_vlq(data, offset)
        tick += delta
        if offset >= len(data):
            raise MidiFormatError("Track ends after delta time")
        first = data[offset]
        if first & 0x80:
            status = first
            offset += 1
            first_data: int | None = None
        else:
            if running_status is None:
                raise MidiFormatError("Running status used before a channel status")
            status = running_status
            first_data = first
            offset += 1

        if status == 0xFF:
            running_status = None
            if first_data is not None or offset >= len(data):
                raise MidiFormatError("Malformed meta event")
            meta_type = data[offset]
            offset += 1
            length, offset = _read_vlq(data, offset)
            end = offset + length
            if end > len(data):
                raise MidiFormatError("Truncated meta event")
            payload = data[offset:end]
            offset = end
            events.append(MidiEvent(tick, order, "meta", data=payload, meta_type=meta_type))
            order += 1
            if meta_type == 0x2F:
                if length != 0 or offset != len(data):
                    raise MidiFormatError("End Of Track must be empty and final")
                saw_eot = True
                break
            continue

        if status in (0xF0, 0xF7):
            running_status = None
            if first_data is not None:
                raise MidiFormatError("Malformed SysEx event")
            length, offset = _read_vlq(data, offset)
            end = offset + length
            if end > len(data):
                raise MidiFormatError("Truncated SysEx event")
            events.append(MidiEvent(tick, order, "sysex", status=status, data=data[offset:end]))
            order += 1
            offset = end
            continue

        if not 0x80 <= status <= 0xEF:
            raise MidiFormatError(f"Unsupported system status: 0x{status:02X}")
        running_status = status
        data_length = 1 if status & 0xF0 in (0xC0, 0xD0) else 2
        payload = bytearray()
        if first_data is not None:
            payload.append(first_data)
        remaining = data_length - len(payload)
        if offset + remaining > len(data):
            raise MidiFormatError("Truncated channel event")
        payload.extend(data[offset : offset + remaining])
        offset += remaining
        if any(byte & 0x80 for byte in payload):
            raise MidiFormatError("Channel data byte exceeds 127")
        events.append(MidiEvent(tick, order, "channel", status=status, data=bytes(payload)))
        order += 1
    if not saw_eot:
        raise MidiFormatError("Track is missing End Of Track")
    return events


def _event_priority(event: MidiEvent) -> int:
    if event.kind == "meta" and event.meta_type == 0x2F:
        return 99
    if event.kind == "channel" and event.command in (0xC0, 0xB0):
        return 0
    if event.is_note_off:
        return 10
    if event.is_note_on:
        return 20
    return 5


def _write_track(events: Iterable[MidiEvent]) -> bytes:
    body = bytearray()
    current_tick = 0
    non_eot = [
        event
        for event in events
        if not (event.kind == "meta" and event.meta_type == 0x2F)
    ]
    ordered = sorted(non_eot, key=lambda item: (item.tick, _event_priority(item), item.order))
    for event in ordered:
        if event.tick < current_tick:
            raise MidiFormatError("Events are not monotonic")
        body.extend(_write_vlq(event.tick - current_tick))
        current_tick = event.tick
        if event.kind == "channel":
            if event.status is None or not 0x80 <= event.status <= 0xEF:
                raise MidiFormatError("Invalid channel event status")
            body.append(event.status)
            body.extend(event.data)
        elif event.kind == "meta":
            if event.meta_type is None or not 0 <= event.meta_type <= 127:
                raise MidiFormatError("Invalid meta event type")
            body.extend((0xFF, event.meta_type))
            body.extend(_write_vlq(len(event.data)))
            body.extend(event.data)
        elif event.kind == "sysex":
            if event.status not in (0xF0, 0xF7):
                raise MidiFormatError("Invalid SysEx status")
            body.append(event.status)
            body.extend(_write_vlq(len(event.data)))
            body.extend(event.data)
        else:
            raise MidiFormatError(f"Unknown event kind: {event.kind}")
    body.extend(_write_vlq(0))
    body.extend((0xFF, 0x2F, 0x00))
    return bytes(body)


def channel_event(tick: int, order: int, status: int, *data: int) -> MidiEvent:
    """Convenience constructor used by fixtures and callers."""

    return MidiEvent(tick, order, "channel", status=status, data=bytes(data))


def meta_event(tick: int, order: int, meta_type: int, data: bytes) -> MidiEvent:
    """Convenience constructor used by fixtures and callers."""

    return MidiEvent(tick, order, "meta", data=data, meta_type=meta_type)