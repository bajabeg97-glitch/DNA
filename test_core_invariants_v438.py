from dna_midi_studio.core_invariants import capture, verify
from dna_midi_studio.midi import MidiEvent, MidiFile, MidiTrack


def _note_events(pitch=60, start=0, end=240, velocity=90, channel=0):
    return [
        MidiEvent(start,0,"channel",0x90 | channel,bytes([pitch,velocity]),None),
        MidiEvent(end,1,"channel",0x80 | channel,bytes([pitch,0]),None),
    ]


def test_outside_target_change_is_rejected():
    m=MidiFile(1,480,[MidiTrack(_note_events(60,0,240,90,0)),MidiTrack(_note_events(65,0,240,90,1))])
    snap=capture(m,track_index=0,channel=0,start_tick=0,end_tick=480)
    bad=MidiFile(1,480,[m.tracks[0],MidiTrack(_note_events(67,0,240,90,1))])
    r=verify(snap,bad)
    assert not r["passed"] and "OUTSIDE_TARGET_NOTE_CHANGED" in r["issues"]


def test_target_note_change_is_allowed_by_core_guard():
    m=MidiFile(1,480,[MidiTrack(_note_events())])
    snap=capture(m,track_index=0,channel=0,start_tick=0,end_tick=480)
    changed=MidiFile(1,480,[MidiTrack(_note_events(62,0,300,90,0))])
    assert verify(snap,changed)["passed"]
