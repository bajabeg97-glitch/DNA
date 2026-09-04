"""Role-aware same-pitch overlap/retrigger policy.

Overlap classification must happen before duration repair. Drum one-shot retriggers
are not treated like pitched legato/voice pairing defects.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
from .midi import MidiFile, MidiEvent

@dataclass(frozen=True)
class OverlapIssue:
    track:int; channel:int; pitch:int; previous_start:int; new_start:int; previous_off:int|None; classification:str; action:str
    def to_dict(self): return asdict(self)

DRUM_CHANNEL=9

def audit_overlaps(midi:MidiFile)->dict[str,Any]:
    issues=[]
    for ti,tr in enumerate(midi.tracks):
        active={}
        offs={}
        evs=sorted(tr.events,key=lambda e:(e.tick,e.order))
        for e in evs:
            if e.is_note_on:
                key=(e.channel or 0,e.note_number or 0)
                q=active.setdefault(key,[])
                if q:
                    prev=q[-1]
                    if key[0]==DRUM_CHANNEL:
                        cls="DRUM_ONE_SHOT_RETRIGGER"; action="PRESERVE_EVENT_SEMANTICS_DO_NOT_GENERIC_TRIM"
                    elif e.tick==prev.tick:
                        cls="PITCHED_SAME_TICK_RETRIGGER"; action="REVIEW_DUPLICATE_ZERO_PAIR_BEFORE_DURATION_REPAIR"
                    else:
                        cls="PITCHED_SAME_NOTE_OVERLAP"; action="RESOLVE_PAIRING_THEN_ROLE_AWARE_GATE_REPAIR"
                    issues.append(OverlapIssue(ti,key[0],key[1],prev.tick,e.tick,None,cls,action))
                q.append(e)
            elif e.is_note_off:
                key=(e.channel or 0,e.note_number or 0); q=active.get(key) or []
                if q: q.pop(0)
    from collections import Counter
    c=Counter(i.classification for i in issues)
    return {"schema":"dna-overlap-recovery-audit","version":"4.46.0","counts":dict(c),"issues":[i.to_dict() for i in issues],
            "policyOrder":["CLASSIFY_OVERLAP","RESOLVE_PAIRING","DURATION_REPAIR","CONTINUITY","MUSICAL_RECONSTRUCTION"]}

def overlap_blocks_blind_duration_repair(report:dict[str,Any])->bool:
    return any(k.startswith("PITCHED_") and v>0 for k,v in (report.get("counts") or {}).items())
