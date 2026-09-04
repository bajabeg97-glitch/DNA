"""Self-authored Session 24 fixture with an exact production Factory sound."""

from __future__ import annotations

from pathlib import Path

from .arrangement_graph import build_arrangement_graph
from .candidate_search import build_candidate_set
from .groove_polyphony import build_groove_plan
from .midi import MidiEvent, MidiFile, MidiTrack
from .premium_expression import build_expression_evidence
from .producer_brief import build_producer_brief
from .session19_fixture import build_benchmark_case
from .song_understanding import analyze_song_map
from .track_identity import identity_for_track


FACTORY_PROFILE_ID = "698.771.685"
SOLO_TRACK_INDEX = 3
SOLO_CHANNEL_INDEX = 2


def build_session24_midi() -> MidiFile:
    midi = MidiFile.from_bytes(build_benchmark_case(0).midi)
    tracks = [MidiTrack(list(track.events)) for track in midi.tracks]
    lead = []
    for event in tracks[SOLO_TRACK_INDEX].events:
        if event.command == 0xC0 and event.channel == SOLO_CHANNEL_INDEX:
            lead.append(MidiEvent(event.tick, event.order, event.kind,
                                  status=event.status, data=bytes((64,)),
                                  meta_type=event.meta_type))
        else:
            lead.append(event)
    lead.extend([
        MidiEvent(0, -3, "channel", status=0xB0 | SOLO_CHANNEL_INDEX, data=bytes((0, 120))),
        MidiEvent(0, -2, "channel", status=0xB0 | SOLO_CHANNEL_INDEX, data=bytes((32, 0))),
    ])
    tracks[SOLO_TRACK_INDEX] = MidiTrack(lead)
    return MidiFile(midi.format_type, midi.ppq, tracks)


def build_session24_chain(root: str | Path):
    root = Path(root)
    midi = build_session24_midi()
    song_map = analyze_song_map(midi.to_bytes(), "session24-reference.mid")
    brief = build_producer_brief(
        "Napravi življi pop-folk Style s punim refrenom, suptilnim prijelazima "
        "i očuvanim originalnim solom."
    )
    graph = build_arrangement_graph(song_map, brief, seed=2124, variant_count=2)
    candidate = build_candidate_set(graph, song_map, root, seed=2224, variant_count=3)
    groove = build_groove_plan(candidate, graph, song_map, root, seed=2324)
    identity = identity_for_track(midi, SOLO_TRACK_INDEX)
    controls = {
        "version": "1.0", "trackUid": identity.track_uid,
        "trackNumber": SOLO_TRACK_INDEX + 1,
        "channelNumber": SOLO_CHANNEL_INDEX + 1,
        "startTick": 0, "endTick": song_map["endTick"],
        "seed": 2424, "intensity": 58, "profileId": FACTORY_PROFILE_ID,
        "enabledLayers": ["grace", "trill", "slide", "turnaround", "third", "echo", "cc11"],
        "minEvidenceConfidence": 0.9,
        "layerBudgets": {"ornament": 24, "third": 16, "echo": 8},
        "allowSharedChannel": False,
    }
    evidence = build_expression_evidence()
    return midi, song_map, brief, graph, candidate, groove, controls, evidence