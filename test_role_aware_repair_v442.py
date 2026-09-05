from dna_midi_studio.midi import MidiFile, MidiTrack, MidiEvent
from dna_midi_studio.role_aware_repair import decide_region


def _midi(notes):
    events=[]; order=0
    for start,end,pitch,vel in notes:
        events.append(MidiEvent(start, order, 'channel', 0x90, bytes((pitch,vel)))); order+=1
        events.append(MidiEvent(end, order, 'channel', 0x80, bytes((pitch,0)))); order+=1
    events.append(MidiEvent(max([n[1] for n in notes], default=1920), order, 'meta', None, b'', 0x2F))
    return MidiFile(1,480,[MidiTrack(events)])


def test_good_bass_keeps():
    m=_midi([(0,400,36,80),(480,880,36,82),(960,1360,43,81),(1440,1840,36,84)])
    d=decide_region(m,role='bass',track_index=0,channel=0,start_tick=0,end_tick=1920,evidence_strength=.8)
    assert d.decision in {'KEEP','REPAIR'}
    assert 'TARGET_REGION_EMPTY' not in d.reason_codes


def test_empty_with_strong_evidence_augments_or_replaces():
    m=_midi([])
    d=decide_region(m,role='bass',track_index=0,channel=0,start_tick=0,end_tick=1920,evidence_strength=.8,target_is_known_bad=True)
    assert d.decision in {'REPLACE','AUGMENT'}


def test_empty_without_evidence_requires_review():
    m=_midi([])
    d=decide_region(m,role='rhythm-guitar',track_index=0,channel=0,start_tick=0,end_tick=1920,evidence_strength=.1,target_is_known_bad=True)
    assert d.decision == 'MANUAL_REVIEW'


def test_solo_never_generic_replace_when_nonempty():
    m=_midi([(0,20,60,85),(480,500,84,86),(960,980,60,84),(1440,1460,84,86)])
    d=decide_region(m,role='solo',track_index=0,channel=0,start_tick=0,end_tick=1920,evidence_strength=.95,target_is_known_bad=True)
    assert d.decision != 'REPLACE'
    assert d.decision in {'REPAIR','MANUAL_REVIEW'}


def test_factory_velocity_policy_is_explicit():
    m=_midi([(0,300,36,80)])
    out=decide_region(m,role='bass',track_index=0,channel=0,start_tick=0,end_tick=480,evidence_strength=.8).to_dict()
    assert out['policy']['velocityAuthority']=='FACTORY_ONLY'
