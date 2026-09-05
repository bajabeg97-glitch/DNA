from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import hashlib, json, statistics

from dna_midi_studio.midi import MidiFile, MidiTrack, Note
from dna_midi_studio.song_understanding import analyze_song_map
try:
    from .relationship_inference import RelationshipInferenceEngine
except Exception:
    RelationshipInferenceEngine=None

_SCALE_BY_QUALITY={
    'major':(0,2,4,5,7,9,11),'major-seventh':(0,2,4,5,7,9,11),
    'dominant-seventh':(0,2,4,5,7,9,10),'minor':(0,2,3,5,7,8,10),
    'minor-seventh':(0,2,3,5,7,8,10),
}

@dataclass(frozen=True)
class MelodicRelationshipRequest:
    source_track_index:int
    source_channel:int
    target_track_index:int
    target_channel:int
    start_bar:int
    end_bar:int
    kind:str  # third | echo | solo-performance
    def validate(self):
        if self.source_track_index<0 or self.target_track_index<0: raise ValueError('track indices must be >=0')
        if not 0<=self.source_channel<=15 or not 0<=self.target_channel<=15: raise ValueError('channel must be 0..15')
        if self.start_bar<1 or self.end_bar<self.start_bar: raise ValueError('invalid bar range')
        if self.kind not in {'third','echo','solo-performance'}: raise ValueError(f'unsupported melodic relationship kind: {self.kind}')

class FactoryMelodyVelocityProvider:
    """Factory-only dynamics for newly generated melodic relationship notes."""
    def __init__(self,profiles_path:str|Path):
        d=json.loads(Path(profiles_path).read_text(encoding='utf-8'))
        self.profiles=[p for p in d.get('profiles',[]) if p.get('role')=='melody']
        if not self.profiles: raise ValueError('Factory melody velocity profiles missing')
    def resolve(self,midi:MidiFile,track:int,channel:int,tick:int,intensity:str)->dict[str,Any]:
        sound=midi.sound_at(channel,tick,track) if track<len(midi.tracks) else None
        exact=[p for p in self.profiles if sound and (p.get('bankMsb'),p.get('bankLsb'),p.get('program'))==sound]
        pool=exact or self.profiles
        def support(p): return float((p.get('velocityCurve') or {}).get('sampleCount') or 0)
        prof=max(pool,key=lambda p:(support(p),str(p.get('id',''))))
        vel=prof.get('velocity') or {}
        key={'third':'lowMid','echo':'soft','solo-performance':'optimal'}.get(intensity,'optimal')
        value=int(vel.get(key,vel.get('optimal',80)))
        return {'velocity':max(1,min(127,value)),'profileId':prof.get('id'),'exactSound':bool(exact),'curvePoint':key,
                'sound':list(sound) if sound else None,'authority':'FACTORY_ONLY'}

