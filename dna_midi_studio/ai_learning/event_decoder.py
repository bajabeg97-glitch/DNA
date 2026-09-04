from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from hashlib import sha256
import json, math, random, zipfile
import numpy as np
import torch
from torch import nn
from .song_context import ROLES, FEATURES, WINDOW, _role, _bar_role_features, _song_rows

EVENTS=24
ROLE_NAMES=("drums","bass","harmony","melody")


def _encode_bar_events(notes,start,end,role):
    span=max(1,end-start)
    own=[n for n in notes if start<=n['start']<end and _role(n)==role]
    own.sort(key=lambda n:(n['start'],n['pitch'],n['end']))
    out=np.zeros((EVENTS,4),np.int64)
    for i,n in enumerate(own[:EVENTS]):
        pos=min(95,max(0,round((n['start']-start)/span*96)))
        dur=min(95,max(1,round((n['end']-n['start'])/span*96)))
        if role=='drums': code=128+int(n['pitch'])
        else: code=int(n['pitch'])
        out[i]=[pos,dur,code,1]
    return out


def build_event_dataset(zip_path:str|Path,out_dir:str|Path,seed:int=825,stride:int=2,max_samples_per_song:int=160):
    import dna_builder
    zpath=Path(zip_path); out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    rng=random.Random(seed); rows=[]; songs=[]
    with zipfile.ZipFile(zpath) as z:
        for name in sorted(n for n in z.namelist() if n.lower().endswith(('.mid','.kar'))):
            data=z.read(name); h=sha256(data).hexdigest()
            try: p=dna_builder.parse_midi(data,name)
            except Exception: continue
            if not p.get('ppq'): continue
            meter=p['meter']; bt=p['ppq']*meter[0]*4/meter[1]
            notes=[n for t in p['tracks'] for n in t['notes']]
            if not notes: continue
            total=max(1,int(math.ceil(max(n['end'] for n in notes)/max(1,bt))))
            if total<2*WINDOW+1: continue
            feats=np.zeros((total,4,len(FEATURES)),np.float32)
            chords=np.full(total,12,np.int64); hist=np.zeros((total,12),np.float64)
            by=[[[] for _ in ROLES] for _ in range(total)]
            for n in notes:
                bi=min(total-1,max(0,int(n['start']//bt))); ri=ROLES.index(_role(n)); by[bi][ri].append(n)
                if n['channel']!=10: hist[bi,n['pitch']%12]+=max(1,n['end']-n['start'])
            for bi in range(total):
                st=bi*bt; en=(bi+1)*bt
                for ri in range(4): feats[bi,ri]=_bar_role_features(by[bi][ri],st,en)
                if hist[bi].sum()>0: chords[bi]=int(hist[bi].argmax())
            sid=len(songs); songs.append({'name':name,'sha256':h,'bars':total})
            centers=list(range(WINDOW,total-WINDOW,stride)); rng.shuffle(centers); centers=centers[:max_samples_per_song]
            for c in centers:
                ctx=feats[c-WINDOW:c+WINDOW+1].copy(); ch=chords[c-WINDOW:c+WINDOW+1].copy()
                for role in ('drums','bass'):
                    ev=_encode_bar_events(notes,c*bt,(c+1)*bt,role)
                    if ev[:,3].sum()==0: continue
                    masked=ctx.copy(); masked[WINDOW,ROLES.index(role)]=0
                    rows.append((sid,c,0 if role=='drums' else 1,masked,ch,ev))
    order=list(range(len(songs))); rng.shuffle(order); n=len(order); a=max(1,int(n*.8)); b=max(a+1,int(n*.9))
    sb={sid:(0 if i<a else 1 if i<b else 2) for i,sid in enumerate(order)}
    contexts=np.stack([r[3] for r in rows]); chords=np.stack([r[4] for r in rows]); roles=np.asarray([r[2] for r in rows],np.int64)
    targets=np.stack([r[5] for r in rows]); song_ids=np.asarray([r[0] for r in rows],np.int64); centers=np.asarray([r[1] for r in rows],np.int64)
    split=np.asarray([sb[r[0]] for r in rows],np.int8)
    np.savez_compressed(out/'event_decoder_v1.npz',contexts=contexts,chords=chords,roles=roles,targets=targets,song_ids=song_ids,centers=centers,split=split)
    (out/'event_decoder_songs.json').write_text(json.dumps(songs,ensure_ascii=False,indent=2),encoding='utf-8')
    meta={'schema':'dna-event-decoder-dataset','version':'1.0','songs':len(songs),'samples':len(rows),'train':int((split==0).sum()),'validation':int((split==1).sum()),'holdout':int((split==2).sum()),'velocityUsed':False,'sourceHash':sha256(zpath.read_bytes()).hexdigest(),'datasetHash':sha256((out/'event_decoder_v1.npz').read_bytes()).hexdigest()}
    (out/'event_decoder_manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8'); return meta

class EventDecoderNet(nn.Module):
    def __init__(self,d=32):
        super().__init__(); self.d=d
        self.ctx=nn.Sequential(nn.Linear(9*4*7+9+2,96),nn.ReLU(),nn.Linear(96,d))
        self.slot=nn.Embedding(EVENTS,d); self.role=nn.Embedding(2,d)
        self.body=nn.TransformerEncoder(nn.TransformerEncoderLayer(d,4,64,batch_first=True),1)
        self.pos=nn.Linear(d,96); self.dur=nn.Linear(d,96); self.code=nn.Linear(d,256); self.pres=nn.Linear(d,2)
    def forward(self,contexts,chords,roles):
        b=contexts.shape[0]; chord=torch.nn.functional.one_hot(chords.clamp(0,12),13).float()[...,:1].reshape(b,-1) # compact root-known signal
        x=torch.cat([contexts.reshape(b,-1), chord, torch.nn.functional.one_hot(roles,2).float()],1)
        c=self.ctx(x).unsqueeze(1); slots=self.slot(torch.arange(EVENTS,device=contexts.device))[None,:,:].expand(b,-1,-1)+self.role(roles)[:,None,:]+c
        h=self.body(slots)
        return {'pos':self.pos(h),'dur':self.dur(h),'code':self.code(h),'pres':self.pres(h)}


def train_event_decoder(dataset_dir:str|Path,out_dir:str|Path,epochs:int=2,batch:int=512,seed:int=825):
    torch.manual_seed(seed); np.random.seed(seed); d=np.load(Path(dataset_dir)/'event_decoder_v1.npz')
    model=EventDecoderNet(); opt=torch.optim.AdamW(model.parameters(),lr=2e-3)
    idx=np.flatnonzero(d['split']==0); val=np.flatnonzero(d['split']==1); hold=np.flatnonzero(d['split']==2)
    def loss_for(ids,train=False):
        losses=[]
        if train: np.random.shuffle(ids)
        for s in range(0,len(ids),batch):
            q=ids[s:s+batch]; ctx=torch.tensor(d['contexts'][q]).float(); ch=torch.tensor(d['chords'][q]).long(); ro=torch.tensor(d['roles'][q]).long(); y=torch.tensor(d['targets'][q]).long(); o=model(ctx,ch,ro)
            pres=y[:,:,3]; lp=nn.functional.cross_entropy(o['pres'].reshape(-1,2),pres.reshape(-1))
            mask=pres.bool()
            if mask.any():
                lp=lp+nn.functional.cross_entropy(o['pos'][mask],y[:,:,0][mask])+nn.functional.cross_entropy(o['dur'][mask],y[:,:,1][mask])+nn.functional.cross_entropy(o['code'][mask],y[:,:,2][mask])
            if train: opt.zero_grad(); lp.backward(); nn.utils.clip_grad_norm_(model.parameters(),1.0); opt.step()
            losses.append(float(lp.detach()))
        return float(np.mean(losses)) if losses else 0.0
    hist=[]; best=None; bestv=1e9
    for ep in range(1,epochs+1):
        model.train(); tr=loss_for(idx.copy(),True); model.eval();
        with torch.no_grad(): va=loss_for(val.copy(),False)
        hist.append({'epoch':ep,'trainLoss':tr,'validationLoss':va})
        if va<bestv: bestv=va; best={k:v.detach().cpu().numpy() for k,v in model.state_dict().items()}
    model.load_state_dict({k:torch.tensor(v) for k,v in best.items()}); model.eval()
    with torch.no_grad(): ho=loss_for(hold.copy(),False)
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True); np.savez_compressed(out/'event_decoder_model_v1.npz',**best)
    rep={'schema':'dna-event-decoder-training','version':'1.0','epochs':epochs,'bestValidationLoss':bestv,'holdoutLoss':ho,'history':hist,'velocityInput':False,'velocityOutput':False,'trainSamples':len(idx),'validationSamples':len(val),'holdoutSamples':len(hold)}
    (out/'event_decoder_training_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); return rep

class EventDecoderInference:
    def __init__(self,checkpoint:str|Path):
        self.model=EventDecoderNet(); z=np.load(checkpoint); self.model.load_state_dict({k:torch.tensor(z[k]) for k in z.files}); self.model.eval()
    def input_from_midi(self,midi_bytes:bytes,bar_number:int,role:int):
        row=_song_rows(midi_bytes,'event-decoder.mid')
        if row is None: raise ValueError('unsupported MIDI')
        feats,chords,_meter=row; c=bar_number-1
        if c<WINDOW or c>=len(feats)-WINDOW: raise ValueError('bar lacks full context window')
        ctx=feats[c-WINDOW:c+WINDOW+1].copy(); ctx[WINDOW,role]=0
        return ctx,chords[c-WINDOW:c+WINDOW+1].copy()
    def generate(self,context,chords,role:int,variant:int=0):
        with torch.no_grad(): o=self.model(torch.tensor(context[None]).float(),torch.tensor(chords[None]).long(),torch.tensor([role]).long())
        rows=[]; pres=o['pres'][0].softmax(-1)[:,1].numpy();
        for i in range(EVENTS):
            if pres[i] < (0.42+0.06*variant): continue
            def pick(logits,k):
                vals=torch.topk(logits,k=min(3,logits.numel())).indices; return int(vals[min(variant,len(vals)-1)])
            pos=pick(o['pos'][0,i],3); dur=max(1,pick(o['dur'][0,i],3)); code=pick(o['code'][0,i],3)
            rows.append([pos,dur,code,1])
        return rows
