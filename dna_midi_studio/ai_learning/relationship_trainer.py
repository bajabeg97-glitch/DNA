from __future__ import annotations
from dataclasses import dataclass,asdict
from pathlib import Path
from hashlib import sha256
import json,random,time
import numpy as np
import torch
from torch import nn
from torch.utils.data import Dataset,DataLoader
from .relationship_model import RelationshipTransformer,RelationshipModelConfig

@dataclass
class RelationshipTrainingConfig:
    epochs:int=24;batch_size:int=16;learning_rate:float=4e-4;weight_decay:float=1e-2;seed:int=800;patience:int=5;device:str='auto'
    max_train_samples:int|None=None;max_validation_samples:int|None=None;max_holdout_samples:int|None=None
    def to_dict(self):return asdict(self)
class DS(Dataset):
    def __init__(self,z,idx):self.z=z;self.idx=np.asarray(idx)
    def __len__(self):return len(self.idx)
    def __getitem__(self,j):
        i=self.idx[j]
        return tuple(torch.from_numpy(np.asarray(self.z[k][i])) if np.asarray(self.z[k][i]).ndim else torch.tensor(self.z[k][i]) for k in ('features','pairs','kind','delay_qn','duration_ratio','median_interval'))
def _save(m,p):np.savez_compressed(p,**{k:v.detach().cpu().numpy() for k,v in m.state_dict().items()})
def _load(m,p):
    z=np.load(p);s=m.state_dict()
    for k in s:s[k]=torch.from_numpy(z[k]).to(s[k].dtype)
    m.load_state_dict(s)
class RelationshipTrainer:
    def __init__(self,mc=None,tc=None):self.mc=mc or RelationshipModelConfig();self.tc=tc or RelationshipTrainingConfig()
    def train(self,dataset_path:str|Path,out_dir:str|Path):
        random.seed(self.tc.seed);np.random.seed(self.tc.seed);torch.manual_seed(self.tc.seed)
        z=np.load(dataset_path);sp=z['split'];ti=np.where(sp==0)[0];vi=np.where(sp==1)[0];hi=np.where(sp==2)[0]
        if self.tc.max_train_samples:ti=ti[:self.tc.max_train_samples]
        if self.tc.max_validation_samples:vi=vi[:self.tc.max_validation_samples]
        if self.tc.max_holdout_samples:hi=hi[:self.tc.max_holdout_samples]
        dev=('cuda' if torch.cuda.is_available() else 'cpu') if self.tc.device=='auto' else self.tc.device
        m=RelationshipTransformer(self.mc).to(dev);opt=torch.optim.AdamW(m.parameters(),lr=self.tc.learning_rate,weight_decay=self.tc.weight_decay);ce=nn.CrossEntropyLoss()
        def loss(batch):
            f,p,k,durdel,dur,ival=[x.to(dev) for x in batch];o=m(f.float(),p.long())
            il=(ival.long()+24).clamp(0,48)
            return ce(o['kind_logits'],k.long())+.30*nn.functional.smooth_l1_loss(o['delay_qn'],durdel.float())+.30*nn.functional.smooth_l1_loss(o['duration_ratio'],dur.float())+.25*ce(o['interval_logits'],il)
        tr=DataLoader(DS(z,ti),batch_size=self.tc.batch_size,shuffle=True);va=DataLoader(DS(z,vi),batch_size=self.tc.batch_size);out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
        best=1e99;stale=0;hist=[];best_ep=0;start=time.time()
        for ep in range(1,self.tc.epochs+1):
            m.train();tl=[]
            for b in tr:opt.zero_grad();l=loss(b);l.backward();nn.utils.clip_grad_norm_(m.parameters(),1.0);opt.step();tl.append(float(l.detach().cpu()))
            m.eval();vl=[]
            with torch.no_grad():
                for b in va:vl.append(float(loss(b).cpu()))
            tv=float(np.mean(tl));vv=float(np.mean(vl));hist.append({'epoch':ep,'trainLoss':tv,'validationLoss':vv})
            if vv<best-1e-5:best=vv;best_ep=ep;stale=0;_save(m,out/'relationship_transformer_v1.npz')
            else:
                stale+=1
                if stale>=self.tc.patience:break
        _load(m,out/'relationship_transformer_v1.npz');m.eval();ho=DataLoader(DS(z,hi),batch_size=self.tc.batch_size);hl=[];correct=0;total=0
        with torch.no_grad():
            for b in ho:
                hl.append(float(loss(b).cpu()));f,p,k,*_= [x.to(dev) for x in b];pred=m(f.float(),p.long())['kind_logits'].argmax(-1);correct+=int((pred==k).sum());total+=len(k)
        cp=out/'relationship_transformer_v1.npz';rep={'schema':'dna-relationship-training-report','version':'1.0','objective':'SOURCE_DISJOINT_SOLO_RELATIONSHIP_LEARNING','modelConfig':self.mc.to_dict(),'trainingConfig':self.tc.to_dict(),'device':dev,'trainSamples':len(ti),'validationSamples':len(vi),'holdoutSamples':len(hi),'bestEpoch':best_ep,'bestValidationLoss':best,'holdoutLoss':float(np.mean(hl)),'holdoutKindAccuracy':correct/max(1,total),'history':hist,'elapsedSeconds':round(time.time()-start,3),'checkpoint':cp.name,'checkpointSha256':sha256(cp.read_bytes()).hexdigest(),'authority':{'goldVelocityUsed':False,'velocityFeature':False,'velocityOutputHead':False,'optimizedMidiTrainingTruth':False,'hardValidatorRequired':True}}
        (out/'relationship_training_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');(out/'relationship_model_config.json').write_text(json.dumps(self.mc.to_dict(),indent=2),encoding='utf-8');return rep
