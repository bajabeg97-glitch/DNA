from dna_midi_studio.midi import Note
from dna_midi_studio.performance_continuity import smooth_phrase_notes


def n(p,s,e,v=80):
    return Note(0,0,p,s,e,v)


def test_bass_micro_gaps_are_bridged_without_changing_onsets_pitch_velocity():
    src=[n(36,0,30), n(38,120,145), n(40,240,270)]
    out,rep=smooth_phrase_notes(src,ppq=480,role='bass',region_end=480)
    assert [x.start for x in out]==[0,120,240]
    assert [x.pitch for x in out]==[36,38,40]
    assert [x.velocity for x in out]==[80,80,80]
    assert out[0].end >= 100
    assert out[0].end < out[1].start
    assert out[1].end < out[2].start
    assert rep['adjusted'] >= 2


def test_drums_are_not_smoothed():
    src=[n(36,0,5),n(38,120,125)]
    out,rep=smooth_phrase_notes(src,ppq=480,role='drums',region_end=480)
    assert [(x.start,x.end) for x in out]==[(0,5),(120,125)]
    assert rep['applied'] is False


def test_long_bass_note_is_capped_before_next_attack():
    src=[n(36,0,300),n(38,240,400)]
    out,rep=smooth_phrase_notes(src,ppq=480,role='bass',region_end=480)
    assert out[0].end < 240
    assert out[1].start==240
    assert rep['shortened'] >= 1
