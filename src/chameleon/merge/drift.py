"""Drift detection: live target files vs state-repo HEAD."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path


def file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def has_drift(live_bytes: bytes, head_bytes: bytes) -> bool:
    return live_bytes != head_bytes


def map_drift(live: Mapping[str, bytes], head: Mapping[str, bytes]) -> dict[str, bool]:
    """For each repo path, True if live differs from head."""
    return {k: has_drift(v, head.get(k, b"")) for k, v in live.items()}


__all__ = ["file_sha256", "has_drift", "map_drift"]
