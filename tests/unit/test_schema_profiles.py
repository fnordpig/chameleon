from __future__ import annotations

from chameleon.schema._constants import BUILTIN_CLAUDE
from chameleon.schema.identity import Identity, ReasoningEffort
from chameleon.schema.profiles import Profile


def test_profile_overlay() -> None:
    p = Profile(
        identity=Identity(
            reasoning_effort=ReasoningEffort.HIGH,
            model={BUILTIN_CLAUDE: "claude-opus-4-7"},
        ),
    )
    assert p.identity is not None
    assert p.identity.reasoning_effort is ReasoningEffort.HIGH


def test_profile_all_optional() -> None:
    p = Profile()
    assert p.identity is None
    assert p.directives is None
