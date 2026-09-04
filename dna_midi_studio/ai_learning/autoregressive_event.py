from __future__ import annotations
from pathlib import Path
import json, numpy as np, torch
from torch import nn
from .event_decoder import EVENTS

class AutoregressiveEventNet(nn.Module):
    """Small causal event decoder. Velocity is intentionally absent."""
    def __init__(self,d=32):
        super().__init__(); self.d=d
        self.ctx=nn.Sequential(nn.Linear(9*4*7+9+2,96),nn.ReLU(),nn.Linear(96,d))
        self.pos_e=nn.Embedding(97,d); self.dur_e=nn.Embedding(97,d); self.code_e=nn.Embedding(257,d); self.pres_e=nn.Embedding(3,d)
        self.role=nn.Embedding(2,d); self.gru=nn.GRU(d*5,d,batch_first=True)
        self.pos=nn.Linear(d,96); self.dur=nn.Linear(d,96); self.code=nn.Linear(d,256); self.pres=nn.Linear(d,2)
    def _context(self,contexts,chords,roles):
        b=contexts.shape[0]
        chord=torch.nn.functional.one_hot(chords.clamp(0,12),13).float()[...,:1].reshape(b,-1)
        x=torch.cat([contexts.reshape(b,-1),chord,torch.nn.functional.one_hot(roles,2).float()],1)
        return self.ctx(x)
    def forward(self,contexts,chords,roles,teacher):
        b=contexts.shape[0]; c=self._context(contexts,chords,roles); r=self.role(roles)
        prev=torch.zeros((b,EVENTS,4),dtype=torch.long,device=contexts.device)
        prev[:,1:]=teacher[:,:-1]
        pos=self.pos_e(prev[:,:,0].clamp(0,96)); dur=self.dur_e(prev[:,:,1].clamp(0,96)); code=self.code_e(prev[:,:,2].clamp(0,256)); pres=self.pres_e(prev[:,:,3].clamp(0,2))
        inp=torch.cat([pos,dur,code,pres,(c+r)[:,None,:].expand(-1,EVENTS,-1)],-1)
        h,_=self.gru(inp)
        return {'pos':self.pos(h),'dur':self.dur(h),'code':self.code(h),'pres':self.pres(h)}
    @torch.no_grad()
    def generate(self,contexts,chords,roles,variant=0):
        b=contexts.shape[0]; assert b==1
        c=self._context(contexts,chords,roles); r=self.role(roles); hidden=None; prev=torch.zeros((1,1,4),dtype=torch.long,device=contexts.device); out=[]
        for i in range(EVENTS):
            inp=torch.cat([self.pos_e(prev[:,:,0]),self.dur_e(prev[:,:,1]),self.code_e(prev[:,:,2]),self.pres_e(prev[:,:,3]),(c+r)[:,None,:]],-1)
            h,hidden=self.gru(inp,hidden); h=h[:,-1]
            def pick(logits,k=3):
                ids=torch.topk(logits,k=min(k,logits.numel())).indices
                return int(ids[min(variant,len(ids)-1)])
            pprob=torch.softmax(self.pres(h)[0],-1)[1].item(); present=1 if pprob >= (0.10+0.05*variant) else 0
            if present:
                pos=pick(self.pos(h)[0]); dur=max(1,pick(self.dur(h)[0]))
                clog=self.code(h)[0].clone()
                if int(roles[0])==1:
                    mask=torch.ones_like(clog,dtype=torch.bool); mask[28:68]=False; clog[mask]=-1e9
                else:
                    clog[:128]=-1e9
                code=pick(clog)
                row=[pos,dur,code,1]; out.append(row)
            else: row=[0,0,0,0]
            prev=torch.tensor(row,dtype=torch.long,device=contexts.device).view(1,1,4)
        return out

def train_autoregressive(dataset_dir:str|Path,out_dir:str|Path,epochs=1,batch=512,seed=826):
    torch.manual_seed(seed); np.random.seed(seed); d=np.load(Path(dataset_dir)/'event_decoder_v1.npz')
    model=AutoregressiveEventNet(); opt=torch.optim.AdamW(model.parameters(),lr=2e-3)
    idx=np.flatnonzero(d['split']==0); val=np.flatnonzero(d['split']==1); hold=np.flatnonzero(d['split']==2)
    def loss_for(ids,train=False):
        losses=[]; ids=ids.copy();
        if train: np.random.shuffle(ids)
        for s in range(0,len(ids),batch):
            q=ids[s:s+batch]; ctx=torch.tensor(d['contexts'][q]).float(); ch=torch.tensor(d['chords'][q]).long(); ro=torch.tensor(d['roles'][q]).long(); y=torch.tensor(d['targets'][q]).long(); o=model(ctx,ch,ro,y)
            pres=y[:,:,3]; loss=nn.functional.cross_entropy(o['pres'].reshape(-1,2),pres.reshape(-1)); mask=pres.bool()
            if mask.any():
                loss += nn.functional.cross_entropy(o['pos'][mask],y[:,:,0][mask])
                loss += nn.functional.cross_entropy(o['dur'][mask],y[:,:,1][mask])
                loss += nn.functional.cross_entropy(o['code'][mask],y[:,:,2][mask])
            if train: opt.zero_grad(); loss.backward(); nn.utils.clip_grad_norm_(model.parameters(),1.0); opt.step()
            losses.append(float(loss.detach()))
        return float(np.mean(losses))
    hist=[]; best=None; bestv=1e9
    for ep in range(1,epochs+1):
        model.train(); tr=loss_for(idx,True); model.eval();
        with torch.no_grad(): va=loss_for(val)
        hist.append({'epoch':ep,'trainLoss':tr,'validationLoss':va})
        if va<bestv: bestv=va; best={k:v.detach().cpu().numpy() for k,v in model.state_dict().items()}
    model.load_state_dict({k:torch.tensor(v) for k,v in best.items()}); model.eval()
    with torch.no_grad(): ho=loss_for(hold)
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True); np.savez_compressed(out/'autoregressive_event_model_v2.npz',**best)
    rep={'schema':'dna-autoregressive-event-training','version':'2.0','epochs':epochs,'bestValidationLoss':bestv,'holdoutLoss':ho,'history':hist,'trainSamples':len(idx),'validationSamples':len(val),'holdoutSamples':len(hold),'velocityInput':False,'velocityOutput':False}
    (out/'autoregressive_event_training_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); return rep

class AutoregressiveEventInference:
    def __init__(self,checkpoint:str|Path):
        self.model=AutoregressiveEventNet(); z=np.load(checkpoint); self.model.load_state_dict({k:torch.tensor(z[k]) for k in z.files}); self.model.eval()
    def generate(self,context,chords,role:int,variant:int=0):
        return self.model.generate(torch.tensor(context[None]).float(),torch.tensor(chords[None]).long(),torch.tensor([role]).long(),variant)
