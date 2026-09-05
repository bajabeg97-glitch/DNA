"""Role-aware KEEP/REPAIR/REPLACE/AUGMENT decision engine.

The engine does not mutate MIDI.  It decides the smallest justified action for a
track region.  Hard device/MIDI invariants remain the responsibility of the
CoreInvariantGuard and EvidenceAuthority layers.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

from .midi import MidiFile
from .instrument_behavior import analyze_role

DECISIONS = ("KEEP", "REPAIR", "REPLACE", "AUGMENT", "MANUAL_REVIEW")

ROLE_POLICY: dict[str, dict[str, float]] = {
    "drums": {"sparse": 0.7, "dense": 12.0, "short": 0.08},
    "percussion": {"sparse": 0.2, "dense": 8.0, "short": 0.06},
    "bass": {"sparse": 0.35, "dense": 5.0, "short": 0.10},
    "rhythm-guitar": {"sparse": 0.7, "dense": 12.0, "short": 0.07},
    "power-riff": {"sparse": 0.25, "dense": 8.0, "short": 0.06},
    "accompaniment": {"sparse": 0.25, "dense": 10.0, "short": 0.08},
    "pad": {"sparse": 0.08, "dense": 5.0, "short": 0.18},
    "strings": {"sparse": 0.10, "dense": 7.0, "short": 0.12},
    "brass": {"sparse": 0.08, "dense": 8.0, "short": 0.07},
    "woodwind": {"sparse": 0.08, "dense": 7.0, "short": 0.10},
    "sax": {"sparse": 0.08, "dense": 7.0, "short": 0.10},
    "accordion": {"sparse": 0.12, "dense": 10.0, "short": 0.08},
    "piano": {"sparse": 0.15, "dense": 14.0, "short": 0.07},
    "organ": {"sparse": 0.10, "dense": 10.0, "short": 0.12},
    "choir": {"sparse": 0.05, "dense": 5.0, "short": 0.20},
    "solo": {"sparse": 0.06, "dense": 9.0, "short": 0.08},
    "third": {"sparse": 0.04, "dense": 7.0, "short": 0.08},
    "echo": {"sparse": 0.02, "dense": 5.0, "short": 0.06},
}

ROLE_ALIASES = {
    "drum": "drums", "guitar": "rhythm-guitar", "harmonic": "accompaniment",
    "riff": "power-riff", "terca": "third",
}

@dataclass(frozen=True)
class RepairDecision:
    decision: str
    confidence: float
    severity: float
    reason_codes: tuple[str, ...]
    allowed_scope: tuple[str, ...]
    metrics: Mapping[str, Any]
    behavior: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "dna-role-aware-repair-decision",
            "version": "1.0",
            "decision": self.decision,
            "confidence": round(self.confidence, 4),
            "severity": round(self.severity, 4),
            "reasonCodes": list(self.reason_codes),
            "allowedScope": list(self.allowed_scope),
            "metrics": dict(self.metrics),
            "instrumentBehavior": dict(self.behavior),
            "policy": {
                "velocityAuthority": "FACTORY_ONLY",
                "replaceRequiresEvidence": True,
                "preserveGoodRegions": True,
                "hardRulesOwnedBy": "CORE_INVARIANT_AND_DEVICE_AUTHORITY",
            },
        }


def _canon_role(role: str) -> str:
    r = str(role or "accompaniment").strip().lower().replace("_", "-")
    return ROLE_ALIASES.get(r, r if r in ROLE_POLICY else "accompaniment")


def _scope(role: str, decision: str) -> tuple[str, ...]:
    if decision == "KEEP": return ()
    base = ["TIMING", "GATE", "PATTERN_SELECTION"]
    if role in {"solo", "third", "echo"}: base += ["PHRASE_CONTINUITY", "ORNAMENT_RELATIONSHIP"]
    if role == "drums": base += ["ELEMENT_BALANCE", "FILL_TRANSITION"]
    if role in {"bass", "rhythm-guitar", "power-riff"}: base += ["POCKET", "VOICE_LEADING"]
    if decision in {"REPLACE", "AUGMENT"}: base += ["NOTE_GENERATION_FACTORY_VELOCITY_AFTER_RENDER"]
    return tuple(base)


def decide_region(
    midi: MidiFile, *, role: str, track_index: int, channel: int,
    start_tick: int, end_tick: int, evidence_strength: float = 0.0,
    target_is_known_bad: bool = False,
) -> RepairDecision:
    role = _canon_role(role)
    policy = ROLE_POLICY[role]
    notes = [n for n in midi.notes() if n.track == track_index and n.channel == channel
             and n.start < end_tick and n.end > start_tick]
    span_qn = max(1e-6, (end_tick - start_tick) / max(1, midi.ppq))
    density = len(notes) / span_qn
    gates = []
    sorted_notes = sorted(notes, key=lambda n: (n.start, n.pitch, n.end))
    for i, n in enumerate(sorted_notes[:-1]):
        nxt = sorted_notes[i + 1]
        ioi = max(1, nxt.start - n.start)
        gates.append((n.end - n.start) / ioi)
    median_gate = median(gates) if gates else None
    zero_or_invalid = sum(n.end <= n.start for n in notes)
    same_start_pitch = len(notes) - len({(n.start, n.pitch) for n in notes})
    huge_leaps = sum(abs(b.pitch-a.pitch) >= 24 for a, b in zip(sorted_notes, sorted_notes[1:]))
    leap_rate = huge_leaps / max(1, len(sorted_notes)-1)

    reasons: list[str] = []
    severity = 0.0
    if not notes:
        reasons.append("TARGET_REGION_EMPTY")
        severity += 0.65
    elif density < policy["sparse"]:
        reasons.append("ROLE_UNDERFILLED")
        severity += min(0.35, (policy["sparse"]-density)/max(policy["sparse"], .01)*.35)
    if density > policy["dense"]:
        reasons.append("ROLE_OVERDENSE")
        severity += min(.25, (density-policy["dense"])/policy["dense"]*.2)
    if median_gate is not None and median_gate < policy["short"]:
        reasons.append("CHOPPY_GATE_PATTERN")
        severity += .22
    if same_start_pitch:
        reasons.append("DUPLICATE_NOTE_PRESSURE")
        severity += min(.2, same_start_pitch/max(1,len(notes))*.6)
    if role in {"bass","solo","third","echo","woodwind","sax"} and leap_rate > .35:
        reasons.append("UNSTABLE_REGISTER_MOTION")
        severity += min(.2, leap_rate*.3)
    if zero_or_invalid:
        reasons.append("INVALID_NOTE_DURATION")
        severity = 1.0
    if target_is_known_bad:
        reasons.append("USER_OR_UPSTREAM_BAD_REGION_FLAG")
        severity = min(1.0, severity + .18)
    severity = min(1.0, severity)

    evidence = max(0.0, min(1.0, float(evidence_strength)))
    if severity < .14:
        decision = "KEEP"
    elif severity < .45:
        decision = "REPAIR"
    elif evidence >= .72:
        decision = "REPLACE"
    elif not notes and evidence >= .45:
        decision = "AUGMENT"
    else:
        decision = "MANUAL_REVIEW"
        reasons.append("INSUFFICIENT_REPLACEMENT_EVIDENCE")

    # Lead tracks are intentionally conservative: never replace a non-empty solo
    # purely from generic density/gate metrics.
    if role == "solo" and notes and decision == "REPLACE":
        decision = "REPAIR"
        reasons.append("SOLO_MELODY_PRESERVATION_DOWNGRADE_REPLACE_TO_REPAIR")

    behavior = analyze_role(midi, role=role, track_index=track_index, channel=channel,
                            start_tick=start_tick, end_tick=end_tick, section="auto-repair")
    confidence = .55 + min(.35, abs(severity-.35)) + (evidence*.1 if decision in {"REPLACE","AUGMENT"} else 0)
    return RepairDecision(decision, min(1.0, confidence), severity, tuple(dict.fromkeys(reasons)),
                          _scope(role, decision), {
                              "noteCount": len(notes), "densityPerQuarter": round(density,4),
                              "medianGateToNextOnset": None if median_gate is None else round(median_gate,4),
                              "duplicatePressure": same_start_pitch,
                              "largeLeapRate": round(leap_rate,4),
                              "evidenceStrength": round(evidence,4),
                          }, behavior)
