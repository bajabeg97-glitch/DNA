import unittest
from pathlib import Path
from dna_midi_studio.ai_learning.track_replacement import TrackReplacementEngine, ReplacementRequest
from dna_midi_studio.song_understanding import analyze_song_map

ROOT=Path(__file__).resolve().parent

try:
    import torch  # noqa: F401
    TORCH_OK=True
except Exception:
    TORCH_OK=False

from dna_midi_studio.max_layout import engine_dirs

@unittest.skipUnless(TORCH_OK and (ROOT/'session35-partial-preview.mid').is_file(),
                     "torch or fixture unavailable")
class MaxTransitionApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dirs=engine_dirs(ROOT)
        cls.raw=(ROOT/'session35-partial-preview.mid').read_bytes()
        cls.engine=TrackReplacementEngine(dirs["model_dir"],dirs["learning_data_dir"],dirs["data_dir"])
    def test_transition_source_is_gated(self):
        song=analyze_song_map(self.raw,'t.mid'); bar=song['bars'][5]
        req=ReplacementRequest(0,8,6,9,'bass')
        quiet=self.engine._transition_event_variants(self.raw,req,bar,{'transitionStrength':0.05})
        strong=self.engine._transition_event_variants(self.raw,req,bar,{'transitionStrength':0.80})
        self.assertEqual(quiet,[])
        self.assertTrue(strong)
        self.assertTrue(all(x['evidenceSource']=='TRANSITION_EVENT_AR_V1' for x in strong))
        self.assertTrue(all(x['transitionOnly'] and not x['velocityUsed'] for x in strong))
    def test_replace_report_contains_max_contract(self):
        r=self.engine.replace(self.raw,ReplacementRequest(0,8,6,9,'bass'),n=8)
        self.assertTrue(r['maxOrchestration']['rankingOnly'])
        self.assertTrue(r['maxOrchestration']['finalValidatorRequired'])
        for v in r['variants'].values():
            self.assertTrue(all('maxScore' in p and 'scoreBreakdown' in p for p in v['candidatePath']))

if __name__=='__main__': unittest.main()
