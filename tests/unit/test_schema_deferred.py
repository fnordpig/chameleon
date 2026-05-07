from __future__ import annotations

from chameleon.schema.authorization import Authorization, SandboxMode
from chameleon.schema.governance import Governance
from chameleon.schema.interface import Interface
from chameleon.schema.lifecycle import Lifecycle


def test_deferred_domains_constructable_empty() -> None:
    Authorization()
    Lifecycle()
    Interface()
    Governance()


def test_authorization_sandbox_mode_typed() -> None:
    a = Authorization(sandbox_mode=SandboxMode.WORKSPACE_WRITE)
    assert a.sandbox_mode is SandboxMode.WORKSPACE_WRITE
