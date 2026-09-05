#!/usr/bin/env python3
"""Hard validator za Korg Pa800 Style Import SMF0."""

from __future__ import annotations

import re
import struct
from collections import defaultdict


# Projektni MIDI-note limiti po fiksnom Pa800 Style kanalu. Ovo nisu tvrdnje o
# stvarnom broju oscilatora koji pojedini Sound trosi na fizickom instrumentu.
PA800_CHANNEL_POLYPHONY_LIMITS = {
    8: 1,   # Bass / CH9
    9: 24,  # Drum / CH10
    10: 16, # Percussion / CH11
    11: 4,  # Acc1 / CH12
    12: 4,  # Acc2 / CH13
    13: 1,  # Acc3 / CH14
    14: 1,  # Acc4 / CH15
    15: 3,  # Acc5 / CH16
}


def valid_marker(marker):
    match = re.fullmatch(r"([ivfe])(\d)cv(\d)", marker or "")
    if not match:
        return False
    element, number, cv = match.group(1), int(match.group(2)), int(match.group(3))
    return ((element == "v" and 1 <= number <= 4 and 1 <= cv <= 6)
            or (element in "ife" and 1 <= number <= 2 and 1 <= cv <= 2))


def validate_pa800_smf(midi, expected_markers, expected_channels):
    issues = []
    if len(midi) < 22 or midi[:4] != b"MThd":
        return {"passed": False, "issues": ["Nedostaje MThd zaglavlje"]}
    header_length, midi_format, track_count, ppq = struct.unpack_from(">IHHH", midi, 4)
    if header_length != 6:
        return {"passed": False, "issues": ["MThd duljina mora biti 6"],
                "format": midi_format, "trackCount": track_count, "ppq": ppq}
    if midi_format != 0:
        issues.append("MIDI mora biti format 0")
    if track_count != 1:
        issues.append("MIDI mora imati jednu traku")
    if ppq != 480:
        issues.append("Pa800 Style MIDI mora koristiti PPQ 480")
    if midi[14:18] != b"MTrk":
        return {"passed": False, "issues": issues + ["Nedostaje MTrk chunk"]}
    track_length = struct.unpack_from(">I", midi, 18)[0]
    pos, end = 22, 22 + track_length
    if end > len(midi):
        return {"passed": False, "issues": issues + ["MTrk duljina prelazi datoteku"]}
    if end != len(midi):
        issues.append("Podaci iza deklariranog MTrk chunka nisu dopušteni")

    def read_variable():
        nonlocal pos
        value = 0
        for _ in range(4):
            if pos >= end:
                raise ValueError("Prekinuta variable-length vrijednost")
            byte = midi[pos]
            pos += 1
            value = (value << 7) | (byte & 127)
            if byte < 128:
                return value
        return value

    tick, running, note_count, event_count = 0, None, 0, 0
    markers, marker_ticks, time_signature_ticks = [], {}, set()
    channel_events = defaultdict(lambda: defaultdict(list))
    used_channels, open_notes = set(), defaultdict(list)
    active_by_channel = defaultdict(int)
    peak_by_channel = defaultdict(int)
    global_active, global_peak = 0, 0
    polyphony_violations = set()
    end_of_track_count, event_after_eot, last_event_eot = 0, False, False
    try:
        while pos < end:
            if end_of_track_count:
                event_after_eot = True
            tick += read_variable()
            if pos >= end:
                raise ValueError("Delta-time bez MIDI događaja")
            status = midi[pos]
            if status < 128:
                if running is None:
                    raise ValueError("Running status bez prethodnog statusa")
                status = running
            else:
                pos += 1
                if status < 240:
                    running = status
            if status == 255:
                if pos >= end:
                    raise ValueError("Prekinut meta event")
                kind = midi[pos]
                pos += 1
                length = read_variable()
                if pos + length > end:
                    raise ValueError("Meta event prelazi granicu trake")
                payload = midi[pos:pos + length]
                pos += length
                event_count += 1
                last_event_eot = kind == 47
                if kind == 6:
                    marker = payload.decode("ascii", "replace")
                    markers.append(marker)
                    marker_ticks[marker] = tick
                elif kind == 88:
                    time_signature_ticks.add(tick)
                elif kind == 47:
                    end_of_track_count += 1
                    if length != 0:
                        issues.append("End Of Track mora imati duljinu 0")
                continue
            if status in (240, 247):
                length = read_variable()
                if pos + length > end:
                    raise ValueError("SysEx event prelazi granicu trake")
                pos += length
                running = None
                event_count += 1
                last_event_eot = False
                continue
            if not 128 <= status <= 239:
                raise ValueError(f"Nepodržan status 0x{status:02X}")
            command, channel = status >> 4, status & 15
            if pos >= end:
                raise ValueError("Prekinut channel event")
            one = midi[pos]
            pos += 1
            two = None
            if command not in (12, 13):
                if pos >= end:
                    raise ValueError("Prekinut channel event")
                two = midi[pos]
                pos += 1
            used_channels.add(channel)
            channel_events[tick][channel].append((command, one, two))
            event_count += 1
            last_event_eot = False
            key = (channel, one)
            if command == 9 and two:
                if not 1 <= two <= 127:
                    issues.append("Note-on velocity izvan raspona 1-127")
                if open_notes[key]:
                    issues.append(f"Preklopljena nota CH{channel + 1} pitch {one}")
                open_notes[key].append(tick)
                active_by_channel[channel] += 1
                global_active += 1
                peak_by_channel[channel] = max(peak_by_channel[channel], active_by_channel[channel])
                global_peak = max(global_peak, global_active)
                limit = PA800_CHANNEL_POLYPHONY_LIMITS.get(channel)
                if limit is not None and active_by_channel[channel] > limit and channel not in polyphony_violations:
                    issues.append(
                        f"Polifonija CH{channel + 1} prelazi limit: "
                        f"{active_by_channel[channel]} > {limit}"
                    )
                    polyphony_violations.add(channel)
                note_count += 1
            elif command == 8 or (command == 9 and not two):
                if open_notes[key]:
                    start_tick = open_notes[key].pop(0)
                    active_by_channel[channel] = max(0, active_by_channel[channel] - 1)
                    global_active = max(0, global_active - 1)
                    if tick <= start_tick:
                        issues.append(f"Nevaljano trajanje note CH{channel + 1} pitch {one}")
                else:
                    issues.append(f"Note-off bez note-on CH{channel + 1} pitch {one}")
    except (ValueError, IndexError) as error:
        issues.append(str(error))

    if markers != expected_markers:
        issues.append("Marker redoslijed ne odgovara manifestu")
    if len(markers) != len(set(markers)):
        issues.append("Markeri nisu jedinstveni")
    for marker in markers:
        if marker != marker.lower() or not valid_marker(marker):
            issues.append(f"Neispravan Pa800 marker: {marker}")
    expected_set = set(expected_channels)
    if expected_set - set(range(8, 16)):
        issues.append("Manifest očekuje kanal izvan Pa800 raspona 9-16")
    if used_channels - set(range(8, 16)):
        issues.append("Pronađeni su MIDI kanali izvan Pa800 raspona 9-16")
    if used_channels - expected_set:
        issues.append("Pronađeni su MIDI kanali izvan aktivnog Pa800 skupa")
    if expected_set - used_channels:
        issues.append("Nedostaje događaj za jednu ili više aktivnih Pa800 traka")
    for marker in expected_markers:
        marker_tick = marker_ticks.get(marker)
        if marker_tick is None:
            continue
        if marker_tick not in time_signature_ticks:
            issues.append(f"Marker {marker} nema Time Signature")
        for channel in expected_channels:
            events = channel_events[marker_tick][channel]
            if not any(command == 11 and one == 0 for command, one, _ in events):
                issues.append(f"{marker} CH{channel + 1} nema CC00")
            if not any(command == 11 and one == 32 for command, one, _ in events):
                issues.append(f"{marker} CH{channel + 1} nema CC32")
            if not any(command == 12 for command, _, _ in events):
                issues.append(f"{marker} CH{channel + 1} nema Program Change")
            if not any(command == 11 and one == 11 and two == 127 for command, one, two in events):
                issues.append(f"{marker} CH{channel + 1} nema CC11=127")
    dangling = sum(len(values) for values in open_notes.values())
    if dangling:
        issues.append(f"Datoteka ima {dangling} visećih nota")
    if end_of_track_count != 1:
        issues.append("Traka mora imati točno jedan End Of Track")
    if event_after_eot or not last_event_eot:
        issues.append("End Of Track mora biti posljednji događaj")
    return {
        "passed": not issues, "issues": issues, "format": midi_format, "trackCount": track_count,
        "ppq": ppq, "markers": markers, "usedChannels": sorted(channel + 1 for channel in used_channels),
        "noteCount": note_count, "danglingNotes": dangling, "eventCount": event_count,
        "endOfTrackCount": end_of_track_count, "eventOrderValid": not event_after_eot and last_event_eot,
        "polyphonyPassed": not polyphony_violations,
        "polyphonyLimits": {str(channel + 1): PA800_CHANNEL_POLYPHONY_LIMITS[channel]
                            for channel in sorted(expected_set)
                            if channel in PA800_CHANNEL_POLYPHONY_LIMITS},
        "peakPolyphonyByChannel": {str(channel + 1): peak_by_channel[channel]
                                   for channel in sorted(expected_set)},
        "globalPeakConcurrentNotes": global_peak,
        "polyphonyMetric": "simultaneous-midi-notes",
        "checkedControllersPerMarker": ["CC00", "CC32", "ProgramChange", "CC11=127"],
    }