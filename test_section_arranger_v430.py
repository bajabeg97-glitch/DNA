from __future__ import annotations
import unittest,sys,json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from dna_midi_studio.ai_learning.section_arranger import SectionArrangerInference,SECTION_TYPES
from dna_midi_studio.ai_learning.track_replacement import TrackReplacementEngine,ReplacementRequest
from dna_midi_studio.midi import MidiFile
class Section430(unittest.TestCase):
 @classmethod
 def setUpClass(cls): cls.data=ROOT/'data'/'ai_section_context'; cls.raw=(ROOT/'artifacts/session35-partial-preview.mid').read_bytes()
 def test_manifest(self):
  m=json.loads((self.data/'section_context_manifest.json').read_text()); self.assertFalse(m['velocityUsed']); self.assertEqual(m['songs'],163); self.assertEqual(m['fillBreak'],'TRANSITION_ONLY_NOT_CONFIRMED_SECTION')
  z=np.load(self.data/'section_context_v1.npz')
  for sid in np.unique(z['song_ids']): self.assertEqual(len(set(z['split'][z['song_ids']==sid].tolist())),1)
 def test_checkpoint_no_velocity(self):
  z=np.load(self.data/'section_arranger_model_v1.npz'); self.assertFalse(any('velocity' in x.lower() for x in z.files))
 def test_inference(self):
  from dna_midi_studio.ai_learning.phrase_planner import PhrasePlannerInference
  p=PhrasePlannerInference(ROOT/'data/ai_phrase_context/phrase_planner_model_v1.npz'); ctx,ch,rid,meter,pos=p.input_from_midi(self.raw,6,'bass')
  e=SectionArrangerInference(self.data/'section_arranger_model_v1.npz'); a=e.predict_from_phrase_input(ctx,ch,rid,meter,pos); b=e.predict_from_phrase_input(ctx,ch,rid,meter,pos)
  self.assertEqual(a,b); self.assertIn(a['section'],SECTION_TYPES); self.assertFalse(a['velocityUsed'])
 def test_replacement_reports_section_intent(self):
  eng=TrackReplacementEngine(ROOT/'models/dna-reconstructor-v2',ROOT/'learning_data',ROOT/'data'); self.assertIsNotNone(eng.section_arranger)
  r=eng.replace(self.raw,ReplacementRequest(0,8,6,9,'bass'),n=8); self.assertTrue(r['sectionArranger']['used']); self.assertFalse(r['sectionArranger']['velocityUsed']); self.assertEqual(r['sectionArranger']['fillBreak'],'TRANSITION_ONLY_NOT_CONFIRMED_SECTION')
  for k in 'ABC': MidiFile.from_bytes(r['variants'][k]['midiBytes']).notes()
if __name__=='__main__': unittest.main()
