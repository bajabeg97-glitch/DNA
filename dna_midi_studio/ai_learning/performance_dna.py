from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import hashlib, json, math
import numpy as np
from dna_midi_studio.instrument_performance_grammar import score_semantic_functions, performance_intent

SUPPORTED_ROLES = {"power-riff", "bass", "rhythm-guitar"}
SOURCE_PRIORITY = {"KORG_RULE": 5.0, "FACTORY_STRUM": 4.0, "GOLD": 3.0, "LEARNED": 2.0, "HEURISTIC": 1.0}

@dataclass(frozen=True)
class PerformanceEvent:
    position: int
    duration: int
    relative_pitch: int
    function: str
    articulation: str
    phrase_position: float
    chord_position: float

@dataclass(frozen=True)
class PerformanceDNA:
    id: str
    role: str
    meter: str
    source: str
    source_ids: tuple[str, ...]
    tempo_min: float
    tempo_max: float
    section: str
    occurrences: int
    confidence: float
    quality: float
    events: tuple[PerformanceEvent, ...]
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        d=asdict(self); d["events"]=[asdict(x) for x in self.events]; return d


def _event_function(role:str, rel:int, pos:int, dur:int)->str:
    if role=="power-riff":
        if abs(rel) in {0,12}: return "ROOT_OCTAVE"
        if abs(rel)%12==7: return "FIFTH"
        return "COLOR"
    if role=="bass":
        pc=rel%12
        if pc==0:return "ROOT"
        if pc in {3,4}:return "THIRD"
        if pc==7:return "FIFTH"
        if abs(rel)>=11:return "OCTAVE_OR_APPROACH"
        return "PASSING"
    return "STRUM_TONE"


def _articulation(dur:int)->str:
    if dur<=8:return "STACCATO"
    if dur<=24:return "SHORT"
    if dur>=72:return "SUSTAIN"
    return "NORMAL"


def _fingerprint(events:list[PerformanceEvent])->str:
    payload=[(e.position,e.duration,e.relative_pitch,e.function) for e in events]
    return hashlib.sha256(json.dumps(payload,separators=(",",":")).encode()).hexdigest()

