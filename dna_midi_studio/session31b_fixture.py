"""Self-authored Session 31B evidence authority fixtures."""

from __future__ import annotations

from pathlib import Path

from .articulation_mapping import build_articulation_plan
from .evidence_authority import build_evidence_ledger
from .premium_expression import build_expression_plan
from .session24_fixture import build_session24_chain
from .session25_fixture import build_session25_chain
from .track_instrument_analysis import analyze_track_instruments, load_factory_catalog


def build_session31b_chain(root: str | Path):
    root = Path(root)
    midi, song_map, brief, graph, candidate, groove, expression_controls, expression_evidence = \
        build_session24_chain(root)
    expression = build_expression_plan(
        midi, groove, song_map, root, expression_controls, expression_evidence
    )
    track_analysis = analyze_track_instruments(
        midi.to_bytes(), "session31b-reference.mid", factory_catalog=load_factory_catalog(root)
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
        "trackAnalysis": track_analysis,
        "songMap": song_map,
        "producerBrief": brief,
        "arrangementGraph": graph,
        "candidateSet": candidate,
        "groovePlan": groove,
        "expressionPlan": expression,
        "articulationPlans": articulation_plans,
    }
    ledger = build_evidence_ledger(documents, root, selected_variant_id="C")
    return {
        "midi": midi, "documents": documents, "ledger": ledger,
        "trackAnalysis": track_analysis, "articulationPlans": articulation_plans,
    }