from pathlib import Path
from dna_midi_studio.factory_calibration import FactoryCalibrationEngine, build_factory_calibration
from dna_midi_studio.evidence_authority import EvidenceAuthorityEngine
ROOT=Path(__file__).resolve().parents[1]

def test_calibration_manifest_real_factory_counts():
    e=FactoryCalibrationEngine(ROOT/'data/factory-calibration-4.37.json')
    s=e.doc['summary']
    assert s['factoryProfiles']==1964
    assert s['profilesWithCalibration']>=1600
    assert s['heldOutExactProfiles']>=900
    assert e.doc['policy']['velocityAuthority']=='FACTORY_ONLY'

def test_exact_or_role_resolution_is_deterministic():
    e=FactoryCalibrationEngine(ROOT/'data/factory-calibration-4.37.json')
    pid=next(iter(e.profiles))
    a=e.resolve(pid,e.profiles[pid].get('role'))
    b=e.resolve(pid,e.profiles[pid].get('role'))
    assert a==b and a['decision']=='PASS'
    assert a['source']=='EXACT_PROFILE_CALIBRATION'

def test_evidence_engine_loads_calibration():
    e=EvidenceAuthorityEngine(ROOT/'data')
    assert e.VERSION=='4.37.0'
    assert e.calibration is not None
    p=e.profiles[0]
    r=e.calibration_for(str(p['id']),p.get('role'))
    assert r['decision']=='PASS'

def test_calibrated_gate_rejects_extreme_deviation():
    e=EvidenceAuthorityEngine(ROOT/'data')
    # choose a profile that has held-out calibration
    pid=next(k for k,v in e.calibration.profiles.items() if v.get('mode')=='HELD_OUT_EXACT')
    p=e.calibration.profiles[pid]
    role=p.get('role') or 'ACC1'
    c={'id':'x','role':role,'meter':'4/4','authority':'FACTORY','register_low':0,'register_high':127,'register_center':127,'density_per_bar':999,'peak_polyphony':1,'transform_cost':0,'confidence':1}
    ctx={'role':role,'meter':'4/4','allowed_register':(0,127),'polyphony_budget':99,'transform_budget':99}
    r=e.calibrated_pattern_gate(c,ctx,pid)
    assert not r.hard_pass
    assert 'CALIBRATED_DENSITY_DEVIATION' in r.details['reasons'] or 'CALIBRATED_REGISTER_DEVIATION' in r.details['reasons']

def test_no_calibration_abstains():
    e=FactoryCalibrationEngine(ROOT/'data/factory-calibration-4.37.json')
    r=e.resolve('NOPE','no-such-role')
    assert r['decision']=='MANUAL_REVIEW'
