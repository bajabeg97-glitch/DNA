"""Self-authored Session 29 Personal Producer Profile fixture."""

from __future__ import annotations

from pathlib import Path

from .personal_profile import (
    build_cold_start_profile,
    build_personal_profile,
    build_preference_ranking_overlay,
    delete_personal_profile,
    edit_personal_profile,
    export_personal_profile,
)
from .session28_fixture import build_session28_chain


REFERENCE_DATE = "2026-09-03"


def build_session29_chain(root: str | Path):
    root = Path(root)
    previous = build_session28_chain(root)
    documents = previous["documents"]
    workflow = previous["workflow"]
    candidate_set = documents["candidateSet"]
    variant = next(item for item in candidate_set["variants"] if item["variantId"] == "variant-C")
    locked = next(item for item in variant["selections"]
                  if item["marker"] == "v2cv1" and item["patternId"] is not None)
    events = [
        {"eventId": "decision-accept-c-001", "eventType": "ACCEPT_VARIANT",
         "source": "USER_EXPLICIT", "accepted": True, "eventDate": REFERENCE_DATE,
         "workflowHash": workflow["workflowHash"], "variantId": "C",
         "marker": None, "role": None, "patternId": None},
        {"eventId": "decision-lock-v2-001", "eventType": "LOCK_SELECTION",
         "source": "USER_EXPLICIT", "accepted": True, "eventDate": REFERENCE_DATE,
         "workflowHash": workflow["workflowHash"], "variantId": "C",
         "marker": locked["marker"], "role": locked["role"],
         "patternId": locked["patternId"]},
    ]
    identity = {"profileId": "profile-local-producer", "displayName": "Moj producent",
                "locale": "hr-HR", "createdDate": REFERENCE_DATE, "enabled": True}
    profile = build_personal_profile(workflow, documents["producerBrief"],
                                     documents["arrangementGraph"], candidate_set,
                                     events, identity)
    overlay = build_preference_ranking_overlay(candidate_set, profile,
                                               genre=documents["producerBrief"]["intent"]["genre"])
    cold = build_cold_start_profile(REFERENCE_DATE, identity)
    cold_overlay = build_preference_ranking_overlay(candidate_set, cold,
                                                    genre=documents["producerBrief"]["intent"]["genre"])
    preferred_pattern = next(item for item in profile["preferences"]["patterns"]
                             if item["weight"] == 1.0)
    edited = edit_personal_profile(profile, {
        "effectiveDate": REFERENCE_DATE, "displayName": "Moj Premium producent",
        "enabled": True, "clearOverrides": False,
        "overrides": [{"dimension": "pattern", "key": preferred_pattern["patternId"],
                       "weight": 0.5, "reason": "Izričito prihvaćen omiljeni pattern",
                       "source": "USER_EXPLICIT"}],
    })
    edited_overlay = build_preference_ranking_overlay(candidate_set, edited,
                                                      genre=documents["producerBrief"]["intent"]["genre"])
    disabled = edit_personal_profile(profile, {"effectiveDate": REFERENCE_DATE,
                                               "enabled": False})
    disabled_overlay = build_preference_ranking_overlay(candidate_set, disabled,
                                                        genre=documents["producerBrief"]["intent"]["genre"])
    exported = export_personal_profile(edited, REFERENCE_DATE)
    deletion = delete_personal_profile(edited, REFERENCE_DATE)
    deleted_overlay = build_preference_ranking_overlay(candidate_set, deletion=deletion,
                                                       genre=documents["producerBrief"]["intent"]["genre"])
    return {**previous, "learningEvents": events, "profileIdentity": identity,
            "personalProfile": profile, "rankingOverlay": overlay,
            "coldStartProfile": cold, "coldStartOverlay": cold_overlay,
            "editedProfile": edited, "editedOverlay": edited_overlay,
            "disabledProfile": disabled, "disabledOverlay": disabled_overlay,
            "profileExport": exported, "profileDeletion": deletion,
            "deletedOverlay": deleted_overlay, "lockedSelection": locked}