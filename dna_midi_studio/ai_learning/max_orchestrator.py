from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from hashlib import sha256
from typing import Any, Iterable
import json
import math


@dataclass(frozen=True)
class ModelSpec:
    key: str
    provenance: str
    relative_path: str
    roles: tuple[str, ...]
    scope: str
    authority: str
    velocity_allowed: bool = False


MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec("pattern_reconstructor", "GOLD_OR_FACTORY_PATTERN_NEURAL", "../models/dna-reconstructor-v2/dna_reconstruction_model_v2.npz", ("bass","drums","percussion","rhythm-guitar","power-riff","accompaniment"), "BAR", "ADVISORY"),
    ModelSpec("song_context", "FULL_SONG_CONTEXT_V1", "ai_song_context/model/song_context_model_v1.pt", ("bass","drums"), "BAR_CONTEXT", "SCORER"),
    ModelSpec("event_v1", "FULL_SONG_EVENT_DECODER_V1", "ai_event_decoder/event_decoder_model_v1.npz", ("bass","drums"), "BAR", "CANDIDATE_SOURCE"),
    ModelSpec("event_ar_v2", "FULL_SONG_AUTOREGRESSIVE_EVENT_V2", "ai_autoregressive_event/autoregressive_event_model_v2.npz", ("bass","drums"), "BAR_SEQUENCE", "CANDIDATE_SOURCE"),
    ModelSpec("phrase_planner", "FULL_SONG_PHRASE_PLANNER_V1", "ai_phrase_context/phrase_planner_model_v1.npz", ("bass","drums"), "4_BAR", "SCORER"),
    ModelSpec("multibar_v3", "FULL_SONG_MULTIBAR_EVENT_V3", "ai_multibar_event/multibar_event_model_v3.npz", ("bass","drums"), "4_BAR_SEQUENCE", "CANDIDATE_SOURCE"),
    ModelSpec("section_arranger", "SECTION_ARRANGER_V1", "ai_section_context/section_arranger_model_v1.npz", ("bass","drums"), "SECTION_INTENT", "SCORER"),
    ModelSpec("transition_fill", "TRANSITION_FILL_INTENT_V1", "ai_transition_fill/transition_fill_model_v1.npz", ("bass","drums"), "TRANSITION_INTENT", "SCORER"),
    ModelSpec("relationship_ranker", "RELATIONSHIP_MODEL_V1", "../models/relationship-transformer-v1/relationship_transformer_v1.npz", ("third","echo"), "MELODIC_RELATION", "SCORER"),
    ModelSpec("relationship_sequence", "RELATIONSHIP_SEQUENCE_V2", "../models/relationship-sequence-v2/relationship_sequence_transformer_v2.npz", ("third","echo"), "MELODIC_SEQUENCE", "CANDIDATE_SOURCE"),
)


SOURCE_CLASS = {
    "GOLD": "EVIDENCE",
    "FACTORY_STRUM": "EVIDENCE",
    "PERFORMANCE_DNA_V1": "EVIDENCE",
    "FULL_SONG_EVENT_DECODER_V1": "NEURAL",
    "FULL_SONG_AUTOREGRESSIVE_EVENT_V2": "NEURAL",
    "FULL_SONG_MULTIBAR_EVENT_V3": "NEURAL",
    "TRANSITION_EVENT_AR_V1": "NEURAL",
}


class MaxModelRegistry:
    """Machine-readable inventory of every learned model used by MAX orchestration."""
    def __init__(self, project_root: str | Path):
        self.root = Path(project_root).resolve()
        # Layout-aware resolution: the repository is a flattened export of a
        # classic workspace (data/, models/, learning_data/).  The registry
        # therefore searches the flat repo root first, then classic data/ and
        # models/ dirs when present.  Kept dependency-free on purpose.
        self.data_dir = self.root / "data"
        bases = [self.root, self.data_dir, self.root / "models"]
        self._bases = list(dict.fromkeys(b for b in bases))

    @staticmethod
    def _file_state(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"present": False, "sha256": None, "bytes": 0}
        raw = path.read_bytes()
        return {"present": True, "sha256": sha256(raw).hexdigest(), "bytes": len(raw)}

    @staticmethod
    def _cleaned(relative_path: str) -> str:
        parts = [x for x in relative_path.replace("\\", "/").split("/") if x not in ("", ".", "..", "data", "models")]
        return "/".join(parts)

    def _candidates(self, relative_path: str) -> list[Path]:
        cleaned = self._cleaned(relative_path)
        out: list[Path] = []
        for base in self._bases:
            for p in (base / cleaned, base / relative_path):
                if p not in out:
                    out.append(p)
        return out

    def scan(self) -> dict[str, Any]:
        rows=[]
        for spec in MODEL_SPECS:
            candidates = self._candidates(spec.relative_path)
            p = next((x for x in candidates if x.exists()), candidates[0]).resolve()
            row=asdict(spec); row.update(self._file_state(p)); row["path"]=str(p.relative_to(self.root)) if p.is_relative_to(self.root) else str(p)
            row["layoutProbeBases"]=[str(b) for b in self._bases]
            rows.append(row)
        return {
            "schema":"dna-max-model-registry","version":"1.0",
            "models":rows,
            "present":sum(1 for x in rows if x["present"]),
            "missing":sum(1 for x in rows if not x["present"]),
            "authority":{
                "velocity":"FACTORY_ONLY",
                "goldVelocity":False,
                "neuralVelocity":False,
                "hardValidatorRequired":True,
                "originalProtectedOutsideTarget":True,
            },
        }


