"""pattern_library.py — milestone 4.64.

Stdlib-only loader + normalizer + statistics over the vendored permissive
drum-machine pattern corpus (vendor-max-4.64/dmp-midi, MIT, gvellut/dmp_midi —
see vendor-max-4.64/dmp-midi/NOTICE.md for provenance and hashes).

The corpus holds transcriptions of "200 Drum Machine Patterns" (200 patterns)
and "260 Drum Machine Patterns" (268 patterns) by Rene-Pierre Bardet. Each
pattern is a 12- or 16-step grid per named drum part, plus optional accents.

This module does NOT write MIDI and does NOT import anything outside the
standard library. GM drum key numbers used for role statistics follow the
public General MIDI drum map / upstream NOTE_MAPPING (see NOTICE.md).

Usage:
    python3.11 -m dna_midi_studio.pattern_library           # prints summary
    python3.11 -m dna_midi_studio.pattern_library --json    # writes artifacts
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "vendor-max-4.64" / "dmp-midi"
CORPUS_FILES = ("input/Patterns_200.json", "input/Patterns_260.json")

# Full corpus track names observed in both books -> GM drum key number
# (standard General MIDI drum map; short codes match upstream NOTE_MAPPING).
GM_NOTE = {
    "BassDrum": 36,      # BD
    "RimShot": 37,       # RS
    "SnareDrum": 38,     # SD
    "Clap": 39,          # CP
    "ClosedHiHat": 42,   # CH
    "LowTom": 43,        # LT
    "OpenHiHat": 46,     # OH
    "MediumTom": 47,     # MT
    "Cymbal": 49,        # CY (crash)
    "HighTom": 50,       # HT
    "Tambourine": 54,    # TM
    "Cowbell": 56,       # CB
}
HIT_TOKENS = ("Note", "Accent", "Flam")
ROLE_OF_NOTE = {
    36: "kick", 37: "snare", 38: "snare", 39: "snare", 40: "snare",
    42: "hats", 44: "hats", 46: "hats",
    41: "toms", 43: "toms", 45: "toms", 47: "toms", 48: "toms", 50: "toms",
    49: "cymbals", 51: "cymbals", 52: "cymbals", 53: "cymbals",
    54: "percussion", 56: "percussion", 70: "percussion", 75: "percussion",
}


class PatternLibraryError(Exception):
    """Raised for structural problems in the vendored corpus."""


def _load_raw(source: str) -> list[dict[str, Any]]:
    path = VENDOR_DIR / source
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:  # pragma: no cover - env issue
        raise PatternLibraryError(f"corpus file missing: {path}") from exc
    if not isinstance(data, list):
        raise PatternLibraryError(f"{source}: top-level JSON must be a list")
    return data


def iter_patterns() -> list[tuple[str, str, dict[str, Any]]]:
    """Yield (source, index, pattern) for every pattern of the corpus."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    for source in CORPUS_FILES:
        for index, raw in enumerate(_load_raw(source)):
            out.append((source, str(index), raw))
    return out


