from __future__ import annotations
import sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'src'))
from dna_midi_studio.ai_learning.song_inpainting import SongConditionedInpaintingEngine,SongRegionRequest
from dna_midi_studio.session19_fixture import build_benchmark_case

class AISongInpainting418Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine=SongConditionedInpaintingEngine(ROOT/'models/dna-reconstructor-v2',ROOT/'learning_data')
        cls.midi=build_benchmark_case(0).midi

    def test_real_song_bar_generate8_select3(self):
        r=self.engine.analyze_and_generate(self.midi,SongRegionRequest(2,1,2,2,'bass'),mask_ratio=.5,n=8)
        self.assertFalse(r['velocityUsedByNeuralModel'])
        self.assertEqual(len(r['bars']),1)
        sel=r['bars'][0]['selection']
        self.assertEqual(len(sel['candidates']),8)
        self.assertEqual(len(sel['selected']),3)
        self.assertTrue(all(c['hard_valid'] for c in sel['candidates']))

    def test_song_region_is_deterministic(self):
        req=SongRegionRequest(3,2,3,3,'solo')
        a=self.engine.analyze_and_generate(self.midi,req,n=8)
        b=self.engine.analyze_and_generate(self.midi,req,n=8)
        self.assertEqual(a,b)

    def test_velocity_independence_of_neural_input(self):
        from dna_midi_studio.session19_fixture import with_velocity
        req=SongRegionRequest(2,1,2,2,'bass')
        quiet=self.engine.analyze_and_generate(with_velocity(self.midi,20),req,n=2)
        loud=self.engine.analyze_and_generate(with_velocity(self.midi,120),req,n=2)
        qa=quiet['bars'][0]['selection']['candidates'][0]['events']
        qb=loud['bars'][0]['selection']['candidates'][0]['events']
        self.assertEqual(qa,qb)

    def test_repair_renders_three_real_midis_and_preserves_velocity(self):
        req=SongRegionRequest(2,1,2,2,'bass')
        r=self.engine.render_selected_variants(self.midi,req,mask_ratio=.5,n=8)
        self.assertEqual(len(r['variants']),3)
        from dna_midi_studio.midi import MidiFile
        src=MidiFile.from_bytes(self.midi)
        src_vel=[n.velocity for n in src.notes() if n.track==2 and n.channel==1 and 1920<=n.start<3840]
        for v in r['variants'].values():
            self.assertTrue(v['velocityPreserved']); self.assertTrue(v['protectedEventsPreserved'])
            out=MidiFile.from_bytes(v['midiBytes'])
            vel=[n.velocity for n in out.notes() if n.track==2 and n.channel==1 and 1920<=n.start<3840]
            self.assertEqual(vel,src_vel)

    def test_multibar_phrase_renders_abc(self):
        req=SongRegionRequest(2,1,2,3,'bass')
        r=self.engine.render_phrase_variants(self.midi,req,mask_ratio=.5,n=8)
        self.assertEqual(set(r['variants']),{'A','B','C'})
        self.assertTrue(all(len(v['candidatePath'])==2 for v in r['variants'].values()))
        self.assertTrue(all(v['velocityPreserved'] for v in r['variants'].values()))

    def test_replace_empty_is_closed_not_fabricated(self):
        with self.assertRaisesRegex(ValueError,'retrieval seed'):
            self.engine.analyze_and_generate(self.midi,SongRegionRequest(0,0,2,2,'rhythm-guitar',mode='REPLACE'))

if __name__=='__main__': unittest.main()
