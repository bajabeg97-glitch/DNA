"""user_reference_bank.py — milestone 4.65.

Classifies the user's own reference MIDI files into the SAME canonical
16-step "pattern row" representation used for the vendored drum-pattern corpus
(dmp_midi, see dna_midi_studio/pattern_library.py), builds a reusable
"user reference bank", matches rows against both the corpus and the bank, and
exposes `pattern_recognition(raw, source_name)` that Session Pass attaches to
every report (full use in the optimizer flow).

Honest model (see docs/honest-report-4.65.md):
- quantization assumes a 4/4, 4-beat, 16-step bar grid aligned to tick 0 and
  GM drum key numbers on channels 9 (drums) / 10 (percussion); files that do
  not follow that (other signatures, other drum maps, human-swing timing) will
  produce rows that mostly do NOT match the 16-step corpus rows — that is a
  documented limitation, not a bug.
- only exact 16-step row equality is matched (no swing tolerance yet).

Stdlib-only. Deterministic ordering everywhere.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from dna_midi_studio.midi import MidiFile
from dna_midi_studio.pattern_library import (GM_NOTE, VENDOR_DIR, aggregate_stats,
                                             build_library, iter_patterns,
                                             normalize_pattern, row_lookup)
from dna_midi_studio.sty_mapper import parse_markers

ROOT = Path(__file__).resolve().parents[1]

# The user's own reference set (their uploads used across the project).
USER_REFERENCE_FILES: tuple[str, ...] = (
    "baseline/reference-style.mid",
    "session35-partial-preview.mid",
    "session19-benchmark/song19-01.mid",
)
DRUM_CHANNELS = (9, 10)          # Korg Pa style convention (ch 9 drums, 10 perc)
BEATS_PER_BAR = 4                # assumed 4/4
STEPS = 16
NAME_OF_GM = {v: k for k, v in GM_NOTE.items()}


def _quantize_row(onsets: Iterable[int], start: int, end: int, steps: int = STEPS) -> str:
    span = max(1, end - start)
    bits = ["0"] * steps
    for tick in onsets:
        if tick < start or tick >= end:
            continue
        idx = int((tick - start) * steps // span)
        if 0 <= idx < steps:
            bits[idx] = "1"
    return "".join(bits)


def bars_of(raw: bytes, midi: MidiFile) -> list[dict[str, Any]]:
    """Every 16-step bar window (aligned to tick 0, 4-beat) that contains drum
    channel onsets; rows keyed by GM note -> 16-step string."""
    ppq = midi.ppq
    notes = [n for n in midi.notes() if n.channel in DRUM_CHANNELS]
    if not notes:
        return []
    markers = parse_markers(raw)
    bar_len = ppq * BEATS_PER_BAR
    first_bar = min(n.start for n in notes) // bar_len
    last_bar = max(n.start for n in notes) // bar_len
    by_pitch: dict[int, list[int]] = {}
    for n in notes:
        by_pitch.setdefault(n.pitch, []).append(n.start)
    bars: list[dict[str, Any]] = []
    for bar in range(first_bar, last_bar + 1):
        start = bar * bar_len
        end = start + bar_len
        rows: dict[int, str] = {}
        for pitch in sorted(by_pitch):
            onsets = [t for t in by_pitch[pitch] if start <= t < end]
            if not onsets:
                continue
            row = _quantize_row(onsets, start, end)
            if row.count("1") > 0:
                rows[pitch] = row
        if not rows:
            continue
        # element name = last marker whose tick <= bar start
        element = None
        for m in markers:
            if m["tick"] <= start + bar_len // 2:
                element = m["text"]
        bars.append({"bar": bar, "startTick": start, "endTick": end,
                     "element": element, "rows": {str(k): v for k, v in rows.items()}})
    return bars


def entries_for_file(path: str | Path, patterns: list[dict[str, Any]]
                     ) -> dict[str, Any]:
    """Classify one user reference file into corpus-style entries + matches."""
    path = Path(path)
    raw = path.read_bytes()
    midi = MidiFile.from_bytes(raw)
    bars = bars_of(raw, midi)
    tracks: dict[str, dict[str, Any]] = {}
    corpus_matches: dict[str, Any] = {}
    motif_counts: dict[str, int] = {}
    bar_labels: dict[str, list[int]] = {}
    for b in bars:
        for gm_s, row in b["rows"].items():
            gm = int(gm_s)
            name = NAME_OF_GM.get(gm, f"GM{gm}")
            tracks.setdefault(name, {"gm": gm, "rows": [], "hits": 0})
            if row not in tracks[name]["rows"]:
                tracks[name]["rows"].append(row)
            tracks[name]["hits"] += row.count("1")
            key = f"{gm}:{row}"
            motif_counts[key] = motif_counts.get(key, 0) + 1
            bar_labels.setdefault(key, []).append(b["bar"])
            found = row_lookup(patterns, gm, row)
            if found:
                corpus_matches.setdefault(key, {"gm": gm, "row": row,
                                                "titles": found})
    # corpus-style normalized pattern entry (mirror of dmp schema)
    pattern_entry = {
        "source": str(path),
        "title": path.stem,
        "signature": f"{BEATS_PER_BAR}/4",
        "signatureAssumed": True,
        "length": STEPS,
        "barsAnalyzed": len(bars),
        "tracks": {name: {"gm": t["gm"], "steps": t["rows"][0]
                          if len(t["rows"]) == 1 else None,
                          "distinctRows": len(t["rows"]), "hits": t["hits"]}
                   for name, t in tracks.items()},
        "digest": hashlib.sha256(
            raw + json.dumps(bars, sort_keys=True).encode("utf-8")).hexdigest()[:16],
    }
    return {
        "file": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "markers": [m["text"] for m in parse_markers(raw)],
        "bars": bars,
        "patternEntry": pattern_entry,
        "corpusMatches": corpus_matches,
        "motifCounts": dict(sorted(motif_counts.items())),
        "barOfMotif": {k: v for k, v in sorted(bar_labels.items())},
    }


def build_bank(patterns: list[dict[str, Any]] | None = None
               ) -> list[dict[str, Any]]:
    patterns = patterns if patterns is not None else build_library()
    return [entries_for_file(ROOT / f, patterns) for f in USER_REFERENCE_FILES]


def bank_digest(bank: list[dict[str, Any]]) -> str:
    payload = json.dumps([(e["file"], e["bars"], e["corpusMatches"])
                          for e in bank], sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def classification_report(bank: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate, corpus-style classification of the user's references."""
    per_file: dict[str, Any] = {}
    shared: dict[str, int] = {}
    per_file_keys: dict[str, set[str]] = {}
    total_bars = 0
    for e in bank:
        stem = Path(e["file"]).name
        keys = set(e["motifCounts"])
        per_file_keys[stem] = keys
        per_file[stem] = {
            "barsAnalyzed": len(e["bars"]),
            "markers": e["markers"],
            "distinctMotifs": len(e["motifCounts"]),
            "corpusMatchedMotifs": len(e["corpusMatches"]),
            "corpusMatches": {k: v["titles"] for k, v in e["corpusMatches"].items()},
        }
        total_bars += len(e["bars"])
        for k, cnt in e["motifCounts"].items():
            shared[k] = shared.get(k, 0) + cnt
    # honest separation: repeats within/overall vs rows truly shared by >=2 files
    repeated_rows = {k: v for k, v in sorted(shared.items()) if v > 1}
    across_files: dict[str, list[str]] = {}
    names = list(per_file_keys)
    for k in sorted(shared):
        owners = [n for n in names if k in per_file_keys[n]]
        if len(owners) >= 2:
            across_files[k] = owners
    return {
        "schema": "dna-user-reference-classification",
        "milestone": "4.65",
        "method": ("16-step row quantization per 4-beat bar (4/4 assumption), "
                   "channels 9/10, GM drum notes; exact-row matching only"),
        "files": per_file,
        "totalBars": total_bars,
        "motifsSeen": sum(per_file[s]["distinctMotifs"] for s in per_file),
        "rowsRepeatedOverall": len(repeated_rows),
        "repeatedRows": repeated_rows,
        "rowsSharedAcrossFiles": len(across_files),
        "sharedRowsAcrossFiles": across_files,
        "digest": bank_digest(bank),
    }


