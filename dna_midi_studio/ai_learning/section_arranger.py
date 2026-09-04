from __future__ import annotations
from pathlib import Path
from hashlib import sha256
import json, zipfile
import numpy as np
import torch
from torch import nn
from .phrase_planner import CONTEXT_BARS, CONTEXT_SIDE, PHRASE_BARS, TARGET_ROLES

SECTION_TYPES=("intro","verse","chorus","bridge","ending")


def build_section_dataset(zip_path:str|Path, phrase_dir:str|Path, out_dir:str|Path):
    """Fast weak-label ETL from the already source-disjoint 4.28 phrase context.
    Section labels are heuristic candidates, never device/ground-truth claims.
    """
    zpath=Path(zip_path); phrase=Path(phrase_dir); out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    base=np.load(phrase/'phrase_context_v1.npz')
    song_ids=base['song_ids'].astype(np.int64); positions=base['positions'].astype(np.float32); contexts=base['contexts'].astype(np.float32)
    # Energy intentionally excludes velocity: density/presence/onset features only.
    # target phrase is central 4 bars; role may be masked, so aggregate remaining arrangement roles.
    central=contexts[:,CONTEXT_SIDE:CONTEXT_SIDE+PHRASE_BARS]
    density=central[:,:,:,0].mean(axis=(1,2)); presence=central[:,:,:,-1].mean(axis=(1,2)); energy=.72*density+.28*presence
    classes=np.ones(len(song_ids),np.int64); trans=np.zeros(len(song_ids),np.float32); inten=np.zeros(len(song_ids),np.float32)
    for sid in np.unique(song_ids):
        ix=np.flatnonzero(song_ids==sid); e=energy[ix]; q35=float(np.quantile(e,.35)); q72=float(np.quantile(e,.72)); emin=float(e.min()); emax=float(e.max()); span=max(1e-6,emax-emin)
        for j in ix:
            pos=float(positions[j]); ee=float(energy[j]); inten[j]=(ee-emin)/span
            bar_e=central[j,:,:,0].mean(axis=1); jump=float(np.max(np.abs(np.diff(bar_e)))) if len(bar_e)>1 else 0.0
            trans[j]=min(1.0,jump*2.5)
            if pos<.08 and ee<=q72: cls=0
            elif pos>.90 and ee<=q72: cls=4
            elif ee>=q72: cls=2
            elif trans[j]>.72 and .15<pos<.85: cls=3
            else: cls=1
            classes[j]=cls
    np.savez_compressed(out/'section_context_v1.npz',contexts=base['contexts'],chords=base['chords'],roles=base['roles'],meters=base['meters'],positions=base['positions'],section_classes=classes,transition=trans,energy=inten,song_ids=base['song_ids'],starts=base['starts'],split=base['split'])
    meta={'schema':'dna-section-context-dataset','version':'1.1','songs':len(np.unique(song_ids)),'samples':len(song_ids),'train':int((base['split']==0).sum()),'validation':int((base['split']==1).sum()),'holdout':int((base['split']==2).sum()),'sectionTypes':SECTION_TYPES,'sectionLabels':'WEAK_HEURISTIC_CANDIDATES_FROM_VELOCITY_FREE_CONTEXT','fillBreak':'TRANSITION_ONLY_NOT_CONFIRMED_SECTION','velocityUsed':False,'sourceHash':sha256(zpath.read_bytes()).hexdigest(),'contextDatasetHash':sha256((phrase/'phrase_context_v1.npz').read_bytes()).hexdigest(),'datasetHash':sha256((out/'section_context_v1.npz').read_bytes()).hexdigest()}
    (out/'section_context_manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8'); return meta


class SectionArrangerNet(nn.Module):
    def __init__(self,d=28):
        super().__init__(); self.d=d
        self.bar=nn.Sequential(nn.Linear(4*7+13,d),nn.ReLU()); self.role=nn.Embedding(2,d); self.meter=nn.Embedding(7,d); self.pos=nn.Linear(1,d)
        self.gru=nn.GRU(d,d,batch_first=True,bidirectional=True)
        self.shared=nn.Sequential(nn.Linear(d*2+d*3,d*2),nn.ReLU())
        self.section=nn.Linear(d*2,len(SECTION_TYPES)); self.transition=nn.Linear(d*2,1); self.energy=nn.Linear(d*2,1)
    def forward(self,contexts,chords,roles,meters,positions):
        oh=torch.nn.functional.one_hot(chords.clamp(0,12),13).float(); x=torch.cat([contexts.reshape(contexts.shape[0],CONTEXT_BARS,-1),oh],-1)
        h,_=self.gru(self.bar(x)); p=h.mean(1); z=self.shared(torch.cat([p,self.role(roles),self.meter(meters.clamp(0,6)),self.pos(positions[:,None])],-1))
        return {'section':self.section(z),'transition':torch.sigmoid(self.transition(z)).squeeze(-1),'energy':torch.sigmoid(self.energy(z)).squeeze(-1)}


