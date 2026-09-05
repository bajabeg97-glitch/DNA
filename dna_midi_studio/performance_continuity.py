from __future__ import annotations

from dataclasses import replace
from typing import Iterable
import json
from pathlib import Path

from .midi import Note

PITCHED_ROLES = {"bass", "rhythm_guitar", "guitar", "power_riff", "accompaniment", "solo", "third", "terca", "echo"}


def _default_evidence_profiles() -> dict:
    # Portable source-tree lookup. Missing evidence is allowed and falls back to conservative constants.
    candidates = [
        Path(__file__).resolve().parents[2] / "data" / "continuity-evidence-profiles-4.34.json",
        Path.cwd() / "data" / "continuity-evidence-profiles-4.34.json",
    ]
    for path in candidates:
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8")).get("profiles", {})
        except Exception:
            pass
    return {}

_EVIDENCE_PROFILES = _default_evidence_profiles()


def smooth_phrase_notes(notes: Iterable[Note], *, ppq: int, role: str, region_end: int | None = None, evidence_profile: dict | None = None) -> tuple[list[Note], dict]:
    """Conservative post-generation continuity repair.

    Preserves pitch, onset and velocity. It only adjusts note end ticks for pitched
    performance roles. Drums/percussion are returned unchanged. The goal is to
    remove tiny artificial gaps and extremely short generated gates while keeping
    rhythmic re-attacks intact.
    """
    src = sorted(list(notes), key=lambda n: (n.start, n.pitch, n.end))
    if role not in PITCHED_ROLES or not src:
        return src, {"applied": False, "role": role, "adjusted": 0, "reason": "NON_PITCHED_OR_EMPTY"}

    normalized_role = "third" if role == "terca" else role
    profile = evidence_profile or _EVIDENCE_PROFILES.get(normalized_role)
    if profile and profile.get("policy"):
        policy = profile["policy"]
        min_gate = max(2, round(ppq * float(policy.get("minGateQn", 0.10))))
        bridge_gap = max(2, round(ppq * float(policy.get("bridgeGapQn", 0.06))))
        release_gap = max(1, round(ppq * float(policy.get("releaseGapQn", 0.01))))
        phrase_legato_max_ioi = max(0, round(ppq * float(policy.get("phraseLegatoMaxIoiQn", 0.0))))
        evidence_source = profile.get("source", "UNKNOWN_EVIDENCE")
        evidence_count = int(profile.get("evidenceCount", 0))
        evidence_confidence = float(profile.get("confidence", 0.0))
        short_gate_evidence_ratio = float(profile.get("shortGateEvidenceRatio", 0.0))
    else:
        # Backward-compatible conservative fallback when the 4.34 evidence artifact is unavailable.
        if role == "bass":
            min_gate=max(2,round(ppq*.22)); bridge_gap=max(2,round(ppq*.12)); release_gap=max(1,round(ppq*.015))
        elif role in {"solo","third","terca","echo"}:
            min_gate=max(2,round(ppq*.12)); bridge_gap=max(2,round(ppq*.08)); release_gap=max(1,round(ppq*.010))
        else:
            min_gate=max(2,round(ppq*.12)); bridge_gap=max(2,round(ppq*.06)); release_gap=max(1,round(ppq*.012))
        phrase_legato_max_ioi = max(0, round(ppq*.32)) if role in {"solo","third","terca","echo"} else 0
        evidence_source = "HEURISTIC_FALLBACK"
        evidence_count = 0
        evidence_confidence = 0.0
        short_gate_evidence_ratio = 0.0

    out: list[Note] = []
    adjusted = 0
    extended = 0
    shortened = 0
    for i, note in enumerate(src):
        next_start = src[i + 1].start if i + 1 < len(src) else None
        desired_end = note.end

        # Never allow a generated pitched note to collide with the next attack.
        hard_cap = None
        if next_start is not None and next_start > note.start:
            hard_cap = max(note.start + 1, next_start - release_gap)
        elif region_end is not None:
            hard_cap = max(note.start + 1, region_end)

        # Repair very short artificial gates where there is room to sustain.
        target_min_end = note.start + min_gate
        if desired_end < target_min_end:
            candidate = target_min_end
            if hard_cap is not None:
                candidate = min(candidate, hard_cap)
            if candidate > desired_end:
                desired_end = candidate
                extended += 1

        # Melodic phrase continuity: original Solo/Terca/Echo evidence shows near-legato
        # note-to-note gaps for short IOIs. If AI produced an early NoteOff, extend it
        # toward the next attack, but only inside the evidence-derived phrase IOI range.
        # Longer IOIs are treated as intentional rests / phrase boundaries.
        if next_start is not None and next_start > note.start and phrase_legato_max_ioi > 0:
            ioi = next_start - note.start
            if ioi <= phrase_legato_max_ioi:
                candidate = max(note.start + 1, next_start - release_gap)
                if candidate > desired_end:
                    desired_end = candidate
                    extended += 1

        # Bridge only tiny gaps to the next onset. This preserves the next NoteOn/re-attack.
        if next_start is not None and next_start > note.start:
            gap = next_start - desired_end
            if 0 < gap <= bridge_gap:
                candidate = max(note.start + 1, next_start - release_gap)
                if candidate > desired_end:
                    desired_end = candidate
                    extended += 1
            if hard_cap is not None and desired_end > hard_cap:
                desired_end = hard_cap
                shortened += 1

        if region_end is not None:
            desired_end = min(desired_end, max(note.start + 1, region_end))
        desired_end = max(note.start + 1, desired_end)
        if desired_end != note.end:
            adjusted += 1
            note = replace(note, end=desired_end)
        out.append(note)

    return out, {
        "applied": True,
        "role": role,
        "adjusted": adjusted,
        "extended": extended,
        "shortened": shortened,
        "minGateTicks": min_gate,
        "bridgeGapTicks": bridge_gap,
        "releaseGapTicks": release_gap,
        "phraseLegatoMaxIoiTicks": phrase_legato_max_ioi,
        "evidenceSource": evidence_source,
        "evidenceCount": evidence_count,
        "evidenceConfidence": evidence_confidence,
        "shortGateEvidenceRatio": short_gate_evidence_ratio,
        "pitchPreserved": True,
        "onsetPreserved": True,
        "velocityPreserved": True,
    }


def continuity_metrics(notes: Iterable[Note], *, ppq: int) -> dict:
    src=sorted(list(notes),key=lambda n:(n.start,n.pitch,n.end))
    if not src:
        return {"notes":0,"shortGateCount":0,"microGapCount":0,"medianGateQn":0.0}
    short=max(2,round(ppq*.12)); micro=max(2,round(ppq*.08))
    gates=[max(1,n.end-n.start) for n in src]
    gaps=[]
    for a,b in zip(src,src[1:]):
        if b.start>a.start:
            gaps.append(max(0,b.start-a.end))
    return {
        "notes":len(src),
        "shortGateCount":sum(g<short for g in gates),
        "microGapCount":sum(0<g<=micro for g in gaps),
        "medianGateQn":round(float(sorted(gates)[len(gates)//2])/max(1,ppq),4),
    }
