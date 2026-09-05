"""
Studio Flow 4.50 — one entry that turns a MIDI song into an instrument-aware,
headroom-safe arrangement step with a measurable quality report.

Flow (all deterministic, evidence-only, no torch needed):

    analyze (song map) -> plan (arranger_planner.plan_regions: per-channel
    instrument-fit + warrants + fill candidates) -> execute the highest-priority
    warranted fill (empty acc slot, strum evidence on the PA800 guitar slot) ->
    metrics (arrangement_metrics: register fit, chord tones, density, dynamics,
    headroom) -> gates (reparse, protected channels diff, factory-only velocity,
    polyphony budget) -> artifacts + full JSON report.

Never edits a channel that already has material; never invents instruments
without evidence; the production default pipeline stays untouched — this is the
opt-in studio layer on top.
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

from dna_midi_studio.midi import MidiFile  # noqa: E402
from dna_midi_studio.song_understanding import analyze_song_map  # noqa: E402
from dna_midi_studio.arranger_contract import (  # noqa: E402
    ACC_CHANNELS, STRUM_REGISTER, chord_pc_set, protected_snapshot,
)
from dna_midi_studio.arranger_planner import plan_regions  # noqa: E402
from dna_midi_studio.arranger_pro import build_strum_part, headroom_audit  # noqa: E402

FILL_ROLE_BY_SLOT = {11: "rhythm-guitar", 12: "rhythm-guitar", 13: "rhythm-guitar",
                     14: "rhythm-guitar", 15: "rhythm-guitar"}


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------
def arrangement_metrics(raw_before: bytes, raw_after: bytes) -> dict[str, Any]:
    """Compare before/after: additions, register fit, chord tones, headroom."""
    midi_b = MidiFile.from_bytes(raw_before)
    midi_a = MidiFile.from_bytes(raw_after)
    notes_b = {(n.track, n.channel, n.pitch, n.start, n.end): n for n in midi_b.notes()}
    notes_a = {(n.track, n.channel, n.pitch, n.start, n.end): n for n in midi_a.notes()}
    added = [notes_a[k] for k in notes_a if k not in notes_b]
    removed = [notes_b[k] for k in notes_b if k not in notes_a]
    channels_added = sorted({n.channel for n in added})
    per_channel: dict[str, Any] = {}
    try:
        song = analyze_song_map(raw_after, "metrics-source.mid")
        cells = song["chordCells"]
    except Exception:
        cells = []

    def cell_at(tick: int):
        for c in cells:
            if int(c["startTick"]) <= tick < int(c["endTick"]):
                return c
        return None

    for ch in channels_added:
        ch_added = [n for n in added if n.channel == ch]
        if not ch_added:
            continue
        chord_checked = chord_hits = 0
        unknown_cells = 0
        out_of_register = 0
        for n in ch_added:
            cell = cell_at(n.start)
            pc = chord_pc_set(cell) if cell else None
            if pc is None:
                unknown_cells += 1
                continue
            chord_checked += 1
            if (n.pitch % 12) in pc:
                chord_hits += 1
            if not (STRUM_REGISTER[0] <= n.pitch <= STRUM_REGISTER[1]):
                out_of_register += 1
        vels = [n.velocity for n in ch_added]
        per_channel[str(ch)] = {
            "addedNotes": len(ch_added),
            "chordToneRatio": round(chord_hits / max(1, chord_checked), 3) if chord_checked else None,
            "chordToneChecked": chord_checked,
            "cellsWithoutChordQuality": unknown_cells,
            "outOfStrumRegister": out_of_register,
            "register": [min(n.pitch for n in ch_added), max(n.pitch for n in ch_added)],
            "avgVelocity": round(sum(vels) / len(vels), 1) if vels else None,
            "velocityMin": min(vels) if vels else None,
            "velocityMax": max(vels) if vels else None,
        }
    head_before = headroom_audit(raw_before)
    head_after = headroom_audit(raw_after)
    return {
        "schema": "dna-studio-flow-metrics", "version": "4.50",
        "addedNotesTotal": len(added), "removedNotesTotal": len(removed),
        "channelsWithAdditions": channels_added,
        "perChannel": per_channel,
        "headroomBefore": {k: v["pass"] for k, v in head_before["channels"].items()},
        "headroomAfter": {k: v["pass"] for k, v in head_after["channels"].items()},
        "mixNoteCountBefore": len(midi_b.notes()), "mixNoteCountAfter": len(midi_a.notes()),
        "policy": "notes outside the target channel must never change; velocity factory-only",
    }


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------
def run_studio(raw: bytes, *, target_channel: int | None = None,
               start_bar: int | None = None, end_bar: int | None = None,
               out_dir: str | Path | None = None,
               write_artifacts: bool = True) -> dict[str, Any]:
    plan = plan_regions(raw)
    fills = list(plan["fillCandidates"])
    chosen = target_channel
    if chosen is None:
        chosen = fills[0] if fills else None
    elif chosen not in ACC_CHANNELS:
        raise ValueError(f"target channel {chosen} not in accompaniment set {ACC_CHANNELS}")
    elif plan["regions"][str(chosen)]["decision"] != "FILL_EMPTY":
        raise ValueError(f"channel {chosen} already has material — studio never overwrites it "
                         "(plan decision: {plan['regions'][str(chosen)]['decision']})")

    out = Path(out_dir) if out_dir else ROOT / "artifacts-max-4.50"
    out.mkdir(parents=True, exist_ok=True)

    if chosen is None:
        arranged_raw = raw
        execution: dict[str, Any] = {"executed": False,
                                     "reason": "no empty accompaniment slot — nothing auto-executed"}
    else:
        midi = MidiFile.from_bytes(raw)
        song = analyze_song_map(raw, "studio-source.mid")
        bars = song["bars"]
        sb = start_bar or 1
        eb = end_bar or len(bars)
        track = max((n.track for n in midi.notes()),
                    key=lambda t: (sum(1 for n in midi.notes() if n.track == t), -t)) \
            if midi.notes() else 0
        strum = build_strum_part(raw, channel=chosen, start_bar=sb, end_bar=eb,
                                 track_index=track)
        start_tick = int(bars[sb - 1]["startTick"])
        end_tick = int(bars[eb - 1]["endTick"])
        arranged = midi.replace_notes(track_index=track, channel=chosen,
                                      start_tick=start_tick, end_tick=end_tick,
                                      new_notes=strum["notes"])
        arranged_raw = arranged.to_bytes()
        proofs = strum.get("velocityProofs") or []
        execution = {
            "executed": True, "channel": chosen,
            "role": FILL_ROLE_BY_SLOT.get(chosen, "rhythm-guitar"),
            "slot": plan["regions"][str(chosen)]["slot"],
            "bars": [sb, eb], "noteCount": len(strum["notes"]),
            "register": list(STRUM_REGISTER),
            "factoryStrumSourceIds": strum["factoryStrumSourceIds"],
            "polyphonyBudget": plan["regions"][str(chosen)]["budget"],
            "velocityProofsTotal": len(proofs),
            "factoryVelocityOnlyByProof": bool(proofs) and all(
                p.get("authority") == "FACTORY_ONLY" for p in proofs),
        }

    metrics = arrangement_metrics(raw, arranged_raw)
    reparsed_ok = True
    MidiFile.from_bytes(arranged_raw)  # raises on corrupt output
    protected_ok = protected_snapshot(MidiFile.from_bytes(arranged_raw),
                                      exclude_channels=()) == \
        protected_snapshot(MidiFile.from_bytes(raw), exclude_channels=())
    # protected diff without target channel when execution happened
    if execution["executed"]:
        ch = execution["channel"]
        protected_ok = protected_snapshot(MidiFile.from_bytes(arranged_raw), exclude_channels=(ch,)) == \
            protected_snapshot(MidiFile.from_bytes(raw), exclude_channels=(ch,))
    after_head = headroom_audit(arranged_raw)
    gates = {
        "reparsed": reparsed_ok,
        "protectedChannelsUnchanged": bool(protected_ok),
        "factoryVelocityOnly": bool(execution.get("executed"))
        and bool(execution.get("factoryVelocityOnlyByProof")),
        "allChannelsWithinPolyphonyLimits": all(v["pass"] for v in after_head["channels"].values()),
    }
    files = {}
    if write_artifacts:
        fname = f"studio-arranged.ch{chosen or 'none'}.mid" if execution["executed"] else "studio-arranged.unchanged.mid"
        (out / fname).write_bytes(arranged_raw)
        files = {"arrangedMidi": fname}
    result = {
        "schema": "dna-studio-flow-run", "version": "4.50",
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "plan": plan,
        "execution": execution,
        "metrics": metrics,
        "gates": gates,
        "status": "STUDIO_RUN_COMPLETE" if execution["executed"] else "STUDIO_RUN_NO_ACTION",
        "outDir": str(out), **files,
    }
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Studio Flow 4.50")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--input", required=True)
    p = sub.add_parser("run")
    p.add_argument("--input", required=True)
    p.add_argument("--target-channel", type=int, default=None)
    p.add_argument("--start-bar", type=int, default=None)
    p.add_argument("--end-bar", type=int, default=None)
    p.add_argument("--out-dir", default="artifacts-max-4.50")
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
    report = Path(args.report) if args.report else Path(args.out_dir) / "studio-run-4.50.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"plan": res["plan"]["summary"], "execution": res["execution"],
                      "gates": res["gates"], "metricsSummary": res["metrics"]["perChannel"],
                      "outDir": res["outDir"], "report": str(report)},
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
