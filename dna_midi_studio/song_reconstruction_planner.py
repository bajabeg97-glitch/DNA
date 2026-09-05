"""Multi-region song reconstruction planner.

Coordinates role-aware KEEP/REPAIR/REPLACE/AUGMENT decisions across a song.
It does not mutate MIDI.  It limits automatic intervention with a global repair
budget so that good material stays untouched and high-risk/low-evidence regions
are escalated instead of regenerated blindly.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Iterable, Mapping

from .midi import MidiFile
from .role_aware_repair import decide_region
from .arrangement_interaction import RoleRegion, analyze_arrangement, target_interaction

@dataclass(frozen=True)
class RegionRequest:
    region_id: str
    role: str
    track_index: int
    channel: int
    start_tick: int
    end_tick: int
    evidence_strength: float = 0.0
    target_is_known_bad: bool = False
    user_priority: float = 0.0


def _priority(decision: Mapping[str, Any], interaction_score: float, user_priority: float) -> float:
    severity = float(decision.get("severity", 0.0))
    evidence = float(decision.get("metrics", {}).get("evidenceStrength", 0.0))
    action = str(decision.get("decision", "KEEP"))
    action_weight = {"KEEP": 0.0, "REPAIR": .55, "AUGMENT": .78, "REPLACE": .9, "MANUAL_REVIEW": .15}.get(action, .0)
    # Interaction is advisory only: lower score can raise urgency slightly, never authorize an edit.
    interaction_need = max(0.0, 1.0 - float(interaction_score))
    return max(0.0, min(1.0, .44*severity + .28*evidence + .16*action_weight + .08*interaction_need + .04*max(0.0,min(1.0,user_priority))))


def plan_song_reconstruction(
    midi: MidiFile,
    requests: Iterable[RegionRequest],
    *,
    max_auto_regions: int = 6,
    max_replace_regions: int = 2,
    max_augment_regions: int = 2,
) -> dict[str, Any]:
    reqs = list(requests)
    interaction = analyze_arrangement(midi, [
        RoleRegion(r.role, r.track_index, r.channel, r.start_tick, r.end_tick) for r in reqs
    ])
    rows=[]
    for r in reqs:
        dec = decide_region(
            midi, role=r.role, track_index=r.track_index, channel=r.channel,
            start_tick=r.start_tick, end_tick=r.end_tick,
            evidence_strength=r.evidence_strength,
            target_is_known_bad=r.target_is_known_bad,
        ).to_dict()
        inter = target_interaction(interaction, r.role)
        p = _priority(dec, float(inter.get("score", .5)), r.user_priority)
        rows.append({
            "region": asdict(r), "decision": dec, "interaction": inter,
            "priority": round(p,4), "selectedForAutomaticAction": False,
            "routing": "PRESERVE_OR_REVIEW",
        })

    candidates=[x for x in rows if x["decision"]["decision"] in {"REPAIR","REPLACE","AUGMENT"}]
    candidates.sort(key=lambda x:(-x["priority"], x["region"]["start_tick"], x["region"]["track_index"], x["region"]["region_id"]))
    selected=0; replacements=0; augments=0
    for row in candidates:
        action=row["decision"]["decision"]
        if selected >= max(0,int(max_auto_regions)):
            row["routing"]="DEFERRED_BY_GLOBAL_REPAIR_BUDGET"; continue
        if action=="REPLACE" and replacements>=max(0,int(max_replace_regions)):
            row["routing"]="DEFERRED_BY_REPLACE_BUDGET"; continue
        if action=="AUGMENT" and augments>=max(0,int(max_augment_regions)):
            row["routing"]="DEFERRED_BY_AUGMENT_BUDGET"; continue
        row["selectedForAutomaticAction"]=True
        row["routing"]="AUTO_ACTION_ALLOWED_WITH_EXISTING_HARD_GATES"
        selected += 1
        replacements += int(action=="REPLACE")
        augments += int(action=="AUGMENT")

    return {
        "schema":"dna-song-reconstruction-plan",
        "version":"1.0",
        "policy":{
            "preserveGoodRegions":True,
            "globalRepairBudget":True,
            "velocityAuthority":"FACTORY_ONLY",
            "interactionEvidence":"SOFT_ONLY",
            "hardAuthority":"CORE_INVARIANTS_AND_DEVICE_EVIDENCE",
            "nonEmptySoloGenericReplace":False,
        },
        "budget":{
            "maxAutoRegions":max_auto_regions,"maxReplaceRegions":max_replace_regions,
            "maxAugmentRegions":max_augment_regions,"selected":selected,
            "selectedReplace":replacements,"selectedAugment":augments,
        },
        "arrangementInteraction":interaction,
        "regions":rows,
    }
