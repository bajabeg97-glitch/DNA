"""
MAX 4.48 layout resolver.

The MAX model registry and the neural replacement engine were authored against a
classic workspace layout (data/, models/, learning_data/, artifacts/), while this
repository is a flattened export where the same files live at the repo root.

This module resolves logical directories to whatever layout is actually present so
that MAX code runs unchanged in BOTH layouts (classic original workspace and the
flattened git repository).  It never moves or duplicates files.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _is_dir_with(p: Path, probe: str) -> bool:
    return (p / probe).is_file() or (p / probe).is_dir()


def resolve_layout(project_root: str | Path) -> dict[str, Path]:
    """Return logical dirs for the layout that exists under project_root.

    Logical keys: data_dir, models_dir, learning_data_dir, artifacts_dir, corpus_dir.
    """
    root = Path(project_root).resolve()
    data_dir = root / "data"
    models_dir = root / "models"
    learning_dir = root / "learning_data"
    artifacts_dir = root / "artifacts"

    # Flat layout: data-ish files (ai_event_decoder/, evidence json) at repo root.
    if _is_dir_with(root, "ai_event_decoder") and not _is_dir_with(data_dir, "ai_event_decoder"):
        return {
            "layout": "flat",
            "projectRoot": root,
            "data_dir": root,                      # evidence json + ai_* model dirs live at root
            "models_dir": root / "dna-reconstructor-v2",  # neural infill model dir
            "learning_data_dir": root,             # learning_dataset_v1.npz at root
            "artifacts_dir": root,                 # session*/artifacts at root
            "corpus_dir": root,
        }

    # Classic layout: data/, models/, learning_data/, artifacts/ all present.
    if _is_dir_with(data_dir, "ai_event_decoder"):
        return {
            "layout": "classic",
            "projectRoot": root,
            "data_dir": data_dir,
            "models_dir": models_dir,
            "learning_data_dir": learning_dir,
            "artifacts_dir": artifacts_dir,
            "corpus_dir": root / "corpus" if (root / "corpus").is_dir() else root,
        }

    # Fallback: best effort with what exists.
    return {
        "layout": "unknown",
        "projectRoot": root,
        "data_dir": data_dir if data_dir.is_dir() else root,
        "models_dir": models_dir if models_dir.is_dir() else root,
        "learning_data_dir": learning_dir if learning_dir.is_dir() else root,
        "artifacts_dir": artifacts_dir if artifacts_dir.is_dir() else root,
        "corpus_dir": root,
    }


def registry_candidate_bases(project_root: str | Path) -> list[Path]:
    """Search bases used by MaxModelRegistry to find a model file by its stem.

    Classic statuses reference paths like 'data/ai_song_context/model/...' or
    '../models/dna-reconstructor-v2/...'; flat layout keeps the same relative
    shape under the repo root.  Candidate order prefers the resolved layout.
    """
    lay = resolve_layout(project_root)
    root = lay["projectRoot"]
    bases = [
        lay["data_dir"],
        lay["models_dir"],
        lay["learning_data_dir"],
        root,
    ]
    # Classic layout variants that the flattened repo may not have but that are
    # unambiguous when they exist (covers data/<rel> and models/<rel>).
    bases += [root / "data", root / "models"]
    out: list[Path] = []
    for b in bases:
        if b not in out:
            out.append(b)
    return out


def normalize_relative(relative_path: str) -> str:
    """Strip layout prefixes ('data/', '../models/', 'models/') from a spec path."""
    p = relative_path.replace("\\", "/")
    parts = [x for x in p.split("/") if x not in ("", ".", "..", "data", "models")]
    return "/".join(parts)


def engine_dirs(project_root: str | Path) -> dict[str, Path]:
    """Directories to construct TrackReplacementEngine(model_dir, learning_dir, data_dir)."""
    lay = resolve_layout(project_root)
    return {
        "model_dir": lay["models_dir"],
        "learning_data_dir": lay["learning_data_dir"],
        "data_dir": lay["data_dir"],
    }


def describe(project_root: str | Path) -> dict[str, Any]:
    lay = resolve_layout(project_root)
    return {k: str(v) for k, v in lay.items()}
