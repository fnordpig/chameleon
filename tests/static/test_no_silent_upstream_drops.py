"""No-silent-upstream-drops: every wire field has a documented disposition.

This is the most valuable single correctness artefact in the project —
the standing assertion that no operator-written wire data can disappear
without one of three documented mechanisms:

1. **Claimed**: a codec actively translates this field. The codec's
   ``claimed_paths`` covers (or exactly is) the field's wire path. The
   round-trip property tests verify the translation.

2. **Pass-through**: Chameleon's data-routing absorbs the field
   losslessly without an active translator. Either the field's top-level
   wire key isn't claimed by any codec (so the assembler routes it to the
   verbatim pass-through bag), or the codec section that handles its
   top-level key has ``extra="allow"`` somewhere along the path
   (so the unmodelled descendant survives via Pydantic's
   ``__pydantic_extra__`` and is re-emitted by the assembler —  B1
   +  F2).

3. **Loss-warned**: the codec source emits a typed ``LossWarning`` that
   names this field — by ``field_path=FieldPath(segments=…)`` argument
   or by quoted token in the ``message=`` string. The operator sees the
   loss explicitly in stderr.

A field that fits *none* of these three is a **silent drop**. That's a
real bug: wire data the operator wrote that disappears with no record.

The walker, classifier, and source scanning live in ``_field_walker``;
this file holds the assertions and the coverage-matrix reporting.

Reading the report
------------------

Run ``uv run pytest tests/static -s`` to see the per-target coverage
matrix on stdout (claimed / pass-through / loss-warned / silent-drop
counts). The terminal-summary hook (``conftest.py``) renders the same
matrix at session end so it's visible even on a non-``-s`` run.

Limitations and design notes
----------------------------

* The ``LossWarning`` text scan is a string heuristic, not a semantic
  check — a quoted identifier-like token (``[A-Za-z_][A-Za-z0-9_]*``)
  inside a ``LossWarning(...)`` constructor counts. Common-English false
  positives are possible but only ever push a field from "silent-drop"
  to "loss-warned", which is the strict direction. The disposition
  reasons emitted on failure flag string-match dispositions explicitly.

* The walk descends into ``RootModel[X]``, every union arm, ``list[T]``
  elements, and ``dict[K, V]`` values when the inner type resolves to a
  ``BaseModel``. Field-path *segments* are the upstream wire keys — the
  Pydantic alias when one is declared, otherwise the Python attribute
  name. That mirrors how codec authors write ``claimed_paths`` and how
  the assembler routes by top-level key.

* The walker yields every wire-level field path — interior tables AND
  leaves. A claim at a non-leaf level (e.g., ``("hooks",)``) covers the
  whole subtree. The classifier honours that prefix-claim semantics.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydantic import BaseModel

from chameleon._types import TargetId
from chameleon.codecs._protocol import Codec
from chameleon.codecs.claude._generated import ClaudeCodeSettings
from chameleon.codecs.codex._generated import ConfigToml
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX
from chameleon.targets.claude import ClaudeTarget
from chameleon.targets.codex import CodexTarget
from tests.static._field_walker import (
    Disposition,
    TargetClassifier,
    codec_source_paths,
    iter_field_paths,
    render_path,
)

# -- Coverage matrix accumulation ----------------------------------------


@dataclasses.dataclass
class TargetCoverage:
    """Per-target coverage tally rendered to stdout for the scorecard."""

    target: TargetId
    full_model_name: str
    total: int = 0
    claimed: int = 0
    pass_through: int = 0
    loss_warned: int = 0
    silent_drop: int = 0
    silent_drop_paths: list[tuple[tuple[str, ...], type[BaseModel]]] = dataclasses.field(
        default_factory=list
    )

    def ingest(self, path: tuple[str, ...], parent: type[BaseModel], disp: Disposition) -> None:
        self.total += 1
        if disp.label == "claimed":
            self.claimed += 1
        elif disp.label == "pass-through":
            self.pass_through += 1
        elif disp.label == "loss-warned":
            self.loss_warned += 1
        else:
            self.silent_drop += 1
            self.silent_drop_paths.append((path, parent))

    def render(self) -> str:
        return (
            f"  target={self.target} ({self.full_model_name}): "
            f"total={self.total} | "
            f"claimed={self.claimed} | "
            f"pass-through={self.pass_through} | "
            f"loss-warned={self.loss_warned} | "
            f"silent-drop={self.silent_drop}"
        )


# Module-scope cache so the conftest terminal-summary hook can pull
# results out without re-running the walk. Each test function populates
# its own entry; the hook reads what it finds.
COVERAGE_REGISTRY: dict[TargetId, TargetCoverage] = {}


# -- Build-once classifier per target ------------------------------------


def _make_classifier(
    target: TargetId,
    full_model: type[BaseModel],
    codecs: tuple[type[Codec], ...],
) -> TargetClassifier:
    sources: Mapping[type[Codec], Path] = codec_source_paths(codecs)
    return TargetClassifier.build(target, full_model, codecs, sources)


def _classify_full_model(
    target: TargetId,
    full_model: type[BaseModel],
    codecs: tuple[type[Codec], ...],
) -> TargetCoverage:
    """Walk every wire-level field and accrue coverage tally + drop list."""
    classifier = _make_classifier(target, full_model, codecs)
    cov = TargetCoverage(target=target, full_model_name=full_model.__name__)

    for path, _info, parent in iter_field_paths(full_model):
        disp = classifier.classify_field(path, parent)
        cov.ingest(path, parent, disp)

    COVERAGE_REGISTRY[target] = cov
    return cov


def _format_silent_drops(cov: TargetCoverage, classifier: TargetClassifier) -> str:
    """Render the silent-drop list with re-classified dispositions for hints."""
    lines: list[str] = []
    for path, parent in cov.silent_drop_paths:
        disp = classifier.classify_field(path, parent)
        lines.append(
            f"  - {render_path(path)}\n      declared in: {parent.__name__}\n      why: {disp.why}"
        )
    return "\n".join(lines)


# -- Tests ---------------------------------------------------------------


def test_claude_no_silent_drops() -> None:
    """No field in ``ClaudeCodeSettings`` is a silent drop.

    If this fails, the failure message lists every offending field path
    and the reason its classifier returned ``silent-drop``. Each entry is
    a  fix candidate: claim the path, set the appropriate codec
    section ancestor to ``extra="allow"``, or add a ``LossWarning`` that
    names the field.
    """
    cov = _classify_full_model(BUILTIN_CLAUDE, ClaudeCodeSettings, ClaudeTarget.codecs)
    # Visible with `pytest -s`; mirrored by the conftest terminal-summary hook.
    print(f"\n[no-silent-drops] {cov.render()}")
    if cov.silent_drop > 0:
        clf = _make_classifier(BUILTIN_CLAUDE, ClaudeCodeSettings, ClaudeTarget.codecs)
        pytest.fail(
            f"{cov.silent_drop} silent-drop field(s) in ClaudeCodeSettings.\n"
            f"Coverage: {cov.render()}\n\n"
            "To fix any one of these, choose ONE:\n"
            "  (a) Claim the path in the relevant codec's claimed_paths.\n"
            "  (b) Set the codec section that owns the top-level key to "
            "extra='allow' so descendants harvest via __pydantic_extra__.\n"
            "  (c) Emit a LossWarning naming the field by path or message.\n\n"
            f"Offending fields:\n{_format_silent_drops(cov, clf)}"
        )


def test_codex_no_silent_drops() -> None:
    """No field in ``ConfigToml`` is a silent drop.

    Mirrors ``test_claude_no_silent_drops`` for the Codex target. See
    that docstring for the resolution recipe.
    """
    cov = _classify_full_model(BUILTIN_CODEX, ConfigToml, CodexTarget.codecs)
    # Visible with `pytest -s`; mirrored by the conftest terminal-summary hook.
    print(f"\n[no-silent-drops] {cov.render()}")
    if cov.silent_drop > 0:
        clf = _make_classifier(BUILTIN_CODEX, ConfigToml, CodexTarget.codecs)
        pytest.fail(
            f"{cov.silent_drop} silent-drop field(s) in ConfigToml.\n"
            f"Coverage: {cov.render()}\n\n"
            "To fix any one of these, choose ONE:\n"
            "  (a) Claim the path in the relevant codec's claimed_paths.\n"
            "  (b) Set the codec section that owns the top-level key to "
            "extra='allow' so descendants harvest via __pydantic_extra__.\n"
            "  (c) Emit a LossWarning naming the field by path or message.\n\n"
            f"Offending fields:\n{_format_silent_drops(cov, clf)}"
        )