class PerformanceDNAEngine:
    """Evidence-driven generation of new instrument performances.

    It transfers rhythmic/gate/voicing behaviour, not literal source pitches.
    GOLD is never used for velocity. Rhythm-guitar prioritizes Factory strumming.
    """
    VERSION="4.42.0"
    def __init__(self, data_dir:str|Path):
        self.data_dir=Path(data_dir)
        self.gold=json.loads((self.data_dir/"gold-performance-patterns.json").read_text(encoding="utf-8"))
        self.factory=json.loads((self.data_dir/"factory-strumming.json").read_text(encoding="utf-8"))
        self.library=self._build_library()

    def _build_library(self)->dict[str,list[PerformanceDNA]]:
        out={r:[] for r in SUPPORTED_ROLES}
        rows=[("GOLD",p) for p in self.gold.get("patterns",[]) if p.get("role") in {"power-riff","riff","bass"}]
        rows += [("FACTORY_STRUM",p) for p in self.factory.get("patterns",[]) if p.get("role")=="factory-strum"]
        for source,p in rows:
            role="rhythm-guitar" if source=="FACTORY_STRUM" else ("power-riff" if p.get("role") in {"power-riff","riff"} else "bass")
            res=max(1,int(p.get("timingResolution") or 96)); ev=[]
            raw=p.get("events") or p.get("notes") or []
            for row in raw:
                if not isinstance(row,(list,tuple)) or len(row)<3: continue
                pos=max(0,min(383,round(float(row[0])*96/res)))
                dur=max(1,min(127,round(float(row[1])*96/res)))
                rel=int(round(float(row[2])))
                # Factory strumming may contain extra columns; third column is chord-relative pitch token.
                ev.append(PerformanceEvent(pos,dur,rel,_event_function(role,rel,pos,dur),_articulation(dur),pos/384.0,(pos%96)/96.0))
            if not ev: continue
            tr=p.get("tempoRange") or [0,300]; tr=(list(tr)+[tr[-1] if tr else 300])[:2]
            dna=PerformanceDNA(str(p.get("id","")),role,str(p.get("meter","unknown")),source,tuple((p.get("sourceIds") or [])[:8]),float(tr[0]),float(tr[1]),str(p.get("sourceSection","unknown")).lower(),int(p.get("occurrences") or 1),float(p.get("confidence") or 0),float(p.get("qualityScore") or 0),tuple(ev),_fingerprint(ev))
            out[role].append(dna)
        for role in out:
            out[role].sort(key=lambda d:(d.occurrences,d.quality,d.confidence,d.id),reverse=True)
        return out

    def evidence_summary(self)->dict[str,Any]:
        return {"version":self.VERSION,"roles":{r:{"patterns":len(v),"events":sum(len(x.events) for x in v),"sources":sorted(set(x.source for x in v))} for r,v in self.library.items()},"velocityAuthority":"FACTORY_ONLY","goldVelocityUsed":False}

    def retrieve(self, role:str, meter:str, bpm:float, section:str="unknown", transition_strength:float=0.0, k:int=12, phrase_position:float=0.5, section_energy:float=0.5, arrangement_score:float=0.5)->list[tuple[float,PerformanceDNA]]:
        if role not in SUPPORTED_ROLES: raise ValueError(f"unsupported role {role}")
        rows=[]
        for d in self.library[role]:
            if d.meter!=meter: continue
            td=0.0 if d.tempo_min<=bpm<=d.tempo_max else min(abs(bpm-d.tempo_min),abs(bpm-d.tempo_max))
            sec=1.0 if section and section!="unknown" and section in d.section else 0.0
            trans=1.0 if transition_strength>=0.18 and any(x in d.section for x in ("transition","fill","ending")) else 0.0
            support=math.log1p(max(1,d.occurrences))
            source=SOURCE_PRIORITY[d.source]
            grammar = score_semantic_functions(role, (e.function for e in d.events), phrase_position=phrase_position,
                                               transition_strength=transition_strength, section_energy=section_energy)
            grammar_score=float(grammar["score"])
            score=1.8*source+1.4*support+1.25*sec+1.0*trans-0.025*td+0.35*d.quality+0.2*d.confidence+1.15*grammar_score+0.45*max(0.0,min(1.0,float(arrangement_score)))
            rows.append((score,d))
        rows.sort(key=lambda x:(x[0],x[1].id),reverse=True)
        return rows[:k]

    @staticmethod
    def _variant_transform(events:list[PerformanceEvent],variant:int)->list[PerformanceEvent]:
        # Preserve performance identity but create deterministic variation without literal copying.
        out=[]
        for i,e in enumerate(events):
            pos=e.position; dur=e.duration; rel=e.relative_pitch
            if variant==1 and i%5==2:
                dur=max(1,round(dur*.88))
            elif variant==2 and i%4==1:
                # octave variant only for root/fifth-like pitched material; rhythm remains intact
                if e.function in {"ROOT","FIFTH","ROOT_OCTAVE"}: rel += 12 if rel<12 else -12
                dur=max(1,round(dur*.95))
            out.append(PerformanceEvent(pos,dur,rel,e.function,e.articulation,e.phrase_position,e.chord_position))
        return out

    @staticmethod
    def _anti_copy(events:list[PerformanceEvent],source_fp:str,variant:int)->dict[str,Any]:
        fp=_fingerprint(events)
        exact=fp==source_fp
        # variant 0 may preserve rhythm/voicing DNA, but provenance prevents raw MIDI copy; variants 1/2 must differ.
        allowed=not exact or variant==0
        return {"sourceFingerprint":source_fp,"generatedFingerprint":fp,"exactPerformanceFingerprint":exact,"allowed":allowed,"policy":"NO_LITERAL_SOURCE_MIDI_COPY"}

    def generate_pattern(self, role:str, meter:str, bpm:float, section:str="unknown", transition_strength:float=0.0, variant:int=0, phrase_position:float=0.5, section_energy:float=0.5, arrangement_score:float=0.5)->dict[str,Any]:
        retrieved=self.retrieve(role,meter,bpm,section,transition_strength,k=12,phrase_position=phrase_position,section_energy=section_energy,arrangement_score=arrangement_score)
        if not retrieved: raise ValueError(f"no performance DNA evidence for {role} {meter}")
        # diversify A/B/C by selecting among top evidence rows as well as transforming events
        pick=min(max(0,variant),min(2,len(retrieved)-1)); score,dna=retrieved[pick]
        ev=self._variant_transform(list(dna.events),variant)
        anti=self._anti_copy(ev,dna.fingerprint,variant)
        if not anti["allowed"]: raise ValueError("anti-copy gate rejected generated performance")
        # TrackReplacement token format: relative pitch encoded +64; no velocity field.
        tokens=[[e.position,e.duration,max(0,min(127,e.relative_pitch+64)),1] for e in ev]
        grammar = score_semantic_functions(role, (e.function for e in ev), phrase_position=phrase_position,
                                           transition_strength=transition_strength, section_energy=section_energy)
        return {"schema":"dna-performance-generator","version":self.VERSION,"role":role,"events":[tokens],"evidenceSource":"PERFORMANCE_DNA_V1","evidenceScore":score,"performanceDNAId":dna.id,"performanceSource":dna.source,"patternSourceIds":list(dna.source_ids),"antiCopy":anti,"goldVelocityUsed":False,"neuralVelocityUsed":False,"velocityAuthority":"FACTORY_ONLY","performanceFunctions":sorted(set(e.function for e in ev)),"transitionStrength":float(transition_strength),"phrasePosition":float(phrase_position),"sectionEnergy":float(section_energy),"grammarScore":grammar["score"],"performanceIntent":grammar["intent"],"performanceGrammarVersion":"2.0","arrangementInteractionScore":round(max(0.0,min(1.0,float(arrangement_score))),4),"arrangementInteractionPolicy":"SOFT_EVIDENCE_ONLY"}