class MaxCandidateOrchestrator:
    """One production scoring contract for GOLD/Factory/neural candidate sources.

    It never authorizes MIDI by itself. It ranks only candidates that already passed
    symbolic hard checks. The final MIDI/Pa800 validator remains mandatory.
    """
    VERSION="1.0"
    WEIGHTS={
        "evidence":1.25,
        "neural":0.55,
        "context":1.10,
        "phrase":1.20,
        "transition":1.10,
        "diversity":0.35,
    }

    @staticmethod
    def _sigmoid(x: float) -> float:
        x=max(-30.0,min(30.0,float(x)))
        return 1.0/(1.0+math.exp(-x))

    @staticmethod
    def _bounded(v: Any, default: float=0.0) -> float:
        try: return max(0.0,min(1.0,float(v)))
        except Exception: return default

    def score_candidate(self, cand: dict[str, Any], role: str, *, transition_score: float | None=None) -> dict[str, Any]:
        if not cand.get("hard_valid", False):
            cand["maxScore"]=-1e9; cand["scoreBreakdown"]={"rejected":"HARD_INVALID"}; return cand
        src=str(cand.get("evidenceSource") or "UNKNOWN")
        cls=SOURCE_CLASS.get(src,"NEURAL" if "DECODER" in src or "AUTOREGRESSIVE" in src or "MULTIBAR" in src else "UNKNOWN")
        retrieval=float(cand.get("retrievalScore") or 0.0)
        neural=float(cand.get("score") or 0.0)
        evidence=self._sigmoid(retrieval/2.5) if cls=="EVIDENCE" else 0.35
        neural_conf=self._sigmoid(neural) if cls=="NEURAL" else 0.50
        context=self._bounded(cand.get("contextScore"),0.50)
        phrase=self._bounded(cand.get("phraseScore"),0.50)
        transition=self._bounded(cand.get("transitionScore") if transition_score is None else transition_score,0.50)
        # reward distinct sources slightly so A/B/C does not collapse to one model family
        diversity=1.0 if cls in {"EVIDENCE","NEURAL"} else 0.25
        total=(self.WEIGHTS["evidence"]*evidence + self.WEIGHTS["neural"]*neural_conf +
               self.WEIGHTS["context"]*context + self.WEIGHTS["phrase"]*phrase +
               self.WEIGHTS["transition"]*transition + self.WEIGHTS["diversity"]*diversity)
        cand["maxScore"]=float(total)
        cand["scoreBreakdown"]={
            "sourceClass":cls,"evidence":evidence,"neural":neural_conf,"context":context,
            "phrase":phrase,"transition":transition,"diversity":diversity,
            "weights":dict(self.WEIGHTS),"total":float(total),
            "role":role,"hardValidatedBeforeRanking":True,
        }
        return cand

    def rank_bar_candidates(self, variants: Iterable[dict[str, Any]], role: str) -> list[dict[str, Any]]:
        rows=[self.score_candidate(v,role) for v in variants]
        rows.sort(key=lambda c:(float(c.get("maxScore",-1e9)),-int(c.get("retrievalRank",9999))),reverse=True)
        return rows

    @staticmethod
    def application_contract() -> dict[str, Any]:
        return {
            "KEEP":"preserve original target events",
            "REPAIR":"bounded target-region edit; preserve velocity unless Factory reapplies it",
            "REPLACE":"new target-region notes require Factory velocity + hard validation",
            "TRANSITION_ONLY":"modify only confirmed/selected transition window",
            "AUTO_COMMIT":False,
            "requiredGates":["SYMBOLIC_HARD_CHECK","FACTORY_VELOCITY_AUTHORITY","PROTECTED_EVENT_CHECK","MIDI_REPARSE","FINAL_PRODUCTION_VALIDATOR"],
        }


def build_max_status(project_root: str | Path) -> dict[str, Any]:
    reg=MaxModelRegistry(project_root).scan()
    return {
        "schema":"dna-max-orchestration-status","version":"1.0",
        "registry":reg,
        "scoring":{"weights":dict(MaxCandidateOrchestrator.WEIGHTS),"authority":"RANKING_ONLY_NOT_VALIDATION"},
        "application":MaxCandidateOrchestrator.application_contract(),
        "productionGoal":"MAX_STRUCTURE_AND_APPLICATION",
    }
