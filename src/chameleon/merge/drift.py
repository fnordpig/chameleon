"""Drift detection: live target files vs state-repo HEAD."""

from __future__ import annotations

import difflib
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


def _decode_for_diff(b: bytes) -> list[str]:
    """Decode bytes to lines for difflib.

    Falls back to latin-1 (lossless) when the content isn't valid UTF-8 — we
    never want a binary blob to crash the diff engine.
    """
    try:
        text = b.decode("utf-8")
    except UnicodeDecodeError:
        text = b.decode("latin-1")
    # `keepends=True` lets unified_diff emit accurate hunks even when the
    # file lacks a trailing newline.
    return text.splitlines(keepends=True)


def unified_diff(
    head: bytes,
    live: bytes,
    *,
    label: str,
    head_label: str = "HEAD",
    live_label: str = "live",
) -> str:
    """Return a unified-diff string `head` -> `live`.

    Empty string when the two byte-strings are identical. The `label`
    appears in the `---` / `+++` headers (e.g. the repo-relative path).
    """
    if head == live:
        return ""
    head_lines = _decode_for_diff(head)
    live_lines = _decode_for_diff(live)
    diff = difflib.unified_diff(
        head_lines,
        live_lines,
        fromfile=f"a/{label} ({head_label})",
        tofile=f"b/{label} ({live_label})",
        n=3,
    )
    out = "".join(diff)
    # difflib does not always terminate the last line; ensure trailing newline
    # so callers can concatenate per-file diffs cleanly.
    if out and not out.endswith("\n"):
        out += "\n"
    return out


__all__ = ["file_sha256", "has_drift", "map_drift", "unified_diff"]
