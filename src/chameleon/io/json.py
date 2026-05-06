"""Stable-ordered JSON I/O.

Python's stdlib json preserves dict insertion order on dump and
load, which is what we want — the live target file's `git diff`
should be informative, not a noisy reordering.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(source: str | bytes | Path) -> Any:
    """Parse JSON from a string, bytes, or file path.

    Preserves insertion order through stdlib json which maintains
    dict insertion order since Python 3.7.
    """
    if isinstance(source, Path):
        return json.loads(source.read_bytes())
    return json.loads(source)


def dump_json(data: Any, *, indent: int = 2) -> str:
    """Serialize to JSON. Insertion order is preserved.

    `sort_keys=False` is critical — sorting destroys the operator's
    intended file ordering. Trailing newline is added so the file
    is POSIX-compliant.
    """
    return json.dumps(data, indent=indent, sort_keys=False, ensure_ascii=False) + "\n"


def write_json(data: Any, path: Path, *, indent: int = 2) -> None:
    """Atomically write JSON to a file (write-temp + rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dump_json(data, indent=indent), encoding="utf-8")
    tmp.replace(path)


__all__ = ["dump_json", "load_json", "write_json"]
