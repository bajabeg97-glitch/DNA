"""Special Track Engine — echo / terca (third-voice) optimization (4.57).

Implements the v4.13-era specification that lived only as an executable test
(`test_echo_terca_engine_v413.py`): `optimize_existing_echo_terca(...)`.

What the engine optimizes in an existing arrangement:

* echo track  — an auxiliary part that answers the main solo ~1/2 beat later
  and quieter.  Optimization = REPAIR (snap echo onsets to the echo grid,
  keep echo quieter than the main part, keep echo durations shorter than the
  main part's) or REBUILD (when the echo track's content does not resemble
  the main part at all: remove the unrelated notes and rebuild the echo from
  the main part at the echo offset).
* terca track — a diatonic-third voice under/above the main solo.
  Optimization = REPAIR (align onsets to the main timing grid and correct
  each third so it is *diatonic* in the detected harmony) or PRESERVE (no
  harmony evidence -> no pitch authority, never guess a key).
* unnamed legacy aux tracks — a second solo-like track that is a consistent
  delayed, quieter copy of the main part is inferred as an echo track.

Authority rules (unchanged DNA policy):
  - the main part is NEVER modified;
  - velocity changes on the aux track only ever go DOWN (quieter) and are
    capped by the factory curve ceiling when a profile is supplied;
  - pitch is changed only for third-voice repair WITH harmony evidence;
  - no bank/program/CC/trigger events are ever emitted;
  - DNC/slap/pop/guitar triggers stay forbidden (see 4.55 warrant ledger).

Note-object format is the plain-dict format used by the legacy suite
(note = {"pitch", "channel", "on": {...}, "off": {...}, "instrumentKey"}),
so the engine operates on real parsed structures without a dependency.
"""
from __future__ import annotations

import statistics
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

DEFAULT_BAR_TICKS = 1920
DEFAULT_PPQ = 480
ECHO_GRID_DIVISOR = 2        # echo grid step = ppq / 2 (half-beat snap)
ECHO_VELOCITY_RATIO = 0.80   # aux velocity target = 80% of the main note
ECHO_DURATION_RATIO = 0.80   # aux duration target = 80% of the main note
ECHO_MIN_DELTA_MAIN = 5      # repaired aux velocity must be at least this far below main
SIMILARITY_DELAY_TOLERANCE_TICKS = 90
SIMILARITY_MATCH_TOLERANCE_TICKS = 30
SIMILARITY_MIN_DELAY_RATIO = 0.5
SIMILARITY_MIN_PITCH_MATCH = 0.5
REBUILD_ECHO_OFFSET_TICKS = 240  # fallback offset when delays are inconsistent
_MAIN_NAME_HINT = "main"


def _snap(value: int, step: int) -> int:
    return int(round(value / step) * step)


def _note_tick(note: Mapping[str, Any]) -> int:
    return int(note["on"]["tick"])


def _note_vel(note: Mapping[str, Any]) -> int:
    return int(note["on"]["data"][1])


def _note_pitch(note: Mapping[str, Any]) -> int:
    return int(note["pitch"])


def _note_dur(note: Mapping[str, Any]) -> int:
    return int(note["off"]["tick"]) - int(note["on"]["tick"])


def _set_tick(note: Mapping[str, Any], tick: int) -> None:
    note["on"]["tick"] = tick
    note["off"]["remove"] = note["off"].get("remove", False)
    if tick is not None:
        note["off"]["tick"] = tick + _note_dur(note)


def _set_vel(note: Mapping[str, Any], vel: int) -> None:
    note["on"]["data"][1] = max(1, min(127, int(vel)))


def _remove(note: Mapping[str, Any]) -> None:
    note["on"]["remove"] = True
    note["off"]["remove"] = True


def _ceil_for(profile: Mapping[str, Any] | None, fallback: int = 127) -> int:
    if not profile:
        return fallback
    curve = profile.get("curve") or {}
    return int(curve.get("ceiling", fallback))


