from __future__ import annotations

from chameleon.codecs._protocol import TranspileCtx
from chameleon.codecs.codex.identity import CodexIdentityCodec
from chameleon.schema._constants import BUILTIN_CODEX
from chameleon.schema.identity import Identity, ReasoningEffort


def test_round_trip_full() -> None:
    orig = Identity(
        reasoning_effort=ReasoningEffort.HIGH,
        model={BUILTIN_CODEX: "gpt-5.4"},
    )
    ctx = TranspileCtx()
    section = CodexIdentityCodec.to_target(orig, ctx)
    restored = CodexIdentityCodec.from_target(section, ctx)
    assert restored.reasoning_effort is ReasoningEffort.HIGH
    assert restored.model == {BUILTIN_CODEX: "gpt-5.4"}


def test_round_trip_empty() -> None:
    ctx = TranspileCtx()
    section = CodexIdentityCodec.to_target(Identity(), ctx)
    restored = CodexIdentityCodec.from_target(section, ctx)
    assert restored.reasoning_effort is None
    assert restored.model is None
