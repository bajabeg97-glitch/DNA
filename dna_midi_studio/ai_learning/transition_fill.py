from __future__ import annotations
from pathlib import Path
from hashlib import sha256
import json
import numpy as np
import torch
from torch import nn

# velocity-free 7 feature order inherited from phrase context:
# density, registerCenter, registerSpan, gate, onsetMean, onsetSpread, presence
FEATURES=("density","registerCenter","registerSpan","gate","onsetMean","onsetSpread","presence")


def build_transition_dataset(phrase_dir:str|Path, section_dir:str|Path, out_dir:str|Path):
    phrase=Path(phrase_dir); section=Path(section_dir); out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    p=np.load(phrase/'phrase_context_v1.npz'); s=np.load(section/'section_context_v1.npz')
    targets=p['targets'].astype(np.float32)
    # Transition profile: last two target bars relative to the first two target bars.
    pre=targets[:,:2].mean(axis=1); post=targets[:,2:].mean(axis=1); delta=(post-pre).astype(np.float32)
    # real observed transition strength from target role itself, not from velocity
    strength=np.clip(np.mean(np.abs(delta[:,[0,3,4,5,6]]),axis=1)*2.2,0,1).astype(np.float32)
    # weak candidate: section transition proximity AND meaningful target-role movement
    candidate=((s['transition']>=0.45)&(strength>=0.20)).astype(np.int64)
    np.savez_compressed(out/'transition_fill_context_v1.npz',
        contexts=p['contexts'],chords=p['chords'],roles=p['roles'],meters=p['meters'],positions=p['positions'],
        section_classes=s['section_classes'],section_transition=s['transition'],target_delta=delta,
        transition_strength=strength,transition_candidate=candidate,song_ids=p['song_ids'],starts=p['starts'],split=p['split'])
    meta={
      'schema':'dna-transition-fill-context','version':'1.0','samples':int(len(delta)),
      'train':int((p['split']==0).sum()),'validation':int((p['split']==1).sum()),'holdout':int((p['split']==2).sum()),
      'songs':int(len(np.unique(p['song_ids']))),'candidateCount':int(candidate.sum()),
      'labels':'WEAK_TARGET_ROLE_TRANSITION_CANDIDATE_NOT_CONFIRMED_KORG_FILL',
      'drumFill':'ONLY_AFTER_RUNTIME_DRUM_FILL_CRITERIA','break':'NOT_INFERRED',
      'velocityUsed':False,'features':FEATURES,
      'phraseHash':sha256((phrase/'phrase_context_v1.npz').read_bytes()).hexdigest(),
      'sectionHash':sha256((section/'section_context_v1.npz').read_bytes()).hexdigest(),
      'datasetHash':sha256((out/'transition_fill_context_v1.npz').read_bytes()).hexdigest(),
    }
    (out/'transition_fill_manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8'); return meta


class TransitionFillNet(nn.Module):
    def __init__(self,d=24):
        super().__init__(); self.d=d
        self.bar=nn.Sequential(nn.Linear(4*7+13,d),nn.ReLU()); self.role=nn.Embedding(2,d); self.meter=nn.Embedding(7,d); self.pos=nn.Linear(1,d)
        self.gru=nn.GRU(d,d,batch_first=True,bidirectional=True)
        self.shared=nn.Sequential(nn.Linear(d*2+d*3,d*2),nn.ReLU())
        self.candidate=nn.Linear(d*2,1); self.strength=nn.Linear(d*2,1); self.delta=nn.Linear(d*2,7)
    def forward(self,contexts,chords,roles,meters,positions):
        oh=torch.nn.functional.one_hot(chords.clamp(0,12),13).float(); x=torch.cat([contexts.reshape(contexts.shape[0],contexts.shape[1],-1),oh],-1)
        h,_=self.gru(self.bar(x)); z=self.shared(torch.cat([h.mean(1),self.role(roles),self.meter(meters.clamp(0,6)),self.pos(positions[:,None])],-1))
        return {'candidate':self.candidate(z).squeeze(-1),'strength':torch.sigmoid(self.strength(z)).squeeze(-1),'delta':self.delta(z)}


