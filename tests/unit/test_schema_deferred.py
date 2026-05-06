from __future__ import annotations

from chameleon.schema.authorization import Authorization, DefaultMode
from chameleon.schema.governance import Governance
from chameleon.schema.interface import Interface
from chameleon.schema.lifecycle import Lifecycle


def test_deferred_domains_constructable_empty() -> None:
    Authorization()
    Lifecycle()
    Interface()
    Governance()


def test_authorization_default_mode_typed() -> None:
    a = Authorization(default_mode=DefaultMode.WORKSPACE_WRITE)
    assert a.default_mode is DefaultMode.WORKSPACE_WRITE
