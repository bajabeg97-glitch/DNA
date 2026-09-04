from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from hashlib import sha256
import json, math, random, zipfile
import numpy as np
import torch
from torch import nn
from .song_context import ROLES, FEATURES, _song_rows, _bar_role_features

PHRASE_BARS=4
CONTEXT_SIDE=4
CONTEXT_BARS=PHRASE_BARS+2*CONTEXT_SIDE
TARGET_ROLES=("drums","bass")

@dataclass
class PhraseManifest:
    schema:str; version:str; songs:int; samples:int; train:int; validation:int; holdout:int; source_hash:str; dataset_hash:str; velocity_used:bool=False
    def to_dict(self): return asdict(self)

def build_phrase_dataset(zip_path:str|Path,out_dir:str|Path,seed:int=828,stride:int=2,max_samples_per_song:int=120):
    zpath=Path(zip_path); out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    rng=random.Random(seed); rows=[]; songs=[]
    with zipfile.ZipFile(zpath) as z:
        for name in sorted(n for n in z.namelist() if n.lower().endswith((".mid",".kar"))):
            data=z.read(name); h=sha256(data).hexdigest()
            try: row=_song_rows(data,name)
            except Exception: continue
            if row is None: continue
            feats,chords,meter=row; total=len(feats)
            if total<CONTEXT_BARS: continue
            starts=list(range(CONTEXT_SIDE,total-PHRASE_BARS-CONTEXT_SIDE+1,stride)); rng.shuffle(starts); starts=starts[:max_samples_per_song]
            sid=len(songs); songs.append({"name":name,"sha256":h,"bars":total})
            for st in starts:
                c0=st-CONTEXT_SIDE; c1=st+PHRASE_BARS+CONTEXT_SIDE
                ctx=feats[c0:c1].copy(); ch=chords[c0:c1].copy()
                if len(ctx)!=CONTEXT_BARS: continue
                for rid,role in enumerate(TARGET_ROLES):
                    full_rid=ROLES.index(role)
                    target=feats[st:st+PHRASE_BARS,full_rid].copy()
                    if target[:,-1].sum()==0: continue
                    masked=ctx.copy(); masked[CONTEXT_SIDE:CONTEXT_SIDE+PHRASE_BARS,full_rid]=0
                    rows.append((sid,st,rid,masked,ch,meter,st/max(1,total-1),target))
    order=list(range(len(songs))); rng.shuffle(order); n=len(order); a=max(1,int(n*.8)); b=max(a+1,int(n*.9))
    split_by_song={sid:(0 if i<a else 1 if i<b else 2) for i,sid in enumerate(order)}
    contexts=np.stack([r[3] for r in rows]).astype(np.float32); chords=np.stack([r[4] for r in rows]).astype(np.int64)
    roles=np.asarray([r[2] for r in rows],np.int64); meters=np.asarray([r[5] for r in rows],np.int64); positions=np.asarray([r[6] for r in rows],np.float32)
    targets=np.stack([r[7] for r in rows]).astype(np.float32); song_ids=np.asarray([r[0] for r in rows],np.int64); starts=np.asarray([r[1] for r in rows],np.int64)
    split=np.asarray([split_by_song[r[0]] for r in rows],np.int8)
    np.savez_compressed(out/'phrase_context_v1.npz',contexts=contexts,chords=chords,roles=roles,meters=meters,positions=positions,targets=targets,song_ids=song_ids,starts=starts,split=split)
    (out/'phrase_context_songs.json').write_text(json.dumps(songs,ensure_ascii=False,indent=2),encoding='utf-8')
    meta=PhraseManifest('dna-multibar-phrase-dataset','1.0',len(songs),len(rows),int((split==0).sum()),int((split==1).sum()),int((split==2).sum()),sha256(zpath.read_bytes()).hexdigest(),sha256((out/'phrase_context_v1.npz').read_bytes()).hexdigest())
    (out/'phrase_context_manifest.json').write_text(json.dumps(meta.to_dict(),indent=2),encoding='utf-8')
    return meta

class PhrasePlannerNet(nn.Module):
    """Predicts a 4-bar performance contour; velocity is absent by construction."""
    def __init__(self,d:int=32):
        super().__init__(); self.d=d
        per_bar=len(ROLES)*len(FEATURES)+13
        self.bar=nn.Sequential(nn.Linear(per_bar,d),nn.ReLU())
        self.role=nn.Embedding(2,d); self.meter=nn.Embedding(7,d); self.pos=nn.Linear(1,d)
        self.gru=nn.GRU(d,d,batch_first=True,bidirectional=True)
        self.head=nn.Sequential(nn.Linear(d*2+d*3,d*2),nn.ReLU(),nn.Linear(d*2,PHRASE_BARS*len(FEATURES)))
    def forward(self,contexts,chords,roles,meters,positions):
        chord_oh=torch.nn.functional.one_hot(chords.clamp(0,12),13).float()
        x=torch.cat([contexts.reshape(contexts.shape[0],CONTEXT_BARS,-1),chord_oh],-1)
        h=self.bar(x); h,_=self.gru(h); pooled=h.mean(1)
        cond=torch.cat([pooled,self.role(roles),self.meter(meters.clamp(0,6)),self.pos(positions[:,None])],-1)
        return self.head(cond).reshape(-1,PHRASE_BARS,len(FEATURES))

