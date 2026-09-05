from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Callable, Any
import numpy as np
from .inference import NeuralInferenceEngine

@dataclass(frozen=True)
class InpaintingCandidate:
    name:str
    variant:int
    score:float
    hard_valid:bool
    changed_events:int
    defect_probabilities:list[float]
    events:list
    validator_report:dict
    def to_dict(self): return asdict(self)

class GenerateSelectEngine:
    """Generate deterministic neural alternatives, validate, then rank.

    This class never writes MIDI by itself. A production caller must translate the
    symbolic candidate through the existing deterministic planner/writer and provide
    a hard_validator callback. Invalid candidates are excluded from A/B/C selection.
    """
    def __init__(self,neural:NeuralInferenceEngine): self.neural=neural

    def generate(self,features,events,roles,meters,sections,sources,mask,n:int=3,
                 hard_validator:Callable[[np.ndarray],dict]|None=None):
        out=[]; base=np.asarray(events)
        for variant in range(max(1,min(int(n),8))):
            r=self.neural.infill(features,base,roles,meters,sections,sources,mask,variant=variant)
            ev=r["events"]; changed=int(np.any(ev!=base,axis=-1).sum())
            report=hard_validator(ev) if hard_validator else {"ok":False,"reason":"HARD_VALIDATOR_NOT_SUPPLIED"}
            ok=bool(report.get("ok",False))
            defect=np.asarray(r["defectProbabilities"])[0]
            # Prefer KEEP/REPAIR probability and smaller edits; hard invalid => rejected.
            musical=float(defect[0]+0.65*defect[1]-0.75*defect[2])-0.0005*changed
            score=musical if ok else -1e9
            out.append(InpaintingCandidate(chr(65+variant),variant,score,ok,changed,defect.tolist(),ev.tolist(),dict(report)))
        out.sort(key=lambda x:(x.hard_valid,x.score,-x.variant),reverse=True)
        return {"candidates":[x.to_dict() for x in out],"selected":[x.name for x in out if x.hard_valid][:3],
                "status":"VALIDATED_SELECTION" if any(x.hard_valid for x in out) else "NO_HARD_VALID_CANDIDATE"}
