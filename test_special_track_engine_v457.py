"""Special Track Engine 4.57 tests — echo/terca optimization.

Covers the v4.13-era executable spec (see test_echo_terca_engine_v413.py)
plus engine guarantees: main part never modified, aux velocity only lowered,
no controller/trigger events ever appended.
"""
import unittest
from collections import Counter

from dna_midi_studio.special_track_engine import (
    ECHO_VELOCITY_RATIO, optimize_existing_echo_terca,
)

PROFILE = {"samples": 100, "register": {"low": 48, "high": 96},
           "curve": {"floor": 45, "soft": 58, "lowMid": 70, "optimal": 82,
                     "highMid": 94, "strong": 106, "ceiling": 118}}
CMAJ = {0: {"root": 0, "quality": "major", "pitchClasses": {0, 4, 7},
            "confidence": 0.9, "score": 1}}


def note(pitch, start, end, vel=100, key="solo-prof"):
    on = {"tick": start, "order": start * 2 + 1, "kind": "channel",
          "command": 9, "channel": 0, "data": [pitch, vel], "remove": False}
    off = {"tick": end, "order": end * 2 + 2, "kind": "channel",
           "command": 8, "channel": 0, "data": [pitch, 0], "remove": False}
    return {"pitch": pitch, "channel": 0, "on": on, "off": off,
            "instrumentKey": key}


def track(index, name):
    return {"index": index, "channel": 0,
            "events": [{"tick": 0, "order": 0, "kind": "meta", "metaType": 3,
                        "payload": name.encode()}], "endTick": 4000}


def group(role, conf, trk, notes, name):
    return {"role": role, "confidence": conf, "track": trk, "channel": 0,
            "notes": notes, "trackName": name}


def run(groups, harmony=None, profiles=None):
    return optimize_existing_echo_terca(
        groups, harmony or {}, 1920, 480, profiles or {"solo-prof": PROFILE},
        {}, Counter())


class LegacySpecComplianceTests(unittest.TestCase):
    def test_echo_repair_aligns_quiets_and_shortens(self):
        main = [note(60, 0, 180, 105), note(62, 480, 660, 100),
                note(64, 960, 1140, 98), note(65, 1440, 1620, 96)]
        echo = [note(60, 245, 430, 90), note(62, 725, 910, 88),
                note(64, 1205, 1390, 85), note(65, 1685, 1870, 84)]
        r = run([group("solo", .95, track(0, "Main Solo"), main, "Main Solo"),
                 group("echo", .99, track(1, "Echo"), echo, "Echo")])
        self.assertEqual(r[0]["kind"], "echo")
        self.assertIn(r[0]["mode"], ("PRESERVE", "REPAIR"))
        self.assertEqual(echo[0]["on"]["tick"], 240)  # snapped to half-beat
        self.assertLess(echo[0]["on"]["data"][1], main[0]["on"]["data"][1])
        self.assertLess(echo[0]["off"]["tick"] - echo[0]["on"]["tick"],
                        main[0]["off"]["tick"] - main[0]["on"]["tick"])

    def test_terca_repair_uses_diatonic_third_and_main_timing(self):
        main = [note(60, 0, 240, 100), note(62, 480, 720, 100),
                note(64, 960, 1200, 100)]
        third = [note(64, 5, 250, 90), note(65, 485, 730, 90),
                 note(68, 965, 1210, 90)]  # last is wrong: E->G# vs diatonic G
        r = run([group("solo", .95, track(0, "Main Solo"), main, "Main Solo"),
                 group("third", .99, track(2, "Terca"), third, "Terca")], CMAJ)
        self.assertEqual(r[0]["kind"], "third")
        self.assertEqual([n["on"]["tick"] for n in third[:2]], [0, 480])
        self.assertEqual(third[0]["pitch"], 64)
        self.assertEqual(third[1]["pitch"], 65)
        self.assertEqual(third[2]["pitch"], 67)

    def test_low_similarity_echo_rebuilds_from_main(self):
        main = [note(60, 0, 220, 100), note(62, 480, 700, 100),
                note(64, 960, 1180, 100), note(65, 1440, 1660, 100)]
        bad = [note(70, 100, 200, 100), note(71, 700, 800, 100),
               note(72, 1300, 1400, 100)]
        echo_t = track(1, "Echo")
        r = run([group("solo", .95, track(0, "Main Solo"), main, "Main Solo"),
                 group("echo", .99, echo_t, bad, "Echo")])
        self.assertEqual(r[0]["mode"], "REBUILD")
        self.assertTrue(all(n["on"]["remove"] for n in bad))
        generated = [e for e in echo_t["events"]
                     if e.get("kind") == "channel" and e.get("command") == 9]
        self.assertTrue(generated)
        for e in generated:
            self.assertLessEqual(e["data"][1], PROFILE["curve"]["ceiling"])
        controllers = [e for e in echo_t["events"]
                       if e.get("kind") == "channel" and e.get("command") == 0xB0]
        self.assertEqual(controllers, [])

    def test_unnamed_aux_track_inferred_as_echo(self):
        main = [note(60, 0, 180, 100), note(62, 480, 660, 100),
                note(64, 960, 1140, 100), note(65, 1440, 1620, 100)]
        aux = [note(60, 240, 360, 75), note(62, 720, 840, 75),
               note(64, 1200, 1320, 75), note(65, 1680, 1800, 75)]
        r = run([group("solo", .88, track(0, "Track 1"), main, "Track 1"),
                 group("solo", .78, track(1, "Track 2"), aux, "Track 2")])
        self.assertTrue(r)
        self.assertEqual(r[0]["kind"], "echo")
        self.assertTrue(r[0]["inferred"])


