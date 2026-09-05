from __future__ import annotations
from pathlib import Path
from hashlib import sha256
import json, math, random, zipfile
import numpy as np
import torch
from torch import nn
from .song_context import ROLES, FEATURES, _role, _bar_role_features

PHRASE_BARS=4
CONTEXT_SIDE=4
CONTEXT_BARS=PHRASE_BARS+2*CONTEXT_SIDE
EVENTS_PER_BAR=12
TOTAL_EVENTS=PHRASE_BARS*EVENTS_PER_BAR
TARGET_ROLES=("drums","bass")


def _encode_phrase_events(notes, bar_ticks, role):
    out=np.zeros((TOTAL_EVENTS,5),np.int64)  # bar,pos,dur,code,pres
    k=0
    for bi,(start,end) in enumerate(bar_ticks):
        span=max(1,end-start)
        own=[n for n in notes if start<=n['start']<end and _role(n)==role]
        own.sort(key=lambda n:(n['start'],n['pitch'],n['end']))
        for n in own[:EVENTS_PER_BAR]:
            if k>=TOTAL_EVENTS: break
            pos=min(95,max(0,round((n['start']-start)/span*96)))
            dur=min(95,max(1,round((n['end']-n['start'])/span*96)))
            code=(128+int(n['pitch'])) if role=='drums' else int(n['pitch'])
            out[k]=[bi,pos,dur,code,1]; k+=1
    return out


