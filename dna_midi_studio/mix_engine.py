"""
Mix Engineer 4.52 — gain staging / layer balance inside existing DNA MIDI Studio.

Goal (user warrant, 2026-09-05): reduce background ("podloga") layers and
percussion-family layers by 45% **globally**, the way a mixing engineer works on
a MIDI arrangement:

  * the arrangement already carries per-bar gain automation (CC7 volume and/or
    CC11 expression) on every channel;
  * a 45% gain cut is therefore applied to the automation events (data value
    x 0.55), NOT to note velocities — timbre, articulation and factory-velocity
    authority stay 100% untouched;
  * no note is added, removed or moved (geometry gate);
  * everything is auditable at byte level (old value -> new value per event).

Rules (hard, checked by gates):
  * only channels explicitly given as targets are touched;
  * only CC11 (expression) events of those channels are scaled (uniform rule,
    both files carry CC11=127 per bar; CC7 master fader architecture is left
    alone);
  * scale is exact: round(new) is the only permitted value;
  * gate set: reparse + noteGeometryUnchanged + velocitiesUntouched +
    onlyTargetChannelCCScaled + exactScaleApplied + maskingWindowsUnchanged
    (pure gain cannot change time/pitch collisions).
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.arranger_contract import CHANNEL_ROLES
from dna_midi_studio.midi import MidiEvent, MidiFile

CC11_EXPRESSION = 11
CC7_VOLUME = 7
MAX_CC = 127


# ---------------------------------------------------------------------------
# analysis (facts only — no mutation)
# ---------------------------------------------------------------------------
def channel_facts(raw: bytes) -> dict[int, dict[str, Any]]:
    """Measured, objective per-channel facts of a parsed SMF."""
    midi = MidiFile.from_bytes(raw)
    notes = midi.notes()
    by: dict[int, list] = defaultdict(list)
    for n in notes:
        by[n.channel].append(n)
    facts: dict[int, dict[str, Any]] = {}
    for ch in sorted(by):
        cn = sorted(by[ch], key=lambda n: (n.start, n.pitch))
        if not cn:
            continue
        starts = sorted({n.start for n in cn})
        dur = [n.end - n.start for n in cn]
        vel = [n.velocity for n in cn]
        facts[ch] = {
            "channel": ch,
            "role": CHANNEL_ROLES.get(ch, "unknown"),
            "sound": midi.sound_at(ch, starts[0]),
            "noteCount": len(cn),
            "register": [min(n.pitch for n in cn), max(n.pitch for n in cn)],
            "avgDurTicks": round(sum(dur) / len(dur), 1),
            "avgVel": round(sum(vel) / len(vel), 1),
            "velMin": min(vel),
            "velMax": max(vel),
            "distinctPitches": len({n.pitch for n in cn}),
            "distinctStarts": len(starts),
        }
    return facts


def _percussion_accents(midi: MidiFile) -> dict[int, list[dict[str, Any]]]:
    """Notes on drum-role channels that read as percussion accents.

    Objective rule, no sound-map magic:
      * the note pitch is NOT part of the channel's own core kit — core = any
        pitch that carries >= 5% of that channel's notes (kick/snare/hat etc.
        repeat constantly);
      * OR the same (pitch, start) also appears on the dedicated percussion
        channel 10 (double-trigger signature).
    """
    notes = midi.notes()
    by = defaultdict(list)
    for n in notes:
        by[n.channel].append(n)
    total = Counter(n.channel for n in notes)
    perc_keys = {(n.pitch, n.start) for n in notes if n.channel == 10}
    out: dict[int, list[dict[str, Any]]] = {}
    for ch in (9, 10):
        if not by.get(ch):
            continue
        cnt = Counter(n.pitch for n in by[ch])
        accents = []
        for n in sorted(by[ch], key=lambda n: (n.start, n.pitch)):
            core = cnt[n.pitch] / total[ch] >= 0.05
            doubled = ch == 9 and (n.pitch, n.start) in perc_keys
            if (not core) or doubled:
                accents.append({"pitch": n.pitch, "startTick": n.start,
                                "velocity": n.velocity, "coreKitPitch": core,
                                "doubleTriggerOnPerc": doubled})
        if accents:
            out[ch] = accents
    return out


def percussion_accents_on_drums(raw: bytes) -> dict[str, Any]:
    """Report of conga/percussion-family content on the drum channel."""
    midi = MidiFile.from_bytes(raw)
    acc = _percussion_accents(midi)
    detail = acc.get(9, [])
    counts = Counter((a["doubleTriggerOnPerc"], a["coreKitPitch"]) for a in detail)
    return {
        "drumChannel": 9,
        "percussionChannel": 10,
        "accentNoteCount": len(detail),
        "byKind": {f"double={dbl},corePitch={core}": c
                   for (core, dbl), c in sorted(counts.items())},
        "accents": detail,
        "applied": False,  # note-level gain would touch factory velocity -> report only
        "policy": "report-only: velocity on protected channels is FACTORY_ONLY; "
                  "whole-channel percussion cut happens via CC11 on ch10",
    }


def masking_windows(raw: bytes, pitch_window: int = 12) -> dict[str, Any]:
    """Time+pitch collision windows between channel pairs (masking metric).

    A collision = a note of channel A whose sounding time overlaps a note of
    channel B within |pitch difference| <= pitch_window.  Purely geometric;
    pure gain staging must not change it (gate).
    """
    midi = MidiFile.from_bytes(raw)
    notes = sorted(midi.notes(), key=lambda n: n.start)
    by: dict[int, list] = defaultdict(list)
    for n in notes:
        by[n.channel].append(n)
    chs = sorted(by)
    pairs: dict[str, int] = {}
    for i, a in enumerate(chs):
        for b in chs[i + 1:]:
            col = 0
            na = by[a]
            nb = sorted(by[b], key=lambda n: n.start)
            for n in na:
                for o in nb:
                    if o.start > n.end + 40:
                        break
                    if o.end + 40 > n.start and abs(o.pitch - n.pitch) <= pitch_window:
                        col += 1
            if col:
                pairs[f"{a}-{b}"] = col
    return {
        "schema": "dna-mix-masking-windows", "pitchWindowSemitones": pitch_window,
        "pairs": pairs,
        "totalCollidingChannelNotes": sum(pairs.values()),
        "busiestPair": max(pairs.items(), key=lambda kv: kv[1])[0] if pairs else None,
    }


# ---------------------------------------------------------------------------
# gain plan + application (the only mutation)
# ---------------------------------------------------------------------------
def _cc_events(midi: MidiFile, controller: int) -> list[tuple[int, int, int, int]]:
    """(track, tick, channel, value) for every matching controller event."""
    found = []
    for ti, track in enumerate(midi.tracks):
        for e in track.events:
            if e.kind != "channel" or e.status is None or (e.status >> 4) != 0xB:
                continue
            if e.channel is not None and len(e.data) == 2 and e.data[0] == controller:
                found.append((ti, e.tick, e.channel, e.data[1]))
    return sorted(found, key=lambda f: (f[1], f[0], f[2]))


def plan_cc_gain(raw: bytes, targets: dict[int, float], *,
                 controller: int = CC11_EXPRESSION) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Plan an exact gain change on CC events of target channels.

    targets: {channel: factor} — factor 0.55 == global -45% gain.
    Returns (plan, summary): one plan entry per affected event with old/new.
    """
    midi = MidiFile.from_bytes(raw)
    plan: list[dict[str, Any]] = []
    missing: list[int] = []
    for ch, factor in sorted(targets.items()):
        events = [f for f in _cc_events(midi, controller) if f[2] == ch]
        if not events:
            missing.append(ch)
            continue
        for ti, tick, channel, value in events:
            new_val = max(1, min(MAX_CC, round(value * factor)))
            if new_val != value:
                plan.append({"track": ti, "tick": tick, "channel": channel,
                             "controller": controller, "oldValue": value,
                             "newValue": new_val, "factor": factor})
    summary = {
        "controller": controller,
        "targets": {str(k): v for k, v in sorted(targets.items())},
        "eventsPlanned": len(plan),
        "channelsMissingCC": missing,
        "noteForMissing": "channel has no CC11 automation; note velocities left "
                          "untouched (FACTORY_ONLY) — no change applied",
    }
    return plan, summary


