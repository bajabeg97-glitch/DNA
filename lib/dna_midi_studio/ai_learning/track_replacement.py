from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import hashlib, json, math
import numpy as np

from dna_midi_studio.midi import MidiFile, Note
from dna_midi_studio.song_understanding import analyze_song_map
from .dataset import ROLE_VOCAB, METER_VOCAB, SECTION_VOCAB, SOURCE_VOCAB, _features, _tokenize_events
from .inference import NeuralInferenceEngine
from .inpainting import GenerateSelectEngine

ROLE_PATTERN = {
    "bass": ("GOLD", {"bass"}),
    "drums": ("GOLD", {"drums"}),
    "percussion": ("GOLD", {"percussion"}),
    "rhythm-guitar": ("FACTORY_STRUM", {"factory-strum"}),
    "power-riff": ("GOLD", {"power-riff", "riff"}),
    "accompaniment": ("GOLD", {"accompaniment"}),
}
ROLE_FACTORY = {
    "bass": "bass", "drums": "drums", "percussion": "drums",
    "rhythm-guitar": "chords", "power-riff": "chords", "accompaniment": "chords",
}
DEFAULT_REGISTER = {
    "bass": (36, 55), "rhythm-guitar": (48, 72), "power-riff": (40, 64),
    "accompaniment": (48, 72),
}

@dataclass(frozen=True)
class ReplacementRequest:
    track_index: int
    channel: int
    start_bar: int
    end_bar: int
    role: str
    def validate(self):
        if self.track_index < 0: raise ValueError("track_index must be >=0")
        if not 0 <= self.channel <= 15: raise ValueError("channel must be 0..15")
        if self.start_bar < 1 or self.end_bar < self.start_bar: raise ValueError("invalid bar range")
        if self.role not in ROLE_PATTERN: raise ValueError(f"REPLACE role not yet supported: {self.role}")

class FactoryVelocityProvider:
    """Factory-only new-note dynamics. GOLD is never inspected here."""
    def __init__(self, profiles_path: str|Path):
        d=json.loads(Path(profiles_path).read_text(encoding="utf-8")); self.profiles=d.get("profiles",[])
    def resolve(self, midi:MidiFile, req:ReplacementRequest, tick:int, pitch:int) -> dict:
        wanted=ROLE_FACTORY[req.role]; sound=midi.sound_at(req.channel,tick,req.track_index)
        candidates=[p for p in self.profiles if p.get("role")==wanted]
        exact=[]
        if sound:
            exact=[p for p in candidates if (p.get("bankMsb"),p.get("bankLsb"),p.get("program"))==sound]
        pool=exact or candidates
        if wanted=="drums":
            key=[p for p in pool if int((p.get("register") or {}).get("low",-1)) <= pitch <= int((p.get("register") or {}).get("high",128))]
            if key: pool=key
        if not pool: raise ValueError(f"No Factory velocity profile for role {wanted}")
        def support(p):
            curve=p.get("velocityCurve") or p.get("velocity_curve") or {}
            return float(curve.get("sampleCount") or p.get("notes") or 0)
        prof=max(pool,key=lambda p:(bool(exact),support(p),str(p.get("id",""))))
        vel=prof.get("velocity") or {}
        # deterministic musical intensity: downbeats slightly stronger, but values
        # are chosen only from the Factory curve.
        phase=(tick % max(1,midi.ppq*4))/max(1,midi.ppq*4)
        label="strong" if phase < .03 else ("highMid" if abs(phase-.5)<.04 else "optimal")
        value=int(vel.get(label,vel.get("optimal",vel.get("max",96))))
        return {"velocity":max(1,min(127,value)),"profileId":prof.get("id"),"exactSound":bool(exact),"curvePoint":label,
                "sound":list(sound) if sound else None,"authority":"FACTORY_ONLY"}

