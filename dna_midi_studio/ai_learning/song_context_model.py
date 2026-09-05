from __future__ import annotations
from dataclasses import dataclass,asdict
import torch
from torch import nn

@dataclass
class SongContextConfig:
    feature_dim:int=7; d_model:int=24; nhead:int=4; layers:int=1; ff_dim:int=64; dropout:float=.08; roles:int=4; meters:int=7; chord_roots:int=13; window_bars:int=9
    def to_dict(self): return asdict(self)

class MultiTrackContextNet(nn.Module):
    """Predicts a masked role/bar performance summary from surrounding full-song context.
    No velocity enters or leaves this model.
    """
    def __init__(self,cfg:SongContextConfig):
        super().__init__(); self.cfg=cfg; d=cfg.d_model
        self.feat=nn.Sequential(nn.Linear(cfg.feature_dim,d),nn.GELU(),nn.LayerNorm(d))
        self.role=nn.Embedding(cfg.roles,d); self.barpos=nn.Embedding(cfg.window_bars,d); self.chord=nn.Embedding(cfg.chord_roots,d)
        self.meter=nn.Embedding(cfg.meters,d); self.target_role=nn.Embedding(cfg.roles,d); self.song_pos=nn.Linear(1,d)
        layer=nn.TransformerEncoderLayer(d,cfg.nhead,cfg.ff_dim,cfg.dropout,batch_first=True,norm_first=False)
        self.encoder=nn.TransformerEncoder(layer,cfg.layers)
        self.out=nn.Sequential(nn.Linear(d*2,d),nn.GELU(),nn.Linear(d,cfg.feature_dim))
    def forward(self,contexts,chords,roles,meters,positions):
        # contexts B,9,4,7 -> B,36,D
        B,W,R,F=contexts.shape
        x=self.feat(contexts)
        r=torch.arange(R,device=x.device).view(1,1,R).expand(B,W,R)
        b=torch.arange(W,device=x.device).view(1,W,1).expand(B,W,R)
        c=chords[:,:,None].expand(B,W,R)
        x=x+self.role(r)+self.barpos(b)+self.chord(c)
        x=x.reshape(B,W*R,-1)
        enc=self.encoder(x).mean(1)
        cond=self.target_role(roles)+self.meter(meters)+self.song_pos(positions[:,None])
        y=self.out(torch.cat([enc,cond],-1))
        return y
