"""Strict musical-role disambiguation for accompaniment, guitar, power-riff, solo and echo.

Instrument family is only a timbral hint.  Musical role is inferred from note behaviour.
Echo is relationship-only: it can never be declared from repetition alone and is explicitly
blocked for strumming/chordal textures.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
import math
import statistics
from typing import Iterable, Sequence, Mapping, Any

from .midi import Note


@dataclass(frozen=True)
class RoleSignature:
    note_count: int
    onset_count: int
    monophonic_onset_rate: float
    chordal_onset_rate: float
    staggered_chord_rate: float
    power_interval_rate: float
    repeated_pitch_rate: float
    melodic_motion_rate: float
    short_gate_rate: float
    median_gate_ticks: float
    median_pitch: float | None
    pitch_span: int
    rhythm_guitar_score: float
    power_riff_score: float
    solo_score: float
    strum_like: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RoleDecision:
    role: str
    confidence: float
    reasons: tuple[str, ...]
    signature: RoleSignature
    echo_eligible: bool

    def to_dict(self) -> dict[str, Any]:
        d = {"role": self.role, "confidence": round(self.confidence, 4),
             "reasons": list(self.reasons), "echoEligible": self.echo_eligible}
        d["signature"] = self.signature.to_dict()
        return d


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _groups(notes: Sequence[Note], tolerance: int) -> list[list[Note]]:
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch, n.end))
    groups: list[list[Note]] = []
    current = [ordered[0]]
    anchor = ordered[0].start
    for n in ordered[1:]:
        if n.start - anchor <= tolerance:
            current.append(n)
        else:
            groups.append(current)
            current = [n]
            anchor = n.start
    groups.append(current)
    return groups


def signature(notes: Sequence[Note], ppq: int) -> RoleSignature:
    notes = list(notes)
    if not notes:
        return RoleSignature(0,0,0,0,0,0,0,0,0,0,None,0,0,0,0,False)
    exact = defaultdict(list)
    for n in notes:
        exact[n.start].append(n)
    onset_count = len(exact)
    mono = sum(len(v) == 1 for v in exact.values()) / max(1, onset_count)
    chordal = sum(len(v) >= 2 for v in exact.values()) / max(1, onset_count)

    tol = max(1, round(ppq * 0.07))
    clusters = _groups(notes, tol)
    staggered = sum(len({n.start for n in g}) >= 2 and len(g) >= 3 for g in clusters) / max(1, len(clusters))

    power_hits = 0
    harmonic_hits = 0
    for g in clusters:
        pitches = sorted(set(n.pitch for n in g))
        if len(pitches) < 2:
            continue
        harmonic_hits += 1
        root = pitches[0]
        pcs = {(p-root) % 12 for p in pitches[1:]}
        # Power voicing: fifth/octave dominant, with no defining 3rd.
        if pcs and pcs.issubset({0,7}) and 7 in pcs:
            power_hits += 1
    power_rate = power_hits / max(1, harmonic_hits)

    ordered = sorted(notes, key=lambda n:(n.start,n.pitch))
    repeated = sum(a.pitch == b.pitch for a,b in zip(ordered, ordered[1:])) / max(1, len(ordered)-1)
    motion_pairs = [(a,b) for a,b in zip(ordered, ordered[1:]) if b.start > a.start]
    melodic_motion = sum(0 < abs(b.pitch-a.pitch) <= 12 for a,b in motion_pairs) / max(1, len(motion_pairs))
    gates = [max(0,n.end-n.start) for n in notes]
    short = sum(g <= max(1, round(ppq*.13)) for g in gates) / len(gates)
    med_gate = float(statistics.median(gates))
    pitches = [n.pitch for n in notes]
    med_pitch = float(statistics.median(pitches))
    span = max(pitches)-min(pitches)

    # Scores are role-behaviour scores, not sound-family scores.
    rg = _clamp(0.36*max(chordal,staggered) + 0.30*staggered + 0.14*short + 0.10*(1-mono) + 0.10*(1-power_rate))
    pr = _clamp(0.58*power_rate + 0.15*(1-mono) + 0.12*short + 0.10*(1 if med_pitch <= 62 else 0) + 0.05*repeated)
    solo = _clamp(0.46*mono + 0.27*melodic_motion + 0.12*(1-chordal) + 0.10*(1-staggered) + 0.05*(1-power_rate))
    strum_like = rg >= .54 and (chordal >= .25 or staggered >= .22)
    return RoleSignature(len(notes),onset_count,round(mono,4),round(chordal,4),round(staggered,4),
                         round(power_rate,4),round(repeated,4),round(melodic_motion,4),round(short,4),
                         round(med_gate,3),round(med_pitch,3),span,round(rg,4),round(pr,4),round(solo,4),strum_like)


def classify(notes: Sequence[Note], ppq: int, *, family: str = "unknown", base_role: str = "unknown",
             track_name: str = "") -> RoleDecision:
    s = signature(notes, ppq)
    name = track_name.lower()
    reasons: list[str] = []
    # Explicit names are evidence, but behaviour still has veto power for destructive routing.
    name_power = any(x in name for x in ("power", "powerchord", "power chord", "riff"))
    name_solo = any(x in name for x in ("solo", "lead", "melody", "melodija"))
    name_gtr = any(x in name for x in ("guitar", "gitara", "gtr", "ritam"))

    if s.power_riff_score >= .62 and s.power_interval_rate >= .45:
        reasons += ["POWER_INTERVAL_VOICING", "LOW_POLYPHONIC_RIFF_BEHAVIOR"]
        if name_power: reasons.append("TRACK_NAME_POWER_HINT")
        return RoleDecision("power-riff", max(.72,s.power_riff_score), tuple(reasons), s, False)

    if s.rhythm_guitar_score >= .54 and (s.chordal_onset_rate >= .25 or s.staggered_chord_rate >= .22):
        reasons += ["CHORDAL_STRUM_TEXTURE", "RHYTHM_GUITAR_BEHAVIOR"]
        if family == "guitar": reasons.append("GUITAR_FAMILY_HINT")
        if name_gtr: reasons.append("TRACK_NAME_GUITAR_HINT")
        # A strum-like track is never echo-eligible.
        return RoleDecision("rhythm-guitar", max(.68,s.rhythm_guitar_score), tuple(reasons), s, False)

    if s.solo_score >= .68 and s.monophonic_onset_rate >= .72:
        reasons += ["MONOPHONIC_MELODIC_CONTOUR", "SOLO_BEHAVIOR"]
        if name_solo: reasons.append("TRACK_NAME_SOLO_HINT")
        return RoleDecision("solo", max(.70,s.solo_score), tuple(reasons), s, True)

    # Keep exact/base roles only when the behaviour does not contradict them.
    if base_role in {"bass","drums","percussion","strings","brass","woodwind","sax","accordion","piano","organ","choir","pad"}:
        return RoleDecision(base_role, .70, ("BASE_ROLE_PRESERVED",), s, False)
    if family == "guitar" or base_role in {"guitar","harmony","accompaniment"}:
        return RoleDecision("accompaniment", .55, ("GUITAR_OR_CHORDAL_FAMILY_WITHOUT_STRUM_POWER_PROOF",), s, False)
    return RoleDecision(base_role if base_role not in {"melody","guitar"} else "unknown", .45,
                        ("INSUFFICIENT_ROLE_DISAMBIGUATION_EVIDENCE",), s, False)


def echo_relationship(source: Sequence[Note], target: Sequence[Note], ppq: int) -> dict[str, Any]:
    """Verify target as echo of source. Strumming/chordal target is a hard veto."""
    ss = signature(source, ppq)
    ts = signature(target, ppq)
    if ts.strum_like or ts.chordal_onset_rate >= .28 or ts.staggered_chord_rate >= .20:
        return {"isEcho":False,"confidence":0.0,"reason":"STRUMMING_VETO","delayTicks":None,
                "targetStrumLike":True}
    if not source or not target:
        return {"isEcho":False,"confidence":0.0,"reason":"NO_SOURCE_OR_TARGET_NOTES","delayTicks":None,
                "targetStrumLike":False}
    source_by_pitch = defaultdict(list)
    for n in source:
        source_by_pitch[n.pitch].append(n.start)
    delays=[]; matched=0
    max_delay=max(ppq*2,1)
    for t in target:
        candidates=[t.start-s for s in source_by_pitch.get(t.pitch,()) if 0 < t.start-s <= max_delay]
        if candidates:
            d=min(candidates)
            delays.append(d); matched+=1
    pitch_match=matched/max(1,len(target))
    if not delays:
        return {"isEcho":False,"confidence":0.0,"reason":"NO_DELAYED_PITCH_RELATION","delayTicks":None,
                "targetStrumLike":False,"pitchMatchRate":round(pitch_match,4)}
    med=int(round(statistics.median(delays)))
    tolerance=max(2,round(ppq*.08))
    consistent=sum(abs(d-med)<=tolerance for d in delays)/len(delays)
    sparse_ratio=len(target)/max(1,len(source))
    sparse_score=1.0 if sparse_ratio <= .72 else max(0.0,1.5-sparse_ratio)
    conf=_clamp(.48*pitch_match+.37*consistent+.15*sparse_score)
    ok=pitch_match>=.68 and consistent>=.62 and sparse_ratio<=1.0 and conf>=.70
    return {"isEcho":bool(ok),"confidence":round(conf,4),"reason":"SOLO_RELATION_CONFIRMED" if ok else "RELATION_TOO_WEAK",
            "delayTicks":med,"pitchMatchRate":round(pitch_match,4),"delayConsistency":round(consistent,4),
            "targetToSourceDensity":round(sparse_ratio,4),"targetStrumLike":False}


def engine_target_policy(engine: str, role: str) -> dict[str, Any]:
    engine=str(engine); role=str(role)
    allowed={
        "guitar":{"rhythm-guitar"},
        "solo":{"solo"},
        "drum":{"drums"},
        # power-riff is deliberately not routed through guitar; harmonic/arrangement layer may own it.
        "harmonic":{"accompaniment","harmony","power-riff","piano","organ","accordion"},
        "rx":{"accompaniment","rhythm-guitar","power-riff","bass","solo","strings","brass","woodwind","sax","accordion","piano","organ","choir","pad"},
        "dnc":{"accompaniment","rhythm-guitar","power-riff","bass","solo","strings","brass","woodwind","sax","accordion","piano","organ","choir","pad"},
    }
    ok=role in allowed.get(engine,{role})
    return {"allowed":ok,"engine":engine,"detectedRole":role,
            "reason":"ROLE_ENGINE_MATCH" if ok else "ROLE_ENGINE_MISMATCH",
            "policy":"POWER_RIFF_IS_NOT_RHYTHM_GUITAR;ECHO_REQUIRES_SOLO_RELATION;STRUMMING_VETOES_ECHO"}
