from pathlib import Path
from dna_midi_studio.unified_pipeline import _evidence_coverage_summary

def test_pipeline_exposes_completed_software_evidence_matrix():
    x=_evidence_coverage_summary(Path('.'))
    assert x['softwareCoveragePercent']==100.0
    assert x['deviceEvidenceStatus']=='HARDWARE_PENDING'
