from __future__ import annotations

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.codex.environment import CodexEnvironmentCodec
from chameleon.schema.environment import Environment


def test_round_trip() -> None:
    orig = Environment(variables={"CI": "true", "DEBUG": "0"})
    ctx = TranspileCtx()
    section = CodexEnvironmentCodec.to_target(orig, ctx)
    restored = CodexEnvironmentCodec.from_target(section, ctx)
    assert restored.variables == orig.variables
