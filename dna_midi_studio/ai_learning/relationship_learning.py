from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from hashlib import sha256
import json, math, random, statistics
from typing import Any
import numpy as np

from dna_midi_studio.midi import MidiFile, Note

KIND_VOCAB = ['third','echo']
FEATURE_NAMES = [
    'source_note_log','aux_note_log','matched_ratio','source_density','aux_density',
    'median_interval','interval_std','third_ratio','unison_ratio','median_delay_qn',
    'delay_std_qn','median_duration_ratio','duration_ratio_std','register_delta',
    'confidence','preserve_mode','ppq_log',
]
PAIR_COLUMNS = ['interval_code','delay_bin','duration_bin','present']

@dataclass
class RelationshipDatasetManifest:
    schema:str; version:str; samples:int; songs:int; train:int; validation:int; holdout:int
    thirds:int; echoes:int; feature_names:list[str]; pair_columns:list[str]
    source_hashes:dict[str,str]; dataset_hash:str; authority:dict[str,Any]
    def to_dict(self): return asdict(self)

def _sha_file(p:Path)->str:
    h=sha256();
    with p.open('rb') as f:
        for c in iter(lambda:f.read(1<<20),b''): h.update(c)
    return h.hexdigest()

def _safe_name(s:str)->str:
    return ' '.join((s or '').replace('\\','/').split('/')[-1].split()).casefold()

def _notes(m:MidiFile,track:int,channel:int)->list[Note]:
    # Relationship mining must tolerate legacy karaoke/MIDI note-pairing defects without
    # weakening the production MidiFile.notes() validator. We therefore pair only the
    # requested track/channel locally and skip orphan/zero/dangling events.
    if not 0 <= track < len(m.tracks): return []
    active={}; out=[]
    for e in sorted(m.tracks[track].events,key=lambda x:(x.tick,x.order)):
        if e.kind!='channel' or e.channel!=channel: continue
        if e.is_note_on:
            active.setdefault(e.note_number,[]).append(e)
        elif e.is_note_off:
            q=active.get(e.note_number) or []
            if not q: continue
            on=q.pop(0)
            if e.tick<=on.tick: continue
            out.append(Note(track,channel,e.note_number,on.tick,e.tick,on.data[1],on_order=on.order,off_order=e.order))
    return sorted(out,key=lambda n:(n.start,n.pitch,n.end))

