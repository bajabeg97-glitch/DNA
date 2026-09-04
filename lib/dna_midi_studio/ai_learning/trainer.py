from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from hashlib import sha256
import json, random, time
import numpy as np
import torch
from torch import nn
from torch.utils.data import Dataset, DataLoader
from .model import DNAReconstructionNet, ModelConfig

@dataclass
class TrainingConfig:
    epochs:int=12; batch_size:int=64; learning_rate:float=3e-4; weight_decay:float=1e-2
    seed:int=800; patience:int=3; device:str="auto"; compile_model:bool=False
    mask_probability:float=0.22; replace_probability:float=0.35
    max_train_samples:int|None=None; max_validation_samples:int|None=None; max_holdout_samples:int|None=None
    def to_dict(self): return asdict(self)

class _DS(Dataset):
    def __init__(self,z,indices): self.z=z; self.i=np.asarray(indices)
    def __len__(self): return len(self.i)
    def __getitem__(self,j):
        i=self.i[j]
        return tuple(torch.from_numpy(np.asarray(self.z[k][i])) if np.asarray(self.z[k][i]).ndim else torch.tensor(self.z[k][i])
                     for k in ("features","events","roles","meters","sections","sources"))

def _save_npz_state(model,path):
    arrays={k:v.detach().cpu().numpy() for k,v in model.state_dict().items()}; np.savez_compressed(path,**arrays)
def _load_npz_state(model,path):
    z=np.load(path); state=model.state_dict()
    for k in state: state[k]=torch.from_numpy(z[k]).to(dtype=state[k].dtype)
    model.load_state_dict(state)
def _sha(path): return sha256(Path(path).read_bytes()).hexdigest()

