"""Complete instrument profile matrix for DNA MIDI Studio 4.44.

This is the single musician-facing profile contract.  It consolidates behavior,
phrase grammar, interaction, repair and generation policies for every canonical
instrument role.  Device-specific RX/DNC/OSC mappings are references only and
remain UNKNOWN unless exact SoundBinding evidence confirms them.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Mapping

from .instrument_behavior import behavior_profile, normalize_role
from .instrument_performance_grammar import grammar_for, phrase_plan


@dataclass(frozen=True)
class CompleteInstrumentProfile:
    role: str
    player_model: str
    register_policy: tuple[str, ...]
    gate_policy: tuple[str, ...]
    density_policy: tuple[str, ...]
    controller_policy: tuple[str, ...]
    articulation_policy: tuple[str, ...]
    interaction_policy: tuple[str, ...]
    repair_policy: tuple[str, ...]
    generation_policy: tuple[str, ...]
    hard_rules: tuple[str, ...]
    velocity_authority: str = "FACTORY_ONLY"
    device_articulation_authority: str = "EXACT_SOUND_PROFILE_REQUIRED"


COMMON_HARD = (
    "PRESERVE_TEMPO_METER_FORM",
    "PRESERVE_PROTECTED_EVENTS",
    "NO_UNVERIFIED_RX_DNC_TRIGGER",
    "NO_UNVERIFIED_BANK_PROGRAM_CHANGE",
    "FACTORY_ONLY_FINAL_VELOCITY",
)

# Musical policies are intentionally descriptive/soft. Only hard_rules are invariant gates.
POLICIES: dict[str, dict[str, tuple[str, ...]]] = {
    "drums": {
        "register": ("pitch-is-kit-element-identity-not-melodic-register", "never-octave-repair-drum-note"),
        "gate": ("short-hit-default", "preserve-open-hat-cymbal-tail", "avoid-zero-duration"),
        "density": ("kick-snare-foundation-first", "hats-timekeeper", "toms-crashes-transition-weighted", "percussion-subordinate"),
        "controllers": ("preserve-kit-sensitive-controllers",),
        "articulation": ("ghost-relative-to-local-snare", "open-closed-hat-state", "crash-entry", "tom-fill"),
        "interaction": ("kick-locks-with-bass", "snare-defines-backbeat", "leave-space-around-solo-and-fills"),
        "repair": ("repair-per-element", "remove-implausible-clutter", "preserve-core-groove", "never-generic-pitch-remap"),
        "generation": ("generate-kit-elements-by-musical-job", "fill-grows-from-existing-groove", "transition-aware-crash-tom"),
        "hard": ("NO_DRUM_OCTAVE_REPAIR", "NO_KIT_REMAP_WITHOUT_CONFIRMED_EVIDENCE"),
    },
    "percussion": {
        "register": ("pitch-is-element-identity", "no-melodic-octave-repair"),
        "gate": ("short-to-medium-hit", "preserve-natural-ring-where-evidenced"),
        "density": ("interlock-not-wall", "reduce-redundant-conga-shaker-clutter", "withdraw-during-dense-fill-or-solo"),
        "controllers": ("preserve-existing-controller-intent",),
        "articulation": ("offbeat-color", "pickup", "sparse-fill"),
        "interaction": ("never-compete-with-kick-snare", "complement-drum-grid"),
        "repair": ("thin-only-redundant-events", "preserve-idiomatic-pattern"),
        "generation": ("generate-sparse-interlock-from-meter-and-groove",),
        "hard": ("NO_UNVERIFIED_PERCUSSION_REMAP",),
    },
    "bass": {
        "register": ("stable-low-register", "octave-jumps-are-musical-functions", "avoid-random-register-jumps"),
        "gate": ("pocket-dependent", "short-for-ghost-slap-candidate", "longer-for-legato-slide-candidate", "continuity-aware"),
        "density": ("root-foundation", "approach-and-passing-contextual", "build-can-increase-octaves-and-pickups"),
        "controllers": ("preserve-pitch-bend", "preserve-expression-if-musically-coherent"),
        "articulation": ("root", "third", "fifth", "octave", "passing", "chromatic-approach", "pickup", "ghost-candidate", "slap-pop-candidate", "slide-legato-candidate"),
        "interaction": ("lock-with-kick", "land-chord-changes", "avoid-low-register-conflict"),
        "repair": ("repair-pocket-before-regeneration", "repair-gate-and-continuity", "replace-only-with-strong-role-evidence"),
        "generation": ("chord-relative-functions", "voice-leading-to-next-root", "phrase-aware-pickups", "controlled-octave-energy"),
        "hard": ("NO_UNVERIFIED_SLAP_POP_TRIGGER", "NO_UNVERIFIED_DNC_SLIDE_TRIGGER"),
    },
    "rhythm-guitar": {
        "register": ("guitar-voicing-register", "avoid-bass-collision", "register-open-in-higher-energy"),
        "gate": ("short-gate-can-be-intentional", "mute-stop-vs-open-sustain-contrast", "never-global-legato"),
        "density": ("stroke-pattern-dependent", "leave-space-for-power-riff-and-solo", "transition-pickup-allowed"),
        "controllers": ("preserve-pitch-bend-and-dnc-sensitive-controllers",),
        "articulation": ("down", "up", "block", "mute-candidate", "palm-mute-candidate", "stop", "pickup", "open-strum", "single-string-candidate", "fret-noise-candidate"),
        "interaction": ("lock-with-drums", "avoid-power-riff-duplication", "support-chord-changes"),
        "repair": ("Factory-strumming-first", "repair-stroke-flow-and-chord-cleanliness", "do-not-piano-quantize-strum"),
        "generation": ("Factory-evidence-strum-skeleton", "down-up-flow", "mute-open-contrast", "phrase-state-variation"),
        "hard": ("FACTORY_STRUMMING_AUTHORITY", "EXACT_SOUND_PROFILE_FOR_RX_FRET_NOISE"),
    },
    "power-riff": {
        "register": ("power-voicing-register", "controlled-octave-lift", "avoid-random-drop"),
        "gate": ("mute-hit-vs-sustain-hit", "transition-clean-stop-or-target-sustain"),
        "density": ("controlled-repeat", "build-can-intensify", "cadence-reduces-clutter"),
        "controllers": ("preserve-existing-bend",),
        "articulation": ("root-fifth", "root-fifth-octave", "octave", "mute-hit", "sustain-hit", "pickup", "register-lift"),
        "interaction": ("lock-with-kick", "avoid-rhythm-guitar-mask", "target-chord-change"),
        "repair": ("preserve-recognizable-riff", "repair-chord-relative-voicing", "repair-transition-targeting"),
        "generation": ("attack-pattern", "chord-relative-voicing", "voice-leading", "phrase-development", "transition-pickup"),
        "hard": ("NO_UNVERIFIED_GUITAR_TRIGGER",),
    },
    "riff": {
        "register": ("preserve-motif-register-identity", "controlled-lift-at-build"),
        "gate": ("motif-specific", "preserve-recognizable-articulation"),
        "density": ("repeat-with-ending-variation", "avoid-unrelated-extra-notes"),
        "controllers": ("preserve-motif-expression-and-bend",),
        "articulation": ("motif", "repeat", "variation", "pickup", "answer", "turnaround"),
        "interaction": ("fit-groove", "avoid-lead-mask", "answer-not-collide-with-solo"),
        "repair": ("repair-motif-continuity", "vary-endings-before-anchors", "replace-only-when-motif-broken"),
        "generation": ("motif-preserving-variation", "phrase-state-ending-variation"),
        "hard": ("PRESERVE_MOTIF_IDENTITY",),
    },
    "accompaniment": {
        "register": ("middle-register-support", "avoid-bass-and-lead-collision"),
        "gate": ("stab-or-sustain-by-role", "common-tone-continuity"),
        "density": ("support-not-dominate", "rest-is-valid", "build-can-widen-not-wall"),
        "controllers": ("preserve-expression-curves",),
        "articulation": ("stab", "sustain", "voice-lead", "answer", "rest"),
        "interaction": ("leave-lead-space", "complement-rhythm-guitar", "avoid-pad-string-duplication"),
        "repair": ("voice-leading-first", "reduce-register-collision", "repair-chord-cleanliness"),
        "generation": ("chord-relative-voicing", "common-tone-retention", "call-response-space"),
        "hard": ("PRESERVE_HARMONY", "DO_NOT_OVERRANK_LEAD"),
    },
    "pad": {
        "register": ("mid-high-support", "avoid-bass-register", "open-around-lead"),
        "gate": ("long-sustain", "minimal-reattack", "slow-release"),
        "density": ("low-event-density", "harmonic-density-not-onset-density"),
        "controllers": ("preserve-expression-swell",),
        "articulation": ("sustain", "common-tone", "slow-inversion", "swell-candidate"),
        "interaction": ("leave-lead-space", "avoid-strings-duplication", "support-section-energy"),
        "repair": ("continuity-first", "remove-unnecessary-reattack", "voice-lead-common-tones"),
        "generation": ("long-harmonic-bed", "common-tone-voice-leading", "section-swell"),
        "hard": ("PRESERVE_HARMONY", "NO_UNVERIFIED_PAD_TRIGGER"),
    },
    "strings": {
        "register": ("section-voicing-register", "open-around-lead", "avoid-bass-mask"),
        "gate": ("legato-sustain-default", "bow-change-is-contextual", "pizzicato-only-if-evidenced"),
        "density": ("voice-leading-over-reattack", "build-by-voicing-and-swell"),
        "controllers": ("preserve-expression-swell", "preserve-modulation-if-role-relevant"),
        "articulation": ("legato", "sustain", "common-tone", "bow-change-candidate", "pizzicato-candidate", "swell-candidate"),
        "interaction": ("avoid-lead-mask", "coordinate-with-pad-and-brass", "common-tone-across-changes"),
        "repair": ("repair-continuity", "reduce-unnecessary-reattack", "repair-register-collision"),
        "generation": ("voice-led-section-lines", "common-tone-retention", "build-release-contour"),
        "hard": ("EXACT_SOUND_PROFILE_FOR_PIZZICATO_OR_BOW_NOISE",),
    },
    "brass": {
        "register": ("idiomatic-section-register", "octave-hit-for-energy", "avoid-lead-mask"),
        "gate": ("short-stab-or-breathed-sustain", "space-between-phrases"),
        "density": ("accent-and-answer-not-wall", "transition-cadence-can-intensify"),
        "controllers": ("preserve-expression", "preserve-bend-if-present"),
        "articulation": ("stab", "unison-hit", "octave-hit", "answer", "sustain", "fall-candidate", "growl-candidate"),
        "interaction": ("call-response-with-lead", "coordinate-with-strings", "avoid-continuous-block-chords"),
        "repair": ("repair-attack-placement", "restore-breathing-space", "repair-answer-logic"),
        "generation": ("phrase-point-stabs", "answers", "build-octave-hits", "cadential-fall-semantic"),
        "hard": ("EXACT_SOUND_PROFILE_FOR_FALL_GROWL",),
    },
    "woodwind": {
        "register": ("instrument-appropriate-melodic-register", "avoid-impossible-random-octave-shifts"),
        "gate": ("breath-phrase-legato", "tongue-articulation-contextual", "avoid-endless-sustain"),
        "density": ("melodic-phrase-density", "space-for-breath"),
        "controllers": ("preserve-expression", "preserve-pitch-bend", "preserve-breath-related-controls-if-evidenced"),
        "articulation": ("legato", "tongue-candidate", "grace", "slide-candidate", "breath-noise-candidate"),
        "interaction": ("avoid-solo-collision", "answer-in-phrase-space"),
        "repair": ("repair-phrase-segmentation", "repair-gate", "preserve-controller-curves"),
        "generation": ("breath-length-phrases", "entry-exit-ornament", "target-aware-approach"),
        "hard": ("EXACT_SOUND_PROFILE_FOR_BREATH_DNC_TRIGGER",),
    },
    "sax": {
        "register": ("lead-register-with-controlled-lift", "preserve-melodic-identity"),
        "gate": ("breath-length-phrases", "legato-and-tongue-contrast"),
        "density": ("lead-phrase-density", "cadence-space"),
        "controllers": ("preserve-pitch-bend", "preserve-expression", "preserve-aftertouch-if-articulation-sensitive"),
        "articulation": ("legato", "tongue-candidate", "bend", "fall-candidate", "growl-candidate", "breath-noise-candidate"),
        "interaction": ("main-lead-priority", "avoid-brass-or-terca-mask"),
        "repair": ("continuity-before-ornament", "preserve-bends", "repair-breath-space"),
        "generation": ("phrase-contour", "repeat-variation", "build-bend", "cadential-fall-semantic"),
        "hard": ("PRESERVE_MAIN_MELODY", "EXACT_SOUND_PROFILE_FOR_GROWL_BREATH"),
    },
    "accordion": {
        "register": ("melodic-register-with-octave-lifts", "avoid-machine-register-jumps"),
        "gate": ("legato-bellows-flow", "repeat-notes-articulated-not-chopped"),
        "density": ("ornaments-near-important-notes", "not-every-note"),
        "controllers": ("preserve-expression-bellows-like-contour",),
        "articulation": ("legato", "grace", "turn", "trill", "repeat-note", "bellows-expression-candidate"),
        "interaction": ("lead-or-answer-role", "avoid-solo-competition-when-accompaniment"),
        "repair": ("repair-legato-and-phrase", "restore-ornament-diversity", "avoid-machine-gun-gate"),
        "generation": ("grace-turn-trill-phrase-aware", "bellows-contour", "cadential-resolution"),
        "hard": ("EXACT_SOUND_PROFILE_FOR_DNC_CONTROLLER",),
    },
    "piano": {
        "register": ("voicing-aware-full-range", "protect-bass-space-when-bass-present"),
        "gate": ("pedal-aware", "avoid-artificial-overlap", "staccato-is-contextual"),
        "density": ("voicing-and-arpeggio-dependent", "avoid-inner-voice-clutter"),
        "controllers": ("PRESERVE_CC64_DAMPER", "preserve-expression-if-present"),
        "articulation": ("legato", "staccato", "pedal", "arpeggio", "grace", "pedal-resonance-candidate"),
        "interaction": ("avoid-bass-collision", "leave-lead-space", "support-harmony"),
        "repair": ("pedal-semantics-first", "voice-leading-inner-voices", "repair-smear-at-harmony-change"),
        "generation": ("voicing", "arpeggio", "pedal-change-at-harmony-boundary"),
        "hard": ("PRESERVE_CC64_SEMANTICS", "EXACT_SOUND_PROFILE_FOR_RX_PEDAL_NOISE"),
    },
    "organ": {
        "register": ("manual-like-register", "keep-bass-clear-if-bass-track-present"),
        "gate": ("sustain-is-normal", "staccato-accent-contextual", "no-piano-decay-assumption"),
        "density": ("chord-density-section-dependent", "avoid-excess-retrigger"),
        "controllers": ("preserve-expression-modulation",),
        "articulation": ("sustain", "manual-change-candidate", "staccato-accent", "gliss-candidate"),
        "interaction": ("harmonic-bed-or-accent", "leave-lead-space"),
        "repair": ("repair-gate-continuity", "repair-voicing", "remove-piano-like-artifacts"),
        "generation": ("sustain-voicing", "build-register-lift", "transition-gliss-semantic"),
        "hard": ("EXACT_SOUND_PROFILE_FOR_MANUAL_OR_EFFECT_TRIGGER",),
    },
    "choir": {
        "register": ("voice-like-section-register", "keep-below-lead-prominence"),
        "gate": ("long-sustain", "smooth-release", "minimal-reattack"),
        "density": ("voicing-density-not-rhythmic-density",),
        "controllers": ("preserve-expression-swell",),
        "articulation": ("sustain", "common-tone", "swell-candidate", "release-fade-candidate"),
        "interaction": ("avoid-lead-mask", "coordinate-with-pad-and-strings"),
        "repair": ("continuity", "voice-leading", "reduce-hard-reattacks"),
        "generation": ("soft-entry", "common-tone-sustain", "build-swell", "long-cadence-release"),
        "hard": ("NO_UNVERIFIED_VOWEL_TRIGGER",),
    },
    "solo": {
        "register": ("melody-led-not-globally-clamped", "octave-motion-allowed-when-phrase-supported"),
        "gate": ("continuity-first", "legato-default-with-articulation-exceptions", "cadential-long-tone-allowed"),
        "density": ("preserve-main-melody", "ornament-budget-per-phrase", "no-ornament-every-note"),
        "controllers": ("preserve-pitch-bend", "preserve-expression", "preserve-aftertouch-and-modulation-if-musical"),
        "articulation": ("chord-tone", "passing", "neighbor", "approach", "grace", "trill", "slide-candidate", "turnaround", "repeat-variation", "cadence"),
        "interaction": ("highest-lead-priority", "terca-and-echo-follow", "other-roles-leave-space"),
        "repair": ("never-generic-replace-nonempty-solo", "repair-continuity-first", "pitch-bend-preservation", "ornament-diversity", "cadence-treatment"),
        "generation": ("melody-preserving-variation", "phrase-state-ornaments", "target-aware-turnaround"),
        "hard": ("PRESERVE_MAIN_MELODY", "NONEMPTY_SOLO_NO_GENERIC_REPLACE", "EXACT_SOUND_PROFILE_FOR_DEVICE_ARTICULATION"),
    },
    "terca": {
        "register": ("relative-to-main-solo", "avoid-crossing-or-outshining-lead"),
        "gate": ("follow-solo-phrase-selectively", "release-with-or-before-lead"),
        "density": ("sparser-than-main-solo", "harmonic-validity-before-density"),
        "controllers": ("do-not-copy-controllers-blindly",),
        "articulation": ("diatonic-third", "contextual-third", "unison-avoidance", "phrase-follow"),
        "interaction": ("dependent-on-main-solo-and-chords", "never-outrank-main-solo"),
        "repair": ("relationship-correctness-first", "fix-pitch-errors", "reduce-level-and-density-if-leading"),
        "generation": ("relationship-conditioned-from-solo", "chord-validated-third-selection"),
        "hard": ("HARMONY_VALIDATION_REQUIRED", "NEVER_OUTRANK_MAIN_SOLO"),
    },
    "echo": {
        "register": ("derived-from-solo-with-safe-register",),
        "gate": ("shorter-or-tail-like-than-source", "stop-before-next-lead-phrase"),
        "density": ("sparse", "never-recursive", "below-main-solo"),
        "controllers": ("do-not-recursively-copy-controller-stream",),
        "articulation": ("nonrecursive-delay", "answer", "tail", "fade-release"),
        "interaction": ("use-empty-phrase-space", "never-mask-solo-or-terca"),
        "repair": ("fix-delay-timing", "remove-recursive-echo", "reduce-density-and-level-hierarchy"),
        "generation": ("derive-once-from-main-solo", "phrase-tail-only", "section-boundary-stop"),
        "hard": ("NON_RECURSIVE", "NEVER_OUTRANK_MAIN_SOLO"),
    },
}


def complete_profile(role: str) -> CompleteInstrumentProfile:
    r = normalize_role(role)
    behavior = behavior_profile(r)
    policy = POLICIES.get(r)
    if policy is None:
        # Explicit conservative profile rather than silent generic mutation authority.
        policy = {
            "register": ("preserve-observed-register",),
            "gate": ("preserve-observed-gate-unless-evidence",),
            "density": ("preserve-observed-density",),
            "controllers": ("preserve-all-controllers",),
            "articulation": ("semantic-analysis-only",),
            "interaction": ("avoid-lead-and-bass-mask",),
            "repair": ("analysis-only-or-manual-review",),
            "generation": ("no-generation-without-role-evidence",),
            "hard": ("UNKNOWN_ROLE_NO_AUTOMATIC_REPLACE",),
        }
    return CompleteInstrumentProfile(
        role=r,
        player_model=behavior.musician_model,
        register_policy=policy["register"], gate_policy=policy["gate"], density_policy=policy["density"],
        controller_policy=policy["controllers"], articulation_policy=policy["articulation"],
        interaction_policy=policy["interaction"], repair_policy=policy["repair"], generation_policy=policy["generation"],
        hard_rules=COMMON_HARD + policy["hard"] + tuple(behavior.forbidden_without_device_evidence),
    )


def profile_document(role: str, *, bars: int = 4) -> dict[str, Any]:
    p = complete_profile(role)
    b = behavior_profile(p.role)
    g = grammar_for(p.role)
    return {
        "schema": "dna-complete-instrument-profile",
        "version": "4.44",
        "role": p.role,
        "playerModel": p.player_model,
        "behavior": asdict(b),
        "performanceGrammar": {
            "playerModel": g.player_model,
            "anchors": list(g.anchors),
            "dependencies": list(g.dependencies),
            "hardBoundaries": list(g.hard_boundaries),
            "phrasePlan": phrase_plan(p.role, bars),
        },
        "policies": {
            "register": list(p.register_policy), "gate": list(p.gate_policy), "density": list(p.density_policy),
            "controllers": list(p.controller_policy), "articulationNoise": list(p.articulation_policy),
            "interaction": list(p.interaction_policy), "repair": list(p.repair_policy), "generation": list(p.generation_policy),
        },
        "hardRules": list(dict.fromkeys(p.hard_rules)),
        "velocityAuthority": p.velocity_authority,
        "deviceArticulationAuthority": p.device_articulation_authority,
        "unknownDeviceFieldsPolicy": "KEEP_UNKNOWN_NEVER_GUESS",
    }


def catalog() -> dict[str, Any]:
    roles = sorted(POLICIES)
    return {
        "schema": "dna-complete-instrument-profile-catalog",
        "version": "4.44",
        "roles": {role: profile_document(role) for role in roles},
        "coverage": {"roleCount": len(roles), "allHaveExplicitPolicy": True, "allHaveExplicitGrammar": all(grammar_for(r).role == r for r in roles)},
        "globalRules": list(COMMON_HARD),
        "velocityAuthority": "FACTORY_ONLY",
        "deviceArticulationPolicy": "EXACT_SOUND_PROFILE_REQUIRED",
    }
