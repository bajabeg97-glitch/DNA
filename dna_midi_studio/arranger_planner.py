"""
Arranger Planner 4.50 — decides what each channel SHOULD play and how well the
current part matches the instrument's nature.

For every style channel the planner computes fit diagnostics:

  register_fit  - observed register vs the factory profile register for the role
  density_fit   - observed notes-per-quarter vs the role's healthy band
  dynamics_fit  - average velocity inside the factory curve min..ceiling
  gate_fit      - average gate ratio vs role-typical band

Then it produces a per-channel instruction with one of
  KEEP | REPAIR_WARRANT | FILL_EMPTY | MANUAL_REVIEW
using role_aware_repair.decide_region for populated channels and register
separation / headroom budgets for empty accompaniment slots.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.midi import MidiFile  # noqa: E402
from dna_midi_studio.song_understanding import analyze_song_map  # noqa: E402
from dna_midi_studio.role_aware_repair import ROLE_POLICY, decide_region  # noqa: E402
from dna_midi_studio.arranger_contract import (  # noqa: E402
    ACC_CHANNELS, CHANNEL_ROLES, COMP_REGISTER, FACTORY_ROLE_BY_STYLE_ROLE,
    SLOT_LABELS, factory_profiles, polyphony_limit, profile_register,
)

ALL_STYLE_CHANNELS: tuple[int, ...] = (8, 9, 10, *ACC_CHANNELS)


def _observed(midi: MidiFile, ch: int) -> dict[str, Any]:
    notes = [n for n in midi.notes() if n.channel == ch]
    if not notes:
        return {"present": False, "noteCount": 0}
    by_start: dict[int, list] = {}
    for n in notes:
        by_start.setdefault(n.start, []).append(n)
    gates = []
    for onset in sorted(by_start):
        nxt = min((x.start for x in notes if x.start > onset), default=None)
        ioi = max(1, (nxt - onset)) if nxt is not None else 1
        for n in by_start[onset]:
            gates.append(max(0.0, min(1.0, (n.end - n.start) / ioi)))
    vels = [n.velocity for n in notes]
    span_qn = max(1e-6, (max(n.end for n in notes) - min(n.start for n in notes)) / max(1, midi.ppq))
    return {
        "present": True, "noteCount": len(notes),
        "register": [min(n.pitch for n in notes), max(n.pitch for n in notes)],
        "avgVelocity": round(sum(vels) / len(vels), 1),
        "velocityMin": min(vels), "velocityMax": max(vels),
        "density": round(len(notes) / span_qn, 3),
        "avgGateRatio": round(sum(gates) / len(gates), 3) if gates else None,
        "polyphonyPeak": max((len(g) for g in by_start.values()), default=0),
    }


def _density_band(style_role: str) -> tuple[float, float]:
    policy = ROLE_POLICY.get(style_role) or ROLE_POLICY["accompaniment"]
    return (float(policy["sparse"]) * 0.5, float(policy["dense"]) * 1.15)


def _gate_band(style_role: str) -> tuple[float, float]:
    return {"bass": (0.35, 0.95), "drums": (0.15, 0.9), "percussion": (0.1, 0.8),
            "accompaniment": (0.35, 1.0)}.get(style_role, (0.2, 1.0))


def _factory_register(style_role: str) -> tuple[int, int]:
    f_role = FACTORY_ROLE_BY_STYLE_ROLE.get(style_role)
    if not f_role:
        return (0, 127)
    try:
        return profile_register(f_role)
    except ValueError:
        return (0, 127)


def _velocity_band(style_role: str) -> tuple[int, int]:
    f_role = FACTORY_ROLE_BY_STYLE_ROLE.get(style_role)
    if not f_role:
        return (1, 127)
    pool = [p for p in factory_profiles() if p.get("role") == f_role]
    if not pool:
        return (1, 127)
    top = max(pool, key=lambda p: (float(p.get("sample_count") or 0), str(p.get("id", ""))))
    v = top.get("velocity") or {}
    return (int(v.get("min", 1) or 1), int(v.get("ceiling", 127) or 127))


def _ratio_ok(lo: int, hi: int, value: int) -> float:
    if value < lo:
        return max(0.0, 1.0 - (lo - value) / 30.0)
    if value > hi:
        return max(0.0, 1.0 - (value - hi) / 40.0)
    return 1.0


def plan_regions(raw: bytes) -> dict[str, Any]:
    midi = MidiFile.from_bytes(raw)
    song = analyze_song_map(raw, "plan-source.mid")
    last_tick = max((n.end for n in midi.notes()), default=midi.ppq)
    regions: dict[str, Any] = {}

    for ch in ALL_STYLE_CHANNELS:
        style_role = CHANNEL_ROLES[ch]
        obs = _observed(midi, ch)
        base_region = {"channel": ch, "styleRole": style_role,
                       "slot": SLOT_LABELS.get(ch, style_role),
                       "budget": polyphony_limit(ch),
                       "registerSeparation": list(COMP_REGISTER.get(ch, (0, 127)))}

        if not obs["present"]:
            regions[str(ch)] = {**base_region,
                                "decision": "FILL_EMPTY" if ch in ACC_CHANNELS else "KEEP",
                                "reason": "no notes on channel", "fit": None,
                                "registerSeparation": list(COMP_REGISTER.get(ch, (0, 127)))}
            continue

        lo, hi = _factory_register(style_role)
        out_of_reg = sum(1 for n in midi.notes() if n.channel == ch and not (lo <= n.pitch <= hi))
        rfit = 1.0 - out_of_reg / max(1, obs["noteCount"])
        d_lo, d_hi = _density_band(style_role)
        density = obs["density"]
        dfit = 1.0 if d_lo <= density <= d_hi else max(0.0, 1.0 - abs(density - (d_lo + d_hi) / 2) / max(0.1, d_hi - d_lo))
        v_lo, v_hi = _velocity_band(style_role)
        vfit = _ratio_ok(v_lo, v_hi, int(obs["avgVelocity"]))
        g_lo, g_hi = _gate_band(style_role)
        g = obs["avgGateRatio"]
        gfit = 1.0 if (g is not None and g_lo <= g <= g_hi) else (0.5 if g is not None else 0.0)

        warrant = decide_region(
            midi, role=style_role, track_index=0, channel=ch,
            start_tick=0, end_tick=last_tick,
            evidence_strength=0.6, target_is_known_bad=False,
        ).to_dict()
        base = str(warrant.get("decision", "KEEP"))
        if base in ("REPLACE", "REPAIR", "AUGMENT"):
            decision = "REPAIR_WARRANT"
            reason = f"role-aware warrant {base}: {warrant.get('reason') or ''}"
        elif rfit < 0.8 or dfit < 0.5:
            decision = "MANUAL_REVIEW"
            reason = "part drifts from the instrument's nature (register/density)"
        else:
            decision = "KEEP"
            reason = "part matches instrument nature"
        regions[str(ch)] = {
            **base_region, "decision": decision, "reason": reason,
            "severity": warrant.get("severity"),
            "fit": {"registerFit": round(rfit, 3), "densityFit": round(dfit, 3),
                    "dynamicsFit": round(vfit, 3), "gateFit": round(gfit, 3),
                    "observed": obs,
                    "factoryRegister": [lo, hi], "densityBand": [round(d_lo, 2), round(d_hi, 2)],
                    "velocityBand": [v_lo, v_hi], "gateBand": [g_lo, g_hi]},
        }

    fill_order = [15, 14, 13, 12, 11]
    empties = [ch for ch in fill_order
               if str(ch) in regions and regions[str(ch)]["decision"] == "FILL_EMPTY"]
    return {
        "schema": "dna-arranger-plan", "version": "4.50",
        "regions": regions, "fillCandidates": empties, "fillPriority": fill_order,
        "summary": {str(ch): regions[str(ch)]["decision"]
                    for ch in ALL_STYLE_CHANNELS if str(ch) in regions},
    }
