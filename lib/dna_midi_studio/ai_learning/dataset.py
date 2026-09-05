from __future__ import annotations
from dataclasses import dataclass, asdict
from hashlib import sha256
import json, math, random
from pathlib import Path
from typing import Iterable
import numpy as np
from .authority import DEFAULT_POLICY

ROLE_VOCAB = ["drums","percussion","bass","rhythm-guitar","power-riff","accompaniment","solo","third","echo","factory-strum","unknown"]
SECTION_VOCAB = ["intro","body","verse","chorus","transition","fill","break","ending","unknown"]
METER_VOCAB = ["2/4","3/4","4/4","6/8","7/8","9/8","unknown"]
SOURCE_VOCAB = ["GOLD","FACTORY_STRUM"]

# Event token channels contain no velocity. Pitch is relative for pitched GOLD
# patterns and absolute key for drums, matching the existing evidence registry.
FEATURE_NAMES = [
    "density","register_span","register_center","median_gate","syncopation",
    "starts_with_rest","ends_with_space","occurrence_log","confidence","quality",
    "event_count_log","tempo_center","tempo_span","length_bars",
]

@dataclass
class DatasetManifest:
    schema: str
    version: str
    samples: int
    train: int
    validation: int
    holdout: int
    feature_names: list[str]
    authority: dict
    source_hashes: dict[str,str]
    dataset_hash: str

    def to_dict(self): return asdict(self)


def _hash_file(path: Path) -> str:
    h=sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024), b""): h.update(chunk)
    return h.hexdigest()

def _safe_float(v, default=0.0):
    try: return float(v)
    except (TypeError,ValueError): return default

def _section(raw: str) -> str:
    s=(raw or "unknown").lower()
    for x in SECTION_VOCAB[:-1]:
        if x in s: return x
    return "unknown"

def _meter(raw: str) -> str:
    return raw if raw in METER_VOCAB else "unknown"

def _role(raw: str) -> str:
    return raw if raw in ROLE_VOCAB else "unknown"

def _tokenize_events(pattern: dict, max_events: int) -> np.ndarray:
    events=pattern.get("events") or pattern.get("notes") or []
    resolution=max(1,int(pattern.get("timingResolution") or 96))
    result=np.zeros((max_events,4),dtype=np.int64)
    # columns: position bin 0..383, duration bin 1..127, pitch code 0..255, present
    for i,event in enumerate(events[:max_events]):
        if not isinstance(event,(list,tuple)) or len(event)<3: continue
        pos=max(0,min(383,round(_safe_float(event[0])*96/resolution)))
        dur=max(1,min(127,round(_safe_float(event[1])*96/resolution)))
        pitch=int(round(_safe_float(event[2])))
        # signed relative pitch encoded +64; absolute drums remain within 0..127 +128
        if pattern.get("pitchMode") == "absolute-drum-note": code=max(128,min(255,128+pitch))
        else: code=max(0,min(127,pitch+64))
        result[i]=(pos,dur,code,1)
    return result

def _features(p: dict) -> np.ndarray:
    reg=p.get("register") or {}
    lo=_safe_float(reg.get("low")); hi=_safe_float(reg.get("high"))
    art=p.get("articulation") or {}; tr=p.get("transitionContext") or {}
    tempo=p.get("tempoRange") or [0,0]
    if len(tempo)<2: tempo=[tempo[0] if tempo else 0,tempo[0] if tempo else 0]
    occ=max(0,_safe_float(p.get("occurrences"),1))
    events=p.get("events") or p.get("notes") or []
    vals=[
        _safe_float(p.get("density")), max(0,hi-lo), (lo+hi)/2,
        _safe_float(art.get("medianGate")), _safe_float(art.get("syncopation")),
        float(bool(tr.get("startsWithRest"))), float(bool(tr.get("endsWithSpace"))),
        math.log1p(occ), _safe_float(p.get("confidence")), _safe_float(p.get("qualityScore")),
        math.log1p(len(events)), (_safe_float(tempo[0])+_safe_float(tempo[1]))/2,
        max(0,_safe_float(tempo[1])-_safe_float(tempo[0])), _safe_float(p.get("lengthBars"),1),
    ]
    return np.asarray(vals,dtype=np.float32)

