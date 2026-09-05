"""
Korg STY Mapper 4.53 — map + export Korg-style SMF (Pa800 importable).

What this module does, and why (facts only, verified 2026-09-05):

  * Korg Pa800 imports a style ONLY as SMF type 0 with section markers
    (forums + our own factory export `baseline/reference-style.mid`).
  * In the SMF export each style element is delimited by a marker meta
    event whose text is the Korg element id, e.g. `i1cv1`, `v2cv1`,
    `f2cv1`, `e2cv1`  (intro/variation/fill/ending + number + cv + CV id).
  * Backing parts live on MIDI channels 9..16 (1-based) == 8..15 (0-based):
    ch8 bass, ch9 drums, ch10 percussion, ch11..15 acc slots.
  * At every element start the Korg export re-asserts per-channel setup:
    CC0 bank MSB, CC32 bank LSB, Program Change, CC11 expression.
  * Our repo files already prove the convention: `reference-style.mid`
    (markers i1cv1@0 ... e2cv1@34560, CC0/CC32/CC11 x80, PC x80, no SysEx).

The exporter therefore NEVER invents sounds: it copies the channel sound
evidence that already exists in the source file; a channel without sound
evidence is reported, not guessed.

Nothing here renders audio. Everything is byte-level: parse -> map -> add
markers/setup if missing -> re-serialize SMF0 -> gates.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dna_midi_studio.arranger_contract import CHANNEL_ROLES, COMP_REGISTER
from dna_midi_studio.midi import MidiEvent, MidiFile

# Korg element token -> canonical section word (Korg vocabulary)
ELEMENT_WORD: dict[str, str] = {
    "i": "intro", "intro": "intro",
    "v": "variation", "var": "variation", "variation": "variation",
    "f": "fill", "fill": "fill",
    "e": "ending", "end": "ending", "ending": "ending",
    "b": "break", "brk": "break", "break": "break",
}
CANONICAL_TOKEN: dict[str, str] = {
    "intro": "i", "variation": "v", "fill": "f", "ending": "e", "break": "b",
}
# canonical display order used by Korg (we only require the elements present)
CANONICAL_ORDER: list[tuple[str, int]] = (
    [("intro", 1), ("intro", 2), ("intro", 3)]
    + [("variation", i) for i in range(1, 5)]
    + [("fill", i) for i in range(1, 5)]
    + [("break", 1)]
    + [("ending", 1), ("ending", 2), ("ending", 3)]
)

_MARKER_RE = re.compile(
    r"^\s*(?P<kind>[ivfe]|intro|var|variation|fill|end|ending|brk|break)"
    r"(?:\s*[-_:]?\s*(?P<number>\d))?"
    r"(?:\s*[-_:]?\s*(?:cv|chord\s*variation)\s*[-_:]?\s*(?P<cv>\d+))?\s*$",
    re.IGNORECASE,
)
# compact Korg form used by our gold files: i1cv1 / v2cv1 / f1cv1 / e2cv1
_COMPACT_RE = re.compile(r"^(?P<kind>[ivfe])(?P<number>\d)cv(?P<cv>\d+)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# markers
# ---------------------------------------------------------------------------
def normalize_marker(text: str) -> tuple[str, int, int] | None:
    """Normalize a Korg style marker to (kind, number, cv).

    Understands the compact gold form (i1cv1, v2cv1, f2cv1, e2cv1) and
    descriptive forms (\"Intro 1 CV1\").  Returns None for unknown labels —
    the mapper never guesses an unknown label.
    """
    if not text:
        return None
    m = _COMPACT_RE.match(text)
    if m:
        kind = ELEMENT_WORD[m.group("kind").lower()]
        return kind, int(m.group("number")), int(m.group("cv"))
    m = _MARKER_RE.match(text)
    if not m:
        return None
    word = ELEMENT_WORD.get(m.group("kind").lower())
    if word is None:
        return None
    number = int(m.group("number") or 1)
    cv = int(m.group("cv") or 1)
    if word == "break":
        number = 1
    if not (1 <= number <= 4 and 1 <= cv <= 4):
        return None
    return word, number, cv


def marker_text(kind: str, number: int, cv: int) -> str:
    return f"{CANONICAL_TOKEN[kind]}{number}cv{cv}"


def parse_markers(raw: bytes) -> list[dict[str, Any]]:
    """All marker meta events (0x06) with their absolute ticks."""
    midi = MidiFile.from_bytes(raw)
    found = []
    for ti, track in enumerate(midi.tracks):
        for e in track.events:
            if e.kind == "meta" and e.meta_type == 0x06:
                text = bytes(e.data).decode("latin-1", "replace")
                found.append({"track": ti, "tick": e.tick, "text": text,
                              "order": e.order})
    return sorted(found, key=lambda m: (m["tick"], m["track"], m["order"]))


# ---------------------------------------------------------------------------
# bar math
# ---------------------------------------------------------------------------
def ticks_per_bar(midi: MidiFile) -> int:
    """Quarter-note PPQ x 4 for 4/4; honors an existing time-signature meta."""
    for track in midi.tracks:
        for e in track.events:
            if e.kind == "meta" and e.meta_type == 0x58 and len(e.data) >= 2:
                nn, dd = e.data[0], e.data[1]
                if dd <= 6:
                    return max(1, int(midi.ppq * nn * 4 / (2 ** dd)))
    return midi.ppq * 4


# ---------------------------------------------------------------------------
# style map  ("gdje šta ide")
# ---------------------------------------------------------------------------
def section_map(raw: bytes) -> dict[str, Any]:
    """Turn marker boundaries + content into a full style map."""
    midi = MidiFile.from_bytes(raw)
    notes = midi.notes()
    by_ch: dict[int, list] = {}
    for n in notes:
        by_ch.setdefault(n.channel, []).append(n)
    tpb = ticks_per_bar(midi)
    markers = parse_markers(raw)
    elements: list[dict[str, Any]] = []
    for i, m in enumerate(markers):
        norm = normalize_marker(m["text"])
        if norm is None:
            continue
        kind, number, cv = norm
        start = m["tick"]
        end = markers[i + 1]["tick"] if i + 1 < len(markers) else max(
            (n.end for n in notes), default=start) + 1
        length_bars = max(1, round((end - start) / tpb))
        elements.append({
            "text": m["text"], "kind": kind, "number": number, "cv": cv,
            "startTick": start, "endTick": end, "lengthBars": length_bars,
        })
    per_element_channels: list[dict[str, Any]] = []
    for el in elements:
        chans = []
        for ch in sorted(by_ch):
            cn = [n for n in by_ch[ch]
                  if el["startTick"] <= n.start < el["endTick"]]
            if not cn:
                continue
            chans.append({
                "channel": ch,
                "role": CHANNEL_ROLES.get(ch, "unknown"),
                "noteCount": len(cn),
                "register": [min(n.pitch for n in cn), max(n.pitch for n in cn)],
                "avgVel": round(sum(n.velocity for n in cn) / len(cn), 1),
            })
        el2 = dict(el)
        el2["channels"] = chans
        per_element_channels.append(el2)
    return {
        "schema": "dna-sty-map",
        "ppq": midi.ppq,
        "ticksPerBar": tpb,
        "markerCount": len(markers),
        "unrecognizedMarkers": [m for m in markers if normalize_marker(m["text"]) is None],
        "elements": per_element_channels,
        "totalBars": elements[-1]["startTick"] // tpb + elements[-1]["lengthBars"]
                     if elements else 0,
    }


def role_map(raw: bytes) -> list[dict[str, Any]]:
    """Per-channel position evidence: where each part sits in the Korg map.

    For channels already in 8..15 the Korg convention is authoritative
    (bass 8, drums 9, percussion 10, acc 11..15).  For out-of-range input
    channels a register-based suggestion is computed and flagged as such
    (never applied silently).
    """
    midi = MidiFile.from_bytes(raw)
    notes = midi.notes()
    by_ch: dict[int, list] = {}
    for n in notes:
        by_ch.setdefault(n.channel, []).append(n)
    out = []
    for ch in sorted(by_ch):
        cn = sorted(by_ch[ch], key=lambda n: n.start)
        first = cn[0].start
        sound = midi.sound_at(ch, first)
        if 8 <= ch <= 15:
            role = CHANNEL_ROLES.get(ch)
            suggestion = f"Korg convention: channel {ch} = {role}"
            slot = ch
        else:
            lo = min(n.pitch for n in cn)
            hi = max(n.pitch for n in cn)
            center = (lo + hi) / 2
            drumkit = sound is not None and sound[0] == 120
            if drumkit:
                role, slot = ("drums", 9) if ch % 2 == 0 else ("percussion", 10)
                suggestion = "sound bank 120 (drum kit) -> drums/percussion slots"
            elif hi <= 48:
                role, slot = ("bass", 8)
                suggestion = "register <= C3 and sparse low part -> bass slot"
            else:
                # nearest acc slot by comp register window
                dist = {c: abs((lo + hi) / 2 - (sum(COMP_REGISTER[c]) / 2))
                        for c in COMP_REGISTER}
                slot = min(dist, key=dist.get)
                role = "accompaniment"
                suggestion = f"register center {center:.0f} nearest acc slot {slot}"
            suggestion += " (suggestion; Korg files keep their own channels)"
        out.append({
            "channel": ch, "sound": sound, "role": role,
            "suggestedKorgChannel": slot, "evidence": suggestion,
            "noteCount": len(cn),
            "register": [min(n.pitch for n in cn), max(n.pitch for n in cn)],
        })
    return out


# ---------------------------------------------------------------------------
# exporter
# ---------------------------------------------------------------------------
def _channel_sound_evidence(midi: MidiFile, ch: int, window_start: int,
                            window_end: int) -> tuple[int, int, int] | None:
    """Exact (bankMSB, bankLSB, program) proven inside the element window."""
    candidates = []
    for track in midi.tracks:
        for e in track.events:
            if (e.kind != "channel" or e.status is None
                    or not window_start <= e.tick < window_end):
                continue
            if e.status >> 4 == 0xB and (e.status & 0x0F) == ch and len(e.data) == 2:
                if e.data[0] == 0:
                    candidates.append((e.tick, e.order, "msb", e.data[1]))
                elif e.data[0] == 32:
                    candidates.append((e.tick, e.order, "lsb", e.data[1]))
            elif e.status >> 4 == 0xC and (e.status & 0x0F) == ch and len(e.data) == 1:
                candidates.append((e.tick, e.order, "pc", e.data[0]))
    candidates.sort()
    msb = [c for c in candidates if c[2] == "msb"]
    lsb = [c for c in candidates if c[2] == "lsb"]
    pc = [c for c in candidates if c[2] == "pc"]
    if pc and (msb or lsb):
        return (msb[-1][3] if msb else 0,
                lsb[-1][3] if lsb else 0,
                pc[-1][3])
    return None


def export_korg_style(raw: bytes, *, layout: list[dict[str, Any]] | None = None,
                      style_name: str | None = None) -> tuple[bytes, dict[str, Any]]:
    """Export/complete a Korg-importable style SMF0 from a source SMF.

    layout: optional explicit element plan [{kind, number, cv, bars}, ...].
            When omitted, the source markers (if any) define the layout.
    Returns (bytes, report) where the report lists every event the exporter
    added or skipped, so nothing is silent.
    """
    midi = MidiFile.from_bytes(raw)
    if midi.format_type != 0 or len(midi.tracks) != 1:
        raise ValueError("Korg style import requires SMF type 0 (one track)")
    if not midi.tracks:
        raise ValueError("empty MIDI file")
    track = midi.tracks[0]
    notes = midi.notes()
    by_ch: dict[int, list] = {}
    for n in notes:
        by_ch.setdefault(n.channel, []).append(n)
    tpb = ticks_per_bar(midi)
    end_all = max((n.end for n in notes), default=0)

    markers = parse_markers(raw)
    norm_markers = [m for m in markers if normalize_marker(m["text"])]
    if layout is not None:
        elements: list[dict[str, Any]] = []
        tick = 0
        for spec in layout:
            kind = spec["kind"]
            bars = int(spec.get("bars", 2))
            el = {"text": marker_text(kind, int(spec.get("number", 1)),
                                      int(spec.get("cv", 1))),
                  "kind": kind, "number": int(spec.get("number", 1)),
                  "cv": int(spec.get("cv", 1)),
                  "startTick": tick, "lengthBars": bars, "endTick": tick + bars * tpb}
            elements.append(el)
            tick += bars * tpb
    else:
        elements = []
        if not norm_markers:
            raise ValueError("no markers and no layout: give an explicit layout")
        for i, m in enumerate(norm_markers):
            kind, number, cv = normalize_marker(m["text"])
            start = m["tick"]
            end = norm_markers[i + 1]["tick"] if i + 1 < len(norm_markers) else max(
                end_all, start + tpb)
            elements.append({"text": marker_text(kind, number, cv), "kind": kind,
                             "number": number, "cv": cv, "startTick": start,
                             "endTick": end,
                             "lengthBars": max(1, round((end - start) / tpb))})

    events = list(track.events)
    next_order = max((e.order for e in events), default=-1) + 1
    report: dict[str, Any] = {"markersExisting": len(norm_markers),
                              "markersAdded": [], "setupsAdded": [],
                              "cc11Added": [], "skippedNoSoundEvidence": [],
                              "channelsWithoutSetup": []}

    def insert(ev: MidiEvent) -> None:
        nonlocal next_order
        events.append(dataclasses.replace(ev, order=next_order))
        next_order += 1

    existing_marker_texts = {m["text"].strip().lower(): m["tick"] for m in markers}
    existing_at = {}
    for e in events:
        if e.kind == "meta" and e.meta_type == 0x06:
            existing_at.setdefault(e.tick, []).append(
                bytes(e.data).decode("latin-1", "replace"))

    for el in elements:
        text = marker_text(el["kind"], el["number"], el["cv"])
        same_tick_texts = {t.lower() for t in existing_at.get(el["startTick"], [])}
        if text.lower() not in same_tick_texts and text.lower() not in existing_marker_texts:
            insert(MidiEvent(tick=el["startTick"], order=-1, kind="meta",
                             meta_type=0x06, data=text.encode("latin-1")))
            report["markersAdded"].append({"tick": el["startTick"], "text": text})
        for ch in sorted(by_ch):
            active = [n for n in by_ch[ch]
                      if el["startTick"] <= n.start < el["endTick"]]
            if not active:
                continue
            ws, we = el["startTick"], el["endTick"]
            # CC11 rule: gold Korg export sets CC11 at every element start for
            # every active channel; only add when the element has none.
            has_cc11 = any(
                e.kind == "channel" and e.status is not None and (e.status >> 4) == 0xB
                and (e.status & 0x0F) == ch and len(e.data) == 2 and e.data[0] == 11
                and ws <= e.tick < we
                for e in events)
            if not has_cc11:
                insert(MidiEvent(tick=ws, order=-1, kind="channel",
                                 status=0xB0 | ch, data=bytes((11, 127))))
                report["cc11Added"].append({"tick": ws, "channel": ch})
            # bank/program setup rule: copy exact sound evidence, never invent.
            has_setup = any(
                e.kind == "channel" and e.status is not None
                and (e.status >> 4) == 0xB and (e.status & 0x0F) == ch
                and len(e.data) == 2 and e.data[0] == 0 and e.tick == ws
                for e in events)
            if has_setup:
                continue
            sound = _channel_sound_evidence(midi, ch, ws, we)
            if sound is None:
                report["channelsWithoutSetup"].append(
                    {"tick": ws, "channel": ch, "reason": "no bank/program evidence "
                     "inside element; nothing guessed"})
                continue
            msb, lsb, prog = sound
            insert(MidiEvent(tick=ws, order=-1, kind="channel",
                             status=0xB0 | ch, data=bytes((0, msb))))
            insert(MidiEvent(tick=ws, order=-1, kind="channel",
                             status=0xB0 | ch, data=bytes((32, lsb))))
            insert(MidiEvent(tick=ws, order=-1, kind="channel",
                             status=0xC0 | ch, data=bytes((prog,))))
            report["setupsAdded"].append(
                {"tick": ws, "channel": ch, "sound": [msb, lsb, prog]})

    out_midi = MidiFile(midi.format_type, midi.ppq, [dataclasses.replace(track)])
    out_midi.tracks[0].events = events
    out_bytes = out_midi.to_bytes()
    report["elementCount"] = len(elements)
    report["addedTotal"] = (len(report["markersAdded"]) + len(report["setupsAdded"])
                            + len(report["cc11Added"]))
    return out_bytes, report


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------
def structure_gates(raw_in: bytes, raw_out: bytes,
                    layout: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Independent structure checks on the exported bytes."""
    midi = MidiFile.from_bytes(raw_out)
    tpb = ticks_per_bar(midi)
    notes_in, notes_out = MidiFile.from_bytes(raw_in).notes(), midi.notes()

    def geom(m):
        return sorted((n.track, n.channel, n.pitch, n.start, n.end) for n in m)

    markers = parse_markers(raw_out)
    norm = [normalize_marker(m["text"]) for m in markers]
    ticks = [m["tick"] for m in markers]
    ok_markers = all(n is not None for n in norm)
    ok_order = all(a < b for a, b in zip(ticks, ticks[1:]))
    ok_grid = all(t % tpb == 0 for t in ticks)
    expected = None
    if layout is not None:
        expected = [marker_text(s["kind"], s.get("number", 1), s.get("cv", 1))
                    for s in layout]
        texts = [m["text"].strip().lower() for m in markers]
        ok_layout = texts == [t.lower() for t in expected]
    else:
        ok_layout = True
    return {
        "smf0": midi.format_type == 0,
        "singleTrack": len(midi.tracks) == 1,
        "markersRecognized": ok_markers,
        "markersStrictlyIncreasing": ok_order,
        "markersOnBarGrid": ok_grid,
        "markersMatchLayout": ok_layout,
        "noteGeometryUnchanged": geom(notes_in) == geom(notes_out),
        "reparsed": len(notes_out) == len(notes_in),
    }


def run_export(raw: bytes, *, source_name: str, layout: list[dict[str, Any]] | None = None,
               out_dir: str | Path | None = None) -> dict[str, Any]:
    out_bytes, report = export_korg_style(raw, layout=layout)
    gates = structure_gates(raw, out_bytes, layout=layout)
    result = {
        "schema": "dna-sty-mapper-run",
        "version": "4.53",
        "sourceName": source_name,
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "styleMap": section_map(raw),
        "roleMap": role_map(raw),
        "exportReport": report,
        "gates": gates,
        "status": "STY_EXPORT_OK" if all(gates.values()) else "STY_EXPORT_GATE_FAILED",
    }
    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stem = Path(source_name).stem
        (out / f"korg-style-{stem}.mid").write_bytes(out_bytes)
        (out / f"sty-run-{stem}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        result["artifactMidi"] = str(out / f"korg-style-{stem}.mid")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Korg STY Mapper 4.53")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pa = sub.add_parser("map", help="print style map (where each part goes)")
    pa.add_argument("--input", required=True)
    pe = sub.add_parser("export", help="export Korg-importable SMF0 style")
    pe.add_argument("--input", required=True)
    pe.add_argument("--layout", default=None,
                    help="optional element plan: kind:number:cv:bars, comma list, "
                         "e.g. intro:1:1:2,variation:1:1:2")
    pe.add_argument("--out-dir", default="artifacts-max-4.53")
    args = ap.parse_args(argv)
    raw = Path(args.input).read_bytes()
    if args.cmd == "map":
        print(json.dumps(section_map(raw), indent=2, ensure_ascii=False))
        return 0
    layout = None
    if args.layout:
        layout = []
        for part in args.layout.split(","):
            k, n, cv, bars = part.split(":")
            layout.append({"kind": k, "number": int(n), "cv": int(cv),
                           "bars": int(bars)})
    res = run_export(raw, source_name=Path(args.input).name, layout=layout,
                     out_dir=args.out_dir)
    print(json.dumps({k: res[k] for k in ("status", "styleMap", "gates")},
                     indent=2, ensure_ascii=False)[:4000])
    return 0 if res["status"] == "STY_EXPORT_OK" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
