"""
Groove Engine 4.54 — measured groove facts + human-performance reference.

Two sources of truth, both executed/verifiable in this repo:

  1. OUR files (factory/arranged): drum & percussion channels are measured the
     same way every time: onset offsets against the 16th-note grid (in ticks,
     converted to ms via the file's own tempo meta), per-pitch element
     velocity stats, density, exact-on-grid share.
  2. HUMAN reference: `baseline/human-groove-rock-derived.json` — compact
     statistics derived (2026-09-05, script executed in this session) from
     wobblemidi's rock profile (MIT).  wobblemidi's author states the sample
     distributions (offset_ms, velocity residual per instrument/tier/16th
     position) were learned from the Magenta Groove MIDI Dataset — real
     drummers.  The original Google/HF/archive hosts are unreachable from this
     sandbox; provenance (repo, file, sha256) is recorded in the JSON.

The engine NEVER edits anything; it reports measured numbers.  Timing edits
(if ever applied later) must go through gates and an explicit warrant.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.midi import MidiFile

HUMAN_SUMMARY = ROOT / "baseline" / "human-groove-rock-derived.json"


def _tempo_us_per_quarter(midi: MidiFile) -> int:
    for track in midi.tracks:
        for e in track.events:
            if e.kind == "meta" and e.meta_type == 0x51 and len(e.data) >= 3:
                return int.from_bytes(e.data[:3], "big")
    return 500_000  # 120 bpm default when the file carries no tempo meta


def tick_to_ms(tick: int, ppq: int, us_per_quarter: int) -> float:
    return tick * us_per_quarter / (ppq * 1000.0)


def drum_groove_stats(raw: bytes, channel: int) -> dict[str, Any]:
    """Measure one drum/percussion channel: timing vs 16th grid + dynamics."""
    midi = MidiFile.from_bytes(raw)
    ppq = midi.ppq
    uspq = _tempo_us_per_quarter(midi)
    notes = sorted((n for n in midi.notes() if n.channel == channel),
                   key=lambda n: (n.start, n.pitch))
    if not notes:
        return {"channel": channel, "noteCount": 0}
    step = max(1, ppq // 4)  # 16th-note grid
    bar = ppq * 4
    offsets_ticks = [n.start - round(n.start / step) * step for n in notes]
    offsets_ms = [tick_to_ms(o, ppq, uspq) for o in offsets_ticks]
    by_pitch: dict[int, list[int]] = {}
    for n in notes:
        by_pitch.setdefault(n.pitch, []).append(n.velocity)
    per_pitch = {}
    for pitch in sorted(by_pitch):
        v = by_pitch[pitch]
        per_pitch[str(pitch)] = {
            "count": len(v),
            "velMean": round(sum(v) / len(v), 1),
            "velMin": min(v), "velMax": max(v),
            "accentShareGtEq110": round(sum(1 for x in v if x >= 110) / len(v), 3),
        }
    # per-16th-position mean offset (all elements pooled)
    pos_off: dict[int, list[float]] = {}
    for n, off in zip(notes, offsets_ms):
        pos = (n.start // step) % 16
        pos_off.setdefault(pos, []).append(off)

    def st(xs: list[float]) -> dict[str, float]:
        if not xs:
            return {}
        s = sorted(xs)
        pct = lambda p: s[min(len(s) - 1, int(p * len(s)))]
        return {"n": len(s), "meanMs": round(sum(s) / len(s), 2),
                "stdMs": round(statistics.pstdev(s), 2) if len(s) > 1 else 0.0,
                "p5Ms": round(pct(.05), 2), "p50Ms": round(pct(.5), 2),
                "p95Ms": round(pct(.95), 2), "exactOnGridShare": round(
                    sum(1 for x in s if abs(x) < 0.001) / len(s), 4)}

    first_bar = max(1, round(max(n.end for n in notes) / bar))
    return {
        "channel": channel,
        "noteCount": len(notes),
        "ppq": ppq,
        "gridSixteenthTicks": step,
        "bpm": round(60_000_000 / uspq, 2),
        "densityNotesPerBar": round(len(notes) / first_bar, 2),
        "timingMs": st(offsets_ms),
        "perPitch": per_pitch,
        "meanOffsetMsBy16thPosition": {str(p): round(sum(v) / len(v), 2)
                                       for p, v in sorted(pos_off.items())},
    }


def load_human_summary() -> dict[str, Any]:
    return json.loads(HUMAN_SUMMARY.read_text(encoding="utf-8"))


def compare_factory_to_human(factory: dict[str, Any],
                             human: dict[str, Any]) -> dict[str, Any]:
    """Numeric comparison: factory channel vs real-drummer reference."""
    ft = factory.get("timingMs") or {}
    ht = human.get("overallTimingMs") or {}
    return {
        "factoryTimingStdMs": ft.get("stdMs"),
        "humanReferenceTimingStdMs": ht.get("stdMs"),
        "factoryExactOnGridShare": ft.get("exactOnGridShare"),
        "humanExactOnGridShare": ht.get("exactOnGridShare"),
        "factoryMeanMs": ft.get("meanMs"),
        "humanMeanMs": ht.get("meanMs"),
        "humanSamples": ht.get("n"),
        "meaning": "factory patterns are quantized by design; the human "
                   "reference quantifies how much real drummers deviate",
    }


def run_groove_evidence(raw: bytes, *, channel: int, source_name: str,
                        out_dir: str | Path | None = None) -> dict[str, Any]:
    factory = drum_groove_stats(raw, channel)
    human = load_human_summary()
    result = {
        "schema": "dna-groove-evidence",
        "version": "4.54",
        "sourceName": source_name,
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "factoryMeasured": factory,
        "humanReference": human,
        "comparison": compare_factory_to_human(factory, human),
    }
    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stem = Path(source_name).stem
        (out / f"groove-ch{channel}-{stem}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        result["artifact"] = str(out / f"groove-ch{channel}-{stem}.json")
    return result


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Groove Engine 4.54")
    ap.add_argument("--input", required=True)
    ap.add_argument("--channel", type=int, required=True)
    ap.add_argument("--out-dir", default="artifacts-max-4.54")
    args = ap.parse_args(argv)
    raw = Path(args.input).read_bytes()
    res = run_groove_evidence(raw, channel=args.channel,
                              source_name=Path(args.input).name,
                              out_dir=args.out_dir)
    print(json.dumps({"measured": res["factoryMeasured"],
                      "comparison": res["comparison"]},
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
