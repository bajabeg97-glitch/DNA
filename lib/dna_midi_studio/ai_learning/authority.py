from __future__ import annotations
from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class LearningAuthorityPolicy:
    """Executable ML authority boundary.

    GOLD is forbidden from providing any velocity/dynamics target.  Neural
    outputs are advisory until they pass the existing deterministic planner,
    collision/budget checks and Pa800 validators.
    """
    factory_velocity_only: bool = True
    gold_velocity_features_forbidden: bool = True
    ai_may_change_harmony: bool = False
    ai_may_change_form: bool = False
    ai_may_change_tempo_map: bool = False
    ai_may_change_meter: bool = False
    ai_may_change_lyrics: bool = False
    ai_may_rank_patterns: bool = True
    ai_may_predict_defects: bool = True
    ai_may_predict_timing: bool = True
    ai_may_predict_gate: bool = True
    ai_may_predict_articulation: bool = True
    ai_may_infill_masked_regions: bool = True
    ai_requires_hard_validation: bool = True

    def validate_training_schema(self, columns: list[str]) -> None:
        lowered = [c.lower() for c in columns]
        if self.gold_velocity_features_forbidden:
            forbidden = [c for c in lowered if "velocity" in c or c in {"vel", "dynamics"}]
            if forbidden:
                raise ValueError(f"GOLD neural schema contains forbidden velocity fields: {forbidden}")

    def to_dict(self) -> dict:
        return asdict(self)

DEFAULT_POLICY = LearningAuthorityPolicy()
