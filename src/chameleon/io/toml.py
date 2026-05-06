"""tomlkit wrapper preserving comments and table order across round-trip."""

from __future__ import annotations

from pathlib import Path

import tomlkit
from tomlkit.toml_document import TOMLDocument


def load_toml(source: str | Path) -> TOMLDocument:
    """Parse TOML preserving comments, table order, and value formatting."""
    if isinstance(source, Path):
        return tomlkit.parse(source.read_text(encoding="utf-8"))
    return tomlkit.parse(source)


def dump_toml(data: TOMLDocument | dict[str, object]) -> str:
    """Serialize a TOMLDocument or dict back to TOML text.

    If `data` is a plain dict (e.g. freshly built by a codec), it's
    converted to a TOMLDocument first. Comments only round-trip when
    `data` is itself a TOMLDocument (i.e. originated from `load_toml`).
    """
    if isinstance(data, TOMLDocument):
        return tomlkit.dumps(data)
    doc = tomlkit.document()
    for k, v in data.items():
        doc[k] = v
    return tomlkit.dumps(doc)


def write_toml(data: TOMLDocument | dict[str, object], path: Path) -> None:
    """Atomically write TOML to a file (write-temp + rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dump_toml(data), encoding="utf-8")
    tmp.replace(path)


__all__ = ["TOMLDocument", "dump_toml", "load_toml", "write_toml"]
