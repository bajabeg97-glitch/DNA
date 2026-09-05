from __future__ import annotations
import unittest, sys, json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from dna_midi_studio.ai_learning.phrase_planner import PhrasePlannerInference
from dna_midi_studio.ai_learning.track_replacement import TrackReplacementEngine, ReplacementRequest
from dna_midi_studio.midi import MidiFile

class PhraseDecoder428Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.data=ROOT/'data'/'ai_phrase_context'; cls.raw=(ROOT/'artifacts/session35-partial-preview.mid').read_bytes()
 def test_manifest_source_disjoint_and_no_velocity(self):
  m=json.loads((self.data/'phrase_context_manifest.json').read_text())
  self.assertFalse(m['velocity_used']); self.assertEqual(m['songs'],163); self.assertGreater(m['samples'],10000)
  z=np.load(self.data/'phrase_context_v1.npz'); songs=z['song_ids']; split=z['split']
  for sid in np.unique(songs): self.assertEqual(len(set(split[songs==sid].tolist())),1)
 def test_checkpoint_has_no_velocity_head(self):
  z=np.load(self.data/'phrase_planner_model_v1.npz'); self.assertFalse(any('velocity' in k.lower() for k in z.files))
 def test_phrase_prediction_shape_and_determinism(self):
  e=PhrasePlannerInference(self.data/'phrase_planner_model_v1.npz')
  a=e.predict(self.raw,6,'bass'); b=e.predict(self.raw,6,'bass')
  self.assertEqual(a.shape,(4,7)); self.assertTrue(np.allclose(a,b))
 def test_replacement_uses_phrase_planner_for_four_bars(self):
  eng=TrackReplacementEngine(ROOT/'models/dna-reconstructor-v2',ROOT/'learning_data',ROOT/'data')
  self.assertIsNotNone(eng.phrase_planner)
  r=eng.replace(self.raw,ReplacementRequest(0,8,6,9,'bass'),n=8)
  self.assertTrue(r['phrasePlanner']['used']); self.assertEqual(r['phrasePlanner']['phraseBars'],4)
  paths=[tuple((x['bar'],x.get('phraseScore')) for x in r['variants'][k]['candidatePath']) for k in 'ABC']
  self.assertTrue(all(len(p)==4 for p in paths)); self.assertTrue(all(all(s is not None for _,s in p) for p in paths))
  for k in 'ABC': MidiFile.from_bytes(r['variants'][k]['midiBytes']).notes(); self.assertFalse(r['variants'][k]['neuralVelocityUsed'])
 def test_non_four_bar_request_does_not_claim_phrase_planner(self):
  eng=TrackReplacementEngine(ROOT/'models/dna-reconstructor-v2',ROOT/'learning_data',ROOT/'data')
  r=eng.replace(self.raw,ReplacementRequest(0,8,6,7,'bass'),n=8)
  self.assertFalse(r['phrasePlanner']['used'])

if __name__=='__main__': unittest.main()
