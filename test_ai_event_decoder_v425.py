from __future__ import annotations
import unittest, sys, json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from dna_midi_studio.ai_learning.event_decoder import EventDecoderInference
from dna_midi_studio.ai_learning.track_replacement import TrackReplacementEngine, ReplacementRequest
from dna_midi_studio.session19_fixture import build_benchmark_case
from dna_midi_studio.song_understanding import analyze_song_map
from dna_midi_studio.midi import MidiFile

class EventDecoder425Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.data=ROOT/'data'/'ai_event_decoder'; cls.raw=(ROOT/'artifacts/session35-partial-preview.mid').read_bytes()
 def test_manifest_source_disjoint_and_no_velocity(self):
  m=json.loads((self.data/'event_decoder_manifest.json').read_text())
  self.assertFalse(m['velocityUsed']); self.assertEqual(m['songs'],163); self.assertGreater(m['samples'],10000)
  z=np.load(self.data/'event_decoder_v1.npz'); songs=z['song_ids']; split=z['split']
  for sid in np.unique(songs): self.assertEqual(len(set(split[songs==sid].tolist())),1)
 def test_checkpoint_has_no_velocity_head(self):
  z=np.load(self.data/'event_decoder_model_v1.npz'); self.assertFalse(any('velocity' in k.lower() for k in z.files))
 def test_full_song_event_generation_is_deterministic(self):
  e=EventDecoderInference(self.data/'event_decoder_model_v1.npz'); ctx,ch=e.input_from_midi(self.raw,6,1)
  a=e.generate(ctx,ch,1,0); b=e.generate(ctx,ch,1,0); self.assertEqual(a,b); self.assertTrue(a)
  self.assertTrue(all(0<=r[0]<=95 and 1<=r[1]<=95 and 0<=r[2]<=255 for r in a))
 def test_replacement_engine_loads_event_decoder(self):
  eng=TrackReplacementEngine(ROOT/'models/dna-reconstructor-v2',ROOT/'learning_data',ROOT/'data'); self.assertIsNotNone(eng.event_decoder)
  song=analyze_song_map(self.raw,'x.mid'); bar=song['bars'][5]
  rows=eng._event_decoder_variants(self.raw,ReplacementRequest(2,1,6,6,'bass'),bar); self.assertGreaterEqual(len(rows),1); self.assertEqual(rows[0]['evidenceSource'],'FULL_SONG_EVENT_DECODER_V1')
 def test_replacement_outputs_remain_parseable(self):
  eng=TrackReplacementEngine(ROOT/'models/dna-reconstructor-v2',ROOT/'learning_data',ROOT/'data')
  r=eng.replace(build_benchmark_case(0).midi,ReplacementRequest(2,1,2,2,'bass'),n=8)
  for k in 'ABC': MidiFile.from_bytes(r['variants'][k]['midiBytes']).notes(); self.assertFalse(r['variants'][k]['neuralVelocityUsed'])

if __name__=='__main__':unittest.main()
