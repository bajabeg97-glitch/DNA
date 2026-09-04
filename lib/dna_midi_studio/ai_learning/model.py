from __future__ import annotations
from dataclasses import dataclass, asdict
import torch
from torch import nn

@dataclass
class ModelConfig:
    feature_dim: int = 14
    d_model: int = 128
    nhead: int = 4
    layers: int = 3
    ff_dim: int = 384
    dropout: float = 0.10
    max_events: int = 96
    embedding_dim: int = 64
    roles: int = 11
    meters: int = 7
    sections: int = 9
    sources: int = 2
    defect_classes: int = 3
    def to_dict(self): return asdict(self)

class DNAReconstructionNet(nn.Module):
    """Multitask Transformer for symbolic performance learning and masked infilling.

    The network intentionally has no velocity input/output.  Factory remains the
    exclusive velocity authority.  The event encoder includes a learned mask-state
    embedding so masked events can be reconstructed from surrounding phrase context.
    """
    def __init__(self, cfg: ModelConfig):
        super().__init__(); self.cfg=cfg
        c=cfg.d_model
        q=c//4
        self.pos_emb=nn.Embedding(384,q); self.dur_emb=nn.Embedding(128,q)
        self.pitch_emb=nn.Embedding(256,q); self.present_emb=nn.Embedding(2,q)
        self.mask_emb=nn.Embedding(2,c)
        self.role_emb=nn.Embedding(cfg.roles,c); self.meter_emb=nn.Embedding(cfg.meters,c)
        self.section_emb=nn.Embedding(cfg.sections,c); self.source_emb=nn.Embedding(cfg.sources,c)
        self.feature_proj=nn.Sequential(nn.Linear(cfg.feature_dim,c),nn.GELU(),nn.LayerNorm(c))
        layer=nn.TransformerEncoderLayer(c,cfg.nhead,cfg.ff_dim,cfg.dropout,batch_first=True,norm_first=True)
        self.encoder=nn.TransformerEncoder(layer,cfg.layers,norm=nn.LayerNorm(c))
        self.embedding_head=nn.Sequential(nn.Linear(c*2,c),nn.GELU(),nn.Linear(c,cfg.embedding_dim))
        self.quality_head=nn.Sequential(nn.Linear(c*2,c//2),nn.GELU(),nn.Linear(c//2,2))
        self.defect_head=nn.Sequential(nn.Linear(c*2,c//2),nn.GELU(),nn.Linear(c//2,cfg.defect_classes))
        self.role_head=nn.Linear(c*2,cfg.roles)
        self.position_head=nn.Linear(c,384); self.duration_head=nn.Linear(c,128); self.pitch_head=nn.Linear(c,256)

    def forward(self, features, events, roles, meters, sections, sources, masked=None):
        token=torch.cat([self.pos_emb(events[...,0]),self.dur_emb(events[...,1]),self.pitch_emb(events[...,2]),self.present_emb(events[...,3])],dim=-1)
        if masked is None: masked=torch.zeros(events.shape[:2],dtype=torch.long,device=events.device)
        token=token+self.mask_emb(masked.long())
        ctx=(self.role_emb(roles)+self.meter_emb(meters)+self.section_emb(sections)+self.source_emb(sources)+self.feature_proj(features))
        token=token+ctx[:,None,:]
        pad=events[...,3].eq(0)
        encoded=self.encoder(token,src_key_padding_mask=pad)
        mask=(~pad).float().unsqueeze(-1); pooled=(encoded*mask).sum(1)/mask.sum(1).clamp_min(1)
        joined=torch.cat([pooled,ctx],dim=-1)
        emb=nn.functional.normalize(self.embedding_head(joined),dim=-1)
        return {"embedding":emb,"quality":self.quality_head(joined),"defect_logits":self.defect_head(joined),
                "role_logits":self.role_head(joined),"position_logits":self.position_head(encoded),
                "duration_logits":self.duration_head(encoded),"pitch_logits":self.pitch_head(encoded)}
