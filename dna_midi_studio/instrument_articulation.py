"""Evidence-gated musical articulation analysis.

This module classifies musician-facing techniques from MIDI context without
inventing Pa800 RX/DNC trigger mappings.  It is intentionally semantic:
`SLIDE_CANDIDATE` or `BREATH_CANDIDATE` may influence ranking/planning, but a
real keyswitch/CC/noise insertion is only allowed later by the exact-sound
RX/DNC engines.
"""
from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any, Mapping, Sequence

from .midi import MidiFile, Note


def _channel_events(midi: MidiFile, track_index: int, channel: int, start_tick: int, end_tick: int):
    if track_index < 0 or track_index >= len(midi.tracks):
        return []
    out = []
    for e in midi.tracks[track_index].events:
        if e.tick < start_tick or e.tick >= end_tick or e.channel != channel:
            continue
        out.append(e)
    return out


def _cc_points(events, cc: int):
    return [(e.tick, int(e.data[1])) for e in events if e.command == 0xB0 and len(e.data) >= 2 and int(e.data[0]) == cc]


def _pitch_bend(events):
    points = []
    for e in events:
        if e.command == 0xE0 and len(e.data) >= 2:
            raw = int(e.data[0]) | (int(e.data[1]) << 7)
            points.append((e.tick, raw - 8192))
    return points


def _aftertouch(events):
    return [(e.tick, int(e.data[0])) for e in events if e.command == 0xD0 and len(e.data) >= 1]


def _trend(points: Sequence[tuple[int, int]]) -> float:
    if len(points) < 2:
        return 0.0
    return float(points[-1][1] - points[0][1])


def _gate_ratios(notes: Sequence[Note]) -> list[float]:
    ratios: list[float] = []
    for i, n in enumerate(notes):
        nxt = notes[i + 1] if i + 1 < len(notes) else None
        ioi = (nxt.start - n.start) if nxt and nxt.start > n.start else max(1, n.end - n.start)
        ratios.append((n.end - n.start) / max(1, ioi))
    return ratios


