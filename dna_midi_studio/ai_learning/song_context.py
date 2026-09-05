from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from hashlib import sha256
from collections import defaultdict
import json, math, statistics, zipfile, random
import numpy as np

ROLES=("drums","bass","harmony","melody")
FEATURES=("density","pitch_center","pitch_span","gate","onset_mean","onset_std","presence")
WINDOW=4


def _role(note):
    if note["channel"]==10: return "drums"
    p=note["instrument"].program
    if 32<=p<=39 or note["pitch"]<48: return "bass"
    if p<=31 or 48<=p<=55 or 88<=p<=103: return "harmony"
    return "melody"


def _bar_role_features(notes,start,end):
    own=[n for n in notes if start<=n["start"]<end]
    if not own: return np.zeros(len(FEATURES),np.float32)
    span=max(1,end-start)
    pitches=[n["pitch"] for n in own]
    gates=[max(1,n["end"]-n["start"])/span for n in own]
    ons=[(n["start"]-start)/span for n in own]
    center=statistics.median(pitches)/127.0
    pspan=(max(pitches)-min(pitches))/127.0
    return np.asarray([math.log1p(len(own))/5.0,center,pspan,min(2.0,statistics.median(gates)),statistics.mean(ons),statistics.pstdev(ons) if len(ons)>1 else 0.0,1.0],np.float32)


def _song_rows(data:bytes,name:str):
    # Runtime-safe parser path: do not depend on repository-root dna_builder.
    from types import SimpleNamespace
    from dna_midi_studio.midi import MidiFile
    midi=MidiFile.from_bytes(data)
    if not midi.ppq: return None
    meter=(4,4)
    # Use the first time-signature meta event when present. MIDI denominator is power-of-two encoded.
    for tr in midi.tracks:
        for e in tr.events:
            if e.kind=="meta" and e.meta_type==0x58 and len(e.data)>=2:
                meter=(int(e.data[0]), 2**int(e.data[1])); break
        else:
            continue
        break
    # Resolve program state per track/channel at note onset, matching the legacy ETL semantics.
    program_events={}
    for ti,tr in enumerate(midi.tracks):
        by={ch:[] for ch in range(16)}
        for e in sorted(tr.events,key=lambda x:(x.tick,x.order)):
            if e.kind=="channel" and e.channel is not None and e.command==0xC0 and e.data:
                by[e.channel].append((e.tick,int(e.data[0])))
        program_events[ti]=by
    def program_at(track,channel,tick):
        prog=0
        for t,p in program_events.get(track,{}).get(channel,[]):
            if t>tick: break
            prog=p
        return prog
    notes=[]
    for n in midi.notes():
        prog=program_at(n.track,n.channel,n.start)
        notes.append({"start":n.start,"end":n.end,"pitch":n.pitch,"velocity":n.velocity,
                      "channel":n.channel+1,"instrument":SimpleNamespace(program=prog)})
    if not notes: return None
    bar_ticks=midi.ppq*meter[0]*4/meter[1]
    last=max(n["end"] for n in notes)
    total=max(1,int(math.ceil(last/max(1,bar_ticks))))
    buckets=[[[] for _ in ROLES] for _ in range(total)]
    hist=np.zeros((total,12),np.float64)
    for n in notes:
        bi=min(total-1,max(0,int(n["start"]//bar_ticks))); ri=ROLES.index(_role(n)); buckets[bi][ri].append(n)
        if n["channel"]!=10: hist[bi,n["pitch"]%12]+=max(1,n["end"]-n["start"])
    feats=np.zeros((total,len(ROLES),len(FEATURES)),np.float32)
    for bi in range(total):
        st=bi*bar_ticks; en=(bi+1)*bar_ticks
        for ri in range(len(ROLES)): feats[bi,ri]=_bar_role_features(buckets[bi][ri],st,en)
    chord=np.full((total,),12,np.int64)
    for bi in range(total):
        if hist[bi].sum()>0: chord[bi]=int(hist[bi].argmax())
    meter_code={"2/4":0,"3/4":1,"4/4":2,"6/8":3,"7/8":4,"9/8":5}.get(f"{meter[0]}/{meter[1]}",6)
    return feats,chord,meter_code

@dataclass
class SongContextManifest:
    schema:str; version:str; songs:int; samples:int; train:int; validation:int; holdout:int; source_hash:str; dataset_hash:str; velocity_used:bool=False
    def to_dict(self): return asdict(self)


def build_song_context_dataset(zip_path:str|Path,out_dir:str|Path,seed:int=800,stride:int=2,max_samples_per_song:int=180):
    zpath=Path(zip_path); out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    rng=random.Random(seed); records=[]; song_meta=[]
    with zipfile.ZipFile(zpath) as z:
        names=sorted(n for n in z.namelist() if n.lower().endswith((".mid",".kar")))
        for name in names:
            data=z.read(name); h=sha256(data).hexdigest()
            try: row=_song_rows(data,name)
            except Exception: continue
            if row is None: continue
            feats,chords,meter=row; total=len(feats)
            centers=list(range(WINDOW,total-WINDOW,stride)); rng.shuffle(centers); centers=centers[:max_samples_per_song]
            sid=len(song_meta); song_meta.append({"name":name,"sha256":h,"bars":total})
            for c in centers:
                ctx=feats[c-WINDOW:c+WINDOW+1].copy() # 9x4x7
                chord=chords[c-WINDOW:c+WINDOW+1].copy()
                for target_role in range(len(ROLES)):
                    target=ctx[WINDOW,target_role].copy()
                    # retain target only when meaningful or 20% silence examples
                    if target[-1]==0 and rng.random()>0.20: continue
                    masked=ctx.copy(); masked[WINDOW,target_role]=0
                    records.append((sid,c,target_role,masked,chord,meter,c/max(1,total-1),target))
    # split by song hash, never by sample
    order=list(range(len(song_meta))); rng.shuffle(order)
    n=len(order); a=max(1,int(n*.8)); b=max(a+1,int(n*.9))
    split_by_song={sid:(0 if i<a else 1 if i<b else 2) for i,sid in enumerate(order)}
    contexts=np.stack([r[3] for r in records]); chords=np.stack([r[4] for r in records])
    roles=np.asarray([r[2] for r in records],np.int64); meters=np.asarray([r[5] for r in records],np.int64)
    positions=np.asarray([r[6] for r in records],np.float32); targets=np.stack([r[7] for r in records])
    song_ids=np.asarray([r[0] for r in records],np.int64); centers=np.asarray([r[1] for r in records],np.int64)
    split=np.asarray([split_by_song[r[0]] for r in records],np.int8)
    np.savez_compressed(out/'song_context_v1.npz',contexts=contexts,chords=chords,roles=roles,meters=meters,positions=positions,targets=targets,song_ids=song_ids,centers=centers,split=split)
    (out/'song_context_songs.json').write_text(json.dumps(song_meta,ensure_ascii=False,indent=2),encoding='utf-8')
    dh=sha256((out/'song_context_v1.npz').read_bytes()).hexdigest(); sh=sha256(zpath.read_bytes()).hexdigest()
    counts=[int((split==i).sum()) for i in range(3)]
    m=SongContextManifest('dna-song-context-dataset','1.0',len(song_meta),len(records),*counts,sh,dh)
    (out/'song_context_manifest.json').write_text(json.dumps(m.to_dict(),indent=2),encoding='utf-8')
    return m
