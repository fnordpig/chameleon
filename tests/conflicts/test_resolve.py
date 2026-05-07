from __future__ import annotations

import pytest

from chameleon._types import FieldPath
from chameleon.merge.changeset import ChangeRecord
from chameleon.merge.conflict import Conflict
from chameleon.merge.resolve import (
    NonInteractiveResolver,
    Strategy,
    on_conflict_to_strategy,
)
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX, Domains, OnConflict
from chameleon.schema.neutral import ResolutionDecisionKind


def _conflict() -> Conflict:
    return Conflict(
        record=ChangeRecord(
            domain=Domains.IDENTITY,
            path=FieldPath(segments=("model",)),
            n0="claude-sonnet-4-6",
            n1="claude-sonnet-4-7",
            per_target={
                BUILTIN_CLAUDE: "claude-opus-4-7",
                BUILTIN_CODEX: "gpt-5-pro",
            },
        ),
    )


def test_strategy_fail_raises() -> None:
    resolver = NonInteractiveResolver(Strategy(kind=OnConflict.FAIL))
    with pytest.raises(RuntimeError):
        resolver.resolve(_conflict())


def test_strategy_keep_returns_none() -> None:
    resolver = NonInteractiveResolver(Strategy(kind=OnConflict.KEEP))
    outcome = resolver.resolve(_conflict())
    assert outcome.value is None
    assert outcome.decision is ResolutionDecisionKind.SKIP
    assert outcome.persist is False


def test_strategy_prefer_neutral_returns_n1() -> None:
    resolver = NonInteractiveResolver(Strategy(kind=OnConflict.PREFER_NEUTRAL))
    outcome = resolver.resolve(_conflict())
    assert outcome.value == "claude-sonnet-4-7"
    assert outcome.decision is ResolutionDecisionKind.TAKE_NEUTRAL
    assert outcome.persist is False


def test_strategy_prefer_target_returns_target_value() -> None:
    resolver = NonInteractiveResolver(
        Strategy(kind=OnConflict.PREFER_TARGET, target=BUILTIN_CLAUDE),
    )
    outcome = resolver.resolve(_conflict())
    assert outcome.value == "claude-opus-4-7"
    assert outcome.decision is ResolutionDecisionKind.TAKE_TARGET
    assert outcome.decision_target == BUILTIN_CLAUDE
    assert outcome.persist is False


def test_on_conflict_to_strategy_parsing() -> None:
    assert on_conflict_to_strategy("fail").kind is OnConflict.FAIL
    assert on_conflict_to_strategy("keep").kind is OnConflict.KEEP
    assert on_conflict_to_strategy("prefer-neutral").kind is OnConflict.PREFER_NEUTRAL
    assert on_conflict_to_strategy("prefer-lkg").kind is OnConflict.PREFER_LKG
    s = on_conflict_to_strategy("prefer=claude")
    assert s.kind is OnConflict.PREFER_TARGET
    assert s.target == BUILTIN_CLAUDE
