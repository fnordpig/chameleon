from __future__ import annotations

from chameleon._types import TargetId
from chameleon.schema._constants import (
    BUILTIN_CLAUDE,
    BUILTIN_CODEX,
    Domains,
    OnConflict,
)


def test_domains_has_eight_members() -> None:
    expected = {
        "IDENTITY",
        "DIRECTIVES",
        "CAPABILITIES",
        "AUTHORIZATION",
        "ENVIRONMENT",
        "LIFECYCLE",
        "INTERFACE",
        "GOVERNANCE",
    }
    assert {d.name for d in Domains} == expected


def test_domains_values_are_lowercase_yaml_keys() -> None:
    for d in Domains:
        assert d.value == d.name.lower()


def test_on_conflict_strategies() -> None:
    assert {s.name for s in OnConflict} == {
        "FAIL",
        "LATEST",
        "KEEP",
        "PREFER_TARGET",
        "PREFER_NEUTRAL",
        "PREFER_LKG",
    }


def test_builtin_target_ids_are_registered() -> None:
    assert isinstance(BUILTIN_CLAUDE, TargetId)
    assert isinstance(BUILTIN_CODEX, TargetId)
    assert BUILTIN_CLAUDE.value == "claude"
    assert BUILTIN_CODEX.value == "codex"


def test_builtin_target_ids_can_be_reconstructed() -> None:
    assert TargetId(value="claude") == BUILTIN_CLAUDE
    assert TargetId(value="codex") == BUILTIN_CODEX