class LearningDatasetBuilder:
    def __init__(self, gold_path: str|Path, factory_strum_path: str|Path, max_events: int=96, seed: int=800):
        self.gold_path=Path(gold_path); self.factory_strum_path=Path(factory_strum_path)
        self.max_events=max_events; self.seed=seed

    def _load_rows(self) -> list[dict]:
        gold=json.loads(self.gold_path.read_text(encoding="utf-8"))
        # Strong guard over actionable GOLD pattern/relationship payloads. Registry
        # metadata may contain an explicit `velocityDataIncluded: false` audit flag;
        # this is allowed, but no trainable pattern field may carry velocity data.
        def walk(v,path="root"):
            if isinstance(v,dict):
                for k,x in v.items():
                    if "velocity" in str(k).lower():
                        raise ValueError(f"GOLD velocity contamination at {path}.{k}")
                    walk(x,f"{path}.{k}")
            elif isinstance(v,list):
                for i,x in enumerate(v): walk(x,f"{path}[{i}]")
        walk(gold.get("patterns",[]), "root.patterns")
        walk(gold.get("relationships",[]), "root.relationships")
        if gold.get("rules",{}).get("velocityDataIncluded") is not False:
            raise ValueError("GOLD registry must explicitly declare velocityDataIncluded=false")
        factory=json.loads(self.factory_strum_path.read_text(encoding="utf-8"))
        rows=[]
        for source,data in (("GOLD",gold),("FACTORY_STRUM",factory)):
            for p in data.get("patterns",[]):
                if not (p.get("events") or p.get("notes")): continue
                rows.append((source,p))
        return rows

    def build(self, output_dir: str|Path) -> DatasetManifest:
        out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
        rows=self._load_rows(); rng=random.Random(self.seed); rng.shuffle(rows)
        feats=[]; events=[]; roles=[]; meters=[]; sections=[]; sources=[]; ids=[]
        for source,p in rows:
            feats.append(_features(p)); events.append(_tokenize_events(p,self.max_events))
            roles.append(ROLE_VOCAB.index(_role(str(p.get("role","unknown")))))
            meters.append(METER_VOCAB.index(_meter(str(p.get("meter","unknown")))))
            sections.append(SECTION_VOCAB.index(_section(str(p.get("sourceSection","unknown")))))
            sources.append(SOURCE_VOCAB.index(source)); ids.append(str(p.get("id","")))
        X=np.stack(feats); E=np.stack(events)
        # normalization is fit on train only after deterministic split by shuffled order
        n=len(rows); n_hold=max(1,round(n*.10)); n_val=max(1,round(n*.10)); n_train=n-n_hold-n_val
        split=np.zeros(n,dtype=np.int8); split[n_train:n_train+n_val]=1; split[n_train+n_val:]=2
        mean=X[:n_train].mean(axis=0); std=X[:n_train].std(axis=0); std[std<1e-6]=1
        Xn=(X-mean)/std
        np.savez_compressed(out/"learning_dataset_v1.npz", features=Xn, events=E,
            roles=np.asarray(roles,np.int64), meters=np.asarray(meters,np.int64),
            sections=np.asarray(sections,np.int64), sources=np.asarray(sources,np.int64),
            split=split, feature_mean=mean, feature_std=std)
        (out/"sample_ids.json").write_text(json.dumps(ids,ensure_ascii=False),encoding="utf-8")
        dataset_hash=_hash_file(out/"learning_dataset_v1.npz")
        manifest=DatasetManifest("dna-neural-learning-dataset","1.0",n,n_train,n_val,n_hold,
            FEATURE_NAMES,DEFAULT_POLICY.to_dict(),
            {"gold":_hash_file(self.gold_path),"factoryStrumming":_hash_file(self.factory_strum_path)},dataset_hash)
        (out/"dataset_manifest.json").write_text(json.dumps(manifest.to_dict(),indent=2),encoding="utf-8")
        return manifest
