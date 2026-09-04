from dna_midi_studio.arrangement_interaction import RoleRegion, analyze_arrangement, target_interaction
from dna_midi_studio.midi import MidiFile, MidiTrack, MidiEvent


def _track(notes, channel=0):
    events=[]; order=0
    for start,end,pitch,vel in notes:
        events.append(MidiEvent(start,order,'channel',0x90|channel,bytes((pitch,vel)))); order+=1
        events.append(MidiEvent(end,order,'channel',0x80|channel,bytes((pitch,0)))); order+=1
    return MidiTrack(events)


def _midi():
    return MidiFile(1,96,[
        _track([(0,48,36,90),(96,144,38,90),(192,240,36,90)]),
        _track([(0,180,36,80),(192,372,43,80)]),
        _track([(0,360,60,85),(384,744,62,85)]),
        _track([(0,360,60,70),(384,744,62,70)]),
    ])


def test_bass_drum_pocket_is_measured():
    m=_midi(); r=analyze_arrangement(m,[RoleRegion('drums',0,0,0,768),RoleRegion('bass',1,0,0,768)])
    t=target_interaction(r,'bass')
    assert t['pocketScore'] is not None
    assert 0 <= t['score'] <= 1


def test_solo_terca_masking_is_soft_not_hard():
    m=_midi(); r=analyze_arrangement(m,[RoleRegion('solo',2,0,0,768),RoleRegion('terca',3,0,0,768)])
    t=target_interaction(r,'solo')
    assert t['hardGate'] is False
    assert t['leadMasking']


def test_interaction_never_claims_device_authority():
    m=_midi(); r=analyze_arrangement(m,[RoleRegion('solo',2,0,0,768)])
    assert r['policy']=='SOFT_MUSICAL_EVIDENCE_ONLY'
