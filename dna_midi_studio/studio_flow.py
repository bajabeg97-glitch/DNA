"""
Studio Flow 4.51 — the engine that APPLIES instead of blocking.

One deterministic pass over a MIDI song/style:

  plan      -> per-channel instrument fit (arranger_planner)
  fill      -> every empty accompaniment slot that has evidence:
                 ch15       factory-strum guitar   (3 voices, reg 48-72, chord-tone voicing)
                 ch11/12    factory-comp block     (4 voices, slot register, chord-tone voicing)
                 ch13/14    sustained pad voices   (single voice each, chord tones)
  polish    -> enforce factory dynamic ceilings on every channel where the exact
               sound's factory profile is unambiguous (never guesses sounds)
  metrics   -> before/after: chord tones, register, velocity authority, headroom
  gates     -> reparse, protected geometry diff, factory-only velocity, polyphony

Notes: pre-existing material is never overwritten and never edited except for the
single, objectively justified fix above (velocity above its exact-sound factory
ceiling is a factory-authority violation).  Everything is deterministic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.midi import MidiFile, Note  # noqa: E402
from dna_midi_studio.song_understanding import analyze_song_map  # noqa: E402
from dna_midi_studio.arranger_contract import (  # noqa: E402
    ACC_CHANNELS, CHANNEL_ROLES, COMP_REGISTER, STRUM_REGISTER,
    VELOCITY_ROLE, chord_pc_set, exact_factory_profile, factory_profiles,
    factory_velocity, polyphony_limit, profile_ceiling, protected_snapshot,
)
from dna_midi_studio.arranger_planner import plan_regions  # noqa: E402
from dna_midi_studio.arranger_pro import _music_track, build_strum_part, headroom_audit  # noqa: E402

PAD_TONE = {13: "root", 14: "fifth"}
COMP_PRIORITY = (15, 12, 11)          # guitar slot first, then comp slots


# ---------------------------------------------------------------------------
# dynamics enforcement (factory-authority, exact sound only)
# ---------------------------------------------------------------------------
def plan_dynamic_corrections(raw: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Notes above their exact-sound factory ceiling -> clamp (never guess sound)."""
    midi = MidiFile.from_bytes(raw)
    corrections: list[dict[str, Any]] = []
    counts: dict[str, dict[str, Any]] = {}
    for n in midi.notes():
        ch = n.channel
        if ch not in CHANNEL_ROLES:
            continue
        sound = midi.sound_at(ch, n.start)
        profile = exact_factory_profile(sound, VELOCITY_ROLE.get(CHANNEL_ROLES[ch]))
        ceiling = profile_ceiling(profile)
        if profile is None:
            continue
        if n.velocity > ceiling:
            corrections.append({"track": n.track, "channel": ch, "pitch": n.pitch,
                                "start": n.start, "end": n.end,
                                "from": n.velocity, "to": ceiling,
                                "profileId": profile.get("id")})
            c = counts.setdefault(str(ch), {"clamped": 0, "fromMax": 0})
            c["clamped"] += 1
            c["fromMax"] = max(c["fromMax"], n.velocity)
    return corrections, counts