def _phrase_breaks(notes: Sequence[Note], ppq: int) -> int:
    if not notes:
        return 0
    return sum(1 for a, b in zip(notes, notes[1:]) if b.start - a.end >= max(ppq // 2, 1))


def analyze_articulation_context(
    midi: MidiFile,
    *,
    role: str,
    track_index: int,
    channel: int,
    start_tick: int,
    end_tick: int,
    notes: Sequence[Note],
    section: str = "generic",
    confirmed_device_articulations: Sequence[str] = (),
) -> dict[str, Any]:
    """Return musician-technique candidates plus device-actionability state."""
    role = role.lower().replace("_", "-")
    events = _channel_events(midi, track_index, channel, start_tick, end_tick)
    pb = _pitch_bend(events)
    cc1 = _cc_points(events, 1)
    cc2 = _cc_points(events, 2)
    cc11 = _cc_points(events, 11)
    cc64 = _cc_points(events, 64)
    at = _aftertouch(events)
    gates = _gate_ratios(notes)
    med_gate = float(median(gates)) if gates else None
    candidates: Counter[str] = Counter()
    reasons: dict[str, list[str]] = {}

    def add(name: str, reason: str, weight: int = 1) -> None:
        candidates[name] += max(1, weight)
        reasons.setdefault(name, []).append(reason)

    # Generic phrase evidence.
    if notes and _phrase_breaks(notes, midi.ppq):
        add("PHRASE_BREATH_OR_RELEASE", "long inter-phrase gap")
    if pb and max(abs(v) for _, v in pb) >= 1024:
        add("PITCH_BEND_EXPRESSIVE", "existing pitch-bend trajectory")
    if cc11 and abs(_trend(cc11)) >= 8:
        add("EXPRESSION_SWELL", "CC11 contour changes through phrase")

    # Note-neighborhood evidence.
    for i, n in enumerate(notes[:-1]):
        b = notes[i + 1]
        interval = b.pitch - n.pitch
        gap = b.start - n.end
        dur = n.end - n.start
        ioi = max(1, b.start - n.start)
        gate = dur / ioi
        if abs(interval) in (1, 2) and dur <= max(1, midi.ppq // 4):
            add("GRACE_OR_TRILL_CONTEXT", "short semitone/whole-tone neighbor")
        if 1 <= abs(interval) <= 7 and gap <= max(1, midi.ppq // 16):
            add("LEGATO_OR_SLIDE_CONTEXT", "small interval with near-legato transition")
        if gate < 0.35:
            add("SHORT_ARTICULATION_CONTEXT", "short gate relative to next onset")

    if role == "bass":
        for i, n in enumerate(notes[:-1]):
            b = notes[i + 1]
            interval = b.pitch - n.pitch
            gate = (n.end - n.start) / max(1, b.start - n.start)
            if abs(interval) == 12:
                add("BASS_OCTAVE", "exact octave movement")
            if abs(interval) >= 7 and gate < 0.45:
                add("SLAP_POP_CANDIDATE", "wide leap + short articulation")
            if 1 <= abs(interval) <= 5 and gate >= 0.85:
                add("BASS_SLIDE_LEGATO_CANDIDATE", "close interval + sustained gate")
            if 1 <= abs(interval) <= 2:
                add("BASS_APPROACH", "chromatic/diatonic approach movement")
    elif role in {"rhythm-guitar", "power-riff", "guitar"}:
        short = sum(1 for g in gates if g < 0.45)
        repeated = sum(1 for a, b in zip(notes, notes[1:]) if a.pitch == b.pitch)
        if short >= max(2, len(notes) // 4):
            add("PALM_MUTE_CANDIDATE", "recurrent short guitar gates", 2)
        if repeated >= 2 and short:
            add("DEAD_GHOST_STRUM_CANDIDATE", "repeated pitch/chord with short articulation")
        if pb:
            add("GUITAR_SLIDE_BEND_CANDIDATE", "existing pitch bend on guitar track", 2)
        if "transition" in section.lower() or "fill" in section.lower():
            add("GUITAR_RELEASE_NOISE_CONTEXT", "phrase/transition boundary")
    elif role in {"solo", "terca", "echo", "accordion", "sax", "woodwind", "brass"}:
        if med_gate is not None and med_gate >= 0.72:
            add("LEGATO_PHRASE", "sustained melodic gate profile")
        if pb:
            add("SLIDE_BEND_CANDIDATE", "existing pitch-bend evidence", 2)
        if cc1 and max(v for _, v in cc1) >= 64:
            add("MODULATION_ARTICULATION_CONTEXT", "CC1 reaches expressive range")
        if at and max(v for _, v in at) >= 90:
            add("AFTERTOUCH_ARTICULATION_CONTEXT", "channel aftertouch reaches high expressive range")
        if role in {"sax", "woodwind", "brass"} and _phrase_breaks(notes, midi.ppq):
            add("BREATH_NOISE_CANDIDATE", "wind/brass phrase boundary")
        if role == "brass" and pb and _trend(pb) < -1024:
            add("FALL_CANDIDATE", "downward pitch-bend trajectory")
        if role == "accordion" and cc11 and abs(_trend(cc11)) >= 8:
            add("BELLOWS_EXPRESSION_CANDIDATE", "CC11 phrase contour")
    elif role in {"strings", "pad", "choir"}:
        if med_gate is not None and med_gate >= 0.8:
            add("SUSTAIN_LEGATO", "high median gate")
        if cc11 and _trend(cc11) > 8:
            add("SWELL_CANDIDATE", "rising expression contour")
        if cc11 and _trend(cc11) < -8:
            add("RELEASE_FADE_CANDIDATE", "falling expression contour")
    elif role in {"piano", "organ"}:
        if cc64:
            add("DAMPER_CONTEXT", "CC64 is present and must be preserved")
        if role == "piano" and cc64 and notes:
            add("PEDAL_RESONANCE_CONTEXT", "pedal state may carry timbral/release semantics")

    confirmed = {x.upper() for x in confirmed_device_articulations}
    rows = []
    for name, count in candidates.most_common():
        device_actionable = name in confirmed
        rows.append({
            "candidate": name,
            "supportCount": int(count),
            "reasons": reasons.get(name, []),
            "deviceActionability": "CONFIRMED_PROFILE" if device_actionable else "SEMANTIC_ONLY",
        })

    return {
        "schema": "dna-articulation-context-analysis",
        "version": "1.0",
        "role": role,
        "candidateCount": len(rows),
        "candidates": rows,
        "controllerContext": {
            "cc1Points": len(cc1),
            "cc2Points": len(cc2),
            "cc11Points": len(cc11),
            "cc64Points": len(cc64),
            "aftertouchPoints": len(at),
            "pitchBendPoints": len(pb),
        },
        "medianGateRatio": round(med_gate, 4) if med_gate is not None else None,
        "policy": {
            "semanticClassificationMayInfluenceRanking": True,
            "deviceTriggerInsertionRequiresExactConfirmedSoundProfile": True,
            "neverInferKeyswitchFromSemanticLabelAlone": True,
            "preserveExistingPitchBendAndDncSensitiveControllers": True,
        },
    }

_SEMANTIC_TO_TRIGGER_TOKENS = {
    "LEGATO_PHRASE": ("legato",),
    "LEGATO_OR_SLIDE_CONTEXT": ("legato", "slide"),
    "BASS_SLIDE_LEGATO_CANDIDATE": ("legato", "slide"),
    "SLIDE_BEND_CANDIDATE": ("slide", "bend", "legato"),
    "GUITAR_SLIDE_BEND_CANDIDATE": ("slide", "bend", "legato"),
    "PALM_MUTE_CANDIDATE": ("palm", "mute"),
    "DEAD_GHOST_STRUM_CANDIDATE": ("ghost", "dead", "mute"),
    "SLAP_POP_CANDIDATE": ("slap", "pop"),
    "BREATH_NOISE_CANDIDATE": ("breath", "noise"),
    "GUITAR_RELEASE_NOISE_CONTEXT": ("release", "fret", "noise"),
    "FALL_CANDIDATE": ("fall",),
    "AFTERTOUCH_ARTICULATION_CONTEXT": ("aftertouch", "after_touch"),
    "MODULATION_ARTICULATION_CONTEXT": ("mod", "y_plus", "y_minus", "controller"),
    "DAMPER_CONTEXT": ("damper", "pedal"),
}


def suggest_confirmed_device_articulations(
    articulation_analysis: Mapping[str, Any],
    confirmed_map: Any,
    *,
    exact_sound_matches: bool,
) -> dict[str, Any]:
    """Map semantic candidates to already-confirmed map trigger names only.

    This never creates trigger definitions.  If exact sound does not match, the
    confirmed map is ignored completely.
    """
    if not exact_sound_matches or confirmed_map is None or not bool(getattr(confirmed_map, "confirmed", False)):
        return {"decision":"BLOCKED","requested":[],"reason":"EXACT_CONFIRMED_SOUND_MAP_REQUIRED"}
    available = [str(getattr(t, "articulation", "")) for t in getattr(confirmed_map, "triggers", ())]
    available_l = {name: name.lower() for name in available if name}
    requested: list[str] = []
    evidence: list[dict[str, Any]] = []
    for row in articulation_analysis.get("candidates", []):
        semantic = str(row.get("candidate", ""))
        tokens = _SEMANTIC_TO_TRIGGER_TOKENS.get(semantic, ())
        if not tokens:
            continue
        matches = [name for name, low in available_l.items() if any(token in low for token in tokens)]
        if not matches:
            continue
        # Stable deterministic choice; the map itself remains the authority.
        chosen = sorted(matches)[0]
        if chosen not in requested:
            requested.append(chosen)
            evidence.append({"semanticCandidate":semantic,"confirmedTrigger":chosen,"supportCount":int(row.get("supportCount",1))})
    return {
        "decision":"APPLICABLE" if requested else "KEEP",
        "requested":requested,
        "evidence":evidence,
        "rule":"SEMANTIC_AI_MAY_SELECT_ONLY_FROM_EXISTING_CONFIRMED_TRIGGER_MAP",
    }
