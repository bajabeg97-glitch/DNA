"""Session 18 track identity, time-scoped sound binding and solo guards.

The module is intentionally independent from musical generation.  It assigns
stable source-track identities, describes Bank/Program state on one physical
track and verifies that protected solo notes survive every pipeline stage.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Mapping

from .midi import MidiEvent, MidiFile, MidiFormatError


TRACK_UID_PATTERN = re.compile(r"^trk-[0-9a-f]{20}(?:-[1-9][0-9]*)?$")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _event_signature(event: MidiEvent) -> list[Any]:
    return [
        event.tick,
        event.kind,
        event.status,
        event.meta_type,
        event.data.hex(),
    ]


def _track_content_hash(events: Iterable[MidiEvent]) -> str:
    payload = sorted(
        [
        _event_signature(event)
        for event in events
        if not (event.kind == "meta" and event.meta_type == 0x2F)
        ],
        key=lambda item: _canonical(item),
    )
    return sha256(_canonical(payload)).hexdigest()


@dataclass(frozen=True)
class TrackIdentity:
    track_uid: str
    track_index: int
    track_number: int
    event_count: int
    channel_indices: tuple[int, ...]
    smf0_merged: bool

    def to_manifest(self) -> dict[str, Any]:
        return {
            "trackUid": self.track_uid,
            "trackIndex": self.track_index,
            "trackNumber": self.track_number,
            "eventCount": self.event_count,
            "channelIndices": list(self.channel_indices),
            "channelNumbers": [channel + 1 for channel in self.channel_indices],
            "smf0Merged": self.smf0_merged,
        }


@dataclass(frozen=True)
class SoundBinding:
    track_uid: str
    track_index: int
    track_number: int
    channel_index: int
    channel_number: int
    start_tick: int
    end_tick: int
    bank_msb: int | None
    bank_lsb: int | None
    program: int | None

    @property
    def complete(self) -> bool:
        return None not in (self.bank_msb, self.bank_lsb, self.program)

    @property
    def sound(self) -> tuple[int, int, int] | None:
        if not self.complete:
            return None
        return int(self.bank_msb), int(self.bank_lsb), int(self.program)

    def to_manifest(self, *, status: str | None = None) -> dict[str, Any]:
        return {
            "schema": "dna-premium-sound-binding",
            "version": "2.0",
            "trackUid": self.track_uid,
            "trackIndex": self.track_index,
            "trackNumber": self.track_number,
            "channelIndex": self.channel_index,
            "channelNumber": self.channel_number,
            "startTick": self.start_tick,
            "endTick": self.end_tick,
            "bankMsb": self.bank_msb,
            "bankLsb": self.bank_lsb,
            "program": self.program,
            "complete": self.complete,
            "status": status or ("EXACT" if self.complete else "UNRESOLVED"),
        }


@dataclass(frozen=True)
class SoloFingerprint:
    track_uid: str
    track_index: int
    track_number: int
    channel_index: int
    channel_number: int
    start_tick: int
    end_tick: int
    notes: tuple[tuple[int, int, int, int], ...]
    sha256: str

    def to_manifest(self) -> dict[str, Any]:
        return {
            "trackUid": self.track_uid,
            "trackIndex": self.track_index,
            "trackNumber": self.track_number,
            "channelIndex": self.channel_index,
            "channelNumber": self.channel_number,
            "window": [self.start_tick, self.end_tick],
            "noteCount": len(self.notes),
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class DelayAllocation:
    allowed: bool
    reason: str
    source_track_uid: str
    target_track_uid: str | None
    target_track_index: int | None
    target_track_number: int | None
    target_created: bool
    shared_channel_tracks: tuple[int, ...]
    authorization: str

    def to_manifest(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "sourceTrackUid": self.source_track_uid,
            "targetTrackUid": self.target_track_uid,
            "targetTrackIndex": self.target_track_index,
            "targetTrackNumber": self.target_track_number,
            "targetCreated": self.target_created,
            "sharedChannelTrackIndices": list(self.shared_channel_tracks),
            "sharedChannelTrackNumbers": [index + 1 for index in self.shared_channel_tracks],
            "authorization": self.authorization,
        }


def build_track_identities(midi: MidiFile) -> tuple[TrackIdentity, ...]:
    """Assign source identities independent of 0/1-based presentation fields."""

    hashes = [_track_content_hash(track.events) for track in midi.tracks]
    seen: Counter[str] = Counter()
    identities = []
    for index, (track, content_hash) in enumerate(zip(midi.tracks, hashes)):
        seen[content_hash] += 1
        duplicate_suffix = f"-{seen[content_hash]}" if hashes.count(content_hash) > 1 else ""
        channels = tuple(sorted({
            event.channel
            for event in track.events
            if event.kind == "channel" and event.channel is not None
        }))
        uid = f"trk-{content_hash[:20]}{duplicate_suffix}"
        identities.append(
            TrackIdentity(
                track_uid=uid,
                track_index=index,
                track_number=index + 1,
                event_count=len(track.events),
                channel_indices=channels,
                smf0_merged=midi.format_type == 0 and len(channels) > 1,
            )
        )
    return tuple(identities)


def identity_for_track(midi: MidiFile, track_index: int) -> TrackIdentity:
    if not 0 <= track_index < len(midi.tracks):
        raise MidiFormatError(f"Invalid target track: {track_index}")
    return build_track_identities(midi)[track_index]


def channel_track_indices(midi: MidiFile, channel: int) -> tuple[int, ...]:
    if not 0 <= channel <= 15:
        raise MidiFormatError(f"Invalid MIDI channel: {channel}")
    return tuple(
        index
        for index, track in enumerate(midi.tracks)
        if any(event.kind == "channel" and event.channel == channel for event in track.events)
    )


def _update_sound_state(
    state: tuple[int | None, int | None, int | None], event: MidiEvent
) -> tuple[int | None, int | None, int | None]:
    bank_msb, bank_lsb, program = state
    if event.command == 0xB0 and len(event.data) == 2:
        if event.data[0] == 0:
            bank_msb = event.data[1]
        elif event.data[0] == 32:
            bank_lsb = event.data[1]
    elif event.command == 0xC0 and event.data:
        program = event.data[0]
    return bank_msb, bank_lsb, program


def sound_bindings(
    midi: MidiFile,
    *,
    track_index: int,
    channel: int,
    start_tick: int,
    end_tick: int,
    track_uid: str | None = None,
) -> tuple[SoundBinding, ...]:
    """Return coalesced Bank/Program segments for one physical source track."""

    if not 0 <= track_index < len(midi.tracks):
        raise MidiFormatError(f"Invalid target track: {track_index}")
    if not 0 <= channel <= 15:
        raise MidiFormatError(f"Invalid MIDI channel: {channel}")
    if start_tick < 0 or end_tick <= start_tick:
        raise ValueError("Invalid SoundBinding window")
    identity = identity_for_track(midi, track_index)
    uid = track_uid or identity.track_uid
    relevant = sorted(
        (
            event
            for event in midi.tracks[track_index].events
            if event.kind == "channel"
            and event.channel == channel
            and (
                event.command == 0xC0
                or (event.command == 0xB0 and len(event.data) == 2 and event.data[0] in {0, 32})
            )
        ),
        key=lambda item: (item.tick, item.order),
    )
    state: tuple[int | None, int | None, int | None] = (None, None, None)
    for event in relevant:
        if event.tick <= start_tick:
            state = _update_sound_state(state, event)
    change_ticks = sorted({event.tick for event in relevant if start_tick < event.tick < end_tick})
    segments: list[SoundBinding] = []
    segment_start = start_tick
    current_state = state
    for tick in change_ticks:
        next_state = current_state
        for event in relevant:
            if event.tick == tick:
                next_state = _update_sound_state(next_state, event)
        if next_state == current_state:
            continue
        segments.append(
            SoundBinding(
                uid, track_index, track_index + 1, channel, channel + 1,
                segment_start, tick, *current_state,
            )
        )
        segment_start = tick
        current_state = next_state
    segments.append(
        SoundBinding(
            uid, track_index, track_index + 1, channel, channel + 1,
            segment_start, end_tick, *current_state,
        )
    )
    return tuple(segments)


def fingerprint_solo(
    midi: MidiFile,
    *,
    track_index: int,
    channel: int,
    start_tick: int,
    end_tick: int,
    track_uid: str | None = None,
) -> SoloFingerprint:
    identity = identity_for_track(midi, track_index)
    uid = track_uid or identity.track_uid
    notes = tuple(sorted(
        (
            note.start,
            note.end,
            note.pitch,
            note.velocity,
        )
        for note in midi.notes()
        if note.track == track_index
        and note.channel == channel
        and start_tick <= note.start < end_tick
    ))
    payload = {
        "trackUid": uid,
        "channelIndex": channel,
        "window": [start_tick, end_tick],
        "notes": notes,
    }
    return SoloFingerprint(
        uid,
        track_index,
        track_index + 1,
        channel,
        channel + 1,
        start_tick,
        end_tick,
        notes,
        sha256(_canonical(payload)).hexdigest(),
    )


def verify_solo_fingerprint(
    fingerprint: SoloFingerprint, midi: MidiFile
) -> dict[str, Any]:
    current = Counter(
        (
            note.start,
            note.end,
            note.pitch,
            note.velocity,
        )
        for note in midi.notes()
        if note.track == fingerprint.track_index
        and note.channel == fingerprint.channel_index
        and fingerprint.start_tick <= note.start < fingerprint.end_tick
    )
    expected = Counter(fingerprint.notes)
    missing = expected - current
    return {
        "passed": not missing,
        "trackUid": fingerprint.track_uid,
        "trackIndex": fingerprint.track_index,
        "trackNumber": fingerprint.track_number,
        "channelIndex": fingerprint.channel_index,
        "channelNumber": fingerprint.channel_number,
        "beforeHash": fingerprint.sha256,
        "afterProtectedHash": fingerprint.sha256 if not missing else None,
        "protectedNoteCount": len(fingerprint.notes),
        "missingNotes": [list(note) for note in sorted(missing.elements())],
    }


def allocate_delay_track(
    midi: MidiFile,
    *,
    source_track_index: int,
    channel: int,
    allow_existing_shared_channel: bool = False,
) -> DelayAllocation:
    identity = identity_for_track(midi, source_track_index)
    owners = channel_track_indices(midi, channel)
    conflicts = tuple(index for index in owners if index != source_track_index)
    if conflicts and not allow_existing_shared_channel:
        return DelayAllocation(
            False,
            "Solo channel is already shared by multiple physical tracks",
            identity.track_uid,
            None,
            None,
            None,
            False,
            owners,
            "required",
        )
    if midi.format_type == 0:
        return DelayAllocation(
            False,
            "SMF0 merges channels into one physical track; Delay/Echo requires a separate free track",
            identity.track_uid,
            None,
            None,
            None,
            False,
            owners,
            "not-applicable",
        )
    identities = build_track_identities(midi)
    for index, track in enumerate(midi.tracks):
        if index == source_track_index:
            continue
        meaningful = [
            event for event in track.events
            if not (event.kind == "meta" and event.meta_type == 0x2F)
        ]
        if not meaningful:
            target = identities[index]
            return DelayAllocation(
                True,
                "First completely free existing track selected",
                identity.track_uid,
                target.track_uid,
                index,
                index + 1,
                False,
                owners,
                "explicit-delay-plan" if conflicts else "not-required",
            )
    if len(midi.tracks) >= 16:
        return DelayAllocation(
            False,
            "Delay/Echo requires a separate free track and cannot allocate a seventeenth track",
            identity.track_uid,
            None,
            None,
            None,
            False,
            owners,
            "not-applicable",
        )
    index = len(midi.tracks)
    uid_seed = f"{midi.digest()}|delay|{identity.track_uid}|{channel}|{index}"
    target_uid = f"trk-{sha256(uid_seed.encode('utf-8')).hexdigest()[:20]}"
    return DelayAllocation(
        True,
        "Next contiguous SMF1 track selected",
        identity.track_uid,
        target_uid,
        index,
        index + 1,
        True,
        owners,
        "explicit-delay-plan" if conflicts else "not-required",
    )


def mapping_warning(
    *,
    source: TrackIdentity,
    channel: int,
    bindings: Iterable[SoundBinding],
    allocation: DelayAllocation | None,
) -> dict[str, Any]:
    binding_list = list(bindings)
    return {
        "code": "SOLO_TRACK_MAPPING",
        "severity": "warning" if allocation and allocation.authorization == "explicit-delay-plan" else "info",
        "message": "Provjeri izvorni i ciljni track, kanal i vremenski SoundBinding prije Apply.",
        "source": {
            **source.to_manifest(),
            "channelIndex": channel,
            "channelNumber": channel + 1,
            "soundBindings": [item.to_manifest() for item in binding_list],
        },
        "target": allocation.to_manifest() if allocation else None,
    }