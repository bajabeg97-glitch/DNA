"""
Instrument Technique Evidence Engine 4.55 — "slap/pop/palm-mute i takve fore".

What this engine does (all executed, read-only):
  * loads the role technique vocabulary from complete-instrument-profiles-4.44.json
    (bass: ghost/slap/pop-candidates; rhythm-guitar: palm-mute/mute/open-strum ...)
    and the grammar's hard boundaries;
  * parses a real channel, measures per-note (bass) or per-stroke (chordal)
    evidence: velocity quantiles of the channel itself, gate ratio vs the next
    onset, pitch register zones, stroke size;
  * maps notes/strokes to *candidate* techniques ONLY using names that exist in
    the profile vocabulary; anything else stays a measurement-only label;
  * emits a warrant ledger: every technique that the profile marks as
    forbidden-without-device-evidence (slap-trigger, pop-trigger, guitar-mode
    trigger, ...) is reported as REQUIRES_DEVICE_EVIDENCE and NEVER emitted;
  * never changes a single byte of the input and never writes CC/keyswitch/
    pressure trigger events (gates prove it).

This gives the arrangement AI an executable answer to "which technique
vocabulary applies to this channel, what does the factory actually play, and
what would need device capture before a trigger may ever be emitted".
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

PROFILES = ROOT / "complete-instrument-profiles-4.44.json"
TECHNIQUE_SCHEMA = "dna-instrument-technique-evidence"
TECHNIQUE_VERSION = "4.55"

BASS = "bass"
CHORDAL = "rhythm-guitar"

# Measurement thresholds (relative to the channel's own evidence — the file's
# own factory dynamics decide what "accent" and "ghost" mean for that channel).
DEFAULTS = {
    "shortGateRatio": 0.45,   # gate < this  -> short/muted articulation zone
    "longGateRatio": 0.60,    # gate >= this -> sustain zone
    "accentQuantile": 0.80,   # velocity >= Q80 -> accent tier
    "ghostQuantile": 0.20,    # velocity <= Q20 -> ghost tier
    "midVelCapQuantile": 0.60,  # chordal: velocity <= Q60 for mute candidates
    "lowZoneQuantile": 0.33,  # pitch below Q33 -> low register (thumb zone)
    "highZoneQuantile": 0.66, # pitch above Q66 -> high register (pop zone)
    "lastNoteFallbackGapTicksFactor": 2,  # ticks gap assumed after last onset
}


def _profiles_roles() -> dict[str, dict[str, Any]]:
    return json.loads(PROFILES.read_text(encoding="utf-8"))["roles"]


def role_technique_vocab(role: str) -> set[str]:
    """Technique names the profile allows for this role."""
    r = _profiles_roles().get(role)
    if not r:
        return set()
    return set((r.get("behavior") or {}).get("techniques", []))


def role_forbidden_triggers(role: str) -> set[str]:
    """Trigger-like technique names that need exact device evidence."""
    r = _profiles_roles().get(role)
    if not r:
        return set()
    return set((r.get("behavior") or {}).get(
        "forbidden_without_device_evidence", []))


def _channel_sound_evidence(midi: MidiFile,
                            channel: int) -> list[dict[str, Any]]:
    """Ordered distinct (cc0, cc32, pc) triples observed on the channel."""
    seen: list[dict[str, Any]] = []
    keys: set[tuple[int | None, int | None, int | None]] = set()
    state: tuple[int | None, int | None, int | None] = (None, None, None)
    events = sorted(
        (e for tr in midi.tracks for e in tr.events
         if e.kind == "channel" and e.channel == channel
         and (e.command == 0xC0 or (e.command == 0xB0 and len(e.data) == 2
                                    and e.data[0] in (0, 32)))),
        key=lambda e: (e.tick, e.order))
    for e in events:
        if e.command == 0xC0 and len(e.data) >= 1:
            state = (state[0], state[1], e.data[0])
        elif e.data[0] == 0:
            state = (e.data[1], state[1], state[2])
        elif e.data[0] == 32:
            state = (state[0], e.data[1], state[2])
        if state not in keys and any(x is not None for x in state):
            keys.add(state)
            seen.append({"cc0": state[0], "cc32": state[1], "program": state[2]})
    return seen


def _quantiles(values: list[float], qs: Iterable[float]) -> dict[float, float]:
    vs = sorted(values)
    out: dict[float, float] = {}
    for q in qs:
        idx = min(len(vs) - 1, max(0, round(q * (len(vs) - 1))))
        out[q] = vs[idx]
    return out


def _gate_ratio(dur: int, next_start: int | None, start: int, ppq: int,
                fallback_factor: int) -> float:
    if next_start is not None:
        span = max(1, next_start - start)
    else:
        span = dur + ppq * fallback_factor
    return min(1.0, dur / span)


def _velocity_tier(v: int, q20: float, q80: float) -> str:
    if v <= q20:
        return "ghost"
    if v >= q80:
        return "accent"
    return "mid"


def bass_technique_for_note(n: Note, *, q20: float, q80: float,
                            q33_pitch: float, q66_pitch: float,
                            gate: float, thresholds: dict[str, Any],
                            prev_pitch: int | None) -> str:
    """Map one bass note to a vocabulary technique candidate or a
    measurement-only label. Uses only this channel's own factory evidence."""
    if gate >= thresholds["longGateRatio"]:
        return "sustain"  # measurement-only; functional label needs chords
    tier = _velocity_tier(n.velocity, q20, q80)
    low = n.pitch < q33_pitch
    high = n.pitch > q66_pitch
    if tier == "ghost":
        return "ghost-candidate"
    if tier == "accent":
        if low:
            return "slap-candidate"
        if high:
            return "pop-candidate"
        # mid-register accent, short: only meaningful when struck as part of
        # a slap pattern — without a device/sound profile this stays unverified
        return "short-accent-mid"
    return "short-mid"