def pattern_recognition(raw: bytes, source_name: str) -> dict[str, Any]:
    """Session Pass integration: classify drum rows of any input and match them
    against (a) the vendored 468-pattern corpus and (b) the user reference
    bank. Deterministic; stdlib-only; never blocks the pass."""
    patterns = build_library()
    midi = MidiFile.from_bytes(raw)
    bars = bars_of(raw, midi)
    bank = build_bank(patterns)
    corpus: dict[str, Any] = {}
    user_refs: dict[str, Any] = {}
    for b in bars:
        for gm_s, row in b["rows"].items():
            gm = int(gm_s)
            found = row_lookup(patterns, gm, row)
            if found:
                corpus[f"{gm}:{row}"] = {"gm": gm, "row": row, "titles": found}
            refs = [Path(x["file"]).name for x in bank
                    if f"{gm}:{row}" in x["motifCounts"]]
            if refs:
                user_refs[f"{gm}:{row}"] = {"gm": gm, "row": row,
                                            "userFiles": sorted(set(refs))}
    return {
        "schema": "dna-pattern-recognition",
        "version": "4.65",
        "sourceName": source_name,
        "note": ("16-step rows on channels 9/10, 4/4 bar grid, exact matches "
                 "only; non-GM or swung tracks may legitimately match nothing"),
        "barsAnalyzed": len(bars),
        "corpusMatches": corpus,
        "userReferenceMatches": user_refs,
        "digest": hashlib.sha256(
            json.dumps([corpus, user_refs], sort_keys=True)
            .encode("utf-8")).hexdigest()[:16],
    }


def _main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    patterns = build_library()
    bank = build_bank(patterns)
    report = classification_report(bank)
    if "--json" in argv:
        out = ROOT / "artifacts-max-4.65"
        out.mkdir(parents=True, exist_ok=True)
        (out / "user-reference-bank.json").write_text(
            json.dumps(bank, indent=1, ensure_ascii=False), encoding="utf-8")
        (out / "user-reference-classification.json").write_text(
            json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        print("wrote user-reference-bank.json + user-reference-classification.json")
        return 0
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
