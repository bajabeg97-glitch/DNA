from dna_midi_studio.midi import MidiFile, MidiTrack, MidiEvent
from dna_midi_studio.structural_recovery import audit_structural, remove_true_orphan_noteoffs

def ev(t,o,status,data): return MidiEvent(t,o,'channel',status=status,data=bytes(data))

def test_clean_pair_passes():
    m=MidiFile(1,96,[MidiTrack([ev(0,0,0x90,[60,90]),ev(96,1,0x80,[60,0])])])
    a=audit_structural(m); assert a.clean and not a.blocks_musical_reconstruction

def test_orphan_is_safe_remove_only():
    m=MidiFile(1,96,[MidiTrack([ev(0,0,0x80,[60,0]),ev(10,1,0x90,[62,90]),ev(20,2,0x80,[62,0])])])
    a=audit_structural(m); assert a.issues[0].kind=='ORPHAN_NOTE_OFF'; assert not a.blocks_musical_reconstruction
    m,n=remove_true_orphan_noteoffs(m); assert n==1; assert audit_structural(m).clean

def test_zero_duration_blocks_and_is_not_shifted():
    m=MidiFile(1,96,[MidiTrack([ev(10,0,0x90,[60,90]),ev(10,1,0x80,[60,0])])])
    a=audit_structural(m); assert a.blocks_musical_reconstruction
    m,n=remove_true_orphan_noteoffs(m); assert n==0
    assert audit_structural(m).issues[0].kind=='NON_POSITIVE_DURATION'

def test_dangling_blocks():
    m=MidiFile(1,96,[MidiTrack([ev(10,0,0x90,[60,90])])])
    assert audit_structural(m).blocks_musical_reconstruction

def test_prepare_removes_orphan_and_allows_clean_pipeline_input():
    from dna_midi_studio.structural_recovery import prepare_for_musical_reconstruction
    m=MidiFile(1,96,[MidiTrack([ev(0,0,0x80,[60,0]),ev(10,1,0x90,[62,90]),ev(20,2,0x80,[62,0])])])
    cleaned, report=prepare_for_musical_reconstruction(m.to_bytes())
    assert report['safeRemovedOrphanNoteOffs']==1
    assert cleaned.notes()[0].pitch==62

def test_prepare_blocks_zero_duration():
    import pytest
    from dna_midi_studio.structural_recovery import prepare_for_musical_reconstruction
    from dna_midi_studio.midi import MidiFormatError
    m=MidiFile(1,96,[MidiTrack([ev(10,0,0x90,[60,90]),ev(10,1,0x80,[60,0])])])
    with pytest.raises(MidiFormatError, match='Structural recovery required'):
        prepare_for_musical_reconstruction(m.to_bytes())
