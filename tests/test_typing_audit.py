"""Forbidden-pattern audit enforcing the "everything is typed - no strings" rule.

Per design spec §5.4: the API surface must not use stringly-typed identifiers.
This is a blunt grep-based test that catches regressions cheaply. Whitelisted
patterns are documented inline; if you need to add to the whitelist, add a
comment explaining *why* the exception is principled.

Limitations:
  - Does NOT check for `Any`. ty does that better.
  - Does NOT check generated files (`_generated.py`).
  - Does NOT check the typing_audit itself (this file).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src" / "chameleon"

# Files exempt from the audit. Each entry MUST be justified.
EXEMPT_PATHS: frozenset[Path] = frozenset(
    {
        # Auto-generated from upstream JSON Schema; cannot enforce style here.
        # The schema-drift tests cover correctness instead.
    }
)


def _python_sources() -> Iterator[Path]:
    for path in SRC_DIR.rglob("*.py"):
        if path.name == "_generated.py":
            continue
        if path in EXEMPT_PATHS:
            continue
        yield path


@pytest.mark.parametrize(
    ("pattern", "rationale"),
    [
        (
            re.compile(r"\bdict\[str,\s*Any\]"),
            "use a typed Pydantic model or PassThroughBag[T] instead of dict[str, Any]",
        ),
        (
            re.compile(r"\bMapping\[str,\s*Any\]"),
            "use a typed Pydantic model or PassThroughBag[T] instead of Mapping[str, Any]",
        ),
        (
            re.compile(r":\s*str\s*=\s*[\"']claude[\"']"),
            "use BUILTIN_CLAUDE (a TargetId) instead of a literal string",
        ),
        (
            re.compile(r":\s*str\s*=\s*[\"']codex[\"']"),
            "use BUILTIN_CODEX (a TargetId) instead of a literal string",
        ),
    ],
    ids=["no-dict-str-any", "no-mapping-str-any", "no-claude-string", "no-codex-string"],
)
def test_forbidden_pattern_absent(pattern: re.Pattern[str], rationale: str) -> None:
    offenders: list[tuple[Path, int, str]] = []
    for src in _python_sources():
        for lineno, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
            # Allow the pattern inside a comment (we use it in docs/strings)
            stripped = line.split("#", 1)[0]
            if pattern.search(stripped):
                offenders.append((src, lineno, line.rstrip()))
    if offenders:
        rendered = "\n".join(f"  {p}:{n}: {ln}" for p, n, ln in offenders)
        pytest.fail(f"{rationale}:\n{rendered}")


def test_no_codec_uses_string_target_attribute() -> None:
    """Codec target= class-var must be a TargetId, not a literal string.

    A common regression: a codec author writes
        target = "claude"
    instead of
        target = BUILTIN_CLAUDE
    """
    pattern = re.compile(r"^\s*target\s*=\s*[\"'][^\"']+[\"']")
    offenders: list[tuple[Path, int, str]] = []
    for src in _python_sources():
        for lineno, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.match(line):
                offenders.append((src, lineno, line.rstrip()))
    if offenders:
        rendered = "\n".join(f"  {p}:{n}: {ln}" for p, n, ln in offenders)
        pytest.fail(
            "codec target= must be a TargetId (e.g. BUILTIN_CLAUDE), not a string:\n" + rendered
        )
