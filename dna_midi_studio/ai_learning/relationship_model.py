from __future__ import annotations
from dataclasses import dataclass,asdict
import torch
from torch import nn

@dataclass
class RelationshipModelConfig:
    feature_dim:int=17; d_model:int=64; nhead:int=4; layers:int=2; ff_dim:int=128; max_pairs:int=192
    def to_dict(self):return asdict(self)

class RelationshipTransformer(nn.Module):
    """Pair-sequence encoder for solo<->third/echo relationship learning.

    No velocity embedding or output head exists by design.
    """
    def __init__(self,cfg:RelationshipModelConfig|None=None):
        super().__init__();self.cfg=cfg or RelationshipModelConfig()
        d=self.cfg.d_model
        self.interval_emb=nn.Embedding(49,d);self.delay_emb=nn.Embedding(128,d);self.duration_emb=nn.Embedding(64,d);self.present_emb=nn.Embedding(2,d)
        self.feature_proj=nn.Sequential(nn.Linear(self.cfg.feature_dim,d),nn.GELU(),nn.LayerNorm(d))
        layer=nn.TransformerEncoderLayer(d_model=d,nhead=self.cfg.nhead,dim_feedforward=self.cfg.ff_dim,batch_first=True,norm_first=True,dropout=.1)
        self.encoder=nn.TransformerEncoder(layer,num_layers=self.cfg.layers)
        self.kind_head=nn.Linear(d,2);self.confidence_head=nn.Linear(d,1);self.delay_head=nn.Linear(d,1);self.duration_head=nn.Linear(d,1);self.interval_head=nn.Linear(d,49)
    def forward(self,features,pairs):
        p=pairs.long();mask=p[...,3]==0
        x=self.interval_emb(p[...,0].clamp(0,48))+self.delay_emb(p[...,1].clamp(0,127))+self.duration_emb(p[...,2].clamp(0,63))+self.present_emb(p[...,3].clamp(0,1))
        x=x+self.feature_proj(features.float()).unsqueeze(1)
        z=self.encoder(x,src_key_padding_mask=mask)
        present=(~mask).float().unsqueeze(-1); pooled=(z*present).sum(1)/present.sum(1).clamp_min(1)
        return {'kind_logits':self.kind_head(pooled),'confidence':self.confidence_head(pooled).squeeze(-1),'delay_qn':self.delay_head(pooled).squeeze(-1),'duration_ratio':self.duration_head(pooled).squeeze(-1),'interval_logits':self.interval_head(pooled),'embedding':pooled}
