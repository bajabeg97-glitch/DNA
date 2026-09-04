"""Self-authored Session 30 release-readiness fixture."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .release_readiness import (
    build_release_readiness,
    build_release_status_matrix,
    build_software_manifest,
    migrate_project_for_release,
    run_hardening_benchmarks,
)
from .session29_fixture import build_session29_chain


REFERENCE_DATE = "2026-09-03"


def build_session30_chain(root: str | Path):
    root = Path(root)
    previous = build_session29_chain(root)
    legacy_project = {
        "name": "ŠĐČŽ Premium projekt",
        "state": {
            "optimizer": {"enabled": True, "seed": 2300},
            "style": {"variant": "C", "target": "Korg Pa800"},
            "locks": ["v2cv1:bass", "v4cv1:drums"],
            "audit": [{"operation": "ACCEPT_VARIANT", "variant": "C"}],
            "path": "C:/Glazba/Ćirilica/ŠĐČŽ/vrlo-duga-mapa/pjesma.mid",
        },
        "source": {"name": "izvorna-pjesma.mid", "embedded": False},
        "artifacts": {"styleManifest": None, "optimizerReport": None},
    }
    source_copy = deepcopy(legacy_project)
    migration = migrate_project_for_release(legacy_project, REFERENCE_DATE)
    software_manifest = build_software_manifest(root, REFERENCE_DATE)
    hardening = run_hardening_benchmarks(root, previous, migration, REFERENCE_DATE)
    status_matrix = build_release_status_matrix(root, migration, hardening, REFERENCE_DATE)
    readiness = build_release_readiness(
        root, REFERENCE_DATE, migration, software_manifest, hardening, status_matrix
    )
    return {
        **previous,
        "legacyProject": legacy_project,
        "legacyProjectBeforeMigration": source_copy,
        "projectMigration": migration,
        "softwareManifest": software_manifest,
        "hardeningReport": hardening,
        "statusMatrix": status_matrix,
        "releaseReadiness": readiness,
    }