def train_phrase_planner(dataset_dir:str|Path,out_dir:str|Path,epochs:int=3,batch:int=512,seed:int=828):
    torch.manual_seed(seed); np.random.seed(seed); d=np.load(Path(dataset_dir)/'phrase_context_v1.npz')
    model=PhrasePlannerNet(); opt=torch.optim.AdamW(model.parameters(),lr=2e-3)
    idx=np.flatnonzero(d['split']==0); val=np.flatnonzero(d['split']==1); hold=np.flatnonzero(d['split']==2)
    def run(ids,train=False):
        ids=ids.copy();
        if train: np.random.shuffle(ids)
        ls=[]
        for s in range(0,len(ids),batch):
            q=ids[s:s+batch]
            ctx=torch.tensor(d['contexts'][q]).float(); ch=torch.tensor(d['chords'][q]).long(); ro=torch.tensor(d['roles'][q]).long(); me=torch.tensor(d['meters'][q]).long(); po=torch.tensor(d['positions'][q]).float(); y=torch.tensor(d['targets'][q]).float()
            pred=model(ctx,ch,ro,me,po); loss=nn.functional.mse_loss(pred,y)
            if train: opt.zero_grad(); loss.backward(); nn.utils.clip_grad_norm_(model.parameters(),1.0); opt.step()
            ls.append(float(loss.detach()))
        return float(np.mean(ls)) if ls else 0.0
    best=None; bestv=1e9; hist=[]
    for ep in range(1,epochs+1):
        model.train(); tr=run(idx,True); model.eval();
        with torch.no_grad(): va=run(val)
        hist.append({'epoch':ep,'trainLoss':tr,'validationLoss':va})
        if va<bestv: bestv=va; best={k:v.detach().cpu().numpy() for k,v in model.state_dict().items()}
    model.load_state_dict({k:torch.tensor(v) for k,v in best.items()}); model.eval()
    with torch.no_grad(): ho=run(hold)
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True); np.savez_compressed(out/'phrase_planner_model_v1.npz',**best)
    rep={'schema':'dna-multibar-phrase-training','version':'1.0','epochs':epochs,'bestValidationLoss':bestv,'holdoutLoss':ho,'trainSamples':len(idx),'validationSamples':len(val),'holdoutSamples':len(hold),'velocityInput':False,'velocityOutput':False,'history':hist}
    (out/'phrase_planner_training_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); return rep

class PhrasePlannerInference:
    def __init__(self,checkpoint:str|Path):
        self.model=PhrasePlannerNet(); z=np.load(checkpoint); self.model.load_state_dict({k:torch.tensor(z[k]) for k in z.files}); self.model.eval()
    def input_from_midi(self,midi_bytes:bytes,start_bar:int,role:str):
        if role not in TARGET_ROLES: raise ValueError('phrase planner supports drums/bass')
        row=_song_rows(midi_bytes,'phrase-context.mid')
        if row is None: raise ValueError('unsupported MIDI')
        feats,chords,meter=row; st=start_bar-1; c0=st-CONTEXT_SIDE; c1=st+PHRASE_BARS+CONTEXT_SIDE
        if c0<0 or c1>len(feats): raise ValueError('phrase lacks full context')
        ctx=feats[c0:c1].copy(); rid=ROLES.index(role); ctx[CONTEXT_SIDE:CONTEXT_SIDE+PHRASE_BARS,rid]=0
        return ctx,chords[c0:c1].copy(),0 if role=='drums' else 1,meter,st/max(1,len(feats)-1)
    def predict(self,midi_bytes:bytes,start_bar:int,role:str)->np.ndarray:
        ctx,ch,rid,meter,pos=self.input_from_midi(midi_bytes,start_bar,role)
        with torch.no_grad(): y=self.model(torch.tensor(ctx[None]).float(),torch.tensor(ch[None]).long(),torch.tensor([rid]),torch.tensor([meter]),torch.tensor([pos],dtype=torch.float32))
        return y[0].numpy()
    @staticmethod
    def compatibility(pred:np.ndarray,actual:np.ndarray)->float:
        mae=float(np.mean(np.abs(np.asarray(pred)-np.asarray(actual))))
        return 1.0/(1.0+5.0*mae)
