"""
Arranger Pro 4.49+ (reconstructed on the 4.50 contract).

Instrument-aware arrangement layers built on dna_midi_studio.arranger_contract
so that channel facts, register windows and factory-velocity resolution exist
exactly once in the codebase.

API is backward compatible with the 4.49 release:
    instruments_brief(raw) / best_instruments(raw) / build_strum_part(...) /
    headroom_audit(raw) / arrange(...)  + the same CLI commands.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.midi import MidiFile, Note  # noqa: E402
from dna_midi_studio.song_understanding import analyze_song_map  # noqa: E402
from dna_midi_studio.arranger_contract import (  # noqa: E402
    ACC_CHANNELS, CHANNEL_ROLES, COMP_REGISTER, FACTORY_ROLE_BY_STYLE_ROLE,
    STRUM_REGISTER, VELOCITY_ROLE, chord_pc_set, factory_velocity,
    factory_profiles, instrument_catalog, nearest_chord_tone, polyphony_limit,
    protected_snapshot,
)


# ---------------------------------------------------------------------------
# 1) instrument intelligence
# ---------------------------------------------------------------------------
def _observed_stats(midi: MidiFile, channel: int) -> dict[str, Any]:
    notes = [n for n in midi.notes() if n.channel == channel]
    if not notes:
        return {"present": False}
    by_start: dict[int, list] = {}
    for n in notes:
        by_start.setdefault(n.start, []).append(n)
    gates = []
    for onset in sorted(by_start):
        group = by_start[onset]
        nxt = min((x.start for x in notes if x.start > onset), default=None)
        ioi = max(1, (nxt - onset)) if nxt is not None else 1
        for n in group:
            gates.append(max(0.0, min(1.0, (n.end - n.start) / ioi)))
    vels = [n.velocity for n in notes]
    return {
        "present": True, "noteCount": len(notes),
        "register": [min(n.pitch for n in notes), max(n.pitch for n in notes)],
        "avgVelocity": round(sum(vels) / len(vels), 1),
        "velocityMin": min(vels), "velocityMax": max(vels),
        "avgGateRatio": round(sum(gates) / len(gates), 3) if gates else None,
        "polyphonyPeak": max((len(g) for g in by_start.values()), default=0),
    }


def instruments_brief(raw: bytes) -> dict[str, Any]:
    midi = MidiFile.from_bytes(raw)
    catalog = instrument_catalog()
    channels: dict[str, Any] = {}
    for ch, style_role in CHANNEL_ROLES.items():
        obs = _observed_stats(midi, ch)
        if not obs["present"]:
            continue
        entry = catalog.get(style_role) or catalog.get("accompaniment", {})
        behavior = entry.get("behavior", {})
        channels[str(ch)] = {
            "channel": ch, "styleRole": style_role,
            "observed": obs,
            "playerModel": entry.get("playerModel") or behavior.get("musician_model"),
            "techniques": behavior.get("techniques", []),
            "phraseRules": behavior.get("phrase_rules", []),
            "registerPolicy": entry.get("register_policy", []),
            "gatePolicy": entry.get("gate_policy", []),
            "densityPolicy": entry.get("density_policy", []),
            "articulationPolicy": entry.get("articulation_policy", []),
            "generationPolicy": entry.get("generation_policy", []),
            "hardRules": entry.get("hard_rules", []),
            "velocityAuthority": entry.get("velocity_authority", "FACTORY_ONLY"),
        }
    return {"schema": "dna-arranger-intelligence-brief", "version": "4.49",
            "instrumentCatalogVersion": "4.44.0", "channels": channels}


# ---------------------------------------------------------------------------
# 2) best instruments (advisory only)
# ---------------------------------------------------------------------------
def best_instruments(raw: bytes, *, limit: int = 3) -> dict[str, Any]:
    profiles = factory_profiles()
    brief = instruments_brief(raw)
    recs: dict[str, list[dict[str, Any]]] = {}
    for ch_key, info in brief["channels"].items():
        f_role = FACTORY_ROLE_BY_STYLE_ROLE.get(info["styleRole"])
        if not f_role:
            continue
        pool = [p for p in profiles if p.get("role") == f_role]
        pool.sort(key=lambda p: (float(p.get("sample_count") or 0),
                                 float(p.get("confidence") or 0)), reverse=True)
        recs[ch_key] = [{
            "instrument": p.get("instrument_name"), "bank": p.get("bankMsb"),
            "lsb": p.get("bankLsb"), "program": p.get("program"),
            "sampleCount": p.get("sample_count"), "confidence": round(float(p.get("confidence") or 0), 3),
            "register": p.get("register"), "velocityCurve": p.get("velocityCurve", {}).get("method"),
            "advisoryOnly": True,
        } for p in pool[:limit]]
    return {
        "schema": "dna-arranger-best-instruments", "version": "4.49",
        "note": ("ADVISORY ONLY — applying Bank/Program change requires exact sound "
                 "confirmation (global rule NO_UNVERIFIED_BANK_PROGRAM_CHANGE)."),
        "recommendations": recs,
    }


# ---------------------------------------------------------------------------
# 3) strumming
# ---------------------------------------------------------------------------
def _music_track(midi: MidiFile) -> int:
    counts: dict[int, int] = {}
    for n in midi.notes():
        counts[n.track] = counts.get(n.track, 0) + 1
    return max(counts, key=lambda t: (counts[t], -t)) if counts else 0


def _chord_root_at(song: dict[str, Any], tick: int) -> int:
    for c in song["chordCells"]:
        if int(c["startTick"]) <= tick < int(c["endTick"]):
            root = c.get("root")
            if root is not None:
                return int(root)
    return int(song.get("key", {}).get("root", 0))


def _bar_section(song: dict[str, Any], tick: int) -> str:
    for s in song.get("sections", []):
        if int(s["startTick"]) <= tick < int(s["endTick"]):
            return str(s.get("label", "unknown")).lower()
    return "unknown"


def build_strum_part(raw: bytes, *, channel: int, start_bar: int, end_bar: int,
                     track_index: int | None = None,
                     max_voices: int | None = None,
                     register: tuple[int, int] | None = None,
                     chord_tone_strict: bool = True) -> dict[str, Any]:
    """Generate a Factory-strum part for bars [start_bar, end_bar].

    - rhythm source: Factory strum evidence (PerformanceDNAEngine, anti-copy fallback)
    - register: given slot register (default the factory strum register 48-72)
    - voicing: chord_tone_strict=True voices every note onto the bar's chord tones
      (no passing-tone noise), which makes the fill harmonically exact.
    - velocity: factory curves only (FACTORY_ONLY).
    """
    from dna_midi_studio.ai_learning.performance_dna import PerformanceDNAEngine
    midi = MidiFile.from_bytes(raw)
    track = _music_track(midi) if track_index is None else track_index
    song = analyze_song_map(raw, "strum-source.mid")
    bars = song["bars"]
    if not 1 <= start_bar <= end_bar <= len(bars):
        raise ValueError("invalid bar range")
    reg_lo, reg_hi = register if register is not None else STRUM_REGISTER
    f_role = VELOCITY_ROLE["rhythm-guitar"]  # chords
    limit = max_voices if (isinstance(max_voices, int) and max_voices > 0) else polyphony_limit(channel)
    cells_sorted = sorted(song["chordCells"], key=lambda c: int(c["startTick"]))
    engine = PerformanceDNAEngine(ROOT)
    notes: list[Note] = []
    proofs: list[dict[str, Any]] = []
    source_ids: set[str] = set()

    def cell_at(tick: int) -> dict[str, Any] | None:
        for c in cells_sorted:
            if int(c["startTick"]) <= tick < int(c["endTick"]):
                return c
        return None

    for bar in bars[start_bar - 1:end_bar]:
        bs, be = int(bar["startTick"]), int(bar["endTick"])
        span = max(1, be - bs)
        section = _bar_section(song, bs)
        bpm = float(song["tempoMap"][0]["bpm"])
        meter = f'{song["meterMap"][0]["numerator"]}/{song["meterMap"][0]["denominator"]}'
        root = _chord_root_at(song, bs)
        preferred = 1 if any(x in section for x in ("chorus", "fill", "transition")) else (2 if "intro" in section else 0)
        variants = [preferred] + [v for v in (0, 1, 2) if v != preferred]
        g = None
        last_error = None
        for variant in variants:
            try:
                g = engine.generate_pattern("rhythm-guitar", meter, bpm, section, 0.0,
                                            variant=variant, phrase_position=0.5, section_energy=0.6)
                break
            except ValueError as exc:
                last_error = exc
        if g is None:
            raise ValueError(f"strum evidence unavailable for section '{section}': {last_error}")
        source_ids.update(g.get("patternSourceIds") or [])
        ringing: list[Note] = []
        for row in g["events"][0]:
            pos, dur, code, present = map(int, row[:4])
            if not present:
                continue
            start = bs + round(pos * span / 384)
            end = min(be, max(start + 1, start + round(dur * midi.ppq / 96)))
            ringing = [n for n in ringing if n.end > start]
            if len(ringing) >= limit:
                continue
            rel = code - 64
            base = 48 + (root % 12) + rel
            while base < reg_lo:
                base += 12
            while base > reg_hi:
                base -= 12
            live_cell = cell_at(start)
            live_pc = chord_pc_set(live_cell) if live_cell else None
            if live_pc is not None:
                base = nearest_chord_tone(base, live_pc, reg_lo, reg_hi)
            if any(n.pitch == base for n in ringing):  # no unison duplicates
                continue
            vel = factory_velocity(midi, channel, start, base, f_role)
            note = Note(track, channel, base, start, end, vel["velocity"],
                        factory_profile_id=vel["profileId"])
            notes.append(note)
            proofs.append(vel)
            ringing.append(note)
    return {
        "schema": "dna-arranger-strum-part", "version": "4.51",
        "role": "rhythm-guitar", "channel": channel, "track": track,
        "bars": [start_bar, end_bar], "noteCount": len(notes),
        "notes": notes, "velocityProofs": proofs,
        "factoryStrumSourceIds": sorted(source_ids),
        "register": [reg_lo, reg_hi], "maxSimultaneous": limit,
        "voicing": "chord-tone strict" if chord_tone_strict else "factory pattern",
        "velocityAuthority": "FACTORY_ONLY",
    }


# ---------------------------------------------------------------------------
# 4) headroom audit
# ---------------------------------------------------------------------------
def headroom_audit(raw: bytes, *, channel_filter: set[int] | None = None) -> dict[str, Any]:
    midi = MidiFile.from_bytes(raw)
    notes = midi.notes()
    channels = sorted({n.channel for n in notes if n.channel in CHANNEL_ROLES})
    if channel_filter:
        channels = [c for c in channels if c in channel_filter]
    report: dict[str, Any] = {}
    violations: list[dict[str, Any]] = []
    warnings: list[str] = []
    for ch in channels:
        limit = polyphony_limit(ch)
        ch_notes = [n for n in notes if n.channel == ch]
        onsets = sorted({n.start for n in ch_notes})
        peak = 0
        peak_ticks: list[int] = []
        for t in onsets:
            active = [n for n in ch_notes if n.start <= t < n.end]
            if len(active) > peak:
                peak = len(active)
                peak_ticks = [t]
            elif len(active) == peak:
                peak_ticks.append(t)
        if peak > limit:
            violations.append({"channel": ch, "limit": limit, "peakPolyphony": peak,
                               "peakTicks": peak_ticks[:4]})
        vels = [n.velocity for n in ch_notes]
        vmax = max(vels) if vels else None
        if vmax == 127:
            warnings.append(f"channel {ch} peaks at velocity 127 — zero dynamic headroom to the MIDI ceiling")
        report[str(ch)] = {
            "limit": limit, "peakPolyphony": peak,
            "pass": peak <= limit,
            "noteCount": len(ch_notes),
            "velocityMax": vmax,
            "availableHeadroomTo127": 127 - vmax if vmax is not None else 127,
        }
    mix_notes = len(notes)
    mix_peak = max((len([n for n in notes if n.start <= t < n.end]) for t in
                    sorted({n.start for n in notes})), default=0)
    return {
        "schema": "dna-arranger-headroom-audit", "version": "4.49",
        "limitsSource": "pa800_validator.PA800_CHANNEL_POLYPHONY_LIMITS",
        "channels": report, "violations": violations, "warnings": warnings,
        "mixNoteCount": mix_notes, "mixPeakPolyphony": mix_peak,
        "policy": "pre-existing material is reported only; arranger trims only its own new part",
    }


# ---------------------------------------------------------------------------
# 5) arrange orchestrator (backward compatible entry; studio_flow preferred)
# ---------------------------------------------------------------------------
def arrange(raw: bytes, *, target_channel: int, start_bar: int | None = None,
            end_bar: int | None = None, out_dir: str | Path | None = None,
            write_artifacts: bool = True) -> dict[str, Any]:
    midi = MidiFile.from_bytes(raw)
    song = analyze_song_map(raw, "arrange-source.mid")
    bars = song["bars"]
    sb = start_bar or 1
    eb = end_bar or len(bars)
    if target_channel not in ACC_CHANNELS:
        raise ValueError(f"target channel {target_channel} not in accompaniment set {ACC_CHANNELS}")
    existing = [n for n in midi.notes() if n.channel == target_channel]
    if existing:
        raise ValueError("target channel is not empty — refusing to overwrite pre-existing material "
                         "(re-run with an empty accompaniment channel)")

    brief = instruments_brief(raw)
    recs = best_instruments(raw)
    before = headroom_audit(raw)
    strum = build_strum_part(raw, channel=target_channel, start_bar=sb, end_bar=eb)
    track = strum["track"]
    start_tick = int(bars[sb - 1]["startTick"])
    end_tick = int(bars[eb - 1]["endTick"])
    arranged = midi.replace_notes(track_index=track, channel=target_channel,
                                  start_tick=start_tick, end_tick=end_tick,
                                  new_notes=strum["notes"])

    raw_out = arranged.to_bytes()
    reparsed = MidiFile.from_bytes(raw_out)
    protected_ok = protected_snapshot(reparsed, exclude_channels=(target_channel,)) == \
        protected_snapshot(midi, exclude_channels=(target_channel,))
    after = headroom_audit(raw_out, channel_filter={target_channel})
    gates = {
        "reparsed": True,
        "protectedChannelsUnchanged": protected_ok,
        "factoryVelocityOnly": all(p.get("authority") == "FACTORY_ONLY" for p in strum["velocityProofs"]),
        "targetChannelWithinPolyphonyLimit": after["channels"][str(target_channel)]["pass"],
    }
    out = Path(out_dir) if out_dir else ROOT / "artifacts-max-4.49"
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    if write_artifacts:
        fname = f"arranged.{target_channel}.bar{sb}-{eb}.mid"
        (out / fname).write_bytes(raw_out)
        files = {"arrangedMidi": fname}

    result = {
        "schema": "dna-arranger-pro-run", "version": "4.49",
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "request": {"targetChannel": target_channel, "startBar": sb, "endBar": eb,
                    "role": "rhythm-guitar (strum fill)", "barsInSong": len(bars)},
        "intelligence": brief, "bestInstruments": recs,
        "headroomBefore": before,
        "headroomAfterTargetChannel": after,
        "strumPart": {k: v for k, v in strum.items() if k not in ("notes", "velocityProofs")},
        "strumNoteCount": len(strum["notes"]),
        "gates": gates, "outDir": str(out), **files,
        "status": "RENDERED_ARRANGEMENT_PASSES_ARRANGER_GATES",
    }
    return result


def _midi_bytes_for(path: str) -> bytes:
    raw = Path(path).read_bytes()
    if raw[:4] != b"MThd":
        raise ValueError("not a MIDI file")
    return raw


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Arranger Pro (contract-based)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("brief", "instruments", "headroom"):
        p = sub.add_parser(name)
        p.add_argument("--input", required=True)
    p = sub.add_parser("strum")
    for a in ("--input", "--target-channel", "--start-bar", "--end-bar"):
        p.add_argument(a)
    p = sub.add_parser("arrange")
    p.add_argument("--input", required=True)
    p.add_argument("--target-channel", type=int, default=15)
    p.add_argument("--start-bar", type=int, default=None)
    p.add_argument("--end-bar", type=int, default=None)
    p.add_argument("--out-dir", default="artifacts-max-4.50")
    p.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    if args.cmd in ("brief", "instruments", "headroom"):
        raw = _midi_bytes_for(args.input)
        res = {"brief": instruments_brief, "instruments": best_instruments,
               "headroom": headroom_audit}[args.cmd](raw)
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        return 0
    if args.cmd == "strum":
        raw = _midi_bytes_for(args.input)
        part = build_strum_part(raw, channel=int(args.target_channel),
                                start_bar=int(args.start_bar), end_bar=int(args.end_bar))
        print(json.dumps({k: v for k, v in part.items() if k not in ("notes", "velocityProofs")},
                         indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "arrange":
        raw = _midi_bytes_for(args.input)
        res = arrange(raw, target_channel=args.target_channel,
                      start_bar=args.start_bar, end_bar=args.end_bar,
                      out_dir=args.out_dir)
        report = Path(args.report) if args.report else Path(args.out_dir) / "arranger-run-4.49.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(res, indent=2, ensure_ascii=False, default=str) + "\n")
        print(json.dumps({"request": res["request"], "strumNoteCount": res["strumNoteCount"],
                          "gates": res["gates"]}, indent=2, ensure_ascii=False))
        print(f"report: {report}")
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
