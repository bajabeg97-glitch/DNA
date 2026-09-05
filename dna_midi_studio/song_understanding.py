"""Session 19 deterministic Song Understanding 2.0.

The analyzer is read-only and velocity-blind.  It converts an SMF into a
strict, explainable SongMap containing tempo/meter grids, half-bar harmony,
phrase/section evidence, time-scoped roles, polyphony and uncertainty.  User
corrections are a separate overlay and never rewrite the source MIDI.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
import json
import math
import re
import statistics
from typing import Any, Iterable, Mapping, Sequence

from .midi import MidiEvent, MidiFile, Note
from .track_identity import build_track_identities


PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
SONG_MAP_SCHEMA = "dna-premium-song-map"
SONG_MAP_VERSION = "2.0"
CORRECTION_SCHEMA = "dna-song-map-corrections"
CORRECTION_VERSION = "1.0"
_TRACK_UID = re.compile(r"^trk-[0-9a-f]{20}(?:-[1-9][0-9]*)?$")
_SECTION_LABELS = {
    "intro": "intro", "uvod": "intro",
    "verse": "verse", "strofa": "verse",
    "chorus": "chorus", "refren": "chorus",
    "bridge": "bridge", "most": "bridge",
    "break": "break", "pauza": "break",
    "build": "build-up", "build-up": "build-up",
    "drop": "drop",
    "ending": "ending", "outro": "ending", "kraj": "ending",
}
_QUALITY_SUFFIX = {
    "major": "", "minor": "m", "power": "5", "diminished": "dim",
    "augmented": "aug", "suspended-2": "sus2", "suspended-4": "sus4",
    "sixth": "6", "minor-sixth": "m6", "dominant-seventh": "7",
    "major-seventh": "maj7", "minor-seventh": "m7",
    "half-diminished": "m7b5", "diminished-seventh": "dim7",
    "add-nine": "add9",
}
_SUFFIX_QUALITY = {suffix: quality for quality, suffix in _QUALITY_SUFFIX.items()}
_CHORD_TEMPLATES = {
    "major": (0, 4, 7), "minor": (0, 3, 7), "power": (0, 7),
    "diminished": (0, 3, 6), "augmented": (0, 4, 8),
    "suspended-2": (0, 2, 7), "suspended-4": (0, 5, 7),
    "sixth": (0, 4, 7, 9), "minor-sixth": (0, 3, 7, 9),
    "dominant-seventh": (0, 4, 7, 10), "major-seventh": (0, 4, 7, 11),
    "minor-seventh": (0, 3, 7, 10), "half-diminished": (0, 3, 6, 10),
    "diminished-seventh": (0, 3, 6, 9), "add-nine": (0, 2, 4, 7),
}
_ROOT_NAMES = {name: index for index, name in enumerate(PITCH_NAMES)}
_ROOT_NAMES.update({"Db": 1, "Eb": 3, "Gb": 6, "Ab": 8, "Bb": 10})


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _map_hash(song_map: Mapping[str, Any]) -> str:
    value = {key: item for key, item in song_map.items() if key != "mapHash"}
    return sha256(_canonical(value)).hexdigest()


def _correlation(left: Sequence[float], right: Sequence[float]) -> float:
    mean_left, mean_right = statistics.mean(left), statistics.mean(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    denominator = math.sqrt(
        sum((a - mean_left) ** 2 for a in left)
        * sum((b - mean_right) ** 2 for b in right)
    )
    return numerator / denominator if denominator else 0.0


def _key(notes: Sequence[Note], ppq: int) -> dict[str, Any]:
    histogram = [0.0] * 12
    for note in notes:
        if note.channel == 9:
            continue
        histogram[note.pitch % 12] += min(note.end - note.start, ppq * 4)
    if not sum(histogram):
        return {"root": 0, "mode": "major", "name": "C", "confidence": 0.0,
                "source": "fallback-no-harmonic-notes"}
    candidates = []
    for root in range(12):
        for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            rotated = [profile[(pitch - root) % 12] for pitch in range(12)]
            candidates.append((_correlation(histogram, rotated), root, mode))
    candidates.sort(reverse=True)
    best, second = candidates[:2]
    confidence = max(0.0, min(1.0, (best[0] - second[0] + 0.04) / 0.22))
    return {
        "root": best[1], "mode": best[2],
        "name": PITCH_NAMES[best[1]] + ("m" if best[2] == "minor" else ""),
        "confidence": round(confidence, 4), "source": "duration-weighted-pitch-class-profile",
    }


def _tempo_map(midi: MidiFile) -> list[dict[str, Any]]:
    events = []
    for track_index, track in enumerate(midi.tracks):
        for event in track.events:
            if event.kind == "meta" and event.meta_type == 0x51 and len(event.data) == 3:
                micros = int.from_bytes(event.data, "big")
                if micros > 0:
                    events.append((event.tick, event.order, track_index, micros))
    events.sort()
    collapsed: dict[int, tuple[int, int, int]] = {}
    for tick, order, track, micros in events:
        collapsed[tick] = (order, track, micros)
    if 0 not in collapsed:
        collapsed[0] = (-1, -1, 500000)
    return [
        {"tick": tick, "bpm": round(60_000_000 / value[2], 6),
         "microsecondsPerQuarter": value[2], "sourceTrackIndex": value[1]}
        for tick, value in sorted(collapsed.items())
    ]


def _meter_map(midi: MidiFile) -> list[dict[str, Any]]:
    events = []
    for track_index, track in enumerate(midi.tracks):
        for event in track.events:
            if event.kind == "meta" and event.meta_type == 0x58 and len(event.data) >= 2:
                numerator, power = event.data[:2]
                denominator = 2 ** power
                if numerator > 0 and denominator in {1, 2, 4, 8, 16, 32}:
                    events.append((event.tick, event.order, track_index, numerator, denominator))
    events.sort()
    collapsed: dict[int, tuple[int, int, int, int]] = {}
    for tick, order, track, numerator, denominator in events:
        collapsed[tick] = (order, track, numerator, denominator)
    if 0 not in collapsed:
        collapsed[0] = (-1, -1, 4, 4)
    return [
        {"tick": tick, "numerator": value[2], "denominator": value[3],
         "sourceTrackIndex": value[1]}
        for tick, value in sorted(collapsed.items())
    ]


def _time_grid(
    ppq: int, meter_map: Sequence[Mapping[str, Any]], end_tick: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    bars: list[dict[str, Any]] = []
    beats: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    bar_number = 1
    for index, meter in enumerate(meter_map):
        start = int(meter["tick"])
        segment_end = int(meter_map[index + 1]["tick"]) if index + 1 < len(meter_map) else end_tick
        if start >= end_tick:
            break
        numerator, denominator = int(meter["numerator"]), int(meter["denominator"])
        beat_ticks = ppq * 4 / denominator
        bar_ticks = beat_ticks * numerator
        cursor = start
        while cursor < min(segment_end, end_tick):
            bar_end = min(segment_end, end_tick, int(round(cursor + bar_ticks)))
            if bar_end <= cursor:
                break
            bar = {
                "bar": bar_number, "startTick": cursor, "endTick": bar_end,
                "numerator": numerator, "denominator": denominator,
            }
            bars.append(bar)
            for beat in range(numerator):
                tick = int(round(cursor + beat * beat_ticks))
                if tick >= bar_end:
                    break
                beats.append({
                    "tick": tick, "bar": bar_number, "beat": beat + 1,
                    "downbeat": beat == 0, "numerator": numerator,
                    "denominator": denominator,
                })
            midpoint = cursor + (bar_end - cursor) // 2
            for part, (cell_start, cell_end) in enumerate(((cursor, midpoint), (midpoint, bar_end)), 1):
                if cell_end > cell_start:
                    cells.append({
                        "cellIndex": len(cells), "bar": bar_number, "part": part,
                        "startTick": cell_start, "endTick": cell_end,
                        "numerator": numerator, "denominator": denominator,
                    })
            cursor = bar_end
            bar_number += 1
    return bars, beats, cells


def _program_at(midi: MidiFile, note: Note) -> int:
    value = midi.program_at(note.channel, note.start, note.track)
    return 0 if value is None else value


def _base_role(midi: MidiFile, note: Note, program: int | None = None) -> str:
    if note.channel == 9:
        return "drums"
    if program is None:
        program = _program_at(midi, note)
    if 32 <= program <= 39 or note.pitch < 45:
        return "bass"
    if program <= 31 or 48 <= program <= 55:
        return "harmony"
    return "melody"


def _scale_pitch_classes(key: Mapping[str, Any]) -> set[int]:
    intervals = (0, 2, 4, 5, 7, 9, 11) if key["mode"] == "major" else (0, 2, 3, 5, 7, 8, 10)
    return {(int(key["root"]) + interval) % 12 for interval in intervals}


def _chord_candidate(
    histogram: Sequence[float], bass_histogram: Sequence[float], key: Mapping[str, Any]
) -> dict[str, Any]:
    total = sum(histogram)
    if total <= 0:
        return {
            "symbol": "N.C.", "root": None, "quality": "none", "bass": None,
            "confidence": 0.0, "coverage": 0.0, "nonChordToneRatio": 0.0,
            "modalBorrowing": False, "alternatives": [],
            "uncertainty": ["no-harmonic-evidence"],
        }
    observed = {pitch for pitch, value in enumerate(histogram) if value > 0}
    scale = _scale_pitch_classes(key)
    ranked = []
    for root in range(12):
        for quality, intervals in _CHORD_TEMPLATES.items():
            tones = {(root + interval) % 12 for interval in intervals}
            inside = sum(histogram[pitch] for pitch in tones)
            precision = inside / total
            tone_coverage = len(observed & tones) / len(tones)
            root_weight = histogram[root] / total
            bass_pc = max(range(12), key=lambda pitch: bass_histogram[pitch]) if sum(bass_histogram) else None
            bass_bonus = 0.07 if bass_pc == root else 0.025 if bass_pc in tones else 0.0
            diatonic_bonus = 0.025 if tones <= scale else 0.0
            complexity_penalty = 0.012 * max(0, len(intervals) - 3)
            missing_penalty = 0.16 * (1 - tone_coverage)
            power_penalty = 0.055 if quality == "power" and len(observed) >= 3 else 0.0
            score = (
                0.57 * precision + 0.27 * tone_coverage + 0.08 * root_weight
                + bass_bonus + diatonic_bonus - complexity_penalty
                - missing_penalty - power_penalty
            )
            ranked.append((score, precision, tone_coverage, root, quality, tones, bass_pc))
    ranked.sort(key=lambda item: (-item[0], item[3], item[4]))
    best, second = ranked[:2]
    margin = max(0.0, best[0] - second[0])
    non_chord = max(0.0, 1 - best[1])
    confidence = max(0.0, min(1.0,
        0.42 * best[1] + 0.28 * best[2] + 0.30 * min(1.0, margin / 0.12)
    ))
    bass = best[6]
    symbol = PITCH_NAMES[best[3]] + _QUALITY_SUFFIX[best[4]]
    if bass is not None and bass != best[3] and bass in best[5]:
        symbol += "/" + PITCH_NAMES[bass]
    uncertainty = []
    if confidence < 0.68:
        uncertainty.append("low-chord-confidence")
    if non_chord > 0.28:
        uncertainty.append("high-non-chord-tone-ratio")
    if margin < 0.045:
        uncertainty.append("close-chord-alternatives")
    if len(observed) < 2:
        uncertainty.append("sparse-harmonic-evidence")
    return {
        "symbol": symbol, "root": best[3], "quality": best[4], "bass": bass,
        "confidence": round(confidence, 4), "coverage": round(best[1], 4),
        "nonChordToneRatio": round(non_chord, 4),
        "modalBorrowing": not best[5] <= scale,
        "alternatives": [
            {"symbol": PITCH_NAMES[item[3]] + _QUALITY_SUFFIX[item[4]],
             "score": round(item[0], 5)}
            for item in ranked[1:4]
        ],
        "uncertainty": uncertainty,
    }


def _chord_cells(
    midi: MidiFile, notes: Sequence[Note], cells: Sequence[Mapping[str, Any]], key: Mapping[str, Any]
) -> list[dict[str, Any]]:
    starts = [int(cell["startTick"]) for cell in cells]
    histograms = [[0.0] * 12 for _ in cells]
    bass_histograms = [[0.0] * 12 for _ in cells]
    role_weights = [Counter() for _ in cells]
    note_counts = [0 for _ in cells]
    program_cache: dict[tuple[int, int, int], int] = {}
    for note in notes:
        if note.channel == 9 or not cells:
            continue
        first = max(0, bisect_left(starts, note.start) - 1)
        while first + 1 < len(cells) and int(cells[first]["endTick"]) <= note.start:
            first += 1
        index = first
        program_key = (note.track, note.channel, note.start)
        if program_key not in program_cache:
            program_cache[program_key] = _program_at(midi, note)
        role = _base_role(midi, note, program_cache[program_key])
        role_weight = {"bass": 1.35, "harmony": 1.0, "melody": 0.34}.get(role, 0.8)
        while index < len(cells) and int(cells[index]["startTick"]) < note.end:
            overlap = max(0, min(note.end, int(cells[index]["endTick"]))
                          - max(note.start, int(cells[index]["startTick"])))
            if overlap:
                histograms[index][note.pitch % 12] += overlap * role_weight
                role_weights[index][role] += overlap * role_weight
                note_counts[index] += 1
                if role == "bass":
                    bass_histograms[index][note.pitch % 12] += overlap
            index += 1
    output = []
    previous: dict[str, Any] | None = None
    for index, cell in enumerate(cells):
        chord = _chord_candidate(histograms[index], bass_histograms[index], key)
        if chord["root"] is None and previous is not None:
            chord.update({
                "symbol": previous["symbol"], "root": previous["root"],
                "quality": previous["quality"], "bass": previous["bass"],
                "uncertainty": ["carried-forward-no-evidence"],
            })
        elif chord["root"] is not None:
            previous = chord
        output.append({
            **dict(cell), **chord, "noteEvidenceCount": note_counts[index],
            "evidenceWeights": {name: round(value, 3) for name, value in sorted(role_weights[index].items())},
            "analysisVelocityUsed": False,
            "decision": "MANUAL_REVIEW" if chord["uncertainty"] else "ACCEPT",
        })
    return output


def _markers(midi: MidiFile) -> list[dict[str, Any]]:
    output = []
    for track_index, track in enumerate(midi.tracks):
        for event in track.events:
            if event.kind != "meta" or event.meta_type not in {0x01, 0x06}:
                continue
            text = event.data.decode("utf-8", errors="replace").strip()
            normalized = text.lower().replace("_", "-")
            candidate = normalized.split(":", 1)[-1].strip()
            label = _SECTION_LABELS.get(candidate)
            if label:
                output.append({"tick": event.tick, "label": label, "text": text,
                               "trackIndex": track_index, "metaType": event.meta_type})
    deduped = {}
    for item in sorted(output, key=lambda value: (value["tick"], value["trackIndex"], value["text"])):
        deduped.setdefault(item["tick"], item)
    return list(deduped.values())


def _bar_metrics(notes: Sequence[Note], bars: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for bar in bars:
        start, end = int(bar["startTick"]), int(bar["endTick"])
        onsets = [note for note in notes if start <= note.start < end]
        active_edges = []
        for note in notes:
            if note.channel == 9:
                continue
            overlap_start, overlap_end = max(start, note.start), min(end, note.end)
            if overlap_end > overlap_start:
                active_edges.extend(((overlap_start, 1), (overlap_end, -1)))
        active = peak = 0
        for _, delta in sorted(active_edges, key=lambda item: (item[0], item[1])):
            active += delta
            peak = max(peak, active)
        output.append({
            **dict(bar), "noteOnsets": len(onsets),
            "drumOnsets": sum(note.channel == 9 for note in onsets),
            "harmonicOnsets": sum(note.channel != 9 for note in onsets),
            "polyphonyPeak": peak,
        })
    return output


def _heuristic_boundaries(bar_metrics: Sequence[Mapping[str, Any]]) -> list[int]:
    if not bar_metrics:
        return [0]
    starts = [int(bar_metrics[0]["startTick"])]
    last_bar = 1
    for index in range(1, len(bar_metrics)):
        previous, current = bar_metrics[index - 1], bar_metrics[index]
        p, c = int(previous["noteOnsets"]), int(current["noteOnsets"])
        change = abs(c - p) / max(1, p, c)
        aligned = (index + 1 - last_bar) >= 4 and index % 2 == 0
        rest_edge = min(p, c) == 0
        if (change >= 0.45 and aligned) or rest_edge or index + 1 - last_bar >= 8:
            starts.append(int(current["startTick"]))
            last_bar = index + 1
    return starts


def _sections(
    midi: MidiFile,
    notes: Sequence[Note],
    bars: Sequence[Mapping[str, Any]],
    bar_metrics: Sequence[Mapping[str, Any]],
    end_tick: int,
) -> list[dict[str, Any]]:
    marker_items = _markers(midi)
    if marker_items:
        starts = [item["tick"] for item in marker_items if item["tick"] < end_tick]
        if not starts or starts[0] != 0:
            marker_items.insert(0, {"tick": 0, "label": "intro", "text": "inferred-intro",
                                    "trackIndex": -1, "metaType": -1})
        markers_by_tick = {item["tick"]: item for item in marker_items if item["tick"] < end_tick}
        ticks = sorted(markers_by_tick)
        sections = []
        for index, start in enumerate(ticks):
            end = ticks[index + 1] if index + 1 < len(ticks) else end_tick
            item = markers_by_tick[start]
            if end > start:
                sections.append({
                    "id": f"sec-{index + 1:03d}", "label": item["label"],
                    "startTick": start, "endTick": end, "confidence": 0.99,
                    "evidence": {"kind": "midi-marker", "text": item["text"],
                                 "trackIndex": item["trackIndex"]},
                    "decision": "ACCEPT",
                })
        return sections
    starts = _heuristic_boundaries(bar_metrics)
    ticks = sorted(set(starts))
    sections = []
    energies = []
    for index, start in enumerate(ticks):
        end = ticks[index + 1] if index + 1 < len(ticks) else end_tick
        local = [item for item in bar_metrics if start <= int(item["startTick"]) < end]
        energies.append(statistics.mean(item["noteOnsets"] for item in local) if local else 0.0)
    median = statistics.median(energies) if energies else 0.0
    maximum = max(energies, default=1.0) or 1.0
    for index, (start, energy) in enumerate(zip(ticks, energies)):
        end = ticks[index + 1] if index + 1 < len(ticks) else end_tick
        if index == 0 and energy < median * 0.85:
            label = "intro"
        elif index == len(ticks) - 1:
            label = "ending"
        elif energy >= maximum * 0.82:
            label = "chorus"
        else:
            label = "verse"
        confidence = 0.72 if len(ticks) > 1 else 0.48
        sections.append({
            "id": f"sec-{index + 1:03d}", "label": label,
            "startTick": start, "endTick": end, "confidence": confidence,
            "evidence": {"kind": "density-phrase-heuristic", "meanOnsets": round(energy, 3)},
            "decision": "ACCEPT" if confidence >= 0.68 else "MANUAL_REVIEW",
        })
    return sections


def _overlap_notes(notes: Iterable[Note], start: int, end: int) -> list[Note]:
    return [note for note in notes if note.end > start and note.start < end]


def _peak_polyphony(notes: Sequence[Note]) -> int:
    events = []
    for note in notes:
        events.extend(((note.start, 1), (note.end, -1)))
    active = peak = 0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        active += delta
        peak = max(peak, active)
    return peak


def _track_name(midi: MidiFile, track_index: int) -> str:
    for event in midi.tracks[track_index].events:
        if event.kind == "meta" and event.meta_type == 0x03:
            return event.data.decode("utf-8", errors="replace").strip()
    return ""


def _role_segments(
    midi: MidiFile, notes: Sequence[Note], sections: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    identities = build_track_identities(midi)
    grouped: dict[tuple[int, int], list[Note]] = defaultdict(list)
    for note in notes:
        grouped[(note.track, note.channel)].append(note)
    result = []
    program_cache: dict[tuple[int, int, int], int] = {}
    for (track_index, channel), track_notes in sorted(grouped.items()):
        name = _track_name(midi, track_index)
        for section in sections:
            local = _overlap_notes(track_notes, int(section["startTick"]), int(section["endTick"]))
            if not local:
                continue
            pitches = [note.pitch for note in local]
            durations = [note.end - note.start for note in local]
            peak = _peak_polyphony(local)
            for note in local:
                key = (note.track, note.channel, note.start)
                if key not in program_cache:
                    program_cache[key] = _program_at(midi, note)
            program_counts = Counter(program_cache[(note.track, note.channel, note.start)]
                                     for note in local)
            program = program_counts.most_common(1)[0][0]
            median_pitch = statistics.median(pitches)
            avg_duration = statistics.mean(durations)
            lowered = name.lower()
            reasons = []
            if channel == 9:
                role, confidence = "drums", 1.0
                reasons.append("midi-channel-10")
            elif 32 <= program <= 39 or median_pitch < 45:
                role, confidence = "bass", 0.93
                reasons.append("bass-program-or-register")
            elif avg_duration >= midi.ppq * 1.8 and peak >= 2:
                role, confidence = "pad", 0.82
                reasons.append("sustained-polyphonic-texture")
            elif any(token in lowered for token in ("solo", "lead", "melody")) and peak <= 2:
                role, confidence = "solo", 0.94
                reasons.append("track-name-and-monophony")
            elif peak >= 3 or program <= 31 or 48 <= program <= 55:
                role, confidence = "harmony", 0.84
                reasons.append("polyphony-or-harmony-program")
            elif peak <= 1:
                role, confidence = "melody", 0.76
                reasons.append("monophonic-contour")
            else:
                role, confidence = "accompaniment", 0.62
                reasons.append("mixed-role-evidence")
            result.append({
                "trackUid": identities[track_index].track_uid,
                "trackIndex": track_index, "trackNumber": track_index + 1,
                "channelIndex": channel, "channelNumber": channel + 1,
                "sectionId": section["id"], "startTick": int(section["startTick"]),
                "endTick": int(section["endTick"]), "role": role,
                "confidence": confidence, "decision": "ACCEPT" if confidence >= 0.68 else "MANUAL_REVIEW",
                "evidence": {"trackName": name, "program": program, "noteCount": len(local),
                             "medianPitch": round(median_pitch, 3),
                             "averageDuration": round(avg_duration, 3),
                             "polyphonyPeak": peak, "reasons": reasons},
            })
    return result


def _section_chords(
    chord_cells: Sequence[Mapping[str, Any]], section: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    return [cell for cell in chord_cells
            if int(cell["endTick"]) > int(section["startTick"])
            and int(cell["startTick"]) < int(section["endTick"])
            and cell.get("root") is not None]


def _cadences(
    sections: Sequence[Mapping[str, Any]], chord_cells: Sequence[Mapping[str, Any]], key: Mapping[str, Any]
) -> list[dict[str, Any]]:
    result = []
    tonic, dominant, subdominant = int(key["root"]), (int(key["root"]) + 7) % 12, (int(key["root"]) + 5) % 12
    for section in sections:
        cells = _section_chords(chord_cells, section)
        unique = []
        for cell in cells:
            if not unique or unique[-1]["symbol"] != cell["symbol"]:
                unique.append(cell)
        roots = [int(cell["root"]) for cell in unique[-2:]]
        cadence = "unresolved"
        if len(roots) >= 2 and roots[-2:] == [dominant, tonic]:
            cadence = "authentic"
        elif len(roots) >= 2 and roots[-2:] == [subdominant, tonic]:
            cadence = "plagal"
        elif roots and roots[-1] == dominant:
            cadence = "half"
        confidence = min((float(cell["confidence"]) for cell in unique[-2:]), default=0.0)
        result.append({
            "sectionId": section["id"], "tick": int(section["endTick"]),
            "type": cadence, "confidence": round(confidence, 4),
            "evidenceSymbols": [cell["symbol"] for cell in unique[-2:]],
            "decision": "ACCEPT" if cadence != "unresolved" and confidence >= 0.68 else "MANUAL_REVIEW",
        })
    return result


def _phrases(
    notes: Sequence[Note], sections: Sequence[Mapping[str, Any]], bars: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    result = []
    previous_density = None
    first_note = min((note.start for note in notes), default=0)
    for section in sections:
        local_bars = [bar for bar in bars if int(section["startTick"]) <= int(bar["startTick"]) < int(section["endTick"])]
        chunks = [local_bars[index:index + 4] for index in range(0, len(local_bars), 4)] or [[]]
        for chunk_index, chunk in enumerate(chunks):
            start = int(chunk[0]["startTick"]) if chunk else int(section["startTick"])
            end = int(chunk[-1]["endTick"]) if chunk else int(section["endTick"])
            local_notes = _overlap_notes(notes, start, end)
            bar_densities = [sum(bar["startTick"] <= note.start < bar["endTick"] for note in local_notes)
                             for bar in chunk]
            density = statistics.mean(bar_densities) if bar_densities else 0.0
            trend = (bar_densities[-1] - bar_densities[0]) if len(bar_densities) >= 2 else 0
            cues = []
            if not result and 0 < first_note < (chunk[0]["endTick"] - chunk[0]["startTick"]) / 2:
                cues.append("pickup")
            if density <= 1:
                cues.append("break")
            if trend >= max(2, density * 0.35):
                cues.append("build-up")
            if previous_density is not None and density >= max(3, previous_density * 1.5):
                cues.append("drop")
            if section["label"] == "ending" and chunk_index == len(chunks) - 1:
                cues.append("ending-phrase")
            result.append({
                "id": f"phrase-{len(result) + 1:03d}", "sectionId": section["id"],
                "startTick": start, "endTick": end,
                "barStart": int(chunk[0]["bar"]) if chunk else None,
                "barEnd": int(chunk[-1]["bar"]) if chunk else None,
                "density": round(density, 3), "trend": round(trend, 3),
                "cues": cues, "confidence": 0.92 if section["evidence"]["kind"] == "midi-marker" else 0.7,
            })
            previous_density = density
    return result


def _polyphony(notes: Sequence[Note], sections: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    melodic = [note for note in notes if note.channel != 9]
    per_section = []
    for section in sections:
        local = _overlap_notes(melodic, int(section["startTick"]), int(section["endTick"]))
        per_section.append({"sectionId": section["id"], "peak": _peak_polyphony(local)})
    return {"globalPeak": _peak_polyphony(melodic), "sections": per_section,
            "method": "full-note-duration-sweep"}


def _manual_review(
    chords: Sequence[Mapping[str, Any]], sections: Sequence[Mapping[str, Any]], roles: Sequence[Mapping[str, Any]],
    cadences: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for kind, items in (("chord", chords), ("section", sections), ("role", roles), ("cadence", cadences)):
        for item in items:
            if item.get("decision") == "MANUAL_REVIEW":
                result.append({
                    "kind": kind,
                    "id": item.get("id", item.get("cellIndex", item.get("sectionId"))),
                    "startTick": item.get("startTick", item.get("tick", 0)),
                    "endTick": item.get("endTick", item.get("tick", 0)),
                    "confidence": item.get("confidence", 0.0),
                    "reasons": item.get("uncertainty", ["confidence-below-accept-threshold"]),
                })
    return result


def analyze_song_map(data: bytes, source_name: str = "song.mid") -> dict[str, Any]:
    source_hash = sha256(data).hexdigest()
    midi = MidiFile.from_bytes(data)
    notes = midi.notes()
    if not notes:
        raise ValueError("SongMap 2.0 requires at least one paired MIDI note")
    tempo_map = _tempo_map(midi)
    meter_map = _meter_map(midi)
    max_event_tick = max((event.tick for track in midi.tracks for event in track.events), default=0)
    note_end = max(note.end for note in notes)
    initial_meter = meter_map[0]
    minimum_end = int(round(midi.ppq * int(initial_meter["numerator"]) * 4 / int(initial_meter["denominator"])))
    end_tick = max(max_event_tick, note_end, minimum_end)
    bars, beats, cells = _time_grid(midi.ppq, meter_map, end_tick)
    key = _key(notes, midi.ppq)
    chords = _chord_cells(midi, notes, cells, key)
    bar_metrics = _bar_metrics(notes, bars)
    sections = _sections(midi, notes, bars, bar_metrics, end_tick)
    roles = _role_segments(midi, notes, sections)
    cadences = _cadences(sections, chords, key)
    phrases = _phrases(notes, sections, bars)
    manual_review = _manual_review(chords, sections, roles, cadences)
    confidence_values = [
        *(float(item["confidence"]) for item in chords),
        *(float(item["confidence"]) for item in sections),
        *(float(item["confidence"]) for item in roles),
    ]
    result = {
        "schema": SONG_MAP_SCHEMA, "version": SONG_MAP_VERSION,
        "source": {"fileName": source_name, "bytes": len(data), "sha256": source_hash,
                   "format": midi.format_type, "ppq": midi.ppq,
                   "trackCount": len(midi.tracks), "noteCount": len(notes)},
        "sourceSha256": source_hash, "ppq": midi.ppq, "endTick": end_tick,
        "tempoMap": tempo_map, "meterMap": meter_map, "bars": bars,
        "beatGrid": beats, "key": key, "chordCells": chords,
        "sections": sections, "phrases": phrases, "cadences": cadences,
        "roleSegments": roles, "polyphony": _polyphony(notes, sections),
        "manualReview": manual_review,
        "confidence": round(statistics.mean(confidence_values), 4) if confidence_values else 0.0,
        "analysisVelocityUsed": False,
        "correctionAudit": [],
        "analysis": {
            "halfBarHarmony": True, "variableTempo": len(tempo_map) > 1,
            "variableMeter": len(meter_map) > 1,
            "sourceBytesMutated": False,
        },
        "invariants": {
            "analysisVelocityUsed": False, "sourceMidiMutated": False,
            "lowConfidenceRequiresManualReview": True,
            "trackRolesAreTimeScoped": True,
        },
    }
    result["mapHash"] = _map_hash(result)
    validate_song_map_v2(result)
    return result


def _parse_chord_symbol(symbol: str) -> tuple[int, str, int | None]:
    match = re.fullmatch(r"([A-G](?:#|b)?)(m7b5|maj7|m7|dim7|sus2|sus4|add9|m6|dim|aug|m|5|6|7)?(?:/([A-G](?:#|b)?))?", symbol)
    if not match or match.group(1) not in _ROOT_NAMES:
        raise ValueError(f"Unsupported corrected chord symbol: {symbol}")
    suffix = match.group(2) or ""
    if suffix not in _SUFFIX_QUALITY:
        raise ValueError(f"Unsupported corrected chord quality: {symbol}")
    bass = _ROOT_NAMES.get(match.group(3)) if match.group(3) else None
    return _ROOT_NAMES[match.group(1)], _SUFFIX_QUALITY[suffix], bass


def apply_song_map_corrections(
    song_map: Mapping[str, Any], corrections: Mapping[str, Any]
) -> dict[str, Any]:
    validate_song_map_v2(song_map)
    allowed = {"schema", "version", "sourceSha256", "chordOverrides", "sectionOverrides", "reason"}
    unknown = sorted(set(corrections) - allowed)
    if unknown:
        raise ValueError("Unknown SongMap correction fields: " + ", ".join(unknown))
    if corrections.get("schema") != CORRECTION_SCHEMA or corrections.get("version") != CORRECTION_VERSION:
        raise ValueError("Unsupported SongMap correction schema/version")
    if corrections.get("sourceSha256") != song_map["sourceSha256"]:
        raise ValueError("SongMap correction source hash mismatch")
    output = deepcopy(song_map)
    audit = list(output.get("correctionAudit", []))
    chord_overrides = corrections.get("chordOverrides", [])
    section_overrides = corrections.get("sectionOverrides", [])
    if not isinstance(chord_overrides, list) or not isinstance(section_overrides, list):
        raise ValueError("SongMap correction overrides must be arrays")
    for override in chord_overrides:
        if set(override) != {"cellIndex", "symbol"}:
            raise ValueError("Chord override requires exactly cellIndex and symbol")
        index = int(override["cellIndex"])
        if not 0 <= index < len(output["chordCells"]):
            raise ValueError("Chord correction cellIndex is outside the SongMap")
        root, quality, bass = _parse_chord_symbol(str(override["symbol"]))
        before = output["chordCells"][index]["symbol"]
        output["chordCells"][index].update({
            "symbol": str(override["symbol"]), "root": root, "quality": quality,
            "bass": bass if bass is not None else root, "confidence": 1.0,
            "decision": "USER_CONFIRMED", "uncertainty": [],
        })
        audit.append({"kind": "chord", "id": index, "before": before,
                      "after": override["symbol"], "source": "explicit-user-correction"})
    by_id = {section["id"]: section for section in output["sections"]}
    for override in section_overrides:
        allowed_section = {"id", "label", "startTick", "endTick"}
        if not set(override) <= allowed_section or "id" not in override:
            raise ValueError("Section override requires id and only supported section fields")
        section = by_id.get(str(override["id"]))
        if section is None:
            raise ValueError("Section correction references an unknown id")
        before = {key: section.get(key) for key in ("label", "startTick", "endTick")}
        if "label" in override:
            label = str(override["label"])
            if label not in set(_SECTION_LABELS.values()):
                raise ValueError("Unsupported corrected section label")
            section["label"] = label
        if "startTick" in override:
            section["startTick"] = int(override["startTick"])
        if "endTick" in override:
            section["endTick"] = int(override["endTick"])
        if not 0 <= section["startTick"] < section["endTick"] <= output["endTick"]:
            raise ValueError("Corrected section window is invalid")
        section.update({"confidence": 1.0, "decision": "USER_CONFIRMED",
                        "evidence": {"kind": "explicit-user-correction"}})
        audit.append({"kind": "section", "id": section["id"], "before": before,
                      "after": {key: section.get(key) for key in before},
                      "source": "explicit-user-correction"})
    ordered = sorted(output["sections"], key=lambda item: (item["startTick"], item["endTick"]))
    if any(left["endTick"] > right["startTick"] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("Corrected sections overlap")
    output["sections"] = ordered
    output["correctionAudit"] = audit
    output["manualReview"] = _manual_review(
        output["chordCells"], output["sections"], output["roleSegments"], output["cadences"]
    )
    output["mapHash"] = _map_hash(output)
    validate_song_map_v2(output)
    return output


def validate_song_map_v2(value: Mapping[str, Any]) -> None:
    required = {
        "schema", "version", "source", "sourceSha256", "ppq", "endTick",
        "tempoMap", "meterMap", "bars", "beatGrid", "key", "chordCells",
        "sections", "phrases", "cadences", "roleSegments", "polyphony",
        "manualReview", "confidence", "analysisVelocityUsed", "correctionAudit",
        "analysis", "invariants", "mapHash",
    }
    if set(value) != required:
        missing, extra = sorted(required - set(value)), sorted(set(value) - required)
        raise ValueError(f"SongMap 2.0 root fields mismatch; missing={missing}, extra={extra}")
    if value.get("schema") != SONG_MAP_SCHEMA or value.get("version") != SONG_MAP_VERSION:
        raise ValueError("Unsupported SongMap schema/version")
    if not re.fullmatch(r"[0-9a-f]{64}", str(value.get("sourceSha256", ""))):
        raise ValueError("SongMap sourceSha256 is invalid")
    if value["source"].get("sha256") != value["sourceSha256"]:
        raise ValueError("SongMap source hashes disagree")
    if value.get("analysisVelocityUsed") is not False:
        raise ValueError("SongMap analysis must not use source velocity")
    if value.get("invariants", {}).get("sourceMidiMutated") is not False:
        raise ValueError("SongMap cannot claim source MIDI mutation")
    if any(not _TRACK_UID.fullmatch(str(item.get("trackUid", ""))) for item in value["roleSegments"]):
        raise ValueError("SongMap contains an invalid trackUid")
    if any(item["endTick"] <= item["startTick"] for item in value["chordCells"]):
        raise ValueError("SongMap contains an invalid chord cell")
    if any(item["endTick"] <= item["startTick"] for item in value["sections"]):
        raise ValueError("SongMap contains an invalid section")
    if value.get("mapHash") != _map_hash(value):
        raise ValueError("SongMap mapHash mismatch")


def weighted_f1(expected: Sequence[str], predicted: Sequence[str]) -> float:
    if len(expected) != len(predicted) or not expected:
        return 0.0
    labels = sorted(set(expected) | set(predicted))
    total = len(expected)
    result = 0.0
    for label in labels:
        tp = sum(e == label and p == label for e, p in zip(expected, predicted))
        fp = sum(e != label and p == label for e, p in zip(expected, predicted))
        fn = sum(e == label and p != label for e, p in zip(expected, predicted))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        support = sum(item == label for item in expected)
        result += support / total * f1
    return result


def boundary_f1(expected: Sequence[int], predicted: Sequence[int], tolerance: int = 0) -> float:
    expected_remaining = list(sorted(expected))
    matches = 0
    for tick in sorted(predicted):
        candidates = [(abs(tick - target), index) for index, target in enumerate(expected_remaining)
                      if abs(tick - target) <= tolerance]
        if candidates:
            _, index = min(candidates)
            expected_remaining.pop(index)
            matches += 1
    precision = matches / len(predicted) if predicted else 0.0
    recall = matches / len(expected) if expected else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0