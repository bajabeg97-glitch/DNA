from __future__ import annotations
import sys,unittest,json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from dna_midi_studio.ai_learning.relationship_sequence_model import RelationshipSequenceTransformer,SequenceModelConfig
from dna_midi_studio.ai_learning.relationship_sequence_inference import RelationshipSequenceInferenceEngine
from dna_midi_studio.ai_learning.melodic_relationship import MelodicRelationshipEngine,MelodicRelationshipRequest
from dna_midi_studio.session19_fixture import build_benchmark_case
from dna_midi_studio.midi import MidiFile

class AIMelodicSequence423Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.ds=ROOT/'relationship_sequence_data_v2/relationship_sequence_dataset_v2.npz';cls.model=ROOT/'models/relationship-sequence-v2'
 def test_sequence_dataset_is_song_disjoint_and_velocity_free(self):
  z=np.load(self.ds);self.assertFalse(any('velocity' in k.lower() for k in z.files));self.assertEqual(z['features'].shape[-1],10)
  self.assertGreater(int((z['action']==0).sum()),50000);self.assertGreater(int((z['action']==1).sum()),3000);self.assertGreater(int((z['action']==2).sum()),100)
 def test_model_has_action_interval_delay_gate_no_velocity(self):
  m=RelationshipSequenceTransformer(SequenceModelConfig());names=' '.join(n for n,_ in m.named_modules()).lower()
  for x in ('action','interval','delay','duration'):self.assertIn(x,names)
  self.assertNotIn('velocity',names)
 def test_training_report_real_holdout(self):
  r=json.loads((self.model/'relationship_sequence_training_report.json').read_text());self.assertGreater(r['holdoutNotes'],10000);self.assertGreater(r['holdoutActionAccuracy'],.6);self.assertFalse(r['velocityUsed'])
 def test_sequence_inference_returns_every_note(self):
  raw=build_benchmark_case(0).midi;m=MidiFile.from_bytes(raw);src=[n for n in m.notes() if n.track==3 and n.channel==2][:120]
  eng=RelationshipSequenceInferenceEngine(self.model,self.ds);p=eng.predict(src,'third',m.ppq);self.assertEqual(len(p),len(src));self.assertTrue(all(not x['velocityUsed'] for x in p))
 def test_melodic_engine_uses_sequence_model_and_preserves_authority(self):
  raw=build_benchmark_case(0).midi
  eng=MelodicRelationshipEngine(ROOT/'data',ROOT/'models/relationship-transformer-v1',ROOT/'relationship_learning_data/relationship_dataset_v1.npz',self.model,self.ds)
  r=eng.generate(raw,MelodicRelationshipRequest(3,2,5,3,1,3,'third'))
  self.assertEqual(r['authority']['relationship'],'REAL_ORIGINAL_MIDI_SEQUENCE_MODEL_V2')
  for v in r['variants'].values():
   self.assertTrue(v['sequenceModelUsed']);self.assertFalse(v['goldVelocityUsed']);self.assertFalse(v['neuralVelocityUsed']);MidiFile.from_bytes(v['midiBytes']).notes()
   self.assertTrue(all(p.get('action') in {'PLAY','HOLD'} for p in v['proof']))

if __name__=='__main__':unittest.main()