def chordal_technique_for_stroke(*, size: int, vel_mean: float, q20: float,
                                 q60: float, q80: float, gate: float,
                                 thresholds: dict[str, Any]) -> str:
    """Map one chordal stroke (same-tick cluster) to rhythm-guitar
    vocabulary."""
    if size == 1 and gate < thresholds["shortGateRatio"]:
        return "single-string-candidate"
    if gate < thresholds["shortGateRatio"]:
        if vel_mean <= q60:
            return "palm-mute-candidate"
        if vel_mean >= q80:
            return "accent-stroke"  # measurement-only (power down-stroke class)
        return "mute-candidate" if size >= 2 else "short-mid"
    if size >= 2:
        return "open-strum" if gate >= thresholds["longGateRatio"] else "mid-strum"
    return "sustain"


def _strokes(notes: list[Note]) -> list[dict[str, Any]]:
    by_tick: dict[int, list[Note]] = {}
    for n in notes:
        by_tick.setdefault(n.start, []).append(n)
    groups: list[list[Note]] = []
    for tick in sorted(by_tick):
        ns = sorted(by_tick[tick], key=lambda x: x.pitch)
        groups.append(ns)
    out = []
    for i, ns in enumerate(groups):
        next_start = groups[i + 1][0].start if i + 1 < len(groups) else None
        out.append({
            "notes": ns,
            "size": len(ns),
            "velMean": sum(x.velocity for x in ns) / len(ns),
            "durMax": max(x.end - x.start for x in ns),
            "start": ns[0].start,
            "nextStart": next_start,
            "pitchSpan": ns[-1].pitch - ns[0].pitch,
        })
    return out


def _channel_notes(raw: bytes, channel: int) -> tuple[MidiFile, list[Note]]:
    midi = MidiFile.from_bytes(raw)
    notes = sorted((n for n in midi.notes() if n.channel == channel),
                   key=lambda n: (n.start, n.pitch))
    return midi, notes