def _nearest_pairs(src:list[Note],aux:list[Note],kind:str,ppq:int,delay_hint:int|None,max_pairs:int)->list[tuple[Note,Note]]:
    if not src or not aux:return []
    used=set(); out=[]
    tol=max(2,ppq//20)
    for s in src:
        target=s.start+(int(delay_hint) if kind=='echo' and delay_hint else 0)
        best=None;best_key=None
        for j,a in enumerate(aux):
            if j in used: continue
            dt=abs(a.start-target)
            if dt>tol: continue
            # third prefers 3/4 semitones; echo prefers same pitch
            musical=0
            if kind=='third': musical=0 if abs(a.pitch-s.pitch) in (3,4) else 12
            else: musical=0 if a.pitch==s.pitch else 12
            key=(dt,musical,abs(a.pitch-s.pitch),j)
            if best_key is None or key<best_key:best_key=key;best=(j,a)
        if best is not None:
            used.add(best[0]);out.append((s,best[1]))
            if len(out)>=max_pairs: break
    return out

def _pair_tokens(pairs:list[tuple[Note,Note]],ppq:int,max_pairs:int)->np.ndarray:
    arr=np.zeros((max_pairs,4),dtype=np.int64)
    for i,(s,a) in enumerate(pairs[:max_pairs]):
        interval=max(-24,min(24,a.pitch-s.pitch)); interval_code=interval+24
        delay=(a.start-s.start)/max(1,ppq)
        delay_bin=max(0,min(127,round((delay+2.0)*24))) # -2..~3.3 qn
        ratio=(a.end-a.start)/max(1,s.end-s.start)
        dur_bin=max(0,min(63,round(min(2.0,max(0.0,ratio))*31.5)))
        arr[i]=(interval_code,delay_bin,dur_bin,1)
    return arr

def _feature_row(src:list[Note],aux:list[Note],pairs:list[tuple[Note,Note]],kind:str,ppq:int,confidence:float,mode:str)->np.ndarray:
    ints=[a.pitch-s.pitch for s,a in pairs]; delays=[(a.start-s.start)/max(1,ppq) for s,a in pairs]
    dr=[(a.end-a.start)/max(1,s.end-s.start) for s,a in pairs]
    span=max(1,max((n.end for n in src+aux),default=ppq)-min((n.start for n in src+aux),default=0))
    def med(x,d=0.0):return float(statistics.median(x)) if x else d
    def std(x):return float(statistics.pstdev(x)) if len(x)>1 else 0.0
    return np.asarray([
        math.log1p(len(src)),math.log1p(len(aux)),len(pairs)/max(1,len(src)),
        len(src)*ppq/span,len(aux)*ppq/span,med(ints),std(ints),
        sum(abs(x) in (3,4) for x in ints)/max(1,len(ints)),sum(x==0 for x in ints)/max(1,len(ints)),
        med(delays),std(delays),med(dr,1.0),std(dr),
        med([a.pitch for _,a in pairs])-med([s.pitch for s,_ in pairs]),
        confidence,1.0 if str(mode).upper()=='PRESERVE' else 0.0,math.log1p(ppq),
    ],dtype=np.float32)

class RelationshipCorpusBuilder:
    """Mines already-existing solo<->third/echo relationships from real songs.

    Reports only nominate candidate track relationships. Every trainable feature is
    recomputed from the original MIDI. Velocity is never loaded into the feature or
    token schema. Generated/optimized MIDI outputs are never used as training truth.
    """
    def __init__(self,midi_dir:str|Path,reports_dir:str|Path,min_confidence:float=.85,min_matches:int=20,max_pairs:int=192,seed:int=800):
        self.midi_dir=Path(midi_dir);self.reports_dir=Path(reports_dir);self.min_confidence=min_confidence;self.min_matches=min_matches;self.max_pairs=max_pairs;self.seed=seed
    def _index(self)->dict[str,Path]:
        return {_safe_name(p.name):p for p in self.midi_dir.rglob('*') if p.suffix.lower() in {'.mid','.midi','.kar'}}
    def build(self,out_dir:str|Path)->RelationshipDatasetManifest:
        out=Path(out_dir);out.mkdir(parents=True,exist_ok=True); index=self._index(); rows=[]; skipped=[]
        report_hash=sha256();
        for rp in sorted(self.reports_dir.glob('*.json')):
            report_hash.update(rp.read_bytes())
            try:d=json.loads(rp.read_text(encoding='utf-8'))
            except Exception as e: skipped.append({'report':rp.name,'reason':f'json:{e}'});continue
            source=str(d.get('source') or '')
            mp=index.get(_safe_name(source))
            if mp is None: skipped.append({'report':rp.name,'source':source,'reason':'source-midi-not-found'});continue
            raw=mp.read_bytes(); song_hash=sha256(raw).hexdigest()
            try:m=MidiFile.from_bytes(raw)
            except Exception as e: skipped.append({'report':rp.name,'source':source,'reason':f'midi:{e}'});continue
            rel=d.get('existingEchoTerca') or []
            if isinstance(rel,dict):rel=[rel]
            for x in rel:
                if not isinstance(x,dict) or x.get('kind') not in KIND_VOCAB:continue
                conf=float(x.get('confidence',0));matched=int(x.get('matched',0))
                if conf<self.min_confidence or matched<self.min_matches:continue
                st=int(x.get('mainSoloTrack',-1));sc=int(x.get('mainSoloChannel',-1))-1;at=int(x.get('track',-1));ac=int(x.get('channel',-1))-1
                if min(st,sc,at,ac)<0 or st>=len(m.tracks) or at>=len(m.tracks):continue
                try:
                    src=_notes(m,st,sc);aux=_notes(m,at,ac)
                except Exception as e:
                    skipped.append({'report':rp.name,'source':source,'reason':f'note-pairing:{e}'})
                    continue
                kind=str(x['kind'])
                pairs=_nearest_pairs(src,aux,kind,m.ppq,x.get('delayTicks'),self.max_pairs)
                if len(pairs)<max(8,min(self.min_matches,16)):continue
                rows.append({'songHash':song_hash,'source':source,'kind':kind,'confidence':conf,'mode':x.get('mode',''),
                             'features':_feature_row(src,aux,pairs,kind,m.ppq,conf,str(x.get('mode',''))),
                             'pairs':_pair_tokens(pairs,m.ppq,self.max_pairs),'pairCount':len(pairs),
                             'delayTicks':int(x.get('delayTicks') or 0),'ppq':m.ppq})
        if len(rows)<8: raise ValueError(f'not enough relationship samples: {len(rows)}')
        # source-disjoint split by song hash, stratified only approximately by deterministic shuffled groups
        groups={}
        for i,r in enumerate(rows):groups.setdefault(r['songHash'],[]).append(i)
        keys=sorted(groups);random.Random(self.seed).shuffle(keys)
        n=len(keys);nh=max(1,round(n*.15));nv=max(1,round(n*.15)); hold=set(keys[-nh:]);val=set(keys[-nh-nv:-nh]);train=set(keys)-val-hold
        split=np.asarray([0 if r['songHash'] in train else 1 if r['songHash'] in val else 2 for r in rows],dtype=np.int8)
        X=np.stack([r['features'] for r in rows]);train_x=X[split==0];mean=train_x.mean(0);std=train_x.std(0);std[std<1e-6]=1.;X=(X-mean)/std
        P=np.stack([r['pairs'] for r in rows]);Y=np.asarray([KIND_VOCAB.index(r['kind']) for r in rows],dtype=np.int64)
        # Targets useful for future relationship generation; no velocity target exists.
        delay=np.asarray([max(-2.0,min(3.0,statistics.median([(a[1]-48)/24 for a in r['pairs'] if a[3]]) if r['pairCount'] else 0.0)) for r in rows],dtype=np.float32)
        # Above token-derived delay is encoded; use actual pair token median below for deterministic target.
        delay=np.asarray([float(statistics.median([(int(t[1])-48)/24 for t in r['pairs'] if int(t[3])])) for r in rows],dtype=np.float32)
        dur=np.asarray([float(statistics.median([int(t[2])/31.5 for t in r['pairs'] if int(t[3])])) for r in rows],dtype=np.float32)
        interval=np.asarray([int(round(statistics.median([int(t[0])-24 for t in r['pairs'] if int(t[3])]))) for r in rows],dtype=np.int64)
        np.savez_compressed(out/'relationship_dataset_v1.npz',features=X,pairs=P,kind=Y,split=split,delay_qn=delay,duration_ratio=dur,median_interval=interval,feature_mean=mean,feature_std=std)
        meta=[{k:r[k] for k in ('songHash','source','kind','confidence','mode','pairCount','delayTicks','ppq')} for r in rows]
        (out/'relationship_samples.json').write_text(json.dumps(meta,indent=2,ensure_ascii=False),encoding='utf-8')
        (out/'relationship_skipped.json').write_text(json.dumps(skipped,indent=2,ensure_ascii=False),encoding='utf-8')
        ds_hash=_sha_file(out/'relationship_dataset_v1.npz')
        mani=RelationshipDatasetManifest('dna-solo-relationship-dataset','1.0',len(rows),len(keys),int((split==0).sum()),int((split==1).sum()),int((split==2).sum()),int((Y==0).sum()),int((Y==1).sum()),FEATURE_NAMES,PAIR_COLUMNS,
            {'reportsDigest':report_hash.hexdigest(),'midiCorpusDigest':sha256(''.join(sorted({r['songHash'] for r in rows})).encode()).hexdigest()},ds_hash,
            {'sourceMidiOnly':True,'optimizedMidiTrainingTruth':False,'velocityFeature':False,'velocityTarget':False,'factoryVelocityAuthorityPreserved':True,'songDisjointSplit':True})
        (out/'relationship_dataset_manifest.json').write_text(json.dumps(mani.to_dict(),indent=2),encoding='utf-8')
        return mani
