"""ruamel.yaml wrapper preserving comments and key order across round-trip."""

from __future__ import annotations

import io
import re
from pathlib import Path

from ruamel.yaml import YAML, YAMLError

# Module-level YAML instance pre-configured for round-trip mode.
# typ="rt" preserves comments, anchors, key order, flow style.
_YAML = YAML(typ="rt")
_YAML.preserve_quotes = True
_YAML.indent(mapping=2, sequence=4, offset=2)


class YamlLoadError(ValueError):
    """Raised when a YAML document cannot be parsed safely."""


def _conflict_marker_regex() -> re.Pattern[str]:
    lt = re.escape("<" * 7)
    eq = re.escape("=" * 7)
    gt = re.escape(">" * 7)
    base = re.escape("|" * 7)
    return re.compile(rf"^(?:{lt}(?: .*)?|{eq}|{gt}(?: .*)?|{base}(?: .*)?)$", re.MULTILINE)


_CONFLICT_MARKER_RE = _conflict_marker_regex()


def _source_text_and_label(source: str | Path) -> tuple[str, str]:
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8"), str(source)
    return source, "<string>"


def _line_column(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    line_start = text.rfind("\n", 0, offset) + 1
    column = offset - line_start + 1
    return line, column


def _raise_if_conflict_marker(text: str, label: str) -> None:
    match = _CONFLICT_MARKER_RE.search(text)
    if match is None:
        return
    line, column = _line_column(text, match.start())
    msg = (
        f"invalid YAML in {label}: line {line}, column {column} contains "
        "a raw git conflict marker; resolve the file-level merge conflict "
        "before rerunning Chameleon"
    )
    raise YamlLoadError(msg)


def load_yaml(source: str | Path) -> object:
    """Parse YAML from a string or path; returns ruamel CommentedMap/Seq.

    The returned object is a plain `dict`/`list` for `==` comparisons
    but preserves `_yaml_comment` attributes for re-serialization.
    """
    text, label = _source_text_and_label(source)
    _raise_if_conflict_marker(text, label)
    try:
        return _YAML.load(text)
    except YAMLError as exc:
        detail = str(exc).strip()
        suffix = f": {detail}" if detail else ""
        raise YamlLoadError(f"invalid YAML in {label}{suffix}") from exc


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


__all__ = ["YamlLoadError", "dump_yaml", "load_yaml", "write_yaml"]
