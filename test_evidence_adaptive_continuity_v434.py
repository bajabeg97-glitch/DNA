from dna_midi_studio.midi import Note
from dna_midi_studio.performance_continuity import smooth_phrase_notes


def n(start,end,pitch=60,vel=90,track=0,channel=0):
    return Note(track=track,channel=channel,pitch=pitch,velocity=vel,start=start,end=end)


def test_solo_uses_real_relation_evidence_and_repairs_large_artificial_gap():
    ppq=480
    # Generated note ends far too early (0.15 QN gap), but next attack is within
    # the real-song phrase-IOI envelope learned from original Solo relations.
    src=[n(0,70,72), n(144,200,74)]  # IOI=.30 QN, initial gap=.154 QN
    out,rep=smooth_phrase_notes(src,ppq=ppq,role='solo',region_end=480)
    assert rep['evidenceSource'].startswith('REAL_ORIGINAL_MIDI_RELATIONSHIP')
    assert rep['phraseLegatoMaxIoiTicks'] >= 144
    assert out[0].end > 70
    assert 0 <= 144-out[0].end <= rep['releaseGapTicks']
    assert [x.pitch for x in out]==[72,74]
    assert [x.start for x in out]==[0,144]
    assert [x.velocity for x in out]==[90,90]


def test_solo_preserves_real_phrase_rest_outside_ioi_envelope():
    ppq=480
    src=[n(0,60,72), n(480,560,74)]  # 1 QN rest / new phrase
    out,rep=smooth_phrase_notes(src,ppq=ppq,role='solo',region_end=960)
    assert out[0].end < 200  # must not legato-bridge the full phrase rest


def test_factory_guitar_short_stroke_evidence_keeps_smoothing_conservative():
    ppq=480
    src=[n(0,35,60),n(96,130,64)]
    out,rep=smooth_phrase_notes(src,ppq=ppq,role='rhythm_guitar',region_end=480)
    assert rep['evidenceSource']=='FACTORY_STRUMMING_STROKE_EVIDENCE'
    assert rep['shortGateEvidenceRatio'] > .20
    # Do not force guitar into melodic near-legato mode.
    assert rep['phraseLegatoMaxIoiTicks']==0
    assert out[0].end < 96


def test_bass_uses_gold_nonvelocity_evidence():
    ppq=480
    src=[n(0,20,40),n(240,300,43)]
    out,rep=smooth_phrase_notes(src,ppq=ppq,role='bass',region_end=480)
    assert rep['evidenceSource']=='GOLD_NON_VELOCITY_PERFORMANCE_PATTERNS'
    assert rep['evidenceCount'] > 1000
    assert out[0].end > 20