def apply_gain_plan(raw: bytes, plan: list[dict[str, Any]]) -> bytes:
    """Rewrite the exact CC events listed in the plan (byte-level audit)."""
    if not plan:
        return raw
    keyed = {(p["track"], p["tick"], p["channel"], p["controller"], p["oldValue"])
             for p in plan}
    new_values = {(p["track"], p["tick"], p["channel"], p["controller"], p["oldValue"]):
                  p["newValue"] for p in plan}
    midi = MidiFile.from_bytes(raw)
    for ti, track in enumerate(midi.tracks):
        events = []
        for e in track.events:
            if (e.kind == "channel" and e.status is not None
                    and (e.status >> 4) == 0xB and len(e.data) == 2):
                key = (ti, e.tick, e.status & 0x0F, e.data[0], e.data[1])
                if key in keyed:
                    nd = bytes((e.data[0], new_values[key]))
                    e = dataclasses.replace(e, data=nd)
            events.append(e)
        track.events = events
    return midi.to_bytes()


# ---------------------------------------------------------------------------
# gates + runner
# ---------------------------------------------------------------------------
def mix_gates(raw_before: bytes, raw_after: bytes, plan: list[dict[str, Any]],
              targets: dict[int, float]) -> dict[str, bool]:
    midi_b = MidiFile.from_bytes(raw_before)
    midi_a = MidiFile.from_bytes(raw_after)

    def geom(m: MidiFile):
        return sorted((n.track, n.channel, n.pitch, n.start, n.end) for n in m.notes())

    def vels(m: MidiFile):
        return sorted((n.track, n.channel, n.pitch, n.start, n.end, n.velocity)
                      for n in m.notes())

    target_chs = set(targets)
    # expected CC11 value per (track, tick, channel) after an exact scale
    expected: dict[tuple[int, int, int], int] = {}
    before_cc = _cc_events(midi_b, CC11_EXPRESSION)
    for ti, tick, ch, value in before_cc:
        if ch in target_chs:
            expected[(ti, tick, ch)] = max(1, min(MAX_CC, round(value * targets[ch])))
    after_cc = _cc_events(midi_a, CC11_EXPRESSION)
    nontarget_before = sorted((ti, tick, ch, value) for ti, tick, ch, value in before_cc
                              if ch not in target_chs)
    nontarget_after = sorted((ti, tick, ch, value) for ti, tick, ch, value in after_cc
                             if ch not in target_chs)
    only_target = nontarget_before == nontarget_after
    for ti, tick, ch, value in after_cc:
        key = (ti, tick, ch)
        if ch in target_chs:
            if key in expected and value != expected[key]:
                only_target = False
            elif key not in expected:
                only_target = False  # a CC11 event appeared that did not exist before
    wrong_scale = []
    for p in plan:
        want = max(1, min(MAX_CC, round(p["oldValue"] * p["factor"])))
        if p["newValue"] != want:
            wrong_scale.append(p)
    return {
        "reparsed": len(midi_a.notes()) == len(midi_b.notes()),
        "noteGeometryUnchanged": geom(midi_a) == geom(midi_b),
        "velocitiesUntouched": vels(midi_a) == vels(midi_b),
        "onlyTargetChannelCCScaled": only_target,
        "exactScaleApplied": not wrong_scale,
        "maskingWindowsUnchanged": masking_windows(raw_before)["totalCollidingChannelNotes"]
        == masking_windows(raw_after)["totalCollidingChannelNotes"],
    }


