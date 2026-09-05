"""
MAX 4.48 activation executor (opt-in).

Full neural-track REPLACE flow used by the MAX orchestration line:

    role decision (REPLACE/AUGMENT warrant)
        -> retrieval (GOLD / Factory-strum evidence)
        -> neural candidates (event decoder, autoregressive, multibar, phrase,
                             section, transition - when models exist)
        -> MAX ranking (MaxCandidateOrchestrator, rankingOnly)
        -> phrase/atomic path selection
        -> decode with Factory-only velocity
        -> continuity pass
        -> hard gates: reparse, protected-events diff, factory-velocity claims,
                       final SMF / PA800-style validation

The production default pipeline (unified_pipeline) is NOT touched: this executor
is opt-in and refuses to run when the role-aware decision engine does not warrant
REPLACE/AUGMENT (unless --force for explicit, logged overrides used by tests).

Run from the repository root:

    python3 max_activation.py --input session35-partial-preview.mid \
        --role bass --track 0 --channel 8 --start-bar 6 --end-bar 9 \
        --out-dir artifacts-max-4.48
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dna_midi_studio.midi import MidiFile  # noqa: E402
from dna_midi_studio.song_understanding import analyze_song_map  # noqa: E402
from dna_midi_studio.role_aware_repair import decide_region  # noqa: E402
from dna_midi_studio.max_layout import engine_dirs, describe as describe_layout  # noqa: E402
from dna_midi_studio.ai_learning.max_orchestrator import MaxCandidateOrchestrator, MaxModelRegistry, build_max_status  # noqa: E402


def _run_decision_gate(raw: bytes, request: dict[str, Any], evidence_strength: float) -> dict[str, Any]:
    """Role-aware warrant gate: REFUSE unless the decision engine says REPLACE/AUGMENT."""
    midi = MidiFile.from_bytes(raw)
    decision = decide_region(
        midi,
        role=request["role"],
        track_index=request["track_index"],
        channel=request["channel"],
        start_tick=request["start_tick"],
        end_tick=request["end_tick"],
        evidence_strength=evidence_strength,
        target_is_known_bad=request.get("target_is_known_bad", False),
    ).to_dict()
    allowed = decision.get("decision") in {"REPLACE", "AUGMENT"}
    return {"decision": decision, "allowed": bool(allowed)}


def execute_max_replace(
    input_path: str | Path,
    *,
    role: str,
    track_index: int,
    channel: int,
    start_bar: int,
    end_bar: int,
    out_dir: str | Path,
    project_root: str | Path | None = None,
    evidence_strength: float = 0.82,
    force: bool = False,
) -> dict[str, Any]:
    root = Path(project_root) if project_root is not None else ROOT
    src = Path(input_path)
    raw = src.read_bytes()
    if raw[:4] != b"MThd":
        raise ValueError(f"{src.name} is not a MIDI file")

    song = analyze_song_map(raw, src.name)
    bars = song["bars"]
    if end_bar > len(bars):
        raise ValueError(f"end_bar {end_bar} exceeds song bars {len(bars)}")
    start_tick = int(bars[start_bar - 1]["startTick"])
    end_tick = int(bars[end_bar - 1]["endTick"])

    request = {
        "track_index": track_index, "channel": channel,
        "start_bar": start_bar, "end_bar": end_bar,
        "role": role, "start_tick": start_tick, "end_tick": end_tick,
    }
    gate = _run_decision_gate(raw, {**request, "start_tick": start_tick, "end_tick": end_tick}, evidence_strength)
    if not gate["allowed"] and not force:
        raise ValueError(
            f"decision gate refused: {gate['decision'].get('decision')} "
            f"(not REPLACE/AUGMENT). Re-run with --force only for explicit overrides."
        )

    dirs = engine_dirs(root)
    from dna_midi_studio.ai_learning.track_replacement import ReplacementRequest, TrackReplacementEngine
    req = ReplacementRequest(track_index, channel, start_bar, end_bar, role)
    req.validate()
    engine = TrackReplacementEngine(dirs["model_dir"], dirs["learning_data_dir"], dirs["data_dir"])
    report = engine.replace(raw, req, n=8)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    variants = {}
    for label, payload in report["variants"].items():
        name = f"{src.stem}.REPLACE.{label}.max-4.48.mid"
        (out / name).write_bytes(payload["midiBytes"])
        variants[label] = {
            "file": name, "sha256": payload["sha256"],
            "noteCount": payload["noteCount"],
            "maxScore": max(float(p.get("maxScore") or -1e9) for p in payload["candidatePath"]),
            "path": payload["candidatePath"],
        }
    return {
        "schema": "dna-max-4.48-execution",
        "version": "1.0",
        "input": {"file": src.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
        "request": asdict(req),
        "decisionGate": {"allowed": gate["allowed"], "decision": gate["decision"].get("decision"),
                          "severity": gate["decision"].get("severity"), "metrics": gate["decision"].get("metrics")},
        "registry": MaxModelRegistry(root).scan(),
        "engineDirs": {k: str(v) for k, v in dirs.items()},
        "authority": report["authority"],
        "maxOrchestration": report["maxOrchestration"],
        "phrasePlanner": report["phrasePlanner"],
        "multibarEventDecoder": report["multibarEventDecoder"],
        "sectionArranger": report["sectionArranger"],
        "transitionFill": report["transitionFill"],
        "performanceDNA": report["performanceDNA"],
        "outputs": variants,
        "engineStatus": report["status"],
        "outDir": str(out),
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MAX 4.48 activation executor")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("replace", help="run gated neural-track REPLACE")
    run.add_argument("--input", required=True)
    run.add_argument("--role", required=True)
    run.add_argument("--track", type=int, default=0)
    run.add_argument("--channel", type=int, default=8)
    run.add_argument("--start-bar", type=int, required=True)
    run.add_argument("--end-bar", type=int, required=True)
    run.add_argument("--out-dir", default="artifacts-max-4.48")
    run.add_argument("--evidence-strength", type=float, default=0.82)
    run.add_argument("--force", action="store_true")
    run.add_argument("--report", default=None, help="json report path (default: <out-dir>/max-execution-4.48.json)")

    st = sub.add_parser("status", help="print MAX registry/status summary")
    args = parser.parse_args(argv)

    if args.command == "status":
        s = build_max_status(ROOT)
        print(json.dumps({"registry": {"present": s["registry"]["present"], "missing": s["registry"]["missing"],
                                       "models": s["registry"]["models"]},
                          "scoring": s["scoring"], "application": s["application"]}, indent=2))
        return 0
    if args.command != "replace":
        parser.print_help()
        return 2

    result = execute_max_replace(
        args.input, role=args.role, track_index=args.track, channel=args.channel,
        start_bar=args.start_bar, end_bar=args.end_bar, out_dir=args.out_dir,
        evidence_strength=args.evidence_strength, force=args.force,
    )
    report_path = Path(args.report) if args.report else Path(args.out_dir) / "max-execution-4.48.json"
    report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: result[k] for k in ("request", "decisionGate", "outputs", "engineStatus")}, indent=2))
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
