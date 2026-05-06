"""Regression test: no tracked file may contain raw git conflict markers.

Wave-2 had a near-miss where a commit was authored with raw
``<<<<<<<`` / ``=======`` / ``>>>>>>>`` markers in tracked files (caught
and discarded before reaching main). This test, combined with the
dedicated ``no-conflict-markers`` workflow and the opt-in pre-commit
hook (``tools/pre-commit-no-conflict-markers.sh``), is the third layer
of defense — it runs on every CI gates job via the existing pytest
gate, so even branches that bypass the dedicated workflow cannot
silently land conflict-marker pollution.

Rules
-----
* Walk every tracked file (``git ls-files``) — never the whole working
  tree, so untracked scratch files don't trip the test.
* The conflict-marker regex is anchored to start-of-line and built from
  character repetition at runtime, so this test file itself contains no
  literal conflict-marker line.
* A small allowlist excludes files that legitimately *discuss* the
  markers in prose (this test, the hook script, the workflow). The
  allowlist is exact paths, not globs — adding a file requires explicit
  intent.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# --- repo discovery ----------------------------------------------------------


def _repo_root() -> Path:
    """Locate the repo root via ``git rev-parse``.

    Using git rather than walking up from ``__file__`` keeps the test
    correct under worktrees (``.claude/worktrees/agent-*``) where the
    parent directory layout differs from a normal clone.
    """
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=True,
        text=True,
    )
    return Path(out.stdout.strip())


def _tracked_files(root: Path) -> list[Path]:
    """Return every tracked file as an absolute path."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        capture_output=True,
        check=True,
        cwd=root,
        text=False,
    )
    # -z uses NUL separators; the trailing NUL produces an empty entry
    # we drop. Decoding with surrogateescape preserves byte-faithful
    # paths even on weird filenames.
    parts = out.stdout.split(b"\x00")
    return [root / p.decode("utf-8", "surrogateescape") for p in parts if p]


# --- pattern construction ----------------------------------------------------


def _conflict_marker_regex() -> re.Pattern[str]:
    """Build the conflict-marker regex without literal marker lines.

    Git emits exactly seven repetitions of ``<``, ``=``, or ``>`` for
    its conflict markers, with a trailing space + ref name on the
    ``<`` and ``>`` variants and nothing after the ``=`` variant.
    Constructing the pattern from char repetition means this source
    file does not itself contain a literal start-of-line marker that
    would self-match if the test were ever run against itself.
    """
    lt = "<" * 7
    eq = "=" * 7
    gt = ">" * 7
    return re.compile(rf"^(?:{lt} |{eq}$|{gt} )", re.MULTILINE)


# --- allowlist ---------------------------------------------------------------

# Exact relative paths (POSIX style) of files that are allowed to mention
# conflict markers in prose or code. Keep this list small and explicit;
# every entry is a guarantee that the file has been hand-audited for
# *intentional* references, not accidental commit pollution.
_ALLOWLIST: frozenset[str] = frozenset(
    {
        "tests/integration/test_no_conflict_markers.py",
        "tools/pre-commit-no-conflict-markers.sh",
        ".github/workflows/no-conflict-markers.yml",
    }
)


# --- the test ----------------------------------------------------------------


def test_no_conflict_markers_in_tracked_files() -> None:
    """Every tracked file (modulo a tiny allowlist) is marker-free."""
    root = _repo_root()
    pattern = _conflict_marker_regex()

    offenders: list[str] = []
    for path in _tracked_files(root):
        rel = path.relative_to(root).as_posix()
        if rel in _ALLOWLIST:
            continue

        # Skip non-files (submodules, broken symlinks).
        if not path.is_file():
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Binary file — conflict markers are an ASCII-text pathology;
            # if a binary somehow contained them they would not be valid
            # diff3 output anyway. Skip.
            continue

        for m in pattern.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            offenders.append(f"{rel}:{line_no}: {m.group(0)!r}")

    if offenders:
        joined = "\n".join(offenders)
        pytest.fail(
            "Tracked file(s) contain raw git conflict markers — "
            "an unresolved merge slipped past review:\n" + joined
        )


def test_allowlist_entries_exist() -> None:
    """Allowlist must not silently rot — every entry must be a real file.

    If a file is renamed or removed, the allowlist entry becomes a hole
    in the guard. Failing here forces the operator to update the
    allowlist deliberately rather than discovering the gap in
    production.
    """
    root = _repo_root()
    missing = [rel for rel in _ALLOWLIST if not (root / rel).is_file()]
    assert not missing, f"allowlist references missing files: {missing}"
