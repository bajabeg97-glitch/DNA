"""Byte-real Session 4 fixture built on the Session 2 and 3 outputs."""

from __future__ import annotations

from pathlib import Path

from .guitar_reconstruction import GuitarConfig, load_guitar_registry
from .harmonic_reconstruction import (
    ChordCell,
    apply_harmonic_reconstruction,
    plan_harmonic_reconstruction,
)
from .session3_fixture import build_session3_case


def build_session4_case(root: str | Path):
    root = Path(root)
    midi, patterns3, profiles3, relationships, chords, config3 = build_session3_case(root)
    midi = apply_harmonic_reconstruction(
        midi,
        plan_harmonic_reconstruction(
            midi, patterns3, profiles3, relationships, chords, config3
        ),
    ).midi
    patterns, profiles, control_maps = load_guitar_registry(
        root / "data" / "session4-demo-registry.json"
    )
    config = GuitarConfig(
        track_index=4,
        channel=11,
        section="variation",
        start_tick=1920,
        end_tick=5760,
        seed=804,
        intensity=50,
        profile_id="130.011.001",
        enable_controls=True,
        allow_synthetic_control_map=True,
    )
    return midi, patterns, profiles, control_maps, chords, config