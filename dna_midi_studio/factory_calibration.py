from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict
from hashlib import sha256
import json, math, statistics
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "dna-midi-studio.factory-calibration"
VERSION = "4.37.0"


def _q(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(float(x) for x in values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs)-1)*p
    lo = int(math.floor(pos)); hi = int(math.ceil(pos))
    if lo == hi: return xs[lo]
    f = pos-lo
    return xs[lo]*(1-f)+xs[hi]*f


def _median_abs_dev(values: list[float]) -> float:
    if not values: return 0.0
    m = statistics.median(values)
    return statistics.median(abs(float(x)-m) for x in values)


def _source_bucket(source_hash: str) -> str:
    h = sha256(source_hash.encode("utf-8")).digest()[0]
    return "holdout" if h % 5 == 0 else "train"


def _segment_features(seg: dict[str, Any]) -> dict[str, float] | None:
    notes = seg.get("notes") or []
    if not notes:
        return None
    pitches = [float(n[2]) for n in notes]
    gates = [float(n[1]) / max(1.0, float(seg.get("ppq") or 192.0)) for n in notes]
    bars = max(0.25, float(seg.get("bars") or 1.0))
    return {
        "pitchLow": min(pitches),
        "pitchHigh": max(pitches),
        "pitchMedian": statistics.median(pitches),
        "pitchMad": _median_abs_dev(pitches),
        "gateMedianQn": statistics.median(gates),
        "gateP10Qn": _q(gates, 0.10),
        "gateP90Qn": _q(gates, 0.90),
        "densityPerBar": len(notes)/bars,
    }


def _aggregate(rows: list[dict[str, float]]) -> dict[str, Any]:
    if not rows:
        return {}
    keys = ("pitchLow","pitchHigh","pitchMedian","pitchMad","gateMedianQn","gateP10Qn","gateP90Qn","densityPerBar")
    out: dict[str, Any] = {"sampleSegments": len(rows)}
    for k in keys:
        vals=[r[k] for r in rows]
        out[k]={
            "p05": round(_q(vals,.05),6),
            "p50": round(_q(vals,.50),6),
            "p95": round(_q(vals,.95),6),
            "mad": round(_median_abs_dev(vals),6),
        }
    return out


def _deviation(train: dict[str, Any], holdout_rows: list[dict[str,float]]) -> dict[str, float]:
    if not train or not holdout_rows: return {}
    features=("pitchMedian","gateMedianQn","densityPerBar")
    out={}
    for k in features:
        center=float(train[k]["p50"])
        deviations=[abs(r[k]-center) for r in holdout_rows]
        out[k+"P95AbsDeviation"] = round(_q(deviations,.95),6)
    return out


