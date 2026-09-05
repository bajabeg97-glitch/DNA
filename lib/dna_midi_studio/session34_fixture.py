from pathlib import Path

from .global_coherence import build_global_coherence_variants, verify_global_coherence
from .session33_fixture import build_session33_chain


def build_session34_chain(root: str | Path) -> dict:
    chain = build_session33_chain(root)
    variants, plan = build_global_coherence_variants(
        chain["renderedMidi"], chain["renderManifest"], chain["documents"]["arrangementGraph"])
    return {**chain, "coherentVariants": variants, "coherencePlan": plan,
            "coherenceVerification": verify_global_coherence(variants, plan, chain["renderedMidi"])}