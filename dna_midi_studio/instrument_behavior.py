"""Instrument Behavior Brain.

Musician-oriented semantic layer for DNA MIDI Studio.  It describes how each
role behaves, scores existing/generative patterns, and labels performance
techniques without claiming unverified Pa800 RX/DNC trigger mappings.

Hard device semantics remain the responsibility of EvidenceAuthority/RX-DNC
profiles.  This module may emit SLAP_CANDIDATE, PALM_MUTE_CANDIDATE, etc.; it
never invents a keyswitch/controller/velocity trigger.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from collections import Counter
from math import exp
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from .midi import MidiFile, Note
from .instrument_articulation import analyze_articulation_context
from .instrument_performance_grammar import phrase_plan as performance_phrase_plan, grammar_for as performance_grammar_for


@dataclass(frozen=True)
class DrumElementBehavior:
    element: str
    musical_job: str
    timing_priority: str
    density_policy: str
    transition_behavior: str
    duration_policy: str = "preserve-hit"


@dataclass(frozen=True)
class InstrumentBehaviorProfile:
    role: str
    musician_model: str
    follows: tuple[str, ...]
    techniques: tuple[str, ...]
    phrase_rules: tuple[str, ...]
    optimization_priorities: tuple[str, ...]
    forbidden_without_device_evidence: tuple[str, ...] = ()
    evidence_policy: str = "evidence-guided-soft-musical-hard-device"


DRUM_ELEMENTS: dict[str, DrumElementBehavior] = {
    "kick": DrumElementBehavior("kick", "low-end pulse; locks with bass", "foundation", "groove-dependent", "pickup/fill support, never random clutter"),
    "snare": DrumElementBehavior("snare", "backbeat/accent and phrase punctuation", "foundation", "meter-dependent", "accent or fill punctuation"),
    "side-stick": DrumElementBehavior("side-stick", "soft backbeat/color", "secondary", "sparse", "normally withdraws in strong fills"),
    "closed-hat": DrumElementBehavior("closed-hat", "subdivision/timekeeper", "high", "continuous-but-patterned", "may open/simplify approaching fill"),
    "open-hat": DrumElementBehavior("open-hat", "lift/release accent", "accent", "sparse", "use as transition lift, not constant"),
    "ride": DrumElementBehavior("ride", "alternate timekeeper/open section energy", "high", "section-dependent", "can replace hats in open/chorus sections"),
    "crash": DrumElementBehavior("crash", "section/phrase accent", "punctuation", "very-sparse", "strong at boundaries/entries"),
    "tom": DrumElementBehavior("tom", "fill/melodic drum movement", "transition", "sparse-outside-fill", "density may rise near transition"),
    "clap": DrumElementBehavior("clap", "backbeat reinforcement", "secondary", "sparse", "section-energy dependent"),
    "ghost-snare": DrumElementBehavior("ghost-snare", "micro-groove between primary accents", "micro", "low-density", "must not replace main backbeat"),
    "percussion": DrumElementBehavior("percussion", "color/interlock", "secondary", "subordinate", "reduce clutter around fills/solo density"),
    "other": DrumElementBehavior("other", "unknown kit element", "preserve", "preserve", "no generated behavior without evidence"),
}


PROFILES: dict[str, InstrumentBehaviorProfile] = {
    "drums": InstrumentBehaviorProfile(
        "drums", "drummer", ("meter", "section", "bass", "phrase-boundary"),
        ("kick-pocket", "backbeat", "ghost", "hat-subdivision", "fill", "crash-entry", "tom-transition"),
        ("foundation before decoration", "fills grow from groove", "preserve clear kick/snare hierarchy"),
        ("element-specific balance", "drum-bass coupling", "transition logic", "remove implausible clutter"),
        ("unverified-kit-remap", "unverified-rx-drum-trigger"),
    ),
    "percussion": InstrumentBehaviorProfile(
        "percussion", "percussionist", ("drums", "meter", "section", "space"),
        ("interlock", "offbeat-color", "pickup", "sparse-fill"),
        ("never compete with kick/snare", "leave space for lead"),
        ("density balance", "interlock with drum grid", "reduce redundant conga/shaker clutter"),
    ),
    "bass": InstrumentBehaviorProfile(
        "bass", "bassist", ("kick", "chord-root", "next-chord", "phrase", "section"),
        ("root", "fifth", "octave", "third", "passing", "chromatic-approach", "pickup", "ghost-candidate", "slap-candidate", "pop-candidate", "slide-candidate"),
        ("land chord changes clearly", "use approaches into targets", "octaves increase energy", "avoid random register jumps", "keep pocket with kick"),
        ("functional-note labeling", "kick-pocket score", "voice-leading", "gate/continuity", "phrase-development"),
        ("slap-trigger", "pop-trigger", "rx-noise-trigger", "dnc-slide-trigger"),
    ),
    "rhythm-guitar": InstrumentBehaviorProfile(
        "rhythm-guitar", "rhythm guitarist", ("chords", "meter", "drums", "section", "phrase"),
        ("down", "up", "block", "mute-candidate", "palm-mute-candidate", "stop", "pickup", "open-strum", "single-string-candidate"),
        ("stroke direction must form playable strum flow", "short gate may be intentional", "open voicing in higher energy", "do not piano-quantize strums"),
        ("Factory strumming evidence", "stroke spacing", "mute/open contrast", "chord change cleanliness"),
        ("guitar-mode-trigger", "rx-fret-noise", "dnc-controller"),
    ),
    "power-riff": InstrumentBehaviorProfile(
        "power-riff", "power-chord guitarist", ("chords", "kick", "section", "transition"),
        ("root-fifth", "root-fifth-octave", "octave", "mute-hit", "sustain-hit", "pickup", "register-lift"),
        ("chord-relative voicing", "repeat with controlled variation", "buildup before transition", "strong voice-leading"),
        ("attack-pattern", "voicing", "gate", "register motion", "phrase development", "transition pickup"),
        ("unverified-guitar-trigger",),
    ),
    "riff": InstrumentBehaviorProfile(
        "riff", "riff player", ("chords", "groove", "phrase"),
        ("motif", "repeat", "variation", "pickup", "answer"),
        ("preserve recognizable motif", "vary endings more than anchors"),
        ("motif continuity", "register", "syncopation", "cadence"),
    ),
    "accompaniment": InstrumentBehaviorProfile(
        "accompaniment", "arranger", ("chords", "lead-space", "section"),
        ("stab", "sustain", "voice-lead", "answer", "rest"),
        ("support not dominate", "common tones preferred", "leave room for solo"),
        ("voice-leading", "register separation", "density", "section energy"),
    ),
    "pad": InstrumentBehaviorProfile(
        "pad", "pad/strings player", ("chords", "section", "lead-space"),
        ("sustain", "common-tone", "inversion", "slow-move", "swell-candidate"),
        ("minimal reattack", "smooth common-tone voice-leading", "avoid bass register"),
        ("continuity", "register separation", "voice-leading", "sparse reattack"),
    ),
    "brass": InstrumentBehaviorProfile(
        "brass", "brass arranger", ("chords", "section", "lead", "transition"),
        ("stab", "unison-hit", "octave-hit", "answer", "sustain", "fall-candidate"),
        ("breathing space", "strong accents at phrase points", "avoid continuous block chords"),
        ("attack placement", "register separation", "answer-phrase logic", "gate"),
        ("unverified-fall-trigger", "unverified-growl-trigger"),
    ),
    "accordion": InstrumentBehaviorProfile(
        "accordion", "accordionist", ("melody", "chords", "phrase", "section"),
        ("legato", "grace", "turn", "trill", "repeat-note", "bellows-expression-candidate"),
        ("phrase in breaths", "ornaments lead into important notes", "avoid machine-gun gate"),
        ("phrase contour", "legato", "ornament placement", "expression continuity"),
        ("unverified-dnc-controller",),
    ),
    "strings": InstrumentBehaviorProfile(
        "strings", "string-section arranger", ("chords", "lead-space", "section", "phrase"),
        ("legato", "sustain", "common-tone", "bow-change-candidate", "pizzicato-candidate", "swell-candidate"),
        ("voice-lead common tones", "avoid unnecessary reattack", "open register around lead", "build/release by section"),
        ("voice-leading", "continuity", "register separation", "expression contour"),
        ("unverified-pizzicato-trigger", "unverified-bow-noise-trigger"),
    ),
    "woodwind": InstrumentBehaviorProfile(
        "woodwind", "wind player", ("melody", "chords", "phrase", "breath-space"),
        ("legato", "tongue-candidate", "grace", "slide-candidate", "breath-noise-candidate"),
        ("phrase in breaths", "avoid endless sustain", "ornament phrase entries/exits", "preserve expressive controller curves"),
        ("phrase segmentation", "gate", "breath placement", "pitch-bend/controller preservation"),
        ("unverified-breath-trigger", "unverified-dnc-controller"),
    ),
    "sax": InstrumentBehaviorProfile(
        "sax", "saxophonist", ("melody", "chords", "phrase", "section"),
        ("legato", "tongue-candidate", "bend", "fall-candidate", "growl-candidate", "breath-noise-candidate"),
        ("shape breath-length phrases", "bend/fall only near musically plausible phrase points", "preserve main melody"),
        ("phrase contour", "legato", "bend/fall evidence", "breath spacing", "expression"),
        ("unverified-growl-trigger", "unverified-breath-trigger", "unverified-dnc-controller"),
    ),
    "piano": InstrumentBehaviorProfile(
        "piano", "pianist", ("harmony", "phrase", "damper", "lead-space"),
        ("legato", "staccato", "pedal", "arpeggio", "grace"),
        ("preserve damper semantics", "avoid artificial overlap under pedal", "voice-lead inner voices"),
        ("pedal preservation", "gate", "voicing", "register separation"),
        ("unverified-rx-pedal-noise-trigger",),
    ),
    "organ": InstrumentBehaviorProfile(
        "organ", "organist", ("chords", "section", "lead-space"),
        ("sustain", "manual-change-candidate", "staccato-accent", "gliss-candidate"),
        ("sustain is normal", "avoid piano-like pedal assumptions", "leave bass register clear when bass is present"),
        ("gate continuity", "voicing", "register separation"),
    ),
    "choir": InstrumentBehaviorProfile(
        "choir", "choir arranger", ("chords", "section", "lead-space", "phrase"),
        ("sustain", "common-tone", "swell-candidate", "release-fade-candidate"),
        ("smooth voice leading", "avoid excessive reattack", "keep below lead prominence"),
        ("continuity", "register separation", "expression contour"),
    ),
    "solo": InstrumentBehaviorProfile(
        "solo", "lead musician", ("chords", "phrase", "section", "cadence", "available-space"),
        ("chord-tone", "passing", "neighbor", "approach", "grace", "trill", "slide-candidate", "turnaround", "repeat-variation", "cadence"),
        ("shape complete phrases, not isolated notes", "ornament only where phrase supports it", "legato by default unless articulation evidence says otherwise", "cadence resolves phrase", "preserve main melodic identity"),
        ("phrase segmentation", "continuity", "ornament evidence", "pitch-bend preservation", "expression contour", "cadence", "relationship layers"),
        ("unverified-rx-dnc-trigger",),
    ),
    "terca": InstrumentBehaviorProfile(
        "terca", "harmony singer/player", ("main-solo", "chords", "phrase"),
        ("diatonic-third", "contextual-third", "unison-avoidance", "phrase-follow"),
        ("follow solo rhythm selectively", "never overpower main solo", "harmonic validity before density"),
        ("relationship correctness", "level hierarchy", "phrase alignment", "continuity"),
    ),
    "echo": InstrumentBehaviorProfile(
        "echo", "delay/answer player", ("main-solo", "phrase-space", "section"),
        ("nonrecursive-delay", "answer", "tail"),
        ("never recursively echo itself", "use available phrase space", "remain below main solo"),
        ("delay timing", "density restraint", "level hierarchy", "gate/continuity"),
    ),
}


GM_DRUM_MAP = {
    35: "kick", 36: "kick",
    37: "side-stick", 38: "snare", 40: "snare",
    39: "clap",
    42: "closed-hat", 44: "closed-hat", 46: "open-hat",
    41: "tom", 43: "tom", 45: "tom", 47: "tom", 48: "tom", 50: "tom",
    49: "crash", 52: "crash", 55: "crash", 57: "crash",
    51: "ride", 53: "ride", 59: "ride",
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def normalize_role(role: str) -> str:
    r = role.lower().replace("_", "-").strip()
    aliases = {
        "guitar": "rhythm-guitar", "rhythm-guitar": "rhythm-guitar",
        "string": "strings", "string-section": "strings", "strings": "strings",
        "woodwinds": "woodwind", "wind": "woodwind", "saxophone": "sax",
        "keys": "piano", "keyboard": "piano",
        "power": "power-riff", "powerchord": "power-riff", "power-chord": "power-riff",
        "melody": "solo", "lead": "solo", "harmony": "accompaniment",
    }
    return aliases.get(r, r)


def behavior_profile(role: str) -> InstrumentBehaviorProfile:
    role = normalize_role(role)
    return PROFILES.get(role, InstrumentBehaviorProfile(
        role, "instrumentalist", ("meter", "section", "phrase"),
        ("preserve", "phrase-aware-variation"),
        ("preserve musical identity",),
        ("timing", "gate", "register", "phrase continuity"),
    ))


def classify_drum_element(pitch: int, *, velocity: int | None = None, local_peak: int | None = None) -> str:
    base = GM_DRUM_MAP.get(pitch, "percussion" if 54 <= pitch <= 82 else "other")
    if base == "snare" and velocity is not None and local_peak is not None and local_peak > 0:
        if velocity <= max(1, int(local_peak * 0.52)):
            return "ghost-snare"
    return base


def _window_notes(midi: MidiFile, track_index: int, channel: int, start_tick: int, end_tick: int) -> list[Note]:
    return sorted((n for n in midi.notes() if n.track == track_index and n.channel == channel and n.start < end_tick and n.end > start_tick), key=lambda n: (n.start, n.pitch, n.end))


def _nearest_distance(value: int, points: Sequence[int]) -> int | None:
    return min((abs(value - p) for p in points), default=None)


def analyze_drum_elements(notes: Sequence[Note], ppq: int, section: str = "generic") -> dict[str, Any]:
    by_pitch_peak: dict[int, int] = {}
    for n in notes:
        by_pitch_peak[n.pitch] = max(by_pitch_peak.get(n.pitch, 0), n.velocity)
    counts: Counter[str] = Counter()
    onset_by_element: dict[str, list[int]] = {}
    for n in notes:
        element = classify_drum_element(n.pitch, velocity=n.velocity, local_peak=by_pitch_peak.get(n.pitch))
        counts[element] += 1
        onset_by_element.setdefault(element, []).append(n.start)
    span_qn = max(1.0, ((max((n.end for n in notes), default=ppq) - min((n.start for n in notes), default=0)) / max(1, ppq)))
    metrics = {}
    for element, count in sorted(counts.items()):
        metrics[element] = {
            "count": count,
            "perQuarter": round(count / span_qn, 4),
            "behavior": asdict(DRUM_ELEMENTS.get(element, DRUM_ELEMENTS["other"])),
        }
    foundation = counts["kick"] + counts["snare"]
    decoration = sum(counts[e] for e in ("tom", "crash", "open-hat", "percussion", "ghost-snare"))
    return {
        "section": section,
        "elements": metrics,
        "foundationCount": foundation,
        "decorationCount": decoration,
        "foundationToDecoration": round(foundation / max(1, decoration), 4),
        "recommendations": [
            "preserve kick/snare hierarchy",
            "treat hats/ride as timekeepers rather than generic percussion",
            "reserve tom/crash density for phrase/transition evidence",
            "ghost snare is relative to local snare dynamics, not an absolute velocity",
            "percussion must interlock and remain subordinate to core groove",
        ],
    }


def drum_element_optimization_plan(notes: Sequence[Note], ppq: int, section: str = "generic") -> dict[str, Any]:
    """Per-element musician plan; advisory only, never remaps kit notes."""
    by_element: dict[str, list[Note]] = {}
    snare_peak = max((n.velocity for n in notes if GM_DRUM_MAP.get(n.pitch) == "snare"), default=0)
    for n in notes:
        e = classify_drum_element(n.pitch, velocity=n.velocity, local_peak=snare_peak)
        by_element.setdefault(e, []).append(n)
    transitionish = any(x in section.lower() for x in ("fill", "transition", "ending", "intro"))
    plans: dict[str, Any] = {}
    for element, group in sorted(by_element.items()):
        starts = sorted(n.start for n in group)
        iois = [b-a for a,b in zip(starts, starts[1:]) if b>a]
        behavior = DRUM_ELEMENTS.get(element, DRUM_ELEMENTS["other"])
        actions: list[str] = []
        if element == "kick":
            actions += ["prioritize pocket with bass", "preserve downbeat/structural accents"]
        elif element in {"snare", "side-stick", "clap"}:
            actions += ["preserve backbeat hierarchy", "separate main accents from ghost support"]
        elif element in {"closed-hat", "ride"}:
            actions += ["preserve subdivision continuity", "avoid random density spikes"]
        elif element == "open-hat":
            actions += ["use as lift/release accent", "avoid constant open-hat repetition"]
        elif element == "crash":
            actions += ["prefer section/phrase entries", "suppress redundant crashes inside stable groove"]
        elif element == "tom":
            actions += (["allow denser transition movement"] if transitionish else ["keep sparse outside fills"])
        elif element == "ghost-snare":
            actions += ["keep below main snare hierarchy", "place only in groove-support gaps"]
        elif element == "percussion":
            actions += ["interlock around core groove", "reduce clutter around solo/fill density"]
        else:
            actions += ["preserve unless role/evidence is known"]
        plans[element] = {
            "noteCount": len(group),
            "medianIoiQn": round(float(median(iois))/ppq, 4) if iois else None,
            "musicalJob": behavior.musical_job,
            "actions": actions,
            "hardRule": "DO_NOT_REMAP_PITCH_WITHOUT_CONFIRMED_KIT_EVIDENCE",
        }
    return {"schema":"dna-drum-element-optimization-plan","version":"1.0","section":section,"elements":plans}


def bass_function_plan(notes: Sequence[Note], ppq: int) -> dict[str, Any]:
    """Label bass notes as performance functions without altering pitch."""
    rows=[]
    ordered=sorted(notes,key=lambda n:(n.start,n.pitch))
    for i,n in enumerate(ordered):
        prev=ordered[i-1] if i else None
        nxt=ordered[i+1] if i+1<len(ordered) else None
        labels=[]
        if prev:
            iv=n.pitch-prev.pitch
            if abs(iv)==12: labels.append("OCTAVE")
            if 1<=abs(iv)<=2: labels.append("APPROACH_OR_PASSING")
        if nxt:
            gate=(n.end-n.start)/max(1,nxt.start-n.start)
            iv=nxt.pitch-n.pitch
            if abs(iv)>=7 and gate<0.45: labels.append("SLAP_POP_CANDIDATE")
            if 1<=abs(iv)<=5 and gate>=0.85: labels.append("SLIDE_LEGATO_CANDIDATE")
            if gate<0.35: labels.append("GHOST_MUTE_CANDIDATE")
        rows.append({"pitch":n.pitch,"start":n.start,"end":n.end,"functions":labels or ["FOUNDATION_OR_CHORD_FUNCTION"]})
    return {"schema":"dna-bass-function-plan","version":"1.0","notes":rows,"deviceTriggerPolicy":"SEMANTIC_ONLY_UNTIL_EXACT_SOUND_PROFILE"}


def solo_phrase_optimization_plan(notes: Sequence[Note], ppq: int) -> dict[str, Any]:
    """Deep solo phrase plan preserving melody while allocating ornament opportunities."""
    ordered=sorted(notes,key=lambda n:(n.start,n.pitch))
    if not ordered:
        return {"schema":"dna-solo-phrase-plan","version":"1.0","phrases":[]}
    break_ticks=max(ppq//2,1)
    phrases=[]; cur=[ordered[0]]
    for a,b in zip(ordered,ordered[1:]):
        if b.start-a.end>=break_ticks:
            phrases.append(cur); cur=[b]
        else: cur.append(b)
    phrases.append(cur)
    out=[]
    for idx,phrase in enumerate(phrases):
        duration=max(1,phrase[-1].end-phrase[0].start)
        ornament_budget=max(1,min(4,len(phrase)//4+1))
        opportunities=[]
        for a,b in zip(phrase,phrase[1:]):
            iv=abs(b.pitch-a.pitch); gap=max(0,b.start-a.end)
            if iv in (1,2) and a.end-a.start<=ppq//3: opportunities.append("GRACE_TRILL")
            if 1<=iv<=7 and gap<=ppq//16: opportunities.append("LEGATO_SLIDE")
            if a.pitch==b.pitch: opportunities.append("REPEAT_VARIATION")
        out.append({
            "phraseIndex":idx,"start":phrase[0].start,"end":phrase[-1].end,"noteCount":len(phrase),
            "durationQn":round(duration/ppq,4),"ornamentBudget":ornament_budget,
            "opportunities":dict(Counter(opportunities)),
            "priorities":["preserve main melody","repair continuity before adding ornament","use phrase-level ornament diversity","cadence/phrase-end gets special treatment"],
        })
    return {"schema":"dna-solo-phrase-plan","version":"1.0","phrases":out,"treatment":"DEEP"}


def analyze_bass_behavior(notes: Sequence[Note], ppq: int, kick_onsets: Sequence[int] = ()) -> dict[str, Any]:
    if not notes:
        return {"techniques": {}, "pocketScore": 0.0, "medianGateRatio": None}
    techniques: Counter[str] = Counter()
    gates = []
    for i, n in enumerate(notes):
        nxt = notes[i + 1] if i + 1 < len(notes) else None
        ioi = (nxt.start - n.start) if nxt and nxt.start > n.start else max(1, n.end - n.start)
        gate = (n.end - n.start) / max(1, ioi)
        gates.append(gate)
        if nxt:
            interval = nxt.pitch - n.pitch
            if abs(interval) == 12:
                techniques["octave"] += 1
            if 1 <= abs(interval) <= 2 and gate < 0.8:
                techniques["approach/passing"] += 1
            if abs(interval) >= 7 and gate < 0.45:
                techniques["slap-pop-candidate"] += 1
            if gate > 0.9 and abs(interval) <= 5:
                techniques["legato/slide-candidate"] += 1
        if gate < 0.35:
            techniques["ghost/mute-candidate"] += 1
    tolerance = max(1, ppq // 24)  # semantic pocket measurement only; no note movement.
    aligned = sum(1 for n in notes if (_nearest_distance(n.start, kick_onsets) or 10**9) <= tolerance)
    return {
        "techniques": dict(techniques),
        "pocketScore": round(aligned / max(1, len(notes)), 4) if kick_onsets else None,
        "medianGateRatio": round(float(median(gates)), 4),
        "rules": list(behavior_profile("bass").phrase_rules),
        "note": "SLAP/POP/SLIDE are semantic candidates only until exact SoundProfile confirms a device articulation mechanism.",
    }


def analyze_solo_behavior(notes: Sequence[Note], ppq: int) -> dict[str, Any]:
    if not notes:
        return {"phraseCandidates": 0, "techniques": {}, "continuity": {}}
    gaps = []
    intervals = []
    techniques: Counter[str] = Counter()
    phrase_break = max(ppq, int(ppq * 0.75))
    phrases = 1
    for a, b in zip(notes, notes[1:]):
        gap = max(0, b.start - a.end)
        gaps.append(gap)
        interval = b.pitch - a.pitch
        intervals.append(interval)
        if gap >= phrase_break:
            phrases += 1
        if abs(interval) <= 2 and gap <= ppq // 16:
            techniques["legato/neighbor"] += 1
        if abs(interval) in (1, 2) and (a.end - a.start) <= ppq // 4:
            techniques["grace/trill-context"] += 1
        if 3 <= abs(interval) <= 7 and gap <= ppq // 16:
            techniques["slide-candidate"] += 1
        if a.pitch == b.pitch:
            techniques["repeat-note"] += 1
    return {
        "phraseCandidates": phrases,
        "techniques": dict(techniques),
        "medianGapTicks": round(float(median(gaps)), 3) if gaps else 0.0,
        "medianAbsInterval": round(float(median([abs(x) for x in intervals])), 3) if intervals else 0.0,
        "priority": "DEEP_TREATMENT",
        "rules": list(behavior_profile("solo").phrase_rules),
    }


def analyze_role(
    midi: MidiFile, *, role: str, track_index: int, channel: int,
    start_tick: int = 0, end_tick: int | None = None, section: str = "generic",
) -> dict[str, Any]:
    role = normalize_role(role)
    end = end_tick if end_tick is not None else max((n.end for n in midi.notes()), default=midi.ppq)
    notes = _window_notes(midi, track_index, channel, start_tick, end)
    profile = behavior_profile(role)
    detail: dict[str, Any] = {}
    if role in {"drums", "percussion"}:
        detail = analyze_drum_elements(notes, midi.ppq, section)
        detail["optimizationPlan"] = drum_element_optimization_plan(notes, midi.ppq, section)
    elif role == "bass":
        kick_onsets = [n.start for n in midi.notes() if n.channel == 9 and classify_drum_element(n.pitch) == "kick"]
        detail = analyze_bass_behavior(notes, midi.ppq, kick_onsets)
        detail["functionPlan"] = bass_function_plan(notes, midi.ppq)
    elif role in {"solo", "terca", "echo", "accordion", "sax", "woodwind", "brass"}:
        detail = analyze_solo_behavior(notes, midi.ppq)
        if role == "solo":
            detail["optimizationPlan"] = solo_phrase_optimization_plan(notes, midi.ppq)
    articulation = analyze_articulation_context(
        midi, role=role, track_index=track_index, channel=channel,
        start_tick=start_tick, end_tick=end, notes=notes, section=section,
    )
    detail = dict(detail)
    detail["articulationContext"] = articulation
    detail["performanceGrammar"] = {
        "playerModel": performance_grammar_for(role).player_model,
        "phrasePlan4Bars": performance_phrase_plan(role, 4, transition_strength=0.65 if any(x in section.lower() for x in ("fill","transition","ending")) else 0.15, section_energy=0.7 if any(x in section.lower() for x in ("chorus","fill","transition")) else 0.5),
    }
    from .instrument_profile_matrix import profile_document as complete_profile_document
    return _jsonable({
        "schema": "dna-instrument-behavior-analysis",
        "version": "1.0",
        "role": role,
        "trackIndex": track_index,
        "channel": channel,
        "window": [start_tick, end],
        "noteCount": len(notes),
        "profile": asdict(profile),
        "completeInstrumentProfile": complete_profile_document(role),
        "detail": detail,
        "velocityAuthority": "FACTORY_ONLY",
        "deviceArticulationPolicy": "EXACT_SOUND_PROFILE_REQUIRED",
    })


def drum_pattern_behavior_score(events: Iterable[Any], *, section: str) -> float:
    """Soft musician score. Never a hard rejection."""
    counts = Counter(getattr(e, "element", "other") for e in events)
    score = 0.0
    if counts["kick"]:
        score += 1.2
    if counts["snare"]:
        score += 1.2
    if counts["hat"] or counts["closed-hat"] or counts["ride"]:
        score += 0.8
    transitionish = any(token in section.lower() for token in ("fill", "transition", "ending", "intro"))
    ornament = counts["tom"] + counts["fill"] + counts["cymbal"] + counts["crash"]
    if transitionish:
        score += min(1.0, ornament * 0.12)
    elif ornament > (counts["kick"] + counts["snare"] + 2):
        score -= min(1.0, (ornament - counts["kick"] - counts["snare"]) * 0.08)
    return score


def harmonic_pattern_behavior_score(pattern: Any, *, role: str, section: str) -> float:
    role = normalize_role(role)
    events = list(getattr(pattern, "events", ()))
    if not events:
        return 0.0
    score = 0.0
    roots = sum(getattr(e, "function", "") == "root" for e in events)
    approaches = sum(getattr(e, "approach", "none") != "none" for e in events)
    chords = sum(getattr(e, "function", "") == "chord" for e in events)
    if role == "bass":
        root_ratio = roots / len(events)
        score += 1.5 * min(1.0, root_ratio / 0.35)
        score += min(1.0, approaches * 0.2)
        # Octave/scale movement is allowed and can be energetic; no hard clamp.
        score += 0.25 if any(getattr(e, "degree", 1) in (1, 5) for e in events) else 0.0
    elif role == "power-riff":
        score += 1.0 if chords else 0.4
        score += 0.4 if roots else 0.0
        score += min(0.6, approaches * 0.15)
    else:
        score += min(0.8, (roots + chords) / max(1, len(events)))
    if any(x in section.lower() for x in ("chorus", "fill", "transition")):
        score += min(0.4, len(events) / 16.0)
    return score


def guitar_pattern_behavior_score(pattern: Any, *, section: str) -> float:
    strokes = list(getattr(pattern, "strokes", ()))
    if not strokes:
        return 0.0
    directions = [getattr(s, "direction", "") for s in strokes]
    score = 0.0
    if "down" in directions and "up" in directions:
        score += 1.0
    if any(d in {"mute", "stop"} for d in directions):
        score += 0.45
    # Repeated one-direction blocks can be legitimate; diversity is a soft preference only.
    score += min(0.6, len(set(directions)) * 0.18)
    if any(x in section.lower() for x in ("chorus", "transition", "fill")):
        score += 0.2
    return score


def profile_catalog() -> dict[str, Any]:
    return _jsonable({
        "schema": "dna-instrument-behavior-catalog",
        "version": "2.0",
        "profiles": {k: asdict(v) for k, v in PROFILES.items()},
        "drumElements": {k: asdict(v) for k, v in DRUM_ELEMENTS.items()},
        "hardBoundary": {
            "musicalBehaviorIsSoftEvidence": True,
            "deviceTriggersRequireExactSoundProfile": True,
            "velocityAuthority": "FACTORY_ONLY",
            "noHarmonyOrFormRewriteByBehaviorBrain": True,
        },
    })


def solo_ornament_behavior_score(kind: str, current: Note, following: Note, ppq: int) -> float:
    """Phrase-aware soft score for evidence-approved solo ornaments."""
    gap = max(0, following.start - current.end)
    interval = abs(following.pitch - current.pitch)
    cur_dur = current.end - current.start
    score = 0.0
    if kind == "slide":
        if 1 <= interval <= 7: score += 1.2
        if gap <= max(1, ppq // 8): score += 0.6
    elif kind == "grace":
        if 1 <= interval <= 5: score += 0.9
        if gap >= max(1, ppq // 12): score += 0.5
    elif kind == "trill":
        if cur_dur >= ppq // 2: score += 0.8
        if gap >= max(1, ppq // 8): score += 0.5
        if interval <= 4: score += 0.3
    return score
