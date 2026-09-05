"""Instrument Performance Grammar V2.

Phrase-state musician model used by DNA MIDI Studio 4.40.
This layer describes *how* an instrument behaves across ENTRY/BODY/BUILD/
TRANSITION/CADENCE. It emits semantic performance intents only. It never
invents Pa800 RX/DNC triggers and never owns final velocity.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable, Mapping, Sequence
from collections import Counter


PHRASE_STATES = ("ENTRY", "BODY", "BUILD", "TRANSITION", "CADENCE")


@dataclass(frozen=True)
class GrammarRule:
    state: str
    preferred: tuple[str, ...]
    avoid: tuple[str, ...]
    energy: float
    density_bias: float = 0.0
    gate_bias: float = 0.0
    register_bias: int = 0
    note: str = ""


@dataclass(frozen=True)
class InstrumentPerformanceGrammar:
    role: str
    player_model: str
    rules: tuple[GrammarRule, ...]
    anchors: tuple[str, ...]
    dependencies: tuple[str, ...]
    hard_boundaries: tuple[str, ...]

    def rule_for(self, state: str) -> GrammarRule:
        s = str(state or "BODY").upper()
        for rule in self.rules:
            if rule.state == s:
                return rule
        return next(r for r in self.rules if r.state == "BODY")


def _r(state: str, preferred: Sequence[str], avoid: Sequence[str]=(), *, energy: float=.5,
       density: float=0.0, gate: float=0.0, register: int=0, note: str="") -> GrammarRule:
    return GrammarRule(state, tuple(preferred), tuple(avoid), float(energy), float(density), float(gate), int(register), note)


GRAMMARS: dict[str, InstrumentPerformanceGrammar] = {
    "drums": InstrumentPerformanceGrammar("drums", "drummer", (
        _r("ENTRY", ("kick-anchor","snare-backbeat","hat-timekeeper","crash-entry"), ("tom-clutter",), energy=.55),
        _r("BODY", ("kick-pocket","snare-hierarchy","hat-subdivision","ghost-support"), ("random-fill","crash-repeat"), energy=.55),
        _r("BUILD", ("hat-lift","open-hat","kick-drive","snare-intensify"), ("full-stop",), energy=.72, density=.12),
        _r("TRANSITION", ("tom-fill","snare-fill","crash-target","kick-pickup"), ("unrelated-percussion-clutter",), energy=.86, density=.22),
        _r("CADENCE", ("crash-resolution","snare-punctuation","kick-resolution"), ("late-fill-after-resolution",), energy=.68, density=-.12),
    ), ("kick","snare"), ("meter","bass","section"), ("NO_KIT_REMAP_WITHOUT_EVIDENCE","FACTORY_VELOCITY_ONLY")),
    "percussion": InstrumentPerformanceGrammar("percussion", "percussionist", (
        _r("ENTRY", ("sparse-color",), ("kick-snare-mask",), energy=.35),
        _r("BODY", ("interlock","offbeat-color"), ("constant-density",), energy=.45),
        _r("BUILD", ("layered-interlock","pickup-color"), ("core-groove-mask",), energy=.6, density=.1),
        _r("TRANSITION", ("sparse-fill","pickup"), ("compete-with-toms",), energy=.65),
        _r("CADENCE", ("release-accent",), ("post-cadence-clutter",), energy=.35, density=-.2),
    ), ("interlock",), ("drums","lead-space"), ("NO_UNVERIFIED_PERCUSSION_REMAP",)),
    "bass": InstrumentPerformanceGrammar("bass", "bassist", (
        _r("ENTRY", ("root","fifth","pickup"), ("random-register-jump",), energy=.45, gate=.05),
        _r("BODY", ("root","fifth","third","passing","kick-pocket"), ("constant-octave-jump",), energy=.55),
        _r("BUILD", ("octave","approach","pickup","slap-candidate"), ("root-only-stasis",), energy=.75, density=.10, register=1),
        _r("TRANSITION", ("chromatic-approach","pickup","octave","slide-candidate"), ("late-target",), energy=.82, density=.12),
        _r("CADENCE", ("root-resolution","fifth-root","sustain-or-clean-stop"), ("unresolved-passing",), energy=.52, density=-.15, gate=.10),
    ), ("chord-root","kick-pocket"), ("kick","chord","next-chord"), ("NO_UNVERIFIED_SLAP_TRIGGER","FACTORY_VELOCITY_ONLY")),
    "rhythm-guitar": InstrumentPerformanceGrammar("rhythm-guitar", "rhythm guitarist", (
        _r("ENTRY", ("down","block","open-strum"), ("dense-mute-chain",), energy=.48),
        _r("BODY", ("down-up-flow","mute-contrast","chord-cleanliness"), ("piano-quantized-strum",), energy=.55),
        _r("BUILD", ("open-strum","denser-down-up","register-open"), ("constant-stop",), energy=.74, density=.08),
        _r("TRANSITION", ("pickup","stop","mute-hit","open-release"), ("random-rx-noise",), energy=.82),
        _r("CADENCE", ("final-down","sustain-chord","clean-stop"), ("extra-post-cadence-stroke",), energy=.58, density=-.12, gate=.12),
    ), ("chord-stroke",), ("chord","drums","Factory-strumming"), ("EXACT_SOUND_PROFILE_FOR_RX_DNC","FACTORY_VELOCITY_ONLY")),
    "power-riff": InstrumentPerformanceGrammar("power-riff", "power-chord guitarist", (
        _r("ENTRY", ("root-fifth","root-fifth-octave","strong-hit"), ("color-tone-clutter",), energy=.62),
        _r("BODY", ("root-fifth","mute-hit","sustain-hit","controlled-repeat"), ("random-voicing",), energy=.68),
        _r("BUILD", ("octave-lift","root-fifth-octave","pickup","denser-hit"), ("register-drop-without-context",), energy=.85, density=.12, register=1),
        _r("TRANSITION", ("pickup","mute-hit","register-lift","target-hit"), ("late-chord-change",), energy=.92, density=.10),
        _r("CADENCE", ("root-fifth-resolution","sustain-hit","clean-stop"), ("unresolved-color",), energy=.66, density=-.15, gate=.15),
    ), ("root-fifth",), ("chord","kick","section"), ("EXACT_SOUND_PROFILE_FOR_GUITAR_TRIGGER",)),
    "riff": InstrumentPerformanceGrammar("riff", "riff player", (
        _r("ENTRY", ("motif-entry","anchor-hit"), ("premature-variation",), energy=.52),
        _r("BODY", ("motif-repeat","controlled-variation","syncopated-answer"), ("identity-loss",), energy=.6),
        _r("BUILD", ("motif-lift","register-lift","ending-variation"), ("new-unrelated-motif",), energy=.76, register=1),
        _r("TRANSITION", ("pickup","motif-turnaround","target-answer"), ("late-target",), energy=.82),
        _r("CADENCE", ("motif-resolution","clean-stop","sustain-anchor"), ("new-motif-after-cadence",), energy=.54, density=-.12),
    ), ("motif-identity",), ("chord","groove","phrase"), ("PRESERVE_MOTIF_IDENTITY",)),
    "accompaniment": InstrumentPerformanceGrammar("accompaniment", "arranger/comping player", (
        _r("ENTRY", ("clean-voicing","soft-stab","common-tone"), ("lead-mask",), energy=.4),
        _r("BODY", ("voice-leading","stab","sustain","answer","rest"), ("constant-block-chords",), energy=.48),
        _r("BUILD", ("wider-voicing","answer-lift","controlled-density"), ("bass-register-mask",), energy=.66, register=1),
        _r("TRANSITION", ("target-voicing","pickup-stab","space-before-target"), ("cross-section-smear",), energy=.72),
        _r("CADENCE", ("resolution-voicing","long-release","rest"), ("post-cadence-fill",), energy=.42, density=-.16),
    ), ("chord-support","lead-space"), ("chord","lead-space","section"), ("PRESERVE_HARMONY","DO_NOT_OVERRANK_LEAD",)),
    "pad": InstrumentPerformanceGrammar("pad", "pad player", (
        _r("ENTRY", ("soft-entry","common-tone","slow-attack"), ("hard-reattack",), energy=.3, gate=.25),
        _r("BODY", ("sustain","common-tone","slow-inversion"), ("busy-inner-motion",), energy=.36, gate=.3),
        _r("BUILD", ("swell-candidate","register-open","voice-leading"), ("bass-register-mask",), energy=.58, register=1),
        _r("TRANSITION", ("swell-to-target","common-tone-shift","release-space"), ("abrupt-retrigger",), energy=.62),
        _r("CADENCE", ("long-release","common-tone-resolution"), ("new-attack-after-resolution",), energy=.34, density=-.2, gate=.3),
    ), ("sustain","common-tone"), ("chord","section","lead-space"), ("NO_UNVERIFIED_PAD_TRIGGER","PRESERVE_HARMONY")),
    "strings": InstrumentPerformanceGrammar("strings", "string-section arranger", (
        _r("ENTRY", ("common-tone","soft-attack","sustain"), ("hard-reattack",), energy=.38, gate=.18),
        _r("BODY", ("legato","common-tone","slow-inversion"), ("busy-inner-motion",), energy=.45, gate=.2),
        _r("BUILD", ("swell-candidate","register-open","voice-leading"), ("bass-register-collision",), energy=.72, register=1),
        _r("TRANSITION", ("voice-lead-target","swell-release","bow-change-candidate"), ("abrupt-cut",), energy=.78),
        _r("CADENCE", ("common-tone-resolution","long-release"), ("unnecessary-reattack",), energy=.48, density=-.2, gate=.25),
    ), ("common-tone","voice-leading"), ("chord","lead-space"), ("EXACT_SOUND_PROFILE_FOR_PIZZICATO_OR_NOISE",)),
    "brass": InstrumentPerformanceGrammar("brass", "brass arranger", (
        _r("ENTRY", ("stab","unison-hit","octave-hit"), ("continuous-block",), energy=.62),
        _r("BODY", ("answer","stab","sustain-with-space"), ("endless-sustain",), energy=.58),
        _r("BUILD", ("rising-answer","octave-hit","strong-stab"), ("dense-every-beat",), energy=.8, density=.05, register=1),
        _r("TRANSITION", ("accent-hit","answer","fall-candidate"), ("fall-every-note",), energy=.88),
        _r("CADENCE", ("fall-candidate","unison-resolution","short-release"), ("new-motif-after-cadence",), energy=.65, density=-.15),
    ), ("attack-space",), ("chord","lead","section"), ("EXACT_SOUND_PROFILE_FOR_FALL_GROWL",)),
    "woodwind": InstrumentPerformanceGrammar("woodwind", "wind player", (
        _r("ENTRY", ("breath-entry","legato","grace"), ("hard-machine-attack",), energy=.42),
        _r("BODY", ("legato","tongue-candidate","breath-space"), ("endless-no-breath",), energy=.5),
        _r("BUILD", ("ascending-line","grace","expression-swell"), ("random-jumps",), energy=.68, register=1),
        _r("TRANSITION", ("approach","slide-candidate","breath-break"), ("overfilled-ornament",), energy=.72),
        _r("CADENCE", ("breath-release","grace-resolution","short-fall-candidate"), ("late-ornament-chain",), energy=.46),
    ), ("breath-phrase",), ("melody","chord","breath-space"), ("EXACT_SOUND_PROFILE_FOR_BREATH_TRIGGER",)),
    "sax": InstrumentPerformanceGrammar("sax", "saxophonist", (
        _r("ENTRY", ("tongue-candidate","legato-entry","breath-entry"), ("growl-every-note",), energy=.5),
        _r("BODY", ("legato","bend","repeat-variation","breath-space"), ("mechanical-gate",), energy=.58),
        _r("BUILD", ("bend","growl-candidate","upper-register-lift","expression-swell"), ("constant-fall",), energy=.8, register=1),
        _r("TRANSITION", ("bend-to-target","fall-candidate","pickup"), ("random-breath-noise",), energy=.88),
        _r("CADENCE", ("fall-candidate","bend-release","breath-release"), ("post-cadence-run",), energy=.58, density=-.12),
    ), ("main-melody",), ("melody","chord","phrase"), ("EXACT_SOUND_PROFILE_FOR_GROWL_BREATH",)),
    "accordion": InstrumentPerformanceGrammar("accordion", "accordionist", (
        _r("ENTRY", ("grace","legato","bellows-entry"), ("machine-gun-repeat",), energy=.48),
        _r("BODY", ("legato","turn","repeat-note","bellows-expression"), ("constant-trill",), energy=.58),
        _r("BUILD", ("trill","turn","octave-lift","bellows-swell"), ("ornament-every-note",), energy=.78, density=.08),
        _r("TRANSITION", ("grace-to-target","turnaround","trill-release"), ("late-ornament",), energy=.82),
        _r("CADENCE", ("turn","grace-resolution","bellows-release"), ("new-run-after-resolution",), energy=.54, density=-.1),
    ), ("melody-anchor",), ("melody","chord","phrase"), ("EXACT_SOUND_PROFILE_FOR_DNC_CONTROLLER",)),
    "piano": InstrumentPerformanceGrammar("piano", "pianist", (
        _r("ENTRY", ("clean-attack","voicing","pedal-context"), ("pedal-smear",), energy=.5),
        _r("BODY", ("voice-leading","arpeggio","pedal-preserve"), ("artificial-overlap",), energy=.55),
        _r("BUILD", ("wider-voicing","arpeggio-lift","denser-inner-motion"), ("bass-collision",), energy=.72, density=.08, register=1),
        _r("TRANSITION", ("pickup-arpeggio","dominant-motion","pedal-change"), ("pedal-through-harmony-break",), energy=.78),
        _r("CADENCE", ("resolution-voicing","pedal-release","long-tone"), ("extra-fill-after-resolution",), energy=.48, density=-.15),
    ), ("voicing","damper-semantics"), ("harmony","damper","lead-space"), ("PRESERVE_CC64_SEMANTICS","EXACT_SOUND_PROFILE_FOR_RX_PEDAL_NOISE")),
    "organ": InstrumentPerformanceGrammar("organ", "organist", (
        _r("ENTRY", ("sustain","clean-chord"), ("piano-like-decay-shaping",), energy=.5),
        _r("BODY", ("sustain","voice-leading","staccato-accent"), ("excess-retrigger",), energy=.55, gate=.2),
        _r("BUILD", ("register-lift","denser-chord","gliss-candidate"), ("random-manual-change",), energy=.72, register=1),
        _r("TRANSITION", ("gliss-candidate","staccato-accent","target-sustain"), ("abrupt-gap",), energy=.8),
        _r("CADENCE", ("long-sustain","clean-release"), ("late-retrigger",), energy=.52, gate=.25),
    ), ("sustain",), ("chord","section"), ("EXACT_SOUND_PROFILE_FOR_MANUAL_OR_EFFECT_TRIGGER",)),
    "choir": InstrumentPerformanceGrammar("choir", "choir arranger", (
        _r("ENTRY", ("soft-entry","common-tone"), ("hard-stab",), energy=.35, gate=.2),
        _r("BODY", ("sustain","voice-leading","vowel-continuity"), ("rapid-retrigger",), energy=.42, gate=.25),
        _r("BUILD", ("swell","register-open","denser-voicing"), ("low-register-mask",), energy=.68, register=1),
        _r("TRANSITION", ("swell-to-target","voice-lead"), ("percussive-cut",), energy=.7),
        _r("CADENCE", ("long-release","common-tone-resolution"), ("new-entry-after-resolution",), energy=.4, density=-.18),
    ), ("sustain","common-tone"), ("chord","lead-space"), ("NO_UNVERIFIED_VOWEL_TRIGGER",)),
    "solo": InstrumentPerformanceGrammar("solo", "lead instrumentalist", (
        _r("ENTRY", ("pickup","grace","clean-anchor"), ("ornament-stack",), energy=.48),
        _r("BODY", ("legato","neighbor","passing","repeat-variation"), ("ornament-every-note",), energy=.58),
        _r("BUILD", ("trill","slide-candidate","register-lift","expression-swell"), ("melody-rewrite",), energy=.8, register=1),
        _r("TRANSITION", ("pickup","turnaround","slide-to-target","cadential-approach"), ("late-target",), energy=.86),
        _r("CADENCE", ("cadence","trill-release","grace-resolution","long-tone"), ("post-cadence-run",), energy=.55, density=-.12, gate=.12),
    ), ("main-melody","phrase-anchor"), ("chord","phrase","section"), ("PRESERVE_MAIN_MELODY","EXACT_SOUND_PROFILE_FOR_DEVICE_ARTICULATION")),
    "terca": InstrumentPerformanceGrammar("terca", "harmony singer/player", (
        _r("ENTRY", ("follow-solo-anchor",), ("lead-overpower",), energy=.35),
        _r("BODY", ("harmonic-support","parallel-or-contextual-third"), ("independent-melody",), energy=.4),
        _r("BUILD", ("support-lift",), ("lead-register-collision",), energy=.52),
        _r("TRANSITION", ("resolve-with-solo",), ("late-entry",), energy=.5),
        _r("CADENCE", ("cadence-with-solo","release-first-or-with-lead"), ("solo-after-lead-release",), energy=.38),
    ), ("solo-relation",), ("solo","harmony"), ("NEVER_OUTRANK_MAIN_SOLO","HARMONY_VALIDATION_REQUIRED")),
    "echo": InstrumentPerformanceGrammar("echo", "echo/delay accompanist", (
        _r("ENTRY", ("delayed-answer",), ("simultaneous-double",), energy=.25),
        _r("BODY", ("sparse-answer","phrase-tail"), ("recursive-echo",), energy=.3),
        _r("BUILD", ("slightly-denser-answer",), ("lead-mask",), energy=.38),
        _r("TRANSITION", ("phrase-tail","stop-before-next-lead"), ("cross-section-tail",), energy=.34),
        _r("CADENCE", ("single-tail","fade-release"), ("recursive-repeat",), energy=.22, density=-.2),
    ), ("solo-source",), ("solo","phrase"), ("NON_RECURSIVE","NEVER_OUTRANK_MAIN_SOLO")),
}


def normalize_role(role: str) -> str:
    s = str(role or "").strip().lower().replace("_", "-")
    aliases = {"rhythm-guitar":"rhythm-guitar","guitar":"rhythm-guitar","power":"power-riff","power-riff":"power-riff","third":"terca","strings/pad":"strings"}
    return aliases.get(s, s)


def grammar_for(role: str) -> InstrumentPerformanceGrammar:
    role = normalize_role(role)
    return GRAMMARS.get(role, InstrumentPerformanceGrammar(role, "instrumentalist", (
        _r("ENTRY", ("clean-entry",), energy=.45), _r("BODY", ("phrase-aware-playing",), energy=.5),
        _r("BUILD", ("controlled-lift",), energy=.65), _r("TRANSITION", ("target-aware-transition",), energy=.7),
        _r("CADENCE", ("clean-resolution",), energy=.45)), ("musical-identity",), ("phrase","section"), ("PRESERVE_IDENTITY",)))


def phrase_state(phrase_position: float, transition_strength: float=0.0, cadence_strength: float=0.0) -> str:
    p = min(1.0, max(0.0, float(phrase_position)))
    if transition_strength >= .55:
        return "TRANSITION"
    if cadence_strength >= .55 or p >= .9:
        return "CADENCE"
    if p >= .78:
        return "TRANSITION"
    if p <= .12:
        return "ENTRY"
    if p >= .58:
        return "BUILD"
    return "BODY"


def performance_intent(role: str, *, phrase_position: float, transition_strength: float=0.0,
                       cadence_strength: float=0.0, section_energy: float=.5) -> dict[str, Any]:
    g = grammar_for(role)
    state = phrase_state(phrase_position, transition_strength, cadence_strength)
    rule = g.rule_for(state)
    e = min(1.0, max(0.0, (.65 * rule.energy) + (.35 * float(section_energy))))
    return {
        "schema": "dna-instrument-performance-intent",
        "version": "2.0",
        "role": g.role,
        "playerModel": g.player_model,
        "state": state,
        "preferred": list(rule.preferred),
        "avoid": list(rule.avoid),
        "energy": round(e, 4),
        "densityBias": rule.density_bias,
        "gateBias": rule.gate_bias,
        "registerBiasOctaves": rule.register_bias,
        "anchors": list(g.anchors),
        "dependencies": list(g.dependencies),
        "hardBoundaries": list(g.hard_boundaries),
        "deviceArticulationPolicy": "SEMANTIC_ONLY_UNTIL_EXACT_SOUND_PROFILE",
        "velocityAuthority": "FACTORY_ONLY",
    }


def score_semantic_functions(role: str, functions: Iterable[str], *, phrase_position: float,
                             transition_strength: float=0.0, cadence_strength: float=0.0,
                             section_energy: float=.5) -> dict[str, Any]:
    intent = performance_intent(role, phrase_position=phrase_position, transition_strength=transition_strength,
                                cadence_strength=cadence_strength, section_energy=section_energy)
    funcs = [str(x).lower().replace("_", "-") for x in functions]
    preferred = [x.lower() for x in intent["preferred"]]
    avoid = [x.lower() for x in intent["avoid"]]
    hits = sum(any(p in f or f in p for p in preferred) for f in funcs)
    bad = sum(any(a in f or f in a for a in avoid) for f in funcs)
    diversity = len(set(funcs)) / max(1, len(funcs))
    raw = .45 + min(.35, hits * .08) - min(.3, bad * .12) + min(.12, diversity * .12)
    score = max(0.0, min(1.0, raw))
    return {"score": round(score, 4), "preferredHits": hits, "avoidHits": bad, "intent": intent}


def phrase_plan(role: str, bars: int=4, *, transition_strength: float=.0, section_energy: float=.5) -> list[dict[str, Any]]:
    bars = max(1, int(bars))
    out=[]
    for i in range(bars):
        pos = 0.0 if bars == 1 else i/(bars-1)
        local_transition = transition_strength if i == bars-1 else min(.35, transition_strength*.35)
        out.append(performance_intent(role, phrase_position=pos, transition_strength=local_transition,
                                      cadence_strength=.65 if i==bars-1 and transition_strength < .4 else 0.0,
                                      section_energy=section_energy))
    return out


def catalog() -> dict[str, Any]:
    return {"schema":"dna-instrument-performance-grammar-catalog","version":"2.0","roles":{
        role:{"role":g.role,"playerModel":g.player_model,"anchors":list(g.anchors),"dependencies":list(g.dependencies),
              "hardBoundaries":list(g.hard_boundaries),"states":[asdict(r) for r in g.rules]}
        for role,g in sorted(GRAMMARS.items())},"velocityAuthority":"FACTORY_ONLY",
        "deviceArticulationPolicy":"EXACT_SOUND_PROFILE_REQUIRED"}
