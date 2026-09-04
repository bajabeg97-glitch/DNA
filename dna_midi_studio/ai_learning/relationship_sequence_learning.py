from __future__ import annotations
from pathlib import Path
from hashlib import sha256
import json, math, random
import numpy as np
from .relationship_learning import _safe_name, _notes, _nearest_pairs, KIND_VOCAB
from dna_midi_studio.midi import MidiFile

ACTION_VOCAB=['PLAY','SKIP','HOLD']
SEQ_FEATURE_NAMES=['pitch_norm','delta_prev','delta_next','ioi_prev_qn','ioi_next_qn','duration_qn','beat_pos_qn','phrase_pos','local_density','is_repeat']

def _features(notes,i,ppq):
    n=notes[i]; prev=notes[i-1] if i else None; nxt=notes[i+1] if i+1<len(notes) else None
    ioi_prev=(n.start-prev.start)/ppq if prev else 0.0; ioi_next=(nxt.start-n.start)/ppq if nxt else 0.0
    beat=(n.start%ppq)/ppq; pos=i/max(1,len(notes)-1)
    lo=n.start-2*ppq; hi=n.start+2*ppq; dens=sum(lo<=x.start<hi for x in notes)/4.0
    return [n.pitch/127.0, ((n.pitch-prev.pitch) if prev else 0)/24.0, ((nxt.pitch-n.pitch) if nxt else 0)/24.0,
            min(4,ioi_prev)/4,min(4,ioi_next)/4,min(4,(n.end-n.start)/ppq)/4,beat,pos,min(8,dens)/8,
            1.0 if prev and prev.pitch==n.pitch else 0.0]

def build_sequence_dataset(midi_dir, reports_dir, out_dir, min_conf=.85, min_matches=20, max_seq=192, seed=823):
    midi_dir=Path(midi_dir); reports_dir=Path(reports_dir); out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    idx={_safe_name(p.name):p for p in midi_dir.iterdir() if p.suffix.lower() in {'.mid','.midi','.kar'}}
    rows=[]
    for rp in sorted(reports_dir.glob('*.json')):
        try:d=json.loads(rp.read_text(encoding='utf-8'))
        except:continue
        mp=idx.get(_safe_name(str(d.get('source') or '')))
        if not mp:continue
        raw=mp.read_bytes(); song_hash=sha256(raw).hexdigest()
        try:m=MidiFile.from_bytes(raw)
        except:continue
        rel=d.get('existingEchoTerca') or []
        if isinstance(rel,dict):rel=[rel]
        for x in rel:
            kind=str(x.get('kind',''))
            if kind not in KIND_VOCAB or float(x.get('confidence',0))<min_conf or int(x.get('matched',0))<min_matches:continue
            st=int(x.get('mainSoloTrack',-1)); sc=int(x.get('mainSoloChannel',-1))-1; at=int(x.get('track',-1)); ac=int(x.get('channel',-1))-1
            if min(st,sc,at,ac)<0 or st>=len(m.tracks) or at>=len(m.tracks):continue
            src=_notes(m,st,sc); aux=_notes(m,at,ac)
            if len(src)<8:continue
            pairs=_nearest_pairs(src,aux,kind,m.ppq,x.get('delayTicks'),999999)
            pairmap={(s.start,s.pitch,s.end):(s,a) for s,a in pairs}
            # source sequence chunks; relation never crosses split because songHash is retained.
            for base in range(0,len(src),max_seq):
                chunk=src[base:base+max_seq]
                if len(chunk)<4:continue
                feats=[]; pitches=[]; actions=[]; intervals=[]; delays=[]; durs=[]
                for gi,n in enumerate(src[base:base+len(chunk)],start=base):
                    key=(n.start,n.pitch,n.end); pa=pairmap.get(key)
                    if pa:
                        a=pa[1]; action=0; interval=max(-24,min(24,a.pitch-n.pitch))+24; delay=(a.start-n.start)/max(1,m.ppq); dur=(a.end-a.start)/max(1,n.end-n.start)
                    else:
                        covering=[a for a in aux if a.start<n.start<a.end]
                        if covering:
                            action=2; a=min(covering,key=lambda z:abs(z.pitch-n.pitch)); interval=max(-24,min(24,a.pitch-n.pitch))+24; delay=(a.start-n.start)/max(1,m.ppq); dur=(a.end-a.start)/max(1,n.end-n.start)
                        else:
                            action=1; interval=24; delay=0.0; dur=1.0
                    feats.append(_features(src,gi,m.ppq)); pitches.append(n.pitch); actions.append(action); intervals.append(interval); delays.append(delay); durs.append(max(.05,min(3.0,dur)))
                rows.append(dict(songHash=song_hash,source=mp.name,kind=kind,features=feats,pitches=pitches,actions=actions,intervals=intervals,delays=delays,durations=durs,ppq=m.ppq))
    if len(rows)<16:raise ValueError('not enough sequence rows')
    groups=sorted({r['songHash'] for r in rows}); random.Random(seed).shuffle(groups); n=len(groups); nh=max(1,round(n*.15)); nv=max(1,round(n*.15)); hold=set(groups[-nh:]); val=set(groups[-nh-nv:-nh]); train=set(groups)-val-hold
    split=np.asarray([0 if r['songHash'] in train else 1 if r['songHash'] in val else 2 for r in rows],dtype=np.int8)
    maxlen=max(len(r['features']) for r in rows); fd=len(SEQ_FEATURE_NAMES); N=len(rows)
    X=np.zeros((N,maxlen,fd),np.float32); P=np.zeros((N,maxlen),np.int64); A=np.full((N,maxlen),-100,np.int64); I=np.full((N,maxlen),-100,np.int64); D=np.zeros((N,maxlen),np.float32); R=np.ones((N,maxlen),np.float32); M=np.zeros((N,maxlen),np.bool_); K=np.zeros(N,np.int64)
    for j,r in enumerate(rows):
        L=len(r['features']); X[j,:L]=np.asarray(r['features']); P[j,:L]=r['pitches']; A[j,:L]=r['actions']; I[j,:L]=r['intervals']; D[j,:L]=r['delays']; R[j,:L]=r['durations']; M[j,:L]=1; K[j]=KIND_VOCAB.index(r['kind'])
    tr=X[split==0][M[split==0]]; mean=tr.mean(0); std=tr.std(0); std[std<1e-6]=1.; X=(X-mean)/std; X[~M]=0
    np.savez_compressed(out/'relationship_sequence_dataset_v2.npz',features=X,pitches=P,kind=K,action=A,interval=I,delay_qn=D,duration_ratio=R,mask=M,split=split,feature_mean=mean,feature_std=std)
    counts={a:int((A[M]==i).sum()) for i,a in enumerate(ACTION_VOCAB)}
    manifest={'schema':'dna-relationship-sequence-dataset','version':'2.0','rows':N,'songs':len(groups),'trainRows':int((split==0).sum()),'validationRows':int((split==1).sum()),'holdoutRows':int((split==2).sum()),'noteActions':counts,'featureNames':SEQ_FEATURE_NAMES,'velocityFeature':False,'velocityTarget':False,'sourceDisjoint':True}
    digest=sha256((out/'relationship_sequence_dataset_v2.npz').read_bytes()).hexdigest(); manifest['datasetHash']=digest
    (out/'relationship_sequence_manifest_v2.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest
