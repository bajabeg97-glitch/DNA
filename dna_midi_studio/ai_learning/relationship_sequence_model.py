from __future__ import annotations
from dataclasses import dataclass,asdict
import torch
from torch import nn

@dataclass
class SequenceModelConfig:
    feature_dim:int=10; d_model:int=32; nhead:int=4; layers:int=1; ff_dim:int=64; max_seq:int=96
    def to_dict(self):return asdict(self)

class RelationshipSequenceTransformer(nn.Module):
    def __init__(self,cfg:SequenceModelConfig=SequenceModelConfig()):
        super().__init__(); self.cfg=cfg
        self.feat=nn.Linear(cfg.feature_dim,cfg.d_model); self.pitch=nn.Embedding(128,cfg.d_model); self.kind=nn.Embedding(2,cfg.d_model); self.pos=nn.Embedding(cfg.max_seq,cfg.d_model)
        enc=nn.TransformerEncoderLayer(cfg.d_model,cfg.nhead,cfg.ff_dim,batch_first=True,norm_first=True,dropout=.1,activation='gelu')
        self.encoder=nn.TransformerEncoder(enc,cfg.layers); self.norm=nn.LayerNorm(cfg.d_model)
        self.action=nn.Linear(cfg.d_model,3); self.interval=nn.Linear(cfg.d_model,49); self.delay=nn.Linear(cfg.d_model,1); self.duration=nn.Linear(cfg.d_model,1)
    def forward(self,features,pitches,kind,mask):
        B,L,_=features.shape; pos=torch.arange(L,device=features.device)[None,:]
        x=self.feat(features)+self.pitch(pitches)+self.kind(kind)[:,None,:]+self.pos(pos)
        x=self.norm(self.encoder(x,src_key_padding_mask=~mask))
        return {'action_logits':self.action(x),'interval_logits':self.interval(x),'delay_qn':self.delay(x).squeeze(-1),'duration_ratio':self.duration(x).squeeze(-1)}