def build_factory_calibration(data_dir: str|Path, output_path: str|Path | None = None) -> dict[str, Any]:
    data_dir=Path(data_dir)
    profiles_doc=json.loads((data_dir/"factory-velocity-profiles.json").read_text(encoding="utf-8"))
    seg_doc=json.loads((data_dir/"factory-style-segments.json").read_text(encoding="utf-8"))
    profiles={str(p.get("id")):p for p in profiles_doc.get("profiles",[]) if p.get("id")}
    by_profile: dict[str,list[tuple[str,dict[str,float],str]]] = defaultdict(list)
    by_role: dict[str,list[tuple[str,dict[str,float],str]]] = defaultdict(list)
    for seg in seg_doc.get("segments",[]):
        feat=_segment_features(seg)
        if not feat: continue
        sh=str(seg.get("sourceHash") or seg.get("sourceId") or "unknown")
        bucket=_source_bucket(sh)
        role=str(seg.get("role") or "unknown")
        by_role[role].append((sh,feat,bucket))
        for pid in seg.get("factoryProfileIds") or []:
            by_profile[str(pid)].append((sh,feat,bucket))

    calibrations={}
    exact_count=0
    for pid,p in profiles.items():
        rows=by_profile.get(pid,[])
        train=[f for _,f,b in rows if b=="train"]
        hold=[f for _,f,b in rows if b=="holdout"]
        source_count=len({s for s,_,_ in rows})
        enough=source_count>=5 and len(train)>=4 and len(hold)>=1
        base=_aggregate(train if enough else [f for _,f,_ in rows])
        if not base:
            continue
        exact_count += int(enough)
        velq=(p.get("velocityCurve") or {}).get("quantiles") or p.get("velocityQuantiles") or {}
        calib={
            "profileId":pid,
            "instrument":p.get("instrument_name") or p.get("instrument"),
            "role":p.get("role"),
            "soundBinding":[p.get("bankMsb"),p.get("bankLsb"),p.get("program")],
            "mode":"HELD_OUT_EXACT" if enough else "OBSERVED_EXACT_NO_HOLDOUT",
            "sourceCount":source_count,
            "trainSegments":len(train),"holdoutSegments":len(hold),
            "registerEnvelope":base,
            "heldOutDeviation":_deviation(base,hold) if enough else {},
            "velocityEnvelope":{
                "p05":velq.get("p05"),"p50":velq.get("p50"),"p95":velq.get("p95"),
                "min":p.get("velocity_min"),"max":p.get("velocity_max"),
                "sampleCount":p.get("sample_count") or p.get("samples"),
                "authority":"FACTORY_ONLY",
            },
            "confidence":round(min(1.0,0.35+0.08*math.log2(max(1,source_count))+0.25*(1 if enough else 0)),6),
        }
        calibrations[pid]=calib

    role_fallback={}
    for role,rows in by_role.items():
        train=[f for _,f,b in rows if b=="train"]
        hold=[f for _,f,b in rows if b=="holdout"]
        base=_aggregate(train)
        if base:
            role_fallback[role]={"mode":"HELD_OUT_ROLE_FALLBACK","role":role,"trainSegments":len(train),"holdoutSegments":len(hold),"envelope":base,"heldOutDeviation":_deviation(base,hold),"confidence":0.55}

    doc={
        "schema":SCHEMA,"version":VERSION,
        "source":{
            "factoryProfilesSha256":sha256((data_dir/"factory-velocity-profiles.json").read_bytes()).hexdigest(),
            "factorySegmentsSha256":sha256((data_dir/"factory-style-segments.json").read_bytes()).hexdigest(),
        },
        "policy":{
            "split":"source-hash deterministic 80/20",
            "tolerance":"95th percentile absolute held-out deviation",
            "fallback":"exact profile observed -> role held-out -> MANUAL_REVIEW",
            "velocityAuthority":"FACTORY_ONLY",
            "deviceUnknowns":"ABSTAIN_NOT_INFER",
        },
        "summary":{
            "factoryProfiles":len(profiles),"profilesWithCalibration":len(calibrations),"heldOutExactProfiles":exact_count,"roleFallbacks":len(role_fallback),"factorySegments":len(seg_doc.get("segments",[])),
        },
        "profiles":calibrations,"roleFallbacks":role_fallback,
    }
    raw=json.dumps(doc,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    doc["calibrationHash"]=sha256(raw).hexdigest()
    if output_path:
        Path(output_path).write_text(json.dumps(doc,indent=2,ensure_ascii=False),encoding="utf-8")
    return doc


class FactoryCalibrationEngine:
    def __init__(self, path: str|Path):
        self.path=Path(path)
        self.doc=json.loads(self.path.read_text(encoding="utf-8"))
        self.profiles=self.doc.get("profiles",{})
        self.role_fallbacks=self.doc.get("roleFallbacks",{})

    def resolve(self, profile_id: str|None, role: str|None) -> dict[str,Any]:
        if profile_id and str(profile_id) in self.profiles:
            return {"decision":"PASS","source":"EXACT_PROFILE_CALIBRATION",**self.profiles[str(profile_id)]}
        if role and str(role) in self.role_fallbacks:
            return {"decision":"PASS","source":"ROLE_FALLBACK_CALIBRATION",**self.role_fallbacks[str(role)]}
        return {"decision":"MANUAL_REVIEW","source":"NO_FACTORY_CALIBRATION","confidence":0.0}

    def pattern_tolerance(self, profile_id: str|None, role: str|None) -> dict[str,Any]:
        r=self.resolve(profile_id,role)
        if r.get("decision")!="PASS": return r
        dev=r.get("heldOutDeviation") or {}
        env=r.get("registerEnvelope") or r.get("envelope") or {}
        return {"decision":"PASS","source":r.get("source"),"profileId":r.get("profileId"),"role":r.get("role") or role,"p95Deviation":dev,"envelope":env,"confidence":r.get("confidence",0.0)}
