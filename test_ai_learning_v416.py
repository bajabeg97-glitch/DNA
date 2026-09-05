from __future__ import annotations
import json, sys, tempfile, unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from dna_midi_studio.ai_learning.authority import DEFAULT_POLICY
from dna_midi_studio.ai_learning.dataset import LearningDatasetBuilder, FEATURE_NAMES
from dna_midi_studio.ai_learning.model import DNAReconstructionNet, ModelConfig
from dna_midi_studio.ai_learning.inference import NeuralInferenceEngine

class AILearningTests(unittest.TestCase):
    def test_authority_forbids_gold_velocity_schema(self):
        with self.assertRaises(ValueError): DEFAULT_POLICY.validate_training_schema(['density','velocityMean'])
        DEFAULT_POLICY.validate_training_schema(FEATURE_NAMES)
    def test_built_dataset_has_no_velocity_feature(self):
        m=json.loads((ROOT/'learning_data/dataset_manifest.json').read_text())
        self.assertGreater(m['samples'],10000)
        self.assertFalse(any('velocity' in x.lower() for x in m['feature_names']))
        self.assertTrue(m['authority']['factory_velocity_only'])
    def test_split_is_disjoint_and_complete(self):
        z=np.load(ROOT/'learning_data/learning_dataset_v1.npz'); split=z['split']
        self.assertEqual(len(split), int((split==0).sum()+(split==1).sum()+(split==2).sum()))
        self.assertGreater((split==2).sum(),0)
    def test_model_has_no_velocity_head(self):
        model=DNAReconstructionNet(ModelConfig(d_model=32,nhead=4,layers=1,ff_dim=64,embedding_dim=16))
        names=' '.join(n for n,_ in model.named_modules()).lower()
        self.assertNotIn('velocity',names)
    def test_smoke_checkpoint_loads_and_infers(self):
        engine=NeuralInferenceEngine(ROOT/'models/dna-reconstructor-v1')
        z=np.load(ROOT/'learning_data/learning_dataset_v1.npz')
        out=engine.encode_batch(z['features'][:2],z['events'][:2],z['roles'][:2],z['meters'][:2],z['sections'][:2],z['sources'][:2])
        self.assertEqual(out['embedding'].shape[0],2)
        self.assertEqual(out['status'],'ADVISORY_REQUIRES_HARD_VALIDATION')
        self.assertTrue(np.isfinite(out['quality']).all())

if __name__=='__main__': unittest.main()
