"""Role Pattern Evidence Engine 4.59 — proves, with measured patterns, what
each role in a file actually plays: bass, drums, percussion, accompaniment
(chordal layers) and solo/melody. Runs AFTER the GUI analysis in the chain
(gui_chain_4.59.mjs) and consumes the same real MIDI files.

Every number is measured from the bytes; nothing is guessed, nothing is
written back. Velocity is reported (FACTORY_ONLY authority: we never change
it). For the solo/melody role the engine also measures contour patterns:
direction shares, interval-size profile, phrasing gaps, short-note share.

Roles are passed in as an explicit channel->role map (evidence from the
GUI run / known arrangement contract, never invented here).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.midi import MidiFile, Note

PATTERN_SCHEMA = "dna-role-pattern-evidence"
PATTERN_VERSION = "4.59"

QUANTILES = (0.20, 0.50, 0.80)
PHRASE_GAP_BEATS = 1.0       # silence gap >= 1 beat -> phrase boundary
GRACE_SHARE_MAX_DUR = 60     # ticks: sub-32nd notes (ornament-like) at ppq 480
SHORT_GATE = 0.35


def _q(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, round(q * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


def _stats(xs: list[float], nd: int = 2) -> dict[str, float]:
    if not xs:
        return {}
    s = sorted(xs)
    out = {"n": len(s), "min": round(s[0], nd), "max": round(s[-1], nd),
           "mean": round(sum(s) / len(s), nd)}
    for q in QUANTILES:
        out[f"q{int(q*100)}"] = round(_q(s, q), nd)
    if len(s) > 1:
        out["std"] = round(statistics.pstdev(s), nd)
    return out


def _tempo_us_per_quarter(midi: MidiFile) -> int:
    for track in midi.tracks:
        for e in track.events:
            if e.kind == "meta" and e.meta_type == 0x51 and len(e.data) >= 3:
                return int.from_bytes(e.data[:3], "big")
    return 500_000


def _interval_profile(notes: list[Note]) -> dict[str, Any]:
    if len(notes) < 2:
        return {"upShare": None, "downShare": None, "sameShare": None,
                "meanAbsSemis": None, "chromaticShare": None,
                "thirdShare": None, "leapShare": None, "meanSemis": None}
    ups = downs = same = chroma = third = leap = 0
    sizes: list[int] = []
    signed: list[int] = []
    for a, b in zip(notes, notes[1:]):
        d = b.pitch - a.pitch
        signed.append(d)
        sizes.append(abs(d))
        if d > 0:
            ups += 1
        elif d < 0:
            downs += 1
        else:
            same += 1
        if abs(d) == 1:
            chroma += 1
        if abs(d) in (3, 4):
            third += 1
        if abs(d) >= 7:
            leap += 1
    total = len(sizes)
    return {"upShare": round(ups / total, 3), "downShare": round(downs / total, 3),
            "sameShare": round(same / total, 3), "meanAbsSemis": round(sum(sizes) / total, 2),
            "chromaticShare": round(chroma / total, 3), "thirdShare": round(third / total, 3),
            "leapShare": round(leap / total, 3), "meanSemis": round(sum(signed) / total, 2)}


def role_pattern_evidence(raw: bytes, *, source_name: str,
                          role_map: dict[int, str],
                          melody_channels: Iterable[int] = ()) -> dict[str, Any]:
    midi = MidiFile.from_bytes(raw)
    ppq = midi.ppq
    bar = ppq * 4
    uspq = _tempo_us_per_quarter(midi)
    total_ticks = max((e.tick for tr in midi.tracks for e in tr.events), default=bar)
    bars = max(1, round(total_ticks / bar))
    notes = sorted(midi.notes(), key=lambda n: (n.channel, n.start, n.pitch))
    channels: dict[str, Any] = {}
    melody_set = {int(c) for c in melody_channels}
    for ch in sorted(role_map):
        ch_notes = [n for n in notes if n.channel == ch]
        role = role_map[ch]
        if not ch_notes:
            channels[str(ch)] = {"role": role, "noteCount": 0}
            continue
        vel = [float(n.velocity) for n in ch_notes]
        dur = [float(n.end - n.start) for n in ch_notes]
        gates: list[float] = []
        onset_gaps: list[float] = []
        by_start: dict[int, list[Note]] = {}
        for n in ch_notes:
            by_start.setdefault(n.start, []).append(n)
        sorted_starts = sorted(by_start)
        for i, st in enumerate(sorted_starts):
            group = sorted(by_start[st], key=lambda x: x.pitch)
            if i + 1 < len(sorted_starts):
                gap = sorted_starts[i + 1] - st
            else:
                gap = None
            for n in group:
                span = gap if gap is not None else max(1, n.end - n.start + ppq * 2)
                gates.append(min(1.0, (n.end - n.start) / max(1, span)))
                if gap is not None:
                    onset_gaps.append(float(gap))
        peaks = [len(v) for v in by_start.values()]
        block: dict[str, Any] = {
            "role": role, "channel": ch, "noteCount": len(ch_notes),
            "register": [min(n.pitch for n in ch_notes), max(n.pitch for n in ch_notes)],
            "densityNotesPerBar": round(len(ch_notes) / bars, 2),
            "velocity": _stats(vel, 1),
            "durationTicks": _stats(dur, 1),
            "gateRatio": _stats(gates, 3),
            "onsetGapTicks": _stats(onset_gaps, 1),
            "polyphonyPeak": max(peaks) if peaks else 0,
            "graceShortNoteShare": round(sum(1 for d in dur if d <= GRACE_SHARE_MAX_DUR) / len(dur), 3),
            "shortGateShare": round(sum(1 for g in gates if g < SHORT_GATE) / len(gates), 3),
        }
        if role in ("solo", "lead", "melody") or ch in melody_set:
            block["melodyPattern"] = {
                **_interval_profile(ch_notes),
                "phraseCount": sum(1 for g in onset_gaps if g >= ppq * PHRASE_GAP_BEATS) + 1,
                "phraseLenNotes": round(len(ch_notes) / (sum(1 for g in onset_gaps if g >= ppq * PHRASE_GAP_BEATS) + 1), 2),
                "barsCovered": round((ch_notes[-1].start - ch_notes[0].start) / bar, 2) if len(ch_notes) > 1 else 0,
            }
        channels[str(ch)] = block
    return {
        "schema": PATTERN_SCHEMA, "version": PATTERN_VERSION,
        "sourceName": source_name,
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "ppq": ppq, "bars": bars,
        "tempoBpm": round(60_000_000 / uspq, 2),
        "roleMap": {str(k): v for k, v in sorted(role_map.items())},
        "channels": channels,
    }


def aggregate_corpus(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-role pattern stats across many files (same role_map)."""
    by_role: dict[str, list[dict[str, Any]]] = {}
    for res in results:
        for ch, block in res["channels"].items():
            if block.get("noteCount", 0) == 0:
                continue
            by_role.setdefault(block["role"], []).append(block)
    out: dict[str, Any] = {}

    def agg(field: str, extract) -> dict[str, Any] | None:
        vals = [extract(b) for b in blocks]
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        return {"files": len(vals), "min": round(min(vals), 3),
                "median": round(statistics.median(vals), 3), "max": round(max(vals), 3)}

    for role, blocks in sorted(by_role.items()):
        total_notes = sum(b["noteCount"] for b in blocks)
        reg_lo = min(b["register"][0] for b in blocks)
        reg_hi = max(b["register"][1] for b in blocks)
        vel_means = [b["velocity"]["mean"] for b in blocks if "mean" in b["velocity"]]
        dens = [b["densityNotesPerBar"] for b in blocks]
        out[role] = {
            "files": len(blocks), "totalNotes": total_notes,
            "register": [reg_lo, reg_hi],
            "densityNotesPerBar": {"min": round(min(dens), 2), "median": round(statistics.median(dens), 2),
                                   "max": round(max(dens), 2)},
            "velocityMean": ({"min": round(min(vel_means), 1), "median": round(statistics.median(vel_means), 1),
                              "max": round(max(vel_means), 1)} if vel_means else None),
        }
        if role in ("solo", "lead", "melody"):
            int_means = []
            for b in blocks:
                mp = b.get("melodyPattern") or {}
                if mp.get("meanAbsSemis") is not None:
                    int_means.append((mp["meanAbsSemis"], mp["upShare"], mp["downShare"],
                                      mp["chromaticShare"], mp["thirdShare"], mp["leapShare"]))
            if int_means:
                out[role]["melody"] = {
                    "meanAbsSemisMedian": round(statistics.median(x[0] for x in int_means), 2),
                    "upShareMedian": round(statistics.median(x[1] for x in int_means), 3),
                    "downShareMedian": round(statistics.median(x[2] for x in int_means), 3),
                    "chromaticShareMedian": round(statistics.median(x[3] for x in int_means), 3),
                    "thirdShareMedian": round(statistics.median(x[4] for x in int_means), 3),
                    "leapShareMedian": round(statistics.median(x[5] for x in int_means), 3),
                    "files": len(int_means),
                }
    return {"schema": PATTERN_SCHEMA + "-corpus", "version": PATTERN_VERSION,
            "roles": out}


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Role Pattern Evidence Engine 4.59")
    ap.add_argument("--input", required=True)
    ap.add_argument("--roles", required=True,
                    help="comma list ch:role, e.g. 8:bass,9:drums,10:percussion,11:accompaniment")
    ap.add_argument("--melody-channels", default="",
                    help="comma list of channels measured as melody/solo")
    ap.add_argument("--out-dir", default="artifacts-max-4.59")
    args = ap.parse_args(argv)
    role_map = {}
    for item in args.roles.split(","):
        ch, role = item.split(":")
        role_map[int(ch)] = role
    melody = [int(x) for x in args.melody_channels.split(",") if x.strip()]
    res = role_pattern_evidence(Path(args.input).read_bytes(),
                                source_name=Path(args.input).name,
                                role_map=role_map, melody_channels=melody)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(args.input).stem
    (out / f"role-patterns-{stem}.json").write_text(
        json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: {"role": v.get("role"), "notes": v.get("noteCount"),
                          "density": v.get("densityNotesPerBar"),
                          "velQ50": (v.get("velocity") or {}).get("q50"),
                          "melody": v.get("melodyPattern")} for k, v in res["channels"].items()},
                     indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
