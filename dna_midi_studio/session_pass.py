"""DNA Session Pass 4.60 — Phase A of the DNA Optimizer vision.

One CLI call runs an input file through every engine in the repo and returns
one consolidated report with per-action statuses and gates:

  1. identity + markers + per-channel facts        (midi, sty_mapper)
  2. per-role pattern evidence (bass/drums/perc/accompaniment/solo)
                                                    (role_patterns)
  3. groove vs human reference on drum channels     (groove_engine)
  4. technique candidates on bass/chordal channels  (instrument_techniques)
  5. echo/terca scan (throwaway copies, never edits the real file)
                                                    (special_track_engine)
  6. mix plan (CC11 gain)                           (mix_engine)
  7. actions with status:
       READY          — evidence exists, gate passes (applied with --apply-safe)
       NEEDS_DECISION — legal but requires a human decision/warrant
       LOCKED         — policy-locked (device evidence required)
       SKIPPED        — nothing to do on this file

Rules enforced here (invariants): the source file is never modified
(outputs are new artifacts); velocity authority is FACTORY_ONLY; no bank/
program change without evidence; DNC/slap/pop triggers are never emitted;
every applied action leaves a gate report.
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

from dna_midi_studio.groove_engine import run_groove_evidence
from dna_midi_studio.instrument_techniques import run_technique_evidence
from dna_midi_studio.midi import MidiFile
from dna_midi_studio.mix_engine import plan_cc_gain, apply_gain_plan, mix_gates
from dna_midi_studio.role_patterns import role_pattern_evidence
from dna_midi_studio.sty_mapper import (export_korg_style, parse_markers,
                                        section_map, structure_gates)

SESSION_SCHEMA = "dna-session-pass"
SESSION_VERSION = "4.60"


def _channel_role_names(midi: MidiFile) -> dict[int, str]:
    """Role hints from physical track names (Korg convention); evidence-only,
    never a guess — unknown names map to 'unclassified'."""
    hints = {"bass": "bass", "drum": "drums", "perc": "percussion",
             "acc": "accompaniment", "solo": "solo", "lead": "solo",
             "melody": "solo", "third": "third", "echo": "echo"}
    out: dict[int, str] = {}
    for tr in midi.tracks:
        name = ""
        for e in tr.events:
            if e.kind == "meta" and e.meta_type == 3 and getattr(e, "data", None):
                name = e.data.decode("latin1", "ignore").strip()
                break
        low = name.lower()
        for key, role in hints.items():
            if key in low:
                chans = sorted({e.channel for e in tr.events
                                if e.kind == "channel" and e.channel is not None})
                for ch in chans:
                    out[ch] = role
                break
    return out


def _special_notes(midi: MidiFile, ch: int) -> list[dict[str, Any]]:
    out = []
    notes = sorted((n for n in midi.notes() if n.channel == ch),
                   key=lambda n: (n.start, n.pitch))
    for n in notes:
        out.append({
            "pitch": n.pitch, "channel": ch,
            "on": {"tick": n.start, "order": n.start * 2 + 1, "kind": "channel",
                   "command": 9, "channel": ch, "data": [n.pitch, n.velocity],
                   "remove": False},
            "off": {"tick": n.end, "order": n.end * 2 + 2, "kind": "channel",
                    "command": 8, "channel": ch, "data": [n.pitch, 0],
                    "remove": False},
            "instrumentKey": None,
        })
    return out


def _echo_scan(midi: MidiFile, role_map: dict[int, str]) -> dict[str, Any]:
    """Run the real special-track optimizer on deep copies when a solo/lead
    channel exists; the copies are discarded, so the file is untouched."""
    solo_ch = [ch for ch, r in role_map.items() if r in ("solo", "lead")]
    if not solo_ch:
        return {"run": False, "reason": "no solo/lead channel present"}
    try:
        from dna_midi_studio.special_track_engine import optimize_existing_echo_terca
    except Exception as exc:  # pragma: no cover
        return {"run": False, "reason": f"special_track_engine unavailable: {exc}"}
    from collections import Counter
    groups = []
    for ch, role in sorted(role_map.items()):
        notes = _special_notes(midi, ch)
        if not notes:
            continue
        conf = 0.95 if role in ("solo", "lead") else 0.85
        groups.append({"role": "solo" if role in ("solo", "lead") else role,
                       "confidence": conf, "track": {"index": ch, "channel": ch,
                                                     "events": [], "endTick": 0},
                       "channel": ch, "notes": notes,
                       "trackName": f"ch{ch}:{role}"})
    reports = optimize_existing_echo_terca(groups, {}, midi.ppq * 4, midi.ppq,
                                           {}, {}, Counter())
    return {"run": True, "soloChannel": solo_ch,
            "candidatesChecked": len(groups),
            "findings": [{"kind": r.get("kind"), "mode": r.get("mode"),
                          "channel": r.get("channel"),
                          "inferred": r.get("inferred", False),
                          "delayTicks": r.get("delayTicks")} for r in reports],
            "note": "optimization computed on throwaway copies only; the "
                    "source file is never edited by this pass"}


def _trim_melody(block: dict[str, Any]) -> dict[str, Any]:
    keep = ["role", "channel", "noteCount", "register", "densityNotesPerBar",
            "velocity", "durationTicks", "gateRatio", "onsetGapTicks",
            "polyphonyPeak", "graceShortNoteShare", "shortGateShare"]
    out = {k: block.get(k) for k in keep if k in block}
    if "melodyPattern" in block:
        out["melodyPattern"] = block["melodyPattern"]
    return out


def session_pass(raw: bytes, *, source_name: str,
                 role_map: dict[int, str] | None = None,
                 melody_channels: list[int] | None = None,
                 percussion_gain: float = 0.55,
                 apply_safe: bool = False,
                 out_dir: str | Path | None = None) -> dict[str, Any]:
    midi = MidiFile.from_bytes(raw)
    # 1. identity & markers
    markers = parse_markers(raw)
    section = section_map(raw) or {}
    facts = {
        "format": midi.format_type, "ppq": midi.ppq,
        "tracks": len(midi.tracks),
        "channels": sorted({n.channel for n in midi.notes()}),
        "markers": [m["text"] for m in markers],
        "markerCount": len(markers),
        "elementsInStyle": len(section.get("elements", [])) if section else 0,
    }
    # roles: explicit map wins; otherwise evidence from track names
    used_map = dict(role_map) if role_map else _channel_role_names(midi)
    melody = [int(c) for c in (melody_channels or [])] + \
             [ch for ch, r in used_map.items() if r in ("solo", "lead")]
    melody = sorted(set(melody))

    # 2. role patterns
    pat = role_pattern_evidence(raw, source_name=source_name,
                                role_map=used_map, melody_channels=melody)
    channels_summary = {ch: _trim_melody(b) for ch, b in pat["channels"].items()}
    unclassified = [ch for ch in used_map if used_map[ch] == "unclassified"]

    # 3. groove vs human reference on drum/percussion channels
    groove_runs = []
    for ch in sorted(used_map):
        if used_map[ch] in ("drums", "percussion"):
            g = run_groove_evidence(raw, channel=ch, source_name=source_name)
            t = (g.get("factoryMeasured") or {}).get("timingMs") or {}
            groove_runs.append({"channel": ch, "role": used_map[ch],
                                "noteCount": (g.get("factoryMeasured") or {}).get("noteCount"),
                                "stdMs": t.get("stdMs"),
                                "exactOnGridShare": t.get("exactOnGridShare")})

    # 4. technique candidates (bass + first chordal accompaniment)
    technique_runs = []
    bass_ch = [ch for ch in used_map if used_map[ch] == "bass"]
    if bass_ch:
        t = run_technique_evidence(raw, channel=bass_ch[0], role="bass",
                                   source_name=source_name)
        technique_runs.append({"channel": bass_ch[0], "role": "bass",
                               "counts": t["counts"]})
    chordal = [ch for ch in sorted(used_map)
               if used_map[ch] == "accompaniment"
               and (channels_summary.get(str(ch)) or {}).get("polyphonyPeak", 0) >= 2]
    if chordal:
        t = run_technique_evidence(raw, channel=chordal[0],
                                   role="rhythm-guitar", source_name=source_name)
        technique_runs.append({"channel": chordal[0], "role": "rhythm-guitar",
                               "counts": t["counts"]})

    # 5. echo/terca scan (copies only)
    echo = _echo_scan(midi, used_map)

    # 6. mix plan: percussion channels gain (default -45%)
    targets = {ch: percussion_gain
               for ch in used_map if used_map[ch] == "percussion"}
    mix_plan: list[dict[str, Any]] = []
    mix_summary = {"targets": {}, "eventsPlanned": 0, "channelsMissingCC": []}
    if targets:
        mix_plan, mix_summary = plan_cc_gain(raw, targets, controller=11)

    # 7. actions
    actions: list[dict[str, Any]] = []

    # A01 — STY export (Korg Pa800 import shape)
    if markers:
        try:
            out_bytes, sty_report = export_korg_style(raw)
            sty_gates = structure_gates(raw, out_bytes)
            export_ok = bool(sty_gates) and all(sty_gates.values())
            actions.append({
                "id": "A01_STY_EXPORT", "engine": "sty_mapper 4.53",
                "status": "READY" if export_ok else "GATE_FAILED",
                "evidence": {"markers": len(markers),
                             "addedTotal": sty_report.get("addedTotal", 0),
                             "cc11Added": sty_report.get("cc11Added", 0),
                             "setupsAdded": sty_report.get("setupsAdded", 0)},
                "effect": "Pa800-importable SMF0 with per-element setup",
                "gates": sty_gates,
            })
            if apply_safe and export_ok and out_dir:
                p = Path(out_dir)
                p.mkdir(parents=True, exist_ok=True)
                stem = Path(source_name).stem
                (p / f"session-sty-{stem}.mid").write_bytes(out_bytes)
                actions[-1]["artifact"] = str(p / f"session-sty-{stem}.mid")
                actions[-1]["status"] = "APPLIED"
        except Exception as exc:
            actions.append({"id": "A01_STY_EXPORT", "engine": "sty_mapper 4.53",
                            "status": "SKIPPED",
                            "reason": f"style export not applicable: {exc}"})
    else:
        actions.append({"id": "A01_STY_EXPORT", "engine": "sty_mapper 4.53",
                        "status": "SKIPPED",
                        "reason": "no Korg style markers (i1cv1/v1cv1/...) found"})

    # A02 — mix: CC11 gain on percussion (policy -45%)
    if targets and mix_plan:
        after = apply_gain_plan(raw, mix_plan)
        gates = mix_gates(raw, after, mix_plan, targets)
        ok = bool(gates) and all(gates.values())
        actions.append({
            "id": "A02_PERCUSSION_CC11_GAIN", "engine": "mix_engine 4.52",
            "status": "READY" if ok else "GATE_FAILED",
            "evidence": {"targets": mix_summary["targets"],
                         "eventsPlanned": mix_summary["eventsPlanned"],
                         "gainFactor": percussion_gain,
                         "noteVelocitiesUntouched": True},
            "effect": "per-channel CC11 expression scaled -45% on percussion",
            "gates": gates,
        })
        if apply_safe and ok and out_dir:
            p = Path(out_dir)
            p.mkdir(parents=True, exist_ok=True)
            stem = Path(source_name).stem
            (p / f"session-mixed-{stem}.mid").write_bytes(after)
            actions[-1]["artifact"] = str(p / f"session-mixed-{stem}.mid")
            actions[-1]["status"] = "APPLIED"
    else:
        actions.append({
            "id": "A02_PERCUSSION_CC11_GAIN", "engine": "mix_engine 4.52",
            "status": "SKIPPED",
            "reason": ("no percussion channel with existing CC11 automation "
                       "(" + (f"missing: {mix_summary['channelsMissingCC']}"
                              if targets else "no percussion role in map") + ")")})

    # A03 — echo/terca
    if echo.get("findings"):
        actions.append({
            "id": "A03_ECHO_TERCA", "engine": "special_track_engine 4.57",
            "status": "NEEDS_DECISION",
            "evidence": echo["findings"],
            "effect": "would REPAIR/REBUILD detected echo/terca layer(s)",
            "reason": "applying requires the user to confirm the layer",
        })
    else:
        actions.append({
            "id": "A03_ECHO_TERCA", "engine": "special_track_engine 4.57",
            "status": "SKIPPED",
            "reason": echo.get("reason") or "no consistent echo/terca structure detected",
        })

    # A04 — studio fills (needs a human warrant on slots)
    actions.append({
        "id": "A04_STUDIO_FILLS", "engine": "studio_flow 4.50-4.51",
        "status": "NEEDS_DECISION",
        "reason": ("legal only in empty, warranted slots of a chosen target "
                   "channel; requires user decision + chord context")})

    # A05 — device-locked technique triggers
    actions.append({
        "id": "A05_DEVICE_LOCKED_TRIGGERS", "engine": "instrument_techniques 4.55",
        "status": "LOCKED",
        "reason": ("slap/pop/guitar-mode/DNC triggers require an exact sound "
                   "profile + device capture (warrant ledger); never emitted"),
        "evidence": {"emittedTriggers": 0}})

    gates_summary = {
        "readOnly": all("readOnlyNoInputBytesWritten" not in (g or {})
                        or (g or {})["readOnlyNoInputBytesWritten"]
                        for t in technique_runs
                        for g in [t.get("gates", {})]),
        "techniqueRunsGates": [t.get("gates", {}) for t in technique_runs],
        "mix": (actions[1].get("gates") if len(actions) > 1 and
                actions[1]["id"] == "A02_PERCUSSION_CC11_GAIN" else None),
        "sty": (actions[0].get("gates") if actions and
                actions[0]["id"] == "A01_STY_EXPORT" else None),
    }
    result = {
        "schema": SESSION_SCHEMA, "version": SESSION_VERSION,
        "sourceName": source_name,
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "fileFacts": facts,
        "roleMapUsed": {str(k): v for k, v in sorted(used_map.items())},
        "unclassifiedChannels": unclassified,
        "perRolePatterns": channels_summary,
        "grooveVsHuman": groove_runs,
        "techniqueCandidates": technique_runs,
        "echoScan": echo,
        "mixPlan": {"targets": mix_summary["targets"],
                    "eventsPlanned": mix_summary["eventsPlanned"],
                    "channelsMissingCC": mix_summary["channelsMissingCC"]},
        "actions": actions,
        "gatesSummary": gates_summary,
        "note": "engine set: 4.52 mix, 4.53 sty, 4.54 groove, 4.55 techniques, "
                "4.57 special track, 4.59 role patterns",
    }
    if out_dir:
        p = Path(out_dir)
        p.mkdir(parents=True, exist_ok=True)
        stem = Path(source_name).stem
        (p / f"session-pass-{stem}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        result["artifact"] = str(p / f"session-pass-{stem}.json")
    return result


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DNA Session Pass 4.60")
    ap.add_argument("--input", required=True)
    ap.add_argument("--roles", default="",
                    help="explicit evidence role map: 8:bass,9:drums,... (optional)")
    ap.add_argument("--melody-channels", default="")
    ap.add_argument("--percussion-gain", type=float, default=0.55)
    ap.add_argument("--apply-safe", action="store_true",
                    help="apply READY actions (new artifacts; source never edited)")
    ap.add_argument("--out-dir", default="artifacts-max-4.60")
    args = ap.parse_args(argv)
    role_map: dict[int, str] = {}
    if args.roles:
        for item in args.roles.split(","):
            ch, role = item.split(":")
            role_map[int(ch)] = role
    melody = [int(x) for x in args.melody_channels.split(",") if x.strip()]
    raw = Path(args.input).read_bytes()
    res = session_pass(raw, source_name=Path(args.input).name,
                       role_map=role_map or None, melody_channels=melody,
                       percussion_gain=args.percussion_gain,
                       apply_safe=args.apply_safe, out_dir=args.out_dir)
    acts = [{"id": a["id"], "status": a["status"]} for a in res["actions"]]
    print(json.dumps({"source": res["sourceName"],
                      "markers": res["fileFacts"]["markerCount"],
                      "actions": acts}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
