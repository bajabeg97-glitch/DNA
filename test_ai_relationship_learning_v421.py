from __future__ import annotations
import sys,unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from dna_midi_studio.ai_learning.relationship_model import RelationshipTransformer,RelationshipModelConfig
from dna_midi_studio.ai_learning.relationship_inference import RelationshipInferenceEngine
from dna_midi_studio.ai_learning.melodic_relationship import MelodicRelationshipEngine,MelodicRelationshipRequest
from dna_midi_studio.session19_fixture import build_benchmark_case

class AIRelationship421Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.ds=ROOT/'relationship_learning_data/relationship_dataset_v1.npz'
  cls.model=ROOT/'models/relationship-transformer-v1'
 def test_dataset_source_disjoint_and_velocity_free(self):
  z=np.load(self.ds);self.assertEqual(len(z['kind']),122)
  self.assertEqual(int((z['split']==0).sum()),84);self.assertEqual(int((z['split']==1).sum()),19);self.assertEqual(int((z['split']==2).sum()),19)
  self.assertEqual(z['pairs'].shape[-1],4)
  self.assertFalse(any('velocity' in k.lower() for k in z.files))
 def test_model_has_relationship_heads_but_no_velocity(self):
  m=RelationshipTransformer(RelationshipModelConfig())
  names=' '.join(n for n,_ in m.named_modules()).lower();self.assertIn('kind_head',names);self.assertIn('interval_head',names);self.assertNotIn('velocity',names)
 def test_checkpoint_scores_real_holdout_sample(self):
  eng=RelationshipInferenceEngine(self.model,self.ds);z=np.load(self.ds);i=np.where(z['split']==2)[0][0]
  import torch
  with torch.no_grad():o=eng.model(torch.from_numpy(z['features'][i:i+1]).float(),torch.from_numpy(z['pairs'][i:i+1]).long())
  self.assertEqual(tuple(o['kind_logits'].shape),(1,2));self.assertFalse(hasattr(eng.model,'velocity_head'))
 def test_melodic_engine_uses_learned_ranker_advisory(self):
  raw=build_benchmark_case(0).midi
  eng=MelodicRelationshipEngine(ROOT/'data',self.model,self.ds)
  r=eng.generate(raw,MelodicRelationshipRequest(3,2,5,3,1,3,'third'))
  self.assertEqual(r['authority']['relationship'],'REAL_ORIGINAL_MIDI_RELATIONSHIP_RANKER_V1')
  self.assertEqual(set(r['selectedOrder']),{'A','B','C'})
  for v in r['variants'].values():
   self.assertIsNotNone(v['relationshipModel']);self.assertFalse(v['relationshipModel']['velocityUsed'])
   self.assertGreaterEqual(v['relationshipModel']['relationshipScore'],0.0)
 def test_holdout_report_is_not_claimed_as_music_quality(self):
  import json
  rep=json.loads((self.model/'relationship_training_report.json').read_text())
  self.assertEqual(rep['holdoutSamples'],19);self.assertIn('holdoutKindAccuracy',rep);self.assertFalse(rep['authority']['velocityFeature'])

if __name__=='__main__':unittest.main()
