from __future__ import annotations
from pathlib import Path
import json
import numpy as np
import torch
from .relationship_model import RelationshipTransformer,RelationshipModelConfig
from .relationship_learning import _nearest_pairs,_pair_tokens,_feature_row,KIND_VOCAB

class RelationshipInferenceEngine:
    def __init__(self,model_dir:str|Path,dataset_path:str|Path):
        self.model_dir=Path(model_dir);self.dataset_path=Path(dataset_path)
        cfgp=self.model_dir/'relationship_model_config.json'
        cfg=RelationshipModelConfig(**json.loads(cfgp.read_text(encoding='utf-8'))) if cfgp.exists() else RelationshipModelConfig()
        self.model=RelationshipTransformer(cfg);z=np.load(self.model_dir/'relationship_transformer_v1.npz');state=self.model.state_dict()
        for k in state:state[k]=torch.from_numpy(z[k]).to(state[k].dtype)
        self.model.load_state_dict(state);self.model.eval()
        ds=np.load(self.dataset_path);self.mean=ds['feature_mean'];self.std=ds['feature_std']
        self.checkpoint=str(self.model_dir/'relationship_transformer_v1.npz')
    def score_notes(self,source,target,kind:str,ppq:int,delay_hint:int|None=None)->dict:
        if kind not in KIND_VOCAB:raise ValueError(kind)
        pairs=_nearest_pairs(list(source),list(target),kind,ppq,delay_hint,self.model.cfg.max_pairs)
        if len(pairs)<4:return {'ok':False,'reason':'insufficient-pairs','pairCount':len(pairs),'relationshipScore':0.0}
        feat=_feature_row(list(source),list(target),pairs,kind,ppq,1.0,'PRESERVE');feat=(feat-self.mean)/self.std
        tok=_pair_tokens(pairs,ppq,self.model.cfg.max_pairs)
        with torch.no_grad():
            o=self.model(torch.from_numpy(feat[None]).float(),torch.from_numpy(tok[None]).long())
            prob=torch.softmax(o['kind_logits'],dim=-1)[0].cpu().numpy();idx=KIND_VOCAB.index(kind)
            pred=int(prob.argmax())
        return {'ok':True,'kind':kind,'predictedKind':KIND_VOCAB[pred],'relationshipScore':float(prob[idx]),'kindProbabilities':{KIND_VOCAB[i]:float(prob[i]) for i in range(len(KIND_VOCAB))},'pairCount':len(pairs),'predictedDelayQn':float(o['delay_qn'][0]),'predictedDurationRatio':float(o['duration_ratio'][0]),'predictedMedianInterval':int(o['interval_logits'][0].argmax())-24,'velocityUsed':False}