class LearningTrainer:
    def __init__(self,model_config:ModelConfig|None=None,training_config:TrainingConfig|None=None):
        self.mc=model_config or ModelConfig(); self.tc=training_config or TrainingConfig()

    def _corrupt(self,e:torch.Tensor, training:bool):
        """Build BERT-like masked event input plus a synthetic defect label.

        KEEP=0, REPAIR=1, REPLACE=2.  Corruption never touches velocity because no
        velocity channel exists in this representation.
        """
        original=e.clone(); present=original[...,3].bool()
        p=self.tc.mask_probability if training else min(self.tc.mask_probability,0.20)
        rand=torch.rand(present.shape,device=e.device)
        masked=(rand<p)&present
        # guarantee at least one supervised event for non-empty rows
        for b in range(e.shape[0]):
            if present[b].any() and not masked[b].any(): masked[b,torch.where(present[b])[0][0]]=True
        inp=original.clone(); inp[masked,0]=0; inp[masked,1]=1; inp[masked,2]=64
        # sample-level defect target from masked fraction; additional structural corruption
        frac=masked.float().sum(1)/present.float().sum(1).clamp_min(1)
        defect=torch.where(frac<0.12,torch.zeros_like(frac,dtype=torch.long),
                torch.where(frac<0.30,torch.ones_like(frac,dtype=torch.long),torch.full_like(frac,2,dtype=torch.long)))
        return inp,original,masked.long(),defect

    def train(self,dataset_path:str|Path,output_dir:str|Path):
        random.seed(self.tc.seed); np.random.seed(self.tc.seed); torch.manual_seed(self.tc.seed)
        z=np.load(dataset_path); split=z["split"]
        train_idx=np.where(split==0)[0]; val_idx=np.where(split==1)[0]; hold_idx=np.where(split==2)[0]
        if self.tc.max_train_samples: train_idx=train_idx[:self.tc.max_train_samples]
        if self.tc.max_validation_samples: val_idx=val_idx[:self.tc.max_validation_samples]
        if self.tc.max_holdout_samples: hold_idx=hold_idx[:self.tc.max_holdout_samples]
        device=("cuda" if torch.cuda.is_available() else "cpu") if self.tc.device=="auto" else self.tc.device
        model=DNAReconstructionNet(self.mc).to(device)
        if self.tc.compile_model and hasattr(torch,"compile"): model=torch.compile(model)
        opt=torch.optim.AdamW(model.parameters(),lr=self.tc.learning_rate,weight_decay=self.tc.weight_decay)
        train_loader=DataLoader(_DS(z,train_idx),batch_size=self.tc.batch_size,shuffle=True,num_workers=0)
        val_loader=DataLoader(_DS(z,val_idx),batch_size=self.tc.batch_size,shuffle=False,num_workers=0)
        ce=nn.CrossEntropyLoss(ignore_index=-100)
        def step(batch,training):
            f,e,r,m,s,src=[x.to(device) for x in batch]
            inp,target,masked,defect=self._corrupt(e.long(),training)
            out=model(f.float(),inp,r.long(),m.long(),s.long(),src.long(),masked=masked)
            supervised=masked.bool() & target[...,3].bool()
            pos=target[...,0].clone(); dur=target[...,1].clone(); pit=target[...,2].clone()
            pos[~supervised]=-100; dur[~supervised]=-100; pit[~supervised]=-100
            event_loss=ce(out["position_logits"].reshape(-1,384),pos.reshape(-1))+ce(out["duration_logits"].reshape(-1,128),dur.reshape(-1))+ce(out["pitch_logits"].reshape(-1,256),pit.reshape(-1))
            role_loss=ce(out["role_logits"],r); defect_loss=ce(out["defect_logits"],defect)
            q_target=torch.stack([torch.sigmoid(f[:,8]),torch.sigmoid(f[:,9])],dim=1)
            q_loss=nn.functional.mse_loss(torch.sigmoid(out["quality"]),q_target)
            return event_loss+0.30*role_loss+0.35*defect_loss+0.20*q_loss,out
        best=float("inf"); best_epoch=0; stale=0; history=[]; outdir=Path(output_dir); outdir.mkdir(parents=True,exist_ok=True); start=time.time()
        for epoch in range(1,self.tc.epochs+1):
            model.train(); tl=[]
            for batch in train_loader:
                opt.zero_grad(set_to_none=True); loss,_=step(batch,True); loss.backward(); nn.utils.clip_grad_norm_(model.parameters(),1.0); opt.step(); tl.append(float(loss.detach().cpu()))
            model.eval(); vl=[]
            with torch.no_grad():
                for batch in val_loader: vl.append(float(step(batch,False)[0].cpu()))
            tr=float(np.mean(tl)); va=float(np.mean(vl)); history.append({"epoch":epoch,"trainLoss":tr,"validationLoss":va})
            if va<best-1e-5: best=va; best_epoch=epoch; stale=0; _save_npz_state(model,outdir/"dna_reconstruction_model_v2.npz")
            else:
                stale+=1
                if stale>=self.tc.patience: break
        _load_npz_state(model,outdir/"dna_reconstruction_model_v2.npz"); model.eval()
        hold_loader=DataLoader(_DS(z,hold_idx),batch_size=self.tc.batch_size,shuffle=False); losses=[]
        with torch.no_grad():
            for batch in hold_loader: losses.append(float(step(batch,False)[0].cpu()))
        report={"schema":"dna-neural-training-report","version":"2.0","objective":"MASKED_EVENT_INFILL_PLUS_DEFECT_CLASSIFICATION",
                "modelConfig":self.mc.to_dict(),"trainingConfig":self.tc.to_dict(),"device":device,"trainSamples":len(train_idx),"validationSamples":len(val_idx),"holdoutSamples":len(hold_idx),
                "bestEpoch":best_epoch,"bestValidationLoss":best,"holdoutLoss":float(np.mean(losses)),"history":history,"elapsedSeconds":round(time.time()-start,3),
                "checkpoint":"dna_reconstruction_model_v2.npz","checkpointSha256":_sha(outdir/"dna_reconstruction_model_v2.npz"),
                "authority":{"goldVelocityUsed":False,"velocityOutputHead":False,"hardValidatorRequired":True,"harmonyLocked":True,"formLocked":True}}
        (outdir/"training_report.json").write_text(json.dumps(report,indent=2),encoding="utf-8"); (outdir/"model_config.json").write_text(json.dumps(self.mc.to_dict(),indent=2),encoding="utf-8")
        return report
