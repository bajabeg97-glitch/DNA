from pathlib import Path

from .reliability_gate import run_reliability_gate
from .session35_fixture import build_session35_chain


def build_session36_chain(root: str | Path, *, stress_note_counts=(250, 1000),
                          fuzz_count: int = 32) -> dict:
    chain = build_session35_chain(root)
    reliability = run_reliability_gate(chain, root, stress_note_counts, fuzz_count)
    return {**chain, "reliabilityReport": reliability["report"],
            "reliabilityVault": reliability["vault"]}