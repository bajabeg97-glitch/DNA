from pathlib import Path
from dna_midi_studio.evidence_authority import EvidenceAuthorityEngine
ROOT=Path(__file__).resolve().parents[1]

def engine(): return EvidenceAuthorityEngine(ROOT/'data')

def test_factory_velocity_interpolation_is_factory_only():
    e=engine(); p=max(e.profiles,key=lambda x:int((x.get('velocityCurve') or {}).get('sampleCount') or 0))
    out=e.velocity_from_profile(p,.5)
    assert out['authority']=='FACTORY_ONLY' and 1<=out['velocity']<=127

def test_octave_repair_preserves_pitch_class():
    out=engine().octave_repair(84,48,72,60,'PLAYABLE')
    assert out['pitch']==60 and out['pitch']%12==84%12

def test_trigger_never_octave_repaired():
    out=engine().octave_repair(24,48,72,60,'RX_TRIGGER')
    assert out['pitch']==24 and out['decision']=='PRESERVE_OR_AUTHORITY_MAP'

def test_rhythm_guitar_gold_hard_rejected():
    c={'id':'x','role':'rhythm-guitar','meter':'4/4','authority':'GOLD','register_low':48,'register_high':72}
    ctx={'role':'rhythm-guitar','meter':'4/4','allowed_register':(40,84),'polyphony_budget':8,'transform_budget':10}
    d=engine().hard_filter_pattern(c,ctx)
    assert not d.hard_pass and 'RHYTHM_GUITAR_FACTORY_ONLY' in d.details['reasons']

def test_hard_pass_candidate():
    c={'id':'x','role':'rhythm-guitar','meter':'4/4','authority':'FACTORY_STRUM','register_low':48,'register_high':72,'peak_polyphony':4,'transform_cost':1}
    ctx={'role':'rhythm-guitar','meter':'4/4','allowed_register':(40,84),'polyphony_budget':8,'transform_budget':10}
    assert engine().hard_filter_pattern(c,ctx).hard_pass
