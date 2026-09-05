"""Shared self-authored fixture for the Session 33 renderer gate."""

from __future__ import annotations

from pathlib import Path

from .arrangement_renderer import render_arrangement, verify_rendered_arrangement
from .session32_fixture import build_session32_chain


def build_session33_chain(root: str | Path) -> dict:
    chain = build_session32_chain(root)
    source = chain["midi"].to_bytes()
    midi, manifest = render_arrangement(
        source, chain["trackPlan"], chain["documents"], chain["ledger"], root
    )
    return {
        **chain,
        "sourceBytes": source,
        "renderedMidi": midi,
        "renderManifest": manifest,
        "verification": verify_rendered_arrangement(midi, manifest, source),
    }