def train_transition_fill(dataset_dir:str|Path,out_dir:str|Path,epochs:int=2,batch:int=512,seed:int=831):
    torch.manual_seed(seed); np.random.seed(seed); d=np.load(Path(dataset_dir)/'transition_fill_context_v1.npz'); model=TransitionFillNet(); opt=torch.optim.AdamW(model.parameters(),lr=2e-3)
    tr=np.flatnonzero(d['split']==0); va=np.flatnonzero(d['split']==1); ho=np.flatnonzero(d['split']==2)
    def run(ids,train=False):
        ids=ids.copy();
        if train: np.random.shuffle(ids)
        ls=[]; correct=total=0
        for st in range(0,len(ids),batch):
            q=ids[st:st+batch]
            ctx=torch.tensor(d['contexts'][q]).float(); ch=torch.tensor(d['chords'][q]).long(); ro=torch.tensor(d['roles'][q]).long(); me=torch.tensor(d['meters'][q]).long(); po=torch.tensor(d['positions'][q]).float()
            yc=torch.tensor(d['transition_candidate'][q]).float(); ys=torch.tensor(d['transition_strength'][q]).float(); yd=torch.tensor(d['target_delta'][q]).float()
            o=model(ctx,ch,ro,me,po); loss=nn.functional.binary_cross_entropy_with_logits(o['candidate'],yc)+.7*nn.functional.mse_loss(o['strength'],ys)+.8*nn.functional.smooth_l1_loss(o['delta'],yd)
            if train: opt.zero_grad(); loss.backward(); nn.utils.clip_grad_norm_(model.parameters(),1.0); opt.step()
            ls.append(float(loss.detach())); pred=(torch.sigmoid(o['candidate'])>=.5).float(); correct+=int((pred==yc).sum()); total+=len(q)
        return float(np.mean(ls)),correct/max(1,total)
    best=None; bestv=1e9; hist=[]
    for ep in range(1,epochs+1):
        model.train(); tl,ta=run(tr,True); model.eval();
        with torch.no_grad(): vl,vaacc=run(va)
        hist.append({'epoch':ep,'trainLoss':tl,'trainCandidateAccuracy':ta,'validationLoss':vl,'validationCandidateAccuracy':vaacc})
        if vl<bestv: bestv=vl; best={k:v.detach().cpu().numpy() for k,v in model.state_dict().items()}
    model.load_state_dict({k:torch.tensor(v) for k,v in best.items()}); model.eval();
    with torch.no_grad(): hl,ha=run(ho)
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True); np.savez_compressed(out/'transition_fill_model_v1.npz',**best)
    rep={'schema':'dna-transition-fill-training','version':'1.0','epochs':epochs,'bestValidationLoss':bestv,'holdoutLoss':hl,'holdoutCandidateAccuracy':ha,'trainSamples':len(tr),'validationSamples':len(va),'holdoutSamples':len(ho),'velocityInput':False,'velocityOutput':False,'labels':'WEAK_TRANSITION_CANDIDATES','history':hist}
    (out/'transition_fill_training_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); return rep


class TransitionFillInference:
    def __init__(self,checkpoint:str|Path):
        self.model=TransitionFillNet(); z=np.load(checkpoint); self.model.load_state_dict({k:torch.tensor(z[k]) for k in z.files}); self.model.eval()
    def predict(self,ctx,ch,rid,meter,pos):
        with torch.no_grad(): o=self.model(torch.tensor(ctx[None]).float(),torch.tensor(ch[None]).long(),torch.tensor([rid]).long(),torch.tensor([meter]).long(),torch.tensor([pos],dtype=torch.float32))
        return {'transitionCandidateProbability':float(torch.sigmoid(o['candidate'][0])),'transitionStrength':float(o['strength'][0]),'targetDelta':o['delta'][0].numpy().astype(float).tolist(),'velocityUsed':False,'labelAuthority':'WEAK_LEARNED_INTENT'}
    @staticmethod
    def compatibility(intent:dict, observed_delta):
        a=np.asarray(intent['targetDelta'],np.float32); b=np.asarray(observed_delta,np.float32)
        return float(1.0/(1.0+3.0*np.mean(np.abs(a-b))))
