"""Schema-level tests for the  resolution-memory types.

Covers:
- ``ResolutionDecisionKind`` enum vocabulary matches the spec.
- ``Resolution`` requires the documented fields and accepts the
  documented optional ``decision_target``.
- ``Resolutions`` defaults to empty.
- ``ResolverOutcome`` defaults to ``persist=True`` and accepts every
  decision kind.
- Round-trip via ``model_dump`` / ``model_validate`` preserves every
  field (this is the persistence guarantee — these objects round-trip
  through neutral.yaml).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from chameleon.merge.resolve import ResolverOutcome
from chameleon.schema._constants import BUILTIN_CLAUDE
from chameleon.schema.neutral import (
    Neutral,
    Resolution,
    ResolutionDecisionKind,
    Resolutions,
)
from chameleon.schema.passthrough import PassThroughBag


def test_resolution_decision_kind_vocabulary() -> None:
    """Spec names exactly five decision kinds; values are stable strings."""
    assert {k.value for k in ResolutionDecisionKind} == {
        "take_neutral",
        "take_lkg",
        "take_target",
        "target_specific",
        "skip",
    }


def test_resolution_requires_decided_at_decision_hash() -> None:
    """``Resolution`` is the persisted record; the hash + timestamp are mandatory."""
    with pytest.raises(ValidationError):
        # Missing decided_at + decision_hash.
        Resolution.model_validate({"decision": "take_neutral"})


def test_resolution_accepts_optional_decision_target() -> None:
    r = Resolution(
        decided_at=datetime(2026, 5, 6, 18, 42, tzinfo=UTC),
        decision=ResolutionDecisionKind.TAKE_TARGET,
        decision_target=BUILTIN_CLAUDE,
        decision_hash="abc",
    )
    assert r.decision_target == BUILTIN_CLAUDE
    # Serializes through model_dump round-trip.
    dumped = r.model_dump(mode="json")
    rehydrated = Resolution.model_validate(dumped)
    assert rehydrated == r


def test_resolutions_defaults_to_empty() -> None:
    rs = Resolutions()
    assert rs.items == {}


def test_neutral_resolutions_field_default_empty() -> None:
    n = Neutral(schema_version=1)
    assert n.resolutions.items == {}


def test_neutral_round_trip_preserves_resolutions() -> None:
    """Persistence guarantee: dump then re-validate keeps every entry."""
    n = Neutral(schema_version=1)
    n.resolutions = Resolutions(
        items={
            "identity.reasoning_effort": Resolution(
                decided_at=datetime(2026, 5, 6, 18, 42, tzinfo=UTC),
                decision=ResolutionDecisionKind.TAKE_NEUTRAL,
                decision_hash="hash-a",
            ),
            "capabilities.plugin_marketplaces[archivium-marketplace]": Resolution(
                decided_at=datetime(2026, 5, 6, 18, 43, 12, tzinfo=UTC),
                decision=ResolutionDecisionKind.TAKE_TARGET,
                decision_target=BUILTIN_CLAUDE,
                decision_hash="hash-b",
            ),
        },
    )
    dumped = n.model_dump(mode="json")
    rehydrated = Neutral.model_validate(dumped)
    assert rehydrated.resolutions == n.resolutions


def test_resolver_outcome_persist_default_true() -> None:
    """Interactive resolvers persist by default — spec."""
    o = ResolverOutcome(decision=ResolutionDecisionKind.TAKE_NEUTRAL, value="x")
    assert o.persist is True


def test_resolver_outcome_accepts_every_decision_kind() -> None:
    """The typed outcome must round-trip every kind the resolver can emit."""
    for kind in ResolutionDecisionKind:
        o = ResolverOutcome(decision=kind, value=None, persist=False)
        assert o.decision is kind


def test_resolver_outcome_round_trip() -> None:
    o = ResolverOutcome(
        decision=ResolutionDecisionKind.TAKE_TARGET,
        decision_target=BUILTIN_CLAUDE,
        value="claude-opus",
        persist=True,
    )
    dumped = o.model_dump()
    rehydrated = ResolverOutcome.model_validate(dumped)
    assert rehydrated.decision is ResolutionDecisionKind.TAKE_TARGET
    assert rehydrated.decision_target == BUILTIN_CLAUDE
    assert rehydrated.value == "claude-opus"
    assert rehydrated.persist is True


def test_passthrough_bag_target_specific_default_empty() -> None:
    """``PassThroughBag.target_specific`` is the new per-target slot."""
    bag = PassThroughBag()
    assert bag.target_specific == {}
    bag2 = PassThroughBag.model_validate(
        {"items": {}, "target_specific": {"identity.reasoning_effort": "high"}}
    )
    assert bag2.target_specific == {"identity.reasoning_effort": "high"}
