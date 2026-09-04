import json
import unittest
from pathlib import Path

from dna_midi_studio.ai_learning.max_orchestrator import MaxCandidateOrchestrator, MaxModelRegistry, build_max_status

ROOT=Path(__file__).resolve().parents[1]

class MaxOrchestratorTests(unittest.TestCase):
    def test_registry_and_authority(self):
        s=build_max_status(ROOT)
        self.assertEqual(s['registry']['authority']['velocity'],'FACTORY_ONLY')
        self.assertFalse(s['registry']['authority']['goldVelocity'])
        self.assertFalse(s['registry']['authority']['neuralVelocity'])
        self.assertTrue(s['application']['requiredGates'])
    def test_hard_invalid_never_ranks(self):
        o=MaxCandidateOrchestrator()
        rows=o.rank_bar_candidates([
            {'hard_valid':False,'evidenceSource':'GOLD','retrievalScore':100,'score':100,'retrievalRank':0},
            {'hard_valid':True,'evidenceSource':'FULL_SONG_EVENT_DECODER_V1','retrievalScore':0,'score':0,'contextScore':.9,'phraseScore':.8,'retrievalRank':1},
        ],'bass')
        self.assertTrue(rows[0]['hard_valid'])
        self.assertLess(rows[-1]['maxScore'],0)
    def test_score_has_full_breakdown(self):
        o=MaxCandidateOrchestrator(); c={'hard_valid':True,'evidenceSource':'GOLD','retrievalScore':2.0,'score':0.0,'contextScore':.8,'phraseScore':.7,'retrievalRank':0}
        r=o.score_candidate(c,'bass')
        for k in ('evidence','neural','context','phrase','transition','diversity','total'):
            self.assertIn(k,r['scoreBreakdown'])
        self.assertGreater(r['maxScore'],0)

if __name__=='__main__': unittest.main()