class EngineGuaranteeTests(unittest.TestCase):
    def test_main_part_never_modified(self):
        main = [note(60, 0, 180, 105), note(62, 480, 660, 100)]
        echo = [note(60, 245, 430, 127), note(62, 725, 910, 100)]
        before = [(n["pitch"], n["on"]["tick"], n["on"]["data"][1],
                   n["off"]["tick"] - n["on"]["tick"]) for n in main]
        run([group("solo", .95, track(0, "Main Solo"), main, "Main Solo"),
             group("echo", .99, track(1, "Echo"), echo, "Echo")])
        after = [(n["pitch"], n["on"]["tick"], n["on"]["data"][1],
                  n["off"]["tick"] - n["on"]["tick"]) for n in main]
        self.assertEqual(before, after)

    def test_echo_velocity_ratio_respected(self):
        main = [note(60, 0, 200, 100)]
        echo = [note(60, 250, 400, 127)]
        run([group("solo", .95, track(0, "Main Solo"), main, "Main Solo"),
             group("echo", .99, track(1, "Echo"), echo, "Echo")])
        target = max(1, int(round(100 * ECHO_VELOCITY_RATIO)))
        self.assertEqual(echo[0]["on"]["data"][1], target)

    def test_terca_without_harmony_evidence_is_preserved(self):
        main = [note(60, 0, 200, 100)]
        third = [note(64, 250, 400, 90)]
        r = run([group("solo", .95, track(0, "Main Solo"), main, "Main Solo"),
                 group("third", .99, track(2, "Terca"), third, "Terca")])
        self.assertEqual(r[0]["mode"], "PRESERVE")
        self.assertEqual(third[0]["pitch"], 64)  # no pitch authority without key

    def test_rebuild_velocity_capped_by_profile_ceiling(self):
        low_profile = {"samples": 10, "curve": {"ceiling": 40}}
        main = [note(60, 0, 200, 127)]
        bad = [note(70, 100, 180, 100)]
        echo_t = track(1, "Echo")
        optimize_existing_echo_terca(
            [group("solo", .95, track(0, "Main Solo"), main, "Main Solo"),
             group("echo", .99, echo_t, bad, "Echo")], {}, 1920, 480,
            {"solo-prof": low_profile}, {}, Counter())
        generated = [e for e in echo_t["events"]
                     if e.get("kind") == "channel" and e.get("command") == 9]
        self.assertTrue(generated)
        self.assertTrue(all(e["data"][1] <= 40 for e in generated))


    def test_polyphonic_consistent_echo_is_not_rebuilt(self):
        # chordal main part (multiple onsets per tick) with a perfect +240 echo
        main = [note(60, 0, 180, 100), note(64, 0, 180, 100),  # C chord
                note(60, 240, 420, 100),
                note(62, 480, 660, 100), note(65, 480, 660, 100)]
        aux = []
        for n in main:
            s = n["on"]["tick"] + 240
            v = max(1, int(n["on"]["data"][1] * ECHO_VELOCITY_RATIO))
            aux.append(note(n["pitch"], s, s + 120, v))
        r = run([group("solo", .9, track(0, "Main Solo"), main, "Main Solo"),
                 group("echo", .99, track(1, "Echo"), aux, "Echo")])
        self.assertEqual(r[0]["kind"], "echo")
        self.assertIn(r[0]["mode"], ("PRESERVE", "REPAIR"))
        self.assertNotEqual(r[0]["mode"], "REBUILD")


if __name__ == "__main__":
    unittest.main()
