"""Self-authored Session 32 full optimizer / TrackPlan fixture."""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path

from .arrangement_graph import build_arrangement_graph
from .articulation_mapping import build_articulation_plan
from .candidate_search import build_candidate_set
from .evidence_authority import build_evidence_ledger
from .groove_polyphony import build_groove_plan
from .midi import MidiEvent, MidiFile, MidiTrack
from .premium_expression import build_expression_evidence, build_expression_plan
from .producer_brief import build_producer_brief
from .session24_fixture import FACTORY_PROFILE_ID, SOLO_CHANNEL_INDEX, SOLO_TRACK_INDEX, build_session24_midi
from .session25_fixture import build_session25_chain
from .song_understanding import analyze_song_map
from .track_identity import identity_for_track
from .track_instrument_analysis import analyze_track_instruments, load_factory_catalog
from .track_plan import build_track_plan


def build_session32_midi() -> MidiFile:
    """Add exact bank state to every melodic reference track and the drum kit."""

    midi = build_session24_midi()
    tracks = [MidiTrack(list(track.events)) for track in midi.tracks]
    for track_index, channel in ((1, 0), (2, 1), (4, 9)):
        tracks[track_index].events.extend((
            MidiEvent(0, -5, "channel", status=0xB0 | channel, data=bytes((0, 0))),
            MidiEvent(0, -4, "channel", status=0xB0 | channel, data=bytes((32, 0))),
        ))
    return MidiFile(midi.format_type, midi.ppq, tracks)


def _target_uid(logical_track: str) -> str:
    return "trk-" + sha256(("session32-target:" + logical_track).encode()).hexdigest()[:20]


def _melodic_profile(profiles: list[dict], role: str) -> dict:
    if role == "bass":
        choices = [item for item in profiles if item.get("kind") == "melodic" and item.get("role") == "bass"]
    elif role == "riff":
        choices = [item for item in profiles if item.get("kind") == "melodic" and item.get("role") == "melody"]
    elif role == "guitar":
        choices = [item for item in profiles if item.get("kind") == "melodic"
                   and item.get("role") == "chords" and 24 <= int(item.get("program", -1)) <= 31]
    elif role == "pad":
        choices = [item for item in profiles if item.get("kind") == "melodic"
                   and item.get("role") == "chords" and 48 <= int(item.get("program", -1)) <= 103]
    else:
        choices = [item for item in profiles if item.get("kind") == "melodic" and item.get("role") == "chords"]
    if not choices:
        raise RuntimeError(f"No production Factory target profile for {role}")
    return max(choices, key=lambda item: (int(item.get("samples", 0)), str(item["id"])))


