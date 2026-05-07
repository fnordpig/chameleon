from __future__ import annotations

import pytest
from pydantic import ValidationError

from chameleon._types import TargetId  # noqa: F401  -- imported for clarity
from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX
from chameleon.schema.identity import (
    AuthMethod,
    Identity,
    IdentityModel,  # noqa: F401  -- imported for re-export verification
    ReasoningEffort,
)


def test_identity_minimal() -> None:
    ident = Identity()
    assert ident.reasoning_effort is None  # all optional


def test_reasoning_effort_enum() -> None:
    assert {e.value for e in ReasoningEffort} == {"minimal", "low", "medium", "high", "xhigh"}


def test_identity_model_per_target_mapping() -> None:
    ident = Identity(model={BUILTIN_CLAUDE: "claude-sonnet-4-7", BUILTIN_CODEX: "gpt-5.4"})
    assert ident.model is not None
    assert ident.model[BUILTIN_CLAUDE] == "claude-sonnet-4-7"
    assert ident.model[BUILTIN_CODEX] == "gpt-5.4"


def test_identity_model_rejects_scalar() -> None:
    with pytest.raises(ValidationError):
        Identity(model="claude-sonnet-4-7")  # type: ignore


def test_identity_reasoning_effort_target_shared_scalar() -> None:
    ident = Identity(reasoning_effort=ReasoningEffort.HIGH)
    assert ident.reasoning_effort is ReasoningEffort.HIGH


def test_auth_method_enum() -> None:
    # Wave-11 §15.x reconciliation shrank AuthMethod from 5 values to 2
    # after confirming neither upstream login-method enum supports
    # bedrock/vertex/azure. See docs/superpowers/specs/2026-05-06-parity-gap.md
    # ("Wave-11 §15.x schema reconciliation") for rationale.
    assert {a.value for a in AuthMethod} == {"oauth", "api-key"}


def test_identity_round_trips_via_pydantic() -> None:
    ident = Identity(
        reasoning_effort=ReasoningEffort.HIGH,
        thinking=True,
        model={BUILTIN_CLAUDE: "claude-sonnet-4-7"},
    )
    dumped = ident.model_dump(mode="json")
    restored = Identity.model_validate(dumped)
    assert restored == ident