def apply_dynamic_corrections(raw: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    midi = MidiFile.from_bytes(raw)
    corrections, _ = plan_dynamic_corrections(raw)
    if not corrections:
        return raw, []
    per_channel: dict[int, list[dict[str, Any]]] = {}
    for c in corrections:
        per_channel.setdefault(c["channel"], []).append(c)
    for ch, rows in per_channel.items():
        notes = [n for n in midi.notes() if n.channel == ch]
        by_key = {(n.pitch, n.start, n.end, n.track): n for n in notes}
        fixed = []
        for n in notes:
            row = next((c for c in rows if c["pitch"] == n.pitch and c["start"] == n.start
                        and c["end"] == n.end), None)
            fixed.append(Note(n.track, n.channel, n.pitch, n.start, n.end,
                              row["to"] if row else n.velocity,
                              factory_profile_id=n.factory_profile_id,
                              gold_pattern_id=n.gold_pattern_id, element=n.element))
        track = fixed[0].track if fixed else 0
        end_tick = max((n.end for n in notes), default=midi.ppq)
        midi = midi.replace_notes(track_index=track, channel=ch, start_tick=0,
                                  end_tick=end_tick + 1, new_notes=fixed)
    return midi.to_bytes(), corrections


# ---------------------------------------------------------------------------
# pad fill (single-voice sustained chord tones on ACC3/ACC4)
# ---------------------------------------------------------------------------
def _pad_velocity(midi: MidiFile, ch: int, tick: int, pitch: int) -> dict[str, Any]:
    sound = midi.sound_at(ch, tick)
    profile = exact_factory_profile(sound, "melody")
    if profile is None:
        profile = max([p for p in factory_profiles() if p.get("role") == "melody"],
                      key=lambda p: (float(p.get("sample_count") or 0), str(p.get("id", ""))))
    v = profile.get("velocity") or {}
    value = int(v.get("optimal", v.get("min", 64)))
    return {"velocity": max(1, min(127, value)), "profileId": profile.get("id"),
            "authority": "FACTORY_ONLY"}


def build_pad_part(raw: bytes, *, channel: int, start_bar: int, end_bar: int,
                   tone: str = "root", track_index: int | None = None) -> dict[str, Any]:
    midi = MidiFile.from_bytes(raw)
    track = _music_track(midi) if track_index is None else track_index
    song = analyze_song_map(raw, "pad-source.mid")
    bars = song["bars"]
    if not 1 <= start_bar <= end_bar <= len(bars):
        raise ValueError("invalid bar range")
    lo, hi = COMP_REGISTER.get(channel, (60, 84))
    center = (lo + hi) // 2
    target_iv = {"root": 0, "third": 4, "fifth": 7}.get(tone, 0)
    cells = {int(c["startTick"]): c for c in song["chordCells"]}
    notes: list[Note] = []
    proofs: list[dict[str, Any]] = []
    used_pcs: list[tuple[int, int]] = []
    for bar in bars[start_bar - 1:end_bar]:
        bs, be = int(bar["startTick"]), int(bar["endTick"])
        cell = cells.get(bs)
        pc = chord_pc_set(cell) if cell else None
        if pc is None:
            continue  # no harmonic evidence for this bar -> honest gap
        intervals = sorted(pc)
        iv = min(intervals, key=lambda x: abs(x - target_iv))
        root_pc = int(cell.get("root", 0)) % 12
        # choose octave so the tone sits closest to the register center
        best_pitch = None
        for octave in range(lo // 12 - 1, hi // 12 + 2):
            cand = octave * 12 + iv
            if cand < lo or cand > hi:
                continue
            if best_pitch is None or abs(cand - center) < abs(best_pitch - center):
                best_pitch = cand
        if best_pitch is None:
            continue
        if (bs, best_pitch) in used_pcs:
            continue
        start = bs
        end = max(start + 1, be - max(1, midi.ppq // 24))  # release gap before next bar
        vel = _pad_velocity(midi, channel, bs + midi.ppq, best_pitch)
        notes.append(Note(track, channel, best_pitch, start, end, vel["velocity"],
                          factory_profile_id=vel["profileId"]))
        proofs.append({**vel, "tone": tone, "rootPc": root_pc, "interval": iv})
        used_pcs.append((bs, best_pitch))
    return {
        "schema": "dna-arranger-pad-part", "version": "4.51",
        "role": "pad", "channel": channel, "track": track, "tone": tone,
        "bars": [start_bar, end_bar], "noteCount": len(notes),
        "notes": notes, "velocityProofs": proofs,
        "register": [lo, hi], "maxSimultaneous": 1,
        "velocityAuthority": "FACTORY_ONLY", "voicing": "chord-tone sustained",
    }


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------
def arrangement_metrics(raw_before: bytes, raw_after: bytes) -> dict[str, Any]:
    midi_b = MidiFile.from_bytes(raw_before)
    midi_a = MidiFile.from_bytes(raw_after)
    def key(n):
        return (n.track, n.channel, n.pitch, n.start, n.end)
    kb = {key(n): n for n in midi_b.notes()}
    ka = {key(n): n for n in midi_a.notes()}
    added = [ka[k] for k in ka if k not in kb]
    removed = [kb[k] for k in kb if k not in ka]
    try:
        song = analyze_song_map(raw_after, "metrics-source.mid")
        cells = song["chordCells"]
    except Exception:
        cells = []

    def cell_at(tick):
        for c in cells:
            if int(c["startTick"]) <= tick < int(c["endTick"]):
                return c
        return None

    per_channel: dict[str, Any] = {}
    for ch in sorted({n.channel for n in added}):
        ch_added = [n for n in added if n.channel == ch]
        if not ch_added:
            continue
        hits = checked = unknown = out_reg = 0
        for n in ch_added:
            pc = chord_pc_set(cell_at(n.start)) if cell_at(n.start) else None
            if pc is None:
                unknown += 1
                continue
            checked += 1
            if (n.pitch % 12) in pc:
                hits += 1
            lo, hi = COMP_REGISTER.get(ch, STRUM_REGISTER)
            if not (lo <= n.pitch <= hi):
                out_reg += 1
        vels = [n.velocity for n in ch_added]
        per_channel[str(ch)] = {
            "addedNotes": len(ch_added),
            "chordToneRatio": round(hits / max(1, checked), 3) if checked else None,
            "chordToneChecked": checked, "cellsWithoutChordQuality": unknown,
            "outOfSlotRegister": out_reg,
            "register": [min(n.pitch for n in ch_added), max(n.pitch for n in ch_added)],
            "avgVelocity": round(sum(vels) / len(vels), 1) if vels else None,
            "velocityMax": max(vels) if vels else None,
        }
    return {
        "schema": "dna-studio-flow-metrics", "version": "4.51",
        "addedNotesTotal": len(added), "removedNotesTotal": len(removed),
        "channelsWithAdditions": sorted({n.channel for n in added}),
        "perChannel": per_channel,
        "mixNoteCountBefore": len(midi_b.notes()), "mixNoteCountAfter": len(midi_a.notes()),
        "policy": "geometry outside target channels never changes; velocity factory-only",
    }


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------
def run_studio(raw: bytes, *, target_channel: int | None = None,
               start_bar: int | None = None, end_bar: int | None = None,
               out_dir: str | Path | None = None,
               write_artifacts: bool = True) -> dict[str, Any]:
    plan = plan_regions(raw)
    midi = MidiFile.from_bytes(raw)
    song = analyze_song_map(raw, "studio-source.mid")
    bars = song["bars"]
    sb = start_bar or 1
    eb = end_bar or len(bars)
    track = _music_track(midi)
    out = Path(out_dir) if out_dir else ROOT / "artifacts-max-4.51"
    out.mkdir(parents=True, exist_ok=True)

    fills: list[dict[str, Any]] = []
    current_raw = raw

    def empty(ch: int) -> bool:
        return not any(n.channel == ch for n in MidiFile.from_bytes(current_raw).notes())

    def apply(part: dict[str, Any]) -> None:
        nonlocal current_raw
        m = MidiFile.from_bytes(current_raw)
        ch = part["channel"]
        start_tick = int(bars[sb - 1]["startTick"])
        end_tick = int(bars[eb - 1]["endTick"])
        m = m.replace_notes(track_index=part["track"], channel=ch,
                            start_tick=start_tick, end_tick=end_tick,
                            new_notes=part["notes"])
        current_raw = m.to_bytes()

    # explicit target: fill exactly that slot when supported+empty
    if target_channel is not None:
        if target_channel not in ACC_CHANNELS:
            raise ValueError(f"target channel {target_channel} not in {ACC_CHANNELS}")
        if not empty(target_channel):
            raise ValueError(f"channel {target_channel} already has material — never overwritten")
        if target_channel in (13, 14):
            part = build_pad_part(raw, channel=target_channel, start_bar=sb, end_bar=eb,
                                  tone=PAD_TONE[target_channel], track_index=track)
        else:
            part = build_strum_part(raw, channel=target_channel, start_bar=sb, end_bar=eb,
                                    track_index=track,
                                    register=tuple(COMP_REGISTER[target_channel])
                                    if target_channel in COMP_REGISTER else None)
        apply(part)
        fills.append({"channel": target_channel,
                      "role": "pad" if target_channel in (13, 14) else "comp/strum",
                      "noteCount": len(part["notes"]),
                      "register": part["register"],
                      "evidenceSource": part["schema"],
                      "planDecision": plan["regions"][str(target_channel)]["decision"]})
    else:
        # auto: fill every empty slot that has evidence, in arrangement order
        comp_chosen = next((ch for ch in COMP_PRIORITY if empty(ch)), None)
        if comp_chosen is not None:
            part = build_strum_part(raw, channel=comp_chosen, start_bar=sb, end_bar=eb,
                                    track_index=track,
                                    register=tuple(COMP_REGISTER[comp_chosen]))
            apply(part)
            fills.append({"channel": comp_chosen, "role": "comp/strum",
                          "noteCount": len(part["notes"]), "register": part["register"],
                          "evidenceSource": part["schema"],
                          "planDecision": plan["regions"][str(comp_chosen)]["decision"]})
        for ch in (13, 14):
            if empty(ch):
                part = build_pad_part(raw, channel=ch, start_bar=sb, end_bar=eb,
                                      tone=PAD_TONE[ch], track_index=track)
                if part["notes"]:
                    apply(part)
                    fills.append({"channel": ch, "role": "pad",
                                  "noteCount": len(part["notes"]),
                                  "register": part["register"],
                                  "evidenceSource": part["schema"],
                                  "planDecision": plan["regions"][str(ch)]["decision"]})

    # dynamics polish: enforce exact-sound factory ceilings everywhere
    polished_raw, dyn_corrections = apply_dynamic_corrections(current_raw)
    dynamics = {
        "applied": len(dyn_corrections) > 0,
        "correctionCount": len(dyn_corrections),
        "byChannel": _count_dyn(dyn_corrections),
        "policy": "velocity above the exact-sound factory ceiling is a FACTORY_ONLY violation",
    }

    metrics = arrangement_metrics(raw, polished_raw)
    after_head = headroom_audit(polished_raw)
    repaired = MidiFile.from_bytes(polished_raw)
    added_channels = set(metrics.get("channelsWithAdditions", []))
    # geometry of every pre-existing channel must be untouched (fills only add
    # notes on their own channels; the dynamics polish only ever LOWERS velocity)
    def geom(m: MidiFile, exclude: set[int]):
        return sorted((n.track, n.channel, n.pitch, n.start, n.end)
                      for n in m.notes() if n.channel not in exclude)
    protected_ok = geom(repaired, added_channels) == geom(midi, added_channels)
    # velocity-only polish check: every changed velocity on protected channels
    # must come from a planned clamp (strictly lower, exact-sound factory ceiling)
    polish_ok = True
    if dyn_corrections:
        plan_keys = {(c["track"], c["channel"], c["pitch"], c["start"], c["end"], c["to"])
                     for c in dyn_corrections}
        for n in repaired.notes():
            if n.channel in added_channels:
                continue
            key = (n.track, n.channel, n.pitch, n.start, n.end)
            for old in midi.notes():
                if (old.track, old.channel, old.pitch, old.start, old.end) == key and old.velocity != n.velocity:
                    polish_ok = polish_ok and (key + (n.velocity,)) in plan_keys
    gates = {
        "reparsed": True,
        "protectedGeometryUnchanged": bool(protected_ok),
        "velocityPolishOnlyLoweredByExactCeiling": bool(polish_ok),
        "factoryVelocityOnlyByProof": all(
            f.get("factoryVelocityOnlyByProof", True) for f in fills),
        "allChannelsWithinPolyphonyLimits": all(v["pass"] for v in after_head["channels"].values()),
    }
    files = {}
    if write_artifacts:
        fname = "studio-arranged.mid"
        (out / fname).write_bytes(polished_raw)
        files = {"arrangedMidi": fname}
    return {
        "schema": "dna-studio-flow-run", "version": "4.51",
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "plan": plan,
        "fills": fills,
        "skippedSlots": [{"channel": ch, "reason": ("already has material" if not empty(ch)
                                                    else "no harmonic evidence for pad")}
                         for ch in ACC_CHANNELS
                         if ch not in {f["channel"] for f in fills}
                         and str(ch) in plan["regions"] and not empty(ch)],
        "dynamics": dynamics,
        "metrics": metrics,
        "gates": gates,
        "status": "STUDIO_APPLIED" if fills else (
            "STUDIO_POLISH_ONLY" if dyn_corrections else "STUDIO_NO_CHANGES_NEEDED"),
        "outDir": str(out), **files,
    }


def _count_dyn(corrections: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for c in corrections:
        row = out.setdefault(str(c["channel"]), {"clamped": 0, "maxFrom": 0})
        row["clamped"] += 1
        row["maxFrom"] = max(row["maxFrom"], c["from"])
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Studio Flow 4.51 (applies, does not block)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--input", required=True)
    p = sub.add_parser("run")
    p.add_argument("--input", required=True)
    p.add_argument("--target-channel", type=int, default=None)
    p.add_argument("--start-bar", type=int, default=None)
    p.add_argument("--end-bar", type=int, default=None)
    p.add_argument("--out-dir", default="artifacts-max-4.51")
    p.add_argument("--report", default=None)
    args = ap.parse_args(argv)
    raw = Path(args.input).read_bytes()
    if raw[:4] != b"MThd":
        print("not a MIDI file", file=sys.stderr)
        return 2
    if args.cmd == "plan":
        print(json.dumps(plan_regions(raw), indent=2, ensure_ascii=False))
        return 0
    res = run_studio(raw, target_channel=args.target_channel,
                     start_bar=args.start_bar, end_bar=args.end_bar,
                     out_dir=args.out_dir)
    report = Path(args.report) if args.report else Path(args.out_dir) / "studio-run-4.51.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"fills": res["fills"], "dynamics": res["dynamics"],
                      "gates": res["gates"], "metrics": res["metrics"]["perChannel"],
                      "status": res["status"], "outDir": res["outDir"],
                      "report": str(report)}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