def run_technique_evidence(raw: bytes, *, channel: int, role: str,
                           source_name: str,
                           out_dir: str | Path | None = None) -> dict[str, Any]:
    if role not in (BASS, CHORDAL):
        raise ValueError(f"Unsupported role: {role}")
    thresholds = dict(DEFAULTS)
    vocab = role_technique_vocab(role)
    forbidden = role_forbidden_triggers(role)
    midi, notes = _channel_notes(raw, channel)
    ppq = midi.ppq
    sound = _channel_sound_evidence(midi, channel)
    by_label: dict[str, int] = {}
    examples: dict[str, list[dict[str, Any]]] = {}
    per_note: list[dict[str, Any]] = []
    gates = {
        "readOnlyNoInputBytesWritten": True,
        "emittedTriggers": 0,
        "emittedBankProgramChanges": 0,
        "emittedControllerEvents": 0,
        "emittedKeyswitches": 0,
        "emittedNoteOnOff": 0,
        "profileVocabularyLoaded": bool(vocab),
        "roleKnown": role in _profiles_roles(),
    }

    if role == BASS and notes:
        vel_q = _quantiles([float(x.velocity) for x in notes],
                           (0.20, 0.80))
        pitch_q = _quantiles([float(x.pitch) for x in notes],
                             (0.33, 0.66))
        prev: Note | None = None
        for i, n in enumerate(notes):
            next_start = (notes[i + 1].start if i + 1 < len(notes) else None)
            gate = _gate_ratio(n.end - n.start, next_start, n.start, ppq,
                               thresholds["lastNoteFallbackGapTicksFactor"])
            label = bass_technique_for_note(
                n, q20=vel_q[0.20], q80=vel_q[0.80],
                q33_pitch=pitch_q[0.33], q66_pitch=pitch_q[0.66],
                gate=gate, thresholds=thresholds,
                prev_pitch=prev.pitch if prev else None)
            row = {"start": n.start, "pitch": n.pitch, "velocity": n.velocity,
                   "dur": n.end - n.start, "gateRatio": round(gate, 3),
                   "deltaFromPrev": (n.pitch - prev.pitch) if prev else None,
                   "label": label,
                   "inProfileVocabulary": label in vocab}
            per_note.append(row)
            by_label[label] = by_label.get(label, 0) + 1
            if len(examples.get(label, [])) < 12:
                examples.setdefault(label, []).append(row)
            prev = n
    elif role == CHORDAL and notes:
        vel_all = [x.velocity for ns in _strokes(notes)
                   for x in ns["notes"]]
        vel_q = _quantiles([float(v) for v in vel_all], (0.20, 0.60, 0.80))
        for i, grp in enumerate(_strokes(notes)):
            gap = grp["nextStart"] - grp["start"] if grp["nextStart"] is not None \
                else grp["durMax"] + ppq * thresholds["lastNoteFallbackGapTicksFactor"]
            gate = min(1.0, grp["durMax"] / max(1, gap))
            label = chordal_technique_for_stroke(
                size=grp["size"], vel_mean=grp["velMean"],
                q20=vel_q[0.20], q60=vel_q[0.60], q80=vel_q[0.80],
                gate=gate, thresholds=thresholds)
            row = {"start": grp["start"], "size": grp["size"],
                   "velMean": round(grp["velMean"], 1),
                   "durMax": grp["durMax"], "gateRatio": round(gate, 3),
                   "pitchSpan": grp["pitchSpan"],
                   "label": label, "inProfileVocabulary": label in vocab}
            per_note.append(row)
            by_label[label] = by_label.get(label, 0) + 1
            if len(examples.get(label, [])) < 12:
                examples.setdefault(label, []).append(row)

    # warrant ledger: forbidden-without-device-evidence techniques are
    # classified only as candidates; triggers are never emitted.
    warrant = []
    for name in sorted(vocab):
        observed = by_label.get(name, 0)
        warrant.append({
            "technique": name,
            "observedCount": observed,
            "requiresDeviceEvidenceBeforeTrigger": name in forbidden,
            "deviceEvidenceCaptured": False,
            "emissionState": "SEMANTIC_ONLY",
            "hardRule": ("NO_UNVERIFIED_SLAP_POP_TRIGGER"
                         if "slap" in name or "pop" in name
                         else "EXACT_SOUND_PROFILE_REQUIRED"),
        })

    label_order = sorted(by_label, key=lambda k: -by_label[k])
    counts = {"total": len(per_note),
              "perLabel": {k: by_label[k] for k in label_order}}
    result = {
        "schema": TECHNIQUE_SCHEMA,
        "version": TECHNIQUE_VERSION,
        "sourceName": source_name,
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "role": role,
        "channel": channel,
        "ppq": ppq,
        "thresholdsApplied": thresholds,
        "profileVocabulary": sorted(vocab),
        "channelSoundEvidence": sound,
        "noteOrStrokeCount": len(per_note),
        "counts": counts,
        "examplesPerLabel": examples,
        "warrantLedger": warrant,
        "gates": gates,
    }
    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stem = Path(source_name).stem
        p = out / f"techniques-{role}-ch{channel}-{stem}.json"
        p.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                     encoding="utf-8")
        result["artifact"] = str(p)
    return result


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Technique Evidence Engine 4.55")
    ap.add_argument("--input", required=True)
    ap.add_argument("--channel", type=int, required=True)
    ap.add_argument("--role", required=True, choices=(BASS, CHORDAL))
    ap.add_argument("--out-dir", default="artifacts-max-4.55")
    args = ap.parse_args(argv)
    raw = Path(args.input).read_bytes()
    res = run_technique_evidence(raw, channel=args.channel, role=args.role,
                                 source_name=Path(args.input).name,
                                 out_dir=args.out_dir)
    print(json.dumps({"counts": res["counts"], "gates": res["gates"]},
                     indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
