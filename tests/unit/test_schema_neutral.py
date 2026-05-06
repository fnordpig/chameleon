from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from ruamel.yaml import YAML

from chameleon.schema._constants import BUILTIN_CLAUDE, BUILTIN_CODEX
from chameleon.schema.identity import ReasoningEffort
from chameleon.schema.neutral import Neutral
from chameleon.schema.passthrough import PassThroughBag


def test_minimal_neutral() -> None:
    n = Neutral(schema_version=1)
    assert n.schema_version == 1
    assert n.identity is not None  # default Identity()
    assert n.profiles == {}
    assert n.targets == {}


def test_full_neutral_round_trip() -> None:
    n = Neutral.model_validate(
        {
            "schema_version": 1,
            "identity": {
                "reasoning_effort": "high",
                "model": {"claude": "claude-sonnet-4-7", "codex": "gpt-5.4"},
            },
        }
    )
    assert n.identity.reasoning_effort is ReasoningEffort.HIGH
    assert n.identity.model is not None
    assert n.identity.model[BUILTIN_CLAUDE] == "claude-sonnet-4-7"
    assert n.identity.model[BUILTIN_CODEX] == "gpt-5.4"


def test_schema_version_required() -> None:
    # Intentionally invalid construction to verify the schema_version
    # field is required at validation time. We go through model_validate
    # so static type-checkers don't flag the missing argument.
    with pytest.raises(ValidationError):
        Neutral.model_validate({})


def test_passthrough_keyed_by_target_id() -> None:
    n = Neutral(
        schema_version=1,
        targets={BUILTIN_CLAUDE: PassThroughBag(items={"voice": {"enabled": True}})},
    )
    assert BUILTIN_CLAUDE in n.targets
    assert n.targets[BUILTIN_CLAUDE].items["voice"] == {"enabled": True}


def test_neutral_validates_example_yaml() -> None:
    example = Path(__file__).resolve().parents[1] / "golden" / "example_neutral.yaml"
    yaml = YAML(typ="safe", pure=True)
    raw = yaml.load(example.read_text(encoding="utf-8"))
    n = Neutral.model_validate(raw)
    assert n.schema_version == 1
