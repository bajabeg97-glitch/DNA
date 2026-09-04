"""Reference fixture chain for Session 38 Pa800 Device Lab."""

from __future__ import annotations

from pathlib import Path

from .device_profile_certification import build_reference_device_lab


def build_session38_chain(root: str | Path) -> dict:
    return build_reference_device_lab(Path(root))