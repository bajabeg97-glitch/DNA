"""Arrangement Interaction Engine.

Band-level evidence layer: measures how instrument roles interact instead of
optimizing each track in isolation.  It is advisory/soft evidence only; hard
MIDI/device rules remain in CoreInvariantGuard and EvidenceAuthority.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from statistics import median
from typing import Any, Iterable, Mapping

from .midi import MidiFile, Note


@dataclass(frozen=True)
class RoleRegion:
    role: str
    track_index: int
    channel: int
    start_tick: int
    end_tick: int


ROLE_RELATIONS: dict[str, tuple[str, ...]] = {
    "drums": ("bass", "percussion"),
    "bass": ("drums", "rhythm-guitar", "power-riff"),
    "rhythm-guitar": ("bass", "power-riff", "solo", "terca"),
    "power-riff": ("rhythm-guitar", "bass", "solo"),
    "brass": ("solo", "strings", "pad"),
    "strings": ("solo", "brass", "pad", "choir"),
    "pad": ("solo", "strings", "brass", "choir"),
    "solo": ("terca", "echo", "brass", "strings", "pad", "rhythm-guitar", "power-riff"),
    "terca": ("solo", "echo"),
    "echo": ("solo", "terca"),
    "percussion": ("drums", "bass"),
}


def normalize_role(role: str) -> str:
    s=str(role or "").strip().lower().replace("_","-")
    aliases={"guitar":"rhythm-guitar","power":"power-riff","third":"terca","drum":"drums"}
    return aliases.get(s,s)


def _notes(midi: MidiFile, region: RoleRegion) -> list[Note]:
    return [n for n in midi.notes() if n.track==region.track_index and n.channel==region.channel
            and n.start < region.end_tick and n.end > region.start_tick]


def _onset_alignment(a: list[Note], b: list[Note], ppq: int) -> float:
    if not a or not b: return 0.0
    tol=max(1, round(ppq/24))
    bs=sorted(n.start for n in b)
    hits=0
    for n in a:
        if any(abs(n.start-x)<=tol for x in bs): hits+=1
    return hits/max(1,len(a))


def _register_overlap(a: list[Note], b: list[Note]) -> float:
    if not a or not b: return 0.0
    alo,ahi=min(n.pitch for n in a),max(n.pitch for n in a)
    blo,bhi=min(n.pitch for n in b),max(n.pitch for n in b)
    overlap=max(0,min(ahi,bhi)-max(alo,blo)+1)
    union=max(1,max(ahi,bhi)-min(alo,blo)+1)
    return overlap/union


def _temporal_overlap(a: list[Note], b: list[Note]) -> float:
    if not a or not b: return 0.0
    total=sum(max(1,n.end-n.start) for n in a)
    ov=0
    for x in a:
        for y in b:
            ov += max(0,min(x.end,y.end)-max(x.start,y.start))
    return min(1.0, ov/max(1,total))


def _density(notes: list[Note], ppq: int, start: int, end: int) -> float:
    beats=max(1e-9,(end-start)/max(1,ppq))
    return len(notes)/beats


def analyze_arrangement(midi: MidiFile, regions: Iterable[RoleRegion]) -> dict[str, Any]:
    regs=[RoleRegion(normalize_role(r.role),r.track_index,r.channel,r.start_tick,r.end_tick) for r in regions]
    rows=[]
    for i,a in enumerate(regs):
        na=_notes(midi,a)
        for b in regs[i+1:]:
            if a.end_tick<=b.start_tick or b.end_tick<=a.start_tick: continue
            nb=_notes(midi,b)
            if not na or not nb: continue
            start=max(a.start_tick,b.start_tick); end=min(a.end_tick,b.end_tick)
            align=_onset_alignment(na,nb,midi.ppq)
            reg=_register_overlap(na,nb)
            temp=_temporal_overlap(na,nb)
            rows.append({
                "roles":[a.role,b.role],
                "tracks":[a.track_index,b.track_index],
                "onsetAlignment":round(align,4),
                "registerOverlap":round(reg,4),
                "temporalOverlap":round(temp,4),
                "densityA":round(_density(na,midi.ppq,start,end),4),
                "densityB":round(_density(nb,midi.ppq,start,end),4),
            })
    return {"schema":"dna-arrangement-interaction","version":"1.0","regions":[asdict(r) for r in regs],"pairs":rows,
            "policy":"SOFT_MUSICAL_EVIDENCE_ONLY","hardAuthority":"CORE_INVARIANTS_AND_DEVICE_EVIDENCE"}


def target_interaction(report: Mapping[str, Any], role: str) -> dict[str, Any]:
    role=normalize_role(role)
    related=set(ROLE_RELATIONS.get(role,()))
    pairs=[]
    for row in report.get("pairs",[]):
        roles=row.get("roles",[])
        if role not in roles: continue
        other=roles[1] if roles[0]==role else roles[0]
        if related and other not in related: continue
        pairs.append((other,row))
    pocket=[]; collisions=[]; masking=[]
    for other,row in pairs:
        onset=float(row.get("onsetAlignment",0)); reg=float(row.get("registerOverlap",0)); temp=float(row.get("temporalOverlap",0))
        if {role,other}=={"bass","drums"}: pocket.append(onset)
        if reg>.45 and temp>.35: collisions.append({"with":other,"severity":round(reg*temp,4)})
        if role in {"solo","terca","echo"} and reg>.3 and temp>.45: masking.append({"with":other,"severity":round(reg*temp,4)})
    pocket_score=median(pocket) if pocket else None
    collision_penalty=min(1.0,sum(x["severity"] for x in collisions)*.6)
    masking_penalty=min(1.0,sum(x["severity"] for x in masking)*.7)
    score=max(0.0,min(1.0,.75 + (.2*(pocket_score if pocket_score is not None else .5)) - .35*collision_penalty - .4*masking_penalty))
    recommendations=[]
    if role=="bass" and pocket_score is not None and pocket_score<.35: recommendations.append("IMPROVE_KICK_BASS_POCKET")
    if collisions: recommendations.append("REDUCE_REGISTER_COLLISION_BY_VOICING_OR_SPACE")
    if masking: recommendations.append("PRESERVE_LEAD_SPACE")
    if role=="echo": recommendations.append("KEEP_ECHO_SPARSE_AND_BELOW_SOLO")
    if role=="terca": recommendations.append("FOLLOW_SOLO_AND_AVOID_LEAD_OVERRANK")
    return {"schema":"dna-target-interaction","version":"1.0","role":role,"score":round(score,4),
            "pocketScore":None if pocket_score is None else round(float(pocket_score),4),
            "collisions":collisions,"leadMasking":masking,"recommendations":recommendations,
            "hardGate":False,"policy":"ADVISORY_SOFT_EVIDENCE"}
