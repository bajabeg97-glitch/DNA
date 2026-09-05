from __future__ import annotations
import unittest,sys,json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from dna_midi_studio.ai_learning.transition_fill import TransitionFillInference
from dna_midi_studio.ai_learning.phrase_planner import PhrasePlannerInference
from dna_midi_studio.ai_learning.track_replacement import TrackReplacementEngine,ReplacementRequest
from dna_midi_studio.midi import MidiFile
class Transition431(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.data=ROOT/'data'/'ai_transition_fill'; cls.raw=(ROOT/'artifacts/session35-partial-preview.mid').read_bytes()
 def test_manifest_source_disjoint_velocity_free(self):
  m=json.loads((self.data/'transition_fill_manifest.json').read_text()); self.assertFalse(m['velocityUsed']); self.assertEqual(m['songs'],163); self.assertEqual(m['break'],'NOT_INFERRED')
  z=np.load(self.data/'transition_fill_context_v1.npz')
  for sid in np.unique(z['song_ids']): self.assertEqual(len(set(z['split'][z['song_ids']==sid].tolist())),1)
 def test_checkpoint_has_no_velocity(self):
  z=np.load(self.data/'transition_fill_model_v1.npz'); self.assertFalse(any('velocity' in x.lower() for x in z.files))
 def test_inference_deterministic(self):
  p=PhrasePlannerInference(ROOT/'data/ai_phrase_context/phrase_planner_model_v1.npz'); ctx,ch,rid,meter,pos=p.input_from_midi(self.raw,6,'bass')
  e=TransitionFillInference(self.data/'transition_fill_model_v1.npz'); a=e.predict(ctx,ch,rid,meter,pos); b=e.predict(ctx,ch,rid,meter,pos)
  self.assertEqual(a,b); self.assertEqual(len(a['targetDelta']),7); self.assertFalse(a['velocityUsed'])
 def test_track_replacement_uses_transition_intent(self):
  eng=TrackReplacementEngine(ROOT/'models/dna-reconstructor-v2',ROOT/'learning_data',ROOT/'data'); self.assertIsNotNone(eng.transition_fill)
  r=eng.replace(self.raw,ReplacementRequest(0,8,6,9,'bass'),n=8)
  self.assertTrue(r['transitionFill']['used']); self.assertFalse(r['transitionFill']['velocityUsed']); self.assertEqual(r['transitionFill']['break'],'NOT_INFERRED')
  for k in 'ABC': MidiFile.from_bytes(r['variants'][k]['midiBytes']).notes()
if __name__=='__main__': unittest.main()