def train_section_arranger(dataset_dir:str|Path,out_dir:str|Path,epochs:int=2,batch:int=512,seed:int=830):
    torch.manual_seed(seed); np.random.seed(seed); d=np.load(Path(dataset_dir)/'section_context_v1.npz'); model=SectionArrangerNet(); opt=torch.optim.AdamW(model.parameters(),lr=2e-3)
    tr=np.flatnonzero(d['split']==0); va=np.flatnonzero(d['split']==1); ho=np.flatnonzero(d['split']==2)
    def run(ids,train=False):
        ids=ids.copy();
        if train: np.random.shuffle(ids)
        losses=[]; correct=total=0
        for s in range(0,len(ids),batch):
            q=ids[s:s+batch]; ctx=torch.tensor(d['contexts'][q]).float(); ch=torch.tensor(d['chords'][q]).long(); ro=torch.tensor(d['roles'][q]).long(); me=torch.tensor(d['meters'][q]).long(); po=torch.tensor(d['positions'][q]).float(); yc=torch.tensor(d['section_classes'][q]).long(); yt=torch.tensor(d['transition'][q]).float(); ye=torch.tensor(d['energy'][q]).float()
            o=model(ctx,ch,ro,me,po); loss=nn.functional.cross_entropy(o['section'],yc)+.7*nn.functional.mse_loss(o['transition'],yt)+.5*nn.functional.mse_loss(o['energy'],ye)
            if train: opt.zero_grad(); loss.backward(); nn.utils.clip_grad_norm_(model.parameters(),1.0); opt.step()
            losses.append(float(loss.detach())); correct+=int((o['section'].argmax(-1)==yc).sum()); total+=len(q)
        return float(np.mean(losses)) if losses else 0.0, correct/max(1,total)
    best=None; bestv=1e9; hist=[]
    for ep in range(1,epochs+1):
        model.train(); tl,ta=run(tr,True); model.eval();
        with torch.no_grad(): vl,vaacc=run(va)
        hist.append({'epoch':ep,'trainLoss':tl,'trainSectionAccuracy':ta,'validationLoss':vl,'validationSectionAccuracy':vaacc})
        if vl<bestv: bestv=vl; best={k:v.detach().cpu().numpy() for k,v in model.state_dict().items()}
    model.load_state_dict({k:torch.tensor(v) for k,v in best.items()}); model.eval();
    with torch.no_grad(): hl,ha=run(ho)
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True); np.savez_compressed(out/'section_arranger_model_v1.npz',**best)
    rep={'schema':'dna-section-arranger-training','version':'1.0','epochs':epochs,'bestValidationLoss':bestv,'holdoutLoss':hl,'holdoutSectionAccuracy':ha,'trainSamples':len(tr),'validationSamples':len(va),'holdoutSamples':len(ho),'velocityInput':False,'velocityOutput':False,'sectionLabels':'HEURISTIC_CANDIDATES','history':hist}
    (out/'section_arranger_training_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); return rep


class SectionArrangerInference:
    def __init__(self,checkpoint:str|Path):
        self.model=SectionArrangerNet(); z=np.load(checkpoint); self.model.load_state_dict({k:torch.tensor(z[k]) for k in z.files}); self.model.eval()
    def predict_from_phrase_input(self,ctx,ch,rid,meter,pos):
        with torch.no_grad(): o=self.model(torch.tensor(ctx[None]).float(),torch.tensor(ch[None]).long(),torch.tensor([rid]).long(),torch.tensor([meter]).long(),torch.tensor([pos],dtype=torch.float32))
        probs=torch.softmax(o['section'],dim=-1)[0].numpy(); idx=int(probs.argmax())
        return {'section':SECTION_TYPES[idx],'sectionConfidence':float(probs[idx]),'transitionProximity':float(o['transition'][0]),'energyIntent':float(o['energy'][0]),'velocityUsed':False}