def run_mix_gain(raw: bytes, *, targets: dict[int, float], controller: int = CC11_EXPRESSION,
                 source_name: str = "source.mid", out_dir: str | Path | None = None,
                 write_artifacts: bool = True) -> dict[str, Any]:
    """Full mix-gain run: facts -> accents -> masking -> plan -> apply -> gates."""
    plan, summary = plan_cc_gain(raw, targets, controller=controller)
    after = apply_gain_plan(raw, plan)
    gates = mix_gates(raw, after, plan, targets)
    result = {
        "schema": "dna-mix-engine-run",
        "version": "4.52",
        "sourceName": source_name,
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "request": {"gainFactorByChannel": {str(k): v for k, v in sorted(targets.items())},
                    "interpretation": "per-bar CC11 expression automation scaled by the "
                                      "factor (0.55 = -45%); note velocities untouched"},
        "channelFacts": channel_facts(raw),
        "percussionAccentReport": percussion_accents_on_drums(raw),
        "maskingBefore": masking_windows(raw),
        "planSummary": summary,
        "plan": plan,
        "gates": gates,
        "status": "MIX_APPLIED" if plan and all(gates.values()) else
                  ("MIX_NO_CHANGE_NEEDED" if not plan else "MIX_GATE_FAILED"),
    }
    if out_dir and write_artifacts:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stem = Path(source_name).stem
        (out / f"mix-arranged-{stem}.mid").write_bytes(after)
        (out / f"mix-run-{stem}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        result["artifactMidi"] = str(out / f"mix-arranged-{stem}.mid")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mix Engineer 4.52 — CC gain staging")
    ap.add_argument("--input", required=True)
    ap.add_argument("--targets", required=True,
                    help="channel:factor pairs, e.g. 11:0.55,13:0.55 (0.55 == -45%)")
    ap.add_argument("--out-dir", default="artifacts-max-4.52")
    args = ap.parse_args(argv)
    targets = {}
    for part in args.targets.split(","):
        ch, factor = part.split(":")
        targets[int(ch)] = float(factor)
    raw = Path(args.input).read_bytes()
    res = run_mix_gain(raw, targets=targets, source_name=Path(args.input).name,
                       out_dir=args.out_dir)
    print(json.dumps({k: res[k] for k in ("status", "sourceSha256", "planSummary", "gates")},
                     indent=2, ensure_ascii=False))
    return 0 if res["status"] in ("MIX_APPLIED", "MIX_NO_CHANGE_NEEDED") else 1


if __name__ == "__main__":
    raise SystemExit(_main())