def normalize_pattern(source: str, index: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Validate one pattern and return a normalized dict with canonical
    1/0 step rows (1 = hit) plus per-track GM keys, accents and flams."""
    if not isinstance(raw, dict):
        raise PatternLibraryError(f"{source}[{index}]: pattern not an object")
    title = raw.get("title")
    signature = raw.get("signature")
    length = raw.get("length")
    tracks = raw.get("tracks")
    accent = raw.get("accent", [])
    if not isinstance(title, str) or not title:
        raise PatternLibraryError(f"{source}[{index}]: missing title")
    if not isinstance(signature, str) or not isinstance(length, int) or length <= 0:
        raise PatternLibraryError(f"{source}[{index}] {title}: bad signature/length")
    if not isinstance(tracks, dict) or not tracks:
        raise PatternLibraryError(f"{source}[{index}] {title}: no tracks")
    rows: dict[str, dict[str, Any]] = {}
    for name, steps in tracks.items():
        if name not in GM_NOTE:
            raise PatternLibraryError(
                f"{source}[{index}] {title}: unknown part {name!r}")
        if not isinstance(steps, list) or len(steps) != length:
            raise PatternLibraryError(
                f"{source}[{index}] {title}: {name} has wrong step count")
        unknown = sorted({s for s in steps if not isinstance(s, str)})
        if unknown:
            raise PatternLibraryError(f"{source}[{index}] {title}: non-string steps")
        bits = []
        hits = 0
        accents = 0
        flams = 0
        for token in steps:
            if token == "Rest":
                bits.append("0")
            elif token in HIT_TOKENS:
                bits.append("1")
                hits += 1
                if token == "Accent":
                    accents += 1
                elif token == "Flam":
                    flams += 1
            else:
                raise PatternLibraryError(
                    f"{source}[{index}] {title}: bad token {token!r} in {name}")
        rows[name] = {
            "gm": GM_NOTE[name],
            "role": ROLE_OF_NOTE.get(GM_NOTE[name], "other"),
            "steps": "".join(bits),
            "hits": hits,
            "accents": accents,
            "flams": flams,
        }
    acc_bits = ["0"] * length
    acc_hits = 0
    if accent:
        if not isinstance(accent, list) or len(accent) != length:
            raise PatternLibraryError(f"{source}[{index}] {title}: bad accent row")
        for i, token in enumerate(accent):
            if token != "Rest":
                acc_bits[i] = "1"
                acc_hits += 1
    total = sum(r["hits"] for r in rows.values())
    return {
        "source": source.replace("input/", "").replace(".json", ""),
        "title": title,
        "signature": signature,
        "length": length,
        "tracks": rows,
        "accentSteps": "".join(acc_bits),
        "accentHits": acc_hits,
        "hitsTotal": total,
        "densityPerStep": round(total / length, 4),
        "parts": len(rows),
        "digest": hashlib.sha256(
            (json.dumps(rows, sort_keys=True) + json.dumps(accent, sort_keys=True)
             + signature + str(length)).encode("utf-8")).hexdigest()[:16],
    }


def build_library() -> list[dict[str, Any]]:
    """Normalize the whole corpus in one pass (deterministic order)."""
    return [normalize_pattern(src, idx, raw) for src, idx, raw in iter_patterns()]


def library_digest(patterns: list[dict[str, Any]]) -> str:
    """Content digest over the normalized library (sorted, stable)."""
    payload = json.dumps(
        [(p["source"], p["title"], p["tracks"], p["accentSteps"]) for p in patterns],
        sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def aggregate_stats(patterns: list[dict[str, Any]]) -> dict[str, Any]:
    """High-level statistics over the whole corpus."""
    per_source: dict[str, dict[str, Any]] = {}
    sig_counts: dict[str, int] = {}
    len_counts: dict[str, int] = {}
    part_counts: dict[str, int] = {}
    note_hits: dict[int, int] = {}
    for p in patterns:
        src = p["source"]
        bucket = per_source.setdefault(src, {"patterns": 0, "hitTotal": 0,
                                             "accentHits": 0})
        bucket["patterns"] += 1
        bucket["hitTotal"] += p["hitsTotal"]
        bucket["accentHits"] += p["accentHits"]
        sig_counts[p["signature"]] = sig_counts.get(p["signature"], 0) + 1
        len_counts[str(p["length"]) + " steps"] = \
            len_counts.get(str(p["length"]) + " steps", 0) + 1
        for name, row in p["tracks"].items():
            part_counts[name] = part_counts.get(name, 0) + row["hits"]
            note_hits[row["gm"]] = note_hits.get(row["gm"], 0) + row["hits"]
    return {
        "patternsTotal": len(patterns),
        "perSource": per_source,
        "signatures": sig_counts,
        "lengths": len_counts,
        "partHits": part_counts,
        "gmNoteHits": {str(k): v for k, v in sorted(note_hits.items())},
        "digest": library_digest(patterns),
    }


def row_lookup(patterns: list[dict[str, Any]],
               gm_note: int, steps: str) -> list[str]:
    """Return titles whose part mapped to `gm_note` has the exact same step row.

    Deterministic, ordered by corpus order."""
    out = []
    for p in patterns:
        for name, row in p["tracks"].items():
            if row["gm"] == gm_note and row["steps"] == steps:
                out.append(f'{p["source"]}:{p["title"]}')
    return out


def _quantize_onsets(onsets_ticks: list[int], ppq: int, length: int,
                     start_tick: int, end_tick: int) -> str:
    """Bucket onsets inside [start_tick, end_tick) into `length` equal steps."""
    span = max(1, end_tick - start_tick)
    step_ticks = span / length
    bits = ["0"] * length
    for tick in onsets_ticks:
        if tick < start_tick or tick >= end_tick:
            continue
        idx = int((tick - start_tick) * length // span)
        if 0 <= idx < length:
            bits[idx] = "1"
    return "".join(bits)


def match_real_bar(notes: list[Any], ppq: int, gm_drum_notes: set[int],
                   patterns: list[dict[str, Any]], length: int = 16,
                   span_bars: int = 4) -> dict[str, Any]:
    """Probe: quantize real MIDI drum onsets (given Note objects with .channel,
    .pitch, .start ticks) into step rows of one bar and look up exact corpus
    rows. Returns matched rows + per-role hit rows for evidence."""
    bar_ticks = ppq * span_bars  # span_bars beats per bar (4/4 assumption)
    rows: dict[int, str] = {}
    per_note: dict[int, list[int]] = {}
    for n in notes:
        if n.pitch in gm_drum_notes:
            per_note.setdefault(n.pitch, []).append(n.start)
    matches: dict[str, Any] = {}
    for gm, onsets in per_note.items():
        # first full bar window only (deterministic probe), quantized by start%bar
        start0 = min(onsets)
        window_onsets = [t for t in onsets if start0 <= t < start0 + bar_ticks]
        row = _quantize_onsets(window_onsets, ppq, length, start0,
                               start0 + bar_ticks)
        rows[gm] = row
        if row.count("1") > 0:
            found = row_lookup(patterns, gm, row)[:6]
            if found:
                matches[str(gm)] = {"row": row, "titles": found}
    return {"rows": {str(k): v for k, v in rows.items()}, "matches": matches}


def _main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    patterns = build_library()
    if "--json" in argv:
        import os
        out_dir = Path(os.environ.get("OUT_DIR", ROOT / "artifacts-max-4.64"))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "dmp-library-stats.json").write_text(
            json.dumps(aggregate_stats(patterns), indent=1, sort_keys=True),
            encoding="utf-8")
        print(f"wrote {out_dir / 'dmp-library-stats.json'}")
        return 0
    stats = aggregate_stats(patterns)
    print(f"patterns: {stats['patternsTotal']} | "
          f"per-source: {stats['perSource']} | "
          f"signatures: {stats['signatures']} | digest: {stats['digest']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
