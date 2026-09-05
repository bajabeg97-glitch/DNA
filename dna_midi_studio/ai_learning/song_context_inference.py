from __future__ import annotations
from pathlib import Path
import numpy as np, torch
from .song_context import _song_rows, ROLES, WINDOW, _bar_role_features
from .song_context_model import MultiTrackContextNet, SongContextConfig

class SongContextInferenceEngine:
    def __init__(self, model_path:str|Path):
        ck=torch.load(model_path,map_location='cpu',weights_only=False)
        cfg=SongContextConfig(**ck['config']); self.model=MultiTrackContextNet(cfg); self.model.load_state_dict(ck['state_dict']); self.model.eval(); self.cfg=cfg

    def predict_target(self,midi_bytes:bytes,bar_number:int,role:str)->np.ndarray:
        if role not in ROLES: raise ValueError(f'unsupported context role: {role}')
        row=_song_rows(midi_bytes,'context.mid')
        if row is None: raise ValueError('unsupported MIDI')
        feats,chords,meter=row; c=bar_number-1
        if c<WINDOW or c>=len(feats)-WINDOW: raise ValueError('bar lacks full context window')
        ctx=feats[c-WINDOW:c+WINDOW+1].copy(); rid=ROLES.index(role); ctx[WINDOW,rid]=0
        with torch.no_grad():
            y=self.model(torch.from_numpy(ctx[None]).float(),torch.from_numpy(chords[c-WINDOW:c+WINDOW+1][None]).long(),torch.tensor([rid]),torch.tensor([meter]),torch.tensor([c/max(1,len(feats)-1)],dtype=torch.float32))
        return y[0].numpy()

    @staticmethod
    def features_from_notes(notes,bar_start:int,bar_end:int)->np.ndarray:
        rows=[{'start':n.start,'end':n.end,'pitch':n.pitch} for n in notes]
        return _bar_role_features(rows,bar_start,bar_end)

    @staticmethod
    def compatibility(predicted:np.ndarray,actual:np.ndarray)->float:
        # bounded score 0..1; no velocity features exist in either vector
        mae=float(np.mean(np.abs(np.asarray(predicted)-np.asarray(actual))))
        return 1.0/(1.0+5.0*mae)