class TrackReplacementEngine:
    """Replace empty/bad accompaniment using retrieval -> neural variation -> Factory velocity -> MIDI hard checks."""
    def __init__(self, model_dir:str|Path, learning_data_dir:str|Path, data_dir:str|Path):
        self.neural=NeuralInferenceEngine(model_dir); self.selector=GenerateSelectEngine(self.neural)
        z=np.load(Path(learning_data_dir)/"learning_dataset_v1.npz")
        self.mean=np.asarray(z["feature_mean"],np.float32); self.std=np.asarray(z["feature_std"],np.float32)
        self.max_events=self.neural.cfg.max_events; self.data_dir=Path(data_dir)
        self.gold=json.loads((self.data_dir/"gold-performance-patterns.json").read_text(encoding="utf-8"))
        self.factory_strum=json.loads((self.data_dir/"factory-strumming.json").read_text(encoding="utf-8"))
        self.velocity=FactoryVelocityProvider(self.data_dir/"factory-velocity-profiles.json")
        # runtime authority guard
        def walk(v):
            if isinstance(v,dict):
                for k,x in v.items():
                    if "velocity" in str(k).lower(): raise ValueError("GOLD velocity contamination in replacement evidence")
                    walk(x)
            elif isinstance(v,list):
                for x in v: walk(x)
        walk(self.gold.get("patterns",[]))

    @staticmethod
    def _meter(song:dict,tick:int)->str:
        m=song["meterMap"][0]
        for x in song["meterMap"]:
            if int(x["tick"])<=tick:m=x
            else:break
        s=f'{m["numerator"]}/{m["denominator"]}'; return s if s in METER_VOCAB else "unknown"
    @staticmethod
    def _section(song:dict,tick:int)->str:
        raw="unknown"
        for s in song["sections"]:
            if int(s["startTick"])<=tick<int(s["endTick"]): raw=str(s.get("label","unknown")).lower(); break
        for x in SECTION_VOCAB[:-1]:
            if x in raw:return x
        return "unknown"
    @staticmethod
    def _tempo(song:dict,tick:int)->float:
        t=song["tempoMap"][0]
        for x in song["tempoMap"]:
            if int(x["tick"])<=tick:t=x
            else:break
        return float(t["bpm"])
    @staticmethod
    def _root(song:dict,tick:int)->int:
        for c in song["chordCells"]:
            if int(c["startTick"])<=tick<int(c["endTick"]):
                return int(c.get("root") if c.get("root") is not None else song["key"].get("root",0))
        return int(song["key"].get("root",0))

    def _retrieve(self, req:ReplacementRequest, song:dict, bar:dict, k:int=8)->list[tuple[float,dict,str]]:
        source,roles=ROLE_PATTERN[req.role]
        patterns=(self.factory_strum if source=="FACTORY_STRUM" else self.gold).get("patterns",[])
        meter=self._meter(song,int(bar["startTick"])); sec=self._section(song,int(bar["startTick"])); bpm=self._tempo(song,int(bar["startTick"]))
        rows=[]
        for p in patterns:
            if p.get("role") not in roles or p.get("meter")!=meter: continue
            # prefer patterns with enough evidence and close tempo/section, 1-bar first
            tr=p.get("tempoRange") or [bpm,bpm]; lo=float(tr[0]); hi=float(tr[-1])
            td=0 if lo<=bpm<=hi else min(abs(bpm-lo),abs(bpm-hi))
            section= str(p.get("sourceSection","unknown")).lower(); sm=1.0 if sec in section else 0.0
            length=abs(float(p.get("lengthBars",1))-1.0)
            occ=math.log1p(float(p.get("occurrences") or 1))
            score=3.0*sm + 1.2*occ - .025*td - .8*length + .02*float(p.get("density") or 0)
            rows.append((score,p,source))
        rows.sort(key=lambda x:(x[0],json.dumps(x[1].get("events",[])[:4],sort_keys=True)),reverse=True)
        if not rows: raise ValueError(f"No {source} pattern evidence for role={req.role}, meter={meter}")
        return rows[:k]

    def _seed_tensor(self,p:dict)->np.ndarray:
        return _tokenize_events(p,self.max_events)[None,:,:]
    def _context(self,p:dict,role:str,song:dict,bar:dict,source:str):
        f=(_features(p)-self.mean)/self.std
        return (f[None,:],np.array([ROLE_VOCAB.index(role)],np.int64),
                np.array([METER_VOCAB.index(self._meter(song,int(bar["startTick"])))],np.int64),
                np.array([SECTION_VOCAB.index(self._section(song,int(bar["startTick"])))],np.int64),
                np.array([SOURCE_VOCAB.index(source)],np.int64))
    @staticmethod
    def _symbolic_validator(events:np.ndarray,role:str)->dict:
        present=events[...,3].astype(bool)
        if not present.any(): return {"ok":False,"reason":"EMPTY_REPLACEMENT"}
        if np.any(events[...,0][present]<0)|np.any(events[...,0][present]>383):return {"ok":False,"reason":"POSITION_RANGE"}
        if np.any(events[...,1][present]<1)|np.any(events[...,1][present]>127):return {"ok":False,"reason":"DURATION_RANGE"}
        codes=events[...,2][present]
        drum=role in {"drums","percussion"}
        if drum and np.any((codes<128)|(codes>255)):return {"ok":False,"reason":"DRUM_GRAMMAR"}
        if not drum and np.any((codes<0)|(codes>127)):return {"ok":False,"reason":"PITCHED_GRAMMAR"}
        return {"ok":True,"reason":"REPLACEMENT_SYMBOLIC_PASS"}

    def _decode(self,midi:MidiFile,song:dict,req:ReplacementRequest,bar:dict,events:list)->tuple[list[Note],list[dict]]:
        bs,be=int(bar["startTick"]),int(bar["endTick"]); span=max(1,be-bs); drum=req.role in {"drums","percussion"}
        lo,hi=DEFAULT_REGISTER.get(req.role,(0,127)); notes=[]; proofs=[]
        for row in events:
            pos,dur,code,present=map(int,row)
            if not present: continue
            start=bs+round(pos*span/384); end=min(be,max(start+1,start+round(dur*midi.ppq/96)))
            if drum: pitch=code-128
            else:
                root=self._root(song,start); rel=code-64
                base=48+(root%12)+rel if req.role!="bass" else 36+(root%12)+rel
                while base<lo:base+=12
                while base>hi:base-=12
                pitch=base
            if not 0<=pitch<=127: continue
            v=self.velocity.resolve(midi,req,start,pitch)
            notes.append(Note(req.track_index,req.channel,pitch,start,end,v["velocity"],factory_profile_id=v["profileId"]))
            proofs.append(v)
        return notes,proofs

    @staticmethod
    def _protected(midi:MidiFile,track:int,channel:int,start:int,end:int)->list[tuple]:
        out=[]
        for ti,t in enumerate(midi.tracks):
            for e in t.events:
                target=(ti==track and e.kind=="channel" and e.channel==channel and (e.is_note_on or e.is_note_off) and start<=e.tick<=end)
                if not target: out.append((ti,e.tick,e.kind,e.status,e.meta_type,bytes(e.data)))
        return sorted(out,key=lambda x:(x[0],x[1],str(x[2]),-1 if x[3] is None else x[3],x[5]))

    def replace(self,midi_bytes:bytes,req:ReplacementRequest,n:int=8)->dict[str,Any]:
        req.validate(); midi=MidiFile.from_bytes(midi_bytes)
        if req.track_index>=len(midi.tracks):raise ValueError("track_index exceeds MIDI tracks")
        song=analyze_song_map(midi_bytes,"ai-track-replace.mid"); bars=song["bars"]
        if req.end_bar>len(bars):raise ValueError("bar range exceeds song")
        chosen=bars[req.start_bar-1:req.end_bar]; start=int(chosen[0]["startTick"]); end=int(chosen[-1]["endTick"])
        # Existing material in REPLACE scope is deliberately removable; everything else is protected.
        protected=self._protected(midi,req.track_index,req.channel,start,end)
        per_bar=[]
        for bar in chosen:
            retrieved=self._retrieve(req,song,bar,k=max(3,n)); variants=[]
            for rank,(retrieval_score,p,source) in enumerate(retrieved[:min(n,8)]):
                seed=self._seed_tensor(p); feat,role,meter,section,src=self._context(p,req.role,song,bar,source)
                present=np.flatnonzero(seed[0,:,3]); mask=np.zeros(seed.shape[:2],np.int64)
                # Let neural model alter interior third; GOLD/Factory seed remains the scaffold.
                if len(present)>2:
                    ids=present[1:-1:3]; mask[0,ids]=1
                g=self.selector.generate(feat,seed,role,meter,section,src,mask,n=1,
                    hard_validator=lambda ev,r=req.role:self._symbolic_validator(ev,r))
                cand=g["candidates"][0]
                if not cand["hard_valid"]: continue
                cand["retrievalScore"]=retrieval_score; cand["retrievalRank"]=rank; cand["evidenceSource"]=source
                cand["patternRole"]=p.get("role"); cand["patternSourceIds"]=(p.get("sourceIds") or [])[:8]
                variants.append(cand)
            if len(variants)<3: raise ValueError(f"fewer than 3 valid replacement candidates for bar {bar.get('bar')}")
            variants.sort(key=lambda c:(c["retrievalScore"]+c["score"],-c["retrievalRank"]),reverse=True)
            per_bar.append((bar,variants[:3]))
        outputs={}
        for rank,label in enumerate("ABC"):
            current=midi; path=[]; factory_ids=set(); exact_all=True
            for bar,variants in per_bar:
                cand=variants[rank]; notes,proofs=self._decode(current,song,req,bar,np.asarray(cand["events"],np.int64)[0].tolist())
                if not notes: raise ValueError("decoded replacement is empty")
                current=current.replace_notes(track_index=req.track_index,channel=req.channel,start_tick=int(bar["startTick"]),end_tick=int(bar["endTick"]),new_notes=notes)
                factory_ids.update(p["profileId"] for p in proofs if p.get("profileId")); exact_all &= all(p.get("exactSound") for p in proofs)
                path.append({"bar":bar.get("bar"),"retrievalRank":cand["retrievalRank"],"evidenceSource":cand["evidenceSource"],"notes":len(notes)})
            raw=current.to_bytes(); reparsed=MidiFile.from_bytes(raw); reparsed.notes()
            if self._protected(reparsed,req.track_index,req.channel,start,end)!=protected: raise ValueError("protected event changed during REPLACE")
            target=[x for x in reparsed.notes() if x.track==req.track_index and x.channel==req.channel and start<=x.start<end]
            if not target: raise ValueError("REPLACE produced no target notes")
            outputs[label]={"midiBytes":raw,"sha256":hashlib.sha256(raw).hexdigest(),"noteCount":len(target),"candidatePath":path,
                            "factoryVelocityProfileIds":sorted(factory_ids),"factorySoundExactForAll":exact_all,
                            "goldVelocityUsed":False,"neuralVelocityUsed":False,"protectedEventsPreserved":True}
        return {"schema":"dna-neural-track-replacement","version":"1.0","request":asdict(req),"sourceSha256":hashlib.sha256(midi_bytes).hexdigest(),
                "variants":outputs,"authority":{"pattern":"GOLD_OR_FACTORY_STRUM","velocity":"FACTORY_ONLY","neuralVelocityOutput":False},
                "status":"RENDERED_REPLACE_A_B_C_REQUIRES_FINAL_PRODUCTION_VALIDATOR"}
