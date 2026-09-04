from __future__ import annotations
from pathlib import Path
import json
import numpy as np
import torch
from .relationship_sequence_model import RelationshipSequenceTransformer,SequenceModelConfig
from .relationship_sequence_learning import ACTION_VOCAB,KIND_VOCAB,_features

class RelationshipSequenceInferenceEngine:
    def __init__(self,model_dir,dataset_path):
        self.model_dir=Path(model_dir); self.dataset_path=Path(dataset_path)
        cfg=SequenceModelConfig(**json.loads((self.model_dir/'relationship_sequence_model_config.json').read_text(encoding='utf-8')))
        self.model=RelationshipSequenceTransformer(cfg); z=np.load(self.model_dir/'relationship_sequence_transformer_v2.npz'); state=self.model.state_dict()
        for k in state: state[k]=torch.from_numpy(z[k]).to(state[k].dtype)
        self.model.load_state_dict(state); self.model.eval(); ds=np.load(self.dataset_path); self.mean=ds['feature_mean']; self.std=ds['feature_std']
        self.checkpoint=str(self.model_dir/'relationship_sequence_transformer_v2.npz')
    def _predict_chunk(self,notes,kind,ppq,offset,full_notes):
        L=len(notes); feats=np.asarray([_features(full_notes,offset+i,ppq) for i in range(L)],np.float32); feats=(feats-self.mean)/self.std
        pitches=np.asarray([n.pitch for n in notes],np.int64); mask=np.ones((1,L),bool)
        with torch.no_grad():
            o=self.model(torch.from_numpy(feats[None]).float(),torch.from_numpy(pitches[None]).long(),torch.tensor([KIND_VOCAB.index(kind)]),torch.from_numpy(mask))
            ap=torch.softmax(o['action_logits'][0],-1).numpy(); ip=torch.softmax(o['interval_logits'][0],-1).numpy()
        out=[]
        for i,n in enumerate(notes):
            ai=int(ap[i].argmax()); ii=int(ip[i].argmax())-24
            out.append({'action':ACTION_VOCAB[ai],'actionProbabilities':{ACTION_VOCAB[j]:float(ap[i,j]) for j in range(3)},'interval':ii,'intervalConfidence':float(ip[i].max()),'delayQn':float(o['delay_qn'][0,i]),'durationRatio':float(o['duration_ratio'][0,i]),'velocityUsed':False})
        return out
    def predict(self,notes,kind,ppq):
        if kind not in KIND_VOCAB: raise ValueError(kind)
        notes=list(notes); out=[]; step=self.model.cfg.max_seq
        for base in range(0,len(notes),step): out.extend(self._predict_chunk(notes[base:base+step],kind,ppq,base,notes))
        return out
