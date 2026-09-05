from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable
import json, math
import numpy as np

from dna_midi_studio.midi import MidiFile, MidiEvent, MidiTrack, Note
from dna_midi_studio.song_understanding import analyze_song_map
from .dataset import ROLE_VOCAB, METER_VOCAB, SECTION_VOCAB, SOURCE_VOCAB
from .inpainting import GenerateSelectEngine
from .inference import NeuralInferenceEngine


@dataclass(frozen=True)
class SongRegionRequest:
    track_index: int
    channel: int
    start_bar: int
    end_bar: int
    role: str
    mode: str = "REPAIR"  # REPAIR may preserve original velocity; REPLACE needs Factory velocity authority.

    def validate(self) -> None:
        if self.track_index < 0: raise ValueError("track_index must be >= 0")
        if not 0 <= self.channel <= 15: raise ValueError("channel must be 0..15")
        if self.start_bar < 1 or self.end_bar < self.start_bar: raise ValueError("invalid bar range")
        if self.role not in ROLE_VOCAB: raise ValueError(f"unsupported neural role: {self.role}")
        if self.mode not in {"REPAIR","REPLACE"}: raise ValueError("mode must be REPAIR or REPLACE")


class SongConditionedInpaintingEngine:
    """Bridge a real MIDI song region into the neural inpainting model.

    Musical authority remains outside the network.  Chord roots are used only to
    express pitched notes as chord-relative offsets.  Source velocity is never fed
    to the model.  REPAIR writes back original velocities exactly; REPLACE is
    blocked unless a Factory velocity provider is supplied by the deterministic
    production engine.
    """
    def __init__(self, model_dir: str|Path, learning_data_dir: str|Path):
        self.neural=NeuralInferenceEngine(model_dir)
        self.selector=GenerateSelectEngine(self.neural)
        z=np.load(Path(learning_data_dir)/"learning_dataset_v1.npz")
        self.feature_mean=np.asarray(z["feature_mean"],dtype=np.float32)
        self.feature_std=np.asarray(z["feature_std"],dtype=np.float32)
        self.max_events=self.neural.cfg.max_events

    @staticmethod
    def _bar_window(song_map: dict, start_bar: int, end_bar: int) -> tuple[int,int,list[dict]]:
        bars=song_map["bars"]
        if end_bar > len(bars): raise ValueError("requested bar range exceeds song")
        chosen=bars[start_bar-1:end_bar]
        return int(chosen[0]["startTick"]), int(chosen[-1]["endTick"]), chosen

    @staticmethod
    def _root_for_tick(song_map: dict, tick: int) -> int:
        for cell in song_map["chordCells"]:
            if int(cell["startTick"]) <= tick < int(cell["endTick"]):
                root=cell.get("root")
                return int(root) if root is not None else int(song_map["key"].get("root",0))
        return int(song_map["key"].get("root",0))

    @staticmethod
    def _meter_index(song_map: dict, tick: int) -> int:
        meter=song_map["meterMap"][0]
        for item in song_map["meterMap"]:
            if int(item["tick"]) <= tick: meter=item
            else: break
        label=f'{meter["numerator"]}/{meter["denominator"]}'
        return METER_VOCAB.index(label if label in METER_VOCAB else "unknown")

    @staticmethod
    def _section_index(song_map: dict, tick: int) -> int:
        label="unknown"
        for section in song_map["sections"]:
            if int(section["startTick"]) <= tick < int(section["endTick"]):
                label=str(section.get("label","unknown")).lower(); break
        # map verbose labels conservatively by substring
        for candidate in SECTION_VOCAB[:-1]:
            if candidate in label: return SECTION_VOCAB.index(candidate)
        return SECTION_VOCAB.index("unknown")

    def _notes(self, midi: MidiFile, request: SongRegionRequest, start_tick:int, end_tick:int) -> list[Note]:
        return [n for n in midi.notes() if n.track==request.track_index and n.channel==request.channel
                and start_tick <= n.start < end_tick]

    def _encode_bar(self, song_map:dict, midi:MidiFile, request:SongRegionRequest, start_tick:int,end_tick:int):
        notes=self._notes(midi,request,start_tick,end_tick)
        if not notes: raise ValueError("target song region contains no notes; REPLACE-from-empty requires a retrieval seed")
        ppq=midi.ppq; span=max(1,end_tick-start_tick)
        E=np.zeros((1,self.max_events,4),dtype=np.int64)
        drum=request.role in {"drums","percussion"}
        for i,n in enumerate(notes[:self.max_events]):
            pos=max(0,min(383,round((n.start-start_tick)*384/span)))
            dur=max(1,min(127,round((n.end-n.start)*96/ppq)))
            if drum: pitch=max(128,min(255,128+n.pitch))
            else:
                root=self._root_for_tick(song_map,n.start)
                rel=((n.pitch-root+64)%128)-64
                pitch=max(0,min(127,rel+64))
            E[0,i]=(pos,dur,pitch,1)
        # Neural features deliberately exclude velocity.
        starts=np.array([n.start for n in notes],dtype=float)
        pitches=np.array([n.pitch for n in notes],dtype=float)
        gates=np.array([(n.end-n.start)/ppq for n in notes],dtype=float)
        pos=((starts-start_tick)/span) if len(starts) else np.array([0.])
        density=len(notes)/max(1,span/ppq)
        sync=float(np.mean(np.abs(pos*16-np.round(pos*16)))) if len(pos) else 0.0
        feat=np.array([density,float(np.ptp(pitches)) if len(pitches)>1 else 0.0,float(np.mean(pitches)),
                       float(np.median(gates)),sync,float(pos.min()>.08),float(pos.max()<.92),
                       math.log1p(1),1.0,1.0,math.log1p(len(notes)),0.0,0.0,1.0],dtype=np.float32)
        feat=(feat-self.feature_mean)/self.feature_std
        role=np.array([ROLE_VOCAB.index(request.role)],dtype=np.int64)
        meter=np.array([self._meter_index(song_map,start_tick)],dtype=np.int64)
        section=np.array([self._section_index(song_map,start_tick)],dtype=np.int64)
        source=np.array([SOURCE_VOCAB.index("GOLD")],dtype=np.int64)
        return feat[None,:],E,role,meter,section,source,notes

    def _decode_candidate(self,song_map:dict,request:SongRegionRequest,start_tick:int,end_tick:int,
                          original_notes:list[Note], encoded:list) -> list[dict]:
        span=max(1,end_tick-start_tick); result=[]
        for i,row in enumerate(encoded):
            if i>=len(original_notes) or int(row[3])==0: break
            pos,dur,code,_=map(int,row)
            tick=start_tick+round(pos*span/384); duration=max(1,round(dur*MidiFile.from_bytes.__func__.__defaults__[0])) if False else None
            # duration token is in 1/96 quarter-note units; ppq is attached later.
            original=original_notes[i]
            pitch=code-128 if request.role in {"drums","percussion"} else self._root_for_tick(song_map,tick)+(code-64)
            result.append({"index":i,"tick":tick,"durationCode":dur,"pitch":max(0,min(127,pitch)),
                           "velocity":original.velocity,"sourceVelocityPreserved":True})
        return result

    @staticmethod
    def _local_hard_validator(base:np.ndarray, mask:np.ndarray, events:np.ndarray, role:str) -> dict:
        if events.shape != base.shape: return {"ok":False,"reason":"SHAPE_CHANGED"}
        active=mask.astype(bool) & base[...,3].astype(bool)
        if np.any(events[~active] != base[~active]): return {"ok":False,"reason":"UNMASKED_EVENT_MUTATED"}
        present=events[...,3].astype(bool)
        if np.any(events[...,0][present] < 0) or np.any(events[...,0][present] > 383): return {"ok":False,"reason":"POSITION_RANGE"}
        if np.any(events[...,1][present] < 1) or np.any(events[...,1][present] > 127): return {"ok":False,"reason":"DURATION_RANGE"}
        codes=events[...,2][present]
        if role in {"drums","percussion"} and np.any((codes<128)|(codes>255)): return {"ok":False,"reason":"DRUM_CODE_RANGE"}
        if role not in {"drums","percussion"} and np.any((codes<0)|(codes>127)): return {"ok":False,"reason":"RELATIVE_PITCH_RANGE"}
        return {"ok":True,"reason":"LOCAL_SYMBOLIC_CONTRACT_PASS"}

    def analyze_and_generate(self,midi_bytes:bytes,request:SongRegionRequest,mask_ratio:float=.35,n:int=8) -> dict[str,Any]:
        request.validate(); midi=MidiFile.from_bytes(midi_bytes)
        if request.track_index >= len(midi.tracks): raise ValueError("track_index exceeds MIDI track count")
        song_map=analyze_song_map(midi_bytes,"neural-song-region.mid")
        start,end,bars=self._bar_window(song_map,request.start_bar,request.end_bar)
        # Process one bar at a time to match training context and avoid positional compression.
        per_bar=[]
        for bar in bars:
            bs,be=int(bar["startTick"]),int(bar["endTick"])
            feat,E,role,meter,section,source,notes=self._encode_bar(song_map,midi,request,bs,be)
            present=np.flatnonzero(E[0,:,3])
            count=max(1,round(len(present)*max(.05,min(.95,mask_ratio))))
            # deterministic center-biased mask: preserve phrase boundaries, repair interior first
            order=sorted(present,key=lambda i:(abs(i-(len(present)-1)/2),i))[:count]
            mask=np.zeros(E.shape[:2],dtype=np.int64); mask[0,order]=1
            result=self.selector.generate(feat,E,role,meter,section,source,mask,n=n,
                hard_validator=lambda ev,b=E,m=mask,r=request.role:self._local_hard_validator(b,m,ev,r))
            per_bar.append({"bar":int(bar.get("bar",request.start_bar+len(per_bar))),"startTick":bs,"endTick":be,
                            "maskedEventIndexes":[int(x) for x in order],"sourceNoteCount":len(notes),"selection":result})
        return {"schema":"dna-song-neural-inpainting","version":"1.0","request":asdict(request),
                "sourceSha256":song_map["sourceSha256"],"songMapHash":song_map["mapHash"],
                "velocityUsedByNeuralModel":False,"factoryVelocityRequiredForNewNotes":True,
                "bars":per_bar,"status":"ADVISORY_CANDIDATES_REQUIRE_PRODUCTION_MIDI_RENDER_AND_FINAL_HARD_VALIDATION"}

    def _render_bar_candidate(self, midi:MidiFile, song_map:dict, request:SongRegionRequest,
                              bar:dict, candidate:dict) -> MidiFile:
        if request.mode != "REPAIR":
            raise ValueError("Direct neural render currently supports REPAIR only; REPLACE requires retrieval seed + Factory velocity provider")
        bs,be=int(bar["startTick"]),int(bar["endTick"])
        notes=self._notes(midi,request,bs,be)
        encoded=np.asarray(candidate["events"],dtype=np.int64)[0]
        mask=set(int(i) for i in bar["maskedEventIndexes"])
        if not mask: return midi
        target_track=midi.tracks[request.track_index]
        by_order={e.order:e for e in target_track.events}
        replacement={}
        for i in sorted(mask):
            if i>=len(notes) or i>=len(encoded) or int(encoded[i,3])==0: continue
            src=notes[i]; row=encoded[i]
            start=bs+round(int(row[0])*(be-bs)/384)
            duration=max(1,round(int(row[1])*midi.ppq/96))
            end=min(be,max(start+1,start+duration))
            if request.role in {"drums","percussion"}:
                pitch=int(row[2])-128
            else:
                root=self._root_for_tick(song_map,start); pitch=root+(int(row[2])-64)
                # Bring chord-relative result into the closest octave to the source note.
                while pitch-src.pitch>6: pitch-=12
                while src.pitch-pitch>6: pitch+=12
            pitch=max(0,min(127,pitch))
            on=by_order.get(src.on_order); off=by_order.get(src.off_order)
            if on is None or off is None: raise ValueError("source note event identity missing during render")
            replacement[src.on_order]=MidiEvent(start,on.order,on.kind,on.status,bytes((pitch,src.velocity)),on.meta_type)
            replacement[src.off_order]=MidiEvent(end,off.order,off.kind,off.status,bytes((pitch,0)),off.meta_type)
        tracks=[]
        for ti,track in enumerate(midi.tracks):
            if ti != request.track_index: tracks.append(MidiTrack(list(track.events))); continue
            events=[replacement.get(e.order,e) for e in track.events]
            events.sort(key=lambda e:(e.tick,e.order))
            tracks.append(MidiTrack(events))
        return MidiFile(midi.format_type,midi.ppq,tracks)

    @staticmethod
    def _protected_event_fingerprint(midi:MidiFile, target_track:int, target_channel:int, start_tick:int, end_tick:int) -> list[tuple]:
        # Event order is a parser-local identity and may be renumbered after a
        # serialize/parse round-trip. Compare stable MIDI semantics instead.
        rows=[]
        for ti,track in enumerate(midi.tracks):
            for e in track.events:
                mutable_note=(ti==target_track and e.kind=="channel" and e.channel==target_channel
                              and (e.is_note_on or e.is_note_off) and start_tick <= e.tick <= end_tick)
                if mutable_note: continue
                rows.append((ti,e.tick,e.kind,e.status,e.meta_type,bytes(e.data)))
        return sorted(rows,key=lambda x:(x[0],x[1],str(x[2]),-1 if x[3] is None else x[3],-1 if x[4] is None else x[4],x[5]))

    def render_selected_variants(self,midi_bytes:bytes,request:SongRegionRequest,mask_ratio:float=.35,n:int=8) -> dict[str,Any]:
        """Produce actual MIDI bytes for selected A/B/C REPAIR candidates.

        Rendering is deliberately conservative: each selected candidate is applied
        independently to the immutable source, target note velocities are preserved,
        and every non-target MIDI event must remain byte-semantically identical.
        """
        if request.mode != "REPAIR":
            raise ValueError("REPLACE render is blocked until retrieval seed and Factory velocity authority are supplied")
        analysis=self.analyze_and_generate(midi_bytes,request,mask_ratio,n)
        if len(analysis["bars"]) != 1:
            raise ValueError("render_selected_variants currently renders one bar per transaction")
        midi=MidiFile.from_bytes(midi_bytes); song_map=analyze_song_map(midi_bytes,"neural-render.mid")
        bar=analysis["bars"][0]; selections=bar["selection"]["selected"]
        candidates={c["name"]:c for c in bar["selection"]["candidates"]}
        src_notes=self._notes(midi,request,int(bar["startTick"]),int(bar["endTick"]))
        protected=self._protected_event_fingerprint(midi,request.track_index,request.channel,int(bar["startTick"]),int(bar["endTick"]))
        source_velocities=[n.velocity for n in src_notes]
        rendered={}
        for name in selections:
            cand=candidates[name]
            out=self._render_bar_candidate(midi,song_map,request,bar,cand)
            # Parse again: writer/parser invariants must hold.
            raw=out.to_bytes(); reparsed=MidiFile.from_bytes(raw); reparsed.notes()
            if self._protected_event_fingerprint(reparsed,request.track_index,request.channel,int(bar["startTick"]),int(bar["endTick"])) != protected:
                raise ValueError("protected MIDI event changed during neural render")
            out_notes=self._notes(reparsed,request,int(bar["startTick"]),int(bar["endTick"]))
            # Existing event velocity is Factory/source authority, never neural output.
            if len(out_notes)!=len(src_notes): raise ValueError("REPAIR changed target note count")
            if [x.velocity for x in out_notes] != source_velocities:
                raise ValueError("neural render changed velocity")
            rendered[name]={"midiBytes":raw,"sha256":__import__('hashlib').sha256(raw).hexdigest(),
                            "changedEvents":cand["changed_events"],"velocityPreserved":True,
                            "protectedEventsPreserved":True,"hardValid":cand["hard_valid"]}
        return {"analysis":analysis,"variants":rendered,"status":"RENDERED_REPAIR_VARIANTS_REQUIRE_EXISTING_FINAL_PRODUCTION_VALIDATOR"}

    def render_phrase_variants(self,midi_bytes:bytes,request:SongRegionRequest,mask_ratio:float=.35,n:int=8) -> dict[str,Any]:
        """Render deterministic A/B/C variants across one or more bars.

        Each bar is generated independently from the immutable source analysis, then
        candidate rank 0/1/2 is applied transactionally across the requested phrase.
        This keeps the current v2 model aligned with its one-pattern/bar training
        context while exposing phrase-level output to the user.
        """
        if request.mode != "REPAIR":
            raise ValueError("REPLACE phrase render requires retrieval seed + Factory velocity authority")
        analysis=self.analyze_and_generate(midi_bytes,request,mask_ratio,n)
        if not analysis["bars"]: raise ValueError("no bars selected")
        source=MidiFile.from_bytes(midi_bytes); song_map=analyze_song_map(midi_bytes,"neural-phrase-render.mid")
        for b in analysis["bars"]:
            if len(b["selection"]["selected"]) < 3:
                raise ValueError(f'bar {b["bar"]} has fewer than three hard-valid candidates')
        outputs={}
        for rank,label in enumerate(("A","B","C")):
            current=source
            path=[]
            for b in analysis["bars"]:
                names=b["selection"]["selected"]; chosen=names[rank]
                cand=next(x for x in b["selection"]["candidates"] if x["name"]==chosen)
                current=self._render_bar_candidate(current,song_map,request,b,cand)
                path.append({"bar":b["bar"],"candidate":chosen,"score":cand["score"]})
            raw=current.to_bytes(); reparsed=MidiFile.from_bytes(raw); reparsed.notes()
            start=int(analysis["bars"][0]["startTick"]); end=int(analysis["bars"][-1]["endTick"])
            protected_src=self._protected_event_fingerprint(source,request.track_index,request.channel,start,end)
            protected_out=self._protected_event_fingerprint(reparsed,request.track_index,request.channel,start,end)
            if protected_src != protected_out: raise ValueError("protected event changed in phrase render")
            src_vel=sorted(n.velocity for n in self._notes(source,request,start,end))
            out_vel=sorted(n.velocity for n in self._notes(reparsed,request,start,end))
            if src_vel != out_vel: raise ValueError("phrase neural render changed velocity multiset")
            outputs[label]={"midiBytes":raw,"sha256":__import__('hashlib').sha256(raw).hexdigest(),
                            "candidatePath":path,"velocityPreserved":True,"protectedEventsPreserved":True}
        return {"analysis":analysis,"variants":outputs,"status":"RENDERED_PHRASE_A_B_C_REQUIRES_FINAL_PRODUCTION_VALIDATION"}
