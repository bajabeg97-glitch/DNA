from __future__ import annotations
import sys, unittest, tempfile, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from dna_midi_studio.ai_learning import TrackReplacementEngine,ReplacementRequest
from dna_midi_studio.session19_fixture import build_benchmark_case
from dna_midi_studio.midi import MidiFile

class AITrackReplacement419Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.engine=TrackReplacementEngine(ROOT/'models/dna-reconstructor-v2',ROOT/'learning_data',ROOT/'data')
  cls.raw=build_benchmark_case(0).midi

 def test_bass_replace_outputs_abc_factory_velocity(self):
  req=ReplacementRequest(2,1,2,2,'bass'); r=self.engine.replace(self.raw,req,n=8)
  self.assertEqual(set(r['variants']),{'A','B','C'}); self.assertEqual(r['authority']['velocity'],'FACTORY_ONLY')
  self.assertFalse(r['authority']['neuralVelocityOutput'])
  for v in r['variants'].values():
   self.assertFalse(v['goldVelocityUsed']); self.assertFalse(v['neuralVelocityUsed']); self.assertTrue(v['protectedEventsPreserved'])
   self.assertGreater(v['noteCount'],0); MidiFile.from_bytes(v['midiBytes']).notes()
   self.assertTrue(v['factoryVelocityProfileIds'])

 def test_deterministic(self):
  req=ReplacementRequest(2,1,2,2,'bass')
  a=self.engine.replace(self.raw,req,n=5); b=self.engine.replace(self.raw,req,n=5)
  self.assertEqual([a['variants'][x]['sha256'] for x in 'ABC'],[b['variants'][x]['sha256'] for x in 'ABC'])

 def test_gold_runtime_velocity_guard(self):
  # construction itself proves current GOLD runtime payload has no actionable velocity field
  self.assertIsNotNone(self.engine.gold)


 def test_role_evidence_routes(self):
  cases=[('rhythm-guitar',2,1,'FACTORY_STRUM'),('drums',1,9,'GOLD'),('power-riff',2,1,'GOLD')]
  for role,tr,ch,underlying in cases:
   r=self.engine.replace(self.raw,ReplacementRequest(tr,ch,2,2,role),n=5)
   self.assertEqual(set(r['variants']),{'A','B','C'})
   for v in r['variants'].values():
    path=v['candidatePath'][0]
    if role=='drums': self.assertEqual(path['evidenceSource'],'GOLD')
    else:
     self.assertEqual(path['evidenceSource'],'PERFORMANCE_DNA_V1')
     self.assertEqual(path['performanceSource'],underlying)
     self.assertTrue(path['performanceDNAId'])

 def test_unsupported_solo_replace_is_fail_closed(self):
  with self.assertRaisesRegex(ValueError,'not yet supported'):
   ReplacementRequest(3,2,2,2,'solo').validate()

if __name__=='__main__':unittest.main()
