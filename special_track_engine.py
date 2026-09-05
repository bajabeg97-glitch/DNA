"""Legacy-layout resolver: root shim -> dna_midi_studio.special_track_engine.

Keeps the flat import style of the v4.13-era test suite working while the
implementation lives in the package (repo policy: flat layout via resolvers).
"""
from dna_midi_studio.special_track_engine import (  # noqa: F401
    optimize_existing_echo_terca,
)
