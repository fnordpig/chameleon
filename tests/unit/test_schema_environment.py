from __future__ import annotations

from chameleon.schema.environment import Environment, InheritPolicy


def test_environment_minimal() -> None:
    e = Environment()
    assert e.variables == {}


def test_environment_with_variables() -> None:
    e = Environment(variables={"CI": "true", "DEBUG": "0"})
    assert e.variables["CI"] == "true"


def test_inherit_policy() -> None:
    assert {p.value for p in InheritPolicy} == {"all", "core", "none"}
