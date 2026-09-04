from pathlib import Path
from dna_midi_studio.evidence_completion import EvidenceCompletionEngine,CANONICAL_ROLES

def eng(): return EvidenceCompletionEngine(Path('data'),Path('.'))

def test_factory_device_unknown_not_guessed():
    r=eng().build_factory_device_registry(); assert r['summary']['profiles']>=1900
    assert all(p['playable_range'] is None and p['device_status'].startswith('HARDWARE_PENDING') for p in r['profiles'])

def test_gold_melodic_roles_complete_no_velocity():
    r=eng().build_gold_melodic_registry(); assert set(r['roles'])=={'solo','terca','echo'}
    assert r['roles']['terca']['samples']>0 and r['roles']['echo']['samples']>0
    assert all(not x['velocityIncluded'] for x in r['roles'].values())

def test_relationship_evidence():
    r=eng().build_relationship_registry(); assert r['kickBassEvidence']['count']>0; assert r['fillSectionEvidence']['count']>0

def test_drum_element_wiring():
    r=eng().build_drum_element_registry(); assert r['gold']['patterns']>0; assert r['wiring']['velocity']=='FACTORY_ONLY'

def test_rhythm_guitar_hybrid():
    r=eng().build_hybrid_rhythm_guitar(); assert r['factory']['strumPatterns']>2000; assert not r['gold']['velocityAuthority']

def test_coverage_100_software_routes():
    r=eng().build_coverage_matrix(); assert set(r['roles'])==set(CANONICAL_ROLES); assert r['softwareCoverage']['percent']==100.0
    assert r['deviceCoverage']['status']=='HARDWARE_PENDING'

def test_build_all_truthful():
    r=eng().build_all(); assert r['completion']['softwareScopePercent']==100.0
    assert r['completion']['unknownDeviceFactsGuessed'] is False
