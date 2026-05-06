"""ruamel.yaml wrapper preserving comments and key order across round-trip."""

from __future__ import annotations

import io
from pathlib import Path

from ruamel.yaml import YAML

# Module-level YAML instance pre-configured for round-trip mode.
# typ="rt" preserves comments, anchors, key order, flow style.
_YAML = YAML(typ="rt")
_YAML.preserve_quotes = True
_YAML.indent(mapping=2, sequence=4, offset=2)


def load_yaml(source: str | Path) -> object:
    """Parse YAML from a string or path; returns ruamel CommentedMap/Seq.

    The returned object is a plain `dict`/`list` for `==` comparisons
    but preserves `_yaml_comment` attributes for re-serialization.
    """
    if isinstance(source, Path):
        with source.open("r", encoding="utf-8") as fh:
            return _YAML.load(fh)
    return _YAML.load(source)


def dump_yaml(data: object) -> str:
    """Serialize a parsed-YAML object back to a string.

    Comments and anchor structure carried on the input are preserved.
    For freshly-built dicts/lists, output uses block style with 2-space
    mapping and 4-space sequence indents.
    """
    buf = io.StringIO()
    _YAML.dump(data, buf)
    return buf.getvalue()


def write_yaml(data: object, path: Path) -> None:
    """Atomically write YAML to a file (write-temp + rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        _YAML.dump(data, fh)
    tmp.replace(path)


__all__ = ["dump_yaml", "load_yaml", "write_yaml"]
