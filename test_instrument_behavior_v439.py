from dna_midi_studio.instrument_behavior import (
    behavior_profile, drum_element_optimization_plan,
    bass_function_plan, solo_phrase_optimization_plan, analyze_role,
)
from dna_midi_studio.instrument_articulation import analyze_articulation_context
from dna_midi_studio.midi import MidiFile, MidiTrack, MidiEvent, Note


def N(p,s,e,v=90,ch=0,tr=0):
    return Note(track=tr, channel=ch, pitch=p, start=s, end=e, velocity=v)


def midi_from_notes(notes, extra=(), ppq=480):
    m=MidiFile(1,ppq,[MidiTrack([])])
    m=m.add_notes(track_index=0,new_notes=notes)
    if extra:
        m=m.add_events(track_index=0,new_events=extra)
    return m


def test_expanded_profiles_are_real_not_pad_aliases():
    assert behavior_profile('strings').role == 'strings'
    assert behavior_profile('strings').musician_model == 'string-section arranger'
    assert behavior_profile('saxophone').role == 'sax'
    assert behavior_profile('woodwinds').role == 'woodwind'
    assert behavior_profile('piano').role == 'piano'
    assert behavior_profile('organ').role == 'organ'


def test_drum_plan_is_per_element_and_never_remaps_unknown_kit():
    notes=[N(36,0,80,110,ch=9),N(38,480,560,112,ch=9),N(38,240,280,35,ch=9),N(42,0,70,65,ch=9),N(47,720,780,75,ch=9),N(49,960,1040,92,ch=9)]
    p=drum_element_optimization_plan(notes,480,'transition')
    assert 'kick' in p['elements'] and 'ghost-snare' in p['elements'] and 'tom' in p['elements']
    assert p['elements']['kick']['hardRule'] == 'DO_NOT_REMAP_PITCH_WITHOUT_CONFIRMED_KIT_EVIDENCE'
    assert 'allow denser transition movement' in p['elements']['tom']['actions']


def test_bass_function_plan_labels_octave_and_semantic_slap_without_trigger():
    notes=[N(36,0,180),N(48,480,560),N(36,720,790)]
    p=bass_function_plan(notes,480)
    assert any('OCTAVE' in row['functions'] for row in p['notes'])
    assert any('SLAP_POP_CANDIDATE' in row['functions'] for row in p['notes'])
    assert p['deviceTriggerPolicy'].startswith('SEMANTIC_ONLY')


def test_solo_phrase_plan_allocates_phrase_level_opportunities():
    notes=[N(60,0,140),N(62,150,400),N(64,410,700),N(65,1500,1750),N(65,1760,1990)]
    p=solo_phrase_optimization_plan(notes,480)
    assert p['treatment']=='DEEP'
    assert len(p['phrases'])==2
    assert all(x['ornamentBudget']>=1 for x in p['phrases'])
    assert 'preserve main melody' in p['phrases'][0]['priorities']


def test_guitar_articulation_is_semantic_until_confirmed_profile():
    notes=[N(52,0,100),N(52,240,320),N(55,480,560)]
    pb=MidiEvent(tick=300,order=0,kind='channel',status=0xE0,data=bytes((0,80)))
    m=midi_from_notes(notes,[pb])
    r=analyze_articulation_context(m,role='rhythm-guitar',track_index=0,channel=0,start_tick=0,end_tick=960,notes=notes)
    names={x['candidate']:x for x in r['candidates']}
    assert 'PALM_MUTE_CANDIDATE' in names
    assert 'GUITAR_SLIDE_BEND_CANDIDATE' in names
    assert all(x['deviceActionability']=='SEMANTIC_ONLY' for x in r['candidates'])


def test_brass_fall_and_breath_context_detected_but_not_auto_triggered():
    notes=[N(60,0,400),N(64,420,800),N(67,1600,1900)]
    pb1=MidiEvent(tick=700,order=0,kind='channel',status=0xE0,data=bytes((0,64)))
    pb2=MidiEvent(tick=780,order=1,kind='channel',status=0xE0,data=bytes((0,48)))
    m=midi_from_notes(notes,[pb1,pb2])
    r=analyze_articulation_context(m,role='brass',track_index=0,channel=0,start_tick=0,end_tick=2200,notes=notes)
    names={x['candidate'] for x in r['candidates']}
    assert 'BREATH_NOISE_CANDIDATE' in names
    assert 'FALL_CANDIDATE' in names


def test_analyze_role_embeds_articulation_and_instrument_specific_plan():
    notes=[N(36,0,200),N(48,480,560),N(47,720,790)]
    m=midi_from_notes(notes)
    r=analyze_role(m,role='bass',track_index=0,channel=0,start_tick=0,end_tick=960)
    assert r['detail']['functionPlan']['schema']=='dna-bass-function-plan'
    assert r['detail']['articulationContext']['schema']=='dna-articulation-context-analysis'
    assert r['velocityAuthority']=='FACTORY_ONLY'


def test_semantic_ai_can_only_select_from_confirmed_exact_sound_map():
    from dataclasses import dataclass
    from dna_midi_studio.instrument_articulation import suggest_confirmed_device_articulations
    @dataclass
    class T: articulation: str
    @dataclass
    class M:
        confirmed: bool
        triggers: tuple
    analysis={"candidates":[{"candidate":"PALM_MUTE_CANDIDATE","supportCount":4},{"candidate":"BREATH_NOISE_CANDIDATE","supportCount":2}]}
    m=M(True,(T('palm_mute'),T('legato')))
    blocked=suggest_confirmed_device_articulations(analysis,m,exact_sound_matches=False)
    assert blocked['decision']=='BLOCKED' and not blocked['requested']
    ok=suggest_confirmed_device_articulations(analysis,m,exact_sound_matches=True)
    assert ok['requested']==['palm_mute']
    assert all(x in {'palm_mute','legato'} for x in ok['requested'])
