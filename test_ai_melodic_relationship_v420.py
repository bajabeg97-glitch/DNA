from __future__ import annotations
import sys,unittest,tempfile,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from dna_midi_studio.ai_learning import MelodicRelationshipEngine,MelodicRelationshipRequest
from dna_midi_studio.session19_fixture import build_benchmark_case
from dna_midi_studio.midi import MidiFile

class AIMelodicRelationship420Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.raw=build_benchmark_case(0).midi;cls.engine=MelodicRelationshipEngine(ROOT/'data')

 def test_third_replace_from_source_solo_factory_velocity(self):
  r=self.engine.generate(self.raw,MelodicRelationshipRequest(3,2,5,3,1,3,'third'))
  self.assertEqual(set(r['variants']),{'A','B','C'})
  self.assertIn('FACTORY_ONLY',r['authority']['velocity'])
  for v in r['variants'].values():
   self.assertGreater(v['noteCount'],0);self.assertTrue(v['factoryVelocityProfileIds'])
   self.assertFalse(v['goldVelocityUsed']);self.assertFalse(v['neuralVelocityUsed']);self.assertTrue(v['protectedEventsPreserved'])
   MidiFile.from_bytes(v['midiBytes']).notes()
   self.assertTrue(all(p['interval'] in (3,4) for p in v['proof']))

 def test_echo_replace_nonrecursive_and_quieter_factory_curve(self):
  r=self.engine.generate(self.raw,MelodicRelationshipRequest(3,2,5,3,1,3,'echo'))
  hashes=[]
  for v in r['variants'].values():
   hashes.append(v['sha256']);self.assertGreater(v['noteCount'],0);self.assertTrue(v['factoryVelocityProfileIds'])
   self.assertTrue(all(p['delayTicks']>0 and 0<p['durationRatio']<1 for p in v['proof']))
  self.assertEqual(len(set(hashes)),3)

 def test_solo_performance_preserves_pitch_and_velocity(self):
  r=self.engine.generate(self.raw,MelodicRelationshipRequest(3,2,3,2,1,3,'solo-performance'))
  source=[n for n in MidiFile.from_bytes(self.raw).notes() if n.track==3 and n.channel==2 and n.start<5760]
  for v in r['variants'].values():
   out=[n for n in MidiFile.from_bytes(v['midiBytes']).notes() if n.track==3 and n.channel==2 and n.start<5760]
   self.assertEqual([n.pitch for n in source],[n.pitch for n in out]);self.assertEqual([n.velocity for n in source],[n.velocity for n in out])
   self.assertEqual(v['factoryVelocityProfileIds'],[])

 def test_deterministic(self):
  q=MelodicRelationshipRequest(3,2,5,3,1,3,'third')
  a=self.engine.generate(self.raw,q);b=self.engine.generate(self.raw,q)
  self.assertEqual([a['variants'][x]['sha256'] for x in 'ABC'],[b['variants'][x]['sha256'] for x in 'ABC'])

 def test_invalid_full_solo_replace_fails_closed(self):
  with self.assertRaisesRegex(ValueError,'unsupported'):
   MelodicRelationshipRequest(3,2,5,3,1,2,'solo').validate()

if __name__=='__main__':unittest.main()
