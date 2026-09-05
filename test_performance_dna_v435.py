from pathlib import Path
from dna_midi_studio.ai_learning.performance_dna import PerformanceDNAEngine

ROOT=Path(__file__).resolve().parents[1]

def eng(): return PerformanceDNAEngine(ROOT/'data')

def test_library_has_real_evidence():
    s=eng().evidence_summary(); assert s['roles']['power-riff']['patterns']>0; assert s['roles']['bass']['patterns']>0; assert s['roles']['rhythm-guitar']['patterns']>0
    assert s['goldVelocityUsed'] is False

def test_powerchord_generation_is_relative_and_velocity_free():
    e=eng(); g=e.generate_pattern('power-riff','4/4',110,'body',0.0,1)
    assert g['evidenceSource']=='PERFORMANCE_DNA_V1'; assert g['velocityAuthority']=='FACTORY_ONLY'
    assert g['events'][0] and all(len(x)==4 and 0<=x[2]<=127 for x in g['events'][0])
    assert 'velocity' not in str(g['events']).lower()

def test_rhythm_guitar_prefers_factory():
    e=eng(); g=e.generate_pattern('rhythm-guitar','4/4',100,'body',0.0,0)
    assert g['performanceSource']=='FACTORY_STRUM'

def test_variants_deterministic_and_different():
    e=eng(); a=e.generate_pattern('power-riff','4/4',110,'body',0,0); b=e.generate_pattern('power-riff','4/4',110,'body',0,1)
    assert a==e.generate_pattern('power-riff','4/4',110,'body',0,0)
    assert a['antiCopy']['generatedFingerprint']!=b['antiCopy']['generatedFingerprint'] or a['performanceDNAId']!=b['performanceDNAId']

def test_transition_changes_retrieval_signal():
    e=eng(); a=e.generate_pattern('power-riff','4/4',110,'transition',0.7,0)
    assert a['transitionStrength']==0.7

def test_track_replacement_uses_performance_dna_for_supported_roles():
    from dna_midi_studio.ai_learning.track_replacement import TrackReplacementEngine, ReplacementRequest
    from dna_midi_studio.session19_fixture import build_benchmark_case
    e=TrackReplacementEngine(ROOT/'models/dna-reconstructor-v2',ROOT/'learning_data',ROOT/'data')
    raw=build_benchmark_case(0).midi
    for role,tr,ch,src in [('bass',2,1,'GOLD'),('power-riff',2,1,'GOLD'),('rhythm-guitar',2,1,'FACTORY_STRUM')]:
        r=e.replace(raw,ReplacementRequest(tr,ch,2,2,role),n=8)
        for key in 'ABC':
            p=r['variants'][key]['candidatePath'][0]
            assert p['evidenceSource']=='PERFORMANCE_DNA_V1'
            assert p['performanceSource']==src
            assert p['performanceDNAId']
            assert r['variants'][key]['goldVelocityUsed'] is False
            assert r['variants'][key]['neuralVelocityUsed'] is False