class MelodicRelationshipEngine:
    """Relationship-conditioned melodic engine.

    THIRD and ECHO may create/replace a target layer from a protected source solo.
    SOLO-PERFORMANCE never changes source pitch or velocity; it only generates bounded
    timing/gate A/B/C variants. This deliberately avoids claiming full neural solo
    replacement before a real approved solo corpus exists.
    """
    def __init__(self,data_dir:str|Path,relationship_model_dir:str|Path|None=None,relationship_dataset_path:str|Path|None=None):
        self.data_dir=Path(data_dir)
        self.velocity=FactoryMelodyVelocityProvider(self.data_dir/'factory-velocity-profiles.json')
        self.relationship_ranker=None
        if RelationshipInferenceEngine is not None and relationship_model_dir and relationship_dataset_path:
            try:self.relationship_ranker=RelationshipInferenceEngine(relationship_model_dir,relationship_dataset_path)
            except Exception:self.relationship_ranker=None
        self.relationship_notice='REAL_ORIGINAL_MIDI_RELATIONSHIP_RANKER_V1' if self.relationship_ranker else 'PROJECT_RELATIONSHIP_BASELINE_NO_LEARNED_RANKER'

    @staticmethod
    def _chord_at(song:dict,tick:int)->dict|None:
        for c in song.get('chordCells',[]):
            if int(c['startTick'])<=tick<int(c['endTick']): return c
        return None
    @staticmethod
    def _third_interval(pitch:int,chord:dict)->int|None:
        scale=_SCALE_BY_QUALITY.get(str(chord.get('quality')))
        if scale is None:return None
        root=int(chord.get('root',0)); rel=(pitch-root)%12
        if rel not in scale:return None
        i=scale.index(rel); target=scale[(i+2)%7]+(12 if i+2>=7 else 0); delta=target-rel
        return delta if delta in (3,4) else None
    @staticmethod
    def _ensure_target(midi:MidiFile,target_index:int)->MidiFile:
        if target_index<len(midi.tracks):return midi
        if target_index!=len(midi.tracks):raise ValueError('new target track must be appended contiguously')
        if midi.format_type==0:raise ValueError('cannot append track to SMF0 song')
        return MidiFile(midi.format_type,midi.ppq,midi.tracks+[MidiTrack([])])
    @staticmethod
    def _protected(midi:MidiFile,target_track:int,target_channel:int,start:int,end:int)->list[tuple]:
        out=[]
        for ti,t in enumerate(midi.tracks):
            for e in t.events:
                # End-of-Track is a writer-maintained technical boundary and may move
                # when target notes are added/replaced; it is not musical protected content.
                if ti==target_track and e.kind=='meta' and e.meta_type==0x2F:
                    continue
                is_target=(ti==target_track and e.kind=='channel' and e.channel==target_channel and
                           (e.is_note_on or e.is_note_off) and start<=e.tick<=end)
                if not is_target:out.append((ti,e.tick,e.kind,e.status,e.meta_type,bytes(e.data)))
        return sorted(out,key=lambda x:(x[0],x[1],str(x[2]),-1 if x[3] is None else x[3],x[5]))
    @staticmethod
    def _source_notes(midi:MidiFile,req:MelodicRelationshipRequest,start:int,end:int)->list[Note]:
        n=[x for x in midi.notes() if x.track==req.source_track_index and x.channel==req.source_channel and start<=x.start<end]
        return sorted(n,key=lambda x:(x.start,x.pitch,x.end))
    @staticmethod
    def _median_ioi(notes:list[Note],ppq:int)->int:
        diffs=[b.start-a.start for a,b in zip(notes,notes[1:]) if b.start>a.start]
        return int(statistics.median(diffs)) if diffs else max(1,ppq//2)

    def _third_notes(self,midi:MidiFile,song:dict,req:MelodicRelationshipRequest,source:list[Note],variant:int,end:int):
        out=[];proof=[];skipped=0
        # A close diatonic third; B drops occasional phrase-repeat note; C slightly shorter gate.
        for i,n in enumerate(source):
            chord=self._chord_at(song,n.start)
            if not chord or float(chord.get('confidence',0))<.80: skipped+=1;continue
            interval=self._third_interval(n.pitch,chord)
            if interval is None:skipped+=1;continue
            if variant==1 and i>0 and i%6==5: continue
            pitch=n.pitch+interval
            if pitch>127:skipped+=1;continue
            dur=n.end-n.start
            ratio=(1.0,.92,.82)[variant]
            note_end=min(end,n.start+max(1,int(round(dur*ratio))))
            v=self.velocity.resolve(midi,req.target_track_index,req.target_channel,n.start,'third')
            out.append(Note(req.target_track_index,req.target_channel,pitch,n.start,note_end,v['velocity'],factory_profile_id=v['profileId'],element='third'))
            proof.append({'sourcePitch':n.pitch,'targetPitch':pitch,'interval':interval,'chord':chord.get('symbol'),'factory':v})
        return out,proof,skipped

    def _echo_notes(self,midi:MidiFile,song:dict,req:MelodicRelationshipRequest,source:list[Note],variant:int,end:int):
        out=[];proof=[];skipped=0;ioi=self._median_ioi(source,midi.ppq)
        # Musical delays: about 1/8, 3/16, 1/4 of a quarter-note grid, bounded by local IOI.
        base=[midi.ppq//2,3*midi.ppq//4,midi.ppq][variant]
        delay=max(1,min(base,max(1,int(ioi*.72))))
        ratios=(.55,.45,.35)
        for i,n in enumerate(source):
            start=n.start+delay
            if start>=end:continue
            next_start=source[i+1].start if i+1<len(source) else end
            # avoid recursive smear into next source onset
            if start>=next_start: skipped+=1;continue
            dur=max(1,int(round((n.end-n.start)*ratios[variant])))
            note_end=min(end,next_start,start+dur)
            if note_end<=start:skipped+=1;continue
            v=self.velocity.resolve(midi,req.target_track_index,req.target_channel,start,'echo')
            out.append(Note(req.target_track_index,req.target_channel,n.pitch,start,note_end,v['velocity'],factory_profile_id=v['profileId'],element='echo'))
            proof.append({'sourcePitch':n.pitch,'delayTicks':delay,'durationRatio':ratios[variant],'factory':v})
        return out,proof,skipped

    @staticmethod
    def _solo_performance_notes(req:MelodicRelationshipRequest,source:list[Note],variant:int,ppq:int,start:int,end:int):
        out=[];proof=[]
        shifts=(0,max(1,ppq//192),-max(1,ppq//192)); gates=(1.0,.92,1.06)
        for i,n in enumerate(source):
            # preserve phrase anchors/downbeat notes; microtiming only on inner notes
            shift=0 if i==0 or n.start==start else shifts[variant]
            ns=max(start,n.start+shift); ne=min(end,ns+max(1,int(round((n.end-n.start)*gates[variant]))))
            out.append(Note(req.target_track_index,req.target_channel,n.pitch,ns,ne,n.velocity,element='solo-performance'))
            proof.append({'sourcePitch':n.pitch,'targetPitch':n.pitch,'sourceVelocity':n.velocity,'targetVelocity':n.velocity,
                          'timingShiftTicks':ns-n.start,'gateRatio':gates[variant]})
        return out,proof,0

    def generate(self,midi_bytes:bytes,req:MelodicRelationshipRequest)->dict[str,Any]:
        req.validate(); midi=MidiFile.from_bytes(midi_bytes)
        if req.source_track_index>=len(midi.tracks):raise ValueError('source track does not exist')
        midi=self._ensure_target(midi,req.target_track_index)
        # Canonicalize appended/empty tracks through the writer so implicit End-of-Track
        # metadata is part of the protected baseline rather than a false mutation.
        midi=MidiFile.from_bytes(midi.to_bytes())
        song=analyze_song_map(midi.to_bytes(),'melodic-relationship.mid');bars=song['bars']
        if req.end_bar>len(bars):raise ValueError('bar range exceeds song')
        selected=bars[req.start_bar-1:req.end_bar];start=int(selected[0]['startTick']);end=int(selected[-1]['endTick'])
        source=self._source_notes(midi,req,start,end)
        if not source:raise ValueError('source solo contains no notes in requested region')
        if req.kind=='solo-performance' and (req.target_track_index!=req.source_track_index or req.target_channel!=req.source_channel):
            raise ValueError('solo-performance target must be the source solo track/channel')
        protected=self._protected(midi,req.target_track_index,req.target_channel,start,end)
        variants={}
        for idx,label in enumerate('ABC'):
            if req.kind=='third': notes,proof,skipped=self._third_notes(midi,song,req,source,idx,end)
            elif req.kind=='echo': notes,proof,skipped=self._echo_notes(midi,song,req,source,idx,end)
            else: notes,proof,skipped=self._solo_performance_notes(req,source,idx,midi.ppq,start,end)
            if not notes:raise ValueError(f'{req.kind} variant {label} produced no safe notes')
            out=midi.replace_notes(track_index=req.target_track_index,channel=req.target_channel,start_tick=start,end_tick=end,new_notes=notes)
            raw=out.to_bytes();reparsed=MidiFile.from_bytes(raw); reparsed.notes()
            if self._protected(reparsed,req.target_track_index,req.target_channel,start,end)!=protected:
                raise ValueError('protected event changed during melodic relationship render')
            target=[n for n in reparsed.notes() if n.track==req.target_track_index and n.channel==req.target_channel and start<=n.start<end]
            if req.kind=='solo-performance':
                src_pitch=[n.pitch for n in source];dst_pitch=[n.pitch for n in target]
                src_vel=[n.velocity for n in source];dst_vel=[n.velocity for n in target]
                if src_pitch!=dst_pitch or src_vel!=dst_vel:raise ValueError('solo performance violated pitch/velocity lock')
            factory_ids=sorted({p['factory']['profileId'] for p in proof if 'factory' in p and p['factory'].get('profileId')})
            exact=all(p['factory'].get('exactSound') for p in proof if 'factory' in p) if factory_ids else None
            learned=None
            if self.relationship_ranker and req.kind in {'third','echo'}:
                delay_hint=None
                if req.kind=='echo' and proof: delay_hint=int(statistics.median([int(x.get('delayTicks',0)) for x in proof]))
                learned=self.relationship_ranker.score_notes(source,target,req.kind,midi.ppq,delay_hint)
            variants[label]={'midiBytes':raw,'sha256':hashlib.sha256(raw).hexdigest(),'noteCount':len(target),'proof':proof,
                             'skipped':skipped,'factoryVelocityProfileIds':factory_ids,'factorySoundExactForAll':exact,
                             'sourcePitchPreserved':req.kind!='third','goldVelocityUsed':False,'neuralVelocityUsed':False,
                             'protectedEventsPreserved':True,'relationshipModel':learned}
        selected=sorted(variants,key=lambda k:(-(variants[k].get('relationshipModel') or {}).get('relationshipScore',0.0),k)) if self.relationship_ranker and req.kind in {'third','echo'} else list('ABC')
        return {'schema':'dna-melodic-relationship-engine','version':'1.1','request':asdict(req),'sourceSha256':hashlib.sha256(midi_bytes).hexdigest(),
                'variants':variants,'selectedOrder':selected,'authority':{'harmony':'SONG_LOCKED','relationship':self.relationship_notice,
                'velocity':'SOURCE_LOCKED_FOR_SOLO_PERFORMANCE__FACTORY_ONLY_FOR_NEW_THIRD_ECHO','goldVelocity':False,'neuralVelocity':False},
                'status':'RENDERED_A_B_C_REQUIRES_FINAL_PRODUCTION_VALIDATOR'}
