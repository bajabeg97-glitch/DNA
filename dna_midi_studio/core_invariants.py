"""Non-negotiable source/device transformation invariants.

This guard is deliberately narrower than musical taste.  Density, register
preference, gate, voicing, syncopation and complexity are NOT hard rules.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
import json
from typing import Any

from .midi import MidiFile, Note

# Channel messages that describe sound identity or RPN/NRPN addressing/data.
_PROTECTED_CC = {0, 32, 6, 38, 98, 99, 100, 101}
# Meta types: sequence number, text/copyright/name/instrument/lyrics/marker/cue,
# channel prefix, port, tempo, SMPTE, time signature, key signature, sequencer.
# End-of-track 0x2F is intentionally excluded: it is a serialization boundary.
_PROTECTED_META = {0x00,0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x20,0x21,0x51,0x54,0x58,0x59,0x7F}


def _event_key(event: Any) -> tuple[Any, ...] | None:
    if event.kind == "sysex":
        return ("sysex", event.tick, bytes(event.data))
    if event.kind == "meta" and event.meta_type in _PROTECTED_META:
        return ("meta", event.tick, event.meta_type, bytes(event.data))
    if event.kind == "channel":
        if event.command == 0xC0:  # Program Change
            return ("pc", event.tick, event.channel, bytes(event.data))
        if event.command == 0xB0 and len(event.data) >= 2 and event.data[0] in _PROTECTED_CC:
            return ("cc", event.tick, event.channel, bytes(event.data))
    return None


def _protected_events(midi: MidiFile) -> tuple[tuple[Any, ...], ...]:
    out=[]
    for ti, track in enumerate(midi.tracks):
        for e in track.events:
            k=_event_key(e)
            if k is not None:
                out.append((ti,)+k)
    return tuple(sorted(out, key=repr))


def _outside_notes(midi: MidiFile, track_index: int, channel: int, start: int, end: int) -> tuple[tuple[int,...], ...]:
    out=[]
    for n in midi.notes():
        targeted = n.track == track_index and n.channel == channel and n.start < end and n.end > start
        if not targeted:
            out.append((n.track,n.channel,n.pitch,n.start,n.end,n.velocity))
    return tuple(sorted(out))


@dataclass(frozen=True)
class CoreSnapshot:
    format_type: int
    ppq: int
    protected_events: tuple[tuple[Any,...], ...]
    outside_notes: tuple[tuple[int,...], ...]
    track_index: int
    channel: int
    start_tick: int
    end_tick: int
    allow_new_outside_notes: bool
    digest: str


def capture(midi: MidiFile, *, track_index: int, channel: int, start_tick: int, end_tick: int, allow_new_outside_notes: bool = False) -> CoreSnapshot:
    protected=_protected_events(midi)
    outside=_outside_notes(midi,track_index,channel,start_tick,end_tick)
    payload=(midi.format_type,midi.ppq,protected,outside,track_index,channel,start_tick,end_tick,allow_new_outside_notes)
    return CoreSnapshot(midi.format_type,midi.ppq,protected,outside,track_index,channel,start_tick,end_tick,allow_new_outside_notes,
                        sha256(repr(payload).encode()).hexdigest())


def verify(snapshot: CoreSnapshot, midi: MidiFile) -> dict[str, Any]:
    issues=[]
    if midi.format_type != snapshot.format_type: issues.append("MIDI_FORMAT_CHANGED")
    if midi.ppq != snapshot.ppq: issues.append("PPQ_CHANGED")
    if _protected_events(midi) != snapshot.protected_events: issues.append("PROTECTED_EVENT_CHANGED")
    after_outside = _outside_notes(midi,snapshot.track_index,snapshot.channel,snapshot.start_tick,snapshot.end_tick)
    if snapshot.allow_new_outside_notes:
        # Existing source notes must survive exactly; new relationship layers may be added.
        from collections import Counter
        if Counter(snapshot.outside_notes) - Counter(after_outside):
            issues.append("OUTSIDE_TARGET_SOURCE_NOTE_CHANGED")
    elif after_outside != snapshot.outside_notes:
        issues.append("OUTSIDE_TARGET_NOTE_CHANGED")
    # Reparse through canonical writer proves note pairing / stream validity.
    try:
        MidiFile.from_bytes(midi.to_bytes())
    except Exception:
        issues.append("MIDI_REPARSE_FAILED")
    return {
        "schema":"dna-core-invariant-verification","version":"1.0",
        "passed": not issues,"issues":issues,"snapshotHash":snapshot.digest,
        "hardRules":[
            "FORMAT_PPQ_PRESERVED","PROTECTED_EVENTS_PRESERVED",
            "OUTSIDE_TARGET_REGION_PRESERVED","CANONICAL_REPARSE_REQUIRED",
            "FACTORY_ONLY_FINAL_VELOCITY_AUTHORITY_IS_SEPARATE_DEVICE_RULE",
        ],
        "notHardRules":["density","register-preference","gate-style","voicing","syncopation","complexity"],
    }
