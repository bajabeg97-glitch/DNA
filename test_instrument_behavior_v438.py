from dna_midi_studio.instrument_behavior import (
    analyze_bass_behavior, analyze_drum_elements, analyze_solo_behavior,
    behavior_profile, classify_drum_element, drum_pattern_behavior_score,
    harmonic_pattern_behavior_score, guitar_pattern_behavior_score, profile_catalog,
)
from dna_midi_studio.midi import Note


def N(p,s,e,v=90,ch=0,tr=0):
    return Note(track=tr, channel=ch, pitch=p, start=s, end=e, velocity=v)


def test_profiles_cover_core_roles():
    for role in ("drums","percussion","bass","rhythm-guitar","power-riff","accompaniment","pad","brass","accordion","solo","terca","echo"):
        p=behavior_profile(role)
        assert p.role == role
        assert p.optimization_priorities
    c=profile_catalog()
    assert c["hardBoundary"]["velocityAuthority"] == "FACTORY_ONLY"
    assert c["hardBoundary"]["deviceTriggersRequireExactSoundProfile"] is True


def test_drum_element_semantics_are_per_element():
    assert classify_drum_element(36) == "kick"
    assert classify_drum_element(38) == "snare"
    assert classify_drum_element(42) == "closed-hat"
    assert classify_drum_element(46) == "open-hat"
    assert classify_drum_element(49) == "crash"
    assert classify_drum_element(47) == "tom"
    notes=[N(36,0,60,110,ch=9),N(38,480,540,112,ch=9),N(38,240,280,35,ch=9),N(42,0,60,60,ch=9),N(46,720,780,70,ch=9)]
    r=analyze_drum_elements(notes,480)
    assert r["elements"]["kick"]["behavior"]["musical_job"].startswith("low-end")
    assert "ghost-snare" in r["elements"]


def test_bass_detects_octave_and_candidates_without_device_trigger_claim():
    notes=[N(36,0,300),N(48,480,620),N(47,720,780),N(36,960,1500)]
    r=analyze_bass_behavior(notes,480,[0,960])
    assert r["techniques"].get("octave",0) >= 1
    assert "semantic candidates only" in r["note"]


def test_solo_is_deep_phrase_treatment():
    notes=[N(60,0,430),N(62,440,850),N(64,860,1180),N(65,1800,2100)]
    r=analyze_solo_behavior(notes,480)
    assert r["priority"] == "DEEP_TREATMENT"
    assert r["phraseCandidates"] >= 2


def test_behavior_scores_are_soft_positive_signals():
    class E:
        def __init__(self, element=None,function=None,approach="none",degree=1,direction=None):
            self.element=element; self.function=function; self.approach=approach; self.degree=degree; self.direction=direction
    assert drum_pattern_behavior_score([E("kick"),E("snare"),E("hat")], section="verse") > 2
    class P: pass
    p=P(); p.events=[E(function="root",degree=1),E(function="scale",approach="chromatic-below",degree=5)]
    assert harmonic_pattern_behavior_score(p, role="bass", section="verse") > 1
    g=P(); g.strokes=[E(direction="down"),E(direction="up"),E(direction="mute")]
    assert guitar_pattern_behavior_score(g, section="chorus") > 1
