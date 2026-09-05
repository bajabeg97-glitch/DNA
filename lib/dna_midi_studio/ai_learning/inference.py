from __future__ import annotations
from pathlib import Path
import json
import numpy as np
import torch
from .model import DNAReconstructionNet, ModelConfig
from .authority import DEFAULT_POLICY

DEFECT_LABELS=("KEEP","REPAIR","REPLACE")

class NeuralInferenceEngine:
    """Inference adapter. Neural outputs remain advisory until hard validation."""
    def __init__(self,model_dir:str|Path,device:str="cpu"):
        p=Path(model_dir); cfg=ModelConfig(**json.loads((p/"model_config.json").read_text()))
        self.model=DNAReconstructionNet(cfg).to(device); self.device=device; self.cfg=cfg
        ck=p/"dna_reconstruction_model_v2.npz"
        if not ck.exists(): ck=p/"dna_reconstruction_model_v1.npz"
        z=np.load(ck); state=self.model.state_dict()
        missing=[]
        for k in state:
            if k in z: state[k]=torch.from_numpy(z[k]).to(dtype=state[k].dtype)
            else: missing.append(k)
        self.model.load_state_dict(state); self.model.eval(); self.checkpoint=ck.name; self.missing_keys=missing

    @torch.inference_mode()
    def encode_batch(self,features,events,roles,meters,sections,sources):
        d=self.device
        out=self.model(torch.as_tensor(features,dtype=torch.float32,device=d),torch.as_tensor(events,dtype=torch.long,device=d),
            torch.as_tensor(roles,dtype=torch.long,device=d),torch.as_tensor(meters,dtype=torch.long,device=d),
            torch.as_tensor(sections,dtype=torch.long,device=d),torch.as_tensor(sources,dtype=torch.long,device=d))
        defect=torch.softmax(out["defect_logits"],-1).cpu().numpy()
        labels=[DEFECT_LABELS[int(i)] for i in defect.argmax(-1)]
        return {"embedding":out["embedding"].cpu().numpy(),"quality":torch.sigmoid(out["quality"]).cpu().numpy(),
                "roleProbabilities":torch.softmax(out["role_logits"],-1).cpu().numpy(),"defectProbabilities":defect,
                "defectDecision":labels,"authority":DEFAULT_POLICY.to_dict(),"status":"ADVISORY_REQUIRES_HARD_VALIDATION"}

    @torch.inference_mode()
    def infill(self,features,events,roles,meters,sections,sources,mask,variant:int=0):
        """Reconstruct only masked event fields; preserve unmasked material exactly.

        variant=0 is argmax. Variants >0 choose deterministic top-k alternatives so
        A/B/C generation is reproducible without random global state.
        """
        d=self.device
        f=torch.as_tensor(features,dtype=torch.float32,device=d); e=torch.as_tensor(events,dtype=torch.long,device=d).clone()
        mk=torch.as_tensor(mask,dtype=torch.long,device=d)
        original=e.clone(); active=mk.bool() & e[...,3].bool()
        e[active,0]=0; e[active,1]=1; e[active,2]=64
        out=self.model(f,e,torch.as_tensor(roles,dtype=torch.long,device=d),torch.as_tensor(meters,dtype=torch.long,device=d),
            torch.as_tensor(sections,dtype=torch.long,device=d),torch.as_tensor(sources,dtype=torch.long,device=d),masked=mk)
        result=original.clone()
        # Deterministic mixed-radix variant decoding.  This yields up to 27
        # reproducible combinations from the top-3 position/duration/pitch
        # alternatives instead of limiting the whole candidate to one rank.
        v=max(0,int(variant))
        ranks=(v % 3, (v // 3) % 3, (v // 9) % 3)
        role_tensor=torch.as_tensor(roles,dtype=torch.long,device=d)
        heads=[out["position_logits"],out["duration_logits"],out["pitch_logits"].clone()]
        # Enforce the tokenizer contract before top-k sampling.  Role ids 0/1 are
        # drums/percussion and use absolute drum-note codes 128..255; every other
        # role uses chord-relative pitched codes 0..127.
        pitch_logits=heads[2]
        for bi,role_id in enumerate(role_tensor.tolist()):
            if int(role_id) in (0,1): pitch_logits[bi,:,:128]=torch.finfo(pitch_logits.dtype).min
            else: pitch_logits[bi,:,128:]=torch.finfo(pitch_logits.dtype).min
        for (logits,col),rank in zip(((heads[0],0),(heads[1],1),(heads[2],2)),ranks):
            k=min(3,logits.shape[-1]); rr=min(rank,k-1)
            top=torch.topk(logits,k=k,dim=-1).indices[...,rr]
            result[...,col]=torch.where(active,top,result[...,col])
        defect=torch.softmax(out["defect_logits"],-1)
        return {"events":result.cpu().numpy(),"mask":mk.cpu().numpy(),"variant":variant,
                "defectProbabilities":defect.cpu().numpy(),"status":"ADVISORY_REQUIRES_HARD_VALIDATION",
                "preservedUnmasked":bool(torch.equal(result[~active],original[~active])),"authority":DEFAULT_POLICY.to_dict()}