def _drum_profiles(profiles: list[dict], required_pitches: set[int]) -> tuple[tuple[int, int, int], list[dict]]:
    by_sound: dict[tuple[int, int, int], dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for item in profiles:
        if item.get("kind") == "drum" and item.get("drumNote") is not None:
            key = (int(item["bankMsb"]), int(item["bankLsb"]), int(item["program"]))
            by_sound[key][int(item["drumNote"])].append(item)
    candidates = []
    for key, by_pitch in by_sound.items():
        if required_pitches <= set(by_pitch):
            selected = [max(by_pitch[pitch], key=lambda item: (int(item.get("samples", 0)), str(item["id"])))
                        for pitch in sorted(required_pitches)]
            candidates.append((sum(int(item.get("samples", 0)) for item in selected), key, selected))
    if not candidates:
        raise RuntimeError(f"No exact Factory drum kit covers pitches {sorted(required_pitches)}")
    _, key, selected = max(candidates, key=lambda item: (item[0], item[1]))
    return key, selected


def build_session32_target_bindings(root: str | Path, candidate: dict, groove: dict,
                                    variant_id: str = "C") -> list[dict]:
    root = Path(root)
    factory = json.loads((root / "data/factory-velocity-profiles.json").read_text(encoding="utf-8"))
    profiles = factory["profiles"]
    gold = json.loads((root / "data/gold-performance-patterns.json").read_text(encoding="utf-8"))
    strumming = json.loads((root / "data/factory-strumming.json").read_text(encoding="utf-8"))
    patterns = {
        "GOLD_PERFORMANCE": {item["id"]: item for item in gold["patterns"]},
        "FACTORY_STRUMMING": {item["id"]: item for item in strumming["patterns"]},
    }
    aliases = {variant_id, "variant-" + variant_id.removeprefix("variant-")}
    selected = next(item for item in candidate["variants"] if item["variantId"] in aliases)
    assignments = {item["role"]: item for item in groove["channelAssignments"]}
    logical_indices = {item["logicalTrack"]: index for index, item in enumerate(groove["channelAssignments"])}
    melodic = {role: _melodic_profile(profiles, role)
               for role in {item["role"] for item in selected["selections"]} - {"drums", "percussion"}}
    bindings = []
    for selection in selected["selections"]:
        role = selection["role"]
        assignment = assignments[role]
        pattern = patterns[selection["sourceKind"]][selection["patternId"]]
        if role in {"drums", "percussion"}:
            pitches = {int(note[2]) for note in pattern.get("notes", ()) if len(note) >= 3}
            (msb, lsb, program), selected_profiles = _drum_profiles(profiles, pitches)
        else:
            selected_profiles = [melodic[role]]
            msb, lsb, program = (int(selected_profiles[0]["bankMsb"]),
                                 int(selected_profiles[0]["bankLsb"]),
                                 int(selected_profiles[0]["program"]))
        index = logical_indices[assignment["logicalTrack"]]
        bindings.append({
            "requestId": selection["requestId"], "role": role,
            "logicalTrack": assignment["logicalTrack"],
            "targetTrackUid": _target_uid(assignment["logicalTrack"]),
            "targetTrackIndex": index, "targetTrackNumber": index + 1,
            "channelIndex": assignment["channelNumber"] - 1,
            "channelNumber": assignment["channelNumber"],
            "bankMsb": msb, "bankLsb": lsb, "program": program,
            "factoryProfileIds": sorted(item["id"] for item in selected_profiles),
            "confirmation": "EXACT_FACTORY_SOFTWARE", "deviceConfirmed": False,
        })
    return bindings


def build_session32_chain(root: str | Path) -> dict:
    root = Path(root)
    midi = build_session32_midi()
    song_map = analyze_song_map(midi.to_bytes(), "session32-reference.mid")
    brief = build_producer_brief(
        "Napravi življi pop-folk Style s punim refrenom, suptilnim prijelazima "
        "i očuvanim originalnim solom."
    )
    graph = build_arrangement_graph(song_map, brief, seed=2124, variant_count=2)
    candidate = build_candidate_set(graph, song_map, root, seed=2224, variant_count=3)
    groove = build_groove_plan(candidate, graph, song_map, root, seed=2324)
    identity = identity_for_track(midi, SOLO_TRACK_INDEX)
    expression_controls = {
        "version": "1.0", "trackUid": identity.track_uid,
        "trackNumber": SOLO_TRACK_INDEX + 1, "channelNumber": SOLO_CHANNEL_INDEX + 1,
        "startTick": 0, "endTick": song_map["endTick"], "seed": 2424,
        "intensity": 58, "profileId": FACTORY_PROFILE_ID,
        "enabledLayers": ["grace", "trill", "slide", "turnaround", "third", "echo", "cc11"],
        "minEvidenceConfidence": 0.9,
        "layerBudgets": {"ornament": 24, "third": 16, "echo": 8},
        "allowSharedChannel": False,
    }
    expression = build_expression_plan(
        midi, groove, song_map, root, expression_controls, build_expression_evidence()
    )
    track_analysis = analyze_track_instruments(
        midi.to_bytes(), "session32-reference.mid", factory_catalog=load_factory_catalog(root)
    )
    articulation_midi, _capture, articulation_catalog, articulation_groove, \
        articulation_expression, articulation_controls = build_session25_chain()
    articulation_plans = [
        build_articulation_plan(
            articulation_midi, articulation_catalog, articulation_groove,
            articulation_expression, articulation_controls[engine],
        )
        for engine in ("GUITAR", "RX", "DNC")
    ]
    documents = {
        "trackAnalysis": track_analysis, "songMap": song_map, "producerBrief": brief,
        "arrangementGraph": graph, "candidateSet": candidate, "groovePlan": groove,
        "expressionPlan": expression, "articulationPlans": articulation_plans,
    }
    ledger = build_evidence_ledger(documents, root, selected_variant_id="C")
    bindings = build_session32_target_bindings(root, candidate, groove)
    controls = {"selectedVariantId": "C", "quantizeDivision": 16,
                "quantizeStrength": 65, "velocityStrength": 65,
                "mixerStrength": 60, "softwareMidiNoteCeiling": 54}
    plan = build_track_plan(midi.to_bytes(), documents, ledger, bindings, controls, root)
    return {"midi": midi, "documents": documents, "ledger": ledger,
            "targetBindings": bindings, "controls": controls, "trackPlan": plan}
