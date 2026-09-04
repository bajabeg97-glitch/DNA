"""Corpus-driven structural recovery gate for real-world bad MIDI.

This module intentionally separates *structural safety* from musical repair.
It can safely remove true orphan note-off events, but it does not invent note
lengths for same-tick note-on/off pairs or dangling notes. Those remain review
items unless a later role/device-aware repair has stronger evidence.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Any
from .midi import MidiFile, MidiEvent, MidiFormatError
from .overlap_recovery import audit_overlaps

@dataclass(frozen=True)
class StructuralIssue:
    kind: str
    track: int
    tick: int
    channel: int | None = None
    pitch: int | None = None
    safe_auto_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "track": self.track, "tick": self.tick,
            "channel": self.channel, "pitch": self.pitch,
            "safeAutoAction": self.safe_auto_action,
        }

@dataclass(frozen=True)
class StructuralAudit:
    issues: tuple[StructuralIssue, ...]
    note_on_count: int
    note_off_count: int

    @property
    def clean(self) -> bool:
        return not self.issues

    @property
    def blocks_musical_reconstruction(self) -> bool:
        return any(i.kind in {"NON_POSITIVE_DURATION", "DANGLING_NOTE_ON"} for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        from collections import Counter
        counts = Counter(i.kind for i in self.issues)
        return {
            "schema": "dna-structural-recovery-audit",
            "version": "4.45",
            "clean": self.clean,
            "blocksMusicalReconstruction": self.blocks_musical_reconstruction,
            "noteOnCount": self.note_on_count,
            "noteOffCount": self.note_off_count,
            "issueCounts": dict(counts),
            "issues": [i.to_dict() for i in self.issues],
            "policy": {
                "ORPHAN_NOTE_OFF": "SAFE_TO_REMOVE_ONLY_WHEN_NO_ACTIVE_MATCH",
                "NON_POSITIVE_DURATION": "BLOCK_AND_REVIEW_DO_NOT_INVENT_DURATION",
                "DANGLING_NOTE_ON": "BLOCK_AND_REVIEW_DO_NOT_INVENT_NOTE_OFF",
                "DRUM_SAME_TICK": "NEVER_SHIFT_BLINDLY",
            },
        }

def audit_structural(midi: MidiFile) -> StructuralAudit:
    issues: list[StructuralIssue] = []
    on_count = off_count = 0
    for ti, track in enumerate(midi.tracks):
        active: dict[tuple[int,int], list[MidiEvent]] = {}
        for e in sorted(track.events, key=lambda x:(x.tick,x.order)):
            if e.is_note_on:
                on_count += 1
                key=(e.channel or 0,e.note_number or 0)
                active.setdefault(key,[]).append(e)
            elif e.is_note_off:
                off_count += 1
                key=(e.channel or 0,e.note_number or 0)
                q=active.get(key)
                if not q:
                    issues.append(StructuralIssue("ORPHAN_NOTE_OFF",ti,e.tick,key[0],key[1],"REMOVE_EVENT"))
                    continue
                onset=q.pop(0)
                if e.tick <= onset.tick:
                    issues.append(StructuralIssue("NON_POSITIVE_DURATION",ti,e.tick,key[0],key[1],None))
        for (ch,pitch), q in active.items():
            for onset in q:
                issues.append(StructuralIssue("DANGLING_NOTE_ON",ti,onset.tick,ch,pitch,None))
    return StructuralAudit(tuple(issues),on_count,off_count)

def remove_true_orphan_noteoffs(midi: MidiFile) -> tuple[MidiFile, int]:
    """Remove only note-offs that have no active matching note-on at that point.

    Does not change note-on, valid note-off, controller, meta, bank/program, or
    same-tick paired events. The returned MIDI may still be blocked by the audit.
    """
    removed=0
    for track in midi.tracks:
        active: dict[tuple[int,int], list[MidiEvent]] = {}
        kept=[]
        for e in sorted(track.events,key=lambda x:(x.tick,x.order)):
            if e.is_note_on:
                key=(e.channel or 0,e.note_number or 0)
                active.setdefault(key,[]).append(e); kept.append(e)
            elif e.is_note_off:
                key=(e.channel or 0,e.note_number or 0)
                q=active.get(key)
                if not q:
                    removed += 1
                    continue
                q.pop(0); kept.append(e)
            else:
                kept.append(e)
        track.events=kept
    return midi, removed

def structural_recovery_report(raw: bytes) -> dict[str, Any]:
    try:
        midi=MidiFile.from_bytes(raw)
    except Exception as exc:
        return {"schema":"dna-structural-recovery-audit","version":"4.45","parseable":False,
                "blocksMusicalReconstruction":True,"error":f"{type(exc).__name__}: {exc}"}
    audit=audit_structural(midi)
    out=audit.to_dict(); out["parseable"]=True
    return out

def prepare_for_musical_reconstruction(raw: bytes) -> tuple[MidiFile, dict[str, Any]]:
    """Parse and apply only provably safe structural cleanup before music engines.

    True orphan note-offs are removed. Any non-positive duration or dangling
    note-on blocks the musical pipeline instead of guessing a repair.
    """
    midi=MidiFile.from_bytes(raw)
    initial=audit_structural(midi)
    removed=0
    if any(i.kind=="ORPHAN_NOTE_OFF" for i in initial.issues):
        midi, removed=remove_true_orphan_noteoffs(midi)
    final=audit_structural(midi)
    overlap = audit_overlaps(midi)
    report={"initial":initial.to_dict(),"safeRemovedOrphanNoteOffs":removed,"final":final.to_dict(),"overlapAudit":overlap,"repairOrder":["OVERLAP_CLASSIFICATION","PAIRING_RECOVERY","DURATION_REPAIR","CONTINUITY","MUSICAL_RECONSTRUCTION"]}
    if final.blocks_musical_reconstruction:
        counts=final.to_dict().get("issueCounts",{})
        raise MidiFormatError("Structural recovery required before musical reconstruction: "+str(counts))
    return midi, report
