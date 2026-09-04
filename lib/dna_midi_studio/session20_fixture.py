"""Self-authored bilingual intent corpus for Session 20."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any


@dataclass(frozen=True)
class IntentCase:
    case_id: str
    text: str
    expected: dict[str, Any]
    conflict: bool = False


CASES = (
    IntentCase("hr-popfolk-energy", "Napravi življi pop-folk Style, sa suzdržanom strofom i punim refrenom.",
               {"genre": "pop-folk", "energy.verse": 35, "energy.chorus": 85}),
    IntentCase("en-popfolk-energy", "Make a lively pop-folk style with a restrained verse and full chorus.",
               {"genre": "pop-folk", "energy.verse": 35, "energy.chorus": 85}),
    IntentCase("hr-rock-roles", "Napravi rock stil sa gitarom i bez perkusija.",
               {"genre": "rock", "required:guitar": True, "forbidden:percussion": True}),
    IntentCase("en-ballad", "Make a ballad with bass and pad, subtle transitions.",
               {"genre": "ballad", "required:bass": True, "required:pad": True, "transitions": "subtle"}),
    IntentCase("hr-folk-open", "Narodni stil, suptilni prijelazi i prozračan miks.",
               {"genre": "folk", "transitions": "subtle", "space": "open"}),
    IntentCase("en-dance-sync", "Dance arrangement with strong syncopation and dramatic transitions.",
               {"genre": "dance", "syncopation": "high", "transitions": "dramatic"}),
    IntentCase("en-disco-full", "Disco, straight groove and full arrangement.",
               {"genre": "disco", "syncopation": "low", "density": "full"}),
    IntentCase("hr-waltz", "Valcer sa uravnoteženim aranžmanom.",
               {"genre": "waltz", "density": "balanced"}),
    IntentCase("hr-polka-dry", "Polka sa suhim miksom.", {"genre": "polka", "space": "dry"}),
    IntentCase("en-latin-open", "Latin style with an open mix.", {"genre": "latin", "space": "open"}),
    IntentCase("en-jazz-sparse", "Jazz style, sparse arrangement.", {"genre": "jazz", "density": "sparse"}),
    IntentCase("hr-acoustic-no-solo", "Akustični stil bez dodatnog sola.",
               {"genre": "acoustic", "soloTreatment": "no-new-layers"}),
    IntentCase("en-electronic-solo", "Electronic style, expressive solo.",
               {"genre": "electronic", "soloTreatment": "expression-only"}),
    IntentCase("en-pop-lock", "Pop style, lock v2cv1.", {"genre": "pop", "locked:v2cv1": True}),
    IntentCase("hr-lock-no-change", "Zaključaj f1cv1 i ne mijenjaj ostatak.",
               {"locked:f1cv1": True, "transformationTolerance": 0}),
    IntentCase("en-heavy-transform", "Transform heavily, use drums and bass.",
               {"transformationTolerance": 90, "required:drums": True, "required:bass": True}),
    IntentCase("hr-straight-fill", "Ravan groove i nježni fillovi.",
               {"syncopation": "low", "transitions": "subtle"}),
    IntentCase("hr-sync-fill", "Sinkopirani groove i snažni fillovi.",
               {"syncopation": "high", "transitions": "dramatic"}),
    IntentCase("hr-medium-space", "Umjereno sinkopiranje i uravnotežen prostor.",
               {"syncopation": "medium", "space": "balanced"}),
    IntentCase("en-minimal", "Minimal arrangement, dry mix, no percussion.",
               {"density": "sparse", "space": "dry", "forbidden:percussion": True}),
    IntentCase("en-dense-roles", "Dense arrangement with guitar and riff.",
               {"density": "full", "required:guitar": True, "required:riff": True}),
    IntentCase("hr-accompaniment", "Sa pratnjom i podlogom, bez riffa.",
               {"required:accompaniment": True, "required:pad": True, "forbidden:riff": True}),
    IntentCase("en-section-energy", "Make a calm verse and powerful chorus.",
               {"energy.verse": 35, "energy.chorus": 85}),
    IntentCase("hr-edge-energy", "Mirni uvod i snažan kraj.",
               {"energy.intro": 35, "energy.ending": 85}),
    IntentCase("en-bridge-ending", "Soft bridge and full ending.",
               {"energy.bridge": 35, "energy.ending": 85}),
    IntentCase("genre-conflict", "Make a pop and rock primary style.", {}, True),
    IntentCase("density-conflict", "Use a sparse arrangement and a full arrangement.", {}, True),
    IntentCase("role-conflict", "Use drums but make it without drums.", {}, True),
    IntentCase("transition-conflict", "Use subtle transitions and dramatic transitions.", {}, True),
    IntentCase("solo-safety-conflict", "Delete the original solo and add a new lead.", {}, True),
)


def intent_value(brief: dict[str, Any], key: str) -> Any:
    if key.startswith("energy."):
        section = key.split(".", 1)[1]
        return next(item["value"] for item in brief["intent"]["energyCurve"]
                    if item["section"] == section)
    if key.startswith("required:"):
        return key.split(":", 1)[1] in brief["intent"]["requiredRoles"]
    if key.startswith("forbidden:"):
        return key.split(":", 1)[1] in brief["intent"]["forbiddenRoles"]
    if key.startswith("locked:"):
        return key.split(":", 1)[1] in brief["intent"]["lockedElements"]
    return brief["intent"][key]


def corpus_hash() -> str:
    payload = [{"id": case.case_id, "text": case.text,
                "expected": case.expected, "conflict": case.conflict} for case in CASES]
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")).hexdigest()