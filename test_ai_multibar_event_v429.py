from __future__ import annotations
import json, sys, unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from dna_midi_studio.ai_learning.multibar_event import MultiBarEventInference,PHRASE_BARS,TOTAL_EVENTS
from dna_midi_studio.ai_learning.track_replacement import TrackReplacementEngine,ReplacementRequest
from dna_midi_studio.midi import MidiFile

class MultiBarEvent429Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.data=ROOT/'data'/'ai_multibar_event'; cls.raw=(ROOT/'artifacts/session35-partial-preview.mid').read_bytes()
 def test_manifest_source_disjoint_and_no_velocity(self):
  m=json.loads((self.data/'multibar_event_manifest.json').read_text())
  self.assertFalse(m['velocityUsed']); self.assertEqual(m['songs'],163); self.assertGreater(m['samples'],10000)
  z=np.load(self.data/'multibar_event_v1.npz')
  for sid in np.unique(z['song_ids']): self.assertEqual(len(set(z['split'][z['song_ids']==sid].tolist())),1)
 def test_checkpoint_has_no_velocity_head_and_shared_hidden_state(self):
  z=np.load(self.data/'multibar_event_model_v3.npz'); self.assertFalse(any('velocity' in k.lower() for k in z.files))
  r=json.loads((self.data/'multibar_event_training_report.json').read_text()); self.assertTrue(r['sharedHiddenState']); self.assertFalse(r['velocityInput']); self.assertFalse(r['velocityOutput'])
 def test_generation_is_deterministic_and_spans_four_bars(self):
  e=MultiBarEventInference(self.data/'multibar_event_model_v3.npz')
  a=e.generate(self.raw,6,'bass',0); b=e.generate(self.raw,6,'bass',0); self.assertEqual(a,b)
  self.assertEqual(len(a),TOTAL_EVENTS); self.assertEqual(set(x[0] for x in a),set(range(PHRASE_BARS)))
 def test_track_replacement_uses_atomic_multibar_phrases(self):
  eng=TrackReplacementEngine(ROOT/'models/dna-reconstructor-v2',ROOT/'learning_data',ROOT/'data')
  self.assertIsNotNone(eng.multibar_decoder)
  r=eng.replace(self.raw,ReplacementRequest(0,8,6,9,'bass'),n=8)
  self.assertTrue(r['multibarEventDecoder']['used']); self.assertTrue(r['multibarEventDecoder']['sharedHiddenState'])
  for k in 'ABC':
   self.assertEqual(len(r['variants'][k]['candidatePath']),4)
   self.assertTrue(all(x['evidenceSource']=='FULL_SONG_MULTIBAR_EVENT_V3' for x in r['variants'][k]['candidatePath']))
   self.assertFalse(r['variants'][k]['neuralVelocityUsed']); MidiFile.from_bytes(r['variants'][k]['midiBytes']).notes()
 def test_non_four_bar_does_not_claim_multibar_decoder(self):
  eng=TrackReplacementEngine(ROOT/'models/dna-reconstructor-v2',ROOT/'learning_data',ROOT/'data')
  r=eng.replace(self.raw,ReplacementRequest(0,8,6,7,'bass'),n=8); self.assertFalse(r['multibarEventDecoder']['used'])

if __name__=='__main__': unittest.main()
