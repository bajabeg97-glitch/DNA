from dna_midi_studio.midi import MidiFile,MidiTrack,MidiEvent
from dna_midi_studio.overlap_recovery import audit_overlaps

def ev(tick,status,d1,d2,order): return MidiEvent(tick=tick,order=order,kind='channel',status=status,data=bytes([d1,d2]))
def test_pitched_overlap_detected_before_duration_repair():
    m=MidiFile(format_type=1,ppq=480,tracks=[MidiTrack([ev(0,0x90,60,90,0),ev(100,0x90,60,90,1),ev(200,0x80,60,0,2),ev(300,0x80,60,0,3)])])
    r=audit_overlaps(m); assert r['counts']['PITCHED_SAME_NOTE_OVERLAP']==1

def test_drum_retrigger_preserved_semantically():
    m=MidiFile(format_type=1,ppq=480,tracks=[MidiTrack([ev(0,0x99,36,100,0),ev(20,0x99,36,100,1),ev(30,0x89,36,0,2),ev(40,0x89,36,0,3)])])
    r=audit_overlaps(m); assert r['counts']['DRUM_ONE_SHOT_RETRIGGER']==1