def _main_candidate(groups: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Highest-confidence solo group; the one named *main* wins ties."""
    solos = [g for g in groups if (g.get("role") or "").lower() == "solo"]
    if not solos:
        return None
    named = [g for g in solos
             if _MAIN_NAME_HINT in (g.get("trackName") or "").lower()]
    pool = named or solos
    return max(pool, key=lambda g: (g.get("confidence") or 0.0,
                                    g.get("track", {}).get("index", 0)))


def _nearest_main_offset(aux_tick: int, main_ticks: Sequence[int]) -> int:
    """Offset from the closest main onset that is at or before the aux tick
    (an echo/answer always follows the note it answers; using the previous
    onset keeps delays positive even on a dense grid)."""
    before = [aux_tick - t for t in main_ticks if t <= aux_tick + 1]
    if before:
        return min(before, key=abs)
    return min((aux_tick - t for t in main_ticks), key=abs)


def _similarity(aux: Sequence[Mapping[str, Any]],
                main: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Onset-pattern delay matching + pitch match between aux and main.

    Delay matching works on *onset positions* (unique ticks), so polyphonic
    chordal parts do not confuse the metric: we search the delay tau that
    aligns the most aux onsets onto a main onset (tolerance 30 ticks) and
    require at least half of the aux onsets to be explained by it.
    """
    aux_ticks = sorted({_note_tick(n) for n in aux})
    main_ticks = sorted({_note_tick(n) for n in main})
    if not aux_ticks or not main_ticks:
        return {"delayTicks": None, "delayRatio": 0.0,
                "pitchMatch": 0.0, "consistent": False}
    tol = SIMILARITY_MATCH_TOLERANCE_TICKS
    candidates: list[int] = []
    for a in aux_ticks:
        prev = [a - m for m in main_ticks if m <= a + tol]
        if prev:
            candidates.append(min(prev, key=abs))
    candidates = [c for c in candidates if c >= 60]
    best_tau, best_ratio = None, 0.0
    for tau in sorted(set(candidates)):
        hit = sum(1 for a in aux_ticks
                  if any(abs(a - tau - m) <= tol for m in main_ticks))
        ratio = hit / len(aux_ticks)
        if ratio > best_ratio:
            best_tau, best_ratio = tau, ratio
    pitch_match = 0.0
    if main:
        pitches = {_note_pitch(n) for n in main}
        matched = sum(1 for n in aux
                      if any(abs(_note_pitch(n) - p) <= 2 for p in pitches))
        pitch_match = matched / len(aux)
    consistent = best_tau is not None \
        and best_ratio >= SIMILARITY_MIN_DELAY_RATIO \
        and pitch_match >= SIMILARITY_MIN_PITCH_MATCH
    return {"delayTicks": best_tau, "delayRatio": round(best_ratio, 3),
            "pitchMatch": round(pitch_match, 3), "consistent": consistent}


def _diatonic_third_above(pitch: int, harmony: Mapping[str, Any]) -> int:
    """Diatonic third above `pitch` in the harmony's major/minor scale.

    Harmony cell: {"root": pitch-class, "quality": "major"|"minor", ...}.
    Returns the scale member that is a third (3rd scale degree) above the
    given pitch, in the pitch's own octave where possible.
    """
    root = int(harmony.get("root", 0)) % 12
    quality = str(harmony.get("quality", "major"))
    semis = (0, 2, 4, 5, 7, 9, 11) if quality.startswith("major") else \
            (0, 2, 3, 5, 7, 8, 10)
    scale = sorted((root + s) % 12 for s in semis)
    pc = pitch % 12
    idx = scale.index(pc) if pc in scale else None
    if idx is None:
        return pitch + 3
    third_pc = scale[(idx + 2) % 7]
    octave = pitch // 12
    cand = octave * 12 + third_pc
    if cand <= pitch:
        cand += 12
    if cand - pitch > 6 and cand - 12 > pitch:
        cand -= 12
    return cand


def _report(kind: str, mode: str, group: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": kind, "mode": mode, "channel": group.get("channel"),
        "trackName": group.get("trackName"),
        "trackIndex": group.get("track", {}).get("index"),
    }
    base.update(extra)
    return base


def _repair_echo(aux: Sequence[Mapping[str, Any]],
                 main: Sequence[Mapping[str, Any]], *,
                 ppq: int, stats: Counter) -> tuple[str, bool]:
    """Snap onsets to echo grid; never let aux be louder/longer than main."""
    step = max(1, ppq // ECHO_GRID_DIVISOR)
    changed = False
    main_by_index = list(main)
    for i, note in enumerate(aux):
        tick = _snap(_note_tick(note), step)
        if tick != _note_tick(note):
            _set_tick(note, tick)
            changed = True
        main_note = main_by_index[i] if i < len(main_by_index) else None
        if main_note is not None:
            mvel = _note_vel(main_note)
            target = max(1, min(int(round(mvel * ECHO_VELOCITY_RATIO)),
                                mvel - ECHO_MIN_DELTA_MAIN))
            if _note_vel(note) > target:
                _set_vel(note, target)
                changed = True
            mdur = _note_dur(main_note)
            max_dur = max(1, int(round(mdur * ECHO_DURATION_RATIO)))
            if _note_dur(note) >= mdur:
                dur = max_dur
                note["off"]["tick"] = note["on"]["tick"] + dur
                changed = True
    stats["echo.notes.repaired"] += len(aux)
    return ("REPAIR" if changed else "PRESERVE"), changed


def _rebuild_echo(aux: Sequence[Mapping[str, Any]],
                  main: Sequence[Mapping[str, Any]], *,
                  track: Mapping[str, Any], channel: int, ppq: int,
                  profiles: Mapping[str, Any], offset: int, stats: Counter) -> None:
    """Remove unrelated aux notes and generate an echo copy of the main part."""
    for note in aux:
        _remove(note)
    stats["echo.notes.removed"] += len(aux)
    if not main or not track:
        return
    profile = None
    for note in main:
        key = note.get("instrumentKey")
        if key and profiles and key in profiles:
            profile = profiles[key]
            break
    ceiling = _ceil_for(profile)
    generated = 0
    order_base = max((e.get("order", 0) for e in track.get("events", [])),
                     default=0) + 1
    for i, note in enumerate(main):
        tick = _note_tick(note) + offset
        vel = int(round(_note_vel(note) * ECHO_VELOCITY_RATIO))
        vel = max(1, min(vel, ceiling))
        dur = max(1, int(round(_note_dur(note) * ECHO_DURATION_RATIO)))
        pitch = _note_pitch(note)
        track.setdefault("events", []).extend([
            {"tick": tick, "order": order_base + i * 2,
             "kind": "channel", "command": 9, "channel": channel,
             "data": [pitch, vel], "remove": False},
            {"tick": tick + dur, "order": order_base + i * 2 + 1,
             "kind": "channel", "command": 8, "channel": channel,
             "data": [pitch, 0], "remove": False},
        ])
        generated += 1
    stats["echo.notes.generated"] += generated


def _repair_third(aux: Sequence[Mapping[str, Any]],
                  main: Sequence[Mapping[str, Any]], *,
                  harmony: Mapping[str, Any], ppq: int,
                  stats: Counter) -> tuple[str, bool]:
    """Align to the main timing grid and correct pitches to diatonic thirds."""
    step = max(1, ppq // ECHO_GRID_DIVISOR)
    changed = False
    main_by_index = list(main)
    for i, note in enumerate(aux):
        tick = _snap(_note_tick(note), step)
        if tick != _note_tick(note):
            _set_tick(note, tick)
            changed = True
        main_note = main_by_index[i] if i < len(main_by_index) else None
        if main_note is None:
            continue
        pitch = _note_pitch(main_note)
        third = _diatonic_third_above(pitch, harmony)
        if third != _note_pitch(note):
            note["pitch"] = third
            note["on"]["data"][0] = third
            changed = True
    stats["third.notes.repaired"] += len(aux)
    return ("REPAIR" if changed else "PRESERVE"), changed


def optimize_existing_echo_terca(
    groups: Sequence[Mapping[str, Any]],
    harmony: Mapping[str, Any] | None = None,
    bar_ticks: int = DEFAULT_BAR_TICKS,
    ppq: int = DEFAULT_PPQ,
    profiles: Mapping[str, Any] | None = None,
    extras: Mapping[str, Any] | None = None,
    stats: Counter | None = None,
) -> list[dict[str, Any]]:
    """Optimize existing echo/terca tracks relative to the main solo part.

    Returns one report per processed aux group; never modifies the main part.
    """
    stats = stats if stats is not None else Counter()
    profiles = profiles or {}
    harmony = harmony or {}
    reports: list[dict[str, Any]] = []
    main = _main_candidate(groups)
    if main is None:
        return reports
    main_notes = list(main.get("notes", []))

    for group in groups:
        role = str(group.get("role") or "").lower()
        if role not in ("echo", "third", "solo"):
            continue
        if group is main:
            continue
        aux_notes = list(group.get("notes", []))
        if not aux_notes:
            continue
        sim = _similarity(aux_notes, main_notes)

        # Explicit third-voice track.
        if role == "third":
            if harmony:
                mode, _ = _repair_third(aux_notes, main_notes,
                                        harmony=harmony, ppq=ppq, stats=stats)
                reports.append(_report("third", mode, group))
            else:
                reports.append(_report("third", "PRESERVE", group,
                                       reason="no-harmony-evidence-no-pitch-authority"))
            continue

        # Explicit echo track.
        if role == "echo":
            if sim["consistent"]:
                mode, _ = _repair_echo(aux_notes, main_notes, ppq=ppq, stats=stats)
                reports.append(_report("echo", mode, group, **sim))
            else:
                offset = sim["delayTicks"] if sim["delayTicks"] is not None \
                    else REBUILD_ECHO_OFFSET_TICKS
                _rebuild_echo(aux_notes, main_notes, track=group.get("track"),
                              channel=int(group.get("channel", 0)), ppq=ppq,
                              profiles=profiles, offset=offset, stats=stats)
                reports.append(_report("echo", "REBUILD", group, **sim))
            continue

        # Second solo-like track: infer echo/terca from relationship.
        if sim["consistent"] and sim["delayTicks"] is not None \
                and sim["delayTicks"] >= 60 and sim["pitchMatch"] >= 0.75:
            mode, _ = _repair_echo(aux_notes, main_notes, ppq=ppq, stats=stats)
            stats["tracks.inferred"] += 1
            reports.append(_report("echo", mode, group, inferred=True, **sim))
    return reports