def build_multibar_event_dataset(zip_path:str|Path,out_dir:str|Path,seed:int=829,stride:int=2,max_samples_per_song:int=120):
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
            if total<CONTEXT_BARS: continue
            feats=np.zeros((total,4,len(FEATURES)),np.float32); chords=np.full(total,12,np.int64)
            hist=np.zeros((total,12),np.float64); by=[[[] for _ in ROLES] for _ in range(total)]
            for n in notes:
                bi=min(total-1,max(0,int(n['start']//bt))); ri=ROLES.index(_role(n)); by[bi][ri].append(n)
                if n['channel']!=10: hist[bi,n['pitch']%12]+=max(1,n['end']-n['start'])
            for bi in range(total):
                st=bi*bt; en=(bi+1)*bt
                for ri in range(4): feats[bi,ri]=_bar_role_features(by[bi][ri],st,en)
                if hist[bi].sum()>0: chords[bi]=int(hist[bi].argmax())
            sid=len(songs); songs.append({'name':name,'sha256':h,'bars':total})
            starts=list(range(CONTEXT_SIDE,total-PHRASE_BARS-CONTEXT_SIDE+1,stride)); rng.shuffle(starts); starts=starts[:max_samples_per_song]
            for st in starts:
                c0=st-CONTEXT_SIDE; c1=st+PHRASE_BARS+CONTEXT_SIDE
                ctx=feats[c0:c1].copy(); ch=chords[c0:c1].copy()
                if len(ctx)!=CONTEXT_BARS: continue
                bar_ticks=[(int((st+i)*bt),int((st+i+1)*bt)) for i in range(PHRASE_BARS)]
                for rid,role in enumerate(TARGET_ROLES):
                    target=_encode_phrase_events(notes,bar_ticks,role)
                    if target[:,4].sum()==0: continue
                    masked=ctx.copy(); masked[CONTEXT_SIDE:CONTEXT_SIDE+PHRASE_BARS,ROLES.index(role)]=0
                    rows.append((sid,st,rid,masked,ch,target))
    order=list(range(len(songs))); rng.shuffle(order); n=len(order); a=max(1,int(n*.8)); b=max(a+1,int(n*.9))
    sb={sid:(0 if i<a else 1 if i<b else 2) for i,sid in enumerate(order)}
    contexts=np.stack([r[3] for r in rows]).astype(np.float32); chords=np.stack([r[4] for r in rows]).astype(np.int64)
    roles=np.asarray([r[2] for r in rows],np.int64); targets=np.stack([r[5] for r in rows]).astype(np.int64)
    song_ids=np.asarray([r[0] for r in rows],np.int64); starts=np.asarray([r[1] for r in rows],np.int64); split=np.asarray([sb[r[0]] for r in rows],np.int8)
    np.savez_compressed(out/'multibar_event_v1.npz',contexts=contexts,chords=chords,roles=roles,targets=targets,song_ids=song_ids,starts=starts,split=split)
    (out/'multibar_event_songs.json').write_text(json.dumps(songs,ensure_ascii=False,indent=2),encoding='utf-8')
    meta={'schema':'dna-multibar-event-dataset','version':'1.0','songs':len(songs),'samples':len(rows),'train':int((split==0).sum()),'validation':int((split==1).sum()),'holdout':int((split==2).sum()),'phraseBars':PHRASE_BARS,'eventsPerBar':EVENTS_PER_BAR,'velocityUsed':False,'sourceHash':sha256(zpath.read_bytes()).hexdigest(),'datasetHash':sha256((out/'multibar_event_v1.npz').read_bytes()).hexdigest()}
    (out/'multibar_event_manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8'); return meta


def build_multibar_event_dataset_from_phrase(zip_path:str|Path,phrase_dir:str|Path,out_dir:str|Path):
    """Fast ETL: reuse 4.28 context tensors/splits and derive only event targets from originals."""
    zpath=Path(zip_path); phrase=Path(phrase_dir); out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    base=np.load(phrase/'phrase_context_v1.npz'); songs=json.loads((phrase/'phrase_context_songs.json').read_text(encoding='utf-8'))
    wanted={}
    for i,(sid,st,rid) in enumerate(zip(base['song_ids'].tolist(),base['starts'].tolist(),base['roles'].tolist())):
        wanted.setdefault(int(sid),[]).append((i,int(st),int(rid)))
    targets=np.zeros((len(base['song_ids']),TOTAL_EVENTS,5),np.int64); keep=np.zeros(len(base['song_ids']),dtype=bool)
    with zipfile.ZipFile(zpath) as z:
        for sid,items in wanted.items():
            name=songs[sid]['name']
            try: data=z.read(name); p=dna_builder.parse_midi(data,name)
            except Exception: continue
            if not p.get('ppq'): continue
            meter=p['meter']; bt=p['ppq']*meter[0]*4/meter[1]; notes=[n for t in p['tracks'] for n in t['notes']]
            for i,st,rid in items:
                role=TARGET_ROLES[rid]; ticks=[(int((st+j)*bt),int((st+j+1)*bt)) for j in range(PHRASE_BARS)]
                ev=_encode_phrase_events(notes,ticks,role)
                if ev[:,4].sum(): targets[i]=ev; keep[i]=True
    contexts=base['contexts'][keep].astype(np.float32); chords=base['chords'][keep].astype(np.int64); roles=base['roles'][keep].astype(np.int64); split=base['split'][keep].astype(np.int8); song_ids=base['song_ids'][keep].astype(np.int64); starts=base['starts'][keep].astype(np.int64); targets=targets[keep]
    np.savez_compressed(out/'multibar_event_v1.npz',contexts=contexts,chords=chords,roles=roles,targets=targets,song_ids=song_ids,starts=starts,split=split)
    (out/'multibar_event_songs.json').write_text(json.dumps(songs,ensure_ascii=False,indent=2),encoding='utf-8')
    meta={'schema':'dna-multibar-event-dataset','version':'1.0','songs':len(songs),'samples':len(targets),'train':int((split==0).sum()),'validation':int((split==1).sum()),'holdout':int((split==2).sum()),'phraseBars':PHRASE_BARS,'eventsPerBar':EVENTS_PER_BAR,'velocityUsed':False,'sourceHash':sha256(zpath.read_bytes()).hexdigest(),'contextDatasetHash':sha256((phrase/'phrase_context_v1.npz').read_bytes()).hexdigest(),'datasetHash':sha256((out/'multibar_event_v1.npz').read_bytes()).hexdigest()}
    (out/'multibar_event_manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8'); return meta


class MultiBarEventNet(nn.Module):
    """Causal 4-bar decoder with one hidden state across the whole phrase; velocity absent."""
    def __init__(self,d=20):
        super().__init__(); self.d=d
        self.ctx=nn.Sequential(nn.Linear(CONTEXT_BARS*4*7+CONTEXT_BARS+2,64),nn.ReLU(),nn.Linear(64,d))
        self.bar_e=nn.Embedding(PHRASE_BARS+1,d); self.pos_e=nn.Embedding(97,d); self.dur_e=nn.Embedding(97,d); self.code_e=nn.Embedding(257,d); self.pres_e=nn.Embedding(3,d)
        self.role=nn.Embedding(2,d); self.slot=nn.Embedding(TOTAL_EVENTS,d)
        self.gru=nn.GRU(d*7,d,batch_first=True)
        self.bar=nn.Linear(d,PHRASE_BARS); self.pos=nn.Linear(d,96); self.dur=nn.Linear(d,96); self.code=nn.Linear(d,256); self.pres=nn.Linear(d,2)
    def _context(self,contexts,chords,roles):
        b=contexts.shape[0]; known=(chords<12).float().reshape(b,-1)
        x=torch.cat([contexts.reshape(b,-1),known,torch.nn.functional.one_hot(roles,2).float()],1)
        return self.ctx(x)
    def forward(self,contexts,chords,roles,teacher):
        b=contexts.shape[0]; c=self._context(contexts,chords,roles); r=self.role(roles)
        prev=torch.zeros((b,TOTAL_EVENTS,5),dtype=torch.long,device=contexts.device); prev[:,1:]=teacher[:,:-1]
        slot=self.slot(torch.arange(TOTAL_EVENTS,device=contexts.device))[None].expand(b,-1,-1)
        inp=torch.cat([self.bar_e(prev[:,:,0].clamp(0,PHRASE_BARS)),self.pos_e(prev[:,:,1].clamp(0,96)),self.dur_e(prev[:,:,2].clamp(0,96)),self.code_e(prev[:,:,3].clamp(0,256)),self.pres_e(prev[:,:,4].clamp(0,2)),slot,(c+r)[:,None,:].expand(-1,TOTAL_EVENTS,-1)],-1)
        h,_=self.gru(inp)
        return {'bar':self.bar(h),'pos':self.pos(h),'dur':self.dur(h),'code':self.code(h),'pres':self.pres(h)}
    @torch.no_grad()
    def generate(self,contexts,chords,roles,variant=0):
        b=contexts.shape[0]; assert b==1
        c=self._context(contexts,chords,roles); r=self.role(roles); hidden=None; prev=torch.zeros((1,1,5),dtype=torch.long,device=contexts.device); out=[]
        for i in range(TOTAL_EVENTS):
            slot=self.slot(torch.tensor([[i]],device=contexts.device))
            inp=torch.cat([self.bar_e(prev[:,:,0]),self.pos_e(prev[:,:,1]),self.dur_e(prev[:,:,2]),self.code_e(prev[:,:,3]),self.pres_e(prev[:,:,4]),slot,(c+r)[:,None,:]],-1)
            h,hidden=self.gru(inp,hidden); q=h[:,-1]
            def pick(logits,k=3):
                ids=torch.topk(logits,k=min(k,logits.numel())).indices; return int(ids[min(variant,len(ids)-1)])
            present=1 if torch.softmax(self.pres(q)[0],-1)[1].item() >= (0.08+0.04*variant) else 0
            if present:
                # Slot-to-bar schedule is fixed so every phrase bar receives capacity; hidden state remains continuous.
                bar=min(PHRASE_BARS-1,i//EVENTS_PER_BAR); pos=pick(self.pos(q)[0]); dur=max(1,pick(self.dur(q)[0]))
                clog=self.code(q)[0].clone()
                if int(roles[0])==1:
                    mask=torch.ones_like(clog,dtype=torch.bool); mask[28:68]=False; clog[mask]=-1e9
                else: clog[:128]=-1e9
                code=pick(clog); row=[bar,pos,dur,code,1]; out.append(row)
            else: row=[0,0,0,0,0]
            prev=torch.tensor(row,dtype=torch.long,device=contexts.device).view(1,1,5)
        return out


def train_multibar_event(dataset_dir:str|Path,out_dir:str|Path,epochs=1,batch=384,seed=829):
    torch.manual_seed(seed); np.random.seed(seed); d=np.load(Path(dataset_dir)/'multibar_event_v1.npz')
    model=MultiBarEventNet(); opt=torch.optim.AdamW(model.parameters(),lr=2e-3)
    idx=np.flatnonzero(d['split']==0); val=np.flatnonzero(d['split']==1); hold=np.flatnonzero(d['split']==2)
    def run(ids,train=False):
        ids=ids.copy();
        if train: np.random.shuffle(ids)
        ls=[]
        for s in range(0,len(ids),batch):
            q=ids[s:s+batch]; ctx=torch.tensor(d['contexts'][q]).float(); ch=torch.tensor(d['chords'][q]).long(); ro=torch.tensor(d['roles'][q]).long(); y=torch.tensor(d['targets'][q]).long(); o=model(ctx,ch,ro,y)
            pres=y[:,:,4]; loss=nn.functional.cross_entropy(o['pres'].reshape(-1,2),pres.reshape(-1)); mask=pres.bool()
            if mask.any():
                loss += .5*nn.functional.cross_entropy(o['bar'][mask],y[:,:,0][mask])
                loss += nn.functional.cross_entropy(o['pos'][mask],y[:,:,1][mask])
                loss += nn.functional.cross_entropy(o['dur'][mask],y[:,:,2][mask])
                loss += nn.functional.cross_entropy(o['code'][mask],y[:,:,3][mask])
            if train: opt.zero_grad(); loss.backward(); nn.utils.clip_grad_norm_(model.parameters(),1.0); opt.step()
            ls.append(float(loss.detach()))
        return float(np.mean(ls)) if ls else 0.0
    hist=[]; best=None; bestv=1e9
    for ep in range(1,epochs+1):
        model.train(); tr=run(idx,True); model.eval();
        with torch.no_grad(): va=run(val)
        hist.append({'epoch':ep,'trainLoss':tr,'validationLoss':va})
        if va<bestv: bestv=va; best={k:v.detach().cpu().numpy() for k,v in model.state_dict().items()}
    model.load_state_dict({k:torch.tensor(v) for k,v in best.items()}); model.eval();
    with torch.no_grad(): ho=run(hold)
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True); np.savez_compressed(out/'multibar_event_model_v3.npz',**best)
    rep={'schema':'dna-multibar-event-training','version':'3.0','epochs':epochs,'bestValidationLoss':bestv,'holdoutLoss':ho,'trainSamples':len(idx),'validationSamples':len(val),'holdoutSamples':len(hold),'phraseBars':PHRASE_BARS,'sharedHiddenState':True,'velocityInput':False,'velocityOutput':False,'history':hist}
    (out/'multibar_event_training_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); return rep


class MultiBarEventInference:
    def __init__(self,checkpoint:str|Path):
        self.model=MultiBarEventNet(); z=np.load(checkpoint); self.model.load_state_dict({k:torch.tensor(z[k]) for k in z.files}); self.model.eval()
    def input_from_midi(self,midi_bytes:bytes,start_bar:int,role:str):
        from .song_context import _song_rows
        if role not in TARGET_ROLES: raise ValueError('multibar decoder supports drums/bass')
        row=_song_rows(midi_bytes,'multibar-event.mid')
        if row is None: raise ValueError('unsupported MIDI')
        feats,chords,_meter=row; st=start_bar-1; c0=st-CONTEXT_SIDE; c1=st+PHRASE_BARS+CONTEXT_SIDE
        if c0<0 or c1>len(feats): raise ValueError('phrase lacks full context')
        ctx=feats[c0:c1].copy(); ctx[CONTEXT_SIDE:CONTEXT_SIDE+PHRASE_BARS,ROLES.index(role)]=0
        return ctx,chords[c0:c1].copy(),0 if role=='drums' else 1
    def generate(self,midi_bytes:bytes,start_bar:int,role:str,variant:int=0):
        ctx,ch,rid=self.input_from_midi(midi_bytes,start_bar,role)
        return self.model.generate(torch.tensor(ctx[None]).float(),torch.tensor(ch[None]).long(),torch.tensor([rid]).long(),variant)
