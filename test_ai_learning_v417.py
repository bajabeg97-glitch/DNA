from __future__ import annotations
import sys, tempfile, unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from dna_midi_studio.ai_learning.model import DNAReconstructionNet,ModelConfig
from dna_midi_studio.ai_learning.trainer import LearningTrainer,TrainingConfig
from dna_midi_studio.ai_learning.inference import NeuralInferenceEngine
from dna_midi_studio.ai_learning.inpainting import GenerateSelectEngine

class AI417Tests(unittest.TestCase):
    def test_model_has_mask_and_defect_but_no_velocity(self):
        m=DNAReconstructionNet(ModelConfig(d_model=32,nhead=4,layers=1,ff_dim=64,embedding_dim=16))
        names=' '.join(n for n,_ in m.named_modules()).lower()
        self.assertIn('mask_emb',names); self.assertIn('defect_head',names); self.assertNotIn('velocity',names)
    def test_corruption_masks_only_present_and_preserves_velocity_absence(self):
        t=LearningTrainer(training_config=TrainingConfig(mask_probability=.5))
        e=np.zeros((2,8,4),dtype=np.int64); e[:,:4,3]=1; e[:,:4,0]=np.arange(4); e[:,:4,1]=4; e[:,:4,2]=64
        import torch
        inp,target,mask,defect=t._corrupt(torch.from_numpy(e),True)
        self.assertTrue((mask[:,4:]==0).all()); self.assertTrue((target[...,3]==torch.from_numpy(e[...,3])).all())
        self.assertEqual(inp.shape[-1],4)
    def test_trained_checkpoint_infill_preserves_unmasked(self):
        engine=NeuralInferenceEngine(ROOT/'models/dna-reconstructor-v2')
        z=np.load(ROOT/'learning_data/learning_dataset_v1.npz'); e=z['events'][:1].copy(); mask=np.zeros(e.shape[:2],dtype=np.int64)
        present=np.where(e[0,:,3]==1)[0]; self.assertGreater(len(present),2); mask[0,present[1]]=1
        r=engine.infill(z['features'][:1],e,z['roles'][:1],z['meters'][:1],z['sections'][:1],z['sources'][:1],mask,0)
        self.assertTrue(r['preservedUnmasked']); self.assertEqual(r['events'].shape,e.shape)
    def test_generate_select_rejects_invalid(self):
        engine=NeuralInferenceEngine(ROOT/'models/dna-reconstructor-v2'); gs=GenerateSelectEngine(engine)
        z=np.load(ROOT/'learning_data/learning_dataset_v1.npz'); e=z['events'][:1].copy(); mask=np.zeros(e.shape[:2],dtype=np.int64); mask[0,0]=1
        calls={'n':0}
        def val(ev): calls['n']+=1; return {'ok':calls['n']!=2,'test':'fixture'}
        r=gs.generate(z['features'][:1],e,z['roles'][:1],z['meters'][:1],z['sections'][:1],z['sources'][:1],mask,3,val)
        self.assertEqual(calls['n'],3); self.assertTrue(all(x!='B' for x in r['selected']))

if __name__=='__main__': unittest.main()
