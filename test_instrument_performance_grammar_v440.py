from dna_midi_studio.instrument_performance_grammar import (
    grammar_for, phrase_state, performance_intent, phrase_plan, score_semantic_functions, catalog
)


def test_phrase_state_progression():
    assert phrase_state(0.0) == 'ENTRY'
    assert phrase_state(0.4) == 'BODY'
    assert phrase_state(0.65) == 'BUILD'
    assert phrase_state(0.82) == 'TRANSITION'
    assert phrase_state(0.95) == 'CADENCE'


def test_bass_build_prefers_octave_and_approach():
    x=performance_intent('bass', phrase_position=.66, section_energy=.8)
    assert x['state']=='BUILD'
    assert 'octave' in x['preferred']
    assert x['velocityAuthority']=='FACTORY_ONLY'


def test_power_transition_has_target_aware_playing():
    x=performance_intent('power-riff', phrase_position=.75, transition_strength=.8)
    assert x['state']=='TRANSITION'
    assert 'target-hit' in x['preferred']


def test_brass_fall_is_not_body_default():
    body=performance_intent('brass', phrase_position=.4)
    cad=performance_intent('brass', phrase_position=.95)
    assert 'fall-candidate' not in body['preferred']
    assert 'fall-candidate' in cad['preferred']


def test_solo_never_gets_melody_rewrite_permission():
    g=grammar_for('solo')
    assert 'PRESERVE_MAIN_MELODY' in g.hard_boundaries
    build=g.rule_for('BUILD')
    assert 'melody-rewrite' in build.avoid


def test_echo_stays_nonrecursive():
    g=grammar_for('echo')
    assert 'NON_RECURSIVE' in g.hard_boundaries
    assert 'recursive-echo' in g.rule_for('BODY').avoid


def test_phrase_plan_has_musical_development():
    p=phrase_plan('accordion',4,transition_strength=.75,section_energy=.7)
    assert len(p)==4
    assert p[0]['state']=='ENTRY'
    assert p[-1]['state']=='TRANSITION'
    assert p[0]['preferred'] != p[-1]['preferred']


def test_semantic_score_rewards_phase_fit():
    good=score_semantic_functions('bass',['OCTAVE','APPROACH','PICKUP'],phrase_position=.65,section_energy=.8)
    bad=score_semantic_functions('bass',['RANDOM_REGISTER_JUMP'],phrase_position=.65,section_energy=.8)
    assert good['score'] > bad['score']


def test_catalog_has_core_roles():
    c=catalog()
    for role in ['drums','bass','rhythm-guitar','power-riff','strings','brass','sax','accordion','solo','terca','echo']:
        assert role in c['roles']
