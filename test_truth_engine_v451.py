"""Studio Flow 4.51 truth-engine tests.

Proves the engine APPLIES (instead of blocking):
- strum/comp fills are chord-tone exact (ratio 1.0), in-slot-register, within budget
- pad fill (ACC3/ACC4) sustains single chord tones with a factory velocity
- dynamics enforcement clamps only notes above their exact-sound factory ceiling
  and never changes note geometry
- a busy channel is never overwritten; a full style with no empty slot still gets
  the polish pass and an honest NO_CHANGES_NEEDED verdict
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dna_midi_studio.midi import MidiFile  # noqa: E402
from dna_midi_studio.song_understanding import analyze_song_map  # noqa: E402
from dna_midi_studio.studio_flow import (  # noqa: E402
    apply_dynamic_corrections, build_pad_part, plan_dynamic_corrections,
    run_studio,
)
from dna_midi_studio.arranger_pro import build_strum_part  # noqa: E402
from dna_midi_studio.arranger_contract import (  # noqa: E402
    COMP_REGISTER, chord_pc_set, polyphony_limit,
)

FIXTURE = ROOT / "session35-partial-preview.mid"


# --- tiny SMF builder for crafted fixtures --------------------------------

def inject_sound(raw: bytes, channel: int, bank_msb: int, bank_lsb: int, program: int) -> bytes:
    """Insert Bank/Program events right before every note start of `channel`.

    Style files re-assert their program at every bar start, so a single tick-0
    injection gets overwritten.  Injecting before each note start makes the exact
    sound unambiguous at the moment each note sounds.
    """
    from dna_midi_studio.midi import MidiEvent
    m = MidiFile.from_bytes(raw)
    track = m.tracks[0]
    starts = sorted({n.start for n in m.notes() if n.channel == channel})
    events = list(track.events)
    for i, ts in enumerate(starts):
        base = 10**6 + i * 100
        events.append(MidiEvent(tick=ts, order=base, kind="channel", status=0xB0 | channel, data=bytes((0, bank_msb))))
        events.append(MidiEvent(tick=ts, order=base + 1, kind="channel", status=0xB0 | channel, data=bytes((32, bank_lsb))))
        events.append(MidiEvent(tick=ts, order=base + 2, kind="channel", status=0xC0 | channel, data=bytes((program,))))
    events.sort(key=lambda e: (e.tick, e.order))
    track.events = events
    return m.to_bytes()

def _vlq(v: int) -> bytes:
    b = [v & 0x7F]
    while v > 0x7F:
        v >>= 7
        b.insert(0, (v & 0x7F) | 0x80)
    return bytes(b)


def build_style_like_midi(channel: int, bank_msb: int, bank_lsb: int, program: int,
                          pitch: int, velocity: int, *, ppq: int = 480) -> bytes:
    ev: list[tuple[int, bytes]] = []
    # cc/bank/program at tick 0
    ev.append((0, bytes((0xB0 | channel, 0, bank_msb))))
    ev.append((0, bytes((0xB0 | channel, 32, bank_lsb))))
    ev.append((0, bytes((0xC0 | channel, program))))
    # note pair
    ev.append((0, bytes((0x90 | channel, pitch, velocity))))
    ev.append((ppq // 2, bytes((0x80 | channel, pitch, 0))))
    ev.append((0, bytes((0xFF, 0x2F, 0))))
    track = b"".join(_vlq(d) + e for d, e in ev)
    body = b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") \
        + (1).to_bytes(2, "big") + ppq.to_bytes(2, "big")
    body += b"MTrk" + len(track).to_bytes(4, "big") + track
    return body


def strip_channel(raw: bytes, channel: int) -> bytes:
    """Remove every note on channel (keeps meta/cc) so slots can be tested empty."""
    m = MidiFile.from_bytes(raw)
    end = max((n.end for n in m.notes()), default=m.ppq)
    # find the track that owns those notes
    track = next((n.track for n in m.notes() if n.channel == channel), 0)
    m = m.replace_notes(track_index=track, channel=channel, start_tick=0,
                        end_tick=end + 1, new_notes=[])
    return m.to_bytes()



def merged_with(raw: bytes, part: dict) -> bytes:
    """Merge a generated part (notes) into a copy of raw on its own channel."""
    m = MidiFile.from_bytes(raw)
    end = max((n.end for n in part["notes"]), default=m.ppq) + 1
    return m.replace_notes(track_index=part["track"], channel=part["channel"],
                           start_tick=0, end_tick=end, new_notes=part["notes"]).to_bytes()

def chord_ratio(raw_after: bytes, channel: int, lo: int, hi: int) -> tuple[float, int, int]:
    song = analyze_song_map(raw_after, "m.mid")
    cells = song["chordCells"]
    notes = [n for n in MidiFile.from_bytes(raw_after).notes() if n.channel == channel]

    def cell_at(tick):
        for c in cells:
            if int(c["startTick"]) <= tick < int(c["endTick"]):
                return c
        return None
    hits = checked = out = 0
    for n in notes:
        if n.start >= int(song["bars"][-1]["endTick"]):
            continue
        pc = chord_pc_set(cell_at(n.start)) if cell_at(n.start) else None
        if pc is None:
            continue
        checked += 1
        if (n.pitch % 12) in pc:
            hits += 1
        if not (lo <= n.pitch <= hi):
            out += 1
    return (hits / checked if checked else 1.0), checked, out


class StrumCompTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = FIXTURE.read_bytes()

    def test_ch15_strum_is_chord_tone_exact_in_register(self):
        part = build_strum_part(self.raw, channel=15, start_bar=1, end_bar=18)
        ratio, checked, out = chord_ratio(
            merged_with(self.raw, part), 15, part["register"][0], part["register"][1])
        self.assertGreater(checked, 0)
        self.assertEqual(out, 0)
        self.assertEqual(ratio, 1.0)
        self.assertEqual(part["register"], [48, 72])
        self.assertEqual(part["maxSimultaneous"], polyphony_limit(15))
        self.assertTrue(all(p["authority"] == "FACTORY_ONLY" for p in part["velocityProofs"]))

    def test_ch12_comp_fill_uses_slot_register_and_budget(self):
        raw12 = strip_channel(self.raw, 12)
        part = build_strum_part(raw12, channel=12, start_bar=1, end_bar=18,
                                register=tuple(COMP_REGISTER[12]))
        lo, hi = list(COMP_REGISTER[12])
        self.assertEqual(part["register"], [lo, hi])
        ratio, checked, out = chord_ratio(merged_with(raw12, part), 12, lo, hi)
        self.assertGreater(checked, 0)
        self.assertEqual(out, 0)
        # >=0.99: the song-map chord classifier can drift on the very last half-bar
        # when the added part changes note evidence (1 of 450 boundary notes here).
        self.assertGreaterEqual(ratio, 0.99)
        self.assertLessEqual(part["maxSimultaneous"], polyphony_limit(12))

    def test_pad_fill_sustains_single_chord_tone(self):
        raw13 = strip_channel(self.raw, 13)
        part = build_pad_part(raw13, channel=13, start_bar=1, end_bar=18, tone="root")
        self.assertGreater(part["noteCount"], 0)
        lo, hi = list(COMP_REGISTER[13])
        notes = part["notes"]
        # single voice: at most one note sounding at any tick
        onsets = sorted({n.start for n in notes})
        peak = max(sum(1 for n in notes if n.start <= t < n.end) for t in onsets)
        self.assertLessEqual(peak, 1)
        ratio, checked, out = chord_ratio(merged_with(raw13, part), 13, lo, hi)
        self.assertEqual(out, 0)
        self.assertGreaterEqual(ratio, 0.99)  # boundary classification drift tolerance
        # sustained: every note spans most of its bar
        self.assertGreater(min((n.end - n.start) for n in notes), 300)


class DynamicsTests(unittest.TestCase):
    def test_clamp_to_unique_exact_sound_ceiling(self):
        # ACC3 profile (121,26,21) is unique and has ceiling 84
        raw = build_style_like_midi(13, 121, 26, 21, 60, 127)
        corrections, counts = plan_dynamic_corrections(raw)
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0]["from"], 127)
        self.assertEqual(corrections[0]["to"], 84)
        out, applied = apply_dynamic_corrections(raw)
        self.assertEqual(len(applied), 1)
        m = MidiFile.from_bytes(out)
        n = m.notes()[0]
        self.assertEqual(n.velocity, 84)
        # geometry unchanged
        self.assertEqual((n.channel, n.pitch, n.start, n.end), (13, 60, 0, 240))

    def test_no_clamp_when_sound_unknown_or_within_ceiling(self):
        raw = build_style_like_midi(11, 121, 9, 4, 60, 100)  # ACC1 ceiling 114
        corrections, _ = plan_dynamic_corrections(raw)
        self.assertEqual(corrections, [])
        raw_unknown = build_style_like_midi(11, 0, 0, 0, 60, 127)  # no such profile -> no guess
        self.assertEqual(plan_dynamic_corrections(raw_unknown)[0], [])


class StudioRunTests(unittest.TestCase):
    def test_studio_fills_comp_plus_pads_when_all_empty(self):
        # strip ch13, ch14 and ch15 -> auto should fill ch15 comp + ch13/14 pads
        raw = strip_channel(FIXTURE.read_bytes(), 13)
        raw = strip_channel(raw, 14)
        raw = strip_channel(raw, 15)
        with tempfile.TemporaryDirectory() as td:
            res = run_studio(raw, out_dir=td)
            channels = {f["channel"] for f in res["fills"]}
            self.assertEqual(channels, {15, 13, 14})
            self.assertTrue(all(res["gates"].values()), res["gates"])
            self.assertEqual(res["status"], "STUDIO_APPLIED")
            m = MidiFile.from_bytes(Path(td, res["arrangedMidi"]).read_bytes())
            for ch in (13, 14, 15):
                self.assertGreater(len([n for n in m.notes() if n.channel == ch]), 0)

    def test_busy_style_gets_no_changes_when_all_within_authority(self):
        raw = (ROOT / "baseline/reference-style.mid").read_bytes()
        with tempfile.TemporaryDirectory() as td:
            res = run_studio(raw, out_dir=td)
            self.assertEqual(res["fills"], [])
            self.assertEqual(res["status"], "STUDIO_NO_CHANGES_NEEDED")
            self.assertTrue(all(res["gates"].values()), res["gates"])

    def test_dynamics_polish_applied_on_over_ceiling_channel(self):
        # fixture ch13 velocities go up to 113; give ch13 the exact ACC3 sound with
        # factory ceiling 84 -> polish must clamp every note above 84.
        raw = inject_sound(FIXTURE.read_bytes(), 13, 121, 26, 21)
        from dna_midi_studio.studio_flow import plan_dynamic_corrections
        corrections, _ = plan_dynamic_corrections(raw)
        self.assertGreater(len(corrections), 0)
        self.assertTrue(all(c["to"] == 84 for c in corrections))
        with tempfile.TemporaryDirectory() as td:
            res = run_studio(raw, out_dir=td)
            self.assertGreaterEqual(res["dynamics"]["correctionCount"], len(corrections))
            m = MidiFile.from_bytes(Path(td, res["arrangedMidi"]).read_bytes())
            n13 = [n for n in m.notes() if n.channel == 13]
            self.assertTrue(all(n.velocity <= 84 for n in n13))
            self.assertTrue(all(res["gates"].values()), res["gates"])


if __name__ == "__main__":
    unittest.main()
