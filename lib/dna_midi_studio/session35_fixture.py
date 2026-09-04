from pathlib import Path

from .end_to_end_arranger import build_song_to_style_project
from .session34_fixture import build_session34_chain


def build_session35_chain(root: str | Path, project_seed: int = 3535) -> dict:
    chain = build_session34_chain(root)
    project = build_song_to_style_project(
        chain["sourceBytes"], chain,
        {"selectedVariantId": "C", "lockedMarkers": [], "projectSeed": project_seed,
         "previewTier": "PREVIEW_ONLY"})
    return {**chain, "project": project}