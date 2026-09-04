from dna_midi_studio.midi import Note
from dna_midi_studio.role_disambiguation import classify, echo_relationship, engine_target_policy

PPQ=480

def N(p,s,d=120,v=90): return Note(0,0,p,s,s+d,v)

def test_strum_is_not_solo_or_echo_eligible():
    notes=[]
    for t in range(0,PPQ*4,PPQ//2):
        notes += [N(48,t,80),N(52,t+8,80),N(55,t+16,80)]
    r=classify(notes,PPQ,family='guitar')
    assert r.role=='rhythm-guitar'
    assert not r.echo_eligible

def test_power_is_separate_from_rhythm_guitar():
    notes=[]
    for t in range(0,PPQ*4,PPQ//2): notes += [N(40,t,140),N(47,t,140),N(52,t,140)]
    r=classify(notes,PPQ,family='guitar')
    assert r.role=='power-riff'
    assert not engine_target_policy('guitar',r.role)['allowed']
    assert engine_target_policy('harmonic',r.role)['allowed']

def test_monophonic_guitar_sound_can_be_solo():
    pitches=[60,62,64,67,65,64,62,60]
    notes=[N(p,i*240,200) for i,p in enumerate(pitches)]
    r=classify(notes,PPQ,family='guitar')
    assert r.role=='solo'
    assert not engine_target_policy('guitar',r.role)['allowed']
    assert engine_target_policy('solo',r.role)['allowed']

def test_echo_requires_solo_relationship_and_strum_is_vetoed():
    src=[N(p,i*240,180) for i,p in enumerate([60,62,64,65,67,65,64,62])]
    echo=[Note(1,1,n.pitch,n.start+120,n.end+100,70) for n in src]
    e=echo_relationship(src,echo,PPQ)
    assert e['isEcho']
    strum=[]
    for t in range(120,PPQ*4,PPQ//2): strum += [Note(1,1,48,t,t+80,70),Note(1,1,52,t+8,t+88,70),Note(1,1,55,t+16,t+96,70)]
    e2=echo_relationship(src,strum,PPQ)
    assert not e2['isEcho'] and e2['reason']=='STRUMMING_VETO'
