from __future__ import annotations

from chameleon._types import FieldPath
from chameleon.merge.changeset import (
    ChangeOutcome,
    ChangeRecord,
    ChangeSource,
    classify_change,
)
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX, Domains


def _path(*segs: str) -> FieldPath:
    return FieldPath(segments=segs)


def test_unchanged_returns_unchanged() -> None:
    record = ChangeRecord(
        domain=Domains.IDENTITY,
        path=_path("model"),
        n0=1,
        n1=1,
        per_target={BUILTIN_CLAUDE: 1, BUILTIN_CODEX: 1},
    )
    assert classify_change(record).outcome is ChangeOutcome.UNCHANGED


def test_neutral_only_change_consensual() -> None:
    record = ChangeRecord(
        domain=Domains.IDENTITY,
        path=_path("reasoning_effort"),
        n0="medium",
        n1="high",
        per_target={BUILTIN_CLAUDE: "medium", BUILTIN_CODEX: "medium"},
    )
    out = classify_change(record)
    assert out.outcome is ChangeOutcome.CONSENSUAL
    assert out.resolved_value == "high"
    assert out.winning_source is ChangeSource.NEUTRAL


def test_single_target_drift_consensual() -> None:
    record = ChangeRecord(
        domain=Domains.IDENTITY,
        path=_path("reasoning_effort"),
        n0="medium",
        n1="medium",
        per_target={BUILTIN_CLAUDE: "high", BUILTIN_CODEX: "medium"},
    )
    out = classify_change(record)
    assert out.outcome is ChangeOutcome.CONSENSUAL
    assert out.resolved_value == "high"
    assert out.winning_source is ChangeSource.TARGET
    assert out.winning_target == BUILTIN_CLAUDE


def test_cross_target_conflict() -> None:
    record = ChangeRecord(
        domain=Domains.ENVIRONMENT,
        path=_path("variables", "FOO"),
        n0="x",
        n1="x",
        per_target={BUILTIN_CLAUDE: "y", BUILTIN_CODEX: "z"},
    )
    out = classify_change(record)
    assert out.outcome is ChangeOutcome.CONFLICT


def test_neutral_vs_target_conflict() -> None:
    record = ChangeRecord(
        domain=Domains.IDENTITY,
        path=_path("reasoning_effort"),
        n0="low",
        n1="high",
        per_target={BUILTIN_CLAUDE: "medium", BUILTIN_CODEX: "low"},
    )
    out = classify_change(record)
    assert out.outcome is ChangeOutcome.CONFLICT


def test_all_change_to_same_value_consensual() -> None:
    record = ChangeRecord(
        domain=Domains.IDENTITY,
        path=_path("reasoning_effort"),
        n0="low",
        n1="high",
        per_target={BUILTIN_CLAUDE: "high", BUILTIN_CODEX: "high"},
    )
    out = classify_change(record)
    assert out.outcome is ChangeOutcome.CONSENSUAL
    assert out.resolved_value == "high"
