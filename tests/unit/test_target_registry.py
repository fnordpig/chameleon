from __future__ import annotations

from chameleon.schema._constants import BUILTIN_CLAUDE
from chameleon.targets._registry import TargetRegistry


def test_target_registry_lists_registered() -> None:
    r = TargetRegistry.discover()
    names = {t.value for t in r.target_ids()}
    assert "claude" in names
    assert "codex" in names


def test_target_registry_lookup() -> None:
    r = TargetRegistry.discover()
    target = r.get(BUILTIN_CLAUDE)
    assert target is not None
