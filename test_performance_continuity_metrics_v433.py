from dna_midi_studio.midi import Note
from dna_midi_studio.performance_continuity import smooth_phrase_notes,continuity_metrics

def test_metrics_improve_for_chopped_bass():
    ns=[Note(0,0,36,0,20,80),Note(0,0,38,120,140,81),Note(0,0,40,240,260,82)]
    b=continuity_metrics(ns,ppq=480)
    out,r=smooth_phrase_notes(ns,ppq=480,role='bass',region_end=480)
    a=continuity_metrics(out,ppq=480)
    assert a['shortGateCount'] < b['shortGateCount']
    assert [n.velocity for n in out]==[80,81,82]
    assert [n.start for n in out]==[0,120,240]
