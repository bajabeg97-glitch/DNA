"""
Arranger Contract 4.50 — single source of truth for arranger-wide facts.

Consolidates facts that previously lived duplicated across
track_replacement.py / arranger_pro.py / pa800_validator.py:

  * PA800 style channel -> role map and per-channel polyphony budgets
  * factory role vocabulary (velocity profiles) and register windows
  * factory-velocity resolution (deterministic, FACTORY_ONLY)
  * protected-channel note snapshot + reparse gate helpers
  * chord tone pitch-class resolution from song chord cells

Nothing here mutates MIDI; it only supplies evidence and hard facts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.midi import MidiFile, Note  # noqa: E402
from dna_midi_studio.pa800_validator import PA800_CHANNEL_POLYPHONY_LIMITS  # noqa: E402

# PA800 style channel semantics (0-based MIDI channels, as used repo-wide).
CHANNEL_ROLES: dict[int, str] = {
    8: "bass", 9: "drums", 10: "percussion",
    11: "accompaniment", 12: "accompaniment", 13: "accompaniment",
    14: "accompaniment", 15: "accompaniment",
}
ACC_CHANNELS: tuple[int, ...] = (11, 12, 13, 14, 15)
# PA800 Acc voices: ch13/14 are single-voice, ch15 is the 3-voice guitar slot,
# ch11/12 are 4-voice comp slots.  Taken from PA800_CHANNEL_POLYPHONY_LIMITS.
FACTORY_ROLE_BY_STYLE_ROLE: dict[str, str] = {
    "bass": "bass", "drums": "drums", "percussion": "drums",
    "accompaniment": "chords", "solo": "melody",
}
STRUM_ROLE = "rhythm-guitar"
STRUM_REGISTER: tuple[int, int] = (48, 72)      # factory rhythm-guitar voicing window
COMP_REGISTER: dict[int, tuple[int, int]] = {   # register separation across acc slots
    11: (48, 64), 12: (55, 72), 13: (60, 79), 14: (64, 84), 15: (48, 72),
}
SLOT_LABELS: dict[int, str] = {
    11: "ACC1 block comp (<=4 voices)", 12: "ACC2 block comp (<=4 voices)",
    13: "ACC3 single-voice pad", 14: "ACC4 single-voice pad", 15: "ACC5 guitar strum (<=3 voices)",
}
# role -> factory role for velocity evidence
VELOCITY_ROLE: dict[str, str] = {"bass": "bass", "drums": "drums", "percussion": "drums",
                                 "accompaniment": "chords", "rhythm-guitar": "chords",
                                 "power-riff": "chords", "solo": "melody", "melody": "melody"}

# chord quality -> semitone intervals (0 = root), used by the metrics agent.
_CHORD_INTERVALS: dict[str, tuple[int, ...]] = {
    "major": (0, 4, 7), "minor": (0, 3, 7), "diminished": (0, 3, 6),
    "augmented": (0, 4, 8), "suspended-2": (0, 2, 7), "suspended-4": (0, 5, 7),
    "dominant-7": (0, 4, 7, 10), "major-7": (0, 4, 7, 11), "minor-7": (0, 3, 7, 10),
    "minor-major-7": (0, 3, 7, 11), "diminished-7": (0, 3, 6, 9),
    "half-diminished-7": (0, 3, 6, 10), "augmented-7": (0, 4, 8, 10),
    "major-6": (0, 4, 7, 9), "minor-6": (0, 3, 7, 9), "power": (0, 7),
    "suspended-4-7": (0, 5, 7, 10), "add-9": (0, 4, 7, 14), "ninth": (0, 4, 7, 10, 14),
    "minor-9": (0, 3, 7, 10, 14), "major-9": (0, 4, 7, 11, 14),
    "six-nine": (0, 4, 7, 9, 14), "eleventh": (0, 4, 7, 10, 14, 17),
    # analyzer vocabulary aliases (song-map naming)
    "major-seventh": (0, 4, 7, 11), "minor-seventh": (0, 3, 7, 10),
    "sixth": (0, 4, 7, 9), "minor-sixth": (0, 3, 7, 9),
    "add-nine": (0, 4, 7, 14), "half-diminished": (0, 3, 6, 10),
    "dominant-seventh": (0, 4, 7, 10), "diminished-seventh": (0, 3, 6, 9),
    "suspended": (0, 5, 7),
}
import re as _re  # noqa: E402
_ROOT_RE = _re.compile(r"^[A-G](#|b)?(.*)$")
_SYMBOL_SUFFIX: dict[str, tuple[int, ...]] = {
    "": (0, 4, 7), "m": (0, 3, 7), "min": (0, 3, 7), "dim": (0, 3, 6),
    "aug": (0, 4, 8), "+": (0, 4, 8), "sus2": (0, 2, 7), "sus4": (0, 5, 7),
    "5": (0, 7), "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9), "7": (0, 4, 7, 10),
    "m7": (0, 3, 7, 10), "maj7": (0, 4, 7, 11), "M7": (0, 4, 7, 11),
    "m7b5": (0, 3, 6, 10), "dim7": (0, 3, 6, 9), "add9": (0, 4, 7, 14),
    "9": (0, 4, 7, 10, 14), "m9": (0, 3, 7, 10, 14), "maj9": (0, 4, 7, 11, 14),
}


# ---------------------------------------------------------------------------
# data loading (cached)
# ---------------------------------------------------------------------------
_cache: dict[str, Any] = {}


def load(name: str):
    if name not in _cache:
        _cache[name] = json.loads((ROOT / name).read_text(encoding="utf-8"))
    return _cache[name]


def factory_profiles() -> list[dict[str, Any]]:
    return load("factory-velocity-profiles.json")["profiles"]


def instrument_catalog() -> dict[str, Any]:
    return load("complete-instrument-profiles-4.44.json")["roles"]


def factory_profile_for(factory_role: str) -> dict[str, Any]:
    pool = [p for p in factory_profiles() if p.get("role") == factory_role]
    if not pool:
        raise ValueError(f"no factory velocity profiles for role {factory_role}")
    return max(pool, key=lambda p: (float(p.get("sample_count") or 0),
                                    float(p.get("confidence") or 0), str(p.get("id", ""))))


def profile_register(factory_role: str) -> tuple[int, int]:
    reg = factory_profile_for(factory_role).get("register") or {}
    return (int(reg.get("low", 0) or 0), int(reg.get("high", 127) or 127))


def polyphony_limit(channel: int) -> int:
    return int(PA800_CHANNEL_POLYPHONY_LIMITS[channel])


# ---------------------------------------------------------------------------
# factory velocity (the ONE implementation)
# ---------------------------------------------------------------------------
def factory_velocity(midi: MidiFile, channel: int, tick: int, pitch: int,
                     factory_role: str) -> dict[str, Any]:
    """Deterministic Factory-curve velocity.  Mirror of FactoryVelocityProvider.resolve.

    Velocities are picked from the factory curve only (labels strong/highMid/optimal
    chosen from a stable phase function).  Exact sound match preferred, else the
    best-evidenced profile for the factory role.
    """
    profiles = factory_profiles()
    pool = [p for p in profiles if p.get("role") == factory_role]
    if not pool:
        raise ValueError(f"no factory velocity profiles for role {factory_role}")
    sound = midi.sound_at(channel, tick)
    exact = []
    if sound:
        exact = [p for p in pool if (p.get("bankMsb"), p.get("bankLsb"), p.get("program")) == tuple(sound)]
    chosen = max(exact or pool,
                 key=lambda p: (bool(exact), float(p.get("sample_count") or 0), str(p.get("id", ""))))
    if chosen.get("role") == "drums":
        low = int((chosen.get("register") or {}).get("low", -1))
        high = int((chosen.get("register") or {}).get("high", 128))
        if not (low <= pitch <= high):
            raise ValueError("drum pitch outside factory profile register")
    phase = (tick % max(1, midi.ppq * 4)) / max(1, midi.ppq * 4)
    label = "strong" if phase < .03 else ("highMid" if abs(phase - .5) < .04 else "optimal")
    vel = chosen.get("velocity") or {}
    value = int(vel.get(label, vel.get("optimal", vel.get("max", 96))))
    return {"velocity": max(1, min(127, value)), "profileId": chosen.get("id"),
            "curvePoint": label, "sound": list(sound) if sound else None,
            "authority": "FACTORY_ONLY"}


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------
def protected_snapshot(midi: MidiFile, exclude_channels: Iterable[int] = ()) -> list[tuple]:
    excluded = set(exclude_channels)
    return sorted((n.track, n.channel, n.pitch, n.start, n.end, n.velocity)
                  for n in midi.notes() if n.channel not in excluded)


def gate_identity(raw: bytes) -> bool:
    """MIDI must reparse into at least one note (structural sanity gate)."""
    try:
        return len(MidiFile.from_bytes(raw).notes()) > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# chord tones (for metrics)
# ---------------------------------------------------------------------------
def chord_pc_set(cell: dict[str, Any]) -> set[int] | None:
    root = int(cell.get("root", 0)) % 12
    quality = str(cell.get("quality") or "").lower()
    intervals = _CHORD_INTERVALS.get(quality)
    # fallback: parse the chord symbol (e.g. Fadd9/A -> add9, Cm7b5 -> m7b5, G -> major)
    if intervals is None:
        symbol = str(cell.get("symbol") or "")
        base = symbol.split("/")[0]
        m = _ROOT_RE.match(base)
        if m:
            intervals = _SYMBOL_SUFFIX.get(m.group(2) or "", _CHORD_INTERVALS.get(quality))
    if intervals is None:
        return None
    return {(root + iv) % 12 for iv in intervals}


def note_is_chord_tone(pitch: int, pc_set: set[int] | None) -> bool | None:
    if pc_set is None:
        return None
    return (pitch % 12) in pc_set


def nearest_chord_tone(pitch: int, pc_set: set[int], lo: int, hi: int) -> int:
    """Nearest playable chord-tone pitch inside [lo, hi] (wrap by octaves)."""
    best = None
    best_d = 10 ** 9
    pc = sorted(pc_set)
    octaves = list(range(lo // 12 - 1, hi // 12 + 2))
    for iv in pc:
        for octave in octaves:
            cand = octave * 12 + iv
            if cand < lo or cand > hi:
                continue
            d = abs(cand - pitch)
            if d < best_d:
                best_d = d
                best = cand
    return best if best is not None else pitch


def exact_factory_profile(sound: tuple[int, int, int] | None,
                          factory_role: str | None = None) -> dict[str, Any] | None:
    """Best-evidenced factory profile for an exact (bank_msb, bank_lsb, program) sound.

    Returns None when the sound is unknown (engine never guesses sound facts).
    """
    if sound is None:
        return None
    pool = [p for p in factory_profiles()
            if (p.get("bankMsb"), p.get("bankLsb"), p.get("program")) == tuple(sound)]
    if factory_role:
        role_pool = [p for p in pool if p.get("role") == factory_role]
        if role_pool:
            pool = role_pool
    if not pool:
        return None
    return max(pool, key=lambda p: (float(p.get("sample_count") or 0),
                                    float(p.get("confidence") or 0), str(p.get("id", ""))))


def profile_ceiling(profile: dict[str, Any] | None) -> int:
    if not profile:
        return 127
    v = profile.get("velocity") or {}
    return int(v.get("ceiling", v.get("max", 127)) or 127)


def register_fit(pitch: int, lo: int, hi: int) -> bool:
    return lo <= pitch <= hi
